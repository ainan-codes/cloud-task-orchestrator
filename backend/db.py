"""
db.py — database access layer (Phase 1).

Thin wrapper over Supabase's Postgres using psycopg. Handles: registering
pages, syncing Drive content into content_items, building the schedule
queue, and the per-page posted ledger (the dedup guarantee).

Set DATABASE_URL to your Supabase connection string
(Supabase -> Project Settings -> Database -> Connection string -> URI).
"""

import datetime as dt
import json
import os
import threading
import psycopg
from psycopg.rows import dict_row

try:
    from dotenv import load_dotenv
    load_dotenv()
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass


DB_CONNECT_TIMEOUT = int(os.environ.get("DB_CONNECT_TIMEOUT", "10"))

_pool = None
_pool_lock = threading.Lock()


def _get_pool():
    global _pool
    if _pool is None or getattr(_pool, "closed", False):
        with _pool_lock:
            if _pool is None or getattr(_pool, "closed", False):
                url = os.environ.get("DATABASE_URL")
                if not url:
                    raise RuntimeError("Set DATABASE_URL to your Supabase Postgres connection string.")
                try:
                    from psycopg_pool import ConnectionPool
                    _pool = ConnectionPool(
                        conninfo=url,
                        min_size=1,
                        max_size=10,
                        kwargs={
                            "row_factory": dict_row,
                            "prepare_threshold": None,
                            "connect_timeout": DB_CONNECT_TIMEOUT,
                        },
                        open=True,
                    )
                except Exception:
                    _pool = False
    return _pool


def _conn():
    pool = _get_pool()
    if pool and not getattr(pool, "closed", False):
        try:
            return pool.connection()
        except Exception:
            pass
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("Set DATABASE_URL to your Supabase Postgres connection string.")
    return psycopg.connect(
        url,
        row_factory=dict_row,
        prepare_threshold=None,
        connect_timeout=DB_CONNECT_TIMEOUT,
    )


# ---------------- pages ----------------
def _pages_where(active_only=True, search=None):
    conds = []
    vals = []
    if active_only:
        conds.append("is_active = true")
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append("(name ilike %s or fb_page_id ilike %s)")
        vals.extend([f"%{term}%", f"%{term}%"])
    return (" where " + " and ".join(conds) if conds else ""), vals


def list_pages(active_only=True, limit=None, offset=0, search=None):
    """limit=None returns every page — the posting/refill jobs need the full
    list. The dashboard passes limit/offset so a 100+ page account doesn't
    render one enormous table."""
    where, vals = _pages_where(active_only, search)
    q = f"select * from pages{where} order by id"
    if limit is not None:
        q += " limit %s offset %s"
        vals = vals + [limit, offset]
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return _with_page_tokens(cur.fetchall())


def count_pages(active_only=True, search=None):
    where, vals = _pages_where(active_only, search)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"select count(*) as n from pages{where}", vals)
        return cur.fetchone()["n"]


def get_page(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from pages where id = %s", (page_id,))
        return _with_page_token(cur.fetchone())



# ---------------- facebook page token, encrypted at rest ----------------
# The Page token used to sit in plaintext in a column the public anon key
# could read (see migration 012). It is now stored as Fernet ciphertext in
# fb_access_token_encrypted and decrypted here, at the few points that
# actually need it.
#
# Reads tolerate both columns on purpose: during the 012 rollout, and after
# any partial restore, some rows may still carry plaintext. Preferring the
# encrypted column and falling back keeps posting working either way rather
# than turning a migration ordering problem into an outage.
def _with_page_token(row):
    """Fill row['fb_access_token'] with the usable plaintext token."""
    if not row:
        return row
    encrypted = row.get("fb_access_token_encrypted")
    if encrypted:
        try:
            from snapchat_crypto import decrypt_token
            row["fb_access_token"] = decrypt_token(encrypted)
        except Exception:
            # A key mismatch must not silently look like "no token": leave
            # whatever plaintext exists so check_token reports a real error.
            pass
    return row


def _with_page_tokens(rows):
    return [_with_page_token(r) for r in rows]


def _encrypted_page_token(value):
    """Ciphertext for a plaintext token, for writes."""
    if not value:
        return None
    from snapchat_crypto import encrypt_token
    return encrypt_token(value)


def upsert_page(**kw):
    cols = ["name", "fb_page_id", "fb_access_token", "drive_folder_id",
            "utc_offset_hours", "schedule_mode", "spread_start_hour",
            "spread_end_hour", "fixed_times", "per_run", "extra_hashtags",
            "caption_footer", "is_active", "hashtag_pool"]
    # The token is written to the encrypted column and the plaintext one is
    # cleared, so a save can never reintroduce a readable token.
    kw = dict(kw)
    token = kw.pop("fb_access_token", None)
    cols = [c for c in cols if c != "fb_access_token"]
    cols += ["fb_access_token_encrypted", "fb_access_token"]
    vals = [kw.get(k) for k in cols[:-2]] + [_encrypted_page_token(token), None]

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""insert into pages ({','.join(cols)})
                values ({','.join(['%s']*len(cols))})
                on conflict (fb_page_id) do update set
                  {', '.join(f'{k}=excluded.{k}' for k in cols)}
                returning *""",
            vals,
        )
        row = cur.fetchone()
        if row:
            try:
                cur.execute("""
                    INSERT INTO platform_accounts (
                        brand_id, platform, account_type, external_account_id,
                        display_name, health_status, is_active
                    ) VALUES (1, 'facebook', 'page', %s, %s, 'healthy', %s)
                    ON CONFLICT (platform, external_account_id) DO UPDATE
                    SET display_name = excluded.display_name, is_active = excluded.is_active;
                """, (row["fb_page_id"], row["name"], row.get("is_active", True)))

                if row.get("drive_folder_id"):
                    cur.execute("""
                        INSERT INTO content_sources (
                            brand_id, provider, folder_id, folder_name, status
                        ) VALUES (1, 'google_drive', %s, %s, 'active')
                        ON CONFLICT (provider, folder_id) DO UPDATE
                        SET folder_name = excluded.folder_name;
                    """, (row["drive_folder_id"], f"{row['name']} Folder"))
            except Exception:
                pass
        return _with_page_token(row)


def update_page(page_id, **kw):
    cols = ["name", "fb_page_id", "fb_access_token", "drive_folder_id",
            "utc_offset_hours", "schedule_mode", "spread_start_hour",
            "spread_end_hour", "fixed_times", "per_run", "extra_hashtags",
            "caption_footer", "is_active", "hashtag_pool"]
    sets = []
    vals = []
    kw = dict(kw)
    token = kw.pop("fb_access_token", None)
    if token:
        # Same as upsert: encrypted in, plaintext cleared.
        sets.append("fb_access_token_encrypted = %s")
        vals.append(_encrypted_page_token(token))
        sets.append("fb_access_token = null")
    for k in cols:
        if k == "fb_access_token":
            continue
        if k in kw and kw[k] is not None:
            sets.append(f"{k} = %s")
            vals.append(kw[k])
    if not sets:
        return get_page(page_id)
    vals.append(page_id)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"update pages set {', '.join(sets)} where id = %s returning *", vals)
        return cur.fetchone()


def set_last_synced_at(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("update pages set last_synced_at = now() where id = %s", (page_id,))


def set_last_auto_schedule_settings(page_id, settings):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update pages set last_auto_schedule_settings = %s::jsonb where id = %s",
            (json.dumps(settings), page_id),
        )


def get_sync_lock(page_id):
    """Returns (state_dict_or_None, started_at_or_None, heartbeat_at_or_None)
    from the pages row — the persisted sync lock, so it survives a backend
    restart instead of silently resetting (or getting stuck) in an in-memory
    dict. Staleness should be judged against heartbeat_at (last progress
    update), not started_at — a slow-but-alive sync keeps bumping the
    heartbeat no matter how long it's been running."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select sync_status, sync_started_at, sync_heartbeat_at from pages where id = %s",
            (page_id,),
        )
        row = cur.fetchone()
        if not row:
            return None, None, None
        return row["sync_status"], row["sync_started_at"], row["sync_heartbeat_at"]


