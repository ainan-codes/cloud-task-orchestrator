"""
backend/core/planning.py — Publication Planning, Tri-Mode Scheduling & Fair-Share Interleaving.

Enforces independent scheduling and dynamic rebalancing for:
  A. VIDEO AUTOMATION (ON/OFF, target count/day)
  B. PHOTO AUTOMATION (ON/OFF, target count/day)
  C. TEXT AUTOMATION  (ON/OFF, target count/day)

Guarantees:
  1. Balanced Fair-Share Interleaving (Bresenham-distribution so content types don't clump).
  2. Safe Dynamic Rebalancing (Never reschedules already published posts; safely adjusts future unposted items).
  3. Content Availability Protection (If fewer valid items exist than requested quota, does NOT create failed jobs).
  4. Platform Capability Awareness (Dailymotion restricts photo/text gracefully).
  5. Dry-Run Schedule Preview (Exact timetable projection without writing changes).
"""

import json
import logging
import datetime as dt
from typing import Dict, Any, List, Optional, Tuple

import db
import scheduling
from core.jobs import DurableJobService
from core.orchestrator import PublishingOrchestrator
from core.capabilities import CapabilityRegistry

logger = logging.getLogger("planning")

DEFAULT_AUTOMATION_PROFILE = {
    "automation_enabled": False,
    "video_enabled": True,
    "photo_enabled": False,
    "text_enabled": False,
    "daily_video_limit": 0,
    "daily_photo_limit": 0,
    "daily_text_limit": 0,
    "posting_window_start": 9,
    "posting_window_end": 21,
    "timezone": "UTC",
    "utc_offset_hours": 0.0,
    "min_spacing_minutes": 30,
    "content_source": "drive",  # "drive" | "generated" | "both"
    "content_source_mode": "drive_only",
    "ai_captioning": False,
    "hashtag_rotation": False,
    "duplicate_protection": True,
    "smart_scheduling": False,
    "auto_retry": False,
    "analytics_sync": False,
    "monetization_tracking": False,
}

DAILYMOTION_DEFAULT_PROFILE = {
    "automation_enabled": False,
    "video_enabled": True,
    "photo_enabled": False,  # Not supported by Dailymotion API
    "text_enabled": False,   # Not supported by Dailymotion API
    "daily_video_limit": 4,
    "daily_photo_limit": 0,
    "daily_text_limit": 0,
    "posting_window_start": 9,
    "posting_window_end": 21,
    "timezone": "UTC",
    "utc_offset_hours": 0.0,
    "min_spacing_minutes": 45,
    "content_source": "both",
    "ai_captioning": True,
    "hashtag_rotation": True,
    "duplicate_protection": True,
    "smart_scheduling": True,
    "auto_retry": True,
    "analytics_sync": True,
    "monetization_tracking": True,  # 70% partner ad share
}


