---
when: 2026-10-21T15:43:37Z
why: Revert dynamic VERSION reading to hardcoded and update bump script to sync both
what: Hardcoded VERSION + bump-version.sh updates both file and script constant
model: mlx-local/Qwen3.6-35B-A3B-MLX-8bit
tags: [chore, versioning, automation]
---

Reverted dynamic VERSION file reading back to hardcoded `VERSION = "0.3.1"` in postfix-messageanalyzer.py. Updated scripts/bump-version.sh to also update the hardcoded constant via sed, so `scripts/bump-version.sh [patch|minor|major]` keeps both VERSION file and script in sync automatically.
