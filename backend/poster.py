"""
poster.py — the core posting engine (Phase 1).

Publishes Reels and Photos to a Facebook Page via the OFFICIAL Graph API,
and records every post in the per-page ledger so nothing double-posts to
the same page (but the same video CAN post to a different page).

This module is deliberately host-agnostic: it's called by the scheduler
(Phase 2) whether that runs on GitHub Actions, a Vercel function, etc.
"""

import datetime as dt
import os
import random
import requests

GRAPH = "https://graph.facebook.com/v21.0"

# Network timeouts are host-dependent, not API-dependent. On GitHub Actions a
# run has 30 minutes and a slow upload should simply be waited out; inside a
# Vercel function the whole invocation dies at 60s, and a request allowed to
# block for 600s guarantees the process is killed before it can record what it
# did. Defaults keep the generous long-running behaviour; the serverless host
# sets these down to fit its own ceiling.
UPLOAD_TIMEOUT = float(os.environ.get("FB_UPLOAD_TIMEOUT", 600))
API_TIMEOUT = float(os.environ.get("FB_API_TIMEOUT", 60))
FINISH_TIMEOUT = float(os.environ.get("FB_FINISH_TIMEOUT", 120))
PHOTO_TIMEOUT = float(os.environ.get("FB_PHOTO_TIMEOUT", 300))


# ----------------------------------------------------------------------
# caption building — caption is LOCKED to the content item, so a caption
# can never land on the wrong video.
# ----------------------------------------------------------------------
def build_caption(caption_text, hashtags, extra_hashtags=None, footer="", hashtag_pool=None):
    """hashtag_pool (optional): a page-level rotating pool (up to 50 tags).
    The video's own hashtags and the page's extra_hashtags are ALWAYS
    included first; if a pool is set, a random sample fills the remaining
    slots up to 8 total hashtags (no duplicates), so repeat posts don't all
    carry the identical tag set."""
    caption_text = (caption_text or "").strip()
    hashtags = hashtags or []
    extra_hashtags = extra_hashtags or []
    hashtag_pool = hashtag_pool or []

    existing = set(h.lower() for h in _find_tags(caption_text))
    tags = []

    def add(h):
        h = h if h.startswith("#") else "#" + h
        if h.lower() not in existing and h not in tags:
            tags.append(h)
            existing.add(h.lower())

    for h in list(hashtags) + list(extra_hashtags):
        add(h)

    if hashtag_pool:
        remaining = max(0, 8 - len(tags))
        if remaining:
            candidates = [h for h in hashtag_pool
                          if (h if h.startswith("#") else "#" + h).lower() not in existing]
            random.shuffle(candidates)
            for h in candidates[:remaining]:
                add(h)

    parts = [caption_text] if caption_text else []
    if tags:
        parts.append(" ".join(tags))
    if footer and footer.strip():
        parts.append(footer.strip())
    return "\n\n".join(parts).strip()


def _find_tags(text):
    import re
    return re.findall(r"#\w+", text or "")


# ----------------------------------------------------------------------
# error classification & token health cache
# ----------------------------------------------------------------------
_TOKEN_CACHE = {}  # key: (fb_page_id, token_suffix) -> (is_valid, detail, timestamp)
_TOKEN_CACHE_TTL = 300  # 5 minutes


def is_auth_error(err_obj_or_str):
    """Returns True if the error indicates invalid/expired token or inaccessible page."""
    s = str(err_obj_or_str).lower()
    return any(k in s for k in [
        "oauthexception",
        "access token",
        "code': 190",
        "code\": 190",
        "code': 102",
        "code\": 102",
        "not accessible",
        "could not be decrypted",
        "session has been invalidated",
        "session has expired",
        "permissions error",
        "unsupported post request",
    ])


def _safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"error": {"message": f"Non-JSON response (HTTP {resp.status_code}): {resp.text[:200]}"}}


