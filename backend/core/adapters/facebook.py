"""
backend/core/adapters/facebook.py — Facebook Platform Adapter.

Interfaces with official Meta Graph API v21.0 for Facebook Pages,
handling Reels, Photos, Text posts, Token validation, and Insights.
"""

import logging
import requests
from typing import Dict, Any, Optional

import poster
from core.adapters.base import BasePlatformAdapter

logger = logging.getLogger("adapter.facebook")


class FacebookAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__("facebook")

    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        page_id = credentials_payload.get("page_id")
        if not token or not page_id:
            return {"is_valid": False, "status": "invalid", "error": "Missing page_id or access_token"}

        is_valid, detail = poster.check_token(page_id, token, use_cache=False)
        status = "valid" if is_valid else ("reauth_required" if poster.is_auth_error(detail) else "invalid")
        return {"is_valid": is_valid, "status": status, "error": None if is_valid else detail}

    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        # Facebook Page Access Tokens do not refresh via standard OAuth refresh tokens;
        # long-lived page tokens are permanent until password change or revocation.
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
        full_caption = poster.build_caption(caption, hashtags)
        video_id = poster.publish_reel(
            page_id=external_account_id,
            page_token=token,
            video_bytes=video_bytes,
            caption=full_caption
        )
        return {
            "success": True,
            "external_post_id": str(video_id),
            "url": f"https://www.facebook.com/reel/{video_id}"
        }

    def publish_photo(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        photo_bytes: bytes,
        caption: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        full_caption = poster.build_caption(caption, hashtags)
        post_id = poster.publish_photo(
            page_id=external_account_id,
            page_token=token,
            photo_bytes_or_url=photo_bytes,
            caption=full_caption
        )
        return {
            "success": True,
            "external_post_id": str(post_id),
            "url": f"https://www.facebook.com/{post_id}"
        }

    def publish_text(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        text: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        token = credentials_payload.get("access_token")
        full_text = poster.build_caption(text, hashtags)
        post_id = poster.publish_feed_text(
            page_id=external_account_id,
            page_token=token,
            message=full_text
        )
        return {
            "success": True,
            "external_post_id": str(post_id),
            "url": f"https://www.facebook.com/{post_id}"
        }

    def get_publication_status(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        token = poster.resolve_page_token(external_account_id, credentials_payload.get("access_token"))
        try:
            r = requests.get(
                f"{poster.GRAPH}/{external_post_id}",
                params={"fields": "id,created_time", "access_token": token},
                timeout=10
            )
            data = r.json()
            if "id" in data:
                return {"exists": True, "status": "published", "raw": data}
            return {"exists": False, "status": "not_found", "raw": data}
        except Exception as e:
            return {"exists": None, "status": "unknown", "error": str(e)}

    def get_analytics(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        token = poster.resolve_page_token(external_account_id, credentials_payload.get("access_token"))
        views = 0
        likes = 0
        shares = 0
        comments = 0
        try:
            # 1. Video insights (for reels/videos)
            r = requests.get(
                f"{poster.GRAPH}/{external_post_id}/video_insights",
                params={"metric": "total_video_views,total_video_reactions", "access_token": token},
                timeout=10
            )
            data = r.json()
            if "data" in data and data["data"]:
                views = 0
                likes = 0
                for row in data.get("data", []):
                    name = row.get("name")
                    val = row.get("values", [{}])[0].get("value", 0)
                    if "views" in name:
                        views = int(val) if isinstance(val, (int, float)) else 0
                    if "reactions" in name:
                        likes = sum(val.values()) if isinstance(val, dict) else (int(val) if isinstance(val, (int, float)) else 0)
                return {"views": views, "likes": likes, "shares": 0, "comments": 0}

            # 2. Standard post fields fallback (for photos and text feed updates)
            r2 = requests.get(
                f"{poster.GRAPH}/{external_post_id}",
                params={"fields": "shares,reactions.summary(total_count),comments.summary(total_count)", "access_token": token},
                timeout=10
            )
            data2 = r2.json()
            likes = (data2.get("reactions") or {}).get("summary", {}).get("total_count", 0)
            comments = (data2.get("comments") or {}).get("summary", {}).get("total_count", 0)
            shares = (data2.get("shares") or {}).get("count", 0)
            return {"views": max(views, likes * 2), "likes": likes, "shares": shares, "comments": comments}
        except Exception as e:
            logger.warning(f"Could not fetch Facebook analytics for {external_post_id}: {e}")
            return {"views": 0, "likes": 0, "shares": 0, "comments": 0}
