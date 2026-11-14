// frontend/src/components/UniversalStudio.jsx
// Universal Content Studio — Topic -> Visual Brief -> Multi-Provider References -> Companion Triplet -> Drive & Library
import React, { useState, useEffect, useRef } from "react";
import { api } from "../api.js";
import {
  Sparkles,
  Search,
  Image as ImageIcon,
  FolderPlus,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  FileText,
  Sliders,
  Play,
  Layers,
  ShieldCheck,
  ExternalLink,
  ChevronRight,
  Database,
  CloudUpload,
  Calendar,
  Lock,
  Globe,
  Camera,
  Cpu,
  Eye,
  Info,
} from "lucide-react";

const PRESET_TOPICS = [
  { label: "Roman Aqueducts", niche: "History", topic: "Ancient Roman Aqueducts engineering marvel" },
  { label: "Nordic Living", niche: "Lifestyle", topic: "Minimalist Scandinavian Interior Design" },
  { label: "James Webb Deep Field", niche: "Science", topic: "James Webb Space Telescope cosmic nebulae" },
  { label: "Artisan Espresso", niche: "Food & Craft", topic: "Specialty Pour-Over & Espresso Extraction" },
  { label: "Cyberpunk Future", niche: "Technology", topic: "Futuristic Megacity Cyberpunk Architecture" },
  { label: "Apollo 11 Landing", niche: "History", topic: "Apollo 11 Saturn V Moon Landing Missions" },
];

