-- ============================================================
-- Reels Dashboard — Migration 011: encrypted credential vault
--
-- Moves the Snapchat OAuth app credentials out of environment
-- variables and into Postgres, encrypted, so they can be rotated
-- without a redeploy and are shared by every runtime that talks to
-- this database (Vercel, the GitHub Actions runner, a local shell).
--
-- The important half of this migration is the LOCKDOWN below.
--
-- This project's dashboard talks to Supabase directly with the anon
-- key, and that key ships inside the built JavaScript bundle — it is
-- public by construction. Every table in this schema was created with
-- RLS disabled and grants to `anon`, which means anyone holding that
-- public key could read them. Verified before writing this migration:
--
--     GET /rest/v1/pages?select=* with the anon key -> 200, rows
--     including fb_access_token in plaintext.
--
-- So putting a client secret into this database WITHOUT locking the
-- table first would be strictly worse than leaving it in an env var.
--
-- The tables locked here are exactly the ones the browser never reads
-- (it reaches Snapchat only through /api/snapchat/*). The backend
-- connects as the owning role over DATABASE_URL and is unaffected;
-- PostgREST, and therefore the anon key, loses them entirely.
--
-- `pages` is NOT locked here: the dashboard reads and writes it
-- directly via supabase-js, so revoking anon would break it. It still
-- holds fb_access_token in plaintext and is still world-readable —
-- see docs/SNAPCHAT.md "Known gap" for the fix that follows.
--
-- Idempotent: safe to run more than once.
-- ============================================================

-- ------------------------------------------------------------
-- 1. The vault
-- ------------------------------------------------------------
create table if not exists app_credentials (
    key              text primary key,          -- e.g. 'snapchat_client_secret'
    value_encrypted  text not null,             -- Fernet ciphertext, never plaintext
    description      text,
    updated_at       timestamptz default now(),
    created_at       timestamptz default now(),
    rotated_at       timestamptz
);

comment on table app_credentials is
    'Encrypted application credentials. Values are Fernet ciphertext produced by '
    'backend/snapchat_crypto.py using SNAPCHAT_ENCRYPTION_KEY. Never readable by '
    'the anon or authenticated roles.';

-- ------------------------------------------------------------
-- 2. Lock the vault and every other credential-bearing table
-- ------------------------------------------------------------
-- RLS with zero policies denies everything to anyone who is not the
-- table owner or a BYPASSRLS role. Revoking the grants as well means
-- PostgREST will not even expose the table in its schema cache.
alter table app_credentials              enable row level security;
alter table snapchat_accounts            enable row level security;
alter table snapchat_oauth_states        enable row level security;
alter table snapchat_publishing_records  enable row level security;

revoke all on table app_credentials             from anon, authenticated;
revoke all on table snapchat_accounts           from anon, authenticated;
revoke all on table snapchat_oauth_states       from anon, authenticated;
revoke all on table snapchat_publishing_records from anon, authenticated;

-- The stats view reads snapchat_accounts, so it inherits the exposure
-- unless it is revoked too.
revoke all on table snapchat_account_stats from anon, authenticated;

-- New tables in this schema should not be handed to anon by default
-- either, so the next migration cannot silently reopen this hole.
alter default privileges in schema public revoke all on tables from anon;
alter default privileges in schema public revoke all on tables from authenticated;

-- ------------------------------------------------------------
-- 3. Seed the credential keys this application looks for
-- ------------------------------------------------------------
-- Rows are inserted by backend/credentials.py, which encrypts the
-- value first. Nothing here writes a plaintext secret; this only
-- records what the application will look for, for operator clarity.
comment on column app_credentials.key is
    'One of: snapchat_client_id, snapchat_client_secret, snapchat_redirect_uri.';
