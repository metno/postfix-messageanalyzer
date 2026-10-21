#!/usr/bin/env python3
"""
postfix-messageanalyzer.py

Postfix real-time analyzer:
- replay (--since) then live tailing
- selectable Recent QIDs (MRU), selection kept visible
- navigation: Up/Down, k/j, PageUp/PageDown, Home/End
- Enter => fetch Subject on-demand (uses postcat, async, cached)
- error highlighting (status != sent, DSN 4xx/5xx, parenthesized replies)
- --debug writes debug.log (optional)

# Generated with AI assistance (Qwen3.6, gpt-5-mini)
"""

import re, time, os, sys, argparse, threading, queue, signal, datetime, subprocess
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from email.header import decode_header, make_header
import curses

# ====== CONFIG ======
LOG_FILE = "/var/log/mail.log"
DEBUG_FILE = "debug.log"
MAX_PER_QID = 200
SHOW_QIDS = 20
DETAILS_LINES = 10
LAST_LINES = 10
DEBUG_LINES = 6
POLL_SLEEP = 0.12

VERSION = "0.2.1"

BATCH_SIZE = 200
DRAW_INTERVAL = 0.06

# Subject fetch config (Enter-driven)
SUBJECT_TTL = 3600  # seconds to keep subject cached
SUBJECT_WORKERS = 2
POSTCAT_CMD = "/usr/sbin/postcat"  # full path recommended
POSTCAT_TIMEOUT = 5
SUBJECT_RETRY_ATTEMPTS = 3
SUBJECT_RETRY_DELAY = 0.3

# ====== REGEXES ======
LOG_REGEX = re.compile(
    r"^(?P<ts>\S+)\s+(?P<host>\S+)\s+postfix/\S+$$\d+$$:\s+"
    r"(?P<queue_id>[A-Z0-9]{6,16}):\s*(?P<message>.*)$"
)
FALLBACK_QID = re.compile(r"(?P<queue_id>[A-Z0-9]{6,16}):\s*(?P<message>.*)$")
TO_RE = re.compile(r"\bto=<([^>]+)>", re.IGNORECASE)
STATUS_RE = re.compile(r"\bstatus=(?P<st>[^\s,()]+)", re.IGNORECASE)
DSN_RE = re.compile(r"\bdsn=(?P<dsn>\d+\.\d+\.\d+)", re.IGNORECASE)
PAREN_CODE_RE = re.compile(r"\(([45]\d{2})\b")
STANDALONE_CODE_RE = re.compile(r"(?<![=\dA-Za-z])([45]\d{2})(?=\s)")

# ====== GLOBALS (set in run()) ======
DEBUG = False
LOG_FILE_ARG = LOG_FILE
DEBUG_FILE_ARG = DEBUG_FILE

line_queue = queue.Queue(maxsize=20000)
stop_event = None
REPLAYING = False
REPLAY_PROGRESS = 0.0
START_SINCE_TS = None

# Subject runtime
subject_executor = ThreadPoolExecutor(max_workers=SUBJECT_WORKERS)
subjects_cache = {}  # qid -> (subject_str_or_None, ts)
subjects_futures = {}  # qid -> Future
subjects_lock = threading.Lock()


# ====== utilities ======
def parse_since_arg(s):
    if not s:
        return None
    s = s.strip()
    # Named anchors: today/yesterday → local midnight, converted to UTC
    if s.lower() == "today":
        local_midnight = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        return local_midnight.astimezone(datetime.timezone.utc)
    if s.lower() == "yesterday":
        local_midnight = datetime.datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ) - datetime.timedelta(days=1)
        return local_midnight.astimezone(datetime.timezone.utc)
    m = re.match(r"^(?P<num>\d+)(?P<unit>[smhd])$", s, re.IGNORECASE)
    if m:
        n = int(m.group("num"))
        unit = m.group("unit").lower()
        seconds = n * (
            1 if unit == "s" else 60 if unit == "m" else 3600 if unit == "h" else 86400
        )
        return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=seconds
        )
    if re.match(r"^\d+$", s):
        return datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            hours=int(s)
        )
    try:
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        raise ValueError(
            "Invalid --since value. Use e.g. '1h', '30m', '1d', 'today', 'yesterday' or ISO timestamp."
        )