def start_sync_lock(page_id, state):
    """Mark a sync as starting: writes the state and stamps both
    sync_started_at and sync_heartbeat_at to now()."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update pages set sync_status = %s::jsonb, sync_started_at = now(),
               sync_heartbeat_at = now() where id = %s""",
            (json.dumps(state), page_id),
        )


def set_sync_lock(page_id, state):
    """Update the sync state and bump sync_heartbeat_at to now() — used for
    every progress update and the final complete/error state, so staleness
    checks always measure time since the last real update."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update pages set sync_status = %s::jsonb, sync_heartbeat_at = now() where id = %s",
            (json.dumps(state), page_id),
        )


def clear_last_auto_schedule_settings(page_id):
    """Forget the remembered auto-schedule pattern so the auto-refill pass
    (which only acts on pages with a remembered pattern) leaves this page
    alone until the user explicitly runs Auto-Schedule again."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update pages set last_auto_schedule_settings = null where id = %s",
            (page_id,),
        )


# ---------------- content sync ----------------
def _content_where(page_id, search=None, source_folder=None):
    """Shared WHERE for the content list and its total count, so the paged
    rows and the "of N" number can never disagree. Search matches caption or
    file name, case-insensitively, server-side."""
    conds = ["ci.page_id = %s"]
    vals = [page_id]
    if search and search.strip():
        # Escape LIKE wildcards so a literal % or _ in a filename searches
        # for itself instead of matching everything.
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append("(ci.caption ilike %s or ci.file_name ilike %s)")
        vals.extend([f"%{term}%", f"%{term}%"])
    if source_folder:
        conds.append("ci.source_folder = %s")
        vals.append(source_folder)
    return " and ".join(conds), vals


def count_content(page_id, search=None, source_folder=None):
    where, vals = _content_where(page_id, search, source_folder)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"select count(*) as n from content_items ci where {where}", vals)
        return cur.fetchone()["n"]


def get_known_captions_map():
    """Returns a dict of {drive_file_id: {caption, hashtags, sound_title}}
    across all existing content_items in the database, allowing instant
    metadata reuse without redundant Drive sidecar downloads."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select distinct on (drive_file_id) drive_file_id, caption, hashtags, sound_title "
            "from content_items where (caption is not null and caption != '') or (hashtags is not null and cardinality(hashtags) > 0)"
        )
        return {
            r["drive_file_id"]: {
                "caption": r["caption"] or "",
                "hashtags": r["hashtags"] or [],
                "sound_title": r["sound_title"],
            }
            for r in cur.fetchall()
        }


def sync_content(page_id, items):
    """Insert newly-discovered Drive items for a page. Dedup key is the Drive
    file ID (page_id, drive_file_id) — not source_video_id, which can repeat
    across different subfolders after a recursive scan (see migration 003).

    If an existing content_items row has an empty/null caption, and a
    sidecar match is now found, update its caption/hashtags/sound_title
    fields (idempotent re-sync).

    Returns (added, updated, duplicates_skipped)."""
    if not items:
        return 0, 0, 0
    added = 0
    updated = 0
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select drive_file_id, caption, hashtags from content_items where page_id = %s",
            (page_id,),
        )
        existing = {r["drive_file_id"]: r for r in cur.fetchall()}

        to_insert = []
        to_update = []
        for it in items:
            dfid = it["drive_file_id"]
            if dfid not in existing:
                to_insert.append((
                    page_id, it["media_type"], it["source_video_id"], dfid,
                    it["file_name"], it["caption"], it["hashtags"], it.get("sound_title"),
                    it.get("source_folder", "root"),
                ))
            else:
                ex = existing[dfid]
                has_sidecar = bool(it.get("caption") or it.get("hashtags"))
                ex_has_cap = bool(ex.get("caption") and str(ex.get("caption")).strip())
                ex_has_tags = bool(ex.get("hashtags") and len(ex.get("hashtags")) > 0)
                if has_sidecar and (not ex_has_cap or not ex_has_tags):
                    to_update.append((
                        it["caption"], it["hashtags"], it.get("sound_title"), page_id, dfid,
                    ))

        if to_insert:
            cur.executemany(
                """insert into content_items
                     (page_id, media_type, source_video_id, drive_file_id,
                      file_name, caption, hashtags, sound_title, source_folder,
                      brand_id, storage_provider, validation_status)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s, 1, 'google_drive', 'ready')
                   on conflict (page_id, drive_file_id) do nothing""",
                to_insert,
            )
            added = len(to_insert)

        if to_update:
            cur.executemany(
                """update content_items
                   set caption = %s,
                       hashtags = %s,
                       sound_title = coalesce(%s, sound_title)
                   where page_id = %s and drive_file_id = %s""",
                to_update,
            )
            updated = len(to_update)

    duplicates = len(items) - added - updated
    return added, updated, duplicates


def list_source_folders(page_id):
    """Distinct source_folder values in this page's library, for the Content
    filter dropdown. 'root' is included only if some video lives there."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select source_folder from content_source_folders where page_id = %s order by source_folder",
            (page_id,),
        )
        return [r["source_folder"] for r in cur.fetchall()]


def count_unposted(page_id):
    """Count of content for this page not posted AND not already pending —
    the true free-inventory number (health dashboard, auto-refill trigger)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select count(*) as n from content_items ci
               where ci.page_id = %s
                 and not exists (select 1 from posted_ledger l
                                 where l.page_id = ci.page_id
                                   and l.source_video_id = ci.source_video_id)
                 and not exists (select 1 from scheduled_posts s
                                 where s.content_item_id = ci.id
                                   and s.status in ('pending','posting','posted'))""",
            (page_id,),
        )
        return cur.fetchone()["n"]


def count_unposted_by_page(page_ids=None):
    """count_unposted() for MANY pages in one query, as {page_id: n}.

    The auto-refill pass in /api/run runs every 15 minutes over every active
    page; at 100+ pages, asking per page (each on its own connection) is the
    single most expensive thing the cron does. Pages with nothing free are
    simply absent from the result — callers should default to 0."""
    q = """select ci.page_id, count(*) as n from content_items ci
           where not exists (select 1 from posted_ledger l
                             where l.page_id = ci.page_id
                               and l.source_video_id = ci.source_video_id)
             and not exists (select 1 from scheduled_posts s
                             where s.content_item_id = ci.id
                               and s.status in ('pending','posting','posted'))"""
    vals = []
    if page_ids is not None:
        if not page_ids:
            return {}
        q += " and ci.page_id = any(%s)"
        vals.append(list(page_ids))
    q += " group by ci.page_id"
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return {r["page_id"]: r["n"] for r in cur.fetchall()}


def unposted_content(page_id, limit=None, media_type=None):
    """Content for this page not yet in the posted ledger AND not already
    queued as pending — the source for building new schedule entries.
    limit=None returns every matching row (used by auto-schedule, which must
    place ALL unposted content, not just a batch).
    media_type optionally filters for 'reel', 'photo', or 'text'."""
    q = """select ci.* from content_items ci
           where ci.page_id = %s
             and coalesce(ci.validation_status, 'ready') = 'ready'
             and not exists (select 1 from posted_ledger l
                             where l.page_id = ci.page_id
                               and l.source_video_id = ci.source_video_id)
             and not exists (select 1 from scheduled_posts s
                             where s.content_item_id = ci.id
                               and s.status in ('pending','posting','posted'))"""
    vals = [page_id]
    if media_type and media_type != "all":
        q += " and ci.media_type = %s"
        vals.append(media_type)
    q += " order by ci.id"
    if limit is not None:
        q += " limit %s"
        vals.append(limit)
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return cur.fetchall()


