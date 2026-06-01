---
when: 2026-06-01T14:00:42Z
why: Embed version number in script and show it in the UI footer bar
what: Add VERSION constant to postfix-messageanalyzer.py and display in key bar
model: github-copilot/claude-sonnet-4.6
tags: [patch, ui, version]
---

Added `VERSION = "0.2.1"` constant to the config block of `postfix-messageanalyzer.py` so the script carries its own version when distributed as part of a collection. The version is appended to the bottom key bar (`v0.2.1`), always visible to the user. Bumped to v0.2.1.
