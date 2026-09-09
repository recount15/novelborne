# -*- coding: utf-8 -*-
"""Modern UI design tokens and CSS helpers for the Fate Engine Gradio UI.

The module is intentionally independent from Gradio so it can be imported by
pure render helpers and tested without starting the application. Use
``gr.Blocks(css=theme.gradio_css())`` or ``inject_theme(existing_css)`` when
building a Gradio app.
"""
from __future__ import annotations

from typing import Literal

ThemeMode = Literal["light", "dark", "auto"]

# Stable hooks for generated markup and Gradio ``elem_classes``.
CHARACTER_CARD_CLASS = "fe-character-card"
CHARACTER_CARD_COMPACT_CLASS = "fe-character-card--compact"
SKILL_ROW_CLASS = "fe-skill-row"
SKILL_NAME_CLASS = "fe-skill-name"
SKILL_VALUE_CLASS = "fe-skill-value"
STATE_ERROR_CLASS = "fe-state-error"
STATE_CONFIRM_CLASS = "fe-state-confirm"
STATE_INFO_CLASS = "fe-state-info"
STATE_WARNING_CLASS = "fe-state-warning"

# Public token map for code that needs to render inline styles or inspect the
# palette. CSS remains the source of truth for browser rendering.
TOKENS: dict[str, str] = {
    "font_family": "Inter, ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif",
    "font_mono": "'SFMono-Regular', Consolas, 'Liberation Mono', monospace",
    "radius_sm": "5px",
    "radius_md": "7px",
    "radius_lg": "8px",
    "focus_ring": "0 0 0 3px rgba(163, 58, 43, .22)",
    "light_bg": "#f3f2ee",
    "light_surface": "#fbfbf8",
    "light_text": "#242522",
    "light_muted": "#666861",
    "light_border": "#d1d0c8",
    "dark_bg": "#1b1d1b",
    "dark_surface": "#242724",
    "dark_text": "#f0f0ea",
    "dark_muted": "#b8bbb3",
    "dark_border": "#4e524c",
    "accent": "#a33a2b",
    "accent_strong": "#812d23",
    "success": "#236b5a",
    "warning": "#95631b",
    "danger": "#a33a2b",
}

