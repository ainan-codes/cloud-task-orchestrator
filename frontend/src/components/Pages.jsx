import React, { useState, useRef, useEffect } from "react";
import { api } from "../api.js";
import CountUp from "./CountUp.jsx";
import { FacebookIcon, GoogleDriveIcon } from "./BrandIcons.jsx";
import {
  X,
  Key,
  Calendar,
  FileText,
  Sparkles,
  BookOpen,
  Eye,
  EyeOff,
  Check,
  AlertCircle,
  ExternalLink,
  Zap,
  Copy,
  CheckCheck,
  Clock,
  Sun,
  Video,
  Camera,
} from "lucide-react";
import AutopilotModal from "./AutopilotModal.jsx";

const POLL_INTERVAL_MS = 2000;

// Past this many pages the table stops being scannable, so it windows.
const PAGES_PER_SCREEN = 30;

const BLANK = {
  name: "", fb_page_id: "", fb_access_token: "", drive_folder_id: "",
  utc_offset_hours: 0, extra_hashtags: "", caption_footer: "",
  schedule_mode: "spread", spread_start_hour: 9, spread_end_hour: 21,
  fixed_times: "09:00,13:00,18:00", per_run: 6, is_active: true,
  hashtag_pool: [],
};

/** Cheap, always-on heuristic from data already on hand (no live FB call —
 * that only happens when a health panel is expanded). */
function healthDot(p) {
  const s = p.stats || {};
  if (p.token_status === "invalid" || s.token_status === "invalid") return "red";
  if ((s.failed_count || 0) > 0 || (s.abandoned_count || 0) > 0) return "amber";
  const queueDepth = s.pending_scheduled || 0;
  const remaining = s.remaining_unposted || 0;
  if (queueDepth === 0 && remaining === 0) return "red";
  if (queueDepth < 3) return "amber";
  return "green";
}

function fmtWhen(iso) {
  return iso ? new Date(iso).toLocaleString() : "Never";
}

