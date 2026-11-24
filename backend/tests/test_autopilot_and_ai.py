import os
import sys
import unittest
import datetime as dt

backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

import gemini_ai
import db

class TestAutopilotAndAi(unittest.TestCase):
    def test_spontaneous_image_url(self):
        url = gemini_ai.generate_spontaneous_image_url("Dark Psychology & Wealth", "Power and influence")
        self.assertTrue(url.startswith("http"))
        self.assertIn("pollinations.ai", url)

    def test_gemini_ai_content_structure(self):
        res = gemini_ai.generate_content("The law of 1% improvement", niche="Stoic Discipline", content_format="photo")
        self.assertTrue(res.get("success"))
        self.assertIn("headline", res)
        self.assertIn("body", res)
        self.assertIn("hashtags", res)
        self.assertTrue(len(res["hashtags"]) >= 4)
        self.assertTrue(res.get("image_url").startswith("http"))

    def test_multi_model_parameter_handling(self):
        # Test that preferred_model 'gemini-2.0-flash' or 'gemini-1.5-pro' is accepted cleanly
        res = gemini_ai.generate_content(
            prompt="Daily stoic meditation",
            niche="Philosophy",
            content_format="text",
            preferred_model="gemini-2.0-flash"
        )
        self.assertTrue(res.get("success"))
        self.assertIn("headline", res)

    def test_autopilot_batch_logic(self):
        # 1. Ensure test page exists
        page = db.upsert_page(
            name="Autopilot Test Page",
            fb_page_id="test_autopilot_fb_9988",
            fb_access_token="EAABtesttoken",
            drive_folder_id="1mockDriveFolder",
            per_run=12,
            spread_start_hour=0,
            spread_end_hour=24,
            is_active=True
        )
        page_id = page["id"]

        # 2. Simulate generating batch of 3 posts
        niche = "Crypto & Web3 Wealth"
        now_utc = dt.datetime.now(dt.timezone.utc)
        base_time = now_utc + dt.timedelta(minutes=30)
        
        for i in range(3):
            chosen_format = "photo" if i % 2 == 0 else "text"
            slot_time = base_time + dt.timedelta(minutes=i * 45)
            ai_res = gemini_ai.generate_content(f"Topic {i+1} in {niche}", niche=niche, content_format=chosen_format)
            
            ci = db.create_content_item(
                page_id=page_id,
                media_type=chosen_format,
                source_video_id=f"auto_{i}_{int(now_utc.timestamp())}",
                drive_file_id=ai_res.get("image_url", f"direct:{chosen_format}:1"),
                file_name=f"Autopilot Item {i+1}",
                caption=ai_res.get("body", "Test caption"),
                hashtags=ai_res.get("hashtags", ["#crypto", "#wealth"]),
                source_folder="autopilot_test"
            )
            self.assertIsNotNone(ci.get("id"))
            
            enq = db.enqueue(page_id, ci["id"], slot_time, ci["caption"])
            self.assertIsNotNone(enq.get("id"))
            self.assertEqual(enq.get("status"), "pending")

        scheduled = db.list_scheduled(page_id=page_id, status="pending")
        self.assertTrue(len(scheduled) >= 3)

        # Cleanup test artifacts from database so production queues remain pristine
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM scheduled_posts WHERE page_id = %s", (page_id,))
                cur.execute("DELETE FROM content_items WHERE page_id = %s", (page_id,))
                cur.execute("DELETE FROM pages WHERE id = %s", (page_id,))
            conn.commit()

if __name__ == "__main__":
    unittest.main()
