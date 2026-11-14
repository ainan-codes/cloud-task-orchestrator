import React, { useState, useEffect } from "react";
import CountUp from "./CountUp.jsx";
import { api } from "../api.js";
import {
  Layers,
  Film,
  Calendar,
  CheckCircle2,
  AlertTriangle,
  ArrowRight,
  Plus,
  Clock,
  RefreshCw,
  BookOpen,
} from "lucide-react";

const CHIP_COLORS = ["#2563eb", "#7c3aed", "#d97706", "#059669", "#dc2626", "#db2777"];

/** Accurate health heuristic checking token validity and failures first. */
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

export default function Overview({ pages, loading, onGo, onReload, onAddPage, onOpenGuide }) {
  const [todayPosts, setTodayPosts] = useState([]);
  const [activity, setActivity] = useState([]);
  const [sysHealth, setSysHealth] = useState(null);

  useEffect(() => {
    api.todayScheduled().then(setTodayPosts).catch(() => setTodayPosts([]));
    api.recentActivity().then(setActivity).catch(() => setActivity([]));
    api.systemHealth().then(setSysHealth).catch(() => null);
  }, [pages]);

  const IST = 5.5;
  function fmtIST(iso) {
    if (!iso) return "";
    const d = new Date(new Date(iso).getTime() + IST * 3600_000);
    const h = d.getUTCHours() % 12 || 12;
    const m = String(d.getUTCMinutes()).padStart(2, "0");
    const ap = d.getUTCHours() >= 12 ? "PM" : "AM";
    return `${h}:${m} ${ap} IST`;
  }

  function timeAgo(iso) {
    if (!iso) return "";
    const s = Math.floor((Date.now() - new Date(iso)) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return `${Math.floor(s / 60)}m ago`;
    if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
    return `${Math.floor(s / 86400)}d ago`;
  }

  if (loading && pages.length === 0) {
    return (
      <div className="view">
        <header className="page-header">
          <div>
            <div className="page-meta">Platform Overview · Meta Network</div>
            <h1 className="page-title">Dashboard Overview</h1>
            <p className="page-description">
              Synchronizing with Ledger backend and database metrics…
            </p>
          </div>
        </header>
        <div className="kpi-grid">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="kpi-card" style={{ minHeight: 108, opacity: 0.5 }}>
              <div style={{ width: 64, height: 12, background: "var(--border-subtle)", borderRadius: 4, marginBottom: 14 }} />
              <div style={{ width: 48, height: 32, background: "var(--border-subtle)", borderRadius: 6, marginBottom: 10 }} />
              <div style={{ width: 120, height: 11, background: "var(--border-subtle)", borderRadius: 4 }} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  const totalPages = pages.length;
  const totalContent = pages.reduce((s, p) => s + (p.stats?.total_content || 0), 0);
  const totalPosted = pages.reduce((s, p) => s + (p.stats?.total_posted || 0), 0);
  const totalScheduled = pages.reduce((s, p) => s + (p.stats?.pending_scheduled || 0), 0);

  const invalidPages = pages.filter((p) => p.token_status === "invalid" || p.stats?.token_status === "invalid").length;
  const hasOutage = invalidPages > 0 || (sysHealth && sysHealth.invalid_tokens > 0);

  const healthy = pages.filter((p) => healthDot(p) === "green").length;
  const warning = pages.filter((p) => healthDot(p) === "amber").length;
  const critical = pages.filter((p) => healthDot(p) === "red").length;
  const healthSummary = [
    healthy && `${healthy} healthy`,
    warning && `${warning} warning`,
    critical && `${critical} critical`,
  ].filter(Boolean).join(" · ") || "No pages configured";

  const pagesWithQueue = pages.filter((p) => (p.stats?.pending_scheduled || 0) > 0).length;

  return (
    <div className="view">
      {/* ── Top Header ── */}
      <header className="page-header">
        <div>
          <div className="page-meta">Platform Overview · Meta Network</div>
          <h1 className="page-title">Dashboard Overview</h1>
          <p className="page-description">
            Unified management for scheduled reels, Google Drive ingestion, token health monitoring,
            and automated deduplicated dispatch across all connected pages.
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <button className="btn btn-secondary" onClick={() => (onOpenGuide ? onOpenGuide() : onGo("pages"))} title="How to get permanent Meta tokens">
            <BookOpen size={15} />
            <span>Permanent Token Guide</span>
          </button>
          <button className="btn btn-primary" onClick={() => (onAddPage ? onAddPage() : onGo("pages"))}>
            <Plus size={15} />
            <span>Add Facebook Page</span>
          </button>
        </div>
      </header>

      {/* ── Token Outage Alert ── */}
      {hasOutage && (
        <div className="alert-card alert-danger">
          <div className="alert-icon">
            <AlertTriangle size={18} />
          </div>
          <div className="alert-content">
            <div className="alert-title">Action Required: Facebook Token Outage Detected</div>
            <div className="alert-text">
              One or more Facebook Page Access Tokens have expired or lost permissions (Meta OAuth Error 190).
              The posting circuit breaker has automatically protected your queue from being abandoned.
            </div>
          </div>
          <button className="btn btn-danger btn-sm" onClick={() => onGo("pages")}>
            Update Tokens
          </button>
        </div>
      )}

      {/* ── Metric Cards ── */}
      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-label">Pages</span>
            <Layers size={16} className="kpi-icon" />
          </div>
          <div className="kpi-value"><CountUp value={totalPages} /></div>
          <div className="kpi-footer">{healthSummary}</div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-label">Content Items</span>
            <Film size={16} className="kpi-icon" />
          </div>
          <div className="kpi-value"><CountUp value={totalContent} /></div>
          <div className="kpi-footer">
            {totalContent} video{totalContent === 1 ? "" : "s"} across {totalPages} page{totalPages === 1 ? "" : "s"}
          </div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-label">Scheduled</span>
            <Calendar size={16} className="kpi-icon" />
          </div>
          <div className="kpi-value"><CountUp value={totalScheduled} /></div>
          <div className="kpi-footer">
            {pagesWithQueue} page{pagesWithQueue === 1 ? "" : "s"} with an active queue
          </div>
        </div>

        <div className="kpi-card">
          <div className="kpi-header">
            <span className="kpi-label">Total Posted</span>
            <CheckCircle2 size={16} className="kpi-icon" />
          </div>
          <div className="kpi-value"><CountUp value={totalPosted} /></div>
          <div className="kpi-footer">Deduplicated audit record</div>
        </div>
      </div>

      {/* ── Section: Connected Pages ── */}
      <div className="section-header">
        <h2 className="section-title">Connected Pages</h2>
        <span className="section-count">{pages.length} configured</span>
      </div>

      {pages.length === 0 ? (
        <div className="empty-state-card">
          <div className="empty-icon-wrap">
            <Layers size={24} />
          </div>
          <h3 className="empty-title">No Facebook Pages Connected</h3>
          <p className="empty-desc">
            Connect your first Facebook page, link its Google Drive folder, and configure posting frequency to begin automated publishing.
          </p>
          <button className="btn btn-primary" onClick={() => (onAddPage ? onAddPage() : onGo("pages"))}>
            <Plus size={15} />
            <span>Connect First Page</span>
          </button>
        </div>
      ) : (
        <div className="page-grid">
          {pages.map((p, i) => {
            const s = p.stats || {};
            return (
              <div key={p.id} className="channel-card">
                <div className="channel-header">
                  <div className="channel-info">
                    <span className="channel-avatar" style={{ background: CHIP_COLORS[i % CHIP_COLORS.length] }}>
                      {p.name.slice(0, 1).toUpperCase()}
                    </span>
                    <div>
                      <div className="channel-name">{p.name}</div>
                      <div className="channel-sub">
                        <span className={`status-dot ${healthDot(p)}`} />
                        <span>{s.pending_scheduled ?? 0} queued · {s.total_posted ?? 0} published</span>
                      </div>
                    </div>
                  </div>
                  <div className="channel-badge">
                    {s.remaining_unposted ?? 0} unposted
                  </div>
                </div>
                <div className="channel-footer">
                  <span>{s.total_content ?? 0} total library items</span>
                  <button className="btn btn-ghost btn-sm" onClick={() => onGo("schedule")}>
                    Queue →
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── Section: Today's Queue ── */}
      <div className="section-header" style={{ marginTop: 36 }}>
        <h2 className="section-title">Today's Publishing Queue</h2>
        <span className="section-count">{todayPosts.length} posts due today</span>
      </div>

      {todayPosts.length === 0 ? (
        <div className="empty-state-card" style={{ padding: "32px 20px" }}>
          <div className="empty-icon-wrap">
            <Calendar size={22} />
          </div>
          <h3 className="empty-title">Queue is Empty for Today</h3>
          <p className="empty-desc">
            No posts are scheduled for release today. Head to the scheduler to auto-schedule or queue posts.
          </p>
          <button className="btn btn-secondary btn-sm" onClick={() => onGo("schedule")}>
            <span>Open Schedule</span>
            <ArrowRight size={13} />
          </button>
        </div>
      ) : (
        <div className="table-card">
          <table>
            <thead>
              <tr>
                <th style={{ width: 140 }}>Time (IST)</th>
                <th>File / Source Video</th>
                <th>Destination Page</th>
                <th style={{ width: 90, textAlign: "right" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {todayPosts.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{fmtIST(r.scheduled_time_utc)}</td>
                  <td style={{ fontWeight: 500 }}>{r.file_name || "—"}</td>
                  <td>{r.page_name || "—"}</td>
                  <td style={{ textAlign: "right" }}>
                    <span className="badge badge-amber">Pending</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Section: Recent Activity ── */}
      {activity.length > 0 && (
        <>
          <div className="section-header" style={{ marginTop: 36 }}>
            <h2 className="section-title">Recent Activity</h2>
            <span className="section-count">Live ledger log</span>
          </div>
          <div className="activity-card">
            {activity.map((a) => (
              <div key={a.id} className="activity-row">
                <div className="activity-icon-wrap">
                  {a.action === "post_now" || a.action === "posted" ? (
                    <CheckCircle2 size={15} style={{ color: "var(--color-success)" }} />
                  ) : (
                    <Clock size={15} style={{ color: "var(--color-secondary)" }} />
                  )}
                </div>
                <div className="activity-body">
                  <div className="activity-detail">{a.detail || a.action}</div>
                  <div className="activity-meta">
                    <span>{a.page_name}</span>
                    <span>·</span>
                    <span>{timeAgo(a.created_at)}</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
