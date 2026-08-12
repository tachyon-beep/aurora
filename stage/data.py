import glob
import json
import os

SELF_MOD_TOOLS = ("write_file", "migrate", "reset", "done")
TAIL_READ_BYTES = 4_000_000
TOMBSTONE_READ_BYTES = 4096

_line_count_state = {}


def _count_lines(path):
    """Count lines in a file, scanning only bytes appended since the previous call."""
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


def _summarize(entry, index):
    request = entry.get("request") or {}
    response = entry.get("response") or {}
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
        "reasoning": reasoning,
        "content": content,
        "tool_calls": tool_calls,
        "error": response.get("error"),
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
    return turns, total


def incarnation_stats(turns, total, work_dir):
    """Derive incarnation number, current model, and session-file presence."""
    notes = glob.glob(os.path.join(work_dir, "tombstones", "incarnation-*.txt"))
    last = turns[-1] if turns else {}
    return {
        "incarnation": len(notes) + 1,
        "model": last.get("model"),
        "transcript_turns": total,
        "last_timestamp": last.get("timestamp"),
        "session_file_present": os.path.exists(os.path.join(work_dir, "session_context.json")),
    }


def self_modification_events(turns, limit=12):
    """Collect recent write_file/migrate/reset/done tool calls from turn summaries."""
    events = []
    for turn in turns:
        for tc in turn.get("tool_calls", []) or []:
            if tc.get("name") in SELF_MOD_TOOLS:
                events.append(
                    {
                        "index": turn.get("index"),
                        "name": tc.get("name"),
                        "detail": (tc.get("arguments") or "")[:120],
                    }
                )
    return events[-limit:]


def first_sentence(text, cap=140):
    """The first sentence of a text, clamped to cap characters."""
    text = " ".join(text.split())
    for stop in (". ", "! ", "? "):
        if stop in text:
            text = text.split(stop, 1)[0] + stop.strip()
            break
    if len(text) > cap:
        text = text[:cap] + "..."
    return text


def lineage(work_dir, turns, limit=3):
    """One-line summaries of recent incarnation endings, newest first."""
    notes = sorted(
        glob.glob(os.path.join(work_dir, "tombstones", "incarnation-*.txt")),
        reverse=True,
    )
    out = []
    for path in notes[:limit]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read(TOMBSTONE_READ_BYTES)
        except OSError:
            continue
        out.append(
            {
                "source": "tombstone",
                "label": os.path.basename(path),
                "summary": first_sentence(text),
            }
        )
    if out:
        return out
    for turn in reversed(turns):
        for tc in turn.get("tool_calls", []) or []:
            if tc.get("name") == "done":
                try:
                    message = json.loads(tc.get("arguments") or "{}").get("message", "")
                except ValueError:
                    message = tc.get("arguments") or ""
                out.append(
                    {
                        "source": "transcript",
                        "label": f"turn {turn.get('index')}",
                        "summary": first_sentence(message),
                    }
                )
                if len(out) >= limit:
                    return out
    return out


def _capped_text(path, cap=2000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)
    except OSError:
        return ""


def diode_activity(diode_dir, limit=8):
    """Newest diode output files plus the console and state file bodies."""
    output_dir = os.path.join(diode_dir, "output")
    outputs = []
    try:
        names = sorted(os.listdir(output_dir), reverse=True)[:limit]
    except OSError:
        names = []
    for name in names:
        full = os.path.join(output_dir, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        outputs.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
    return {
        "outputs": outputs,
        "console": _capped_text(os.path.join(diode_dir, "console.json")),
        "state": _capped_text(os.path.join(diode_dir, "state.json")),
    }