def create_content_item(page_id, media_type, source_video_id, drive_file_id, file_name, caption="", hashtags=None, source_folder="direct"):
    """Inserts a new content item (image, text update, or video) directly for a page."""
    hashtags = hashtags or []
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into content_items
                 (page_id, media_type, source_video_id, drive_file_id,
                  file_name, caption, hashtags, source_folder,
                  brand_id, storage_provider, validation_status)
               values (%s,%s,%s,%s,%s,%s,%s,%s, 1, 'google_drive', 'ready')
               returning *""",
            (page_id, media_type, source_video_id, drive_file_id, file_name, caption, hashtags, source_folder)
        )
        row = cur.fetchone()
        if row:
            try:
                cur.execute("""
                    INSERT INTO content_assets (
                        content_item_id, storage_provider, storage_key, file_name, mime_type, is_master
                    ) VALUES (%s, 'google_drive', %s, %s, %s, true);
                """, (
                    row["id"], drive_file_id, file_name,
                    "video/mp4" if media_type == "reel" else ("image/jpeg" if media_type == "photo" else "text/plain")
                ))
            except Exception:
                pass
        return row


def bulk_reschedule(updates):
    """updates is a list of dicts: [{'id': sched_id, 'scheduled_time_utc': dt_obj}].
    Updates scheduled post times in batch to resolve collisions."""
    if not updates:
        return 0
    with _conn() as c, c.cursor() as cur:
        cur.executemany(
            """update scheduled_posts
               set scheduled_time_utc = %s
               where id = %s and status = 'pending'""",
            [(u["scheduled_time_utc"], u["id"]) for u in updates]
        )
    return len(updates)



# ---------------- scheduling queue ----------------
def enqueue(page_id, content_item_id, scheduled_time_utc, caption_used):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into scheduled_posts
                 (page_id, content_item_id, scheduled_time_utc, caption_used)
               values (%s,%s,%s,%s) returning *""",
            (page_id, content_item_id, scheduled_time_utc, caption_used),
        )
        return cur.fetchone()


def bulk_enqueue(rows):
    """rows: list of (page_id, content_item_id, scheduled_time_utc, caption_used).
    Auto-schedule can place hundreds of items at once; executemany avoids a
    round-trip per row."""
    if not rows:
        return 0
    with _conn() as c, c.cursor() as cur:
        cur.executemany(
            """insert into scheduled_posts
                 (page_id, content_item_id, scheduled_time_utc, caption_used)
               values (%s,%s,%s,%s)""",
            rows,
        )
    return len(rows)


def due_posts(now_utc=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select sp.*, ci.media_type, ci.drive_file_id, ci.source_video_id, ci.file_name,
                      p.fb_page_id, p.fb_access_token, p.fb_access_token_encrypted,
                      p.name as page_name
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join pages p on p.id = sp.page_id
               where sp.status = 'pending' and sp.scheduled_time_utc <= %s
               order by sp.scheduled_time_utc
               limit 20""",
            (now_utc,),
        )
        return _with_page_tokens(cur.fetchall())


def retryable_failed_posts(cutoff_minutes=30):
    """Failed posts that haven't exhausted their 3 attempts and have sat
    for a cooldown period (avoids hammering a transient FB error every 15
    minutes). Requires the updated_at column from migration 001 — raises
    a normal DB error (translated by the caller) if that hasn't run yet."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select sp.*, ci.media_type, ci.drive_file_id, ci.source_video_id,
                      p.fb_page_id, p.fb_access_token, p.fb_access_token_encrypted,
                      p.name as page_name
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join pages p on p.id = sp.page_id
               where sp.status = 'failed'
                 and sp.attempts < 3
                 and sp.updated_at < now() - (%s * interval '1 minute')
               order by sp.updated_at
               limit 20""",
            (cutoff_minutes,),
        )
        return _with_page_tokens(cur.fetchall())


def mark_status(sched_id, status, **fields):
    sets = ["status = %s"]
    vals = [status]
    for k, v in fields.items():
        sets.append(f"{k} = %s")
        vals.append(v)
    if status == "posted":
        sets.append("posted_at = now()")
    if status == "failed":
        # Counts actual failures only. It used to also increment on the
        # "posting" transition, double-counting every attempt — that made
        # the retry job's "attempts < 3" cutoff abandon posts after ~1.5
        # real tries instead of 3.
        sets.append("attempts = attempts + 1")
    vals.append(sched_id)
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"update scheduled_posts set {', '.join(sets)} where id = %s", vals)


def set_caption_used(sched_id, caption):
    """Overwrite a scheduled post's caption_used. Called immediately before
    publishing (so the audit row matches what was actually sent) and by the
    refresh-pending-captions maintenance endpoint."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update scheduled_posts set caption_used = %s where id = %s",
            (caption, sched_id),
        )


def pending_posts_with_content(page_id):
    """Every pending post for a page alongside its content row's CURRENT
    caption fields — one query, so refreshing a backlog of hundreds of posts
    doesn't become hundreds of round-trips."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select sp.id, sp.content_item_id, sp.caption_used,
                      ci.caption as live_caption, ci.hashtags as live_hashtags
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               where sp.page_id = %s and sp.status = 'pending'
               order by sp.scheduled_time_utc""",
            (page_id,),
        )
        return cur.fetchall()


def bulk_set_caption_used(rows):
    """rows: list of (caption, sched_id). One round-trip for the whole batch."""
    if not rows:
        return 0
    with _conn() as c, c.cursor() as cur:
        cur.executemany(
            "update scheduled_posts set caption_used = %s where id = %s",
            rows,
        )
    return len(rows)


def record_ledger(page_id, source_video_id, content_item_id, fb_creation_id, caption_used):
    """The dedup write. Unique (page_id, source_video_id) makes a double-post
    to the same page impossible even under a race."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into posted_ledger
                 (page_id, source_video_id, content_item_id, fb_creation_id, caption_used)
               values (%s,%s,%s,%s,%s)
               on conflict (page_id, source_video_id) do nothing
               returning id""",
            (page_id, source_video_id, content_item_id, fb_creation_id, caption_used),
        )
        return cur.fetchone() is not None


def already_posted(page_id, source_video_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select 1 from posted_ledger where page_id=%s and source_video_id=%s",
            (page_id, source_video_id),
        )
        return cur.fetchone() is not None


def page_stats(page_ids=None):
    """Per-page counts straight from the page_stats view (aggregate COUNTs in
    Postgres — no row lists pulled into the app to be counted). Pass page_ids
    to fetch stats for just one page of results."""
    with _conn() as c, c.cursor() as cur:
        if page_ids is not None:
            if not page_ids:
                return []
            cur.execute(
                "select * from page_stats where page_id = any(%s) order by page_id",
                (list(page_ids),),
            )
        else:
            cur.execute("select * from page_stats order by page_id")
        return cur.fetchall()


def page_stat(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from page_stats where page_id = %s", (page_id,))
        return cur.fetchone()


def last_posted_at(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select max(posted_at) as t from posted_ledger where page_id = %s", (page_id,))
        row = cur.fetchone()
        return row["t"] if row else None


def last_failed(page_id):
    """Most recent failed/abandoned scheduled post for this page. Requires
    the updated_at column from migration 001."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select error, updated_at from scheduled_posts
               where page_id = %s and status in ('failed', 'abandoned')
               order by updated_at desc
               limit 1""",
            (page_id,),
        )
        return cur.fetchone()


def log_activity(page_id, event_type, description=None, metadata=None):
    """Best-effort audit trail (migration 001's activity_log table). Never
    raises: a missing table pre-migration, or any other issue here, must
    never break the action being logged."""
    try:
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                """insert into activity_log (page_id, event_type, description, metadata)
                   values (%s, %s, %s, %s::jsonb)""",
                (page_id, event_type, description,
                 json.dumps(metadata) if metadata is not None else None),
            )
    except Exception:
        pass


def save_analytics(page_id, fb_creation_id, source_video_id, metrics):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into analytics_snapshots
                 (page_id, fb_creation_id, source_video_id, views, likes, comments, shares, reach)
               values (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (page_id, fb_creation_id, source_video_id,
             metrics.get("views", 0), metrics.get("likes", 0),
             metrics.get("comments", 0), metrics.get("shares", 0),
             metrics.get("reach", 0)),
        )


# ---------------- dashboard read helpers (Phase 3/4) ----------------
def list_content(page_id, limit=None, offset=0, search=None, source_folder=None):
    """One page of content items, annotated with whether they're posted or
    currently scheduled, so the dashboard can show status per item.

    A page's library can run to thousands of rows, so callers pass limit/
    offset rather than pulling the whole thing. The posted/scheduled
    annotations are correlated subqueries in the target list, so they're only
    evaluated for the rows actually returned."""
    where, vals = _content_where(page_id, search, source_folder)
    q = f"""select ci.*,
                   exists(select 1 from posted_ledger l
                          where l.page_id = ci.page_id
                            and l.source_video_id = ci.source_video_id) as is_posted,
                   (select sp.scheduled_time_utc from scheduled_posts sp
                    where sp.content_item_id = ci.id and sp.status = 'pending'
                    order by sp.scheduled_time_utc limit 1) as scheduled_time_utc
            from content_items ci
            where {where}
            order by ci.id"""
    if limit is not None:
        q += " limit %s offset %s"
        vals = vals + [limit, offset]
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return cur.fetchall()


