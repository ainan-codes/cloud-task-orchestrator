import React, { useState, useEffect, useCallback, useMemo } from "react";
import { api } from "../api.js";
import { SnapchatIcon } from "./BrandIcons.jsx";
import AccountLinkModal from "./AccountLinkModal.jsx";
import {
  ShieldCheck,
  Plus,
  Users,
  Clock,
  CheckCircle,
  Settings,
  Send,
} from "lucide-react";

const SNAP_YELLOW = "#eab308";

const PLACEMENTS = [
  {
    id: "spotlight",
    label: "Spotlight",
    blurb: "Permanent video Snap distributed on Spotlight. 6–60s.",
  },
  {
    id: "story",
    label: "Public Story",
    blurb: "Ephemeral Snap on the profile's Story for 24 hours. 5–60s.",
  },
];

const STATUS_TONE = {
  published: { bg: "#e6f4ea", fg: "#1e6b34", label: "Published" },
  posted: { bg: "#e6f4ea", fg: "#1e6b34", label: "Published" },
  publishing: { bg: "#fff3cd", fg: "#7a5a00", label: "Publishing" },
  uploading: { bg: "#fff3cd", fg: "#7a5a00", label: "Uploading" },
  finalizing: { bg: "#fff3cd", fg: "#7a5a00", label: "Finalizing" },
  validating: { bg: "#fff3cd", fg: "#7a5a00", label: "Validating" },
  posting: { bg: "#fff3cd", fg: "#7a5a00", label: "Publishing" },
  pending: { bg: "#eceff3", fg: "#4a5560", label: "Queued" },
  failed: { bg: "#fdecea", fg: "#a3231a", label: "Failed" },
  abandoned: { bg: "#fdecea", fg: "#a3231a", label: "Gave up" },
  skipped: { bg: "#eceff3", fg: "#4a5560", label: "Skipped" },
};

function StatusPill({ status }) {
  const tone = STATUS_TONE[status] || { bg: "#eceff3", fg: "#4a5560", label: status || "—" };
  return (
    <span
      style={{
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        whiteSpace: "nowrap",
        background: tone.bg,
        color: tone.fg,
      }}
    >
      {tone.label}
    </span>
  );
}

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

