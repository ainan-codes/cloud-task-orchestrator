"""
test_multi_format_and_collision.py — Unit tests for multi-format publishing (Reels + Images + Text),
timeline collision detection, 24/7 spread calculation, and Gemini AI engine.
"""

import os
import sys
import datetime as dt
import unittest
from unittest.mock import patch, MagicMock

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import scheduling
import poster
import drive
import gemini_ai
import credentials


class TestCollisionEngine(unittest.TestCase):
    def test_detect_collisions_finds_close_posts(self):
        t0 = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.timezone.utc)
        t_colliding = dt.datetime(2026, 9, 27, 10, 15, tzinfo=dt.timezone.utc) # 15m gap < 30m
        t_safe = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)      # 105m gap

        posts = [
            {"id": 1, "scheduled_time_utc": t0.isoformat(), "file_name": "Reel_01.mp4", "media_type": "reel"},
            {"id": 2, "scheduled_time_utc": t_colliding.isoformat(), "file_name": "Photo_01.jpg", "media_type": "photo"},
            {"id": 3, "scheduled_time_utc": t_safe.isoformat(), "file_name": "Reel_02.mp4", "media_type": "reel"},
        ]

        collisions = scheduling.detect_collisions(posts, min_gap_minutes=30)
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0]["post1_id"], 1)
        self.assertEqual(collisions[0]["post2_id"], 2)
        self.assertEqual(collisions[0]["gap_minutes"], 15.0)

    def test_resolve_collisions_respaces_posts(self):
        t0 = dt.datetime(2026, 9, 27, 10, 0, tzinfo=dt.timezone.utc)
        t1 = dt.datetime(2026, 9, 27, 10, 10, tzinfo=dt.timezone.utc) # 10m gap
        t2 = dt.datetime(2026, 9, 27, 10, 20, tzinfo=dt.timezone.utc) # 10m gap

        posts = [
            {"id": 1, "scheduled_time_utc": t0.isoformat()},
            {"id": 2, "scheduled_time_utc": t1.isoformat()},
            {"id": 3, "scheduled_time_utc": t2.isoformat()},
        ]

        updates = scheduling.resolve_collisions(posts, min_gap_minutes=45)
        self.assertEqual(len(updates), 2)
        # Post 2 should be moved to 10:45
        self.assertEqual(updates[0]["id"], 2)
        self.assertEqual(updates[0]["scheduled_time_utc"], t0 + dt.timedelta(minutes=45))
        # Post 3 should be moved to 11:30
        self.assertEqual(updates[1]["id"], 3)
        self.assertEqual(updates[1]["scheduled_time_utc"], t0 + dt.timedelta(minutes=90))

    def test_24_hour_spread_no_overflow(self):
        page = {
            "schedule_mode": "spread",
            "spread_start_hour": 0,
            "spread_end_hour": 24, # 24/7 continuous
            "utc_offset_hours": 0,
        }
        # Compute 12 post times
        times = scheduling.compute_times(page, count=12)
        self.assertEqual(len(times), 12)
        for t in times:
            self.assertIsInstance(t, dt.datetime)
            self.assertIsNotNone(t.tzinfo)


class TestMultiFormatPoster(unittest.TestCase):
    @patch("poster.publish_feed_text")
    def test_publish_text_dispatches_correctly(self, mock_feed_text):
        mock_feed_text.return_value = "fb_text_123"
        res = poster.publish(
            media_type="text",
            page_id="12345",
            page_token="tok",
            media_bytes=None,
            caption="Hello world status update!",
        )
        self.assertEqual(res, "fb_text_123")
        mock_feed_text.assert_called_once_with("12345", "tok", "Hello world status update!", None)

    @patch("poster.publish_photo")
    def test_publish_photo_dispatches_correctly(self, mock_photo):
        mock_photo.return_value = "fb_photo_456"
        res = poster.publish(
            media_type="photo",
            page_id="12345",
            page_token="tok",
            media_bytes=b"fake_jpeg_bytes",
            caption="Look at this picture #viral",
        )
        self.assertEqual(res, "fb_photo_456")
        mock_photo.assert_called_once_with("12345", "tok", b"fake_jpeg_bytes", "Look at this picture #viral", None)


class TestUniversalDriveDownload(unittest.TestCase):
    def test_download_data_uri(self):
        import base64
        test_bytes = b"hello image binary"
        b64_str = base64.b64encode(test_bytes).decode("utf-8")
        data_uri = f"data:image/jpeg;base64,{b64_str}"

        res = drive.download_file(data_uri)
        self.assertEqual(res, test_bytes)

    def test_download_empty_or_none(self):
        self.assertEqual(drive.download_file(None), b"")
        self.assertEqual(drive.download_file("none"), b"")
        self.assertEqual(drive.download_file(""), b"")


class TestGeminiAiEngine(unittest.TestCase):
    def test_offline_fallback_structure(self):
        res = gemini_ai.generate_content("mindset tips", niche="finance", content_format="text", api_key="")
        self.assertTrue(res["success"])
        self.assertIn("headline", res)
        self.assertIn("body", res)
        self.assertIn("hashtags", res)
        self.assertTrue(len(res["hashtags"]) > 0)


if __name__ == "__main__":
    unittest.main()
