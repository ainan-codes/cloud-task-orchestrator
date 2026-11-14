"""
app.py — the backend API for the Reels Dashboard (Phases 2–6).

Design principle you asked for: YOU decide every post time. The dashboard
sends an explicit `scheduled_time_utc` for each post; this backend only ever
publishes items whose time you set has arrived. It never invents times.

Endpoints
  Pages
    GET    /api/pages                 list pages + stats (optional
                                      ?limit&offset&search for 100+ pages)
    POST   /api/pages                 add / update a page
    DELETE /api/pages/{id}            remove a page
    POST   /api/pages/{id}/sync       import that page's Drive folder content
                                      (runs in the background; returns immediately)
    GET    /api/pages/{id}/sync/status  poll: {status, found, added} (a
                                      "running" lock with no progress for
                                      5 minutes auto-expires here — a slow
                                      but still-progressing sync does not)
    POST   /api/pages/{id}/sync/reset  force-clear a stuck sync lock
    GET    /api/pages/{id}/health     live token check + queue/content health
    GET    /api/pages/{id}/calendar   scheduled posts for one local week
  Content
    GET    /api/pages/{id}/content    ONE page of content items (with
                                      posted/scheduled status);
                                      ?limit=50&offset=0&search=text
                                      -> {"items": [...], "total": N}
  Schedule (you control the times)
    GET    /api/schedule              list the queue
    POST   /api/schedule              schedule one item at a time YOU pick
    PATCH  /api/schedule/{id}         change the time
    DELETE /api/schedule/{id}         cancel a pending post
    POST   /api/schedule/suggest      OPTIONAL: return suggested times (you may
                                      accept or ignore — nothing is scheduled)
    POST   /api/pages/{id}/refresh-pending-captions
                                      rebuild caption_used for every pending
                                      post from current content/page data
    POST   /api/pages/{id}/auto-schedule  schedule EVERY unposted item at once,
                                      posts_per_day-at-a-time across days,
                                      cycling through post_times (page-local).
                                      dry_run: true previews without writing.
  Runner (called by your free cron trigger)
    POST   /api/run                   starts posting in the background and
                                      returns instantly; also retries cooled-
                                      down failures and auto-refills queues
                                      running low
  Analytics
    GET    /api/pages/{id}/analytics  performance per posted item
    POST   /api/analytics/refresh     pull fresh numbers from Facebook

Auth: set RUN_TOKEN in the environment; /api/run requires header
`X-Run-Token` to match, so only your cron job can trigger posting.
"""

import datetime as dt
import os
import re
import sys
import threading
import traceback
from pathlib import Path

# Ensure backend directory is in sys.path
_backend_dir = str(Path(__file__).resolve().parent)
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

from fastapi import FastAPI, HTTPException, Header, Body, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from urllib.parse import urlencode

import captions
import credentials
import db
import drive
import gemini_ai
import logger as applog
import poster
import scheduling
import snapchat_api
import snapchat_crypto
import uuid
import snapchat_media
import snapchat_oauth
import snapchat_poster
import snapchat_runner
from snapchat_errors import SnapError

applog.install()
import logging
logger = logging.getLogger("app")

app = FastAPI(
    title="Cloud Task Orchestrator API",
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
)

from youtube_api import youtube_router
from rumble_api import rumble_router
from dailymotion_api import dailymotion_router
from flow_api import flow_router
from universal_api import universal_router
from studio_api import studio_router

app.include_router(youtube_router)
app.include_router(rumble_router)
app.include_router(dailymotion_router)
app.include_router(flow_router)
app.include_router(universal_router)
app.include_router(studio_router)

