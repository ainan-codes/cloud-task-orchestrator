-- ============================================================
-- Reels Dashboard — Migration 010: Snapchat Public Profile API
--
-- Brings the Snapchat tables in line with what Snap's official API
-- actually returns and requires:
--
--   * Spotlight needs a locale and takes skip_save_to_profile.
--   * Snap's Post Story response has NO story id — only a request_id — so
--     the record stores which kind of identifier it holds.
--   * A media container expires 24h after creation, so its id and the
--     probed media specs are kept for diagnosis.
--   * Retry state (attempts / max_attempts / next_retry_at / error_code)
--     lives on the publishing record, not just on scheduled_posts.
--   * An idempotency key plus a real unique index make a duplicate publish
--     impossible rather than merely unlikely.
--
-- Idempotent: safe to run more than once.
-- Run in Supabase -> SQL Editor after 009.
-- ============================================================

-- ------------------------------------------------------------
-- 1. snapchat_accounts — profile metadata and connection state
-- ------------------------------------------------------------
alter table snapchat_accounts add column if not exists organization_id text;
alter table snapchat_accounts add column if not exists profile_type text;
alter table snapchat_accounts add column if not exists default_locale text default 'en_US';
alter table snapchat_accounts add column if not exists skip_save_to_profile boolean default false;

-- token_status answers "is the credential usable"; connection_status answers
-- "is this account linked at all". Disconnect clears the tokens and sets this
-- to 'disconnected' while keeping the row, its schedule and its history.
alter table snapchat_accounts add column if not exists connection_status text default 'disconnected';
alter table snapchat_accounts add column if not exists connected_at timestamptz;
alter table snapchat_accounts add column if not exists disconnected_at timestamptz;
alter table snapchat_accounts add column if not exists last_publish_at timestamptz;

-- Backfill for accounts connected under migration 009.
update snapchat_accounts
   set connection_status = 'connected',
       connected_at = coalesce(connected_at, token_checked_at, created_at)
 where access_token_encrypted is not null
   and connection_status is distinct from 'connected';

update snapchat_accounts
   set connection_status = 'disconnected'
 where access_token_encrypted is null
   and connection_status is null;

create index if not exists idx_snap_accounts_connection
    on snapchat_accounts (connection_status, is_active);

-- ------------------------------------------------------------
-- 2. snapchat_oauth_states — CSRF, with an expiry sweep
-- ------------------------------------------------------------
create index if not exists idx_snap_oauth_states_expiry
    on snapchat_oauth_states (expires_at);

-- ------------------------------------------------------------
-- 3. snapchat_publishing_records — the publishing job
-- ------------------------------------------------------------
alter table snapchat_publishing_records add column if not exists content_item_id bigint
    references content_items(id) on delete set null;
alter table snapchat_publishing_records add column if not exists description text;
alter table snapchat_publishing_records add column if not exists locale text default 'en_US';
alter table snapchat_publishing_records add column if not exists skip_save_to_profile boolean default false;

-- publish_id (from 009) held whatever came back. post_id is the real
-- Snapchat post identifier and post_id_kind names it: 'spotlight_id',
-- 'story_id', or 'request_id' when Snap gave us nothing better.
alter table snapchat_publishing_records add column if not exists post_id text;
alter table snapchat_publishing_records add column if not exists post_id_kind text;
alter table snapchat_publishing_records add column if not exists request_id text;

-- Where in the six-step pipeline this job is, for a live progress readout.
alter table snapchat_publishing_records add column if not exists stage text;

-- Retry accounting.
alter table snapchat_publishing_records add column if not exists max_attempts int default 3;
alter table snapchat_publishing_records add column if not exists next_retry_at timestamptz;
alter table snapchat_publishing_records add column if not exists error_code text;
alter table snapchat_publishing_records add column if not exists error_kind text;
alter table snapchat_publishing_records add column if not exists last_attempt_at timestamptz;

-- Probed media specs, so a rejected upload can be explained after the fact.
alter table snapchat_publishing_records add column if not exists media_duration_seconds numeric;
alter table snapchat_publishing_records add column if not exists media_width int;
alter table snapchat_publishing_records add column if not exists media_height int;
alter table snapchat_publishing_records add column if not exists media_size_bytes bigint;

-- The duplicate guard. One key per (account, content, placement, schedule
-- row): a retry reuses its record instead of creating a second publish.
alter table snapchat_publishing_records add column if not exists idempotency_key text;

