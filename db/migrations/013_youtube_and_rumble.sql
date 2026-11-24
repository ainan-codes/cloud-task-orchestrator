-- ============================================================
-- Reels Dashboard — Migration 013: YouTube & Rumble Platforms
--
-- Adds first-class support for YouTube Shorts and Rumble Video
-- automation, complete with accounts, scheduling queues, and
-- publishing history ledgers.
-- ============================================================

-- ---------- 1. YOUTUBE PLATFORM ----------
create table if not exists youtube_channels (
    id                      bigint generated always as identity primary key,
    channel_id              text not null unique,
    title                   text not null,
    custom_url              text,
    thumbnail_url           text,
    access_token_encrypted  text,
    refresh_token_encrypted text,
    token_expires_at        timestamptz,
    default_privacy         text default 'public',      -- 'public' | 'unlisted' | 'private'
    default_tags            text[] default array[]::text[],
    is_connected            boolean default true,
    created_at              timestamptz default now(),
    updated_at              timestamptz default now()
);

create table if not exists youtube_jobs (
    id                  bigint generated always as identity primary key,
    channel_id          bigint not null references youtube_channels(id) on delete cascade,
    content_item_id     bigint references content_items(id) on delete set null,
    file_name           text,
    title               text not null,
    description         text default '',
    tags                text[] default array[]::text[],
    privacy_status      text not null default 'public',
    is_short            boolean default true,
    scheduled_time_utc  timestamptz not null,
    status              text not null default 'pending', -- 'pending' | 'uploading' | 'posted' | 'failed' | 'cancelled'
    video_id            text,
    error               text,
    attempts            int default 0,
    created_at          timestamptz default now(),
    posted_at           timestamptz
);

create table if not exists youtube_publishing_records (
    id              bigint generated always as identity primary key,
    channel_id      bigint not null references youtube_channels(id) on delete cascade,
    video_id        text not null,
    title           text,
    privacy_status  text,
    status          text not null default 'published',
    error           text,
    published_at    timestamptz default now()
);

create index if not exists idx_youtube_jobs_sched
    on youtube_jobs (scheduled_time_utc)
    where status = 'pending';

-- ---------- 2. RUMBLE PLATFORM ----------
create table if not exists rumble_accounts (
    id                          bigint generated always as identity primary key,
    username                    text not null unique,
    channel_name                text,
    channel_id                  text,
    default_license             text not null default 'exclusive', -- 'exclusive' | 'rumble_only' | 'non_exclusive'
    default_visibility          text not null default 'public',    -- 'public' | 'unlisted'
    session_cookie_encrypted    text,
    is_connected                boolean default true,
    created_at                  timestamptz default now(),
    updated_at                  timestamptz default now()
);

create table if not exists rumble_jobs (
    id                  bigint generated always as identity primary key,
    account_id          bigint not null references rumble_accounts(id) on delete cascade,
    content_item_id     bigint references content_items(id) on delete set null,
    file_name           text,
    title               text not null,
    description         text default '',
    tags                text[] default array[]::text[],
    license_type        text not null default 'exclusive',
    visibility          text not null default 'public',
    scheduled_time_utc  timestamptz not null,
    status              text not null default 'pending', -- 'pending' | 'uploading' | 'posted' | 'failed' | 'cancelled'
    rumble_video_id     text,
    rumble_url          text,
    error               text,
    attempts            int default 0,
    created_at          timestamptz default now(),
    posted_at           timestamptz
);

create table if not exists rumble_publishing_records (
    id                  bigint generated always as identity primary key,
    account_id          bigint not null references rumble_accounts(id) on delete cascade,
    rumble_video_id     text,
    rumble_url          text,
    title               text,
    license_type        text,
    status              text not null default 'published',
    error               text,
    published_at        timestamptz default now()
);

create index if not exists idx_rumble_jobs_sched
    on rumble_jobs (scheduled_time_utc)
    where status = 'pending';
