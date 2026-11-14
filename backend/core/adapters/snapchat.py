"""
backend/core/adapters/snapchat.py — Snapchat Platform Adapter.

Interfaces with official Snap Kit & Snapchat Public Profile APIs
for Spotlight submissions and Public Stories.
"""

import logging
from typing import Dict, Any, Optional

import snapchat_poster
import snapchat_oauth
from snapchat_errors import SnapAuthError, SnapApiError
from core.adapters.base import BasePlatformAdapter

logger = logging.getLogger("adapter.snapchat")


class SnapchatAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__("snapchat")

    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        access_token = credentials_payload.get("access_token")
        if not access_token:
            return {"is_valid": False, "status": "invalid", "error": "Missing access_token"}
        try:
            # Check profile identity via Snap API
            profile = snapchat_oauth.fetch_me(access_token)
            return {"is_valid": True, "status": "valid", "profile": profile}
        except SnapAuthError as e:
            return {"is_valid": False, "status": "reauth_required", "error": str(e)}
        except Exception as e:
            return {"is_valid": False, "status": "invalid", "error": str(e)}

    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        refresh_token = credentials_payload.get("refresh_token")
        if not refresh_token:
            raise SnapAuthError("No refresh token available for Snapchat account")
        refreshed = snapchat_oauth.refresh_access_token(refresh_token)
        credentials_payload.update(refreshed)
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
        access_token = credentials_payload.get("access_token")
        profile_id = credentials_payload.get("public_profile_id") or external_account_id

        # Post to Spotlight
        result = snapchat_poster.post_spotlight(
            profile_id=profile_id,
            access_token=access_token,
            media_bytes=video_bytes,
            description=caption,
            locale="en_US"
        )
        post_id = result.get("post_id") or result.get("request_id")
        return {
            "success": True,
            "external_post_id": str(post_id),
            "url": f"https://www.snapchat.com/spotlight/{post_id}" if post_id else None
        }

    def publish_photo(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        photo_bytes: bytes,
        caption: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        access_token = credentials_payload.get("access_token")
        profile_id = credentials_payload.get("public_profile_id") or external_account_id

        # Post to Public Story
        result = snapchat_poster.post_story(
            profile_id=profile_id,
            access_token=access_token,
            media_bytes=photo_bytes,
            media_type="photo"
        )
        post_id = result.get("request_id") or result.get("post_id")
        return {
            "success": True,
            "external_post_id": str(post_id),
            "url": None
        }

    def publish_text(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        text: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        raise NotImplementedError("Snapchat does not support text-only status updates.")

    def get_publication_status(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        return {"exists": True, "status": "published"}

    def get_analytics(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        # Snap Kit Story/Spotlight stats require Creator Marketplace or Snap Analytics API
        return {"views": 0, "likes": 0, "shares": 0, "comments": 0}
