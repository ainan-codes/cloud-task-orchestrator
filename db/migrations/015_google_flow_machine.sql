-- ============================================================
-- Reels Dashboard — Migration 015: Google Flow Video & Image Machine
--
-- Adds tables for Google Flow multi-account management (1000 credits each),
-- multi-scene vertical video jobs, on-demand image generation, and preset memory.
-- ============================================================

create table if not exists flow_accounts (
    id                  bigint generated always as identity primary key,
    label               text not null,                         -- e.g. "Account 1 (Primary)"
    email               text,                                  -- Google account email
    profile_folder      text not null unique,                 -- e.g. "acc_1" inside data/flow_profiles/
    credits_remaining   int default 1000,                      -- current live balance
    credits_total       int default 1000,                      -- starting credit pool
    is_active           boolean default true,
    last_used_at        timestamptz,
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

create table if not exists flow_jobs (
    id                  bigint generated always as identity primary key,
    account_id          bigint references flow_accounts(id) on delete set null,
    target_page_id      bigint references pages(id) on delete set null,
    media_type          text not null default 'reel',         -- 'reel' (video) or 'photo' (image)
    niche               text not null,
    duration_seconds    int default 10,                        -- 10, 30, 60, etc.
    scene_count         int default 1,                         -- number of clips stitched
    headline            text not null,
    prompt_used         text not null,
    caption             text,
    hashtags            text[] default array[]::text[],
    status              text default 'queued',                 -- 'queued' | 'generating' | 'stitching' | 'completed' | 'failed'
    credit_cost         int default 15,
    local_filepath      text,
    drive_folder_id     text,
    drive_file_id       text,
    content_item_id     bigint references content_items(id) on delete set null,
    error_message       text,
    created_at          timestamptz default now(),
    updated_at          timestamptz default now()
);

create table if not exists flow_settings (
    id                  text primary key default 'default',
    last_niche          text default 'Dark Psychology & Wealth',
    last_duration_seconds int default 10,
    last_media_type     text default 'reel',
    last_ai_model       text default 'gemini-2.5-flash',
    last_folder_option  text default 'auto',
    last_folder_id      text,
    last_batch_count    int default 5,
    custom_system_prompt text,
    updated_at          timestamptz default now()
);

-- Seed default settings row if not exists
insert into flow_settings (id, last_niche, last_duration_seconds, last_media_type, last_ai_model)
values ('default', 'Dark Psychology & Wealth', 10, 'reel', 'gemini-2.5-flash')
on conflict (id) do nothing;