# CORS: the dashboard (Vercel) calls this API from the browser.
_origins = os.environ.get("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Autonomous Startup Recovery & Watchdog Lifecycle
# ============================================================

def _run_periodic_watchdog():
    """Background daemon thread executing periodic health & healing sweeps."""
    import time
    while True:
        try:
            time.sleep(120)
            from core.watchdog import WatchdogService
            WatchdogService.run_sweep()
        except Exception as e:
            logger.warning(f"Periodic watchdog sweep error: {e}")


@app.on_event("startup")
def startup_recovery():
    """
    Autonomous Startup Recovery Engine:
    1. Reclaims expired worker leases.
    2. Recovers zombie publications stuck in uploading/processing.
    3. Reconciles platform_accounts and publication_rules.
    4. Starts autonomous watchdog background thread.
    """
    if os.environ.get("VERCEL"):
        return

    logger.info("Initializing Autonomous Startup Recovery...")
    try:
        from core.watchdog import WatchdogService
        sweep_res = WatchdogService.run_sweep()
        logger.info(f"Startup sweep complete: {sweep_res}")
    except Exception as e:
        logger.warning(f"Startup recovery sweep warning: {e}")

    # Synchronize platform_accounts for any unlinked pages
    try:
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO platform_accounts (brand_id, platform, external_account_id, display_name, health_status, is_active)
                    SELECT 1, 'facebook', p.fb_page_id, p.name, 'healthy', true
                    FROM pages p
                    WHERE NOT EXISTS (
                        SELECT 1 FROM platform_accounts pa WHERE pa.external_account_id = p.fb_page_id
                    )
                    ON CONFLICT DO NOTHING;
                """)
            conn.commit()
    except Exception as e:
        logger.debug(f"Platform accounts sync note: {e}")

    # Launch autonomous watchdog thread
    w_thread = threading.Thread(target=_run_periodic_watchdog, daemon=True, name="ReelLedgerWatchdog")
    w_thread.start()
    logger.info("Autonomous Watchdog daemon thread started.")



def _iso(v):
    return v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v


def _clean(row):
    if row is None:
        return None
    return {k: _iso(v) for k, v in dict(row).items()}


def _clean_list(rows):
    return [_clean(r) for r in rows]


def _migration_error(e):
    """True if this exception looks like a missing-migration column/table."""
    return "does not exist" in str(e)


MIGRATION_MSG = (
    "Database migration required — run db/migrations/001_upgrade.sql in the "
    "Supabase SQL Editor, then try again."
)


# ============================ HEALTH & READINESS PROBES ============================
@app.get("/api/health")
def health_check():
    """Liveness probe: returns 200 OK if service is up and responsive."""
    return {
        "status": "healthy",
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "service": "reel-ledger-api",
        "version": "2.1.0",
    }


@app.get("/api/ready")
def readiness_check():
    """Readiness probe: validates database connectivity and latency."""
    t0 = dt.datetime.now(dt.timezone.utc)
    try:
        # Fast lightweight query
        db.list_pages(active_only=False, limit=1, offset=0)
        latency_ms = round((dt.datetime.now(dt.timezone.utc) - t0).total_seconds() * 1000, 2)
        return {
            "status": "ready",
            "database": "connected",
            "latency_ms": latency_ms,
            "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "disconnected",
                "error": str(e),
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        )


@app.get("/api/system/service-account")
def get_service_account():
    """Returns the email address of the active Google Service Account for easy sharing."""
    email = drive.get_service_account_email()
    return {"service_account_email": email}


# ============================ PAGES ============================
class PageIn(BaseModel):
    name: str
    fb_page_id: str
    fb_access_token: str
    drive_folder_id: Optional[str] = None
    utc_offset_hours: float = 0.0
    schedule_mode: str = "spread"
    spread_start_hour: int = 9
    spread_end_hour: int = 21
    fixed_times: List[str] = ["09:00", "13:00", "18:00"]
    per_run: int = 6
    extra_hashtags: List[str] = []
    caption_footer: str = ""
    is_active: bool = True
    hashtag_pool: List[str] = []


@app.get("/api/pages")
def get_pages(limit: Optional[int] = None, offset: int = 0, search: Optional[str] = None):
    """List pages with their stats.

    Stays cheap at 100+ pages: counts come from the page_stats VIEW (four
    aggregate COUNTs per page, computed in Postgres against the indexes from
    migration 002) — content and schedule rows are never loaded into memory
    to be counted here. Two queries total, regardless of page count.

    Pass limit (+ optional offset/search) to page the list; the response then
    becomes {"items": [...], "total": N}. Without limit it returns the plain
    array it always did, which the jobs and older clients rely on."""
    pages = db.list_pages(active_only=False, limit=limit, offset=offset, search=search)
    if not pages:
        return [] if limit is None else {"items": [], "total": 0}
    # Only the returned page's stats — not the whole account's.
    page_ids = [p["id"] for p in pages]
    stats = {s["page_id"]: _clean(s) for s in db.page_stats(page_ids)}
    out = []
    for p in pages:
        d = _clean(p)
        # Indicate whether a token is already configured without leaking secret values
        d["has_token"] = bool(p.get("fb_access_token") or p.get("fb_access_token_encrypted"))
        # Neither the plaintext token nor its ciphertext ever leaves the API.
        d.pop("fb_access_token", None)
        d.pop("fb_access_token_encrypted", None)
        d["stats"] = stats.get(p["id"], {})
        out.append(d)
    if limit is None:
        return out
    return {"items": out, "total": db.count_pages(active_only=False, search=search)}


def _clean_page_input(data: dict) -> dict:
    data["name"] = (data.get("name") or "").strip()
    data["fb_access_token"] = (data.get("fb_access_token") or "").strip()

    # Clean numeric FB Page ID even if full URL or spaces are passed
    raw_fb_id = str(data.get("fb_page_id") or "").strip()
    m_fb = re.search(r"\d{10,}", raw_fb_id)
    if m_fb:
        data["fb_page_id"] = m_fb.group(0)
    else:
        data["fb_page_id"] = raw_fb_id

    # Clean Google Drive Folder ID even if full URL is passed
    raw_folder = str(data.get("drive_folder_id") or "").strip()
    data["drive_folder_id"] = drive.clean_folder_id(raw_folder) if raw_folder else None

    return data


@app.post("/api/pages")
def add_page(page: PageIn):
    try:
        clean_data = _clean_page_input(page.model_dump())
        saved = db.upsert_page(**clean_data)
    except Exception as e:
        if _migration_error(e):
            raise HTTPException(503, MIGRATION_MSG)
        raise
    d = _clean(saved)
    d["has_token"] = bool(saved.get("fb_access_token") or saved.get("fb_access_token_encrypted"))
    d.pop("fb_access_token", None)
    d.pop("fb_access_token_encrypted", None)
    return d


@app.put("/api/pages/{page_id}")
def edit_page(page_id: int, page: PageIn):
    try:
        clean_data = _clean_page_input(page.model_dump())
        saved = db.update_page(page_id, **clean_data)
    except Exception as e:
        if _migration_error(e):
            raise HTTPException(503, MIGRATION_MSG)
        raise
    if not saved:
        raise HTTPException(404, "Page not found")
    d = _clean(saved)
    d["has_token"] = bool(saved.get("fb_access_token") or saved.get("fb_access_token_encrypted"))
    d.pop("fb_access_token", None)
    d.pop("fb_access_token_encrypted", None)
    return d


@app.delete("/api/pages/{page_id}")
def remove_page(page_id: int):
    ok = db.delete_page(page_id)
    if not ok:
        raise HTTPException(404, "Page not found")
    return {"deleted": True}


class TokenTestIn(BaseModel):
    token: Optional[str] = None
    fb_page_id: Optional[str] = None


@app.post("/api/pages/{page_id}/test-token")
def test_page_token(page_id: int, req: TokenTestIn = TokenTestIn()):
    """Live validation of a page token against Facebook Graph API.
    Supports validating a freshly entered token or testing the page's existing encrypted token."""
    fb_id = req.fb_page_id
    token = (req.token or "").strip()
    if page_id > 0:
        p = db.get_page(page_id)
        if not p:
            raise HTTPException(404, "Page not found")
        if not fb_id:
            fb_id = p.get("fb_page_id")
        if not token:
            token = p.get("fb_access_token")
    if not token:
        raise HTTPException(400, "No access token provided or found for this page")
    fb_id = (fb_id or "me").strip()
    info = poster.inspect_token(fb_id, token)
    if page_id > 0:
        status_str = "valid" if info.get("valid") else "invalid"
        db.update_page_token_health(page_id, status_str, info.get("error"))
    return info


@app.post("/api/pages/test-token")
def test_new_token(req: TokenTestIn):
    """Live validation of a page token before a page is created."""
    token = (req.token or "").strip()
    if not token:
        raise HTTPException(400, "No token provided")
    fb_id = (req.fb_page_id or "me").strip()
    return poster.inspect_token(fb_id, token)


# Sync progress is persisted on the pages row (sync_status/sync_started_at/
# sync_heartbeat_at) rather than kept in memory, so it survives a backend
# restart instead of getting stuck or silently resetting. Staleness is judged
# against sync_heartbeat_at (bumped on every progress update, which now fires
# roughly once per file) rather than sync_started_at — a large folder can
# legitimately take much longer than this to finish one pass, but it keeps
# making progress the whole time. Only a lock that has gone quiet for this
# long is treated as a crashed/incomplete sync and cleared automatically.
SYNC_STALE_AFTER = dt.timedelta(minutes=5)


def _idle_sync_state(**overrides):
    state = {
        "status": "complete", "found": 0, "added": 0, "updated": 0,
        "duplicates_skipped": 0, "subfolders_scanned": 0, "total_in_library": 0,
        "matched_captions": 0, "missing_captions": 0,
    }
    state.update(overrides)
    return state


def _run_sync(page_id: int):
    try:
        page = db.get_page(page_id)
        folder = page.get("drive_folder_id") if page else None
        print(f"[sync debug] page={page_id} using drive_folder_id={folder!r}")
        if not folder:
            db.set_sync_lock(page_id, _idle_sync_state())
            return

        def progress(n):
            db.set_sync_lock(page_id, _idle_sync_state(status="running", found=n))

        known_captions = db.get_known_captions_map()
        items, subfolders_scanned = drive.list_folder(folder, progress_cb=progress, known_captions=known_captions)
        matched_captions = sum(1 for it in items if it.get("caption") or it.get("hashtags"))
        missing_captions = len(items) - matched_captions

        added, updated, duplicates_skipped = db.sync_content(page_id, items)
        try:
            db.set_last_synced_at(page_id)
        except Exception:
            pass  # pre-migration: last_synced_at column not there yet — harmless
        total_in_library = db.count_content(page_id)
        db.set_sync_lock(page_id, _idle_sync_state(
            found=len(items),
            added=added,
            updated=updated,
            duplicates_skipped=duplicates_skipped,
            subfolders_scanned=subfolders_scanned,
            total_in_library=total_in_library,
            matched_captions=matched_captions,
            missing_captions=missing_captions,
        ))
        db.log_activity(
            page_id, "sync",
            f"Synced: {added} new video{'s' if added != 1 else ''} found, "
            f"{subfolders_scanned} subfolder{'s' if subfolders_scanned != 1 else ''} scanned, "
            f"{duplicates_skipped} duplicate{'s' if duplicates_skipped != 1 else ''} skipped, "
            f"{total_in_library} total video{'s' if total_in_library != 1 else ''} in library now"
        )
        print(f"[sync page={page_id}] found {len(items)}, added {added}, updated {updated}, "
              f"duplicates {duplicates_skipped}, subfolders {subfolders_scanned}, "
              f"matched {matched_captions}, missing {missing_captions}")
    except Exception as e:
        db.set_sync_lock(page_id, _idle_sync_state(error=str(e)))
        print(f"[sync page={page_id}] FAILED")
        traceback.print_exc()


def _sync_lock_is_stale(heartbeat_at):
    if not heartbeat_at:
        return True
    now = dt.datetime.now(dt.timezone.utc)
    if heartbeat_at.tzinfo is None:
        heartbeat_at = heartbeat_at.replace(tzinfo=dt.timezone.utc)
    return (now - heartbeat_at) > SYNC_STALE_AFTER


@app.post("/api/pages/{page_id}/sync")
def sync_page(page_id: int):
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    if not page.get("drive_folder_id"):
        raise HTTPException(400, "This page has no Drive folder set. Add its folder ID first.")

    state, _started_at, heartbeat_at = db.get_sync_lock(page_id)
    if state and state.get("status") == "running" and not _sync_lock_is_stale(heartbeat_at):
        return state

    db.start_sync_lock(page_id, _idle_sync_state(status="running"))
    _run_sync(page_id)
    final_state, _, _ = db.get_sync_lock(page_id)
    return final_state or {"status": "complete"}


@app.get("/api/pages/{page_id}/sync/status")
def get_sync_status(page_id: int):
    state, _started_at, heartbeat_at = db.get_sync_lock(page_id)
    if not state:
        return _idle_sync_state(status="idle")
    if state.get("status") == "running" and _sync_lock_is_stale(heartbeat_at):
        # No progress update in SYNC_STALE_AFTER — a crashed/incomplete sync
        # from a previous run (e.g. a redeploy mid-sync), not just a slow one
        # (a slow-but-alive sync keeps bumping the heartbeat and never hits
        # this). Auto-expire so the page isn't stuck "running" forever.
        minutes = int(SYNC_STALE_AFTER.total_seconds() // 60)
        state = _idle_sync_state(error=f"Sync timed out (no progress for over {minutes} minutes) — try again.")
        db.set_sync_lock(page_id, state)
    return state


@app.post("/api/pages/{page_id}/sync/reset")
def reset_sync_lock(page_id: int):
    """Manually clear a stuck sync lock without waiting for the staleness
    timeout — e.g. an admin confirms the previous sync process is dead."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    db.set_sync_lock(page_id, _idle_sync_state(status="idle"))
    return {"status": "idle"}


# ---- health ----
@app.get("/api/pages/{page_id}/health")
def page_health(page_id: int):
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")

    token_valid, token_detail = poster.check_token(page["fb_page_id"], page["fb_access_token"])
    # Persist the health check outcome to the database
    db.update_page_token_health(page_id, "valid" if token_valid else "invalid", token_detail if not token_valid else None)

    stat = db.page_stat(page_id) or {}
    queue_depth = stat.get("pending_scheduled", 0) or 0
    failed_count = stat.get("failed_count", 0) or 0
    abandoned_count = stat.get("abandoned_count", 0) or 0

    try:
        content_remaining = db.count_unposted(page_id)
    except Exception:
        content_remaining = stat.get("remaining_unposted")

    last_posted = db.last_posted_at(page_id)

    last_failed_at = last_error = None
    try:
        lf = db.last_failed(page_id)
        if lf:
            last_failed_at = lf["updated_at"]
            last_error = lf["error"]
    except Exception:
        pass  # pre-migration: updated_at column not there yet

    settings = page.get("last_auto_schedule_settings") or {}
    posts_per_day = settings.get("posts_per_day") or page.get("per_run") or 1
    days_remaining = round(queue_depth / posts_per_day, 1) if posts_per_day else None

    return {
        "token_valid": token_valid,
        "token_status": "valid" if token_valid else "invalid",
        "token_detail": token_detail,
        "queue_depth": queue_depth,
        "failed_count": failed_count,
        "abandoned_count": abandoned_count,
        "days_remaining": days_remaining,
        "last_posted_at": _iso(last_posted),
        "last_failed_at": _iso(last_failed_at),
        "last_error": last_error,
        "content_remaining": content_remaining,
    }


# ---- calendar ----
@app.get("/api/pages/{page_id}/calendar")
def get_calendar(page_id: int, week_start: str):
    """Every scheduled post whose UTC time falls inside the 7-day window
    starting at week_start (a YYYY-MM-DD date in the page's OWN local
    calendar — a page in Dubai's "Monday" is a different UTC range than a
    page at UTC+0's "Monday")."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    try:
        start_local_date = dt.date.fromisoformat(week_start)
    except ValueError:
        raise HTTPException(400, "week_start must be YYYY-MM-DD")

    offset = dt.timedelta(hours=float(page.get("utc_offset_hours", 0) or 0))
    start_utc = (
        dt.datetime(start_local_date.year, start_local_date.month, start_local_date.day) - offset
    ).replace(tzinfo=dt.timezone.utc)
    end_utc = start_utc + dt.timedelta(days=7)

    return _clean_list(db.calendar_posts(page_id, start_utc, end_utc))


# ============================ CONTENT ============================
MAX_CONTENT_PAGE = 200


@app.get("/api/pages/{page_id}/content")
def page_content(page_id: int, limit: int = 50, offset: int = 0,
                 search: Optional[str] = None, source_folder: Optional[str] = None):
    """ONE page of a page's content library — never the whole thing. A single
    Drive folder can hold thousands of videos, and fetching all of them for
    every view is the first thing that falls over at scale.

    search filters server-side on caption or file name (case-insensitive).
    source_folder filters to one Drive subfolder ('root' for the top-level
    folder). Returns {"items": [...], "total": N} where total counts
    everything matching the same filters, so the UI can say "Showing 1–50 of 756"."""
    limit = max(1, min(limit, MAX_CONTENT_PAGE))
    offset = max(0, offset)
    items = db.list_content(page_id, limit=limit, offset=offset, search=search, source_folder=source_folder)
    return {
        "items": _clean_list(items),
        "total": db.count_content(page_id, search=search, source_folder=source_folder),
    }


@app.get("/api/pages/{page_id}/content/folders")
def content_folders(page_id: int):
    """Distinct source_folder values in this page's library, for the Content
    page's 'Source Folder' filter dropdown."""
    return db.list_source_folders(page_id)


# ============================ SCHEDULE ============================
class ScheduleIn(BaseModel):
    content_item_id: int
    scheduled_time_utc: str   # ISO-8601 UTC time YOU choose


@app.get("/api/schedule")
def get_schedule(page_id: Optional[int] = None, status: Optional[str] = None):
    return _clean_list(db.list_scheduled(page_id=page_id, status=status))


@app.post("/api/schedule")
def schedule_one(item: ScheduleIn):
    ci = db.get_content_item(item.content_item_id)
    if not ci:
        raise HTTPException(404, "Content item not found")
    if db.already_posted(ci["page_id"], ci["source_video_id"]):
        raise HTTPException(409, "That video is already posted to this page (won't double-post).")
    page = db.get_page(ci["page_id"])
    when = dt.datetime.fromisoformat(item.scheduled_time_utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    caption = poster.build_caption(
        ci["caption"], ci["hashtags"],
        page.get("extra_hashtags"), page.get("caption_footer", ""),
        page.get("hashtag_pool"),
    )
    row = db.enqueue(ci["page_id"], ci["id"], when, caption)
    return _clean(row)


@app.patch("/api/schedule/{sched_id}")
def change_time(sched_id: int, new_time_utc: str = Body(..., embed=True)):
    when = dt.datetime.fromisoformat(new_time_utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    row = db.reschedule(sched_id, when)
    if not row:
        raise HTTPException(404, "Pending post not found")
    return _clean(row)


@app.delete("/api/schedule/{sched_id}")
def cancel(sched_id: int):
    ok = db.delete_scheduled(sched_id)
    if not ok:
        raise HTTPException(404, "Pending post not found")
    return {"cancelled": True}


@app.delete("/api/pages/{page_id}/schedule/all")
def cancel_all_scheduled(page_id: int):
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    count = db.delete_all_pending_scheduled(page_id)
    db.clear_last_auto_schedule_settings(page_id)
    db.log_activity(page_id, "cancel_all", f"Cancelled {count} pending scheduled post(s)")
    return {"cancelled": count}


@app.delete("/api/pages/{page_id}/posted-history")
def clear_posted_history(page_id: int):
    """Full page reset: wipe posted history AND the entire schedule (any
    status), then forget the remembered auto-schedule pattern. Every video
    for this page becomes available in the library again, since availability
    is derived from the absence of posted_ledger/scheduled_posts rows, not a
    status column on content_items."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    deleted_ledger = db.delete_posted_ledger(page_id)
    deleted_scheduled = db.delete_all_scheduled(page_id)
    db.clear_last_auto_schedule_settings(page_id)
    db.log_activity(
        page_id, "clear_posted_history",
        f"Cleared {deleted_ledger} posted record(s) and {deleted_scheduled} scheduled post(s)"
    )
    return {"deleted": deleted_ledger, "cleared_scheduled": deleted_scheduled}


@app.post("/api/pages/{page_id}/refresh-pending-captions")
def refresh_pending_captions(page_id: int):
    """Rebuild caption_used for every PENDING post on this page from the
    current content_items + pages data.

    The posting path already does this per-post the moment it fires, so this
    endpoint isn't required for correctness — it's here so a backlog that was
    scheduled before a Drive sync populated the captions can be fixed
    immediately and inspected in the UI, rather than only becoming correct as
    each post's turn comes around.

    Note: if the page has a hashtag_pool, every rebuild draws a fresh random
    sample, so most rows will legitimately count as changed."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")

    rows = db.pending_posts_with_content(page_id)
    updates = []
    for r in rows:
        fresh = poster.build_caption(
            r["live_caption"], r["live_hashtags"],
            page.get("extra_hashtags"), page.get("caption_footer", ""),
            page.get("hashtag_pool"),
        )
        old = r["caption_used"] or ""
        # Never trade a real caption for an empty one (content row cleared).
        if not fresh.strip() and old.strip():
            continue
        if fresh != old:
            updates.append((fresh, r["id"]))

    db.bulk_set_caption_used(updates)
    if updates:
        db.log_activity(
            page_id, "refresh_captions",
            f"Rebuilt captions for {len(updates)} of {len(rows)} pending post(s)"
        )
    return {"refreshed": len(updates), "checked": len(rows)}


class SuggestIn(BaseModel):
    page_id: int
    count: int


@app.post("/api/schedule/suggest")
def suggest_times(req: SuggestIn):
    """Optional convenience: returns times you MAY use. Nothing is scheduled;
    you stay in control and can edit or ignore every one."""
    page = db.get_page(req.page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    times = scheduling.compute_times(page, req.count)
    return {"suggested_utc": [t.isoformat() for t in times]}


MIN_LEAD = dt.timedelta(minutes=15)  # comfortably clear of FB's 10-minute minimum


# ---- auto-schedule core, shared by the HTTP endpoint AND the cron
# auto-refill job below, so the two can never drift apart ----
def _do_auto_schedule(page, posts_per_day, start_date, post_times, dry_run=False,
                       media_filter="all", avoid_collisions=True):
    page_id = page["id"]
    parsed_times = [tuple(int(x) for x in t.strip().split(":")) for t in post_times]

    items = db.unposted_content(page_id, limit=None, media_type=media_filter)
    if not items:
        return {
            "scheduled": 0, "days_needed": 0, "first_post_utc": None, "last_post_utc": None,
            "total_unposted": 0,
            "message": "All requested items are posted or scheduled. Add more content or sync Drive.",
        }

    offset = dt.timedelta(hours=float(page.get("utc_offset_hours", 0) or 0))
    now_utc = dt.datetime.now(dt.timezone.utc)
    cutoff = now_utc + MIN_LEAD

    def slot_utc(date, hh, mm):
        local_naive = dt.datetime(date.year, date.month, date.day, hh, mm)
        return (local_naive - offset).replace(tzinfo=dt.timezone.utc)

    # Collision avoidance against already pending posts on this page
    existing_slots = []
    if avoid_collisions:
        pending = db.list_scheduled(page_id=page_id, status="pending")
        for p in pending:
            s_val = p.get("scheduled_time_utc")
            if isinstance(s_val, dt.datetime):
                existing_slots.append(s_val if s_val.tzinfo else s_val.replace(tzinfo=dt.timezone.utc))
            elif s_val:
                existing_slots.append(dt.datetime.fromisoformat(str(s_val).replace("Z", "+00:00")))

    needed = len(items)
    slots = []
    day_offset = 0
    min_gap_sec = 25 * 60  # minimum 25 minute buffer to avoid collision

    while len(slots) < needed and day_offset <= 3660:  # ~10yr safety valve
        date = start_date + dt.timedelta(days=day_offset)
        for pos in range(posts_per_day):
            hh, mm = parsed_times[pos % len(parsed_times)]
            candidate = slot_utc(date, hh, mm)
            if candidate > cutoff:
                # If avoid_collisions is on, nudge candidate forward if colliding with existing pending posts
                if avoid_collisions and existing_slots:
                    conflict = True
                    attempts = 0
                    while conflict and attempts < 12:
                        conflict = any(abs((candidate - ex).total_seconds()) < min_gap_sec for ex in existing_slots)
                        if conflict:
                            candidate += dt.timedelta(minutes=30)
                            attempts += 1
                slots.append(candidate)
                if avoid_collisions:
                    existing_slots.append(candidate)
                if len(slots) >= needed:
                    break
        day_offset += 1

    if dry_run:
        scheduled = len(slots)
    else:
        rows = []
        for ci, when in zip(items, slots):
            caption = poster.build_caption(
                ci["caption"], ci["hashtags"],
                page.get("extra_hashtags"), page.get("caption_footer", ""),
                page.get("hashtag_pool"),
            )
            rows.append((page_id, ci["id"], when, caption))
        scheduled = db.bulk_enqueue(rows)

    last_date_str = slots[-1].strftime("%Y-%m-%d %H:%M UTC") if slots else ""
    msg = (f"{scheduled} post{'s' if scheduled != 1 else ''} scheduled across "
           f"{day_offset} day{'s' if day_offset != 1 else ''}. Last post: {last_date_str}.")

    return {
        "scheduled": scheduled,
        "days_needed": day_offset,
        "first_post_utc": slots[0].isoformat() if slots else None,
        "last_post_utc": slots[-1].isoformat() if slots else None,
        "total_unposted": needed,
        "message": msg,
    }


class AutoScheduleIn(BaseModel):
    posts_per_day: int
    start_date: str          # "YYYY-MM-DD", interpreted in the page's local time
    post_times: List[str]    # ["09:00", "13:00", ...], the page's local time
    dry_run: bool = False    # true = preview only (counts/dates), nothing written
    gap_minutes: Optional[int] = None
    start_time: Optional[str] = None
    media_filter: Optional[str] = "all"  # "all" | "reel" | "photo" | "text"
    avoid_collisions: bool = True


@app.post("/api/pages/{page_id}/auto-schedule")
def auto_schedule(page_id: int, req: AutoScheduleIn):
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    if req.posts_per_day <= 0:
        raise HTTPException(400, "posts_per_day must be at least 1")
    if not req.post_times:
        raise HTTPException(400, "post_times must not be empty")

    try:
        start_date = dt.date.fromisoformat(req.start_date)
    except ValueError:
        raise HTTPException(400, "start_date must be YYYY-MM-DD")

    for t in req.post_times:
        try:
            hh, mm = (int(x) for x in t.strip().split(":"))
            if not (0 <= hh < 24 and 0 <= mm < 60):
                raise ValueError
        except Exception:
            raise HTTPException(400, f"Invalid time '{t}', expected HH:MM")

    result = _do_auto_schedule(page, req.posts_per_day, start_date, req.post_times,
                                dry_run=req.dry_run, media_filter=req.media_filter,
                                avoid_collisions=req.avoid_collisions)

    if not req.dry_run and result["scheduled"]:
        try:
            settings = {
                "posts_per_day": req.posts_per_day,
                "post_times": req.post_times,
                "media_filter": req.media_filter,
                "avoid_collisions": req.avoid_collisions,
            }
            if req.gap_minutes is not None:
                settings["gap_minutes"] = req.gap_minutes
            if req.start_time is not None:
                settings["start_time"] = req.start_time
            db.set_last_auto_schedule_settings(page_id, settings)
        except Exception:
            pass
        db.log_activity(
            page_id, "auto_schedule",
            f"Scheduled {result['scheduled']} post(s) across {result['days_needed']} day(s)"
        )

    return result


# ============================ COLLISION PREVENTION ============================
@app.get("/api/pages/{page_id}/collisions")
def get_collisions(page_id: int, min_gap_minutes: int = 30):
    """Audits the pending queue for timeline collisions and potential algorithmic
    cannibalization on Facebook."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    pending = db.list_scheduled(page_id=page_id, status="pending")
    collisions = scheduling.detect_collisions(pending, min_gap_minutes=min_gap_minutes)
    return {
        "page_id": page_id,
        "min_gap_minutes": min_gap_minutes,
        "has_collisions": len(collisions) > 0,
        "count": len(collisions),
        "collisions": collisions,
    }


class ResolveCollisionsIn(BaseModel):
    min_gap_minutes: int = 45


@app.post("/api/pages/{page_id}/resolve-collisions")
def resolve_page_collisions(page_id: int, req: ResolveCollisionsIn = None):
    """Automatically adjusts colliding post times with a safe buffer."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    gap = req.min_gap_minutes if req else 45
    pending = db.list_scheduled(page_id=page_id, status="pending")
    updates = scheduling.resolve_collisions(pending, min_gap_minutes=gap)
    if updates:
        db.bulk_reschedule(updates)
        db.log_activity(page_id, "resolve_collisions", f"Eliminated collisions: re-spaced {len(updates)} post(s) to {gap}m intervals.")
    return {
        "resolved_count": len(updates),
        "updates": [{"id": u["id"], "scheduled_time_utc": u["scheduled_time_utc"].isoformat()} for u in updates]
    }


# ============================ DIRECT POST CREATION & AI ============================
class CreateContentIn(BaseModel):
    media_type: str = "photo"  # "photo" | "text" | "reel"
    caption: str = ""
    hashtags: List[str] = []
    file_name: Optional[str] = None
    image_url_or_data: Optional[str] = None
    scheduled_time_utc: Optional[str] = None
    post_now: bool = False


@app.post("/api/pages/{page_id}/content/create")
def create_custom_content(page_id: int, req: CreateContentIn):
    """Creates a custom photo, text update, or reel directly for a page."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")

    unique_id = uuid.uuid4().hex[:10]
    source_video_id = f"{req.media_type}_{unique_id}"
    file_name = req.file_name or f"{req.media_type.capitalize()} Update {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}"
    
    # Spontaneous image resolution: if photo format has no image provided, generate a guaranteed aesthetic visual
    if req.media_type == "photo" and not req.image_url_or_data:
        drive_file_id = gemini_ai.generate_spontaneous_image_url(
            niche=page.get("name", "Motivation"),
            prompt=req.caption or req.file_name or "inspiration",
        )
    else:
        drive_file_id = req.image_url_or_data or f"direct:{req.media_type}:{unique_id}"

    ci = db.create_content_item(
        page_id=page_id,
        media_type=req.media_type,
        source_video_id=source_video_id,
        drive_file_id=drive_file_id,
        file_name=file_name,
        caption=req.caption,
        hashtags=req.hashtags,
        source_folder="direct_creation"
    )

    caption = poster.build_caption(
        ci["caption"], ci["hashtags"],
        page.get("extra_hashtags"), page.get("caption_footer", ""),
        page.get("hashtag_pool"),
    )

    # Immediate Post ("Post Now")
    if req.post_now:
        media_bytes = None
        if req.media_type != "text":
            media_bytes = drive.download_file(drive_file_id)

        fb_creation_id = poster.publish(
            media_type=req.media_type,
            page_id=page["fb_page_id"],
            page_token=page["fb_access_token"],
            media_bytes=media_bytes,
            caption=caption,
            scheduled_ts=None,
        )
        now_utc = dt.datetime.now(dt.timezone.utc)
        db.record_ledger(page_id, source_video_id, ci["id"], fb_creation_id, caption)
        row = db.enqueue(page_id, ci["id"], now_utc, caption)
        db.mark_status(row["id"], "posted", fb_creation_id=fb_creation_id)
        db.log_activity(page_id, "direct_post_now", f"Posted {req.media_type} '{file_name}' — fb_id={fb_creation_id}")
        return {"success": True, "content_item": _clean(ci), "fb_creation_id": fb_creation_id}

    # Scheduled Post
    if req.scheduled_time_utc:
        when = dt.datetime.fromisoformat(req.scheduled_time_utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.timezone.utc)
        row = db.enqueue(page_id, ci["id"], when, caption)
        return {"success": True, "content_item": _clean(ci), "scheduled_post": _clean(row)}

    return {"success": True, "content_item": _clean(ci)}


class AiGenerateIn(BaseModel):
    prompt: Optional[str] = "Trending viral insights and engagement"
    topic: Optional[str] = None
    niche: Optional[str] = "general"
    content_format: Optional[str] = "all"  # "text" | "photo" | "reel" | "all"
    api_key: Optional[str] = None
    preferred_model: Optional[str] = None


@app.post("/api/pages/{page_id}/ai/generate")
def ai_generate_post(page_id: int, req: AiGenerateIn):
    """Uses Multi-Model AI (Gemini 2.0 Flash, Gemini 1.5 Pro, Groq Llama 3.3) to generate content."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    niche = req.niche or page.get("name") or "general"
    prompt_text = req.topic or req.prompt or f"Viral engaging post for {niche}"
    return gemini_ai.generate_content(
        prompt=prompt_text,
        niche=niche,
        content_format=req.content_format or "all",
        api_key=req.api_key,
        preferred_model=req.preferred_model,
    )


class AutopilotBatchIn(BaseModel):
    niche: Optional[str] = None
    count: int = 4
    formats: List[str] = ["photo", "text", "reel"]
    drive_niche_folder_id: Optional[str] = None
    min_gap_minutes: int = 45


@app.post("/api/pages/{page_id}/autopilot/batch")
def generate_autopilot_batch(page_id: int, req: AutopilotBatchIn):
    """
    Autonomous Niche Batch Engine:
    Generates and schedules a tailored batch of multi-format posts (Reels, Photos, Text)
    with zero collision, spontaneous aesthetic visuals, and high-CTR captions.
    """
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")

    niche = req.niche or page.get("name") or "Success and Motivation"
    count = max(1, min(req.count, 24))
    formats = req.formats or ["photo", "text"]

    # 1. Existing schedule times to avoid collision
    scheduled_rows = db.list_scheduled(page_id=page_id, status="pending")
    existing_slots = []
    for r in scheduled_rows:
        t = r.get("scheduled_time_utc")
        if t:
            if isinstance(t, dt.datetime):
                existing_slots.append(t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc))
            else:
                parsed_t = dt.datetime.fromisoformat(str(t))
                existing_slots.append(parsed_t if parsed_t.tzinfo else parsed_t.replace(tzinfo=dt.timezone.utc))

    # Spread candidate slots across 24/7 or page schedule window
    now_utc = dt.datetime.now(dt.timezone.utc)
    base_time = now_utc + dt.timedelta(minutes=30)
    
    generated_items = []
    created_posts = []

    for i in range(count):
        chosen_format = formats[i % len(formats)]
        slot_time = base_time + dt.timedelta(minutes=i * req.min_gap_minutes)
        
        # Check collision with existing slots and nudge if necessary
        for ex in existing_slots:
            if abs((slot_time - ex).total_seconds()) < req.min_gap_minutes * 60:
                slot_time = ex + dt.timedelta(minutes=req.min_gap_minutes)

        # Generate content via AI Engine (Gemini / Groq / Fallback)
        ai_res = gemini_ai.generate_content(
            prompt=f"Topic #{i+1} high engagement perspective in {niche}",
            niche=niche,
            content_format=chosen_format,
        )

        caption_text = ai_res.get("body", f"Top perspective on {niche}.")
        hashtags = ai_res.get("hashtags", ["#viral", "#success"])
        unique_id = uuid.uuid4().hex[:8]

        # Spontaneous image resolution
        if chosen_format == "photo":
            image_url = ai_res.get("image_url") or gemini_ai.generate_spontaneous_image_url(niche, caption_text)
            drive_file_id = image_url
            file_name = f"Photo Quote {unique_id}"
        elif chosen_format == "text":
            drive_file_id = f"text:{unique_id}"
            file_name = f"Status Thought {unique_id}"
        else: # reel
            # Check for unposted reels in drive
            unposted_reels = db.unposted_content(page_id, limit=5, media_type="reel")
            if unposted_reels:
                existing_reel = unposted_reels[0]
                drive_file_id = existing_reel["drive_file_id"]
                file_name = existing_reel["file_name"]
            else:
                # If no reel file is present in Drive yet, create photo quote
                chosen_format = "photo"
                image_url = ai_res.get("image_url") or gemini_ai.generate_spontaneous_image_url(niche, caption_text)
                drive_file_id = image_url
                file_name = f"Spontaneous Graphic {unique_id}"

        ci = db.create_content_item(
            page_id=page_id,
            media_type=chosen_format,
            source_video_id=f"{chosen_format}_{unique_id}",
            drive_file_id=drive_file_id,
            file_name=file_name,
            caption=caption_text,
            hashtags=hashtags,
            source_folder=f"autopilot_{niche.lower().replace(' ', '_')}"
        )

        full_caption = poster.build_caption(
            ci["caption"], ci["hashtags"],
            page.get("extra_hashtags"), page.get("caption_footer", ""),
            page.get("hashtag_pool"),
        )

        enq = db.enqueue(page_id, ci["id"], slot_time, full_caption)
        created_posts.append({
            "content_id": ci["id"],
            "media_type": chosen_format,
            "scheduled_time": slot_time.isoformat(),
            "headline": ai_res.get("headline"),
            "provider": ai_res.get("provider"),
        })
        existing_slots.append(slot_time)

    db.log_activity(page_id, "autopilot_batch", f"Autonomously generated and scheduled {len(created_posts)} posts for niche '{niche}'.")
    return {
        "success": True,
        "niche": niche,
        "count_scheduled": len(created_posts),
        "posts": created_posts
    }


# ============================================================
# Page Automation Profiles & Dynamic Scheduler Endpoints
# ============================================================

@app.get("/api/pages/{page_id}/automation-profile")
def get_page_automation_profile(page_id: int):
    """Returns independent automation profile for a Facebook page."""
    from core.planning import PublicationPlanningService
    return PublicationPlanningService.get_page_automation_profile(page_id)


@app.post("/api/pages/{page_id}/automation-profile")
def save_page_automation_profile(page_id: int, profile: dict = Body(...)):
    """Saves page automation profile and triggers safe dynamic schedule rebalancing."""
    from core.planning import PublicationPlanningService
    return PublicationPlanningService.save_page_automation_profile(page_id, profile)


@app.post("/api/pages/{page_id}/schedule/preview")
def preview_page_schedule(page_id: int, profile: Optional[dict] = Body(default=None)):
    """Dry-run schedule preview calculating balanced fair-share slot distribution."""
    from core.planning import PublicationPlanningService
    return PublicationPlanningService.preview_page_schedule(page_id, custom_profile=profile)


@app.post("/api/pages/{page_id}/schedule/rebalance")
def rebalance_page_schedule(page_id: int):
    """Safely recalculates future unposted schedule slots without touching published posts."""
    from core.planning import PublicationPlanningService
    return PublicationPlanningService.rebalance_page_schedule(page_id)


@app.get("/api/pages/{page_id}/monetization")
def get_page_monetization_status(page_id: int):
    """Returns verified Facebook page monetization and ad revenue status without fabricating figures."""
    from core.monetization import MonetizationService
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT pa.id FROM platform_accounts pa
                JOIN pages p ON p.fb_page_id = pa.external_account_id
                WHERE p.id = %s LIMIT 1;
            """, (page_id,))
            pa = cur.fetchone()

    if not pa:
        return {
            "platform": "facebook",
            "page_id": page_id,
            "monetization_enabled": False,
            "program_name": "Facebook Content Monetization",
            "eligibility_status": "Unavailable via API",
            "estimated_revenue": None,
            "currency": "USD",
            "verified_source": False,
            "note": "Private financial metrics require Meta Business Manager financial admin access."
        }

    return MonetizationService.get_account_monetization_status(pa["id"])


class ContentBatchGenerateIn(BaseModel):
    niche: str
    topic: Optional[str] = ""
    quantity: int = 1
    style: Optional[str] = "photorealistic"
    language: Optional[str] = "en"
    target_platform: Optional[str] = "facebook"
    content_format: Optional[str] = "photo"
    page_id: Optional[int] = None
    auto_queue: bool = True


@app.post("/api/content/generate-batch")
def generate_content_batch(req: ContentBatchGenerateIn):
    """
    Dynamic Autonomous Content Generation (Content Machine):
    Generates dynamic visuals, platform-aware structured text, registers items into Content Library,
    and automatically queues publications and durable jobs when auto_queue is True.
    """
    from content_machine import ContentMachine
    try:
        items = ContentMachine.generate_asset_batch(
            niche=req.niche,
            topic=req.topic or "",
            quantity=req.quantity,
            style=req.style or "photorealistic",
            language=req.language or "en",
            target_platform=req.target_platform or "facebook",
            content_format=req.content_format or "photo",
            page_id=req.page_id,
            auto_queue=req.auto_queue
        )
        return {
            "success": True,
            "count": len(items),
            "items": items
        }
    except Exception as e:
        logger.error(f"Error in ContentMachine generate-batch: {e}")
        raise HTTPException(500, f"Content generation failed: {str(e)}")


@app.post("/api/flow/prompts")
def generate_flow_video_prompts(req: dict = Body(...)):
    """Generates cinematic vertical video prompts engineered for Google Flow."""
    niche = req.get("niche", "Dark Psychology & Wealth")
    count = int(req.get("count", 3))
    try:
        from flow_worker import generate_flow_prompts_for_niche
        prompts = generate_flow_prompts_for_niche(niche=niche, count=min(count, 15))
        return {"success": True, "niche": niche, "prompts": prompts}
    except Exception as e:
        logger.error(f"Error generating flow prompts: {e}")
        raise HTTPException(500, f"Flow prompt generation failed: {str(e)}")


class AiConfigIn(BaseModel):
    gemini_api_key: Optional[str] = None
    gemini_api_keys: Optional[str] = None
    groq_api_key: Optional[str] = None
    ai_model: Optional[str] = "gemini-2.0-flash"


@app.post("/api/ai/config")
def save_ai_config(cfg: AiConfigIn):
    """Saves AI model configurations and keys into the secure vault."""
    saved = {}
    if cfg.gemini_api_key is not None:
        credentials.set_key("gemini_api_key", cfg.gemini_api_key.strip())
        saved["gemini_api_key"] = bool(cfg.gemini_api_key.strip())
    if cfg.gemini_api_keys is not None:
        credentials.set_key("gemini_api_keys", cfg.gemini_api_keys.strip())
        saved["gemini_api_keys_count"] = len([k for k in cfg.gemini_api_keys.split(",") if k.strip()])
    if cfg.groq_api_key is not None:
        credentials.set_key("groq_api_key", cfg.groq_api_key.strip())
        saved["groq_api_key"] = bool(cfg.groq_api_key.strip())
    if cfg.ai_model is not None:
        credentials.set_key("ai_model", cfg.ai_model.strip())
        saved["ai_model"] = cfg.ai_model.strip()

    return {"success": True, "saved": saved}


@app.get("/api/ai/config")
def get_ai_config():
    """Returns status of AI models and providers without exposing secrets."""
    gemini_keys = gemini_ai.get_all_gemini_keys()
    groq_configured = bool(gemini_ai.get_groq_key())
    preferred_model = gemini_ai.get_configured_model()

    return {
        "gemini_keys_count": len(gemini_keys),
        "gemini_active": len(gemini_keys) > 0,
        "groq_active": groq_configured,
        "preferred_model": preferred_model,
        "supported_models": gemini_ai.GEMINI_MODELS + [gemini_ai.GROQ_MODEL],
        "spontaneous_image_engine": "active (Pollinations AI + Unsplash Curated)",
    }



def _gap_minutes_for(posts_per_day):
    """Default spacing between posts, by intensity bucket. 1/day needs no
    gap; everything else buckets down as density rises."""
    if posts_per_day <= 1:
        return None
    if posts_per_day <= 10:
        return 60
    if posts_per_day <= 24:
        return 30
    return 15


def _update_intensity_message(will_add, will_cancel, shortfall):
    parts = []
    if will_add:
        parts.append(f"{will_add} post{'s' if will_add != 1 else ''} will be added")
    if will_cancel:
        parts.append(f"{will_cancel} pending post{'s' if will_cancel != 1 else ''} will be cancelled")
    if not parts:
        return "No change — every scheduled day already matches this intensity."
    msg = " and ".join(parts) + "."
    if shortfall:
        msg += f" {shortfall} fewer than requested — not enough unposted content to fill every slot."
    return msg


def _do_update_intensity(page, new_ppd, dry_run=False):
    """Surgically adjusts an EXISTING pending schedule to a new posts/day
    target, without ever touching already-posted rows:

      - Days with fewer pending posts than the target get new slots appended
        after that day's last existing time, spaced by the gap rule for
        new_ppd. Existing pending times are never moved.
      - Days with more pending posts than the target have their excess
        cancelled, keeping the earliest new_ppd posts of that day.

    Use auto_schedule() instead when the page has no pending posts at all —
    this function has nothing to adjust in that case."""
    page_id = page["id"]
    offset = dt.timedelta(hours=float(page.get("utc_offset_hours", 0) or 0))
    pending = db.list_scheduled(page_id=page_id, status="pending")
    if not pending:
        return {
            "mode": "fresh", "will_add": 0, "will_cancel": 0,
            "message": "No pending posts yet — use Auto-Schedule to build a fresh queue instead.",
        }

    by_day = {}
    for p in pending:
        local_date = (p["scheduled_time_utc"] + offset).date()
        by_day.setdefault(local_date, []).append(p)
    for posts in by_day.values():
        posts.sort(key=lambda r: r["scheduled_time_utc"])

    gap_minutes = _gap_minutes_for(new_ppd) or 60
    to_cancel = []
    add_plan = []  # (day, needed, last_scheduled_time_utc)
    for day, posts in sorted(by_day.items()):
        current = len(posts)
        if current > new_ppd:
            to_cancel.extend(posts[new_ppd:])
        elif current < new_ppd:
            add_plan.append((day, new_ppd - current, posts[-1]["scheduled_time_utc"]))

    total_to_add = sum(n for _, n, _ in add_plan)
    total_to_cancel = len(to_cancel)

    items = db.unposted_content(page_id, limit=total_to_add) if total_to_add else []
    will_add = min(total_to_add, len(items))
    shortfall = total_to_add - will_add

    if dry_run:
        return {
            "mode": "update",
            "will_add": will_add,
            "will_cancel": total_to_cancel,
            "shortfall": shortfall,
            "message": _update_intensity_message(will_add, total_to_cancel, shortfall),
        }

    cancelled = db.delete_scheduled_bulk([p["id"] for p in to_cancel]) if to_cancel else 0

    rows = []
    item_iter = iter(items)
    for _, needed, last_time in add_plan:
        t = last_time
        for _ in range(needed):
            ci = next(item_iter, None)
            if ci is None:
                break
            t = t + dt.timedelta(minutes=gap_minutes)
            caption = poster.build_caption(
                ci["caption"], ci["hashtags"],
                page.get("extra_hashtags"), page.get("caption_footer", ""),
                page.get("hashtag_pool"),
            )
            rows.append((page_id, ci["id"], t, caption))
    added = db.bulk_enqueue(rows) if rows else 0

    return {
        "mode": "update",
        "added": added,
        "cancelled": cancelled,
        "shortfall": shortfall,
        "message": _update_intensity_message(added, cancelled, shortfall),
    }


class UpdateIntensityIn(BaseModel):
    posts_per_day: int
    dry_run: bool = False


@app.post("/api/pages/{page_id}/update-intensity")
def update_intensity(page_id: int, req: UpdateIntensityIn):
    """Mode B of the intensity selector: the page already has pending posts,
    so this adjusts the existing schedule surgically instead of rebuilding
    it (see _do_update_intensity). Pass dry_run: true to preview the add/
    cancel counts before committing."""
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found")
    if req.posts_per_day <= 0:
        raise HTTPException(400, "posts_per_day must be at least 1")

    result = _do_update_intensity(page, req.posts_per_day, dry_run=req.dry_run)

    if not req.dry_run and result.get("mode") == "update" and (result.get("added") or result.get("cancelled")):
        try:
            prior_times = (page.get("last_auto_schedule_settings") or {}).get("post_times") or ["12:00"]
            db.set_last_auto_schedule_settings(
                page_id, {"posts_per_day": req.posts_per_day, "post_times": prior_times}
            )
        except Exception:
            pass  # pre-migration: won't be remembered for auto-refill yet, not fatal
        db.log_activity(
            page_id, "update_intensity",
            f"Updated intensity to {req.posts_per_day}/day — "
            f"added {result.get('added', 0)}, cancelled {result.get('cancelled', 0)}"
        )

    return result


class RescueIn(BaseModel):
    page_id: Optional[int] = None
    reflow: bool = True
    include_failed: bool = True


@app.post("/api/schedule/rescue-abandoned")
def rescue_abandoned(req: RescueIn = RescueIn()):
    """Rescues abandoned or failed posts and returns them to the active queue.
    If reflow=True, intelligently spaces them out into upcoming schedule slots
    after the last scheduled post for that page, so they don't flood Facebook."""
    rescued = db.rescue_abandoned_posts(page_id=req.page_id, reflow=req.reflow, include_failed=req.include_failed)
    msg = f"Rescued {rescued} post(s) back to the active queue{' (smoothly reflowed)' if req.reflow else ''}."
    if req.page_id:
        db.log_activity(req.page_id, "rescue_abandoned", msg)
    return {"rescued": rescued, "message": msg}



@app.get("/api/system/health-summary")
def system_health_summary():
    """System-wide health summary including token status, queue health,
    failure counts, and active outages."""
    return db.system_health_summary()


# ============================================================
# Credential vault — admin only
#
# The Snapchat client ID and secret live encrypted in Postgres so they can be
# rotated without a redeploy. These routes are the rotation path.
#
# They are guarded by the same RUN_TOKEN / CRON_SECRET that guards /api/run,
# passed as X-Run-Token, `Authorization: Bearer …`, or ?token=. A secret can
# be written and its fingerprint read back; it can never be read back in full.
# ============================================================
class CredentialIn(BaseModel):
    value: str
    description: Optional[str] = None


@app.get("/api/admin/credentials")
def get_credentials_status(
    x_run_token: str = Header("", alias="X-Run-Token"),
    authorization: str = Header("", alias="Authorization"),
    token: Optional[str] = None,
):
    """What is configured and where it is read from. Never a secret value.

    The client secret is reported as a length plus a short SHA-256 prefix, so
    an operator can confirm WHICH secret is loaded — and that a rotation
    actually took effect — without the value being exposed.
    """
    _authorize_run(x_run_token, authorization, token)
    return {
        "credentials": credentials.status(),
        "encryption_key_configured": credentials.encryption_ready(),
        "vault_note": (
            "Values are stored as Fernet ciphertext in app_credentials, a table "
            "revoked from the anon and authenticated roles. SNAPCHAT_ENCRYPTION_KEY "
            "and DATABASE_URL remain environment variables — the key that opens the "
            "vault cannot live inside it."
        ),
    }


@app.put("/api/admin/credentials/{key}")
def set_credential(
    key: str,
    req: CredentialIn,
    x_run_token: str = Header("", alias="X-Run-Token"),
    authorization: str = Header("", alias="Authorization"),
    token: Optional[str] = None,
):
    """Store or rotate one credential. The value is encrypted before it is written."""
    _authorize_run(x_run_token, authorization, token)

    if not credentials.encryption_ready():
        raise HTTPException(
            500,
            "Refusing to write to the vault: SNAPCHAT_ENCRYPTION_KEY is not set, so "
            "values would be encrypted with a key derived from the client secret "
            "itself. Set it first.",
        )

    try:
        stored = credentials.put(key, req.value, req.description)
    except ValueError as e:
        raise HTTPException(400, str(e))

    return {
        "stored": True,
        "key": key,
        "credential": _clean(stored),
        "status": credentials.status().get(key),
    }


@app.delete("/api/admin/credentials/{key}")
def remove_credential(
    key: str,
    x_run_token: str = Header("", alias="X-Run-Token"),
    authorization: str = Header("", alias="Authorization"),
    token: Optional[str] = None,
):
    """Drop a credential from the vault; resolution falls back to the environment."""
    _authorize_run(x_run_token, authorization, token)
    try:
        removed = credentials.delete(key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not removed:
        raise HTTPException(404, f"No credential '{key}' is stored in the vault.")
    return {"deleted": True, "key": key, "status": credentials.status().get(key)}


# ============================================================
# Snapchat Public Profile API — OAuth, accounts, publishing jobs
#
# Route map (all server-side; no Snapchat secret or token ever crosses
# the API boundary):
#
#   GET    /api/snapchat/config                        integration readiness
#   GET    /api/snapchat/oauth/authorize               start OAuth
#   GET    /api/snapchat/oauth/callback                finish OAuth (Snap calls this)
#   GET    /api/snapchat/accounts                      connected accounts
#   GET    /api/snapchat/accounts/{id}                 one account
#   PATCH  /api/snapchat/accounts/{id}                 settings
#   GET    /api/snapchat/accounts/{id}/health          live connection check
#   POST   /api/snapchat/accounts/{id}/refresh-token   force a token refresh
#   POST   /api/snapchat/accounts/{id}/disconnect      revoke locally, keep history
#   DELETE /api/snapchat/accounts/{id}                 delete account + history
#   GET    /api/snapchat/accounts/{id}/content         schedulable content
#   POST   /api/snapchat/accounts/{id}/publish         publish now
#   GET    /api/snapchat/jobs                          the Snapchat queue
#   POST   /api/snapchat/jobs                          create a publishing job
#   DELETE /api/snapchat/jobs/{id}                     cancel a queued job
#   POST   /api/snapchat/jobs/{id}/retry               retry a failed job
#   GET    /api/snapchat/publishing/history            publishing records
#   GET    /api/snapchat/publishing/{id}/status        one record + live status
#   GET    /api/snapchat/stats                         per-account rollup
# ============================================================

SNAP_ERROR_STATUS = {
    "SnapAuthError": 401,
    "SnapMediaError": 422,
    "SnapTransientError": 503,
    "SnapConfigError": 500,
    "SnapApiError": 400,
}

# Columns that must never leave the backend, under any endpoint.
_SNAP_SECRET_COLUMNS = (
    "access_token_encrypted",
    "refresh_token_encrypted",
    "session_data",
)


def _snap_http(e: Exception) -> HTTPException:
    """Translate a Snapchat failure into the right HTTP status.

    The message goes through the log redactor first: Snap's debug_message
    echoes parts of the request back, and an API response is every bit as
    public as a log line.
    """
    if isinstance(e, SnapError):
        return HTTPException(
            SNAP_ERROR_STATUS.get(type(e).__name__, 400),
            applog.safe(e.message),
        )
    return HTTPException(400, applog.safe(str(e)))


def _clean_snap_account(acc):
    """Strip every credential column before an account row leaves the API."""
    if not acc:
        return None
    d = _clean(acc)
    has_access = bool(acc.get("access_token_encrypted"))
    has_refresh = bool(acc.get("refresh_token_encrypted"))
    for col in _SNAP_SECRET_COLUMNS:
        d.pop(col, None)
    d["is_connected"] = has_access
    d["can_auto_refresh"] = has_refresh
    d["connection_status"] = acc.get("connection_status") or (
        "connected" if has_access else "disconnected"
    )
    return d


def _get_snap_account_or_404(account_id: int):
    acc = db.get_snapchat_account(account_id)
    if not acc:
        raise HTTPException(404, "Snapchat account not found")
    return acc


def _dashboard_redirect(**params) -> RedirectResponse:
    """Send the browser back to the dashboard's Snapchat page with a result.

    /snapchat is a real address: vercel.json rewrites unknown paths to
    index.html and the app reads the section out of the pathname, so the
    creator lands on the Snapchat page instead of the overview.
    """
    query = urlencode({k: v for k, v in params.items() if v not in (None, "")})
    return RedirectResponse(f"/snapchat?{query}")


@app.get("/api/snapchat/config")
def snapchat_config():
    """What is configured, as booleans. Never the client secret itself.

    The dashboard uses this to tell "SNAPCHAT_CLIENT_ID was never set" apart
    from "Snapchat rejected the login", which look identical from a browser.
    """
    cfg = snapchat_oauth.config_status()
    cfg["ready"] = cfg["client_id_configured"] and cfg["client_secret_configured"]
    cfg["credentials_in_vault"] = [
        k for k, v in (cfg.get("credential_sources") or {}).items() if v == "database"
    ]
    cfg["callback_path"] = snapchat_oauth.OAUTH_CALLBACK_PATH
    cfg["spotlight_seconds"] = list(snapchat_media.duration_bounds("spotlight"))
    cfg["story_seconds"] = list(snapchat_media.duration_bounds("story"))
    cfg["min_resolution"] = [snapchat_media.MIN_WIDTH, snapchat_media.MIN_HEIGHT]
    # Publishing and earning are different bars. Snap accepts a 6s Spotlight
    # but only pays on 30s+, so the dashboard states both.
    cfg["monetization_min_spotlight_seconds"] = snapchat_media.MONETIZATION_MIN_SPOTLIGHT_SECONDS
    return cfg


# ------------------------------- OAuth --------------------------------
@app.get("/api/snapchat/oauth/authorize")
def snapchat_oauth_authorize(redirect_uri: Optional[str] = None):
    """Begin the OAuth flow.

    The state is stored server-side together with the redirect URI it was
    issued for, because Snap requires the token exchange to present the
    identical URI. Recomputing it at callback time would break the moment the
    environment variable and the registered value diverge.
    """
    try:
        db.purge_expired_oauth_states()
        auth_url, state = snapchat_oauth.generate_authorization_url(redirect_uri=redirect_uri)
        db.save_oauth_state(state, redirect_uri or snapchat_oauth.get_redirect_uri())
        return {
            "authorization_url": auth_url,
            "state": state,
            "redirect_uri": redirect_uri or snapchat_oauth.get_redirect_uri(),
            "scope": snapchat_oauth.DEFAULT_SCOPE,
        }
    except SnapError as e:
        raise _snap_http(e)
    except Exception as e:
        raise HTTPException(500, f"Failed to start Snapchat authorization: {applog.safe(str(e))}")


@app.get("/api/snapchat/oauth/callback")
def snapchat_oauth_callback(
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """Snapchat redirects the creator's browser here after consent.

    Order matters: the state is validated and burned BEFORE the code is
    exchanged, so a replayed callback URL finds no state row and its code is
    never presented to Snap.
    """
    if error:
        return _dashboard_redirect(snap_error=error_description or error)

    if not code or not state:
        return _dashboard_redirect(snap_error="Snapchat did not return an authorization code.")

    state_rec = db.consume_oauth_state(state)
    if not state_rec:
        return _dashboard_redirect(
            snap_error="Authorization could not be verified (the state was missing, "
                       "already used, or older than 15 minutes). Start the connection again."
        )

    try:
        tokens = snapchat_oauth.exchange_code_for_tokens(code, redirect_uri=state_rec["redirect_uri"])
    except SnapError as e:
        return _dashboard_redirect(snap_error=applog.safe(e.message))
    except Exception as e:
        return _dashboard_redirect(snap_error=applog.safe(str(e)))

    # Which Public Profile this token owns. Without it there is nothing to
    # publish to, and this is also the first call that proves the app is
    # allowlisted for the Public Profile API.
    try:
        profile = snapchat_api.get_my_profile(tokens["access_token"])
    except SnapError as e:
        return _dashboard_redirect(
            snap_error="Signed in, but Snapchat would not return a Public Profile: "
                       + applog.safe(e.message)
        )

    username = profile.get("snap_user_name") or (profile.get("id") or "")[:8]
    account = db.upsert_snapchat_oauth_account(
        snapchat_username=username,
        access_token_encrypted=tokens["access_token_encrypted"],
        refresh_token_encrypted=tokens["refresh_token_encrypted"],
        token_expires_at=tokens["expires_at"],
        scope=tokens["scope"],
        name=profile.get("display_name") or f"@{username}",
        public_profile_id=profile.get("id"),
        profile_name=profile.get("display_name"),
        organization_id=profile.get("organization_id"),
        profile_type=profile.get("profile_type"),
    )

    return _dashboard_redirect(
        snap_connected="1",
        snap_username=username,
        snap_account_id=account["id"],
    )


# ------------------------------ accounts ------------------------------
@app.get("/api/snapchat/accounts")
def list_snapchat_accounts(active_only: bool = False):
    """Every connected Snapchat Public Profile. Credentials are stripped."""
    return [_clean_snap_account(a) for a in db.list_snapchat_accounts(active_only=active_only)]


@app.get("/api/snapchat/accounts/{account_id}")
def get_snapchat_account(account_id: int):
    return _clean_snap_account(_get_snap_account_or_404(account_id))


class SnapchatAccountUpdateIn(BaseModel):
    name: Optional[str] = None
    default_placement: Optional[str] = None
    default_locale: Optional[str] = None
    skip_save_to_profile: Optional[bool] = None
    drive_folder_id: Optional[str] = None
    utc_offset_hours: Optional[int] = None
    schedule_mode: Optional[str] = None
    spread_start_hour: Optional[int] = None
    spread_end_hour: Optional[int] = None
    fixed_times: Optional[List[str]] = None
    per_run: Optional[int] = None
    extra_hashtags: Optional[List[str]] = None
    caption_footer: Optional[str] = None
    is_active: Optional[bool] = None


@app.patch("/api/snapchat/accounts/{account_id}")
def update_snapchat_account(account_id: int, req: SnapchatAccountUpdateIn):
    _get_snap_account_or_404(account_id)
    data = {k: v for k, v in req.model_dump().items() if v is not None}
    if data.get("default_placement") not in (None, "spotlight", "story"):
        raise HTTPException(400, "default_placement must be 'spotlight' or 'story'.")
    data["id"] = account_id
    return _clean_snap_account(db.upsert_snapchat_account(**data))


@app.get("/api/snapchat/accounts/{account_id}/health")
def snapchat_account_health(account_id: int):
    """Live check: refresh the token if needed, then ask Snapchat who we are.

    It also re-syncs the cached profile metadata, so a creator who renamed
    their profile does not keep showing under the old name.
    """
    acc = _get_snap_account_or_404(account_id)
    health = snapchat_poster.check_health(acc, db_module=db)

    if health.get("healthy"):
        db.update_snapchat_account_health(account_id, "valid")
        db.update_snapchat_profile_metadata(account_id, {
            "id": health["profile"].get("id"),
            "display_name": health["profile"].get("display_name"),
            "organization_id": health["profile"].get("organization_id"),
            "profile_type": health["profile"].get("profile_type"),
            "snap_user_name": health["profile"].get("snap_user_name"),
        })
    else:
        db.update_snapchat_account_health(
            account_id, health.get("token_status") or "invalid", health.get("error")
        )

    health["error"] = applog.safe(health.get("error"))
    health["token_expires_at"] = _iso(acc.get("token_expires_at"))
    health["encryption_key_configured"] = snapchat_crypto.is_encryption_configured()
    return health


@app.post("/api/snapchat/accounts/{account_id}/refresh-token")
def refresh_snapchat_account_token(account_id: int):
    """Force an immediate refresh. Returns status only, never a token."""
    acc = _get_snap_account_or_404(account_id)
    try:
        snapchat_runner.refresh_account_token(acc)
    except SnapError as e:
        db.update_snapchat_account_health(account_id, "invalid", e.message)
        raise _snap_http(e)
    except Exception as e:
        db.update_snapchat_account_health(account_id, "invalid", str(e))
        raise HTTPException(400, applog.safe(str(e)))

    fresh = db.get_snapchat_account(account_id)
    return {
        "success": True,
        "message": "Snapchat access token refreshed.",
        "token_expires_at": _iso(fresh.get("token_expires_at")),
    }


@app.post("/api/snapchat/accounts/{account_id}/disconnect")
def disconnect_snapchat_account(account_id: int):
    """Disconnect one account: wipe its credentials, keep everything else.

    Scoped to a single row, so other connected profiles keep publishing. Its
    queued posts are marked skipped rather than left to fail on every run,
    and its history stays for the record.
    """
    _get_snap_account_or_404(account_id)
    row = db.disconnect_snapchat_account(account_id)
    return {
        "disconnected": True,
        "id": account_id,
        "account": _clean_snap_account(row),
    }


@app.delete("/api/snapchat/accounts/{account_id}")
def delete_snapchat_account(account_id: int):
    """Remove the account entirely, with its content, queue and ledger rows."""
    _get_snap_account_or_404(account_id)
    db.delete_snapchat_account(account_id)
    return {"deleted": True, "id": account_id}


@app.get("/api/snapchat/accounts/{account_id}/content")
def snapchat_account_content(account_id: int, limit: int = 50, offset: int = 0,
                             search: Optional[str] = None,
                             placement: str = "spotlight"):
    """Content this account can still publish for the given placement."""
    _get_snap_account_or_404(account_id)
    items = db.snapchat_available_content(
        account_id, placement=placement, limit=limit, offset=offset, search=search,
    )
    return {"items": _clean_list(items), "placement": placement}


@app.get("/api/snapchat/stats")
def list_snapchat_stats():
    return _clean_list(db.list_snapchat_stats())


# --------------------------- publishing jobs --------------------------
class SnapJobIn(BaseModel):
    snapchat_account_id: int
    content_item_id: int
    scheduled_time_utc: str            # ISO-8601 UTC time YOU choose
    placement: str = "spotlight"       # 'spotlight' | 'story'
    caption: Optional[str] = None
    locale: Optional[str] = None
    skip_save_to_profile: Optional[bool] = None


@app.post("/api/snapchat/jobs")
def create_snapchat_job(req: SnapJobIn):
    """Queue one Snapchat publish at a time you pick.

    Ownership is checked here, not only at publish time, so a job that could
    never legally run is refused while a human is watching: content belonging
    to a different Snapchat account is rejected outright.
    """
    if req.placement not in ("spotlight", "story"):
        raise HTTPException(400, "placement must be 'spotlight' or 'story'.")

    acc = _get_snap_account_or_404(req.snapchat_account_id)
    if not acc.get("access_token_encrypted"):
        raise HTTPException(400, "That Snapchat account is not connected. Connect it first.")

    ci = db.get_content_item(req.content_item_id)
    if not ci:
        raise HTTPException(404, "Content item not found")

    owner = ci.get("snapchat_account_id")
    if owner is not None and owner != req.snapchat_account_id:
        raise HTTPException(403, "That content item belongs to a different Snapchat account.")

    if ci.get("source_video_id") and db.already_posted_snapchat(
        req.snapchat_account_id, ci["source_video_id"], req.placement
    ):
        raise HTTPException(
            409, f"That video has already been published to this account's {req.placement}."
        )

    when = dt.datetime.fromisoformat(req.scheduled_time_utc)
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)

    caption = req.caption if req.caption is not None else (ci.get("caption") or "")
    description = snapchat_poster.build_description(
        caption, ci.get("hashtags"), acc.get("extra_hashtags"), acc.get("caption_footer") or "",
    )

    row = db.enqueue_snapchat(
        account_id=req.snapchat_account_id,
        content_item_id=req.content_item_id,
        scheduled_time_utc=when,
        caption_used=description,
        placement=req.placement,
        locale=req.locale or acc.get("default_locale") or "en_US",
        skip_save_to_profile=(
            acc.get("skip_save_to_profile") if req.skip_save_to_profile is None
            else req.skip_save_to_profile
        ),
    )
    return _clean(row)


@app.get("/api/snapchat/jobs")
def list_snapchat_jobs(account_id: Optional[int] = None, status: Optional[str] = None):
    return _clean_list(db.list_snapchat_scheduled(account_id=account_id, status=status))


@app.delete("/api/snapchat/jobs/{sched_id}")
def cancel_snapchat_job(sched_id: int):
    row = db.get_scheduled_post(sched_id)
    if not row or row.get("platform") != "snapchat":
        raise HTTPException(404, "Snapchat job not found")
    if row.get("status") == "posted":
        raise HTTPException(409, "That job already published; it cannot be cancelled.")
    db.delete_scheduled(sched_id)
    return {"cancelled": True, "id": sched_id}


@app.post("/api/snapchat/jobs/{sched_id}/retry")
def retry_snapchat_job(sched_id: int):
    """Retry a failed or abandoned job right now.

    It resets the attempt counter, since the operator has presumably fixed
    whatever caused the failure, but still runs through the idempotency key:
    a job that actually succeeded is reported as already published rather
    than posted a second time.
    """
    try:
        outcome, err = snapchat_runner.retry_one(sched_id)
    except SnapError as e:
        raise _snap_http(e)
    if err:
        raise HTTPException(400, err)

    row = db.get_scheduled_post(sched_id)
    return {"outcome": outcome, "job": _clean(row)}


class SnapPublishIn(BaseModel):
    content_item_id: int
    placement: str = "spotlight"
    caption: Optional[str] = None
    topics: Optional[List[str]] = None
    locale: Optional[str] = None
    skip_save_to_profile: bool = False


@app.post("/api/snapchat/accounts/{account_id}/publish")
def publish_to_snapchat_now(account_id: int, req: SnapPublishIn):
    """Download from Drive and publish to Snapchat immediately."""
    if req.placement not in ("spotlight", "story"):
        raise HTTPException(400, "placement must be 'spotlight' or 'story'.")

    acc = _get_snap_account_or_404(account_id)
    ci = db.get_content_item(req.content_item_id)
    if not ci:
        raise HTTPException(404, "Content item not found")

    try:
        result = snapchat_runner.publish_now(
            account=acc,
            content_item=ci,
            placement=req.placement,
            caption=req.caption,
            topics=req.topics,
            locale=req.locale,
            skip_save_to_profile=req.skip_save_to_profile,
        )
    except SnapError as e:
        raise _snap_http(e)
    except Exception as e:
        raise HTTPException(502, applog.safe(str(e)))

    return result


# ------------------------- publishing records -------------------------
@app.get("/api/snapchat/publishing/history")
def get_snapchat_publishing_history(account_id: Optional[int] = None,
                                    status: Optional[str] = None,
                                    limit: int = 20):
    return _clean_list(
        db.list_snapchat_publishing_records(account_id=account_id, status=status, limit=limit)
    )


@app.get("/api/snapchat/publishing/{record_id}/status")
def get_snapchat_record_status(record_id: int, live: bool = False):
    """The stored state of one publishing job.

    `live=true` additionally reads the post back from Snapchat to confirm it
    really is on the profile. That is the check the acceptance test needs,
    and the reason a "published" row can be trusted.
    """
    rec = db.get_snapchat_publishing_record(record_id)
    if not rec:
        raise HTTPException(404, "Publishing record not found")

    out = _clean(rec)
    out["error_message"] = applog.safe(out.get("error_message"))

    if live and rec.get("post_id") and rec.get("post_id_kind") != "request_id":
        acc = db.get_snapchat_account(rec["snapchat_account_id"])
        try:
            out["live"] = snapchat_poster.fetch_post(
                acc, rec["post_id"], placement=rec["target_placement"], db_module=db,
            )
            out["live_confirmed"] = bool(out["live"])
        except SnapError as e:
            out["live_error"] = applog.safe(e.message)
        except Exception as e:
            out["live_error"] = applog.safe(str(e))

    return out


@app.post("/api/content/batch-delete")
def batch_delete_content(body: dict):
    """Deletes one or multiple content_items by ID, cascading to publications and assets."""
    ids = body.get("ids") or []
    if not ids:
        return {"deleted": 0}
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM content_items WHERE id = ANY(%s);", (list(map(int, ids)),))
            deleted = cur.rowcount
        conn.commit()
    return {"deleted": deleted}


@app.post("/api/content/{content_item_id}/post-now")
def post_now_immediate(content_item_id: int):
    """Synchronously download the file from Google Drive and publish it to
    Facebook right now — no scheduling, no cron, no background task.

    Returns {success, fb_creation_id, message} on success.
    Raises HTTP 400 with the exact Facebook error on failure so the
    frontend can display it directly to the user.

    Render gives this endpoint 120 s before it times out; Drive download
    and the FB upload each have generous individual timeouts inside
    poster.publish() / drive.download_file(), so in practice a normal
    short reel completes well under 60 s.
    """
    ci = db.get_content_item(content_item_id)
    if not ci:
        raise HTTPException(404, "Content item not found")

    page_id = ci["page_id"]
    page = db.get_page(page_id)
    if not page:
        raise HTTPException(404, "Page not found for this content item")

    if db.already_posted(page_id, ci["source_video_id"]):
        raise HTTPException(409, "That video is already posted to this page.")

    # Cancel any pending schedule entry for this item so we can post immediately
    # without leaving a dangling pending row that the cron would later re-post.
    db.cancel_pending_for_content(content_item_id)

    caption = poster.build_caption(
        ci["caption"], ci["hashtags"],
        page.get("extra_hashtags"), page.get("caption_footer", ""),
        page.get("hashtag_pool"),
    )

    # --- Download media (raises on failure, skipped for text status posts) ---
    media_bytes = None
    if ci.get("media_type") != "text":
        try:
            media_bytes = drive.download_file(ci["drive_file_id"])
        except drive.DriveFileNotFoundError as e:
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE content_items
                        SET validation_status = 'unavailable',
                            validation_error = %s
                        WHERE id = %s;
                    """, (str(e), content_item_id))
                conn.commit()
            raise HTTPException(404, f"Media file is not found or was deleted on Google Drive: {e}")
        except Exception as e:
            raise HTTPException(502, f"Could not download media file: {e}")

    # --- Publish to Facebook synchronously ---
    try:
        fb_creation_id = poster.publish(
            media_type=ci["media_type"],
            page_id=page["fb_page_id"],
            page_token=page["fb_access_token"],
            media_bytes=media_bytes,
            caption=caption,
            scheduled_ts=None,   # publish immediately
        )
    except RuntimeError as e:
        # poster.publish() raises RuntimeError with the raw Facebook
        # response baked in — surface exactly that to the user.
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"Unexpected error while posting: {e}")

    # --- Record success ---
    now_utc = dt.datetime.now(dt.timezone.utc)
    db.record_ledger(
        page_id, ci["source_video_id"], content_item_id,
        fb_creation_id, caption,
    )
    # Create a scheduled_posts row with status='posted' so the content
    # list shows this item as posted immediately.
    try:
        row = db.enqueue(page_id, content_item_id, now_utc, caption)
        db.mark_status(row["id"], "posted", fb_creation_id=fb_creation_id)
    except Exception:
        pass  # ledger write succeeded; schedule row is best-effort

    db.log_activity(
        page_id, "post_now",
        f"Immediately posted '{ci['file_name']}' ({ci['media_type']}) — fb_id={fb_creation_id}",
    )
    return {
        "success": True,
        "fb_creation_id": fb_creation_id,
        "message": f"Posted successfully to Facebook! (id: {fb_creation_id})",
    }


# ============================ RUNNER (cron) ============================
def _attempt_post(sp, page_cache=None):
    """Try to publish one scheduled post. Raises on failure; records the
    ledger entry and returns the Facebook creation id on success."""
    caption = captions.freshen_for_posting(sp, page_cache=page_cache)
    media = None
    if sp.get("media_type") != "text":
        media = drive.download_file(sp["drive_file_id"])
    creation_id = poster.publish(
        media_type=sp["media_type"],
        page_id=sp["fb_page_id"],
        page_token=sp["fb_access_token"],
        media_bytes=media,
        caption=caption,
        scheduled_ts=None,  # time already arrived -> publish now
    )
    db.record_ledger(sp["page_id"], sp["source_video_id"], sp["content_item_id"],
                     creation_id, caption)
    return creation_id


# Vercel caps a Hobby serverless invocation at 60s. If we blow through that
# the process is killed mid-flight: the `finally` that clears the run lock
# never executes, and on a warm-reused instance every later invocation short
# circuits on "already_running" — posting stops until the instance recycles.
# So every pass works against one shared deadline and, crucially, refuses to
# START a unit of work it cannot expect to finish inside it.
RUN_BUDGET_SECONDS = float(os.environ.get("RUN_BUDGET_SECONDS", 50))
POST_RESERVE_SECONDS = float(os.environ.get("POST_RESERVE_SECONDS", 22))
REFILL_RESERVE_SECONDS = float(os.environ.get("REFILL_RESERVE_SECONDS", 8))


def _remaining(start_time):
    """Seconds left in this invocation's budget."""
    if not start_time:
        return float("inf")
    return RUN_BUDGET_SECONDS - (dt.datetime.now().timestamp() - start_time)


def _process_due_posts(start_time=None, max_seconds=None):
    posted = failed = 0
    deferred = 0
    page_cache = {}  # one page lookup per page, not per post
    broken_pages = set()  # circuit breaker: pages with token/auth outage

    for sp in db.due_posts():
        if _remaining(start_time) < POST_RESERVE_SECONDS:
            deferred += 1
            continue

        sched_id = sp["id"]
        page_id = sp["page_id"]

        # 1. Circuit breaker: if page already failed with an auth outage, skip without downloading
        if page_id in broken_pages:
            deferred += 1
            continue

        # No artificial rate-limiting caps — users have full freedom to post anytime


        # 3. Dedup check
        if db.already_posted(page_id, sp["source_video_id"]):
            db.mark_status(sched_id, "skipped", error="already posted to this page")
            continue

        # 4. Pre-flight token check before downloading heavy video file from Google Drive
        token_valid, token_detail = poster.check_token(sp["fb_page_id"], sp["fb_access_token"])
        if not token_valid:
            if poster.is_auth_error(token_detail):
                broken_pages.add(page_id)
                db.update_page_token_health(page_id, "invalid", token_detail)
                print(f"[run] Auth circuit breaker tripped for page={page_id} ({sp.get('page_name')}): {token_detail}. Post kept pending.")
                continue
            db.mark_status(sched_id, "failed", error=f"Token check failed: {token_detail}")
            failed += 1
            continue
        else:
            db.update_page_token_health(page_id, "valid")

        db.mark_status(sched_id, "posting")
        try:
            creation_id = _attempt_post(sp, page_cache=page_cache)
            db.mark_status(sched_id, "posted", fb_creation_id=creation_id)
            posted += 1
            posts_per_page_count[page_id] = posts_per_page_count.get(page_id, 0) + 1
        except Exception as e:
            err_msg = str(e)
            if poster.is_auth_error(err_msg):
                broken_pages.add(page_id)
                db.update_page_token_health(page_id, "invalid", err_msg)
                print(f"[run] Auth failure tripped breaker for page={page_id}: {err_msg}")
            db.mark_status(sched_id, "failed", error=err_msg)
            failed += 1

    if deferred:
        print(f"[run] deferred {deferred} due post(s) to protect schedule/budget")
    return {"posted": posted, "failed": failed, "deferred": deferred}


def _process_due_snapchat_posts(start_time=None):
    """Snapchat leg of an /api/run pass.

    The queue logic lives in snapchat_runner, shared with the GitHub Actions
    runner and the dashboard's retry button. `remaining` lets this stop
    cleanly before Vercel's function timeout instead of being killed
    mid-upload; unclaimed jobs simply stay pending for the next run.
    """
    def remaining():
        return _remaining(start_time) - POST_RESERVE_SECONDS

    counts = snapchat_runner.process_due(remaining=remaining)
    retry_counts = snapchat_runner.process_retries()

    return {
        "posted": counts["posted"] + retry_counts["posted"],
        "failed": counts["failed"] + retry_counts["failed"],
        "abandoned": counts["abandoned"] + retry_counts["abandoned"],
        "skipped": counts["skipped"],
        "deferred": counts["deferred"],
    }


def _process_retries(start_time=None, max_seconds=None):
    """Best-effort: retries failed posts that have cooled down.
    Crucial fix: posts failed due to AUTH / TOKEN errors are NEVER marked
    abandoned, so a temporary token outage doesn't destroy the schedule queue."""
    retried = abandoned = 0
    page_cache = {}
    broken_pages = set()

    try:
        for sp in db.retryable_failed_posts():
            if _remaining(start_time) < POST_RESERVE_SECONDS:
                print("[run] budget spent; deferring remaining retries to the next run")
                break

            sched_id = sp["id"]
            page_id = sp["page_id"]

            if page_id in broken_pages:
                continue

            if db.already_posted(page_id, sp["source_video_id"]):
                db.mark_status(sched_id, "skipped", error="already posted to this page")
                continue

            # Pre-flight check before retrying
            token_valid, token_detail = poster.check_token(sp["fb_page_id"], sp["fb_access_token"])
            if not token_valid:
                if poster.is_auth_error(token_detail):
                    broken_pages.add(page_id)
                    db.update_page_token_health(page_id, "invalid", token_detail)
                # DO NOT increment attempts or abandon on auth outage
                continue

            db.mark_status(sched_id, "posting")
            try:
                creation_id = _attempt_post(sp, page_cache=page_cache)
                db.mark_status(sched_id, "posted", fb_creation_id=creation_id)
                retried += 1
            except Exception as e:
                err_str = str(e)
                if poster.is_auth_error(err_str):
                    broken_pages.add(page_id)
                    db.update_page_token_health(page_id, "invalid", err_str)
                    # Never abandon on auth failure
                    db.mark_status(sched_id, "failed", error=err_str)
                else:
                    prior_attempts = sp.get("attempts") or 0
                    if prior_attempts + 1 >= 3:
                        db.mark_status(sched_id, "abandoned", error=err_str)
                        abandoned += 1
                    else:
                        db.mark_status(sched_id, "failed", error=err_str)
    except Exception:
        print("[run] retry pass skipped (likely pre-migration):")
        traceback.print_exc()
    return {"retried": retried, "abandoned": abandoned}


def _process_auto_refill(start_time=None):
    """Best-effort: for each active page whose pending queue is running low
    (<10) and which has unscheduled content plus a remembered auto-schedule
    pattern, re-run that pattern to top the queue back up. Skips pages that
    have never used auto-schedule (no pattern to repeat).

    Queue depths and free-inventory counts are fetched for all candidate pages
    in two queries rather than two per page — this runs every 15 minutes, and
    per-page round-trips are what make it crawl at 100+ pages."""
    refilled = []
    # Refilling is pure bookkeeping and always safe to skip — a topped-up queue
    # matters far less than returning before the function is killed, which is
    # what leaves the run lock wedged.
    if _remaining(start_time) < REFILL_RESERVE_SECONDS:
        print("[run] budget spent; skipping auto-refill this run")
        return refilled
    try:
        candidates = [p for p in db.list_pages(active_only=True)
                      if p.get("last_auto_schedule_settings")]
        if not candidates:
            return refilled
        ids = [p["id"] for p in candidates]
        stats = {s["page_id"]: s for s in db.page_stats(ids)}
        unposted = db.count_unposted_by_page(ids)

        for page in candidates:
            if _remaining(start_time) < REFILL_RESERVE_SECONDS:
                print("[run] budget spent mid-refill; remaining pages wait for the next run")
                break
            try:
                settings = page["last_auto_schedule_settings"]
                stat = stats.get(page["id"]) or {}
                if (stat.get("pending_scheduled") or 0) >= 10:
                    continue
                if unposted.get(page["id"], 0) <= 0:
                    continue
                result = _do_auto_schedule(
                    page,
                    posts_per_day=settings.get("posts_per_day") or 1,
                    start_date=dt.date.today(),
                    post_times=settings.get("post_times") or ["12:00"],
                )
                if result["scheduled"]:
                    refilled.append({"page_id": page["id"], "scheduled": result["scheduled"]})
                    db.log_activity(page["id"], "auto_refill",
                                     f"Auto-refilled {result['scheduled']} post(s)")
            except Exception:
                print(f"[run] auto-refill failed for page={page.get('id')}:")
                traceback.print_exc()
    except Exception:
        print("[run] auto-refill pass failed:")
        traceback.print_exc()
    return refilled


_run_lock = threading.Lock()
# Timestamp of the in-flight run rather than a bare boolean. A serverless
# invocation killed at the wall clock limit never reaches its `finally`, so a
# boolean would stay True on a warm-reused instance and every subsequent cron
# fire would answer "already_running" forever — the posting queue silently
# stops. A stamp lets the next run notice the holder is impossibly old and
# take over.
_run_started_at = None
# Comfortably longer than any single invocation can live, so a genuinely
# concurrent run is still excluded but a dead one never blocks forever.
RUN_LOCK_TTL_SECONDS = float(os.environ.get("RUN_LOCK_TTL_SECONDS", 120))


def _claim_run_slot():
    """Returns True if this caller now owns the posting run."""
    global _run_started_at
    now = dt.datetime.now().timestamp()
    with _run_lock:
        if _run_started_at is not None and (now - _run_started_at) < RUN_LOCK_TTL_SECONDS:
            return False
        if _run_started_at is not None:
            print(f"[run] previous run stalled {now - _run_started_at:.0f}s ago "
                  f"(likely killed at the function time limit); taking over")
        _run_started_at = now
        return True


def _release_run_slot():
    global _run_started_at
    with _run_lock:
        _run_started_at = None


def _do_run(start_time):
    # 1. Automatically self-heal any zombie posts stuck in 'posting' status
    healed = db.heal_stuck_posting_posts(max_age_minutes=15)
    if healed:
        print(f"[run] auto-healed {healed} zombie post(s) stuck in 'posting'")

    due_fb = _process_due_posts(start_time=start_time)
    due_snap = _process_due_snapchat_posts(start_time=start_time)
    retry = _process_retries(start_time=start_time)
    refill = _process_auto_refill(start_time=start_time)

    total_posted = due_fb["posted"] + due_snap["posted"]
    total_failed = due_fb["failed"] + due_snap["failed"]
    total_deferred = due_fb.get("deferred", 0) + due_snap.get("deferred", 0)

    print(f"[run] due: {total_posted} posted (FB: {due_fb['posted']}, Snap: {due_snap['posted']}), "
          f"{total_failed} failed, {total_deferred} deferred | "
          f"retried: {retry['retried']}, abandoned: {retry['abandoned']} | "
          f"refilled: {len(refill)} page(s)")
    return {"fb": due_fb, "snap": due_snap, "posted": total_posted, "failed": total_failed}, retry, refill


def _run_posting_job():
    try:
        _do_run(dt.datetime.now().timestamp())
    except Exception:
        print("[run] FAILED")
        traceback.print_exc()
    finally:
        _release_run_slot()


def _authorize_run(x_run_token="", authorization="", token=None):
    """Accepts either RUN_TOKEN (your own cron) or CRON_SECRET (the header
    Vercel's native cron sends), via X-Run-Token, a Bearer header, or ?token=."""
    expected = os.environ.get("RUN_TOKEN", "")
    cron_secret = os.environ.get("CRON_SECRET", "")

    bearer_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    if expected or cron_secret:
        valid_tokens = {t for t in [expected, cron_secret] if t}
        provided_tokens = {t for t in [x_run_token, bearer_token, token] if t}
        if not (valid_tokens & provided_tokens):
            raise HTTPException(401, "Bad run token or unauthorized cron")
    return expected or cron_secret


def _self_base_url():
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    host = (os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
            or os.environ.get("VERCEL_URL") or "").strip().rstrip("/")
    return f"https://{host}" if host else ""


@app.api_route("/api/run/trigger", methods=["GET", "POST"])
def run_trigger(
    x_run_token: str = Header(default=""),
    authorization: str = Header(default=""),
    token: Optional[str] = None,
):
    """Fire-and-forget entry point for cron services that cap how long they
    will wait. cron-job.org aborts a request at 30 seconds and a single reel —
    a Drive download plus a resumable upload to Facebook — takes about that
    long by itself, so a cron pointed straight at /api/run records a timeout
    on almost every fire even though the post succeeded. Those pile up as
    failures and eventually get the job disabled.

    This starts /api/run as a SEPARATE invocation and returns immediately, so
    the cron always sees a fast 200 while the posting run still gets its full
    time budget."""
    run_token = _authorize_run(x_run_token, authorization, token)

    base = _self_base_url()
    if not base:
        raise HTTPException(500, "PUBLIC_BASE_URL is not set; cannot self-trigger")

    import requests
    try:
        # The read timeout is the point: we hang up early on purpose. The
        # request has already been delivered, so the /api/run invocation runs
        # to completion on its own regardless of this socket closing.
        requests.get(f"{base}/api/run",
                     headers={"X-Run-Token": run_token},
                     timeout=(5, 1.5))
    except requests.exceptions.ReadTimeout:
        pass
    except Exception as e:
        raise HTTPException(502, f"Could not reach the run endpoint: {e}")

    return {"status": "triggered"}


@app.api_route("/api/run", methods=["GET", "POST"])
def run_due(
    x_run_token: str = Header(default=""),
    authorization: str = Header(default=""),
    token: Optional[str] = None,
):
    _authorize_run(x_run_token, authorization, token)

    if not _claim_run_slot():
        # A previous run is still working through a backlog — guard
        # against an overlapping cron fire double-posting.
        return {"status": "already_running"}

    try:
        due, retry, refill = _do_run(dt.datetime.now().timestamp())
        return {"status": "complete", "due": due, "retry": retry, "refill": refill}
    except Exception as e:
        print("[run] FAILED")
        traceback.print_exc()
        raise HTTPException(500, f"Run job failed: {e}")
    finally:
        _release_run_slot()


# ============================ ANALYTICS ============================
IST_OFFSET = dt.timedelta(hours=5, minutes=30)


def _fmt_ist(ts):
    """Format a UTC timestamp as a human-readable IST string."""
    if not ts:
        return None
    if isinstance(ts, str):
        try:
            ts = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return ts
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=dt.timezone.utc)
    ist = ts + IST_OFFSET
    return ist.strftime("%a %d %b at %-I:%M %p IST").replace("AM", "AM").replace("PM", "PM")


@app.get("/api/pages/{page_id}/analytics")
def page_analytics(page_id: int):
    snapshots = db.analytics_for_page(page_id)
    if snapshots:
        # Real analytics data available — return as-is
        return _clean_list(snapshots)

    # Fallback: return posted_ledger rows with 0-metric stubs so the
    # analytics page immediately shows posted reels instead of being blank.
    ledger = db.posted_ledger_for_page(page_id)
    out = []
    for row in ledger:
        d = _clean(row)
        d.setdefault("views", 0)
        d.setdefault("likes", 0)
        d.setdefault("comments", 0)
        d.setdefault("shares", 0)
        d.setdefault("reach", 0)
        d["note"] = "Analytics will refresh soon"
        d["posted_at_ist"] = _fmt_ist(row.get("posted_at"))
        out.append(d)
    return out


@app.api_route("/api/analytics/refresh", methods=["GET", "POST"])
def refresh_analytics(
    x_run_token: str = Header(default=""),
    authorization: str = Header(default=""),
    token: Optional[str] = None,
):
    expected = os.environ.get("RUN_TOKEN", "")
    cron_secret = os.environ.get("CRON_SECRET", "")

    bearer_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    if expected or cron_secret:
        valid_tokens = {t for t in [expected, cron_secret] if t}
        provided_tokens = {t for t in [x_run_token, bearer_token, token] if t}
        if not (valid_tokens & provided_tokens):
            raise HTTPException(401, "Bad run token or unauthorized cron")

    updated = 0
    for row in db.posted_items_for_analytics():
        m = poster.fetch_reel_insights(row["fb_creation_id"], row["fb_access_token"])
        if "error" in m:
            continue
        db.save_analytics(row["page_id"], row["fb_creation_id"], row["source_video_id"], m)
        updated += 1
    return {"updated": updated}


@app.get("/api/health")
def health():
    return {"ok": True, "time": dt.datetime.now(dt.timezone.utc).isoformat()}


# ==============================================================================
# Remote MCP (Model Context Protocol) API Bridge
# Allows remote external platforms, agents, and auto-sharing tools to discover
# and execute dashboard tools securely over HTTP.
# ==============================================================================

@app.get("/api/mcp/tools")
def list_mcp_tools():
    """Returns the list of available MCP tools and their parameter schemas."""
    return {
        "server": "Reels Dashboard MCP Bridge",
        "version": "1.0.0",
        "tools": [
            {
                "name": "list_pages",
                "description": "Lists all 9 Facebook pages with library counts, posted counts, unposted remaining, and schedule status.",
                "parameters": {"active_only": {"type": "boolean", "default": False}},
            },
            {
                "name": "get_page_stats",
                "description": "Returns detailed statistics and Drive configuration for a page.",
                "parameters": {"page_id": {"type": "integer", "required": True}},
            },
            {
                "name": "get_published_reels",
                "description": "Returns recently published Reels with FB creation IDs, captions, hashtags, and post links. (Core tool for auto-sharing).",
                "parameters": {
                    "page_id": {"type": "integer", "required": False},
                    "limit": {"type": "integer", "default": 20},
                },
            },
            {
                "name": "get_unposted_library",
                "description": "Fetches unposted library videos ready for scheduling.",
                "parameters": {
                    "page_id": {"type": "integer", "required": True},
                    "limit": {"type": "integer", "default": 20},
                    "search": {"type": "string", "required": False},
                },
            },
            {
                "name": "trigger_drive_sync",
                "description": "Triggers Google Drive recursive sync for a page.",
                "parameters": {"page_id": {"type": "integer", "required": True}},
            },
            {
                "name": "get_sync_status",
                "description": "Checks live sync progress for a page.",
                "parameters": {"page_id": {"type": "integer", "required": True}},
            },
            {
                "name": "get_upcoming_schedule",
                "description": "Returns pending upcoming scheduled posts.",
                "parameters": {
                    "page_id": {"type": "integer", "required": False},
                    "limit": {"type": "integer", "default": 30},
                },
            },
            {
                "name": "get_analytics_leaderboard",
                "description": "Returns top-performing Reels ranked by views, likes, comments, and reach.",
                "parameters": {"page_id": {"type": "integer", "required": True}},
            },
        ],
    }


@app.post("/api/mcp/execute")
def execute_mcp_tool(
    payload: dict = Body(...),
    x_mcp_key: str = Header(default=""),
    authorization: str = Header(default=""),
):
    """Executes an MCP tool remotely and returns structured JSON output."""
    expected_key = os.environ.get("MCP_API_KEY", "")
    if expected_key:
        token = x_mcp_key or (authorization.replace("Bearer ", "") if authorization else "")
        if token != expected_key:
            raise HTTPException(401, "Invalid MCP API Key")

    tool_name = payload.get("tool")
    params = payload.get("params", {})

    if tool_name == "list_pages":
        pages = db.list_pages()
        if params.get("active_only"):
            pages = [p for p in pages if p.get("is_active")]
        return {"result": _clean_list(pages)}

    elif tool_name == "get_page_stats":
        page_id = params.get("page_id")
        if not page_id:
            raise HTTPException(400, "Missing required parameter 'page_id'")
        page = db.get_page(int(page_id))
        return {"result": _clean(page) if page else None}

    elif tool_name == "get_published_reels":
        page_id = params.get("page_id")
        limit = int(params.get("limit", 20))
        with db._conn() as c, c.cursor() as cur:
            if page_id:
                cur.execute(
                    """select l.id, l.page_id, p.name as page_name, l.source_video_id,
                              l.content_item_id, l.fb_creation_id, l.posted_at,
                              ci.file_name, ci.caption, ci.hashtags, ci.sound_title
                       from posted_ledger l
                       join pages p on p.id = l.page_id
                       left join content_items ci on ci.id = l.content_item_id
                       where l.page_id = %s and l.fb_creation_id is not null
                       order by l.posted_at desc
                       limit %s""",
                    (int(page_id), limit),
                )
            else:
                cur.execute(
                    """select l.id, l.page_id, p.name as page_name, l.source_video_id,
                              l.content_item_id, l.fb_creation_id, l.posted_at,
                              ci.file_name, ci.caption, ci.hashtags, ci.sound_title
                       from posted_ledger l
                       join pages p on p.id = l.page_id
                       left join content_items ci on ci.id = l.content_item_id
                       where l.fb_creation_id is not null
                       order by l.posted_at desc
                       limit %s""",
                    (limit,),
                )
            rows = cur.fetchall()
        result = []
        for r in rows:
            cid = r.get("fb_creation_id")
            result.append({
                "ledger_id": r.get("id"),
                "page_id": r.get("page_id"),
                "page_name": r.get("page_name"),
                "source_video_id": r.get("source_video_id"),
                "file_name": r.get("file_name"),
                "fb_creation_id": cid,
                "fb_reel_url": f"https://www.facebook.com/reel/{cid}" if cid else None,
                "caption": r.get("caption") or "",
                "hashtags": r.get("hashtags") or [],
                "sound_title": r.get("sound_title"),
                "posted_at": r.get("posted_at"),
            })
        return {"result": _clean_list(result)}

    elif tool_name == "get_unposted_library":
        page_id = int(params.get("page_id"))
        limit = int(params.get("limit", 20))
        search = params.get("search")
        items = db.list_unposted_content(page_id, limit=limit, search=search)
        return {"result": _clean_list(items)}

    elif tool_name == "trigger_drive_sync":
        page_id = int(params.get("page_id"))
        _run_sync(page_id)
        final_state, _, _ = db.get_sync_lock(page_id)
        return {"result": final_state or {"status": "complete"}}

    elif tool_name == "get_sync_status":
        page_id = int(params.get("page_id"))
        state, _, _ = db.get_sync_lock(page_id)
        return {"result": state or {"status": "idle"}}

    elif tool_name == "get_upcoming_schedule":
        page_id = params.get("page_id")
        limit = int(params.get("limit", 30))
        posts = db.list_scheduled_posts(page_id=int(page_id) if page_id else None, status="pending", limit=limit)
        return {"result": _clean_list(posts)}

    elif tool_name == "get_analytics_leaderboard":
        page_id = int(params.get("page_id"))
        analytics = db.analytics_for_page(page_id)
        return {"result": _clean_list(analytics)}

    else:
        raise HTTPException(404, f"Unknown MCP tool '{tool_name}'")


# ============================ RUNNER CRON TRIGGER ============================
@app.post("/api/run")
@app.get("/api/run")
def trigger_runner(bg: BackgroundTasks, x_run_token: Optional[str] = Header(None)):
    """Triggered by cron / scheduler to execute due posts and self-healing sweep."""
    expected = os.environ.get("RUN_TOKEN")
    if expected and x_run_token != expected:
        raise HTTPException(403, "Invalid run token")

    def run_all():
        from core.watchdog import WatchdogService
        import runner
        WatchdogService.run_sweep()
        try:
            runner.run_due_posts()
        except Exception as e:
            applog.logger.error(f"Error in runner.run_due_posts: {e}")
        try:
            runner.run_due_snapchat_posts()
        except Exception as e:
            applog.logger.error(f"Error in runner.run_due_snapchat_posts: {e}")

    bg.add_task(run_all)
    return {"status": "started", "message": "Self-healing sweep & due post execution started in background."}
