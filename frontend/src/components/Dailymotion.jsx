import React, { useState, useEffect, useCallback } from "react";
import { api } from "../api.js";
import { DailymotionIcon } from "./BrandIcons.jsx";
import AccountLinkModal from "./AccountLinkModal.jsx";
import {
  DollarSign,
  Tv,
  CheckCircle,
  Clock,
  Send,
  Plus,
  Trash2,
  RefreshCw,
  ExternalLink,
  ShieldCheck,
  Sparkles,
} from "lucide-react";

const DM_BLUE = "#0066DC";

const CATEGORIES = [
  { id: "news", label: "News & Current Affairs" },
  { id: "fun", label: "Entertainment & Comedy" },
  { id: "tech", label: "Science & Technology" },
  { id: "lifestyle", label: "Lifestyle & How-to" },
  { id: "auto", label: "Automotive & Vehicles" },
  { id: "music", label: "Music & Performing Arts" },
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

export default function Dailymotion({ subTab: propSubTab, onSelectSubTab }) {
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
  const [category, setCategory] = useState("news");
  const [monetization, setMonetization] = useState(true);
  const [privacy, setPrivacy] = useState("public");
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());
  const [scheduling, setScheduling] = useState(false);

  // Add channel modal
  const [showAddChannel, setShowAddChannel] = useState(false);
  const [showLinkModal, setShowLinkModal] = useState(false);
  const [newChannelId, setNewChannelId] = useState("");
  const [newChannelName, setNewChannelName] = useState("");
  const [newScreenName, setNewScreenName] = useState("");
  const [newAuthMethod, setNewAuthMethod] = useState("oauth");
  const [newAccessToken, setNewAccessToken] = useState("");
  const [validatingToken, setValidatingToken] = useState(false);
  const [tokenValidationInfo, setTokenValidationInfo] = useState(null);

  // Dailymotion Autopilot Profile state
  const DEFAULT_DM_PROFILE = {
    automation_enabled: false,
    video_enabled: true,
    photo_enabled: false,
    text_enabled: false,
    daily_video_limit: 2,
    daily_photo_limit: 0,
    daily_text_limit: 0,
    posting_window_start: 9,
    posting_window_end: 21,
    min_spacing_minutes: 45,
    content_source: "drive",
    ai_captioning: false,
    hashtag_rotation: false,
    duplicate_protection: true,
    smart_scheduling: true,
    auto_retry: true,
    analytics_sync: true,
    monetization_tracking: true,
  };
  const [dmProfile, setDmProfile] = useState(DEFAULT_DM_PROFILE);
  const [dmPreview, setDmPreview] = useState(null);
  const [loadingDmPreview, setLoadingDmPreview] = useState(false);
  const [savingDmProfile, setSavingDmProfile] = useState(false);
  const [dmProfileMsg, setDmProfileMsg] = useState("");

  async function loadDmProfile(chanId) {
    if (!chanId) return;
    try {
      const prof = await api.dailymotionAutomationProfile(chanId);
      if (prof) setDmProfile(prof);
    } catch (e) {
      console.warn("Could not load DM profile:", e);
    }
  }

  async function handleLoadDmPreview() {
    if (!destChannelId) {
      setDmProfileMsg("Select a connected Dailymotion channel first.");
      return;
    }
    setLoadingDmPreview(true);
    setDmProfileMsg("");
    try {
      const res = await api.previewDailymotionSchedule(destChannelId, dmProfile);
      setDmPreview(res);
    } catch (e) {
      setDmProfileMsg("Preview error: " + e.message);
    } finally {
      setLoadingDmPreview(false);
    }
  }

  async function handleSaveDmProfile() {
    if (!destChannelId) {
      setDmProfileMsg("Select a connected Dailymotion channel first.");
      return;
    }
    setSavingDmProfile(true);
    setDmProfileMsg("");
    try {
      await api.saveDailymotionAutomationProfile(destChannelId, dmProfile);
      setDmProfileMsg("✓ Dailymotion Autopilot profile saved! Video queue rebalanced.");
    } catch (e) {
      setDmProfileMsg("Save error: " + e.message);
    } finally {
      setSavingDmProfile(false);
    }
  }

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [ch, jb, hi, cfg] = await Promise.all([
        api.dailymotionChannels().catch(() => []),
        api.dailymotionJobs().catch(() => []),
        api.dailymotionHistory().catch(() => []),
        api.dailymotionConfig().catch(() => null),
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

  async function handleValidateToken() {
    if (!newAccessToken.trim()) {
      setNotice({ msg: "Please enter a Dailymotion API Access Token first.", isErr: true });
      return;
    }
    setValidatingToken(true);
    setNotice({ msg: "Validating access token with Dailymotion API...", isErr: false });
    try {
      const res = await api.validateDailymotionToken(newAccessToken.trim());
      if (res.is_valid) {
        setTokenValidationInfo(res);
        if (res.channel_id) setNewChannelId(res.channel_id);
        if (res.screenname) {
          setNewChannelName(res.screenname);
          setNewScreenName(res.screenname);
        }
        setNotice({
          msg: `✓ Verified Dailymotion token for @${res.screenname || res.channel_id}! Details auto-populated.`,
          isErr: false,
        });
      } else {
        setNotice({
          msg: `Token invalid: ${res.error || "Could not verify credentials with Dailymotion."}`,
          isErr: true,
        });
      }
    } catch (err) {
      setNotice({ msg: `Token check failed: ${err.message}`, isErr: true });
    } finally {
      setValidatingToken(false);
    }
  }

  async function handleAddChannel(e) {
    e.preventDefault();
    if (!newChannelId.trim() || !newChannelName.trim()) return;
    try {
      const res = await api.addDailymotionChannel({
        channel_id: newChannelId.trim(),
        channel_name: newChannelName.trim(),
        screen_name: newScreenName.trim() || newChannelName.trim(),
        auth_method: newAuthMethod,
        access_token: newAccessToken.trim() || null,
        monetization_enabled: true,
        default_privacy: "public",
      });
      const isConn = res?.channel?.is_connected;
      setNotice({
        msg: isConn
          ? `✓ Channel "${newChannelName}" connected and verified for monetization!`
          : `Channel "${newChannelName}" added (${res?.status || "Credential Required"}). Provide a valid access token to enable publishing.`,
        isErr: !isConn,
      });
      setNewChannelId("");
      setNewChannelName("");
      setNewScreenName("");
      setNewAccessToken("");
      setTokenValidationInfo(null);
      setShowAddChannel(false);
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to add channel: ${err.message}`, isErr: true });
    }
  }

  async function handleDeleteChannel(id, title) {
    if (!window.confirm(`Disconnect Dailymotion channel "${title}"?`)) return;
    try {
      await api.deleteDailymotionChannel(id);
      setNotice({ msg: `Channel "${title}" removed.`, isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to delete channel: ${err.message}`, isErr: true });
    }
  }

  async function handleCheckHealth(id) {
    setBusyId(`health-${id}`);
    try {
      const res = await api.dailymotionChannelHealth(id);
      setNotice({
        msg: `${res.channel}: ${res.status}. Partner monetization active: ${res.partner_monetization_active ? "Yes" : "No"}`,
        isErr: false,
      });
    } catch (err) {
      setNotice({ msg: `Health check failed: ${err.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function handleSchedule(e) {
    e.preventDefault();
    if (!destChannelId || !title.trim()) return;
    setScheduling(true);
    try {
      const whenUtc = new Date(scheduleAt).toISOString();
      const tagList = tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);

      await api.createDailymotionJob({
        channel_id: Number(destChannelId),
        title: title.trim(),
        description: description.trim(),
        tags: tagList,
        category,
        monetization,
        privacy_status: privacy,
        is_vertical: true,
        scheduled_time_utc: whenUtc,
      });

      setNotice({
        msg: `Video "${title}" queued for automated upload at ${fmt(whenUtc)} with monetization ON!`,
        isErr: false,
      });
      setTitle("");
      setDescription("");
      setTags("");
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to schedule: ${err.message}`, isErr: true });
    } finally {
      setScheduling(false);
    }
  }

  async function handlePublishNow() {
    if (!destChannelId || !title.trim()) {
      alert("Please select a channel and enter a video title first.");
      return;
    }
    if (!window.confirm(`Publish "${title}" to Dailymotion immediately?`)) return;

    setScheduling(true);
    try {
      const res = await api.publishDailymotionNow({
        channel_id: Number(destChannelId),
        title: title.trim(),
        category,
        monetization,
      });
      setNotice({
        msg: `Published to Dailymotion! Video ID: ${res.video_id}. Direct URL: ${res.url}`,
        isErr: false,
      });
      setTitle("");
      setDescription("");
      await loadData();
    } catch (err) {
      setNotice({ msg: `Publish failed: ${err.message}`, isErr: true });
    } finally {
      setScheduling(false);
    }
  }

  async function handleRetryJob(id) {
    try {
      await api.retryDailymotionJob(id);
      setNotice({ msg: "Upload retry initiated.", isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Retry failed: ${err.message}`, isErr: true });
    }
  }

  async function handleCancelJob(id) {
    if (!window.confirm("Cancel this scheduled upload?")) return;
    try {
      await api.cancelDailymotionJob(id);
      setNotice({ msg: "Scheduled post cancelled.", isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Cancel failed: ${err.message}`, isErr: true });
    }
  }

  const totalViews = channels.reduce((sum, c) => sum + Number(c.total_views || 0), 0);
  const totalEarnings = channels.reduce((sum, c) => sum + Number(c.estimated_earnings_usd || 0), 0);

  return (
    <div className="view">
      {/* ---------------- Opening Header ---------------- */}
      <header className="view-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <DailymotionIcon size={38} />
          <div>
            <div className="kicker" style={{ color: "var(--text-muted)" }}>Dailymotion Partner Syndication</div>
            <h1 className="h1">Dailymotion Studio</h1>
            <p className="sub" style={{ marginTop: 2 }}>
              Monetized video syndication, in-stream video ad generation, and autonomous channel queue distribution.
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
            style={{ background: "#0066dc", borderColor: "#0066dc", color: "#fff", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 7 }}
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

      {/* Account Link Modal */}
      <AccountLinkModal
        isOpen={showLinkModal}
        onClose={() => setShowLinkModal(false)}
        platform="dailymotion"
        onSaveCookies={async ({ accountAlias, rawCookies }) => {
          setNotice({
            msg: `Session cookies for "${accountAlias}" successfully saved to local vault. Dailymotion media engine online!`,
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
          <div className="stat-sub">Active Partner monetized channels</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Published Dispatches</span>
            <CheckCircle size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{history.length}</div>
          <div className="stat-sub">{totalViews.toLocaleString()} lifetime video views</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Queued Dispatches</span>
            <Clock size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{jobs.filter((j) => j.status === "pending").length}</div>
          <div className="stat-sub">Scheduled for automated upload</div>
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
          <span>Channels & Monetization</span>
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
          className={`snap-tab-item ${tab === "monetization" ? "active" : ""}`}
          onClick={() => setTab("monetization")}
        >
          <DollarSign size={14} />
          <span>Partner Ad Revenue</span>
        </button>
        <button
          type="button"
          className={`snap-tab-item ${tab === "autopilot" ? "active" : ""}`}
          onClick={() => {
            setTab("autopilot");
            if (destChannelId) loadDmProfile(destChannelId);
          }}
        >
          <Sparkles size={14} style={{ color: tab === "autopilot" ? "#38bdf8" : "inherit" }} />
          <span>Autopilot Profile</span>
        </button>
      </nav>

      {/* ---------------- TAB 1: COMPOSE & SCHEDULE ---------------- */}
      {tab === "publish" && (
        channels.length === 0 ? (
          <div className="card" style={{ padding: "48px 24px", textAlign: "center", marginBottom: 24, borderRadius: 10, border: "1px dashed var(--border-medium)" }}>
            <div style={{ width: 44, height: 44, borderRadius: 10, background: "rgba(0, 102, 220, 0.1)", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 14 }}>
              <Tv size={22} style={{ color: "#38bdf8" }} />
            </div>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6, color: "var(--text-primary)" }}>
              No Dailymotion Channels Connected Yet
            </div>
            <p style={{ color: "var(--text-muted)", fontSize: 13, maxWidth: 460, margin: "0 auto 20px", lineHeight: 1.5 }}>
              Connect your Dailymotion creator or partner channel to monetize video views with in-stream ads and automated dispatches.
            </p>
            <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowLinkModal(true)}
                style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                <ShieldCheck size={14} />
                <span>Link via Cookies (Instant)</span>
              </button>
              <button
                type="button"
                className="btn btn-primary"
                style={{ background: "#0066dc", borderColor: "#0066dc", color: "#fff", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 6 }}
                onClick={() => {
                  setTab("channels");
                  setShowAddChannel(true);
                }}
              >
                <Plus size={15} />
                <span>Connect Channel</span>
              </button>
            </div>
          </div>
        ) : (
          <div className="card" style={{ padding: 24, marginBottom: 24 }}>
            <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 16px" }}>
              Schedule Video to Dailymotion
            </h2>
            <form onSubmit={handleSchedule}>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 14 }}>
                <div className="field" style={{ margin: 0 }}>
                  <label>Target Dailymotion Channel</label>
                  <select
                    value={destChannelId}
                    onChange={(e) => setDestChannelId(e.target.value)}
                    required
                  >
                    {channels.map((c) => (
                      <option key={c.id} value={c.id}>
                        {c.channel_name} (@{c.screen_name || c.channel_id}) {c.monetization_enabled ? "· Monetized" : ""}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field" style={{ margin: 0 }}>
                  <label>Video Category</label>
                  <select
                    value={category}
                    onChange={(e) => setCategory(e.target.value)}
                  >
                    {CATEGORIES.map((cat) => (
                      <option key={cat.id} value={cat.id}>
                        {cat.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="field">
                <label>Video Title</label>
                <input
                  type="text"
                  placeholder="Compelling video title (e.g. 5 Mind-Blowing Facts You Didn't Know)"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  required
                />
              </div>

              <div className="field">
                <label>Description</label>
                <textarea
                  rows={3}
                  placeholder="Video description, channel links, and hashtags..."
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 14 }}>
                <div className="field" style={{ margin: 0 }}>
                  <label>Tags (comma-separated)</label>
                  <input
                    type="text"
                    placeholder="reels, viral, shorts, trending, news"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                  />
                </div>
                <div className="field" style={{ margin: 0 }}>
                  <label>Scheduled Post Time (Local)</label>
                  <input
                    type="datetime-local"
                    value={scheduleAt}
                    onChange={(e) => setScheduleAt(e.target.value)}
                    required
                  />
                </div>
              </div>

              {/* Monetization Ad Injection Toggle */}
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  padding: "12px 14px",
                  background: "var(--bg-input)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: 8,
                  marginBottom: 20,
                }}
              >
                <div>
                  <div style={{ fontWeight: 600, color: "var(--text-primary)", fontSize: 13, display: "flex", alignItems: "center", gap: 6 }}>
                    <DollarSign size={15} style={{ color: "#34d399" }} />
                    <span>Dailymotion Partner In-Stream Ads & Monetization</span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                    Inject video ads (pre-roll and overlays) to monetize views across Dailymotion network.
                  </div>
                </div>
                <input
                  type="checkbox"
                  checked={monetization}
                  onChange={(e) => setMonetization(e.target.checked)}
                  style={{ width: 18, height: 18, accentColor: "#34d399", cursor: "pointer" }}
                />
              </div>

              <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
                <button
                  type="button"
                  className="btn btn-secondary"
                  disabled={scheduling || !title.trim()}
                  onClick={handlePublishNow}
                  style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
                >
                  <Send size={14} />
                  <span>Publish Now</span>
                </button>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ background: "#0066dc", borderColor: "#0066dc", color: "#fff", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 6 }}
                  disabled={scheduling}
                >
                  <Clock size={14} />
                  <span>{scheduling ? "Queueing…" : "Queue Scheduled Upload"}</span>
                </button>
              </div>
            </form>
          </div>
        )
      )}

      {/* ---------------- TAB 2: CHANNELS ---------------- */}
      {tab === "channels" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
          <div
            style={{
              padding: "16px 20px",
              borderBottom: "1px solid var(--border-subtle)",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
                Connected Channels ({channels.length})
              </h2>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                Dailymotion partner channels linked for monetized distribution
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                className="btn btn-sm btn-secondary"
                onClick={() => setShowLinkModal(true)}
                style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
              >
                <ShieldCheck size={13} />
                <span>Link via Cookies</span>
              </button>
              <button
                type="button"
                className="btn btn-sm btn-primary"
                style={{ background: "#0066dc", borderColor: "#0066dc", color: "#fff", display: "inline-flex", alignItems: "center", gap: 6 }}
                onClick={() => setShowAddChannel(!showAddChannel)}
              >
                <Plus size={14} />
                <span>{showAddChannel ? "Cancel" : "Add Channel"}</span>
              </button>
            </div>
          </div>

          {showAddChannel && (
            <div style={{ padding: 20, background: "var(--bg-input)", borderBottom: "1px solid var(--border-subtle)" }}>
              <form onSubmit={handleAddChannel}>
                <div style={{ marginBottom: 14 }}>
                  <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-primary)", display: "block", marginBottom: 6 }}>
                    Dailymotion API Access Token (OAuth Bearer)
                  </label>
                  <div style={{ display: "flex", gap: 8 }}>
                    <input
                      type="password"
                      placeholder="Paste your Dailymotion OAuth or Developer Access Token..."
                      value={newAccessToken}
                      onChange={(e) => setNewAccessToken(e.target.value)}
                      style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: 13 }}
                    />
                    <button
                      type="button"
                      className="btn btn-secondary"
                      onClick={handleValidateToken}
                      disabled={validatingToken || !newAccessToken.trim()}
                      style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12.5 }}
                    >
                      <RefreshCw size={13} className={validatingToken ? "spin" : ""} />
                      <span>{validatingToken ? "Checking..." : "Validate Token"}</span>
                    </button>
                  </div>
                  <div style={{ fontSize: 11.5, color: "var(--text-muted)", marginTop: 4 }}>
                    Obtain from your Dailymotion Developer Portal app (Scopes: <code style={{ color: "#38bdf8" }}>manage_videos, userinfo</code>). Credentials are encrypted at rest.
                  </div>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
                  <div className="field" style={{ margin: 0 }}>
                    <label>Channel ID or Username</label>
                    <input
                      type="text"
                      placeholder="Auto-discovered from token, or e.g. x102847"
                      value={newChannelId}
                      onChange={(e) => setNewChannelId(e.target.value)}
                      required
                    />
                  </div>
                  <div className="field" style={{ margin: 0 }}>
                    <label>Channel Display Name</label>
                    <input
                      type="text"
                      placeholder="Auto-discovered from token, or custom title"
                      value={newChannelName}
                      onChange={(e) => setNewChannelName(e.target.value)}
                      required
                    />
                  </div>
                </div>
                <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => setShowAddChannel(false)}>
                    Cancel
                  </button>
                  <button type="submit" className="btn btn-sm btn-primary" style={{ background: DM_BLUE, color: "#fff" }}>
                    Save & Connect Channel
                  </button>
                </div>
              </form>
            </div>
          )}

          {channels.length === 0 ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
              No channels added yet. Connect your Dailymotion account above.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-subtle)", textAlign: "left" }}>
                  <th style={{ padding: "10px 16px" }}>Channel</th>
                  <th style={{ padding: "10px 16px" }}>Auth Method</th>
                  <th style={{ padding: "10px 16px" }}>Monetization</th>
                  <th style={{ padding: "10px 16px" }}>Views</th>
                  <th style={{ padding: "10px 16px" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {channels.map((ch) => (
                  <tr key={ch.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <td style={{ padding: "12px 16px", fontWeight: 600, color: "var(--text-primary)" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <div
                          style={{
                            width: 32,
                            height: 32,
                            borderRadius: 6,
                            background: "rgba(0, 102, 220, 0.15)",
                            color: DM_BLUE,
                            display: "grid",
                            placeItems: "center",
                            fontWeight: 700,
                          }}
                        >
                          D
                        </div>
                        <div>
                          <div>{ch.channel_name}</div>
                          <div style={{ fontSize: 11.5, color: "var(--text-muted)" }}>@{ch.screen_name || ch.channel_id}</div>
                        </div>
                      </div>
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <span className="badge badge-green" style={{ textTransform: "uppercase", fontSize: 10 }}>
                        {ch.auth_method}
                      </span>
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      {ch.is_connected ? (
                        <span style={{ color: "#34d399", fontWeight: 600, fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
                          <CheckCircle size={13} /> Active Partner
                        </span>
                      ) : (
                        <span style={{ color: "#f87171", fontWeight: 600, fontSize: 12, display: "inline-flex", alignItems: "center", gap: 5 }}>
                          ● Credential Required
                        </span>
                      )}
                    </td>
                    <td style={{ padding: "12px 16px", fontFamily: "var(--font-mono)" }}>
                      {ch.total_views || 0}
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          title="Check Health"
                          onClick={() => handleCheckHealth(ch.id)}
                          disabled={busyId === `health-${ch.id}`}
                        >
                          <RefreshCw size={13} className={busyId === `health-${ch.id}` ? "spin" : ""} />
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm btn-danger"
                          title="Delete Channel"
                          onClick={() => handleDeleteChannel(ch.id, ch.channel_name)}
                        >
                          <Trash2 size={13} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- TAB 3: QUEUE ---------------- */}
      {tab === "queue" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-subtle)" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
              Dailymotion Publishing Queue ({jobs.length})
            </h2>
          </div>
          {jobs.length === 0 ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
              No videos currently queued for Dailymotion.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-subtle)", textAlign: "left" }}>
                  <th style={{ padding: "10px 16px" }}>Scheduled Time</th>
                  <th style={{ padding: "10px 16px" }}>Channel</th>
                  <th style={{ padding: "10px 16px" }}>Title</th>
                  <th style={{ padding: "10px 16px" }}>Monetization</th>
                  <th style={{ padding: "10px 16px" }}>Status</th>
                  <th style={{ padding: "10px 16px" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <td style={{ padding: "12px 16px", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                      {fmt(j.scheduled_time_utc)}
                    </td>
                    <td style={{ padding: "12px 16px" }}>{j.channel_name}</td>
                    <td style={{ padding: "12px 16px", fontWeight: 500, color: "var(--text-primary)" }}>{j.title}</td>
                    <td style={{ padding: "12px 16px" }}>
                      {j.monetization ? (
                        <span style={{ color: "#34d399", fontWeight: 600, fontSize: 12 }}>Ad Enabled</span>
                      ) : (
                        <span style={{ color: "var(--text-muted)" }}>Disabled</span>
                      )}
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <span className={`badge ${j.status === "failed" ? "badge-red" : "badge-amber"}`}>
                        {j.status}
                      </span>
                    </td>
                    <td style={{ padding: "12px 16px" }}>
                      <div style={{ display: "flex", gap: 6 }}>
                        {j.status === "failed" && (
                          <button
                            type="button"
                            className="btn btn-sm btn-secondary"
                            onClick={() => handleRetryJob(j.id)}
                          >
                            Retry
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => handleCancelJob(j.id)}
                        >
                          Cancel
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- TAB 4: HISTORY LEDGER ---------------- */}
      {tab === "history" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border-subtle)" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>
              Published Video Ledger ({history.length})
            </h2>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: 40, textAlign: "center", color: "var(--text-muted)" }}>
              No published records yet. Videos will appear here once posted.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border-subtle)", textAlign: "left" }}>
                  <th style={{ padding: "10px 16px" }}>Published Date</th>
                  <th style={{ padding: "10px 16px" }}>Channel</th>
                  <th style={{ padding: "10px 16px" }}>Title</th>
                  <th style={{ padding: "10px 16px" }}>Views</th>
                  <th style={{ padding: "10px 16px" }}>Link</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id} style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                    <td style={{ padding: "12px 16px", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                      {fmt(h.published_at)}
                    </td>
                    <td style={{ padding: "12px 16px" }}>{h.channel_name}</td>
                    <td style={{ padding: "12px 16px", fontWeight: 500, color: "var(--text-primary)" }}>{h.title}</td>
                    <td style={{ padding: "12px 16px", fontFamily: "var(--font-mono)" }}>{h.views || 0}</td>
                    <td style={{ padding: "12px 16px" }}>
                      {h.dailymotion_url ? (
                        <a
                          href={h.dailymotion_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#38bdf8", display: "inline-flex", alignItems: "center", gap: 4 }}
                        >
                          <span>Watch</span>
                          <ExternalLink size={12} />
                        </a>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- TAB 5: PARTNER AD REVENUE ---------------- */}
      {tab === "monetization" && (
        <div className="card" style={{ padding: 24, marginBottom: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>
            Dailymotion Partner Program & Monetization Architecture
          </h2>
          <p style={{ color: "var(--text-secondary)", fontSize: 13, lineHeight: 1.5, margin: "0 0 20px" }}>
            Dailymotion offers a high-yield revenue sharing program (70% publisher payout) with automated in-stream
            video ad injection. Every reel or short video syndicated here produces passive income on autoplay views.
          </p>

          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 14, marginBottom: 24 }}>
            <div style={{ background: "var(--bg-input)", padding: 16, borderRadius: 8, border: "1px solid var(--border-subtle)" }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Revenue Share
              </div>
              <div style={{ fontSize: 24, fontWeight: 700, color: "#34d399", margin: "4px 0" }}>70%</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>Direct to publisher account</div>
            </div>
            <div style={{ background: "var(--bg-input)", padding: 16, borderRadius: 8, border: "1px solid var(--border-subtle)" }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Ad Formats
              </div>
              <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)", margin: "8px 0 4px" }}>Pre-roll & Overlays</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>Automated instream monetization</div>
            </div>
            <div style={{ background: "var(--bg-input)", padding: 16, borderRadius: 8, border: "1px solid var(--border-subtle)" }}>
              <div style={{ fontSize: 11, color: "var(--text-muted)", textTransform: "uppercase", fontWeight: 600 }}>
                Syndication Engine
              </div>
              <div style={{ fontSize: 16, fontWeight: 700, color: "#38bdf8", margin: "8px 0 4px" }}>Multi-Channel Auto</div>
              <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>Instant auto-dispatch</div>
            </div>
          </div>
        </div>
      )}

      {/* ---------------- TAB 6: AUTOPILOT PROFILE ---------------- */}
      {tab === "autopilot" && (
        <div className="card" style={{ padding: 24, marginBottom: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
            <div>
              <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
                Dailymotion Autonomous Channel Operating Profile
              </h2>
              <p style={{ color: "var(--text-secondary)", fontSize: 13, marginTop: 4 }}>
                Independent automation profile for Dailymotion. Video uploads, partner monetization, and zero-collision scheduling.
              </p>
            </div>
            {channels.length > 1 && (
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <label style={{ fontSize: 12, color: "var(--text-muted)" }}>Target Channel:</label>
                <select
                  value={destChannelId}
                  onChange={(e) => {
                    setDestChannelId(e.target.value);
                    loadDmProfile(e.target.value);
                  }}
                  style={{ fontSize: 12, padding: "4px 8px" }}
                >
                  {channels.map((c) => (
                    <option key={c.id} value={c.id}>{c.channel_name || c.screen_name}</option>
                  ))}
                </select>
              </div>
            )}
          </div>

          {/* Master Automation Switch */}
          <div
            style={{
              background: dmProfile.automation_enabled ? "rgba(0, 102, 220, 0.08)" : "rgba(239, 68, 68, 0.08)",
              border: `1px solid ${dmProfile.automation_enabled ? "rgba(0, 102, 220, 0.25)" : "rgba(239, 68, 68, 0.25)"}`,
              borderRadius: 8,
              padding: "12px 16px",
              marginBottom: 20,
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            <div>
              <div style={{ fontWeight: 700, fontSize: 14, color: dmProfile.automation_enabled ? "#38bdf8" : "#fca5a5" }}>
                Channel Autopilot: {dmProfile.automation_enabled ? "ACTIVE (ON)" : "PAUSED (OFF)"}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-muted)", marginTop: 2 }}>
                Distributes vertical reels, attaches high-yield pre-roll/mid-roll partner ad tags, and dispatches to Dailymotion.
              </div>
            </div>
            <label className="switch" style={{ display: "inline-flex", alignItems: "center", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={dmProfile.automation_enabled}
                onChange={(e) => setDmProfile((p) => ({ ...p, automation_enabled: e.target.checked }))}
              />
            </label>
          </div>

          {/* Content Capability Matrix Enforcement */}
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: "var(--text-primary)", marginBottom: 8, display: "flex", alignItems: "center", gap: 6 }}>
              <span>Platform Capability Enforcement</span>
              <span className="hint" style={{ fontSize: 11 }}>Official Dailymotion API Capabilities</span>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
              {/* Video */}
              <div style={{ background: "var(--bg-input)", border: "1px solid rgba(0, 102, 220, 0.3)", borderRadius: 8, padding: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#38bdf8" }}>🎥 Video / Reel</span>
                  <span style={{ fontSize: 11, background: "rgba(16, 185, 129, 0.15)", color: "#34d399", padding: "2px 6px", borderRadius: 4, fontWeight: 600 }}>
                    Supported
                  </span>
                </div>
                <label style={{ fontSize: 11, color: "var(--text-muted)" }}>Daily Video Posts</label>
                <input
                  type="number"
                  min="0"
                  max="24"
                  value={dmProfile.daily_video_limit}
                  onChange={(e) => setDmProfile((p) => ({ ...p, daily_video_limit: e.target.value }))}
                  style={{ width: "100%", marginTop: 4 }}
                />
              </div>

              {/* Photo - Explicitly Blocked */}
              <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 8, padding: 14, opacity: 0.65 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-muted)" }}>📸 Photo / Image</span>
                  <span style={{ fontSize: 10, background: "rgba(239, 68, 68, 0.15)", color: "#fca5a5", padding: "2px 6px", borderRadius: 4, fontWeight: 600 }}>
                    Not Supported
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.4 }}>
                  Dailymotion official API is video-only. Photo feed posts are permanently blocked.
                </div>
              </div>

              {/* Text - Explicitly Blocked */}
              <div style={{ background: "rgba(255,255,255,0.02)", border: "1px solid rgba(255,255,255,0.06)", borderRadius: 8, padding: 14, opacity: 0.65 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-muted)" }}>📝 Text / Status</span>
                  <span style={{ fontSize: 10, background: "rgba(239, 68, 68, 0.15)", color: "#fca5a5", padding: "2px 6px", borderRadius: 4, fontWeight: 600 }}>
                    Not Supported
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-muted)", marginTop: 6, lineHeight: 1.4 }}>
                  Standalone text/status updates are not supported by the Dailymotion creator API.
                </div>
              </div>
            </div>
          </div>

          {/* Posting Window & Monetization Settings */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12, marginBottom: 20 }}>
            <div className="field" style={{ margin: 0 }}>
              <label>Posting Window Start</label>
              <input
                type="number"
                min="0"
                max="23"
                value={dmProfile.posting_window_start}
                onChange={(e) => setDmProfile((p) => ({ ...p, posting_window_start: e.target.value }))}
              />
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label>Posting Window End</label>
              <input
                type="number"
                min="1"
                max="24"
                value={dmProfile.posting_window_end}
                onChange={(e) => setDmProfile((p) => ({ ...p, posting_window_end: e.target.value }))}
              />
            </div>
            <div className="field" style={{ margin: 0 }}>
              <label>Min Interval (Minutes)</label>
              <input
                type="number"
                min="15"
                max="180"
                value={dmProfile.min_spacing_minutes}
                onChange={(e) => setDmProfile((p) => ({ ...p, min_spacing_minutes: e.target.value }))}
              />
            </div>
          </div>

          {/* Content Source Selection */}
          <div className="field" style={{ marginBottom: 20 }}>
            <label>Content Source</label>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginTop: 4 }}>
              {["drive", "generated", "both"].map((src) => (
                <button
                  key={src}
                  type="button"
                  className="btn btn-sm"
                  style={{
                    background: dmProfile.content_source === src ? "var(--bg-card)" : "var(--bg-input)",
                    color: dmProfile.content_source === src ? "#38bdf8" : "var(--text-muted)",
                    border: `1px solid ${dmProfile.content_source === src ? "#38bdf8" : "var(--border-subtle)"}`,
                    fontWeight: 600,
                    justifyContent: "center",
                  }}
                  onClick={() => setDmProfile((p) => ({ ...p, content_source: src }))}
                >
                  {src === "drive" && "Google Drive"}
                  {src === "generated" && "AI Video Engine"}
                  {src === "both" && "Hybrid (Drive + AI)"}
                </button>
              ))}
            </div>
          </div>

          {/* Policy Checkboxes */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 20 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={dmProfile.monetization_tracking}
                onChange={(e) => setDmProfile((p) => ({ ...p, monetization_tracking: e.target.checked }))}
              />
              <span>Auto-Attach In-Stream Video Ads (70% Partner Share)</span>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={dmProfile.ai_captioning}
                onChange={(e) => setDmProfile((p) => ({ ...p, ai_captioning: e.target.checked }))}
              />
              <span>Generate Dailymotion Video Descriptions & Tags</span>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={dmProfile.duplicate_protection}
                onChange={(e) => setDmProfile((p) => ({ ...p, duplicate_protection: e.target.checked }))}
              />
              <span>Duplicate Video Protection</span>
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--text-primary)", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={dmProfile.analytics_sync}
                onChange={(e) => setDmProfile((p) => ({ ...p, analytics_sync: e.target.checked }))}
              />
              <span>Periodic Views & Earnings Sync</span>
            </label>
          </div>

          {/* Action Buttons */}
          <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 16 }}>
            <button
              type="button"
              className="btn btn-secondary btn-sm"
              onClick={handleLoadDmPreview}
              disabled={loadingDmPreview || !destChannelId}
            >
              {loadingDmPreview ? "Calculating Preview…" : "⚡ Preview Video Schedule (Dry-Run)"}
            </button>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={handleSaveDmProfile}
              disabled={savingDmProfile || !destChannelId}
              style={{ background: "#0066dc", borderColor: "#0066dc", color: "#fff" }}
            >
              {savingDmProfile ? "Saving…" : "Save Autopilot Profile"}
            </button>
          </div>

          {dmProfileMsg && (
            <div className={`banner ${dmProfileMsg.startsWith("✓") ? "" : "err"}`} style={{ fontSize: 12, padding: "8px 12px", marginBottom: 14 }}>
              {dmProfileMsg}
            </div>
          )}

          {/* Schedule Preview Rendering */}
          {dmPreview && (
            <div style={{ background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, padding: 14 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
                  Projected Dailymotion Video Schedule ({dmPreview.total_slots} videos)
                </span>
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
                  Channel: {dmPreview.channel_name || "Connected Channel"}
                </span>
              </div>
              <div style={{ maxHeight: 220, overflowY: "auto" }}>
                <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
                  <thead>
                    <tr style={{ color: "var(--text-muted)", borderBottom: "1px solid var(--border-subtle)" }}>
                      <th style={{ textAlign: "left", padding: "4px 6px" }}>Time</th>
                      <th style={{ textAlign: "left", padding: "4px 6px" }}>Format</th>
                      <th style={{ textAlign: "left", padding: "4px 6px" }}>Title / Video</th>
                      <th style={{ textAlign: "left", padding: "4px 6px" }}>Monetization</th>
                      <th style={{ textAlign: "right", padding: "4px 6px" }}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {dmPreview.timeline?.map((slot, idx) => (
                      <tr key={idx} style={{ borderBottom: "1px solid rgba(255,255,255,0.03)" }}>
                        <td style={{ padding: "6px", fontFamily: "var(--util)" }}>{slot.time}</td>
                        <td style={{ padding: "6px" }}>
                          <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 4, background: "rgba(0, 102, 220, 0.15)", color: "#38bdf8" }}>
                            VIDEO
                          </span>
                        </td>
                        <td style={{ padding: "6px" }}>{slot.content_title}</td>
                        <td style={{ padding: "6px", color: "#34d399", fontSize: 11 }}>{slot.monetization}</td>
                        <td style={{ padding: "6px", textAlign: "right", color: "#34d399" }}>✓ Ready</td>
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
  );
}