update snapchat_publishing_records
   set post_id = coalesce(post_id, publish_id),
       post_id_kind = coalesce(post_id_kind,
                               case when publish_id is null then null
                                    when target_placement = 'spotlight' then 'spotlight_id'
                                    else 'request_id' end)
 where post_id is null and publish_id is not null;

update snapchat_publishing_records
   set idempotency_key = 'legacy:' || id::text
 where idempotency_key is null;

create unique index if not exists uq_snap_pub_idempotency
    on snapchat_publishing_records (idempotency_key);

create index if not exists idx_snap_pub_retry
    on snapchat_publishing_records (status, next_retry_at)
    where status = 'failed';

create index if not exists idx_snap_pub_post_id
    on snapchat_publishing_records (post_id)
    where post_id is not null;

-- ------------------------------------------------------------
-- 4. scheduled_posts — per-job Snapchat publishing options
-- ------------------------------------------------------------
alter table scheduled_posts add column if not exists snap_locale text;
alter table scheduled_posts add column if not exists snap_skip_save_to_profile boolean default false;
alter table scheduled_posts add column if not exists snap_post_id text;

create index if not exists idx_sched_snap_retry
    on scheduled_posts (platform, status, updated_at)
    where platform = 'snapchat';

-- ------------------------------------------------------------
-- 5. posted_ledger — make Snapchat dedup a constraint, not a query
-- ------------------------------------------------------------
-- The base schema's unique (page_id, source_video_id) does not bind Snapchat
-- rows: their page_id is NULL, and NULLs never collide in a unique index. A
-- crashed run that re-processed the same queue could therefore post twice.
-- Clear any duplicates that already landed, then constrain it.
delete from posted_ledger a
 using posted_ledger b
 where a.snapchat_account_id is not null
   and a.snapchat_account_id = b.snapchat_account_id
   and a.source_video_id = b.source_video_id
   and coalesce(a.target_placement, 'spotlight') = coalesce(b.target_placement, 'spotlight')
   and a.id > b.id;

create unique index if not exists uq_posted_ledger_snapchat
    on posted_ledger (snapchat_account_id, source_video_id, target_placement)
    where snapchat_account_id is not null and source_video_id is not null;

alter table posted_ledger add column if not exists snap_post_id text;

-- ------------------------------------------------------------
-- 6. snapchat_account_stats — connection health for the dashboard
-- ------------------------------------------------------------
drop view if exists snapchat_account_stats cascade;

create view snapchat_account_stats as
select
    s.id                                as account_id,
    s.name,
    s.snapchat_username,
    s.profile_name,
    s.profile_picture_url,
    s.public_profile_id,
    s.organization_id,
    s.account_type,
    s.default_placement,
    s.default_locale,
    s.skip_save_to_profile,
    s.is_active,
    s.connection_status,
    s.connected_at,
    s.disconnected_at,
    s.last_publish_at,
    s.token_status,
    s.token_error,
    s.token_expires_at,
    s.token_checked_at,
    (s.access_token_encrypted is not null)                                              as is_connected,
    (s.refresh_token_encrypted is not null)                                             as can_auto_refresh,
    (s.token_expires_at is not null and s.token_expires_at <= now())                    as token_expired,
    (select count(*) from content_items c  where c.snapchat_account_id = s.id)          as total_content,
    (select count(*) from posted_ledger l  where l.snapchat_account_id = s.id)          as total_posted,
    (select count(*) from scheduled_posts sp
      where sp.snapchat_account_id = s.id and sp.status = 'pending')                    as pending_scheduled,
    (select count(*) from scheduled_posts sp
      where sp.snapchat_account_id = s.id and sp.status in ('failed','abandoned'))      as failed_count,
    (select count(*) from snapchat_publishing_records pr
      where pr.snapchat_account_id = s.id and pr.status = 'published')                  as published_count,
    (select count(*) from snapchat_publishing_records pr
      where pr.snapchat_account_id = s.id and pr.status = 'published'
        and pr.target_placement = 'spotlight')                                          as published_spotlight_count,
    (select count(*) from snapchat_publishing_records pr
      where pr.snapchat_account_id = s.id and pr.status = 'published'
        and pr.target_placement = 'story')                                              as published_story_count,
    (select count(*) from snapchat_publishing_records pr
      where pr.snapchat_account_id = s.id and pr.status = 'failed')                     as failed_publish_count
from snapchat_accounts s;
