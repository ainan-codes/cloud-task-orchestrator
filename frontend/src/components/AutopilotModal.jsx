import React, { useState } from "react";
import { api } from "../api.js";
import { Sparkles, Calendar, Layers, CheckCircle2, Clock, Image, Video, FileText } from "lucide-react";

const NICHES = [
  "Dark Psychology & Influence",
  "Stoic Motivation & Wealth",
  "AI & Future Tech",
  "High-Performance Fitness",
  "Crypto & Financial Freedom",
  "Luxury Lifestyle & Travel",
];

export default function AutopilotModal({ isOpen, onClose, page, onScheduled }) {
  const [niche, setNiche] = useState(NICHES[0]);
  const [customNiche, setCustomNiche] = useState("");
  const [count, setCount] = useState(4);
  const [includePhotos, setIncludePhotos] = useState(true);
  const [includeText, setIncludeText] = useState(true);
  const [includeReels, setIncludeReels] = useState(true);
  const [minGap, setMinGap] = useState(45);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");

  if (!isOpen) return null;

  const targetNiche = customNiche.trim() || niche;

  async function handleLaunch(e) {
    e.preventDefault();
    if (!page?.id) return;
    setGenerating(true);
    setError("");
    setResult(null);

    const formats = [];
    if (includePhotos) formats.push("photo");
    if (includeText) formats.push("text");
    if (includeReels) formats.push("reel");

    if (formats.length === 0) {
      setError("Please select at least one format (Photos, Text, or Reels).");
      setGenerating(false);
      return;
    }

    try {
      const res = await api.autopilotBatch(page.id, {
        niche: targetNiche,
        count: Number(count),
        formats,
        min_gap_minutes: Number(minGap),
      });

      setResult(res);
      if (onScheduled) onScheduled();
    } catch (err) {
      setError(err.message || "Failed to generate autopilot queue.");
    } finally {
      setGenerating(false);
    }
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(580px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", paddingBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, background: "rgba(16, 185, 129, 0.12)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Sparkles size={18} style={{ color: "#34d399" }} />
            </div>
            <div>
              <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
                Autopilot Niche Batch Generator
              </h2>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Zero-collision autonomous multi-format scheduling for <b>{page?.name || "Page"}</b>
              </div>
            </div>
          </div>
          <button type="button" className="btn ghost small" onClick={onClose} style={{ fontSize: 16, padding: "4px 8px" }}>
            ✕
          </button>
        </div>

        {error && (
          <div className="banner err" style={{ marginTop: 14, marginBottom: 14, fontSize: 13 }}>
            {error}
          </div>
        )}

        {result ? (
          <div style={{ padding: "20px 0", textAlign: "center" }}>
            <div style={{ width: 44, height: 44, borderRadius: "50%", background: "rgba(16, 185, 129, 0.15)", color: "#10b981", display: "inline-flex", alignItems: "center", justifyContent: "center", marginBottom: 12 }}>
              <CheckCircle2 size={24} />
            </div>
            <h3 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 6px", color: "var(--text-primary)" }}>
              Autopilot Batch Scheduled!
            </h3>
            <p style={{ fontSize: 13, color: "var(--text-muted)", maxWidth: 440, margin: "0 auto 16px" }}>
              Generated and queued <b>{result.count_scheduled} posts</b> for niche <b>"{result.niche}"</b> with guaranteed 45m+ collision buffers.
            </p>
            <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
              <button type="button" className="btn btn-secondary" onClick={() => setResult(null)}>
                Generate Another Batch
              </button>
              <button type="button" className="btn btn-primary" onClick={onClose}>
                View Scheduled Queue
              </button>
            </div>
          </div>
        ) : (
          <form onSubmit={handleLaunch} style={{ marginTop: 16 }}>
            {/* Niche Preset / Custom Selector */}
            <div className="field">
              <label>Select Target Niche</label>
              <select value={niche} onChange={(e) => { setNiche(e.target.value); setCustomNiche(""); }}>
                {NICHES.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
                <option value="custom">✏️ Custom Niche / Topic…</option>
              </select>
            </div>

            {niche === "custom" && (
              <div className="field">
                <label>Custom Niche or Campaign Idea</label>
                <input
                  type="text"
                  placeholder="e.g. AI Prompt Engineering & Automations"
                  value={customNiche}
                  onChange={(e) => setCustomNiche(e.target.value)}
                  required
                />
              </div>
            )}

            {/* Post Count Selector */}
            <div className="field">
              <label style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Posts to Generate & Schedule</span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{count} posts</span>
              </label>
              <div style={{ display: "flex", gap: 8 }}>
                {[2, 3, 4, 6, 8, 12].map((num) => (
                  <button
                    key={num}
                    type="button"
                    className={`btn small ${count === num ? "btn-primary" : "ghost"}`}
                    style={count === num ? { flex: 1, fontWeight: 700 } : { flex: 1 }}
                    onClick={() => setCount(num)}
                  >
                    {num}
                  </button>
                ))}
              </div>
            </div>

            {/* Format Mix Checkboxes */}
            <div className="field">
              <label>Content Format Mix</label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8, marginTop: 4 }}>
                <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 6, cursor: "pointer", fontSize: 12.5 }}>
                  <input type="checkbox" checked={includePhotos} onChange={(e) => setIncludePhotos(e.target.checked)} />
                  <Image size={14} style={{ color: "#60a5fa" }} />
                  <span>AI Photos</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 6, cursor: "pointer", fontSize: 12.5 }}>
                  <input type="checkbox" checked={includeText} onChange={(e) => setIncludeText(e.target.checked)} />
                  <FileText size={14} style={{ color: "#c084fc" }} />
                  <span>Text Quotes</span>
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px", background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 6, cursor: "pointer", fontSize: 12.5 }}>
                  <input type="checkbox" checked={includeReels} onChange={(e) => setIncludeReels(e.target.checked)} />
                  <Video size={14} style={{ color: "#34d399" }} />
                  <span>Drive Reels</span>
                </label>
              </div>
              <div className="hint">If Drive has no video available for a slot, it automatically generates a spontaneous photo card.</div>
            </div>

            {/* Minimum Gap Buffer */}
            <div className="field">
              <label style={{ display: "flex", justifyContent: "space-between" }}>
                <span>Collision Safety Buffer</span>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{minGap} minutes minimum</span>
              </label>
              <input
                type="range"
                min="25"
                max="90"
                step="5"
                value={minGap}
                onChange={(e) => setMinGap(e.target.value)}
              />
              <div className="hint">Prevents Meta Graph API concurrency locks and feed impression cannibalization.</div>
            </div>

            <div className="modal-actions" style={{ marginTop: 24, display: "flex", justifyContent: "flex-end", gap: 10 }}>
              <button type="button" className="btn ghost" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="btn btn-primary" disabled={generating} style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                <Sparkles size={15} />
                <span>{generating ? "Generating & Spacing…" : "Launch Autopilot Queue"}</span>
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
