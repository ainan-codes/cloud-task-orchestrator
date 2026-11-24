-- ============================================================
-- Reels Dashboard — Migration 007: Fault-Tolerant Posting Engine
-- Adds token health tracking, indexes, and failure visibility
-- ============================================================

-- 1. Add token health tracking columns to pages table
alter table pages add column if not exists token_status text default 'unknown';
alter table pages add column if not exists token_error text;
alter table pages add column if not exists token_checked_at timestamptz;

-- 2. Add performance index for due & retry lookups
create index if not exists idx_sched_status_due
    on scheduled_posts (status, scheduled_time_utc);

create index if not exists idx_sched_status_page
    on scheduled_posts (status, page_id);

-- 3. Recreate page_stats view with failure counts and token health
drop view if exists page_stats cascade;

create view page_stats as
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
                          where l.page_id = p.id and l.source_video_id = c.source_video_id))   as remaining_unposted,
    p.token_status,
    p.token_error,
    p.token_checked_at,
    (select count(*) from scheduled_posts s where s.page_id = p.id and s.status in ('failed', 'abandoned')) as failed_count,
    (select count(*) from scheduled_posts s where s.page_id = p.id and s.status = 'abandoned') as abandoned_count
from pages p;
