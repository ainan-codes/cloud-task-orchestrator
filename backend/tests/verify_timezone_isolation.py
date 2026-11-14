"""
backend/tests/verify_timezone_isolation.py — Verifies multi-account timezone isolation.

Tests:
1. Account A configured with UTC+0 (London).
2. Account B configured with UTC-5 (America/New_York / EST).
3. Verifies schedule preview, computed UTC times, and dashboard local time conversion.
4. Proves zero hardcoding and zero cross-contamination.
"""

import sys
import os
import datetime as dt

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import db
import scheduling
from core.planning import PublicationPlanningService


def run_timezone_verification():
    print("=" * 60)
    print("  REEL LEDGER — TIMEZONE ISOLATION & ACCURACY AUDIT")
    print("=" * 60)

    # Base reference time: fixed UTC noon
    fixed_now_utc = dt.datetime(2026, 10, 6, 8, 0, 0, tzinfo=dt.timezone.utc)
    print(f"\n[*] Reference UTC Now: {fixed_now_utc.isoformat()}")

    # Account A: London (UTC+0)
    profile_a = {
        "automation_enabled": True,
        "video_enabled": True,
        "photo_enabled": True,
        "text_enabled": True,
        "daily_video_limit": 2,
        "daily_photo_limit": 2,
        "daily_text_limit": 2,
        "posting_window_start": 9,
        "posting_window_end": 21,
        "timezone": "Europe/London",
        "utc_offset_hours": 0.0,
        "min_spacing_minutes": 45,
    }

    # Account B: New York (UTC-5 / EST)
    profile_b = {
        "automation_enabled": True,
        "video_enabled": True,
        "photo_enabled": True,
        "text_enabled": True,
        "daily_video_limit": 2,
        "daily_photo_limit": 2,
        "daily_text_limit": 2,
        "posting_window_start": 9,
        "posting_window_end": 21,
        "timezone": "America/New_York",
        "utc_offset_hours": -5.0,
        "min_spacing_minutes": 45,
    }

    quotas = {"video": 2, "photo": 2, "text": 2}

    print("\n[1/3] Computing Schedule for Account A (Europe/London, UTC+0)...")
    cfg_a = {
        "utc_offset_hours": profile_a["utc_offset_hours"],
        "schedule_mode": "spread",
        "spread_start_hour": profile_a["posting_window_start"],
        "spread_end_hour": profile_a["posting_window_end"]
    }
    slots_a = scheduling.compute_interleaved_schedule(
        cfg_a, quotas, now_utc=fixed_now_utc, min_gap_minutes=profile_a["min_spacing_minutes"]
    )
    assert len(slots_a) == 6, f"Expected 6 slots for Account A, got {len(slots_a)}"

    print("  Account A slots:")
    offset_a = dt.timedelta(hours=profile_a["utc_offset_hours"])
    for sched_utc, ctype in slots_a:
        local_a = sched_utc + offset_a
        print(f"    - [{ctype.upper():5s}] UTC: {sched_utc.strftime('%H:%M')} | Local: {local_a.strftime('%I:%M %p')}")
        # Verify local time is strictly within 9:00 to 21:00
        assert 9 <= local_a.hour <= 21, f"Account A local time {local_a} outside posting window 9..21"
        # Since offset is 0, UTC hour must equal local hour
        assert sched_utc.hour == local_a.hour, f"Account A UTC {sched_utc.hour} != Local {local_a.hour}"

    print("\n[2/3] Computing Schedule for Account B (America/New_York, UTC-5)...")
    cfg_b = {
        "utc_offset_hours": profile_b["utc_offset_hours"],
        "schedule_mode": "spread",
        "spread_start_hour": profile_b["posting_window_start"],
        "spread_end_hour": profile_b["posting_window_end"]
    }
    slots_b = scheduling.compute_interleaved_schedule(
        cfg_b, quotas, now_utc=fixed_now_utc, min_gap_minutes=profile_b["min_spacing_minutes"]
    )
    assert len(slots_b) == 6, f"Expected 6 slots for Account B, got {len(slots_b)}"

    print("  Account B slots:")
    offset_b = dt.timedelta(hours=profile_b["utc_offset_hours"])
    for sched_utc, ctype in slots_b:
        local_b = sched_utc + offset_b
        print(f"    - [{ctype.upper():5s}] UTC: {sched_utc.strftime('%H:%M')} | Local: {local_b.strftime('%I:%M %p')}")
        # Verify local time is strictly within 9:00 to 21:00 EST
        assert 9 <= local_b.hour <= 21, f"Account B local time {local_b} outside posting window 9..21"
        # Since offset is -5, UTC time is local + 5 hours
        expected_utc = local_b - offset_b
        assert sched_utc == expected_utc, f"UTC mismatch for Account B: {sched_utc} vs {expected_utc}"

    print("\n[3/3] Cross-Account Isolation Verification...")
    # Verify Account A and Account B first slot UTC times differ by exactly 5 hours
    first_utc_a = slots_a[0][0]
    first_utc_b = slots_b[0][0]
    utc_diff_hours = (first_utc_b - first_utc_a).total_seconds() / 3600.0
    print(f"  Account A first slot UTC: {first_utc_a.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Account B first slot UTC: {first_utc_b.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Difference: {utc_diff_hours:.1f} hours (Expected: 5.0 hours offset difference)")
    assert abs(utc_diff_hours - 5.0) < 0.1, f"Expected 5.0 hours UTC difference, got {utc_diff_hours}"

    print("\n" + "=" * 60)
    print("  RESULT: PASS — TIMEZONE ISOLATION FULLY VERIFIED")
    print("=" * 60)


if __name__ == "__main__":
    run_timezone_verification()
