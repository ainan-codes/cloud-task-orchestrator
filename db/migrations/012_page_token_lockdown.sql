-- ============================================================
-- Reels Dashboard — Migration 012: close the Facebook token leak
--
-- Migration 011 locked the Snapchat tables but deliberately left `pages`
-- alone, because the dashboard reads and writes it directly through
-- supabase-js with the anon key. That left the worst exposure in the
-- schema still open, and it was verified open:
--
--     GET /rest/v1/pages?select=*  with the PUBLIC anon key  ->  200
--     [{"id":2,"name":"petflix","fb_access_token":"EAAIBd2ki1YkBS…"}]
--
-- The anon key ships inside the built JavaScript bundle, so those
-- long-lived Facebook Page tokens were readable by anyone who opened the
-- site and looked at the network tab.
--
-- This migration closes it on two axes:
--
--   1. COLUMN-LEVEL GRANTS. PostgREST honours Postgres column privileges,
--      so `anon` keeps SELECT on every column of `pages` EXCEPT
--      fb_access_token(_encrypted). The dashboard's reads keep working
--      untouched; the token simply stops being selectable.
--
--   2. NO WRITES FROM THE BROWSER. INSERT/UPDATE/DELETE on `pages` are
--      revoked entirely. The dashboard now goes through POST/PUT/DELETE
--      /api/pages, which already existed and already strips the token from
--      its responses. Without this, anon could not read the token but
--      could still overwrite it.
--
-- It also adds fb_access_token_encrypted so the token can be encrypted at
-- rest, matching how Snapchat tokens are already stored. The plaintext
-- column stays in place, nulled, so a failed rollout is recoverable — the
-- application reads whichever column has a value.
--
-- Still readable by anon after this, by design: page metadata, content
-- items, schedules, the posted ledger and the activity log. That is
-- business metadata, not credentials, and the dashboard reads all of it
-- directly. Closing it means moving those reads onto the backend API
-- first — see docs/SNAPCHAT.md "Known gap".
--
-- Idempotent: safe to run more than once.
-- ============================================================

-- ------------------------------------------------------------
-- 1. Encrypted storage for the Page token
-- ------------------------------------------------------------
alter table pages add column if not exists fb_access_token_encrypted text;

-- The plaintext column was declared NOT NULL by the base schema, so the
-- backfill cannot null it out until that constraint is lifted. The token is
-- no longer optional-but-plaintext; it is required-but-encrypted, and the
-- encrypted column carries it.
alter table pages alter column fb_access_token drop not null;

comment on column pages.fb_access_token_encrypted is
    'Fernet ciphertext of the long-lived Facebook Page access token. Written '
    'and read only by the backend. Never granted to anon or authenticated.';

comment on column pages.fb_access_token is
    'DEPRECATED plaintext token. Nulled by the 012 backfill; kept for rollback. '
    'Read only as a fallback when fb_access_token_encrypted is empty.';

-- ------------------------------------------------------------
-- 2. Column-level SELECT: everything except the token
-- ------------------------------------------------------------
-- Enumerated dynamically so a future column is granted automatically and
-- this migration does not have to be edited every time `pages` changes.
do $$
declare
    cols text;
begin
    select string_agg(quote_ident(column_name), ', ')
      into cols
      from information_schema.columns
     where table_schema = 'public'
       and table_name = 'pages'
       and column_name not in ('fb_access_token', 'fb_access_token_encrypted');

    execute 'revoke all on table public.pages from anon, authenticated';
    execute format('grant select (%s) on table public.pages to anon, authenticated', cols);
end $$;

-- Writes go through the backend, which holds the encryption key.
-- (The revoke above already removed them; this documents the intent.)

-- ------------------------------------------------------------
-- 3. Views over pages must not reintroduce the column
-- ------------------------------------------------------------
-- page_stats selects named columns only and does not include the token,
-- so it stays readable. Re-granted explicitly in case step 2's blanket
-- revoke on the base table cascades in a future Postgres version.
grant select on table public.page_stats to anon, authenticated;

-- ------------------------------------------------------------
-- 4. Verification
-- ------------------------------------------------------------
-- After running this, with the anon key:
--   GET /rest/v1/pages?select=*                 -> 401 (token column denied)
--   GET /rest/v1/pages?select=id,name           -> 200
--   POST/PATCH/DELETE /rest/v1/pages            -> 401
--   GET /rest/v1/page_stats?select=*            -> 200
