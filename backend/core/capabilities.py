"""
backend/core/capabilities.py — Platform Capability Registry.

Declares actual capabilities and operational constraints for each platform.
Never assumes that a platform supports all media types or operations.
Fails unsupported requests gracefully BEFORE creating jobs.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PlatformCapabilities:
    platform: str
    supports_video: bool = True
    supports_photo: bool = False
    supports_text: bool = False
    supports_direct_publish: bool = True
    supports_native_scheduling: bool = False
    supports_analytics: bool = True
    supports_monetization: bool = False
    max_video_duration_sec: int = 60
    min_video_duration_sec: int = 3
    supported_aspect_ratios: List[str] = field(default_factory=lambda: ["9:16"])
    max_text_characters: int = 2200
    rate_limit_requests_per_minute: int = 60


# ============================================================
# Platform Capability Matrix
# ============================================================

PLATFORM_REGISTRY: Dict[str, PlatformCapabilities] = {
    "facebook": PlatformCapabilities(
        platform="facebook",
        supports_video=True,
        supports_photo=True,
        supports_text=True,
        supports_direct_publish=True,
        supports_native_scheduling=True,
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=90,
        min_video_duration_sec=3,
        supported_aspect_ratios=["9:16", "1:1", "4:5", "16:9"],
        max_text_characters=63206,
        rate_limit_requests_per_minute=100
    ),
    "instagram": PlatformCapabilities(
        platform="instagram",
        supports_video=True,
        supports_photo=True,
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=True,
        supports_analytics=True,
        supports_monetization=False,
        max_video_duration_sec=90,
        min_video_duration_sec=3,
        supported_aspect_ratios=["9:16", "1:1", "4:5"],
        max_text_characters=2200,
        rate_limit_requests_per_minute=50
    ),
    "youtube": PlatformCapabilities(
        platform="youtube",
        supports_video=True,
        supports_photo=False,
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=True,
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=60,
        min_video_duration_sec=3,
        supported_aspect_ratios=["9:16", "1:1"],
        max_text_characters=5000,
        rate_limit_requests_per_minute=30
    ),
    "snapchat": PlatformCapabilities(
        platform="snapchat",
        supports_video=True,
        supports_photo=True,    # Public Story only
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=False,  # Reel Ledger queue manages timing
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=60,
        min_video_duration_sec=5,
        supported_aspect_ratios=["9:16"],
        max_text_characters=250,
        rate_limit_requests_per_minute=40
    ),
    "tiktok": PlatformCapabilities(
        platform="tiktok",
        supports_video=True,
        supports_photo=True,    # Photo mode supported in official API
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=False,
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=600,
        min_video_duration_sec=3,
        supported_aspect_ratios=["9:16"],
        max_text_characters=2200,
        rate_limit_requests_per_minute=20
    ),
    "rumble": PlatformCapabilities(
        platform="rumble",
        supports_video=True,
        supports_photo=False,
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=True,
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=7200,
        min_video_duration_sec=5,
        supported_aspect_ratios=["9:16", "16:9"],
        max_text_characters=5000,
        rate_limit_requests_per_minute=30
    ),
    "dailymotion": PlatformCapabilities(
        platform="dailymotion",
        supports_video=True,
        supports_photo=False,
        supports_text=False,
        supports_direct_publish=True,
        supports_native_scheduling=True,
        supports_analytics=True,
        supports_monetization=True,
        max_video_duration_sec=7200,
        min_video_duration_sec=5,
        supported_aspect_ratios=["9:16", "16:9"],
        max_text_characters=3000,
        rate_limit_requests_per_minute=40
    ),
}


class CapabilityRegistry:
    """Provides querying and validation against the platform capability matrix."""

    @staticmethod
    def get(platform: str) -> Optional[PlatformCapabilities]:
        return PLATFORM_REGISTRY.get(platform.lower())

    @staticmethod
    def validate_request(
        platform: str,
        content_type: str,
        duration_seconds: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validates whether platform supports the given content type and specifications.
        Returns: (is_supported: bool, error_message: Optional[str])
        """
        caps = PLATFORM_REGISTRY.get(platform.lower())
        if not caps:
            return False, f"Unknown platform: '{platform}'"

        ctype = "video" if content_type in ("reel", "video") else content_type

        if ctype not in ("video", "photo", "text"):
            return False, f"Platform '{platform}' does not support content type '{content_type}'."

        if ctype == "video" and not caps.supports_video:
            return False, f"NOT SUPPORTED BY PLATFORM API: Platform '{platform}' does not support video publishing."
        if ctype == "photo" and not caps.supports_photo:
            return False, f"NOT SUPPORTED BY PLATFORM API: Platform '{platform}' does not support photo publishing."
        if ctype == "text" and not caps.supports_text:
            return False, f"NOT SUPPORTED BY PLATFORM API: Platform '{platform}' does not support text/status publishing."

        if ctype == "video" and duration_seconds is not None:
            if duration_seconds > caps.max_video_duration_sec:
                return False, f"Video duration ({duration_seconds:.1f}s) exceeds {platform}'s limit of {caps.max_video_duration_sec}s."
            if duration_seconds < caps.min_video_duration_sec:
                return False, f"Video duration ({duration_seconds:.1f}s) is below {platform}'s minimum of {caps.min_video_duration_sec}s."

        return True, None
