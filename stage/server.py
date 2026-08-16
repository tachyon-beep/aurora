import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from stage import (
    browse,
    codewatch,
    commentary,
    data,
    desk,
    diag,
    diag_page,
    pages,
    records,
    sensecam,
)

try:
    from stage import summary
except Exception:
    summary = None

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
SENSE_DIR = os.environ.get("SENSE_DIR", "/sense")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "8091"))
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8092"))

DISPLAY_TURNS = 6
DISPLAY_SUBCALLS = 3
DISPLAY_OUTPUTS = 4
DISPLAY_PUBLISHED = 2
DISPLAY_SPOKEN = 2
TEXT_CAP = 8000
ARGUMENTS_CAP = 400
ERROR_CAP = 600
CODE_CAP = 40
STORY_CAP = 1200
MODEL_CAP = 60
NAME_CAP = 64
LANES_CAP = 9
LANE_NAME_CAP = 32
TIMESTAMP_CAP = 40
PROMPT_CAP = 160
AUDIO_MAX_BYTES = 4_000_000

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; media-src 'self'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
}


def _sanitize_header_value(value):
    """Strip CR, LF, and double-quote so a value is safe to embed in a response header."""
    return value.replace("\r", "").replace("\n", "").replace('"', "")


def browse_roots():
    """The directories the console browser may serve, by public name."""
    return {
        "telemetry": TELEMETRY_DIR,
        "transcripts": TRANSCRIPT_DIR,
        "diode": DIODE_DIR,
    }


def console_token():
    """The operator token; empty means the console is disabled."""
    return os.environ.get("STAGE_CONSOLE_TOKEN", "")


class _BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _reject_method(self):
        self._send(405, json.dumps({"error": "method not allowed"}))

    do_POST = _reject_method
    do_PUT = _reject_method
    do_DELETE = _reject_method
    do_PATCH = _reject_method


