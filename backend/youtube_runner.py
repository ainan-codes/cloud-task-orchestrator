"""
youtube_runner.py — Processes due YouTube Short / Video upload jobs.

Called by runner.py (invoked by GitHub Actions every 15 minutes) or triggered directly via "Post Now".
Guarantees:
1. Daily quota safety: Enforces YouTube Data API v3 upload quota (max 6 Shorts / 24h per channel).
2. Resilient token refresh: Automatically refreshes Google OAuth tokens using stored refresh_token.
3. Resumable media stream: Downloads video directly from Google Drive and streams to YouTube API.
4. Idempotency: Claims job atomically ('pending' -> 'posting') to prevent duplicate uploads.
"""

import io
import os
import sys
import logging
import datetime as dt
from typing import Tuple, Dict, Any, Optional

import psycopg
from psycopg.rows import dict_row

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseUpload
except ImportError:
    pass

import db
import drive

logger = logging.getLogger("youtube_runner")

DAILY_QUOTA_UPLOAD_LIMIT = 6  # 10,000 quota units / 1,600 per video upload = 6 videos/day


def _get_youtube_creds(channel: Dict[str, Any]) -> Credentials:
    """Decrypt and return valid Google OAuth credentials, refreshing if needed."""
    from snapchat_crypto import decrypt_token, encrypt_token

    enc_access = channel.get("access_token_encrypted")
    enc_refresh = channel.get("refresh_token_encrypted")

    access_token = decrypt_token(enc_access) if enc_access else None
    refresh_token = decrypt_token(enc_refresh) if enc_refresh else None

    try:
        import credentials
    except ImportError:
        credentials = None

    client_id = (
        channel.get("client_id")
        or (credentials.get("youtube_client_id") if credentials else None)
        or os.environ.get("YOUTUBE_CLIENT_ID")
        or os.environ.get("GOOGLE_CLIENT_ID")
    )
    client_secret = (
        channel.get("client_secret")
        or (credentials.get("youtube_client_secret") if credentials else None)
        or os.environ.get("YOUTUBE_CLIENT_SECRET")
        or os.environ.get("GOOGLE_CLIENT_SECRET")
    )

    if not client_id or not client_secret:
        raise RuntimeError("Missing YOUTUBE_CLIENT_ID or YOUTUBE_CLIENT_SECRET in environment.")

    if not refresh_token and not access_token:
        raise RuntimeError(f"No tokens available for YouTube channel {channel.get('title')}.")

    creds = Credentials(
        token=access_token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri="https://oauth2.googleapis.com/token",
    )

    # If token expired or close to expiring, refresh now
    expires_at = channel.get("token_expires_at")
    now_utc = dt.datetime.now(dt.timezone.utc)
    needs_refresh = not creds.valid or (expires_at and expires_at <= now_utc + dt.timedelta(minutes=5))

    if needs_refresh and refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed access token
            new_enc_access = encrypt_token(creds.token)
            new_expires = creds.expiry or (now_utc + dt.timedelta(seconds=3600))
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE youtube_channels SET access_token_encrypted = %s, token_expires_at = %s WHERE id = %s",
                        (new_enc_access, new_expires, channel["id"])
                    )
                    conn.commit()
            logger.info(f"Refreshed YouTube access token for {channel.get('title')}.")
        except Exception as e:
            logger.warning(f"Error refreshing YouTube token: {e}")

    return creds


def get_daily_uploaded_count(channel_id: int) -> int:
    """Return count of videos published to this channel in the last 24 hours."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT count(*) as cnt
                FROM youtube_jobs
                WHERE channel_id = %s
                  AND status = 'posted'
                  AND posted_at >= NOW() - INTERVAL '24 hours';
            """, (channel_id,))
            row = cur.fetchone()
            return row["cnt"] if row else 0


def _find_thumbnail_for_job(job: Dict[str, Any]) -> Optional[str]:
    """Find a matching high-impact vertical thumbnail for the job."""
    fn = (job.get("file_name") or "").lower()
    title = (job.get("title") or "").lower()

    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "thumbnails")

    if "athlete" in fn or "athlete" in title:
        candidate = os.path.join(base_dir, "Beat_Athlete_Win_100k.jpg")
        if os.path.exists(candidate):
            return candidate
    elif "grocery" in fn or "grocery" in title:
        candidate = os.path.join(base_dir, "Grocery_Store_Win_1M.jpg")
        if os.path.exists(candidate):
            return candidate
    elif "hunger" in fn or "hunger" in title:
        candidate = os.path.join(base_dir, "Hunger_Games_Real_Life.jpg")
        if os.path.exists(candidate):
            return candidate

    return None


