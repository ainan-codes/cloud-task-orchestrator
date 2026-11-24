"""
backend/tests/test_state_machine_sync.py — State Machine Synchronization Regression Tests.

Validates the required behavior specified in Priority #1:
  A. Publication fails permanently -> job is NOT completed (moves to dead_letter)
  B. Publication retries (transient failure) -> job is retrying (NOT completed)
  C. Publication auth failure -> job is blocked (NOT completed, attempts not exhausted)
  D. Publication succeeds -> job becomes completed
"""

import sys
import os
import unittest
import json
import time
import datetime as dt
from unittest.mock import patch

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import db
from core.jobs import DurableJobService
from core.orchestrator import PublishingOrchestrator


class TestStateMachineSync(unittest.TestCase):

    created_pub_ids = []

    @classmethod
    def setUpClass(cls):
        with db._conn() as conn:
            with conn.cursor() as cur:
                # 1. Look up existing Facebook platform account or insert one
                cur.execute("SELECT id FROM platform_accounts WHERE platform = 'facebook' LIMIT 1;")
                acc = cur.fetchone()
                if acc:
                    cls.platform_account_id = acc["id"]
                else:
                    cur.execute("""
                        INSERT INTO platform_accounts (
                            brand_id, platform, account_type, display_name, external_account_id, health_status
                        ) VALUES (1, 'facebook', 'page', 'Sync Test Account', 'fb_test_page_9999', 'healthy')
                        RETURNING id;
                    """)
                    cls.platform_account_id = cur.fetchone()["id"]

                # 2. Always create a dedicated unposted content item for isolated test runs
                cur.execute("SELECT id FROM pages LIMIT 1;")
                p = cur.fetchone()
                page_id = p["id"] if p else 3
                test_src_id = f"sync_test_asset_{int(dt.datetime.now().timestamp())}"
                cur.execute("""
                    INSERT INTO content_items (
                        page_id, brand_id, media_type, source_video_id, drive_file_id,
                        file_name, caption, validation_status
                    ) VALUES (
                        %s, 1, 'photo', %s, 'drive_sync_test_9999',
                        'test_image.jpg', 'State machine sync test', 'ready'
                    ) RETURNING id;
                """, (page_id, test_src_id))
                cls.content_item_id = cur.fetchone()["id"]
            conn.commit()

    @classmethod
    def tearDownClass(cls):
        with db._conn() as conn:
            with conn.cursor() as cur:
                if cls.created_pub_ids:
                    cur.execute("DELETE FROM dead_letter_jobs WHERE publication_id = ANY(%s);", (cls.created_pub_ids,))
                    cur.execute("DELETE FROM publication_attempts WHERE publication_id = ANY(%s);", (cls.created_pub_ids,))
                    cur.execute("DELETE FROM publications WHERE id = ANY(%s);", (cls.created_pub_ids,))
                cur.execute("DELETE FROM durable_jobs WHERE job_type LIKE 'sync_regress_%';")
                if hasattr(cls, "content_item_id") and cls.content_item_id:
                    cur.execute("DELETE FROM content_items WHERE id = %s;", (cls.content_item_id,))
            conn.commit()
        if hasattr(db, "_pool") and db._pool:
            try:
                db._pool.close()
            except Exception:
                pass

    def test_case_a_permanent_failure_moves_job_to_dead_letter(self):
        """
        Case A: When publication fails permanently (e.g., validation/unsupported capability),
        publication -> 'failed', durable_job -> 'dead_letter' (NEVER 'completed').
        """
        ts = int(time.time() * 1000)
        job_type = f"sync_regress_perm_{ts}"

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO publications (
                        platform_account_id, content_item_id, content_type,
                        title, caption, status, scheduled_at, idempotency_key, created_at, updated_at
                    ) VALUES (
                        %s, %s, 'unsupported_type', 'Test Title', 'Test Caption',
                        'queued', NOW(), %s, NOW(), NOW()
                    ) RETURNING id;
                """, (self.platform_account_id, self.content_item_id, f"perm_fail_key_{ts}"))
                pub_id = cur.fetchone()["id"]
                self.created_pub_ids.append(pub_id)
            conn.commit()

        # Enqueue and lease job
        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"publication_id": pub_id},
            priority=1,
            idempotency_key=f"perm_fail_job_{ts}"
        )
        claimed = DurableJobService.claim(worker_id="test-worker", job_types=[job_type], lease_seconds=60)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["status"], "leased")

        # Execute publication
        res = PublishingOrchestrator.execute_publication(pub_id, job_id=job["id"])
        self.assertFalse(res["success"])
        self.assertEqual(res["status"], "failed")

        # Verify publication and durable_job state
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, error_kind FROM publications WHERE id = %s;", (pub_id,))
                pub_row = cur.fetchone()
                self.assertEqual(pub_row["status"], "failed")

                cur.execute("SELECT status, last_error FROM durable_jobs WHERE id = %s;", (job["id"],))
                job_row = cur.fetchone()
                # Must be 'dead_letter', NEVER 'completed'
                self.assertEqual(job_row["status"], "dead_letter")
                self.assertIsNotNone(job_row["last_error"])

                # Attempting to call complete() on this job must fail and not change status
                completed_override = DurableJobService.complete(job["id"])
                self.assertFalse(completed_override)

                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job["id"],))
                self.assertEqual(cur.fetchone()["status"], "dead_letter")

    def test_case_b_transient_failure_moves_job_to_retrying(self):
        """
        Case B: When publication encounters a transient failure (e.g., network timeout / drive fetch retry),
        publication -> 'retrying', durable_job -> 'retrying' (NEVER 'completed').
        """
        ts = int(time.time() * 1000)
        job_type = f"sync_regress_trans_{ts}"

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO publications (
                        platform_account_id, content_item_id, content_type,
                        title, caption, status, scheduled_at, idempotency_key, created_at, updated_at
                    ) VALUES (
                        %s, %s, 'photo', 'Test Photo', 'Test Caption',
                        'queued', NOW(), %s, NOW(), NOW()
                    ) RETURNING id;
                """, (self.platform_account_id, self.content_item_id, f"trans_fail_key_{ts}"))
                pub_id = cur.fetchone()["id"]
                self.created_pub_ids.append(pub_id)
            conn.commit()

        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"publication_id": pub_id},
            priority=1,
            idempotency_key=f"trans_fail_job_{ts}"
        )
        claimed = DurableJobService.claim(worker_id="test-worker", job_types=[job_type], lease_seconds=60)
        self.assertIsNotNone(claimed)

        # Mock adapter credential validation to pass, but drive download to throw a transient network error
        with patch("core.adapters.facebook.FacebookAdapter.validate_credentials", return_value={"is_valid": True}), \
             patch("core.orchestrator.drive.download_file", side_effect=ConnectionResetError("Remote server closed connection [WinError 10054]")):

            res = PublishingOrchestrator.execute_publication(pub_id, job_id=job["id"])
            self.assertFalse(res["success"])
            self.assertEqual(res["status"], "retrying")
            self.assertEqual(res["error_kind"], "transient")

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, error_kind FROM publications WHERE id = %s;", (pub_id,))
                pub_row = cur.fetchone()
                self.assertEqual(pub_row["status"], "retrying")
                self.assertEqual(pub_row["error_kind"], "transient")

                cur.execute("SELECT status, available_at FROM durable_jobs WHERE id = %s;", (job["id"],))
                job_row = cur.fetchone()
                # Must be 'retrying', NEVER 'completed'
                self.assertEqual(job_row["status"], "retrying")
                # Backoff scheduled in the future
                self.assertGreater(job_row["available_at"], dt.datetime.now(dt.timezone.utc))

                # Attempting to call complete() must NOT succeed
                completed_override = DurableJobService.complete(job["id"])
                self.assertFalse(completed_override)

                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job["id"],))
                self.assertEqual(cur.fetchone()["status"], "retrying")

    def test_case_c_auth_failure_moves_job_to_blocked(self):
        """
        Case C: When publication encounters an auth / token failure,
        publication -> 'reauth_required', durable_job -> 'blocked' (NEVER 'completed').
        """
        ts = int(time.time() * 1000)
        job_type = f"sync_regress_auth_{ts}"

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO publications (
                        platform_account_id, content_item_id, content_type,
                        title, caption, status, scheduled_at, idempotency_key, created_at, updated_at
                    ) VALUES (
                        %s, %s, 'photo', 'Test Photo', 'Test Caption',
                        'queued', NOW(), %s, NOW(), NOW()
                    ) RETURNING id;
                """, (self.platform_account_id, self.content_item_id, f"auth_fail_key_{ts}"))
                pub_id = cur.fetchone()["id"]
                self.created_pub_ids.append(pub_id)
            conn.commit()

        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"publication_id": pub_id},
            priority=1,
            idempotency_key=f"auth_fail_job_{ts}"
        )
        claimed = DurableJobService.claim(worker_id="test-worker", job_types=[job_type], lease_seconds=60)
        self.assertIsNotNone(claimed)

        # Mock adapter credential validation to return reauth_required
        with patch("core.adapters.facebook.FacebookAdapter.validate_credentials", return_value={
            "is_valid": False, "status": "reauth_required", "error": "OAuthException: Session expired (code 190)"
        }):
            res = PublishingOrchestrator.execute_publication(pub_id, job_id=job["id"])
            self.assertFalse(res["success"])
            self.assertEqual(res["status"], "reauth_required")
            self.assertEqual(res["error_kind"], "authentication")

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, error_kind FROM publications WHERE id = %s;", (pub_id,))
                pub_row = cur.fetchone()
                self.assertEqual(pub_row["status"], "reauth_required")
                self.assertEqual(pub_row["error_kind"], "authentication")

                cur.execute("SELECT status, attempt_count FROM durable_jobs WHERE id = %s;", (job["id"],))
                job_row = cur.fetchone()
                # Must be 'blocked'
                self.assertEqual(job_row["status"], "blocked")

                # Complete cannot override blocked
                completed_override = DurableJobService.complete(job["id"])
                self.assertFalse(completed_override)

                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job["id"],))
                self.assertEqual(cur.fetchone()["status"], "blocked")

    def test_case_d_successful_publication_marks_job_completed(self):
        """
        Case D: When publication actually succeeds,
        publication -> 'published', durable_job -> 'completed'.
        """
        ts = int(time.time() * 1000)
        job_type = f"sync_regress_succ_{ts}"

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO publications (
                        platform_account_id, content_item_id, content_type,
                        title, caption, status, scheduled_at, idempotency_key, created_at, updated_at
                    ) VALUES (
                        %s, %s, 'photo', 'Success Photo', 'Success Caption',
                        'queued', NOW(), %s, NOW(), NOW()
                    ) RETURNING id;
                """, (self.platform_account_id, self.content_item_id, f"succ_key_{ts}"))
                pub_id = cur.fetchone()["id"]
                self.created_pub_ids.append(pub_id)
            conn.commit()

        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"publication_id": pub_id},
            priority=1,
            idempotency_key=f"succ_job_{ts}"
        )
        claimed = DurableJobService.claim(worker_id="test-worker", job_types=[job_type], lease_seconds=60)
        self.assertIsNotNone(claimed)

        # Mock adapter credential validation and publish_photo to return successful post ID
        dummy_jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00\xff\xdb\x00C\x00" + b"\x00" * 64 + b"\xff\xc0\x00\x0b\x08\x00\x0a\x00\x0a\x01\x01\x11\x00\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        with patch("core.adapters.facebook.FacebookAdapter.validate_credentials", return_value={"is_valid": True}), \
             patch("core.orchestrator.drive.download_file", return_value=dummy_jpeg), \
             patch("core.orchestrator.validate_media", return_value={"is_valid": True, "mime_type": "image/jpeg"}), \
             patch("core.adapters.facebook.FacebookAdapter.publish_photo", return_value={"external_post_id": f"fb_post_succ_{ts}"}):

            res = PublishingOrchestrator.execute_publication(pub_id, job_id=job["id"])
            self.assertTrue(res["success"])
            self.assertEqual(res["status"], "published")
            self.assertEqual(res["external_post_id"], f"fb_post_succ_{ts}")

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, external_post_id FROM publications WHERE id = %s;", (pub_id,))
                pub_row = cur.fetchone()
                self.assertEqual(pub_row["status"], "published")
                self.assertEqual(pub_row["external_post_id"], f"fb_post_succ_{ts}")

                cur.execute("SELECT status, completed_at FROM durable_jobs WHERE id = %s;", (job["id"],))
                job_row = cur.fetchone()
                # Must be 'completed'
                self.assertEqual(job_row["status"], "completed")
                self.assertIsNotNone(job_row["completed_at"])


if __name__ == "__main__":
    unittest.main()
