"""
youtube_api.py — YouTube Studio & Shorts API endpoints.

Handles YouTube channels, scheduled upload jobs, and publishing records.
Uses Supabase Postgres backend.
"""

import os
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from db import _conn

youtube_router = APIRouter(prefix="/api/youtube", tags=["youtube"])


class ChannelCreateRequest(BaseModel):
    channel_id: str
    title: str
    custom_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    default_privacy: str = "public"
    default_tags: List[str] = []
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None


class JobCreateRequest(BaseModel):
    channel_id: int
    content_item_id: Optional[int] = None
    file_name: Optional[str] = None
    title: str
    description: str = ""
    tags: List[str] = []
    privacy_status: str = "public"
    is_short: bool = True
    scheduled_time_utc: str


@youtube_router.get("/config")
def get_youtube_config():
    """Return YouTube API configuration status."""
    client_id = os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.environ.get("YOUTUBE_REDIRECT_URI", "https://your-domain/api/youtube/oauth/callback")
    return {
        "ready": bool(client_id and client_secret),
        "client_id_configured": bool(client_id),
        "client_secret_configured": bool(client_secret),
        "redirect_uri": redirect_uri,
        "api_service": "YouTube Data API v3",
        "quota_limit_per_day": 10000,
        "upload_cost_units": 1600,
    }


@youtube_router.get("/channels")
def list_youtube_channels():
    """List connected YouTube channels."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, channel_id, title, custom_url, thumbnail_url,
                       default_privacy, default_tags, is_connected,
                       token_expires_at, created_at, updated_at
                FROM youtube_channels
                ORDER BY id ASC;
            """)
            return cur.fetchall()


@youtube_router.post("/channels")
def add_youtube_channel(req: ChannelCreateRequest):
    """Add or update a YouTube channel."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO youtube_channels (
                    channel_id, title, custom_url, thumbnail_url,
                    default_privacy, default_tags, is_connected,
                    access_token_encrypted, refresh_token_encrypted, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, true, %s, %s, now())
                ON CONFLICT (channel_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    custom_url = EXCLUDED.custom_url,
                    thumbnail_url = EXCLUDED.thumbnail_url,
                    default_privacy = EXCLUDED.default_privacy,
                    default_tags = EXCLUDED.default_tags,
                    is_connected = true,
                    updated_at = now()
                RETURNING id, channel_id, title, default_privacy;
            """, (
                req.channel_id, req.title, req.custom_url, req.thumbnail_url,
                req.default_privacy, req.default_tags,
                req.access_token, req.refresh_token
            ))
            row = cur.fetchone()
            conn.commit()
            return {"success": True, "channel": row}


@youtube_router.delete("/channels/{channel_id}")
def delete_youtube_channel(channel_id: int):
    """Remove a YouTube channel and cascade delete its jobs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM youtube_channels WHERE id = %s RETURNING id;", (channel_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Channel not found")
            conn.commit()
            return {"deleted": True, "id": channel_id}


@youtube_router.get("/jobs")
def list_youtube_jobs():
    """List queued and historical YouTube posting jobs."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.id, j.channel_id, c.title as channel_title,
                       j.content_item_id, j.file_name, j.title, j.description,
                       j.tags, j.privacy_status, j.is_short, j.scheduled_time_utc,
                       j.status, j.video_id, j.error, j.attempts, j.created_at, j.posted_at
                FROM youtube_jobs j
                JOIN youtube_channels c ON j.channel_id = c.id
                ORDER BY j.scheduled_time_utc DESC
                LIMIT 100;
            """)
            return cur.fetchall()


@youtube_router.post("/jobs")
def create_youtube_job(req: JobCreateRequest):
    """Queue a video or Short to YouTube."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO youtube_jobs (
                    channel_id, content_item_id, file_name, title, description,
                    tags, privacy_status, is_short, scheduled_time_utc, status
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id, channel_id, title, scheduled_time_utc, status;
            """, (
                req.channel_id, req.content_item_id, req.file_name, req.title,
                req.description, req.tags, req.privacy_status, req.is_short,
                req.scheduled_time_utc
            ))
            row = cur.fetchone()
            conn.commit()
            return {"success": True, "job": row}


@youtube_router.post("/jobs/{job_id}/retry")
def retry_youtube_job(job_id: int):
    """Retry a failed YouTube upload job."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE youtube_jobs
                SET status = 'pending', attempts = 0, error = null
                WHERE id = %s
                RETURNING id, status;
            """, (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            conn.commit()
            return {"success": True, "job": row}


@youtube_router.delete("/jobs/{job_id}")
def cancel_youtube_job(job_id: int):
    """Cancel a scheduled YouTube post."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM youtube_jobs WHERE id = %s RETURNING id;", (job_id,))
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "Job not found")
            conn.commit()
            return {"cancelled": True, "id": job_id}


@youtube_router.get("/history")
def list_youtube_history():
    """List YouTube published ledger records."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT r.id, r.channel_id, c.title as channel_title,
                       r.video_id, r.title, r.privacy_status, r.status,
                       r.error, r.published_at
                FROM youtube_publishing_records r
                JOIN youtube_channels c ON r.channel_id = c.id
                ORDER BY r.published_at DESC
                LIMIT 100;
            """)
            return cur.fetchall()
