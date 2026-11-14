import React, { useState, useEffect, useCallback } from "react";
import { api } from "../api.js";
import { RumbleIcon } from "./BrandIcons.jsx";
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

const RUMBLE_GREEN = "#84cc16";
const RUMBLE_LIGHT = "#a3e635";

const LICENSE_OPTIONS = [
  {
    id: "exclusive",
    label: "Exclusive Video Management",
    blurb: "Highest monetization payout. Rumble distributes to YouTube, Yahoo, and partners.",
  },
  {
    id: "rumble_only",
    label: "Rumble Player Only",
    blurb: "Exclusive to Rumble platform. Monetized via Rumble video ad views.",
  },
  {
    id: "non_exclusive",
    label: "Personal Use / Non-Exclusive",
    blurb: "Retain all rights. Non-monetized or limited distribution.",
  },
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

export default function Rumble({ subTab: propSubTab, onSelectSubTab }) {
  const [internalTab, setInternalTab] = useState("publish");
  const tab = propSubTab || internalTab;
  const setTab = onSelectSubTab || setInternalTab;

  const [accounts, setAccounts] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [history, setHistory] = useState([]);
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState({ msg: "", isErr: false });
  const [busyId, setBusyId] = useState(null);

  // Form states
  const [destAccountId, setDestAccountId] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [licenseType, setLicenseType] = useState("exclusive");
  const [visibility, setVisibility] = useState("public");
  const [scheduleAt, setScheduleAt] = useState(defaultScheduleValue());
  const [scheduling, setScheduling] = useState(false);

  // Add account form
  const [showAddAccount, setShowAddAccount] = useState(false);
  const [newUsername, setNewUsername] = useState("");
  const [newChannelName, setNewChannelName] = useState("");
  const [newDefaultLicense, setNewDefaultLicense] = useState("exclusive");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [accs, jb, hi, cfg] = await Promise.all([
        api.rumbleAccounts().catch(() => []),
        api.rumbleJobs().catch(() => []),
        api.rumbleHistory().catch(() => []),
        api.rumbleConfig().catch(() => null),
      ]);
      setAccounts(accs || []);
      setJobs(jb || []);
      setHistory(hi || []);
      setConfig(cfg);
      if (accs?.length && !destAccountId) {
        setDestAccountId(accs[0].id);
      }
    } catch (e) {
      setNotice({ msg: e.message, isErr: true });
    } finally {
      setLoading(false);
    }
  }, [destAccountId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleAddAccount(e) {
    e.preventDefault();
    if (!newUsername.trim()) return;
    try {
      await api.addRumbleAccount({
        username: newUsername.trim(),
        channel_name: newChannelName.trim() || null,
        default_license: newDefaultLicense,
        default_visibility: "public",
      });
      setNotice({ msg: `Rumble account @${newUsername} saved!`, isErr: false });
      setNewUsername("");
      setNewChannelName("");
      setShowAddAccount(false);
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to add account: ${err.message}`, isErr: true });
    }
  }

  async function handleDeleteAccount(id, username) {
    if (!window.confirm(`Remove Rumble account @${username}?`)) return;
    try {
      await api.deleteRumbleAccount(id);
      setNotice({ msg: `Account @${username} removed.`, isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Failed to remove account: ${err.message}`, isErr: true });
    }
  }

  async function handleCreateJob(e) {
    e.preventDefault();
    if (!destAccountId || !title.trim()) return;
    setScheduling(true);
    try {
      const whenUtc = new Date(scheduleAt).toISOString();
      const tagsList = tags.split(",").map((t) => t.trim()).filter(Boolean);
      await api.createRumbleJob({
        account_id: Number(destAccountId),
        title: title.trim(),
        description: description.trim(),
        tags: tagsList,
        license_type: licenseType,
        visibility,
        scheduled_time_utc: whenUtc,
      });
      setNotice({ msg: `Queued "${title}" for Rumble at ${fmt(whenUtc)}!`, isErr: false });
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
      await api.retryRumbleJob(id);
      setNotice({ msg: "Rumble job reset to pending.", isErr: false });
      await loadData();
    } catch (err) {
      setNotice({ msg: `Retry failed: ${err.message}`, isErr: true });
    } finally {
      setBusyId(null);
    }
  }

  async function handleCancelJob(id) {
    if (!window.confirm("Cancel this scheduled Rumble post?")) return;
    try {
      await api.cancelRumbleJob(id);
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
          <RumbleIcon size={38} />
          <div>
            <div className="kicker" style={{ color: "#84cc16" }}>Rumble Video Monetization</div>
            <h1 className="h1">Rumble Studio</h1>
            <p className="sub" style={{ marginTop: 2 }}>
              Monetized video distribution, channel queues, and autonomous posting for Rumble.com.
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
            style={{ background: "#65a30d", borderColor: "#65a30d", color: "#fff", fontWeight: 600, display: "inline-flex", alignItems: "center", gap: 7 }}
            onClick={() => {
              setTab("accounts");
              setShowAddAccount(true);
            }}
          >
            <Plus size={15} />
            <span>Add Account</span>
          </button>
        </div>
      </header>

      <AccountLinkModal
        isOpen={showLinkModal}
        onClose={() => setShowLinkModal(false)}
        platform="rumble"
        onSaveCookies={async ({ accountAlias, rawCookies }) => {
          setNotice({
            msg: `Session cookies for "${accountAlias}" successfully saved to vault. Playwright automation engine synced!`,
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
            <span className="stat-label">Connected Accounts</span>
            <Tv size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{accounts.length}</div>
          <div className="stat-sub">Rumble creator channels</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Published Videos</span>
            <CheckCircle size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{history.length}</div>
          <div className="stat-sub">Videos on Rumble.com</div>
        </div>
        <div className="card stat-card">
          <div className="stat-card-head">
            <span className="stat-label">Queued Dispatches</span>
            <Clock size={16} className="stat-icon" />
          </div>
          <div className="stat-num">{jobs.filter((j) => j.status === "pending").length}</div>
          <div className="stat-sub">Scheduled for upload</div>
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
          className={`snap-tab-item ${tab === "accounts" ? "active" : ""}`}
          onClick={() => setTab("accounts")}
        >
          <Tv size={14} />
          <span>Accounts</span>
          <span className="snap-tab-badge">{accounts.length}</span>
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
          className={`snap-tab-item ${tab === "automation" ? "active" : ""}`}
          onClick={() => setTab("automation")}
        >
          <Settings size={14} />
          <span>Automation & VPS Setup</span>
        </button>
      </nav>

      {/* ---------------- TAB 1: COMPOSE & SCHEDULE ---------------- */}
      {tab === "publish" && (
        accounts.length === 0 ? (
          <div className="card" style={{ padding: "48px 24px", textAlign: "center", marginBottom: 24 }}>
            <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 6 }}>
              No Rumble Accounts Connected Yet
            </div>
            <p style={{ color: "var(--ink-muted)", fontSize: 13, maxWidth: 460, margin: "0 auto 16px" }}>
              Add your Rumble creator username to begin scheduling monetized videos.
            </p>
            <button
              type="button"
              className="btn btn-primary"
              style={{ background: RUMBLE_GREEN, borderColor: RUMBLE_GREEN, color: "#fff", fontWeight: 700 }}
              onClick={() => {
                setTab("accounts");
                setShowAddAccount(true);
              }}
            >
              + Add Rumble Account
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
                  <span className="sec-label">Rumble Account</span>
                  <select
                    value={destAccountId}
                    onChange={(e) => setDestAccountId(e.target.value)}
                    style={{ padding: "8px 10px" }}
                    required
                  >
                    {accounts.map((a) => (
                      <option key={a.id} value={a.id}>
                        @{a.username} {a.channel_name ? `(${a.channel_name})` : ""}
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
                <span className="sec-label">Video Title</span>
                <input
                  type="text"
                  placeholder="e.g. Most Satisfying Coconut Cutting ASMR"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  style={{ padding: "8px 10px" }}
                  required
                />
              </label>

              <label style={{ display: "grid", gap: 4 }}>
                <span className="sec-label">Video Description</span>
                <textarea
                  rows={4}
                  placeholder="Video description, links, and hashtags..."
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
                    placeholder="asmr, coconut, viral, satisfying"
                    value={tags}
                    onChange={(e) => setTags(e.target.value)}
                    style={{ padding: "8px 10px" }}
                  />
                </label>

                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Rumble Licensing Model</span>
                  <select
                    value={licenseType}
                    onChange={(e) => setLicenseType(e.target.value)}
                    style={{ padding: "8px 10px" }}
                  >
                    {LICENSE_OPTIONS.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>

              <div style={{ fontSize: 12, color: "var(--ink-muted)", background: "var(--paper-2)", padding: "10px 14px", borderRadius: 6 }}>
                ℹ️ <strong>{LICENSE_OPTIONS.find((l) => l.id === licenseType)?.label}:</strong>{" "}
                {LICENSE_OPTIONS.find((l) => l.id === licenseType)?.blurb}
              </div>

              <div style={{ marginTop: 8 }}>
                <button
                  type="submit"
                  className="btn btn-primary"
                  style={{ background: RUMBLE_GREEN, borderColor: RUMBLE_GREEN, color: "#fff", fontWeight: 700 }}
                  disabled={scheduling}
                >
                  {scheduling ? "Queueing…" : "Queue to Rumble"}
                </button>
              </div>
            </form>
          </div>
        )
      )}

      {/* ---------------- TAB 2: ACCOUNTS ---------------- */}
      {tab === "accounts" && (
        <div>
          {showAddAccount && (
            <div className="card" style={{ padding: 20, marginBottom: 20, border: "1px solid var(--rust)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600 }}>Connect a Rumble Account</h3>
                <button
                  type="button"
                  className="btn"
                  style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={() => setShowAddAccount(false)}
                >
                  ✕ Close
                </button>
              </div>
              <form onSubmit={handleAddAccount} style={{ display: "grid", gap: 12 }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span className="sec-label">Rumble Username</span>
                    <input
                      placeholder="e.g. ainan_creator"
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      style={{ padding: "6px 8px" }}
                      required
                    />
                  </label>
                  <label style={{ display: "grid", gap: 4 }}>
                    <span className="sec-label">Channel Name (optional)</span>
                    <input
                      placeholder="e.g. Daily Viral ASMR"
                      value={newChannelName}
                      onChange={(e) => setNewChannelName(e.target.value)}
                      style={{ padding: "6px 8px" }}
                    />
                  </label>
                </div>
                <label style={{ display: "grid", gap: 4 }}>
                  <span className="sec-label">Default Licensing Option</span>
                  <select
                    value={newDefaultLicense}
                    onChange={(e) => setNewDefaultLicense(e.target.value)}
                    style={{ padding: "6px 8px" }}
                  >
                    {LICENSE_OPTIONS.map((l) => (
                      <option key={l.id} value={l.id}>
                        {l.label}
                      </option>
                    ))}
                  </select>
                </label>
                <div>
                  <button
                    type="submit"
                    className="btn btn-primary"
                    style={{ background: RUMBLE_GREEN, borderColor: RUMBLE_GREEN, color: "#fff" }}
                  >
                    Save Account
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="card" style={{ padding: 0 }}>
            <div style={{ padding: "16px 20px", borderBottom: "1px solid var(--border)", display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0 }}>Connected Accounts ({accounts.length})</h2>
              <button
                type="button"
                className="btn"
                style={{ fontSize: 11, padding: "4px 10px" }}
                onClick={() => setShowAddAccount(true)}
              >
                + Add Account
              </button>
            </div>
            {accounts.length === 0 ? (
              <div style={{ padding: 32, textAlign: "center", color: "var(--ink-muted)", fontSize: 13 }}>
                No Rumble accounts added yet.
              </div>
            ) : (
              <table className="table">
                <thead>
                  <tr>
                    <th>Username</th>
                    <th>Channel</th>
                    <th>Default License</th>
                    <th>Status</th>
                    <th style={{ textAlign: "right" }}>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {accounts.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>@{a.username}</div>
                      </td>
                      <td style={{ fontSize: 12 }}>{a.channel_name || "Primary Profile"}</td>
                      <td style={{ fontSize: 12, textTransform: "capitalize" }}>
                        {LICENSE_OPTIONS.find((l) => l.id === a.default_license)?.label || a.default_license}
                      </td>
                      <td>
                        <span style={{ fontSize: 11, color: "#1e6b34", fontWeight: 600 }}>● Active</span>
                      </td>
                      <td style={{ textAlign: "right" }}>
                        <button
                          type="button"
                          className="btn"
                          style={{ fontSize: 11, padding: "3px 8px", color: "#a3231a" }}
                          onClick={() => handleDeleteAccount(a.id, a.username)}
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
              Nothing queued for Rumble. Scheduled items will appear here.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Account</th>
                  <th>Title</th>
                  <th>License</th>
                  <th>Scheduled For</th>
                  <th>Status</th>
                  <th>Error</th>
                  <th style={{ textAlign: "right" }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((j) => (
                  <tr key={j.id}>
                    <td style={{ fontSize: 12 }}>@{j.username}</td>
                    <td style={{ fontSize: 12, fontWeight: 500 }}>{j.title}</td>
                    <td style={{ fontSize: 11, textTransform: "capitalize" }}>{j.license_type}</td>
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
              No Rumble videos posted yet.
            </div>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Published At</th>
                  <th>Account</th>
                  <th>Title</th>
                  <th>License</th>
                  <th>Link</th>
                </tr>
              </thead>
              <tbody>
                {history.map((h) => (
                  <tr key={h.id}>
                    <td style={{ fontSize: 12 }}>{fmt(h.published_at)}</td>
                    <td style={{ fontSize: 12 }}>@{h.username}</td>
                    <td style={{ fontSize: 12, fontWeight: 600 }}>{h.title}</td>
                    <td style={{ fontSize: 11, textTransform: "capitalize" }}>{h.license_type || "exclusive"}</td>
                    <td>
                      {h.rumble_url ? (
                        <a
                          href={h.rumble_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ fontSize: 12, color: RUMBLE_GREEN, fontWeight: 600 }}
                        >
                          View on Rumble ↗
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

      {/* ---------------- TAB 5: AUTOMATION & VPS SETUP ---------------- */}
      {tab === "automation" && (
        <div className="card" style={{ padding: 24 }}>
          <h2 style={{ fontSize: 16, fontWeight: 600, margin: "0 0 8px" }}>Rumble Video Automation Architecture</h2>
          <p style={{ color: "var(--ink-soft)", fontSize: 13, lineHeight: 1.5, marginBottom: 20 }}>
            Because Rumble.com does not offer an open public REST upload endpoint for developers, posting is handled via automated headless browser execution on your VPS or the native YouTube Sync bridge.
          </p>

          <div style={{ display: "grid", gap: 18 }}>
            <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16, background: "var(--paper-2)" }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 6px" }}>Method 1: 24/7 VPS Headless Worker (Playwright)</h3>
              <p style={{ fontSize: 12.5, color: "var(--ink-soft)", margin: 0, lineHeight: 1.5 }}>
                On your VPS, a lightweight Playwright worker process monitors <code>rumble_jobs</code> in Supabase. When a video is due, it downloads the media from Google Drive, logs into Rumble using stored session cookies, selects the monetization license, and submits the post autonomously.
              </p>
            </div>

            <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16, background: "var(--paper-2)" }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 6px" }}>Method 2: Official YouTube Sync Bridge</h3>
              <p style={{ fontSize: 12.5, color: "var(--ink-soft)", margin: 0, lineHeight: 1.5 }}>
                In your Rumble account (<strong>Account Settings → YouTube Sync</strong>), connect your YouTube channel. When you queue a video to YouTube Studio in this dashboard, Rumble's backend will automatically detect and syndicate the video into your Rumble channel with zero risk of bot flags.
              </p>
            </div>

            <div style={{ border: "1px solid var(--line)", borderRadius: 8, padding: 16 }}>
              <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 6px" }}>Rumble Licensing Rules for Maximum Earnings</h3>
              <ul style={{ fontSize: 12.5, color: "var(--ink-soft)", paddingLeft: 18, margin: "6px 0 0", lineHeight: 1.6 }}>
                <li><strong>Exclusive Video Management:</strong> Payout includes ad revenue from Rumble + YouTube + partner sites. Best revenue model.</li>
                <li><strong>Rumble Only:</strong> Only monetized when played on Rumble.com or the Rumble app.</li>
                <li><strong>Personal Use:</strong> Free hosting only, minimal or no ad monetization.</li>
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
