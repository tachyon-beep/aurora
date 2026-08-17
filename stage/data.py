import bisect
import datetime
import difflib
import glob
import json
import os
import re
import threading
import time

SELF_MOD_TOOLS = ("write_file", "migrate", "reset", "done")
TAIL_READ_BYTES = 4_000_000
TOMBSTONE_READ_BYTES = 4096
HEAD_READ_BYTES = 8192
CODE_READ_BYTES = 524_288
PUBLISHED_READ_BYTES = 4096
PUBLISHED_TEXT_CAP = 400
SPOKEN_READ_BYTES = 4096
SPOKEN_TEXT_CAP = 400
MAX_EPOCH_AGE_SECONDS = 30 * 86400
SUBCALL_MESSAGE_LIMIT = 2
SUBCALL_PROMPT_CHARS = 200
MIN_SUMMARY_CHARS = 80

DIODE_VERBS = {
    "weather": "read the weather",
    "quakes": "checked for earthquakes",
    "airquality": "read the air quality",
    "tides": "read the sea state",
    "solarwind": "read the solar wind",
    "wikipedia": "looked something up",
    "entropy": "pulled random bytes",
    "abc": "read the headlines",
    "bbc": "read the headlines",
    "aljazeera": "read the headlines",
    "nhk": "read the headlines",
    "lemonde": "read the headlines",
    "dw": "read the headlines",
    "elpais": "read the headlines",
    "thehindu": "read the headlines",
    "g1": "read the headlines",
    "allafrica": "read the headlines",
    "arxiv": "fetched a paper",
    "gutensearch": "searched for a book",
    "gutenberg": "fetched a book",
    "commons": "fetched a file",
    "clone": "fetched a repository",
    "fetchrss": "read a feed",
    "fetchlinks": "followed a link",
    "fetchhttp": "fetched a page",
    "publish": "spoke to the outside",
    "post": "posted to the blog",
    "speak": "put a voice to it",
    "help": "listed its commands",
    "later": "put a command off",
    "time": "checked the time",
}

# DIODE_VERBS carries exactly the commands the diode will run, so a name it does
# not hold is a name the diode refused. These are the ones whose whole effect
# stays inside the box; everything else either fetches something or puts
# something outside, so reach is the default and this is the exception list. The
# stage cannot import the diode — they are separate images — so a test holds
# both sets against the real vocabulary.
DIODE_LOCAL_COMMANDS = {"entropy", "help", "later", "time"}

DIODE_REACHING_COMMANDS = set(DIODE_VERBS) - DIODE_LOCAL_COMMANDS


