import React, { useState } from "react";
import { Film } from "lucide-react";

const EMAIL = "ainan@admin.com";
const PASSWORD = "qwerty";
const KEY = "reel-ledger-auth";

export function isAuthed() {
  return localStorage.getItem(KEY) === "1";
}

export function signOut() {
  localStorage.removeItem(KEY);
}

export default function Login({ onDone }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");

  function submit(e) {
    e.preventDefault();
    if (email.trim().toLowerCase() === EMAIL && password === PASSWORD) {
      localStorage.setItem(KEY, "1");
      onDone();
    } else {
      setError("Invalid credentials. Please verify your email and password.");
    }
  }

  function fillDemo() {
    setEmail(EMAIL);
    setPassword(PASSWORD);
    setError("");
  }

  const today = new Date().toLocaleDateString("en-US", {
    weekday: "long", month: "short", day: "numeric", year: "numeric",
  }).toUpperCase();

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <div style={{ textAlign: "center", marginBottom: 14 }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 44,
            height: 44,
            borderRadius: 10,
            background: "var(--accent)",
            color: "#fff",
            marginBottom: 12,
          }}>
            <Film size={22} />
          </div>
          <h1 className="gate-title">Reel Ledger</h1>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
            Multi-Platform Distribution Control Room
          </div>
        </div>

        <div className="gate-date">
          <span>LIVE EDITIONS</span>
          <span>{today}</span>
        </div>

        {error && <div className="banner err">{error}</div>}

        <div className="field">
          <label>Admin Email</label>
          <input
            type="email"
            value={email}
            autoFocus
            autoComplete="username"
            placeholder="ainan@admin.com"
            onChange={(e) => { setEmail(e.target.value); setError(""); }}
          />
        </div>

        <div className="field">
          <label>Password</label>
          <input
            type="password"
            value={password}
            autoComplete="current-password"
            placeholder="••••••••"
            onChange={(e) => { setPassword(e.target.value); setError(""); }}
          />
        </div>

        <button className="btn gate-btn" type="submit">
          <span>Enter Studio Dashboard</span>
          <span>→</span>
        </button>

        {/* Quick Fill Helper for Easy Access */}
        <div style={{
          marginTop: 18,
          padding: "10px 12px",
          background: "rgba(255, 255, 255, 0.03)",
          border: "1px dashed var(--border-medium)",
          borderRadius: 8,
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          fontSize: 12,
          color: "var(--text-muted)",
        }}>
          <div>
            <span>Default: </span>
            <strong style={{ color: "var(--accent-light)" }}>ainan@admin.com</strong>
          </div>
          <button
            type="button"
            onClick={fillDemo}
            style={{
              background: "rgba(99, 102, 241, 0.15)",
              border: "1px solid rgba(99, 102, 241, 0.3)",
              color: "var(--accent-light)",
              padding: "4px 8px",
              borderRadius: 5,
              fontSize: 11,
              fontWeight: 600,
            }}
          >
            Auto-fill
          </button>
        </div>

        <div className="gate-foot">
          End-to-End Encrypted Session · Meta · Snap Kit · YouTube · Rumble
        </div>
      </form>
    </div>
  );
}