def get_content_item(content_item_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from content_items where id = %s", (content_item_id,))
        return cur.fetchone()


def list_scheduled(page_id=None, status=None):
    q = """select sp.*, ci.file_name, ci.media_type, ci.source_video_id,
                  p.name as page_name
           from scheduled_posts sp
           join content_items ci on ci.id = sp.content_item_id
           join pages p on p.id = sp.page_id"""
    conds, vals = [], []
    if page_id:
        conds.append("sp.page_id = %s"); vals.append(page_id)
    if status:
        conds.append("sp.status = %s"); vals.append(status)
    if conds:
        q += " where " + " and ".join(conds)
    q += " order by sp.scheduled_time_utc"
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return cur.fetchall()


def calendar_posts(page_id, start_utc, end_utc):
    """All scheduled_posts (any status) for this page in [start_utc, end_utc)
    — the caller computes those bounds from the page's own utc_offset_hours
    so a 'week' lines up with the page's local calendar, not UTC's."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select sp.*, ci.file_name, ci.media_type, ci.caption
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               where sp.page_id = %s
                 and sp.scheduled_time_utc >= %s
                 and sp.scheduled_time_utc < %s
               order by sp.scheduled_time_utc""",
            (page_id, start_utc, end_utc),
        )
        return cur.fetchall()


def delete_scheduled(sched_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from scheduled_posts where id = %s and status = 'pending'", (sched_id,))
        return cur.rowcount > 0


def delete_all_pending_scheduled(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from scheduled_posts where page_id = %s and status = 'pending'", (page_id,))
        return cur.rowcount


def delete_posted_ledger(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from posted_ledger where page_id = %s", (page_id,))
        return cur.rowcount


def delete_all_scheduled(page_id):
    """Wipe every scheduled_posts row for a page regardless of status. Used
    by the full page reset (Clear Posted History) — that action needs the
    pending queue gone too, not just the posted/failed rows, so the library
    reports as fully available again (count_unposted checks scheduled_posts
    status in ('pending','posting','posted'), not just 'posted')."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from scheduled_posts where page_id = %s", (page_id,))
        return cur.rowcount


def delete_scheduled_bulk(ids):
    """Surgical cancellation for the update-intensity 'decreasing' path —
    only ever cancels rows still pending, never a posted/posting one."""
    if not ids:
        return 0
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "delete from scheduled_posts where id = any(%s) and status = 'pending'",
            (list(ids),),
        )
        return cur.rowcount


def cancel_pending_for_content(content_item_id):
    """Cancel any pending scheduled_posts row for this content item so
    post-now can proceed without a unique-constraint violation later."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "delete from scheduled_posts where content_item_id = %s and status = 'pending'",
            (content_item_id,),
        )
        return cur.rowcount


def posted_ledger_for_page(page_id):
    """Return posted_ledger rows joined with content_items for analytics
    fallback when no analytics_snapshots exist yet."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select l.fb_creation_id, l.source_video_id, l.content_item_id,
                      l.posted_at, ci.file_name
               from posted_ledger l
               left join content_items ci on ci.id = l.content_item_id
               where l.page_id = %s
               order by l.posted_at desc""",
            (page_id,),
        )
        return cur.fetchall()


def reschedule(sched_id, new_time_utc):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update scheduled_posts set scheduled_time_utc = %s where id = %s and status = 'pending' returning *",
            (new_time_utc, sched_id),
        )
        return cur.fetchone()


def delete_page(page_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from pages where id = %s", (page_id,))
        return cur.rowcount > 0


def analytics_for_page(page_id):
    """Latest snapshot per posted item for a page, best-performers first."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select distinct on (fb_creation_id)
                      fb_creation_id, source_video_id, views, likes, comments, shares, reach, captured_at
               from analytics_snapshots
               where page_id = %s
               order by fb_creation_id, captured_at desc""",
            (page_id,),
        )
        rows = cur.fetchall()
    rows.sort(key=lambda r: (r.get("views") or 0), reverse=True)
    return rows


def posted_items_for_analytics():
    """Every posted item with its fb id + page token, for the analytics refresh."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select l.page_id, l.source_video_id, l.fb_creation_id,
                      p.fb_access_token, p.fb_access_token_encrypted
               from posted_ledger l
               join pages p on p.id = l.page_id
               where l.fb_creation_id is not null"""
        )
        return _with_page_tokens(cur.fetchall())


# ---------------- fault-tolerance & resilience ----------------
def heal_stuck_posting_posts(max_age_minutes=15):
    """Self-healing: resets posts stuck in 'posting' status longer than
    max_age_minutes (e.g. killed worker or edge proxy timeout) back to
    'failed' so they can be reviewed/retried instead of staying in limbo forever."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update scheduled_posts
               set status = 'failed',
                   error = 'Worker timed out or crashed mid-upload'
               where status = 'posting'
                 and (
                     (updated_at is not null and updated_at < now() - (%s * interval '1 minute'))
                     or
                     (updated_at is null and created_at < now() - (%s * interval '1 minute'))
                 )
               returning id""",
            (max_age_minutes, max_age_minutes),
        )
        return len(cur.fetchall())


def update_page_token_health(page_id, status, error=None):
    """Persist page token validity and error to the pages table."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update pages
               set token_status = %s,
                   token_error = %s,
                   token_checked_at = now()
               where id = %s""",
            (status, error, page_id),
        )


def rescue_abandoned_posts(page_id=None, reflow=True, include_failed=False):
    """Rescues posts that were marked 'abandoned' or 'failed' (e.g. during an auth/token outage).
    Sets their status back to 'pending', resets attempts to 0, and clears error.
    If reflow=True: intelligently spreads them out into upcoming schedule slots
    after the last currently scheduled post for that page, so they don't flood Facebook."""
    if include_failed:
        where = "where status in ('abandoned', 'failed')"
    else:
        where = "where status = 'abandoned'"
    vals = []
    if page_id is not None:
        where += " and page_id = %s"
        vals.append(page_id)

    with _conn() as c, c.cursor() as cur:
        cur.execute(f"select * from scheduled_posts {where} order by page_id, id", vals)
        abandoned = cur.fetchall()
        if not abandoned:
            return 0

        if not reflow:
            now_plus = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=2)
            cur.execute(
                f"""update scheduled_posts
                    set status = 'pending',
                        attempts = 0,
                        error = null,
                        scheduled_time_utc = %s
                    {where}""",
                [now_plus] + vals,
            )
            return len(abandoned)

        # Reflow: space them out smoothly into future empty slots for each page
        by_page = {}
        for row in abandoned:
            by_page.setdefault(row["page_id"], []).append(row)

        rescued_count = 0
        now_utc = dt.datetime.now(dt.timezone.utc)

        for pid, rows in by_page.items():
            cur.execute("select * from pages where id = %s", (pid,))
            p = cur.fetchone()
            if not p:
                continue

            # Find last scheduled pending time for this page (if any)
            cur.execute(
                """select max(scheduled_time_utc) as last_time
                   from scheduled_posts
                   where page_id = %s and status = 'pending'""",
                (pid,),
            )
            max_r = cur.fetchone()
            last_time = max_r["last_time"] if max_r and max_r["last_time"] else None

            # If last_time is in past or None, start from now + 1 hour
            if not last_time or last_time < now_utc:
                base_time = now_utc + dt.timedelta(hours=1)
            else:
                base_time = last_time + dt.timedelta(minutes=60)

            gap = dt.timedelta(minutes=60)
            slot = base_time

            update_data = []
            for row in rows:
                update_data.append((slot, row["id"]))
                slot += gap

            cur.executemany(
                """update scheduled_posts
                   set status = 'pending',
                       attempts = 0,
                       error = null,
                       scheduled_time_utc = %s
                   where id = %s""",
                update_data,
            )
            rescued_count += len(update_data)

        return rescued_count


