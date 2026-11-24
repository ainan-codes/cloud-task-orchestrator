-- ============================================================
-- Reels Dashboard — Migration 004: persist the Drive-sync lock
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Additive only, safe to run more than once.
--
-- Why: sync progress ({"status": "running"/"complete"/..., "found": N, ...})
-- lived only in an in-memory dict in the backend process. Any process
-- restart/redeploy while a sync was mid-flight, or a sync that hung and
-- never reached its except/finally block, left GET /sync/status stuck
-- returning {"status": "running", "found": 0} forever, and (once the
-- backend added a same-page concurrency guard) that stuck "running" state
-- blocked every future sync attempt for the page. Moving the lock into the
-- pages row means it survives restarts consistently (instead of silently
-- resetting to idle on one worker and staying "running" on another), and
-- gives the backend a started-at timestamp to detect and auto-expire a
-- stale lock.
-- ============================================================

alter table pages add column if not exists sync_status jsonb;
alter table pages add column if not exists sync_started_at timestamptz;