export default function Pages({
  pages,
  onReload,
  initialShowForm,
  onFormClosed,
  initialShowGuide,
  onGuideClosed,
}) {
  const modalBodyRef = useRef(null);
  const [pageOffset, setPageOffset] = useState(0);
  const [pageSearch, setPageSearch] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [editingPageId, setEditingPageId] = useState(null);
  const [form, setForm] = useState(BLANK);
  const [poolInput, setPoolInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [msgIsErr, setMsgIsErr] = useState(false);

  const DEFAULT_AUTOPILOT = {
    automation_enabled: false,
    video_enabled: true,
    photo_enabled: true,
    text_enabled: true,
    daily_video_limit: 2,
    daily_photo_limit: 2,
    daily_text_limit: 2,
    posting_window_start: 9,
    posting_window_end: 21,
    min_spacing_minutes: 30,
    content_source: "drive",
    ai_captioning: false,
    hashtag_rotation: false,
    duplicate_protection: true,
    smart_scheduling: false,
    auto_retry: false,
    analytics_sync: false,
    monetization_tracking: false,
  };
  const [profileForm, setProfileForm] = useState(DEFAULT_AUTOPILOT);
  const [previewData, setPreviewData] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);
  const [profileMsg, setProfileMsg] = useState("");

  const [tokenTesting, setTokenTesting] = useState(false);
  const [tokenTestResult, setTokenTestResult] = useState(null);
  const [hasExistingToken, setHasExistingToken] = useState(false);
  const [showToken, setShowToken] = useState(false);
  const [saEmail, setSaEmail] = useState("");
  const [copiedSaEmail, setCopiedSaEmail] = useState(false);
  const [showGuideModal, setShowGuideModal] = useState(false);
  const [autopilotPage, setAutopilotPage] = useState(null);

  const [syncingId, setSyncingId] = useState(null);
  const [liveSync, setLiveSync] = useState(null);
  const [stuckSyncId, setStuckSyncId] = useState(null);
  const pollTimer = useRef(null);

  const [healthOpenId, setHealthOpenId] = useState(null);
  const [healthData, setHealthData] = useState({});
  const [healthLoading, setHealthLoading] = useState(null);

  useEffect(() => {
    api.getServiceAccount()
      .then((res) => {
        if (res && res.service_account_email) {
          setSaEmail(res.service_account_email);
        }
      })
      .catch(() => {});
  }, []);

  function copySaEmail() {
    const emailToCopy = saEmail || "reel-ledger@reel-ledger-505014.iam.gserviceaccount.com";
    navigator.clipboard.writeText(emailToCopy);
    setCopiedSaEmail(true);
    setTimeout(() => setCopiedSaEmail(false), 2000);
  }

  // Stop any in-flight poll if the tab is switched away from (unmounts this component).
  useEffect(() => () => clearTimeout(pollTimer.current), []);

  useEffect(() => {
    if (initialShowForm) {
      openAddModal();
      if (onFormClosed) onFormClosed();
    }
  }, [initialShowForm]);

  useEffect(() => {
    if (initialShowGuide) {
      setShowGuideModal(true);
      if (onGuideClosed) onGuideClosed();
    }
  }, [initialShowGuide]);

  const [formTab, setFormTab] = useState("credentials");

  function set(k, v) { setForm((f) => ({ ...f, [k]: v })); }

  function extractDriveFolderId(val) {
    if (!val) return "";
    const parts = val.split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
    if (parts.length > 1) {
      const ids = parts.map((p) => {
        const folderMatch = p.match(/\/folders\/([a-zA-Z0-9_-]+)/);
        if (folderMatch) return folderMatch[1];
        const idMatch = p.match(/id=([a-zA-Z0-9_-]+)/);
        if (idMatch) return idMatch[1];
        return p;
      }).filter(Boolean);
      return Array.from(new Set(ids)).join(", ");
    }
    const trimmed = val.trim();
    const folderMatch = trimmed.match(/\/folders\/([a-zA-Z0-9_-]+)/);
    if (folderMatch) return folderMatch[1];
    const idMatch = trimmed.match(/id=([a-zA-Z0-9_-]+)/);
    if (idMatch) return idMatch[1];
    return trimmed;
  }

  function switchTab(t) {
    setFormTab(t);
    if (modalBodyRef.current) modalBodyRef.current.scrollTop = 0;
  }

  function openAddModal() {
    setEditingPageId(null);
    setHasExistingToken(false);
    setShowToken(false);
    setForm(BLANK);
    setPoolInput("");
    setTokenTestResult(null);
    setMsg("");
    setMsgIsErr(false);
    setProfileForm(DEFAULT_AUTOPILOT);
    setPreviewData(null);
    setProfileMsg("");
    setFormTab("credentials");
    if (modalBodyRef.current) modalBodyRef.current.scrollTop = 0;
    setShowForm(true);
  }

  function openEditModal(p, tab = "credentials") {
    setEditingPageId(p.id);
    setHasExistingToken(Boolean(p.has_token || p.fb_access_token_encrypted || p.token_health));
    setShowToken(false);
    setForm({
      name: p.name || "",
      fb_page_id: p.fb_page_id || "",
      fb_access_token: "",
      drive_folder_id: p.drive_folder_id || "",
      utc_offset_hours: p.utc_offset_hours ?? 0,
      extra_hashtags: Array.isArray(p.extra_hashtags) ? p.extra_hashtags.join(", ") : (p.extra_hashtags || ""),
      caption_footer: p.caption_footer || "",
      schedule_mode: p.schedule_mode || "spread",
      spread_start_hour: p.spread_start_hour ?? 9,
      spread_end_hour: p.spread_end_hour ?? 21,
      fixed_times: Array.isArray(p.fixed_times) ? p.fixed_times.join(",") : (p.fixed_times || "09:00,13:00,18:00"),
      per_run: p.per_run ?? 6,
      is_active: p.is_active ?? true,
      hashtag_pool: Array.isArray(p.hashtag_pool) ? p.hashtag_pool : [],
    });
    setPoolInput("");
    setTokenTestResult(null);
    setMsg("");
    setMsgIsErr(false);
    setProfileForm({ ...DEFAULT_AUTOPILOT, ...(p.automation_profile || {}) });
    setPreviewData(null);
    setProfileMsg("");
    if (p.id) {
      api.pageAutomationProfile(p.id).then((prof) => {
        if (prof) setProfileForm(prof);
      }).catch(() => {});
    }
    setFormTab(tab);
    if (modalBodyRef.current) modalBodyRef.current.scrollTop = 0;
    setShowForm(true);
  }

  function updateProfileField(k, v) {
    setProfileForm((prev) => ({ ...prev, [k]: v }));
  }

  async function loadPreview() {
    if (!editingPageId) return;
    setLoadingPreview(true);
    setProfileMsg("");
    try {
      const res = await api.previewPageSchedule(editingPageId, profileForm);
      setPreviewData(res);
    } catch (e) {
      setProfileMsg("Preview error: " + e.message);
    } finally {
      setLoadingPreview(false);
    }
  }

  async function handleSaveProfile() {
    if (!editingPageId) {
      setProfileMsg("Please save the page credentials first before saving Autopilot profile.");
      return;
    }
    setSavingProfile(true);
    setProfileMsg("");
    try {
      await api.savePageAutomationProfile(editingPageId, profileForm);
      setProfileMsg("✓ Autopilot profile saved! Future schedule dynamically rebalanced.");
      onReload();
    } catch (e) {
      setProfileMsg("Save error: " + e.message);
    } finally {
      setSavingProfile(false);
    }
  }

  async function testTokenLive() {
    if (!form.fb_access_token && !hasExistingToken) {
      setTokenTestResult({ valid: false, error: "Please paste a token first" });
      return;
    }
    setTokenTesting(true);
    setTokenTestResult(null);
    try {
      const pageId = editingPageId || 0;
      const res = await api.testPageToken(pageId, form.fb_access_token || "", form.fb_page_id);
      setTokenTestResult(res);
    } catch (e) {
      setTokenTestResult({ valid: false, error: e.message });
    } finally {
      setTokenTesting(false);
    }
  }

  function addPoolTag(e) {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const raw = poolInput.trim();
    if (!raw) return;
    const tag = raw.startsWith("#") ? raw : "#" + raw;
    setForm((f) => (f.hashtag_pool.includes(tag) || f.hashtag_pool.length >= 50)
      ? f : { ...f, hashtag_pool: [...f.hashtag_pool, tag] });
    setPoolInput("");
  }
  function removePoolTag(tag) {
    setForm((f) => ({ ...f, hashtag_pool: f.hashtag_pool.filter((t) => t !== tag) }));
  }

  function cleanDriveFolderId(val) {
    if (!val) return "";
    const s = String(val).trim();
    const m = s.match(/folders\/([a-zA-Z0-9_-]+)/) || s.match(/id=([a-zA-Z0-9_-]+)/) || s.match(/([a-zA-Z0-9_-]{25,})/);
    return m ? (m[1] || m[0]) : s;
  }

  function cleanFbPageId(val) {
    if (!val) return "";
    const s = String(val).trim();
    if (s.includes("@")) return ""; // Suppress browser autofilled emails
    const digits = s.match(/\d{10,}/);
    return digits ? digits[0] : s;
  }

  async function save() {
    setBusy(true); setMsg(""); setMsgIsErr(false);
    try {
      const payload = {
        ...form,
        name: (form.name || "").trim(),
        fb_page_id: cleanFbPageId(form.fb_page_id),
        drive_folder_id: cleanDriveFolderId(form.drive_folder_id),
        utc_offset_hours: Number(form.utc_offset_hours),
        spread_start_hour: Number(form.spread_start_hour),
        spread_end_hour: Number(form.spread_end_hour),
        per_run: Number(form.per_run),
        extra_hashtags: typeof form.extra_hashtags === "string"
          ? form.extra_hashtags.split(",").map((s) => s.trim()).filter(Boolean)
          : (form.extra_hashtags || []),
        fixed_times: typeof form.fixed_times === "string"
          ? form.fixed_times.split(",").map((s) => s.trim()).filter(Boolean)
          : (form.fixed_times || []),
        hashtag_pool: form.hashtag_pool || [],
      };
      if (form.fb_access_token && form.fb_access_token.trim()) {
        payload.fb_access_token = form.fb_access_token.trim();
      } else if (editingPageId) {
        delete payload.fb_access_token;
      }
      if (editingPageId) {
        await api.updatePage(editingPageId, payload);
      } else {
        await api.addPage(payload);
      }
      setShowForm(false); setEditingPageId(null); setForm(BLANK); setPoolInput(""); onReload();
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  async function sync(id) {
    setMsg(""); setMsgIsErr(false);
    setStuckSyncId(null);
    clearTimeout(pollTimer.current);
    setSyncingId(id);
    setLiveSync({ found: 0, added: 0 });

    const poll = async () => {
      try {
        const s = await api.syncStatus(id);
        if (s.status === "running") {
          setLiveSync({ found: s.found || 0, added: s.added || 0 });
          pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
          return;
        }
        setSyncingId(null);
        setLiveSync(null);
        if (s.error) {
          setMsg(`Sync failed: ${s.error}`); setMsgIsErr(true);
          setStuckSyncId(id);
        } else {
          const imported = s.added || 0;
          const updated = s.updated || 0;
          const matched = s.matched_captions ?? 0;
          const missing = s.missing_captions ?? 0;
          let details = `${imported} video${imported === 1 ? "" : "s"} imported`;
          if (updated > 0) {
            details += `, ${updated} updated`;
          }
          details += ` (${matched} caption${matched === 1 ? "" : "s"} matched`;
          if (missing > 0) {
            details += `, ${missing} missing`;
          }
          details += `)`;
          setMsg(`Synced — ${details}.`);
          setMsgIsErr(false);
        }
        onReload(); // pulls fresh page_stats so the Library count reflects the sync
      } catch (e) {
        // If an intermittent status check fails, retry polling
        pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);
      }
    };

    // Begin polling immediately for live real-time feedback
    pollTimer.current = setTimeout(poll, POLL_INTERVAL_MS);

    try {
      const res = await api.syncPage(id);
      if (res && res.status === "complete") {
        clearTimeout(pollTimer.current);
        poll();
      }
    } catch (e) {
      // If the POST request timed out on an edge proxy, the backend is still running; poll continues
      console.warn("syncPage POST request notice:", e);
    }
  }

  async function resetStuckSync(id) {
    setMsg(""); setMsgIsErr(false);
    try {
      await api.resetSync(id);
      setStuckSyncId(null);
      setMsg("Sync lock cleared — you can sync again.");
    } catch (e) {
      setMsg(`Reset failed: ${e.message}`); setMsgIsErr(true);
    }
  }

  async function toggleHealth(id) {
    if (healthOpenId === id) { setHealthOpenId(null); return; }
    setHealthOpenId(id);
    setHealthLoading(id);
    try {
      const h = await api.pageHealth(id);
      setHealthData((m) => ({ ...m, [id]: h }));
    } catch (e) {
      setHealthData((m) => ({ ...m, [id]: { error: e.message } }));
    } finally {
      setHealthLoading(null);
    }
  }

  async function remove(id) {
    if (!confirm("Remove this page and its records from the ledger?")) return;
    await api.deletePage(id); onReload();
  }

  // Filter + window the list. The pages themselves (name, token, offset,
  // hashtag settings) are already loaded once by App for every page picker in
  // the app, and each row's counts come from the page_stats view rather than
  // from any per-page query — so this windows what's rendered rather than
  // issuing a second paged fetch that would fight onReload. The backend's
  // GET /api/pages?limit&offset&search does the same server-side for when a
  // full list is no longer sensible to hold at all.
  const q = pageSearch.trim().toLowerCase();
  const matching = q
    ? pages.filter(
        (p) =>
          p.name?.toLowerCase().includes(q) ||
          String(p.fb_page_id || "").toLowerCase().includes(q)
      )
    : pages;
  const paginate = matching.length > PAGES_PER_SCREEN;
  const offset = paginate ? Math.min(pageOffset, Math.max(0, matching.length - 1)) : 0;
  const shown = paginate ? matching.slice(offset, offset + PAGES_PER_SCREEN) : matching;
  const from = matching.length === 0 ? 0 : offset + 1;
  const to = offset + shown.length;

  return (
    <>
      <div className="eyebrow">PROFILE DISPATCH · FACEBOOK NETWORK</div>
      <h1 className="headline">Pages & Channels</h1>
      <p className="lede">
        Each page carries its own access token and dedicated Google Drive folder.
        Add a page, connect its media folder, then trigger <b>Sync</b> to ingest
        content into the publishing ledger.
      </p>
      <div className="toolbar" style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
        <button className="btn" onClick={openAddModal}>+ Connect New Page</button>
        <button
          className="btn"
          style={{ background: "rgba(99, 102, 241, 0.15)", color: "#818cf8", border: "1px solid rgba(99, 102, 241, 0.3)" }}
          onClick={() => setAutopilotPage(pages[0] || true)}
          disabled={!pages.length}
        >
          <Sparkles size={14} style={{ marginRight: 6 }} />
          Launch Autopilot
        </button>
        <button className="btn ghost" onClick={() => setShowGuideModal(true)}>
          <BookOpen size={14} style={{ marginRight: 6 }} />
          Token Guide
        </button>
      </div>

      <hr className="rule" />

      {pages.length > PAGES_PER_SCREEN && (
        <div className="field" style={{ maxWidth: 340 }}>
          <label>Find a page</label>
          <input
            type="search"
            value={pageSearch}
            placeholder="Page name or Facebook ID…"
            onChange={(e) => { setPageSearch(e.target.value); setPageOffset(0); }}
          />
        </div>
      )}

      {msg && <div className={"banner" + (msgIsErr ? " err" : "")}>{msg}</div>}

      {pages.length === 0 ? (
        <div className="empty">No pages yet. Add your first one above.</div>
      ) : matching.length === 0 ? (
        <div className="empty">No page matches “{pageSearch.trim()}”.</div>
      ) : (
        <>
        <table>
          <thead>
            <tr>
              <th>Page</th><th>Facebook ID</th><th>Drive folder</th>
              <th>Library</th><th>Posted</th><th></th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <React.Fragment key={p.id}>
                <tr>
                  <td style={{ fontWeight: 500 }}>
                    <span
                      className={"health-dot " + healthDot(p)}
                      onClick={() => toggleHealth(p.id)}
                      title="Click for page health"
                    />
                    {p.name}
                  </td>
                  <td style={{ fontFamily: "var(--util)", color: "var(--ink-soft)" }}>{p.fb_page_id}</td>
                  <td style={{ fontFamily: "var(--util)", fontSize: 12 }}>
                    {p.drive_folder_id ? p.drive_folder_id.slice(0, 14) + "…" : <span className="err">not set</span>}
                  </td>
                  <td><CountUp value={p.stats?.total_content ?? 0} /></td>
                  <td><CountUp value={p.stats?.total_posted ?? 0} /></td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button className="btn ghost small" disabled={syncingId === p.id} onClick={() => sync(p.id)}>
                      {syncingId === p.id
                        ? `Syncing… (${liveSync?.found ?? 0} found so far)`
                        : "Sync"}
                    </button>{" "}
                    {stuckSyncId === p.id && (
                      <>
                        <button
                          className="btn ghost small"
                          title="Force-clear a sync lock stuck from a crashed/incomplete previous run"
                          onClick={() => resetStuckSync(p.id)}
                        >
                          Reset stuck sync
                        </button>{" "}
                      </>
                    )}
                    <button
                      className="btn ghost small"
                      style={{ color: "var(--accent)", borderColor: "rgba(99, 102, 241, 0.3)" }}
                      title="Launch 1-Click Autopilot Niche Batch for this page"
                      onClick={() => setAutopilotPage(p)}
                    >
                      <Sparkles size={13} style={{ marginRight: 4 }} />
                      Autopilot
                    </button>{" "}
                    <button
                      className="btn ghost small"
                      style={{ color: "#34d399", borderColor: "rgba(16, 185, 129, 0.3)" }}
                      title="Configure Autonomous Multi-Format Profile"
                      onClick={() => openEditModal(p, "autopilot")}
                    >
                      <Sparkles size={12} style={{ marginRight: 3 }} />
                      Profile
                    </button>{" "}
                    <button className="btn ghost small" disabled={syncingId === p.id} onClick={() => openEditModal(p)}>
                      Edit
                    </button>{" "}
                    <button className="btn ghost small" disabled={syncingId === p.id} onClick={() => remove(p.id)}>Remove</button>
                  </td>
                </tr>
                {healthOpenId === p.id && (
                  <tr>
                    <td colSpan={6}>
                      <div className="health-panel">
                        {healthLoading === p.id ? (
                          "Checking token & queue…"
                        ) : healthData[p.id]?.error ? (
                          <div className="err">{healthData[p.id].error}</div>
                        ) : healthData[p.id] ? (
                          <>
                            <div className="row">
                              <span className="k">Token status</span>
                              <span className="v">
                                {healthData[p.id].token_valid
                                  ? `Valid — page token for ${p.name} is active`
                                  : `Invalid — ${healthData[p.id].token_detail}`}
                              </span>
                            </div>
                            <div className="row">
                              <span className="k">Queue depth</span>
                              <span className="v">
                                {healthData[p.id].queue_depth} post{healthData[p.id].queue_depth === 1 ? "" : "s"} queued
                                {healthData[p.id].days_remaining != null
                                  ? ` · ${healthData[p.id].days_remaining} day(s) remaining`
                                  : ""}
                              </span>
                            </div>
                            <div className="row">
                              <span className="k">Last successful post</span>
                              <span className="v">{fmtWhen(healthData[p.id].last_posted_at)}</span>
                            </div>
                            <div className="row">
                              <span className="k">Last failed post</span>
                              <span className="v">
                                {healthData[p.id].last_failed_at
                                  ? `${fmtWhen(healthData[p.id].last_failed_at)} — ${healthData[p.id].last_error || "unknown error"}`
                                  : "None"}
                              </span>
                            </div>
                            <div className="row">
                              <span className="k">Content remaining</span>
                              <span className="v">
                                {healthData[p.id].content_remaining ?? "—"} unposted video(s) in library
                              </span>
                            </div>
                          </>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
          </tbody>
        </table>

        {paginate && (
          <div className="pager">
            <div className="pager-count">
              Showing {from}–{to} of {matching.length} page{matching.length === 1 ? "" : "s"}
              {q ? ` matching “${pageSearch.trim()}”` : ""}
            </div>
            <div className="pager-nav">
              <button className="btn ghost small" disabled={offset === 0}
                onClick={() => setPageOffset(Math.max(0, offset - PAGES_PER_SCREEN))}>
                &lt; Previous
              </button>
              <span className="pager-count">
                Page {Math.floor(offset / PAGES_PER_SCREEN) + 1} of{" "}
                {Math.max(1, Math.ceil(matching.length / PAGES_PER_SCREEN))}
              </span>
              <button className="btn ghost small"
                disabled={offset + PAGES_PER_SCREEN >= matching.length}
                onClick={() => setPageOffset(offset + PAGES_PER_SCREEN)}>
                Next &gt;
              </button>
            </div>
          </div>
        )}
        </>
      )}

      {showForm && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowForm(false)}>
          <div className="modal" style={{ width: "min(640px, 95vw)", padding: "22px 26px" }}>
            <form
              autoComplete="off"
              onSubmit={(e) => {
                e.preventDefault();
                save();
              }}
              style={{ display: "contents" }}
            >
              {/* Browser Autofill Decoy Fields */}
              <input type="text" name="prevent_autofill_username_decoy" style={{ display: "none" }} tabIndex={-1} autoComplete="off" />
              <input type="password" name="prevent_autofill_pwd_decoy" style={{ display: "none" }} tabIndex={-1} autoComplete="new-password" />

              {/* Modal Header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <FacebookIcon size={32} />
                  <div>
                    <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: "var(--text-primary)", letterSpacing: "-0.01em" }}>
                      {editingPageId ? `Configure Page: ${form.name || "Untitled"}` : "Connect Facebook Page"}
                    </h2>
                    <div style={{ fontSize: 12.5, color: "var(--text-secondary)", marginTop: 2 }}>
                      Publish and schedule media directly via Meta Graph API credentials
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setShowForm(false)}
                  style={{
                    background: "transparent",
                    border: "none",
                    color: "var(--text-muted)",
                    padding: 4,
                    borderRadius: 6,
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                  aria-label="Close"
                >
                  <X size={18} />
                </button>
              </div>

              {/* Modal Section Tabs */}
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(4, 1fr)",
                  gap: 6,
                  background: "var(--bg-input)",
                  padding: 4,
                  borderRadius: 8,
                  border: "1px solid var(--border-subtle)",
                  marginBottom: 18,
                }}
              >
                {[
                  { id: "credentials", label: "Credentials", icon: Key },
                  { id: "schedule", label: "Drive & Timing", icon: Calendar },
                  { id: "captions", label: "Captions & Tags", icon: FileText },
                  { id: "autopilot", label: "Autopilot", icon: Sparkles },
                ].map((tab) => {
                  const Icon = tab.icon;
                  const active = formTab === tab.id;
                  return (
                    <button
                      key={tab.id}
                      type="button"
                      className="btn btn-sm"
                      style={{
                        justifyContent: "center",
                        gap: 6,
                        fontSize: 12,
                        fontWeight: active ? 600 : 500,
                        background: active
                          ? tab.id === "autopilot"
                            ? "rgba(16, 185, 129, 0.15)"
                            : "var(--bg-card)"
                          : "transparent",
                        color: active
                          ? tab.id === "autopilot"
                            ? "#34d399"
                            : "var(--text-primary)"
                          : "var(--text-muted)",
                        border: active
                          ? tab.id === "autopilot"
                            ? "1px solid rgba(16, 185, 129, 0.3)"
                            : "1px solid var(--border-medium)"
                          : "1px solid transparent",
                      }}
                      onClick={() => switchTab(tab.id)}
                    >
                      <Icon size={13} />
                      <span>{tab.label}</span>
                    </button>
                  );
                })}
              </div>

              {/* Modal Scrollable Body */}
              <div ref={modalBodyRef} style={{ maxHeight: "68vh", overflowY: "auto", paddingRight: 6, paddingTop: 4, paddingBottom: 16 }}>
                {formTab === "credentials" && (
                  <div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Page Display Name</label>
                      <input
                        id="page_display_name_input"
                        name="page_display_name_input"
                        autoComplete="off"
                        data-lpignore="true"
                        data-1p-ignore="true"
                        value={form.name}
                        onChange={(e) => set("name", e.target.value)}
                        placeholder="e.g. Apex Media Group"
                      />
                      <div className="hint">An internal label to identify this account in your dashboard.</div>
                    </div>
                    <div className="field">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Facebook Page ID</label>
                        <span className="hint" style={{ fontSize: 11, color: "var(--text-muted)" }}>15–16 digit numeric ID</span>
                      </div>
                      <input
                        id="meta_page_numeric_id"
                        name="meta_page_numeric_id"
                        type="text"
                        inputMode="numeric"
                        autoComplete="off"
                        data-lpignore="true"
                        data-1p-ignore="true"
                        data-form-type="other"
                        value={form.fb_page_id}
                        onChange={(e) => {
                          const val = e.target.value;
                          if (val.includes("@")) return;
                          set("fb_page_id", val);
                        }}
                        placeholder="e.g. 100084928374920"
                        spellCheck={false}
                      />
                      <div className="hint">Found in Meta Business Suite or under your Facebook Page About tab.</div>
                    </div>
                    <div className="field">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)", margin: 0 }}>
                            Page Access Token
                          </label>
                          {hasExistingToken && !form.fb_access_token && (
                            <span style={{
                              fontSize: 11,
                              background: "rgba(16, 185, 129, 0.15)",
                              color: "#34d399",
                              padding: "2px 8px",
                              borderRadius: 10,
                              fontWeight: 500,
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 4
                            }}>
                              <Check size={11} /> Saved & Active
                            </span>
                          )}
                        </div>
                        <button
                          type="button"
                          className="btn btn-secondary btn-sm"
                          style={{ fontSize: 11, padding: "3px 8px", display: "inline-flex", alignItems: "center", gap: 5 }}
                          disabled={tokenTesting || (!form.fb_access_token && !hasExistingToken)}
                          onClick={testTokenLive}
                        >
                          <Zap size={12} />
                          <span>{tokenTesting ? "Verifying with Meta…" : "Test Token"}</span>
                        </button>
                      </div>
                      <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
                        <input
                          id="meta_graph_access_token"
                          name="meta_graph_access_token"
                          type={showToken ? "text" : "password"}
                          autoComplete="new-password"
                          data-lpignore="true"
                          data-1p-ignore="true"
                          data-form-type="other"
                          value={form.fb_access_token}
                          onChange={(e) => {
                            set("fb_access_token", e.target.value);
                            setTokenTestResult(null);
                          }}
                          placeholder={hasExistingToken ? "•••••••••••••••• (Leave blank to keep saved token, or paste new)" : "EAAB..."}
                          spellCheck={false}
                          style={{ paddingRight: 40, fontFamily: form.fb_access_token ? "var(--font-mono, monospace)" : "inherit" }}
                        />
                        <button
                          type="button"
                          onClick={() => setShowToken(!showToken)}
                          style={{
                            position: "absolute",
                            right: 8,
                            top: "50%",
                            transform: "translateY(-50%)",
                            background: "transparent",
                            border: "none",
                            color: "var(--text-muted)",
                            cursor: "pointer",
                            padding: 4,
                            display: "flex",
                            alignItems: "center",
                            justifyContent: "center",
                          }}
                          title={showToken ? "Hide token" : "Show token"}
                          tabIndex={-1}
                        >
                          {showToken ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                      {tokenTestResult && (
                        <div style={{
                          marginTop: 8,
                          padding: "8px 12px",
                          borderRadius: 6,
                          fontSize: 12,
                          background: tokenTestResult.valid ? "rgba(16, 185, 129, 0.1)" : "rgba(239, 68, 68, 0.1)",
                          color: tokenTestResult.valid ? "#34d399" : "#fca5a5",
                          border: `1px solid ${tokenTestResult.valid ? "rgba(16, 185, 129, 0.25)" : "rgba(239, 68, 68, 0.25)"}`,
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                        }}>
                          {tokenTestResult.valid ? <Check size={14} /> : <AlertCircle size={14} />}
                          <span>
                            {tokenTestResult.valid
                              ? `Verified: Token is valid for "${tokenTestResult.page_name}" (ID: ${tokenTestResult.page_id})`
                              : `Token verification failed: ${tokenTestResult.error}`}
                          </span>
                        </div>
                      )}
                      <div className="hint" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 6 }}>
                        <span>
                          {hasExistingToken && !form.fb_access_token
                            ? "Encrypted securely in database. Only paste here if rotating."
                            : "Encrypted at rest with AES-256; never exposed in client bundles."}
                        </span>
                        <button
                          type="button"
                          style={{
                            background: "transparent",
                            border: "none",
                            padding: 0,
                            color: "var(--accent-light)",
                            cursor: "pointer",
                            fontSize: 11.5,
                            display: "inline-flex",
                            alignItems: "center",
                            gap: 4,
                          }}
                          onClick={() => setShowGuideModal(true)}
                        >
                          <BookOpen size={12} />
                          <span>Step-by-step setup guide →</span>
                        </button>
                      </div>
                    </div>
                    <div className="field">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                          <GoogleDriveIcon size={16} />
                          <span>Google Drive Asset Folder</span>
                        </label>
                        <span className="hint" style={{ fontSize: 11, color: "var(--accent-light)" }}>
                          Single or multiple links supported
                        </span>
                      </div>
                      <input
                        id="drive_folder_id_credentials"
                        name="drive_folder_id_credentials"
                        autoComplete="off"
                        data-lpignore="true"
                        data-1p-ignore="true"
                        value={form.drive_folder_id}
                        onChange={(e) => set("drive_folder_id", extractDriveFolderId(e.target.value))}
                        placeholder="Paste folder link(s) or ID(s) — comma-separated for multiple folders"
                      />
                      <div style={{
                        marginTop: 8,
                        padding: "10px 12px",
                        background: "rgba(255, 255, 255, 0.03)",
                        borderRadius: 6,
                        border: "1px solid var(--border-subtle)",
                        fontSize: 12,
                      }}>
                        <div style={{ color: "var(--text-secondary)", marginBottom: 4 }}>
                          Share this Drive folder with your Google Service Account email (give <b>Viewer</b> access):
                        </div>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <code style={{ fontSize: 11.5, color: "var(--accent-light)", wordBreak: "break-all", fontFamily: "var(--font-mono, monospace)" }}>
                            {saEmail || "reel-ledger@reel-ledger-505014.iam.gserviceaccount.com"}
                          </code>
                          <button
                            type="button"
                            className="btn ghost small"
                            style={{ padding: "2px 8px", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}
                            onClick={copySaEmail}
                          >
                            {copiedSaEmail ? <CheckCheck size={12} style={{ color: "#34d399" }} /> : <Copy size={12} />}
                            <span>{copiedSaEmail ? "Copied" : "Copy"}</span>
                          </button>
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                {formTab === "schedule" && (
                  <div>
                    <div className="field">
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                          <GoogleDriveIcon size={16} />
                          <span>Google Drive Asset Folder</span>
                        </label>
                        <span className="hint" style={{ fontSize: 11, color: "var(--accent-light)" }}>
                          Single or multiple links supported
                        </span>
                      </div>
                      <input
                        id="drive_folder_id_schedule"
                        name="drive_folder_id_schedule"
                        autoComplete="off"
                        data-lpignore="true"
                        data-1p-ignore="true"
                        value={form.drive_folder_id}
                        onChange={(e) => set("drive_folder_id", extractDriveFolderId(e.target.value))}
                        placeholder="Paste folder link(s) or ID(s) — comma-separated for multiple folders"
                      />
                      <div style={{
                        marginTop: 8,
                        padding: "10px 12px",
                        background: "rgba(255, 255, 255, 0.03)",
                        borderRadius: 6,
                        border: "1px solid var(--border-subtle)",
                        fontSize: 12,
                      }}>
                        <div style={{ color: "var(--text-secondary)", marginBottom: 4 }}>
                          Share this Drive folder with your Google Service Account email (give <b>Viewer</b> access):
                        </div>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 }}>
                          <code style={{ fontSize: 11.5, color: "var(--accent-light)", wordBreak: "break-all", fontFamily: "var(--font-mono, monospace)" }}>
                            {saEmail || "reel-ledger@reel-ledger-505014.iam.gserviceaccount.com"}
                          </code>
                          <button
                            type="button"
                            className="btn ghost small"
                            style={{ padding: "2px 8px", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 4 }}
                            onClick={copySaEmail}
                          >
                            {copiedSaEmail ? <CheckCheck size={12} style={{ color: "#34d399" }} /> : <Copy size={12} />}
                            <span>{copiedSaEmail ? "Copied" : "Copy"}</span>
                          </button>
                        </div>
                      </div>
                    </div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Workspace UTC Offset (hours)</label>
                      <input
                        type="number"
                        step="0.5"
                        value={form.utc_offset_hours}
                        onChange={(e) => set("utc_offset_hours", e.target.value)}
                      />
                      <div className="hint">e.g. +5.5 for IST (India), -5 for EST (US East), or 0 for UTC. Used to render schedule times locally.</div>
                    </div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Distribution Strategy</label>
                      <select value={form.schedule_mode} onChange={(e) => set("schedule_mode", e.target.value)}>
                        <option value="spread">Spread across day (automatic interval spacing)</option>
                        <option value="fixed">Fixed daily post times</option>
                      </select>
                    </div>
                    {form.schedule_mode === "spread" ? (
                      <div>
                        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                          <div className="field" style={{ margin: 0 }}>
                            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Start Hour (0–23)</label>
                            <input type="number" min="0" max="23" value={form.spread_start_hour} onChange={(e) => set("spread_start_hour", e.target.value)} />
                          </div>
                          <div className="field" style={{ margin: 0 }}>
                            <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>End Hour (0–24)</label>
                            <input type="number" min="1" max="24" value={form.spread_end_hour} onChange={(e) => set("spread_end_hour", e.target.value)} />
                          </div>
                        </div>
                        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                          <button
                            type="button"
                            className="btn ghost small"
                            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
                            onClick={() => { set("spread_start_hour", 0); set("spread_end_hour", 24); }}
                          >
                            <Clock size={12} />
                            <span>24/7 Continuous (00:00 – 24:00)</span>
                          </button>
                          <button
                            type="button"
                            className="btn ghost small"
                            style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
                            onClick={() => { set("spread_start_hour", 9); set("spread_end_hour", 22); }}
                          >
                            <Sun size={12} />
                            <span>Daytime Peak (09:00 – 22:00)</span>
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="field">
                        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Fixed Times (comma-separated, e.g. 09:00, 13:00, 18:00)</label>
                        <input value={form.fixed_times} onChange={(e) => set("fixed_times", e.target.value)} placeholder="09:00, 13:00, 18:00" />
                      </div>
                    )}
                    <div className="field" style={{ marginTop: 14 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                        <label style={{ margin: 0, fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Target Posts per Day</label>
                        <span style={{ fontSize: 11, color: "var(--accent-light)", fontWeight: 600 }}>Unrestricted</span>
                      </div>
                      <input type="number" min="1" max="200" value={form.per_run} onChange={(e) => set("per_run", e.target.value)} />
                      <div className="hint">
                        The scheduling engine calculates safe post intervals across your operating window to ensure optimal pacing.
                      </div>
                    </div>
                  </div>
                )}

                {formTab === "captions" && (
                  <div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Default Caption Footer (Optional)</label>
                      <input value={form.caption_footer} onChange={(e) => set("caption_footer", e.target.value)} placeholder="e.g. Follow for daily stories · Link in bio" />
                      <div className="hint">Appended to the end of every post caption for this page.</div>
                    </div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Default Hashtags (comma-separated)</label>
                      <input value={form.extra_hashtags} onChange={(e) => set("extra_hashtags", e.target.value)} placeholder="#spotlight, #creators, #highlight" />
                      <div className="hint">Included on every post in addition to item-specific hashtags.</div>
                    </div>
                    <div className="field">
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Rotating Hashtag Pool (Optional)</label>
                      <input
                        value={poolInput}
                        onChange={(e) => setPoolInput(e.target.value)}
                        onKeyDown={addPoolTag}
                        placeholder="Type a hashtag and press Enter (e.g. #trending)"
                      />
                      <div className="hint">
                        The scheduler rotates tags from this pool with each post to expand audience reach while avoiding repetitive tag spam.
                      </div>
                      {form.hashtag_pool.length > 0 && (
                        <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
                          {form.hashtag_pool.map((t) => (
                            <span key={t} className="tag removable">
                              {t}
                              <button type="button" onClick={() => removePoolTag(t)} aria-label={`Remove ${t}`}>×</button>
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}

                {formTab === "autopilot" && (
                  <div>
                    {/* Master Automation Switch */}
                    <div
                      style={{
                        background: profileForm.automation_enabled ? "rgba(16, 185, 129, 0.08)" : "rgba(239, 68, 68, 0.08)",
                        border: `1px solid ${profileForm.automation_enabled ? "rgba(16, 185, 129, 0.25)" : "rgba(239, 68, 68, 0.25)"}`,
                        borderRadius: 8,
                        padding: "12px 16px",
                        marginBottom: 16,
                        display: "flex",
                        justifyContent: "space-between",
                        alignItems: "center"
                      }}
                    >
                      <div>
                        <div style={{ fontWeight: 700, fontSize: 14, color: profileForm.automation_enabled ? "#34d399" : "#fca5a5" }}>
                          Automated Content Dispatch: {profileForm.automation_enabled ? "ACTIVE (ON)" : "PAUSED (OFF)"}
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                          Automatically ingests media from Drive, formats captions, and dispatches content according to schedule.
                        </div>
                      </div>
                      <label className="switch" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.automation_enabled}
                          onChange={(e) => updateProfileField("automation_enabled", e.target.checked)}
                        />
                      </label>
                    </div>

                    {/* Independent Tri-Mode Controls */}
                    <div style={{ marginBottom: 16 }}>
                      <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
                        <span>Content Channels & Daily Limits</span>
                        <span className="hint" style={{ fontSize: 11, fontWeight: 400 }}>Fair-Share Interleaving</span>
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
                        {/* Video / Reel */}
                        <div style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: "#93c5fd", display: "inline-flex", alignItems: "center", gap: 5 }}>
                              <Video size={13} /> Video / Reel
                            </span>
                            <input
                              type="checkbox"
                              checked={profileForm.video_enabled}
                              onChange={(e) => updateProfileField("video_enabled", e.target.checked)}
                            />
                          </div>
                          <label style={{ fontSize: 11, color: "var(--text-muted)" }}>Posts / Day</label>
                          <input
                            type="number"
                            min="0"
                            max="50"
                            disabled={!profileForm.video_enabled}
                            value={profileForm.daily_video_limit}
                            onChange={(e) => updateProfileField("daily_video_limit", e.target.value)}
                            style={{ width: "100%", marginTop: 4 }}
                          />
                        </div>

                        {/* Photo */}
                        <div style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: "#c084fc", display: "inline-flex", alignItems: "center", gap: 5 }}>
                              <Camera size={13} /> Photo / Image
                            </span>
                            <input
                              type="checkbox"
                              checked={profileForm.photo_enabled}
                              onChange={(e) => updateProfileField("photo_enabled", e.target.checked)}
                            />
                          </div>
                          <label style={{ fontSize: 11, color: "var(--text-muted)" }}>Posts / Day</label>
                          <input
                            type="number"
                            min="0"
                            max="50"
                            disabled={!profileForm.photo_enabled}
                            value={profileForm.daily_photo_limit}
                            onChange={(e) => updateProfileField("daily_photo_limit", e.target.value)}
                            style={{ width: "100%", marginTop: 4 }}
                          />
                        </div>

                        {/* Text */}
                        <div style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
                          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                            <span style={{ fontSize: 13, fontWeight: 600, color: "#34d399", display: "inline-flex", alignItems: "center", gap: 5 }}>
                              <FileText size={13} /> Text / Status
                            </span>
                            <input
                              type="checkbox"
                              checked={profileForm.text_enabled}
                              onChange={(e) => updateProfileField("text_enabled", e.target.checked)}
                            />
                          </div>
                          <label style={{ fontSize: 11, color: "var(--text-muted)" }}>Posts / Day</label>
                          <input
                            type="number"
                            min="0"
                            max="50"
                            disabled={!profileForm.text_enabled}
                            value={profileForm.daily_text_limit}
                            onChange={(e) => updateProfileField("daily_text_limit", e.target.value)}
                            style={{ width: "100%", marginTop: 4 }}
                          />
                        </div>
                      </div>
                    </div>

                    {/* Window & Spacing */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 16 }}>
                      <div className="field" style={{ margin: 0 }}>
                        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Window Start (Hour)</label>
                        <input
                          type="number"
                          min="0"
                          max="23"
                          value={profileForm.posting_window_start}
                          onChange={(e) => updateProfileField("posting_window_start", e.target.value)}
                        />
                      </div>
                      <div className="field" style={{ margin: 0 }}>
                        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Window End (Hour)</label>
                        <input
                          type="number"
                          min="1"
                          max="24"
                          value={profileForm.posting_window_end}
                          onChange={(e) => updateProfileField("posting_window_end", e.target.value)}
                        />
                      </div>
                      <div className="field" style={{ margin: 0 }}>
                        <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Min Spacing (Minutes)</label>
                        <input
                          type="number"
                          min="10"
                          max="180"
                          value={profileForm.min_spacing_minutes}
                          onChange={(e) => updateProfileField("min_spacing_minutes", e.target.value)}
                        />
                      </div>
                    </div>

                    {/* Content Source Selection */}
                    <div className="field" style={{ marginBottom: 16 }}>
                      <label style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>Content Source</label>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginTop: 4 }}>
                        {["drive", "generated", "both"].map((src) => (
                          <button
                            key={src}
                            type="button"
                            className="btn btn-sm"
                            style={{
                              background: profileForm.content_source === src ? "var(--bg-card)" : "var(--bg-input)",
                              color: profileForm.content_source === src ? "var(--accent-light)" : "var(--text-muted)",
                              border: `1px solid ${profileForm.content_source === src ? "var(--accent)" : "var(--border-subtle)"}`,
                              fontWeight: 600,
                              justifyContent: "center"
                            }}
                            onClick={() => updateProfileField("content_source", src)}
                          >
                            {src === "drive" && "Google Drive"}
                            {src === "generated" && "Content Generator"}
                            {src === "both" && "Hybrid (Drive + AI)"}
                          </button>
                        ))}
                      </div>
                    </div>

                    {/* Operational Policy Toggles */}
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 16 }}>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.ai_captioning}
                          onChange={(e) => updateProfileField("ai_captioning", e.target.checked)}
                        />
                        <span>AI Caption & Hook Generator</span>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.hashtag_rotation}
                          onChange={(e) => updateProfileField("hashtag_rotation", e.target.checked)}
                        />
                        <span>Hashtag Pool Rotation</span>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.duplicate_protection}
                          onChange={(e) => updateProfileField("duplicate_protection", e.target.checked)}
                        />
                        <span>Duplicate Protection & Dedup Ledger</span>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.smart_scheduling}
                          onChange={(e) => updateProfileField("smart_scheduling", e.target.checked)}
                        />
                        <span>Smart Fair-Share Interleaving</span>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.auto_retry}
                          onChange={(e) => updateProfileField("auto_retry", e.target.checked)}
                        />
                        <span>Automatic Exponential Retry</span>
                      </label>
                      <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
                        <input
                          type="checkbox"
                          checked={profileForm.analytics_sync}
                          onChange={(e) => updateProfileField("analytics_sync", e.target.checked)}
                        />
                        <span>Post-Publish Analytics Sync</span>
                      </label>
                    </div>

                    {/* Profile Actions & Dry-Run Preview */}
                    <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14 }}>
                      <button
                        type="button"
                        className="btn btn-secondary btn-sm"
                        style={{ display: "inline-flex", alignItems: "center", gap: 5 }}
                        onClick={loadPreview}
                        disabled={loadingPreview || !editingPageId}
                      >
                        <Clock size={12} />
                        <span>{loadingPreview ? "Calculating Timeline…" : "Preview Live Schedule (Dry-Run)"}</span>
                      </button>
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={handleSaveProfile}
                        disabled={savingProfile || !editingPageId}
                        style={{ background: "#10b981", borderColor: "#10b981" }}
                      >
                        {savingProfile ? "Saving & Rebalancing…" : "Save Autopilot Profile"}
                      </button>
                    </div>

                    {profileMsg && (
                      <div className={`banner ${profileMsg.startsWith("✓") ? "" : "err"}`} style={{ fontSize: 12, padding: "8px 12px", marginBottom: 12 }}>
                        {profileMsg}
                      </div>
                    )}

                    {/* Dry-run schedule timeline rendering */}
                    {previewData && (
                      <div style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 12 }}>
                        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                          <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
                            Dry-Run Interleaved Schedule ({previewData.total_slots} slots projected)
                          </span>
                          <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                            Zero published content modified · Safe Dynamic Rebalance
                          </span>
                        </div>
                        <div style={{ maxHeight: 220, overflowY: "auto" }}>
                          <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                            <thead>
                              <tr style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>
                                <th style={{ textAlign: "left", padding: "4px 6px" }}>Time</th>
                                <th style={{ textAlign: "left", padding: "4px 6px" }}>Type</th>
                                <th style={{ textAlign: "left", padding: "4px 6px" }}>Content Asset</th>
                                <th style={{ textAlign: "left", padding: "4px 6px" }}>Source</th>
                                <th style={{ textAlign: "right", padding: "4px 6px" }}>Status</th>
                              </tr>
                            </thead>
                            <tbody>
                              {previewData.timeline?.map((slot, sIdx) => (
                                <tr key={sIdx} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                                  <td style={{ padding: "6px", fontFamily: "var(--font-mono, monospace)" }}>{slot.time}</td>
                                  <td style={{ padding: "6px" }}>
                                    <span
                                      style={{
                                        fontSize: 10,
                                        fontWeight: 700,
                                        padding: "2px 6px",
                                        borderRadius: 4,
                                        background:
                                          slot.content_type === "VIDEO" ? "rgba(59, 130, 246, 0.15)" :
                                          slot.content_type === "PHOTO" ? "rgba(168, 85, 247, 0.15)" : "rgba(16, 185, 129, 0.15)",
                                        color:
                                          slot.content_type === "VIDEO" ? "#60a5fa" :
                                          slot.content_type === "PHOTO" ? "#c084fc" : "#34d399",
                                      }}
                                    >
                                      {slot.content_type}
                                    </span>
                                  </td>
                                  <td style={{ padding: "6px", maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {slot.content_title}
                                  </td>
                                  <td style={{ padding: "6px", color: "var(--text-muted)" }}>{slot.source}</td>
                                  <td style={{ padding: "6px", textAlign: "right" }}>
                                    <span style={{ color: slot.status === "ready" ? "#34d399" : "#fbbf24", fontSize: 11, display: "inline-flex", alignItems: "center", gap: 3 }}>
                                      {slot.status === "ready" ? <Check size={11} /> : null}
                                      {slot.status === "ready" ? "Ready" : "Scheduled"}
                                    </span>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {msg && <div className="banner err" style={{ marginTop: 14 }}>{msg}</div>}

              {/* Modal Actions Footer */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  paddingTop: 16,
                  marginTop: 16,
                  borderTop: "1px solid var(--border-subtle)",
                }}
              >
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  {(!form.name || !form.fb_page_id || (!form.fb_access_token && !hasExistingToken)) ? (
                    <span style={{ color: "#f87171" }}>* Required: Page Name, Page ID, and Access Token</span>
                  ) : (
                    <span style={{ color: "#34d399", display: "inline-flex", alignItems: "center", gap: 5 }}>
                      <Check size={13} /> All required credentials configured
                    </span>
                  )}
                </div>
                <div style={{ display: "flex", gap: 10 }}>
                  <button type="button" className="btn btn-secondary" onClick={() => setShowForm(false)}>
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    disabled={busy || !form.name || !form.fb_page_id || (!form.fb_access_token && !hasExistingToken)}
                  >
                    {busy ? "Saving…" : (editingPageId ? "Update Page" : "Save Page")}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}

      {showGuideModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowGuideModal(false)}>
          <div className="modal" style={{ width: "min(620px, 94vw)", maxHeight: "88vh", overflowY: "auto" }}>
            <h2>How to Generate Never-Expiring Facebook Tokens</h2>
            <p style={{ marginTop: 8, fontSize: 13.5, lineHeight: 1.6, color: "var(--ink-soft)" }}>
              Standard user access tokens expire every 60 days. To ensure your posting engine
              never fails, create a <b>System User</b> in Meta Business Suite — these tokens <b>never expire</b>.
            </p>

            <div style={{ marginTop: 16, display: "flex", flexDirection: "column", gap: 12 }}>
              <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--card)" }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Step 1: Open Meta Business Suite Settings</div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  Go to <a href="https://business.facebook.com/settings" target="_blank" rel="noreferrer" style={{ color: "var(--accent)", textDecoration: "underline" }}>business.facebook.com/settings</a> and make sure your Business Portfolio is selected.
                </div>
              </div>

              <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--card)" }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Step 2: Create a System User</div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  In the left sidebar, click <b>Users</b> → <b>System Users</b>. Click <b>Add</b>, name it (e.g. <code>Reels Publisher</code>), and choose role: <b>Admin</b>.
                </div>
              </div>

              <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--card)" }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Step 3: Assign Your Facebook Pages</div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  Click <b>Assign Assets</b> → Select <b>Pages</b> → Choose your Facebook Pages → Turn on <b>Full Control (Manage Page & Content)</b> → Save Changes.
                </div>
              </div>

              <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--card)" }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Step 4: Generate the Permanent Token</div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  Click <b>Generate New Token</b> on the System User. Select your Meta App. Under <b>Token Expiration</b>, choose <b>Never</b>. Check these 4 permissions:
                  <div style={{ fontFamily: "var(--util)", fontSize: 12, background: "rgba(0,0,0,0.15)", padding: "6px 10px", borderRadius: 4, marginTop: 6 }}>
                    pages_manage_posts, pages_read_engagement, pages_show_list, publish_video
                  </div>
                </div>
              </div>

              <div style={{ padding: "12px 14px", border: "1px solid var(--line)", borderRadius: 8, background: "var(--card)" }}>
                <div style={{ fontWeight: 600, fontSize: 14 }}>Step 5: Test & Save in Dashboard</div>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4, lineHeight: 1.5 }}>
                  Copy the permanent token, paste it into <b>Page access token</b> in the Edit Page modal, click <b>Test Token Live</b> to verify, then save.
                </div>
              </div>
            </div>

            <div className="modal-actions" style={{ marginTop: 22 }}>
              <button className="btn" onClick={() => setShowGuideModal(false)}>Got it, close guide</button>
            </div>
          </div>
        </div>
      )}

      <AutopilotModal
        isOpen={Boolean(autopilotPage)}
        onClose={() => setAutopilotPage(null)}
        page={typeof autopilotPage === "object" ? autopilotPage : (pages[0] || null)}
        onScheduled={() => onReload()}
      />
    </>
  );
}