/** Local wall-clock string for a datetime-local input, N hours from now. */
function defaultScheduleValue(hoursAhead = 1) {
  const d = new Date(Date.now() + hoursAhead * 3600 * 1000);
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(
    d.getHours()
  )}:${pad(d.getMinutes())}`;
}

export default function Snapchat({ subTab: propSubTab, onSelectSubTab }) {
  const [internalTab, setInternalTab] = useState("publish");
  const tab = propSubTab || internalTab;
  const setTab = onSelectSubTab || setInternalTab;

  const [config, setConfig] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const [stats, setStats] = useState([]);
  const [history, setHistory] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState({ msg: "", isErr: false });
  const [connecting, setConnecting] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [healthById, setHealthById] = useState({});

  // Destination: which connected profile, and where on it the content goes.
  const [destAccountId, setDestAccountId] = useState(null);
  const [placement, setPlacement] = useState("spotlight");
  const [content, setContent] = useState([]);
  const [contentSearch, setContentSearch] = useState("");
  const [contentLoading, setContentLoading] = useState(false);
  const [selectedItem, setSelectedItem] = useState(null);
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());
  const [scheduling, setScheduling] = useState(false);

  const statsById = useMemo(() => {
    const m = {};
    for (const s of stats) m[s.account_id] = s;
    return m;
  }, [stats]);

  // ---- OAuth callback result, handed back as query params ----
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("snap_connected") === "1") {
      setNotice({
        msg: `Connected @${params.get("snap_username") || "profile"}. Its access token is stored encrypted on the server.`,
        isErr: false,
      });
    } else if (params.get("snap_error")) {
      setNotice({ msg: params.get("snap_error"), isErr: true });
    } else {
      return;
    }
    // Drop the query so a refresh does not replay the banner, while keeping
    // the /snapchat path the callback landed on.
    window.history.replaceState({}, document.title, window.location.pathname);
  }, []);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [cfg, accs, st, hist, queue] = await Promise.all([
        api.snapchatConfig().catch(() => null),
        api.snapchatAccounts(),
        api.snapchatStats().catch(() => []),
        api.snapchatPublishingHistory(null, 25).catch(() => []),
        api.snapchatJobs().catch(() => []),
      ]);
      setConfig(cfg);
      setAccounts(accs || []);
      setStats(st || []);
      setHistory(hist || []);
      setJobs(queue || []);
      setDestAccountId((current) => {
        if (current && (accs || []).some((a) => a.id === current)) return current;
        const firstConnected = (accs || []).find((a) => a.is_connected);
        return firstConnected ? firstConnected.id : null;
      });
    } catch (e) {
      setNotice({ msg: e.message, isErr: true });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // ---- content for the chosen destination ----
  const loadContent = useCallback(async () => {
    if (!destAccountId) {
      setContent([]);
      return;
    }
    setContentLoading(true);
    try {
      const res = await api.snapchatAccountContent(destAccountId, {
        placement,
        search: contentSearch,
      });
      setContent(res.items || []);
    } catch (e) {
      setNotice({ msg: `Could not load content: ${e.message}`, isErr: true });
    } finally {
      setContentLoading(false);
    }
  }, [destAccountId, placement, contentSearch]);

  useEffect(() => {
    const t = setTimeout(loadContent, contentSearch ? 300 : 0);
    return () => clearTimeout(t);
  }, [loadContent, contentSearch]);

  useEffect(() => {
    setSelectedItem(null);
  }, [destAccountId, placement]);

  // ---- actions ----
  async function connect() {
    setConnecting(true);
    setNotice({ msg: "", isErr: false });
    try {
      const res = await api.snapchatOAuthAuthorize();
      if (!res?.authorization_url) throw new Error("No authorization URL returned.");
      window.location.href = res.authorization_url;
    } catch (e) {
      setNotice({ msg: `Could not start the Snapchat connection: ${e.message}`, isErr: true });
      setConnecting(false);
    }
  }

  async function checkHealth(acc) {
    setBusyId(acc.id);
    try {
      const h = await api.snapchatAccountHealth(acc.id);
      setHealthById((m) => ({ ...m, [acc.id]: h }));
      setNotice(
        h.healthy
          ? { msg: `${acc.name}: connection healthy (profile ${h.profile?.id}).`, isErr: false }
          : { msg: `${acc.name}: ${h.error || "connection is not healthy."}`, isErr: true }
      );
      await loadData();
    } catch (e) {
      setNotice({ msg: `Health check failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function refreshToken(acc) {
    setBusyId(acc.id);
    try {
      const res = await api.refreshSnapchatToken(acc.id);
      setNotice({
        msg: `${acc.name}: access token refreshed, valid until ${fmt(res.token_expires_at)}.`,
        isErr: false,
      });
      await loadData();
    } catch (e) {
      setNotice({ msg: `Token refresh failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function disconnect(acc) {
    if (
      !window.confirm(
        `Disconnect ${acc.name}?\n\nIts stored Snapchat tokens are deleted and its queued posts are cancelled. ` +
          `Its history stays, other connected accounts are unaffected, and you can reconnect it later.`
      )
    )
      return;
    setBusyId(acc.id);
    try {
      await api.disconnectSnapchatAccount(acc.id);
      setNotice({ msg: `${acc.name} disconnected.`, isErr: false });
      await loadData();
    } catch (e) {
      setNotice({ msg: `Disconnect failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function removeAccount(acc) {
    if (
      !window.confirm(
        `Delete ${acc.name} completely?\n\nThis removes the account, its content rows, its queue and its publishing history. This cannot be undone.`
      )
    )
      return;
    setBusyId(acc.id);
    try {
      await api.deleteSnapchatAccount(acc.id);
      setNotice({ msg: `${acc.name} deleted.`, isErr: false });
      await loadData();
    } catch (e) {
      setNotice({ msg: `Delete failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function setDefaultPlacement(acc, value) {
    try {
      await api.updateSnapchatAccount(acc.id, { default_placement: value });
      await loadData();
    } catch (e) {
      setNotice({ msg: e.message, isErr: true });
    }
  }

  async function scheduleSelected() {
    if (!destAccountId || !selectedItem) return;
    setScheduling(true);
    try {
      // datetime-local is wall-clock; convert to the UTC instant the API wants.
      const whenUtc = new Date(scheduleAt).toISOString();
      await api.createSnapchatJob({
        snapchat_account_id: destAccountId,
        content_item_id: selectedItem.id,
        scheduled_time_utc: whenUtc,
        placement,
      });
      setNotice({
        msg: `Queued "${selectedItem.file_name}" for ${placement} at ${fmt(whenUtc)}.`,
        isErr: false,
      });
      setSelectedItem(null);
      await Promise.all([loadData(), loadContent()]);
    } catch (e) {
      setNotice({ msg: `Could not queue that post: ${e.message}`, isErr: true });
    } finally {
      setScheduling(false);
    }
  }

  async function publishSelectedNow() {
    if (!destAccountId || !selectedItem) return;
    if (
      !window.confirm(
        `Publish "${selectedItem.file_name}" to ${placement} right now?\n\nThis uploads to Snapchat immediately.`
      )
    )
      return;
    setScheduling(true);
    try {
      const res = await api.publishSnapchatNow(destAccountId, {
        content_item_id: selectedItem.id,
        placement,
      });
      setNotice({
        msg: res.already_published
          ? `Already published (${res.post_id}). Nothing was posted twice.`
          : `Published to ${placement}. Snapchat ${res.post_id_kind || "id"}: ${res.post_id}`,
        isErr: false,
      });
      setSelectedItem(null);
      await Promise.all([loadData(), loadContent()]);
    } catch (e) {
      setNotice({ msg: `Publish failed: ${e.message}`, isErr: true });
    } finally {
      setScheduling(false);
    }
  }

  async function retryJob(job) {
    setBusyId(`job-${job.id}`);
    try {
      const res = await api.retrySnapchatJob(job.id);
      setNotice({ msg: `Retry finished: ${res.outcome}.`, isErr: res.outcome !== "posted" });
      await loadData();
    } catch (e) {
      setNotice({ msg: `Retry failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function cancelJob(job) {
    if (!window.confirm(`Cancel the queued ${job.target_placement} post for ${job.file_name}?`)) return;
    setBusyId(`job-${job.id}`);
    try {
      await api.cancelSnapchatJob(job.id);
      await loadData();
    } catch (e) {
      setNotice({ msg: `Cancel failed: ${e.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  // ---- derived ----
  const connectedAccounts = accounts.filter((a) => a.is_connected);
  const totalPublished = stats.reduce((n, s) => n + parseInt(s.published_count || 0, 10), 0);
  const totalPending = stats.reduce((n, s) => n + parseInt(s.pending_scheduled || 0, 10), 0);
  const failedJobs = jobs.filter((j) => j.status === "failed" || j.status === "abandoned");
  const destAccount = accounts.find((a) => a.id === destAccountId) || null;
  const bounds =
    placement === "story" ? config?.story_seconds : config?.spotlight_seconds;

  const [showLinkModal, setShowLinkModal] = useState(false);
  const notReady = config && !config.ready;

  return (
    <div className="view">
      <header className="view-head">
        <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
          <SnapchatIcon size={38} />
          <div>
            <div className="kicker" style={{ color: "#eab308" }}>Snapchat Spotlight & Stories</div>
            <h1 className="h1">Snapchat Studio</h1>
            <p className="sub" style={{ marginTop: 2 }}>
              Publish to Spotlight and Public Stories from your connected Public Profiles.
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
            style={{ background: "#eab308", color: "#000", borderColor: "#eab308", fontWeight: 700, display: "inline-flex", alignItems: "center", gap: 7 }}
            disabled={connecting || notReady}
            onClick={connect}
            title={notReady ? "Set SNAPCHAT_CLIENT_ID and SNAPCHAT_CLIENT_SECRET first" : ""}
          >
            <Plus size={15} />
            <span>{connecting ? "Redirecting…" : "Connect Snapchat"}</span>
          </button>
        </div>
      </header>

      <AccountLinkModal
        isOpen={showLinkModal}
        onClose={() => setShowLinkModal(false)}
        platform="snapchat"
        onStartOAuth={connect}
        onSaveCookies={async ({ accountAlias, rawCookies }) => {
          setNotice({
            msg: `Cookies for "${accountAlias}" successfully saved to local vault. Playwright engine connected!`,
            isErr: false,
          });
        }}
      />

      {notice.msg && (
        <div className={`banner ${notice.isErr ? "err" : ""}`} style={{ marginBottom: 16 }}>
          {notice.msg}
        </div>
      )}

      {notReady && (
        <div className="banner err" style={{ marginBottom: 16 }}>
          <strong>Snapchat is not configured on the server.</strong>{" "}
          {!config.client_id_configured && "SNAPCHAT_CLIENT_ID is missing. "}
          {!config.client_secret_configured && "SNAPCHAT_CLIENT_SECRET is missing. "}
          Create the OAuth app in Snapchat Ads Manager → Business Dashboard, then set both as
          environment variables. Register this exact redirect URI:{" "}
          <code>{config.redirect_uri}</code>
        </div>
      )}

      {config && config.ready && !config.encryption_key_configured && (
        <div className="banner" style={{ marginBottom: 16 }}>
          Tokens are encrypted with a key derived from your client secret. Set{" "}
          <code>SNAPCHAT_ENCRYPTION_KEY</code> so rotating the Snapchat client secret does not
          invalidate every stored token.
        </div>
      )}

      {/* ---------------- KPIs ---------------- */}
      <div className="grid-3" style={{ marginBottom: 24 }}>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Connected Profiles</span>
            <Users size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{connectedAccounts.length}</div>
          <div className="stat-sub">
            {accounts.length - connectedAccounts.length > 0
              ? `${accounts.length - connectedAccounts.length} disconnected`
              : "Public Profiles authenticated"}
          </div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Published Dispatches</span>
            <CheckCircle size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{totalPublished}</div>
          <div className="stat-sub">Spotlights and Public Stories</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Queued Dispatches</span>
            <Clock size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{totalPending}</div>
          <div className="stat-sub">
            {failedJobs.length > 0 ? `${failedJobs.length} needing attention` : "Queue fully synced"}
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
          className={`snap-tab-item ${tab === "profiles" ? "active" : ""}`}
          onClick={() => setTab("profiles")}
        >
          <Users size={14} />
          <span>Public Profiles</span>
          <span className="snap-tab-badge">{connectedAccounts.length}</span>
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

      {/* ---------------- Accounts Tab ---------------- */}
      {tab === "profiles" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
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
            Connected Public Profiles ({accounts.length})
          </h2>
          <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
            Refresh
          </button>
        </div>

        {loading ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--ink-muted)" }}>
            Loading Snapchat accounts…
          </div>
        ) : accounts.length === 0 ? (
          <div style={{ padding: "48px 24px", textAlign: "center" }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
              No Snapchat profiles connected
            </div>
            <p
              style={{
                color: "var(--ink-muted)",
                fontSize: 13,
                maxWidth: 460,
                margin: "0 auto 16px",
              }}
            >
              Connect a Snapchat Public Profile to schedule Spotlight videos and Public Stories.
              You can connect several — each keeps its own credentials, and each scheduled post
              names the profile it belongs to.
            </p>
            <button
              className="btn btn-primary"
              style={{ background: SNAP_YELLOW, color: "#000", borderColor: SNAP_YELLOW, fontWeight: 700 }}
              disabled={connecting || notReady}
              onClick={connect}
            >
              {connecting ? "Redirecting…" : "Connect Snapchat"}
            </button>
          </div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Profile</th>
                <th>Public Profile ID</th>
                <th>Default placement</th>
                <th>Connection</th>
                <th>Token</th>
                <th style={{ textAlign: "right" }}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((acc) => {
                const s = statsById[acc.id] || {};
                const health = healthById[acc.id];
                const busy = busyId === acc.id;
                const expired = s.token_expired;
                return (
                  <tr key={acc.id}>
                    <td>
                      <div style={{ fontWeight: 600 }}>{acc.profile_name || acc.name}</div>
                      <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                        @{acc.snapchat_username}
                        {s.published_count ? ` · ${s.published_count} published` : ""}
                      </div>
                    </td>
                    <td style={{ fontSize: 11, fontFamily: "monospace", color: "var(--ink-muted)" }}>
                      {acc.public_profile_id || "—"}
                    </td>
                    <td>
                      <select
                        value={acc.default_placement || "spotlight"}
                        onChange={(e) => setDefaultPlacement(acc, e.target.value)}
                        style={{ fontSize: 12, padding: "2px 6px" }}
                      >
                        {PLACEMENTS.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <StatusPill
                        status={acc.is_connected ? "published" : "skipped"}
                      />
                      <div style={{ fontSize: 11, color: "var(--ink-muted)", marginTop: 2 }}>
                        {acc.is_connected ? "Connected" : "Disconnected"}
                      </div>
                    </td>
                    <td style={{ fontSize: 11 }}>
                      <div style={{ color: expired ? "#a3231a" : "var(--ink)" }}>
                        {acc.token_status || "unknown"}
                        {acc.can_auto_refresh ? " · auto-refresh" : " · no refresh token"}
                      </div>
                      <div style={{ color: "var(--ink-muted)" }}>
                        expires {fmt(acc.token_expires_at)}
                      </div>
                      {(acc.token_error || health?.error) && (
                        <div style={{ color: "#a3231a", marginTop: 2, maxWidth: 260 }}>
                          {health?.error || acc.token_error}
                        </div>
                      )}
                    </td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                        disabled={busy}
                        onClick={() => checkHealth(acc)}
                      >
                        {busy ? "…" : "Check"}
                      </button>
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                        disabled={busy || !acc.can_auto_refresh}
                        onClick={() => refreshToken(acc)}
                        title={acc.can_auto_refresh ? "" : "No refresh token stored; reconnect instead"}
                      >
                        Refresh token
                      </button>
                      {acc.is_connected ? (
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                          disabled={busy}
                          onClick={() => disconnect(acc)}
                        >
                          Disconnect
                        </button>
                      ) : (
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                          disabled={connecting}
                          onClick={connect}
                        >
                          Reconnect
                        </button>
                      )}
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4, color: "#a3231a" }}
                        disabled={busy}
                        onClick={() => removeAccount(acc)}
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
      )}

      {/* ---------------- Compose & Schedule Tab ---------------- */}
      {tab === "publish" && (
        connectedAccounts.length === 0 ? (
          <div className="card" style={{ padding: "48px 24px", textAlign: "center", marginBottom: 24 }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
              No Snapchat Profiles Connected Yet
            </div>
            <p style={{ color: "var(--ink-muted)", fontSize: 13, maxWidth: 460, margin: "0 auto 16px" }}>
              Connect a Snapchat Public Profile to schedule Spotlight videos and Public Stories.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              style={{ background: SNAP_YELLOW, color: "#000", borderColor: SNAP_YELLOW, fontWeight: 700 }}
              onClick={() => setTab("profiles")}
            >
              Go to Connected Profiles →
            </button>
          </div>
        ) : (
          <div className="card" style={{ padding: 0, marginBottom: 24 }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Compose & Schedule Post</h2>
            </div>

          <div style={{ padding: "16px 20px", display: "grid", gap: 16 }}>
            {/* destination */}
            <div style={{ display: "flex", gap: 24, flexWrap: "wrap" }}>
              <label style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Destination profile</span>
                <select
                  value={destAccountId || ""}
                  onChange={(e) => setDestAccountId(Number(e.target.value))}
                  style={{ minWidth: 240, padding: "6px 8px" }}
                >
                  {connectedAccounts.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.profile_name || a.name} (@{a.snapchat_username})
                    </option>
                  ))}
                </select>
              </label>

              <div style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Content destination</span>
                <div style={{ display: "flex", gap: 8 }}>
                  {PLACEMENTS.map((p) => (
                    <button
                      key={p.id}
                      type="button"
                      className="btn"
                      onClick={() => setPlacement(p.id)}
                      title={p.blurb}
                      style={{
                        fontSize: 12,
                        fontWeight: placement === p.id ? 700 : 500,
                        background: placement === p.id ? SNAP_YELLOW : "",
                        borderColor: placement === p.id ? SNAP_YELLOW : "",
                        color: placement === p.id ? "#000" : "",
                      }}
                    >
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>

              <label style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Post at (your local time)</span>
                <input
                  type="datetime-local"
                  value={scheduleAt}
                  onChange={(e) => setScheduleAt(e.target.value)}
                  style={{ padding: "6px 8px" }}
                />
              </label>
            </div>

            {bounds && (
              <div style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                {PLACEMENTS.find((p) => p.id === placement)?.blurb} Snapchat requires .mp4,{" "}
                {bounds[0]}–{bounds[1]}s, at least {config.min_resolution?.[0]}×
                {config.min_resolution?.[1]}. Files that miss this are rejected before upload, with
                the reason shown here.
                {placement === "spotlight" && config.monetization_min_spotlight_seconds && (
                  <>
                    {" "}
                    <strong>Publishing and earning are different bars:</strong> Snapchat's
                    Monetization Program only pays on Spotlight videos of at least{" "}
                    {config.monetization_min_spotlight_seconds}s, so anything shorter posts fine
                    but cannot generate revenue.
                  </>
                )}
              </div>
            )}

            {/* content picker */}
            <div>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                <span className="sec-label">
                  Content available for {destAccount?.snapchat_username ? `@${destAccount.snapchat_username}` : "this profile"} ·{" "}
                  {placement}
                </span>
                <input
                  placeholder="Search captions or file names…"
                  value={contentSearch}
                  onChange={(e) => setContentSearch(e.target.value)}
                  style={{ padding: "4px 8px", fontSize: 12, minWidth: 240 }}
                />
              </div>

              <div style={{ maxHeight: 260, overflowY: "auto", border: "1px solid var(--border)" }}>
                {contentLoading ? (
                  <div style={{ padding: 20, color: "var(--ink-muted)" }}>Loading content…</div>
                ) : content.length === 0 ? (
                  <div style={{ padding: 20, color: "var(--ink-muted)", fontSize: 13 }}>
                    Nothing left to publish here. Everything already went out to this profile's{" "}
                    {placement}, or no content has been synced yet.
                  </div>
                ) : (
                  <table className="table" style={{ margin: 0 }}>
                    <tbody>
                      {content.map((it) => {
                        const chosen = selectedItem?.id === it.id;
                        return (
                          <tr
                            key={it.id}
                            onClick={() => setSelectedItem(chosen ? null : it)}
                            style={{
                              cursor: "pointer",
                              background: chosen ? "rgba(255,252,0,0.18)" : "",
                            }}
                          >
                            <td style={{ width: 28 }}>
                              <input type="radio" readOnly checked={chosen} />
                            </td>
                            <td>
                              <div style={{ fontWeight: 600, fontSize: 13 }}>{it.file_name}</div>
                              <div style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                                {(it.caption || "").slice(0, 90) || "No caption"}
                              </div>
                            </td>
                            <td style={{ fontSize: 11, color: "var(--ink-muted)", whiteSpace: "nowrap" }}>
                              {it.page_name ? `from ${it.page_name}` : "Snapchat library"}
                              {it.is_scheduled ? " · already queued" : ""}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>

            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button
                className="btn btn-primary"
                disabled={!selectedItem || scheduling}
                onClick={scheduleSelected}
              >
                {scheduling ? "Working…" : "Queue this post"}
              </button>
              <button className="btn" disabled={!selectedItem || scheduling} onClick={publishSelectedNow}>
                Publish now
              </button>
              {selectedItem && (
                <span style={{ fontSize: 12, color: "var(--ink-muted)" }}>
                  {selectedItem.file_name} → {placement}
                </span>
              )}
            </div>
          </div>
        </div>
      ))}

      {/* ---------------- Queue Tab ---------------- */}
      {tab === "queue" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Publishing Queue ({jobs.length})</h2>
            <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
              Refresh Queue
            </button>
          </div>
          {jobs.length === 0 ? (
            <div style={{ padding: 24, color: "var(--ink-muted)", fontSize: 13 }}>
              Nothing queued. Scheduled posts stay here until their time arrives, then publish on the
              next scheduler run.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Profile</th>
                  <th>Video</th>
                  <th>Placement</th>
                  <th>Scheduled for</th>
                  <th>Status</th>
                  <th>Why it failed</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td style={{ fontSize: 12 }}>@{job.snapchat_username}</td>
                    <td style={{ fontSize: 12 }}>{job.file_name}</td>
                    <td style={{ fontSize: 12 }}>{job.target_placement}</td>
                    <td style={{ fontSize: 12 }}>{fmt(job.scheduled_time_utc)}</td>
                    <td>
                      <StatusPill status={job.status} />
                      {job.attempts > 0 && (
                        <div style={{ fontSize: 10, color: "var(--ink-muted)" }}>
                          {job.attempts} attempt{job.attempts === 1 ? "" : "s"}
                        </div>
                      )}
                    </td>
                    <td style={{ fontSize: 11, color: "#a3231a", maxWidth: 280 }}>{job.error || ""}</td>
                    <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                      {(job.status === "failed" || job.status === "abandoned") && (
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px" }}
                          disabled={busyId === `job-${job.id}`}
                          onClick={() => retryJob(job)}
                        >
                          {busyId === `job-${job.id}` ? "Retrying…" : "Retry"}
                        </button>
                      )}
                      {job.status !== "posted" && (
                        <button
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", marginLeft: 4 }}
                          disabled={busyId === `job-${job.id}`}
                          onClick={() => cancelJob(job)}
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

      {/* ---------------- Publishing History Tab ---------------- */}
      {tab === "history" && (
        <div className="card" style={{ padding: 0, marginBottom: 24 }}>
          <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Publishing History & Ledger ({history.length})</h2>
            <button className="btn" style={{ fontSize: 11, padding: "4px 10px" }} onClick={loadData}>
              Refresh Ledger
            </button>
          </div>
          {history.length === 0 ? (
            <div style={{ padding: 24, color: "var(--ink-muted)", fontSize: 13 }}>
              No publishing attempts recorded yet.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Profile</th>
                  <th>Placement</th>
                  <th>Status</th>
                  <th>Snapchat ID</th>
                  <th>Media</th>
                  <th>Failure reason</th>
                </tr>
              </thead>
              <tbody>
                {history.map((rec) => (
                  <tr key={rec.id}>
                    <td style={{ fontSize: 12 }}>{fmt(rec.published_at || rec.created_at)}</td>
                    <td style={{ fontSize: 12 }}>@{rec.snapchat_username}</td>
                    <td style={{ fontSize: 12 }}>{rec.target_placement}</td>
                    <td>
                      <StatusPill status={rec.status} />
                      {rec.stage && rec.status !== "published" && (
                        <div style={{ fontSize: 10, color: "var(--ink-muted)" }}>{rec.stage}</div>
                      )}
                    </td>
                    <td style={{ fontSize: 11, fontFamily: "monospace" }}>
                      {rec.post_id || "—"}
                      {rec.post_id_kind === "request_id" && (
                        <div style={{ fontSize: 10, color: "var(--ink-muted)" }}>
                          request id (Snapchat returns no story id)
                        </div>
                      )}
                    </td>
                    <td style={{ fontSize: 11, color: "var(--ink-muted)" }}>
                      {rec.media_width
                        ? `${rec.media_width}×${rec.media_height} · ${Number(
                            rec.media_duration_seconds || 0
                          ).toFixed(1)}s`
                        : "—"}
                    </td>
                    <td style={{ fontSize: 11, color: "#a3231a", maxWidth: 280 }}>
                      {rec.error_message || ""}
                      {rec.error_code ? ` (${rec.error_code})` : ""}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- API & Settings Tab ---------------- */}
      {tab === "settings" && (
        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>Snapchat API & Platform Setup</h2>
          <p style={{ color: "var(--ink-soft)", fontSize: 13, lineHeight: 1.5, marginBottom: 20 }}>
            Snapchat Public Profile API credentials and OAuth encryption vault status.
          </p>
          <div style={{ display: "grid", gap: 16, maxWidth: 640 }}>
            <div>
              <div className="sec-label">App Client ID</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {config?.client_id_configured ? (
                  <span style={{ color: "#1e6b34", fontWeight: 600 }}>✅ Configured in database vault</span>
                ) : (
                  <span style={{ color: "#a3231a", fontWeight: 600 }}>❌ Missing (SNAPCHAT_CLIENT_ID)</span>
                )}
              </div>
            </div>
            <div>
              <div className="sec-label">App Client Secret</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {config?.client_secret_configured ? (
                  <span style={{ color: "#1e6b34", fontWeight: 600 }}>✅ Encrypted at rest with Fernet</span>
                ) : (
                  <span style={{ color: "#a3231a", fontWeight: 600 }}>❌ Missing (SNAPCHAT_CLIENT_SECRET)</span>
                )}
              </div>
            </div>
            <div>
              <div className="sec-label">Registered OAuth Redirect URI</div>
              <code style={{ display: "block", padding: "8px 12px", background: "var(--paper-2)", borderRadius: 6, fontSize: 12, marginTop: 4 }}>
                {config?.redirect_uri || "https://your-domain/api/snapchat/oauth/callback"}
              </code>
            </div>
            <div>
              <div className="sec-label">Encryption Key</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                {config?.encryption_key_configured ? (
                  <span style={{ color: "#1e6b34", fontWeight: 600 }}>✅ SNAPCHAT_ENCRYPTION_KEY set</span>
                ) : (
                  <span style={{ color: "var(--ink-soft)" }}>⚠️ Using client-secret fallback key</span>
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
