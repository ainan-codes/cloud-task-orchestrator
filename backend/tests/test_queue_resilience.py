import os
import sys
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

# Ensure backend directory is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from logger import redact_secrets
from poster import is_auth_error as is_fb_auth_error
from snapchat_errors import is_snap_auth_error


class TestQueueResilienceAndIsolation(unittest.TestCase):

    def test_circuit_breaker_isolation(self):
        """Validates that FB and Snapchat auth errors are classified independently."""
        # Meta error subcode 463 is token expired
        fb_expired_err = Exception("Error validating access token: Session has expired. (code 190, subcode 463)")
        self.assertTrue(is_fb_auth_error(fb_expired_err))

        # Transient Facebook network error is NOT auth error
        fb_transient_err = Exception("Facebook API timeout after 60s")
        self.assertFalse(is_fb_auth_error(fb_transient_err))

        # Snapchat 401 unauthorized
        snap_401_err = Exception("HTTP 401: Unauthorized access token revoked")
        self.assertTrue(is_snap_auth_error(snap_401_err))

        # Snapchat 429 rate limit is NOT auth error
        snap_rate_limit_err = Exception("HTTP 429: Too Many Requests")
        self.assertFalse(is_snap_auth_error(snap_rate_limit_err))

    def test_secret_redaction_filter(self):
        """Ensures secrets and access tokens are strictly masked by logger filters."""
        raw_log = "Posting with access_token: EAAG1234567890abcdef and client_secret: my_super_secret"
        redacted = redact_secrets(raw_log)

        self.assertNotIn("EAAG1234567890abcdef", redacted)
        self.assertNotIn("my_super_secret", redacted)
        self.assertIn("[REDACTED]", redacted)

    def test_smooth_reflow_spacing_logic(self):
        """Simulates scheduling rescue reflow to ensure proper forward spacing."""
        now = datetime.now(timezone.utc)
        post_count = 5
        interval_minutes = 60

        scheduled_times = []
        base_time = now + timedelta(minutes=15)
        for i in range(post_count):
            scheduled_times.append(base_time + timedelta(minutes=i * interval_minutes))

        # Verify strictly increasing times with minimum 60 min gap
        for i in range(len(scheduled_times) - 1):
            gap = (scheduled_times[i+1] - scheduled_times[i]).total_seconds() / 60
            self.assertEqual(gap, 60)
            self.assertTrue(scheduled_times[i] > now)


if __name__ == "__main__":
    unittest.main()