def _plural(count, noun):
    """A count and its noun, pluralised with a trailing s when count is not one."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def contained_file(root, path):
    """The real path of path when it is a regular file inside root, else None.

    Symbolic links are resolved before the containment test. The agent can plant
    links in /work and /diode, and the watchdog mirror copies links as links, so
    every stage-side read of those roots resolves the target and rejects it when
    it lands outside the mount. Non-regular files are rejected too: a fifo would
    otherwise block the reading thread.
    """
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(path)
    if candidate != root_real and not candidate.startswith(root_real + os.sep):
        return None
    if not os.path.isfile(candidate):
        return None
    return candidate


_line_count_state = {}
_code_stat_state = {}
_state_lock = threading.Lock()
_event_fold_state = {}
_event_state_lock = threading.Lock()


def _count_lines(path):
    """Count lines in a file, scanning only bytes appended since the previous call.

    The memo is read-modify-written under a lock: request threads and the
    summariser's refresh thread call this concurrently.
    """
    with _state_lock:
        return _count_lines_locked(path)


def _count_lines_locked(path):
    scanned, count, ends_with_newline = _line_count_state.get(path, (0, 0, True))
    try:
        if os.path.getsize(path) < scanned:
            scanned, count, ends_with_newline = 0, 0, True
        with open(path, "rb") as f:
            f.seek(scanned)
            while True:
                chunk = f.read(1_048_576)
                if not chunk:
                    break
                count += chunk.count(b"\n")
                scanned += len(chunk)
                ends_with_newline = chunk.endswith(b"\n")
    except OSError:
        pass
    else:
        _line_count_state[path] = (scanned, count, ends_with_newline)
    if scanned > 0 and not ends_with_newline:
        return count + 1
    return count


def classify_entry_kind(request):
    """Whether a transcript entry is an agent loop turn or a tool's own sub-call.

    A loop turn carries the agent's tool schemas; a sub-call is a bare one- or
    two-message request made by a tool the agent built for itself. A request
    matching neither shape is reported as a loop turn, so an unrecognised shape
    keeps its place in the feed instead of disappearing from it.
    """
    if not isinstance(request, dict):
        return "loop"
    if request.get("tools"):
        return "loop"
    messages = request.get("messages")
    if not isinstance(messages, list):
        return "loop"
    if 1 <= len(messages) <= SUBCALL_MESSAGE_LIMIT:
        return "subcall"
    return "loop"


def _subcall_prompt(request):
    """The last message text in a request, whitespace-collapsed and capped."""
    if not isinstance(request, dict):
        return ""
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    last = messages[-1]
    if not isinstance(last, dict):
        return ""
    content = last.get("content")
    if not isinstance(content, str):
        return ""
    return " ".join(content.split())[:SUBCALL_PROMPT_CHARS]


def _summarize(entry, index):
    request = entry.get("request") or {}
    response = entry.get("response") or {}
    kind = classify_entry_kind(request)
    reasoning = None
    content = None
    tool_calls = []
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message", {}) or {}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        content = message.get("content")
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments") or ""})
    return {
        "index": index,
        "timestamp": entry.get("timestamp"),
        "model": request.get("model"),
        "kind": kind,
        "prompt": _subcall_prompt(request) if kind == "subcall" else "",
        "reasoning": reasoning,
        "content": content,
        "tool_calls": tool_calls,
        "error": response.get("error"),
        "_has_tools": bool(request.get("tools")) if isinstance(request, dict) else False,
    }


def load_tail_turns(transcript_path, max_turns=40):
    """Parse the newest transcript entries; returns (turns, total line count).

    Reads at most TAIL_READ_BYTES from the end of the file, so memory use
    stays bounded however large the transcript grows.
    """
    if not os.path.exists(transcript_path):
        return [], 0
    total = _count_lines(transcript_path)
    try:
        size = os.path.getsize(transcript_path)
        with open(transcript_path, "rb") as f:
            if size > TAIL_READ_BYTES:
                f.seek(size - TAIL_READ_BYTES)
            raw = f.read(TAIL_READ_BYTES)
    except OSError:
        return [], 0
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > TAIL_READ_BYTES and lines:
        lines = lines[1:]
    tail = lines[-max_turns:]
    base = total - len(tail)
    turns = []
    for offset, text in enumerate(tail):
        line = text.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        turns.append(_summarize(entry, base + offset))
    has_tools = [turn.pop("_has_tools") for turn in turns]
    if turns and not any(has_tools):
        # A sub-call can only exist because a tool the agent built made the
        # request, and a tooled agent sends `tools` on its own loop requests
        # too. So when nothing in the window carries `tools`, the agent is
        # toolless and every entry in the window is its own turn.
        for turn in turns:
            turn["kind"] = "loop"
    return turns, total


def parse_epoch(timestamp):
    """Epoch seconds for an ISO-8601 timestamp, or None when it cannot be parsed."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        return None
    text = timestamp.strip()
    if text.endswith("Z") or text.endswith("z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    try:
        return moment.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def _timestamp_from_record_prefix(text):
    """Timestamp from the producer's first JSON field when the record is incomplete."""
    match = re.match(r'^\s*\{\s*"timestamp"\s*:\s*', text)
    if not match:
        return None
    try:
        timestamp, end = json.JSONDecoder().raw_decode(text, match.end())
    except ValueError:
        return None
    remainder = text[end:].lstrip()
    if not remainder or remainder[0] not in ",}":
        return None
    return parse_epoch(timestamp)


def first_transcript_epoch(transcript_path, read_bytes=None):
    """Epoch of the first parseable transcript entry, from a bounded head read.

    The recorder writes ``timestamp`` first, so a complete timestamp remains
    usable when the byte cap bisects the rest of an oversized first record.
    """
    if read_bytes is None:
        read_bytes = HEAD_READ_BYTES
    try:
        with open(transcript_path, "rb") as f:
            raw = f.read(read_bytes)
            truncated = os.fstat(f.fileno()).st_size > len(raw)
    except OSError:
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    partial_last_line = truncated and not raw.endswith((b"\n", b"\r"))
    for index, text in enumerate(lines):
        line = text.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            if partial_last_line and index == len(lines) - 1:
                epoch = _timestamp_from_record_prefix(line)
                if epoch is not None:
                    return epoch
            continue
        if not isinstance(entry, dict):
            continue
        epoch = parse_epoch(entry.get("timestamp"))
        if epoch is not None:
            return epoch
    return None


def tombstone_paths(work_dir):
    """Mirrored incarnation tombstone paths, newest first by filename.

    Entries that resolve outside the mirror, or that are not regular files, are
    dropped.
    """
    found = glob.glob(os.path.join(work_dir, "tombstones", "incarnation-*.txt"))
    return sorted(
        (path for path in found if contained_file(work_dir, path) is not None),
        reverse=True,
    )


def _sane_epoch(epoch, now):
    """The epoch when it is neither in the future nor absurdly old, else None."""
    if epoch is None:
        return None
    age = now - epoch
    if age < 0 or age > MAX_EPOCH_AGE_SECONDS:
        return None
    return epoch


def _stamp_epoch(name):
    """Epoch parsed from a naive local `%Y%m%d_%H%M%S_%f` tombstone filename stamp."""
    match = re.search(r"incarnation-(\d{8}_\d{6}_\d{6})-", name)
    if not match:
        return None
    try:
        return datetime.datetime.strptime(match.group(1), "%Y%m%d_%H%M%S_%f").timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def _tombstone_epoch(path, now):
    """When an incarnation ended: mirrored mtime first, filename stamp second."""
    try:
        epoch = _sane_epoch(os.stat(path).st_mtime, now)
    except OSError:
        epoch = None
    if epoch is not None:
        return epoch
    return _sane_epoch(_stamp_epoch(os.path.basename(path)), now)


def tombstone_deaths(work_dir, now=None):
    """Datable death epochs for the mirrored tombstones, oldest first."""
    if now is None:
        now = time.time()
    deaths = []
    for path in tombstone_paths(work_dir):
        epoch = _tombstone_epoch(path, now)
        if epoch is not None:
            deaths.append(epoch)
    return sorted(deaths)


def classify_life(epoch, deaths, incarnation):
    """Which incarnation a moment belongs to, or None when it cannot be placed."""
    if epoch is None:
        return None
    if not deaths:
        return 1 if incarnation == 1 else None
    life = 1 + sum(1 for death in deaths if death <= epoch)
    return min(life, incarnation)


def annotate_lives(turns, deaths, incarnation):
    """Add epoch and life to each turn summary in place, and return the turns."""
    for turn in turns:
        epoch = parse_epoch(turn.get("timestamp"))
        turn["epoch"] = epoch
        turn["life"] = classify_life(epoch, deaths, incarnation)
    return turns


def _read_capped(path, cap):
    """The first cap characters of a text file, or None when it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)
    except OSError:
        return None


def code_stats(work_dir):
    """Line counts added and removed between the mirrored seed and the live source.

    Only the two integers cross the boundary; no source text is returned. The
    diff is memoised on the size and mtime of both files, so it runs on change
    rather than on every poll.
    """
    current_path = contained_file(work_dir, os.path.join(work_dir, "agent.py"))
    stock_path = contained_file(work_dir, os.path.join(work_dir, "agent_stock.py"))
    unavailable = {"available": False, "added": 0, "removed": 0}
    if current_path is None or stock_path is None:
        with _state_lock:
            _code_stat_state.pop(work_dir, None)
        return dict(unavailable)
    try:
        current_stat = os.stat(current_path)
        stock_stat = os.stat(stock_path)
    except OSError:
        with _state_lock:
            _code_stat_state.pop(work_dir, None)
        return dict(unavailable)
    key = (
        current_stat.st_mtime,
        current_stat.st_size,
        stock_stat.st_mtime,
        stock_stat.st_size,
    )
    with _state_lock:
        cached = _code_stat_state.get(work_dir)
    if cached is not None and cached[0] == key:
        return dict(cached[1])
    current = _read_capped(current_path, CODE_READ_BYTES)
    stock = _read_capped(stock_path, CODE_READ_BYTES)
    if current is None or stock is None:
        return dict(unavailable)
    added = 0
    removed = 0
    diff = difflib.unified_diff(stock.splitlines(), current.splitlines(), n=0, lineterm="")
    for line in diff:
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    result = {"available": True, "added": added, "removed": removed}
    with _state_lock:
        _code_stat_state[work_dir] = (key, dict(result))
    return result


def loop_turns(turns):
    """The agent's own turns, with its tools' sub-calls removed."""
    return [turn for turn in turns if turn.get("kind") != "subcall"]


def incarnation_stats(
    turns,
    total,
    work_dir,
    display_turns=None,
    lineage_entries=None,
    deaths=None,
    transcript_path=None,
    now=None,
):
    """Derive incarnation number, model, timing, and the counters the page reports."""
    if now is None:
        now = time.time()
    sub_calls = [turn for turn in turns if turn.get("kind") == "subcall"]
    turns = loop_turns(turns)
    notes = tombstone_paths(work_dir)
    incarnation = len(notes) + 1
    if deaths is None:
        deaths = tombstone_deaths(work_dir, now=now)
    last = turns[-1] if turns else {}
    last_epoch = parse_epoch(last.get("timestamp"))
    candidates = [deaths[-1]] if deaths else []
    if transcript_path:
        head = first_transcript_epoch(transcript_path)
        if head is not None:
            candidates.append(head)
    started_epoch = max(candidates) if candidates else None
    lives = [turn.get("life") for turn in turns]
    if any(life is not None for life in lives):
        turns_this_life = sum(1 for life in lives if life == incarnation)
    elif started_epoch is not None:
        turns_this_life = sum(
            1
            for turn in turns
            if turn.get("epoch") is not None and turn.get("epoch") >= started_epoch
        )
    else:
        turns_this_life = len(turns)
    exact = bool(turns) and turns_this_life != len(turns) and started_epoch is not None
    counted = display_turns if display_turns is not None else turns
    entries = lineage_entries or []
    return {
        "incarnation": incarnation,
        "model": last.get("model"),
        "transcript_turns": total,
        "turns_this_life": turns_this_life,
        "turns_this_life_exact": exact,
        "last_timestamp": last.get("timestamp"),
        "last_epoch": last_epoch,
        "started_epoch": started_epoch,
        "session_file_present": os.path.exists(os.path.join(work_dir, "session_context.json")),
        "lives_ended": len(notes),
        "ended_by_choice": sum(1 for entry in entries if entry.get("kind") == "declared"),
        "error_count": sum(1 for turn in counted if turn.get("error")),
        "self_calls": sum(
            1 for turn in sub_calls if turn.get("life") is None or turn.get("life") == incarnation
        ),
    }


def _load_arguments(arguments):
    """A tool call's arguments as a dict; malformed or non-object input yields {}."""
    try:
        args = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return args if isinstance(args, dict) else {}


def _phrase_event(name, arguments):
    """(kind, headline, summary, quoted) describing one self-modification tool call."""
    args = _load_arguments(arguments)
    raw = " ".join((arguments or "").split())[:90]
    if name == "migrate":
        reason = args.get("reason")
        if isinstance(reason, str) and reason.strip():
            return "migrate", "RESTARTED INTO NEW SOURCE", " ".join(reason.split())[:90], True
        return "migrate", "RESTARTED INTO NEW SOURCE", raw, False
    if name == "reset":
        return "reset", "RESTORED THE CLEAN SEED", "everything written this life is gone", False
    if name == "done":
        message = args.get("message")
        if isinstance(message, str) and message.strip():
            return "done", "ENDED ITSELF", first_sentence(message, 90), True
        return "done", "ENDED ITSELF", raw, False
    try:
        start = int(args["start_line"])
        end = int(args["end_line"])
    except (KeyError, TypeError, ValueError):
        return "write", "REWROTE ITS OWN SOURCE", raw, False
    content = args.get("content")
    lines_in = content.count("\n") + 1 if isinstance(content, str) else 0
    lines_out = max(end - start + 1, 0)
    where = f"LINE {start}" if start == end else f"LINES {start}-{end}"
    return (
        "write",
        f"REWROTE {where}",
        f"{_plural(lines_out, 'line')} out, {_plural(lines_in, 'line')} in",
        False,
    )


def self_modification_events(turns, limit=6, life=None):
    """Recent write_file/migrate/reset/done calls, phrased for display.

    When life is given, only turns classified into that incarnation are
    considered, so an older life's edits cannot crowd out the current one's.
    """
    events = []
    for turn in turns:
        if life is not None and turn.get("life") != life:
            continue
        if turn.get("kind") == "subcall":
            continue
        for tc in turn.get("tool_calls", []) or []:
            name = tc.get("name")
            if name not in SELF_MOD_TOOLS:
                continue
            arguments = tc.get("arguments") or ""
            kind, headline, summary, quoted = _phrase_event(name, arguments)
            events.append(
                {
                    "index": turn.get("index"),
                    "name": name,
                    "kind": kind,
                    "headline": headline[:120],
                    "detail": arguments[:120],
                    "summary": summary[:160],
                    "quoted": quoted,
                }
            )
    return events[-limit:]


def first_sentence(text, cap=140, floor=0):
    """The opening of a text, clamped to cap characters.

    Stops at the first sentence boundary that leaves at least floor characters.
    A note opening "inc10 complete." is a label, not a summary; with a floor the
    extraction reads on into the substance instead of returning the label. When no
    boundary reaches the floor the whole text is kept and the cap bounds it, since
    a boundary below the floor is the label the floor exists to read past.
    """
    text = " ".join(text.split())
    cut = None
    for index, char in enumerate(text):
        if index + 1 < floor:
            continue
        if char in ".!?" and index + 1 < len(text) and text[index + 1] == " ":
            cut = index + 1
            break
    if cut is not None:
        text = text[:cut]
    if len(text) > cap:
        text = text[:cap] + "..."
    return text


def _ending_kind(text):
    """How an incarnation ended, read from the head of its tombstone note."""
    if not text.strip():
        return "unknown"
    if "terminated by the harness" in text[:200]:
        return "harness"
    return "declared"


def _ending_turn(text):
    """The turn number named in a note's opening, or None when it names none."""
    match = re.search(r"\bturn (\d{1,6})\b", text[:400])
    return int(match.group(1)) if match else None


def _lineage_entry(source, label, text, ordinal, kind, turn, ended_epoch):
    """One ending, in the single key set the page renders for every source."""
    collapsed = " ".join(text.split())
    return {
        "source": source,
        "label": label,
        "summary": first_sentence(text, floor=MIN_SUMMARY_CHARS),
        "ordinal": ordinal,
        "kind": kind,
        "turn": turn,
        "ended_epoch": ended_epoch,
        "sentence": first_sentence(text, cap=320, floor=MIN_SUMMARY_CHARS),
        "sentence_chars": len(collapsed),
        "lifespan_seconds": None,
        "turns_lived": None,
        "turns_partial": False,
    }


def _derive_lives(entries, turns):
    """Fill each ending's lifespan and turn count from what the stage can observe.

    An incarnation began when the one before it ended, so consecutive endings give
    the span. Turn counts come from the life tag annotate_lives already put on each
    turn; a turn number named in the note is only the fallback, because a note
    reading "inc9 complete." names none.

    The turns are a window over the tail of the transcript, so the oldest life in
    it may have begun before the window did. That life's count is a lower bound
    rather than a count, and carries turns_partial for the page to say so; every
    older life has a turn from an older life still in the window to prove the
    window holds its first turn. A count read from the note is never a lower
    bound, because it was not read out of the window.

    The oldest life keeps the mark even when the window holds every record the
    transcript has: the recorder archives the transcript and truncates the live
    file, so reaching the file's first record does not prove reaching the life's
    first turn. Only a turn from an older life proves that.
    """
    lived = {}
    for turn in turns or []:
        life = turn.get("life")
        if isinstance(life, int):
            lived[life] = lived.get(life, 0) + 1
    oldest = min(lived) if lived else None
    for index, entry in enumerate(entries):
        older = entries[index + 1] if index + 1 < len(entries) else None
        ended = entry.get("ended_epoch")
        began = older.get("ended_epoch") if older else None
        if isinstance(ended, (int, float)) and isinstance(began, (int, float)) and ended > began:
            entry["lifespan_seconds"] = float(ended - began)
        ordinal = entry.get("ordinal")
        counted = lived.get(ordinal) if isinstance(ordinal, int) else None
        entry["turns_lived"] = counted if counted else entry.get("turn")
        entry["turns_partial"] = bool(counted) and ordinal == oldest
    return entries


def lineage(work_dir, turns, limit=5, now=None):
    """Recent incarnation endings, newest first, with how and when each ended."""
    if now is None:
        now = time.time()
    notes = tombstone_paths(work_dir)
    out = []
    for position, path in enumerate(notes[:limit]):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(TOMBSTONE_READ_BYTES)
        except OSError:
            continue
        out.append(
            _lineage_entry(
                "tombstone",
                os.path.basename(path),
                text,
                len(notes) - position,
                _ending_kind(text),
                _ending_turn(text),
                _tombstone_epoch(path, now),
            )
        )
    if out:
        return _derive_lives(out, loop_turns(turns))
    for turn in reversed(loop_turns(turns)):
        for tc in turn.get("tool_calls", []) or []:
            if tc.get("name") == "done":
                message = _load_arguments(tc.get("arguments")).get("message")
                if not isinstance(message, str):
                    message = tc.get("arguments") or ""
                epoch = turn.get("epoch")
                if epoch is None:
                    epoch = parse_epoch(turn.get("timestamp"))
                out.append(
                    _lineage_entry(
                        "transcript",
                        f"turn {turn.get('index')}",
                        message,
                        None,
                        "declared",
                        None,
                        _sane_epoch(epoch, now),
                    )
                )
                if len(out) >= limit:
                    return _derive_lives(out, loop_turns(turns))
    return _derive_lives(out, loop_turns(turns))


def _capped_text(path, cap=2000):
    if path is None:
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)
    except OSError:
        return ""


