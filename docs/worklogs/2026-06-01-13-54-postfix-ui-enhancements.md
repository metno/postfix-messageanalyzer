---
when: 2026-06-01T13:54:48Z
why: Add scroll-lock follow mode, terminal resize support, full-width bars, and responsive panel collapse
what: UI enhancements for postfix-messageanalyzer.py — follow mode, live resize, adaptive layout
model: github-copilot/claude-sonnet-4.6
tags: [feature, ui, curses, resize, layout]
---

Added scroll-lock follow mode (Space to toggle, auto-disables on navigation) with `[FOLLOW]`/`[PAUSED]` indicator in the top bar. Terminal resize now works live via `KEY_RESIZE` + `curses.update_lines_cols()` without breaking key input. Top and bottom bars extended to full terminal width using a `bar_str()` helper. Details panel and raw-log panel are now anchored from the bottom; details scales from 10 down to 5 message lines as the window shrinks, and a "window too small" message replaces the details panel below 20 rows. Bumped to v0.2.0.
