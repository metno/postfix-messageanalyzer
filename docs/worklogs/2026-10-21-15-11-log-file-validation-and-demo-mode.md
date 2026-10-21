---
when: 2026-10-21T15:11:56Z
why: Add log file validation warning and demo mode for testing without Postfix
what: Log file accessibility check, demo-mail.log sample, and README screenshot
model: mlx-local/Qwen3.6-35B-A3B-MLX-8bit
tags: [feature, bugfix, demo, documentation]
---

Added `_check_log_file()` validation in `postfix-messageanalyzer.py:847` that exits with a clear error message before entering curses if the log file is missing, not a regular file, or unreadable — including possible causes and fix suggestions. Created `demo-mail.log` with realistic Postfix entries (deliveries, bounces, deferrals) and added `screenshots/demo-ui.png` with a new "Demo mode" section in README.md. Bumped to v0.3.0.
