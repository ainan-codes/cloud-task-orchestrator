"""
Pydantic validation schemas for Reel Ledger API requests and responses.
Provides runtime validation, serialization, and auto-generated OpenAPI documentation.
"""

from datetime import datetime
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


class PageCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    drive_folder_id: str = Field(..., min_length=1)
    fb_page_id: str = Field(..., min_length=1)
    fb_page_access_token: str = Field(..., min_length=1)
    daily_post_count: int = Field(default=3, ge=1, le=100)


class PageUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    drive_folder_id: Optional[str] = None
    fb_page_id: Optional[str] = None
    fb_page_access_token: Optional[str] = None
    daily_post_count: Optional[int] = Field(None, ge=1, le=100)
    caption_template: Optional[str] = None
    first_comment_template: Optional[str] = None
    hashtag_pool: Optional[str] = None
    hashtag_count: Optional[int] = Field(None, ge=0, le=30)


class PageResponse(BaseModel):
    id: int
    name: str
    drive_folder_id: str
    fb_page_id: str
    daily_post_count: int
    token_valid: Optional[bool] = None
    token_last_checked_at: Optional[datetime] = None
    token_error: Optional[str] = None
    created_at: Optional[datetime] = None


class SnapchatAccountUpdate(BaseModel):
    drive_folder_id: Optional[str] = None
    target_placement: Optional[Literal["spotlight", "story"]] = None
    daily_post_count: Optional[int] = Field(None, ge=1, le=100)
    is_active: Optional[bool] = None


class SnapchatAccountResponse(BaseModel):
    id: int
    display_name: str
    snapchat_username: str
    profile_id: Optional[str] = None
    drive_folder_id: Optional[str] = None
    target_placement: str = "spotlight"
    is_active: bool = True
    token_valid: bool = True
    token_expires_at: Optional[str] = None
    created_at: Optional[str] = None


class SnapchatPublishRequest(BaseModel):
    content_item_id: int
    target_placement: Optional[Literal["spotlight", "story"]] = None
    caption: Optional[str] = None


class ScheduleRescueRequest(BaseModel):
    page_id: Optional[int] = None
    reflow: bool = True


class HealthCheckResponse(BaseModel):
    status: Literal["healthy", "degraded", "unhealthy"]
    timestamp: str
    database: bool
    version: str = "2.0.0"
