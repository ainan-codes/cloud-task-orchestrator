"""
snapchat_runner.py — the Snapchat half of the scheduler.

One module so the queue behaves identically wherever it is driven from: the
Vercel cron hitting /api/run, the GitHub Actions `runner.py`, the retry pass,
and the dashboard's "publish now" button all end up in `execute_job`.

What the scheduler guarantees, and how:

  stays queued until its time    claim_due_snapchat_posts filters on
                                 scheduled_time_utc <= now
  picks the right account        the job carries snapchat_account_id, and
                                 every credential is read from that row only
  refreshes the token            ensure_valid_token, before the download
  never posts twice              three independent guards, below
  retries transient failures     next_retry_at + attempts, per error class
  gives up eventually            SNAP_MAX_ATTEMPTS, then 'abandoned'
  survives a crash               claimed rows sit in 'posting' and are
                                 reclaimed by the stuck-row sweep

The three duplicate guards are deliberate overlap, because each covers a
different failure:

  1. posted_ledger has a unique index on (account, video, placement) —
     the database refuses the second row even under a race.
  2. the publishing record's idempotency key is checked before publishing,
     so a retry of a job that already succeeded stops before uploading.
  3. claiming flips 'pending' -> 'posting' in one statement, so two workers
     cannot both hold the same job.

An authorization failure is not a post failure. It releases the job back to
'pending' without spending an attempt: the video is fine, the connection is
not, and burning three retries on an expired token would abandon a queue
that a single reconnect would have fixed.
"""

import datetime as dt
import logging
import os

import db
import drive
import logger as applog
import snapchat_oauth
import snapchat_poster
from snapchat_errors import SnapApiError, SnapAuthError, SnapError, SnapMediaError

applog.install()
log = logging.getLogger("snapchat_runner")

MAX_ATTEMPTS = int(os.environ.get("SNAP_MAX_ATTEMPTS", "3"))
RETRY_COOLDOWN_MINUTES = int(os.environ.get("SNAP_RETRY_COOLDOWN_MINUTES", "30"))
BATCH_LIMIT = int(os.environ.get("SNAP_BATCH_LIMIT", "20"))

# Terminal states a publishing record can already be in when a retry arrives.
_DONE_STATES = ("published",)


def _placement_for(job):
    return (
        job.get("target_placement")
        or job.get("account_default_placement")
        or "spotlight"
    )


def _locale_for(job):
    return job.get("snap_locale") or job.get("account_default_locale") or "en_US"


def _skip_save_for(job):
    value = job.get("snap_skip_save_to_profile")
    if value is None:
        value = job.get("account_skip_save_to_profile")
    return bool(value)


def _retry_delay(attempt):
    """Exponential backoff, capped, so a Snap outage is not hammered."""
    minutes = RETRY_COOLDOWN_MINUTES * (2 ** max(0, attempt - 1))
    return dt.timedelta(minutes=min(minutes, 6 * 60))


def assert_owns_content(job):
    """Refuse a job whose content belongs to a different Snapchat account.

    Without this a scheduled row could name account B while pointing at
    content synced for account A, and the publish would go out on B's
    credentials. Content with no Snapchat owner (a page's Drive item being
    cross-posted) is allowed through.
    """
    content_owner = job.get("content_account_id")
    account_id = job.get("snapchat_account_id")
    if content_owner is not None and content_owner != account_id:
        # SnapApiError, not SnapAuthError: the credentials are fine, the job
        # is malformed. Flagging the account would take a healthy connection
        # offline over one bad row, so this fails the job permanently and
        # leaves the account publishing.
        raise SnapApiError(
            f"Scheduled post {job.get('id')} targets Snapchat account {account_id} but its "
            f"content item belongs to account {content_owner}. Refusing to publish."
        )


