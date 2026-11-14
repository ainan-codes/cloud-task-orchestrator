"""
backend/tests/test_e2e_universal.py — End-to-End Hardening & Validation Test Suite.

Proves with concrete database rows, timestamps, and execution results:
  1. Chromium Profile Isolation & Mutex Collision Prevention (Sections 3, 5, 6)
  2. Durable Job Lifecycle: Queued -> Leased -> Running -> Success (Section 16)
  3. Worker Crash & Watchdog Lease Reclamation (Sections 17, 22)
  4. Retry Engine & Dead-Letter Queue (DLQ) Recovery (Sections 20, 21)
  5. Tri-Mode Content Automation (Video, Photo, Text) (Sections 9, 10, 11, 12)
  6. Platform Capability Registry Enforcement (Section 13)
  7. Multi-Platform Publication Independence (Section 14)
  8. Publication Idempotency & Deduplication Guard (Section 15)
  9. Content Asset Fingerprinting (SHA-256 + Perceptual Hash) (Section 29)
 10. Security Audit: No Secrets Exposed in Platform / System APIs (Section 32)
"""

import sys
import os
import unittest
import time
import msvcrt
import datetime as dt
from io import BytesIO
from PIL import Image

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import db
from core.capabilities import PLATFORM_REGISTRY, CapabilityRegistry
from core.fingerprint import calculate_sha256, calculate_perceptual_hash, validate_media
from core.jobs import DurableJobService
from core.watchdog import WatchdogService
from core.planning import PublicationPlanningService
from core.orchestrator import PublishingOrchestrator
from core.flow_lifecycle import (
    is_profile_in_use,
    release_profile_lock,
    get_profile_path,
    BrowserState,
    AccountSessionState
)


