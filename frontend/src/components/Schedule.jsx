import React, { useState, useEffect } from "react";
import { api } from "../api.js";
import PageCombo from "./PageCombo.jsx";
import AutopilotModal from "./AutopilotModal.jsx";
import { Sparkles } from "lucide-react";

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

const TIMEZONE_OPTIONS = [
  { label: "India/IST (UTC+5:30)", offset: 5.5 },
  { label: "Dubai (UTC+4)", offset: 4 },
  { label: "US Eastern (UTC-4 EDT)", offset: -4 },
  { label: "US Central (UTC-5 CDT)", offset: -5 },
  { label: "US Pacific (UTC-7 PDT)", offset: -7 },
  { label: "UTC (UTC+0)", offset: 0 },
];

const IST_OFFSET = 5.5;

function fmt(iso, offset) {
  if (!iso) return "—";
  const d = new Date(iso);
  const local = new Date(d.getTime() + offset * 3600 * 1000);
  return local.toUTCString().replace(" GMT", "");
}

function fmtReadable(iso, offset) {
  if (!iso) return "—";
  const local = new Date(new Date(iso).getTime() + offset * 3600 * 1000);
  const days = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
  const day = days[local.getUTCDay()];
  const date = local.getUTCDate();
  const month = months[local.getUTCMonth()];
  let h = local.getUTCHours(), m = local.getUTCMinutes();
  const ampm = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  const mm = String(m).padStart(2, "0");
  const tzLabel = offset === 5.5 ? "IST" : `UTC${offset >= 0 ? "+" : ""}${offset}`;
  return `${day} ${date} ${month} at ${h}:${mm} ${ampm} ${tzLabel}`;
}

function fmtTimeOnly(iso, offset) {
  const local = new Date(new Date(iso).getTime() + offset * 3600 * 1000);
  return local.toISOString().slice(11, 16);
}

function utcToInputStr(utcIso, offsetHours) {
  if (!utcIso) return "";
  const localMs = new Date(utcIso).getTime() + offsetHours * 3600 * 1000;
  return new Date(localMs).toISOString().slice(0, 16);
}

function inputStr2UtcIso(inputVal, offsetHours) {
  const localMs = new Date(inputVal + ":00Z").getTime() - offsetHours * 3600 * 1000;
  return new Date(localMs).toISOString();
}

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function toDateStr(d) { return d.toISOString().slice(0, 10); }

function mondayOf(date) {
  const d = new Date(date);
  const day = d.getUTCDay();
  const diff = (day === 0 ? -6 : 1) - day;
  const m = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  m.setUTCDate(m.getUTCDate() + diff);
  return m;
}

function fmtWeekLabel(weekStartStr) {
  const s = new Date(weekStartStr + "T00:00:00Z");
  const e = new Date(s); e.setUTCDate(e.getUTCDate() + 6);
  const opts = { month: "short", day: "numeric", timeZone: "UTC" };
  return `${s.toLocaleDateString("en-US", opts)} – ${e.toLocaleDateString("en-US", { ...opts, year: "numeric" })}`;
}

function fmt12h(hhmm) {
  const [h, m] = hhmm.split(":").map(Number);
  const ampm = h >= 12 ? "PM" : "AM";
  return `${h % 12 || 12}:${String(m).padStart(2,"0")} ${ampm}`;
}

function fmtDateUS(d) {
  if (!d) return "—";
  const [y, m, day] = d.split("-");
  return `${m}/${day}/${y}`;
}

// Default gap between posts, by intensity bucket (mirrors backend's
// _gap_minutes_for) — only used to seed a starting value the moment
// posts/day is (re)picked. The gap selector lets the user override it from
// there; individual times stay fully editable regardless.
function gapMinutesFor(n) {
  if (n <= 1) return 60;
  if (n <= 10) return 60;
  if (n <= 24) return 30;
  return 15;
}

const GAP_PRESETS = [
  { label: "15 minutes", value: 15 },
  { label: "30 minutes", value: 30 },
  { label: "45 minutes", value: 45 },
  { label: "1 hour", value: 60 },
  { label: "2 hours", value: 120 },
  { label: "3 hours", value: 180 },
];

function suggestTimes(n, gapMinutes, startTimeStr) {
  if (n <= 0) return [];
  const [sh, sm] = (startTimeStr || "09:00").split(":").map(Number);
  const startMinutes = (sh || 0) * 60 + (sm || 0);
  const gap = gapMinutes || gapMinutesFor(n) || 60;
  const out = [];
  for (let i = 0; i < n; i++) {
    const total = (startMinutes + i * gap) % 1440;
    const hh = Math.floor(total / 60);
    const mm = total % 60;
    out.push(`${String(hh).padStart(2, "0")}:${String(mm).padStart(2, "0")}`);
  }
  return out;
}

const PRESETS = [1, 2, 4, 6, 8, 12, 16, 24, 36, 48];

