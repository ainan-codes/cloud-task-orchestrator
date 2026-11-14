import React, { useState, useRef, useEffect } from "react";

/**
 * PageCombo — a searchable page picker.
 *
 * A plain <select> stops being usable somewhere past a few dozen pages: you
 * can't see what you're looking for and you can't type more than the first
 * letter to get there. This is an input + filtered list instead — type to
 * narrow, ↑/↓ to move, Enter to pick, Esc to close. No new dependency.
 *
 * Props: pages, value (page id), onChange(id), label, hint
 */
export default function PageCombo({ pages, value, onChange, label = "Page", hint }) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [highlight, setHighlight] = useState(0);
  const wrapRef = useRef(null);
  const inputRef = useRef(null);

  const selected = pages.find((p) => p.id === value);

  // While closed the input shows the selected page's name; while open it
  // shows whatever is being typed, so the whole list is reachable.
  const shown = open ? query : selected?.name || "";

  const q = query.trim().toLowerCase();
  const filtered = !open || !q
    ? pages
    : pages.filter(
        (p) =>
          p.name?.toLowerCase().includes(q) ||
          String(p.fb_page_id || "").toLowerCase().includes(q)
      );

  // Close on any click outside the widget.
  useEffect(() => {
    function onDocDown(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocDown);
    return () => document.removeEventListener("mousedown", onDocDown);
  }, []);

  useEffect(() => { setHighlight(0); }, [query, open]);

  function pick(p) {
    onChange(p.id);
    setOpen(false);
    setQuery("");
    inputRef.current?.blur();
  }

  function onKeyDown(e) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) { setOpen(true); return; }
      setHighlight((h) => {
        const next = e.key === "ArrowDown" ? h + 1 : h - 1;
        if (filtered.length === 0) return 0;
        return (next + filtered.length) % filtered.length;
      });
    } else if (e.key === "Enter") {
      if (open && filtered[highlight]) { e.preventDefault(); pick(filtered[highlight]); }
    } else if (e.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  }

  return (
    <div className="field combo" ref={wrapRef} style={{ marginBottom: 0 }}>
      {label && <label>{label}</label>}
      <input
        ref={inputRef}
        type="text"
        role="combobox"
        aria-expanded={open}
        autoComplete="off"
        value={shown}
        placeholder={pages.length ? "Type to search pages…" : "No pages yet"}
        onFocus={() => setOpen(true)}
        onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
        onKeyDown={onKeyDown}
      />
      {open && (
        <div className="combo-list" role="listbox">
          {filtered.length === 0 ? (
            <div className="combo-empty">No page matches “{query.trim()}”</div>
          ) : (
            filtered.map((p, i) => (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={p.id === value}
                className={
                  "combo-opt" +
                  (i === highlight ? " hl" : "") +
                  (p.id === value ? " sel" : "")
                }
                onMouseEnter={() => setHighlight(i)}
                onClick={() => pick(p)}
              >
                <span className="combo-opt-name">{p.name}</span>
                <span className="combo-opt-meta">
                  {(p.stats?.total_content ?? p.total_content ?? 0)} in library
                </span>
              </button>
            ))
          )}
        </div>
      )}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
