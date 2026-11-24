"""
runner.py — called by GitHub Actions every 15 minutes.
Posts every scheduled item whose time has arrived, then exits.
No web server needed. No timeout risk.

Usage:
  python runner.py              # post due items
  python runner.py --analytics  # also refresh analytics
"""

import sys
import datetime as dt

import captions
import db
import drive
import logger as applog
import poster
import snapchat_runner
import youtube_runner

applog.install()


def run_due_snapchat_posts():
    """Publish every Snapchat post whose time has arrived.

    The queue logic lives in snapchat_runner so this job, the Vercel cron and
    the dashboard's retry button cannot drift apart.
    """
    counts = snapchat_runner.process_due()
    if any(counts.values()):
        print(f"  [SNAPCHAT] {counts['posted']} posted, {counts['failed']} failed, "
              f"{counts['abandoned']} abandoned, {counts['skipped']} skipped, "
              f"{counts['deferred']} deferred (account needs reconnection).")
    return counts["posted"], counts["failed"] + counts["abandoned"]


def run_snapchat_retries():
    """Retry cooled-down Snapchat failures that still have attempts left."""
    counts = snapchat_runner.process_retries()
    if any(counts.values()):
        print(f"  [SNAPCHAT RETRY] {counts['posted']} recovered, "
              f"{counts['failed']} still failing, {counts['abandoned']} abandoned.")
    return counts["posted"], counts["abandoned"]


def run_due_posts():
    # 1. Self-heal any zombie rows stuck in 'posting'
    healed = db.heal_stuck_posting_posts(max_age_minutes=15)
    if healed:
        print(f"  [SELF-HEAL] Recovered {healed} zombie post(s) stuck in 'posting' state.")

    due = db.due_posts()
    if not due:
        print("Nothing due right now.")
        return 0, 0

    posted, failed = 0, 0
    page_cache = {}  # one page lookup per page, not per post
    broken_pages = set()  # circuit breaker for pages with auth outages

    for sp in due:
        sched_id = sp["id"]
        page_id = sp["page_id"]

        # Circuit breaker: if this page already failed with an auth outage, skip
        if page_id in broken_pages:
            print(f"  [CIRCUIT PAUSE] Skipping {sp['source_video_id']} for page {sp['page_name']} (auth outage).")
            continue

        if db.already_posted(page_id, sp["source_video_id"]):
            db.mark_status(sched_id, "skipped", error="already posted to this page")
            print(f"  SKIP {sp['source_video_id']} — already posted to this page")
            continue

        # Pre-flight token validation before downloading heavy media from Google Drive
        token_valid, token_detail = poster.check_token(sp["fb_page_id"], sp["fb_access_token"])
        if not token_valid:
            is_auth = poster.is_auth_error(token_detail)
            if is_auth:
                broken_pages.add(page_id)
                db.update_page_token_health(page_id, "invalid", token_detail)
                print(f"  [AUTH CIRCUIT BREAKER] Page {sp['page_name']} token invalid: {token_detail}. Breaker tripped, keeping post pending.")
                continue
            db.mark_status(sched_id, "failed", error=f"Token check failed: {token_detail}")
            failed += 1
            continue
        else:
            db.update_page_token_health(page_id, "valid")

        db.mark_status(sched_id, "posting")
        try:
            caption = captions.freshen_for_posting(sp, page_cache=page_cache)

            media = None
            if sp.get("media_type") == "text":
                print(f"  Publishing text status update to {sp['fb_page_id']}...")
            else:
                fname = sp.get("file_name") or sp.get("source_video_id") or "media"
                print(f"  Downloading {fname} ({sp.get('media_type', 'reel')})...")
                media = drive.download_file(sp["drive_file_id"])
                print(f"  Posting to page {sp['fb_page_id']} ({sp['media_type']})...")

            creation_id = poster.publish(
                media_type=sp["media_type"],
                page_id=sp["fb_page_id"],
                page_token=sp["fb_access_token"],
                media_bytes=media,
                caption=caption,
                scheduled_ts=None,
            )

            db.record_ledger(
                sp["page_id"], sp["source_video_id"],
                sp["content_item_id"], creation_id, caption,
            )
            db.mark_status(sched_id, "posted", fb_creation_id=creation_id)
            print(f"  [OK] Posted {sp['source_video_id']} -> fb id {creation_id}")
            posted += 1

        except Exception as e:
            err_msg = str(e)
            is_auth = poster.is_auth_error(err_msg)
            if is_auth:
                broken_pages.add(page_id)
                db.update_page_token_health(page_id, "invalid", err_msg)
                print(f"  [AUTH FAILURE] Tripped circuit breaker for page {sp['page_name']}: {err_msg}")

            db.mark_status(sched_id, "failed", error=err_msg)
            print(f"  [FAIL] Failed {sp['source_video_id']}: {err_msg}")
            failed += 1

    return posted, failed


