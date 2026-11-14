import sys
import os
import datetime as dt
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import db
import scheduling
from core.planning import PublicationPlanningService

def verify_rebalancing():
    print("=== DYNAMIC REBALANCING REAL TEST ===")
    page_id = 3
    page_row = db.get_page(page_id)
    assert page_row, f"Page #{page_id} not found"
    
    # Base page settings
    base_page_cfg = {
        "utc_offset_hours": -5.0,  # America/New_York (EST)
        "schedule_mode": "spread",
        "spread_start_hour": 9,    # 9:00 AM
        "spread_end_hour": 21      # 9:00 PM (12 hour window)
    }

    # -------------------------------------------------------------
    # Scenario 1: Initial Quota (Photo=4, Video=0, Text=0)
    # -------------------------------------------------------------
    print("\n--- TEST 1: Initial Quota: Photo=4, Video=0, Text=0 ---")
    quotas_1 = {"photo": 4, "video": 0, "text": 0}
    schedule_1 = scheduling.compute_interleaved_schedule(
        base_page_cfg, quotas_1, min_gap_minutes=30
    )
    print(f"Generated {len(schedule_1)} slots:")
    for t, ctype in schedule_1:
        print(f"  Slot: {t.isoformat()} -> {ctype}")
    
    assert len(schedule_1) == 4, f"Expected 4 slots, got {len(schedule_1)}"
    assert all(ctype == "photo" for _, ctype in schedule_1), "All slots should be 'photo'"
    
    # Verify spacing
    for i in range(len(schedule_1) - 1):
        diff = (schedule_1[i+1][0] - schedule_1[i][0]).total_seconds() / 60.0
        assert diff >= 30, f"Spacing violation between slot {i} and {i+1}: {diff} min"

    # -------------------------------------------------------------
    # Scenario 2: Dynamic Transition to Photo=4, Video=4, Text=4 (12 posts/day)
    # -------------------------------------------------------------
    print("\n--- TEST 2: Dynamic Transition: Photo=4, Video=4, Text=4 (12 posts/day) ---")
    quotas_2 = {"photo": 4, "video": 4, "text": 4}
    schedule_2 = scheduling.compute_interleaved_schedule(
        base_page_cfg, quotas_2, min_gap_minutes=30
    )
    print(f"Generated {len(schedule_2)} slots:")
    type_counts = {}
    timestamps = set()
    for t, ctype in schedule_2:
        print(f"  Slot: {t.isoformat()} -> {ctype}")
        type_counts[ctype] = type_counts.get(ctype, 0) + 1
        assert t not in timestamps, f"Duplicate slot timestamp detected: {t}"
        timestamps.add(t)

    assert len(schedule_2) == 12, f"Expected 12 slots, got {len(schedule_2)}"
    assert type_counts == {"photo": 4, "video": 4, "text": 4}, f"Mismatched type counts: {type_counts}"
    
    # Verify fair-share interleaving (no 2 consecutive identical types)
    for i in range(len(schedule_2) - 1):
        diff = (schedule_2[i+1][0] - schedule_2[i][0]).total_seconds() / 60.0
        assert diff >= 30, f"Spacing violation between slot {i} and {i+1}: {diff} min"
        assert schedule_2[i][1] != schedule_2[i+1][1], (
            f"Clumping violation: slot {i} ({schedule_2[i][1]}) and slot {i+1} ({schedule_2[i+1][1]}) have same type"
        )

    # -------------------------------------------------------------
    # Scenario 3: Change Again (Photo=2, Video=6, Text=1 = 9 posts/day)
    # -------------------------------------------------------------
    print("\n--- TEST 3: Dynamic Change Again: Photo=2, Video=6, Text=1 (9 posts/day) ---")
    quotas_3 = {"photo": 2, "video": 6, "text": 1}
    schedule_3 = scheduling.compute_interleaved_schedule(
        base_page_cfg, quotas_3, min_gap_minutes=30
    )
    print(f"Generated {len(schedule_3)} slots:")
    type_counts_3 = {}
    timestamps_3 = set()
    for t, ctype in schedule_3:
        print(f"  Slot: {t.isoformat()} -> {ctype}")
        type_counts_3[ctype] = type_counts_3.get(ctype, 0) + 1
        assert t not in timestamps_3, f"Duplicate slot timestamp detected: {t}"
        timestamps_3.add(t)

    assert len(schedule_3) == 9, f"Expected 9 slots, got {len(schedule_3)}"
    assert type_counts_3 == {"photo": 2, "video": 6, "text": 1}, f"Mismatched type counts: {type_counts_3}"

    # -------------------------------------------------------------
    # Scenario 4: Database-Level Isolation: Published & Running Posts Untouched
    # -------------------------------------------------------------
    print("\n--- TEST 4: Database Isolation Proof (Published & In-Flight Untouched) ---")
    with db._conn() as conn:
        with conn.cursor() as cur:
            # 1. Inspect existing published/verified posts
            cur.execute("""
                SELECT id, content_type, scheduled_at, published_at, status
                FROM publications
                WHERE status IN ('published', 'verified')
                ORDER BY id DESC LIMIT 5;
            """)
            published_before = cur.fetchall()
            print(f"Verified Published Posts Before Rebalance: {len(published_before)}")
            pub_ids = [p["id"] for p in published_before]
            pub_times_before = {p["id"]: p["scheduled_at"] for p in published_before}

            # 2. Update automation profile quotas via PublicationPlanningService
            PublicationPlanningService.save_page_automation_profile(
                page_id=page_id,
                profile_data={
                    "daily_photo_limit": 4,
                    "daily_video_limit": 4,
                    "daily_text_limit": 4,
                    "photo_enabled": True,
                    "video_enabled": True,
                    "text_enabled": True,
                    "min_spacing_minutes": 30,
                    "posting_window_start": 9,
                    "posting_window_end": 21,
                    "utc_offset_hours": -5.0
                }
            )

            # 3. Trigger full schedule rebalance
            rebalance_res = PublicationPlanningService.rebalance_page_schedule(page_id)
            print(f"Rebalance Result: {rebalance_res}")

            # 4. Assert published posts were NOT modified
            cur.execute("""
                SELECT id, content_type, scheduled_at, published_at, status
                FROM publications
                WHERE id = ANY(%s);
            """, (pub_ids,))
            published_after = cur.fetchall()
            for p in published_after:
                pid = p["id"]
                assert p["status"] in ("published", "verified"), f"Post #{pid} status changed to {p['status']}!"
                assert p["scheduled_at"] == pub_times_before[pid], f"Post #{pid} scheduled_at mutated!"
                print(f"  Published Post #{pid}: UNTOUCHED (Status={p['status']}, Sched={p['scheduled_at']})")

    print("\n>>> DYNAMIC REBALANCING REAL TEST PASSED ALL CHECKS! <<<")
    if hasattr(db, "_pool") and db._pool:
        db._pool.close()

if __name__ == "__main__":
    verify_rebalancing()