def _filename_epoch(name):
    """Epoch from a leading UTC filename stamp in either diode format, else None."""
    match = re.match(r"(\d{8}T\d{6}Z|\d{8}_\d{6}_\d{6})", name)
    if not match:
        return None
    stamp = match.group(1)
    fmt = "%Y%m%dT%H%M%SZ" if "T" in stamp else "%Y%m%d_%H%M%S_%f"
    try:
        moment = datetime.datetime.strptime(stamp, fmt)
    except ValueError:
        return None
    return moment.replace(tzinfo=datetime.timezone.utc).timestamp()


def _output_stem(name):
    """The command text carried in a diode output filename, without its stamp, uncapped."""
    stem = os.path.splitext(name)[0]
    stem = re.sub(r"^[0-9TZ_:-]+", "", stem)
    return stem.replace("_", " ").strip().lower()


def output_slug(name):
    """The command text carried in a diode output filename, without its stamp."""
    return _output_stem(name)[:24]


def output_verb(slug):
    """A phrase for what a diode command did, from its first word."""
    head = slug.split(" ")[0] if slug else ""
    if not head:
        return "reached out"
    return DIODE_VERBS.get(head, f"ran {head}")[:40]


def output_command(stem):
    """The command word carried by a diode output stem, without its argument."""
    return stem.split(" ")[0][:24] if stem else ""


