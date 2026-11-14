"""
backend/core/adapters/__init__.py — Factory for platform adapters.
"""

from typing import Dict
from core.adapters.base import BasePlatformAdapter
from core.adapters.facebook import FacebookAdapter
from core.adapters.snapchat import SnapchatAdapter
from core.adapters.youtube import YouTubeAdapter
from core.adapters.rumble import RumbleAdapter
from core.adapters.dailymotion import DailymotionAdapter

_ADAPTERS: Dict[str, BasePlatformAdapter] = {
    "facebook": FacebookAdapter(),
    "snapchat": SnapchatAdapter(),
    "youtube": YouTubeAdapter(),
    "rumble": RumbleAdapter(),
    "dailymotion": DailymotionAdapter(),
}


def get_adapter(platform: str) -> BasePlatformAdapter:
    """Returns the adapter instance for the specified platform."""
    plat = platform.lower().strip()
    adapter = _ADAPTERS.get(plat)
    if not adapter:
        raise ValueError(f"No platform adapter registered for: '{platform}'")
    return adapter
