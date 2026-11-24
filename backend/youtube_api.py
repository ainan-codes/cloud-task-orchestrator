"""
youtube_api.py — YouTube Studio & Shorts API endpoints.

Handles YouTube channels, scheduled upload jobs, and publishing records.
Uses Supabase Postgres backend.
"""

import os
import requests
import datetime as dt
from typing import Optional, List
from urllib.parse import urlencode
from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from db import _conn

try:
    from snapchat_crypto import encrypt_token, decrypt_token
except ImportError:
    def encrypt_token(val): return val
    def decrypt_token(val): return val

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


try:
    import credentials
except ImportError:
    credentials = None


def _get_active_credentials():
    cid = (credentials.get("youtube_client_id") if credentials else None) or os.environ.get("YOUTUBE_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID")
    csec = (credentials.get("youtube_client_secret") if credentials else None) or os.environ.get("YOUTUBE_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET")
    uri = (credentials.get("youtube_redirect_uri") if credentials else None) or os.environ.get("YOUTUBE_REDIRECT_URI") or "https://cloud-task-orchestrator.vercel.app/api/youtube/oauth/callback"
    return cid, csec, uri


@youtube_router.get("/config")
def get_youtube_config():
    """Return YouTube API configuration status."""
    client_id, client_secret, redirect_uri = _get_active_credentials()
    return {
        "ready": bool(client_id and client_secret),
        "client_id_configured": bool(client_id),
        "client_secret_configured": bool(client_secret),
        "redirect_uri": redirect_uri,
        "api_service": "YouTube Data API v3",
        "quota_limit_per_day": 10000,
        "upload_cost_units": 1600,
    }


@youtube_router.get("/oauth/authorize")
def youtube_oauth_authorize():
    """Return Google OAuth 2.0 authorization URL for YouTube."""
    client_id, client_secret, redirect_uri = _get_active_credentials()
    if not client_id:
        raise HTTPException(400, "YOUTUBE_CLIENT_ID is not configured in environment or vault")

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/youtube.force-ssl https://www.googleapis.com/auth/youtube https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
    }
    auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    return {"authorization_url": auth_url}