def execute_job(job, publishing_record=None):
    """Publish one claimed Snapchat job. Returns the result dict.

    The caller has already moved the scheduled row to 'posting'. This raises
    on failure; `process_job` translates that into queue state.
    """
    account_id = job["snapchat_account_id"]
    placement = _placement_for(job)

    assert_owns_content(job)

    caption = job.get("caption_used") or job.get("content_caption") or ""
    topics = job.get("content_hashtags") or []

    record = publishing_record
    if record is None:
        record, created = db.create_snapchat_publishing_record(
            snapchat_account_id=account_id,
            scheduled_post_id=job["id"],
            content_item_id=job.get("content_item_id"),
            target_placement=placement,
            status="pending",
            caption=caption,
            topics=topics,
            locale=_locale_for(job),
            skip_save_to_profile=_skip_save_for(job),
            max_attempts=MAX_ATTEMPTS,
        )
        # Guard 2: this job already succeeded — do not publish it again.
        if not created and record and record.get("status") in _DONE_STATES:
            log.info(
                "Snapchat job %s already published (%s); not republishing.",
                job["id"], record.get("post_id"),
            )
            return {
                "already_published": True,
                "post_id": record.get("post_id"),
                "media_id": record.get("media_id"),
                "placement": placement,
                "record_id": record["id"],
            }

    record_id = record["id"] if record else None

    def on_stage(stage, detail):
        if not record_id:
            return
        fields = {"stage": stage}
        if detail.get("media_id"):
            fields["media_id"] = detail["media_id"]
        if "duration_seconds" in detail:
            fields["media_duration_seconds"] = detail.get("duration_seconds")
            fields["media_width"] = detail.get("width")
            fields["media_height"] = detail.get("height")
            fields["media_size_bytes"] = detail.get("size_bytes")
        status = {
            "validating": "validating",
            "creating_media": "uploading",
            "uploading": "uploading",
            "finalizing": "finalizing",
            "publishing": "publishing",
        }.get(stage)
        db.update_snapchat_publishing_record(record_id, status=status, **fields)

    if record_id:
        db.update_snapchat_publishing_record(record_id, status="validating", increment_attempt=True)

    media = drive.download_file(job["drive_file_id"])

    result = snapchat_poster.publish(
        media_bytes=media,
        account=job,
        placement=placement,
        caption=caption,
        topics=topics,
        locale=_locale_for(job),
        skip_save_to_profile=_skip_save_for(job),
        file_name=job.get("file_name"),
        db_module=db,
        on_stage=on_stage,
    )

    # Guard 1: the ledger's unique index is the last word on duplicates.
    db.record_snapchat_ledger(
        account_id,
        job.get("source_video_id"),
        job.get("content_item_id"),
        result["media_id"],
        result["description"],
        placement=placement,
        snap_post_id=result["post_id"],
    )
    db.mark_snapchat_published(account_id)

    if record_id:
        db.update_snapchat_publishing_record(
            record_id,
            status="published",
            stage="published",
            media_id=result["media_id"],
            post_id=result["post_id"],
            post_id_kind=result["post_id_kind"],
            publish_id=result["post_id"],
            request_id=result.get("request_id"),
            description=result["description"],
            locale=result["locale"],
            topics=result["topics"],
            error_message=None,
            error_code=None,
            error_kind=None,
        )
        result["record_id"] = record_id

    return result


def process_job(job):
    """Run one claimed job and write the outcome to the queue.

    Returns "posted", "skipped", "failed", "abandoned" or "deferred".
    """
    sched_id = job["id"]
    account_id = job["snapchat_account_id"]
    placement = _placement_for(job)

    # Guard 3 (cheap pre-check; the unique index is the real one).
    if job.get("source_video_id") and db.already_posted_snapchat(
        account_id, job["source_video_id"], placement
    ):
        db.mark_status(sched_id, "skipped",
                       error=f"already posted to this Snapchat account ({placement})")
        return "skipped"

    try:
        result = execute_job(job)
    except SnapAuthError as e:
        # The connection is broken, not the post. Keep the queue intact.
        db.update_snapchat_account_health(account_id, "invalid", e.message)
        db.release_snapchat_post(sched_id, f"Snapchat authorization needed: {e.message}")
        log.warning("Snapchat account %s needs reconnection: %s",
                    job.get("snapchat_username"), e.message)
        return "deferred"
    except SnapError as e:
        return _record_failure(job, e, retryable=e.retryable)
    except Exception as e:
        # Drive download failures, DB hiccups: treat as transient.
        return _record_failure(job, e, retryable=True)

    if result.get("already_published"):
        db.mark_status(sched_id, "posted", snap_post_id=result.get("post_id"))
        return "skipped"

    db.mark_status(
        sched_id, "posted",
        snap_media_id=result["media_id"],
        snap_post_id=result["post_id"],
    )
    db.update_snapchat_account_health(account_id, "valid")
    log.info("Published %s to @%s (%s) -> %s",
             job.get("source_video_id"), job.get("snapchat_username"),
             placement, result["post_id"])
    return "posted"


