import bisect
import json
import os
import threading
import time

from stage import data

FIELD_CAP = 100_000
RAW_CAP = 400_000
DEFAULT_PAGE = 50
PAGE_LIMIT_MAX = 200
TIMESTAMP_CAP = 40
MODEL_CAP = 200
NAME_CAP = 200
ERROR_CAP = 2000

_index_lock = threading.Lock()
_index_state = {}


EDIT_TOOLS = ("write_file", "migrate")


def _edit_count(response):
    """How many self-edit tool calls the entry's first choice carries."""
    if not isinstance(response, dict):
        return 0
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return 0
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return 0
    calls = message.get("tool_calls")
    if not isinstance(calls, list):
        return 0
    count = 0
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function")
        if isinstance(fn, dict) and fn.get("name") in EDIT_TOOLS:
            count += 1
    return count


def _record(offset, raw):
    """One indexed line: (offset, length, epoch, stream, kind, has_error, edits)."""
    length = len(raw)
    text = raw.decode("utf-8", errors="replace").strip()
    entry = None
    if text:
        try:
            entry = json.loads(text)
        except ValueError:
            entry = None
    if not isinstance(entry, dict):
        return (offset, length, None, "", "invalid", False, 0)
    epoch = data.parse_epoch(entry.get("timestamp"))
    stream = entry.get("stream")
    if not isinstance(stream, str) or not stream:
        stream = "core"
    kind = data.classify_entry_kind(entry.get("request"))
    response = entry.get("response")
    has_error = isinstance(response, dict) and bool(response.get("error"))
    return (offset, length, epoch, stream, kind, has_error, _edit_count(response))


def _scan(path, scanned, records):
    """Extend the index with lines appended since the last scan.

    Only complete lines are indexed; a partial trailing line is left for the
    next scan, so the scanned position always sits on a line boundary.
    """
    with open(path, "rb") as f:
        f.seek(scanned)
        pending = b""
        position = scanned
        while True:
            chunk = f.read(1_048_576)
            if not chunk:
                break
            parts = (pending + chunk).split(b"\n")
            pending = parts.pop()
            for line in parts:
                records.append(_record(position, line))
                position += len(line) + 1
    return position, records


def _index(path):
    """The per-line index of a transcript, refreshed incrementally under a lock.

    The index resets when the file shrinks, so a rotated transcript is
    re-indexed from its new content rather than read through stale offsets.
    """
    with _index_lock:
        try:
            size = os.path.getsize(path)
        except OSError:
            _index_state.pop(path, None)
            return []
        scanned, records = _index_state.get(path, (0, []))
        if size < scanned:
            scanned, records = 0, []
        if size > scanned:
            records = list(records)
            try:
                scanned, records = _scan(path, scanned, records)
            except OSError:
                _index_state.pop(path, None)
                return []
            _index_state[path] = (scanned, records)
        return records


def _life_of(epoch, deaths, incarnation):
    """Which life an indexed entry belongs to; the unplaceable land in the current one."""
    life = data.classify_life(epoch, deaths, incarnation)
    return incarnation if life is None else life


def _clip_int(value, low, high):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, value))