@youtube_router.get("/oauth/callback")
def youtube_oauth_callback(
    code: Optional[str] = None,
    error: Optional[str] = None
):
    """Handle OAuth redirect from Google, store channel tokens, and redirect to UI."""
    client_id, client_secret, redirect_uri = _get_active_credentials()

    frontend_base = "http://localhost:5173" if "localhost" in redirect_uri else ""

    if error:
        return RedirectResponse(f"{frontend_base}/?tab=youtube&error={error}")
    if not code:
        raise HTTPException(400, "Missing authorization code from Google")

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        },
        timeout=15,
    )
    tokens = token_resp.json()
    if "access_token" not in tokens:
        err = tokens.get("error_description") or tokens.get("error") or token_resp.text
        return RedirectResponse(f"{frontend_base}/?tab=youtube&error={err}")

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 3600)
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=expires_in)

    channel_resp = requests.get(
        "https://www.googleapis.com/youtube/v3/channels",
        params={"part": "snippet,contentDetails", "mine": "true"},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    ch_data = channel_resp.json()
    items = ch_data.get("items", [])
    if not items:
        return RedirectResponse(f"{frontend_base}/?tab=youtube&error=No+YouTube+channel+found+for+this+Google+account")

    ch = items[0]
    channel_id = ch["id"]
    snippet = ch.get("snippet", {})
    title = snippet.get("title", "My YouTube Channel")
    custom_url = snippet.get("customUrl")
    thumb = snippet.get("thumbnails", {}).get("default", {}).get("url")

    enc_access = encrypt_token(access_token) if callable(encrypt_token) else access_token
    enc_refresh = encrypt_token(refresh_token) if (refresh_token and callable(encrypt_token)) else refresh_token

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO youtube_channels (
                    channel_id, title, custom_url, thumbnail_url,
                    access_token_encrypted, refresh_token_encrypted,
                    token_expires_at, is_connected, client_id, client_secret, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s, now())
                ON CONFLICT (channel_id) DO UPDATE SET
                    title = EXCLUDED.title,
                    custom_url = COALESCE(EXCLUDED.custom_url, youtube_channels.custom_url),
                    thumbnail_url = COALESCE(EXCLUDED.thumbnail_url, youtube_channels.thumbnail_url),
                    access_token_encrypted = EXCLUDED.access_token_encrypted,
                    refresh_token_encrypted = COALESCE(EXCLUDED.refresh_token_encrypted, youtube_channels.refresh_token_encrypted),
                    token_expires_at = EXCLUDED.token_expires_at,
                    client_id = COALESCE(EXCLUDED.client_id, youtube_channels.client_id),
                    client_secret = COALESCE(EXCLUDED.client_secret, youtube_channels.client_secret),
                    is_connected = true,
                    updated_at = now()
                RETURNING id, channel_id, title;
            """, (
                channel_id, title, custom_url, thumb,
                enc_access, enc_refresh, expires_at,
                client_id, client_secret
            ))
            conn.commit()

    return RedirectResponse(f"{frontend_base}/?tab=youtube&connected=1")


class ChannelDescUpdate(BaseModel):
    description: str


@youtube_router.get("/channels")
def list_youtube_channels():
    """List connected YouTube channels with per-channel job statistics."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.channel_id, c.title, c.custom_url, c.thumbnail_url,
                       c.default_privacy, c.default_tags, c.is_connected,
                       c.token_expires_at, c.created_at, c.updated_at, c.description,
                       COUNT(j.id) FILTER (WHERE j.status = 'pending') as pending_count,
                       COUNT(j.id) FILTER (WHERE j.status = 'posted') as posted_count,
                       COUNT(j.id) FILTER (WHERE j.status = 'failed') as failed_count,
                       COUNT(j.id) as total_jobs_count
                FROM youtube_channels c
                LEFT JOIN youtube_jobs j ON c.id = j.channel_id
                GROUP BY c.id
                ORDER BY c.id ASC;
            """)
            return cur.fetchall()


@youtube_router.post("/channels/{channel_id}/update-description")
def update_channel_description(channel_id: int, req: ChannelDescUpdate):
    """Update channel description in database and attempt live YouTube API sync."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE youtube_channels
                SET description = %s, updated_at = now()
                WHERE id = %s
                RETURNING id, channel_id, title, client_id, client_secret,
                          access_token_encrypted, refresh_token_encrypted;
            """, (req.description, channel_id))
            ch = cur.fetchone()
            conn.commit()

    if not ch:
        raise HTTPException(404, "Channel not found")

    api_success = False
    api_err = None
    try:
        import youtube_runner
        from googleapiclient.discovery import build
        creds = youtube_runner._get_youtube_creds(ch)
        yt = build("youtube", "v3", credentials=creds)
        yt.channels().update(
            part="brandingSettings",
            body={
                "id": ch["channel_id"],
                "brandingSettings": {
                    "channel": {
                        "description": req.description
                    }
                }
            }
        ).execute()
        api_success = True
    except Exception as e:
        api_err = str(e)

    return {
        "success": True,
        "channel_id": channel_id,
        "api_synced": api_success,
        "error": api_err,
        "message": (
            "Channel description updated live on YouTube!"
            if api_success
            else "Description saved in system. (To push live to YouTube via API, re-authorize channel to grant channel management scope)."
        )
    }


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
def list_youtube_jobs(
    limit: int = 1000,
    offset: int = 0,
    order: str = "asc",
    status: Optional[str] = None,
    channel_id: Optional[int] = None
):
    """List queued and historical YouTube posting jobs.
    Defaults to ASC order so upcoming scheduled posts appear first chronologically.
    Supports filtering by status and channel_id. Default limit is 1000."""
    with _conn() as conn:
        with conn.cursor() as cur:
            sort_order = "DESC" if order.lower() == "desc" else "ASC"
            where_clauses = []
            params = []
            if status:
                where_clauses.append("j.status = %s")
                params.append(status)
            if channel_id:
                where_clauses.append("j.channel_id = %s")
                params.append(channel_id)
            where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            cur.execute(f"""
                SELECT j.id, j.channel_id, c.title as channel_title,
                       j.content_item_id, j.file_name, j.title, j.description,
                       j.tags, j.privacy_status, j.is_short, j.scheduled_time_utc,
                       j.status, j.video_id, j.error, j.attempts, j.created_at, j.posted_at
                FROM youtube_jobs j
                JOIN youtube_channels c ON j.channel_id = c.id
                {where_sql}
                ORDER BY j.scheduled_time_utc {sort_order}
                LIMIT %s OFFSET %s;
            """, (*params, limit, offset))
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


@youtube_router.post("/jobs/{job_id}/post-now")
def post_youtube_job_now_endpoint(job_id: int):
    """Instantly publish a scheduled YouTube Short or video now."""
    try:
        import youtube_runner
        res = youtube_runner.post_youtube_job_now(job_id)
        return res
    except ValueError as ve:
        raise HTTPException(404, str(ve))
    except Exception as e:
        raise HTTPException(500, str(e))


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
def list_youtube_history(
    channel_id: Optional[int] = None,
    limit: int = 200
):
    """List YouTube published ledger records with optional channel filter."""
    with _conn() as conn:
        with conn.cursor() as cur:
            where_sql = "WHERE r.channel_id = %s" if channel_id else ""
            params = [channel_id] if channel_id else []
            cur.execute(f"""
                SELECT r.id, r.channel_id, c.title as channel_title,
                       r.video_id, r.title, r.privacy_status, r.status,
                       r.error, r.published_at
                FROM youtube_publishing_records r
                JOIN youtube_channels c ON r.channel_id = c.id
                {where_sql}
                ORDER BY r.published_at DESC
                LIMIT %s;
            """, (*params, limit))
            return cur.fetchall()
