-- ============================================================
-- Reels Dashboard / Reel Ledger — Migration 016
-- Universal Multi-Platform Architecture & Decoupled Engine
--
-- Introduces:
--  1. workspaces & brands
--  2. platform_credentials & platform_accounts (universal polymorphism)
--  3. content_sources (Google Drive watcher & reconciliation state)
--  4. content_assets & content_variants
--  5. publication_rules & publication_plans
--  6. publications & publication_attempts (durable state machine)
--  7. durable_jobs, job_attempts & dead_letter_jobs (distributed leases)
--  8. outbox_events (transactional outbox pattern)
--  9. monetization_accounts & revenue_records
-- 10. audit_logs & system_health_snapshots
--
-- 100% Additive & Backward-Compatible:
-- Existing tables (pages, content_items, scheduled_posts, posted_ledger)
-- remain fully functional and unmutated for existing code and UI.
-- ============================================================

-- ---------- 1. WORKSPACES & BRANDS ----------
create table if not exists workspaces (
    id              bigint generated always as identity primary key,
    name            text not null,
    slug            text not null unique,
    settings        jsonb default '{}'::jsonb,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now()
);

create table if not exists brands (
    id              bigint generated always as identity primary key,
    workspace_id    bigint not null references workspaces(id) on delete cascade,
    name            text not null,
    slug            text not null,
    niche           text default 'General',
    settings        jsonb default '{}'::jsonb,
    created_at      timestamptz default now(),
    updated_at      timestamptz default now(),
    unique (workspace_id, slug)
);

-- Seed default workspace & brand
insert into workspaces (id, name, slug)
overriding system value
values (1, 'Default Workspace', 'default')
on conflict (id) do nothing;

insert into brands (id, workspace_id, name, slug, niche)
overriding system value
values (1, 1, 'Default Brand', 'default', 'General')
on conflict (id) do nothing;