export default function UniversalStudio({ pages = [], activePage, setActivePage }) {
  // Tabs: "studio" | "buffer" | "providers" | "history"
  const [activeTab, setActiveTab] = useState("studio");

  // Form State
  const [topic, setTopic] = useState("Ancient Roman Aqueducts engineering marvel");
  const [niche, setNiche] = useState("History");
  const [instructions, setInstructions] = useState("");
  const [mode, setMode] = useState("hybrid"); // "hybrid" | "real" | "ai"
  const [batchCount, setBatchCount] = useState(3);
  const resolvePageId = (ap, pgs) => {
    if (typeof ap === "object" && ap?.id) return ap.id;
    if (ap) return ap;
    return pgs?.[0]?.id || "";
  };

  const [selectedPageId, setSelectedPageId] = useState(() => resolvePageId(activePage, pages));

  useEffect(() => {
    const pId = resolvePageId(activePage, pages);
    if (pId && (!selectedPageId || selectedPageId !== pId)) {
      setSelectedPageId(pId);
    }
  }, [activePage, pages]);
  const [selectedFolderId, setSelectedFolderId] = useState("");
  const [autoQueue, setAutoQueue] = useState(false);
  const [requiresApproval, setRequiresApproval] = useState(true);

  // Discovery & Planning State
  const [brief, setBrief] = useState(null);
  const [candidates, setCandidates] = useState([]);
  const [selectedCandidate, setSelectedCandidate] = useState(null);
  const [captionData, setCaptionData] = useState(null);
  const [editedCaption, setEditedCaption] = useState("");
  const [createdAsset, setCreatedAsset] = useState(null);

  // Background Batch Job State
  const [activeJobId, setActiveJobId] = useState(null);
  const [currentJob, setCurrentJob] = useState(null);
  const [jobsHistory, setJobsHistory] = useState([]);

  // Data & Diagnostics
  const [providersStatus, setProvidersStatus] = useState(null);
  const [driveFolders, setDriveFolders] = useState([]);
  const [bufferData, setBufferData] = useState(null);

  // UI Loaders & Feedback
  const [loadingBrief, setLoadingBrief] = useState(false);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [loadingCaption, setLoadingCaption] = useState(false);
  const [loadingTriplet, setLoadingTriplet] = useState(false);
  const [statusMessage, setStatusMessage] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);

  // New Folder Modal
  const [showFolderModal, setShowFolderModal] = useState(false);
  const [newFolderName, setNewFolderName] = useState("");
  const [creatingFolder, setCreatingFolder] = useState(false);

  // Poll active background job
  useEffect(() => {
    let interval = null;
    if (activeJobId) {
      interval = setInterval(async () => {
        try {
          const res = await api.studioJob(activeJobId);
          setCurrentJob(res);
          if (res.status === "completed" || res.status === "failed") {
            setActiveJobId(null);
            loadJobs();
            loadBuffer();
            if (res.status === "completed") {
              setStatusMessage(`Batch completed! Generated ${res.items?.length || 0} paired assets.`);
            } else {
              setErrorMessage(`Batch failed: ${res.error || "Unknown error"}`);
            }
          }
        } catch (e) {
          console.error("Job poll error:", e);
        }
      }, 2500);
    }
    return () => clearInterval(interval);
  }, [activeJobId]);

  // Initial Load
  useEffect(() => {
    loadProviders();
    loadFolders();
    loadJobs();
    if (selectedPageId) loadBuffer(selectedPageId);
  }, [selectedPageId]);

  const loadProviders = async () => {
    try {
      const res = await api.studioProviders();
      setProvidersStatus(res);
    } catch (e) {
      console.warn("Could not load provider diagnostics:", e);
    }
  };

  const loadFolders = async () => {
    try {
      const res = await api.studioFolders();
      setDriveFolders(res.folders || []);
      if (res.folders?.length > 0 && !selectedFolderId) {
        setSelectedFolderId(res.folders[0].id);
      }
    } catch (e) {
      console.warn("Could not load drive folders:", e);
    }
  };

  const loadJobs = async () => {
    try {
      const res = await api.studioJobs();
      setJobsHistory(res.jobs || []);
    } catch (e) {
      console.warn("Could not load jobs history:", e);
    }
  };

  const loadBuffer = async (pageId = selectedPageId) => {
    try {
      const res = await api.studioBuffer(pageId);
      setBufferData(res);
    } catch (e) {
      console.warn("Could not load buffer:", e);
    }
  };

  // Action: Generate Visual Brief & Search Candidates
  const handleDiscover = async () => {
    setErrorMessage(null);
    setStatusMessage(null);
    setLoadingBrief(true);
    setCandidates([]);
    setSelectedCandidate(null);
    setCaptionData(null);
    setCreatedAsset(null);

    try {
      // 1. Generate Brief
      const bRes = await api.studioBrief({ topic, niche, instructions });
      setBrief(bRes.brief);
      setLoadingBrief(false);

      // 2. Discover References
      setLoadingCandidates(true);
      const cRes = await api.studioDiscover({
        topic,
        niche,
        instructions,
        mode,
        max_candidates: 12,
      });
      setCandidates(cRes.candidates || []);
      if (cRes.candidates?.length > 0) {
        handleSelectCandidate(cRes.candidates[0], bRes.brief);
      } else {
        setErrorMessage("No candidate images discovered. Try switching mode to Hybrid or AI Creative.");
      }
    } catch (e) {
      setErrorMessage(e.message || "Discovery failed.");
    } finally {
      setLoadingBrief(false);
      setLoadingCandidates(false);
    }
  };

  // Action: Select Candidate & Generate Tailored Caption
  const handleSelectCandidate = async (cand, customBrief = brief) => {
    setSelectedCandidate(cand);
    setLoadingCaption(true);
    setCaptionData(null);
    try {
      const cRes = await api.studioCaption({
        topic: customBrief?.topic || topic,
        niche: customBrief?.niche || niche,
        image_title: cand.title,
        image_creator: cand.creator,
        image_license: cand.license,
        attribution_text: cand.attribution_text,
        page_id: selectedPageId ? parseInt(selectedPageId) : null,
      });
      setCaptionData(cRes);
      setEditedCaption(cRes.caption);
    } catch (e) {
      console.warn("Caption generation error:", e);
      setEditedCaption(`${cand.title}\n\n#${niche.replace(/\s+/g, "")} #Perspective`);
    } finally {
      setLoadingCaption(false);
    }
  };

  // Action: Package Companion Triplet & Sync
  const handleCreateTriplet = async () => {
    if (!selectedCandidate) return;
    setLoadingTriplet(true);
    setErrorMessage(null);
    setStatusMessage(null);
    try {
      const res = await api.studioCreateTriplet({
        topic,
        niche,
        candidate: selectedCandidate,
        caption: editedCaption,
        hashtags: captionData?.hashtags || [],
        page_id: selectedPageId ? parseInt(selectedPageId) : null,
        drive_folder_id: selectedFolderId || null,
        auto_queue: autoQueue,
        requires_approval: requiresApproval,
      });
      setCreatedAsset(res);
      setStatusMessage(`Companion triplet successfully created & synced to Content Library (#${res.content_item_id})!`);
      loadBuffer();
    } catch (e) {
      setErrorMessage(e.message || "Failed to create companion files.");
    } finally {
      setLoadingTriplet(false);
    }
  };

  // Action: Start Background Batch Generation
  const handleLaunchBatch = async () => {
    setErrorMessage(null);
    setStatusMessage(null);
    try {
      const res = await api.studioBatchGenerate({
        topic,
        niche,
        instructions,
        quantity: batchCount,
        mode,
        page_id: selectedPageId ? parseInt(selectedPageId) : null,
        drive_folder_id: selectedFolderId || null,
        auto_upload_drive: !!selectedFolderId,
        auto_queue: autoQueue,
        requires_approval: requiresApproval,
      });
      setActiveJobId(res.job_id);
      setStatusMessage(res.message);
      loadJobs();
    } catch (e) {
      setErrorMessage(e.message || "Could not launch batch job.");
    }
  };

  // Action: Create New Google Drive Folder
  const handleCreateFolder = async (e) => {
    e.preventDefault();
    if (!newFolderName.trim()) return;
    setCreatingFolder(true);
    try {
      const res = await api.studioCreateFolder({ name: newFolderName.trim() });
      setShowFolderModal(false);
      setNewFolderName("");
      await loadFolders();
      if (res.folder?.id) {
        setSelectedFolderId(res.folder.id);
      }
      setStatusMessage(`Google Drive folder '${res.folder?.name}' created successfully!`);
    } catch (e) {
      alert(`Could not create folder: ${e.message}`);
    } finally {
      setCreatingFolder(false);
    }
  };

  return (
    <div className="view-container" style={{ padding: "24px 32px", maxWidth: 1400, margin: "0 auto" }}>
      {/* Top Header Banner */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{
              width: 38,
              height: 38,
              borderRadius: 10,
              background: "linear-gradient(135deg, #6366f1 0%, #a855f7 100%)",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              boxShadow: "0 4px 14px rgba(99, 102, 241, 0.35)"
            }}>
              <Sparkles size={20} color="#fff" />
            </div>
            <div>
              <h1 style={{ fontSize: 24, fontWeight: 700, margin: 0, letterSpacing: "-0.02em" }}>
                Universal Content Studio
              </h1>
              <p style={{ margin: 0, fontSize: 13, color: "var(--text-muted)" }}>
                Provider-independent reference discovery, companion triplets (.jpg/.txt/.json), and automated library scheduling
              </p>
            </div>
          </div>
        </div>

        {/* View Switcher Tabs */}
        <div style={{ display: "flex", background: "var(--bg-card)", padding: 4, borderRadius: 10, border: "1px solid var(--border-color)" }}>
          <button
            className={`btn small ${activeTab === "studio" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setActiveTab("studio")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Sliders size={14} />
            <span>Studio Engine</span>
          </button>
          <button
            className={`btn small ${activeTab === "buffer" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setActiveTab("buffer")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Database size={14} />
            <span>Buffer Autopilot</span>
            {bufferData?.deficit > 0 && (
              <span style={{ background: "#ef4444", color: "#fff", fontSize: 10, padding: "1px 6px", borderRadius: 10 }}>
                -{bufferData.deficit}
              </span>
            )}
          </button>
          <button
            className={`btn small ${activeTab === "providers" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setActiveTab("providers")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <ShieldCheck size={14} />
            <span>Providers & Provenance</span>
          </button>
          <button
            className={`btn small ${activeTab === "history" ? "btn-primary" : "btn-secondary"}`}
            onClick={() => setActiveTab("history")}
            style={{ display: "flex", alignItems: "center", gap: 6 }}
          >
            <Layers size={14} />
            <span>Job History</span>
          </button>
        </div>
      </div>

      {/* Global Banners */}
      {statusMessage && (
        <div className="banner success" style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <CheckCircle2 size={16} />
            <span>{statusMessage}</span>
          </div>
          <button className="btn small" onClick={() => setStatusMessage(null)}>Dismiss</button>
        </div>
      )}
      {errorMessage && (
        <div className="banner err" style={{ marginBottom: 16, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertCircle size={16} />
            <span>{errorMessage}</span>
          </div>
          <button className="btn small" onClick={() => setErrorMessage(null)}>Dismiss</button>
        </div>
      )}

      {/* Active Job Progress Bar if running */}
      {currentJob && currentJob.status === "processing" && (
        <div style={{
          background: "var(--bg-card)",
          border: "1px solid #6366f1",
          borderRadius: 12,
          padding: 16,
          marginBottom: 20,
          boxShadow: "0 4px 20px rgba(99, 102, 241, 0.15)"
        }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8, alignItems: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <RefreshCw size={16} className="spin" style={{ color: "#6366f1" }} />
              <span style={{ fontWeight: 600, fontSize: 14 }}>Batch Job in Progress: {currentJob.topic}</span>
            </div>
            <span style={{ fontSize: 13, color: "var(--accent-light)", fontWeight: 700 }}>{currentJob.progress}%</span>
          </div>
          <div style={{ width: "100%", height: 6, background: "rgba(255,255,255,0.08)", borderRadius: 3, overflow: "hidden", marginBottom: 8 }}>
            <div style={{ width: `${currentJob.progress}%`, height: "100%", background: "linear-gradient(90deg, #6366f1, #a855f7)", transition: "width 0.4s ease" }} />
          </div>
          <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
            {currentJob.current_step || "Processing..."}
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB 1: STUDIO ENGINE */}
      {/* ============================================================ */}
      {activeTab === "studio" && (
        <div style={{ display: "grid", gridTemplateColumns: "420px 1fr", gap: 24, alignItems: "start" }}>
          {/* Left Column: Topic & Controls Configuration Card */}
          <div className="card" style={{ padding: 22, background: "var(--bg-card)", borderRadius: 14, border: "1px solid var(--border-color)" }}>
            <h3 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 16px 0", display: "flex", alignItems: "center", gap: 8 }}>
              <Sliders size={18} style={{ color: "var(--accent-light)" }} />
              <span>Topic & Visual Brief</span>
            </h3>

            {/* Quick Topic Presets */}
            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 11, fontWeight: 600, color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", display: "block", marginBottom: 6 }}>
                Curated Niche Presets
              </label>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {PRESET_TOPICS.map((p, i) => (
                  <button
                    key={i}
                    type="button"
                    onClick={() => {
                      setTopic(p.topic);
                      setNiche(p.niche);
                    }}
                    style={{
                      background: topic === p.topic ? "rgba(99, 102, 241, 0.2)" : "var(--bg-input)",
                      border: topic === p.topic ? "1px solid #6366f1" : "1px solid var(--border-color)",
                      color: topic === p.topic ? "#a5b4fc" : "var(--text-main)",
                      fontSize: 11,
                      padding: "4px 8px",
                      borderRadius: 6,
                      cursor: "pointer",
                      transition: "all 0.15s"
                    }}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Main Topic Input */}
            <div style={{ marginBottom: 14 }}>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 5 }}>
                Topic / Subject <span style={{ color: "#ef4444" }}>*</span>
              </label>
              <textarea
                rows={2}
                value={topic}
                onChange={(e) => setTopic(e.target.value)}
                placeholder="Enter any topic (e.g. Ancient Roman Aqueducts, 1920s Art Deco, Minimalist Morning Routine)..."
                style={{
                  width: "100%",
                  padding: "9px 12px",
                  borderRadius: 8,
                  background: "var(--bg-input)",
                  border: "1px solid var(--border-color)",
                  color: "var(--text-main)",
                  fontSize: 13,
                  resize: "vertical",
                  boxSizing: "border-box"
                }}
              />
            </div>

            {/* Niche & Instructions */}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 5 }}>Niche</label>
                <input
                  type="text"
                  value={niche}
                  onChange={(e) => setNiche(e.target.value)}
                  placeholder="History, Lifestyle, Tech"
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-main)",
                    fontSize: 13,
                    boxSizing: "border-box"
                  }}
                />
              </div>
              <div>
                <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 5 }}>Batch Size</label>
                <select
                  value={batchCount}
                  onChange={(e) => setBatchCount(parseInt(e.target.value))}
                  style={{
                    width: "100%",
                    padding: "8px 10px",
                    borderRadius: 8,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-main)",
                    fontSize: 13,
                    boxSizing: "border-box"
                  }}
                >
                  <option value={1}>1 Asset (Single)</option>
                  <option value={3}>3 Assets (Recommended)</option>
                  <option value={5}>5 Assets (Standard)</option>
                  <option value={10}>10 Assets (Full Batch)</option>
                </select>
              </div>
            </div>

            {/* Acquisition Mode Switcher */}
            <div style={{ marginBottom: 16 }}>
              <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                Image Acquisition Strategy
              </label>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 6 }}>
                {[
                  { id: "hybrid", label: "Hybrid Mode", sub: "Sources + AI Fallback" },
                  { id: "real", label: "Authentic Real", sub: "Openverse & Archives" },
                  { id: "ai", label: "Creative AI", sub: "Synthetic Visuals" }
                ].map((m) => (
                  <button
                    key={m.id}
                    type="button"
                    onClick={() => setMode(m.id)}
                    style={{
                      background: mode === m.id ? "rgba(99, 102, 241, 0.18)" : "var(--bg-input)",
                      border: mode === m.id ? "2px solid #6366f1" : "1px solid var(--border-color)",
                      borderRadius: 8,
                      padding: "8px 6px",
                      textAlign: "center",
                      cursor: "pointer",
                      transition: "all 0.15s"
                    }}
                  >
                    <div style={{ fontSize: 11, fontWeight: 700, color: mode === m.id ? "#a5b4fc" : "var(--text-main)" }}>
                      {m.label}
                    </div>
                    <div style={{ fontSize: 9, color: "var(--text-muted)", marginTop: 2 }}>{m.sub}</div>
                  </button>
                ))}
              </div>
            </div>

            {/* Destination Configuration */}
            <div style={{ background: "rgba(255,255,255,0.02)", padding: 12, borderRadius: 10, border: "1px solid var(--border-color)", marginBottom: 18 }}>
              <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8, color: "var(--accent-light)" }}>
                Destination & Storage Pairing
              </div>

              {/* Facebook Page */}
              <div style={{ marginBottom: 10 }}>
                <label style={{ fontSize: 11, color: "var(--text-muted)", display: "block", marginBottom: 4 }}>Destination Account</label>
                <select
                  value={selectedPageId}
                  onChange={(e) => {
                    setSelectedPageId(e.target.value);
                    if (setActivePage) {
                      const val = e.target.value ? Number(e.target.value) : null;
                      setActivePage(val);
                    }
                  }}
                  style={{
                    width: "100%",
                    padding: "7px 10px",
                    borderRadius: 6,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-main)",
                    fontSize: 12,
                    boxSizing: "border-box"
                  }}
                >
                  <option value="">-- No Account (Staging Only) --</option>
                  {pages.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.fb_page_id})</option>
                  ))}
                </select>
              </div>

              {/* Google Drive Destination Folder */}
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                  <label style={{ fontSize: 11, color: "var(--text-muted)" }}>Google Drive Folder</label>
                  <button
                    type="button"
                    onClick={() => setShowFolderModal(true)}
                    style={{ fontSize: 11, color: "#818cf8", background: "none", border: "none", cursor: "pointer", padding: 0 }}
                  >
                    + New Folder
                  </button>
                </div>
                <select
                  value={selectedFolderId}
                  onChange={(e) => setSelectedFolderId(e.target.value)}
                  style={{
                    width: "100%",
                    padding: "7px 10px",
                    borderRadius: 6,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-main)",
                    fontSize: 12,
                    boxSizing: "border-box"
                  }}
                >
                  <option value="">-- Local Staging Only (No Drive Upload) --</option>
                  {driveFolders.map((f) => (
                    <option key={f.id} value={f.id}>{f.name}</option>
                  ))}
                </select>
              </div>
            </div>

            {/* Automation Toggles */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 18, fontSize: 12 }}>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={requiresApproval}
                  onChange={(e) => setRequiresApproval(e.target.checked)}
                />
                <span>Require Operator Approval before Scheduling</span>
              </label>
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={autoQueue}
                  onChange={(e) => setAutoQueue(e.target.checked)}
                />
                <span>Auto-Queue into Reel Ledger Scheduler on sync</span>
              </label>
            </div>

            {/* Action Buttons */}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <button
                type="button"
                className="btn btn-primary"
                onClick={handleDiscover}
                disabled={loadingBrief || loadingCandidates}
                style={{ width: "100%", justifyContent: "center", padding: "10px 16px", fontWeight: 700 }}
              >
                {loadingBrief || loadingCandidates ? (
                  <>
                    <RefreshCw size={16} className="spin" />
                    <span>Searching Reference Archives...</span>
                  </>
                ) : (
                  <>
                    <Search size={16} />
                    <span>Discover References & Formulate Brief</span>
                  </>
                )}
              </button>

              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleLaunchBatch}
                disabled={activeJobId !== null}
                style={{ width: "100%", justifyContent: "center", padding: "9px 16px", fontSize: 13 }}
              >
                <Play size={14} style={{ color: "#34d399" }} />
                <span>Launch Autonomous Batch ({batchCount} Posts)</span>
              </button>
            </div>
          </div>

          {/* Right Column: Dynamic Workspace (Brief, Gallery, and Companion Triplet) */}
          <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
            {/* Visual Brief Summary Card (if generated) */}
            {brief && (
              <div className="card" style={{ padding: 18, background: "var(--bg-card)", borderRadius: 12, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <Info size={16} style={{ color: "#818cf8" }} />
                    <span style={{ fontWeight: 700, fontSize: 14 }}>Structured Visual Brief</span>
                  </div>
                  <span style={{
                    fontSize: 11,
                    padding: "3px 8px",
                    borderRadius: 20,
                    background: brief.requires_authentic_source ? "rgba(239, 68, 68, 0.15)" : "rgba(34, 197, 94, 0.15)",
                    color: brief.requires_authentic_source ? "#f87171" : "#4ade80",
                    fontWeight: 600
                  }}>
                    {brief.requires_authentic_source ? "Authentic Source Required" : "Creative Illustrations Permitted"}
                  </span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, fontSize: 12 }}>
                  <div>
                    <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Targeted Search Queries:</div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
                      {brief.search_queries?.map((q, i) => (
                        <span key={i} style={{ background: "rgba(255,255,255,0.06)", padding: "2px 6px", borderRadius: 4, fontFamily: "monospace", fontSize: 11 }}>
                          "{q}"
                        </span>
                      ))}
                    </div>
                  </div>
                  <div>
                    <div style={{ color: "var(--text-muted)", fontSize: 11 }}>Composition & Verification:</div>
                    <div style={{ marginTop: 4, color: "var(--text-main)" }}>
                      {brief.desired_composition} · {brief.factual_verification_notes}
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Candidates Reference Gallery */}
            <div className="card" style={{ padding: 20, background: "var(--bg-card)", borderRadius: 14, border: "1px solid var(--border-color)" }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <ImageIcon size={18} style={{ color: "var(--accent-light)" }} />
                  <span style={{ fontWeight: 700, fontSize: 15 }}>
                    Discovered Candidates ({candidates.length})
                  </span>
                </div>
                <span style={{ fontSize: 12, color: "var(--text-muted)" }}>
                  Click an image card to adopt & preview caption
                </span>
              </div>

              {candidates.length === 0 && !loadingCandidates && (
                <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--text-muted)" }}>
                  <Search size={36} style={{ opacity: 0.3, marginBottom: 10 }} />
                  <p style={{ margin: 0, fontSize: 14 }}>No reference candidates loaded yet.</p>
                  <p style={{ margin: "4px 0 0 0", fontSize: 12 }}>
                    Click "Discover References & Formulate Brief" on the left to start multi-provider discovery.
                  </p>
                </div>
              )}

              {loadingCandidates && (
                <div style={{ textAlign: "center", padding: "40px 20px" }}>
                  <RefreshCw size={28} className="spin" style={{ color: "var(--accent-light)", marginBottom: 10 }} />
                  <div style={{ fontSize: 14, fontWeight: 600 }}>Querying Openverse, Wikimedia, LOC and Archives...</div>
                </div>
              )}

              {/* Grid of Candidate Cards */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 14 }}>
                {candidates.map((cand, idx) => {
                  const isSelected = selectedCandidate?.result_id === cand.result_id;
                  return (
                    <div
                      key={cand.result_id || idx}
                      onClick={() => handleSelectCandidate(cand)}
                      style={{
                        background: isSelected ? "rgba(99, 102, 241, 0.12)" : "rgba(255,255,255,0.03)",
                        border: isSelected ? "2px solid #6366f1" : "1px solid var(--border-color)",
                        borderRadius: 10,
                        overflow: "hidden",
                        cursor: "pointer",
                        transition: "all 0.18s",
                        position: "relative",
                        display: "flex",
                        flexDirection: "column"
                      }}
                    >
                      {/* Thumbnail Container */}
                      <div style={{ height: 160, background: "#0b0f19", position: "relative", overflow: "hidden" }}>
                        <img
                          src={cand.preview_url || cand.direct_image_url}
                          alt={cand.title}
                          style={{ width: "100%", height: "100%", objectFit: "cover" }}
                          onError={(e) => {
                            e.target.style.display = "none";
                          }}
                        />
                        {/* Provider Badge */}
                        <div style={{
                          position: "absolute",
                          top: 6,
                          left: 6,
                          background: "rgba(15, 23, 42, 0.85)",
                          backdropFilter: "blur(6px)",
                          padding: "2px 6px",
                          borderRadius: 4,
                          fontSize: 10,
                          fontWeight: 700,
                          textTransform: "uppercase",
                          color: cand.provider === "wikimedia" ? "#38bdf8" : (cand.provider === "openverse" ? "#f472b6" : (cand.provider.includes("ai") ? "#a855f7" : "#34d399"))
                        }}>
                          {cand.provider}
                        </div>

                        {/* Relevance Score */}
                        <div style={{
                          position: "absolute",
                          bottom: 6,
                          right: 6,
                          background: "rgba(0,0,0,0.8)",
                          padding: "2px 5px",
                          borderRadius: 4,
                          fontSize: 10,
                          fontWeight: 700,
                          color: "#10b981"
                        }}>
                          {Math.round(cand.relevance_score * 100)}%
                        </div>
                      </div>

                      {/* Card Meta */}
                      <div style={{ padding: 10, flex: 1, display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
                        <div>
                          <div style={{ fontSize: 12, fontWeight: 600, lineHeight: 1.3, marginBottom: 4, color: "var(--text-main)" }}>
                            {cand.title?.length > 45 ? cand.title.slice(0, 45) + "..." : cand.title}
                          </div>
                          <div style={{ fontSize: 10, color: "var(--text-muted)", marginBottom: 4 }}>
                            By {cand.creator || "Archive Contributor"}
                          </div>
                        </div>

                        <div style={{ marginTop: 8, display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <span style={{ fontSize: 9, padding: "2px 4px", borderRadius: 3, background: "rgba(255,255,255,0.06)", color: "var(--text-muted)" }}>
                            {cand.license || "Public Domain"}
                          </span>
                          <span style={{
                            fontSize: 9,
                            fontWeight: 700,
                            color: cand.rights_status === "AI_GENERATED" ? "#c084fc" : "#4ade80"
                          }}>
                            {cand.rights_status}
                          </span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Selected Asset Companion Preview & Packaging Card */}
            {selectedCandidate && (
              <div className="card" style={{ padding: 22, background: "var(--bg-card)", borderRadius: 14, border: "1px solid #6366f1" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <CheckCircle2 size={18} style={{ color: "#6366f1" }} />
                    <span style={{ fontWeight: 700, fontSize: 15 }}>
                      Selected Companion Asset: {selectedCandidate.title}
                    </span>
                  </div>
                  <span style={{ fontSize: 12, color: "#818cf8", fontWeight: 600 }}>
                    Triplet: .jpg + .caption.txt + .metadata.json
                  </span>
                </div>

                <div style={{ display: "grid", gridTemplateColumns: "180px 1fr", gap: 20 }}>
                  {/* Image Preview & Provenance */}
                  <div>
                    <div style={{ borderRadius: 8, overflow: "hidden", border: "1px solid var(--border-color)", marginBottom: 8 }}>
                      <img
                        src={selectedCandidate.preview_url || selectedCandidate.direct_image_url}
                        alt="Selected"
                        style={{ width: "100%", height: 220, objectFit: "cover", display: "block" }}
                      />
                    </div>
                    <div style={{ fontSize: 11, color: "var(--text-muted)" }}>
                      <div><strong>License:</strong> {selectedCandidate.license}</div>
                      <div><strong>Creator:</strong> {selectedCandidate.creator}</div>
                      {selectedCandidate.source_page_url && (
                        <a
                          href={selectedCandidate.source_page_url}
                          target="_blank"
                          rel="noreferrer"
                          style={{ color: "#818cf8", display: "inline-flex", alignItems: "center", gap: 3, marginTop: 4, textDecoration: "none" }}
                        >
                          <span>Original Source Page</span>
                          <ExternalLink size={10} />
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Caption Editor & Metadata */}
                  <div style={{ display: "flex", flexDirection: "column", justifyContent: "space-between" }}>
                    <div>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 }}>
                        <label style={{ fontSize: 12, fontWeight: 600 }}>
                          Matching Viral Caption & License Attribution (.caption.txt)
                        </label>
                        {loadingCaption && (
                          <span style={{ fontSize: 11, color: "#818cf8", display: "flex", alignItems: "center", gap: 4 }}>
                            <RefreshCw size={11} className="spin" /> Generating AI Caption...
                          </span>
                        )}
                      </div>
                      <textarea
                        rows={7}
                        value={editedCaption}
                        onChange={(e) => setEditedCaption(e.target.value)}
                        placeholder="Caption with hook, body, and required photo attribution..."
                        style={{
                          width: "100%",
                          padding: "10px 12px",
                          borderRadius: 8,
                          background: "var(--bg-input)",
                          border: "1px solid var(--border-color)",
                          color: "var(--text-main)",
                          fontSize: 13,
                          lineHeight: 1.5,
                          resize: "vertical",
                          boxSizing: "border-box"
                        }}
                      />
                    </div>

                    {/* Companion Packaging Action */}
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 14 }}>
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        Will save <code>POST_xxxx.jpg</code> & <code>POST_xxxx.caption.txt</code> and register 1 asset in Content Library.
                      </div>
                      <button
                        type="button"
                        className="btn btn-primary"
                        onClick={handleCreateTriplet}
                        disabled={loadingTriplet}
                        style={{ padding: "9px 20px", fontWeight: 700 }}
                      >
                        {loadingTriplet ? (
                          <>
                            <RefreshCw size={15} className="spin" />
                            <span>Packaging & Uploading Triplet...</span>
                          </>
                        ) : (
                          <>
                            <CloudUpload size={15} />
                            <span>Package & Sync to Reel Ledger</span>
                          </>
                        )}
                      </button>
                    </div>
                  </div>
                </div>

                {/* Newly Created Asset Confirmation Card */}
                {createdAsset && (
                  <div style={{
                    marginTop: 18,
                    background: "rgba(16, 185, 129, 0.08)",
                    border: "1px solid #10b981",
                    borderRadius: 10,
                    padding: 14
                  }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8, color: "#10b981", fontWeight: 700, fontSize: 13, marginBottom: 6 }}>
                      <CheckCircle2 size={16} />
                      <span>Asset Registered: {createdAsset.file_name} (Content Item #{createdAsset.content_item_id})</span>
                    </div>
                    <div style={{ fontSize: 12, display: "flex", flexWrap: "wrap", gap: 14, color: "var(--text-main)" }}>
                      <span><strong>Drive Media ID:</strong> {createdAsset.drive_file_id || "Local Staging"}</span>
                      <span><strong>Drive Caption ID:</strong> {createdAsset.drive_caption_id || "Staged"}</span>
                      <span><strong>Validation Status:</strong> {createdAsset.validation_status}</span>
                      {createdAsset.scheduled && (
                        <span><strong>Scheduled For:</strong> {new Date(createdAsset.scheduled.scheduled_time_utc).toLocaleString()}</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB 2: BUFFER AUTOPILOT */}
      {/* ============================================================ */}
      {activeTab === "buffer" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card" style={{ padding: 24, background: "var(--bg-card)", borderRadius: 14, border: "1px solid var(--border-color)" }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, margin: "0 0 16px 0" }}>
              Universal Ready-to-Publish Content Buffer
            </h3>
            <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "0 0 20px 0" }}>
              The Content Buffer ensures your publishing queue never starves. When the buffer drops below the target threshold,
              Reel Ledger automatically discovers new non-repetitive topics, creates companion triplets, and stages them.
            </p>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 16, marginBottom: 24 }}>
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Ready in Library</div>
                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: "#38bdf8" }}>
                  {bufferData?.ready_in_library ?? "--"}
                </div>
              </div>
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Future Scheduled</div>
                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: "#a855f7" }}>
                  {bufferData?.scheduled_in_queue ?? "--"}
                </div>
              </div>
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ fontSize: 12, color: "var(--text-muted)" }}>Target Buffer Target</div>
                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: "#e2e8f0" }}>
                  {bufferData?.target_buffer ?? 15}
                </div>
              </div>
              <div style={{
                background: (bufferData?.deficit || 0) > 0 ? "rgba(239, 68, 68, 0.08)" : "rgba(34, 197, 94, 0.08)",
                padding: 16,
                borderRadius: 10,
                border: (bufferData?.deficit || 0) > 0 ? "1px solid #ef4444" : "1px solid #22c55e"
              }}>
                <div style={{ fontSize: 12, color: (bufferData?.deficit || 0) > 0 ? "#f87171" : "#4ade80" }}>Current Deficit</div>
                <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4, color: (bufferData?.deficit || 0) > 0 ? "#ef4444" : "#22c55e" }}>
                  {bufferData?.deficit ?? 0}
                </div>
              </div>
            </div>

            <div style={{ display: "flex", justifyContent: "flex-end", gap: 12 }}>
              <button
                className="btn btn-secondary"
                onClick={() => loadBuffer()}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <RefreshCw size={14} /> Refresh Buffer Status
              </button>
              <button
                className="btn btn-primary"
                onClick={async () => {
                  try {
                    const res = await api.studioRefillBuffer({
                      page_id: selectedPageId ? parseInt(selectedPageId) : 1,
                      target_buffer: 15,
                      auto_refill: true,
                      niche,
                      drive_folder_id: selectedFolderId
                    });
                    if (res.job_id) {
                      setActiveJobId(res.job_id);
                    }
                    setStatusMessage(res.message);
                  } catch (e) {
                    setErrorMessage(e.message);
                  }
                }}
                disabled={activeJobId !== null || (bufferData?.deficit || 0) === 0}
                style={{ display: "flex", alignItems: "center", gap: 6 }}
              >
                <Sparkles size={14} />
                <span>Replenish Deficit Now ({bufferData?.deficit || 0} Assets)</span>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB 3: PROVIDERS & PROVENANCE DIAGNOSTICS */}
      {/* ============================================================ */}
      {activeTab === "providers" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card" style={{ padding: 24, background: "var(--bg-card)", borderRadius: 14, border: "1px solid var(--border-color)" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
              <div>
                <h3 style={{ fontSize: 18, fontWeight: 700, margin: 0 }}>
                  Real Provider Connectivity & Licensing Matrix
                </h3>
                <p style={{ fontSize: 13, color: "var(--text-muted)", margin: "4px 0 0 0" }}>
                  Status, latency, and license verification across all official image source endpoints
                </p>
              </div>
              <button className="btn btn-secondary small" onClick={loadProviders} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <RefreshCw size={13} />
                <span>Re-Test All Endpoints</span>
              </button>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))", gap: 16 }}>
              {/* Google Drive Status */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>Google Drive Service Account</span>
                  <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(34, 197, 94, 0.15)", color: "#4ade80", fontWeight: 700 }}>
                    OPERATIONAL
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Client:</strong> {providersStatus?.google_drive?.email || "Service Account"}</div>
                  <div><strong>Scopes:</strong> drive.file, drive (read/write)</div>
                  <div><strong>Triplets:</strong> .jpg, .caption.txt, .metadata.json</div>
                </div>
              </div>

              {/* Openverse */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>Openverse (WordPress CC)</span>
                  <span style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: providersStatus?.providers?.openverse?.status === "operational" ? "rgba(34, 197, 94, 0.15)" : "rgba(239, 68, 68, 0.15)",
                    color: providersStatus?.providers?.openverse?.status === "operational" ? "#4ade80" : "#f87171",
                    fontWeight: 700
                  }}>
                    {providersStatus?.providers?.openverse?.status?.toUpperCase() || "CHECKING"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Latency:</strong> {providersStatus?.providers?.openverse?.latency_ms ?? "--"} ms</div>
                  <div><strong>License:</strong> CC0, Public Domain, CC BY (Commercial)</div>
                  <div><strong>Authentication:</strong> Open Access API</div>
                </div>
              </div>

              {/* Wikimedia Commons */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>Wikimedia Commons</span>
                  <span style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: providersStatus?.providers?.wikimedia?.status === "operational" ? "rgba(34, 197, 94, 0.15)" : "rgba(239, 68, 68, 0.15)",
                    color: providersStatus?.providers?.wikimedia?.status === "operational" ? "#4ade80" : "#f87171",
                    fontWeight: 700
                  }}>
                    {providersStatus?.providers?.wikimedia?.status?.toUpperCase() || "CHECKING"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Latency:</strong> {providersStatus?.providers?.wikimedia?.latency_ms ?? "--"} ms</div>
                  <div><strong>License:</strong> Public Domain, CC BY, CC BY-SA</div>
                  <div><strong>Authentication:</strong> MediaWiki API (Official)</div>
                </div>
              </div>

              {/* Library of Congress */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>Library of Congress (LOC)</span>
                  <span style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: providersStatus?.providers?.library_of_congress?.status === "operational" ? "rgba(34, 197, 94, 0.15)" : "rgba(245, 158, 11, 0.15)",
                    color: providersStatus?.providers?.library_of_congress?.status === "operational" ? "#4ade80" : "#fbbf24",
                    fontWeight: 700
                  }}>
                    {providersStatus?.providers?.library_of_congress?.status?.toUpperCase() || "CHECKING"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Latency:</strong> {providersStatus?.providers?.library_of_congress?.latency_ms ?? "--"} ms</div>
                  <div><strong>License:</strong> Public Domain / Historical Archives</div>
                  <div><strong>Authentication:</strong> Public Search API</div>
                </div>
              </div>

              {/* National Archives */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>National Archives Catalog</span>
                  <span style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: providersStatus?.providers?.national_archives?.status === "operational" ? "rgba(34, 197, 94, 0.15)" : "rgba(245, 158, 11, 0.15)",
                    color: providersStatus?.providers?.national_archives?.status === "operational" ? "#4ade80" : "#fbbf24",
                    fontWeight: 700
                  }}>
                    {providersStatus?.providers?.national_archives?.status?.toUpperCase() || "CHECKING"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Latency:</strong> {providersStatus?.providers?.national_archives?.latency_ms ?? "--"} ms</div>
                  <div><strong>License:</strong> Public Domain (US Federal Works)</div>
                  <div><strong>Authentication:</strong> NARA v2 API</div>
                </div>
              </div>

              {/* Pexels Commercial Stock */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>Pexels Commercial Stock</span>
                  <span style={{
                    fontSize: 11,
                    padding: "2px 6px",
                    borderRadius: 4,
                    background: providersStatus?.providers?.pexels?.status === "operational" ? "rgba(34, 197, 94, 0.15)" : "rgba(148, 163, 184, 0.15)",
                    color: providersStatus?.providers?.pexels?.status === "operational" ? "#4ade80" : "#94a3b8",
                    fontWeight: 700
                  }}>
                    {providersStatus?.providers?.pexels?.status === "operational" ? "ACTIVE" : "KEY REQUIRED"}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>License:</strong> Pexels License (Commercial, Free to use)</div>
                  <div><strong>Setup:</strong> Set <code>PEXELS_API_KEY</code> in .env to activate</div>
                </div>
              </div>

              {/* AI Generative Provider */}
              <div style={{ background: "rgba(255,255,255,0.03)", padding: 16, borderRadius: 10, border: "1px solid var(--border-color)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontSize: 14 }}>AI Generative Engine</span>
                  <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 4, background: "rgba(168, 85, 247, 0.15)", color: "#c084fc", fontWeight: 700 }}>
                    OPERATIONAL
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.5 }}>
                  <div><strong>Provider:</strong> Pollinations AI + Gemini Fallback</div>
                  <div><strong>Rights Policy:</strong> Labeled as AI_GENERATED (Never misrepresented)</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* ============================================================ */}
      {/* TAB 4: JOB HISTORY & AUDIT */}
      {/* ============================================================ */}
      {activeTab === "history" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="card" style={{ padding: 22, background: "var(--bg-card)", borderRadius: 14, border: "1px solid var(--border-color)" }}>
            <h3 style={{ fontSize: 18, fontWeight: 700, margin: "0 0 16px 0" }}>
              Studio Generation Job Ledger
            </h3>

            {jobsHistory.length === 0 ? (
              <div style={{ textAlign: "center", padding: "40px 20px", color: "var(--text-muted)" }}>
                <FileText size={32} style={{ opacity: 0.3, marginBottom: 8 }} />
                <p style={{ margin: 0 }}>No batch jobs recorded in this session yet.</p>
              </div>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {jobsHistory.map((job) => (
                  <div
                    key={job.job_id}
                    style={{
                      background: "rgba(255,255,255,0.02)",
                      border: "1px solid var(--border-color)",
                      borderRadius: 10,
                      padding: 14,
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center"
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                        <span style={{ fontWeight: 700, fontSize: 14 }}>{job.topic}</span>
                        <span style={{
                          fontSize: 10,
                          fontWeight: 700,
                          padding: "2px 6px",
                          borderRadius: 4,
                          background: job.status === "completed" ? "rgba(34, 197, 94, 0.15)" : (job.status === "failed" ? "rgba(239, 68, 68, 0.15)" : "rgba(99, 102, 241, 0.15)"),
                          color: job.status === "completed" ? "#4ade80" : (job.status === "failed" ? "#f87171" : "#a5b4fc")
                        }}>
                          {job.status?.toUpperCase()}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-muted)" }}>
                        Niche: {job.niche} · Quantity: {job.quantity} items · {new Date(job.created_at).toLocaleString()}
                      </div>
                    </div>

                    <div style={{ textAlign: "right", fontSize: 12 }}>
                      <div style={{ fontWeight: 600 }}>{job.items?.length || 0} Assets Generated</div>
                      <div style={{ color: "var(--text-muted)", fontSize: 11 }}>{job.current_step}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Create Google Drive Folder Modal */}
      {showFolderModal && (
        <div className="modal-backdrop">
          <div className="modal card" style={{ maxWidth: 420 }}>
            <h3 style={{ margin: "0 0 14px 0", fontSize: 17, fontWeight: 700 }}>
              Create Google Drive Folder
            </h3>
            <form onSubmit={handleCreateFolder}>
              <div style={{ marginBottom: 14 }}>
                <label style={{ fontSize: 12, fontWeight: 600, display: "block", marginBottom: 6 }}>
                  Folder Name
                </label>
                <input
                  type="text"
                  value={newFolderName}
                  onChange={(e) => setNewFolderName(e.target.value)}
                  placeholder="e.g. 2026-10-Roman_Aqueducts"
                  required
                  style={{
                    width: "100%",
                    padding: "9px 12px",
                    borderRadius: 8,
                    background: "var(--bg-input)",
                    border: "1px solid var(--border-color)",
                    color: "var(--text-main)",
                    boxSizing: "border-box"
                  }}
                />
              </div>
              <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
                <button
                  type="button"
                  className="btn btn-secondary small"
                  onClick={() => setShowFolderModal(false)}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="btn btn-primary small"
                  disabled={creatingFolder}
                >
                  {creatingFolder ? "Creating..." : "Create Folder"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
