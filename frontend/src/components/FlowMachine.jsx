import React, { useState, useEffect, useRef } from "react";
import { api } from "../api.js";
import {
  Sparkles,
  Video,
  Image as ImageIcon,
  Clock,
  Layers,
  FolderPlus,
  Folder,
  Play,
  CheckCircle2,
  AlertCircle,
  ExternalLink,
  Plus,
  Trash2,
  RefreshCw,
  Sliders,
  Send,
  Eye,
  Key,
} from "lucide-react";

const NICHES = [
  "American History & Innovation",
  "Stoic Motivation & Discipline",
  "AI & Future Technology",
  "High-Performance Fitness",
  "Financial Insights & Wealth",
  "Luxury Architecture & Design",
  "Deep Space & Science",
  "Wisdom & Life Lessons",
];

const AI_MODELS = [
  { id: "gemini-2.5-flash", label: "Gemini 2.5 Flash (Fast & High-CTR)" },
  { id: "gemini-2.5-pro", label: "Gemini 2.5 Pro (Deep Storyboarding)" },
  { id: "gemini-3.0-flash", label: "Gemini 3.0 Flash (Frontier Speed)" },
  { id: "gemini-2.0-flash", label: "Gemini 2.0 Flash (Standard)" },
  { id: "gemini-2.0-pro-exp", label: "Gemini 2.0 Pro Experimental" },
  { id: "groq-llama-3.3", label: "Groq Llama 3.3 70B (Ultra Fast)" },
];

