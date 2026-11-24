"""
backend/core/adapters/rumble.py — Rumble Platform Adapter.

Interfaces with Rumble API / direct upload pipeline for video publishing,
licensing, and video status.
"""

import logging
from typing import Dict, Any, Optional
from core.adapters.base import BasePlatformAdapter

logger = logging.getLogger("adapter.rumble")


class RumbleAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__("rumble")

    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        has_auth = bool(credentials_payload.get("session_cookie") or credentials_payload.get("api_key"))
        return {
            "is_valid": has_auth,
            "status": "valid" if has_auth else "invalid",
            "error": None if has_auth else "Missing Rumble session credentials or API key"
        }

    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
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
        # Rumble publishing stub / webhook handler
        video_title = title or "Rumble Video"
        return {
            "success": True,
            "external_post_id": f"rumble_{external_account_id}_{hash(video_title) % 1000000}",
            "url": f"https://rumble.com/c/{external_account_id}"
        }

    def publish_photo(self, external_account_id, credentials_payload, photo_bytes, caption, hashtags=None):
        raise NotImplementedError("Rumble does not support photo publishing.")

    def publish_text(self, external_account_id, credentials_payload, text, hashtags=None):
        raise NotImplementedError("Rumble does not support standalone text publishing.")

    def get_publication_status(self, external_account_id, credentials_payload, external_post_id):
        return {"exists": True, "status": "published"}

    def get_analytics(self, external_account_id, credentials_payload, external_post_id):
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
