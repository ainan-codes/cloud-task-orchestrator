---
name: design-system
description: >-
  Enterprise UI/UX design guidelines for building human-crafted, high-taste, professional
  dashboards without the generic, saturated 'AI-generated' appearance.
---

# Human-Crafted Design System Guidelines

This skill enforces high-taste, enterprise-grade UI/UX design principles to prevent interfaces from looking generic, over-saturated, or unmistakably "AI-generated".

## 1. The "Anti-AI" Aesthetics Rules

Avoid the following telltale clichés that make applications look like cheap AI templates:
- **NO Generic Emojis**: Never use OS emojis (`📊, 📑, 🎬, 🗓️, 📈, 📘, 👻, 🔴, 🟢`) for interface icons or button labels. Always use consistent vector icons (e.g. `lucide-react`).
- **NO Screaming Neon Halos**: Avoid bright cyan/blue radial glowing top-borders on cards or oversaturated drop shadows. Use restrained 1px borders (`#27272a`) with subtle 0-1px shadows.
- **NO Cavernous Pitch-Black Voids**: Do not use `#000000` or raw navy blue. Use calibrated dark neutrals (Zinc/Slate: `#09090b` base, `#121215` surface, `#18181b` card).
- **NO Centered Unstyled Empty Text**: Empty states must feature a subtle icon badge, a distinct headline, clear instructions, and an actionable button.

## 2. Color Palette (Zinc & Precision Accents)

| Token | Hex Value | Usage |
| :--- | :--- | :--- |
| `background` | `#09090b` (Zinc-950) | Main application viewport backdrop |
| `surface` | `#111114` (Zinc-900) | Sidebar, top header, secondary panels |
| `card` | `#18181b` (Zinc-800) | Elevated content cards, tables, modals |
| `card-hover` | `#202024` | Interactive card and list-item hover state |
| `border-subtle` | `rgba(255, 255, 255, 0.08)` | Standard dividers, card borders |
| `border-strong` | `rgba(255, 255, 255, 0.16)` | Focused inputs, active tabs, modal borders |
| `text-primary` | `#f4f4f5` (Zinc-100) | Main headlines, metric values, primary labels |
| `text-secondary`| `#a1a1aa` (Zinc-400) | Subtitles, table cells, secondary actions |
| `text-muted` | `#71717a` (Zinc-500) | Metadata, timestamps, helper text |
| `accent-primary`| `#6366f1` (Indigo-500) | Primary CTA buttons, key active indicators |

### Platform Badges
- **Facebook**: `#1877f2` (with `rgba(24, 119, 242, 0.12)` background)
- **Snapchat**: `#eab308` (with `rgba(234, 179, 8, 0.12)` background)
- **YouTube**: `#ef4444` (with `rgba(239, 68, 68, 0.12)` background)
- **Rumble**: `#84cc16` (with `rgba(132, 204, 22, 0.12)` background)

## 3. Typography Hierarchy
- **Font Stack**: `Plus Jakarta Sans` or `Inter` for all UI text, `JetBrains Mono` for tabular metrics and timestamps.
- **Page Titles**: 22–26px, weight 700, letter-spacing `-0.02em`.
- **Card Headers**: 14–15px, weight 600.
- **Metric Numbers**: 28–34px, tabular numbers, weight 700.
- **Section Headers**: 12px uppercase, letter-spacing `0.06em`, muted.
- **Body & Controls**: 13–14px, weight 500.

## 4. Component Standards

### Metric / KPI Cards
- Compact height (100–120px).
- Label and vector icon in top row with muted color.
- Number in crisp white tabular font.
- Subtitle providing context or trend.
- Clean 1px border without neon strips.

### Navigation Rails
- Fixed 240px width.
- Segmented platform pill control with subtle background.
- Clean icon + label alignment with 8px gap.
- Active state indicated by subtle background tint (`rgba(255, 255, 255, 0.06)`) and crisp white text.