def resolve_page_token(page_id, token):
    """If the provided token is a User or System User token, attempt to resolve
    the Page-scoped access token via /{page_id}?fields=access_token."""
    if not token or not page_id:
        return token
    try:
        r = requests.get(
            f"{GRAPH}/{page_id}",
            params={"fields": "access_token", "access_token": token},
            timeout=10,
        )
        res = r.json()
        if "access_token" in res and res["access_token"]:
            return res["access_token"]
    except Exception:
        pass
    return token


# ----------------------------------------------------------------------
# Reels: official 3-step (start -> upload -> finish)
# `video_bytes` is the raw file (downloaded from Google Drive by the caller)
# ----------------------------------------------------------------------
def publish_reel(page_id, page_token, video_bytes, caption, scheduled_ts=None):
    page_token = resolve_page_token(page_id, page_token)
    if not page_token:
        raise RuntimeError(f"Missing Facebook Page Access Token for Page ID {page_id}. Please ensure your page has a valid token.")
    resp = requests.post(
        f"{GRAPH}/{page_id}/video_reels",
        data={"upload_phase": "start", "access_token": page_token},
        timeout=API_TIMEOUT,
    )
    start = _safe_json(resp)
    if "video_id" not in start:
        raise RuntimeError(f"Reel START failed: {start}")
    video_id = start["video_id"]
    upload_url = start.get("upload_url") or f"https://rupload.facebook.com/video-upload/v21.0/{video_id}"

    resp_up = requests.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {page_token}",
            "offset": "0",
            "file_size": str(len(video_bytes)),
            "Content-Type": "application/octet-stream",
        },
        data=video_bytes,
        timeout=UPLOAD_TIMEOUT,
    )
    up = _safe_json(resp_up)
    if up.get("success") is False or "error" in up:
        raise RuntimeError(f"Reel UPLOAD failed: {up}")

    finish_data = {
        "upload_phase": "finish",
        "video_id": video_id,
        "description": caption,
        "access_token": page_token,
    }
    if scheduled_ts:
        finish_data["video_state"] = "SCHEDULED"
        finish_data["scheduled_publish_time"] = str(int(scheduled_ts))
    else:
        finish_data["video_state"] = "PUBLISHED"

    resp_finish = requests.post(f"{GRAPH}/{page_id}/video_reels", data=finish_data, timeout=FINISH_TIMEOUT)
    finish = _safe_json(resp_finish)
    if not finish.get("success", False):
        raise RuntimeError(f"Reel FINISH failed: {finish}")
    return video_id


# ----------------------------------------------------------------------
# Photos & Feed Text
# ----------------------------------------------------------------------
def publish_photo(page_id, page_token, photo_bytes_or_url, caption, scheduled_ts=None):
    """Publishes a photo either from raw image bytes or from a public image URL."""
    page_token = resolve_page_token(page_id, page_token)
    data = {"caption": caption, "access_token": page_token}
    if scheduled_ts:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(scheduled_ts))

    if isinstance(photo_bytes_or_url, str) and (photo_bytes_or_url.startswith("http://") or photo_bytes_or_url.startswith("https://")):
        data["url"] = photo_bytes_or_url
        resp = requests.post(f"{GRAPH}/{page_id}/photos", data=data, timeout=PHOTO_TIMEOUT)
    else:
        resp = requests.post(
            f"{GRAPH}/{page_id}/photos",
            data=data,
            files={"source": ("photo.jpg", photo_bytes_or_url)},
            timeout=PHOTO_TIMEOUT,
        )
    res = _safe_json(resp)
    if "id" not in res and "post_id" not in res:
        raise RuntimeError(f"Photo publish failed: {res}")
    return res.get("id") or res.get("post_id")


def publish_feed_text(page_id, page_token, message, scheduled_ts=None):
    """Publishes a text / status update to the page feed."""
    page_token = resolve_page_token(page_id, page_token)
    data = {"message": message, "access_token": page_token}
    if scheduled_ts:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(int(scheduled_ts))
    resp = requests.post(f"{GRAPH}/{page_id}/feed", data=data, timeout=API_TIMEOUT)
    res = _safe_json(resp)
    if "id" not in res:
        raise RuntimeError(f"Text post failed: {res}")
    return res["id"]


