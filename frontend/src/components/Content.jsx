import React, { useState, useEffect } from "react";
import { api, buildCaption } from "../api.js";
import PageCombo from "./PageCombo.jsx";
import { Sparkles, Send, Plus } from "lucide-react";
import { FacebookIcon } from "./BrandIcons.jsx";

// One request per screenful. A page's library can run to thousands of items,
// so the list is always fetched a window at a time — never all at once.
const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 300;

// Convert a datetime-local input value (local wall-clock) to a UTC ISO string.
function localInputToUtcIso(localValue) {
  // localValue like "2026-08-10T14:30" interpreted in the browser's local tz
  const d = new Date(localValue);
  return d.toISOString();
}

function truncate(s, n) {
  if (!s) return "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

export default function Content({ pages, activePage, setActivePage, onReload }) {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [folders, setFolders] = useState([]);
  const [sourceFolder, setSourceFolder] = useState("");
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState(null);
  const [previewCaption, setPreviewCaption] = useState("");
  const [when, setWhen] = useState("");
  const [busy, setBusy] = useState(false);
  const [postNowId, setPostNowId] = useState(null);
  const [msg, setMsg] = useState("");
  const [msgIsErr, setMsgIsErr] = useState(false);
  const [selectedIds, setSelectedIds] = useState([]);
  const [showMarkUnpostedModal, setShowMarkUnpostedModal] = useState(false);
  const [markingUnposted, setMarkingUnposted] = useState(false);

  // Create Post / Image Modal state
  const [showCreateModal, setShowCreateModal] = useState(false);
  const [createMediaType, setCreateMediaType] = useState("photo"); // "photo" | "text" | "reel"
  const [createCaption, setCreateCaption] = useState("");
  const [createTags, setCreateTags] = useState("");
  const [createImageUrl, setCreateImageUrl] = useState("");
  const [createFileName, setCreateFileName] = useState("");
  const [createWhen, setCreateWhen] = useState("");
  const [createAiPrompt, setCreateAiPrompt] = useState("");
  const [aiGenerating, setAiGenerating] = useState(false);
  const [creating, setCreating] = useState(false);

  // Multi-platform cross-posting state
  const [snapAccounts, setSnapAccounts] = useState([]);
  const [targetPlatform, setTargetPlatform] = useState("facebook"); // "facebook" | "snapchat" | "both"
  const [selectedSnapAccount, setSelectedSnapAccount] = useState("");
  const [snapPlacement, setSnapPlacement] = useState("spotlight"); // "spotlight" | "story"

  const page = pages.find((p) => p.id === activePage);

  // Load connected Snapchat accounts for cross-posting selection
  useEffect(() => {
    api.snapchatAccounts()
      .then((accs) => {
        setSnapAccounts(accs || []);
        if (accs && accs.length > 0) {
          setSelectedSnapAccount(accs[0].id);
        }
      })
      .catch(() => setSnapAccounts([]));
  }, []);

  // Debounce typing so a search runs one query, not one per keystroke. A new
  // search always restarts at the first window; both state updates land in
  // the same render, so the fetch below runs once.
  useEffect(() => {
    const t = setTimeout(() => { setSearch(searchInput); setOffset(0); }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [searchInput]);

  async function load(nextOffset = offset, nextSearch = search, nextFolder = sourceFolder) {
    if (!activePage) return;
    setLoading(true);
    setSelectedIds([]);
    try {
      const { items: rows, total: n } = await api.content(activePage, {
        limit: PAGE_SIZE,
        offset: nextOffset,
        search: nextSearch,
        sourceFolder: nextFolder,
      });
      setItems(rows);
      setTotal(n);
      // If items were removed while we sat on the last window, step back so
      // the user doesn't land on an empty page.
      if (rows.length === 0 && n > 0 && nextOffset >= n) {
        const back = Math.max(0, Math.floor((n - 1) / PAGE_SIZE) * PAGE_SIZE);
        if (back !== nextOffset) setOffset(back);
      }
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setLoading(false); }
  }

  useEffect(() => { load(offset, search, sourceFolder); }, [activePage, search, offset, sourceFolder]);

  // Folder list comes from a separate lightweight query (distinct values
  // only) so it doesn't scale with library size the way content() does.
  useEffect(() => {
    if (!activePage) { setFolders([]); return; }
    api.sourceFolders(activePage).then(setFolders).catch(() => setFolders([]));
  }, [activePage]);

  function changeFolder(f) {
    setSourceFolder(f);
    setOffset(0);
  }

  // Switching page resets the window too — done here rather than in another
  // effect so it doesn't cause a second fetch at the old offset first.
  function switchPage(id) {
    setActivePage(id);
    setOffset(0);
    setSearchInput("");
    setSearch("");
    setSourceFolder("");
    setSelected(null);
  }

  // Freezes the caption (including any random hashtag-pool draw) at the
  // moment an item is picked, so what's previewed is exactly what gets
  // stored — recomputing on every render would re-roll the pool sample.
  // (The poster rebuilds from live data at post time regardless; this is
  // only about the preview matching the row that gets written now.)
  function selectItem(it) {
    setSelected(it);
    setPreviewCaption(
      buildCaption(it.caption, it.hashtags, page?.extra_hashtags, page?.caption_footer, page?.hashtag_pool)
    );
  }

  async function scheduleIt() {
    if (!selected || !when) return;
    setBusy(true); setMsg(""); setMsgIsErr(false);
    const utcTime = localInputToUtcIso(when);
    const scheduledTo = [];
    try {
      if (targetPlatform === "facebook" || targetPlatform === "both") {
        await api.scheduleOne(selected.id, utcTime, previewCaption);
        scheduledTo.push("Facebook");
      }
      if ((targetPlatform === "snapchat" || targetPlatform === "both") && selectedSnapAccount) {
        await api.publishSnapchatDirect(selectedSnapAccount, {
          content_item_id: selected.id,
          target_placement: snapPlacement,
          caption: previewCaption,
        });
        scheduledTo.push(`Snapchat ${snapPlacement}`);
      }
      setMsg(`✓ Scheduled "${selected.file_name}" to ${scheduledTo.join(" & ")}.`);
      setSelected(null); setWhen(""); load(); onReload();
    } catch (e) { setMsg(e.message); setMsgIsErr(true); }
    finally { setBusy(false); }
  }

  async function postNow(it) {
    setPostNowId(it.id);
    setMsg("Uploading to Facebook… please wait (up to 60 seconds)");
    setMsgIsErr(false);
    try {
      await api.postNow(it.id);
      setMsg(`✓ Posted successfully to Facebook! (${it.file_name})`);
      load(); onReload();
    } catch (e) {
      setMsg(e.message);
      setMsgIsErr(true);
    } finally {
      setPostNowId(null);
    }
  }

  const postedIds = items.filter((it) => it.is_posted).map((it) => it.id);

  function toggleSelectRow(id) {
    setSelectedIds((prev) => prev.includes(id) ? prev.filter((i) => i !== id) : [...prev, id]);
  }

  function toggleSelectAll() {
    if (items.length === 0) return;
    setSelectedIds((prev) => (prev.length === items.length ? [] : items.map((it) => it.id)));
  }

  async function handleDeleteSelected() {
    if (!selectedIds.length) return;
    if (!window.confirm(`Are you sure you want to permanently delete ${selectedIds.length} item(s) from the library?`)) return;
    setBusy(true); setMsg(""); setMsgIsErr(false);
    try {
      const res = await api.deleteContent(selectedIds);
      setMsg(`✓ Successfully deleted ${res?.deleted ?? selectedIds.length} item(s).`);
      setSelectedIds([]);
      load();
      if (onReload) onReload();
    } catch (e) {
      setMsg(`Delete failed: ${e.message}`);
      setMsgIsErr(true);
    } finally {
      setBusy(false);
    }
  }

  async function confirmMarkUnposted() {
    setMarkingUnposted(true); setMsg(""); setMsgIsErr(false);
    try {
      const r = await api.markUnposted(selectedIds);
      setMsg(
        r.unmarked === 0
          ? "Nothing to mark as unposted."
          : `Marked ${r.unmarked} video${r.unmarked === 1 ? "" : "s"} as unposted.`
      );
      setSelectedIds([]);
      setShowMarkUnpostedModal(false);
      load(); onReload();
    } catch (e) { setMsg(`Mark as unposted failed: ${e.message}`); setMsgIsErr(true); }
    finally { setMarkingUnposted(false); }
  }

  async function handleAiGenerate() {
    if (!createAiPrompt.trim()) {
      setMsg("Please enter an idea or topic for AI generation.");
      setMsgIsErr(true);
      return;
    }
    setAiGenerating(true);
    try {
      const res = await api.generateAiPost(activePage, {
        prompt: createAiPrompt,
        niche: page?.name || "general",
        content_format: createMediaType === "text" ? "text" : "image_quote",
      });
      if (res.success) {
        setCreateCaption(res.body || "");
        if (res.hashtags && res.hashtags.length > 0) {
          setCreateTags(res.hashtags.join(", "));
        }
        if (!createFileName) {
          setCreateFileName(res.headline || `AI ${createMediaType.toUpperCase()} Post`);
        }
        setMsg("✓ Generated content via Google Gemini AI!");
        setMsgIsErr(false);
      } else {
        setMsg(`AI Generation error: ${res.error || "Unknown error"}`);
        setMsgIsErr(true);
      }
    } catch (e) {
      setMsg(`AI Generation failed: ${e.message}`);
      setMsgIsErr(true);
    } finally {
      setAiGenerating(false);
    }
  }

  async function handleCreatePost(postNow = false) {
    if (!activePage) return;
    setCreating(true);
    try {
      const tagsArray = createTags
        .split(",")
        .map((t) => t.trim().replace(/^#/, ""))
        .filter(Boolean);

      const payload = {
        media_type: createMediaType,
        caption: createCaption,
        hashtags: tagsArray,
        file_name: createFileName || `${createMediaType.toUpperCase()} Update ${new Date().toLocaleDateString()}`,
        image_url_or_data: createImageUrl || null,
        scheduled_time_utc: !postNow && createWhen ? localInputToUtcIso(createWhen) : null,
        post_now: postNow,
      };

      const res = await api.createContentItem(activePage, payload);
      if (res.success) {
        setMsg(postNow ? "✓ Published immediately to Facebook!" : createWhen ? "✓ Scheduled successfully!" : "✓ Saved to Content Library!");
        setMsgIsErr(false);
        setShowCreateModal(false);
        setCreateCaption("");
        setCreateTags("");
        setCreateImageUrl("");
        setCreateFileName("");
        setCreateWhen("");
        load();
        onReload();
      }
    } catch (e) {
      setMsg(`Creation failed: ${e.message}`);
      setMsgIsErr(true);
    } finally {
      setCreating(false);
    }
  }

  if (pages.length === 0)
    return (<><div className="eyebrow">MEDIA ASSETS · GOOGLE DRIVE CATALOG</div><h1 className="headline">Content Library</h1>
      <div className="empty">Connect a page first, then sync its Google Drive folder.</div></>);

  // Library-wide counts come from the page_stats view (already loaded with
  // the pages list) — not from the rows on screen, which are one window only.
  const s = page?.stats || {};
  const libTotal = s.total_content ?? 0;
  const postedCount = s.total_posted ?? 0;
  const scheduledCount = s.pending_scheduled ?? 0;
  const unposted = Math.max(0, libTotal - postedCount - scheduledCount);

  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + items.length, total);
  const hasPrev = offset > 0;
  const hasNext = offset + PAGE_SIZE < total;

  return (
    <>
      <div className="eyebrow">MEDIA ASSETS · GOOGLE DRIVE CATALOG</div>
      <h1 className="headline">Content Library</h1>
      <p className="lede">
        Central media repository synced directly from Google Drive. Review ingested sidecars,
        inspect captions, and dispatch or schedule posts with custom timing.
      </p>
      <hr className="rule" />

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
        <div style={{ width: 320 }}>
          <PageCombo pages={pages} value={activePage} onChange={switchPage} />
        </div>
        <div className="field" style={{ width: 320, marginBottom: 0 }}>
          <label>Search this library</label>
          <input
            type="search"
            value={searchInput}
            placeholder="Caption or file name…"
            onChange={(e) => setSearchInput(e.target.value)}
          />
          <div className="hint">Filtered on the server — the whole library, not just this page.</div>
        </div>
        {folders.length > 0 && (
          <div className="field" style={{ width: 220, marginBottom: 0 }}>
            <label>Source folder</label>
            <select value={sourceFolder} onChange={(e) => changeFolder(e.target.value)}>
              <option value="">All folders</option>
              {folders.map((f) => (
                <option key={f} value={f}>{f}</option>
              ))}
            </select>
          </div>
        )}
        <div style={{ marginLeft: "auto", alignSelf: "flex-end", marginBottom: 0 }}>
          <button className="btn" onClick={() => setShowCreateModal(true)}>
            + Create Image / Post
          </button>
        </div>
      </div>

      {libTotal > 0 && (
        <p className="lede" style={{ marginTop: 10, fontSize: 15 }}>
          <b>{libTotal}</b> video{libTotal === 1 ? "" : "s"} in library · <b>{unposted}</b> unposted ·{" "}
          <b>{scheduledCount}</b> scheduled · <b>{postedCount}</b> posted
        </p>
      )}
      {msg && <div className={"banner" + (msgIsErr ? " err" : "")}>{msg}</div>}

      {selectedIds.length > 0 && (
        <div className="banner" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
          <span><b>{selectedIds.length}</b> item{selectedIds.length === 1 ? "" : "s"} selected</span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn danger small" disabled={busy} onClick={handleDeleteSelected}>
              Delete Selected ({selectedIds.length})
            </button>
            {postedIds.some((id) => selectedIds.includes(id)) && (
              <button className="btn ghost small" onClick={() => setShowMarkUnpostedModal(true)}>
                Mark as Unposted
              </button>
            )}
            <button className="btn ghost small" onClick={() => setSelectedIds([])}>
              Deselect All
            </button>
          </div>
        </div>
      )}

      <div className="split" style={{ marginTop: 18 }}>
        <div>
          {loading && items.length === 0 ? (
            <div className="empty">Fetching the library…</div>
          ) : items.length === 0 ? (
            <div className="empty">
              {search
                ? <>Nothing in this library matches “{search}”.</>
                : <>Nothing synced yet. Go to Pages → Sync for this page.</>}
            </div>
          ) : (
            <>
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 36 }}>
                      <input type="checkbox"
                        checked={items.length > 0 && selectedIds.length === items.length}
                        disabled={items.length === 0}
                        onChange={toggleSelectAll}
                        title="Select all items on this page" />
                    </th>
                    <th>File & Caption</th><th>Metadata</th><th>Status</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr key={it.id}>
                      <td>
                        <input type="checkbox"
                          checked={selectedIds.includes(it.id)}
                          onChange={() => toggleSelectRow(it.id)} />
                      </td>
                      <td style={{ maxWidth: 320 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 4 }}>
                          <span style={{
                            fontSize: 10.5,
                            padding: "2px 6px",
                            borderRadius: 4,
                            fontWeight: 600,
                            background: it.media_type === "photo" ? "rgba(59, 130, 246, 0.12)" : it.media_type === "text" ? "rgba(168, 85, 247, 0.12)" : "rgba(16, 185, 129, 0.12)",
                            color: it.media_type === "photo" ? "#60a5fa" : it.media_type === "text" ? "#c084fc" : "#34d399",
                            border: `1px solid ${it.media_type === "photo" ? "rgba(59, 130, 246, 0.25)" : it.media_type === "text" ? "rgba(168, 85, 247, 0.25)" : "rgba(16, 185, 129, 0.25)"}`,
                          }}>
                            {it.media_type === "photo" ? "Photo" : it.media_type === "text" ? "Text" : "Reel"}
                          </span>
                          <span style={{ fontFamily: "var(--util)", fontSize: 12.5, fontWeight: 500 }}>{it.file_name}</span>
                        </div>
                        {it.caption ? (
                          <div style={{ color: "var(--ink-soft)", fontSize: 12, marginTop: 4, lineHeight: 1.4 }}>
                            {truncate(it.caption, 100)}
                          </div>
                        ) : (
                          <div className="err" style={{ fontStyle: "italic", fontSize: 12, marginTop: 4 }}>no caption</div>
                        )}
                      </td>
                      <td style={{ maxWidth: 260 }}>
                        {it.hashtags?.length > 0 && (
                          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                            {it.hashtags.map((h, i) => (
                              <span key={i} className="tag">{h.startsWith("#") ? h : "#" + h}</span>
                            ))}
                          </div>
                        )}
                        {it.sound_title && (
                          <div style={{ fontFamily: "var(--util)", fontSize: 11.5, color: "var(--ink-faint)", marginTop: 4 }}>
                            ♫ {it.sound_title}
                          </div>
                        )}
                      </td>
                      <td>
                        {it.is_posted ? <span className="pill posted">Posted</span>
                          : it.scheduled_time_utc ? <span className="pill pending">Scheduled</span>
                          : <span className="pill free">Unposted</span>}
                      </td>
                      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                        {!it.is_posted && (
                          <>
                            <button
                              className="btn ghost small" disabled={postNowId === it.id}
                              onClick={() => postNow(it)}
                            >
                              {postNowId === it.id ? "Uploading…" : "Post Now"}
                            </button>{" "}
                            {!it.scheduled_time_utc && (
                              <button className="btn ghost small" onClick={() => selectItem(it)}>Schedule</button>
                            )}
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="pager">
                <div className="pager-count">
                  Showing {from}–{to} of {total}
                  {search ? ` matching “${search}”` : ""}
                  {loading ? " · updating…" : ""}
                </div>
                <div className="pager-nav">
                  <button className="btn ghost small" disabled={!hasPrev || loading}
                    onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                    &lt; Previous
                  </button>
                  <span className="pager-count">
                    Page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(total / PAGE_SIZE))}
                  </span>
                  <button className="btn ghost small" disabled={!hasNext || loading}
                    onClick={() => setOffset(offset + PAGE_SIZE)}>
                    Next &gt;
                  </button>
                </div>
              </div>
            </>
          )}
        </div>

        <div className="dossier">
          {selected ? (
            <>
              <div className="name">Schedule this</div>
              <div className="kv"><span className="k">File</span><span className="v">{selected.file_name}</span></div>
              <div className="kv"><span className="k">Type</span><span className="v" style={{ textTransform: "capitalize" }}>{selected.media_type}</span></div>
              <div className="dossier-box">
                <div className="h">Full Caption</div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, marginTop: 8, lineHeight: 1.5 }}>
                  {selected.caption || <span className="err" style={{ fontStyle: "italic" }}>No caption provided</span>}
                </div>
                {selected.hashtags?.length > 0 && (
                  <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {selected.hashtags.map((h, i) => (
                      <span key={i} className="tag">{h.startsWith("#") ? h : "#" + h}</span>
                    ))}
                  </div>
                )}
                {selected.sound_title && (
                  <div style={{ fontFamily: "var(--util)", fontSize: 12, color: "var(--ink-faint)", marginTop: 8 }}>
                    ♫ {selected.sound_title}
                  </div>
                )}
              </div>
              <div className="dossier-box">
                <div className="h">Final Formatted Caption</div>
                <div style={{ whiteSpace: "pre-wrap", fontSize: 13.5, marginTop: 8, lineHeight: 1.5 }}>
                  {previewCaption || <span className="err" style={{ fontStyle: "italic" }}>Empty caption</span>}
                </div>
                <div className="hint" style={{ marginTop: 8 }}>
                  Rebuilt from the latest caption data again at post time, so a
                  later Drive sync still lands on this post.
                </div>
              </div>
              <div className="dossier-box">
                <div className="h">Target Platform</div>
                <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
                  <button
                    type="button"
                    className={`btn small ${targetPlatform === "facebook" ? "" : "ghost"}`}
                    style={{ flex: 1, padding: "6px 8px", fontSize: 12.5, display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}
                    onClick={() => setTargetPlatform("facebook")}
                  >
                    <FacebookIcon size={14} />
                    <span>Facebook</span>
                  </button>
                  {snapAccounts.length > 0 && (
                    <>
                      <button
                        type="button"
                        className={`btn small ${targetPlatform === "snapchat" ? "" : "ghost"}`}
                        style={{ flex: 1, padding: "6px 8px", fontSize: 12.5 }}
                        onClick={() => setTargetPlatform("snapchat")}
                      >
                        Snapchat
                      </button>
                      <button
                        type="button"
                        className={`btn small ${targetPlatform === "both" ? "" : "ghost"}`}
                        style={{ flex: 1, padding: "6px 8px", fontSize: 12.5 }}
                        onClick={() => setTargetPlatform("both")}
                      >
                        Both Platforms
                      </button>
                    </>
                  )}
                </div>

                {(targetPlatform === "snapchat" || targetPlatform === "both") && snapAccounts.length > 0 && (
                  <div style={{ marginTop: 12, padding: "10px", background: "var(--card)", borderRadius: 6, border: "1px solid var(--line)" }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-soft)", marginBottom: 4 }}>Snapchat Account & Placement</div>
                    <div style={{ display: "flex", gap: 8 }}>
                      <select
                        style={{ flex: 1, fontSize: 12.5 }}
                        value={selectedSnapAccount}
                        onChange={(e) => setSelectedSnapAccount(e.target.value)}
                      >
                        {snapAccounts.map((acc) => (
                          <option key={acc.id} value={acc.id}>
                            @{acc.snapchat_username} ({acc.display_name})
                          </option>
                        ))}
                      </select>
                      <select
                        style={{ width: 110, fontSize: 12.5 }}
                        value={snapPlacement}
                        onChange={(e) => setSnapPlacement(e.target.value)}
                      >
                        <option value="spotlight">Spotlight</option>
                        <option value="story">Story</option>
                      </select>
                    </div>
                  </div>
                )}
              </div>

              <div className="dossier-box">
                <div className="h">Post time — you choose</div>
                <div className="field" style={{ marginTop: 8 }}>
                  <input type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} />
                  <div className="hint">Pick a time in IST (UTC+5:30) — at least 11 minutes from now.</div>
                </div>
                <button className="btn" disabled={busy || !when} onClick={scheduleIt} style={{ width: "100%", justifyContent: "center" }}>
                  {busy ? "Scheduling…" : `Add to schedule (${targetPlatform === "both" ? "FB + Snap" : targetPlatform})`}
                </button>
                <button className="btn ghost small" onClick={() => setSelected(null)} style={{ width: "100%", justifyContent: "center", marginTop: 8 }}>
                  Cancel
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="name">The dossier</div>
              <p className="lede" style={{ fontSize: 14, marginTop: 10 }}>
                Select an unposted item's <b>Schedule</b> button to set its post
                time. Posted and already-scheduled items are locked so nothing
                doubles up.
              </p>
            </>
          )}
        </div>
      </div>

      {showMarkUnpostedModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowMarkUnpostedModal(false)}>
          <div className="modal" style={{ width: "min(440px, 92vw)" }}>
            <h2>Mark as Unposted</h2>
            <p style={{ marginTop: 8, fontSize: 14, lineHeight: 1.5 }}>
              Reset <b>{selectedIds.length}</b> video{selectedIds.length === 1 ? "" : "s"} back to unposted? Their posting history is removed so they can be scheduled or posted again.
            </p>
            <div className="modal-actions" style={{ marginTop: 20 }}>
              <button className="btn ghost" onClick={() => setShowMarkUnpostedModal(false)}>Cancel</button>
              <button className="btn" style={{ background: "#c0392b", color: "#fff", borderColor: "transparent" }}
                disabled={markingUnposted} onClick={confirmMarkUnposted}>
                {markingUnposted ? "Marking…" : "Yes, mark as unposted"}
              </button>
            </div>
          </div>
        </div>
      )}

      {showCreateModal && (
        <div className="overlay" onClick={(e) => e.target.className === "overlay" && setShowCreateModal(false)}>
          <div className="modal" style={{ width: "min(580px, 94vw)", maxHeight: "90vh", overflowY: "auto" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
              <div>
                <div className="eyebrow">FACEBOOK PUBLISHING · MULTI-FORMAT CREATOR</div>
                <h2 style={{ margin: "4px 0 0" }}>Create Content / Image</h2>
                <div style={{ fontSize: 13, color: "var(--ink-soft)", marginTop: 2 }}>
                  Publishing to <b>{page?.name || "Selected Page"}</b>
                </div>
              </div>
              <button className="btn ghost small" onClick={() => setShowCreateModal(false)}>✕</button>
            </div>

            {/* Media Format Picker */}
            <div className="field">
              <label>Post Format</label>
              <div style={{ display: "flex", gap: 8 }}>
                {[
                  { id: "photo", label: "Photo / Image" },
                  { id: "text", label: "Text Status Update" },
                  { id: "reel", label: "Reel / Short Clip" },
                ].map((fmt) => (
                  <button
                    key={fmt.id}
                    type="button"
                    className={"btn small " + (createMediaType === fmt.id ? "" : "ghost")}
                    style={createMediaType === fmt.id ? { background: "var(--accent)", color: "#fff" } : {}}
                    onClick={() => setCreateMediaType(fmt.id)}
                  >
                    {fmt.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Gemini AI Content Assistant */}
            <div style={{
              background: "rgba(99, 102, 241, 0.07)",
              border: "1px solid rgba(99, 102, 241, 0.22)",
              borderRadius: 8,
              padding: "12px 14px",
              marginBottom: 16,
            }}>
              <div style={{ fontWeight: 600, fontSize: 13, color: "var(--accent-light)", display: "flex", alignItems: "center", gap: 6, marginBottom: 8 }}>
                <Sparkles size={14} />
                <span>Google Gemini AI Content Assistant</span>
                <span style={{ fontSize: 11, color: "var(--text-muted)", fontWeight: 400 }}>(Structured Engine)</span>
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <input
                  type="text"
                  placeholder="Enter topic, headline idea, or niche prompt…"
                  value={createAiPrompt}
                  onChange={(e) => setCreateAiPrompt(e.target.value)}
                  style={{ flex: 1, fontSize: 13 }}
                  onKeyDown={(e) => e.key === "Enter" && handleAiGenerate()}
                />
                <button
                  type="button"
                  className="btn small"
                  disabled={aiGenerating}
                  onClick={handleAiGenerate}
                  style={{ whiteSpace: "nowrap", display: "inline-flex", alignItems: "center", gap: 6 }}
                >
                  <Sparkles size={13} />
                  <span>{aiGenerating ? "Generating…" : "Generate Post"}</span>
                </button>
              </div>
            </div>

            {/* Photo / Media Details */}
            {createMediaType === "photo" && (
              <div className="field">
                <label>Image URL or Base64 Data</label>
                <input
                  type="text"
                  placeholder="https://example.com/image.jpg or data:image/png;base64,..."
                  value={createImageUrl}
                  onChange={(e) => setCreateImageUrl(e.target.value)}
                />
                <div className="hint">Direct image URL to publish to the Facebook Page feed.</div>
              </div>
            )}

            <div className="field">
              <label>Post Title / Reference</label>
              <input
                type="text"
                placeholder="e.g. Morning Motivation Quote"
                value={createFileName}
                onChange={(e) => setCreateFileName(e.target.value)}
              />
            </div>

            <div className="field">
              <label>Caption / Post Message</label>
              <textarea
                rows={4}
                placeholder="Write your Facebook status or image caption here..."
                value={createCaption}
                onChange={(e) => setCreateCaption(e.target.value)}
                style={{ resize: "vertical" }}
              />
            </div>

            <div className="field">
              <label>Hashtags (comma-separated)</label>
              <input
                type="text"
                placeholder="reels, viral, motivation, business"
                value={createTags}
                onChange={(e) => setCreateTags(e.target.value)}
              />
            </div>

            <div className="field">
              <label>Schedule for Later (optional — leave blank to save or post now)</label>
              <input
                type="datetime-local"
                value={createWhen}
                onChange={(e) => setCreateWhen(e.target.value)}
              />
              <div className="hint">Collision buffer automatically prevents overlap with your Drive reels.</div>
            </div>

            <div className="modal-actions" style={{ marginTop: 24, display: "flex", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
              <button type="button" className="btn ghost" onClick={() => setShowCreateModal(false)}>
                Cancel
              </button>
              <div style={{ display: "flex", gap: 8 }}>
                <button
                  type="button"
                  className="btn ghost"
                  disabled={creating}
                  onClick={() => handleCreatePost(false)}
                >
                  {createWhen ? "Schedule Post" : "Save to Library"}
                </button>
                <button
                  type="button"
                  className="btn"
                  style={{ background: "#2f7d54", color: "#fff", borderColor: "transparent", display: "inline-flex", alignItems: "center", gap: 6 }}
                  disabled={creating}
                  onClick={() => handleCreatePost(true)}
                >
                  <Send size={14} />
                  <span>{creating ? "Publishing…" : "Post to Facebook Now"}</span>
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
