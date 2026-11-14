import React, { useState, useEffect } from "react";
import { api } from "../api.js";
import PageCombo from "./PageCombo.jsx";
import {
  BarChart3,
  TrendingUp,
  Eye,
  Heart,
  Film,
  RefreshCw,
  MessageCircle,
  Share2,
  Users,
} from "lucide-react";

export default function Analytics({ pages, activePage, setActivePage }) {
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(false);

  async function load() {
    if (!activePage) return;
    setLoading(true);
    try {
      const data = await api.analytics(activePage);
      setRows(data || []);
    } catch (e) {
      setMsg(e.message || "Failed to load analytics.");
    } finally {
      setLoading(false);
    }
  }

  async function refresh() {
    setRefreshing(true);
    setMsg("");
    try {
      await api.refreshAnalytics();
      await load();
      setMsg("✓ Analytics refreshed from platform APIs.");
    } catch (e) {
      setMsg(`Refresh failed: ${e.message}`);
    } finally {
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load();
  }, [activePage]);

  if (!pages || pages.length === 0) {
    return (
      <div className="view">
        <div className="page-header">
          <div>
            <div className="page-meta">AUDIT & ENGAGEMENT · PERFORMANCE METRICS</div>
            <h1 className="page-title">Analytics & Insights</h1>
          </div>
        </div>
        <div className="empty-state-card">
          <div className="empty-icon-wrap">
            <BarChart3 size={22} />
          </div>
          <h3 className="empty-title">No Pages Connected</h3>
          <p className="empty-desc">
            Connect a Facebook Page in the Pages tab and publish reels to inspect performance metrics.
          </p>
        </div>
      </div>
    );
  }

  const top = rows[0];
  const totalViews = rows.reduce((s, r) => s + (r.views || 0), 0);
  const totalLikes = rows.reduce((s, r) => s + (r.likes || 0), 0);
  const totalComments = rows.reduce((s, r) => s + (r.comments || 0), 0);
  const totalShares = rows.reduce((s, r) => s + (r.shares || 0), 0);

  return (
    <div className="view">
      {/* Page Header */}
      <div className="page-header">
        <div>
          <div className="page-meta">AUDIT & ENGAGEMENT · PERFORMANCE METRICS</div>
          <h1 className="page-title">Analytics & Insights</h1>
          <p className="page-description">
            Track video reach, impressions, views, and audience engagement across published reels.
            Metrics sync directly from official platform analytics APIs.
          </p>
        </div>
        <div className="page-actions" style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <div style={{ width: 280 }}>
            <PageCombo pages={pages} value={activePage} onChange={setActivePage} />
          </div>
          <button
            className="btn btn-secondary"
            disabled={refreshing || !activePage}
            onClick={refresh}
            style={{ gap: 6 }}
          >
            <RefreshCw size={14} className={refreshing ? "spin" : ""} />
            <span>{refreshing ? "Refreshing…" : "Refresh"}</span>
          </button>
        </div>
      </div>

      {msg && (
        <div className="banner" style={{ marginBottom: 20 }}>
          {msg}
        </div>
      )}

      {loading && rows.length === 0 ? (
        <div className="empty-state-card">
          <div className="empty-icon-wrap">
            <RefreshCw size={20} className="spin" />
          </div>
          <h3 className="empty-title">Loading Performance Insights…</h3>
          <p className="empty-desc">Pulling latest reach, impressions, and engagement data.</p>
        </div>
      ) : rows.length === 0 ? (
        <div className="empty-state-card">
          <div className="empty-icon-wrap">
            <BarChart3 size={24} />
          </div>
          <h3 className="empty-title">No Performance Metrics Yet</h3>
          <p className="empty-desc">
            No published posts with tracked analytics were found for this page. Once posts are published and analytics sync has run, top performers and reach breakdowns will appear here.
          </p>
          <button
            className="btn btn-secondary small"
            disabled={refreshing || !activePage}
            onClick={refresh}
            style={{ gap: 6 }}
          >
            <RefreshCw size={13} className={refreshing ? "spin" : ""} />
            <span>{refreshing ? "Checking API…" : "Sync Analytics Now"}</span>
          </button>
        </div>
      ) : (
        <>
          {/* Top KPI Cards Grid */}
          <div className="kpi-grid">
            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Top Reel Views</span>
                <TrendingUp size={16} className="kpi-icon" />
              </div>
              <div className="kpi-value">{(top?.views ?? 0).toLocaleString()}</div>
              <div className="kpi-footer">Best performing asset</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Total Views</span>
                <Eye size={16} className="kpi-icon" />
              </div>
              <div className="kpi-value">{totalViews.toLocaleString()}</div>
              <div className="kpi-footer">Across {rows.length} tracked items</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Total Reactions</span>
                <Heart size={16} className="kpi-icon" />
              </div>
              <div className="kpi-value">{totalLikes.toLocaleString()}</div>
              <div className="kpi-footer">Likes and reactions</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-header">
                <span className="kpi-label">Tracked Items</span>
                <Film size={16} className="kpi-icon" />
              </div>
              <div className="kpi-value">{rows.length}</div>
              <div className="kpi-footer">Reels & posts with insights</div>
            </div>
          </div>

          {/* Performance Data Table */}
          <div className="section-header" style={{ marginTop: 24, marginBottom: 12 }}>
            <h2 className="section-title">Published Asset Breakdown (Best First)</h2>
            <span className="section-count">{rows.length} items</span>
          </div>

          <div className="table-card">
            <table>
              <thead>
                <tr>
                  <th>Asset / File Name</th>
                  <th>Posted (IST)</th>
                  <th style={{ textAlign: "right" }}>Views</th>
                  <th style={{ textAlign: "right" }}>Likes</th>
                  <th style={{ textAlign: "right" }}>Comments</th>
                  <th style={{ textAlign: "right" }}>Shares</th>
                  <th style={{ textAlign: "right" }}>Reach</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.fb_creation_id || r.id || i}>
                    <td style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      {r.file_name || r.source_video_id || r.fb_creation_id}
                    </td>
                    <td className="mono" style={{ fontSize: 12 }}>
                      {r.posted_at_ist || "—"}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {(r.views ?? 0).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {(r.likes ?? 0).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {(r.comments ?? 0).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {(r.shares ?? 0).toLocaleString()}
                    </td>
                    <td style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
                      {(r.reach ?? 0).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