class PublicationPlanningService:
    """Calculates balanced publication schedules, dry-run previews, and safe dynamic rebalancing."""

    @staticmethod
    def update_rule(rule_id: int, **kwargs) -> Dict[str, Any]:
        """Updates publication_rules row and returns the updated record as a dict."""
        allowed_fields = {
            "video_enabled", "photo_enabled", "text_enabled",
            "schedule_mode", "daily_video_limit", "daily_photo_limit",
            "daily_text_limit", "fixed_times", "spread_start_hour",
            "spread_end_hour", "timezone", "min_spacing_minutes",
            "quiet_hours_start", "quiet_hours_end", "is_active"
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if not updates:
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM publication_rules WHERE id = %s;", (rule_id,))
                    return dict(cur.fetchone() or {})

        set_clauses = [f"{k} = %s" for k in updates.keys()]
        set_clauses.append("updated_at = NOW()")
        values = list(updates.values())
        values.append(rule_id)

        query = f"""
            UPDATE publication_rules
            SET {', '.join(set_clauses)}
            WHERE id = %s
            RETURNING *;
        """
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(query, tuple(values))
                row = cur.fetchone()
            conn.commit()
        return dict(row) if row else {}

    # ----------------------------------------------------------------------
    # Facebook Page Automation Profiles
    # ----------------------------------------------------------------------

    @staticmethod
    def get_page_automation_profile(page_id: int) -> Dict[str, Any]:
        """Returns the active automation profile for a Facebook page with defaults populated."""
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name, fb_page_id, utc_offset_hours, spread_start_hour, spread_end_hour, automation_profile FROM pages WHERE id = %s;", (page_id,))
                row = cur.fetchone()
                if not row:
                    return dict(DEFAULT_AUTOMATION_PROFILE)
                saved = row.get("automation_profile") or {}
                merged = dict(DEFAULT_AUTOMATION_PROFILE)
                merged.update(saved)
                if "utc_offset_hours" in row and row["utc_offset_hours"] is not None:
                    merged["utc_offset_hours"] = float(row["utc_offset_hours"])
                if "spread_start_hour" in row and row["spread_start_hour"] is not None:
                    merged["posting_window_start"] = int(row["spread_start_hour"])
                if "spread_end_hour" in row and row["spread_end_hour"] is not None:
                    merged["posting_window_end"] = int(row["spread_end_hour"])
                return merged

    @staticmethod
    def save_page_automation_profile(page_id: int, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Saves page automation profile, synchronizes universal rules, and triggers safe rebalancing."""
        current = PublicationPlanningService.get_page_automation_profile(page_id)
        current.update(profile_data)

        # Enforce numeric sanity
        current["daily_video_limit"] = max(0, int(current.get("daily_video_limit", 4)))
        current["daily_photo_limit"] = max(0, int(current.get("daily_photo_limit", 4)))
        current["daily_text_limit"] = max(0, int(current.get("daily_text_limit", 4)))
        current["posting_window_start"] = max(0, min(23, int(current.get("posting_window_start", 9))))
        current["posting_window_end"] = max(1, min(24, int(current.get("posting_window_end", 21))))
        current["min_spacing_minutes"] = max(10, int(current.get("min_spacing_minutes", 30)))

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE pages
                    SET automation_profile = %s,
                        spread_start_hour = %s,
                        spread_end_hour = %s,
                        utc_offset_hours = %s
                    WHERE id = %s
                    RETURNING *;
                """, (
                    json.dumps(current),
                    current["posting_window_start"],
                    current["posting_window_end"],
                    current.get("utc_offset_hours", 0.0),
                    page_id
                ))
                page_row = cur.fetchone()

                # Sync to platform_accounts & publication_rules if account exists
                if page_row:
                    fb_id = page_row["fb_page_id"]
                    cur.execute("SELECT id FROM platform_accounts WHERE external_account_id = %s LIMIT 1;", (fb_id,))
                    pa = cur.fetchone()
                    if pa:
                        cur.execute("""
                            INSERT INTO publication_rules (
                                brand_id, platform_account_id, video_enabled, photo_enabled, text_enabled,
                                daily_video_limit, daily_photo_limit, daily_text_limit,
                                spread_start_hour, spread_end_hour, min_spacing_minutes, is_active
                            ) VALUES (1, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (brand_id, platform_account_id) DO UPDATE SET
                                video_enabled = EXCLUDED.video_enabled,
                                photo_enabled = EXCLUDED.photo_enabled,
                                text_enabled = EXCLUDED.text_enabled,
                                daily_video_limit = EXCLUDED.daily_video_limit,
                                daily_photo_limit = EXCLUDED.daily_photo_limit,
                                daily_text_limit = EXCLUDED.daily_text_limit,
                                spread_start_hour = EXCLUDED.spread_start_hour,
                                spread_end_hour = EXCLUDED.spread_end_hour,
                                min_spacing_minutes = EXCLUDED.min_spacing_minutes,
                                is_active = EXCLUDED.is_active,
                                updated_at = NOW();
                        """, (
                            pa["id"],
                            current.get("video_enabled", True),
                            current.get("photo_enabled", True),
                            current.get("text_enabled", True),
                            current["daily_video_limit"],
                            current["daily_photo_limit"],
                            current["daily_text_limit"],
                            current["posting_window_start"],
                            current["posting_window_end"],
                            current["min_spacing_minutes"],
                            current.get("automation_enabled", False)
                        ))
            conn.commit()

        # Trigger safe rebalance of future scheduled posts
        rebalance_res = PublicationPlanningService.rebalance_page_schedule(page_id)
        return {
            "status": "saved",
            "profile": current,
            "rebalance": rebalance_res
        }

    # ----------------------------------------------------------------------
    # Dailymotion Automation Profiles
    # ----------------------------------------------------------------------

    @staticmethod
    def get_dailymotion_profile(channel_id: int) -> Dict[str, Any]:
        """Returns automation profile for Dailymotion channel, enforcing video-only platform capability."""
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, channel_id, channel_name, automation_profile FROM dailymotion_channels WHERE id = %s;", (channel_id,))
                row = cur.fetchone()
                if not row:
                    return dict(DAILYMOTION_DEFAULT_PROFILE)
                saved = row.get("automation_profile") or {}
                merged = dict(DAILYMOTION_DEFAULT_PROFILE)
                merged.update(saved)
                # Hard lock photo and text to False with platform capability declaration
                merged["photo_enabled"] = False
                merged["text_enabled"] = False
                merged["daily_photo_limit"] = 0
                merged["daily_text_limit"] = 0
                merged["capabilities"] = {
                    "video": {"supported": True},
                    "photo": {"supported": False, "reason": "Dailymotion API does not support photo feed posts"},
                    "text": {"supported": False, "reason": "Dailymotion API does not support text/status posts"}
                }
                return merged

    @staticmethod
    def save_dailymotion_profile(channel_id: int, profile_data: Dict[str, Any]) -> Dict[str, Any]:
        """Saves Dailymotion automation profile enforcing video-only support."""
        current = PublicationPlanningService.get_dailymotion_profile(channel_id)
        current.update(profile_data)
        # Lock video-only constraints
        current["photo_enabled"] = False
        current["text_enabled"] = False
        current["daily_photo_limit"] = 0
        current["daily_text_limit"] = 0
        current["daily_video_limit"] = max(0, int(current.get("daily_video_limit", 4)))

        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE dailymotion_channels
                    SET automation_profile = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING *;
                """, (json.dumps(current), channel_id))
            conn.commit()

        return {"status": "saved", "profile": current}

    # ----------------------------------------------------------------------
    # Dry-Run / Preview Engine
    # ----------------------------------------------------------------------

    @staticmethod
    def preview_page_schedule(page_id: int, custom_profile: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Calculates an exact dry-run preview timeline:
        DATE | TIME | CONTENT TYPE | CONTENT TITLE | PLATFORM | ACCOUNT
        Never writes records to database.
        """
        page = None
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM pages WHERE id = %s;", (page_id,))
                page = cur.fetchone()

        if not page:
            return {"error": "Page not found", "timeline": []}

        profile = custom_profile or PublicationPlanningService.get_page_automation_profile(page_id)
        if not profile.get("automation_enabled", False):
            return {
                "status": "disabled",
                "message": "Automation profile is turned OFF.",
                "timeline": []
            }

        quotas = {}
        if profile.get("photo_enabled", True):
            quotas["photo"] = int(profile.get("daily_photo_limit", 4))
        if profile.get("video_enabled", True):
            quotas["video"] = int(profile.get("daily_video_limit", 4))
        if profile.get("text_enabled", True):
            quotas["text"] = int(profile.get("daily_text_limit", 4))

        if sum(quotas.values()) == 0:
            return {"status": "empty", "message": "All content types are disabled or set to 0.", "timeline": []}

        now = dt.datetime.now(dt.timezone.utc)
        page_sched_cfg = {
            "utc_offset_hours": profile.get("utc_offset_hours", 0.0),
            "schedule_mode": "spread",
            "spread_start_hour": profile.get("posting_window_start", 9),
            "spread_end_hour": profile.get("posting_window_end", 21)
        }

        min_gap = int(profile.get("min_spacing_minutes", 30))
        schedule_slots = scheduling.compute_interleaved_schedule(
            page_sched_cfg,
            quotas,
            now_utc=now,
            min_gap_minutes=min_gap
        )

        # Inspect unposted ready items in Content Library to assign to slots
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, file_name, caption, media_type, validation_status, content_source_id
                    FROM content_items
                    WHERE coalesce(validation_status, 'ready') = 'ready'
                    ORDER BY discovered_at ASC;
                """)
                avail_items = cur.fetchall()

        by_type = {
            "photo": [i for i in avail_items if i.get("media_type") == "photo"],
            "video": [i for i in avail_items if i.get("media_type") in ("video", "reel")],
            "text": [i for i in avail_items if i.get("media_type") == "text"]
        }

        indices = {"photo": 0, "video": 0, "text": 0}
        timeline = []
        offset = dt.timedelta(hours=float(profile.get("utc_offset_hours", 0.0)))

        for sched_time_utc, ctype in schedule_slots:
            local_dt = sched_time_utc + offset
            items_pool = by_type.get(ctype, [])
            curr_idx = indices[ctype]
            assigned_item = items_pool[curr_idx] if curr_idx < len(items_pool) else None
            indices[ctype] += 1

            title_display = assigned_item.get("file_name") or assigned_item.get("caption", "")[:35] if assigned_item else f"Generated {ctype.title()} #{indices[ctype]}"
            source_display = "Google Drive" if (assigned_item and assigned_item.get("content_source_id")) else "Content Machine / AI"

            timeline.append({
                "date": local_dt.strftime("%b %d, %Y"),
                "time": local_dt.strftime("%I:%M %p"),
                "scheduled_time_utc": sched_time_utc.isoformat(),
                "content_type": ctype.upper(),
                "content_id": assigned_item["id"] if assigned_item else None,
                "content_title": title_display,
                "source": source_display,
                "platform": "Facebook",
                "account": page["name"],
                "status": "ready" if assigned_item else "needs_generation"
            })

        return {
            "status": "ok",
            "page_name": page["name"],
            "total_slots": len(timeline),
            "quotas": quotas,
            "timeline": timeline
        }

    # ----------------------------------------------------------------------
    # Safe Dynamic Rebalancing Engine
    # ----------------------------------------------------------------------

    @staticmethod
    def rebalance_page_schedule(page_id: int) -> Dict[str, Any]:
        """
        Rebalances all future unposted schedule slots across the posting window.
        CRITICAL RULES:
          1. Existing published content is NEVER modified.
          2. Only unposted future publications (status = 'queued') are rescheduled.
          3. Distributes items fairly using fair-share interleaving.
          4. No duplicate slots or overlapping leases.
        """
        profile = PublicationPlanningService.get_page_automation_profile(page_id)
        if not profile.get("automation_enabled", False):
            logger.info(f"Page {page_id} automation profile is disabled; skipping rebalance.")
            return {"status": "skipped", "reason": "automation_disabled"}

        now = dt.datetime.now(dt.timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        # 1. Count already published items today per type
        posted_today_counts = {"photo": 0, "video": 0, "text": 0}
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.content_type, count(*) as count
                    FROM publications p
                    JOIN platform_accounts pa ON pa.id = p.platform_account_id
                    JOIN pages pg ON pg.fb_page_id = pa.external_account_id
                    WHERE pg.id = %s
                      AND p.status IN ('published', 'verified')
                      AND p.published_at >= %s
                    GROUP BY p.content_type;
                """, (page_id, today_start))
                for r in cur.fetchall():
                    ctype = "video" if r["content_type"] in ("reel", "video") else r["content_type"]
                    posted_today_counts[ctype] = r["count"]

        # 2. Get active quotas from profile
        new_quotas = {}
        if profile.get("photo_enabled", True):
            new_quotas["photo"] = int(profile.get("daily_photo_limit", 4))
        if profile.get("video_enabled", True):
            new_quotas["video"] = int(profile.get("daily_video_limit", 4))
        if profile.get("text_enabled", True):
            new_quotas["text"] = int(profile.get("daily_text_limit", 4))

        # 3. Calculate remaining slots for today via scheduling.rebalance_timeline
        page_sched_cfg = {
            "utc_offset_hours": profile.get("utc_offset_hours", 0.0),
            "schedule_mode": "spread",
            "spread_start_hour": profile.get("posting_window_start", 9),
            "spread_end_hour": profile.get("posting_window_end", 21)
        }
        min_gap = int(profile.get("min_spacing_minutes", 30))

        remaining_slots = scheduling.rebalance_timeline(
            page_sched_cfg,
            new_quotas,
            posted_today_counts=posted_today_counts,
            now_utc=now,
            min_gap_minutes=min_gap
        )

        # 4. Fetch future unposted publications for this page
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.id, p.content_type, p.scheduled_at
                    FROM publications p
                    JOIN platform_accounts pa ON pa.id = p.platform_account_id
                    JOIN pages pg ON pg.fb_page_id = pa.external_account_id
                    WHERE pg.id = %s
                      AND p.status = 'queued'
                      AND p.scheduled_at >= %s
                    ORDER BY p.scheduled_at ASC;
                """, (page_id, now))
                queued_pubs = cur.fetchall()

        if not queued_pubs:
            return {
                "status": "rebalanced",
                "rebalanced_count": 0,
                "remaining_today_capacity": len(remaining_slots),
                "message": "No future queued publications found to reposition."
            }

        # 5. Reassign future queued publications to newly calculated balanced timestamps
        rebalanced_count = 0
        with db._conn() as conn:
            with conn.cursor() as cur:
                for idx, pub in enumerate(queued_pubs):
                    if idx < len(remaining_slots):
                        new_time, _ = remaining_slots[idx]
                    else:
                        # Extra items placed after window with min_gap increments
                        last_time = remaining_slots[-1][0] if remaining_slots else now
                        new_time = last_time + dt.timedelta(minutes=min_gap * (idx - len(remaining_slots) + 1))

                    cur.execute("""
                        UPDATE publications
                        SET scheduled_at = %s,
                            updated_at = NOW()
                        WHERE id = %s;
                    """, (new_time, pub["id"]))

                    # Update associated durable_jobs
                    cur.execute("""
                        UPDATE durable_jobs
                        SET available_at = %s,
                            updated_at = NOW()
                        WHERE job_type = 'publication'
                          AND (payload->>'publication_id')::bigint = %s;
                    """, (new_time, pub["id"]))

                    rebalanced_count += 1
            conn.commit()

        logger.info(f"Rebalanced {rebalanced_count} future publications for Page #{page_id}.")
        return {
            "status": "rebalanced",
            "page_id": page_id,
            "rebalanced_count": rebalanced_count,
            "remaining_today_slots": len(remaining_slots)
        }

    # ----------------------------------------------------------------------
    # Horizon Planner (Durable Enqueueing)
    # ----------------------------------------------------------------------

    @staticmethod
    def plan_horizon(
        brand_id: int,
        platform_account_id: int,
        days_ahead: int = 1
    ) -> Dict[str, Any]:
        """
        Builds the publication horizon for the next N days.
        Fairly interleaves Video, Photo, and Text across the window.
        Guarantees that content shortages do not create failed jobs.
        """
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM platform_accounts WHERE id = %s;", (platform_account_id,))
                account = cur.fetchone()

        if not account or not account["is_active"]:
            return {"status": "inactive_account", "scheduled_count": 0}

        platform = account["platform"]
        now = dt.datetime.now(dt.timezone.utc)

        # Look up corresponding page or publication_rule
        rule = None
        utc_offset_hours = 0.0
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM publication_rules WHERE platform_account_id = %s;", (platform_account_id,))
                rule = cur.fetchone()
                if rule and rule.get("utc_offset_hours") is not None:
                    utc_offset_hours = float(rule["utc_offset_hours"])
                else:
                    cur.execute("""
                        SELECT p.utc_offset_hours, p.automation_profile
                        FROM pages p
                        JOIN platform_accounts pa ON pa.external_account_id = p.fb_page_id
                        WHERE pa.id = %s;
                    """, (platform_account_id,))
                    prow = cur.fetchone()
                    if prow:
                        prof = prow.get("automation_profile") or {}
                        if isinstance(prof, str):
                            try:
                                prof = json.loads(prof)
                            except Exception:
                                prof = {}
                        utc_offset_hours = float(prof.get("utc_offset_hours", prow.get("utc_offset_hours") or 0.0))
                    elif platform == "dailymotion":
                        cur.execute("""
                            SELECT dm.automation_profile
                            FROM dailymotion_channels dm
                            JOIN platform_accounts pa ON pa.external_account_id = dm.channel_id
                            WHERE pa.id = %s;
                        """, (platform_account_id,))
                        dm_row = cur.fetchone()
                        if dm_row and dm_row.get("automation_profile"):
                            prof = dm_row["automation_profile"]
                            if isinstance(prof, str):
                                try:
                                    prof = json.loads(prof)
                                except Exception:
                                    prof = {}
                            utc_offset_hours = float(prof.get("utc_offset_hours", 0.0))

        quotas = {}
        if rule:
            if rule.get("photo_enabled"):
                quotas["photo"] = int(rule.get("daily_photo_limit", 4))
            if rule.get("video_enabled"):
                quotas["video"] = int(rule.get("daily_video_limit", 4))
            if rule.get("text_enabled"):
                quotas["text"] = int(rule.get("daily_text_limit", 4))
            start_h = rule.get("spread_start_hour", 9)
            end_h = rule.get("spread_end_hour", 21)
            min_gap = rule.get("min_spacing_minutes", 30)
        else:
            quotas = {"photo": 4, "video": 4, "text": 4} if platform == "facebook" else {"video": 4}
            start_h, end_h, min_gap = 9, 21, 30

        # Validate capabilities
        filtered_quotas = {}
        for ctype, target in quotas.items():
            ok, _ = CapabilityRegistry.validate_request(platform, ctype)
            if ok:
                filtered_quotas[ctype] = target

        if not filtered_quotas or sum(filtered_quotas.values()) == 0:
            return {"status": "empty_quotas", "scheduled_count": 0}

        page_sched_cfg = {
            "utc_offset_hours": utc_offset_hours,
            "schedule_mode": "spread",
            "spread_start_hour": start_h,
            "spread_end_hour": end_h
        }

        # Interleaved sequence of (timestamp, media_type)
        slots = scheduling.compute_interleaved_schedule(
            page_sched_cfg,
            filtered_quotas,
            now_utc=now,
            min_gap_minutes=min_gap
        )

        scheduled_total = 0
        unfulfilled = {}

        for sched_time, media_type in slots:
            # Query unposted and ready item for this media_type
            type_filter = ("video", "reel") if media_type == "video" else (media_type,)
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT ci.* FROM content_items ci
                        WHERE ci.media_type = ANY(%s)
                          AND coalesce(ci.validation_status, 'ready') = 'ready'
                          AND NOT EXISTS (
                              SELECT 1 FROM publications p
                              WHERE p.platform_account_id = %s
                                AND p.content_item_id = ci.id
                                AND p.status IN ('queued', 'uploading', 'processing', 'published', 'verified')
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM posted_ledger l
                              WHERE l.source_video_id = ci.source_video_id
                                AND l.platform = %s
                          )
                        ORDER BY ci.discovered_at ASC
                        LIMIT 1;
                    """, (list(type_filter), platform_account_id, platform))
                    item = cur.fetchone()

            if not item:
                unfulfilled[media_type] = unfulfilled.get(media_type, 0) + 1
                continue

            cid = item["id"]
            idemp_key = PublishingOrchestrator.generate_idempotency_key(
                platform=platform,
                account_id=platform_account_id,
                content_item_id=cid,
                content_type=media_type,
                scheduled_ts=int(sched_time.timestamp())
            )

            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO publications (
                            platform_account_id, content_item_id, content_type,
                            title, caption, hashtags, scheduled_at,
                            status, idempotency_key
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s)
                        ON CONFLICT (idempotency_key) DO NOTHING
                        RETURNING id;
                    """, (
                        platform_account_id,
                        cid,
                        media_type,
                        item.get("file_name", ""),
                        item.get("caption", ""),
                        item.get("hashtags", []),
                        sched_time,
                        idemp_key
                    ))
                    pub_row = cur.fetchone()
                    if pub_row:
                        pub_id = pub_row["id"]
                        DurableJobService.enqueue(
                            job_type="publication",
                            payload={"publication_id": pub_id, "platform": platform},
                            priority=5,
                            available_at=sched_time,
                            idempotency_key=f"pub_job_{pub_id}"
                        )
                        scheduled_total += 1
                conn.commit()

        logger.info(f"Planned horizon for Account #{platform_account_id}: {scheduled_total} enqueued. Unfulfilled capacity: {unfulfilled}")
        return {
            "status": "planned",
            "scheduled_count": scheduled_total,
            "unfulfilled_capacity": unfulfilled
        }

    @staticmethod
    def maintain_continuous_buffer(page_id: Optional[int] = None, target_buffer: int = 12) -> Dict[str, Any]:
        """
        Maintains an autonomous 24/7 future publishing buffer (Sections 15, 16, 35).
        Inspects future queued capacity:
        - If future unposted queue is below target_buffer, automatically triggers
          Content Engine to synthesize and validate new content according to account niche.
        - Automatically registers into Content Library, queues, and fair-share interleaves.
        - Preserves all historical published posts and existing schedules without duplication.
        """
        from content_machine import ContentMachine

        pages_to_check = []
        with db._conn() as conn:
            with conn.cursor() as cur:
                if page_id:
                    cur.execute("SELECT id, name, fb_page_id, automation_profile FROM pages WHERE id = %s;", (page_id,))
                else:
                    cur.execute("SELECT id, name, fb_page_id, automation_profile FROM pages;")
                pages_to_check = cur.fetchall()

        summary = {"pages_checked": len(pages_to_check), "items_generated": 0, "rebalanced_pages": 0}
        now_utc = dt.datetime.now(dt.timezone.utc)

        for p in pages_to_check:
            pid = p["id"]
            prof = p.get("automation_profile") or {}
            if isinstance(prof, str):
                try:
                    prof = json.loads(prof)
                except Exception:
                    prof = {}

            if not prof.get("automation_enabled", False):
                continue

            # Strict source guard: Never auto-generate synthetic/AI items if page is configured for Google Drive or not explicitly set to generated/both
            content_source = prof.get("content_source", "drive")
            content_source_mode = prof.get("content_source_mode", "")
            if content_source == "drive" or content_source_mode == "drive_only" or content_source not in ("generated", "both"):
                continue

            photo_lim = int(prof.get("daily_photo_limit", 0))
            text_lim = int(prof.get("daily_text_limit", 0))
            if photo_lim <= 0 and text_lim <= 0:
                continue

            target_depth = int(prof.get("target_buffer_posts", target_buffer))

            # Count future queued publications for this page
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT count(*) as queued_future
                        FROM publications pub
                        JOIN platform_accounts pa ON pa.id = pub.platform_account_id
                        WHERE pa.external_account_id = %s
                          AND pub.status IN ('queued', 'retrying')
                          AND pub.scheduled_at >= %s;
                    """, (p["fb_page_id"], now_utc))
                    row = cur.fetchone()
                    queued_future = int(row["queued_future"] if row else 0)

            if queued_future < target_depth:
                deficit = target_depth - queued_future
                logger.info(f"Page #{pid} ({p['name']}) queue depth {queued_future} < target {target_depth}. Auto-generating {deficit} items...")

                # Determine active formats
                enabled_types = []
                if prof.get("photo_enabled", True):
                    enabled_types.append("photo")
                if prof.get("text_enabled", True):
                    enabled_types.append("text")
                if not enabled_types:
                    enabled_types = ["photo", "text"]

                niche = prof.get("niche") or p.get("name") or "Insights"
                generated_this_page = 0

                for idx in range(min(deficit, 6)):  # Batch up to 6 per sweep to preserve rate limits
                    chosen_type = enabled_types[idx % len(enabled_types)]
                    try:
                        new_items = ContentMachine.generate_asset_batch(
                            niche=niche,
                            topic="",
                            quantity=1,
                            target_platform="facebook",
                            content_format=chosen_type,
                            page_id=pid,
                            auto_queue=True
                        )
                        generated_this_page += len(new_items)
                    except Exception as g_err:
                        logger.warning(f"Continuous buffer generation error for Page #{pid}: {g_err}")

                if generated_this_page > 0:
                    PublicationPlanningService.rebalance_page_schedule(pid)
                    summary["items_generated"] += generated_this_page
                    summary["rebalanced_pages"] += 1

        return summary