def _record_failure(job, error, retryable=True):
    """Write a failure to the queue, deciding retry vs. give up."""
    sched_id = job["id"]
    attempts = (job.get("attempts") or 0) + 1
    message = error.message if isinstance(error, SnapError) else str(error)
    permanent = (not retryable) or isinstance(error, SnapMediaError)

    record = db.get_publishing_record_by_key(
        db.build_idempotency_key(
            job["snapchat_account_id"], job.get("content_item_id"),
            _placement_for(job), sched_id,
        )
    )
    if record:
        db.update_snapchat_publishing_record(
            record["id"],
            status="failed",
            error_message=message,
            error_code=getattr(error, "error_code", None),
            error_kind=type(error).__name__,
            next_retry_at=None if permanent else dt.datetime.now(dt.timezone.utc) + _retry_delay(attempts),
        )

    if permanent or attempts >= MAX_ATTEMPTS:
        db.mark_status(sched_id, "abandoned", error=message)
        log.error("Snapchat post %s abandoned after %s attempt(s): %s",
                  sched_id, attempts, message)
        return "abandoned"

    db.mark_status(sched_id, "failed", error=message)
    log.warning("Snapchat post %s failed (attempt %s/%s): %s",
                sched_id, attempts, MAX_ATTEMPTS, message)
    return "failed"


# ============================== queue passes ===========================
def process_due(limit=None, remaining=None):
    """Publish everything whose scheduled time has arrived.

    `remaining()` lets a serverless caller stop before the platform kills it;
    unclaimed work simply stays pending for the next run.
    """
    counts = {"posted": 0, "failed": 0, "abandoned": 0, "skipped": 0, "deferred": 0}
    broken_accounts = set()

    jobs = db.claim_due_snapchat_posts(limit=limit or BATCH_LIMIT)
    for job in jobs:
        account_id = job["snapchat_account_id"]

        # Circuit breaker: one dead token should not drag every post for
        # that account through its own upload attempt.
        if account_id in broken_accounts:
            db.release_snapchat_post(job["id"], "Deferred: account needs reconnection")
            counts["deferred"] += 1
            continue

        if remaining is not None and remaining() <= 0:
            db.release_snapchat_post(job["id"], None)
            counts["deferred"] += 1
            continue

        outcome = process_job(job)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "deferred":
            broken_accounts.add(account_id)

    return counts


def process_retries(limit=None):
    """Give cooled-down Snapchat failures another attempt."""
    counts = {"posted": 0, "failed": 0, "abandoned": 0, "skipped": 0, "deferred": 0}
    broken_accounts = set()

    healed = db.heal_stuck_snapchat_records()
    if healed:
        log.info("Recovered %s Snapchat publishing record(s) stuck mid-publish.", healed)

    candidates = db.retryable_failed_snapchat_posts(
        cutoff_minutes=RETRY_COOLDOWN_MINUTES,
        max_attempts=MAX_ATTEMPTS,
        limit=limit or BATCH_LIMIT,
    )
    for candidate in candidates:
        if candidate["snapchat_account_id"] in broken_accounts:
            continue
        job = db.claim_snapchat_post(candidate["id"], from_statuses=("failed",))
        if not job:
            continue
        outcome = process_job(job)
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome == "deferred":
            broken_accounts.add(job["snapchat_account_id"])

    return counts


