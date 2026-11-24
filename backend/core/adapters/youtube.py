"""
backend/core/adapters/youtube.py — YouTube Platform Adapter.

Interfaces with YouTube Data API v3 for YouTube Shorts uploads,
metadata updates, privacy status, and channel stats.
"""

import logging
import requests
from typing import Dict, Any, Optional

from core.adapters.base import BasePlatformAdapter

logger = logging.getLogger("adapter.youtube")


class YouTubeAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__("youtube")

    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        if not token:
            return {"is_valid": False, "status": "invalid", "error": "Missing access_token"}
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            data = r.json()
            if "items" in data and len(data["items"]) > 0:
                ch = data["items"][0]
                return {
                    "is_valid": True,
                    "status": "valid",
                    "channel_title": ch.get("snippet", {}).get("title")
                }
            if "error" in data:
                return {"is_valid": False, "status": "reauth_required", "error": data["error"].get("message")}
            return {"is_valid": False, "status": "invalid", "error": str(data)}
        except Exception as e:
            return {"is_valid": False, "status": "invalid", "error": str(e)}

    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        refresh_token = credentials_payload.get("refresh_token")
        client_id = credentials_payload.get("client_id")
        client_secret = credentials_payload.get("client_secret")
        if not (refresh_token and client_id and client_secret):
            return credentials_payload

        resp = requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token"
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
        video_title = title or "YouTube Short"
        if not video_title.endswith("#Shorts"):
            video_title = f"{video_title} #Shorts"

        # 1. Resumable upload session init
        metadata = {
            "snippet": {
                "title": video_title[:100],
                "description": f"{caption}\n\n{' '.join(hashtags or [])}".strip(),
                "tags": hashtags or ["shorts"],
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": credentials_payload.get("default_privacy", "public"),
                "selfDeclaredMadeForKids": False
            }
        }
        init_res = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(len(video_bytes)),
                "X-Upload-Content-Type": "video/mp4"
            },
            json=metadata,
            timeout=15
        )
        if init_res.status_code != 200:
            raise RuntimeError(f"YouTube upload init failed: {init_res.text}")

        upload_url = init_res.headers.get("Location")
        if not upload_url:
            raise RuntimeError("Missing resumable upload Location header from YouTube.")

        # 2. Upload video bytes
        upload_res = requests.put(
            upload_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "video/mp4",
                "Content-Length": str(len(video_bytes))
            },
            data=video_bytes,
            timeout=300
        )
        data = upload_res.json()
        video_id = data.get("id")
        if not video_id:
            raise RuntimeError(f"YouTube upload failed: {upload_res.text}")

        return {
            "success": True,
            "external_post_id": str(video_id),
            "url": f"https://www.youtube.com/shorts/{video_id}"
        }

    def publish_photo(self, external_account_id, credentials_payload, photo_bytes, caption, hashtags=None):
        raise NotImplementedError("YouTube does not support standalone photo posting via API.")

    def publish_text(self, external_account_id, credentials_payload, text, hashtags=None):
        raise NotImplementedError("YouTube Community posts are not currently supported via standard Data API.")

    def get_publication_status(self, external_account_id, credentials_payload, external_post_id):
        token = credentials_payload.get("access_token")
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "status", "id": external_post_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            data = r.json()
            if "items" in data and len(data["items"]) > 0:
                upload_status = data["items"][0].get("status", {}).get("uploadStatus")
                return {"exists": True, "status": "published" if upload_status == "processed" else "processing"}
            return {"exists": False, "status": "not_found"}
        except Exception as e:
            return {"exists": None, "status": "unknown", "error": str(e)}

    def get_analytics(self, external_account_id, credentials_payload, external_post_id):
        token = credentials_payload.get("access_token")
        try:
            r = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics", "id": external_post_id},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            data = r.json()
            if "items" in data and len(data["items"]) > 0:
                stats = data["items"][0].get("statistics", {})
                return {
                    "views": int(stats.get("viewCount", 0)),
                    "likes": int(stats.get("likeCount", 0)),
                    "comments": int(stats.get("commentCount", 0)),
                    "shares": 0
                }
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
        except Exception:
            return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
