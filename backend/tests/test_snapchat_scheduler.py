"""
test_snapchat_scheduler.py — queue behaviour and API surface.

Covers the promises the scheduler makes that are easy to break and expensive
to get wrong in production: a post goes out once and only once, a dead token
pauses an account instead of burning its queue, one broken account does not
stall the others, and no endpoint ever returns a credential.

The database is mocked, so these run anywhere:

    cd backend && python -m unittest discover -s tests -v
"""

import datetime as dt
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Pinned, not setdefault: db.py loads .env on import, and those keys are
# deliberately empty now that the real values live in the vault.
os.environ["SNAPCHAT_CLIENT_ID"] = "test-client-id"
os.environ["SNAPCHAT_CLIENT_SECRET"] = "test-client-secret"
os.environ.setdefault("CREDENTIAL_CACHE_TTL", "0")   # no cross-test cache bleed
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/testdb")

import snapchat_runner                        # noqa: E402
from snapchat_errors import (                 # noqa: E402
    SnapApiError,
    SnapAuthError,
    SnapMediaError,
    SnapTransientError,
)

# Credential resolution reads the vault first. These tests have no database,
# so point it at an empty one: every lookup falls through to the environment
# immediately instead of waiting on a connection that will never open.
import credentials as _credentials                            # noqa: E402
from unittest.mock import MagicMock as _MagicMock             # noqa: E402

_credentials.db = _MagicMock()
_credentials.db.get_app_credential.return_value = None
_credentials.invalidate()

from tests.test_snapchat_integration import make_mp4  # noqa: E402


