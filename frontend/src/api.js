// api.js — talks to Supabase directly from the browser.
// Set VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY in your .env (or Vercel env vars).
import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;
const BACKEND_URL = import.meta.env.VITE_API_URL ?? "";

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn(
    "Missing VITE_SUPABASE_URL or VITE_SUPABASE_ANON_KEY. " +
      "Add them to frontend/.env.local for development."
  );
}

const supabase = createClient(supabaseUrl, supabaseAnonKey);

// ─── helpers ────────────────────────────────────────────────────────────────

/** Throw a real Error from a Supabase response if it has an error. */
function check({ data, error }) {
  if (error) throw new Error(error.message);
  return data;
}

/** Same, but also hands back the exact-count PostgREST returns alongside. */
function checkWithCount({ data, error, count }) {
  if (error) throw new Error(error.message);
  return { data, count: count ?? 0 };
}

/**
 * Escape a user-typed search term for a PostgREST `ilike` filter: LIKE
 * wildcards become literals, and the value is wrapped in double quotes so
 * commas / parentheses don't break `.or(...)` parsing.
 */
function ilikeArg(term) {
  const escaped = String(term)
    .replace(/\\/g, "\\\\")
    .replace(/%/g, "\\%")
    .replace(/_/g, "\\_")
    .replace(/"/g, "");
  return `"%${escaped}%"`;
}

async function backendFetch(path, options) {
  const res = await fetch(`${BACKEND_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const j = await res.json().catch(() => ({}));
    const err = new Error(j.detail || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

/** True if a Supabase/PostgREST error looks like a missing migration column. */
function isMigrationError(e) {
  return /does not exist|schema cache/i.test(e?.message || "");
}

function findTags(text) {
  return (text || "").match(/#\w+/g) || [];
}

/**
 * Mirrors backend/poster.py's build_caption() exactly, so a caption preview
 * shown in the browser always matches what actually gets posted. Both sides
 * must stay in sync if the format ever changes.
 *
 * hashtagPool (optional): the page's rotating pool. The video's own tags and
 * the page's extra_hashtags are always included first; a RANDOM sample of
 * the pool fills the remaining slots up to 8 total. Because this draws
 * randomly, call it once and freeze the result if you need the preview
 * shown to a user to match exactly what gets persisted later.
 */
export function buildCaption(captionText, hashtags, extraHashtags, footer, hashtagPool) {
  captionText = (captionText || "").trim();
  hashtags = hashtags || [];
  extraHashtags = extraHashtags || [];
  hashtagPool = hashtagPool || [];

  const existing = new Set(findTags(captionText).map((h) => h.toLowerCase()));
  const tags = [];
  const add = (h) => {
    h = h.startsWith("#") ? h : "#" + h;
    if (!existing.has(h.toLowerCase()) && !tags.includes(h)) {
      tags.push(h);
      existing.add(h.toLowerCase());
    }
  };
  for (const h of [...hashtags, ...extraHashtags]) add(h);

  if (hashtagPool.length) {
    const remaining = Math.max(0, 8 - tags.length);
    if (remaining > 0) {
      const candidates = hashtagPool.filter(
        (h) => !existing.has((h.startsWith("#") ? h : "#" + h).toLowerCase())
      );
      for (let i = candidates.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [candidates[i], candidates[j]] = [candidates[j], candidates[i]];
      }
      for (const h of candidates.slice(0, remaining)) add(h);
    }
  }

  const parts = captionText ? [captionText] : [];
  if (tags.length) parts.push(tags.join(" "));
  if (footer && footer.trim()) parts.push(footer.trim());
  return parts.join("\n\n").trim();
}

// ─── API ────────────────────────────────────────────────────────────────────

export const api = {
  // ── Pages ────────────────────────────────────────────────────────────────

  /**
   * List all active pages, joined with per-page stats from the page_stats
   * VIEW — two queries total, no matter how many pages there are. The counts
   * are aggregate COUNTs computed inside Postgres; content and schedule rows
   * are never fetched here to be counted in the browser.
   *
   * Each page carries its counts BOTH nested under `stats` (what the
   * components read, and what the backend's /api/pages returns) and spread
   * flat, so either access style works.
   */
  /**
   * List pages with their per-page stats.
   *
   * Goes through the backend rather than straight to Postgres. The `pages`
   * row carries the long-lived Facebook Page token, and the anon key this
   * client holds is public — it ships in the built bundle. The backend
   * strips both the token and its ciphertext before responding, and
   * migration 012 revokes the column from `anon` so this cannot regress to
   * a direct read.
   *
   * Counts are returned nested under `stats` and spread flat, because
   * components read both shapes.
   */
  pages: async () => {
    const rows = await backendFetch("/api/pages");
    const list = Array.isArray(rows) ? rows : rows.items || [];
    return list.map((p) => ({ ...p, ...(p.stats || {}), stats: p.stats || {} }));
  },

  /**
   * Create or update a page (upsert on fb_page_id).
   *
   * The token is written server-side: it is encrypted at rest with a key the
   * browser does not have, so this cannot be a direct table write.
   */
  addPage: async (p) => {
    const cols = [
      "name", "fb_page_id", "fb_access_token", "drive_folder_id",
      "utc_offset_hours", "schedule_mode", "spread_start_hour",
      "spread_end_hour", "fixed_times", "per_run", "extra_hashtags",
      "caption_footer", "is_active", "hashtag_pool",
    ];
    const row = Object.fromEntries(cols.filter((k) => k in p).map((k) => [k, p[k]]));
    return backendFetch("/api/pages", {
      method: "POST",
      body: JSON.stringify(row),
    });
  },

  /** Update an existing page by id. */
  updatePage: async (id, p) => {
    const cols = [
      "name", "fb_page_id", "fb_access_token", "drive_folder_id",
      "utc_offset_hours", "schedule_mode", "spread_start_hour",
      "spread_end_hour", "fixed_times", "per_run", "extra_hashtags",
      "caption_footer", "is_active", "hashtag_pool",
    ];
    const row = Object.fromEntries(cols.filter((k) => k in p).map((k) => [k, p[k]]));
    return backendFetch(`/api/pages/${id}`, {
      method: "PUT",
      body: JSON.stringify(row),
    });
  },

  /** Remove a page. */
  deletePage: async (id) => {
    await backendFetch(`/api/pages/${id}`, { method: "DELETE" });
    return null;
  },

  /**
   * syncPage — Drive sync requires a backend call (service-account auth).
   * Starts the sync in the background and returns instantly; poll
   * syncStatus() for progress.
   */
  syncPage: (id) => backendFetch(`/api/pages/${id}/sync`, { method: "POST" }),

  /** Poll progress of a background sync: {status: "running"|"complete"|"idle", found, added}. */
  syncStatus: (id) => backendFetch(`/api/pages/${id}/sync/status`),

  /**
   * Force-clear a stuck sync lock (e.g. a crashed/incomplete sync from a
   * prior deploy) without waiting for the 10-minute staleness auto-expiry.
   */
  resetSync: (id) => backendFetch(`/api/pages/${id}/sync/reset`, { method: "POST" }),

  /**
   * Live page health: Facebook token validity, queue depth, days of
   * runway left, last posted/failed times. Backend-only (needs a live
   * call to the Graph API).
   */
  pageHealth: (id) => backendFetch(`/api/pages/${id}/health`),

  /** Scheduled posts for one local calendar week (Mon-Sun), any status. */
  calendar: (id, weekStartDate) =>
    backendFetch(`/api/pages/${id}/calendar?week_start=${weekStartDate}`),

  /**
   * Auto-schedule every unposted item for a page at once. Pass
   * { dry_run: true } to preview counts/dates without writing anything.
   * body: { posts_per_day, start_date: "YYYY-MM-DD", post_times: ["09:00", ...], dry_run? }
   */
  autoSchedule: (page_id, body) =>
    backendFetch(`/api/pages/${page_id}/auto-schedule`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /**
   * Mode B of the intensity selector: the page already has pending posts,
   * so this surgically adjusts the existing schedule to a new posts/day
   * target instead of rebuilding it. Pass { dry_run: true } to preview the
   * add/cancel counts first.
   * body: { posts_per_day, dry_run? }
   */
  updateIntensity: (page_id, body) =>
    backendFetch(`/api/pages/${page_id}/update-intensity`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** Immediately post a content item to Facebook (synchronous, up to ~60 s). */
  postNow: async (content_item_id) => {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 120_000);
    try {
      const res = await fetch(
        `${BACKEND_URL}/api/content/${content_item_id}/post-now`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          signal: controller.signal,
        }
      );
      if (!res.ok) {
        const j = await res.json().catch(() => ({}));
        throw new Error(j.detail || `Request failed (${res.status})`);
      }
      return res.json();
    } catch (e) {
      if (e.name === "AbortError") throw new Error("Request timed out after 120 seconds.");
      throw e;
    } finally {
      clearTimeout(timer);
    }
  },

  /** Delete content item(s) by ID, cascading to publications and media assets. */
  deleteContent: (ids) =>
    backendFetch("/api/content/batch-delete", {
      method: "POST",
      body: JSON.stringify({ ids: Array.isArray(ids) ? ids : [ids] }),
    }),

  // ── Content ───────────────────────────────────────────────────────────────

  /**
   * ONE page of a page's content library, annotated with posted / scheduled
   * status. Never fetches a whole library — a Drive folder can hold thousands
   * of videos, and pulling all of them into the browser is what breaks first
   * at scale.
   *
   * opts: { limit = 50, offset = 0, search } — search matches caption or file
   * name, case-insensitively, filtered SERVER-SIDE by PostgREST.
   * Returns { items, total }, where total counts everything matching the same
   * search (for "Showing 1–50 of 756").
   *
   * The backend's GET /api/pages/{id}/content?limit&offset&search is the same
   * query with the same contract, for API clients and as a fallback. This
   * path goes straight to Postgres so browsing the library never waits on a
   * Render cold start; keep the two in step if the filter ever changes.
   */
  content: async (pageId, opts = {}) => {
    const limit = opts.limit ?? 50;
    const offset = opts.offset ?? 0;
    const search = (opts.search || "").trim();
    const sourceFolder = opts.sourceFolder || "";

    let q = supabase
      .from("content_items")
      .select("*", { count: "exact" })
      .eq("page_id", pageId);
    if (search) {
      const arg = ilikeArg(search);
      q = q.or(`caption.ilike.${arg},file_name.ilike.${arg}`);
    }
    if (sourceFolder) {
      q = q.eq("source_folder", sourceFolder);
    }
    const { data: items, count } = checkWithCount(
      await q.order("id").range(offset, offset + limit - 1)
    );

    const total = count ?? 0;
    if (!items.length) return { items: [], total };

    const ids = items.map((i) => i.id);
    const videoIds = items.map((i) => i.source_video_id).filter(Boolean);

    // Check posted ledger
    const ledger = check(
      await supabase
        .from("posted_ledger")
        .select("source_video_id")
        .eq("page_id", pageId)
        .in("source_video_id", videoIds)
    );
    const postedSet = new Set(ledger.map((l) => l.source_video_id));

    // Check pending scheduled times
    const scheduled = check(
      await supabase
        .from("scheduled_posts")
        .select("content_item_id, scheduled_time_utc")
        .in("content_item_id", ids)
        .eq("status", "pending")
        .order("scheduled_time_utc")
    );
    // Pick the earliest scheduled time per content item
    const schedMap = {};
    for (const s of scheduled) {
      if (!schedMap[s.content_item_id]) schedMap[s.content_item_id] = s.scheduled_time_utc;
    }

    return {
      items: items.map((ci) => ({
        ...ci,
        is_posted: postedSet.has(ci.source_video_id),
        scheduled_time_utc: schedMap[ci.id] || null,
      })),
      total,
    };
  },

  /**
   * Distinct source_folder values in a page's library (from the
   * content_source_folders view — migration 003), for the Content page's
   * "Source Folder" filter dropdown. Best-effort: returns [] pre-migration.
   */
  sourceFolders: async (pageId) => {
    try {
      const rows = check(
        await supabase
          .from("content_source_folders")
          .select("source_folder")
          .eq("page_id", pageId)
          .order("source_folder")
      );
      return rows.map((r) => r.source_folder);
    } catch (e) {
      if (isMigrationError(e)) return [];
      throw e;
    }
  },

  /**
   * "Clear Posted History" for a page: a full reset. Deletes its
   * posted_ledger rows AND every scheduled_posts row (pending included) and
   * forgets the page's remembered auto-schedule pattern, so the whole video
   * library reports as available again and the pending queue reads 0.
   * content_items rows themselves are never touched — the video library
   * itself is untouched, just no longer marked posted/queued.
   * Returns { deleted, cleared_scheduled }.
   */
  clearPostedHistory: (pageId) =>
    backendFetch(`/api/pages/${pageId}/posted-history`, { method: "DELETE" }),

  /**
   * "Mark as Unposted" bulk action: resets specific content items back to
   * unposted by removing their posted_ledger entry (and any lingering
   * status='posted' scheduled_posts row) — without touching any other
   * posted history for the page. Returns { unmarked }.
   */
  markUnposted: async (contentItemIds) => {
    if (!contentItemIds?.length) return { unmarked: 0 };

    const items = check(
      await supabase
        .from("content_items")
        .select("id, page_id, source_video_id")
        .in("id", contentItemIds)
    );

    const byPage = new Map();
    for (const it of items) {
      if (!byPage.has(it.page_id)) byPage.set(it.page_id, []);
      byPage.get(it.page_id).push(it.source_video_id);
    }

    let unmarked = 0;
    for (const [pageId, videoIds] of byPage) {
      const deletedRows = check(
        await supabase
          .from("posted_ledger")
          .delete()
          .eq("page_id", pageId)
          .in("source_video_id", videoIds)
          .select("id")
      );
      unmarked += deletedRows.length;
    }

    check(
      await supabase
        .from("scheduled_posts")
        .delete()
        .in("content_item_id", contentItemIds)
        .eq("status", "posted")
    );

    return { unmarked };
  },

  // ── Schedule ──────────────────────────────────────────────────────────────

  /**
   * List scheduled posts, optionally filtered.
   * params: a query string like "?page_id=1&status=pending"
   *         or an object { page_id, status }
   */
  schedule: async (params = "") => {
    let query = supabase
      .from("scheduled_posts")
      .select(`
        *,
        content_items ( file_name, media_type, source_video_id, caption ),
        pages ( name )
      `)
      .order("scheduled_time_utc");

    // Accept either a URLSearchParams string or a plain object
    const p =
      typeof params === "string"
        ? Object.fromEntries(new URLSearchParams(params.replace(/^\?/, "")))
        : params;

    if (p.page_id) query = query.eq("page_id", p.page_id);
    if (p.status)  query = query.eq("status",  p.status);

    const rows = check(await query);
    // Flatten joined fields to match the old backend shape
    return rows.map((r) => ({
      ...r,
      file_name:       r.content_items?.file_name,
      media_type:      r.content_items?.media_type,
      source_video_id: r.content_items?.source_video_id,
      caption:         r.content_items?.caption,
      page_name:       r.pages?.name,
    }));
  },

  /**
   * Queue one content item at a specific UTC time. Pass captionOverride to
   * use an exact, already-built caption (e.g. one already shown as a
   * preview to the user) instead of rebuilding — rebuilding would draw a
   * different random hashtag-pool sample than whatever was previewed.
   */
  scheduleOne: async (content_item_id, scheduled_time_utc, captionOverride) => {
    const item = check(
      await supabase
        .from("content_items")
        .select("page_id, caption, hashtags")
        .eq("id", content_item_id)
        .maybeSingle()
    );

    let caption_used = captionOverride;
    if (caption_used == null) {
      let page;
      try {
        page = check(
          await supabase
            .from("pages")
            .select("extra_hashtags, caption_footer, hashtag_pool")
            .eq("id", item.page_id)
            .maybeSingle()
        );
      } catch (e) {
        if (!isMigrationError(e)) throw e;
        page = check(
          await supabase
            .from("pages")
            .select("extra_hashtags, caption_footer")
            .eq("id", item.page_id)
            .maybeSingle()
        );
        if (page) page.hashtag_pool = [];
      }
      caption_used = buildCaption(item?.caption, item?.hashtags, page?.extra_hashtags, page?.caption_footer, page?.hashtag_pool);
    }

    return check(
      await supabase
        .from("scheduled_posts")
        .insert({ page_id: item.page_id, content_item_id, scheduled_time_utc, caption_used })
        .select()
        .maybeSingle()
    );
  },

  /** Move a pending scheduled post to a new time. */
  reschedule: async (id, new_time_utc) =>
    check(
      await supabase
        .from("scheduled_posts")
        .update({ scheduled_time_utc: new_time_utc })
        .eq("id", id)
        .eq("status", "pending")
        .select()
        .maybeSingle()
    ),

  /** Cancel (delete) a pending scheduled post. */
  cancel: async (id) => {
    check(
      await supabase
        .from("scheduled_posts")
        .delete()
        .eq("id", id)
        .eq("status", "pending")
    );
    return null;
  },

  /**
   * Rebuild caption_used for every pending post on a page from the current
   * content + page data. Backend-only: it reuses the exact same
   * build_caption() the poster uses, so a refreshed caption is byte-for-byte
   * what would go out. Returns { refreshed, checked }.
   */
  refreshPendingCaptions: (pageId) =>
    backendFetch(`/api/pages/${pageId}/refresh-pending-captions`, { method: "POST" }),

  /**
   * Cancel (hard delete) all pending scheduled posts for a page. Routed
   * through the backend so it can also forget the page's remembered
   * auto-schedule pattern — otherwise the periodic auto-refill pass in
   * /api/run would just repopulate the queue on its next run.
   */
  cancelAll: (pageId) =>
    backendFetch(`/api/pages/${pageId}/schedule/all`, { method: "DELETE" }),

  /** Retry an abandoned post: put it back to pending, ~2 minutes from now. */
  retryAbandoned: async (id) =>
    check(
      await supabase
        .from("scheduled_posts")
        .update({
          status: "pending",
          scheduled_time_utc: new Date(Date.now() + 2 * 60_000).toISOString(),
          error: null,
        })
        .eq("id", id)
        .eq("status", "abandoned")
        .select()
        .maybeSingle()
    ),

  /** Dismiss (delete) an abandoned post without retrying it. */
  dismissAbandoned: async (id) => {
    check(
      await supabase
        .from("scheduled_posts")
        .delete()
        .eq("id", id)
        .eq("status", "abandoned")
    );
    return null;
  },

  /**
   * Suggest N posting times for a page based on its schedule config.
   * (Pure client-side calculation — no backend needed.)
   */
  suggest: async (page_id, count = 6) => {
    // Named columns, not select("*"): migration 012 revokes fb_access_token
    // from the anon role, and a wildcard select over a denied column is
    // rejected outright by PostgREST.
    const page = check(
      await supabase
        .from("pages")
        .select("utc_offset_hours, schedule_mode, fixed_times, spread_start_hour, spread_end_hour")
        .eq("id", page_id)
        .maybeSingle()
    );

    const slots = [];
    const now = new Date();
    const offsetMs = (page.utc_offset_hours || 0) * 3600_000;

    if (page.schedule_mode === "fixed" && page.fixed_times?.length) {
      // Walk forward day by day until we have `count` future slots
      for (let d = 0; slots.length < count && d < 14; d++) {
        const base = new Date(now.getTime() + d * 86_400_000);
        for (const t of page.fixed_times) {
          const [h, m] = t.split(":").map(Number);
          const candidate = new Date(
            Date.UTC(base.getUTCFullYear(), base.getUTCMonth(), base.getUTCDate(), h, m) - offsetMs
          );
          if (candidate > now) slots.push(candidate.toISOString());
          if (slots.length >= count) break;
        }
      }
    } else {
      // Spread mode: evenly distribute across [spread_start_hour, spread_end_hour]
      const start = page.spread_start_hour ?? 9;
      const end   = page.spread_end_hour   ?? 21;
      const range = (end - start) * 3600_000;
      const step  = range / count;
      for (let i = 0; i < count; i++) {
        const base = new Date(now);
        base.setUTCHours(start, 0, 0, 0);
        const candidate = new Date(base.getTime() + i * step - offsetMs);
        if (candidate <= now) candidate.setDate(candidate.getDate() + 1);
        slots.push(candidate.toISOString());
      }
    }

    return { suggested_times: slots };
  },

  // ── Analytics ─────────────────────────────────────────────────────────────

  /** Analytics: uses backend which falls back to posted_ledger when no snapshots exist. */
  analytics: (pageId) => backendFetch(`/api/pages/${pageId}/analytics`),

  /** Trigger a fresh pull of Facebook insights for all posted items. */
  refreshAnalytics: () =>
    backendFetch("/api/analytics/refresh", { method: "POST" }),

  /** All pending posts scheduled for today (UTC) across all pages. */
  todayScheduled: async () => {
    const now = new Date();
    const todayStart = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate())).toISOString();
    const todayEnd   = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1)).toISOString();
    const rows = check(
      await supabase
        .from("scheduled_posts")
        .select("*, content_items(file_name, media_type), pages(name)")
        .eq("status", "pending")
        .gte("scheduled_time_utc", todayStart)
        .lt("scheduled_time_utc", todayEnd)
        .order("scheduled_time_utc")
    );
    return rows.map((r) => ({
      ...r,
      file_name: r.content_items?.file_name,
      media_type: r.content_items?.media_type,
      page_name:  r.pages?.name,
    }));
  },

  /** Last 5 activity_log entries (best-effort — table may not exist pre-migration). */
  recentActivity: async () => {
    try {
      const rows = check(
        await supabase
          .from("activity_log")
          .select("*, pages(name)")
          .order("created_at", { ascending: false })
          .limit(5)
      );
      return rows.map((r) => ({ ...r, page_name: r.pages?.name }));
    } catch {
      return [];
    }
  },

  /** Live test a page access token with Facebook Graph API */
  testPageToken: (pageId, token, fb_page_id) =>
    backendFetch(pageId ? `/api/pages/${pageId}/test-token` : "/api/pages/test-token", {
      method: "POST",
      body: JSON.stringify({ token, fb_page_id }),
    }),

  /** Bulk rescue abandoned posts and reflow them into the schedule */
  rescueAbandoned: (pageId = null, reflow = true) =>
    backendFetch("/api/schedule/rescue-abandoned", {
      method: "POST",
      body: JSON.stringify({ page_id: pageId, reflow }),
    }),

  /** System-wide health rollup */
  systemHealth: () => backendFetch("/api/system/health-summary"),

  // ============ Snapchat Public Profile API (official) ============
  // Every one of these is server-side: the browser never sees a Snapchat
  // client secret, access token or refresh token. Connecting is a full-page
  // redirect to accounts.snapchat.com, and the callback lands on the backend.

  /** Integration readiness: which env vars are set, the exact redirect URI,
   *  the scope, and Snap's media rules. Booleans only, never the secret. */
  snapchatConfig: () => backendFetch("/api/snapchat/config"),

  /** Ask the backend for an authorization URL (it stores the CSRF state). */
  snapchatOAuthAuthorize: (redirectUri = null) =>
    backendFetch(
      `/api/snapchat/oauth/authorize${
        redirectUri ? `?redirect_uri=${encodeURIComponent(redirectUri)}` : ""
      }`
    ),

  /** Connected Public Profiles. */
  snapchatAccounts: () => backendFetch("/api/snapchat/accounts"),

  snapchatAccount: (id) => backendFetch(`/api/snapchat/accounts/${id}`),

  /** Live connection check against Snapchat (refreshes the token if needed). */
  snapchatAccountHealth: (id) => backendFetch(`/api/snapchat/accounts/${id}/health`),

  updateSnapchatAccount: (id, data) =>
    backendFetch(`/api/snapchat/accounts/${id}`, {
      method: "PATCH",
      body: JSON.stringify(data),
    }),

  /** Force a token refresh. Returns status only. */
  refreshSnapchatToken: (id) =>
    backendFetch(`/api/snapchat/accounts/${id}/refresh-token`, { method: "POST" }),

  /** Disconnect: wipes this account's credentials, keeps its history.
   *  Other connected accounts are untouched. */
  disconnectSnapchatAccount: (id) =>
    backendFetch(`/api/snapchat/accounts/${id}/disconnect`, { method: "POST" }),

  /** Delete the account outright, with its content and queue. */
  deleteSnapchatAccount: (id) =>
    backendFetch(`/api/snapchat/accounts/${id}`, { method: "DELETE" }),

  /** Content this account can still publish for a placement. */
  snapchatAccountContent: (id, { placement = "spotlight", search = "", limit = 50, offset = 0 } = {}) =>
    backendFetch(
      `/api/snapchat/accounts/${id}/content?placement=${placement}&limit=${limit}&offset=${offset}` +
        (search ? `&search=${encodeURIComponent(search)}` : "")
    ),

  /** Publish a content item to Spotlight or the Public Story right now. */
  publishSnapchatNow: (id, payload) =>
    backendFetch(`/api/snapchat/accounts/${id}/publish`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /** The Snapchat queue. */
  snapchatJobs: (accountId = null, status = null) => {
    const q = new URLSearchParams();
    if (accountId) q.set("account_id", accountId);
    if (status) q.set("status", status);
    const qs = q.toString();
    return backendFetch(`/api/snapchat/jobs${qs ? `?${qs}` : ""}`);
  },

  /** Queue one publish at a time you choose. */
  createSnapchatJob: (payload) =>
    backendFetch("/api/snapchat/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  cancelSnapchatJob: (schedId) =>
    backendFetch(`/api/snapchat/jobs/${schedId}`, { method: "DELETE" }),

  /** Retry a failed or abandoned job now. Safe to press twice: the backend
   *  keys on the job, so a publish that actually landed is not repeated. */
  retrySnapchatJob: (schedId) =>
    backendFetch(`/api/snapchat/jobs/${schedId}/retry`, { method: "POST" }),

  /** Publishing records, newest first. */
  snapchatPublishingHistory: (accountId = null, limit = 20) =>
    backendFetch(
      `/api/snapchat/publishing/history?limit=${limit}` +
        (accountId ? `&account_id=${accountId}` : "")
    ),

  /** One publishing record. `live` re-reads the post from Snapchat. */
  snapchatPublishingStatus: (recordId, live = false) =>
    backendFetch(`/api/snapchat/publishing/${recordId}/status${live ? "?live=true" : ""}`),

  /** Rollup stats for all Snapchat accounts. */
  snapchatStats: () => backendFetch("/api/snapchat/stats"),

  // ---------------- YOUTUBE ----------------
  youtubeConfig: () => backendFetch("/api/youtube/config"),
  youtubeChannels: () => backendFetch("/api/youtube/channels"),
  addYoutubeChannel: (payload) =>
    backendFetch("/api/youtube/channels", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteYoutubeChannel: (id) =>
    backendFetch(`/api/youtube/channels/${id}`, { method: "DELETE" }),
  youtubeJobs: () => backendFetch("/api/youtube/jobs"),
  createYoutubeJob: (payload) =>
    backendFetch("/api/youtube/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retryYoutubeJob: (id) =>
    backendFetch(`/api/youtube/jobs/${id}/retry`, { method: "POST" }),
  cancelYoutubeJob: (id) =>
    backendFetch(`/api/youtube/jobs/${id}`, { method: "DELETE" }),
  youtubeHistory: () => backendFetch("/api/youtube/history"),

  // ---------------- RUMBLE ----------------
  rumbleConfig: () => backendFetch("/api/rumble/config"),
  rumbleAccounts: () => backendFetch("/api/rumble/accounts"),
  addRumbleAccount: (payload) =>
    backendFetch("/api/rumble/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteRumbleAccount: (id) =>
    backendFetch(`/api/rumble/accounts/${id}`, { method: "DELETE" }),
  rumbleJobs: () => backendFetch("/api/rumble/jobs"),
  createRumbleJob: (payload) =>
    backendFetch("/api/rumble/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retryRumbleJob: (id) =>
    backendFetch(`/api/rumble/jobs/${id}/retry`, { method: "POST" }),
  cancelRumbleJob: (id) =>
    backendFetch(`/api/rumble/jobs/${id}`, { method: "DELETE" }),
  rumbleHistory: () => backendFetch("/api/rumble/history"),

  // ---------------- DAILYMOTION ----------------
  dailymotionConfig: () => backendFetch("/api/dailymotion/config"),
  dailymotionChannels: () => backendFetch("/api/dailymotion/channels"),
  validateDailymotionToken: (accessToken) =>
    backendFetch("/api/dailymotion/validate-token", {
      method: "POST",
      body: JSON.stringify({ access_token: accessToken }),
    }),
  addDailymotionChannel: (payload) =>
    backendFetch("/api/dailymotion/channels", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteDailymotionChannel: (id) =>
    backendFetch(`/api/dailymotion/channels/${id}`, { method: "DELETE" }),
  dailymotionChannelHealth: (id) =>
    backendFetch(`/api/dailymotion/channels/${id}/health`, { method: "POST" }),
  dailymotionJobs: () => backendFetch("/api/dailymotion/jobs"),
  createDailymotionJob: (payload) =>
    backendFetch("/api/dailymotion/jobs", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  retryDailymotionJob: (id) =>
    backendFetch(`/api/dailymotion/jobs/${id}/retry`, { method: "POST" }),
  cancelDailymotionJob: (id) =>
    backendFetch(`/api/dailymotion/jobs/${id}`, { method: "DELETE" }),
  dailymotionHistory: () => backendFetch("/api/dailymotion/history"),
  publishDailymotionNow: (payload) =>
    backendFetch("/api/dailymotion/publish-now", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ---------------- COLLISION & MULTI-FORMAT CREATION ----------------
  getCollisions: (pageId, minGapMinutes = 30) =>
    backendFetch(`/api/pages/${pageId}/collisions?min_gap_minutes=${minGapMinutes}`),
  resolveCollisions: (pageId, minGapMinutes = 45) =>
    backendFetch(`/api/pages/${pageId}/resolve-collisions`, {
      method: "POST",
      body: JSON.stringify({ min_gap_minutes: minGapMinutes }),
    }),
  createContentItem: (pageId, payload) =>
    backendFetch(`/api/pages/${pageId}/content/create`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generateAiPost: (pageId, payload) =>
    backendFetch(`/api/pages/${pageId}/ai/generate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  autopilotBatch: (pageId, payload) =>
    backendFetch(`/api/pages/${pageId}/autopilot/batch`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getAiConfig: () => backendFetch("/api/ai/config"),
  saveAiConfig: (payload) =>
    backendFetch("/api/ai/config", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  testPageToken: (pageId, token, fbPageId) => {
    const url = pageId && pageId > 0 ? `/api/pages/${pageId}/test-token` : "/api/pages/test-token";
    return backendFetch(url, {
      method: "POST",
      body: JSON.stringify({ token, fb_page_id: fbPageId || null }),
    });
  },
  generateFlowPrompts: (payload) =>
    backendFetch("/api/flow/prompts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowAccounts: () => backendFetch("/api/flow/accounts"),
  flowAddAccount: (payload) =>
    backendFetch("/api/flow/accounts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowUpdateAccount: (id, payload) =>
    backendFetch("/api/flow/accounts/" + id, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  flowDeleteAccount: (id) =>
    backendFetch("/api/flow/accounts/" + id, { method: "DELETE" }),
  flowLaunchLogin: (id) =>
    backendFetch("/api/flow/accounts/" + id + "/login", { method: "POST" }),
  flowVerifySession: (id) =>
    backendFetch("/api/flow/accounts/" + id + "/verify-session", { method: "POST" }),
  flowGetSettings: () => backendFetch("/api/flow/settings"),
  flowSaveSettings: (payload) =>
    backendFetch("/api/flow/settings", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowDriveFolders: (payload) =>
    backendFetch("/api/flow/drive/folders", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowBatchGenerate: (payload) =>
    backendFetch("/api/flow/batch", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowJobs: (params) =>
    backendFetch("/api/flow/jobs?" + new URLSearchParams(params || {})),
  flowPreviewImage: (payload) =>
    backendFetch("/api/flow/images/preview", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  flowCloseBrowser: (id) =>
    backendFetch("/api/flow/accounts/" + id + "/close-browser", { method: "POST" }),
  flowRetryJob: (id) =>
    backendFetch("/api/flow/jobs/retry/" + id, { method: "POST" }),
  flowRunPendingJobs: () =>
    backendFetch("/api/flow/jobs/run-pending", { method: "POST" }),

  // Autopilot Content Operating System
  pageAutomationProfile: (pageId) =>
    backendFetch(`/api/pages/${pageId}/automation-profile`),
  savePageAutomationProfile: (pageId, profile) =>
    backendFetch(`/api/pages/${pageId}/automation-profile`, {
      method: "POST",
      body: JSON.stringify(profile),
    }),
  previewPageSchedule: (pageId, profile) =>
    backendFetch(`/api/pages/${pageId}/schedule/preview`, {
      method: "POST",
      body: JSON.stringify(profile || {}),
    }),
  rebalancePageSchedule: (pageId) =>
    backendFetch(`/api/pages/${pageId}/schedule/rebalance`, {
      method: "POST",
    }),
  pageMonetization: (pageId) =>
    backendFetch(`/api/pages/${pageId}/monetization`),

  // Dailymotion Independent Automation & Monetization
  dailymotionAutomationProfile: (channelId) =>
    backendFetch(`/api/dailymotion/channels/${channelId}/automation-profile`),
  saveDailymotionAutomationProfile: (channelId, profile) =>
    backendFetch(`/api/dailymotion/channels/${channelId}/automation-profile`, {
      method: "POST",
      body: JSON.stringify(profile),
    }),
  previewDailymotionSchedule: (channelId, profile) =>
    backendFetch(`/api/dailymotion/channels/${channelId}/schedule/preview`, {
      method: "POST",
      body: JSON.stringify(profile || {}),
    }),
  dailymotionMonetizationOverview: () =>
    backendFetch("/api/dailymotion/monetization"),

  // Content Machine Dynamic Generation
  generateContentBatch: (payload) =>
    backendFetch("/api/content/generate-batch", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Universal Content Studio
  studioProviders: () => backendFetch("/api/studio/providers"),
  studioBrief: (payload) =>
    backendFetch("/api/studio/brief", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioDiscover: (payload) =>
    backendFetch("/api/studio/discover", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioCaption: (payload) =>
    backendFetch("/api/studio/caption", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioCreateTriplet: (payload) =>
    backendFetch("/api/studio/create-triplet", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioBatchGenerate: (payload) =>
    backendFetch("/api/studio/batch-generate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioJobs: () => backendFetch("/api/studio/jobs"),
  studioJob: (id) => backendFetch(`/api/studio/jobs/${id}`),
  studioFolders: () => backendFetch("/api/studio/folders"),
  studioCreateFolder: (payload) =>
    backendFetch("/api/studio/folders/create", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  studioBuffer: (pageId) =>
    backendFetch(`/api/studio/buffer${pageId ? "?page_id=" + pageId : ""}`),
  studioRefillBuffer: (payload) =>
    backendFetch("/api/studio/buffer/refill", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
};


