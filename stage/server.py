import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from stage import browse, data, pages

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "8091"))
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8092"))

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
        with open(target, "rb") as f:
            body = f.read()
        name = _sanitize_header_value(os.path.basename(target))
        self._send(
            200,
            body,
            content_type="application/octet-stream",
            extra={"Content-Disposition": f'attachment; filename="{name}"'},
        )

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


def _public_turn(turn):
    """A turn summary with content, reasoning, and tool call arguments capped for public display."""
    return {
        **turn,
        "content": _clip(turn.get("content"), 2000),
        "reasoning": _clip(turn.get("reasoning"), 2000),
        "tool_calls": [
            {**tc, "arguments": _clip(tc.get("arguments"), 400)}
            for tc in turn.get("tool_calls") or []
        ],
    }


def stream_snapshot():
    """Assemble the stream page's data snapshot."""
    work = os.path.join(TELEMETRY_DIR, "work")
    turns, total = data.load_tail_turns(transcript_path())
    diode = data.diode_activity(DIODE_DIR)
    return {
        "turns": [_public_turn(t) for t in turns],
        "stats": data.incarnation_stats(turns, total, work),
        "events": data.self_modification_events(turns),
        "diode": {"outputs": diode["outputs"]},
        "lineage": data.lineage(work, turns),
    }


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
    print(f"stage: stream on :{STREAM_PORT}, console on :{CONSOLE_PORT}")
    make_server(STREAM_PORT, StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