def make_job(**overrides):
    """One row as claim_due_snapchat_posts returns it: the scheduled post,
    its content item, and its account's credentials, joined."""
    job = {
        "id": 501,
        "platform": "snapchat",
        "status": "posting",
        "snapchat_account_id": 1,
        "content_item_id": 77,
        "content_account_id": 1,
        "attempts": 0,
        "scheduled_time_utc": dt.datetime.now(dt.timezone.utc),
        "caption_used": "Reef dive #ocean",
        "target_placement": "spotlight",
        "snap_locale": "en_US",
        "snap_skip_save_to_profile": False,
        "drive_file_id": "drive-abc",
        "file_name": "dive.mp4",
        "source_video_id": "vid-123",
        "content_caption": "Reef dive",
        "content_hashtags": ["ocean"],
        "snapchat_username": "whalelife",
        "account_name": "Whale Life",
        "public_profile_id": "profile-aaa",
        "account_default_placement": "spotlight",
        "account_default_locale": "en_US",
        "account_skip_save_to_profile": False,
        "extra_hashtags": [],
        "caption_footer": "",
        "access_token_encrypted": "enc-at",
        "refresh_token_encrypted": "enc-rt",
        "token_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    }
    job.update(overrides)
    return job


def publish_result(post_id="spot-1", media_id="media-1", placement="spotlight"):
    return {
        "placement": placement,
        "profile_id": "profile-aaa",
        "media_id": media_id,
        "post_id": post_id,
        "post_id_kind": "spotlight_id" if placement == "spotlight" else "story_id",
        "request_id": "req-1",
        "description": "Reef dive #ocean",
        "locale": "en_US",
        "topics": ["ocean"],
        "skip_save_to_profile": False,
        "media_info": {"width": 1080, "height": 1920, "duration_seconds": 12.0,
                       "size_bytes": 4096},
        "status": "published",
    }


class SchedulerTestCase(unittest.TestCase):
    """Wires snapchat_runner to mock db / drive / publisher."""

    def setUp(self):
        self.db = MagicMock()
        self.drive = MagicMock()
        self.publish = MagicMock()

        self.db.already_posted_snapchat.return_value = False
        self.db.create_snapchat_publishing_record.return_value = ({"id": 900, "status": "pending"}, True)
        self.db.get_publishing_record_by_key.return_value = {"id": 900, "status": "failed"}
        self.db.build_idempotency_key.side_effect = (
            lambda acc, item=None, placement="spotlight", sched=None, salt=None:
            f"sched:{sched}:{placement}"
        )
        self.db.record_snapchat_ledger.return_value = True
        self.db.heal_stuck_snapchat_records.return_value = 0
        self.db.retryable_failed_snapchat_posts.return_value = []
        self.drive.download_file.return_value = make_mp4(12.0)
        self.publish.return_value = publish_result()

        patches = [
            patch.object(snapchat_runner, "db", self.db),
            patch.object(snapchat_runner, "drive", self.drive),
            patch.object(snapchat_runner.snapchat_poster, "publish", self.publish),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


# ======================================================================
# 1. the happy path
# ======================================================================
class TestScheduledPublish(SchedulerTestCase):
    def test_due_post_publishes_and_is_recorded(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job()]

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["posted"], 1)
        self.publish.assert_called_once()

        # The queue row is closed out with Snapchat's own identifiers.
        self.db.mark_status.assert_any_call(
            501, "posted", snap_media_id="media-1", snap_post_id="spot-1",
        )
        # And the permanent ledger row is written for the dedup guarantee.
        ledger = self.db.record_snapchat_ledger.call_args
        self.assertEqual(ledger[0][0], 1)               # account
        self.assertEqual(ledger[0][1], "vid-123")       # source video
        self.assertEqual(ledger[1]["placement"], "spotlight")
        self.assertEqual(ledger[1]["snap_post_id"], "spot-1")

    def test_publish_receives_the_jobs_own_account_row(self):
        """The credentials handed to the publisher come from the job's own
        joined account columns, never from a shared or ambient token."""
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        snapchat_runner.process_due()

        account = self.publish.call_args[1]["account"]
        self.assertEqual(account["snapchat_account_id"], 1)
        self.assertEqual(account["public_profile_id"], "profile-aaa")
        self.assertEqual(account["access_token_encrypted"], "enc-at")

    def test_placement_locale_and_skip_flag_come_from_the_job(self):
        self.db.claim_due_snapchat_posts.return_value = [
            make_job(target_placement="story", snap_locale="fr_FR",
                     snap_skip_save_to_profile=True)
        ]
        self.publish.return_value = publish_result(post_id="story-1", placement="story")

        snapchat_runner.process_due()

        kwargs = self.publish.call_args[1]
        self.assertEqual(kwargs["placement"], "story")
        self.assertEqual(kwargs["locale"], "fr_FR")
        self.assertIs(kwargs["skip_save_to_profile"], True)

    def test_publishing_record_tracks_the_pipeline_stages(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        snapchat_runner.process_due()

        # The attempt is counted once, up front, not per stage.
        first = self.db.update_snapchat_publishing_record.call_args_list[0]
        self.assertEqual(first[1]["status"], "validating")
        self.assertIs(first[1]["increment_attempt"], True)

        final = self.db.update_snapchat_publishing_record.call_args_list[-1]
        self.assertEqual(final[1]["status"], "published")
        self.assertEqual(final[1]["post_id"], "spot-1")
        self.assertEqual(final[1]["post_id_kind"], "spotlight_id")

    def test_a_job_not_yet_due_is_simply_not_claimed(self):
        """Timing is enforced by the claim query, so an empty claim means an
        empty pass — nothing is published early."""
        self.db.claim_due_snapchat_posts.return_value = []
        counts = snapchat_runner.process_due()
        self.assertEqual(counts["posted"], 0)
        self.publish.assert_not_called()


# ======================================================================
# 2. duplicate prevention
# ======================================================================
class TestDuplicatePrevention(SchedulerTestCase):
    def test_already_in_the_ledger_is_skipped_before_any_upload(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        self.db.already_posted_snapchat.return_value = True

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["skipped"], 1)
        self.publish.assert_not_called()
        self.drive.download_file.assert_not_called()
        self.assertEqual(self.db.mark_status.call_args[0][1], "skipped")

    def test_a_retry_of_an_already_published_job_does_not_publish_again(self):
        """The idempotency key finds the completed record, so a rerun after a
        crash between the publish and the status write cannot double-post."""
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        self.db.create_snapchat_publishing_record.return_value = (
            {"id": 900, "status": "published", "post_id": "spot-1", "media_id": "media-1"},
            False,
        )

        counts = snapchat_runner.process_due()

        self.publish.assert_not_called()
        self.assertEqual(counts["skipped"], 1)
        self.db.mark_status.assert_called_with(501, "posted", snap_post_id="spot-1")

    def test_the_same_video_may_go_to_spotlight_and_to_the_story(self):
        """Dedup is per (account, video, placement) — publishing to Spotlight
        must not block the Public Story of the same clip."""
        self.db.claim_due_snapchat_posts.return_value = [make_job(target_placement="story")]
        self.db.already_posted_snapchat.return_value = False
        self.publish.return_value = publish_result(post_id="story-1", placement="story")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["posted"], 1)
        self.assertEqual(self.db.already_posted_snapchat.call_args[0][2], "story")

    def test_publish_now_refuses_a_video_already_published(self):
        self.db.already_posted_snapchat.return_value = True
        with self.assertRaises(SnapMediaError):
            snapchat_runner.publish_now(
                account={"id": 1, "snapchat_username": "whalelife"},
                content_item={"id": 77, "drive_file_id": "d", "file_name": "a.mp4",
                              "source_video_id": "vid-123", "snapchat_account_id": 1},
                placement="spotlight",
            )
        self.publish.assert_not_called()

    def test_publish_now_is_idempotent_on_a_double_click(self):
        self.db.create_snapchat_publishing_record.return_value = (
            {"id": 900, "status": "published", "post_id": "spot-1"}, False,
        )
        result = snapchat_runner.publish_now(
            account={"id": 1, "snapchat_username": "whalelife"},
            content_item={"id": 77, "drive_file_id": "d", "file_name": "a.mp4",
                          "source_video_id": "vid-9", "snapchat_account_id": 1},
        )
        self.assertTrue(result["already_published"])
        self.publish.assert_not_called()


# ======================================================================
# 3. authorization failures pause, they do not consume the queue
# ======================================================================
class TestRevokedAuthorization(SchedulerTestCase):
    def test_revoked_token_releases_the_post_and_flags_the_account(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        self.publish.side_effect = SnapAuthError("Access revoked by the user")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["deferred"], 1)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["abandoned"], 0)
        # The post goes back to pending; no attempt is spent on it.
        self.db.release_snapchat_post.assert_called_once()
        self.assertEqual(self.db.release_snapchat_post.call_args[0][0], 501)
        self.db.update_snapchat_account_health.assert_called_with(
            1, "invalid", "Access revoked by the user",
        )
        for call in self.db.mark_status.call_args_list:
            self.assertNotIn(call[0][1], ("failed", "abandoned"))

    def test_one_dead_account_does_not_drag_its_whole_queue_through_uploads(self):
        self.db.claim_due_snapchat_posts.return_value = [
            make_job(id=501), make_job(id=502), make_job(id=503),
        ]
        self.publish.side_effect = SnapAuthError("token revoked")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["deferred"], 3)
        # Only the first job attempted a publish; the breaker caught the rest.
        self.assertEqual(self.publish.call_count, 1)

    def test_a_broken_account_does_not_stop_a_healthy_one(self):
        good = make_job(id=601, snapchat_account_id=2, content_account_id=2,
                        snapchat_username="reefdaily", source_video_id="vid-777")
        self.db.claim_due_snapchat_posts.return_value = [make_job(id=501), good]

        def publish_side_effect(**kwargs):
            if kwargs["account"]["snapchat_account_id"] == 1:
                raise SnapAuthError("token revoked")
            return publish_result(post_id="spot-2")

        self.publish.side_effect = publish_side_effect

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["deferred"], 1)
        self.assertEqual(counts["posted"], 1)
        self.db.mark_status.assert_any_call(
            601, "posted", snap_media_id="media-1", snap_post_id="spot-2",
        )

    def test_a_job_pointing_at_another_accounts_content_is_refused(self):
        """Account 1's credentials must never publish account 2's content.

        The job is abandoned rather than deferred, and the account keeps
        working: one malformed row should not take a healthy connection
        offline.
        """
        self.db.claim_due_snapchat_posts.return_value = [
            make_job(snapchat_account_id=1, content_account_id=2)
        ]
        counts = snapchat_runner.process_due()

        self.assertEqual(counts["abandoned"], 1)
        self.publish.assert_not_called()
        self.db.update_snapchat_account_health.assert_not_called()
        self.assertIn("belongs to account 2", self.db.mark_status.call_args[1]["error"])

    def test_unowned_page_content_is_allowed_to_cross_post(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job(content_account_id=None)]
        counts = snapchat_runner.process_due()
        self.assertEqual(counts["posted"], 1)


