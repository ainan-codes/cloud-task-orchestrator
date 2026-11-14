"""
backend/core/adapters/base.py — Universal Platform Adapter Abstract Base Class.

Every platform (Facebook, Instagram, YouTube, Snapchat, TikTok, Rumble, Dailymotion)
implements this interface.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from core.capabilities import PlatformCapabilities, CapabilityRegistry


class BasePlatformAdapter(ABC):
    """Stateless platform adapter interface."""

    def __init__(self, platform_name: str):
        self.platform_name = platform_name

    def get_capabilities(self) -> PlatformCapabilities:
        caps = CapabilityRegistry.get(self.platform_name)
        if not caps:
            raise ValueError(f"No capabilities defined for platform: {self.platform_name}")
        return caps

    @abstractmethod
    def validate_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Validates whether stored credentials are functional.
        Returns: {"is_valid": bool, "status": str, "error": Optional[str]}
        """
        pass

    @abstractmethod
    def refresh_credentials(self, credentials_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Refreshes expired OAuth tokens if refresh token is available.
        Returns updated credentials payload.
        """
        pass

    @abstractmethod
    def publish_video(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        video_bytes: bytes,
        caption: str,
        title: Optional[str] = None,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Publishes vertical video reel/short.
        Returns: {"success": bool, "external_post_id": str, "url": Optional[str]}
        """
        pass

    @abstractmethod
    def publish_photo(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        photo_bytes: bytes,
        caption: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        """Publishes photo card or image post."""
        pass

    @abstractmethod
    def publish_text(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        text: str,
        hashtags: Optional[list] = None
    ) -> Dict[str, Any]:
        """Publishes text status update."""
        pass

    @abstractmethod
    def get_publication_status(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        """Verifies status of remote post."""
        pass

    @abstractmethod
    def get_analytics(
        self,
        external_account_id: str,
        credentials_payload: Dict[str, Any],
        external_post_id: str
    ) -> Dict[str, Any]:
        """Fetches views, likes, comments, shares, etc."""
        pass