def execute_youtube_job(job: Dict[str, Any]) -> str:
    """Executes a claimed YouTube job (downloads media, streams to YouTube API, updates DB). Returns video_id."""
    job_id = job["id"]
    cid = job["channel_id"]
    channel_title = job.get("channel_title", "Channel")

    # 1. Obtain authenticated credentials
    creds = _get_youtube_creds(job)
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)

    # 2. Download media bytes from Google Drive
    drive_fid = job.get("drive_file_id")
    if not drive_fid:
        raise ValueError(f"Job #{job_id} has no drive_file_id.")

    media_bytes = drive.download_file(drive_fid)
    if not media_bytes:
        raise ValueError(f"Failed to download media bytes for file {drive_fid}.")

    # 3. Format title & description for Shorts
    raw_title = job["title"] or "New Short"
    if job.get("is_short", True) and "#Shorts" not in raw_title and "#shorts" not in raw_title:
        if len(raw_title) <= 90:
            raw_title = f"{raw_title} #Shorts"
    # YouTube titles cannot exceed 100 characters
    final_title = raw_title[:100]

    raw_desc = job.get("description") or ""
    if job.get("is_short", True) and "#Shorts" not in raw_desc:
        raw_desc = f"{raw_desc}\n\n#Shorts #MrBeast #Viral"

    # Always enforce the YouTube Fan Channel Policy disclaimer
    fan_disclaimer = "\n\n---\n⚠️ Disclaimer: Unofficial fan channel sharing curated highlights and challenge moments. Not affiliated with or endorsed by MrBeast."
    if "Disclaimer:" not in raw_desc and "fan channel" not in raw_desc.lower():
        raw_desc = f"{raw_desc.rstrip()}{fan_disclaimer}"

    tags = job.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    body = {
        "snippet": {
            "title": final_title,
            "description": raw_desc,
            "tags": tags,
            "categoryId": "24",  # Entertainment
        },
        "status": {
            "privacyStatus": job.get("privacy_status", "public"),
            "selfDeclaredMadeForKids": False,
        }
    }

    media_upload = MediaIoBaseUpload(
        io.BytesIO(media_bytes),
        mimetype="video/mp4",
        resumable=True,
        chunksize=10 * 1024 * 1024
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media_upload
    )

    resp = None
    while resp is None:
        status, resp = request.next_chunk()
        if status:
            progress = int(status.progress() * 100)
            print(f"    [YOUTUBE PROGRESS] #{job_id}: {progress}%")

    video_id = resp.get("id")
    print(f"  [YOUTUBE OK] ✅ Published #{job_id} -> https://youtube.com/shorts/{video_id}")

    # 4. Attach custom Short thumbnail if available
    try:
        thumb_path = _find_thumbnail_for_job(job)
        if thumb_path and os.path.exists(thumb_path):
            print(f"  [YOUTUBE] Attaching custom thumbnail: {os.path.basename(thumb_path)}...")
            with open(thumb_path, "rb") as tf:
                thumb_bytes = tf.read()
            thumb_upload = MediaIoBaseUpload(
                io.BytesIO(thumb_bytes),
                mimetype="image/jpeg",
                resumable=False
            )
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=thumb_upload
            ).execute()
            print(f"  [YOUTUBE] ✅ Custom thumbnail successfully attached to {video_id}!")
        else:
            print(f"  [YOUTUBE] No companion thumbnail found for job #{job_id}, YouTube will use primary high-res frame.")
    except Exception as te:
        print(f"  [YOUTUBE NOTICE] Custom thumbnail could not be attached: {te}")
        print(f"  [YOUTUBE NOTICE] (Note: YouTube requires phone verification in Studio to allow custom thumbnails via API)")

    # 5. Mark posted & write publishing record
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE youtube_jobs
                SET status = 'posted', video_id = %s, posted_at = NOW(), error = NULL
                WHERE id = %s;
            """, (video_id, job_id))

            cur.execute("""
                INSERT INTO youtube_publishing_records (
                    channel_id, video_id, title, privacy_status, status, published_at
                ) VALUES (%s, %s, %s, %s, 'published', NOW());
            """, (cid, video_id, final_title, job.get("privacy_status", "public")))
            conn.commit()

    return video_id


def post_youtube_job_now(job_id: int) -> Dict[str, Any]:
    """Force post a specific YouTube job right now (bypasses schedule, checks quota)."""
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT j.*, c.channel_id as yt_channel_id, c.title as channel_title,
                       c.access_token_encrypted, c.refresh_token_encrypted, c.token_expires_at,
                       c.client_id, c.client_secret
                FROM youtube_jobs j
                JOIN youtube_channels c ON j.channel_id = c.id
                WHERE j.id = %s;
            """, (job_id,))
            job = cur.fetchone()

    if not job:
        raise ValueError(f"Job #{job_id} not found.")

    if job["status"] == "posted":
        return {
            "success": True,
            "already_posted": True,
            "video_id": job.get("video_id"),
            "url": f"https://youtube.com/shorts/{job.get('video_id')}"
        }

    # Check daily quota
    daily_count = get_daily_uploaded_count(job["channel_id"])
    if daily_count >= DAILY_QUOTA_UPLOAD_LIMIT:
        raise RuntimeError(
            f"Daily quota limit reached: {daily_count} uploads in the last 24h. "
            f"YouTube API allows max {DAILY_QUOTA_UPLOAD_LIMIT} uploads/day to avoid quota rejection."
        )

    # Claim job
    with db._conn() as conn:
        with conn.cursor() as cur:
            cur.execute("UPDATE youtube_jobs SET status = 'posting' WHERE id = %s;", (job_id,))
            conn.commit()

    try:
        vid = execute_youtube_job(job)
        return {
            "success": True,
            "video_id": vid,
            "url": f"https://youtube.com/shorts/{vid}",
            "job_id": job_id
        }
    except Exception as e:
        err_msg = str(e)
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE youtube_jobs SET status = 'failed', error = %s WHERE id = %s;", (err_msg, job_id))
                conn.commit()
        raise RuntimeError(err_msg)