# ======================================================================
# 4. retries and giving up
# ======================================================================
class TestRetryPolicy(SchedulerTestCase):
    def test_transient_failure_is_marked_failed_with_a_retry_time(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job(attempts=0)]
        self.publish.side_effect = SnapTransientError("Snapchat 502")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["failed"], 1)
        self.db.mark_status.assert_called_with(501, "failed", error="Snapchat 502")
        record_update = self.db.update_snapchat_publishing_record.call_args
        self.assertEqual(record_update[1]["status"], "failed")
        self.assertIsNotNone(record_update[1]["next_retry_at"])

    def test_bad_media_fails_permanently_on_the_first_attempt(self):
        """Retrying uploads the same rejected bytes, so it is abandoned at
        once with the reason a creator can act on."""
        self.db.claim_due_snapchat_posts.return_value = [make_job(attempts=0)]
        self.publish.side_effect = SnapMediaError("Video is 3.40s. Spotlight requires at least 6s.")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["abandoned"], 1)
        self.assertEqual(self.db.mark_status.call_args[0][1], "abandoned")
        self.assertIn("3.40s", self.db.mark_status.call_args[1]["error"])

    def test_the_last_attempt_abandons_instead_of_looping_forever(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job(attempts=2)]
        self.publish.side_effect = SnapTransientError("still failing")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["abandoned"], 1)
        self.assertEqual(self.db.mark_status.call_args[0][1], "abandoned")

    def test_backoff_grows_with_each_attempt(self):
        first = snapchat_runner._retry_delay(1)
        second = snapchat_runner._retry_delay(2)
        self.assertGreater(second, first)
        self.assertLessEqual(snapchat_runner._retry_delay(99).total_seconds(), 6 * 3600)

    def test_a_drive_failure_is_treated_as_transient(self):
        self.db.claim_due_snapchat_posts.return_value = [make_job()]
        self.drive.download_file.side_effect = OSError("Drive read timed out")

        counts = snapchat_runner.process_due()

        self.assertEqual(counts["failed"], 1)
        self.publish.assert_not_called()

    def test_the_retry_pass_recovers_a_cooled_down_failure(self):
        self.db.retryable_failed_snapchat_posts.return_value = [{"id": 501, "snapchat_account_id": 1}]
        self.db.claim_snapchat_post.return_value = make_job(status="posting", attempts=1)

        counts = snapchat_runner.process_retries()

        self.assertEqual(counts["posted"], 1)
        self.db.claim_snapchat_post.assert_called_once_with(501, from_statuses=("failed",))

    def test_the_retry_pass_skips_a_row_another_worker_already_took(self):
        self.db.retryable_failed_snapchat_posts.return_value = [{"id": 501, "snapchat_account_id": 1}]
        self.db.claim_snapchat_post.return_value = None   # lost the race

        counts = snapchat_runner.process_retries()

        self.assertEqual(sum(counts.values()), 0)
        self.publish.assert_not_called()

    def test_manual_retry_clears_the_attempt_history_then_republishes(self):
        self.db.get_scheduled_post.return_value = {"id": 501, "platform": "snapchat",
                                                   "status": "abandoned"}
        self.db.claim_snapchat_post.return_value = make_job()

        outcome, err = snapchat_runner.retry_one(501)

        self.assertIsNone(err)
        self.assertEqual(outcome, "posted")
        self.db.reset_snapchat_post_for_retry.assert_called_once_with(501)

    def test_manual_retry_refuses_an_already_published_post(self):
        self.db.get_scheduled_post.return_value = {"id": 501, "platform": "snapchat",
                                                   "status": "posted"}
        outcome, err = snapchat_runner.retry_one(501)
        self.assertIsNone(outcome)
        self.assertIn("already published", err)

    def test_manual_retry_refuses_a_facebook_post(self):
        self.db.get_scheduled_post.return_value = {"id": 9, "platform": "facebook",
                                                   "status": "failed"}
        outcome, err = snapchat_runner.retry_one(9)
        self.assertIsNone(outcome)
        self.assertIn("not a Snapchat post", err)