def parse_line_ts(line):
    if not line:
        return None
    tok = line.split(None, 1)[0]
    try:
        dt = datetime.datetime.fromisoformat(tok)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def find_offset_for_since(path, since_dt, chunk_size=1 * 1024 * 1024):
    try:
        size = os.path.getsize(path)
    except Exception:
        return 0
    if size == 0:
        return 0
    chunk_size = min(int(chunk_size), size)
    pos = max(0, size - chunk_size)
    while True:
        try:
            with open(path, "rb") as fh:
                fh.seek(pos)
                data = fh.read(size - pos).decode("utf-8", errors="replace")
        except Exception:
            return 0
        cur = 0
        found_offset = None
        for line in data.splitlines(True):
            line_start = pos + cur
            cur += len(line)
            ts = parse_line_ts(line)
            if ts and ts >= since_dt:
                found_offset = line_start
                break
        if found_offset is not None:
            earliest = found_offset
            back_pos = max(0, pos - chunk_size)
            while back_pos >= 0:
                try:
                    with open(path, "rb") as fh:
                        fh.seek(back_pos)
                        read_len = earliest - back_pos
                        if read_len <= 0:
                            break
                        data2 = fh.read(read_len).decode("utf-8", errors="replace")
                except Exception:
                    break
                cur2 = 0
                earlier_found = None
                for line2 in data2.splitlines(True):
                    line2_start = back_pos + cur2
                    cur2 += len(line2)
                    ts2 = parse_line_ts(line2)
                    if ts2 and ts2 >= since_dt:
                        earlier_found = line2_start
                        break
                if earlier_found is not None:
                    earliest = earlier_found
                    if back_pos == 0:
                        break
                    back_pos = max(0, back_pos - chunk_size)
                else:
                    break
            return max(0, earliest)
        if pos == 0:
            return 0
        pos = max(0, pos - chunk_size)


def replay_worker(path, out_queue, since_dt, stop_event_local):
    global REPLAYING, REPLAY_PROGRESS
    REPLAYING = True
    REPLAY_PROGRESS = 0.0
    try:
        offset = find_offset_for_since(path, since_dt)
        try:
            total_size = os.path.getsize(path)
        except Exception:
            total_size = None
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(offset)
            if offset > 0:
                fh.readline()
            while not stop_event_local.is_set():
                line = fh.readline()
                if not line:
                    break
                try:
                    out_queue.put(line.rstrip("\n"), timeout=0.5)
                except queue.Full:
                    pass
                if total_size and total_size > offset:
                    try:
                        REPLAY_PROGRESS = min(
                            1.0,
                            (fh.tell() - offset) / float(max(1, total_size - offset)),
                        )
                    except Exception:
                        pass
        REPLAY_PROGRESS = 1.0
    except Exception:
        REPLAY_PROGRESS = 1.0
    finally:
        REPLAYING = False


def tail_file_thread(path, out_queue, stop_event_local):
    f = None
    inode = None
    while not stop_event_local.is_set():
        try:
            if f is None:
                f = open(path, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)
                inode = os.fstat(f.fileno()).st_ino
            line = f.readline()
            if line:
                try:
                    out_queue.put(line.rstrip("\n"), timeout=0.5)
                except queue.Full:
                    pass
                continue
            try:
                st = os.stat(path)
                if st.st_ino != inode:
                    try:
                        f.close()
                    except Exception:
                        pass
                    f = open(path, "r", encoding="utf-8", errors="replace")
                    inode = os.fstat(f.fileno()).st_ino
                    continue
            except FileNotFoundError:
                time.sleep(0.5)
                continue
            time.sleep(POLL_SLEEP)
        except Exception:
            time.sleep(0.5)
    try:
        if f:
            f.close()
    except Exception:
        pass


def detect_error_in_message(msg):
    if not msg:
        return False
    m = STATUS_RE.search(msg)
    if m:
        st = m.group("st").lower()
        return st != "sent"
    m2 = DSN_RE.search(msg)
    if m2:
        first = m2.group("dsn").split(".")[0]
        if first and first[0] in ("4", "5"):
            return True
    if PAREN_CODE_RE.search(msg):
        return True
    if STANDALONE_CODE_RE.search(msg):
        return True
    return False


