-- ============================================================
-- Reels Dashboard / Reel Ledger — Migration 017
-- Autopilot Content Operating System & Balanced Interleaving Engine
--
-- Adds:
--  1. automation_profile JSONB column to pages & dailymotion_channels
--  2. Content rotation tracking & collision indexes
--  3. Content Library source status tracking ('ready', 'unavailable', 'processing')
-- ============================================================

ALTER TABLE pages 
ADD COLUMN IF NOT EXISTS automation_profile JSONB DEFAULT '{
    "automation_enabled": true,
    "video_enabled": true,
    "photo_enabled": true,
    "text_enabled": true,
    "daily_video_limit": 4,
    "daily_photo_limit": 4,
    "daily_text_limit": 4,
    "content_source": "both",
    "ai_captioning": true,
    "hashtag_rotation": true,
    "duplicate_protection": true,
    "smart_scheduling": true,
    "auto_retry": true,
    "analytics_sync": true,
    "monetization_tracking": false,
    "min_spacing_minutes": 30
}'::jsonb;

ALTER TABLE dailymotion_channels 
ADD COLUMN IF NOT EXISTS automation_profile JSONB DEFAULT '{
    "automation_enabled": true,
    "video_enabled": true,
    "photo_enabled": false,
    "text_enabled": false,
    "daily_video_limit": 3,
    "content_source": "both",
    "ai_captioning": true,
    "monetization_enabled": true,
    "min_spacing_minutes": 60
}'::jsonb;

CREATE INDEX IF NOT EXISTS idx_content_items_validation ON content_items(validation_status);
CREATE INDEX IF NOT EXISTS idx_content_items_media_type ON content_items(page_id, media_type, validation_status);
