-- ============================================================
-- Reels Dashboard — Migration 008: Snapchat Integration
-- Adds snapchat_accounts, multi-platform queue, and stats view
-- ============================================================

-- 1. Create snapchat_accounts table
create table if not exists snapchat_accounts (
    id                  bigint generated always as identity primary key,
    name                text not null,                 -- friendly name, e.g. "Whale Life Snapchat"
    snapchat_username   text not null,                 -- e.g. "whalelife_official"
    account_type        text default 'public_profile', -- 'public_profile' | 'creator'
    public_profile_id   text,                          -- Snap Public Profile ID
    auth_type           text default 'session',        -- 'session' (cookies/headers) | 'oauth'
    session_data        jsonb default '{}'::jsonb,     -- secure session cookies or OAuth tokens
    drive_folder_id     text,                          -- Google Drive folder holding content for this account
    default_placement   text default 'spotlight',      -- 'spotlight' | 'story' | 'both'
    utc_offset_hours    int default 0,
    schedule_mode       text default 'spread',         -- 'spread' | 'fixed'
    spread_start_hour   int default 9,
    spread_end_hour     int default 21,
    fixed_times         text[] default array['09:00','13:00','18:00'],
    per_run             int default 4,
    extra_hashtags      text[] default array[]::text[],
    caption_footer      text default '',
    is_active           boolean default true,
    token_status        text default 'valid',          -- 'valid' | 'invalid' | 'expired'
    token_error         text,
    token_checked_at    timestamptz default now(),
    created_at          timestamptz default now(),
    unique (snapchat_username)
);

-- 2. Extend content_items for Snapchat and multi-platform
alter table content_items alter column page_id drop not null;
alter table content_items add column if not exists snapchat_account_id bigint references snapchat_accounts(id) on delete cascade;
alter table content_items add column if not exists platform text default 'facebook';

create index if not exists idx_content_snapchat
    on content_items (snapchat_account_id)
    where snapchat_account_id is not null;

-- 3. Extend scheduled_posts for Snapchat and multi-platform
alter table scheduled_posts alter column page_id drop not null;
alter table scheduled_posts add column if not exists platform text not null default 'facebook';
alter table scheduled_posts add column if not exists snapchat_account_id bigint references snapchat_accounts(id) on delete cascade;
alter table scheduled_posts add column if not exists target_placement text default 'reel'; -- 'reel' | 'photo' | 'spotlight' | 'story'
alter table scheduled_posts add column if not exists snap_media_id text;

create index if not exists idx_sched_platform_status
    on scheduled_posts (platform, status, scheduled_time_utc);

create index if not exists idx_sched_snapchat_status
    on scheduled_posts (snapchat_account_id, status)
    where snapchat_account_id is not null;

-- 4. Extend posted_ledger for Snapchat dedup guarantee
alter table posted_ledger alter column page_id drop not null;
alter table posted_ledger add column if not exists platform text not null default 'facebook';
alter table posted_ledger add column if not exists snapchat_account_id bigint references snapchat_accounts(id) on delete cascade;
alter table posted_ledger add column if not exists target_placement text default 'reel';
alter table posted_ledger add column if not exists snap_media_id text;

create index if not exists idx_posted_ledger_snapchat
    on posted_ledger (snapchat_account_id, source_video_id, target_placement)
    where snapchat_account_id is not null;

-- 5. Create snapchat_account_stats view
drop view if exists snapchat_account_stats cascade;

create view snapchat_account_stats as
select
    s.id                                                                               as account_id,
    s.name,
    s.snapchat_username,
    s.account_type,
    s.default_placement,
    s.is_active,
    s.token_status,
    s.token_error,
    s.token_checked_at,
    (select count(*) from content_items c where c.snapchat_account_id = s.id)           as total_content,
    (select count(*) from posted_ledger l where l.snapchat_account_id = s.id)           as total_posted,
    (select count(*) from scheduled_posts sp where sp.snapchat_account_id = s.id and sp.status = 'pending') as pending_scheduled,
    (select count(*) from scheduled_posts sp where sp.snapchat_account_id = s.id and sp.status in ('failed', 'abandoned')) as failed_count
from snapchat_accounts s;