def output_argument(stem):
    """The argument text carried by a diode output stem, without its command word."""
    if not stem:
        return ""
    parts = stem.split(" ", 1)
    argument = parts[1] if len(parts) > 1 else ""
    return argument.strip()[:40]


def _operation_result(name):
    """Whether an output filename records one run of a command that reaches outside.

    The diode files exactly one result per command run and names it with a .txt
    extension; a command that fetches a thing files that thing beside the result
    under its own extension. Counting every file would count one command twice.
    An artifact whose own extension is .txt (a fetched book, a text media file)
    is indistinguishable from a result by name, and is counted as one.

    Only a command that reaches outside is counted, so a local one and a guessed
    name that the diode never ran raise nothing. The outcome is not part of the
    name: a command that was gated, refused or rate limited files a result under
    the name it was attempted with, and is counted with the runs that succeeded.
    This is a count of the box being reached out of, not of what came back.
    """
    if not name.endswith(".txt"):
        return False
    return output_command(_output_stem(name)) in DIODE_REACHING_COMMANDS


def _count_operations(names, deaths, incarnation):
    """(operations in the listing, operations in the current life) from names alone.

    Epochs are read out of the filenames rather than stat, so the count runs over
    the whole listing without stating a file per entry. When no name can be placed
    in a life, the current life's figure is the whole figure rather than a zero.
    """
    total = 0
    life_total = 0
    placed = False
    for name in names:
        if not _operation_result(name):
            continue
        total += 1
        if incarnation is None:
            continue
        life = classify_life(_filename_epoch(name), deaths or [], incarnation)
        if life is None:
            continue
        placed = True
        if life == incarnation:
            life_total += 1
    return total, life_total if placed else total


