"""
rumble_api.py — Rumble Platform API & Automation endpoints.

Handles Rumble accounts, video queue, licensing options, and publishing records.
Designed to pair with the background Playwright automation runner on the VPS.
"""

from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db import _conn

rumble_router = APIRouter(prefix="/api/rumble", tags=["rumble"])


class RumbleAccountCreateRequest(BaseModel):
    username: str
    channel_name: Optional[str] = None
    channel_id: Optional[str] = None
    default_license: str = "exclusive"  # 'exclusive' | 'rumble_only' | 'non_exclusive'
    default_visibility: str = "public"
    session_cookie: Optional[str] = None


class RumbleJobCreateRequest(BaseModel):
    account_id: int
    content_item_id: Optional[int] = None
    file_name: Optional[str] = None
    title: str
    description: str = ""
    tags: List[str] = []
    license_type: str = "exclusive"
    visibility: str = "public"
    scheduled_time_utc: str


@rumble_router.get("/config")
def get_rumble_config():
    """Return Rumble automation configuration status."""
    return {
        "ready": True,
        "automation_engine": "Playwright Headless Chrome (VPS Daemon)",
        "supported_licenses": [
            {
                "id": "exclusive",
                "label": "Exclusive Video Management",
                "blurb": "Rumble distributes to YouTube & third parties; highest revenue share."
            },
            {
                "id": "rumble_only",
                "label": "Rumble Player Only",
                "blurb": "Exclusive to Rumble platform; monetized via Rumble ads."
            },
            {
                "id": "non_exclusive",
                "label": "Personal Use / Non-Exclusive",
                "blurb": "Retains all rights; non-monetized or limited distribution."
            }
        ],
        "native_youtube_sync_supported": True,
        "supported_formats": [".mp4", ".mov"],
        "max_file_size_gb": 5,
    }


@rumble_router.get("/accounts")
def list_rumble_accounts():
    """List connected Rumble accounts."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, username, channel_name, channel_id,
                       default_license, default_visibility, is_connected,
                       created_at, updated_at
                FROM rumble_accounts
                ORDER BY id ASC;
            """)
            return cur.fetchall()


@rumble_router.post("/accounts")
def add_rumble_account(req: RumbleAccountCreateRequest):
    """Add or update a Rumble account."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rumble_accounts (
                    username, channel_name, channel_id,
                    default_license, default_visibility, session_cookie_encrypted,
                    is_connected, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, true, now())
                ON CONFLICT (username) DO UPDATE SET
                    channel_name = EXCLUDED.channel_name,
                    channel_id = EXCLUDED.channel_id,
                    default_license = EXCLUDED.default_license,
                    default_visibility = EXCLUDED.default_visibility,
                    session_cookie_encrypted = COALESCE(EXCLUDED.session_cookie_encrypted, rumble_accounts.session_cookie_encrypted),
                    is_connected = true,
                    updated_at = now()
                RETURNING id, username, channel_name, default_license;
            """, (
                req.username, req.channel_name, req.channel_id,
                req.default_license, req.default_visibility, req.session_cookie
            ))
            row = cur.fetchone()
            conn.commit()
            return {"success": True, "account": row}


@rumble_router.delete("/accounts/{account_id}")
def delete_rumble_account(account_id: int):
    """Remove a Rumble account and cascade delete its jobs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rumble_accounts WHERE id = %s RETURNING id;", (account_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Account not found")
            conn.commit()
            return {"deleted": True, "id": account_id}


@rumble_router.get("/jobs")
def list_rumble_jobs():
    """List queued and historical Rumble posting jobs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.id, j.account_id, a.username, a.channel_name,
                       j.content_item_id, j.file_name, j.title, j.description,
                       j.tags, j.license_type, j.visibility, j.scheduled_time_utc,
                       j.status, j.rumble_video_id, j.rumble_url, j.error,
                       j.attempts, j.created_at, j.posted_at
                FROM rumble_jobs j
                JOIN rumble_accounts a ON j.account_id = a.id
                ORDER BY j.scheduled_time_utc DESC
                LIMIT 100;
            """)
            return cur.fetchall()


@rumble_router.post("/jobs")
def create_rumble_job(req: RumbleJobCreateRequest):
    """Queue a video to Rumble."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO rumble_jobs (
                    account_id, content_item_id, file_name, title, description,
                    tags, license_type, visibility, scheduled_time_utc, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id, account_id, title, scheduled_time_utc, status;
            """, (
                req.account_id, req.content_item_id, req.file_name, req.title,
                req.description, req.tags, req.license_type, req.visibility,
                req.scheduled_time_utc
            ))
            row = cur.fetchone()
            conn.commit()
            return {"success": True, "job": row}


@rumble_router.post("/jobs/{job_id}/retry")
def retry_rumble_job(job_id: int):
    """Retry a failed Rumble upload job."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE rumble_jobs
                SET status = 'pending', attempts = 0, error = null
                WHERE id = %s
                RETURNING id, status;
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            conn.commit()
            return {"success": True, "job": row}


@rumble_router.delete("/jobs/{job_id}")
def cancel_rumble_job(job_id: int):
    """Cancel a scheduled Rumble post."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM rumble_jobs WHERE id = %s RETURNING id;", (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            conn.commit()
            return {"cancelled": True, "id": job_id}


@rumble_router.get("/history")
def list_rumble_history():
    """List Rumble published ledger records."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.account_id, a.username,
                       r.rumble_video_id, r.rumble_url, r.title,
                       r.license_type, r.status, r.error, r.published_at
                FROM rumble_publishing_records r
                JOIN rumble_accounts a ON r.account_id = a.id
                ORDER BY r.published_at DESC
                LIMIT 100;
            """)
            return cur.fetchall()