def parse_log_line(line):
    m = LOG_REGEX.search(line)
    if m:
        return m.group("queue_id"), m.group("ts"), m.group("message")
    m2 = FALLBACK_QID.search(line)
    if m2:
        return m2.group("queue_id"), None, m2.group("message")
    if DEBUG:
        try:
            with open(DEBUG_FILE_ARG, "a", encoding="utf-8", errors="replace") as df:
                df.write(line + "\n")
        except Exception:
            pass
    return None, None, None


def extract_recent_recipient(q_msgs):
    for _, msg, _ in reversed(q_msgs):
        if not msg:
            continue
        m = TO_RE.search(msg)
        if m:
            return m.group(1)
    return None


# ===== Subject fetching (on-demand) =====
def decode_rfc2047(s):
    try:
        return str(make_header(decode_header(s)))
    except Exception:
        return s


def get_subject_from_queue(qid, postcat_cmd=POSTCAT_CMD):
    try:
        p = subprocess.run(
            [postcat_cmd, "-q", qid],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=POSTCAT_TIMEOUT,
        )
    except Exception:
        return None
    if p.returncode != 0:
        return None
    out = p.stdout
    lines = out.splitlines()
    headers_joined = []
    cur = ""
    for ln in lines:
        if ln.startswith((" ", "\t")) and cur != "":
            cur += "\n" + ln.strip()
        else:
            if cur != "":
                headers_joined.append(cur)
            cur = ln
    if cur != "":
        headers_joined.append(cur)
    subjects = []
    for h in headers_joined:
        if h.lower().startswith("subject:"):
            subjects.append(h.split(":", 1)[1].lstrip())
    if not subjects:
        # fallback regex
        matches = re.findall(r"(?im)^\s*Subject:\s*(.*)$", out)
        if not matches:
            return None
        subjects = [m.strip() for m in matches]
    subj_raw = subjects[-1].strip()
    subj_decoded = decode_rfc2047(subj_raw)
    return subj_decoded.strip() if subj_decoded else None


def _subject_worker_with_retries(
    qid, attempts=SUBJECT_RETRY_ATTEMPTS, delay=SUBJECT_RETRY_DELAY
):
    subj = None
    for i in range(attempts):
        try:
            subj = get_subject_from_queue(qid)
        except Exception:
            subj = None
        if subj:
            break
        if i < attempts - 1:
            time.sleep(delay)
    ts = time.time()
    with subjects_lock:
        subjects_cache[qid] = (subj, ts)
        subjects_futures.pop(qid, None)
    return subj


def schedule_subject_fetch(qid, force=False):
    now = time.time()
    with subjects_lock:
        entry = subjects_cache.get(qid)
        if entry and not force:
            subj, ts = entry
            if ts and (now - ts) < SUBJECT_TTL:
                return
        if qid in subjects_futures and not force:
            return
        future = subject_executor.submit(_subject_worker_with_retries, qid)
        subjects_futures[qid] = future


# ===== UI helpers =====
def safe_addstr(win, y, x, text, attr=0):
    try:
        maxx = curses.COLS - 1
        if x < 0:
            x = 0
        if y < 0 or y >= curses.LINES:
            return
        if len(text) > maxx - x:
            text = text[: maxx - x]
        win.addstr(y, x, text, attr)
    except Exception:
        pass


def bar_str(text):
    """Pad text to full terminal width for a solid reverse bar."""
    width = max(0, curses.COLS - 1)
    return text[:width].ljust(width)


