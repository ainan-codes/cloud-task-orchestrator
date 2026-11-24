-- ============================================================
-- Reels Dashboard — Migration 009: Official Snapchat OAuth 2.0
-- Adds encrypted token storage, OAuth states, and publishing records
-- ============================================================

-- 1. Upgrade snapchat_accounts for official OAuth 2.0
alter table snapchat_accounts add column if not exists access_token_encrypted text;
alter table snapchat_accounts add column if not exists refresh_token_encrypted text;
alter table snapchat_accounts add column if not exists token_expires_at timestamptz;
alter table snapchat_accounts add column if not exists scope text default 'snapchat-profile-api';
alter table snapchat_accounts add column if not exists snapchat_user_id text;
alter table snapchat_accounts add column if not exists profile_name text;
alter table snapchat_accounts add column if not exists profile_picture_url text;

-- 2. Create snapchat_oauth_states for CSRF protection
create table if not exists snapchat_oauth_states (
    id           bigint generated always as identity primary key,
    state        text unique not null,
    redirect_uri text not null,
    created_at   timestamptz default now(),
    expires_at   timestamptz not null
);

create index if not exists idx_snap_oauth_states_lookup
    on snapchat_oauth_states (state);

-- 3. Create snapchat_publishing_records for official tracking
create table if not exists snapchat_publishing_records (
    id                  bigint generated always as identity primary key,
    scheduled_post_id   bigint references scheduled_posts(id) on delete set null,
    snapchat_account_id bigint not null references snapchat_accounts(id) on delete cascade,
    target_placement    text not null default 'spotlight', -- 'spotlight' | 'story'
    media_id            text,
    publish_id          text,
    status              text not null default 'pending',   -- 'pending' | 'uploading' | 'processing' | 'published' | 'failed'
    caption             text,
    topics              text[],
    error_message       text,
    attempts            int default 0,
    created_at          timestamptz default now(),
    published_at        timestamptz,
    updated_at          timestamptz default now()
);

create index if not exists idx_snap_pub_account_status
    on snapchat_publishing_records (snapchat_account_id, status);

create index if not exists idx_snap_pub_publish_id
    on snapchat_publishing_records (publish_id)
    where publish_id is not null;

-- 4. Recreate snapchat_account_stats view with official tracking
drop view if exists snapchat_account_stats cascade;

create view snapchat_account_stats as
select
    s.id                                                                               as account_id,
    s.name,
    s.snapchat_username,
    s.profile_name,
    s.profile_picture_url,
    s.account_type,
    s.default_placement,
    s.is_active,
    s.token_status,
    s.token_error,
    s.token_expires_at,
    s.token_checked_at,
    (s.access_token_encrypted is not null)                                             as is_connected,
    (select count(*) from content_items c where c.snapchat_account_id = s.id)          as total_content,
    (select count(*) from posted_ledger l where l.snapchat_account_id = s.id)          as total_posted,
    (select count(*) from scheduled_posts sp where sp.snapchat_account_id = s.id and sp.status = 'pending') as pending_scheduled,
    (select count(*) from scheduled_posts sp where sp.snapchat_account_id = s.id and sp.status in ('failed', 'abandoned')) as failed_count,
    (select count(*) from snapchat_publishing_records pr where pr.snapchat_account_id = s.id and pr.status = 'published') as published_spotlight_count
from snapchat_accounts s;