def system_health_summary():
    """Rollup of system-wide posting health for dashboard overview & alerts."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select count(*) as total, count(*) filter (where is_active) as active from pages")
        page_counts = cur.fetchone()

        cur.execute("""
            select status, count(*) as count
            from scheduled_posts
            group by status
        """)
        status_counts = {r["status"]: r["count"] for r in cur.fetchall()}

        cur.execute("""
            select count(*) as count
            from pages
            where token_status = 'invalid'
        """)
        invalid_tokens = cur.fetchone()["count"]

        return {
            "total_pages": page_counts["total"],
            "active_pages": page_counts["active"],
            "invalid_tokens": invalid_tokens,
            "pending": status_counts.get("pending", 0),
            "posted": status_counts.get("posted", 0),
            "failed": status_counts.get("failed", 0),
            "abandoned": status_counts.get("abandoned", 0),
            "posting": status_counts.get("posting", 0),
        }


# ---------------- snapchat accounts ----------------
def _snapchat_where(active_only=True, search=None):
    conds = []
    vals = []
    if active_only:
        conds.append("is_active = true")
    if search and search.strip():
        term = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        conds.append("(name ilike %s or snapchat_username ilike %s)")
        vals.extend([f"%{term}%", f"%{term}%"])
    return (" where " + " and ".join(conds) if conds else ""), vals


def list_snapchat_accounts(active_only=True, limit=None, offset=0, search=None):
    where, vals = _snapchat_where(active_only, search)
    q = f"select * from snapchat_accounts{where} order by id"
    if limit is not None:
        q += " limit %s offset %s"
        vals = vals + [limit, offset]
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return cur.fetchall()


def get_snapchat_account(account_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from snapchat_accounts where id = %s", (account_id,))
        return cur.fetchone()


def upsert_snapchat_account(**kw):
    cols = [
        "name", "snapchat_username", "account_type", "public_profile_id",
        "auth_type", "session_data", "drive_folder_id", "default_placement",
        "utc_offset_hours", "schedule_mode", "spread_start_hour", "spread_end_hour",
        "fixed_times", "per_run", "extra_hashtags", "caption_footer", "is_active",
    ]
    if "session_data" in kw and isinstance(kw["session_data"], dict):
        kw["session_data"] = json.dumps(kw["session_data"])

    acc_id = kw.get("id")

    with _conn() as c, c.cursor() as cur:
        if acc_id:
            sets = [f"{k} = %s" for k in cols if k in kw]
            u_vals = [kw[k] for k in cols if k in kw] + [acc_id]
            cur.execute(f"update snapchat_accounts set {', '.join(sets)} where id = %s returning *", u_vals)
            return cur.fetchone()
        else:
            cols_clean = [k for k in cols if k in kw]
            v_clean = [kw[k] for k in cols_clean]
            cur.execute(
                f"""insert into snapchat_accounts ({', '.join(cols_clean)})
                    values ({', '.join(['%s'] * len(cols_clean))})
                    on conflict (snapchat_username) do update
                    set {', '.join([f'{k} = excluded.{k}' for k in cols_clean if k != 'snapchat_username'])}
                    returning *""",
                v_clean,
            )
            return cur.fetchone()


def delete_snapchat_account(account_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from snapchat_accounts where id = %s", (account_id,))


def update_snapchat_account_health(account_id, status, error=None):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update snapchat_accounts
               set token_status = %s,
                   token_error = %s,
                   token_checked_at = now()
               where id = %s""",
            (status, error, account_id),
        )


def list_snapchat_stats():
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from snapchat_account_stats order by account_id")
        return cur.fetchall()


_SNAP_JOB_COLUMNS = """sp.*, ci.media_type, ci.drive_file_id, ci.source_video_id, ci.file_name,
                      ci.caption as content_caption, ci.hashtags as content_hashtags,
                      ci.snapchat_account_id as content_account_id,
                      s.snapchat_username, s.name as account_name, s.auth_type,
                      s.access_token_encrypted, s.refresh_token_encrypted, s.token_expires_at,
                      s.public_profile_id, s.organization_id, s.connection_status,
                      s.default_placement as account_default_placement,
                      s.default_locale as account_default_locale,
                      s.skip_save_to_profile as account_skip_save_to_profile,
                      s.extra_hashtags, s.caption_footer"""


def due_snapchat_posts(now_utc=None, limit=20):
    """Read-only view of what is due. `claim_due_snapchat_posts` is what the
    runner uses — this stays for previews and tests."""
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select {_SNAP_JOB_COLUMNS}
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join snapchat_accounts s on s.id = sp.snapchat_account_id
               where sp.platform = 'snapchat'
                 and sp.status = 'pending'
                 and sp.scheduled_time_utc <= %s
               order by sp.scheduled_time_utc
               limit %s""",
            (now_utc, limit),
        )
        return cur.fetchall()


def claim_due_snapchat_posts(now_utc=None, limit=20):
    """Atomically take ownership of the due Snapchat queue.

    Selecting the rows and marking them 'posting' in one statement, behind
    `for update skip locked`, is what keeps two runners from publishing the
    same video: the Vercel cron and the GitHub Actions job can overlap, and
    a crashed run leaves rows in 'posting' for heal_stuck_posting_posts to
    reclaim rather than in 'pending' for a second worker to pick up mid-upload.
    """
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""with claimed as (
                    select sp.id
                      from scheduled_posts sp
                     where sp.platform = 'snapchat'
                       and sp.status = 'pending'
                       and sp.scheduled_time_utc <= %s
                     order by sp.scheduled_time_utc
                     limit %s
                     for update skip locked
                )
                update scheduled_posts sp
                   set status = 'posting'
                  from claimed
                 where sp.id = claimed.id
             returning sp.id""",
            (now_utc, limit),
        )
        claimed_ids = [r["id"] for r in cur.fetchall()]

    if not claimed_ids:
        return []

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select {_SNAP_JOB_COLUMNS}
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join snapchat_accounts s on s.id = sp.snapchat_account_id
               where sp.id = any(%s)
               order by sp.scheduled_time_utc""",
            (claimed_ids,),
        )
        return cur.fetchall()


def reset_snapchat_post_for_retry(sched_id):
    """Clear a post's failure history so a manual Retry starts clean.

    mark_status() increments `attempts` on every 'failed' write, so it cannot
    also be used to zero the counter — the UPDATE would assign the column
    twice. This is the one place attempts goes backwards, and only because a
    human explicitly asked for another try.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update scheduled_posts
               set status = 'failed', attempts = 0, error = null
               where id = %s and platform = 'snapchat'
               returning *""",
            (sched_id,),
        )
        return cur.fetchone()


def claim_snapchat_post(sched_id, from_statuses=("pending", "failed")):
    """Take one specific post, atomically. Returns the job row or None.

    The retry pass and the "publish now" button both reach for individual
    rows; doing it as a conditional UPDATE means only one caller can win,
    even if a cron run and a dashboard click land in the same second.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update scheduled_posts
               set status = 'posting'
               where id = %s and status = any(%s)
               returning id""",
            (sched_id, list(from_statuses)),
        )
        if not cur.fetchone():
            return None

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select {_SNAP_JOB_COLUMNS}
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join snapchat_accounts s on s.id = sp.snapchat_account_id
               where sp.id = %s""",
            (sched_id,),
        )
        return cur.fetchone()