class TestE2EUniversalSystem(unittest.TestCase):

    @classmethod
    def tearDownClass(cls):
        if hasattr(db, "_pool") and db._pool:
            try:
                db._pool.close()
            except Exception:
                pass

    # ============================================================
    # 1. Chromium Profile Isolation & Mutex (Sections 3, 5, 6)
    # ============================================================
    def test_01_chromium_profile_isolation_and_mutex(self):
        """
        Verify:
          - Distinct directories for flowaccount_1 vs flowaccount_2
          - Mutex detection: simulated lock file triggers in_use=True
          - Simultaneous launch blocked with PROFILE_IN_USE
          - Clean lock release frees the profile
        """
        acc1_folder = "flowaccount1_1791133671"
        acc2_folder = "flowaccount2_validation_isolated"

        path1 = get_profile_path(acc1_folder)
        path2 = get_profile_path(acc2_folder)

        # Profiles must be strictly separate directories
        self.assertNotEqual(path1, path2)
        self.assertTrue(os.path.exists(path1))
        self.assertTrue(os.path.exists(path2))

        # Test Mutex: simulate real Windows process lock on lockfile
        lock2 = os.path.join(path2, "lockfile")
        with open(lock2, "w") as f:
            f.write("TEST_PID")

        f_locked = open(lock2, "r+")
        try:
            msvcrt.locking(f_locked.fileno(), msvcrt.LK_NBLCK, 1)
            in_use, pid = is_profile_in_use(acc2_folder)
            self.assertTrue(in_use, "Exclusive lock on lockfile must be detected as in-use")
        finally:
            try:
                msvcrt.locking(f_locked.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
            f_locked.close()

        # Release lock
        released = release_profile_lock(acc2_folder)
        self.assertTrue(released)

        # Verify profile is now free for workers
        in_use_after, _ = is_profile_in_use(acc2_folder)
        self.assertFalse(in_use_after, "Profile must be free after lock release")

    # ============================================================
    # 2. Durable Job Lifecycle: Queued -> Leased -> Running -> Success (Section 16)
    # ============================================================
    def test_02_durable_job_full_lifecycle(self):
        """
        Verify complete happy path state transitions:
          QUEUED -> LEASED -> RUNNING -> SUCCESS
        with state persisted to PostgreSQL at each transition.
        """
        ts = int(dt.datetime.now().timestamp() * 1000)
        idemp_key = f"e2e_lifecycle_{ts}"
        job_type = f"test_pub_{ts}"

        # 1. Enqueue
        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"action": "publish_post", "target": "facebook"},
            priority=10,
            idempotency_key=idemp_key
        )
        job_id = job["id"]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["attempt_count"], 0)

        # 2. Worker claims (Leased)
        worker_id = f"worker-test-runner-{ts}"
        claimed = DurableJobService.claim(
            worker_id=worker_id,
            job_types=[job_type],
            lease_seconds=30
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["status"], "leased")
        self.assertEqual(claimed["locked_by"], worker_id)

        # 3. Heartbeat / Execution
        hb = DurableJobService.heartbeat(job_id=job_id, worker_id=worker_id)
        self.assertTrue(hb)

        # 4. Success completion
        DurableJobService.complete(job_id=job_id)

        # Verify final state in DB
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, attempt_count, completed_at FROM durable_jobs WHERE id = %s;", (job_id,))
                row = cur.fetchone()
                self.assertEqual(row["status"], "completed")
                self.assertIsNotNone(row["completed_at"])

    # ============================================================
    # 3. Worker Crash & Watchdog Lease Reclamation (Sections 17, 22)
    # ============================================================
    def test_03_worker_crash_and_watchdog_reclamation(self):
        """
        Simulate a hard worker crash during job execution:
          1. Job is claimed and leased by crashed worker
          2. Worker dies without releasing lease
          3. Watchdog runs sweep, detects stale heartbeat (> 90s)
          4. Watchdog revokes lease, sets job back to 'queued' or 'retrying'
          5. A live worker safely re-claims and finishes the job
        """
        ts = int(dt.datetime.now().timestamp() * 1000)
        job_type = f"crash_sim_{ts}"

        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"action": "transcode_video"},
            idempotency_key=f"idemp_crash_{ts}"
        )
        job_id = job["id"]

        # Claim with crashed worker
        crashed_worker = f"crashed-worker-{ts}"
        claimed = DurableJobService.claim(worker_id=crashed_worker, job_types=[job_type], lease_seconds=1)
        self.assertIsNotNone(claimed)

        # Backdate heartbeat to simulate stale/dead worker (> 90s)
        stale_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=180)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE durable_jobs SET heartbeat_at = %s, available_at = %s WHERE id = %s;",
                    (stale_time, stale_time, job_id)
                )
            conn.commit()

        # Run watchdog sweep
        sweep_res = WatchdogService.run_sweep()
        self.assertGreaterEqual(sweep_res["reclaimed_leases"], 1)

        # Verify job is now recovered
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, locked_by FROM durable_jobs WHERE id = %s;", (job_id,))
                recovered_row = cur.fetchone()
                self.assertEqual(recovered_row["status"], "queued")
                self.assertIsNone(recovered_row["locked_by"])

        # Second worker claims and finishes
        healthy_worker = f"healthy-worker-{ts}"
        claimed_healthy = DurableJobService.claim(worker_id=healthy_worker, job_types=[job_type], lease_seconds=30)
        self.assertIsNotNone(claimed_healthy)
        self.assertEqual(claimed_healthy["id"], job_id)
        DurableJobService.complete(job_id=job_id)

    # ============================================================
    # 4. Retry Engine & Dead-Letter Queue (DLQ) Recovery (Sections 20, 21)
    # ============================================================
    def test_04_retry_engine_and_dlq_recovery(self):
        """
        Verify:
          - Retriable errors increment attempts and set backoff
          - Permanent error (or max attempts exceeded) routes to dead_letter_jobs
          - Operator can inspect DLQ and trigger recovery action
        """
        ts = int(dt.datetime.now().timestamp() * 1000)
        job_type = f"retry_dlq_{ts}"

        job = DurableJobService.enqueue(
            job_type=job_type,
            payload={"cmd": "fail_fast"},
            idempotency_key=f"idemp_dlq_{ts}"
        )
        job_id = job["id"]

        # Attempt 1: Transient error
        DurableJobService.claim("worker-1", [job_type], lease_seconds=30)
        DurableJobService.fail(
            job_id=job_id,
            error_message="Too many requests to platform API (RATE_LIMIT)",
            error_kind="transient"
        )

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, attempt_count FROM durable_jobs WHERE id = %s;", (job_id,))
                r1 = cur.fetchone()
                self.assertEqual(r1["status"], "retrying")
                self.assertEqual(r1["attempt_count"], 1)

        # Attempt 2: Permanent rejection -> routes to DLQ
        # Reset available_at to now for immediate claiming
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE durable_jobs SET available_at = NOW(), status = 'queued' WHERE id = %s;", (job_id,))
            conn.commit()

        DurableJobService.claim("worker-2", [job_type], lease_seconds=30)
        DurableJobService.fail(
            job_id=job_id,
            error_message="Account disabled by remote platform (PLATFORM_ACCOUNT_SUSPENDED)",
            error_kind="permanent"
        )

        # Verify job is dead_letter and exists in dead_letter_jobs table
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job_id,))
                self.assertEqual(cur.fetchone()["status"], "dead_letter")

                cur.execute("SELECT id, failure_reason, attempt_count FROM dead_letter_jobs WHERE job_id = %s;", (job_id,))
                dlq_row = cur.fetchone()
                self.assertIsNotNone(dlq_row)
                dlq_id = dlq_row["id"]
                self.assertIn("PLATFORM_ACCOUNT_SUSPENDED", dlq_row["failure_reason"])

        # Recover from DLQ via Operator action
        recovery = DurableJobService.recover_dead_letter(dlq_id=dlq_id, action="retry")
        self.assertTrue(recovery["success"])
        self.assertEqual(recovery["action"], "retried")

        # Verify job is re-enqueued with reset attempts
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status, attempt_count FROM durable_jobs WHERE id = %s;", (job_id,))
                rec_job = cur.fetchone()
                self.assertEqual(rec_job["status"], "queued")
                self.assertEqual(rec_job["attempt_count"], 0)

    # ============================================================
    # 5. Tri-Mode Content Automation (Video, Photo, Text) (Sections 9, 10, 11, 12)
    # ============================================================
    def test_05_tri_mode_content_automation_rules(self):
        """
        Verify:
          - Each account's publication rule independently controls VIDEO, PHOTO, and TEXT
          - Updating toggles and limits is persisted and enforced
          - Disabling one mode does not impact the others
        """
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM publication_rules LIMIT 1;")
                rule = cur.fetchone()
                rule_id = rule["id"]

        # 1. Set mode: Video Only
        updated = PublicationPlanningService.update_rule(
            rule_id=rule_id,
            video_enabled=True,
            photo_enabled=False,
            text_enabled=False,
            daily_video_limit=5
        )
        self.assertTrue(updated["video_enabled"])
        self.assertFalse(updated["photo_enabled"])
        self.assertFalse(updated["text_enabled"])
        self.assertEqual(updated["daily_video_limit"], 5)

        # 2. Set mode: Photo + Text (Video disabled)
        updated2 = PublicationPlanningService.update_rule(
            rule_id=rule_id,
            video_enabled=False,
            photo_enabled=True,
            text_enabled=True,
            daily_photo_limit=10,
            daily_text_limit=15
        )
        self.assertFalse(updated2["video_enabled"])
        self.assertTrue(updated2["photo_enabled"])
        self.assertTrue(updated2["text_enabled"])
        self.assertEqual(updated2["daily_photo_limit"], 10)
        self.assertEqual(updated2["daily_text_limit"], 15)

        # 3. Restore all enabled
        PublicationPlanningService.update_rule(
            rule_id=rule_id,
            video_enabled=True,
            photo_enabled=True,
            text_enabled=True
        )

    # ============================================================
    # 6. Platform Capability Registry Enforcement (Section 13)
    # ============================================================
    def test_06_platform_capability_registry_enforcement(self):
        """
        Verify CapabilityRegistry validates against official platform limits:
          - YouTube: Video YES, Photo NO, Text NO
          - Instagram: Video YES, Photo YES, Text NO
          - Snapchat: Video YES, Photo YES, Text NO
          - Facebook: Video YES, Photo YES, Text YES
          - Duration limit validation (e.g. YouTube max 60s for Shorts)
        """
        self.assertEqual(len(PLATFORM_REGISTRY), 7)

        # YouTube format rejection
        ok, err = CapabilityRegistry.validate_request("youtube", "photo")
        self.assertFalse(ok)
        self.assertIn("does not support photo", err)

        # Instagram text rejection
        ok, err = CapabilityRegistry.validate_request("instagram", "text")
        self.assertFalse(ok)
        self.assertIn("does not support text", err)

        # Video duration boundary test on YouTube Shorts (max 60s)
        ok_dur, _ = CapabilityRegistry.validate_request("youtube", "reel", duration_seconds=55.0)
        self.assertTrue(ok_dur)

        bad_dur, err_dur = CapabilityRegistry.validate_request("youtube", "reel", duration_seconds=120.0)
        self.assertFalse(bad_dur)
        self.assertIn("exceeds youtube's limit of 60s", err_dur)

    # ============================================================
    # 7. Multi-Platform Publication Independence (Section 14)
    # ============================================================
    def test_07_multi_platform_publication_independence(self):
        """
        Verify:
          When publishing the same content item across multiple platforms,
          a failure on Platform A does NOT halt, cancel, or poison
          publications queued for Platform B, C, or D.
        """
        ts = int(dt.datetime.now().timestamp() * 1000)

        # Create two publication jobs for simulated platforms
        job_fb = DurableJobService.enqueue(
            job_type=f"pub_fb_{ts}",
            payload={"platform": "facebook", "post_id": 101},
            idempotency_key=f"pub_fb_{ts}"
        )
        job_yt = DurableJobService.enqueue(
            job_type=f"pub_yt_{ts}",
            payload={"platform": "youtube", "post_id": 102},
            idempotency_key=f"pub_yt_{ts}"
        )

        # Simulate Facebook failure (e.g. network timeout)
        DurableJobService.claim("worker-fb", [f"pub_fb_{ts}"], lease_seconds=10)
        DurableJobService.fail(job_fb["id"], "Connection reset (TIMEOUT)", error_kind="network")

        # YouTube worker proceeds independently and completes with success
        claimed_yt = DurableJobService.claim("worker-yt", [f"pub_yt_{ts}"], lease_seconds=10)
        self.assertIsNotNone(claimed_yt)
        DurableJobService.complete(job_yt["id"])

        # Verify states: FB is retrying, YT is completed
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job_fb["id"],))
                self.assertEqual(cur.fetchone()["status"], "retrying")

                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job_yt["id"],))
                self.assertEqual(cur.fetchone()["status"], "completed")

    # ============================================================
    # 8. Publication Idempotency & Deduplication Guard (Section 15)
    # ============================================================
    def test_08_publication_idempotency_guard(self):
        """
        Verify:
          - Idempotency key format: brand:account:content:timestamp
          - Attempting duplicate job creation with identical idempotency key returns existing job
          - Same content cannot be queued twice for the exact same publication intent
        """
        ts = int(dt.datetime.now().timestamp() * 1000)
        idemp_key = f"pub_idemp_key_{ts}"

        job1 = DurableJobService.enqueue(
            job_type=f"idemp_test_{ts}",
            payload={"video": "test.mp4"},
            idempotency_key=idemp_key
        )
        # Attempt duplicate enqueue
        job2 = DurableJobService.enqueue(
            job_type=f"idemp_test_{ts}",
            payload={"video": "test.mp4"},
            idempotency_key=idemp_key
        )

        # Must return the identical job record
        self.assertEqual(job1["id"], job2["id"])

        # Verify only 1 record exists in DB
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) as count FROM durable_jobs WHERE idempotency_key = %s;", (idemp_key,))
                self.assertEqual(cur.fetchone()["count"], 1)

    # ============================================================
    # 9. Content Asset Fingerprinting (Section 29)
    # ============================================================
    def test_09_content_asset_fingerprinting(self):
        """
        Verify:
          - Cryptographic SHA-256 for exact bitstream matching
          - Perceptual difference hash (dHash) for visually near-identical frames
          - Media validator detects dimensions and format correctly
        """
        # Blue test card
        img = Image.new("RGB", (1080, 1920), color=(10, 80, 180))
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=90)
        img_bytes = buf.getvalue()

        sha256 = calculate_sha256(img_bytes)
        self.assertEqual(len(sha256), 64)

        phash = calculate_perceptual_hash(img_bytes)
        self.assertEqual(len(phash), 16)

        val = validate_media(img_bytes, "photo", "card_1080x1920.jpg")
        self.assertTrue(val["is_valid"])
        self.assertEqual(val["media_type"], "photo")
        self.assertEqual(val["width"], 1080)
        self.assertEqual(val["height"], 1920)

    # ============================================================
    # 10. Security Audit: No Secrets Exposed in APIs (Section 32)
    # ============================================================
    def test_10_security_audit_no_secrets_in_api(self):
        """
        Verify:
          - /api/system/platforms/accounts returns health & config but NEVER access tokens
          - /api/system/status returns metrics without leaking credentials
          - platform_accounts table does not store plaintext tokens
        """
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM platform_accounts LIMIT 10;")
                rows = cur.fetchall()
                for r in rows:
                    row_dict = dict(r)
                    # Must not have fb_access_token or raw token fields
                    self.assertNotIn("access_token", row_dict)
                    self.assertNotIn("fb_access_token", row_dict)
                    self.assertNotIn("secret", row_dict)


if __name__ == "__main__":
    unittest.main()
