// Reel Ledger Core App (Lifecycle Verified: Stable)
import React, { useState, useEffect } from "react";
import { api } from "./api.js";
import Overview from "./components/Overview.jsx";
import Pages from "./components/Pages.jsx";
import Snapchat from "./components/Snapchat.jsx";
import YouTube from "./components/YouTube.jsx";
import Rumble from "./components/Rumble.jsx";
import Dailymotion from "./components/Dailymotion.jsx";
import Content from "./components/Content.jsx";
import Schedule from "./components/Schedule.jsx";
import Analytics from "./components/Analytics.jsx";
import FlowMachine from "./components/FlowMachine.jsx";
import UniversalStudio from "./components/UniversalStudio.jsx";
import Login, { isAuthed, signOut } from "./components/Login.jsx";
import AiConfigModal from "./components/AiConfigModal.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import {
  FacebookIcon,
  SnapchatIcon,
  YouTubeIcon,
  RumbleIcon,
  DailymotionIcon,
} from "./components/BrandIcons.jsx";

import {
  LayoutDashboard,
  Layers,
  Film,
  Calendar,
  BarChart3,
  Send,
  Users,
  ListFilter,
  CheckCircle,
  Sliders,
  LogOut,
  Video,
  Cpu,
  Sparkles,
} from "lucide-react";

const FB_NAV = [
  { id: "overview", Icon: LayoutDashboard, label: "Overview" },
  { id: "pages", Icon: Layers, label: "Pages" },
  { id: "studio", Icon: Sparkles, label: "Content Studio" },
  { id: "content", Icon: Film, label: "Content Library" },
  { id: "schedule", Icon: Calendar, label: "Publish Queue" },
  { id: "analytics", Icon: BarChart3, label: "Analytics" },
];

const SNAP_NAV = [
  { sub: "publish", Icon: Send, label: "Compose & Schedule" },
  { sub: "profiles", Icon: Users, label: "Public Profiles" },
  { sub: "queue", Icon: ListFilter, label: "Publishing Queue" },
  { sub: "history", Icon: CheckCircle, label: "Published Ledger" },
  { sub: "settings", Icon: Sliders, label: "API Configuration" },
];

const YT_NAV = [
  { sub: "publish", Icon: Send, label: "Compose & Schedule" },
  { sub: "channels", Icon: Video, label: "YouTube Channels" },
  { sub: "queue", Icon: ListFilter, label: "Publishing Queue" },
  { sub: "history", Icon: CheckCircle, label: "Published Ledger" },
  { sub: "settings", Icon: Sliders, label: "API Configuration" },
];

const RUMBLE_NAV = [
  { sub: "publish", Icon: Send, label: "Compose & Schedule" },
  { sub: "accounts", Icon: Users, label: "Rumble Accounts" },
  { sub: "queue", Icon: ListFilter, label: "Publishing Queue" },
  { sub: "history", Icon: CheckCircle, label: "Published Ledger" },
  { sub: "automation", Icon: Sliders, label: "Automation & Setup" },
];

const DM_NAV = [
  { sub: "publish", Icon: Send, label: "Compose & Schedule" },
  { sub: "channels", Icon: Video, label: "Channels & Monetization" },
  { sub: "queue", Icon: ListFilter, label: "Publishing Queue" },
  { sub: "history", Icon: CheckCircle, label: "Published Ledger" },
  { sub: "monetization", Icon: Sliders, label: "Partner Ad Revenue" },
];

const ALL_VIEWS = ["overview", "pages", "content", "studio", "flow", "schedule", "analytics", "snapchat", "youtube", "rumble", "dailymotion"];
const isView = (id) => ALL_VIEWS.includes(id);

function platformFromView(v) {
  if (v === "snapchat") return "snapchat";
  if (v === "youtube") return "youtube";
  if (v === "rumble") return "rumble";
  if (v === "dailymotion") return "dailymotion";
  if (v === "flow") return "flow";
  return "facebook";
}

/**
 * Which view the current URL asks for.
 */
function viewFromLocation() {
  try {
    const path = window.location.pathname.replace(/^\/+|\/+$/g, "");
    if (isView(path)) return path;
    const wanted = new URLSearchParams(window.location.search).get("tab");
    if (isView(wanted)) return wanted;
  } catch {
    /* no window.location in a non-browser render */
  }
  return "overview";
}