def release_snapchat_post(sched_id, reason=None):
    """Put a claimed post back on the queue without counting an attempt.

    Used when the account's authorization is broken: the post is fine, the
    credential is not, so it waits for a reconnect instead of burning retries.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update scheduled_posts set status = 'pending', error = %s where id = %s",
            (reason, sched_id),
        )


def retryable_failed_snapchat_posts(cutoff_minutes=30, max_attempts=3, limit=20):
    """Failed Snapchat posts that have cooled down and have attempts left.

    The Facebook equivalent joins `pages`, so Snapchat rows were invisible to
    it and never retried at all.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select {_SNAP_JOB_COLUMNS}
               from scheduled_posts sp
               join content_items ci on ci.id = sp.content_item_id
               join snapchat_accounts s on s.id = sp.snapchat_account_id
               where sp.platform = 'snapchat'
                 and sp.status = 'failed'
                 and sp.attempts < %s
                 and s.is_active = true
                 and s.connection_status = 'connected'
                 and coalesce(sp.updated_at, sp.created_at) < now() - (%s * interval '1 minute')
               order by coalesce(sp.updated_at, sp.created_at)
               limit %s""",
            (max_attempts, cutoff_minutes, limit),
        )
        return cur.fetchall()


def enqueue_snapchat(account_id, content_item_id, scheduled_time_utc, caption_used,
                     placement="spotlight", locale=None, skip_save_to_profile=False):
    """Put one Snapchat publish on the queue at a time the caller chose."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into scheduled_posts
                 (platform, snapchat_account_id, content_item_id, scheduled_time_utc,
                  caption_used, target_placement, snap_locale, snap_skip_save_to_profile)
               values ('snapchat', %s, %s, %s, %s, %s, %s, %s)
               returning *""",
            (account_id, content_item_id, scheduled_time_utc, caption_used,
             placement, locale, bool(skip_save_to_profile)),
        )
        return cur.fetchone()


def list_snapchat_scheduled(account_id=None, status=None, limit=200):
    q = """select sp.*, ci.file_name, ci.media_type, ci.source_video_id,
                  s.name as account_name, s.snapchat_username
           from scheduled_posts sp
           join content_items ci on ci.id = sp.content_item_id
           join snapchat_accounts s on s.id = sp.snapchat_account_id
           where sp.platform = 'snapchat'"""
    vals = []
    if account_id:
        q += " and sp.snapchat_account_id = %s"
        vals.append(account_id)
    if status:
        q += " and sp.status = %s"
        vals.append(status)
    q += " order by sp.scheduled_time_utc limit %s"
    vals.append(limit)
    with _conn() as c, c.cursor() as cur:
        cur.execute(q, vals)
        return cur.fetchall()


def get_scheduled_post(sched_id):
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from scheduled_posts where id = %s", (sched_id,))
        return cur.fetchone()


def already_posted_snapchat(account_id, source_video_id, placement="spotlight"):
    if not source_video_id:
        return False
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select 1 from posted_ledger
               where snapchat_account_id = %s
                 and source_video_id = %s
                 and (target_placement = %s or target_placement is null)
               limit 1""",
            (account_id, source_video_id, placement),
        )
        return cur.fetchone() is not None


def record_snapchat_ledger(account_id, source_video_id, content_item_id, snap_media_id,
                           caption, placement="spotlight", snap_post_id=None):
    """Write the permanent "this went out" row.

    Returns True if this call created the row, False if an identical one was
    already there — migration 010's unique index makes that second case a
    real constraint, so a duplicate publish is refused by the database rather
    than by a preceding SELECT that another worker could race.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into posted_ledger
               (platform, snapchat_account_id, source_video_id, content_item_id,
                snap_media_id, snap_post_id, caption_used, target_placement, posted_at)
               values ('snapchat', %s, %s, %s, %s, %s, %s, %s, now())
               on conflict do nothing
               returning id""",
            (account_id, source_video_id, content_item_id, snap_media_id,
             snap_post_id, caption, placement),
        )
        return cur.fetchone() is not None


def snapchat_available_content(account_id, placement="spotlight", limit=50, offset=0, search=None):
    """Content this Snapchat account can still publish for `placement`.

    Includes items synced for this account plus unassigned page content (the
    cross-post case), and excludes anything already in the ledger for this
    account and placement — the same pair the unique index enforces, so the
    picker cannot offer a video the publish would reject as a duplicate.
    """
    # Values are appended in the order the placeholders appear in the query:
    # the is_scheduled sub-select, then the WHERE clauses, then paging.
    vals = [account_id, placement, account_id]

    conds = ["(ci.snapchat_account_id = %s or ci.snapchat_account_id is null)"]
    if search and search.strip():
        term = (search.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_"))
        conds.append("(ci.caption ilike %s or ci.file_name ilike %s)")
        vals.extend([f"%{term}%", f"%{term}%"])

    vals.extend([account_id, placement, limit, offset])

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select ci.*, p.name as page_name,
                       exists (select 1 from scheduled_posts sp
                                where sp.content_item_id = ci.id
                                  and sp.platform = 'snapchat'
                                  and sp.snapchat_account_id = %s
                                  and sp.target_placement = %s
                                  and sp.status in ('pending','posting')) as is_scheduled
                  from content_items ci
                  left join pages p on p.id = ci.page_id
                 where {' and '.join(conds)}
                   and (ci.source_video_id is null or not exists (
                        select 1 from posted_ledger l
                         where l.snapchat_account_id = %s
                           and l.source_video_id = ci.source_video_id
                           and l.target_placement = %s))
                 order by ci.id desc
                 limit %s offset %s""",
            vals,
        )
        return cur.fetchall()


# ---------------- encrypted credential vault ----------------
def get_app_credential(key):
    """One credential row, ciphertext included. Callers decrypt."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from app_credentials where key = %s", (key,))
        return cur.fetchone()


def list_app_credentials():
    """Every credential row. The ciphertext is stripped by credentials.py
    before anything reaches an API response."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from app_credentials order by key")
        return cur.fetchall()


def upsert_app_credential(key, value_encrypted, description=None):
    """Store or replace a credential.

    rotated_at only moves when the ciphertext actually changes, so it records
    real rotations rather than every idempotent re-save.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into app_credentials (key, value_encrypted, description)
               values (%s, %s, %s)
               on conflict (key) do update
                  set value_encrypted = excluded.value_encrypted,
                      description = coalesce(excluded.description, app_credentials.description),
                      updated_at = now(),
                      rotated_at = case
                          when app_credentials.value_encrypted is distinct from excluded.value_encrypted
                          then now() else app_credentials.rotated_at end
               returning *""",
            (key, value_encrypted, description),
        )
        return cur.fetchone()


def delete_app_credential(key):
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from app_credentials where key = %s returning key", (key,))
        return cur.fetchone() is not None


# ---------------- snapchat oauth, accounts & publishing jobs ----------------
def save_oauth_state(state: str, redirect_uri: str, ttl_seconds: int = 900):
    """Persist a one-time CSRF state for an in-flight authorization."""
    expires_at = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=ttl_seconds)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into snapchat_oauth_states (state, redirect_uri, expires_at)
               values (%s, %s, %s)""",
            (state, redirect_uri, expires_at),
        )


def consume_oauth_state(state: str):
    """Validate and burn a state in one statement.

    Deleting and returning together is what makes the state single-use: two
    concurrent callbacks carrying the same value cannot both find it, so a
    replayed authorization code has nothing to bind to.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """delete from snapchat_oauth_states
               where state = %s and expires_at > now()
               returning *""",
            (state,),
        )
        return cur.fetchone()


def purge_expired_oauth_states():
    """Drop states nobody came back for. Called on each authorize."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from snapchat_oauth_states where expires_at <= now() returning id")
        return len(cur.fetchall())


def update_snapchat_tokens(account_id: int, access_token_encrypted: str,
                           refresh_token_encrypted: str, token_expires_at):
    """Store a refreshed credential pair (already encrypted by the caller)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update snapchat_accounts
               set access_token_encrypted = %s,
                   refresh_token_encrypted = %s,
                   token_expires_at = %s,
                   token_status = 'valid',
                   token_error = null,
                   token_checked_at = now(),
                   connection_status = 'connected'
               where id = %s""",
            (access_token_encrypted, refresh_token_encrypted, token_expires_at, account_id),
        )


def find_snapchat_account_by_profile(public_profile_id: str):
    if not public_profile_id:
        return None
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select * from snapchat_accounts where public_profile_id = %s limit 1",
            (public_profile_id,),
        )
        return cur.fetchone()


def find_snapchat_account_by_username(snapchat_username: str):
    if not snapchat_username:
        return None
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select * from snapchat_accounts where snapchat_username = %s limit 1",
            (snapchat_username,),
        )
        return cur.fetchone()