-- ---------- 2. PLATFORM CREDENTIALS & ACCOUNTS ----------
create table if not exists platform_credentials (
    id                  bigint generated always as identity primary key,
    workspace_id        bigint not null references workspaces(id) on delete cascade,
    platform            text not null,               -- 'facebook' | 'instagram' | 'youtube' | 'snapchat' | 'tiktok' | 'rumble' | 'dailymotion'
    auth_type           text not null default 'oauth',-- 'oauth' | 'api_key' | 'cookie' | 'service_account'
    encrypted_payload   text not null,               -- Fernet encrypted JSON containing access/refresh tokens, cookies, secrets
    token_status        text not null default 'valid',-- 'valid' | 'expiring' | 'expired' | 'revoked' | 'invalid' | 'reauth_required' | 'rate_limited'
    scopes              text[] default array[]::text[],
    expires_at          timestamptz,
    last_validated_at   timestamptz,
    validation_error    text,
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

create table if not exists platform_accounts (
    id                          bigint generated always as identity primary key,
    brand_id                    bigint not null references brands(id) on delete cascade,
    platform                    text not null,       -- 'facebook' | 'instagram' | 'youtube' | 'snapchat' | 'tiktok' | 'rumble' | 'dailymotion'
    account_type                text not null,       -- 'page' | 'creator_account' | 'channel' | 'public_profile'
    external_account_id         text not null,       -- e.g. Facebook numeric Page ID, YouTube channel ID, Snapchat username
    username                    text,
    display_name                text not null,
    credential_id               bigint references platform_credentials(id) on delete set null,
    health_status               text not null default 'healthy', -- 'healthy' | 'attention_required' | 'reauth_required' | 'rate_limited' | 'error' | 'disconnected'
    is_active                   boolean default true,
    last_verified_at            timestamptz,
    last_successful_publish_at  timestamptz,
    last_error_at               timestamptz,
    last_error_message          text,
    metadata                    jsonb default '{}'::jsonb,
    created_at                  timestamptz default now(),
    updated_at                  timestamptz default now(),
    unique (platform, external_account_id)
);

create index if not exists idx_platform_accounts_brand on platform_accounts(brand_id, platform);
create index if not exists idx_platform_accounts_health on platform_accounts(health_status, is_active);

-- ---------- 3. CONTENT SOURCES (GOOGLE DRIVE / S3) ----------
create table if not exists content_sources (
    id                      bigint generated always as identity primary key,
    brand_id                bigint not null references brands(id) on delete cascade,
    provider                text not null default 'google_drive', -- 'google_drive' | 's3' | 'r2' | 'local'
    folder_id               text not null,                        -- Google Drive folder ID
    folder_name             text not null,
    authentication_ref      text,                                 -- Service account or OAuth ref
    scan_cursor             text,                                 -- Drive sync page token
    last_successful_sync    timestamptz,
    last_attempted_sync     timestamptz,
    watch_channel_id        text,                                 -- Drive push notification channel
    watch_resource_id       text,
    watch_expiry            timestamptz,
    status                  text not null default 'active',       -- 'active' | 'syncing' | 'paused' | 'error'
    error_state             text,
    created_at              timestamptz default now(),
    updated_at              timestamptz default now(),
    unique (provider, folder_id)
);

-- ---------- 4. UNIVERSAL CONTENT ASSETS & VARIANTS ----------
-- Enrich content_items safely with non-destructive columns
alter table content_items add column if not exists brand_id bigint references brands(id) on delete set null;
alter table content_items add column if not exists content_source_id bigint references content_sources(id) on delete set null;
alter table content_items add column if not exists sha256_hash text;
alter table content_items add column if not exists perceptual_hash text;
alter table content_items add column if not exists video_fingerprint text;
alter table content_items add column if not exists validation_status text default 'ready'; -- 'validating' | 'ready' | 'validation_failed'
alter table content_items add column if not exists validation_error text;
alter table content_items add column if not exists content_category text;
alter table content_items add column if not exists storage_provider text default 'google_drive';
alter table content_items add column if not exists source_revision_id text;

create index if not exists idx_content_sha256 on content_items(sha256_hash) where sha256_hash is not null;
create index if not exists idx_content_brand on content_items(brand_id, media_type);

create table if not exists content_assets (
    id                  bigint generated always as identity primary key,
    content_item_id     bigint not null references content_items(id) on delete cascade,
    storage_provider    text not null default 'google_drive',
    storage_key         text not null,          -- drive_file_id or S3 key
    file_name           text not null,
    mime_type           text not null,
    file_size_bytes     bigint default 0,
    duration_seconds    numeric(10, 2),
    width               int,
    height              int,
    aspect_ratio        text,                   -- '9:16' | '1:1' | '16:9'
    has_audio           boolean default true,
    is_master           boolean default true,
    created_at          timestamptz default now()
);

create table if not exists content_variants (
    id                  bigint generated always as identity primary key,
    asset_id            bigint not null references content_assets(id) on delete cascade,
    platform            text not null,          -- 'facebook' | 'youtube' | 'tiktok' | 'snapchat'
    target_spec         text not null,          -- '1080x1920_h264_aac'
    storage_provider    text not null default 'google_drive',
    storage_key         text not null,
    status              text not null default 'ready', -- 'generating' | 'ready' | 'failed'
    created_at          timestamptz default now()
);

-- ---------- 5. PUBLICATION RULES & PLANS ----------
create table if not exists publication_rules (
    id                      bigint generated always as identity primary key,
    brand_id                bigint not null references brands(id) on delete cascade,
    platform_account_id     bigint not null references platform_accounts(id) on delete cascade,
    video_enabled           boolean default true,
    photo_enabled           boolean default false,
    text_enabled            boolean default false,
    schedule_mode           text default 'spread',      -- 'spread' | 'fixed'
    daily_video_limit       int default 3,
    daily_photo_limit       int default 2,
    daily_text_limit        int default 2,
    fixed_times             text[] default array['09:00', '14:00', '19:00']::text[],
    spread_start_hour       int default 9,
    spread_end_hour         int default 21,
    timezone                text default 'UTC',
    min_spacing_minutes     int default 30,
    quiet_hours_start       int,
    quiet_hours_end         int,
    is_active               boolean default true,
    created_at              timestamptz default now(),
    updated_at              timestamptz default now(),
    unique (brand_id, platform_account_id)
);

create table if not exists publication_plans (
    id                  bigint generated always as identity primary key,
    rule_id             bigint references publication_rules(id) on delete set null,
    content_item_id     bigint not null references content_items(id) on delete cascade,
    content_type        text not null default 'reel',  -- 'reel' | 'photo' | 'text'
    scheduled_at        timestamptz not null,
    status              text not null default 'planned', -- 'planned' | 'queued' | 'dispatched' | 'cancelled'
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

create index if not exists idx_pub_plans_due on publication_plans(scheduled_at) where status = 'planned';

-- ---------- 6. UNIVERSAL PUBLICATIONS & ATTEMPTS ----------
create table if not exists publications (
    id                      bigint generated always as identity primary key,
    plan_id                 bigint references publication_plans(id) on delete set null,
    platform_account_id     bigint not null references platform_accounts(id) on delete cascade,
    content_item_id         bigint not null references content_items(id) on delete cascade,
    media_variant_id        bigint references content_variants(id) on delete set null,
    content_type            text not null default 'reel', -- 'reel' | 'photo' | 'text'
    title                   text,
    caption                 text,
    description             text,
    hashtags                text[] default array[]::text[],
    scheduled_at            timestamptz not null,
    status                  text not null default 'queued',
    -- State machine: 'planned'|'queued'|'ready'|'uploading'|'processing'|'published'|'verifying'|'verified'|'failed'|'retrying'|'blocked'|'reauth_required'|'rate_limited'|'dead_letter'|'cancelled'
    external_post_id        text,
    idempotency_key         text not null unique,
    error_code              text,
    error_kind              text,  -- 'transient' | 'permanent' | 'authentication' | 'rate_limit' | 'validation'
    error_message           text,
    attempt_count           int default 0,
    max_attempts            int default 5,
    next_retry_at           timestamptz,
    published_at            timestamptz,
    verified_at             timestamptz,
    created_at              timestamptz default now(),
    updated_at              timestamptz default now()
);

create index if not exists idx_publications_due on publications(scheduled_at, status) where status in ('queued', 'retrying');
create index if not exists idx_publications_account on publications(platform_account_id, status);

create table if not exists publication_attempts (
    id                  bigint generated always as identity primary key,
    publication_id      bigint not null references publications(id) on delete cascade,
    attempt_number      int not null,
    stage               text not null, -- 'pre_check' | 'upload' | 'publish' | 'verify'
    status              text not null, -- 'running' | 'succeeded' | 'failed'
    error_kind          text,
    error_code          text,
    error_message       text,
    response_payload    jsonb,
    started_at          timestamptz default now(),
    completed_at        timestamptz
);

-- ---------- 7. DURABLE JOBS, WORKER LEASES & DEAD LETTER QUEUE ----------
create table if not exists durable_jobs (
    id                  bigint generated always as identity primary key,
    job_type            text not null,           -- 'drive_sync'|'content_ingestion'|'media_process'|'publication'|'analytics_sync'|'credential_validation'|'watchdog'|'reconciliation'
    priority            int default 10,          -- lower number = higher priority
    payload             jsonb not null default '{}'::jsonb,
    status              text not null default 'queued', -- 'queued'|'leased'|'completed'|'retrying'|'failed'|'dead_letter'|'cancelled'
    attempt_count       int default 0,
    max_attempts        int default 5,
    available_at        timestamptz default now(),
    locked_by           text,                    -- worker hostname/worker UUID
    locked_at           timestamptz,
    heartbeat_at        timestamptz,
    started_at          timestamptz,
    completed_at        timestamptz,
    last_error          text,
    idempotency_key     text unique,
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

create index if not exists idx_durable_jobs_claim 
    on durable_jobs (priority asc, available_at asc) 
    where status in ('queued', 'retrying');

create index if not exists idx_durable_jobs_stuck 
    on durable_jobs (heartbeat_at) 
    where status = 'leased';

create table if not exists dead_letter_jobs (
    id                  bigint generated always as identity primary key,
    job_id              bigint not null,
    publication_id      bigint references publications(id) on delete set null,
    original_type       text not null,
    payload             jsonb not null,
    failure_reason      text not null,
    error_details       jsonb,
    attempt_count       int not null,
    recovery_state      text not null default 'unresolved', -- 'unresolved' | 'retried' | 'rescheduled' | 'cancelled' | 'dismissed'
    last_attempt_at     timestamptz default now(),
    resolved_at         timestamptz,
    created_at          timestamptz default now()
);

-- ---------- 8. TRANSACTIONAL OUTBOX / EVENTS ----------
create table if not exists outbox_events (
    id                  bigint generated always as identity primary key,
    event_type          text not null,           -- 'content.ingested' | 'content.ready' | 'publication.scheduled' | 'publication.published' | 'publication.failed' | 'account.reauth_required'
    aggregate_type      text not null,           -- 'content_item' | 'publication' | 'platform_account'
    aggregate_id        text not null,
    payload             jsonb not null,
    status              text not null default 'pending', -- 'pending' | 'dispatched' | 'failed'
    created_at          timestamptz default now(),
    processed_at        timestamptz
);

create index if not exists idx_outbox_pending on outbox_events(status, created_at) where status = 'pending';

-- ---------- 9. REVENUE & MONETIZATION ----------
create table if not exists monetization_accounts (
    id                      bigint generated always as identity primary key,
    platform_account_id     bigint not null references platform_accounts(id) on delete cascade,
    program_name            text not null,          -- 'Facebook Reels Bonus' | 'YouTube Partner' | 'Dailymotion Monetization' | 'Snapchat Spotlight Reward'
    eligibility_status      text not null default 'eligible', -- 'eligible' | 'ineligible' | 'pending_review'
    currency                text not null default 'USD',
    balance                 numeric(12, 2) default 0.00,
    status                  text not null default 'active',
    created_at              timestamptz default now(),
    updated_at              timestamptz default now(),
    unique (platform_account_id, program_name)
);

create table if not exists revenue_records (
    id                      bigint generated always as identity primary key,
    publication_id          bigint references publications(id) on delete set null,
    platform_account_id     bigint not null references platform_accounts(id) on delete cascade,
    content_item_id         bigint references content_items(id) on delete set null,
    program_name            text not null,
    currency                text not null default 'USD',
    gross_amount            numeric(10, 2) default 0.00,
    net_amount              numeric(10, 2) default 0.00,
    period_start            date,
    period_end              date,
    payout_status           text not null default 'accrued', -- 'accrued' | 'pending' | 'paid'
    recorded_at             timestamptz default now()
);

-- ---------- 10. AUDIT LOG & SYSTEM HEALTH SNAPSHOTS ----------
create table if not exists audit_logs (
    id              bigint generated always as identity primary key,
    actor           text not null default 'system', -- 'user' | 'system' | 'watchdog' | 'worker'
    action          text not null,
    entity_type     text not null,
    entity_id       text,
    payload         jsonb default '{}'::jsonb,
    ip_address      text,
    created_at      timestamptz default now()
);

create index if not exists idx_audit_logs_action on audit_logs(action, created_at desc);

create table if not exists system_health_snapshots (
    id              bigint generated always as identity primary key,
    component       text not null,               -- 'api' | 'database' | 'durable_jobs' | 'google_drive' | 'workers' | 'meta_graph'
    status          text not null,               -- 'healthy' | 'degraded' | 'unhealthy'
    metrics         jsonb default '{}'::jsonb,
    checked_at      timestamptz default now()
);

create index if not exists idx_health_component on system_health_snapshots(component, checked_at desc);

-- ---------- 11. DATA BACKFILL & SYNC FROM PAGES ----------
-- Backfill existing pages into platform_accounts if not already registered
insert into platform_accounts (
    brand_id,
    platform,
    account_type,
    external_account_id,
    display_name,
    health_status,
    is_active,
    created_at
)
select
    1 as brand_id,
    'facebook' as platform,
    'page' as account_type,
    p.fb_page_id as external_account_id,
    p.name as display_name,
    case when p.token_status = 'invalid' then 'attention_required' else 'healthy' end as health_status,
    p.is_active,
    p.created_at
from pages p
on conflict (platform, external_account_id) do update
set display_name = excluded.display_name,
    is_active = excluded.is_active;

-- Backfill default publication rules for connected Facebook pages
insert into publication_rules (
    brand_id,
    platform_account_id,
    video_enabled,
    photo_enabled,
    text_enabled,
    schedule_mode,
    daily_video_limit,
    fixed_times,
    spread_start_hour,
    spread_end_hour,
    is_active
)
select
    pa.brand_id,
    pa.id as platform_account_id,
    true as video_enabled,
    true as photo_enabled,
    true as text_enabled,
    coalesce(p.schedule_mode, 'spread') as schedule_mode,
    coalesce(p.per_run, 3) as daily_video_limit,
    coalesce(p.fixed_times, array['09:00', '14:00', '19:00']::text[]) as fixed_times,
    coalesce(p.spread_start_hour, 9) as spread_start_hour,
    coalesce(p.spread_end_hour, 21) as spread_end_hour,
    p.is_active
from platform_accounts pa
join pages p on p.fb_page_id = pa.external_account_id
where pa.platform = 'facebook'
on conflict (brand_id, platform_account_id) do nothing;