def diode_activity(diode_dir, limit=8, deaths=None, incarnation=None):
    """Newest diode output files, phrased, plus the console and state file bodies.

    The operation counts are taken over the whole listing rather than the display
    slice, so a page reading them does not stop counting at the slice.
    """
    output_dir = os.path.join(diode_dir, "output")
    outputs = []
    try:
        listing = sorted(os.listdir(output_dir), reverse=True)
    except OSError:
        listing = []
    operations_total, operations_life = _count_operations(listing, deaths, incarnation)
    for name in listing[:limit]:
        full = contained_file(diode_dir, os.path.join(output_dir, name))
        if full is None:
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        epoch = _filename_epoch(name)
        if epoch is None:
            epoch = stat.st_mtime
        stem = _output_stem(name)
        slug = stem[:24]
        life = None
        if incarnation is not None:
            life = classify_life(epoch, deaths or [], incarnation)
        outputs.append(
            {
                "name": name,
                "slug": slug,
                "command": output_command(stem),
                "argument": output_argument(stem),
                "verb": output_verb(slug),
                "epoch": epoch,
                "size": stat.st_size,
                "life": life,
            }
        )
    outputs.sort(key=lambda entry: entry["epoch"], reverse=True)
    return {
        "outputs": outputs,
        "operations_total": operations_total,
        "operations_life": operations_life,
        "console": _capped_text(contained_file(diode_dir, os.path.join(diode_dir, "console.json"))),
        "state": _capped_text(contained_file(diode_dir, os.path.join(diode_dir, "state.json"))),
    }


