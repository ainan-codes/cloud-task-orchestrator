-- ============================================================
-- Reels Dashboard — Migration 003: recursive Drive subfolder sync
-- Run in Supabase → SQL Editor → New query → paste → Run.
--
-- Additive + safe to run twice, EXCEPT for the constraint drop below,
-- which is a one-time, intentional change (see note).
-- ============================================================

-- ---------- content_items: which subfolder a video came from ----------
-- "root" for files living directly in the page's main Drive folder;
-- otherwise the name of the immediate subfolder that contains the file.
alter table content_items add column if not exists source_folder text not null default 'root';

-- ---------- switch the library's dedup key to the Drive file ID ----------
-- Recursive subfolder scanning means two DIFFERENT real videos in two
-- DIFFERENT subfolders can easily share the same numeric filename prefix
-- (source_video_id) — e.g. every subfolder's own "01_*.mp4". Keeping the
-- old (page_id, source_video_id) unique constraint would make sync fail or
-- silently drop unrelated videos. The Drive file ID is globally unique per
-- file, so that becomes the real "already in the library" guarantee.
--
-- Note: this does NOT change posted_ledger's own (page_id, source_video_id)
-- dedup key — that still governs "never post the same video twice to this
-- page" and is unaffected by this migration.
alter table content_items drop constraint if exists content_items_page_id_source_video_id_key;

create unique index if not exists idx_content_page_drivefile
    on content_items(page_id, drive_file_id);

-- ---------- distinct source folders per page, for the Content filter ----------
create or replace view content_source_folders as
select distinct page_id, source_folder
from content_items;