def incarnations(transcript_path, work_dir, now=None):
    """Every incarnation, newest first, with its ending and indexed activity.

    Turn, sub-call, and error counts cover only entries present in the live
    transcript; a life whose entries have rotated into an archive still
    appears, counted at zero, because the tombstone mirror names it.
    """
    if now is None:
        now = time.time()
    records = _index(transcript_path)
    notes = data.tombstone_paths(work_dir)
    incarnation = len(notes) + 1
    deaths = data.tombstone_deaths(work_dir, now=now)
    counts = {}
    unplaced = 0
    for _offset, _length, epoch, stream, kind, has_error, edits in records:
        if kind == "invalid" or stream != "core":
            continue
        life = _life_of(epoch, deaths, incarnation)
        if data.classify_life(epoch, deaths, incarnation) is None:
            unplaced += 1
        bucket = counts.setdefault(life, {"turns": 0, "subcalls": 0, "errors": 0, "edits": 0})
        if kind == "subcall":
            bucket["subcalls"] += 1
        else:
            bucket["turns"] += 1
            bucket["edits"] += edits
        if has_error:
            bucket["errors"] += 1
    # The counts place each entry by its timestamp against the datable deaths.
    # When a tombstone cannot be dated, or an entry has no usable timestamp,
    # entries fold into the current life and the counts are no longer a per-life
    # measurement: they stay reported (the console shows what it can) but are
    # marked inexact so the stream page does not present them as figures.
    exact = len(deaths) == len(notes) and unplaced == 0
    endings = {}
    for position, path in enumerate(notes):
        ordinal = len(notes) - position
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(data.TOMBSTONE_READ_BYTES)
        except OSError:
            text = ""
        endings[ordinal] = {
            "label": os.path.basename(path),
            "summary": data.first_sentence(text, cap=320, floor=data.MIN_SUMMARY_CHARS),
            "ending_kind": data._ending_kind(text),
            "ended_epoch": data._tombstone_epoch(path, now),
        }
    # The first life began when the transcript did; every later life began
    # when the one before it ended.
    first_began = data.first_transcript_epoch(transcript_path)
    out = []
    for ordinal in range(incarnation, 0, -1):
        ending = endings.get(ordinal)
        previous = endings.get(ordinal - 1)
        bucket = counts.get(ordinal, {"turns": 0, "subcalls": 0, "errors": 0, "edits": 0})
        if previous:
            began = previous["ended_epoch"]
        elif ordinal == 1:
            began = data._sane_epoch(first_began, now)
        else:
            began = None
        out.append(
            {
                "ordinal": ordinal,
                "current": ordinal == incarnation,
                "began_epoch": began,
                "ended_epoch": ending["ended_epoch"] if ending else None,
                "ending_kind": ending["ending_kind"] if ending else None,
                "label": ending["label"] if ending else None,
                "summary": ending["summary"] if ending else "",
                "turns": bucket["turns"],
                "subcalls": bucket["subcalls"],
                "errors": bucket["errors"],
                "edits": bucket["edits"],
                "exact": exact,
            }
        )
    return out


