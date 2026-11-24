import React, { useState, useEffect } from "react";
import { api } from "../api.js";
import { Sparkles, ShieldCheck, CheckCircle2, AlertCircle, RefreshCw, Key, Cpu, Eye, EyeOff } from "lucide-react";

export default function AiConfigModal({ isOpen, onClose }) {
  const [config, setConfig] = useState(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");

  const [geminiKey, setGeminiKey] = useState("");
  const [geminiKeys, setGeminiKeys] = useState("");
  const [groqKey, setGroqKey] = useState("");
  const [selectedModel, setSelectedModel] = useState("gemini-2.0-flash");
  const [showKeys, setShowKeys] = useState(false);

  useEffect(() => {
    if (isOpen) {
      loadConfig();
    }
  }, [isOpen]);

  async function loadConfig() {
    setLoading(true);
    setStatusMsg("");
    try {
      const data = await api.getAiConfig();
      setConfig(data);
      setSelectedModel(data.preferred_model || "gemini-2.0-flash");
    } catch (err) {
      setStatusMsg("Failed to load AI configuration: " + err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setStatusMsg("");
    try {
      const payload = {
        ai_model: selectedModel,
      };
      if (geminiKey.trim()) payload.gemini_api_key = geminiKey.trim();
      if (geminiKeys.trim()) payload.gemini_api_keys = geminiKeys.trim();
      if (groqKey.trim()) payload.groq_api_key = groqKey.trim();

      await api.saveAiConfig(payload);
      setStatusMsg("AI configuration saved successfully into secure vault!");
      setGeminiKey("");
      setGeminiKeys("");
      setGroqKey("");
      await loadConfig();
    } catch (err) {
      setStatusMsg("Error saving config: " + err.message);
    } finally {
      setSaving(false);
    }
  }

  if (!isOpen) return null;

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" style={{ width: "min(560px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: "1px solid var(--border-subtle)", paddingBottom: 14 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, background: "rgba(99, 102, 241, 0.12)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Cpu size={18} style={{ color: "var(--accent-light)" }} />
            </div>
            <div>
              <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0, color: "var(--text-primary)" }}>
                AI Engine & Multi-Provider Keys
              </h2>
              <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                Google Gemini 2.0, Multi-Account Rotation, and Groq Fallback
              </div>
            </div>
          </div>
          <button type="button" className="btn ghost small" onClick={onClose} style={{ fontSize: 16, padding: "4px 8px" }}>
            ✕
          </button>
        </div>

        {statusMsg && (
          <div className="banner" style={{ marginTop: 14, marginBottom: 14, fontSize: 13, background: statusMsg.includes("Error") ? "rgba(239, 68, 68, 0.1)" : "rgba(16, 185, 129, 0.1)", border: `1px solid ${statusMsg.includes("Error") ? "rgba(239, 68, 68, 0.3)" : "rgba(16, 185, 129, 0.3)"}` }}>
            {statusMsg}
          </div>
        )}

        {/* Live Status Indicators */}
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 16, marginBottom: 18 }}>
          <div className="card" style={{ padding: "10px 14px", background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, marginBottom: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase" }}>Gemini Engine</div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
              {config?.gemini_active ? (
                <>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#10b981" }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#10b981" }}>
                    Active ({config.gemini_keys_count} key{config.gemini_keys_count === 1 ? "" : "s"})
                  </span>
                </>
              ) : (
                <>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#f59e0b" }} />
                  <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>Using Dynamic Heuristic</span>
                </>
              )}
            </div>
          </div>

          <div className="card" style={{ padding: "10px 14px", background: "var(--bg-input)", border: "1px solid var(--border-subtle)", borderRadius: 8, marginBottom: 0 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase" }}>Groq Fallback</div>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
              {config?.groq_active ? (
                <>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#10b981" }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#10b981" }}>Active (Llama 3.3 70B)</span>
                </>
              ) : (
                <>
                  <span style={{ width: 8, height: 8, borderRadius: "50%", background: "#71717a" }} />
                  <span style={{ fontSize: 12.5, color: "var(--text-muted)" }}>Not Configured</span>
                </>
              )}
            </div>
          </div>
        </div>

        <form onSubmit={handleSave}>
          <div className="field">
            <label style={{ display: "flex", justifyContent: "space-between" }}>
              <span>Preferred LLM Model</span>
              <span style={{ fontSize: 11, color: "var(--accent-light)", fontWeight: 400 }}>Auto-Waterfall enabled</span>
            </label>
            <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
              <option value="gemini-2.0-flash">Gemini 2.0 Flash (Recommended: Ultra-fast & High Reasoning)</option>
              <option value="gemini-1.5-pro">Gemini 1.5 Pro (Deep Creative Writing & Complexity)</option>
              <option value="gemini-1.5-flash">Gemini 1.5 Flash (Legacy Model)</option>
              <option value="llama-3.3-70b-versatile">Groq: Llama 3.3 70B Versatile (Free & Instant)</option>
            </select>
          </div>

          <div className="field">
            <label style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span>Primary Google Gemini API Key</span>
              <button type="button" onClick={() => setShowKeys(!showKeys)} style={{ background: "none", color: "var(--text-muted)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11 }}>
                {showKeys ? <EyeOff size={12} /> : <Eye size={12} />}
                <span>{showKeys ? "Hide" : "Show"}</span>
              </button>
            </label>
            <input
              type={showKeys ? "text" : "password"}
              placeholder="Paste primary Gemini API key (AIzaSy...)"
              value={geminiKey}
              onChange={(e) => setGeminiKey(e.target.value)}
            />
            <div className="hint">Get your free key from Google AI Studio (aistudio.google.com).</div>
          </div>

          <div className="field">
            <label>Additional Gemini Keys (Multi-Account Rotation)</label>
            <textarea
              rows={2}
              placeholder="Paste comma-separated keys from other Google accounts: AIzaKey2, AIzaKey3..."
              value={geminiKeys}
              onChange={(e) => setGeminiKeys(e.target.value)}
              style={{ resize: "vertical", WebkitTextSecurity: showKeys ? "none" : "disc" }}
            />
            <div className="hint">The engine automatically rotates to the next account key if one hits rate limits or quota caps.</div>
          </div>

          <div className="field">
            <label>Groq API Key (High-Speed Free Fallback)</label>
            <input
              type={showKeys ? "text" : "password"}
              placeholder="gsk_..."
              value={groqKey}
              onChange={(e) => setGroqKey(e.target.value)}
            />
            <div className="hint">Free Llama-3.3-70b inference from console.groq.com. Kicks in if Gemini is exhausted.</div>
          </div>

          <div className="modal-actions" style={{ marginTop: 24, display: "flex", justifyContent: "flex-end", gap: 10 }}>
            <button type="button" className="btn ghost" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? "Encrypting & Storing…" : "Save AI Configuration"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