def init_colors():
    has = curses.has_colors()
    if not has:
        return (curses.A_REVERSE, curses.A_BOLD)
    curses.start_color()
    try:
        curses.init_pair(1, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(2, curses.COLOR_RED, curses.COLOR_BLACK)
        sel_attr = curses.color_pair(1) | curses.A_BOLD
        err_attr = curses.color_pair(2) | curses.A_BOLD
        return (sel_attr, err_attr)
    except Exception:
        return (curses.A_REVERSE, curses.A_BOLD)


def _sigint_handler(signum, frame):
    if stop_event:
        stop_event.set()


# ===== Main UI loop =====


# ===== Main UI loop =====
def main(stdscr):
    global REPLAYING, REPLAY_PROGRESS
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.timeout(50)

    sel_attr, err_attr = init_colors()

    logs_by_qid = defaultdict(lambda: deque(maxlen=MAX_PER_QID))
    last_lines = deque(maxlen=LAST_LINES)
    debug_msgs = deque(maxlen=DEBUG_LINES) if DEBUG else None
    total_lines = 0
    total_matched = 0
    recent_qid_order = deque(maxlen=1000)

    selected_qid = None
    scroll_lock = True  # follow newest QID; Space toggles, navigation disables
    last_draw = 0.0

    tail_thread = None
    replay_thread = None
    if START_SINCE_TS:
        replay_thread = threading.Thread(
            target=replay_worker,
            args=(LOG_FILE_ARG, line_queue, START_SINCE_TS, stop_event),
            daemon=True,
        )
        replay_thread.start()
    else:
        tail_thread = threading.Thread(
            target=tail_file_thread,
            args=(LOG_FILE_ARG, line_queue, stop_event),
            daemon=True,
        )
        tail_thread.start()

    try:
        while True:
            if stop_event.is_set():
                break

            processed = 0
            while processed < BATCH_SIZE:
                try:
                    line = line_queue.get_nowait()
                except queue.Empty:
                    break
                processed += 1
                total_lines += 1
                last_lines.append(line)
                qid, ts, msg = parse_log_line(line)
                if qid:
                    total_matched += 1
                    entry_ts = ts if ts else (line.split()[0] if line.split() else "")
                    is_err = detect_error_in_message(msg)
                    logs_by_qid[qid].append((entry_ts, msg, is_err))
                    try:
                        recent_qid_order.remove(qid)
                    except ValueError:
                        pass
                    recent_qid_order.append(qid)
                    if DEBUG and debug_msgs is not None:
                        debug_msgs.appendleft(
                            f"Matched: {qid} ({len(logs_by_qid[qid])})"
                        )
                else:
                    if DEBUG and debug_msgs is not None:
                        debug_msgs.appendleft(f"No QID: {line[:80]}")

            if replay_thread is not None and not REPLAYING and tail_thread is None:
                tail_thread = threading.Thread(
                    target=tail_file_thread,
                    args=(LOG_FILE_ARG, line_queue, stop_event),
                    daemon=True,
                )
                tail_thread.start()

            recents = list(reversed(recent_qid_order))

            # Auto-follow: keep cursor on newest QID when scroll lock is on
            if scroll_lock and recents:
                selected_qid = recents[0]
            elif selected_qid is None and recents:
                selected_qid = recents[0]

            top_reserved = 5
            debug_block = (DEBUG_LINES + 1) if DEBUG else 0
            KEY_BAR = 1
            MIN_RECENTS = 5
            MIN_DETAIL_MSGS = 5  # minimum message lines before "too small" kicks in
            RAWLOG_ROWS = LAST_LINES + 2  # divider+label+lines

            # Rawlog collapses first when terminal is too small.
            # Use full DETAILS_LINES for this threshold.
            FULL_DETAIL_ROWS = DETAILS_LINES + 3 + debug_block
            show_rawlog = (
                curses.LINES
                >= top_reserved
                + MIN_RECENTS
                + 1
                + FULL_DETAIL_ROWS
                + RAWLOG_ROWS
                + KEY_BAR
            )

            # Determine how many detail message lines actually fit.
            # available = rows for recents + divider + details (rawlog already subtracted)
            available = (
                curses.LINES
                - top_reserved
                - KEY_BAR
                - (RAWLOG_ROWS if show_rawlog else 0)
            )
            detail_avail = (
                available - MIN_RECENTS - 1
            )  # rows for details panel incl. top divider
            eff_detail_lines = min(
                DETAILS_LINES, max(0, detail_avail - 3 - debug_block)
            )
            too_small = eff_detail_lines < MIN_DETAIL_MSGS

            # Recents height fills rows between top headers and the details panel
            eff_detail_rows = (
                eff_detail_lines + 3 + debug_block
            )  # divider+header+QID+msgs
            if show_rawlog:
                last_start_calc = curses.LINES - RAWLOG_ROWS - KEY_BAR
                detail_start_calc = last_start_calc - eff_detail_rows
                avail_for_recents = detail_start_calc - top_reserved - 1
            elif not too_small:
                detail_start_calc = curses.LINES - eff_detail_rows - KEY_BAR
                avail_for_recents = detail_start_calc - top_reserved - 1
            else:
                # Reserve rows for divider + message + key bar below recents
                avail_for_recents = curses.LINES - top_reserved - KEY_BAR - 3

            recents_height = min(SHOW_QIDS, max(1, avail_for_recents))

            ch = stdscr.getch()
            if ch != -1:
                if recents:
                    sel_index = (
                        recents.index(selected_qid) if selected_qid in recents else 0
                    )
                else:
                    sel_index = 0
                if ch in (curses.KEY_UP, ord("k")):
                    if recents:
                        scroll_lock = False
                        sel_index = max(0, sel_index - 1)
                        selected_qid = recents[sel_index]
                elif ch in (curses.KEY_DOWN, ord("j")):
                    if recents:
                        scroll_lock = False
                        sel_index = min(len(recents) - 1, sel_index + 1)
                        selected_qid = recents[sel_index]
                elif ch in (curses.KEY_PPAGE,):
                    if recents:
                        scroll_lock = False
                        step = max(1, recents_height)
                        sel_index = max(0, sel_index - step)
                        selected_qid = recents[sel_index]
                elif ch in (curses.KEY_NPAGE,):
                    if recents:
                        scroll_lock = False
                        step = max(1, recents_height)
                        sel_index = min(len(recents) - 1, sel_index + step)
                        selected_qid = recents[sel_index]
                elif ch in (curses.KEY_HOME, ord("g")):
                    if recents:
                        scroll_lock = False
                        selected_qid = recents[0]
                elif ch in (curses.KEY_END, ord("G")):
                    if recents:
                        scroll_lock = False
                        selected_qid = recents[-1]
                elif ch in (curses.KEY_ENTER, 10, 13):
                    if selected_qid:
                        schedule_subject_fetch(selected_qid, force=True)
                elif ch == ord(" "):
                    scroll_lock = not scroll_lock
                elif ch == curses.KEY_RESIZE:
                    # update_lines_cols() syncs curses.LINES/COLS from the OS;
                    # avoid resizeterm() — it resets window flags (keypad, nodelay)
                    # which permanently breaks input. The erase+redraw is enough.
                    curses.update_lines_cols()
                    stdscr.keypad(True)
                    stdscr.nodelay(True)
                    stdscr.erase()
                    last_draw = 0.0  # force immediate redraw
                elif ch == ord("q"):
                    break

            display_qids = []
            window_start = 0
            total_recents = len(recents)
            if recents:
                max_start = max(0, len(recents) - recents_height)
                window_start = 0
                if selected_qid in recents:
                    sel_idx = recents.index(selected_qid)
                    if sel_idx >= window_start + recents_height:
                        window_start = min(sel_idx - (recents_height - 1), max_start)
                    elif sel_idx < window_start:
                        window_start = sel_idx
                if window_start < 0:
                    window_start = 0
                if window_start > max_start:
                    window_start = max_start
                display_qids = recents[window_start : window_start + recents_height]
                if selected_qid not in recents:
                    selected_qid = recents[0]

            selected_index = (
                display_qids.index(selected_qid)
                if (selected_qid in display_qids)
                else -1
            )

            now = time.time()
            redraw = False
            if (now - last_draw) >= DRAW_INTERVAL:
                redraw = True
            elif processed > 0:
                redraw = True
            elif ch != -1:
                redraw = True

            if redraw:
                stdscr.erase()
                follow_label = "  [FOLLOW]" if scroll_lock else "  [PAUSED]"
                if REPLAYING:
                    safe_addstr(
                        stdscr,
                        0,
                        0,
                        bar_str(
                            f"Mode: replaying {int(REPLAY_PROGRESS * 100):3d}% (since {START_SINCE_TS.isoformat()}){follow_label}  Ctrl-C to quit"
                        ),
                        curses.A_REVERSE,
                    )
                else:
                    safe_addstr(
                        stdscr,
                        0,
                        0,
                        bar_str(f"Mode: live{follow_label}  Ctrl-C to quit"),
                        curses.A_REVERSE,
                    )
                safe_addstr(stdscr, 1, 0, f"Log file: {LOG_FILE_ARG}")
                safe_addstr(
                    stdscr,
                    2,
                    0,
                    f"Total lines read: {total_lines}    Total matched: {total_matched}    Unique QIDs: {len(logs_by_qid)}",
                )
                safe_addstr(stdscr, 3, 0, "")
                visible_start = window_start
                visible_count = len(display_qids)
                visible_end = visible_start + visible_count
                range_label = (
                    f"{visible_start + 1}-{visible_end} of {total_recents}"
                    if total_recents > 0 and visible_count > 0
                    else f"0 of {total_recents}"
                )
                safe_addstr(
                    stdscr, 4, 0, f"Recent Queue IDs (most recent first): {range_label}"
                )

                # draw recent QIDs (with error marking/coloring)
                for idx, q in enumerate(display_qids):
                    y = 5 + idx
                    cnt = len(logs_by_qid[q])

                    # detect if any stored line for this QID is an error
                    has_error = any(entry[2] for entry in logs_by_qid[q])

                    recipient = extract_recent_recipient(list(logs_by_qid[q]))
                    suffix = f" - to=<{recipient}>" if recipient else ""

                    with subjects_lock:
                        future = subjects_futures.get(q)
                        cache_entry = subjects_cache.get(q)
                    if future is not None:
                        subj_label = " - subj=[...]"
                    elif cache_entry:
                        subj_val = cache_entry[0]
                        if subj_val:
                            s = subj_val.replace("\n", " ").replace("\r", "")
                            subj_label = " - subj=" + (
                                s[:60] + "..." if len(s) > 60 else s
                            )
                        else:
                            subj_label = ""
                    else:
                        subj_label = ""

                    base_label = f"{q} : {cnt} lines{suffix}{subj_label}"

                    if idx == selected_index:
                        # selected: keep selection attribute, append error marker if present
                        label = f"> {base_label}"
                        if has_error:
                            label = label + "  !ERR"
                        safe_addstr(stdscr, y, 2, label, sel_attr)
                    else:
                        # non-selected: color the row red if it has an error
                        label = f"  {base_label}"
                        if has_error:
                            safe_addstr(stdscr, y, 2, label, err_attr)
                        else:
                            safe_addstr(stdscr, y, 2, label)

                # All panel positions anchored from the bottom
                last_start = 0
                detail_start = 0
                if show_rawlog:
                    last_start = curses.LINES - RAWLOG_ROWS - KEY_BAR
                    detail_start = last_start - eff_detail_rows
                elif not too_small:
                    detail_start = curses.LINES - eff_detail_rows - KEY_BAR

                if too_small:
                    msg = "Window too small — resize to see details panel"
                    safe_addstr(
                        stdscr,
                        top_reserved + recents_height,
                        0,
                        "-" * (curses.COLS - 1),
                    )
                    safe_addstr(stdscr, top_reserved + recents_height + 1, 2, msg)
                else:
                    safe_addstr(stdscr, detail_start - 1, 0, "-" * (curses.COLS - 1))
                    safe_addstr(
                        stdscr,
                        detail_start,
                        0,
                        f"Details (selected QID) - {eff_detail_lines} lines:",
                    )

                    if selected_qid and selected_qid in logs_by_qid:
                        safe_addstr(stdscr, detail_start + 1, 2, f"QID: {selected_qid}")
                        q_msgs = list(logs_by_qid[selected_qid])[-eff_detail_lines:]
                        for i, (ets, emsg, is_err) in enumerate(q_msgs):
                            y = detail_start + 2 + i
                            if is_err:
                                safe_addstr(
                                    stdscr, y, 4, f"{ets or '-'}  {emsg}", err_attr
                                )
                            else:
                                safe_addstr(stdscr, y, 4, f"{ets or '-'}  {emsg}")
                    else:
                        safe_addstr(
                            stdscr,
                            detail_start + 1,
                            2,
                            "(no QID selected or no messages yet)",
                        )

                    if DEBUG and debug_msgs is not None:
                        debug_top = detail_start + eff_detail_lines + 2
                        safe_addstr(stdscr, debug_top - 1, 0, "-" * (curses.COLS - 1))
                        safe_addstr(stdscr, debug_top, 0, "Debug (recent):")
                        for i, d in enumerate(debug_msgs):
                            safe_addstr(
                                stdscr, debug_top + 1 + i, 2, d[: curses.COLS - 4]
                            )

                if show_rawlog:
                    safe_addstr(stdscr, last_start - 1, 0, "-" * (curses.COLS - 1))
                    safe_addstr(
                        stdscr, last_start, 0, f"Last {LAST_LINES} raw log lines:"
                    )
                    for i, raw in enumerate(last_lines):
                        y = last_start + 1 + i
                        safe_addstr(stdscr, y, 2, raw[: curses.COLS - 4])

                # Bottom key bar
                key_bar = f"Keys: Up/Down k/j  PgUp/PgDn  Home/End  Enter=subject  Space=follow  q=quit  v{VERSION}"
                safe_addstr(
                    stdscr, curses.LINES - 1, 0, bar_str(key_bar), curses.A_REVERSE
                )

                stdscr.refresh()
                last_draw = now

            time.sleep(0.005)
    finally:
        try:
            stop_event.set()
        except Exception:
            pass
        try:
            if replay_thread is not None and replay_thread.is_alive():
                replay_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            if tail_thread is not None and tail_thread.is_alive():
                tail_thread.join(timeout=1.0)
        except Exception:
            pass
        try:
            subject_executor.shutdown(wait=False)
        except Exception:
            pass


def _check_log_file(path):
    """Validate that the log file is accessible. Exit with a clear message if not."""
    reasons = []

    if not os.path.exists(path):
        reasons.append("The log file does not exist at the specified path: %s" % path)
        reasons.append("")
        reasons.append("Possible causes:")
        reasons.append("  - Postfix is not installed or not running")
        reasons.append(
            "  - The mail log is located at a different path (use --logfile to specify)"
        )
        reasons.append("  - The log file has been rotated and not yet recreated")
        if os.path.exists("/var/log"):
            reasons.append("  - /var/log exists but may not contain mail.log")
        else:
            reasons.append("  - /var/log directory does not exist on this system")
        reasons.append("")
        reasons.append("To find the mail log location, run:")
        reasons.append(
            "  journalctl -u postfix 2>/dev/null || find /var/log -name 'mail*' 2>/dev/null"
        )
    elif not os.path.isfile(path):
        reasons.append("The log path exists but is not a regular file: %s" % path)
    elif not os.access(path, os.R_OK):
        reasons.append("The log file is not readable: %s" % path)
        reasons.append("")
        reasons.append("Possible causes:")
        reasons.append("  - Insufficient permissions (need read access for this file)")
        reasons.append(
            "  - The file is owned by root and you are not in the adm/syslog group"
        )
        reasons.append("")
        reasons.append("To fix, run one of:")
        reasons.append("  sudo chmod o+r %s" % path)
        reasons.append(
            "  sudo usermod -a -G adm $(whoami)   # then log out and back in"
        )
    if reasons:
        print("", file=sys.stderr)
        print("ERROR: Cannot access mail log file", file=sys.stderr)
        for r in reasons:
            print(r, file=sys.stderr)
        sys.exit(1)


def run():
    global DEBUG, LOG_FILE_ARG, DEBUG_FILE_ARG, stop_event, START_SINCE_TS
    p = argparse.ArgumentParser(description="Postfix message analyzer (curses UI).")
    p.add_argument(
        "--debug", action="store_true", help="Enable debug panel and debug.log writes"
    )
    p.add_argument(
        "--logfile",
        default=LOG_FILE,
        help="Path to mail log (default /var/log/mail.log)",
    )
    p.add_argument(
        "--debugfile", default=DEBUG_FILE, help="Debug file path (default debug.log)"
    )
    p.add_argument(
        "--since",
        default=None,
        help="Start replay from this time: '1h', '30m', '1d', 'today', 'yesterday' or ISO timestamp",
    )
    args = p.parse_args()

    DEBUG = bool(args.debug)
    LOG_FILE_ARG = args.logfile
    DEBUG_FILE_ARG = args.debugfile

    if args.since:
        try:
            START_SINCE_TS = parse_since_arg(args.since)
        except Exception as e:
            print("Invalid --since:", e, file=sys.stderr)
            sys.exit(1)
    else:
        START_SINCE_TS = None

    # Validate log file accessibility before entering curses
    _check_log_file(LOG_FILE_ARG)

    stop_event_local = threading.Event()
    stop_event = stop_event_local
    signal.signal(signal.SIGINT, lambda s, f: stop_event.set())
    curses.wrapper(main)


if __name__ == "__main__":
    run()
