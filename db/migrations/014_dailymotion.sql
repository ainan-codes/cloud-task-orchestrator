-- ============================================================
-- Reels Dashboard — Migration 014: Dailymotion Video Monetization
--
-- Adds full support for Dailymotion Partner channels, monetization
-- ad formats (pre-roll, mid-roll), video queue, and published ledger.
-- Supports OAuth, Session Cookies, and API Key / Secret auth.
-- ============================================================

create table if not exists dailymotion_channels (
    id                          bigint generated always as identity primary key,
    channel_id                  text not null unique,
    channel_name                text not null,
    screen_name                 text,
    avatar_url                  text,
    auth_method                 text not null default 'oauth', -- 'oauth' | 'cookie' | 'api_key'
    access_token_encrypted      text,
    refresh_token_encrypted     text,
    session_cookies_encrypted   text,
    api_key                     text,
    api_secret_encrypted        text,
    token_expires_at            timestamptz,
    monetization_enabled        boolean default true,
    default_privacy             text default 'public',          -- 'public' | 'private' | 'password'
    default_tags                text[] default array[]::text[],
    is_connected                boolean default true,
    total_views                 bigint default 0,
    estimated_earnings_usd      numeric(10, 2) default 0.00,
    created_at                  timestamptz default now(),
    updated_at                  timestamptz default now()
);

create table if not exists dailymotion_jobs (
    id                  bigint generated always as identity primary key,
    channel_id          bigint not null references dailymotion_channels(id) on delete cascade,
    content_item_id     bigint references content_items(id) on delete set null,
    file_name           text,
    title               text not null,
    description         text default '',
    tags                text[] default array[]::text[],
    category            text default 'news',            -- 'news' | 'fun' | 'tech' | 'lifestyle' | 'auto' | 'music'
    monetization        boolean default true,
    privacy_status      text not null default 'public',
    is_vertical         boolean default true,
    scheduled_time_utc  timestamptz not null,
    status              text not null default 'pending', -- 'pending' | 'uploading' | 'posted' | 'failed' | 'cancelled'
    video_id            text,
    dailymotion_url     text,
    error               text,
    attempts            int default 0,
    created_at          timestamptz default now(),
    posted_at           timestamptz
);

create table if not exists dailymotion_publishing_records (
    id              bigint generated always as identity primary key,
    channel_id      bigint not null references dailymotion_channels(id) on delete cascade,
    video_id        text not null,
    dailymotion_url text,
    title           text,
    views           bigint default 0,
    earnings_usd    numeric(10, 2) default 0.00,
    status          text not null default 'published',
    error           text,
    published_at    timestamptz default now()
);

create index if not exists idx_dailymotion_jobs_sched
    on dailymotion_jobs (scheduled_time_utc)
    where status = 'pending';
