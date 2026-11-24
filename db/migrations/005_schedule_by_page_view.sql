-- ============================================================
-- Reels Dashboard — Migration 005: page-labeled schedule view
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Additive only (a view, no table changes), safe to run more than once.
--
-- Why: scheduled_posts is one table shared by every page, keyed by the
-- numeric page_id — browsing it directly in the Supabase Table Editor
-- mixes every page's rows together with no name to sort/group by. This
-- view joins in the page name and orders by page first, so the Table
-- Editor (or SQL Editor) reads as "each page's schedule, grouped" instead
-- of one flat, unordered table. The app itself is unaffected — it keeps
-- reading/writing scheduled_posts directly.
-- ============================================================

create or replace view scheduled_posts_by_page as
select
    p.name as page_name,
    sp.page_id,
    sp.id as scheduled_post_id,
    sp.status,
    sp.scheduled_time_utc,
    sp.posted_at,
    ci.file_name,
    ci.media_type,
    sp.attempts,
    sp.error,
    sp.created_at,
    sp.updated_at
from scheduled_posts sp
join pages p on p.id = sp.page_id
left join content_items ci on ci.id = sp.content_item_id
order by p.name, sp.status, sp.scheduled_time_utc;