export default function Schedule({ pages, activePage, setActivePage, onReload }) {
  const [view, setView] = useState("list");
  const [rows, setRows] = useState([]);
  const [abandonedRows, setAbandonedRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [msgIsErr, setMsgIsErr] = useState(false);
  const [busy, setBusy] = useState(false);

  // Timeline Collision Management
  const [collisions, setCollisions] = useState([]);
  const [resolvingCollisions, setResolvingCollisions] = useState(false);
  const [mediaFilter, setMediaFilter] = useState("all");
  const [avoidCollisions, setAvoidCollisions] = useState(true);
  const [showAutopilotModal, setShowAutopilotModal] = useState(false);

  const page = pages.find((p) => p.id === activePage);
  const [displayTzOffset, setDisplayTzOffset] = useState(IST_OFFSET);

  const [editingRowId, setEditingRowId] = useState(null);
  const [editingTimeVal, setEditingTimeVal] = useState("");

  // Post Now per row: keyed by content_item_id
  const [postingNowId, setPostingNowId] = useState(null);
  const [postNowMsg, setPostNowMsg] = useState({});

  const [selectedIds, setSelectedIds] = useState([]);
  const [showCancelSelectedModal, setShowCancelSelectedModal] = useState(false);
  const [showCancelAllModal, setShowCancelAllModal] = useState(false);
  const [showClearPostedModal, setShowClearPostedModal] = useState(false);
  const [clearingPosted, setClearingPosted] = useState(false);
  const [refreshingCaptions, setRefreshingCaptions] = useState(false);
  const [showRescueModal, setShowRescueModal] = useState(false);
  const [rescuing, setRescuing] = useState(false);
  const [rescueReflow, setRescueReflow] = useState(true);

  const [postsPerDay, setPostsPerDay] = useState(null);
  const [customMode, setCustomMode] = useState(false);
  const [gapMinutes, setGapMinutes] = useState(null);
  const [customGapMode, setCustomGapMode] = useState(false);
  const [startTime, setStartTime] = useState("09:00");
  const [times, setTimes] = useState([]);
  const [startDate, setStartDate] = useState("");
  const [preview, setPreview] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [committing, setCommitting] = useState(false);


  const [showCopyModal, setShowCopyModal] = useState(false);
  const [copyTargetIds, setCopyTargetIds] = useState([]);
  const [copying, setCopying] = useState(false);
  const [copyResult, setCopyResult] = useState(null);

  const [intensityPreview, setIntensityPreview] = useState(null);
  const [intensityPreviewLoading, setIntensityPreviewLoading] = useState(false);
  const [intensityCommitting, setIntensityCommitting] = useState(false);

  const [weekStart, setWeekStart] = useState("");
  const [calRows, setCalRows] = useState([]);
  const [calLoading, setCalLoading] = useState(false);
  const [chipDetail, setChipDetail] = useState(null);

  useEffect(() => {
    if (page?.utc_offset_hours !== undefined) {
      setDisplayTzOffset(page.utc_offset_hours);
    }
  }, [activePage]);

  async function loadCollisions() {
    if (!activePage) { setCollisions([]); return; }
    try {
      const res = await api.getCollisions(activePage, 30);
      setCollisions(res.collisions || []);
    } catch {
      setCollisions([]);
    }
  }

  async function handleResolveCollisions() {
    if (!activePage) return;
    setResolvingCollisions(true);
    try {
      const res = await api.resolveCollisions(activePage, 45);
      setMsg(`Resolved timeline collisions: re-spaced ${res.resolved_count} post(s) to 45-minute safe intervals.`);
      setMsgIsErr(false);
      await load();
      await loadCollisions();
    } catch (e) {
      setMsg(`Collision resolution failed: ${e.message}`);
      setMsgIsErr(true);
    } finally {
      setResolvingCollisions(false);
    }
  }

  async function load() {
    try {
      const q = activePage ? `?page_id=${activePage}&status=pending` : "?status=pending";
      setRows(await api.schedule(q));
      loadCollisions();
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
  }
  async function loadAbandoned() {
    if (!activePage) return;
    try { setAbandonedRows(await api.schedule({ page_id: activePage, status: "abandoned" })); }
    catch { /* best-effort */ }
  }
  useEffect(() => {
    load();
    loadAbandoned();
    loadCollisions();
    setPreview(null);
    setIntensityPreview(null);
    setSelectedIds([]);
    setEditingRowId(null);
    setPostNowMsg({});
  }, [activePage]);

  // Mode B (page already has pending posts): prefill the selector with the
  // page's last-used intensity so "Update Intensity" starts from where the
  // current schedule actually is.
  useEffect(() => {
    if (rows.length === 0) return;
    const remembered = page?.last_auto_schedule_settings?.posts_per_day;
    if (remembered > 0) {
      setCustomMode(!PRESETS.includes(remembered));
      setPostsPerDay(remembered);
    }
  }, [activePage, rows.length]);

  // Fresh Auto-Schedule wizard (no pending posts yet): prefill posts/day,
  // gap, and start time from this page's remembered settings — including
  // right after "Copy to Other Pages" writes them for a page for the first
  // time — so the wizard doesn't reset to blank defaults on every visit.
  // When this page has no remembered pattern, reset to blank instead of
  // leaving whatever a previously-viewed page's wizard state happened to be
  // (postsPerDay/times otherwise leak across a page switch since nothing
  // else clears them).
  useEffect(() => {
    if (rows.length !== 0) return;
    const remembered = page?.last_auto_schedule_settings;
    const n = remembered?.posts_per_day;
    if (!(n > 0)) {
      setCustomMode(false);
      setPostsPerDay(null);
      setGapMinutes(null);
      setCustomGapMode(false);
      setStartTime("09:00");
      setTimes([]);
      return;
    }
    const gap = remembered.gap_minutes || gapMinutesFor(n);
    const st = remembered.start_time || "09:00";
    setCustomMode(!PRESETS.includes(n));
    setPostsPerDay(n);
    setGapMinutes(gap);
    setCustomGapMode(!GAP_PRESETS.some((g) => g.value === gap));
    setStartTime(st);
    setTimes(
      remembered.post_times && remembered.post_times.length === n
        ? remembered.post_times
        : suggestTimes(n, gap, st)
    );
  }, [activePage, rows.length]);

  useEffect(() => {
    const t = new Date(); t.setDate(t.getDate() + 1);
    setStartDate(t.toISOString().slice(0, 10));
    setWeekStart(toDateStr(mondayOf(new Date())));
  }, []);

  async function cancel(id) {
    setBusy(true);
    try {
      await api.cancel(id);
      setSelectedIds((prev) => prev.filter((i) => i !== id));
      load();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  function startEditInline(row) {
    setEditingRowId(row.id);
    setEditingTimeVal(utcToInputStr(row.scheduled_time_utc, displayTzOffset));
  }

  async function saveInlineTime(rowId) {
    if (!editingTimeVal) return;
    const utcIso = inputStr2UtcIso(editingTimeVal, displayTzOffset);
    setBusy(true); setMsg(""); setMsgIsErr(false);
    try {
      await api.reschedule(rowId, utcIso);
      setRows((prev) => prev.map((r) => (r.id === rowId ? { ...r, scheduled_time_utc: utcIso } : r)));
      setEditingRowId(null);
      setMsg("Post time updated.");
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  async function postRowNow(row) {
    const cid = row.content_item_id;
    setPostingNowId(cid);
    setPostNowMsg((m) => ({ ...m, [cid]: { ok: null, text: "Posting to Facebook…" } }));
    try {
      await api.postNow(cid);
      setPostNowMsg((m) => ({ ...m, [cid]: { ok: true, text: "✓ Posted!" } }));
      load();
      loadAbandoned();
      if (view === "calendar") loadCalendar();
    } catch (e) {
      setPostNowMsg((m) => ({ ...m, [cid]: { ok: false, text: e.message } }));
    } finally {
      setPostingNowId(null);
    }
  }

  function toggleSelectAll() {
    if (rows.length === 0) return;
    setSelectedIds(selectedIds.length === rows.length ? [] : rows.map((r) => r.id));
  }

  function toggleSelectRow(id) {
    setSelectedIds((prev) => prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]);
  }

  async function confirmCancelSelected() {
    setBusy(true); setMsg(""); setMsgIsErr(false);
    try {
      for (const id of selectedIds) { await api.cancel(id); }
      setMsg(`Cancelled ${selectedIds.length} selected post(s).`);
      setSelectedIds([]);
      setShowCancelSelectedModal(false);
      load();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Cancel failed: ${e.message}`); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  async function confirmCancelAll() {
    if (!activePage) return;
    setBusy(true); setMsg(""); setMsgIsErr(false);
    try {
      await api.cancelAll(activePage);
      setMsg(`Cancelled all pending posts for ${page?.name || "this page"}.`);
      setSelectedIds([]);
      setShowCancelAllModal(false);
      load();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Cancel all failed: ${e.message}`); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  async function confirmClearPosted() {
    if (!activePage) return;
    setClearingPosted(true); setMsg(""); setMsgIsErr(false);
    try {
      const r = await api.clearPostedHistory(activePage);
      setMsg(
        r.deleted === 0
          ? `No posted records to clear for ${page?.name || "this page"}.`
          : `Cleared ${r.deleted} posted record${r.deleted === 1 ? "" : "s"} for ${page?.name || "this page"}. Those videos are available to repost.`
      );
      setShowClearPostedModal(false);
      load();
      onReload?.();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Clear posted history failed: ${e.message}`); setMsgIsErr(true); }
    finally { setClearingPosted(false); }
  }

  /** Rebuild every pending post's caption from the current content + page
   * data. The poster does this per-post as each one fires anyway, so this is
   * for fixing a whole backlog at once — e.g. after a Drive sync finally
   * matched the sidecar captions for videos that were scheduled without them. */
  async function refreshCaptions() {
    if (!activePage) return;
    setRefreshingCaptions(true); setMsg(""); setMsgIsErr(false);
    try {
      const r = await api.refreshPendingCaptions(activePage);
      setMsg(
        r.checked === 0
          ? "No pending posts on this page to refresh."
          : r.refreshed === 0
          ? `All ${r.checked} pending caption${r.checked === 1 ? "" : "s"} were already up to date.`
          : `Rebuilt ${r.refreshed} of ${r.checked} pending caption${r.checked === 1 ? "" : "s"} from the latest content data.`
      );
      load();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Caption refresh failed: ${e.message}`); setMsgIsErr(true); }
    finally { setRefreshingCaptions(false); }
  }

  async function retry(id) {
    try { await api.retryAbandoned(id); loadAbandoned(); load(); }
    catch (e) { setMsg(e.message); setMsgIsErr(true); }
  }
  async function dismiss(id) {
    try { await api.dismissAbandoned(id); loadAbandoned(); }
    catch (e) { setMsg(e.message); setMsgIsErr(true); }
  }

  async function handleRescueAll() {
    setRescuing(true);
    setMsg("");
    setMsgIsErr(false);
    try {
      const res = await api.rescueAbandoned(activePage || null, rescueReflow);
      setMsg(res.message || `Successfully rescued ${res.rescued} post(s)!`);
      load();
      loadAbandoned();
      setShowRescueModal(false);
      onReload?.();
    } catch (e) {
      setMsg(`Rescue failed: ${e.message}`);
      setMsgIsErr(true);
    } finally {
      setRescuing(false);
    }
  }

  // Picking (or re-picking) posts/day re-applies that bucket's default gap —
  // the user can then override the gap manually for this count via the gap
  // selector below, without it fighting back.
  function selectPreset(n) {
    setCustomMode(false); setPostsPerDay(n);
    const gap = gapMinutesFor(n);
    setGapMinutes(gap); setCustomGapMode(false);
    setTimes(suggestTimes(n, gap, startTime));
    setPreview(null); setIntensityPreview(null);
  }
  function setCustomCount(n) {
    setPostsPerDay(n);
    if (n > 0) {
      const gap = gapMinutesFor(n);
      setGapMinutes(gap); setCustomGapMode(false);
      setTimes(suggestTimes(n, gap, startTime));
    }
    setPreview(null); setIntensityPreview(null);
  }
  function selectGapPreset(minutes) {
    setCustomGapMode(false); setGapMinutes(minutes);
    if (postsPerDay > 0) setTimes(suggestTimes(postsPerDay, minutes, startTime));
    setPreview(null);
  }
  function setCustomGap(minutes) {
    setGapMinutes(minutes);
    if (postsPerDay > 0 && minutes > 0) setTimes(suggestTimes(postsPerDay, minutes, startTime));
    setPreview(null);
  }
  function changeStartTime(v) {
    setStartTime(v);
    if (postsPerDay > 0) setTimes(suggestTimes(postsPerDay, gapMinutes, v));
    setPreview(null);
  }
  function updateTime(i, v) { setTimes((ts) => ts.map((t, idx) => (idx === i ? v : t))); setPreview(null); }
  function validWizard() { return postsPerDay > 0 && startDate && times.length === postsPerDay && times.every(Boolean); }

  // Shared by preview/commit for this page and by "Copy to Other Pages" —
  // gap_minutes/start_time are purely descriptive (remembered alongside
  // post_times so the wizard can show what produced them); the backend only
  // ever schedules from post_times itself.
  function scheduleBody(overrides = {}) {
    return {
      posts_per_day: postsPerDay,
      start_date: startDate,
      post_times: times,
      gap_minutes: gapMinutes,
      start_time: startTime,
      media_filter: mediaFilter,
      avoid_collisions: avoidCollisions,
      dry_run: false,
      ...overrides,
    };
  }

  async function runPreview() {
    if (!validWizard()) { setMsg("Pick posts per day, a start date, and fill in every time."); setMsgIsErr(true); return; }
    setMsg(""); setPreviewLoading(true);
    try {
      const r = await api.autoSchedule(activePage, scheduleBody({ dry_run: true }));
      setPreview(r);
    } catch (e) { setMsg(`Preview failed: ${e.message}`); setMsgIsErr(true); }
    finally { setPreviewLoading(false); }
  }

  async function commitSchedule() {
    setCommitting(true); setMsg(""); setMsgIsErr(false);
    try {
      const r = await api.autoSchedule(activePage, scheduleBody());
      setPreview(null);
      setMsg(
        r.scheduled === 0
          ? "No unposted videos to schedule for this page. Sync Drive to add more content."
          : `Scheduling complete — ${r.scheduled} video${r.scheduled === 1 ? "" : "s"} across ` +
            `${r.days_needed} day${r.days_needed === 1 ? "" : "s"}. Last post: ${fmtReadable(r.last_post_utc, displayTzOffset)}.`
      );
      load();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Auto-schedule failed: ${e.message}`); setMsgIsErr(true); }
    finally { setCommitting(false); }
  }

  function toggleCopyTarget(id) {
    setCopyTargetIds((prev) => prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]);
  }

  async function copyToOtherPages() {
    setCopying(true); setCopyResult(null);
    const results = await Promise.allSettled(
      copyTargetIds.map((id) => api.autoSchedule(id, scheduleBody()))
    );
    const perPage = copyTargetIds.map((id, i) => ({
      id,
      name: pages.find((p) => p.id === id)?.name || `Page ${id}`,
      outcome: results[i],
    }));
    const succeeded = perPage.filter((p) => p.outcome.status === "fulfilled");
    const failed = perPage.filter((p) => p.outcome.status === "rejected");
    const totalScheduled = succeeded.reduce((sum, p) => sum + (p.outcome.value?.scheduled || 0), 0);
    const pagesWithPosts = succeeded.filter((p) => (p.outcome.value?.scheduled || 0) > 0).length;
    setCopyResult({ succeeded, failed, totalScheduled, pagesWithPosts });
    setCopying(false);
    onReload?.();
  }

  async function runIntensityPreview() {
    if (!(postsPerDay > 0)) { setMsg("Pick a posts-per-day number first."); setMsgIsErr(true); return; }
    setMsg(""); setIntensityPreviewLoading(true);
    try {
      const r = await api.updateIntensity(activePage, { posts_per_day: postsPerDay, dry_run: true });
      setIntensityPreview(r);
    } catch (e) { setMsg(`Preview failed: ${e.message}`); setMsgIsErr(true); }
    finally { setIntensityPreviewLoading(false); }
  }

  async function commitIntensityUpdate() {
    setIntensityCommitting(true); setMsg(""); setMsgIsErr(false);
    try {
      const r = await api.updateIntensity(activePage, { posts_per_day: postsPerDay, dry_run: false });
      setIntensityPreview(null);
      setMsg(r.message || "Intensity updated.");
      load();
      onReload?.();
      if (view === "calendar") loadCalendar();
    } catch (e) { setMsg(`Update intensity failed: ${e.message}`); setMsgIsErr(true); }
    finally { setIntensityCommitting(false); }
  }

  async function loadCalendar() {
    if (!activePage || !weekStart) return;
    setCalLoading(true);
    try { setCalRows(await api.calendar(activePage, weekStart)); }
    catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setCalLoading(false); }
  }
  useEffect(() => { if (view === "calendar") loadCalendar(); }, [view, activePage, weekStart]);

  function shiftWeek(delta) {
    const d = new Date(weekStart + "T00:00:00Z");
    d.setUTCDate(d.getUTCDate() + delta * 7);
    setWeekStart(toDateStr(d));
  }
  function goToday() { setWeekStart(toDateStr(mondayOf(new Date()))); }

  if (pages.length === 0)
    return (<><div className="eyebrow">DISTRIBUTION TIMELINE · DISPATCH QUEUE</div><h1 className="headline">Publishing Schedule</h1>
      <div className="empty">Connect a page and schedule content items first.</div></>);

  const days = weekStart
    ? Array.from({ length: 7 }, (_, i) => {
        const d = new Date(weekStart + "T00:00:00Z");
        d.setUTCDate(d.getUTCDate() + i);
        return toDateStr(d);
      })
    : [];
  const byDay = {};
  for (const d of days) byDay[d] = [];
  for (const r of calRows) {
    const local = new Date(new Date(r.scheduled_time_utc).getTime() + displayTzOffset * 3600 * 1000);
    const key = toDateStr(local);
    if (byDay[key]) byDay[key].push(r);
  }
  const todayStr = toDateStr(mondayOf(new Date())) === weekStart ? toDateStr(new Date()) : null;

  return (
    <>
      <div className="eyebrow">DISTRIBUTION TIMELINE · DISPATCH QUEUE</div>
      <h1 className="headline">Publishing Schedule</h1>
      <p className="lede">
        Chronological dispatch queue across connected channels. Fine-tune release dates,
        adjust posting frequency, perform emergency rescues, or trigger immediate publication.
      </p>
      <hr className="rule" />

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap", marginBottom: 16 }}>
        <div style={{ width: 300 }}>
          <PageCombo pages={pages} value={activePage} onChange={setActivePage} />
        </div>

        <div className="field" style={{ maxWidth: 280, marginBottom: 0 }}>
          <label>Viewing times in</label>
          <select value={displayTzOffset} onChange={(e) => setDisplayTzOffset(Number(e.target.value))}>
            {TIMEZONE_OPTIONS.map((tz) => (
              <option key={tz.label} value={tz.offset}>{tz.label}</option>
            ))}
          </select>
        </div>

        {activePage && (rows.length > 0 || (page?.stats?.total_posted || 0) > 0) && (
          <div style={{ marginLeft: "auto", alignSelf: "flex-end", display: "flex", gap: 10, flexWrap: "wrap" }}>
            {rows.length > 0 && (
              <button
                className="btn ghost"
                disabled={busy || refreshingCaptions}
                title="Rebuild every pending post's caption from the current Drive/content data"
                onClick={refreshCaptions}
              >
                {refreshingCaptions ? "Refreshing captions…" : "Refresh Captions for Pending Posts"}
              </button>
            )}
            {(page?.stats?.total_posted || 0) > 0 && (
              <button
                className="btn ghost"
                disabled={busy || clearingPosted}
                title="Delete this page's posted history so those videos can be reposted — the video library itself is untouched"
                onClick={() => setShowClearPostedModal(true)}
              >
                Clear Posted History
              </button>
            )}
            {rows.length > 0 && (
              <button
                className="btn"
                style={{ background: "#c0392b", color: "#fff", borderColor: "transparent" }}
                disabled={busy || refreshingCaptions}
                onClick={() => setShowCancelAllModal(true)}
              >
                Cancel All Pending ({rows.length} posts)
              </button>
            )}
          </div>
        )}
      </div>

      {msg && <div className={"banner" + (msgIsErr ? " err" : "")}>{msg}</div>}

      {/* ── Timeline Collision Alert & 1-Click Fix ── */}
      {collisions.length > 0 && (
        <div style={{
          background: "rgba(245, 158, 11, 0.12)",
          border: "1px solid rgba(245, 158, 11, 0.35)",
          borderRadius: 8,
          padding: "14px 18px",
          marginBottom: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          gap: 16,
          flexWrap: "wrap"
        }}>
          <div>
            <div style={{ fontWeight: 600, color: "#f59e0b", display: "flex", alignItems: "center", gap: 8, fontSize: 14.5 }}>
              <span>⚠️</span>
              <span>Timeline Collision Detected ({collisions.length} conflict{collisions.length > 1 ? "s" : ""})</span>
            </div>
            <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, maxWidth: 700, lineHeight: 1.5 }}>
              Posts are scheduled closer than 30 minutes on <b>{page?.name || "this page"}</b>. Rapid overlapping publication causes Facebook's delivery algorithm to cannibalize impressions between Reels and Images, and risks Meta API concurrency limits.
            </div>
          </div>
          <button
            className="btn small"
            style={{ background: "#f59e0b", color: "#000", fontWeight: 600, whiteSpace: "nowrap" }}
            disabled={resolvingCollisions}
            onClick={handleResolveCollisions}
          >
            {resolvingCollisions ? "Resolving…" : "Auto-Space Posts (45m)"}
          </button>
        </div>
      )}

      {/* ── Auto-Schedule / Update Intensity ── */}
      <div className="dossier-box" style={{ maxWidth: 680, marginBottom: 28 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
          <div className="h">{rows.length === 0 ? "Auto-Schedule" : "Update Intensity"}</div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <button
              type="button"
              className="btn small ghost"
              style={{ color: "#34d399", borderColor: "rgba(16, 185, 129, 0.28)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 6 }}
              title="Rebalance pending queue across fair-share window without touching published posts"
              onClick={async () => {
                if (!activePage) return;
                setMsg("Rebalancing schedule across fair-share window…");
                setMsgIsErr(false);
                try {
                  const res = await api.rebalancePageSchedule(activePage);
                  setMsg(`✓ Schedule rebalanced! Repositioned ${res.rebalanced_count || 0} future post(s).`);
                  load();
                  if (view === "calendar") loadCalendar();
                } catch (e) {
                  setMsg("Rebalance failed: " + e.message);
                  setMsgIsErr(true);
                }
              }}
            >
              <span>⚡ Rebalance Queue</span>
            </button>
            <button
              type="button"
              className="btn small"
              style={{ background: "rgba(16, 185, 129, 0.12)", color: "#10b981", border: "1px solid rgba(16, 185, 129, 0.28)", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 6 }}
              onClick={() => setShowAutopilotModal(true)}
            >
              <Sparkles size={14} />
              <span>Launch Autopilot Batch</span>
            </button>
          </div>
        </div>

        <AutopilotModal
          isOpen={showAutopilotModal}
          onClose={() => setShowAutopilotModal(false)}
          page={page}
          onScheduled={() => {
            loadRows();
            checkCollisions();
          }}
        />

        <p className="lede" style={{ fontSize: 14, marginTop: 8, marginBottom: 14 }}>
          {rows.length === 0
            ? <>Fills the queue with unposted content for <b>{page?.name || "this page"}</b>.</>
            : <><b>{page?.name || "This page"}</b> already has <b>{rows.length}</b> pending post{rows.length === 1 ? "" : "s"}. Pick a new posts/day target — existing pending posts are only added to or trimmed, never moved.</>}
        </p>

        {rows.length === 0 && (
          <div className="field">
            <label>Content Format</label>
            <select value={mediaFilter} onChange={(e) => { setMediaFilter(e.target.value); setPreview(null); }}>
              <option value="all">All Formats (Reels + Photos + Text)</option>
              <option value="reel">Reels & Videos Only (Google Drive / Flow)</option>
              <option value="photo">Photos & Images Only</option>
              <option value="text">Text Updates Only</option>
            </select>
          </div>
        )}

        <div style={{ marginBottom: 14 }}>
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13, color: "var(--ink-soft)" }}>
            <input
              type="checkbox"
              checked={avoidCollisions}
              onChange={(e) => { setAvoidCollisions(e.target.checked); setPreview(null); }}
            />
            <span>Intelligent Collision Avoidance (guarantees safe 25m+ buffer with existing posts)</span>
          </label>
        </div>

        <div className="field">
          <label>Posts per day</label>
          <div className="preset-group">
            {PRESETS.map((n) => (
              <button key={n} type="button"
                className={"preset-btn" + (postsPerDay === n && !customMode ? " active" : "")}
                onClick={() => selectPreset(n)}>{n}</button>
            ))}
            <button type="button" className={"preset-btn" + (customMode ? " active" : "")}
              onClick={() => { setCustomMode(true); setPreview(null); setIntensityPreview(null); }}>Custom</button>
          </div>
          {customMode && (
            <input type="number" min="1" style={{ marginTop: 8, maxWidth: 140 }}
              value={postsPerDay || ""} placeholder="e.g. 25 — no upper limit"
              onChange={(e) => setCustomCount(Number(e.target.value) || 0)} />
          )}
        </div>

        {rows.length === 0 ? (
          <>
            {postsPerDay > 0 && (
              <div className="field">
                <label>Gap between posts</label>
                <div className="preset-group">
                  {GAP_PRESETS.map((g) => (
                    <button key={g.value} type="button"
                      className={"preset-btn" + (gapMinutes === g.value && !customGapMode ? " active" : "")}
                      onClick={() => selectGapPreset(g.value)}>{g.label}</button>
                  ))}
                  <button type="button" className={"preset-btn" + (customGapMode ? " active" : "")}
                    onClick={() => setCustomGapMode(true)}>Custom</button>
                </div>
                {customGapMode && (
                  <input type="number" min="1" style={{ marginTop: 8, maxWidth: 140 }}
                    value={gapMinutes || ""} placeholder="minutes"
                    onChange={(e) => setCustomGap(Number(e.target.value) || 0)} />
                )}
                <div className="hint">
                  Defaults to the usual gap for {postsPerDay}/day — pick a different one any time and the times below update instantly.
                </div>
              </div>
            )}

            {postsPerDay > 0 && (
              <div className="field">
                <label>Start time</label>
                <input type="time" style={{ maxWidth: 140 }} value={startTime}
                  onChange={(e) => changeStartTime(e.target.value)} />
                <div className="hint">When the first post of the day goes out — every time below recalculates from this.</div>
              </div>
            )}

            {postsPerDay > 0 && (
              <div className="field">
                <label>Post times ({postsPerDay}/day) — IST local time</label>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {times.map((t, i) => (
                    <input key={i} type="time" value={t} style={{ width: 118 }}
                      onChange={(e) => updateTime(i, e.target.value)} />
                  ))}
                </div>
                <div className="hint">
                  Generated from the start time and gap above — each one is still individually editable.
                </div>
              </div>
            )}

            {postsPerDay > 0 && (
              <button type="button" className="btn ghost" disabled={!validWizard() || pages.length <= 1}
                onClick={() => { setCopyResult(null); setCopyTargetIds([]); setShowCopyModal(true); }}
                style={{ marginBottom: 14 }}>
                Copy to Other Pages
              </button>
            )}

            <div className="field">
              <label>Start date</label>
              <input type="date" value={startDate} onChange={(e) => { setStartDate(e.target.value); setPreview(null); }} />
            </div>

            <button className="btn" disabled={previewLoading || committing || !validWizard()}
              onClick={runPreview} style={{ width: "100%", justifyContent: "center" }}>
              {previewLoading ? "Checking…" : "Preview"}
            </button>

            {preview && (
              <div className="dossier-box" style={{ marginTop: 14, background: "var(--card)" }}>
                <div className="h">Preview</div>
                {preview.scheduled === 0 ? (
                  <p style={{ marginTop: 8 }}>No unposted videos to schedule. Sync Drive to add more content.</p>
                ) : (
                  <>
                    <p style={{ marginTop: 8, fontSize: 15.5 }}>
                      <b>{preview.total_unposted ?? preview.scheduled}</b> videos ·{" "}
                      <b>{postsPerDay}</b> posts per day ·{" "}
                      <b>{preview.days_needed}</b> day{preview.days_needed === 1 ? "" : "s"} of content
                    </p>
                    <p style={{ fontSize: 13.5, color: "var(--ink-soft)", marginTop: 6, lineHeight: 1.7 }}>
                      First post: {fmtReadable(preview.first_post_utc, displayTzOffset)}<br />
                      Last post: {fmtReadable(preview.last_post_utc, displayTzOffset)}<br />
                      Times: {times.map(fmt12h).join(" · ")}
                    </p>
                    <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
                      <button className="btn" disabled={committing} onClick={commitSchedule}>
                        {committing ? "Scheduling…" : "Confirm & Schedule All"}
                      </button>
                      <button className="btn ghost" disabled={committing} onClick={() => setPreview(null)}>Cancel</button>
                    </div>
                    {committing && <div className="progress-bar"><div className="progress-bar-fill indeterminate" /></div>}
                  </>
                )}
              </div>
            )}
          </>
        ) : (
          <>
            <button className="btn" disabled={intensityPreviewLoading || intensityCommitting || !(postsPerDay > 0)}
              onClick={runIntensityPreview} style={{ width: "100%", justifyContent: "center" }}>
              {intensityPreviewLoading ? "Checking…" : "Preview Update"}
            </button>

            {intensityPreview && (
              <div className="dossier-box" style={{ marginTop: 14, background: "var(--card)" }}>
                <div className="h">Preview</div>
                {intensityPreview.mode === "fresh" ? (
                  <p style={{ marginTop: 8 }}>{intensityPreview.message}</p>
                ) : (
                  <>
                    <p style={{ marginTop: 8, fontSize: 15.5 }}>{intensityPreview.message}</p>
                    {intensityPreview.shortfall > 0 && (
                      <p style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 6 }}>
                        Sync Drive for more content to fill every slot.
                      </p>
                    )}
                    {(intensityPreview.will_add > 0 || intensityPreview.will_cancel > 0) && (
                      <div style={{ display: "flex", gap: 10, marginTop: 12 }}>
                        <button className="btn" disabled={intensityCommitting} onClick={commitIntensityUpdate}>
                          {intensityCommitting ? "Updating…" : "Update Intensity"}
                        </button>
                        <button className="btn ghost" disabled={intensityCommitting} onClick={() => setIntensityPreview(null)}>Cancel</button>
                      </div>
                    )}
                    {intensityCommitting && <div className="progress-bar"><div className="progress-bar-fill indeterminate" /></div>}
                  </>
                )}
              </div>
            )}
          </>
        )}
      </div>

      {/* ── List / Calendar toggle ── */}
      <div className="preset-group" style={{ marginBottom: 20 }}>
        <button className={"preset-btn" + (view === "list" ? " active" : "")} style={{ minWidth: 96 }}
          onClick={() => setView("list")}>List view</button>
        <button className={"preset-btn" + (view === "calendar" ? " active" : "")} style={{ minWidth: 96 }}
          onClick={() => setView("calendar")}>Calendar view</button>
      </div>

      {selectedIds.length > 0 && view === "list" && (
        <div className="banner" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <span><b>{selectedIds.length}</b> post{selectedIds.length === 1 ? "" : "s"} selected</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn ghost small" onClick={() => setShowCancelSelectedModal(true)}>Cancel Selected</button>
            <button className="btn ghost small" onClick={() => setSelectedIds([])}>Deselect All</button>
          </div>
        </div>
      )}

      {view === "list" ? (
        rows.length === 0 ? (
          <div className="empty">Nothing scheduled for this page. Go to <b>Content</b> to queue posts.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th style={{ width: 36 }}>
                  <input type="checkbox"
                    checked={rows.length > 0 && selectedIds.length === rows.length}
                    onChange={toggleSelectAll} />
                </th>
                <th>When</th>
                <th>Content</th>
                <th>Type</th>
                <th>Page</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const cid = r.content_item_id;
                const pnMsg = postNowMsg[cid];
                return (
                  <tr key={r.id}>
                    <td>
                      <input type="checkbox"
                        checked={selectedIds.includes(r.id)}
                        onChange={() => toggleSelectRow(r.id)} />
                    </td>
                    <td style={{ fontFamily: "var(--util)", whiteSpace: "nowrap", verticalAlign: "top", paddingTop: 12 }}>
                      {editingRowId === r.id ? (
                        <input type="datetime-local" value={editingTimeVal}
                          onChange={(e) => setEditingTimeVal(e.target.value)}
                          style={{ fontSize: 13, padding: "3px 6px" }} />
                      ) : (
                        fmt(r.scheduled_time_utc, displayTzOffset)
                      )}
                    </td>
                    <td style={{ maxWidth: 300 }}>
                      <div style={{ fontFamily: "var(--util)", fontSize: 12.5, fontWeight: 500 }}>{r.file_name}</div>
                      {r.caption && (
                        <div style={{ color: "var(--ink-soft)", fontSize: 12, marginTop: 3, lineHeight: 1.4 }}>
                          {truncate(r.caption, 100)}
                        </div>
                      )}
                      {r.hashtags?.length > 0 && (
                        <div style={{ display: "flex", flexWrap: "wrap", gap: 3, marginTop: 4 }}>
                          {r.hashtags.slice(0, 4).map((h, i) => (
                            <span key={i} className="tag" style={{ fontSize: 11 }}>
                              {h.startsWith("#") ? h : "#" + h}
                            </span>
                          ))}
                          {r.hashtags.length > 4 && (
                            <span className="tag" style={{ fontSize: 11 }}>+{r.hashtags.length - 4} more</span>
                          )}
                        </div>
                      )}
                      {r.sound_title && (
                        <div style={{ fontFamily: "var(--util)", fontSize: 11, color: "var(--ink-faint)", marginTop: 3 }}>
                          ♫ {r.sound_title}
                        </div>
                      )}
                      {pnMsg && (
                        <div style={{
                          fontSize: 11.5, marginTop: 4, fontWeight: 500,
                          color: pnMsg.ok === true ? "#2f7d54" : pnMsg.ok === false ? "#c0392b" : "var(--ink-soft)"
                        }}>
                          {pnMsg.text}
                        </div>
                      )}
                    </td>
                    <td style={{ whiteSpace: "nowrap", verticalAlign: "top", paddingTop: 12 }}>
                      <span style={{
                        fontSize: 11,
                        padding: "3px 8px",
                        borderRadius: 6,
                        fontWeight: 600,
                        background: r.media_type === "photo" ? "rgba(59, 130, 246, 0.12)" : r.media_type === "text" ? "rgba(168, 85, 247, 0.12)" : "rgba(16, 185, 129, 0.12)",
                        color: r.media_type === "photo" ? "#60a5fa" : r.media_type === "text" ? "#c084fc" : "#34d399",
                        border: `1px solid ${r.media_type === "photo" ? "rgba(59, 130, 246, 0.25)" : r.media_type === "text" ? "rgba(168, 85, 247, 0.25)" : "rgba(16, 185, 129, 0.25)"}`,
                        display: "inline-flex",
                        alignItems: "center",
                        gap: 4
                      }}>
                        {r.media_type === "photo" ? "Photo" : r.media_type === "text" ? "Text" : "Reel"}
                      </span>
                    </td>
                    <td style={{ whiteSpace: "nowrap", verticalAlign: "top", paddingTop: 12 }}>{r.page_name}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap", verticalAlign: "top", paddingTop: 8 }}>
                      {editingRowId === r.id ? (
                        <>
                          <button className="btn small" disabled={busy} onClick={() => saveInlineTime(r.id)}>Save</button>{" "}
                          <button className="btn ghost small" onClick={() => setEditingRowId(null)}>Discard</button>
                        </>
                      ) : (
                        <>
                          <button
                            className="btn small"
                            style={{ background: "#2f7d54", color: "#fff", borderColor: "transparent", marginBottom: 4 }}
                            disabled={postingNowId === cid || busy}
                            onClick={() => postRowNow(r)}
                          >
                            {postingNowId === cid ? "Posting…" : "Post Now"}
                          </button>
                          <br />
                          <button className="btn ghost small" disabled={busy} onClick={() => startEditInline(r)}>Change time</button>{" "}
                          <button className="btn ghost small" disabled={busy} onClick={() => cancel(r.id)}>Cancel</button>
                        </>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )
      ) : (
        <>
          <div className="cal-toolbar">
            <div className="cal-nav">
              <button className="btn ghost small" onClick={() => shiftWeek(-1)}>&lt; Previous</button>
              <button className="btn ghost small" onClick={goToday}>Today</button>
              <button className="btn ghost small" onClick={() => shiftWeek(1)}>Next &gt;</button>
            </div>
            <div className="cal-label">{weekStart ? fmtWeekLabel(weekStart) : ""}</div>
          </div>
          {calLoading ? (
            <div className="empty">Loading week…</div>
          ) : (
            <div className="cal-grid">
              {days.map((d, i) => {
                const dayRows = byDay[d] || [];
                const shown = dayRows.slice(0, 4);
                const extra = dayRows.length - shown.length;
                return (
                  <div key={d} className={"cal-day" + (d === todayStr ? " today" : "")}>
                    <div className="cal-day-head">{WEEKDAY_LABELS[i]}</div>
                    <div className="cal-day-num">{Number(d.slice(8, 10))}</div>
                    {dayRows.length === 0 && <div className="cal-empty">No posts</div>}
                    {shown.map((r) => (
                      <button key={r.id} className={"cal-chip " + r.status} onClick={() => setChipDetail(r)}>
                        {fmtTimeOnly(r.scheduled_time_utc, displayTzOffset)} · {truncate(r.file_name, 16)}
                      </button>
                    ))}
                    {extra > 0 && <div className="cal-more">+{extra} more</div>}
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}

      {abandonedRows.length > 0 && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 32, marginBottom: 8 }}>
            <div className="sec-label" style={{ margin: 0 }}>
              Failed / Abandoned Posts ({abandonedRows.length})
            </div>
            <button
              className="btn small"
              style={{ background: "#2f7d54", color: "#fff", borderColor: "transparent", fontWeight: 600 }}
              onClick={() => setShowRescueModal(true)}
            >
              ✦ Rescue & Reflow All ({abandonedRows.length})
            </button>
          </div>
          <table>
            <thead><tr><th>File</th><th>Page</th><th>Last error</th><th></th></tr></thead>
            <tbody>
              {abandonedRows.map((r) => (
                <tr key={r.id}>
                  <td>{r.file_name}</td>
                  <td>{r.page_name}</td>
                  <td style={{ fontSize: 12.5, color: "var(--ink-soft)" }}>{truncate(r.error || "unknown error", 80)}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button className="btn ghost small" onClick={() => retry(r.id)}>Retry</button>{" "}
                    <button className="btn ghost small" onClick={() => dismiss(r.id)}>Dismiss</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {showRescueModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && !rescuing && setShowRescueModal(false)}>
          <div className="modal" style={{ width: "min(500px, 92vw)" }}>
            <h2>Rescue Abandoned Posts</h2>
            <p style={{ marginTop: 8, fontSize: 14, lineHeight: 1.5, color: "var(--ink-soft)" }}>
              Restore <b>{abandonedRows.length}</b> abandoned post{abandonedRows.length === 1 ? "" : "s"} back into the active scheduling queue.
            </p>
            <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 10 }}>
              <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 6, background: rescueReflow ? "var(--card)" : "transparent" }}>
                <input
                  type="radio"
                  name="rescueMode"
                  checked={rescueReflow}
                  onChange={() => setRescueReflow(true)}
                  style={{ marginTop: 3 }}
                />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>Smooth Reflow into Future Slots (Recommended)</div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 2 }}>
                    Evenly spaces rescued posts after the last currently scheduled post for each page. Avoids Meta rate-limiting and burst flags.
                  </div>
                </div>
              </label>
              <label style={{ display: "flex", gap: 10, alignItems: "flex-start", cursor: "pointer", padding: "10px 12px", border: "1px solid var(--line)", borderRadius: 6, background: !rescueReflow ? "var(--card)" : "transparent" }}>
                <input
                  type="radio"
                  name="rescueMode"
                  checked={!rescueReflow}
                  onChange={() => setRescueReflow(false)}
                  style={{ marginTop: 3 }}
                />
                <div>
                  <div style={{ fontWeight: 600, fontSize: 13.5 }}>Direct to Pending (Immediate)</div>
                  <div style={{ fontSize: 12.5, color: "var(--ink-soft)", marginTop: 2 }}>
                    Sets all rescued posts as due immediately. Posts will fire in small batches per cron run.
                  </div>
                </div>
              </label>
            </div>
            <div className="modal-actions" style={{ marginTop: 24 }}>
              <button className="btn ghost" disabled={rescuing} onClick={() => setShowRescueModal(false)}>Cancel</button>
              <button
                className="btn"
                style={{ background: "#2f7d54", color: "#fff", borderColor: "transparent", fontWeight: 600 }}
                disabled={rescuing}
                onClick={handleRescueAll}
              >
                {rescuing ? "Rescuing posts…" : "Confirm & Rescue"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCancelSelectedModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowCancelSelectedModal(false)}>
          <div className="modal" style={{ width: "min(440px, 92vw)" }}>
            <h2>Cancel Selected Posts</h2>
            <p style={{ marginTop: 8, fontSize: 14 }}>
              Are you sure you want to cancel <b>{selectedIds.length}</b> selected post{selectedIds.length === 1 ? "" : "s"}?
            </p>
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn ghost" onClick={() => setShowCancelSelectedModal(false)}>Abort</button>
              <button className="btn" style={{ background: "#c0392b", color: "#fff", borderColor: "transparent" }}
                disabled={busy} onClick={confirmCancelSelected}>
                {busy ? "Canceling…" : "Confirm"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCancelAllModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowCancelAllModal(false)}>
          <div className="modal" style={{ width: "min(460px, 92vw)" }}>
            <h2>Cancel All Pending Posts</h2>
            <p style={{ marginTop: 8, fontSize: 14, lineHeight: 1.5 }}>
              This will cancel all <b>{rows.length}</b> scheduled post{rows.length === 1 ? "" : "s"} for <b>{page?.name || "this page"}</b>. This cannot be undone.
            </p>
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn ghost" onClick={() => setShowCancelAllModal(false)}>Keep them</button>
              <button className="btn" style={{ background: "#c0392b", color: "#fff", borderColor: "transparent" }}
                disabled={busy} onClick={confirmCancelAll}>
                {busy ? "Canceling all…" : "Yes, cancel all"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCopyModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && !copying && setShowCopyModal(false)}>
          <div className="modal" style={{ width: "min(520px, 92vw)" }}>
            <h2>Copy Schedule to Other Pages</h2>

            {!copyResult ? (
              <>
                <div className="dossier-box" style={{ marginTop: 12, background: "var(--card)" }}>
                  <div className="h">What will be copied</div>
                  <div style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.8 }}>
                    Posts per day: <b>{postsPerDay}</b><br />
                    Gap: <b>{GAP_PRESETS.find((g) => g.value === gapMinutes)?.label || `${gapMinutes} minutes`}</b><br />
                    Start time: <b>{startTime ? fmt12h(startTime) : "—"}</b><br />
                    Start date: <b>{fmtDateUS(startDate)}</b>
                  </div>
                </div>

                <p style={{ marginTop: 14, fontSize: 13.5, color: "var(--ink-soft)" }}>
                  Pick which pages to copy this schedule to. Each page uses its own timezone and its own unposted videos — only that page's pending queue is added to.
                </p>

                <div style={{ maxHeight: 260, overflowY: "auto", marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  {pages.filter((p) => p.id !== activePage).map((p) => {
                    const pending = p.stats?.pending_scheduled || 0;
                    const checked = copyTargetIds.includes(p.id);
                    return (
                      <label key={p.id} style={{ display: "flex", flexDirection: "column", gap: 4, padding: "6px 8px", border: "1px solid var(--line)", borderRadius: 6 }}>
                        <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <input type="checkbox" checked={checked} onChange={() => toggleCopyTarget(p.id)} />
                          <span style={{ fontWeight: 500 }}>{p.name}</span>
                          <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--ink-soft)" }}>
                            {pending > 0 ? `${pending} pending` : "no pending posts"}
                          </span>
                        </span>
                        {checked && pending > 0 && (
                          <span style={{ fontSize: 12, color: "#c0392b" }}>
                            {p.name} already has {pending} pending post{pending === 1 ? "" : "s"}. Copying will ADD to the existing schedule — cancel existing posts first if you want a clean slate.
                          </span>
                        )}
                      </label>
                    );
                  })}
                  {pages.filter((p) => p.id !== activePage).length === 0 && (
                    <div className="empty" style={{ padding: 12 }}>No other pages to copy to.</div>
                  )}
                </div>

                <div className="modal-actions" style={{ marginTop: 20 }}>
                  <button className="btn ghost" disabled={copying} onClick={() => setShowCopyModal(false)}>Cancel</button>
                  <button className="btn" disabled={copying || copyTargetIds.length === 0} onClick={copyToOtherPages}>
                    {copying ? "Copying…" : `Copy & Schedule${copyTargetIds.length > 0 ? ` (${copyTargetIds.length})` : ""}`}
                  </button>
                </div>
                {copying && <div className="progress-bar"><div className="progress-bar-fill indeterminate" /></div>}
              </>
            ) : (
              <>
                <p style={{ marginTop: 12, fontSize: 15 }}>
                  Scheduled <b>{copyResult.totalScheduled}</b> post{copyResult.totalScheduled === 1 ? "" : "s"} across <b>{copyResult.pagesWithPosts}</b> page{copyResult.pagesWithPosts === 1 ? "" : "s"}.
                </p>
                <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 6, fontSize: 13.5 }}>
                  {copyResult.succeeded.map((p) => (
                    <div key={p.id}>
                      ✓ <b>{p.name}</b>: {p.outcome.value?.scheduled ?? 0} post{(p.outcome.value?.scheduled ?? 0) === 1 ? "" : "s"} scheduled
                      {p.outcome.value?.scheduled === 0 && <span style={{ color: "var(--ink-soft)" }}> — no unposted videos left to schedule</span>}
                    </div>
                  ))}
                  {copyResult.failed.map((p) => (
                    <div key={p.id} style={{ color: "#c0392b" }}>
                      ✗ <b>{p.name}</b>: {p.outcome.reason?.message || "failed"}
                    </div>
                  ))}
                </div>
                <div className="modal-actions" style={{ marginTop: 20 }}>
                  <button className="btn" onClick={() => setShowCopyModal(false)}>Done</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {showClearPostedModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowClearPostedModal(false)}>
          <div className="modal" style={{ width: "min(460px, 92vw)" }}>
            <h2>Clear Posted History</h2>
            <p style={{ marginTop: 8, fontSize: 14, lineHeight: 1.5 }}>
              Are you sure? This will remove <b>{page?.stats?.total_posted || 0}</b> posted record{(page?.stats?.total_posted || 0) === 1 ? "" : "s"} and cancel any pending posts for <b>{page?.name || "this page"}</b>. The video library stays intact — every video becomes available to schedule again.
            </p>
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn ghost" onClick={() => setShowClearPostedModal(false)}>Keep them</button>
              <button className="btn" style={{ background: "#c0392b", color: "#fff", borderColor: "transparent" }}
                disabled={clearingPosted} onClick={confirmClearPosted}>
                {clearingPosted ? "Clearing…" : "Yes, clear posted history"}
              </button>
            </div>
          </div>
        </div>
      )}

      {chipDetail && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setChipDetail(null)}>
          <div className="modal" style={{ width: "min(480px, 92vw)" }}>
            <h2>{chipDetail.file_name}</h2>
            <div className="kv"><span className="k">When</span><span className="v">{fmt(chipDetail.scheduled_time_utc, displayTzOffset)}</span></div>
            <div className="kv"><span className="k">Status</span><span className="v" style={{ textTransform: "capitalize" }}>{chipDetail.status}</span></div>
            {chipDetail.caption_used && (
              <div className="dossier-box">
                <div className="h">Caption</div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, marginTop: 8 }}>{chipDetail.caption_used}</div>
              </div>
            )}
            <div className="modal-actions">
              {chipDetail.status === "pending" && (
                <>
                  <button className="btn ghost" onClick={() => { startEditInline(chipDetail); setChipDetail(null); }}>Reschedule</button>
                  <button className="btn ghost" onClick={async () => { await cancel(chipDetail.id); setChipDetail(null); }}>Cancel post</button>
                </>
              )}
              <button className="btn" onClick={() => setChipDetail(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
