-- ============================================================
-- Reels Dashboard — Database Schema (Supabase / PostgreSQL)
-- Phase 1: pages, content items, schedules, the posted ledger
-- (per-page dedup), and analytics snapshots.
--
-- Run this in Supabase → SQL Editor → New query → paste → Run.
-- ============================================================

-- ---------- PAGES ----------
-- One row per Facebook Page you manage. Each has its own access token
-- and its own Google Drive folder of content.
create table if not exists pages (
    id              bigint generated always as identity primary key,
    name            text not null,                 -- friendly label, e.g. "Coconut Page"
    fb_page_id      text not null,                 -- Facebook numeric Page ID
    fb_access_token text not null,                 -- long-lived Page access token
    drive_folder_id text,                          -- Google Drive folder ID holding this page's content
    -- scheduling defaults for this page
    utc_offset_hours   int  default 0,             -- your local offset from UTC (Dubai = 4)
    schedule_mode      text default 'spread',      -- 'spread' | 'fixed'
    spread_start_hour  int  default 9,
    spread_end_hour    int  default 21,
    fixed_times        text[] default array['09:00','13:00','18:00'],
    per_run            int  default 6,             -- how many items to queue per run
    extra_hashtags     text[] default array[]::text[],
    caption_footer     text default '',
    is_active          boolean default true,
    created_at         timestamptz default now(),
    unique (fb_page_id)
);

-- ---------- CONTENT ITEMS ----------
-- One row per video/photo discovered in a page's Drive folder.
-- The SAME source file can appear for multiple pages (different rows),
-- which is exactly how "same video, different page" works.
create table if not exists content_items (
    id              bigint generated always as identity primary key,
    page_id         bigint not null references pages(id) on delete cascade,
    media_type      text not null default 'reel',  -- 'reel' | 'photo'
    source_video_id text,                           -- TikTok id parsed from filename (dedup key)
    drive_file_id   text not null,                  -- Google Drive file ID to download at post time
    file_name       text,
    caption         text default '',                -- caption text (locked to THIS item, no mismatch)
    hashtags        text[] default array[]::text[],
    sound_title     text,
    source_folder   text not null default 'root',    -- Drive subfolder name, or 'root' for the top-level folder
    discovered_at   timestamptz default now(),
    -- the dedup guarantee: a given Drive file can exist once per page
    -- (source_video_id can repeat across subfolders — see migration 003)
    unique (page_id, drive_file_id)
);

create or replace view content_source_folders as
select distinct page_id, source_folder
from content_items;

-- ---------- SCHEDULED POSTS ----------
-- The queue. Each row is one planned post at a specific time.
create table if not exists scheduled_posts (
    id                 bigint generated always as identity primary key,
    page_id            bigint not null references pages(id) on delete cascade,
    content_item_id    bigint not null references content_items(id) on delete cascade,
    scheduled_time_utc timestamptz not null,
    status             text not null default 'pending', -- pending|posting|posted|failed|skipped
    fb_creation_id     text,                             -- FB video/photo id once posted
    caption_used       text,                             -- exact caption sent (audit; prevents mismatch confusion)
    error              text,
    attempts           int default 0,
    created_at         timestamptz default now(),
    posted_at          timestamptz
);

create index if not exists idx_sched_due
    on scheduled_posts (scheduled_time_utc)
    where status = 'pending';

-- ---------- POSTED LEDGER ----------
-- The permanent record: (page, source_video) -> posted. This is what
-- guarantees the same video is NEVER posted twice to the SAME page,
-- while still allowing the same video on a DIFFERENT page.
create table if not exists posted_ledger (
    id               bigint generated always as identity primary key,
    page_id          bigint not null references pages(id) on delete cascade,
    source_video_id  text not null,
    content_item_id  bigint references content_items(id) on delete set null,
    fb_creation_id   text,
    caption_used     text,
    posted_at        timestamptz default now(),
    unique (page_id, source_video_id)   -- <-- the per-page dedup guarantee
);

-- ---------- ANALYTICS SNAPSHOTS ----------
-- Basic performance per posted item, refreshed from Facebook Insights.
create table if not exists analytics_snapshots (
    id               bigint generated always as identity primary key,
    page_id          bigint not null references pages(id) on delete cascade,
    fb_creation_id   text not null,
    source_video_id  text,
    views            bigint default 0,
    likes            bigint default 0,
    comments         bigint default 0,
    shares           bigint default 0,
    reach            bigint default 0,
    captured_at      timestamptz default now()
);

create index if not exists idx_analytics_page on analytics_snapshots (page_id, captured_at desc);

-- ---------- helper view: per-page counts for the dashboard ----------
create or replace view page_stats as
select
    p.id                                as page_id,
    p.name,
    p.is_active,
    (select count(*) from content_items c where c.page_id = p.id)                              as total_content,
    (select count(*) from posted_ledger l where l.page_id = p.id)                              as total_posted,
    (select count(*) from scheduled_posts s where s.page_id = p.id and s.status = 'pending')   as pending_scheduled,
    (select count(*) from content_items c
        where c.page_id = p.id
          and not exists (select 1 from posted_ledger l
                          where l.page_id = p.id and l.source_video_id = c.source_video_id))   as remaining_unposted
from pages p;
