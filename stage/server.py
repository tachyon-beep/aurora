import hmac
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from stage import browse, data, pages

try:
    from stage import summary
except Exception:
    summary = None

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "8091"))
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8092"))

DISPLAY_TURNS = 6
DISPLAY_OUTPUTS = 4
DISPLAY_PUBLISHED = 2
TEXT_CAP = 8000
ARGUMENTS_CAP = 400
ERROR_CAP = 600
CODE_CAP = 40
STORY_CAP = 1200
MODEL_CAP = 60
NAME_CAP = 64
TIMESTAMP_CAP = 40

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
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
        current = os.path.join(work, "agent.py")
        stock = os.path.join(work, "agent_stock.py")
        if not (os.path.isfile(current) and os.path.isfile(stock)):
            self._send(404, json.dumps({"error": "mirror not available"}))
            return
        text = browse.unified_diff_text(stock, current, "agent_stock.py", "agent.py")
        self._send(200, json.dumps({"diff": text}))


def transcript_path():
    """The recorder's JSONL transcript file path."""
    return os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")


def _clip(text, cap):
    """The first cap characters of text; falsy input yields an empty string."""
    if not text:
        return ""
    return text[:cap]


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


def _public_turn(turn):
    """A turn summary with every field enumerated and capped for public display."""
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
        },
        "code": {"available": False, "added": 0, "removed": 0},
        "turns": [],
        "events": [],
        "diode": {"outputs": [], "published": [], "published_total": 0},
        "lineage": [],
        "story": None,
    }


def _assemble_snapshot(now):
    """Read every source and project it into the public snapshot shape."""
    work = os.path.join(TELEMETRY_DIR, "work")
    turns, total = data.load_tail_turns(transcript_path())
    incarnation = len(data.tombstone_paths(work)) + 1
    deaths = data.tombstone_deaths(work, now=now)
    data.annotate_lives(turns, deaths, incarnation)
    display = turns[-DISPLAY_TURNS:]
    lineage = data.lineage(work, turns, limit=5, now=now)
    stats = data.incarnation_stats(
        turns,
        total,
        work,
        display_turns=display,
        lineage_entries=lineage,
        deaths=deaths,
        transcript_path=transcript_path(),
        now=now,
    )
    stats["model"] = _clip(str(stats.get("model") or ""), MODEL_CAP) or None
    life = stats["incarnation"] if any(t.get("life") is not None for t in turns) else None
    diode = data.diode_activity(DIODE_DIR, deaths=deaths, incarnation=incarnation)
    published, published_total = data.diode_published(DIODE_DIR, limit=DISPLAY_PUBLISHED)
    return {
        "now": now,
        "stats": stats,
        "code": data.code_stats(work),
        "turns": [_public_turn(t) for t in display],
        "events": data.self_modification_events(turns, limit=6, life=life),
        "diode": {
            "outputs": diode["outputs"][:DISPLAY_OUTPUTS],
            "published": published,
            "published_total": published_total,
        },
        "lineage": lineage,
        "story": _public_story(),
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
        else:
            self._send(404, json.dumps({"error": "not found"}))


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
    print(f"stage: stream on :{STREAM_PORT}, console on :{CONSOLE_PORT}")
    make_server(STREAM_PORT, StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
