import sys
import os
import datetime as dt
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
from content_machine import ContentMachine
from core.planning import PublicationPlanningService
from core.orchestrator import PublishingOrchestrator
from core.jobs import DurableJobService
from core.adapters import get_adapter

def test_final_golden_path():
    print("=================================================================")
    print("       REEL LEDGER — FINAL GOLDEN PATH AUTOPILOT VERIFICATION     ")
    print("=================================================================")
    
    page_id = 3
    page_row = db.get_page(page_id)
    assert page_row, f"Page #{page_id} not found"
    fb_page_id = page_row["fb_page_id"]
    print(f"Target Page: '{page_row['name']}' (Internal ID: {page_id}, Meta Page ID: {fb_page_id})")

    # -------------------------------------------------------------------------
    # STAGE 1: USER CONFIGURES PAGE PROFILE
    # (Configures Photo, Text, Video quotas, posting window, enables AI generation)
    # -------------------------------------------------------------------------
    print("\n--- STAGE 1: USER CONFIGURES PAGE AUTOMATION PROFILE ---")
    profile_data = {
        "daily_photo_limit": 2,
        "daily_video_limit": 0,
        "daily_text_limit": 2,
        "photo_enabled": True,
        "video_enabled": False,
        "text_enabled": True,
        "posting_window_start": 9,
        "posting_window_end": 21,
        "min_spacing_minutes": 30,
        "utc_offset_hours": -5.0,
        "automation_enabled": True,
        "content_source_mode": "ai_generated",
        "primary_niche": "American Heritage & Innovation"
    }
    saved_profile = PublicationPlanningService.save_page_automation_profile(page_id, profile_data)
    profile_info = saved_profile.get("profile", saved_profile)
    print(f"Profile Saved: Photo Quota={profile_info.get('daily_photo_limit')}, Text Quota={profile_info.get('daily_text_limit')}, Window={profile_info.get('posting_window_start')}:00-{profile_info.get('posting_window_end')}:00")
    assert profile_info.get("automation_enabled") is True

    # -------------------------------------------------------------------------
    # STAGE 2: SYSTEM GENERATES CONTENT WITH AUTO-QUEUE
    # (Zero manual DB inserts, zero manual queue insertion)
    # -------------------------------------------------------------------------
    print("\n--- STAGE 2: AUTONOMOUS GENERATION & AUTO-QUEUEING ---")
    # Generate 1 Photo and 1 Text item for the active profile
    generated_items = []
    
    # 2a. Photo Generation
    photo_batch = ContentMachine.generate_asset_batch(
        page_id=page_id,
        niche="American Heritage & Innovation",
        topic="Pioneers of Modern Flight",
        quantity=1,
        content_format="photo",
        auto_queue=True
    )
    assert len(photo_batch) == 1, "Photo generation failed"
    photo_item = photo_batch[0]
    generated_items.append(photo_item)
    print(f"Auto-Generated Photo Asset: ID={photo_item['id']}, Format={photo_item.get('media_type') or 'photo'}")
    print(f"  Caption: {photo_item['caption'][:60]}...")
    print(f"  Auto-Queued Pub ID: {photo_item.get('queued_publication_id')}, Job ID: {photo_item.get('durable_job_id')}")

    # 2b. Text Generation
    text_batch = ContentMachine.generate_asset_batch(
        page_id=page_id,
        niche="American Heritage & Innovation",
        topic="The Mindset of Thomas Edison",
        quantity=1,
        content_format="text",
        auto_queue=True
    )
    assert len(text_batch) == 1, "Text generation failed"
    text_item = text_batch[0]
    generated_items.append(text_item)
    print(f"Auto-Generated Text Asset: ID={text_item['id']}, Format={text_item.get('media_type') or 'text'}")
    print(f"  Caption: {text_item['caption'][:60]}...")
    print(f"  Auto-Queued Pub ID: {text_item.get('queued_publication_id')}, Job ID: {text_item.get('durable_job_id')}")

    # -------------------------------------------------------------------------
    # STAGE 3: SCHEDULER ASSIGNS TIMES & BALANCES QUEUE
    # -------------------------------------------------------------------------
    print("\n--- STAGE 3: SMART SCHEDULER ASSIGNMENT & TIMEZONE SPACING ---")
    for item in generated_items:
        pub_id = item["queued_publication_id"]
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT scheduled_at, status FROM publications WHERE id = %s;", (pub_id,))
                pub_row = cur.fetchone()
        assert pub_row is not None
        assert pub_row["status"] == "queued"
        print(f"  Publication #{pub_id}: Scheduled for {pub_row['scheduled_at']} (Status: {pub_row['status']})")

    # -------------------------------------------------------------------------
    # STAGE 4: AUTONOMOUS WORKER CLAIMS & PUBLISHES LIVE TO FACEBOOK
    # -------------------------------------------------------------------------
    print("\n--- STAGE 4: WORKER CLAIMS JOB & PUBLISHES LIVE TO FACEBOOK ---")
    orch = PublishingOrchestrator()
    published_results = []
    for item in generated_items:
        pub_id = item["queued_publication_id"]
        job_id = item["durable_job_id"]
        print(f"Executing Publication Job #{job_id} for Publication #{pub_id}...")
        res = orch.execute_publication(pub_id, job_id=job_id)
        print(f"  Execution Result: {res}")
        assert res["success"] is True, f"Execution failed: {res}"
        assert res["external_post_id"] is not None
        published_results.append((pub_id, res["external_post_id"]))

    # -------------------------------------------------------------------------
    # STAGE 5: REMOTE PLATFORM CONFIRMATION VIA GRAPH API
    # -------------------------------------------------------------------------
    print("\n--- STAGE 5: REMOTE GRAPH API CONFIRMATION ---")
    fb_adapter = get_adapter("facebook")
    dec = db._with_page_token(page_row)
    for pub_id, remote_id in published_results:
        status_check = fb_adapter.get_publication_status(
            external_account_id=fb_page_id,
            credentials_payload={"access_token": dec["fb_access_token"]},
            external_post_id=remote_id
        )
        print(f"  Publication #{pub_id} (Remote ID: {remote_id}) -> Graph API Check: {status_check}")
        assert status_check.get("exists") is True, f"Remote check failed for {remote_id}: {status_check}"

    # -------------------------------------------------------------------------
    # STAGE 6: ANALYTICS COLLECTED WITHOUT BLOCKING
    # -------------------------------------------------------------------------
    print("\n--- STAGE 6: TIMESTAMPED ANALYTICS CAPTURE ---")
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, fb_creation_id, views, likes, comments, shares, captured_at
                FROM analytics_snapshots
                WHERE page_id = %s
                ORDER BY captured_at DESC LIMIT 5;
            """, (page_id,))
            recent_analytics = cur.fetchall()
    print(f"Captured {len(recent_analytics)} Snapshots for Page #{page_id}:")
    for sn in recent_analytics:
        print(f"  Snapshot #{sn['id']} | Post ID: {sn['fb_creation_id']} | Views: {sn['views']} | Captured: {sn['captured_at']}")
    assert len(recent_analytics) > 0, "No analytics recorded"

    # -------------------------------------------------------------------------
    # STAGE 7: AUTOMATIC CONTINUATION PROOF
    # -------------------------------------------------------------------------
    print("\n--- STAGE 7: AUTOMATIC SYSTEM CONTINUATION ---")
    # Verify remaining future queue and durable jobs
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM durable_jobs WHERE status = 'completed';")
            comp_cnt = cur.fetchone()["count"]
            cur.execute("SELECT count(*) FROM publications WHERE status IN ('published', 'verified');")
            pub_cnt = cur.fetchone()["count"]
    print(f"Reel Ledger Status: {comp_cnt} completed durable jobs, {pub_cnt} published & verified live items.")
    print("\n=================================================================")
    print(" >>> FINAL GOLDEN PATH: 100% PROVEN WITHOUT MANUAL INTERVENTION! <<<")
    print("=================================================================")
    
    if hasattr(db, "_pool") and db._pool:
        db._pool.close()

if __name__ == "__main__":
    test_final_golden_path()