def run_due_youtube_jobs(limit: int = 5) -> Tuple[int, int]:
    """Find and execute due YouTube upload jobs within quota limits. Returns (posted, failed)."""
    posted = 0
    failed = 0

    with db._conn() as conn:
        with conn.cursor() as cur:
            # Self-heal zombie jobs stuck in 'posting' over 30 mins
            cur.execute("""
                UPDATE youtube_jobs
                SET status = 'pending'
                WHERE status = 'posting'
                  AND scheduled_time_utc < NOW() - INTERVAL '30 minutes';
            """)
            conn.commit()

            cur.execute("""
                SELECT j.*, c.channel_id as yt_channel_id, c.title as channel_title,
                       c.access_token_encrypted, c.refresh_token_encrypted, c.token_expires_at,
                       c.client_id, c.client_secret
                FROM youtube_jobs j
                JOIN youtube_channels c ON j.channel_id = c.id
                WHERE j.status = 'pending'
                  AND j.scheduled_time_utc <= NOW()
                ORDER BY j.scheduled_time_utc ASC
                LIMIT %s;
            """, (limit,))
            due_jobs = cur.fetchall()

    if not due_jobs:
        return 0, 0

    print(f"  [YOUTUBE] Found {len(due_jobs)} due job(s). Processing...")

    for job in due_jobs:
        job_id = job["id"]
        cid = job["channel_id"]
        channel_title = job["channel_title"]

        # Check daily quota limit
        daily_count = get_daily_uploaded_count(cid)
        if daily_count >= DAILY_QUOTA_UPLOAD_LIMIT:
            print(f"  [YOUTUBE QUOTA GUARD] Channel '{channel_title}' reached daily limit of {DAILY_QUOTA_UPLOAD_LIMIT} uploads ({daily_count} in last 24h). Deferring job #{job_id}.")
            continue

        # Claim job atomically
        with db._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE youtube_jobs SET status = 'posting' WHERE id = %s AND status = 'pending' RETURNING id", (job_id,))
                claimed = cur.fetchone()
                conn.commit()

        if not claimed:
            continue

        try:
            print(f"  [YOUTUBE] Uploading #{job_id}: '{job['title']}' to {channel_title}...")
            execute_youtube_job(job)
            posted += 1
        except Exception as e:
            err_msg = str(e)
            print(f"  [YOUTUBE ERROR] Failed #{job_id}: {err_msg}")
            with db._conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE youtube_jobs
                        SET status = 'failed', error = %s, attempts = COALESCE(attempts, 0) + 1
                        WHERE id = %s;
                    """, (err_msg, job_id))
                    conn.commit()
            failed += 1

    return posted, failed
