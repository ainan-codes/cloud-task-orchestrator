-- ============================================================
-- Reels Dashboard — Migration 001: production upgrade
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Everything here is additive and idempotent (IF NOT EXISTS
-- everywhere) — safe to run more than once, and safe to run
-- while the app is live. Nothing here drops or rewrites data.
-- ============================================================

-- ---------- pages: hashtag rotation pool ----------
-- Up to 50 hashtags a page can draw from when building a caption,
-- mixed in with (never replacing) the video's own hashtags.
alter table pages add column if not exists hashtag_pool text[] default array[]::text[];

-- ---------- pages: last successful Drive sync ----------
alter table pages add column if not exists last_synced_at timestamptz;

-- ---------- pages: remembered auto-schedule settings ----------
-- {"posts_per_day": 6, "post_times": ["09:00", ...]} — used by the
-- auto-refill job in /api/run to keep a page's queue topped up
-- without needing you to re-enter the pattern every time.
alter table pages add column if not exists last_auto_schedule_settings jsonb;

-- ---------- scheduled_posts: last-modified timestamp ----------
-- Needed so the retry job can tell "failed 5 minutes ago" (leave it,
-- might be a transient blip still cooling down) from "failed 45
-- minutes ago" (safe to retry now).
alter table scheduled_posts add column if not exists updated_at timestamptz default now();

-- Keep updated_at current automatically, so app code never has to
-- remember to set it on every UPDATE.
create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_scheduled_posts_updated_at on scheduled_posts;
create trigger trg_scheduled_posts_updated_at
  before update on scheduled_posts
  for each row
  execute function set_updated_at();

-- ---------- retry lookup index ----------
create index if not exists idx_scheduled_retry
  on scheduled_posts (status, attempts)
  where status = 'failed' and attempts < 3;

-- ---------- activity log ----------
-- Lightweight audit trail: syncs, auto-schedule runs, cron summaries,
-- page changes. Nothing reads this yet in the UI; it's here so that
-- history isn't lost while a future "Activity" view gets built.
create table if not exists activity_log (
    id          bigint generated always as identity primary key,
    page_id     bigint references pages(id) on delete cascade,
    event_type  text not null,
    description text,
    metadata    jsonb,
    created_at  timestamptz default now()
);

create index if not exists idx_activity_log_page on activity_log (page_id, created_at desc);