def retry_one(sched_id):
    """Force one failed/abandoned post back through the pipeline now.

    This is the dashboard's Retry button. It resets the attempt counter so a
    creator who fixed the underlying problem — reconnected the account,
    replaced a too-short video — is not blocked by earlier failures.
    """
    row = db.get_scheduled_post(sched_id)
    if not row:
        return None, "Scheduled post not found."
    if row.get("platform") != "snapchat":
        return None, "That scheduled post is not a Snapchat post."
    if row.get("status") == "posted":
        return None, "That post already published."

    db.reset_snapchat_post_for_retry(sched_id)
    job = db.claim_snapchat_post(sched_id, from_statuses=("failed", "pending", "abandoned"))
    if not job:
        return None, "Could not claim that post — another run may be handling it."

    return process_job(job), None


def publish_now(account, content_item, placement="spotlight", caption=None,
                topics=None, locale=None, skip_save_to_profile=False):
    """Publish a content item immediately, outside the queue.

    Goes through the same publishing record and idempotency key as a
    scheduled job, so pressing the button twice cannot post twice.
    """
    placement = (placement or "spotlight").lower()
    account_id = account["id"]

    content_owner = content_item.get("snapchat_account_id")
    if content_owner is not None and content_owner != account_id:
        raise SnapAuthError(
            "That content item belongs to a different Snapchat account."
        )

    if content_item.get("source_video_id") and db.already_posted_snapchat(
        account_id, content_item["source_video_id"], placement
    ):
        raise SnapMediaError(
            f"'{content_item.get('file_name')}' has already been published to this "
            f"account's {placement}."
        )

    caption = caption if caption is not None else (content_item.get("caption") or "")
    topics = topics if topics is not None else (content_item.get("hashtags") or [])

    record, created = db.create_snapchat_publishing_record(
        snapchat_account_id=account_id,
        content_item_id=content_item["id"],
        target_placement=placement,
        status="pending",
        caption=caption,
        topics=topics,
        locale=locale or account.get("default_locale") or "en_US",
        skip_save_to_profile=skip_save_to_profile,
        max_attempts=MAX_ATTEMPTS,
    )
    if not created and record and record.get("status") in _DONE_STATES:
        return {
            "already_published": True,
            "record_id": record["id"],
            "post_id": record.get("post_id"),
            "media_id": record.get("media_id"),
            "placement": placement,
        }

    # A manual publish carries the account row's own credentials, joined with
    # the content item — the same shape execute_job's helpers expect.
    job = dict(account)
    job.update({
        "id": None,
        "snapchat_account_id": account_id,
        "content_item_id": content_item["id"],
        "content_account_id": content_owner,
        "drive_file_id": content_item["drive_file_id"],
        "file_name": content_item.get("file_name"),
        "source_video_id": content_item.get("source_video_id"),
        "caption_used": caption,
        "content_hashtags": topics,
        "target_placement": placement,
        "snap_locale": locale,
        "snap_skip_save_to_profile": skip_save_to_profile,
        "account_default_placement": account.get("default_placement"),
        "account_default_locale": account.get("default_locale"),
        "account_skip_save_to_profile": account.get("skip_save_to_profile"),
    })

    try:
        result = execute_job(job, publishing_record=record)
    except SnapAuthError as e:
        db.update_snapchat_account_health(account_id, "invalid", e.message)
        if record:
            db.update_snapchat_publishing_record(
                record["id"], status="failed", error_message=e.message,
                error_kind="SnapAuthError", error_code=e.error_code,
            )
        raise
    except SnapError as e:
        if record:
            db.update_snapchat_publishing_record(
                record["id"], status="failed", error_message=e.message,
                error_kind=type(e).__name__, error_code=e.error_code,
            )
        raise

    db.update_snapchat_account_health(account_id, "valid")
    return result


def refresh_account_token(account):
    """Force a token refresh and report the outcome. Never returns a token."""
    snapchat_oauth.ensure_valid_token(account, db_module=db, force=True)
    db.update_snapchat_account_health(account["id"], "valid")
    return {"refreshed": True}