function subFromLocation() {
  try {
    const params = new URLSearchParams(window.location.search);
    const sub = params.get("sub") || params.get("snap_tab") || params.get("section");
    if (sub) return sub;
  } catch {}
  return "publish";
}

export default function App() {
  const [authed, setAuthed] = useState(isAuthed);
  const [view, setViewState] = useState(viewFromLocation);
  const [platform, setPlatform] = useState(() => platformFromView(viewFromLocation()));
  const [snapSubTab, setSnapSubTab] = useState(subFromLocation);
  const [ytSubTab, setYtSubTab] = useState(subFromLocation);
  const [rumbleSubTab, setRumbleSubTab] = useState(subFromLocation);
  const [dmSubTab, setDmSubTab] = useState(subFromLocation);
  const [showAiModal, setShowAiModal] = useState(false);
  const [theme, setTheme] = useState(() => localStorage.getItem("reel_ledger_theme") || "dark");
  const [colorCombo, setColorCombo] = useState(() => localStorage.getItem("reel_ledger_combo") || "slate");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    document.documentElement.setAttribute("data-color-combo", colorCombo);
  }, [theme, colorCombo]);

  /** Change section and put it in the address bar, so Back works. */
  const setView = React.useCallback((id) => {
    setViewState(id);
    setPlatform((prev) => {
      if (id === "studio") {
        return prev === "flow" ? "flow" : "facebook";
      }
      return platformFromView(id);
    });
    try {
      const path = id === "overview" ? "/" : `/${id}`;
      if (window.location.pathname !== path) {
        window.history.pushState({ view: id }, "", path);
      }
    } catch {
      /* history unavailable — the view still changes */
    }
  }, []);

  // Browser back/forward move between sections.
  useEffect(() => {
    const onPop = () => {
      const v = viewFromLocation();
      setViewState(v);
      setPlatform(platformFromView(v));
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const [pages, setPages] = useState([]);
  const [activePage, setActivePage] = useState(null);
  const [openAddPage, setOpenAddPage] = useState(false);
  const [openGuide, setOpenGuide] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function loadPages() {
    setLoading(true);
    setError("");
    try {
      const p = await api.pages();
      setPages(p);
      if (!activePage && p.length) setActivePage(p[0].id);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (authed) loadPages();
  }, [authed]);

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "short", month: "short", day: "numeric",
  }).toUpperCase();

  if (!authed) return <Login onDone={() => setAuthed(true)} />;

  return (
    <div className="app">
      <aside className="rail">
        <div className="rail-head">
          <div className="brand-logo-wrap">
            <Film size={18} className="brand-icon" />
          </div>
          <div>
            <div className="rail-title">Reel Ledger</div>
            <div className="rail-vol">Multi-Platform Dispatch</div>
          </div>
        </div>

        {/* Platform Switcher */}
        <div className="platform-selector">
          <button
            type="button"
            className={"platform-btn fb" + (platform === "facebook" ? " active" : "")}
            onClick={() => {
              setPlatform("facebook");
              if (["snapchat", "youtube", "rumble", "dailymotion"].includes(view)) setView("overview");
            }}
          >
            <div className="platform-icon-wrap">
              <FacebookIcon size={18} />
            </div>
            <span className="p-label">Facebook</span>
            {platform === "facebook" && <span className="p-active-dot" />}
          </button>
          <button
            type="button"
            className={"platform-btn snap" + (platform === "snapchat" ? " active" : "")}
            onClick={() => {
              setPlatform("snapchat");
              setView("snapchat");
            }}
          >
            <div className="platform-icon-wrap">
              <SnapchatIcon size={18} />
            </div>
            <span className="p-label">Snapchat</span>
            {platform === "snapchat" && <span className="p-active-dot" />}
          </button>
          <button
            type="button"
            className={"platform-btn yt" + (platform === "youtube" ? " active" : "")}
            onClick={() => {
              setPlatform("youtube");
              setView("youtube");
            }}
          >
            <div className="platform-icon-wrap">
              <YouTubeIcon size={18} />
            </div>
            <span className="p-label">YouTube</span>
            {platform === "youtube" && <span className="p-active-dot" />}
          </button>
          <button
            type="button"
            className={"platform-btn rumble" + (platform === "rumble" ? " active" : "")}
            onClick={() => {
              setPlatform("rumble");
              setView("rumble");
            }}
          >
            <div className="platform-icon-wrap">
              <RumbleIcon size={18} />
            </div>
            <span className="p-label">Rumble</span>
            {platform === "rumble" && <span className="p-active-dot" />}
          </button>
          <button
            type="button"
            className={"platform-btn dm" + (platform === "dailymotion" ? " active" : "")}
            onClick={() => {
              setPlatform("dailymotion");
              setView("dailymotion");
            }}
          >
            <div className="platform-icon-wrap">
              <DailymotionIcon size={18} />
            </div>
            <span className="p-label">Dailymotion</span>
            {platform === "dailymotion" && <span className="p-active-dot" />}
          </button>
          <button
            type="button"
            className={"platform-btn flow" + ((platform === "flow" || view === "flow") ? " active" : "")}
            onClick={() => {
              setPlatform("flow");
              setView("flow");
            }}
          >
            <div className="platform-icon-wrap" style={{ color: "#34d399" }}>
              <Sparkles size={18} />
            </div>
            <span className="p-label" style={{ fontWeight: 600 }}>Content Machine</span>
            {(platform === "flow" || view === "flow") && <span className="p-active-dot" style={{ background: "#34d399" }} />}
          </button>
        </div>

        {platform === "flow" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>Google Flow Studio</span>
              <span className="nav-section-badge flow">Omni Flash</span>
            </div>
            <nav className="nav">
              <button
                className={"nav-item" + (view === "studio" ? " active" : "")}
                onClick={() => setView("studio")}
              >
                <Sparkles size={16} className="nav-icon" />
                <span>Universal Studio</span>
              </button>
              <button
                className={"nav-item" + (view === "flow" ? " active" : "")}
                onClick={() => setView("flow")}
              >
                <Cpu size={16} className="nav-icon" />
                <span>Google Flow Machine</span>
              </button>
              <button
                className={"nav-item" + (view === "content" ? " active" : "")}
                onClick={() => setView("content")}
              >
                <Film size={16} className="nav-icon" />
                <span>Content Library</span>
              </button>
              <button
                className={"nav-item" + (view === "pages" ? " active" : "")}
                onClick={() => setView("pages")}
              >
                <Layers size={16} className="nav-icon" />
                <span>Drive & Pages</span>
              </button>
            </nav>
          </div>
        )}

        {platform === "facebook" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>Facebook Reels</span>
              <span className="nav-section-badge fb">Meta</span>
            </div>
            <nav className="nav">
              {FB_NAV.map((n) => (
                <button
                  key={n.id}
                  className={"nav-item" + (view === n.id ? " active" : "")}
                  onClick={() => setView(n.id)}
                >
                  <n.Icon size={16} className="nav-icon" />
                  <span>{n.label}</span>
                </button>
              ))}
            </nav>
          </div>
        )}

        {platform === "snapchat" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>Snapchat Spotlight</span>
              <span className="nav-section-badge snap">Snap Kit</span>
            </div>
            <nav className="nav">
              {SNAP_NAV.map((n) => (
                <button
                  key={n.sub}
                  className={"nav-item" + (view === "snapchat" && snapSubTab === n.sub ? " active" : "")}
                  onClick={() => {
                    setView("snapchat");
                    setSnapSubTab(n.sub);
                  }}
                >
                  <n.Icon size={16} className="nav-icon" />
                  <span>{n.label}</span>
                </button>
              ))}
            </nav>
          </div>
        )}

        {platform === "youtube" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>YouTube Shorts</span>
              <span className="nav-section-badge yt">Data API</span>
            </div>
            <nav className="nav">
              {YT_NAV.map((n) => (
                <button
                  key={n.sub}
                  className={"nav-item" + (view === "youtube" && ytSubTab === n.sub ? " active" : "")}
                  onClick={() => {
                    setView("youtube");
                    setYtSubTab(n.sub);
                  }}
                >
                  <n.Icon size={16} className="nav-icon" />
                  <span>{n.label}</span>
                </button>
              ))}
            </nav>
          </div>
        )}

        {platform === "rumble" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>Rumble Studio</span>
              <span className="nav-section-badge rumble">Portal</span>
            </div>
            <nav className="nav">
              {RUMBLE_NAV.map((n) => (
                <button
                  key={n.sub}
                  className={"nav-item" + (view === "rumble" && rumbleSubTab === n.sub ? " active" : "")}
                  onClick={() => {
                    setView("rumble");
                    setRumbleSubTab(n.sub);
                  }}
                >
                  <n.Icon size={16} className="nav-icon" />
                  <span>{n.label}</span>
                </button>
              ))}
            </nav>
          </div>
        )}

        {platform === "dailymotion" && (
          <div className="nav-group">
            <div className="nav-section-title">
              <span>Dailymotion</span>
              <span className="nav-section-badge dm">Partner</span>
            </div>
            <nav className="nav">
              {DM_NAV.map((n) => (
                <button
                  key={n.sub}
                  className={"nav-item" + (view === "dailymotion" && dmSubTab === n.sub ? " active" : "")}
                  onClick={() => {
                    setView("dailymotion");
                    setDmSubTab(n.sub);
                  }}
                >
                  <n.Icon size={16} className="nav-icon" />
                  <span>{n.label}</span>
                </button>
              ))}
            </nav>
          </div>
        )}

        <div className="rail-foot">
          <ThemeToggle
            theme={theme}
            setTheme={setTheme}
            colorCombo={colorCombo}
            setColorCombo={setColorCombo}
          />

          <button
            type="button"
            className="btn btn-secondary small"
            style={{ width: "100%", marginBottom: 12, display: "flex", alignItems: "center", justifyContent: "center", gap: 7, fontSize: 12, fontWeight: 600 }}
            onClick={() => setShowAiModal(true)}
          >
            <Cpu size={14} style={{ color: "var(--accent-light)" }} />
            <span>AI Engine & Keys</span>
          </button>

          <div className="rail-user">
            <div className="avatar-initials">AL</div>
            <div className="rail-user-meta">
              <div className="who">ainan@admin.com</div>
              <div className="sub">
                <span className="status-dot green" />
                <span>Online · Local Engine</span>
              </div>
            </div>
            <button
              className="signout-icon-btn"
              title="Sign out of Ledger"
              onClick={() => { signOut(); setAuthed(false); }}
            >
              <LogOut size={15} />
            </button>
          </div>
        </div>
      </aside>

      <AiConfigModal isOpen={showAiModal} onClose={() => setShowAiModal(false)} />

      <main className="main">
        {error && <div className="banner err">Couldn't reach the backend: {error}</div>}

        {view === "overview" && (
          <Overview
            pages={pages}
            loading={loading}
            onGo={setView}
            onReload={loadPages}
            onAddPage={() => {
              setView("pages");
              setOpenAddPage(true);
            }}
            onOpenGuide={() => {
              setView("pages");
              setOpenGuide(true);
            }}
          />
        )}
        {view === "pages" && (
          <Pages
            pages={pages}
            onReload={loadPages}
            initialShowForm={openAddPage}
            onFormClosed={() => setOpenAddPage(false)}
            initialShowGuide={openGuide}
            onGuideClosed={() => setOpenGuide(false)}
          />
        )}
        {view === "snapchat" && (
          <Snapchat subTab={snapSubTab} onSelectSubTab={setSnapSubTab} />
        )}
        {view === "youtube" && (
          <YouTube subTab={ytSubTab} onSelectSubTab={setYtSubTab} />
        )}
        {view === "rumble" && (
          <Rumble subTab={rumbleSubTab} onSelectSubTab={setRumbleSubTab} />
        )}
        {view === "dailymotion" && (
          <Dailymotion subTab={dmSubTab} onSelectSubTab={setDmSubTab} />
        )}
        {view === "content" && (
          <Content pages={pages} activePage={activePage} setActivePage={setActivePage} onReload={loadPages} />
        )}
        {view === "studio" && (
          <UniversalStudio pages={pages} activePage={activePage} setActivePage={setActivePage} />
        )}
        {view === "flow" && (
          <FlowMachine pages={pages} activePage={activePage} />
        )}
        {view === "schedule" && (
          <Schedule pages={pages} activePage={activePage} setActivePage={setActivePage} onReload={loadPages} />
        )}
        {view === "analytics" && (
          <Analytics pages={pages} activePage={activePage} setActivePage={setActivePage} />
        )}
      </main>
    </div>
  );
}