def run_retries():
    """Retry transient failures; auth failures stay paused without abandonment."""
    retried, abandoned = 0, 0
    page_cache = {}
    broken_pages = set()

    try:
        candidates = db.retryable_failed_posts()
        for sp in candidates:
            page_id = sp["page_id"]
            if page_id in broken_pages:
                continue

            sched_id = sp["id"]
            if db.already_posted(page_id, sp["source_video_id"]):
                db.mark_status(sched_id, "skipped", error="already posted to this page")
                continue

            # Pre-flight check
            token_valid, token_detail = poster.check_token(sp["fb_page_id"], sp["fb_access_token"])
            if not token_valid:
                if poster.is_auth_error(token_detail):
                    broken_pages.add(page_id)
                    db.update_page_token_health(page_id, "invalid", token_detail)
                # DO NOT increment attempts or abandon on auth outage
                continue

            db.mark_status(sched_id, "posting")
            try:
                caption = captions.freshen_for_posting(sp, page_cache=page_cache)
                media = drive.download_file(sp["drive_file_id"])
                creation_id = poster.publish(
                    media_type=sp["media_type"],
                    page_id=sp["fb_page_id"],
                    page_token=sp["fb_access_token"],
                    media_bytes=media,
                    caption=caption,
                    scheduled_ts=None,
                )
                db.record_ledger(page_id, sp["source_video_id"], sp["content_item_id"], creation_id, caption)
                db.mark_status(sched_id, "posted", fb_creation_id=creation_id)
                retried += 1
            except Exception as e:
                err_str = str(e)
                is_auth = poster.is_auth_error(err_str)
                if is_auth:
                    broken_pages.add(page_id)
                    db.update_page_token_health(page_id, "invalid", err_str)
                    db.mark_status(sched_id, "failed", error=err_str)
                else:
                    prior = sp.get("attempts") or 0
                    if prior + 1 >= 3:
                        db.mark_status(sched_id, "abandoned", error=err_str)
                        abandoned += 1
                    else:
                        db.mark_status(sched_id, "failed", error=err_str)
    except Exception as e:
        print(f"Retry loop error: {e}")

    return retried, abandoned


def run_analytics():
    updated = 0
    for row in db.posted_items_for_analytics():
        m = poster.fetch_reel_insights(row["fb_creation_id"], row["fb_access_token"])
        if "error" in m:
            print(f"  analytics skip {row['fb_creation_id']}: {m['error']}")
            continue
        db.save_analytics(
            row["page_id"], row["fb_creation_id"],
            row["source_video_id"], m,
        )
        updated += 1
    return updated


if __name__ == "__main__":
    now = dt.datetime.now(dt.timezone.utc)
    print(f"Runner started at {now.isoformat()}")

    fb_posted, fb_failed = run_due_posts()
    snap_posted, snap_failed = run_due_snapchat_posts()
    yt_posted, yt_failed = youtube_runner.run_due_youtube_jobs()
    retried, abandoned = run_retries()
    snap_retried, snap_abandoned = run_snapchat_retries()

    total_posted = fb_posted + snap_posted + snap_retried + yt_posted
    total_failed = fb_failed + snap_failed + yt_failed
    print(f"Done: {total_posted} posted (FB: {fb_posted}, Snap: {snap_posted}, YT: {yt_posted}), "
          f"{total_failed} failed, {retried + snap_retried} retried, "
          f"{abandoned + snap_abandoned} abandoned.")

    if "--analytics" in sys.argv:
        print("Refreshing analytics...")
        updated = run_analytics()
        print(f"Analytics updated: {updated} items.")

    # Exit cleanly so GitHub Actions workflow remains healthy while application retries handle cooled-down items
    sys.exit(0)
