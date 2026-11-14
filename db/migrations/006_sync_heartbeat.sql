-- ============================================================
-- Reels Dashboard — Migration 006: sync heartbeat
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Additive only, safe to run more than once.
--
-- Why: the sync-lock staleness check (migration 004) measured "stuck" as
-- time since the sync STARTED, but a large flat Drive folder (hundreds of
-- videos, each needing its own sidecar download) can legitimately take
-- longer than that threshold to finish one pass. That made the auto-expiry
-- fire on syncs that were still genuinely working. sync_heartbeat_at is
-- bumped on every progress update (now roughly once per file, since
-- progress reporting was made per-item instead of per-folder-batch), so
-- staleness is now "no progress for N minutes" instead of "running for
-- longer than N minutes" — a real hang gets caught without punishing a
-- slow-but-alive sync.
-- ============================================================

alter table pages add column if not exists sync_heartbeat_at timestamptz;