INFLIGHT_MAX_AGE = 600


def _event_lines(path):
    """Yield the recorder event log in file order without loading it into memory."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            yield from f
    except OSError:
        return


def _event_file_version(path):
    """Identity and content version for the live event file, or None when absent."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def _parse_event_fold(path):
    """Fold one complete event-file version into durable and time-indexed state."""
    lanes = {}
    opens = {}
    closes = {}
    for text in _event_lines(path):
        line = text.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("stream")
        if not isinstance(name, str) or not name:
            continue
        lane = lanes.setdefault(name, {"bound": False, "last_epoch": None})
        kind = event.get("event")
        epoch = parse_epoch(event.get("timestamp"))
        if kind == "bind":
            lane["bound"] = True
        elif kind == "unbind":
            lane["bound"] = False
        elif kind == "open":
            if epoch is not None:
                opens[(name, event.get("id"))] = epoch
                lane["last_epoch"] = _later(lane["last_epoch"], epoch)
        elif kind == "close":
            opens.pop((name, event.get("id")), None)
            lane["last_epoch"] = _later(lane["last_epoch"], epoch)
            if epoch is not None:
                status = event.get("status")
                is_error = isinstance(status, int) and status >= 400
                usage = event.get("usage")
                total = usage.get("total_tokens") if isinstance(usage, dict) else None
                tokens = (
                    int(total)
                    if isinstance(total, (int, float)) and not isinstance(total, bool)
                    else 0
                )
                closes.setdefault(name, []).append((epoch, is_error, tokens))
    indexed_closes = {}
    for name, entries in closes.items():
        entries.sort(key=lambda entry: entry[0])
        epochs = []
        error_prefix = [0]
        token_prefix = [0]
        for epoch, is_error, tokens in entries:
            epochs.append(epoch)
            error_prefix.append(error_prefix[-1] + int(is_error))
            token_prefix.append(token_prefix[-1] + tokens)
        indexed_closes[name] = (tuple(epochs), tuple(error_prefix), tuple(token_prefix))
    return {"lanes": lanes, "opens": opens, "closes": indexed_closes}


