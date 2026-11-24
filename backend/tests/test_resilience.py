"""
backend/tests/test_resilience.py — Autonomous Resilience, Idempotency & Fault-Tolerance Tests.

Tests:
  1. Cryptographic SHA-256 and perceptual dHash integrity.
  2. Platform capability matrix enforcement.
  3. Distributed worker lease acquisition and watchdog lease reclamation.
  4. Dead-letter queue transition on permanent error.
  5. Publication state machine idempotency guard.
"""

import sys
import os
import unittest
import datetime as dt
from io import BytesIO
from PIL import Image

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from core.fingerprint import calculate_sha256, calculate_perceptual_hash, validate_media
from core.capabilities import CapabilityRegistry
from core.jobs import DurableJobService
from core.watchdog import WatchdogService
from core.orchestrator import PublishingOrchestrator
import db


class TestResilienceAndIdempotency(unittest.TestCase):

    def test_01_media_fingerprinting_integrity(self):
        """Verify deterministic SHA-256 and perceptual difference hash."""
        # Create test red square image in memory
        img = Image.new("RGB", (200, 200), color=(255, 0, 0))
        buf = BytesIO()
        img.save(buf, format="JPEG")
        data = buf.getvalue()

        sha1 = calculate_sha256(data)
        sha2 = calculate_sha256(data)
        self.assertEqual(sha1, sha2)
        self.assertEqual(len(sha1), 64)

        phash = calculate_perceptual_hash(data)
        self.assertIsNotNone(phash)
        self.assertEqual(len(phash), 16)

        # Validate media function
        val = validate_media(data, "photo", "test.jpg")
        self.assertTrue(val["is_valid"])
        self.assertEqual(val["width"], 200)
        self.assertEqual(val["height"], 200)

    def test_02_platform_capability_matrix(self):
        """Verify capability registry rejects unsupported content types before queueing."""
        # YouTube: Video supported, photo unsupported
        yt_v, _ = CapabilityRegistry.validate_request("youtube", "reel")
        self.assertTrue(yt_v)
        yt_p, err_p = CapabilityRegistry.validate_request("youtube", "photo")
        self.assertFalse(yt_p)
        self.assertIn("does not support photo", err_p)

        # Snapchat: Text unsupported
        snap_t, err_t = CapabilityRegistry.validate_request("snapchat", "text")
        self.assertFalse(snap_t)
        self.assertIn("does not support text", err_t)

        # Facebook: All three supported
        fb_v, _ = CapabilityRegistry.validate_request("facebook", "reel")
        fb_p, _ = CapabilityRegistry.validate_request("facebook", "photo")
        fb_t, _ = CapabilityRegistry.validate_request("facebook", "text")
        self.assertTrue(fb_v)
        self.assertTrue(fb_p)
        self.assertTrue(fb_t)

    def test_03_durable_job_lease_and_watchdog_reclaim(self):
        """Verify atomic worker leases and watchdog reclamation of expired leases."""
        # Enqueue job
        test_key = f"test_lease_{int(dt.datetime.now().timestamp())}"
        unique_type = f"watchdog_{int(dt.datetime.now().timestamp())}"
        job = DurableJobService.enqueue(
            job_type=unique_type,
            payload={"msg": "test_lease"},
            priority=1,
            idempotency_key=test_key
        )
        job_id = job["id"]

        # Claim job
        worker_id = "test-worker-alpha"
        claimed = DurableJobService.claim(worker_id=worker_id, job_types=[unique_type], lease_seconds=1)
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["id"], job_id)
        self.assertEqual(claimed["status"], "leased")

        # Simulate heartbeat staleness (> 90 seconds ago)
        stale_time = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=120)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE durable_jobs SET heartbeat_at = %s WHERE id = %s;", (stale_time, job_id))
            conn.commit()

        # Run Watchdog sweep
        reclaimed_count = DurableJobService.reclaim_expired_leases(lease_expiry_seconds=60)
        self.assertGreaterEqual(reclaimed_count, 1)

        # Verify job is returned to 'queued' status
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT status FROM durable_jobs WHERE id = %s;", (job_id,))
                status = cur.fetchone()["status"]
                self.assertEqual(status, "queued")

        # Clean up
        DurableJobService.complete(job_id)

    def test_04_dead_letter_queue_and_recovery(self):
        """Verify retry exhaustion routes job to DLQ and operator recovery works."""
        test_key = f"test_dlq_{int(dt.datetime.now().timestamp())}"
        dlq_type = f"dlq_{int(dt.datetime.now().timestamp())}"
        job = DurableJobService.enqueue(
            job_type=dlq_type,
            payload={"msg": "fail_test"},
            priority=1,
            max_attempts=1,
            idempotency_key=test_key
        )
        job_id = job["id"]

        # Claim and fail with permanent error
        DurableJobService.claim("test-worker-dlq", job_types=[dlq_type])
        res = DurableJobService.fail(job_id, error_message="Fatal unrecoverable rejection", error_kind="permanent")
        self.assertEqual(res["status"], "dead_letter")

        # Verify DLQ record exists
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, recovery_state FROM dead_letter_jobs WHERE job_id = %s;", (job_id,))
                dlq_row = cur.fetchone()
                self.assertIsNotNone(dlq_row)
                dlq_id = dlq_row["id"]
                self.assertEqual(dlq_row["recovery_state"], "unresolved")

        # Recover from DLQ
        rec = DurableJobService.recover_dead_letter(dlq_id, action="retry")
        self.assertTrue(rec["success"])

        # Clean up
        DurableJobService.complete(job_id)

    def test_05_idempotency_key_generation(self):
        """Verify identical parameters generate identical SHA-256 idempotency keys."""
        k1 = PublishingOrchestrator.generate_idempotency_key(
            platform="facebook",
            account_id=1,
            content_item_id=42,
            content_type="reel",
            scheduled_ts=1700000000
        )
        k2 = PublishingOrchestrator.generate_idempotency_key(
            platform="facebook",
            account_id=1,
            content_item_id=42,
            content_type="reel",
            scheduled_ts=1700000000
        )
        k3 = PublishingOrchestrator.generate_idempotency_key(
            platform="facebook",
            account_id=2,  # different account
            content_item_id=42,
            content_type="reel",
            scheduled_ts=1700000000
        )
        self.assertEqual(k1, k2)
        self.assertNotEqual(k1, k3)

    @classmethod
    def tearDownClass(cls):
        if db._pool and hasattr(db._pool, "close"):
            try:
                db._pool.close()
            except Exception:
                pass


if __name__ == "__main__":
    unittest.main()