# ----------------------------------------------------------------------
# unified entrypoint used by the scheduler
# ----------------------------------------------------------------------
def publish(media_type, page_id, page_token, media_bytes, caption, scheduled_ts=None):
    if media_type == "text":
        return publish_feed_text(page_id, page_token, caption, scheduled_ts)
    if media_type == "photo":
        return publish_photo(page_id, page_token, media_bytes, caption, scheduled_ts)
    return publish_reel(page_id, page_token, media_bytes, caption, scheduled_ts)



# ----------------------------------------------------------------------
# health: is this page's token still usable?
# ----------------------------------------------------------------------
def check_token(fb_page_id, page_token, use_cache=True):
    """Live check against Facebook. Returns (True, page_name) if the token
    still works, or (False, error_message) if it doesn't.
    Caches results for 5 minutes so repeated checks don't hammer Facebook."""
    if not fb_page_id or not page_token:
        return False, "Missing Facebook Page ID or Access Token"
    page_token = resolve_page_token(fb_page_id, page_token)

    cache_key = (str(fb_page_id), str(page_token)[-16:])
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    if use_cache and cache_key in _TOKEN_CACHE:
        is_val, det, ts = _TOKEN_CACHE[cache_key]
        if (now - ts) < _TOKEN_CACHE_TTL:
            return is_val, det

    try:
        r = requests.get(
            f"{GRAPH}/{fb_page_id}",
            params={"fields": "id,name", "access_token": page_token},
            timeout=10,
        )
        res = _safe_json(r)
        if "name" in res:
            res_tuple = (True, res["name"])
        else:
            err = res.get("error", {}) or {}
            msg = err.get("message") or str(res)
            res_tuple = (False, msg)
        _TOKEN_CACHE[cache_key] = (res_tuple[0], res_tuple[1], now)
        return res_tuple
    except Exception as e:
        # Don't cache network timeouts for 5 minutes
        return False, str(e)


def inspect_token(fb_page_id, page_token):
    """Full diagnostic check for a page token: returns a structured dict
    containing validity, page name, error codes, auth status, and debug info."""
    clean_fb_id = str(fb_page_id).strip()
    clean_tok = str(page_token).strip()
    if not clean_tok:
        return {"valid": False, "error": "Token string is empty", "is_auth_error": True}

    try:
        r = requests.get(
            f"{GRAPH}/{clean_fb_id}",
            params={"fields": "id,name,category,link", "access_token": clean_tok},
            timeout=10,
        )
        res = _safe_json(r)
        if "name" in res:
            return {
                "valid": True,
                "page_name": res.get("name"),
                "page_id": res.get("id"),
                "category": res.get("category"),
                "link": res.get("link"),
            }
        err = res.get("error", {}) or {}
        msg = err.get("message") or str(res)
        code = err.get("code")
        subcode = err.get("error_subcode")
        return {
            "valid": False,
            "error": msg,
            "code": code,
            "subcode": subcode,
            "is_auth_error": is_auth_error(msg) or code in (190, 102),
        }
    except Exception as e:
        return {"valid": False, "error": str(e), "is_auth_error": False}


# ----------------------------------------------------------------------
# analytics: pull basic insights for a posted item
# ----------------------------------------------------------------------
def fetch_reel_insights(fb_creation_id, page_token):
    """Best-effort basic metrics. Field availability varies by API version;
    caller should tolerate missing keys."""
    try:
        r = requests.get(
            f"{GRAPH}/{fb_creation_id}",
            params={
                "fields": "views,likes.summary(true),comments.summary(true),shares",
                "access_token": page_token,
            },
            timeout=60,
        ).json()
        return {
            "views": r.get("views", 0) or 0,
            "likes": (r.get("likes", {}) or {}).get("summary", {}).get("total_count", 0),
            "comments": (r.get("comments", {}) or {}).get("summary", {}).get("total_count", 0),
            "shares": (r.get("shares", {}) or {}).get("count", 0),
            "raw": r,
        }
    except Exception as e:
        return {"error": str(e)}