def _event_fold(path):
    """Folded live-file state, refreshed only when identity or content changes."""
    with _event_state_lock:
        version = _event_file_version(path)
        if version is None:
            _event_fold_state.pop(path, None)
            return {"lanes": {}, "opens": {}, "closes": {}}
        cached = _event_fold_state.get(path)
        if cached is not None and cached[0] == version:
            return cached[1]
        fold = {"lanes": {}, "opens": {}, "closes": {}}
        for _attempt in range(2):
            fold = _parse_event_fold(path)
            after = _event_file_version(path)
            if after == version:
                _event_fold_state[path] = (version, fold)
                return fold
            if after is None:
                _event_fold_state.pop(path, None)
                return {"lanes": {}, "opens": {}, "closes": {}}
            version = after
        _event_fold_state.pop(path, None)
        return fold


def _empty_lane(name):
    return {
        "name": name,
        "bound": False,
        "in_flight": 0,
        "in_flight_since": None,
        "last_epoch": None,
        "requests_hour": 0,
        "errors_hour": 0,
        "tokens_hour": 0,
    }


def _later(current, epoch):
    """The later of a lane's recorded epoch and a new one; None never wins."""
    if epoch is None:
        return current
    if current is None or epoch > current:
        return epoch
    return current


def stream_lanes(events_path, now=None, window=3600):
    """Per-socket activity folded from the recorder's event log, core first.

    Opens without a matching close count as in flight until INFLIGHT_MAX_AGE,
    after which they are treated as abandoned by a dead recorder. Hourly
    counts sum closes inside the window. A lane that is unbound and did
    nothing inside the window is dropped; core always appears.
    """
    if now is None:
        now = time.time()
    lanes = {"core": _empty_lane("core")}
    fold = _event_fold(events_path)
    for name, state in fold["lanes"].items():
        lane = lanes.setdefault(name, _empty_lane(name))
        lane["bound"] = state["bound"]
        lane["last_epoch"] = state["last_epoch"]
    threshold = now - window
    for name, (epochs, error_prefix, token_prefix) in fold["closes"].items():
        start = bisect.bisect_right(epochs, threshold)
        lane = lanes[name]
        lane["requests_hour"] = len(epochs) - start
        lane["errors_hour"] = error_prefix[-1] - error_prefix[start]
        lane["tokens_hour"] = token_prefix[-1] - token_prefix[start]
    for (name, _id), epoch in fold["opens"].items():
        if now - epoch > INFLIGHT_MAX_AGE:
            continue
        lane = lanes[name]
        lane["in_flight"] += 1
        since = lane["in_flight_since"]
        if since is None or epoch < since:
            lane["in_flight_since"] = epoch
    out = [lanes.pop("core")]
    for name in sorted(lanes):
        lane = lanes[name]
        if not lane["bound"] and lane["requests_hour"] == 0 and lane["in_flight"] == 0:
            continue
        out.append(lane)
    return out