def _read_line(path, offset, length):
    """The raw bytes of one indexed line, or None when the read no longer fits."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            raw = f.read(length)
    except OSError:
        return None
    if len(raw) != length:
        return None
    return raw


def _load_entry(path, offset, length):
    raw = _read_line(path, offset, length)
    if raw is None:
        return None
    try:
        entry = json.loads(raw.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return entry if isinstance(entry, dict) else None


def _capped(value, cap):
    """(clipped text, true length, whether it was clipped) for one text field."""
    text = value if isinstance(value, str) else ""
    return text[:cap], len(text), len(text) > cap


def _error_detail(error):
    if error is None:
        return None
    if isinstance(error, dict):
        code = error.get("code")
        if isinstance(code, bool) or not isinstance(code, (int, str)):
            code = None
        elif isinstance(code, str):
            code = code[:NAME_CAP]
        return {"message": str(error.get("message", ""))[:ERROR_CAP], "code": code}
    return {"message": str(error)[:ERROR_CAP], "code": None}


def _turn_detail(entry, index, kind, epoch, stream):
    """One turn with full operator-level detail, every text field capped."""
    request = entry.get("request") or {}
    response = entry.get("response") or {}
    if not isinstance(request, dict):
        request = {}
    if not isinstance(response, dict):
        response = {}
    message = {}
    choices = response.get("choices") or []
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if not isinstance(message, dict):
            message = {}
    reasoning, reasoning_chars, reasoning_truncated = _capped(
        message.get("reasoning_content") or message.get("reasoning"), FIELD_CAP
    )
    content, content_chars, content_truncated = _capped(message.get("content"), FIELD_CAP)
    tool_calls = []
    raw_calls = message.get("tool_calls")
    for tc in raw_calls if isinstance(raw_calls, list) else []:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function") or {}
        if not isinstance(fn, dict):
            fn = {}
        arguments, arguments_chars, arguments_truncated = _capped(fn.get("arguments"), FIELD_CAP)
        tool_calls.append(
            {
                "name": str(fn.get("name") or "")[:NAME_CAP] or None,
                "arguments": arguments,
                "arguments_chars": arguments_chars,
                "arguments_truncated": arguments_truncated,
            }
        )
    return {
        "index": index,
        "kind": kind,
        "stream": stream,
        "timestamp": str(entry.get("timestamp") or "")[:TIMESTAMP_CAP] or None,
        "epoch": epoch,
        "model": str(request.get("model") or "")[:MODEL_CAP] or None,
        "prompt": data._subcall_prompt(request) if kind == "subcall" else "",
        "reasoning": reasoning,
        "reasoning_chars": reasoning_chars,
        "reasoning_truncated": reasoning_truncated,
        "content": content,
        "content_chars": content_chars,
        "content_truncated": content_truncated,
        "tool_calls": tool_calls,
        "error": _error_detail(response.get("error")),
    }


def incarnation_turns(transcript_path, work_dir, life, offset=0, limit=DEFAULT_PAGE, now=None):
    """One life's core-stream turns, newest first, paginated with full detail."""
    if now is None:
        now = time.time()
    records = _index(transcript_path)
    notes = data.tombstone_paths(work_dir)
    incarnation = len(notes) + 1
    deaths = data.tombstone_deaths(work_dir, now=now)
    life = _clip_int(life, 1, incarnation)
    matching = [
        index
        for index, (_offset, _length, epoch, stream, kind, _has_error, _edits) in enumerate(records)
        if kind != "invalid" and stream == "core" and _life_of(epoch, deaths, incarnation) == life
    ]
    offset = _clip_int(offset, 0, len(matching))
    limit = _clip_int(limit, 1, PAGE_LIMIT_MAX)
    matching.reverse()
    turns = []
    for index in matching[offset : offset + limit]:
        line_offset, length, epoch, stream, kind, _has_error, _edits = records[index]
        entry = _load_entry(transcript_path, line_offset, length)
        if entry is None:
            continue
        turns.append(_turn_detail(entry, index, kind, epoch, stream))
    return {"life": life, "total": len(matching), "offset": offset, "turns": turns}


def life_turns(transcript_path, work_dir, life, now=None):
    """One life's core-stream loop turns, oldest first, yielded as (index, epoch, entry).

    Sub-calls are left out: the digest reads the agent's own turns. A line
    that no longer loads (rotated away between the index and the read) is
    skipped. A generator, so a long life is never held as parsed entries at
    once; callers keep only what they render.
    """
    if now is None:
        now = time.time()
    records = _index(transcript_path)
    notes = data.tombstone_paths(work_dir)
    incarnation = len(notes) + 1
    deaths = data.tombstone_deaths(work_dir, now=now)
    life = _clip_int(life, 1, incarnation)
    for index, (offset, length, epoch, stream, kind, _has_error, _edits) in enumerate(records):
        if kind != "loop" or stream != "core":
            continue
        if _life_of(epoch, deaths, incarnation) != life:
            continue
        loaded = _load_entry(transcript_path, offset, length)
        if loaded is None:
            continue
        yield (index, epoch, loaded)


def entry(transcript_path, index):
    """One transcript record by line index, pretty-printed and capped, or None."""
    records = _index(transcript_path)
    if not isinstance(index, int) or not 0 <= index < len(records):
        return None
    offset, length, _epoch, _stream, _kind, _has_error, _edits = records[index]
    raw = _read_line(transcript_path, offset, length)
    if raw is None:
        return None
    text = raw.decode("utf-8", errors="replace")
    try:
        text = json.dumps(json.loads(text), indent=2)
    except ValueError:
        pass
    return {
        "index": index,
        "raw": text[:RAW_CAP],
        "chars": len(text),
        "truncated": len(text) > RAW_CAP,
    }


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
        "requests_total": 0,
        "errors_total": 0,
        "tokens_total": 0,
        "transcript_entries": 0,
    }


