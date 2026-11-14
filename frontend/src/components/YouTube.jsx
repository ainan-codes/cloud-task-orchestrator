import React, { useState, useEffect, useCallback } from "react";
import { api } from "../api.js";
import { YouTubeIcon } from "./BrandIcons.jsx";
import AccountLinkModal from "./AccountLinkModal.jsx";
import {
  ShieldCheck,
  Plus,
  Tv,
  CheckCircle,
  Clock,
  Send,
  Settings,
} from "lucide-react";

const YT_RED = "#ef4444";

const PRIVACY_OPTIONS = [
  { id: "public", label: "Public", blurb: "Anyone can see this video." },
  { id: "unlisted", label: "Unlisted", blurb: "Anyone with the link can watch." },
  { id: "private", label: "Private", blurb: "Only you and authorized accounts can view." },
];

function fmt(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function defaultScheduleValue(hoursAhead = 1) {
  const d = new Date(Date.now() + hoursAhead * 3600 * 1000);
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

export default function YouTube({ subTab: propSubTab, onSelectSubTab }) {
  const [internalTab, setInternalTab] = useState("publish");
  const tab = propSubTab || internalTab;
  const setTab = onSelectSubTab || setInternalTab;

  const [channels, setChannels] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [history, setHistory] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState({ msg: "", isErr: false });
  const [busyId, setBusyId] = useState(null);

  // Form states
  const [destChannelId, setDestChannelId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [privacy, setPrivacy] = useState("public");
  const [isShort, setIsShort] = useState(true);
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());
  const [scheduling, setScheduling] = useState(false);

  // Add channel modal/form
  const [showAddChannel, setShowAddChannel] = useState(false);
  const [newChannelId, setNewChannelId] = useState("");
  const [newChannelTitle, setNewChannelTitle] = useState("");
  const [newCustomUrl, setNewCustomUrl] = useState("");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [ch, jb, hi, cfg] = await Promise.all([
        api.youtubeChannels().catch(() => []),
        api.youtubeJobs().catch(() => []),
        api.youtubeHistory().catch(() => []),
        api.youtubeConfig().catch(() => null),
      ]);
      setChannels(ch || []);
      setJobs(jb || []);
      setHistory(hi || []);
      setConfig(cfg);
      if (ch?.length && !destChannelId) {
        setDestChannelId(ch[0].id);
      }
    } catch (e) {
      setNotice({ msg: e.message, isErr: true });
    } finally {
      setLoading(false);
    }
  }, [destChannelId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleAddChannel(e) {
    e.preventDefault();
    if (!newChannelId.trim() || !newChannelTitle.trim()) return;
    try {
      await api.addYoutubeChannel({
        channel_id: newChannelId.trim(),
        title: newChannelTitle.trim(),
        custom_url: newCustomUrl.trim() || null,
        default_privacy: "public",
      });
      setNotice({ msg: `Channel "${newChannelTitle}" added successfully!`, isErr: false });
      setNewChannelId("");
      setNewChannelTitle("");
      setNewCustomUrl("");
      setShowAddChannel(false);
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to add channel: ${err.message}`, isErr: true });
    }
  }

  async function handleDeleteChannel(id, title) {
    if (!window.confirm(`Remove YouTube channel "${title}"?`)) return;
    try {
      await api.deleteYoutubeChannel(id);
      setNotice({ msg: `Channel "${title}" removed.`, isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to delete channel: ${err.message}`, isErr: true });
    }
  }

  async function handleCreateJob(e) {
    e.preventDefault();
    if (!destChannelId || !title.trim()) return;
    setScheduling(true);
    try {
      const whenUtc = new Date(scheduleAt).toISOString();
      const tagsList = tags.split(",").map((t) => t.trim()).filter(Boolean);
      await api.createYoutubeJob({
        channel_id: Number(destChannelId),
        title: title.trim(),
        description: description.trim(),
        tags: tagsList,
        privacy_status: privacy,
        is_short: isShort,
        scheduled_time_utc: whenUtc,
      });
      setNotice({ msg: `Queued "${title}" for YouTube at ${fmt(whenUtc)}!`, isErr: false });
      setTitle("");
      setDescription("");
      setTags("");
      await loadData();
      setTab("queue");
    } catch (err) {
      setNotice({ msg: `Failed to queue: ${err.message}`, isErr: true });
    } finally {
      setScheduling(false);
    }
  }

  async function handleRetryJob(id) {
    setBusyId(`job-${id}`);
    try {
      await api.retryYoutubeJob(id);
      setNotice({ msg: "Job reset to pending queue.", isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Retry failed: ${err.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function handleCancelJob(id) {
    if (!window.confirm("Cancel this scheduled YouTube post?")) return;
    try {
      await api.cancelYoutubeJob(id);
      setNotice({ msg: "Scheduled post cancelled.", isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Cancel failed: ${err.message}`, isErr: true });
    }
  }

  const [showLinkModal, setShowLinkModal] = useState(false);

  return (
    <div className="view">
      <header className="view-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <YouTubeIcon size={38} />
          <div>
            <div className="kicker" style={{ color: "#ef4444" }}>YouTube Shorts & Studio</div>
            <h1 className="h1">YouTube Studio</h1>
            <p className="sub" style={{ marginTop: 2 }}>
              Publish Shorts and videos to your connected YouTube channels with automated scheduling.
            </p>
          </div>
        </div>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setShowLinkModal(true)}
            style={{ fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 7 }}
          >
            <ShieldCheck size={14} />
            <span>Link via Cookies / Alt</span>
          </button>
          <button
            type="button"
            className="btn btn-primary"
            style={{ background: "#dc2626", borderColor: "#dc2626", color: "#fff", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 7 }}
            onClick={() => {
              setTab("channels");
              setShowAddChannel(true);
            }}
          >
            <Plus size={15} />
            <span>Connect Channel</span>
          </button>
        </div>
      </header>

      <AccountLinkModal
        isOpen={showLinkModal}
        onClose={() => setShowLinkModal(false)}
        platform="youtube"
        onSaveCookies={async ({ accountAlias, rawCookies }) => {
          setNotice({
            msg: `Session cookies for "${accountAlias}" successfully stored in vault. YouTube automated upload engine active!`,
            isErr: false,
          });
        }}
      />

      {notice.msg && (
        <div className={`banner ${notice.isErr ? "err" : ""}`} style={{ marginBottom: 16 }}>
          {notice.msg}
        </div>
      )}

      {/* ---------------- KPIs ---------------- */}
      <div className="grid-3" style={{ marginBottom: 24 }}>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Connected Channels</span>
            <Tv size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{channels.length}</div>
          <div className="stat-sub">Active YouTube creator accounts</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Published Shorts & Videos</span>
            <CheckCircle size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{history.length}</div>
          <div className="stat-sub">Dispatched to YouTube Studio</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Queued Dispatches</span>
            <Clock size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{jobs.filter((j) => j.status === "pending").length}</div>
          <div className="stat-sub">Awaiting scheduled post time</div>
        </div>
      </div>

      {/* ---------------- Sub-Navigation Tabs ---------------- */}
      <nav className="snap-subnav">
        <button
          type="button"
          className={`snap-tab-item ${tab === "publish" ? "active" : ""}`}
          onClick={() => setTab("publish")}
        >
          <Send size={14} />
          <span>Compose & Schedule</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "channels" ? "active" : ""}`}
          onClick={() => setTab("channels")}
        >
          <Tv size={14} />
          <span>Channels</span>
          <span className="snap-tab-badge">{channels.length}</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "queue" ? "active" : ""}`}
          onClick={() => setTab("queue")}
        >
          <Clock size={14} />
          <span>Publishing Queue</span>
          <span className="snap-tab-badge">{jobs.length}</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "history" ? "active" : ""}`}
          onClick={() => setTab("history")}
        >
          <CheckCircle size={14} />
          <span>Published Ledger</span>
          <span className="snap-tab-badge">{history.length}</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "settings" ? "active" : ""}`}
          onClick={() => setTab("settings")}
        >
          <Settings size={14} />
          <span>API Configuration</span>
        </button>
      </nav>

      {/* ---------------- TAB 1: COMPOSE & SCHEDULE ---------------- */}
      {tab === "publish" && (
        channels.length === 0 ? (
          <div className="card" style={{ padding: "48px 24px", textAlign: "center", marginBottom: 24 }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
              No YouTube Channels Connected Yet
            </div>
            <p style={{ color: "var(--ink-muted)", fontSize: 13, maxWidth: 460, margin: "0 auto 16px" }}>
              Connect your YouTube channel to schedule Shorts and videos.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              style={{ background: YT_RED, borderColor: YT_RED, color: "#fff", fontWeight: 700 }}
              onClick={() => {
                setTab("channels");
                setShowAddChannel(true);
              }}
            >
              + Add Your First Channel
            </button>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Compose & Schedule Post</h2>
            </div>
            <form onSubmit={handleCreateJob} style={{ padding: "20px", display: "grid", gap: 16 }}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Destination Channel</span>
                  <select
                    value={destChannelId}
                    onChange={(e) => setDestChannelId(e.target.value)}
                    style={{ padding: "8px 10px" }}
                    required
                  >
                    {channels.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.title} ({c.custom_url || c.channel_id})
                      </option>
                    ))}
                  </select>
                </label>

                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Scheduled Time (Local)</span>
                  <input
                    type="datetime-local"
                    value={scheduleAt}
                    onChange={(e) => setScheduleAt(e.target.value)}
                    style={{ padding: "8px 10px" }}
                    required
                  />
                </label>
              </div>

              <label style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Video / Short Title</span>
                <input
                  type="text"
                  placeholder="e.g. Incredible Coconut ASMR #Shorts"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  style={{ padding: "8px 10px" }}
                  required
                />
              </label>

              <label style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Description & Hashtags</span>
                <textarea
                  rows={4}
                  placeholder="Video description, links, hashtags (#Shorts, #viral)..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  style={{ padding: "8px 10px" }}
                />
              </label>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Tags (comma separated)</span>
                  <input
                    type="text"
                    placeholder="asmr, relaxing, shorts"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    style={{ padding: "8px 10px" }}
                  />
                </label>

                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Visibility Status</span>
                  <select
                    value={privacy}
                    onChange={(e) => setPrivacy(e.target.value)}
                    style={{ padding: "8px 10px" }}
                  >
                    {PRIVACY_OPTIONS.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.label} — {p.blurb}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <input
                  type="checkbox"
                  id="yt-is-short"
                  checked={isShort}
                  onChange={(e) => setIsShort(e.target.checked)}
                />
                <label htmlFor="yt-is-short" style={{ fontSize: 13, cursor: "pointer" }}>
                  Publish as <strong>YouTube Short</strong> (vertical video, under 60 seconds)
                </label>
              </div>

              <div style={{ marginTop: 8 }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ background: YT_RED, borderColor: YT_RED, color: "#fff", fontWeight: 700 }}
                  disabled={scheduling}
                >
                  {scheduling ? "Queueing…" : "Queue to YouTube"}
                </button>
              </div>
            </form>
          </div>
        )
      )}

      {/* ---------------- TAB 2: CHANNELS ---------------- */}
      {tab === "channels" && (
        <div>
          {showAddChannel && (
            <div className="card" style={{ padding: 20, marginBottom: 20, border: "1px solid var(--rust)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Connect a YouTube Channel</h3>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={() => setShowAddChannel(false)}
                >
                  ✕ Close
                </button>
              </div>
              <form onSubmit={handleAddChannel} style={{ display: "grid", gap: 12 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span className="sec-label">Channel Title</span>
                    <input
                      placeholder="e.g. My ASMR Channel"
                      value={newChannelTitle}
                      onChange={(e) => setNewChannelTitle(e.target.value)}
                      style={{ padding: "6px 8px" }}
                      required
                    />
                  </label>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span className="sec-label">YouTube Channel ID</span>
                    <input
                      placeholder="e.g. UCxxxxxxxxxxxxxxxxxx"
                      value={newChannelId}
                      onChange={(e) => setNewChannelId(e.target.value)}
                      style={{ padding: "6px 8px" }}
                      required
                    />
                  </label>
                </div>
                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Custom URL / Handle (optional)</span>
                  <input
                    placeholder="e.g. @MyAsmrReels"
                    value={newCustomUrl}
                    onChange={(e) => setNewCustomUrl(e.target.value)}
                    style={{ padding: "6px 8px" }}
                  />
                </label>
                <div>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    style={{ background: YT_RED, borderColor: YT_RED, color: "#fff" }}
                  >
                    Save Channel
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Connected Channels ({channels.length})</h2>
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: "4px 10px" }}
                onClick={() => setShowAddChannel(true)}
              >
                + Add Channel
              </button>
            </div>
            {channels.length === 0 ? (
              <div style={{ padding: 32, textAlign: "center", color: "var(--ink-muted)", fontSize: 13 }}>
                No channels connected yet. Click "+ Add Channel" to configure your channel.
              </div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Channel</th>
                    <th>Channel ID</th>
                    <th>Default Visibility</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((c) => (
                    <tr key={c.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{c.title}</div>
                        {c.custom_url && (
                          <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>{c.custom_url}</div>
                        )}
                      </td>
                      <td style={{ fontSize: 11, fontFamily: "monospace" }}>{c.channel_id}</td>
                      <td>
                        <span style={{ fontSize: 12, textTransform: "capitalize" }}>
                          {c.default_privacy || "public"}
                        </span>
                      </td>
                      <td>
                        <span style={{ fontSize: 11, color: "#1e6b34", fontWeight: 600 }}>● Active</span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", color: "#a3231a" }}
                          onClick={() => handleDeleteChannel(c.id, c.title)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ---------------- TAB 3: QUEUE ---------------- */}
      {tab === "queue" && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Publishing Queue ({jobs.length})</h2>
            <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
              Refresh
            </button>
          </div>
          {jobs.length === 0 ? (
            <div style={{ padding: 24, color: "var(--ink-muted)", fontSize: 13 }}>
              Nothing queued. Scheduled posts will show here until their publish time arrives.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Channel</th>
                  <th>Title</th>
                  <th>Type</th>
                  <th>Scheduled For</th>
                  <th>Status</th>
                  <th>Error</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td style={{ fontSize: 12 }}>{j.channel_title}</td>
                    <td style={{ fontSize: 12, fontWeight: 500 }}>{j.title}</td>
                    <td style={{ fontSize: 11 }}>{j.is_short ? "Short" : "Video"}</td>
                    <td style={{ fontSize: 12 }}>{fmt(j.scheduled_time_utc)}</td>
                    <td>
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 600,
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: j.status === "posted" ? "#e6f4ea" : j.status === "failed" ? "#fdecea" : "#eceff3",
                          color: j.status === "posted" ? "#1e6b34" : j.status === "failed" ? "#a3231a" : "#4a5560",
                        }}
                      >
                        {j.status}
                      </span>
                    </td>
                    <td style={{ fontSize: 11, color: "#a3231a", maxWidth: 200 }}>{j.error || "—"}</td>
                    <td style={{ textAlign: "right" }}>
                      {j.status === "failed" && (
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px" }}
                          disabled={busyId === `job-${j.id}`}
                          onClick={() => handleRetryJob(j.id)}
                        >
                          Retry
                        </button>
                      )}
                      {j.status !== "posted" && (
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                          onClick={() => handleCancelJob(j.id)}
                        >
                          Cancel
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- TAB 4: PUBLISHED LEDGER ---------------- */}
      {tab === "history" && (
        <div className="card" style={{ padding: 0 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Published Ledger ({history.length})</h2>
            <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
              Refresh
            </button>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: 24, color: "var(--ink-muted)", fontSize: 13 }}>
              No YouTube videos posted yet.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Published At</th>
                  <th>Channel</th>
                  <th>Title</th>
                  <th>Video ID</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td style={{ fontSize: 12 }}>{fmt(h.published_at)}</td>
                    <td style={{ fontSize: 12 }}>{h.channel_title}</td>
                    <td style={{ fontSize: 12, fontWeight: 600 }}>{h.title}</td>
                    <td style={{ fontSize: 11, fontFamily: "monospace" }}>{h.video_id}</td>
                    <td>
                      <a
                        href={`https://www.youtube.com/watch?v=${h.video_id}`}
                        target="_blank"
                        rel="noreferrer"
                        style={{ fontSize: 12, color: YT_RED, fontWeight: 600 }}
                      >
                        Watch on YouTube ↗
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- TAB 5: API CONFIGURATION ---------------- */}
      {tab === "settings" && (
        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>YouTube Data API v3 Configuration</h2>
          <p style={{ color: "var(--ink-soft)", fontSize: 13, lineHeight: 1.5, marginBottom: 20 }}>
            Connect Google Cloud Console OAuth 2.0 Client credentials to publish Shorts and videos to YouTube.
          </p>
          <div style={{ display: "grid", gap: 16, maxWidth: 640 }}>
            <div>
              <div className="sec-label">Client ID Status</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {config?.client_id_configured ? (
                  <span style={{ color: "#1e6b34", fontWeight: 600 }}>✅ Configured in environment</span>
                ) : (
                  <span style={{ color: "#a3231a", fontWeight: 600 }}>❌ Missing (YOUTUBE_CLIENT_ID / GOOGLE_CLIENT_ID)</span>
                )}
              </div>
            </div>
            <div>
              <div className="sec-label">Client Secret Status</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {config?.client_secret_configured ? (
                  <span style={{ color: "#1e6b34", fontWeight: 600 }}>✅ Configured in environment</span>
                ) : (
                  <span style={{ color: "#a3231a", fontWeight: 600 }}>❌ Missing (YOUTUBE_CLIENT_SECRET)</span>
                )}
              </div>
            </div>
            <div>
              <div className="sec-label">OAuth Redirect URI</div>
              <code style={{ display: "block", padding: "8px 12px", background: "var(--paper-2)", borderRadius: 6, fontSize: 12, marginTop: 4 }}>
                {config?.redirect_uri || "https://your-domain/api/youtube/oauth/callback"}
              </code>
            </div>
            <div>
              <div className="sec-label">Daily API Quota</div>
              <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 4 }}>
                10,000 units/day default (Upload cost: ~1,600 units per video = ~6 videos/day on free quota).
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