def upsert_snapchat_oauth_account(
    snapchat_username: str,
    access_token_encrypted: str,
    refresh_token_encrypted: str,
    token_expires_at,
    scope: str = "snapchat-profile-api",
    name: str = None,
    public_profile_id: str = None,
    profile_name: str = None,
    profile_picture_url: str = None,
    organization_id: str = None,
    profile_type: str = None,
):
    """Create or re-connect the account for one Public Profile.

    Identity is the Public Profile ID, not the username: two creators can
    rename themselves into each other's old handle, and reconnecting an
    existing profile must update that row rather than open a second one that
    would then compete for the same schedule. The username lookup is the
    fallback for rows created before a profile ID was known.
    """
    existing = (
        find_snapchat_account_by_profile(public_profile_id)
        or find_snapchat_account_by_username(snapchat_username)
    )

    with _conn() as c, c.cursor() as cur:
        if existing:
            cur.execute(
                """update snapchat_accounts
                   set snapchat_username = coalesce(%s, snapchat_username),
                       profile_name = coalesce(%s, profile_name),
                       profile_picture_url = coalesce(%s, profile_picture_url),
                       public_profile_id = coalesce(%s, public_profile_id),
                       organization_id = coalesce(%s, organization_id),
                       profile_type = coalesce(%s, profile_type),
                       access_token_encrypted = %s,
                       refresh_token_encrypted = %s,
                       token_expires_at = %s,
                       scope = %s,
                       token_status = 'valid',
                       token_error = null,
                       token_checked_at = now(),
                       connection_status = 'connected',
                       connected_at = now(),
                       disconnected_at = null,
                       is_active = true
                   where id = %s
                   returning *""",
                (snapchat_username, profile_name, profile_picture_url, public_profile_id,
                 organization_id, profile_type,
                 access_token_encrypted, refresh_token_encrypted, token_expires_at, scope,
                 existing["id"]),
            )
            return cur.fetchone()

        cur.execute(
            """insert into snapchat_accounts
               (name, snapchat_username, profile_name, profile_picture_url,
                public_profile_id, organization_id, profile_type,
                access_token_encrypted, refresh_token_encrypted, token_expires_at, scope,
                token_status, token_checked_at, connection_status, connected_at, is_active)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                       'valid', now(), 'connected', now(), true)
               returning *""",
            (name or profile_name or f"@{snapchat_username}", snapchat_username,
             profile_name, profile_picture_url, public_profile_id, organization_id,
             profile_type, access_token_encrypted, refresh_token_encrypted,
             token_expires_at, scope),
        )
        return cur.fetchone()


def disconnect_snapchat_account(account_id: int):
    """Revoke locally: wipe the credentials, keep the account and its history.

    Scoped to one row by primary key, so disconnecting one profile cannot
    touch another's tokens. The account's past posts, ledger entries and
    settings survive, and reconnecting restores it in place.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update snapchat_accounts
               set access_token_encrypted = null,
                   refresh_token_encrypted = null,
                   token_expires_at = null,
                   token_status = 'disconnected',
                   token_error = null,
                   token_checked_at = now(),
                   connection_status = 'disconnected',
                   disconnected_at = now()
               where id = %s
               returning *""",
            (account_id,),
        )
        row = cur.fetchone()

    if row:
        # Pending jobs for a disconnected account would fail every run.
        with _conn() as c, c.cursor() as cur:
            cur.execute(
                """update scheduled_posts
                   set status = 'skipped', error = 'Snapchat account disconnected'
                   where platform = 'snapchat'
                     and snapchat_account_id = %s
                     and status in ('pending', 'posting')""",
                (account_id,),
            )
    return row


def mark_snapchat_published(account_id: int):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "update snapchat_accounts set last_publish_at = now() where id = %s",
            (account_id,),
        )


def update_snapchat_profile_metadata(account_id: int, profile: dict):
    """Refresh the cached profile fields from a live my_profile response."""
    if not profile:
        return None
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update snapchat_accounts
               set public_profile_id = coalesce(%s, public_profile_id),
                   profile_name = coalesce(%s, profile_name),
                   organization_id = coalesce(%s, organization_id),
                   profile_type = coalesce(%s, profile_type),
                   snapchat_username = coalesce(%s, snapchat_username)
               where id = %s
               returning *""",
            (profile.get("id"), profile.get("display_name"), profile.get("organization_id"),
             profile.get("profile_type"), profile.get("snap_user_name"), account_id),
        )
        return cur.fetchone()


# ---------------- snapchat publishing records ----------------
def build_idempotency_key(account_id, content_item_id=None, placement="spotlight",
                          scheduled_post_id=None, salt=None):
    """The key that makes a publish happen at most once.

    A scheduled job keys on its schedule row, so every retry of that job
    reuses one record. A manual publish keys on the content item, so double
    clicking "Publish now" cannot produce two Spotlights of the same video.
    """
    if scheduled_post_id:
        return f"sched:{scheduled_post_id}:{placement}"
    if content_item_id:
        return f"acct:{account_id}:item:{content_item_id}:{placement}"
    return f"acct:{account_id}:adhoc:{placement}:{salt or dt.datetime.now(dt.timezone.utc).timestamp()}"


def get_snapchat_publishing_record(record_id: int):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select pr.*, s.name as account_name, s.snapchat_username, s.public_profile_id
               from snapchat_publishing_records pr
               join snapchat_accounts s on s.id = pr.snapchat_account_id
               where pr.id = %s""",
            (record_id,),
        )
        return cur.fetchone()


def get_publishing_record_by_key(idempotency_key: str):
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            "select * from snapchat_publishing_records where idempotency_key = %s",
            (idempotency_key,),
        )
        return cur.fetchone()


def create_snapchat_publishing_record(
    snapchat_account_id: int,
    target_placement: str = "spotlight",
    scheduled_post_id=None,
    content_item_id=None,
    media_id: str = None,
    post_id: str = None,
    status: str = "pending",
    caption: str = "",
    description: str = None,
    locale: str = "en_US",
    skip_save_to_profile: bool = False,
    topics=None,
    error_message: str = None,
    max_attempts: int = 3,
    idempotency_key: str = None,
):
    """Open (or re-open) the publishing job for this unit of work.

    Returns (record, created). When the key already exists the stored record
    comes back with created=False — the caller inspects its status to decide
    whether this is a retry to continue or a completed publish to leave alone.
    """
    key = idempotency_key or build_idempotency_key(
        snapchat_account_id, content_item_id, target_placement, scheduled_post_id
    )

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into snapchat_publishing_records
               (scheduled_post_id, snapchat_account_id, content_item_id, target_placement,
                media_id, post_id, status, caption, description, locale,
                skip_save_to_profile, topics, error_message, max_attempts, idempotency_key)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (idempotency_key) do nothing
               returning *""",
            (scheduled_post_id, snapchat_account_id, content_item_id, target_placement,
             media_id, post_id, status, caption, description, locale,
             bool(skip_save_to_profile), topics or [], error_message, max_attempts, key),
        )
        row = cur.fetchone()

    if row:
        return row, True
    return get_publishing_record_by_key(key), False


_PUB_RECORD_FIELDS = (
    "media_id", "post_id", "post_id_kind", "request_id", "publish_id", "stage",
    "description", "locale", "skip_save_to_profile", "topics", "caption",
    "error_message", "error_code", "error_kind", "next_retry_at",
    "media_duration_seconds", "media_width", "media_height", "media_size_bytes",
)


def update_snapchat_publishing_record(record_id: int, status: str = None,
                                      increment_attempt: bool = False, **fields):
    """Patch a publishing record. Only known columns are accepted."""
    sets = ["updated_at = now()"]
    vals = []

    if status:
        sets.append("status = %s")
        vals.append(status)
        if status == "published":
            sets.append("published_at = now()")

    if increment_attempt:
        sets.append("attempts = coalesce(attempts, 0) + 1")
        sets.append("last_attempt_at = now()")

    for key, value in fields.items():
        if key not in _PUB_RECORD_FIELDS:
            continue
        sets.append(f"{key} = %s")
        vals.append(value)

    vals.append(record_id)
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"update snapchat_publishing_records set {', '.join(sets)} where id = %s returning *",
            vals,
        )
        return cur.fetchone()


def list_snapchat_publishing_records(account_id=None, status=None, limit: int = 20):
    conds = []
    vals = []
    if account_id:
        conds.append("pr.snapchat_account_id = %s")
        vals.append(account_id)
    if status:
        conds.append("pr.status = %s")
        vals.append(status)
    where = ("where " + " and ".join(conds)) if conds else ""
    vals.append(limit)

    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""select pr.*, s.name as account_name, s.snapchat_username,
                       ci.file_name, ci.source_video_id
                from snapchat_publishing_records pr
                join snapchat_accounts s on s.id = pr.snapchat_account_id
                left join content_items ci on ci.id = pr.content_item_id
                {where}
                order by pr.created_at desc
                limit %s""",
            vals,
        )
        return cur.fetchall()