export default function FlowMachine({ pages, activePage }) {
  const [accounts, setAccounts] = useState([]);
  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [statusMsg, setStatusMsg] = useState("");
  const [isError, setIsError] = useState(false);

  // Studio Form State (Loaded from Settings Memory)
  const [niche, setNiche] = useState(NICHES[0]);
  const [customNiche, setCustomNiche] = useState("");
  const [mediaType, setMediaType] = useState("reel"); // 'reel' or 'photo'
  const [duration, setDuration] = useState(10); // 10, 30, 60
  const [count, setCount] = useState(3);
  const [aiModel, setAiModel] = useState("gemini-2.5-flash");
  const [folderOption, setFolderOption] = useState("auto"); // 'auto' | 'page' | 'id' | 'custom'
  const [customFolderName, setCustomFolderName] = useState("");
  const [customFolderId, setCustomFolderId] = useState("");
  const [targetPageId, setTargetPageId] = useState(activePage || (pages?.[0]?.id || ""));
  const [customPrompt, setCustomPrompt] = useState("");

  // Add Account Modal State
  const [showAddModal, setShowAddModal] = useState(false);
  const [newLabel, setNewLabel] = useState("");
  const [newEmail, setNewEmail] = useState("");

  // Image Preview Modal State
  const [previewDataUri, setPreviewDataUri] = useState(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [verifyingId, setVerifyingId] = useState(null);

  const pollRef = useRef(null);
  const authPollRef = useRef(null);

  useEffect(() => {
    loadAll();
    loadSettings();
    pollRef.current = setInterval(loadJobs, 4000);
    return () => {
      clearInterval(pollRef.current);
      if (authPollRef.current) clearInterval(authPollRef.current);
    };
  }, []);

  async function loadAll() {
    setLoading(true);
    await Promise.all([loadAccounts(), loadJobs()]);
    setLoading(false);
  }

  async function loadAccounts() {
    try {
      const res = await api.flowAccounts();
      setAccounts(res.accounts || []);
    } catch (e) {
      console.error("Failed to load accounts:", e);
    }
  }

  async function loadJobs() {
    try {
      const res = await api.flowJobs({ limit: 30 });
      setJobs(res.jobs || []);
    } catch (e) {
      console.error("Failed to load jobs:", e);
    }
  }

  async function loadSettings() {
    try {
      const res = await api.flowGetSettings();
      if (res && res.settings) {
        const s = res.settings;
        if (s.last_niche) setNiche(s.last_niche);
        if (s.last_duration_seconds) setDuration(Number(s.last_duration_seconds));
        if (s.last_media_type) setMediaType(s.last_media_type);
        if (s.last_ai_model) setAiModel(s.last_ai_model);
        if (s.last_batch_count) setCount(Number(s.last_batch_count));
      }
    } catch (e) {
      console.error("Could not load settings:", e);
    }
  }

  async function handleAddAccount(e) {
    e.preventDefault();
    if (!newLabel.trim()) return;
    try {
      const res = await api.flowAddAccount({
        label: newLabel.trim(),
        email: newEmail.trim() || null,
        credits_total: 1000,
      });
      setNewLabel("");
      setNewEmail("");
      setShowAddModal(false);
      await loadAccounts();
      if (res && res.account && res.account.id) {
        handleLaunchLogin(res.account.id);
      }
    } catch (e) {
      setStatusMsg(e.message || "Failed to add account");
      setIsError(true);
    }
  }

  async function handleVerifySession(accId) {
    setVerifyingId(accId);
    setStatusMsg("Probing Google Flow session in dedicated profile...");
    setIsError(false);
    try {
      const res = await api.flowVerifySession(accId);
      if (res.authenticated) {
        setStatusMsg("✓ Google Flow authenticated and connected! 1,000 Omni Flash credits available.");
        setIsError(false);
        if (authPollRef.current) clearInterval(authPollRef.current);
      } else {
        setStatusMsg("Google Flow session not yet signed in. Please complete sign-in in the open browser window.");
        setIsError(true);
      }
      await loadAccounts();
    } catch (e) {
      setStatusMsg("Verification error: " + (e.message || "Failed to probe session"));
      setIsError(true);
    } finally {
      setVerifyingId(null);
    }
  }

  async function handleLaunchLogin(accId) {
    try {
      setStatusMsg("Launching visible Google login window... Complete sign-in in the browser.");
      setIsError(false);
      const res = await api.flowLaunchLogin(accId);
      setStatusMsg(res.message || "Login window active. Once Google Flow loads, it will automatically connect.");
      await loadAccounts();

      // Auto-poll verification every 4 seconds for up to 3 minutes
      if (authPollRef.current) clearInterval(authPollRef.current);
      let attempts = 0;
      authPollRef.current = setInterval(async () => {
        attempts += 1;
        if (attempts > 45) {
          clearInterval(authPollRef.current);
          return;
        }
        try {
          const check = await api.flowVerifySession(accId);
          if (check.authenticated) {
            clearInterval(authPollRef.current);
            setStatusMsg("✓ Google Flow session verified and connected! You can close the Chrome window.");
            setIsError(false);
            loadAccounts();
          }
        } catch (_) {}
      }, 4000);
    } catch (e) {
      setStatusMsg(e.message || "Could not launch login window");
      setIsError(true);
    }
  }

  async function handleDeleteAccount(accId) {
    if (!window.confirm("Remove this Google Flow account profile?")) return;
    try {
      await api.flowDeleteAccount(accId);
      loadAccounts();
    } catch (e) {
      alert("Error: " + e.message);
    }
  }

  async function handleCloseBrowser(accId) {
    try {
      setStatusMsg("Closing Google Flow browser session...");
      const res = await api.flowCloseBrowser(accId);
      setStatusMsg(res.message || "Browser session closed.");
      await loadAccounts();
    } catch (e) {
      setStatusMsg("Failed to close browser: " + e.message);
      setIsError(true);
    }
  }

  async function handleRunPendingJobs() {
    try {
      setStatusMsg("Starting execution for queued jobs...");
      const res = await api.flowRunPendingJobs();
      setStatusMsg(res.message || "Jobs dispatched to background worker.");
      loadJobs();
    } catch (e) {
      setStatusMsg("Error running jobs: " + e.message);
      setIsError(true);
    }
  }

  async function handleRetryJob(jobId) {
    try {
      setStatusMsg(`Triggering job #${jobId}...`);
      await api.flowRetryJob(jobId);
      loadJobs();
    } catch (e) {
      setStatusMsg("Error triggering job: " + e.message);
      setIsError(true);
    }
  }

  async function handleLaunchBatch(e) {
    e.preventDefault();
    setSubmitting(true);
    setStatusMsg("");
    setIsError(false);

    const targetNiche = customNiche.trim() || niche;
    const selectedPage = pages?.find((p) => p.id === Number(targetPageId)) || pages?.find((p) => p.id === activePage) || pages?.[0];
    const resolvedFolderId = folderOption === "page" 
      ? selectedPage?.drive_folder_id 
      : (folderOption === "id" ? customFolderId.trim() : null);

    try {
      const payload = {
        niche: targetNiche,
        media_type: mediaType,
        duration_seconds: Number(duration),
        count: Number(count),
        target_page_id: selectedPage?.id || null,
        drive_folder_id: resolvedFolderId || null,
        auto_create_folder: folderOption === "auto" || folderOption === "custom",
        custom_folder_name: folderOption === "custom" ? customFolderName : null,
        custom_prompt: customPrompt.trim() || null,
        ai_model: aiModel,
      };

      const res = await api.flowBatchGenerate(payload);
      setStatusMsg(res.message || "Generation run queued successfully!");
      setIsError(false);
      loadJobs();
      loadAccounts();
    } catch (err) {
      setStatusMsg(err.message || "Failed to launch batch.");
      setIsError(true);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleQuickImagePreview() {
    setPreviewLoading(true);
    setPreviewDataUri(null);
    try {
      const targetNiche = customNiche.trim() || niche;
      const res = await api.flowPreviewImage({
        niche: targetNiche,
        headline: customPrompt || `The Reality of ${targetNiche}`,
        body: "Success is not given. It is demanded every single day through unyielding discipline.",
        include_text_overlay: true,
      });
      setPreviewDataUri(res.image_data_uri);
    } catch (e) {
      alert("Preview error: " + e.message);
    } finally {
      setPreviewLoading(false);
    }
  }

  return (
    <div className="tab-pane" style={{ padding: "20px 24px", maxWidth: 1320, margin: "0 auto" }}>
      {/* Header Banner */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 24 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{ width: 38, height: 38, borderRadius: 10, background: "linear-gradient(135deg, #10b981 0%, #065f46 100%)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <Sparkles size={20} color="#fff" />
            </div>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
                Google Flow Content Machine
                <span style={{ fontSize: 11, background: "rgba(16, 185, 129, 0.15)", color: "#34d399", padding: "3px 8px", borderRadius: 12, border: "1px solid rgba(16, 185, 129, 0.3)" }}>
                  Omni Flash 9:16 Studio
                </span>
              </h1>
              <div style={{ fontSize: 13, color: "var(--text-muted)", marginTop: 3 }}>
                Automate vertical short-form reels and 9:16 photo cards using your 1,000-credit Google accounts.
              </div>
            </div>
          </div>
        </div>

        <button
          className="btn btn-secondary"
          onClick={() => setShowAddModal(true)}
          style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13 }}
        >
          <Plus size={15} />
          <span>Add Google Account</span>
        </button>
      </div>

      {statusMsg && (
        <div style={{
          padding: "10px 14px",
          borderRadius: 8,
          marginBottom: 20,
          fontSize: 13,
          background: isError ? "rgba(239, 68, 68, 0.12)" : "rgba(16, 185, 129, 0.12)",
          color: isError ? "#f87171" : "#34d399",
          border: `1px solid ${isError ? "rgba(239, 68, 68, 0.25)" : "rgba(16, 185, 129, 0.25)"}`,
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}>
          {isError ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
          <span>{statusMsg}</span>
        </div>
      )}

      {/* Account Credit Matrix */}
      <div style={{ marginBottom: 28 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: 0.5, marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }}>
          <Key size={14} />
          <span>Connected Google Flow Accounts ({accounts.length})</span>
        </div>

        {accounts.length === 0 ? (
          <div style={{ padding: "20px 24px", borderRadius: 10, background: "var(--bg-card)", border: "1px dashed var(--border-subtle)", textAlign: "center" }}>
            <p style={{ margin: "0 0 12px", color: "var(--text-muted)", fontSize: 13.5 }}>
              No Google Flow accounts connected yet. Add your accounts to unlock 1,000 credits each.
            </p>
            <button className="btn btn-primary" onClick={() => setShowAddModal(true)} style={{ fontSize: 12.5 }}>
              <Plus size={14} /> Connect First Google Account
            </button>
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 14 }}>
            {accounts.map((acc) => {
              const remaining = acc.credits_remaining ?? 1000;
              const total = acc.credits_total ?? 1000;
              const pct = Math.max(0, Math.min(100, Math.round((remaining / total) * 100)));
              const isLow = remaining < 100;
              const isBrowserOpen = Boolean(acc.is_browser_open);
              const isConnected = acc.session_status === "CONNECTED" || Boolean(acc.is_connected);
              const isVerifying = verifyingId === acc.id;

              return (
                <div
                  key={acc.id}
                  style={{
                    background: "var(--bg-card)",
                    border: isBrowserOpen
                      ? "1px solid rgba(245, 158, 11, 0.5)"
                      : isConnected
                      ? "1px solid rgba(16, 185, 129, 0.35)"
                      : "1px solid rgba(239, 68, 68, 0.35)",
                    borderRadius: 10,
                    padding: "16px 18px",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                  }}
                >
                  <div>
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 8 }}>
                      <div>
                        <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
                          <span style={{ fontWeight: 600, fontSize: 14.5, color: "var(--text-primary)" }}>{acc.label}</span>
                          {isBrowserOpen ? (
                            <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 4, background: "rgba(245, 158, 11, 0.2)", color: "#fbbf24" }}>
                              WINDOW OPEN
                            </span>
                          ) : isConnected ? (
                            <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 4, background: "rgba(16, 185, 129, 0.2)", color: "#34d399", display: "inline-flex", alignItems: "center", gap: 3 }}>
                              <CheckCircle2 size={11} /> CONNECTED
                            </span>
                          ) : (
                            <span style={{ fontSize: 10, fontWeight: 700, padding: "2px 6px", borderRadius: 4, background: "rgba(239, 68, 68, 0.2)", color: "#f87171", display: "inline-flex", alignItems: "center", gap: 3 }}>
                              <AlertCircle size={11} /> LOGIN REQUIRED
                            </span>
                          )}
                        </div>
                        <div style={{ fontSize: 12, color: "var(--text-muted)" }}>{acc.email || "Google Account"}</div>
                      </div>
                      <button
                        onClick={() => handleDeleteAccount(acc.id)}
                        style={{ background: "transparent", border: "none", color: "var(--text-muted)", cursor: "pointer", padding: 4 }}
                        title="Delete Account"
                      >
                        <Trash2 size={14} />
                      </button>
                    </div>

                    <div style={{ marginTop: 12, marginBottom: 14 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
                        <span style={{ color: "var(--text-muted)" }}>Remaining Credits</span>
                        <span style={{ fontWeight: 600, color: isLow ? "#f87171" : "#34d399" }}>
                          {remaining} / {total}
                        </span>
                      </div>
                      <div style={{ width: "100%", height: 6, background: "var(--border-medium)", borderRadius: 3, overflow: "hidden" }}>
                        <div style={{ width: `${pct}%`, height: "100%", background: isLow ? "#ef4444" : "#10b981", transition: "width 0.4s" }} />
                      </div>
                    </div>
                  </div>

                  {isBrowserOpen ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={() => handleVerifySession(acc.id)}
                          disabled={isVerifying}
                          style={{ flex: 1, justifyContent: "center", fontSize: 12, fontWeight: 600 }}
                        >
                          <RefreshCw size={12} className={isVerifying ? "spin" : ""} />
                          <span>{isVerifying ? "Verifying..." : "Verify Session"}</span>
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleCloseBrowser(acc.id)}
                          style={{
                            flex: 1,
                            justifyContent: "center",
                            fontSize: 12,
                            fontWeight: 600,
                            background: "rgba(245, 158, 11, 0.15)",
                            color: "#fbbf24",
                            borderColor: "rgba(245, 158, 11, 0.4)",
                          }}
                        >
                          <span>Close Chrome</span>
                        </button>
                      </div>
                      <div style={{ textAlign: "center", fontSize: 11, color: "#fbbf24" }}>
                        ⚠️ Sign in Google Flow in window, then click Verify Session
                      </div>
                    </div>
                  ) : isConnected ? (
                    <div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleLaunchLogin(acc.id)}
                          style={{ flex: 1, justifyContent: "center", fontSize: 12, fontWeight: 600, gap: 5 }}
                        >
                          <ExternalLink size={12} />
                          <span>Open Window</span>
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => handleVerifySession(acc.id)}
                          disabled={isVerifying}
                          style={{ fontSize: 12, gap: 5 }}
                          title="Re-verify active session and balance"
                        >
                          <RefreshCw size={12} className={isVerifying ? "spin" : ""} />
                          <span>Check</span>
                        </button>
                      </div>
                      <div style={{ textAlign: "center", marginTop: 6, fontSize: 11, color: "#34d399", fontWeight: 500 }}>
                        ✓ Connected & ready for batch generation
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div style={{ display: "flex", gap: 6 }}>
                        <button
                          className="btn btn-primary btn-sm"
                          onClick={() => handleLaunchLogin(acc.id)}
                          style={{ flex: 1, justifyContent: "center", fontSize: 12.5, fontWeight: 600, gap: 6, padding: "8px 12px" }}
                        >
                          <ExternalLink size={14} />
                          <span>Sign in & Connect Google Account</span>
                        </button>
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => handleVerifySession(acc.id)}
                          disabled={isVerifying}
                          title="Check if session already signed in"
                        >
                          <RefreshCw size={13} className={isVerifying ? "spin" : ""} />
                        </button>
                      </div>
                      <div style={{ textAlign: "center", marginTop: 6, fontSize: 11, color: "var(--text-muted)" }}>
                        Opens Google Flow to link your 1,000 Omni Flash credits
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Main Studio Launcher */}
      <div style={{ maxWidth: 820, margin: "0 auto", width: "100%" }}>
        {/* Studio Launcher Form */}
        <div style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: "26px 28px" }}>
          <h2 style={{ margin: "0 0 18px", fontSize: 16, fontWeight: 700, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
            <Sliders size={18} color="var(--accent)" />
            <span>Generation Studio & Presets</span>
          </h2>

          <form onSubmit={handleLaunchBatch}>
            {/* Format Selection */}
            <div className="field" style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                Content Format (100% 9:16 Vertical)
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <button
                  type="button"
                  className={`btn ${mediaType === "reel" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setMediaType("reel")}
                  style={{ justifyContent: "center", fontSize: 12.5, gap: 6 }}
                >
                  <Video size={14} />
                  <span>Vertical Reel (Omni Flash)</span>
                </button>
                <button
                  type="button"
                  className={`btn ${mediaType === "photo" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setMediaType("photo")}
                  style={{ justifyContent: "center", fontSize: 12.5, gap: 6 }}
                >
                  <ImageIcon size={14} />
                  <span>9:16 Photo Card (Visual + Quote)</span>
                </button>
              </div>
            </div>

            {/* Target Duration (Only for Video) */}
            {mediaType === "reel" && (
              <div className="field" style={{ marginBottom: 16 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                  Target Video Duration & Scenes
                </label>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
                  {[
                    { sec: 10, label: "10s (Single Hook)", scenes: "1 Scene" },
                    { sec: 30, label: "30s (Story Arc)", scenes: "3 Scenes Stitched" },
                    { sec: 60, label: "60s (Cinematic)", scenes: "5 Scenes Stitched" },
                  ].map((d) => (
                    <button
                      key={d.sec}
                      type="button"
                      className={`btn ${duration === d.sec ? "btn-primary" : "btn-secondary"}`}
                      onClick={() => setDuration(d.sec)}
                      style={{ flexDirection: "column", padding: "8px 6px", height: "auto", fontSize: 12 }}
                    >
                      <span style={{ fontWeight: 600 }}>{d.label}</span>
                      <span style={{ fontSize: 10, opacity: 0.8 }}>{d.scenes}</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Batch Count (Support 100+ Pieces) */}
            <div className="field" style={{ marginBottom: 16 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)" }}>
                  How Many Pieces to Create?
                </label>
                <span style={{ fontSize: 11, color: "var(--accent-light)", fontWeight: 600 }}>
                  Est. {count * (mediaType === "photo" ? 1 : (duration <= 12 ? 15 : (duration <= 35 ? 45 : 75)))} Credits
                </span>
              </div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 8 }}>
                {[1, 5, 10, 25, 50, 100].map((num) => (
                  <button
                    key={num}
                    type="button"
                    className={`btn ${count === num ? "btn-primary" : "btn-secondary"}`}
                    onClick={() => setCount(num)}
                    style={{ flex: "1 0 auto", minWidth: 44, justifyContent: "center", fontSize: 12.5, padding: "6px 8px" }}
                  >
                    {num}
                  </button>
                ))}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 8, background: "var(--bg-elevated)", padding: "6px 10px", borderRadius: 6, border: "1px solid var(--border-subtle)" }}>
                <span style={{ fontSize: 12, color: "var(--text-muted)", whiteSpace: "nowrap" }}>Custom Volume (1 to 500):</span>
                <input
                  type="number"
                  min="1"
                  max="500"
                  value={count}
                  onChange={(e) => setCount(Math.max(1, parseInt(e.target.value) || 1))}
                  style={{ width: 80, padding: "4px 8px", borderRadius: 4, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13, textAlign: "center" }}
                />
                <span style={{ fontSize: 11, color: "var(--text-muted)" }}>videos queued in autonomous batch</span>
              </div>
            </div>

            {/* Frontier AI Model Selector */}
            <div className="field" style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                AI Director Model (Free Tier & Frontier)
              </label>
              <select
                value={aiModel}
                onChange={(e) => setAiModel(e.target.value)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13 }}
              >
                {AI_MODELS.map((m) => (
                  <option key={m.id} value={m.id} style={{ background: "var(--bg-surface)", color: "var(--text-primary)" }}>{m.label}</option>
                ))}
              </select>
            </div>

            {/* Niche Category */}
            <div className="field" style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                Content Niche
              </label>
              <select
                value={niche}
                onChange={(e) => setNiche(e.target.value)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13, marginBottom: 8 }}
              >
                {NICHES.map((n) => (
                  <option key={n} value={n} style={{ background: "var(--bg-surface)", color: "var(--text-primary)" }}>{n}</option>
                ))}
              </select>
              <input
                type="text"
                placeholder="Or type a custom niche / sub-topic (e.g. Ancient Architecture, Mindset)..."
                value={customNiche}
                onChange={(e) => setCustomNiche(e.target.value)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13 }}
              />
            </div>

            {/* Google Drive Destination (Zero Overwhelm) */}
            <div className="field" style={{ marginBottom: 18 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                Google Drive Storage & Target Page
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6, marginBottom: 8 }}>
                <button
                  type="button"
                  className={`btn ${folderOption === "auto" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setFolderOption("auto")}
                  style={{ fontSize: 11, justifyContent: "center", padding: "6px 4px" }}
                >
                  <FolderPlus size={12} />
                  <span>Auto Date Folder</span>
                </button>
                <button
                  type="button"
                  className={`btn ${folderOption === "page" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setFolderOption("page")}
                  style={{ fontSize: 11, justifyContent: "center", padding: "6px 4px" }}
                >
                  <Layers size={12} />
                  <span>Attach To Page</span>
                </button>
                <button
                  type="button"
                  className={`btn ${folderOption === "id" ? "btn-primary" : "btn-secondary"}`}
                  onClick={() => setFolderOption("id")}
                  style={{ fontSize: 11, justifyContent: "center", padding: "6px 4px" }}
                >
                  <Folder size={12} />
                  <span>Drive Folder ID</span>
                </button>
              </div>

              {folderOption === "page" && (
                <div style={{ marginBottom: 8 }}>
                  <select
                    value={targetPageId}
                    onChange={(e) => setTargetPageId(e.target.value)}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13 }}
                  >
                    {pages?.map((p) => (
                      <option key={p.id} value={p.id} style={{ background: "var(--bg-surface)", color: "var(--text-primary)" }}>
                        {p.name} {p.drive_folder_id ? `(Drive: ...${p.drive_folder_id.slice(-6)})` : "(No Drive folder configured)"}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {folderOption === "id" && (
                <div style={{ marginBottom: 8 }}>
                  <input
                    type="text"
                    placeholder="Paste Google Drive Folder ID (e.g. 1a2b3c4d5e...)"
                    value={customFolderId}
                    onChange={(e) => setCustomFolderId(e.target.value)}
                    style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13 }}
                  />
                  <span style={{ fontSize: 10.5, color: "var(--text-muted)", display: "block", marginTop: 4 }}>
                    From your browser URL: drive.google.com/drive/folders/<b>FOLDER_ID</b>
                  </span>
                </div>
              )}
            </div>

            {/* Optional Specific Prompt / Hook */}
            <div className="field" style={{ marginBottom: 20 }}>
              <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
                Custom Hook Idea or Prompt (Optional)
              </label>
              <textarea
                rows={2}
                placeholder="Leave blank for autonomous AI formulation, or provide a specific seed thought..."
                value={customPrompt}
                onChange={(e) => setCustomPrompt(e.target.value)}
                style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)", fontSize: 13, resize: "vertical" }}
              />
            </div>

            {/* Actions */}
            <div style={{ display: "flex", gap: 10 }}>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={submitting || accounts.length === 0}
                style={{ flex: 1, justifyContent: "center", padding: "10px 16px", fontSize: 14, fontWeight: 600, gap: 8 }}
              >
                <Play size={16} fill="currentColor" />
                <span>{submitting ? "Dispatching Jobs…" : `Launch Generation (${count} Pieces)`}</span>
              </button>

              {mediaType === "photo" && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleQuickImagePreview}
                  disabled={previewLoading}
                  style={{ fontSize: 13, gap: 6 }}
                >
                  <Eye size={15} />
                  <span>{previewLoading ? "Rendering…" : "Preview Image"}</span>
                </button>
              )}
            </div>
          </form>
        </div>
      </div>

      {/* Add Account Modal */}
      {showAddModal && (
        <div className="overlay" onClick={() => setShowAddModal(false)}>
          <div className="modal" style={{ width: "min(480px, 94vw)" }} onClick={(e) => e.stopPropagation()}>
            <h2 style={{ margin: "0 0 8px", fontSize: 17, color: "var(--text-primary)", display: "flex", alignItems: "center", gap: 8 }}>
              <Key size={18} color="var(--accent)" />
              <span>Connect Google Flow Account (1,000 Credits)</span>
            </h2>
            <p style={{ margin: "0 0 16px", fontSize: 13, color: "var(--text-muted)", lineHeight: 1.5 }}>
              Enter an account label and your Google email below. Clicking <b>Connect & Launch Sign-In</b> will immediately open Google Flow (<code>labs.google/fx/tools/flow</code>) so you can sign in and link your 1,000 Omni Flash credits.
            </p>
            <form onSubmit={handleAddAccount}>
              <div className="field" style={{ marginBottom: 12 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                  Account Nickname / Label
                </label>
                <input
                  type="text"
                  placeholder="e.g. Google Account 1 (Primary)"
                  required
                  value={newLabel}
                  onChange={(e) => setNewLabel(e.target.value)}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)" }}
                />
              </div>
              <div className="field" style={{ marginBottom: 18 }}>
                <label style={{ fontSize: 12.5, fontWeight: 600, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>
                  Google Account Email (for reference)
                </label>
                <input
                  type="email"
                  placeholder="e.g. creator@gmail.com"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  style={{ width: "100%", padding: "8px 12px", borderRadius: 6, background: "var(--bg-input)", border: "1px solid var(--border-subtle)", color: "var(--text-primary)" }}
                />
              </div>

              <div style={{ background: "rgba(16, 185, 129, 0.08)", border: "1px solid rgba(16, 185, 129, 0.2)", borderRadius: 6, padding: "10px 12px", marginBottom: 18, fontSize: 12, color: "#34d399", display: "flex", gap: 8, alignItems: "center" }}>
                <CheckCircle2 size={16} style={{ flexShrink: 0 }} />
                <span>All session cookies and credits are saved permanently in your local profile. You only log in once.</span>
              </div>

              <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
                <button type="button" className="btn btn-secondary" onClick={() => setShowAddModal(false)}>Cancel</button>
                <button type="submit" className="btn btn-primary" style={{ gap: 6 }}>
                  <span>Connect & Launch Google Sign-In</span>
                  <ExternalLink size={14} />
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* On-Demand Image Preview Modal */}
      {previewDataUri && (
        <div className="overlay" onClick={() => setPreviewDataUri(null)}>
          <div className="modal" style={{ width: "min(400px, 94vw)", textAlign: "center" }} onClick={(e) => e.stopPropagation()}>
            <h3 style={{ margin: "0 0 12px", fontSize: 16, color: "var(--text-primary)" }}>Instant 9:16 Vertical Preview</h3>
            <img
              src={previewDataUri}
              alt="Vertical Preview"
              style={{ width: "100%", maxHeight: "68vh", objectFit: "contain", borderRadius: 8, marginBottom: 14 }}
            />
            <button className="btn btn-primary" onClick={() => setPreviewDataUri(null)} style={{ width: "100%", justifyContent: "center" }}>
              Close Preview
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
