import React, { useState, useEffect, useCallback, useMemo } from "react";
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
  Globe,
  ExternalLink,
  RefreshCw,
  AlertCircle,
  Filter,
  CheckCircle2,
  ArrowRight,
  Play,
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
  const [internalTab, setInternalTab] = useState("queue");
  const tab = propSubTab || internalTab;
  const setTab = onSelectSubTab || setInternalTab;

  const [channels, setChannels] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [history, setHistory] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState({ msg: "", isErr: false });
  const [busyId, setBusyId] = useState(null);

  // Active Channel Filter: "all" or specific channel id (number)
  const [selectedChannelId, setSelectedChannelId] = useState("all");

  // Form states
  const [destChannelId, setDestChannelId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [privacy, setPrivacy] = useState("public");
  const [isShort, setIsShort] = useState(true);
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());
  const [scheduling, setScheduling] = useState(false);

  // Queue search, filter, and pagination states
  const [queueSearch, setQueueSearch] = useState("");
  const [queueStatusFilter, setQueueStatusFilter] = useState("all");
  const [queueSort, setQueueSort] = useState("asc"); // "asc" (soonest first) | "desc"
  const [pageSize, setPageSize] = useState(50);
  const [currentPage, setCurrentPage] = useState(1);

  // Add channel modal/form
  const [showAddChannel, setShowAddChannel] = useState(false);
  const [newChannelId, setNewChannelId] = useState("");
  const [newChannelTitle, setNewChannelTitle] = useState("");
  const [newCustomUrl, setNewCustomUrl] = useState("");
  const [showLinkModal, setShowLinkModal] = useState(false);

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

  // Keep destChannelId aligned when user picks a specific channel
  useEffect(() => {
    if (selectedChannelId !== "all") {
      setDestChannelId(selectedChannelId);
    } else if (channels.length > 0 && !destChannelId) {
      setDestChannelId(channels[0].id);
    }
  }, [selectedChannelId, channels, destChannelId]);

  // Selected Channel Object
  const selectedChannel = useMemo(() => {
    if (selectedChannelId === "all") return null;
    return channels.find((c) => String(c.id) === String(selectedChannelId)) || null;
  }, [channels, selectedChannelId]);

  // Active jobs based on selectedChannelId
  const activeJobs = useMemo(() => {
    if (selectedChannelId === "all") return jobs;
    return jobs.filter((j) => String(j.channel_id) === String(selectedChannelId));
  }, [jobs, selectedChannelId]);

  // Active history based on selectedChannelId
  const activeHistory = useMemo(() => {
    if (selectedChannelId === "all") return history;
    return history.filter((h) => String(h.channel_id) === String(selectedChannelId));
  }, [history, selectedChannelId]);

  const pendingJobsCount = useMemo(() => {
    return activeJobs.filter((j) => j.status === "pending").length;
  }, [activeJobs]);

  const postedJobsCount = useMemo(() => {
    return activeJobs.filter((j) => j.status === "posted").length;
  }, [activeJobs]);

  const failedJobsCount = useMemo(() => {
    return activeJobs.filter((j) => j.status === "failed").length;
  }, [activeJobs]);

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

  async function handleDeleteChannel(id, channelTitle) {
    if (!window.confirm(`Remove YouTube channel "${channelTitle}"? All associated schedules will be deleted.`)) return;
    try {
      await api.deleteYoutubeChannel(id);
      setNotice({ msg: `Channel "${channelTitle}" removed.`, isErr: false });
      if (String(selectedChannelId) === String(id)) {
        setSelectedChannelId("all");
      }
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

  async function handlePostNow(id, jobTitle) {
    if (!window.confirm(`Publish "${jobTitle}" to YouTube right now?`)) return;
    setBusyId(`post-${id}`);
    setNotice({ msg: `Uploading "${jobTitle}" to YouTube now... Please wait.`, isErr: false });
    try {
      const res = await api.postYoutubeJobNow(id);
      if (res && res.url) {
        setNotice({
          msg: `Successfully published to YouTube! Short URL: ${res.url}`,
          isErr: false,
        });
      } else {
        setNotice({ msg: `Published "${jobTitle}" successfully!`, isErr: false });
      }
      await loadData();
    } catch (err) {
      setNotice({ msg: `Post Now failed: ${err.message}`, isErr: true });
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

  return (
    <div className="view">
      <header className="view-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <YouTubeIcon size={38} />
          <div>
            <div className="kicker" style={{ color: YT_RED, fontWeight: 700 }}>
              YouTube Shorts & Studio
            </div>
            <h1 className="h1">YouTube Studio</h1>
            <p className="sub" style={{ marginTop: 2 }}>
              Multi-channel YouTube automation, publishing queue, and autonomous Shorts scheduler.
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
            style={{
              background: "#dc2626",
              borderColor: "#dc2626",
              color: "#fff",
              fontWeight: 600,
              display: "inline-flex",
              alignItems: "center",
              gap: 7,
            }}
            onClick={async () => {
              try {
                const res = await api.youtubeOAuthAuthorize();
                if (res?.authorization_url) {
                  window.location.href = res.authorization_url;
                }
              } catch (e) {
                setShowLinkModal(true);
              }
            }}
          >
            <Plus size={15} />
            <span>Connect Channel (1-Click Google)</span>
          </button>
        </div>
      </header>

      <AccountLinkModal
        isOpen={showLinkModal}
        onClose={() => setShowLinkModal(false)}
        platform="youtube"
        onStartOAuth={async () => {
          try {
            const res = await api.youtubeOAuthAuthorize();
            if (res?.authorization_url) {
              window.location.href = res.authorization_url;
            }
          } catch (e) {
            setNotice({ msg: `Failed to start YouTube connection: ${e.message}`, isErr: true });
          }
        }}
        onSaveCookies={async ({ accountAlias }) => {
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

      {/* ---------------- CHANNEL SELECTOR BAR (FACEBOOK PARITY) ---------------- */}
      <div
        className="card"
        style={{
          padding: "12px 18px",
          marginBottom: 20,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          flexWrap: "wrap",
          gap: 14,
          background: "var(--bg-card, #121417)",
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: 11, fontWeight: 700, textTransform: "uppercase", letterSpacing: 0.6, color: "var(--ink-muted)" }}>
              Channel:
            </span>
            <select
              value={selectedChannelId}
              onChange={(e) => {
                const val = e.target.value === "all" ? "all" : Number(e.target.value);
                setSelectedChannelId(val);
                setCurrentPage(1);
              }}
              style={{
                padding: "6px 12px",
                fontSize: 13,
                fontWeight: 600,
                borderRadius: 6,
                background: "var(--bg-subtle, #1a1d21)",
                color: "var(--ink)",
                border: "1px solid var(--border)",
                cursor: "pointer",
                minWidth: 200,
              }}
            >
              <option value="all">🌐 All Channels ({channels.length}) · {jobs.length} Queued</option>
              {channels.map((c) => {
                const count = jobs.filter((j) => j.channel_id === c.id).length;
                return (
                  <option key={c.id} value={c.id}>
                    {c.title} {c.custom_url ? `(${c.custom_url})` : ""} · {count} queued
                  </option>
                );
              })}
            </select>
          </div>

          {/* Quick-switch Channel Buttons */}
          <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <button
              type="button"
              className={`btn ${selectedChannelId === "all" ? "btn-primary" : "btn-secondary"}`}
              style={{
                fontSize: 11,
                padding: "4px 10px",
                fontWeight: 600,
                background: selectedChannelId === "all" ? YT_RED : undefined,
                borderColor: selectedChannelId === "all" ? YT_RED : undefined,
                color: selectedChannelId === "all" ? "#fff" : undefined,
                display: "inline-flex",
                alignItems: "center",
                gap: 5,
              }}
              onClick={() => {
                setSelectedChannelId("all");
                setCurrentPage(1);
              }}
            >
              <span>🌐 All ({channels.length})</span>
            </button>
            {channels.map((c) => {
              const isSel = String(selectedChannelId) === String(c.id);
              const count = jobs.filter((j) => j.channel_id === c.id).length;
              return (
                <button
                  key={c.id}
                  type="button"
                  className={`btn ${isSel ? "btn-primary" : "btn-secondary"}`}
                  style={{
                    fontSize: 11,
                    padding: "4px 10px",
                    fontWeight: 600,
                    background: isSel ? YT_RED : undefined,
                    borderColor: isSel ? YT_RED : undefined,
                    color: isSel ? "#fff" : undefined,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                  }}
                  onClick={() => {
                    setSelectedChannelId(c.id);
                    setCurrentPage(1);
                  }}
                >
                  <YouTubeIcon size={12} />
                  <span>{c.title}</span>
                  <span
                    style={{
                      fontSize: 10,
                      padding: "1px 5px",
                      borderRadius: 10,
                      background: isSel ? "rgba(255,255,255,0.25)" : "var(--bg-subtle, rgba(255,255,255,0.08))",
                      color: isSel ? "#fff" : "var(--ink-muted)",
                    }}
                  >
                    {count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Selected Channel Metadata */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 12 }}>
          {selectedChannel ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ color: "#10b981", fontSize: 10 }}>●</span>
                <span style={{ fontWeight: 600, color: "var(--ink)" }}>{selectedChannel.title}</span>
                {selectedChannel.custom_url && (
                  <span style={{ color: "var(--ink-muted)", fontFamily: "monospace", fontSize: 11 }}>
                    {selectedChannel.custom_url}
                  </span>
                )}
              </div>
              <span style={{ color: "var(--border)" }}>|</span>
              <a
                href={`https://youtube.com/channel/${selectedChannel.channel_id}`}
                target="_blank"
                rel="noreferrer"
                style={{
                  color: YT_RED,
                  textDecoration: "none",
                  fontWeight: 600,
                  fontSize: 11,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 3,
                }}
              >
                <span>Studio</span>
                <ExternalLink size={11} />
              </a>
            </>
          ) : (
            <div style={{ color: "var(--ink-muted)", fontSize: 12 }}>
              Aggregated across <strong style={{ color: "var(--ink)" }}>{channels.length}</strong> active creator channels
            </div>
          )}
        </div>
      </div>

      {/* ---------------- DYNAMIC KPIs ---------------- */}
      <div className="grid-3" style={{ marginBottom: 24 }}>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">
              {selectedChannel ? "Selected Channel" : "Connected Channels"}
            </span>
            <Tv size={16} className="stat-icon" />
          </div>
          <div className="stat-num" style={{ fontSize: selectedChannel ? 20 : 28, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {selectedChannel ? selectedChannel.title : channels.length}
          </div>
          <div className="stat-sub">
            {selectedChannel
              ? (selectedChannel.custom_url || selectedChannel.channel_id)
              : "Active YouTube creator accounts"}
          </div>
        </div>

        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">
              {selectedChannel ? `Published (${selectedChannel.title})` : "Published Shorts & Videos"}
            </span>
            <CheckCircle size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{activeHistory.length}</div>
          <div className="stat-sub">
            {selectedChannel
              ? `Dispatched to ${selectedChannel.title}`
              : "Dispatched across all connected channels"}
          </div>
        </div>

        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">
              {selectedChannel ? `Queued (${selectedChannel.title})` : "Queued Dispatches"}
            </span>
            <Clock size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{pendingJobsCount}</div>
          <div className="stat-sub">
            {selectedChannel
              ? `~${Math.ceil(pendingJobsCount / 6)} days runway at 6 Shorts/day`
              : "Awaiting scheduled post time"}
          </div>
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
          <span className="snap-tab-badge">{activeJobs.length}</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "history" ? "active" : ""}`}
          onClick={() => setTab("history")}
        >
          <CheckCircle size={14} />
          <span>Published Ledger</span>
          <span className="snap-tab-badge">{activeHistory.length}</span>
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
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Compose & Schedule Post</h2>
              {selectedChannel && (
                <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                  Targeting: <strong style={{ color: "var(--ink)" }}>{selectedChannel.title}</strong>
                </span>
              )}
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
              <div
                style={{
                  marginBottom: 18,
                  padding: "14px 16px",
                  background: "rgba(239, 68, 68, 0.08)",
                  border: "1px solid rgba(239, 68, 68, 0.25)",
                  borderRadius: 8,
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  gap: 14,
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, color: "#fff", fontSize: 13.5 }}>
                    Automatic 1-Click Connection (Recommended)
                  </div>
                  <div style={{ fontSize: 12, color: "var(--ink-muted)", marginTop: 2 }}>
                    Sign in with Google to automatically link your channel and grant upload permission.
                  </div>
                </div>
                <button
                  type="button"
                  className="btn btn-primary"
                  style={{
                    background: YT_RED,
                    borderColor: YT_RED,
                    color: "#fff",
                    fontWeight: 600,
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 7,
                    whiteSpace: "nowrap",
                    padding: "8px 16px",
                  }}
                  onClick={async () => {
                    try {
                      const res = await api.youtubeOAuthAuthorize();
                      if (res?.authorization_url) {
                        window.location.href = res.authorization_url;
                      }
                    } catch (e) {
                      setNotice({ msg: "Failed to start Google OAuth: " + e.message, isErr: true });
                    }
                  }}
                >
                  <YouTubeIcon size={16} />
                  <span>Sign in with Google</span>
                </button>
              </div>

              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  margin: "10px 0 14px",
                  color: "var(--ink-muted)",
                  fontSize: 11,
                  textTransform: "uppercase",
                  letterSpacing: 0.5,
                }}
              >
                <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
                <span>Or Enter Manually</span>
                <div style={{ flex: 1, height: 1, background: "var(--border)" }} />
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
            <div
              style={{
                padding: "16px 20px",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                Connected Channels ({channels.length})
              </h2>
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
                    <th>Queue Depth</th>
                    <th>Published</th>
                    <th>Default Visibility</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((c) => {
                    const cJobs = jobs.filter((j) => j.channel_id === c.id);
                    const cPending = cJobs.filter((j) => j.status === "pending").length;
                    const cHistory = history.filter((h) => h.channel_id === c.id).length;
                    return (
                      <tr key={c.id}>
                        <td>
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <YouTubeIcon size={20} />
                            <div>
                              <div style={{ fontWeight: 600 }}>{c.title}</div>
                              {c.custom_url && (
                                <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>{c.custom_url}</div>
                              )}
                            </div>
                          </div>
                        </td>
                        <td style={{ fontSize: 11, fontFamily: "monospace" }}>{c.channel_id}</td>
                        <td>
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 600,
                              padding: "2px 7px",
                              borderRadius: 4,
                              background: "rgba(239, 68, 68, 0.1)",
                              color: YT_RED,
                            }}
                          >
                            {cPending} pending
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: 12, color: "var(--ink-soft)", fontWeight: 500 }}>
                            {cHistory} posted
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: 12, textTransform: "capitalize" }}>
                            {c.default_privacy || "public"}
                          </span>
                        </td>
                        <td>
                          <span style={{ fontSize: 11, color: "#1e6b34", fontWeight: 600 }}>● Active</span>
                        </td>
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          <button
                            type="button"
                            className="btn btn-secondary"
                            style={{ fontSize: 11, padding: "3px 8px", marginRight: 6 }}
                            onClick={() => {
                              setSelectedChannelId(c.id);
                              setTab("queue");
                            }}
                          >
                            View Queue →
                          </button>
                          <a
                            href={`https://youtube.com/channel/${c.channel_id}`}
                            target="_blank"
                            rel="noreferrer"
                            className="btn"
                            style={{ fontSize: 11, padding: "3px 8px", marginRight: 6, textDecoration: "none" }}
                          >
                            Studio ↗
                          </a>
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
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* ---------------- TAB 3: PUBLISHING QUEUE ---------------- */}
      {tab === "queue" && (() => {
        // Filter jobs based on selected channel, status, search query, and sort
        const filteredJobs = activeJobs
          .filter((j) => {
            if (queueStatusFilter !== "all" && j.status !== queueStatusFilter) return false;
            if (queueSearch.trim()) {
              const q = queueSearch.trim().toLowerCase();
              return (
                (j.title && j.title.toLowerCase().includes(q)) ||
                (j.file_name && j.file_name.toLowerCase().includes(q)) ||
                (j.channel_title && j.channel_title.toLowerCase().includes(q))
              );
            }
            return true;
          })
          .sort((a, b) => {
            const ta = new Date(a.scheduled_time_utc).getTime();
            const tb = new Date(b.scheduled_time_utc).getTime();
            return queueSort === "asc" ? ta - tb : tb - ta;
          });

        const effectivePageSize = pageSize === 0 ? filteredJobs.length : pageSize;
        const totalPages = Math.ceil(filteredJobs.length / (effectivePageSize || 1)) || 1;
        const safePage = Math.min(currentPage, totalPages);
        const startIndex = (safePage - 1) * effectivePageSize;
        const pagedJobs = pageSize === 0 ? filteredJobs : filteredJobs.slice(startIndex, startIndex + effectivePageSize);

        return (
          <div className="card" style={{ padding: 0 }}>
            <div
              style={{
                padding: "16px 20px",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                flexWrap: "wrap",
                gap: 12,
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                    Publishing Queue ({filteredJobs.length} {filteredJobs.length !== activeJobs.length ? `of ${activeJobs.length}` : ""})
                  </h2>
                  {selectedChannel && (
                    <span
                      style={{
                        fontSize: 11,
                        padding: "2px 8px",
                        borderRadius: 12,
                        background: "rgba(239, 68, 68, 0.12)",
                        color: YT_RED,
                        fontWeight: 600,
                      }}
                    >
                      {selectedChannel.title}
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 2 }}>
                  Showing scheduled YouTube Shorts & Videos in chronological dispatch order
                </div>
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                {filteredJobs.some((j) => j.status === "pending") && (
                  <button
                    type="button"
                    className="btn btn-primary"
                    style={{
                      fontSize: 11,
                      padding: "5px 12px",
                      background: YT_RED,
                      borderColor: YT_RED,
                      color: "#fff",
                      fontWeight: 600,
                    }}
                    disabled={!!busyId}
                    onClick={() => {
                      const nextDue = filteredJobs.find((j) => j.status === "pending");
                      if (nextDue) handlePostNow(nextDue.id, nextDue.title);
                    }}
                  >
                    ⚡ Post Next Due Now
                  </button>
                )}
                <button className="btn" style={{ fontSize: 11, padding: "5px 12px" }} onClick={loadData}>
                  <RefreshCw size={12} style={{ marginRight: 4 }} />
                  Refresh
                </button>
              </div>
            </div>

            {/* Filter & Search Bar */}
            <div
              style={{
                padding: "12px 20px",
                background: "var(--bg-subtle, rgba(255,255,255,0.02))",
                borderBottom: "1px solid var(--border)",
                display: "flex",
                flexWrap: "wrap",
                gap: 12,
                alignItems: "center",
                justifyContent: "space-between",
              }}
            >
              <div style={{ display: "flex", gap: 8, alignItems: "center", flex: "1 1 250px" }}>
                <input
                  type="text"
                  placeholder="Search by title, series, or filename..."
                  value={queueSearch}
                  onChange={(e) => {
                    setQueueSearch(e.target.value);
                    setCurrentPage(1);
                  }}
                  style={{ padding: "6px 10px", fontSize: 12, flex: 1, minWidth: 180 }}
                />
                {queueSearch && (
                  <button
                    className="btn"
                    style={{ fontSize: 11, padding: "5px 8px" }}
                    onClick={() => setQueueSearch("")}
                  >
                    Clear
                  </button>
                )}
              </div>

              <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
                {/* Channel Filter in Queue Bar */}
                <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "var(--ink-muted)" }}>Channel:</span>
                  <select
                    value={selectedChannelId}
                    onChange={(e) => {
                      const val = e.target.value === "all" ? "all" : Number(e.target.value);
                      setSelectedChannelId(val);
                      setCurrentPage(1);
                    }}
                    style={{ padding: "5px 8px", fontSize: 12 }}
                  >
                    <option value="all">All Channels ({jobs.length})</option>
                    {channels.map((c) => {
                      const cCount = jobs.filter((j) => j.channel_id === c.id).length;
                      return (
                        <option key={c.id} value={c.id}>
                          {c.title} ({cCount})
                        </option>
                      );
                    })}
                  </select>
                </label>

                <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "var(--ink-muted)" }}>Status:</span>
                  <select
                    value={queueStatusFilter}
                    onChange={(e) => {
                      setQueueStatusFilter(e.target.value);
                      setCurrentPage(1);
                    }}
                    style={{ padding: "5px 8px", fontSize: 12 }}
                  >
                    <option value="all">All ({activeJobs.length})</option>
                    <option value="pending">Pending ({pendingJobsCount})</option>
                    <option value="posted">Posted ({postedJobsCount})</option>
                    <option value="failed">Failed ({failedJobsCount})</option>
                  </select>
                </label>

                <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "var(--ink-muted)" }}>Order:</span>
                  <select
                    value={queueSort}
                    onChange={(e) => setQueueSort(e.target.value)}
                    style={{ padding: "5px 8px", fontSize: 12 }}
                  >
                    <option value="asc">Soonest First (Next Up)</option>
                    <option value="desc">Latest First</option>
                  </select>
                </label>

                <label style={{ fontSize: 12, display: "flex", alignItems: "center", gap: 4 }}>
                  <span style={{ color: "var(--ink-muted)" }}>Per Page:</span>
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      setPageSize(Number(e.target.value));
                      setCurrentPage(1);
                    }}
                    style={{ padding: "5px 8px", fontSize: 12 }}
                  >
                    <option value={25}>25</option>
                    <option value={50}>50</option>
                    <option value={100}>100</option>
                    <option value={0}>Show All ({filteredJobs.length})</option>
                  </select>
                </label>
              </div>
            </div>

            {filteredJobs.length === 0 ? (
              <div style={{ padding: 36, textAlign: "center", color: "var(--ink-muted)", fontSize: 13 }}>
                {queueSearch || queueStatusFilter !== "all" || selectedChannelId !== "all"
                  ? "No scheduled jobs match your filter criteria."
                  : "Nothing queued. Scheduled posts will appear here until their publish time arrives."}
              </div>
            ) : (
              <>
                <table className="table" style={{ width: "100%" }}>
                  <thead>
                    <tr>
                      <th style={{ width: 40, textAlign: "center" }}>#</th>
                      <th>Channel</th>
                      <th>Title</th>
                      <th>Type</th>
                      <th>Scheduled For (Local)</th>
                      <th>Status</th>
                      <th>Error</th>
                      <th style={{ textAlign: "right" }}>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pagedJobs.map((j, idx) => (
                      <tr key={j.id}>
                        <td style={{ fontSize: 11, color: "var(--ink-muted)", textAlign: "center" }}>
                          {startIndex + idx + 1}
                        </td>
                        <td style={{ fontSize: 12 }}>
                          <button
                            type="button"
                            title={`Click to filter only ${j.channel_title}`}
                            onClick={() => {
                              setSelectedChannelId(j.channel_id);
                              setCurrentPage(1);
                            }}
                            style={{
                              background: "none",
                              border: "none",
                              padding: 0,
                              cursor: "pointer",
                              color: String(selectedChannelId) === String(j.channel_id) ? YT_RED : "var(--ink)",
                              fontWeight: 600,
                              textAlign: "left",
                              display: "inline-flex",
                              alignItems: "center",
                              gap: 5,
                            }}
                          >
                            <YouTubeIcon size={13} />
                            <span>{j.channel_title}</span>
                          </button>
                        </td>
                        <td
                          style={{
                            fontSize: 12,
                            fontWeight: 500,
                            maxWidth: 360,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={j.title}
                        >
                          {j.title}
                        </td>
                        <td style={{ fontSize: 11 }}>
                          <span
                            style={{
                              padding: "2px 6px",
                              borderRadius: 4,
                              background: "rgba(239,68,68,0.1)",
                              color: YT_RED,
                              fontWeight: 600,
                            }}
                          >
                            {j.is_short ? "Short" : "Video"}
                          </span>
                        </td>
                        <td style={{ fontSize: 12, whiteSpace: "nowrap" }}>
                          {fmt(j.scheduled_time_utc)}
                        </td>
                        <td>
                          <span
                            style={{
                              fontSize: 11,
                              fontWeight: 600,
                              padding: "2px 6px",
                              borderRadius: 4,
                              background:
                                j.status === "posted"
                                  ? "#e6f4ea"
                                  : j.status === "failed"
                                  ? "#fdecea"
                                  : "#eceff3",
                              color:
                                j.status === "posted"
                                  ? "#1e6b34"
                                  : j.status === "failed"
                                  ? "#a3231a"
                                  : "#4a5560",
                            }}
                          >
                            {j.status}
                          </span>
                        </td>
                        <td
                          style={{
                            fontSize: 11,
                            color: "#a3231a",
                            maxWidth: 160,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {j.error || "—"}
                        </td>
                        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                          {j.status !== "posted" && (
                            <button
                              type="button"
                              className="btn btn-primary"
                              style={{
                                fontSize: 11,
                                padding: "3px 9px",
                                background: YT_RED,
                                borderColor: YT_RED,
                                color: "#fff",
                                fontWeight: 600,
                                marginRight: 6,
                              }}
                              disabled={busyId === `post-${j.id}`}
                              onClick={() => handlePostNow(j.id, j.title)}
                            >
                              {busyId === `post-${j.id}` ? "Posting..." : "Post Now ⚡"}
                            </button>
                          )}
                          {j.status === "failed" && (
                            <button
                              type="button"
                              className="btn"
                              style={{ fontSize: 11, padding: "3px 8px", marginRight: 4 }}
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
                              style={{ fontSize: 11, padding: "3px 8px" }}
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

                {/* Pagination Controls */}
                {pageSize > 0 && totalPages > 1 && (
                  <div
                    style={{
                      padding: "12px 20px",
                      borderTop: "1px solid var(--border)",
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 10,
                    }}
                  >
                    <div style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                      Showing {startIndex + 1}–
                      {Math.min(startIndex + effectivePageSize, filteredJobs.length)} of {filteredJobs.length} scheduled items
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <button
                        type="button"
                        className="btn"
                        style={{ fontSize: 11, padding: "4px 10px" }}
                        disabled={safePage <= 1}
                        onClick={() => setCurrentPage(safePage - 1)}
                      >
                        ← Previous
                      </button>
                      <span style={{ fontSize: 12, padding: "0 8px", fontWeight: 500 }}>
                        Page {safePage} of {totalPages}
                      </span>
                      <button
                        type="button"
                        className="btn"
                        style={{ fontSize: 11, padding: "4px 10px" }}
                        disabled={safePage >= totalPages}
                        onClick={() => setCurrentPage(safePage + 1)}
                      >
                        Next →
                      </button>
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        );
      })()}

      {/* ---------------- TAB 4: PUBLISHED LEDGER ---------------- */}
      {tab === "history" && (
        <div className="card" style={{ padding: 0 }}>
          <div
            style={{
              padding: "16px 20px",
              borderBottom: "1px solid var(--border)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              flexWrap: "wrap",
              gap: 10,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                Published Ledger ({activeHistory.length})
              </h2>
              {selectedChannel && (
                <span
                  style={{
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 12,
                    background: "rgba(239, 68, 68, 0.12)",
                    color: YT_RED,
                    fontWeight: 600,
                  }}
                >
                  {selectedChannel.title}
                </span>
              )}
            </div>

            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <select
                value={selectedChannelId}
                onChange={(e) => {
                  const val = e.target.value === "all" ? "all" : Number(e.target.value);
                  setSelectedChannelId(val);
                }}
                style={{ padding: "4px 8px", fontSize: 11 }}
              >
                <option value="all">All Channels</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.title}
                  </option>
                ))}
              </select>
              <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
                Refresh
              </button>
            </div>
          </div>
          {activeHistory.length === 0 ? (
            <div style={{ padding: 32, color: "var(--ink-muted)", fontSize: 13, textAlign: "center" }}>
              No YouTube videos posted yet for this channel selection.
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
                {activeHistory.map((h) => (
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
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>
            YouTube Data API v3 Configuration
          </h2>
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
                  <span style={{ color: "#a3231a", fontWeight: 600 }}>
                    ❌ Missing (YOUTUBE_CLIENT_ID / GOOGLE_CLIENT_ID)
                  </span>
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
              <code
                style={{
                  display: "block",
                  padding: "8px 12px",
                  background: "var(--paper-2)",
                  borderRadius: 6,
                  fontSize: 12,
                  marginTop: 4,
                }}
              >
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