# ======================================================================
# 5. crash recovery and time limits
# ======================================================================
class TestCrashAndTimeLimits(SchedulerTestCase):
    def test_the_retry_pass_reclaims_records_stuck_mid_publish(self):
        self.db.heal_stuck_snapchat_records.return_value = 2
        snapchat_runner.process_retries()
        self.db.heal_stuck_snapchat_records.assert_called_once()

    def test_a_serverless_run_out_of_time_releases_the_rest_of_the_batch(self):
        """Vercel kills the function at its timeout. Releasing the unstarted
        jobs leaves them pending for the next run rather than stuck in
        'posting' waiting for the stuck-row sweep."""
        self.db.claim_due_snapchat_posts.return_value = [make_job(id=501), make_job(id=502)]
        budget = iter([30, -1, -1])

        counts = snapchat_runner.process_due(remaining=lambda: next(budget))

        self.assertEqual(counts["posted"], 1)
        self.assertEqual(counts["deferred"], 1)
        self.db.release_snapchat_post.assert_called_once_with(502, None)


# ======================================================================
# 6. the API never returns a credential
# ======================================================================
class TestApiSurface(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        import app as app_module

        self.app_module = app_module
        self.db = MagicMock()
        p = patch.object(app_module, "db", self.db)
        p.start()
        self.addCleanup(p.stop)
        self.client = TestClient(app_module.app)

    def _account_row(self, **overrides):
        row = {
            "id": 1,
            "name": "Whale Life",
            "snapchat_username": "whalelife",
            "public_profile_id": "profile-aaa",
            "connection_status": "connected",
            "token_status": "valid",
            "default_placement": "spotlight",
            "access_token_encrypted": "ENCRYPTED-ACCESS-TOKEN",
            "refresh_token_encrypted": "ENCRYPTED-REFRESH-TOKEN",
            "session_data": {"cookie": "legacy"},
            "token_expires_at": dt.datetime.now(dt.timezone.utc),
        }
        row.update(overrides)
        return row

    def test_account_listing_strips_every_credential_column(self):
        self.db.list_snapchat_accounts.return_value = [self._account_row()]
        body = self.client.get("/api/snapchat/accounts").json()

        self.assertEqual(len(body), 1)
        account = body[0]
        for column in ("access_token_encrypted", "refresh_token_encrypted", "session_data"):
            self.assertNotIn(column, account)
        self.assertNotIn("ENCRYPTED-ACCESS-TOKEN", str(body))
        # The useful facts survive the stripping.
        self.assertTrue(account["is_connected"])
        self.assertTrue(account["can_auto_refresh"])

    def test_single_account_endpoint_strips_them_too(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        body = self.client.get("/api/snapchat/accounts/1").json()
        self.assertNotIn("ENCRYPTED-REFRESH-TOKEN", str(body))
        self.assertNotIn("access_token_encrypted", body)

    def test_disconnected_account_reports_no_credentials(self):
        self.db.list_snapchat_accounts.return_value = [
            self._account_row(access_token_encrypted=None, refresh_token_encrypted=None,
                              connection_status="disconnected")
        ]
        account = self.client.get("/api/snapchat/accounts").json()[0]
        self.assertFalse(account["is_connected"])
        self.assertEqual(account["connection_status"], "disconnected")

    def test_config_endpoint_reports_readiness_without_the_secret(self):
        with patch.dict(os.environ, {"SNAPCHAT_CLIENT_SECRET": "the-real-secret-value"}):
            body = self.client.get("/api/snapchat/config").json()
        self.assertTrue(body["client_secret_configured"])
        self.assertNotIn("the-real-secret-value", str(body))
        self.assertEqual(body["scope"], "snapchat-profile-api")
        self.assertEqual(body["spotlight_seconds"], [6.0, 60.0])
        self.assertEqual(body["story_seconds"], [5.0, 60.0])
        self.assertEqual(body["min_resolution"], [540, 960])

    def test_authorize_stores_the_state_it_hands_out(self):
        self.db.purge_expired_oauth_states.return_value = 0
        body = self.client.get("/api/snapchat/oauth/authorize").json()

        self.assertIn("accounts.snapchat.com", body["authorization_url"])
        self.db.save_oauth_state.assert_called_once()
        self.assertEqual(self.db.save_oauth_state.call_args[0][0], body["state"])

    def test_callback_with_an_unknown_state_never_exchanges_the_code(self):
        self.db.consume_oauth_state.return_value = None
        with patch.object(self.app_module.snapchat_oauth, "exchange_code_for_tokens") as mock_ex:
            resp = self.client.get(
                "/api/snapchat/oauth/callback?code=stolen&state=forged",
                follow_redirects=False,
            )
        mock_ex.assert_not_called()
        self.assertEqual(resp.status_code, 307)
        self.assertIn("snap_error", resp.headers["location"])

    def test_callback_consumes_the_state_before_exchanging(self):
        self.db.consume_oauth_state.return_value = {"redirect_uri": "https://x/cb"}
        self.db.upsert_snapchat_oauth_account.return_value = {"id": 4}

        with patch.object(self.app_module.snapchat_oauth, "exchange_code_for_tokens") as mock_ex:
            mock_ex.return_value = {
                "access_token": "at", "access_token_encrypted": "enc-at",
                "refresh_token_encrypted": "enc-rt",
                "expires_at": dt.datetime.now(dt.timezone.utc), "scope": "snapchat-profile-api",
            }
            with patch.object(self.app_module.snapchat_api, "get_my_profile") as mock_profile:
                mock_profile.return_value = {
                    "id": "profile-aaa", "display_name": "Whale Life",
                    "snap_user_name": "whalelife", "organization_id": "org-1",
                    "profile_type": "PUBLIC_PROFILE",
                }
                resp = self.client.get(
                    "/api/snapchat/oauth/callback?code=abc&state=good",
                    follow_redirects=False,
                )

        self.db.consume_oauth_state.assert_called_once_with("good")
        # The exchange must reuse the redirect URI the state was issued for.
        self.assertEqual(mock_ex.call_args[1]["redirect_uri"], "https://x/cb")
        saved = self.db.upsert_snapchat_oauth_account.call_args[1]
        self.assertEqual(saved["public_profile_id"], "profile-aaa")
        self.assertEqual(saved["snapchat_username"], "whalelife")
        self.assertIn("snap_connected=1", resp.headers["location"])

    def test_callback_reports_a_denied_consent_without_touching_the_database(self):
        resp = self.client.get(
            "/api/snapchat/oauth/callback?error=access_denied&error_description=User+declined",
            follow_redirects=False,
        )
        self.db.consume_oauth_state.assert_not_called()
        self.assertIn("snap_error", resp.headers["location"])

    def test_job_creation_rejects_content_owned_by_another_account(self):
        self.db.get_snapchat_account.return_value = self._account_row(id=1)
        self.db.get_content_item.return_value = {
            "id": 77, "snapchat_account_id": 2, "caption": "x",
            "hashtags": [], "source_video_id": "vid-1",
        }
        resp = self.client.post("/api/snapchat/jobs", json={
            "snapchat_account_id": 1,
            "content_item_id": 77,
            "scheduled_time_utc": "2026-10-01T10:00:00Z",
            "placement": "spotlight",
        })
        self.assertEqual(resp.status_code, 403)
        self.db.enqueue_snapchat.assert_not_called()

    def test_job_creation_rejects_a_disconnected_account(self):
        self.db.get_snapchat_account.return_value = self._account_row(
            access_token_encrypted=None
        )
        resp = self.client.post("/api/snapchat/jobs", json={
            "snapchat_account_id": 1, "content_item_id": 77,
            "scheduled_time_utc": "2026-10-01T10:00:00Z",
        })
        self.assertEqual(resp.status_code, 400)
        self.assertIn("not connected", resp.json()["detail"])

    def test_job_creation_rejects_a_duplicate(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        self.db.get_content_item.return_value = {
            "id": 77, "snapchat_account_id": 1, "caption": "x",
            "hashtags": [], "source_video_id": "vid-1",
        }
        self.db.already_posted_snapchat.return_value = True
        resp = self.client.post("/api/snapchat/jobs", json={
            "snapchat_account_id": 1, "content_item_id": 77,
            "scheduled_time_utc": "2026-10-01T10:00:00Z",
        })
        self.assertEqual(resp.status_code, 409)

    def test_job_creation_rejects_an_unknown_placement(self):
        resp = self.client.post("/api/snapchat/jobs", json={
            "snapchat_account_id": 1, "content_item_id": 77,
            "scheduled_time_utc": "2026-10-01T10:00:00Z",
            "placement": "saved_story",
        })
        self.assertEqual(resp.status_code, 400)

    def test_disconnect_touches_only_the_named_account(self):
        self.db.get_snapchat_account.return_value = self._account_row(id=2)
        self.db.disconnect_snapchat_account.return_value = self._account_row(
            id=2, access_token_encrypted=None, refresh_token_encrypted=None,
            connection_status="disconnected",
        )
        body = self.client.post("/api/snapchat/accounts/2/disconnect").json()

        self.db.disconnect_snapchat_account.assert_called_once_with(2)
        self.assertTrue(body["disconnected"])
        self.assertFalse(body["account"]["is_connected"])

    def test_refresh_token_endpoint_returns_status_not_a_token(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        with patch.object(self.app_module.snapchat_runner, "refresh_account_token") as mock_refresh:
            mock_refresh.return_value = {"refreshed": True}
            body = self.client.post("/api/snapchat/accounts/1/refresh-token").json()
        self.assertTrue(body["success"])
        self.assertNotIn("access_token", body)
        self.assertNotIn("ENCRYPTED-ACCESS-TOKEN", str(body))

    def test_a_revoked_account_surfaces_as_401_not_500(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        with patch.object(self.app_module.snapchat_runner, "refresh_account_token") as mock_refresh:
            mock_refresh.side_effect = SnapAuthError("Refresh token revoked")
            resp = self.client.post("/api/snapchat/accounts/1/refresh-token")
        self.assertEqual(resp.status_code, 401)

    def test_bad_media_surfaces_as_422_with_the_reason(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        self.db.get_content_item.return_value = {"id": 77, "snapchat_account_id": 1,
                                                 "drive_file_id": "d", "file_name": "a.mp4"}
        with patch.object(self.app_module.snapchat_runner, "publish_now") as mock_pub:
            mock_pub.side_effect = SnapMediaError("Video is 3.40s. Spotlight requires at least 6s.")
            resp = self.client.post("/api/snapchat/accounts/1/publish",
                                    json={"content_item_id": 77, "placement": "spotlight"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("3.40s", resp.json()["detail"])

    def test_a_snap_outage_surfaces_as_503(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        self.db.get_content_item.return_value = {"id": 77, "snapchat_account_id": 1,
                                                 "drive_file_id": "d", "file_name": "a.mp4"}
        with patch.object(self.app_module.snapchat_runner, "publish_now") as mock_pub:
            mock_pub.side_effect = SnapTransientError("Snapchat is unavailable")
            resp = self.client.post("/api/snapchat/accounts/1/publish",
                                    json={"content_item_id": 77})
        self.assertEqual(resp.status_code, 503)

    def test_an_api_rejection_surfaces_as_400(self):
        self.db.get_snapchat_account.return_value = self._account_row()
        self.db.get_content_item.return_value = {"id": 77, "snapchat_account_id": 1,
                                                 "drive_file_id": "d", "file_name": "a.mp4"}
        with patch.object(self.app_module.snapchat_runner, "publish_now") as mock_pub:
            mock_pub.side_effect = SnapApiError("Spotlight rejected")
            resp = self.client.post("/api/snapchat/accounts/1/publish",
                                    json={"content_item_id": 77})
        self.assertEqual(resp.status_code, 400)

    def test_missing_account_is_404_everywhere(self):
        self.db.get_snapchat_account.return_value = None
        for path, method in (
            ("/api/snapchat/accounts/99", "get"),
            ("/api/snapchat/accounts/99/health", "get"),
            ("/api/snapchat/accounts/99/refresh-token", "post"),
            ("/api/snapchat/accounts/99/disconnect", "post"),
            ("/api/snapchat/accounts/99", "delete"),
        ):
            resp = getattr(self.client, method)(path)
            self.assertEqual(resp.status_code, 404, path)


class TestMultipleAccounts(unittest.TestCase):
    """Each connected profile keeps its own credentials and its own queue."""

    def test_accounts_carry_independent_credentials(self):
        import snapchat_crypto

        a = {"id": 1, "public_profile_id": "profile-a",
             "access_token_encrypted": snapchat_crypto.encrypt_token("token-a"),
             "refresh_token_encrypted": snapchat_crypto.encrypt_token("refresh-a"),
             "token_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}
        b = {"id": 2, "public_profile_id": "profile-b",
             "access_token_encrypted": snapchat_crypto.encrypt_token("token-b"),
             "refresh_token_encrypted": snapchat_crypto.encrypt_token("refresh-b"),
             "token_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)}

        import snapchat_oauth
        self.assertEqual(snapchat_oauth.ensure_valid_token(a), "token-a")
        self.assertEqual(snapchat_oauth.ensure_valid_token(b), "token-b")
        self.assertNotEqual(a["access_token_encrypted"], b["access_token_encrypted"])

    def test_a_token_may_not_be_aimed_at_another_accounts_profile(self):
        import snapchat_poster
        account_b = {"id": 2, "public_profile_id": "profile-b", "name": "B"}
        with patch.object(snapchat_poster.snapchat_api, "get_my_profile",
                          return_value={"id": "profile-a"}):
            with self.assertRaises(SnapAuthError):
                snapchat_poster.resolve_profile_id(account_b, "token-a")


if __name__ == "__main__":
    unittest.main(verbosity=2)