_BASE_CSS = r"""
/* Fate Engine modern tokens. Keep selectors scoped to avoid overriding user content. */
:root,
.fe-theme,
.fe-theme[data-theme="light"] {
  --fe-font: Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  --fe-font-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  --fe-bg: #f3f2ee;
  --fe-surface: #fbfbf8;
  --fe-surface-raised: #ffffff;
  --fe-text: #242522;
  --fe-text-muted: #666861;
  --fe-border: #d1d0c8;
  --fe-border-strong: #a6a79f;
  --fe-accent: #a33a2b;
  --fe-accent-strong: #812d23;
  --fe-accent-soft: #f4e3df;
  --fe-success: #236b5a;
  --fe-success-soft: #deeee8;
  --fe-warning: #95631b;
  --fe-warning-soft: #f4ead4;
  --fe-danger: #a33a2b;
  --fe-danger-soft: #f4e3df;
  --fe-info: #356a72;
  --fe-info-soft: #e0ecec;
  --fe-shadow: 0 1px 3px rgba(35, 37, 34, .08);
  --fe-focus: 0 0 0 3px rgba(163, 58, 43, .22);
  --fe-radius-sm: 5px;
  --fe-radius-md: 7px;
  --fe-radius-lg: 8px;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]),
  .fe-theme[data-theme="auto"] {
    --fe-bg: #1b1d1b;
    --fe-surface: #242724;
    --fe-surface-raised: #2c302c;
    --fe-text: #f0f0ea;
    --fe-text-muted: #b8bbb3;
    --fe-border: #4e524c;
    --fe-border-strong: #6f746c;
    --fe-accent: #df7867;
    --fe-accent-strong: #ef9a8d;
    --fe-accent-soft: #4a2924;
    --fe-success: #6ee7b7;
    --fe-success-soft: #123b2d;
    --fe-warning: #fbbf24;
    --fe-warning-soft: #4a3410;
    --fe-danger: #fca5a5;
    --fe-danger-soft: #4b1d1d;
    --fe-info: #7dd3fc;
    --fe-info-soft: #12364b;
    --fe-shadow: 0 2px 10px rgba(0, 0, 0, .25);
    --fe-focus: 0 0 0 3px rgba(96, 165, 250, .38);
  }
}

.fe-theme[data-theme="dark"] {
  --fe-bg: #1b1d1b;
  --fe-surface: #242724;
  --fe-surface-raised: #2c302c;
  --fe-text: #f0f0ea;
  --fe-text-muted: #b8bbb3;
  --fe-border: #4e524c;
  --fe-border-strong: #6f746c;
  --fe-accent: #df7867;
  --fe-accent-strong: #ef9a8d;
  --fe-accent-soft: #4a2924;
  --fe-success: #6ee7b7;
  --fe-success-soft: #123b2d;
  --fe-warning: #fbbf24;
  --fe-warning-soft: #4a3410;
  --fe-danger: #fca5a5;
  --fe-danger-soft: #4b1d1d;
  --fe-info: #7dd3fc;
  --fe-info-soft: #12364b;
  --fe-shadow: 0 2px 10px rgba(0, 0, 0, .25);
  --fe-focus: 0 0 0 3px rgba(96, 165, 250, .38);
}

.gradio-container {
  max-width: none !important;
  background: var(--fe-bg);
  color: var(--fe-text);
  font-family: var(--fe-font);
}

.gradio-container :is(input, textarea, select, button):focus-visible {
  outline: 0;
  box-shadow: var(--fe-focus);
}

.fe-app-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 24px;
  margin: 0 0 16px;
  padding: 20px 24px 18px;
  border-bottom: 1px solid var(--fe-border);
  background: var(--fe-surface);
}

.fe-app-header h1 {
  margin: 0;
  color: var(--fe-text);
  font-family: Georgia, "Noto Serif SC", serif;
  font-size: 2rem;
  font-weight: 700;
  letter-spacing: 0;
}

.fe-app-header p { margin: 3px 0 0; color: var(--fe-text-muted); }
.fe-app-header .fe-kicker {
  margin: 0 0 5px;
  color: var(--fe-accent);
  font-size: .7rem;
  font-weight: 750;
}

.fe-library-count {
  display: flex;
  align-items: baseline;
  gap: 7px;
  color: var(--fe-text-muted);
}
.fe-library-count strong { color: var(--fe-text); font-size: 1.4rem; }
.fe-library-count span { font-size: .8rem; }

.fe-workbench { align-items: flex-start; gap: 18px; padding: 0 18px 24px; }
.fe-config-panel {
  max-height: calc(100vh - 132px);
  overflow-y: auto;
  padding: 2px 14px 18px 2px;
  scrollbar-width: thin;
}
.fe-config-panel h3 { margin: 2px 0 8px; font-size: 1.05rem; }
.fe-story-panel {
  min-height: calc(100vh - 132px);
  padding: 2px 0 0 6px;
  border-left: 1px solid var(--fe-border);
}
.fe-settings-accordion { margin-bottom: 10px; }
.fe-mode-switch { margin-bottom: 8px; }

.gradio-container button.primary {
  border-color: var(--fe-accent) !important;
  background: var(--fe-accent) !important;
  color: #fff !important;
}
.gradio-container button.primary:hover { background: var(--fe-accent-strong) !important; }
.gradio-container :is(input, textarea) { border-radius: var(--fe-radius-sm) !important; }

.fe-roster-editor {
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius-sm);
  padding: 12px;
  background: var(--fe-surface-raised);
}

.fe-card-preview {
  margin: 8px 0 10px;
}

.fe-theme .fe-character-card {
  display: flex;
  flex-direction: column;
  gap: 8px;
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--fe-border);
  border-left: 4px solid var(--fe-accent);
  overflow-wrap: anywhere;
  border-radius: var(--fe-radius-md);
  background: var(--fe-surface);
  color: var(--fe-text);
  box-shadow: var(--fe-shadow);
}

.fe-theme .fe-character-card--compact {
  gap: 4px;
  padding: 10px 12px;
  border-left-width: 3px;
}

.fe-theme .fe-character-card__header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
}

.fe-theme .fe-character-card__name {
  overflow: hidden;
  font-size: 1rem;
  font-weight: 700;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fe-theme .fe-character-card__meta {
  color: var(--fe-text-muted);
  font-size: .82rem;
  white-space: nowrap;
}

.fe-theme .fe-character-card__body {
  color: var(--fe-text-muted);
  font-size: .9rem;
  line-height: 1.45;
}

.fe-theme .fe-skill-row {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  min-height: 34px;
  padding: 6px 10px;
  border: 1px solid var(--fe-border);
  border-radius: var(--fe-radius-sm);
  background: var(--fe-surface-raised);
}

.fe-theme .fe-skill-name {
  flex: 0 0 7rem;
  overflow: hidden;
  color: var(--fe-text);
  font-size: .87rem;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fe-theme .fe-skill-value {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  color: var(--fe-text-muted);
  font-family: var(--fe-font-mono);
  font-size: .8rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fe-theme :is(.fe-state-error, .fe-state-confirm, .fe-state-info, .fe-state-warning) {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 9px 12px;
  border: 1px solid currentColor;
  border-radius: var(--fe-radius-sm);
  font-size: .88rem;
  font-weight: 600;
  line-height: 1.4;
}
.fe-theme .fe-state-error { color: var(--fe-danger); background: var(--fe-danger-soft); }
.fe-theme .fe-state-confirm { color: var(--fe-success); background: var(--fe-success-soft); }
.fe-theme .fe-state-info { color: var(--fe-info); background: var(--fe-info-soft); }
.fe-theme .fe-state-warning { color: var(--fe-warning); background: var(--fe-warning-soft); }

.fe-theme .fe-status-token {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 2px 8px;
  border: 1px solid currentColor;
  border-radius: 999px;
  font-size: .75rem;
  font-weight: 700;
  line-height: 1.2;
}
.fe-theme .fe-status-token--error { color: var(--fe-danger); background: var(--fe-danger-soft); }
.fe-theme .fe-status-token--confirm { color: var(--fe-success); background: var(--fe-success-soft); }
.fe-theme .fe-status-token--warning { color: var(--fe-warning); background: var(--fe-warning-soft); }
.fe-theme .fe-status-token--info { color: var(--fe-info); background: var(--fe-info-soft); }

@media (max-width: 640px) {
  .fe-theme .fe-skill-row { align-items: flex-start; flex-direction: column; gap: 2px; }
  .fe-theme .fe-skill-name, .fe-theme .fe-skill-value { flex-basis: auto; max-width: 100%; }
}
"""


