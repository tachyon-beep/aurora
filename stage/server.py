import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse

from stage import (
    blog,
    blog_page,
    browse,
    census,
    codewatch,
    commentary,
    data,
    desk,
    diag,
    diag_page,
    moments,
    pages,
    records,
    sensecam,
    telemetry_page,
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

DISPLAY_TURNS = 10
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
LIVES_CAP = 200
LIFE_KINDS = ("declared", "harness", "unknown")
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

BLOG_CSP = SECURITY_HEADERS["Content-Security-Policy"].replace(
    "script-src 'unsafe-inline'", f"script-src 'unsafe-inline' {blog_page.MERMAID_URL}"
)


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
        headers = dict(SECURITY_HEADERS)
        headers.update(extra or {})
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _send_contained(self, root, path, content_type, max_bytes=None, extra=None):
        """Stream a regular file inside root, or answer 404.

        The file is opened through data.open_contained and everything after
        that - the size check, the announced length, and the bytes - comes
        from that one descriptor. Resolving the name again to stat or reopen
        it would let the agent, which writes /diode directly, replace the
        validated file with a link between the check and the read.
        """
        handle = data.open_contained(root, path)
        if handle is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            size = os.fstat(handle.fileno()).st_size
        except OSError:
            handle.close()
            self._send(404, json.dumps({"error": "not found"}))
            return
        if max_bytes is not None and size > max_bytes:
            handle.close()
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._stream_handle(handle, size, content_type, extra)

    def _stream_handle(self, handle, size, content_type, extra=None):
        """Stream size bytes from an already-validated descriptor.

        The body is streamed rather than buffered: these routes serve files
        the agent writes, so concurrent requests for one file must not each
        hold a whole copy in the container's memory. A file that shrinks
        while it is being sent cannot fill the length already announced, so
        the connection is closed rather than left short.
        """
        with handle:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            headers = dict(SECURITY_HEADERS)
            headers.update(extra or {})
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            remaining = size
            while remaining > 0:
                chunk = handle.read(min(65536, remaining))
                if not chunk:
                    self.close_connection = True
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

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
        root = browse_roots().get(query.get("root", [""])[0])
        handle = data.open_contained(root, target) if target and root else None
        if handle is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        tail = query.get("tail", ["0"])[0] == "1"
        self._send(200, json.dumps(browse.read_text_preview(handle, tail=tail)))

    def _handle_download(self, query):
        target = self._resolve(query)
        root = browse_roots().get(query.get("root", [""])[0])
        if target is None or root is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        name = _sanitize_header_value(os.path.basename(target))
        self._send_contained(
            root,
            target,
            "application/octet-stream",
            extra={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    def _handle_diff(self):
        work = os.path.join(TELEMETRY_DIR, "work")
        current = data.open_contained(TELEMETRY_DIR, os.path.join(work, "agent.py"))
        stock = data.open_contained(TELEMETRY_DIR, os.path.join(work, "agent_stock.py"))
        if current is None or stock is None:
            if current is not None:
                current.close()
            if stock is not None:
                stock.close()
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
ACHIEVEMENTS_CAP = 12
ACHIEVEMENT_CHARS = 100


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
    raw_lives = [life for life in (book.get("lives") or []) if isinstance(life, dict)]
    lives = []
    for life in raw_lives[-LIVES_CAP:]:
        kind = life.get("kind")
        seconds = life.get("seconds")
        ended = life.get("ended_epoch")
        try:
            ordinal = int(life.get("ordinal") or 0)
        except (TypeError, ValueError):
            ordinal = 0
        lives.append(
            {
                "ordinal": ordinal,
                "kind": kind if kind in LIFE_KINDS else "unknown",
                "seconds": float(seconds) if isinstance(seconds, (int, float)) else None,
                "ended_epoch": float(ended) if isinstance(ended, (int, float)) else None,
            }
        )
    return {
        "lives_ended": int(book.get("lives_ended") or 0),
        "chose": int(book.get("chose") or 0),
        "longest_life": longest,
        "most_recent_gap_seconds": float(gap) if isinstance(gap, (int, float)) else None,
        "lives": lives,
        "lives_omitted": max(0, len(raw_lives) - LIVES_CAP),
    }


def _empty_records():
    """The record-book key set with nothing in it."""
    return _public_records({})


NOTE_CAP = 320
MOMENT_LINE_CAP = 140
MOMENTS_CAP = 6
DIGEST_STATES = ("ready", "pending", "skipped", "off")


def _public_moment(moment):
    """One digest moment with every field enumerated and capped, or None."""
    if not isinstance(moment, dict):
        return None
    turn = moment.get("turn")
    stars = moment.get("stars")
    if isinstance(turn, bool) or not isinstance(turn, int):
        return None
    if isinstance(stars, bool) or not isinstance(stars, int):
        return None
    line = _clip(str(moment.get("line") or ""), MOMENT_LINE_CAP)
    if not line:
        return None
    return {"turn": turn, "stars": min(5, max(1, stars)), "line": line}


def _public_verdict(verdict):
    """One desk verdict with every field enumerated and capped, or None."""
    if not isinstance(verdict, dict):
        return None
    stars = verdict.get("stars")
    if isinstance(stars, bool) or not isinstance(stars, int):
        return None
    return {
        "stars": min(5, max(1, stars)),
        "line": _clip(str(verdict.get("line") or ""), DESK_LINE_CAP),
        "evidence": _clip(str(verdict.get("evidence") or ""), DESK_EVIDENCE_CAP),
        "depth": _clip(str(verdict.get("depth") or ""), DESK_DEPTH_CAP),
    }


def _digest_state(ordinal, digest, enabled):
    """Where a dead life's digest stands: ready, pending, skipped, or off."""
    if not enabled:
        return "off"
    if isinstance(digest, dict) and digest.get("moments"):
        return "ready"
    return "skipped" if moments.given_up(ordinal) else "pending"


def _public_life(row, verdict, digest, enabled):
    """One life for the lineage endpoint, every field enumerated and capped.

    row is a census entry (or a bare {ordinal, current, ...} stand-in when the
    census has not run); counts absent from it are null rather than zero so
    the page can say "not counted yet" instead of "0".
    """
    row = row if isinstance(row, dict) else {}
    try:
        ordinal = int(row.get("ordinal") or 0)
    except (TypeError, ValueError):
        ordinal = 0
    current = bool(row.get("current"))
    began = row.get("began_epoch")
    ended = row.get("ended_epoch")
    began = (
        float(began) if isinstance(began, (int, float)) and not isinstance(began, bool) else None
    )
    ended = (
        float(ended) if isinstance(ended, (int, float)) and not isinstance(ended, bool) else None
    )
    lived = ended - began if began is not None and ended is not None and ended > began else None
    kind = row.get("ending_kind")
    kind = (
        kind if kind in LIFE_KINDS else ("unknown" if not current and ended is not None else None)
    )

    exact = bool(row.get("exact"))

    def count(name):
        value = row.get(name)
        if not exact or isinstance(value, bool) or not isinstance(value, int):
            return None
        return max(0, value)

    public_moments = []
    if isinstance(digest, dict):
        for moment in digest.get("moments") or []:
            item = _public_moment(moment)
            if item is not None:
                public_moments.append(item)
            if len(public_moments) >= MOMENTS_CAP:
                break
    achievement = None
    if isinstance(digest, dict) and digest.get("achievement"):
        achievement = _clip(str(digest["achievement"]), ACHIEVEMENT_CHARS) or None
    generated = digest.get("generated_at") if isinstance(digest, dict) else None
    shown = digest.get("turns_shown") if isinstance(digest, dict) else None
    total = digest.get("turns_total") if isinstance(digest, dict) else None
    return {
        "ordinal": ordinal,
        "current": current,
        "began_epoch": began,
        "ended_epoch": ended,
        "lived_seconds": lived,
        "kind": None if current else kind,
        "turns": count("turns"),
        "subcalls": count("subcalls"),
        "errors": count("errors"),
        "edits": count("edits"),
        "note": "" if current else _clip(str(row.get("summary") or ""), NOTE_CAP),
        "verdict": None if current else _public_verdict(verdict),
        "moments": [] if current else public_moments,
        "achievement": None if current else achievement,
        "digest": {
            "state": "off"
            if current and not enabled
            else ("pending" if current else _digest_state(ordinal, digest, enabled)),
            "turns_shown": int(shown)
            if isinstance(shown, int) and not isinstance(shown, bool)
            else None,
            "turns_total": int(total)
            if isinstance(total, int) and not isinstance(total, bool)
            else None,
            "generated_at": float(generated)
            if isinstance(generated, (int, float)) and not isinstance(generated, bool)
            else None,
        },
    }


def _fallback_lives(work, now):
    """Lives from the tombstone mirror alone, newest first, when the census has not run."""
    book = records.record_book(work, now=now)
    incarnation = len(data.tombstone_paths(work)) + 1
    by_ordinal = {}
    for life in book.get("lives") or []:
        if isinstance(life, dict) and isinstance(life.get("ordinal"), int):
            by_ordinal[life["ordinal"]] = life
    notes = {}
    for entry in data.lineage(work, [], limit=LIVES_CAP, now=now):
        if isinstance(entry.get("ordinal"), int):
            notes[entry["ordinal"]] = entry
    rows = [{"ordinal": incarnation, "current": True}]
    for ordinal in range(incarnation - 1, 0, -1):
        life = by_ordinal.get(ordinal, {})
        note = notes.get(ordinal, {})
        ended = life.get("ended_epoch", note.get("ended_epoch"))
        seconds = life.get("seconds")
        began = (
            ended - seconds
            if isinstance(ended, (int, float)) and isinstance(seconds, (int, float))
            else None
        )
        rows.append(
            {
                "ordinal": ordinal,
                "current": False,
                "began_epoch": began,
                "ended_epoch": ended,
                "ending_kind": life.get("kind") or note.get("kind"),
                "summary": note.get("sentence") or "",
            }
        )
    return rows


def _lineage_snapshot(now=None):
    """Every life with its figures, verdict and digest, for the telemetry panel."""
    if now is None:
        now = time.time()
    work = os.path.join(TELEMETRY_DIR, "work")
    rows = census.cached_lives()
    if rows is None:
        rows = _fallback_lives(work, now)
    enabled = moments.enabled()
    digests = moments.cached_digests() if enabled else {}
    lives = []
    for row in rows[: LIVES_CAP + 1]:
        ordinal = row.get("ordinal") if isinstance(row, dict) else None
        verdict = desk.cached_verdict(ordinal) if isinstance(ordinal, int) else None
        lives.append(_public_life(row, verdict, digests.get(ordinal), enabled))
    incarnation = lives[0]["ordinal"] if lives else 1
    return {
        "now": now,
        "incarnation": incarnation,
        "digests_enabled": enabled,
        "desk_model": _clip(desk.model_name(), MODEL_CAP) if enabled else "",
        "digest_model": _clip(moments.model_name(), MODEL_CAP) if enabled else "",
        "lives": lives,
        "lives_omitted": max(0, len(rows) - len(lives)),
    }


def lineage_snapshot():
    """Assemble the lineage endpoint's data; never raises, never omits a key."""
    now = time.time()
    try:
        return _lineage_snapshot(now)
    except Exception:
        return {
            "now": now,
            "incarnation": 1,
            "digests_enabled": False,
            "desk_model": "",
            "digest_model": "",
            "lives": [],
            "lives_omitted": 0,
        }


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


def _public_achievements(rows):
    """The achievement nominations, newest first, every field enumerated and capped."""
    out = []
    for row in rows or []:
        if len(out) >= ACHIEVEMENTS_CAP:
            break
        if not isinstance(row, dict):
            continue
        line = _clip(str(row.get("line") or ""), ACHIEVEMENT_CHARS)
        ordinal = row.get("ordinal")
        if not line or not isinstance(ordinal, int) or isinstance(ordinal, bool):
            continue
        generated = row.get("generated_at")
        out.append(
            {
                "ordinal": ordinal,
                "line": line,
                "generated_at": float(generated) if isinstance(generated, (int, float)) else None,
            }
        )
    return out


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
            "self_edits_this_life": None,
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
        "achievements": [],
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


def _overlay_census(stats, row):
    """Replace the tail-derived life figures with the census's exact ones, in place.

    The census counts every core-stream entry the live transcript holds, so
    its turn count is exact and its self-edit count does not saturate at the
    event-list cap. Without a census row — or with one the census marks
    inexact, because a tombstone could not be dated or an entry could not be
    placed — the tail figures stand and the self-edit count is null, which the
    page reads as "count the events".
    """
    stats["self_edits_this_life"] = None
    if not isinstance(row, dict) or not row.get("exact"):
        return stats
    turns = row.get("turns")
    if isinstance(turns, int) and not isinstance(turns, bool):
        stats["turns_this_life"] = turns
        stats["turns_this_life_exact"] = True
    edits = row.get("edits")
    if isinstance(edits, int) and not isinstance(edits, bool):
        stats["self_edits_this_life"] = edits
    return stats


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
    _overlay_census(stats, census.cached_life(stats["incarnation"]))
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
        "achievements": _public_achievements(moments.achievements(ACHIEVEMENTS_CAP)),
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


def blog_response(query):
    """(status, html) for the blog page; query is the parsed query dict."""
    raw = (query.get("page") or ["1"])[0]
    try:
        page = int(raw)
    except ValueError:
        return 404, "<!doctype html><title>not found</title><p>not found</p>"
    names, list_truncated = blog.list_posts(DIODE_DIR)
    window = blog.paginate(len(names), page)
    if window is None:
        return 404, "<!doctype html><title>not found</title><p>not found</p>"
    start, end, total_pages = window
    posts = [p for p in (blog.read_post(DIODE_DIR, n) for n in names[start:end]) if p]
    return 200, blog_page.render_page(posts, page, total_pages, len(names), list_truncated)


class StreamHandler(_BaseHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        route = parsed.path
        if route == "/":
            self._send(200, pages.STREAM_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/telemetry":
            self._send(
                200, telemetry_page.TELEMETRY_PAGE_HTML, content_type="text/html; charset=utf-8"
            )
        elif route == "/blog":
            try:
                status, body = blog_response(parse_qs(parsed.query))
            except Exception:
                status, body = (
                    500,
                    "<!doctype html><title>error</title><p>the blog could not be rendered</p>",
                )
            self._send(
                status,
                body,
                content_type="text/html; charset=utf-8",
                extra={"Content-Security-Policy": BLOG_CSP},
            )
        elif route == "/api/stream":
            self._send(200, json.dumps(stream_snapshot()))
        elif route == "/api/lineage":
            self._send(200, json.dumps(lineage_snapshot()))
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
        self._send_contained(
            DIODE_DIR,
            os.path.join(spoken_dir, name),
            "audio/mpeg",
            max_bytes=AUDIO_MAX_BYTES,
        )

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
        self._send_contained(SENSE_DIR, target, "image/jpeg", max_bytes=sensecam.FRAME_MAX_BYTES)


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
    try:
        census.start_background_refresh(transcript_path(), os.path.join(TELEMETRY_DIR, "work"))
    except Exception:
        pass
    try:
        moments.start_background_refresh(transcript_path(), os.path.join(TELEMETRY_DIR, "work"))
    except Exception:
        pass
    print(f"stage: stream on :{STREAM_PORT}, console on :{CONSOLE_PORT}")
    make_server(STREAM_PORT, StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
