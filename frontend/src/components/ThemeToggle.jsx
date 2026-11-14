import React from "react";
import { Sun, Moon, Palette, Check } from "lucide-react";

export default function ThemeToggle({ theme, setTheme, colorCombo, setColorCombo }) {
  const isLight = theme === "light";

  const toggleTheme = (newTheme) => {
    setTheme(newTheme);
    localStorage.setItem("reel_ledger_theme", newTheme);
    document.documentElement.setAttribute("data-theme", newTheme);
  };

  const selectCombo = (combo) => {
    setColorCombo(combo);
    localStorage.setItem("reel_ledger_combo", combo);
    document.documentElement.setAttribute("data-color-combo", combo);
  };

  return (
    <div className="theme-toggle-container">
      <div className="theme-toggle-bar">
        <button
          type="button"
          className={"theme-mode-btn" + (!isLight ? " active" : "")}
          onClick={() => toggleTheme("dark")}
          title="Switch to Dark Mode"
        >
          <Moon size={13} />
          <span>Dark</span>
        </button>

        <button
          type="button"
          className={"theme-mode-btn" + (isLight ? " active" : "")}
          onClick={() => toggleTheme("light")}
          title="Switch to Light Mode"
        >
          <Sun size={13} />
          <span>Light</span>
        </button>
      </div>

      {isLight && (
        <div className="combo-picker">
          <div className="combo-picker-header">
            <Palette size={11} className="combo-picker-icon" />
            <span className="combo-picker-label">Color Combination</span>
          </div>

          <div className="combo-pills">
            <button
              type="button"
              className={"combo-pill" + (colorCombo === "slate" ? " active" : "")}
              onClick={() => selectCombo("slate")}
              title="Combination 1: Clean Slate & Indigo"
            >
              <span className="color-swatch slate-swatch" />
              <span className="combo-name">Slate · Indigo</span>
              {colorCombo === "slate" && <Check size={11} className="combo-check" />}
            </button>

            <button
              type="button"
              className={"combo-pill" + (colorCombo === "warm" ? " active" : "")}
              onClick={() => selectCombo("warm")}
              title="Combination 2: Warm Sand & Emerald"
            >
              <span className="color-swatch warm-swatch" />
              <span className="combo-name">Sand · Emerald</span>
              {colorCombo === "warm" && <Check size={11} className="combo-check" />}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