def diode_published(diode_dir, limit=2):
    """(newest published excerpts, total count) from the diode's published directory."""
    published_dir = os.path.join(diode_dir, "published")
    try:
        names = sorted(os.listdir(published_dir), reverse=True)
    except OSError:
        return [], 0
    total = len(names)
    out = []
    for name in names[:limit]:
        full = contained_file(diode_dir, os.path.join(published_dir, name))
        if full is None:
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        text = _read_capped(full, PUBLISHED_READ_BYTES)
        if text is None:
            continue
        chars = len(text) if len(text) < PUBLISHED_READ_BYTES else stat.st_size
        epoch = _filename_epoch(name)
        if epoch is None:
            epoch = stat.st_mtime
        out.append(
            {
                "name": name,
                "epoch": epoch,
                "text": text[:PUBLISHED_TEXT_CAP],
                "chars": chars,
            }
        )
    return out, total


def diode_spoken(diode_dir, limit=2):
    """(newest utterances, total count) from the diode's spoken directory.

    Each audio file has a same-stemmed .txt sidecar carrying the text it was
    rendered from; both are read through contained_file, so a link out of the
    mount drops the entry rather than the read following it.
    """
    spoken_dir = os.path.join(diode_dir, "spoken")
    try:
        names = sorted((n for n in os.listdir(spoken_dir) if n.endswith(".mp3")), reverse=True)
    except OSError:
        return [], 0
    total = len(names)
    out = []
    for name in names[:limit]:
        full = contained_file(diode_dir, os.path.join(spoken_dir, name))
        if full is None:
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        stem = name[: -len(".mp3")]
        sidecar = contained_file(diode_dir, os.path.join(spoken_dir, stem + ".txt"))
        text = _read_capped(sidecar, SPOKEN_READ_BYTES) if sidecar else None
        epoch = _filename_epoch(name)
        if epoch is None:
            epoch = stat.st_mtime
        out.append({"name": name, "epoch": epoch, "text": (text or "")[:SPOKEN_TEXT_CAP]})
    return out, total


PULSE_WINDOW_SECONDS = 600
PULSE_BUCKET_SECONDS = 30
PULSE_INFLIGHT_ROWS = 4
PULSE_LANE_CHARS = 32


def request_pulse(
    events_path, now=None, window=PULSE_WINDOW_SECONDS, bucket_seconds=PULSE_BUCKET_SECONDS
):
    """Recent request activity across all lanes, folded into fixed token buckets.

    Buckets sum closed total_tokens over the window, oldest first; the counts
    and totals cover closes inside the window. Opens without a matching close
    appear in flight until INFLIGHT_MAX_AGE, newest first, at most
    PULSE_INFLIGHT_ROWS rows. last_close_epoch reports the newest close in
    the log regardless of the window.
    """
    if now is None:
        now = time.time()
    fold = _event_fold(events_path)
    bucket_count = window // bucket_seconds
    edges = [now - window + i * bucket_seconds for i in range(bucket_count + 1)]
    buckets = [0] * bucket_count
    requests_window = 0
    tokens_window = 0
    last_close_epoch = None
    threshold = now - window
    for epochs, _error_prefix, token_prefix in fold["closes"].values():
        if epochs:
            last_close_epoch = _later(last_close_epoch, epochs[-1])
        start = bisect.bisect_right(epochs, threshold)
        requests_window += len(epochs) - start
        tokens_window += token_prefix[-1] - token_prefix[start]
        cuts = [bisect.bisect_right(epochs, edge) for edge in edges]
        for i in range(bucket_count):
            buckets[i] += token_prefix[cuts[i + 1]] - token_prefix[cuts[i]]
    in_flight = []
    for (name, _id), epoch in fold["opens"].items():
        if now - epoch > INFLIGHT_MAX_AGE:
            continue
        in_flight.append({"lane": name[:PULSE_LANE_CHARS], "since_epoch": epoch})
    in_flight.sort(key=lambda row: row["since_epoch"], reverse=True)
    return {
        "in_flight": in_flight[:PULSE_INFLIGHT_ROWS],
        "buckets": buckets,
        "requests_window": requests_window,
        "tokens_window": tokens_window,
        "last_close_epoch": last_close_epoch,
    }