class ConsoleHandler(_BaseHandler):
    def _authorized(self):
        token = console_token()
        if not token:
            return None
        query = parse_qs(urlparse(self.path).query)
        supplied = self.headers.get("X-Console-Token") or query.get("token", [""])[0]
        return hmac.compare_digest(
            supplied.encode("utf-8", "surrogateescape"), token.encode("utf-8", "surrogateescape")
        )

    def do_GET(self):
        auth = self._authorized()
        if auth is None:
            self._send(403, json.dumps({"error": "console disabled: no token configured"}))
            return
        if not auth:
            self._send(401, json.dumps({"error": "missing or invalid token"}))
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path
        if route == "/":
            self._send(200, pages.CONSOLE_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/diag":
            self._send(200, diag_page.DIAG_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/api/diag/incarnations":
            self._send(
                200,
                json.dumps(
                    {"incarnations": diag.incarnations(transcript_path(), self._work_dir())}
                ),
            )
        elif route == "/api/diag/incarnation":
            self._handle_diag_incarnation(query)
        elif route == "/api/diag/streams":
            self._send(
                200,
                json.dumps({"streams": diag.streams(transcript_path(), events_path())}),
            )
        elif route == "/api/diag/stream":
            self._handle_diag_stream(query)
        elif route == "/api/diag/entry":
            self._handle_diag_entry(query)
        elif route == "/api/roots":
            self._send(200, json.dumps(sorted(browse_roots())))
        elif route == "/api/browse":
            self._handle_browse(query)
        elif route == "/api/file":
            self._handle_file(query)
        elif route == "/download":
            self._handle_download(query)
        elif route == "/api/diff":
            self._handle_diff()
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _resolve(self, query):
        root_name = query.get("root", [""])[0]
        rel = query.get("path", [""])[0]
        root = browse_roots().get(root_name)
        if root is None:
            return None
        return browse.resolve_within(root, rel)

    def _handle_browse(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isdir(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, json.dumps({"entries": browse.list_directory(target)}))

    def _handle_file(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isfile(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        tail = query.get("tail", ["0"])[0] == "1"
        self._send(200, json.dumps(browse.read_text_preview(target, tail=tail)))

    def _handle_download(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isfile(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        name = _sanitize_header_value(os.path.basename(target))
        try:
            size = os.path.getsize(target)
            f = open(target, "rb")
        except OSError:
            self._send(404, json.dumps({"error": "not found"}))
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
            self.end_headers()
            remaining = size
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _handle_diff(self):
        work = os.path.join(TELEMETRY_DIR, "work")
        current = data.contained_file(TELEMETRY_DIR, os.path.join(work, "agent.py"))
        stock = data.contained_file(TELEMETRY_DIR, os.path.join(work, "agent_stock.py"))
        if current is None or stock is None:
            self._send(404, json.dumps({"error": "mirror not available"}))
            return
        text = browse.unified_diff_text(stock, current, "agent_stock.py", "agent.py")
        self._send(200, json.dumps({"diff": text}))

    def _work_dir(self):
        return os.path.join(TELEMETRY_DIR, "work")

    @staticmethod
    def _query_int(query, key):
        value = query.get(key, [""])[0]
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _handle_diag_incarnation(self, query):
        work = self._work_dir()
        life = self._query_int(query, "life")
        if life is None:
            life = len(data.tombstone_paths(work)) + 1
        offset = self._query_int(query, "offset") or 0
        limit = self._query_int(query, "limit") or diag.DEFAULT_PAGE
        page = diag.incarnation_turns(transcript_path(), work, life, offset=offset, limit=limit)
        self._send(200, json.dumps(page))

    def _handle_diag_stream(self, query):
        name = query.get("name", [""])[0]
        if not name:
            self._send(404, json.dumps({"error": "not found"}))
            return
        offset = self._query_int(query, "offset") or 0
        limit = self._query_int(query, "limit") or diag.DEFAULT_PAGE
        page = diag.stream_requests(transcript_path(), name, offset=offset, limit=limit)
        self._send(200, json.dumps(page))

    def _handle_diag_entry(self, query):
        index = self._query_int(query, "index")
        record = diag.entry(transcript_path(), index) if index is not None else None
        if record is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, json.dumps(record))


def transcript_path():
    """The recorder's JSONL transcript file path."""
    return os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")


def events_path():
    """The recorder's telemetry event log path."""
    return os.path.join(TRANSCRIPT_DIR, "events.jsonl")


def _public_lane(lane):
    """A lane with every field enumerated and capped for public display."""
    since = lane.get("in_flight_since")
    last = lane.get("last_epoch")
    return {
        "name": _clip(str(lane.get("name") or ""), LANE_NAME_CAP),
        "bound": bool(lane.get("bound")),
        "in_flight": int(lane.get("in_flight") or 0),
        "in_flight_since": float(since) if isinstance(since, (int, float)) else None,
        "last_epoch": float(last) if isinstance(last, (int, float)) else None,
        "requests_hour": int(lane.get("requests_hour") or 0),
        "errors_hour": int(lane.get("errors_hour") or 0),
        "tokens_hour": int(lane.get("tokens_hour") or 0),
    }


def _lane_rank(lane):
    """Sort key ordering lanes by how much a viewer needs to see them.

    stream_lanes retains a recently active unbound lane for an hour and sorts the
    rest by name, so after a round of renaming the list can outrun the sockets that
    exist, with retired lanes sorted ahead of live ones. Bound lanes can never
    outnumber the sockets, so ranking them first means the cap only ever drops
    lanes that are already retired. last_epoch is None until a lane opens or closes
    a request, so it is ranked as older than any recorded epoch rather than
    compared with one.
    """
    last = lane.get("last_epoch")
    epoch = float(last) if isinstance(last, (int, float)) else float("-inf")
    return (
        lane.get("name") != "core",
        not lane.get("bound"),
        not (lane.get("in_flight") or 0),
        -epoch,
        str(lane.get("name") or ""),
    )


def _public_lanes(lanes):
    """The public lane list, ranked so the cap drops the least live lanes first."""
    return [_public_lane(lane) for lane in sorted(lanes, key=_lane_rank)[:LANES_CAP]]


def _lanes_omitted(lanes):
    """How many lanes the cap dropped, so the page can disclose them."""
    return max(0, len(lanes) - LANES_CAP)


def _clip(text, cap):
    """The first cap characters of text; falsy input yields an empty string."""
    if not text:
        return ""
    return text[:cap]


def _cap_subcall_runs(turns, cap):
    """Keep at most cap sub-calls in every run that follows a loop turn.

    A run's newest members, the ones nearest the loop turn that follows (or
    nearest now, for a run still in progress), are kept; its oldest are
    dropped from the front of the run. This bounds both payload size and
    on-screen sub-row count regardless of how many times a tool calls the
    model between one loop turn and the next.
    """
    out = []
    run_start = None
    for turn in turns:
        if turn.get("kind") == "subcall":
            if run_start is None:
                run_start = len(out)
            out.append(turn)
            continue
        if run_start is not None:
            out[run_start:] = out[run_start:][-cap:]
            run_start = None
        out.append(turn)
    if run_start is not None:
        out[run_start:] = out[run_start:][-cap:]
    return out


def select_display(turns, count=DISPLAY_TURNS, subcall_cap=DISPLAY_SUBCALLS):
    """The newest count loop turns, with the sub-calls that follow each of them.

    Sub-calls do not consume display slots: a tool that calls the model many
    times cannot push the agent's own turns out of the feed. Each parent's
    sub-calls are separately capped so neither the payload nor the on-screen
    sub-row count grows unbounded.
    """
    keep = []
    loops = 0
    for turn in reversed(turns):
        if turn.get("kind") != "subcall":
            if loops == count:
                break
            loops += 1
        keep.append(turn)
    keep.reverse()
    while keep and keep[0].get("kind") == "subcall":
        keep.pop(0)
    return _cap_subcall_runs(keep, subcall_cap)


def _clipped_field(value, cap):
    """(clipped text, true length, whether it was clipped) for one public text field."""
    text = value if isinstance(value, str) else ""
    return text[:cap], len(text), len(text) > cap


def _error_code(code):
    """An upstream error code as an int or a short string; other shapes yield None."""
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    if isinstance(code, str):
        return code[:CODE_CAP]
    return None


def _public_error(error):
    """An upstream error as a capped message and a code, whatever shape it arrived in."""
    if error is None:
        return None
    if isinstance(error, dict):
        return {
            "message": _clip(str(error.get("message", "")), ERROR_CAP),
            "code": _error_code(error.get("code")),
        }
    return {"message": _clip(str(error), ERROR_CAP), "code": None}


def _public_subcall(turn):
    """A sub-call's public dict: only what updateSubRows renders, nothing else.

    A sub-call has no reasoning, say, or tool-call blocks in the page, so its
    model's own reasoning and content text are dropped here rather than
    carried to the client unread.
    """
    return {
        "index": turn.get("index"),
        "kind": "subcall",
        "prompt": _clip(str(turn.get("prompt") or ""), PROMPT_CAP),
        "timestamp": _clip(str(turn.get("timestamp") or ""), TIMESTAMP_CAP) or None,
        "epoch": turn.get("epoch"),
        "life": turn.get("life"),
    }


def _public_turn(turn):
    """A turn summary with every field enumerated and capped for public display."""
    if turn.get("kind") == "subcall":
        return _public_subcall(turn)
    reasoning, reasoning_chars, reasoning_truncated = _clipped_field(
        turn.get("reasoning"), TEXT_CAP
    )
    content, content_chars, content_truncated = _clipped_field(turn.get("content"), TEXT_CAP)
    tool_calls = []
    names = []
    for tc in turn.get("tool_calls") or []:
        arguments, arguments_chars, arguments_truncated = _clipped_field(
            tc.get("arguments"), ARGUMENTS_CAP
        )
        name = _clip(str(tc.get("name") or ""), NAME_CAP) or None
        names.append(name)
        tool_calls.append(
            {
                "name": name,
                "arguments": arguments,
                "arguments_chars": arguments_chars,
                "arguments_truncated": arguments_truncated,
            }
        )
    return {
        "index": turn.get("index"),
        "kind": "loop",
        "prompt": _clip(str(turn.get("prompt") or ""), PROMPT_CAP),
        "timestamp": _clip(str(turn.get("timestamp") or ""), TIMESTAMP_CAP) or None,
        "epoch": turn.get("epoch"),
        "life": turn.get("life"),
        "reasoning": reasoning,
        "reasoning_chars": reasoning_chars,
        "reasoning_truncated": reasoning_truncated,
        "content": content,
        "content_chars": content_chars,
        "content_truncated": content_truncated,
        "tool_calls": tool_calls,
        "error": _public_error(turn.get("error")),
        "is_edit": any(name in ("write_file", "migrate") for name in names),
        "is_end": any(name == "done" for name in names),
    }


def _public_story():
    """The generated recap, capped, or None when the summariser is off or unavailable."""
    if summary is None:
        return None
    try:
        story = summary.cached_story()
    except Exception:
        return None
    if not isinstance(story, dict):
        return None
    text = story.get("text")
    if not isinstance(text, str) or not text.strip():
        return None
    generated_at = story.get("generated_at")
    if isinstance(generated_at, bool) or not isinstance(generated_at, (int, float)):
        generated_at = time.time()
    return {
        "text": text[:STORY_CAP],
        "generated_at": float(generated_at),
        "model": _clip(str(story.get("model") or ""), MODEL_CAP),
    }


DIFF_EXCERPT_CAP = 1600
DESK_LINE_CAP = 160
DESK_EVIDENCE_CAP = 120
DESK_DEPTH_CAP = 20
DESK_VERDICTS_CAP = 5
PULSE_BUCKET_COUNT = data.PULSE_WINDOW_SECONDS // data.PULSE_BUCKET_SECONDS
FEED_CAP = 8


def _public_pulse(pulse):
    """The request pulse with every field enumerated and capped for public display."""
    pulse = pulse if isinstance(pulse, dict) else {}
    rows = []
    for row in (pulse.get("in_flight") or [])[: data.PULSE_INFLIGHT_ROWS]:
        since = row.get("since_epoch")
        rows.append(
            {
                "lane": _clip(str(row.get("lane") or ""), data.PULSE_LANE_CHARS),
                "since_epoch": float(since) if isinstance(since, (int, float)) else None,
            }
        )
    buckets = [int(n) for n in (pulse.get("buckets") or [])][:PULSE_BUCKET_COUNT]
    buckets += [0] * (PULSE_BUCKET_COUNT - len(buckets))
    last = pulse.get("last_close_epoch")
    return {
        "in_flight": rows,
        "buckets": buckets,
        "requests_window": int(pulse.get("requests_window") or 0),
        "tokens_window": int(pulse.get("tokens_window") or 0),
        "last_close_epoch": float(last) if isinstance(last, (int, float)) else None,
    }


def _empty_pulse():
    """The pulse key set with nothing in it."""
    return _public_pulse({})


def _public_edit(edit):
    """The latest observed change to the agent source, capped, or None."""
    if not isinstance(edit, dict):
        return None
    epoch = edit.get("epoch")
    return {
        "epoch": float(epoch) if isinstance(epoch, (int, float)) else None,
        "added": int(edit.get("added") or 0),
        "removed": int(edit.get("removed") or 0),
        "excerpt": _clip(str(edit.get("excerpt") or ""), DIFF_EXCERPT_CAP),
        "restored": bool(edit.get("restored")),
    }


def _public_records(book):
    """The record book with every field enumerated for public display."""
    book = book if isinstance(book, dict) else {}
    longest = book.get("longest_life")
    if isinstance(longest, dict) and isinstance(longest.get("seconds"), (int, float)):
        longest = {
            "ordinal": int(longest.get("ordinal") or 0),
            "seconds": float(longest["seconds"]),
        }
    else:
        longest = None
    gap = book.get("most_recent_gap_seconds")
    return {
        "lives_ended": int(book.get("lives_ended") or 0),
        "chose": int(book.get("chose") or 0),
        "longest_life": longest,
        "most_recent_gap_seconds": float(gap) if isinstance(gap, (int, float)) else None,
    }


def _empty_records():
    """The record-book key set with nothing in it."""
    return _public_records({})


def _public_desk(package):
    """The analyst verdicts, capped, or None when the desk has nothing."""
    if not isinstance(package, dict):
        return None
    verdicts = []
    for verdict in (package.get("verdicts") or [])[:DESK_VERDICTS_CAP]:
        if not isinstance(verdict, dict):
            continue
        stars = verdict.get("stars")
        if not isinstance(stars, int) or isinstance(stars, bool):
            continue
        verdicts.append(
            {
                "ordinal": int(verdict.get("ordinal") or 0),
                "stars": min(5, max(1, stars)),
                "line": _clip(str(verdict.get("line") or ""), DESK_LINE_CAP),
                "evidence": _clip(str(verdict.get("evidence") or ""), DESK_EVIDENCE_CAP),
                "depth": _clip(str(verdict.get("depth") or ""), DESK_DEPTH_CAP),
            }
        )
    if not verdicts:
        return None
    generated_at = package.get("generated_at")
    return {
        "verdicts": verdicts,
        "generated_at": float(generated_at) if isinstance(generated_at, (int, float)) else None,
        "model": _clip(str(package.get("model") or ""), MODEL_CAP),
        "duration_seconds": int(package.get("duration_seconds") or 20),
    }


def _public_sense(frame):
    """The newest sense frame as a servable reference, or None."""
    if not isinstance(frame, dict):
        return None
    feed = _clip(str(frame.get("feed") or ""), FEED_CAP)
    name = str(frame.get("name") or "")
    epoch = frame.get("captured_epoch")
    if not feed or not name:
        return None
    return {
        "feed": feed,
        "url": "/frame/" + quote(feed, safe="") + "/" + quote(name, safe=""),
        "captured_epoch": float(epoch) if isinstance(epoch, (int, float)) else None,
    }


def _empty_snapshot(now):
    """The full key set with nothing in it, so a failed read still renders a page."""
    return {
        "now": now,
        "stats": {
            "incarnation": 1,
            "model": None,
            "transcript_turns": 0,
            "turns_this_life": 0,
            "turns_this_life_exact": False,
            "last_timestamp": None,
            "last_epoch": None,
            "started_epoch": None,
            "session_file_present": False,
            "lives_ended": 0,
            "ended_by_choice": 0,
            "error_count": 0,
            "self_calls": 0,
        },
        "code": {"available": False, "added": 0, "removed": 0, "latest_edit": None},
        "turns": [],
        "events": [],
        "lanes": [],
        "lanes_omitted": 0,
        "pulse": _empty_pulse(),
        "records": _empty_records(),
        "desk": None,
        "sense": None,
        "diode": {
            "outputs": [],
            "operations_total": 0,
            "operations_life": 0,
            "published": [],
            "published_total": 0,
            "spoken": [],
            "spoken_total": 0,
        },
        "lineage": [],
        "story": None,
        "commentary": {
            "play": commentary.play_by_play([], {}, {}),
            "colour": {
                "text": commentary.BEAT_TEMPLATES["working"],
                "generated": False,
                "beat": commentary.working_beat_id(),
                "evidence": "",
            },
        },
    }


def _assemble_snapshot(now):
    """Read every source and project it into the public snapshot shape."""
    work = os.path.join(TELEMETRY_DIR, "work")
    turns, total = data.load_tail_turns(transcript_path())
    incarnation = len(data.tombstone_paths(work)) + 1
    deaths = data.tombstone_deaths(work, now=now)
    data.annotate_lives(turns, deaths, incarnation)
    display = select_display(turns)
    lineage = data.lineage(work, turns, limit=5, now=now)
    stats = data.incarnation_stats(
        turns,
        total,
        work,
        display_turns=data.loop_turns(display),
        lineage_entries=lineage,
        deaths=deaths,
        transcript_path=transcript_path(),
        now=now,
    )
    stats["model"] = _clip(str(stats.get("model") or ""), MODEL_CAP) or None
    life = stats["incarnation"] if any(t.get("life") is not None for t in turns) else None
    lanes = data.stream_lanes(events_path(), now=now)
    diode = data.diode_activity(DIODE_DIR, deaths=deaths, incarnation=incarnation)
    published, published_total = data.diode_published(DIODE_DIR, limit=DISPLAY_PUBLISHED)
    spoken, spoken_total = data.diode_spoken(DIODE_DIR, limit=DISPLAY_SPOKEN)
    beat = commentary.detect_beat(
        data.loop_turns(turns), stats, diode, published, now, spoken=spoken
    )
    commentary.publish_beat(beat)
    return {
        "now": now,
        "stats": stats,
        "code": dict(
            data.code_stats(work),
            latest_edit=_public_edit(codewatch.cached_edit()),
        ),
        "turns": [_public_turn(t) for t in display],
        "events": data.self_modification_events(turns, limit=6, life=life),
        "lanes": _public_lanes(lanes),
        "lanes_omitted": _lanes_omitted(lanes),
        "pulse": _public_pulse(data.request_pulse(events_path(), now=now)),
        "records": _public_records(records.record_book(work, now=now)),
        "desk": _public_desk(desk.cached_verdicts()),
        "sense": _public_sense(sensecam.newest_frame(SENSE_DIR, now=now)),
        "diode": {
            "outputs": diode["outputs"][:DISPLAY_OUTPUTS],
            "operations_total": diode["operations_total"],
            "operations_life": diode["operations_life"],
            "published": published,
            "published_total": published_total,
            "spoken": spoken,
            "spoken_total": spoken_total,
        },
        "lineage": lineage,
        "story": _public_story(),
        "commentary": {
            "play": commentary.play_by_play(data.loop_turns(turns), diode, stats),
            "colour": dict(commentary.colour_line(beat), evidence=commentary.beat_evidence(beat)),
        },
    }


def stream_snapshot():
    """Assemble the stream page's data snapshot; never raises, never omits a key."""
    now = time.time()
    try:
        return _assemble_snapshot(now)
    except Exception:
        return _empty_snapshot(now)


class StreamHandler(_BaseHandler):
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self._send(200, pages.STREAM_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/api/stream":
            self._send(200, json.dumps(stream_snapshot()))
        elif route.startswith("/audio/"):
            self._handle_audio(unquote(route[len("/audio/") :]))
        elif route.startswith("/frame/"):
            self._handle_frame(route)
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _handle_audio(self, name):
        """Serve one utterance's audio.

        The name is matched against the directory listing rather than joined
        from the request, so traversal is impossible by construction. The
        listing check runs before any path is built, so no request string ever
        reaches the filesystem.

        The body is streamed rather than buffered. This route is on the public
        stream port, so concurrent requests for one file must not each hold a
        whole copy in the container's memory.
        """
        spoken_dir = os.path.join(DIODE_DIR, "spoken")
        try:
            listing = os.listdir(spoken_dir)
        except OSError:
            listing = []
        if not name.endswith(".mp3") or name not in listing:
            self._send(404, json.dumps({"error": "not found"}))
            return
        target = data.contained_file(DIODE_DIR, os.path.join(spoken_dir, name))
        if target is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            size = os.path.getsize(target)
            if size > AUDIO_MAX_BYTES:
                raise OSError("too large")
            f = open(target, "rb")
        except OSError:
            self._send(404, json.dumps({"error": "not found"}))
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(size))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            remaining = size
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def _handle_frame(self, route):
        """Serve one captured sense frame.

        The route carries exactly two segments after the prefix. Both are
        matched against directory listings inside sensecam rather than joined
        from the request, so traversal is impossible by construction, and the
        resolved path is contained and size-capped there. The body is
        streamed for the same reason the audio route streams: this route is
        on the public stream port.
        """
        parts = route.split("/")
        if len(parts) != 4:
            self._send(404, json.dumps({"error": "not found"}))
            return
        target = sensecam.frame_bytes_path(SENSE_DIR, unquote(parts[2]), unquote(parts[3]))
        if target is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            size = os.path.getsize(target)
            f = open(target, "rb")
        except OSError:
            self._send(404, json.dumps({"error": "not found"}))
            return
        with f:
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(size))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            remaining = size
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)


def make_server(port, handler):
    """A threading HTTP server bound to all interfaces on the given port."""
    return ThreadingHTTPServer(("0.0.0.0", port), handler)


def main():
    console = make_server(CONSOLE_PORT, ConsoleHandler)
    threading.Thread(target=console.serve_forever, daemon=True).start()
    if summary is not None:
        try:
            summary.start_background_refresh(TELEMETRY_DIR, transcript_path())
        except Exception:
            pass
    try:
        commentary.start_background_refresh()
    except Exception:
        pass
    try:
        desk.start_background_refresh(TELEMETRY_DIR, transcript_path())
    except Exception:
        pass
    try:
        codewatch.start_background_refresh(os.path.join(TELEMETRY_DIR, "work"))
    except Exception:
        pass
    print(f"stage: stream on :{STREAM_PORT}, console on :{CONSOLE_PORT}")
    make_server(STREAM_PORT, StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
