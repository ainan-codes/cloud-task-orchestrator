"""
backend/core/adapters/dailymotion.py — Dailymotion Platform Adapter.

Interfaces with Dailymotion Graph API for Partner channels, video uploads,
monetization settings (pre-roll / mid-roll), and view/earnings analytics.
"""

import logging
import requests
from typing import Dict, Any, Optional
from core.adapters.base import BasePlatformAdapter

logger = logging.getLogger("adapter.dailymotion")


class DailymotionAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__("dailymotion")

    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        if not token:
            return {"is_valid": False, "status": "invalid", "error": "Missing Dailymotion access token"}
        try:
            r = requests.get("https://api.dailymotion.com/me", headers={"Authorization": f"Bearer {token}"}, timeout=10)
            data = r.json()
            if "id" in data:
                return {
                    "is_valid": True,
                    "status": "valid",
                    "channel_id": data.get("id"),
                    "screenname": data.get("screenname"),
                    "avatar_url": data.get("avatar_360_url") or data.get("avatar_url")
                }
            return {"is_valid": False, "status": "reauth_required", "error": data.get("error", {}).get("message")}
        except Exception as e:
            return {"is_valid": False, "status": "invalid", "error": str(e)}

    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        refresh_token = credentials_payload.get("refresh_token")
        api_key = credentials_payload.get("api_key")
        api_secret = credentials_payload.get("api_secret")
        if not (refresh_token and api_key and api_secret):
            return credentials_payload

        resp = requests.post(
            "https://api.dailymotion.com/oauth/token",
            data={
                "grant_type": "refresh_token",
                "client_id": api_key,
                "client_secret": api_secret,
                "refresh_token": refresh_token
            },
            timeout=10
        )
        data = resp.json()
        if "access_token" in data:
            credentials_payload["access_token"] = data["access_token"]
        return credentials_payload

    def publish_video(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        video_bytes: bytes,
        caption: str,
        title: Optional[str] = None,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        # 1. Get upload URL
        url_res = requests.get("https://api.dailymotion.com/file/upload", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        upload_url = url_res.json().get("upload_url")
        if not upload_url:
            raise RuntimeError(f"Dailymotion upload url failed: {url_res.text}")

        # 2. Upload video bytes
        files = {"file": ("video.mp4", video_bytes, "video/mp4")}
        up_res = requests.post(upload_url, files=files, timeout=300)
        file_url = up_res.json().get("url")
        if not file_url:
            raise RuntimeError(f"Dailymotion file upload failed: {up_res.text}")

        # 3. Create video object
        create_res = requests.post(
            "https://api.dailymotion.com/me/videos",
            headers={"Authorization": f"Bearer {token}"},
            data={
                "url": file_url,
                "title": (title or "Dailymotion Reel")[:255],
                "description": f"{caption}\n\n{' '.join(hashtags or [])}".strip(),
                "published": "true",
                "channel": "news",
                "is_created_for_kids": "false"
            },
            timeout=15
        )
        vid_data = create_res.json()
        video_id = vid_data.get("id")
        if not video_id:
            raise RuntimeError(f"Dailymotion create video failed: {create_res.text}")

        return {
            "success": True,
            "external_post_id": str(video_id),
            "url": f"https://www.dailymotion.com/video/{video_id}"
        }

    def publish_photo(self, external_account_id, credentials_payload, photo_bytes, caption, hashtags=None):
        raise NotImplementedError("NOT SUPPORTED BY PLATFORM API: Dailymotion does not support photo publishing.")

    def publish_text(self, external_account_id, credentials_payload, text, hashtags=None):
        raise NotImplementedError("NOT SUPPORTED BY PLATFORM API: Dailymotion does not support text publishing.")

    def get_publication_status(self, external_account_id, credentials_payload, external_post_id):
        token = credentials_payload.get("access_token")
        try:
            r = requests.get(
                f"https://api.dailymotion.com/video/{external_post_id}",
                params={"fields": "id,status"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            data = r.json()
            if "id" in data:
                return {"exists": True, "status": "published" if data.get("status") == "ready" else "processing"}
            return {"exists": False, "status": "not_found"}
        except Exception as e:
            return {"exists": None, "status": "unknown", "error": str(e)}

    def get_analytics(self, external_account_id, credentials_payload, external_post_id):
        token = credentials_payload.get("access_token")
        try:
            r = requests.get(
                f"https://api.dailymotion.com/video/{external_post_id}",
                params={"fields": "views_total,likes_total,comments_total"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            data = r.json()
            return {
                "views": int(data.get("views_total", 0)),
                "likes": int(data.get("likes_total", 0)),
                "comments": int(data.get("comments_total", 0)),
                "shares": 0
            }
        except Exception:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
