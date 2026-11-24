-- ============================================================
-- Reels Dashboard — Migration 002: scale to 100+ pages
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Index-only. Nothing here adds, drops, or rewrites a column, so
-- it is safe to run while the app is live and safe to run twice
-- (every statement is IF NOT EXISTS).
--
-- Why: every hot query in the app is scoped to one page_id. With
-- 100+ pages and thousands of content rows each, a sequential scan
-- per lookup is what falls over first. These make each of those a
-- targeted index lookup instead.
-- ============================================================

-- ---------- the three core page-scoped lookups ----------

-- content_items: every content list, count, and unposted-inventory
-- query filters on page_id.
create index if not exists idx_content_page on content_items(page_id);

-- scheduled_posts: the queue list, page_stats' pending count, and
-- cancel-all all filter on (page_id, status).
create index if not exists idx_scheduled_page_status on scheduled_posts(page_id, status);

-- posted_ledger: the per-page posted count and analytics fallback.
create index if not exists idx_ledger_page on posted_ledger(page_id);

-- ---------- supporting indexes for the paged views ----------

-- The content list pages with `where page_id = ? order by id limit ? offset ?`.
-- A (page_id, id) index lets Postgres walk straight to the offset in order
-- instead of sorting the page's whole library on every request.
create index if not exists idx_content_page_id_order on content_items(page_id, id);

-- unposted_content() / count_unposted() / list_content() all run
-- `not exists (select 1 from scheduled_posts where content_item_id = ? and status in (...))`.
-- Without this the anti-join scans scheduled_posts once per content row.
create index if not exists idx_scheduled_content_status
    on scheduled_posts(content_item_id, status);

-- The same helpers anti-join posted_ledger on (page_id, source_video_id).
-- The unique constraint already covers that pair, so nothing extra is
-- needed there — noted here so it isn't "fixed" later by mistake.

-- Analytics fallback reads posted_ledger newest-first per page.
create index if not exists idx_ledger_page_posted_at
    on posted_ledger(page_id, posted_at desc);
