# postfix-messageanalyzer

Real-time Postfix mail log analyzer with a curses-based terminal UI. Displays queue IDs (QIDs), message counts, error detection, and on-demand subject line fetching via `postcat`.

## Features

- **Live tailing** or **replay mode** (`--since`) for historical analysis
- **Recent QID list** sorted by most recently seen, with selection and navigation
- **Error highlighting** — rows turn red for DSN 4xx/5xx, `status=sent` mismatches, and parenthesized SMTP replies
- **On-demand subject lines** — press Enter on a QID to fetch its subject via `postcat`, with retries and 60-minute cache
- **Adaptive layout** — panels resize dynamically; raw log section collapses when the terminal is small
- **Scroll lock** — press Space to toggle following the newest QID automatically

## Requirements

- Linux (or any Unix-like system with Postfix)
- Python 3.6+ (stdlib only — no external dependencies)
- `postcat` from Postfix (typically at `/usr/sbin/postcat`)
- Read access to the mail log file

## Installation

No installation required beyond cloning:

```bash
git clone <repo-url>
cd postfix-messageanalyzer
chmod +x postfix-messageanalyzer.py
```

## Usage

### Live tailing (default)

Run as a user with read access to the mail log:

```bash
sudo python3 postfix-messageanalyzer.py
```

### Replay mode

Replay the last hour:

```bash
sudo python3 postfix-messageanalyzer.py --since 1h
```

Other `--since` formats:

| Format | Example | Meaning |
|--------|---------|---------|
| Number + unit | `30m`, `2h`, `1d` | Hours, minutes, seconds, days |
| Plain number | `24` | Hours |
| Named | `today`, `yesterday` | Local midnight (converted to UTC) |
| ISO timestamp | `2026-01-15T08:00:00Z` | Specific start time |

### Custom log file path

```bash
sudo python3 postfix-messageanalyzer.py --logfile /var/log/mail.log.1
```

### Debug mode

Enables a debug panel and writes unmatched log lines to `debug.log`:

```bash
sudo python3 postfix-messageanalyzer.py --debug
```

### All options

```
Usage: postfix-messageanalyzer.py [OPTIONS]

Options:
  --debug           Enable debug panel and debug.log writes
  --logfile PATH    Path to mail log (default /var/log/mail.log)
  --debugfile PATH  Debug file path (default debug.log)
  --since VALUE     Start replay from this time: '1h', '30m', or ISO timestamp
  -h, --help        Show this help message and exit
```

## Keyboard shortcuts

| Key | Action |
|-----|--------|
| Up / Down, k / j | Navigate QID list |
| PageUp / PageDown | Scroll by page |
| Home / End, g / G | Jump to first / last QID |
| Enter | Fetch subject line for selected QID |
| Space | Toggle scroll lock (follow newest) |
| q | Quit |

## UI walkthrough

The interface is divided into four panels, arranged from top to bottom:

**Top bar** — Shows the current mode (`live` or `replaying  XX%`) and a follow status (`[FOLLOW]` or `[PAUSED]`). Press Ctrl-C to quit at any time.

**Stats line** — Displays the log file path, total lines read, total matched lines (lines containing a queue ID), and unique QIDs seen so far.

**Recent Queue IDs** — A scrollable list of the most recently active queue IDs, sorted newest first. Each row shows:
- `> QID : N lines` — selected entry (highlighted in cyan)
- `  QID : N lines` — unselected entries
- ` - to=<recipient@example.com>` — the most recent recipient for that QID (when available)
- ` - subj=...` — fetched subject line (truncated at 60 chars), or `[...]` while fetching
- `!ERR` — appended to selected rows that have error messages; entire rows turn red for errored QIDs

**Details panel** — Shows the full log history for the selected QID, including timestamps and message content. Error lines are displayed in red.

**Raw log section** — Shows the last 10 raw log lines read from the file. This panel collapses automatically when the terminal is too small to fit everything.

If the window is resized below the minimum required height, a "Window too small" message appears and only the QID list remains visible.

## Architecture

```
┌─────────────────────────────────┐
│  curses UI (main thread)        │
│  - QID list, details, raw log   │
│  - Adaptive panel layout        │
└───────────────┬─────────────────┘
                │ line_queue (bounded)
      ┌─────────┴──────────┐
      │                     │
  replay_worker       tail_file_thread
  (historical)        (live tail)
      │                     │
      └─────────┬───────────┘
                │
        /var/log/mail.log

  subject_executor (ThreadPoolExecutor)
    └── schedule_subject_fetch(qid) → postcat -q <qid>
```

- **Log reading**: Background thread tails (or replays) the log file, pushing lines into a bounded queue.
- **Parsing**: Main thread parses each line for Postfix queue IDs using regex, grouping messages by QID.
- **Error detection**: Checks each message for DSN error codes (4xx/5xx), `status=` values other than "sent", and parenthesized SMTP replies.
- **Subject fetching**: Pressing Enter triggers an async `postcat -q <qid>` call with retry logic. Results are cached for 60 minutes.
- **Layout**: Panels adapt to terminal size — the raw log section collapses first, then detail lines shrink.

## Development

The project uses an AI agent orchestration framework (opencode) for development workflows. See `AGENTS.md` for the protocol definition.

### Versioning

Version is managed in the `VERSION` file. Bump with:

```bash
scripts/bump-version.sh [patch|minor|major]
```

### Scripts

| Script | Purpose |
|--------|---------|
| `scripts/bump-version.sh` | Bump version (patch/minor/major) |
| `scripts/validate-worklog.sh` | Validate worklog YAML front-matter |

## License

[Add license information]