def streams(transcript_path, events_path, now=None, window=3600):
    """Every lane ever seen, core first, with hourly and lifetime figures.

    Unlike the public page's lane list, nothing is dropped or capped: an
    unbound, idle lane still appears, and lanes named only by transcript
    entries appear with their entry counts.
    """
    if now is None:
        now = time.time()
    lanes = {"core": _empty_lane("core")}
    fold = data._event_fold(events_path)
    for name, state in fold["lanes"].items():
        lane = lanes.setdefault(name, _empty_lane(name))
        lane["bound"] = state["bound"]
        lane["last_epoch"] = state["last_epoch"]
    threshold = now - window
    for name, (epochs, error_prefix, token_prefix) in fold["closes"].items():
        lane = lanes.setdefault(name, _empty_lane(name))
        start = bisect.bisect_right(epochs, threshold)
        lane["requests_hour"] = len(epochs) - start
        lane["errors_hour"] = error_prefix[-1] - error_prefix[start]
        lane["tokens_hour"] = token_prefix[-1] - token_prefix[start]
        lane["requests_total"] = len(epochs)
        lane["errors_total"] = error_prefix[-1]
        lane["tokens_total"] = token_prefix[-1]
    for (name, _id), epoch in fold["opens"].items():
        if now - epoch > data.INFLIGHT_MAX_AGE:
            continue
        lane = lanes.setdefault(name, _empty_lane(name))
        lane["in_flight"] += 1
        since = lane["in_flight_since"]
        if since is None or epoch < since:
            lane["in_flight_since"] = epoch
    for _offset, _length, _epoch, stream, kind, _has_error, _edits in _index(transcript_path):
        if kind == "invalid":
            continue
        lane = lanes.setdefault(stream, _empty_lane(stream))
        lane["transcript_entries"] += 1
    out = [lanes.pop("core")]
    for name in sorted(lanes):
        out.append(lanes[name])
    return out


def stream_requests(transcript_path, name, offset=0, limit=DEFAULT_PAGE):
    """One lane's transcript entries, newest first, as request summary rows."""
    records = _index(transcript_path)
    matching = [
        index
        for index, (_offset, _length, _epoch, stream, kind, _has_error, _edits) in enumerate(
            records
        )
        if kind != "invalid" and stream == name
    ]
    offset = _clip_int(offset, 0, len(matching))
    limit = _clip_int(limit, 1, PAGE_LIMIT_MAX)
    matching.reverse()
    requests = []
    for index in matching[offset : offset + limit]:
        line_offset, length, epoch, _stream, _kind, _has_error, _edits = records[index]
        record = _load_entry(transcript_path, line_offset, length)
        if record is None:
            continue
        requests.append(_request_row(record, index, epoch))
    return {"name": name, "total": len(matching), "offset": offset, "requests": requests}


def _request_row(record, index, epoch):
    request = record.get("request") or {}
    response = record.get("response") or {}
    if not isinstance(request, dict):
        request = {}
    if not isinstance(response, dict):
        response = {}
    messages = request.get("messages")
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    content = None
    choices = response.get("choices") or []
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message") or {}
        if isinstance(message, dict):
            content = message.get("content")
    error = response.get("error")
    if isinstance(error, dict):
        error = str(error.get("message", ""))[:ERROR_CAP]
    elif error is not None:
        error = str(error)[:ERROR_CAP]
    return {
        "index": index,
        "timestamp": str(record.get("timestamp") or "")[:TIMESTAMP_CAP] or None,
        "epoch": epoch,
        "model": str(request.get("model") or "")[:MODEL_CAP] or None,
        "messages": len(messages) if isinstance(messages, list) else 0,
        "prompt_tokens": _token_count(usage.get("prompt_tokens")),
        "completion_tokens": _token_count(usage.get("completion_tokens")),
        "total_tokens": _token_count(usage.get("total_tokens")),
        "error": error,
        "content_chars": len(content) if isinstance(content, str) else 0,
    }


def _token_count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)