def get_css(mode: ThemeMode = "auto") -> str:
    """Return scoped CSS for direct use with ``gr.Blocks(css=...)``.

    ``mode`` controls the root data attribute. ``auto`` follows the operating
    system preference; light and dark force a stable palette.
    """
    if mode not in ("light", "dark", "auto"):
        raise ValueError("mode must be 'light', 'dark' or 'auto'")
    return f'.fe-theme[data-theme="{mode}"] {{ color-scheme: {"dark" if mode == "dark" else "light"}; }}\n' + _BASE_CSS


def gradio_css(mode: ThemeMode = "auto") -> str:
    """Alias intended for ``gr.Blocks(css=gradio_css())``."""
    return get_css(mode)


def inject_theme(existing_css: str = "", mode: ThemeMode = "auto") -> str:
    """Append theme CSS to an existing Gradio stylesheet."""
    prefix = str(existing_css or "").rstrip()
    return f"{prefix}\n\n{get_css(mode)}" if prefix else get_css(mode)


def theme_root_class(mode: ThemeMode = "auto") -> str:
    """Return the class hook for a wrapping Gradio container."""
    if mode not in ("light", "dark", "auto"):
        raise ValueError("mode must be 'light', 'dark' or 'auto'")
    return f"fe-theme fe-theme--{mode}"


__all__ = [
    "CHARACTER_CARD_CLASS",
    "CHARACTER_CARD_COMPACT_CLASS",
    "SKILL_ROW_CLASS",
    "SKILL_NAME_CLASS",
    "SKILL_VALUE_CLASS",
    "STATE_ERROR_CLASS",
    "STATE_CONFIRM_CLASS",
    "STATE_INFO_CLASS",
    "STATE_WARNING_CLASS",
    "TOKENS",
    "get_css",
    "gradio_css",
    "inject_theme",
    "theme_root_class",
]