def retryable_snapchat_publishing_records(limit: int = 20):
    """Failed jobs whose cooldown has passed and whose retries are not spent."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select pr.*, s.name as account_name, s.snapchat_username
               from snapchat_publishing_records pr
               join snapchat_accounts s on s.id = pr.snapchat_account_id
               where pr.status = 'failed'
                 and coalesce(pr.attempts, 0) < coalesce(pr.max_attempts, 3)
                 and (pr.next_retry_at is null or pr.next_retry_at <= now())
                 and s.connection_status = 'connected'
               order by pr.updated_at
               limit %s""",
            (limit,),
        )
        return cur.fetchall()


def heal_stuck_snapchat_records(max_age_minutes: int = 30):
    """Reclaim jobs a crashed worker left mid-upload.

    A run that dies between "creating_media" and "publishing" leaves the
    record in a working state forever. Flipping it to 'failed' hands it to
    the retry path; the idempotency key keeps that retry from becoming a
    second post if the original publish had in fact landed.
    """
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update snapchat_publishing_records
               set status = 'failed',
                   error_message = coalesce(error_message,
                       'Worker stopped mid-publish; recovered by the scheduler'),
                   error_kind = coalesce(error_kind, 'SnapTransientError'),
                   updated_at = now()
               where status in ('pending','validating','uploading','finalizing','publishing')
                 and updated_at < now() - (%s * interval '1 minute')
               returning id""",
            (max_age_minutes,),
        )
        return len(cur.fetchall())


# ============================================================
# Google Flow Accounts & Generation Jobs (Migration 015)
# ============================================================

def list_flow_accounts(active_only=False):
    """List all registered Google Flow accounts and their credit balances."""
    with _conn() as c, c.cursor() as cur:
        query = "select * from flow_accounts"
        if active_only:
            query += " where is_active = true"
        query += " order by id asc"
        cur.execute(query)
        return cur.fetchall()


def get_flow_account(account_id: int):
    """Retrieve a single Google Flow account by ID."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from flow_accounts where id = %s", (account_id,))
        return cur.fetchone()


def get_best_available_flow_account():
    """Returns the active account with the highest remaining credits (> 15)."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select * from flow_accounts
               where is_active = true and credits_remaining >= 15
               order by credits_remaining desc, last_used_at asc nulls first
               limit 1"""
        )
        return cur.fetchone()


def add_flow_account(label: str, email: str = None, profile_folder: str = None, credits_total: int = 1000):
    """Register a new Google Flow profile account."""
    if not profile_folder:
        clean_lbl = "".join(ch for ch in (label or "acc") if ch.isalnum() or ch == "_").lower()
        profile_folder = f"{clean_lbl}_{int(dt.datetime.now(dt.timezone.utc).timestamp())}"
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into flow_accounts (label, email, profile_folder, credits_remaining, credits_total)
               values (%s, %s, %s, %s, %s)
               returning *""",
            (label.strip(), (email or "").strip(), profile_folder.strip(), credits_total, credits_total)
        )
        c.commit()
        return cur.fetchone()


def update_flow_account(account_id: int, **fields):
    """Update metadata or active status for a Flow account."""
    if not fields:
        return get_flow_account(account_id)
    fields["updated_at"] = dt.datetime.now(dt.timezone.utc)
    cols = [f"{k} = %s" for k in fields.keys()]
    vals = list(fields.values()) + [account_id]
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"update flow_accounts set {', '.join(cols)} where id = %s returning *", vals)
        c.commit()
        return cur.fetchone()


def update_flow_credits(account_id: int, credits_remaining: int):
    """Update live credit balance scraped from Google Flow interface."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """update flow_accounts
               set credits_remaining = %s,
                   last_used_at = now(),
                   updated_at = now()
               where id = %s
               returning *""",
            (credits_remaining, account_id)
        )
        c.commit()
        return cur.fetchone()


def delete_flow_account(account_id: int):
    """Delete a Google Flow account registration."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("delete from flow_accounts where id = %s returning id", (account_id,))
        c.commit()
        return bool(cur.fetchone())


def create_flow_job(
    account_id: int,
    target_page_id: int,
    media_type: str,
    niche: str,
    duration_seconds: int,
    scene_count: int,
    headline: str,
    prompt_used: str,
    caption: str,
    hashtags: list,
    credit_cost: int = 15,
    drive_folder_id: str = None,
):
    """Record a video or image generation job in the queue."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """insert into flow_jobs (
                   account_id, target_page_id, media_type, niche, duration_seconds,
                   scene_count, headline, prompt_used, caption, hashtags, credit_cost,
                   drive_folder_id, status
               ) values (
                   %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'queued'
               ) returning *""",
            (
                account_id, target_page_id, media_type or "reel", niche, duration_seconds or 10,
                scene_count or 1, headline, prompt_used, caption or "", hashtags or [], credit_cost,
                drive_folder_id
            )
        )
        c.commit()
        return cur.fetchone()


def get_flow_job(job_id: int):
    """Retrieve a single flow job with joined account and page info."""
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            """select j.*, a.label as account_label, p.name as target_page_name
               from flow_jobs j
               left join flow_accounts a on a.id = j.account_id
               left join pages p on p.id = j.target_page_id
               where j.id = %s""",
            (job_id,)
        )
        return cur.fetchone()


def insert_content_item(page_id, media_type, source_video_id, drive_file_id, file_name, caption="", hashtags=None, source_folder="direct"):
    """Compatibility alias for create_content_item."""
    return create_content_item(page_id, media_type, source_video_id, drive_file_id, file_name, caption=caption, hashtags=hashtags, source_folder=source_folder)


def list_flow_jobs(limit: int = 50, offset: int = 0, status: str = None):
    """List generation jobs with pagination and optional status filter."""
    with _conn() as c, c.cursor() as cur:
        where = "where status = %s" if status else ""
        params = [status] if status else []
        params.extend([limit, offset])
        cur.execute(
            f"""select j.*, a.label as account_label, p.name as target_page_name
               from flow_jobs j
               left join flow_accounts a on a.id = j.account_id
               left join pages p on p.id = j.target_page_id
               {where}
               order by j.id desc
               limit %s offset %s""",
            params
        )
        rows = cur.fetchall()

        count_query = f"select count(*) as total from flow_jobs {where}"
        cur.execute(count_query, [status] if status else [])
        total = cur.fetchone()["total"]
        return {"jobs": rows, "total": total}


def update_flow_job(job_id: int, **fields):
    """Update job state (generating, stitching, completed, failed, filepaths, etc.)."""
    if not fields:
        return None
    fields["updated_at"] = dt.datetime.now(dt.timezone.utc)
    cols = [f"{k} = %s" for k in fields.keys()]
    vals = list(fields.values()) + [job_id]
    with _conn() as c, c.cursor() as cur:
        cur.execute(f"update flow_jobs set {', '.join(cols)} where id = %s returning *", vals)
        c.commit()
        return cur.fetchone()


def get_flow_settings():
    """Retrieve saved user presets for the Content Machine."""
    with _conn() as c, c.cursor() as cur:
        cur.execute("select * from flow_settings where id = 'default'")
        row = cur.fetchone()
        if not row:
            cur.execute("insert into flow_settings (id) values ('default') returning *")
            c.commit()
            row = cur.fetchone()
        return row


def save_flow_settings(**settings):
    """Save user presets into flow_settings."""
    if not settings:
        return get_flow_settings()
    settings["updated_at"] = dt.datetime.now(dt.timezone.utc)
    cols = [f"{k} = %s" for k in settings.keys()]
    vals = list(settings.values())
    with _conn() as c, c.cursor() as cur:
        cur.execute(
            f"""insert into flow_settings (id, {', '.join(settings.keys())})
               values ('default', {', '.join(['%s']*len(settings))})
               on conflict (id) do update set {', '.join(cols)}
               returning *""",
            vals + vals
        )
        c.commit()
        return cur.fetchone()

