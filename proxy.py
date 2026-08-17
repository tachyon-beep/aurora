import gzip
import http.server
import shutil
import socket
import socketserver
import threading
import time
import urllib.request
import urllib.error
import json
import os
import sys
import datetime

import recorder_streams

SOCKET_PATH = os.environ.get("LLM_SOCKET_PATH", "/llm/sock/core.sock")
TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", os.path.dirname(os.path.abspath(__file__)))
TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")
PLAIN_TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.txt")
TRANSCRIPT_MAX_BYTES = int(os.environ.get("TRANSCRIPT_MAX_BYTES", str(134_217_728)))
EVENTS_FILE = os.path.join(TRANSCRIPT_DIR, "events.jsonl")
EVENTS_MAX_BYTES = 16_777_216
# The recorder holds a request body whole while it forwards and records it, so
# a body is refused above this size rather than read into memory. Resource
# hygiene inside the recorder, which runs under a memory limit; not a ceiling
# on what a stream may spend.
REQUEST_MAX_BYTES = int(os.environ.get("REQUEST_MAX_BYTES", str(16_777_216)))
# The most of a streamed body's raw bytes the recorder keeps, for a body that
# carries no events, and the longest single event line it parses. The
# reassembled completion is held whole; the body it came from is not.
STREAM_RETAIN_BYTES = 1_048_576

_transcript_lock = threading.Lock()
_events_lock = threading.Lock()
_active_bindings = set()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
FRAMING_HEADERS = ("content-length", "transfer-encoding", "connection", "content-encoding")
REASONING_DELTA_FIELDS = ("reasoning", "reasoning_content")


def upstream_url():
    """Return the upstream chat-completions URL.

    When LLM_BASE_URL is set, the recorder forwards to that OpenAI-compatible
    endpoint; otherwise it forwards to OpenRouter.
    """
    base = os.environ.get("LLM_BASE_URL", "").strip()
    if base:
        return base.rstrip("/") + "/chat/completions"
    return OPENROUTER_URL


def upstream_api_key():
    """Return the API key injected into upstream requests.

    LLM_API_KEY (possibly empty, for no-auth local servers) applies when
    LLM_BASE_URL is set; otherwise OPENROUTER_API_KEY applies.
    """
    if os.environ.get("LLM_BASE_URL", "").strip():
        return os.environ.get("LLM_API_KEY", "")
    return os.environ.get("OPENROUTER_API_KEY", "")


def stream_upstream_url():
    """Return the chat-completions URL serving declared stream sockets.

    Declared streams always forward to OpenRouter, independent of
    LLM_BASE_URL, which governs only core.sock. STREAM_UPSTREAM_URL
    overrides the target for the verification harness and tests; leave it
    unset in normal operation.
    """
    base = os.environ.get("STREAM_UPSTREAM_URL", "").strip()
    if base:
        return base.rstrip("/") + "/chat/completions"
    return OPENROUTER_URL


def upstream_for(stream):
    """Return the (url, api_key) pair a stream's requests forward to.

    core.sock follows the configured upstream; every declared stream
    forwards to the stream upstream with the recorder's OpenRouter key.
    """
    if stream == "core":
        return upstream_url(), upstream_api_key()
    return stream_upstream_url(), os.environ.get("OPENROUTER_API_KEY", "")


def build_forward_headers(headers, api_key):
    """Build the headers forwarded upstream.

    Drops hop-by-hop headers. When api_key is non-empty, overrides Authorization
    with it, so the recorder injects the real key and the agent never holds it.
    """
    hop_by_hop = {"host", "content-length", "connection", "accept-encoding"}
    forwarded = {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}
    if api_key:
        forwarded["Authorization"] = f"Bearer {api_key}"
    return forwarded


def utc_timestamp():
    """The current UTC time as an ISO-8601 string with a trailing Z."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def archive_name(path, stamp=None):
    """Return the timestamped gzip archive name for a transcript path."""
    if stamp is None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    root, ext = os.path.splitext(path)
    return f"{root}-{stamp}{ext}.gz"


def rotate_if_needed(path, max_bytes=None):
    """Archive a transcript to gzip and truncate it once it reaches max_bytes.

    Returns the archive path, or None when no rotation happened. The file is
    compressed in chunks and the archive is renamed into place before the
    live file is truncated. On failure the live file is left unchanged.
    """
    if max_bytes is None:
        max_bytes = TRANSCRIPT_MAX_BYTES
    tmp = None
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return None
        final = archive_name(path)
        if os.path.exists(final):
            return None
        tmp = final + ".tmp"
        with open(path, "rb") as src, gzip.open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 65536)
        os.rename(tmp, final)
        with open(path, "w", encoding="utf-8"):
            pass
        return final
    except Exception as e:
        print(f"Error rotating transcript: {e}", file=sys.stderr)
        if tmp is not None:
            try:
                os.remove(tmp)
            except Exception:
                pass
        return None


def request_id():
    """A random hex token pairing one request's open and close events."""
    return os.urandom(8).hex()


def log_event(event, stream, **fields):
    """Append one telemetry event; failures never affect the request.

    Events carry names, counts, statuses, durations, and token totals only,
    never message content or headers.
    """
    entry = {
        "timestamp": utc_timestamp(),
        "event": event,
        "stream": stream,
    }
    entry.update(fields)
    try:
        with _events_lock:
            os.makedirs(os.path.dirname(EVENTS_FILE) or ".", exist_ok=True)
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            if event == "bind":
                _active_bindings.add(stream)
            elif event == "unbind":
                _active_bindings.discard(stream)
            if rotate_if_needed(EVENTS_FILE, EVENTS_MAX_BYTES) is not None:
                with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                    for name in sorted(_active_bindings):
                        checkpoint = {
                            "timestamp": entry["timestamp"],
                            "event": "bind",
                            "stream": name,
                        }
                        f.write(json.dumps(checkpoint) + "\n")
    except Exception as e:
        print(f"Error writing event: {e}", file=sys.stderr)


def passthrough_headers(items):
    """The upstream response headers, minus the framing the recorder sets itself."""
    return [(k, v) for k, v in items if k.lower() not in FRAMING_HEADERS]


def iter_response_chunks(response, size=65536):
    """Yield an upstream response body in the pieces it arrives in.

    read1 returns what a single underlying read produced, so a piece leaves
    for the client as soon as the upstream sends it. An empty piece is the
    end of the body.
    """
    reader = getattr(response, "read1", None)
    if reader is None:
        reader = response.read
    while True:
        piece = reader(size)
        if not piece:
            return
        yield piece


def relay_chunks(writer, response, record, size=65536):
    """Relay a response body to the client with chunked transfer framing.

    Each piece is fed to the record as it passes, so nothing holds the body
    whole. Returns the exception that ended the relay early, or None. Each
    frame is written in one call, so a failed write leaves no partial frame,
    and the terminating chunk is written only when the body ended.
    """
    error = None
    try:
        for piece in iter_response_chunks(response, size):
            record.feed(piece)
            writer.write(b"%X\r\n" % len(piece) + piece + b"\r\n")
        writer.write(b"0\r\n\r\n")
    except Exception as e:
        error = e
    record.finish()
    return error


def sse_payload(line):
    """The JSON object a data: line carries, or None.

    The terminating [DONE] event, comments, blank lines, and any payload that
    is not a JSON object yield None.
    """
    line = line.strip()
    if not line.startswith(b"data:"):
        return None
    data = line[len(b"data:") :].strip()
    if not data or data == b"[DONE]":
        return None
    try:
        payload = json.loads(data.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _delta_index(fragment, position):
    """The index a delta fragment belongs to, defaulting to its position."""
    index = fragment.get("index")
    if isinstance(index, int) and not isinstance(index, bool):
        return index
    return position


def _accumulate_tool_calls(accumulated, fragments):
    """Merge one delta's tool-call fragments into the calls built so far.

    Fragments key on the index they carry; arguments concatenate, and id,
    type, and name are taken from the first fragment supplying them.
    """
    for position, fragment in enumerate(fragments):
        if not isinstance(fragment, dict):
            continue
        call = accumulated.setdefault(
            _delta_index(fragment, position),
            {"id": None, "type": "function", "name": None, "arguments": []},
        )
        if call["id"] is None and isinstance(fragment.get("id"), str):
            call["id"] = fragment["id"]
        if isinstance(fragment.get("type"), str):
            call["type"] = fragment["type"]
        function = fragment.get("function")
        if not isinstance(function, dict):
            continue
        if call["name"] is None and isinstance(function.get("name"), str):
            call["name"] = function["name"]
        if isinstance(function.get("arguments"), str):
            call["arguments"].append(function["arguments"])


def _accumulate_choice(accumulated, choice):
    """Merge one streamed choice into the message built for its index."""
    if choice.get("finish_reason") is not None:
        accumulated["finish_reason"] = choice["finish_reason"]
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return
    if accumulated["role"] is None and isinstance(delta.get("role"), str):
        accumulated["role"] = delta["role"]
    if isinstance(delta.get("content"), str):
        accumulated["content"].append(delta["content"])
    for field in REASONING_DELTA_FIELDS:
        if isinstance(delta.get(field), str):
            accumulated["reasoning"].setdefault(field, []).append(delta[field])
    if isinstance(delta.get("tool_calls"), list):
        _accumulate_tool_calls(accumulated["tool_calls"], delta["tool_calls"])


def _render_choice(index, accumulated):
    """The completed choice object for one accumulated stream index."""
    message = {"role": accumulated["role"] or "assistant"}
    for field, parts in accumulated["reasoning"].items():
        message[field] = "".join(parts)
    message["content"] = "".join(accumulated["content"]) or None
    if accumulated["tool_calls"]:
        message["tool_calls"] = [
            {
                "id": call["id"],
                "type": call["type"],
                "function": {
                    "name": call["name"] or "",
                    "arguments": "".join(call["arguments"]),
                },
            }
            for _, call in sorted(accumulated["tool_calls"].items())
        ]
    return {"index": index, "message": message, "finish_reason": accumulated["finish_reason"]}


class StreamRecord:
    """The completion a streamed response describes, rebuilt as its bytes pass.

    Each piece of the body is fed in the order it arrives; events are parsed
    line by line, so an event split across two pieces still decodes, and only
    the reassembled completion is held: content and reasoning fragments
    concatenate, tool calls reassemble on the index their fragments carry,
    the finish reason is the one a chunk set, and usage comes from the event
    carrying it. The body itself is not kept, beyond a prefix of at most
    STREAM_RETAIN_BYTES for a body that turns out to carry no events. A single
    line longer than that is dropped rather than held.
    """

    def __init__(self):
        self._completion = {"object": "chat.completion"}
        self._choices = {}
        self._tail = b""
        self._dropping = False
        self.raw = b""
        self.raw_bytes = 0
        self.raw_truncated = False
        self.events = 0

    def feed(self, piece):
        """Take the next piece of the body."""
        limit = STREAM_RETAIN_BYTES
        self.raw_bytes += len(piece)
        if len(self.raw) < limit:
            self.raw += piece[: limit - len(self.raw)]
        if self.raw_bytes > limit:
            self.raw_truncated = True
        data = (self._tail + piece).replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        lines = data.split(b"\n")
        self._tail = lines.pop()
        for line in lines:
            if self._dropping:
                self._dropping = False
                continue
            self._line(line)
        if len(self._tail) > limit:
            self._tail = b""
            self._dropping = True

    def finish(self):
        """Take a trailing event the body ended without a newline after."""
        if self._tail and not self._dropping:
            self._line(self._tail)
        self._tail = b""
        self._dropping = False

    def _line(self, line):
        payload = sse_payload(line)
        if payload is None:
            return
        self.events += 1
        completion = self._completion
        for field in ("id", "created", "model"):
            if field not in completion and payload.get(field) is not None:
                completion[field] = payload[field]
        if isinstance(payload.get("usage"), dict):
            completion["usage"] = payload["usage"]
        entries = payload.get("choices")
        if not isinstance(entries, list):
            return
        for position, choice in enumerate(entries):
            if not isinstance(choice, dict):
                continue
            index = _delta_index(choice, position)
            if index not in self._choices:
                self._choices[index] = {
                    "role": None,
                    "content": [],
                    "reasoning": {},
                    "tool_calls": {},
                    "finish_reason": None,
                }
            _accumulate_choice(self._choices[index], choice)

    def completion(self):
        """The completed chat-completion object the events described so far.

        The usage field is absent when no event carried one.
        """
        completion = dict(self._completion)
        completion["choices"] = [
            _render_choice(index, self._choices[index]) for index in sorted(self._choices)
        ]
        return completion


def reconstruct_completion(chunks):
    """Rebuild a completed chat-completion object from a body's pieces.

    Pieces may be bytes or text. The result is the shape a buffered exchange
    has, so a streamed exchange is recorded like one.
    """
    record = StreamRecord()
    for chunk in chunks:
        record.feed(chunk.encode("utf-8") if isinstance(chunk, str) else chunk)
    record.finish()
    return record.completion()


def stream_response_data(record):
    """The response object recorded for a relayed body.

    Deltas are reassembled into a completed object. A body that carried no
    events is recorded as itself, so an upstream answering a streamed request
    with a whole document, or with an error, is not lost from the transcript;
    one longer than the record kept is recorded as the prefix it kept, marked
    truncated.
    """
    completion = record.completion()
    if completion["choices"] or "usage" in completion:
        return completion
    text = record.raw.decode("utf-8", errors="replace")
    if not record.raw_truncated:
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict):
            return data
        return {"raw_body": text}
    return {"raw_body": text, "raw_body_truncated": True}


class ProxyHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 300

    def log_message(self, format, *args):
        sys.stdout.write(
            f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {self.client_address[0]} - {format % args}\n"
        )
        sys.stdout.flush()

    def do_POST(self):
        if self.path != "/api/v1/chat/completions":
            self.send_error(404, "Not Found")
            return

        if self.headers.get("Transfer-Encoding"):
            self._refuse_and_close(411, "request body must carry a content-length")
            return

        raw_length = self.headers.get("Content-Length", "0")
        try:
            content_length = int(raw_length)
        except ValueError:
            self._refuse_and_close(400, "content-length must be an integer")
            return
        if content_length < 0:
            self._refuse_and_close(400, "content-length must not be negative")
            return
        if content_length > REQUEST_MAX_BYTES:
            self._refuse_and_close(413, f"request body must be at most {REQUEST_MAX_BYTES} bytes")
            return
        req_body = self.rfile.read(content_length)
        stream = getattr(self.server, "stream_name", "core")
        registry = getattr(self.server, "registry", None)

        refused = None
        ticket = None
        if registry is not None and stream != "core":
            req_body, refused, ticket = registry.admit(stream, req_body)

        try:
            req_data = json.loads(req_body.decode("utf-8"))
        except Exception:
            req_data = None
        if not isinstance(req_data, dict):
            req_data = {"raw_body": req_body.decode("utf-8", errors="replace")}

        event_id = request_id()
        messages = req_data.get("messages")
        started = time.monotonic()
        log_event(
            "open",
            stream,
            id=event_id,
            model=req_data.get("model"),
            messages=len(messages) if isinstance(messages, list) else 0,
        )

        if refused is not None:
            status_code, message = refused
            log_event(
                "close",
                stream,
                id=event_id,
                status=status_code,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            self._finish_local(stream, req_data, status_code, message)
            return

        target_url, target_key = upstream_for(stream)
        headers_to_forward = build_forward_headers(self.headers, target_key)

        req = urllib.request.Request(
            target_url,
            data=req_body,
            headers=headers_to_forward,
            method="POST",
        )

        response_body = b""
        response_code = 500
        response_headers = []
        relayed = None
        upstream_refused = False

        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                response_code = res.status
                response_headers = passthrough_headers(res.getheaders())
                if req_data.get("stream") is True:
                    relayed = self._relay(response_code, response_headers, res)
                else:
                    response_body = res.read()
        except urllib.error.HTTPError as e:
            response_code = e.code
            response_body = e.read()
            response_headers = passthrough_headers(e.headers.items())
            upstream_refused = True
        except Exception as e:
            response_code = 500
            response_body = json.dumps({"error": {"message": f"Proxy error: {str(e)}"}}).encode(
                "utf-8"
            )
            response_headers = [("Content-Type", "application/json")]

        if relayed is not None:
            res_data = stream_response_data(relayed)
        else:
            try:
                res_data = json.loads(response_body.decode("utf-8"))
            except Exception:
                res_data = {"raw_body": response_body.decode("utf-8", errors="replace")}

        close_fields = {
            "id": event_id,
            "status": response_code,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        usage = res_data.get("usage") if isinstance(res_data, dict) else None
        if isinstance(usage, dict):
            close_fields["usage"] = {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), (int, float))
            }
        log_event("close", stream, **close_fields)

        if registry is not None and stream != "core":
            # An upstream that answered with an error status generated nothing,
            # so the request settles at zero. A request whose usage is unknown
            # for any other reason - a relay that broke, a transport failure
            # after the request may have been received - keeps its reservation.
            spent = usage.get("total_tokens") if isinstance(usage, dict) else None
            if isinstance(spent, bool) or not isinstance(spent, (int, float)):
                spent = 0 if upstream_refused else None
            registry.settle(stream, ticket, spent)

        self.log_transcript(req_data, res_data, stream=stream)

        if relayed is None:
            self.send_response(response_code)
            for k, v in response_headers:
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            self.wfile.write(response_body)

    def _refuse_and_close(self, status_code, message):
        """Answer a request whose body was not read, and close the connection.

        The unread body would otherwise be parsed as the next request line.
        """
        body = json.dumps({"error": {"message": message}}).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Connection", "close")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _relay(self, response_code, response_headers, res):
        """Stream an upstream response to the client and return its record.

        The response is framed as chunked transfer encoding rather than by
        length, since its size is unknown until it ends. Nothing here raises:
        once framing has begun the client cannot be told anything else, so a
        relay that ends early only closes the connection.
        """
        record = StreamRecord()
        try:
            self.send_response(response_code)
            for k, v in response_headers:
                self.send_header(k, v)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
        except Exception:
            self.close_connection = True
            return record
        error = relay_chunks(self.wfile, res, record)
        if error is not None:
            self.close_connection = True
        return record

    def _finish_local(self, stream, req_data, status_code, message):
        """Answer a request locally with a factual error and record the exchange."""
        res_data = {"error": {"message": message}}
        body = json.dumps(res_data).encode("utf-8")
        self.log_transcript(req_data, res_data, stream=stream)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _display_text(content):
        """A message's content as text.

        A multimodal message carries a list of parts rather than a string, and
        a length test over that list counts parts, not characters, so the
        display caps below would not bound an embedded data URL.
        """
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        try:
            return json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(content)

    def log_transcript(self, request_data, response_data, stream="core"):
        """Appends a new conversation step to the transcript file and dumps it to stdout."""
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

        entry = {
            "timestamp": utc_timestamp(),
            "stream": stream,
            "request": request_data,
            "response": response_data,
        }

        print("\n" + "=" * 80)
        print(f"PROXY INTERCEPTED REQUEST | Model: {request_data.get('model')}")
        print("=" * 80)
        for msg in request_data.get("messages", []):
            role = msg.get("role", "unknown").upper()
            content = self._display_text(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            name = msg.get("name")
            name_suffix = f" (Name: {name})" if name else ""

            if content:
                display_content = (
                    content
                    if len(content) < 1500
                    else content[:1500] + "\n... [TRUNCATED FOR CONSOLE DISPLAY] ..."
                )
                print(f"[{role}{name_suffix}]: {display_content}")
            if tool_calls:
                print(f"[{role} TOOL CALLS]:")
                for tc in tool_calls:
                    print(
                        f"  - ID: {tc.get('id')} | Function: {tc.get('function', {}).get('name')} | Args: {tc.get('function', {}).get('arguments')}"
                    )
        print("-" * 80)

        print("PROXY INTERCEPTED RESPONSE")
        print("=" * 80)
        choices = response_data.get("choices", [])
        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            content = message.get("content")
            tool_calls = message.get("tool_calls")

            if reasoning:
                print(f"[REASONING]: {reasoning}")
            if content:
                print(f"[ASSISTANT]: {content}")
            if tool_calls:
                print("[ASSISTANT TOOL CALLS]:")
                for tc in tool_calls:
                    print(
                        f"  - ID: {tc.get('id')} | Function: {tc.get('function', {}).get('name')} | Args: {tc.get('function', {}).get('arguments')}"
                    )
        elif "error" in response_data:
            print(f"ERROR: {json.dumps(response_data.get('error'))}")
        else:
            print(f"[RAW RESPONSE]: {json.dumps(response_data)[:500]}")
        print("=" * 80 + "\n")
        sys.stdout.flush()

        with _transcript_lock:
            try:
                with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
                print(f"Recorded transaction in: {os.path.basename(TRANSCRIPT_FILE)}")
            except Exception as e:
                print(f"Error writing transcript: {e}", file=sys.stderr)
            rotate_if_needed(TRANSCRIPT_FILE)

        plain_log_lines = []
        timestamp = entry["timestamp"]
        plain_log_lines.append("=" * 80)
        plain_log_lines.append(f"TRANSACTION | {timestamp} | Model: {request_data.get('model')}")
        plain_log_lines.append("=" * 80)

        plain_log_lines.append("--- REQUEST MESSAGES ---")
        for msg in request_data.get("messages", []):
            role = msg.get("role", "unknown").upper()
            content = self._display_text(msg.get("content", ""))
            tool_calls = msg.get("tool_calls")
            name = msg.get("name")
            name_suffix = f" (Name: {name})" if name else ""

            if role == "TOOL":
                plain_log_lines.append(f"[{role}{name_suffix}]: [Tool call output omitted]")
            else:
                if content:
                    plain_log_lines.append(f"[{role}{name_suffix}]: {content}")
                else:
                    plain_log_lines.append(f"[{role}{name_suffix}]: [No text content]")

            if tool_calls:
                tc_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
                plain_log_lines.append(f"[{role} TOOL CALLS]: {', '.join(tc_names)}")

        plain_log_lines.append("-" * 40)
        plain_log_lines.append("--- RESPONSE ---")

        choices = response_data.get("choices", [])
        if choices:
            choice = choices[0]
            message = choice.get("message", {})
            reasoning = message.get("reasoning_content") or message.get("reasoning")
            content = message.get("content")
            tool_calls = message.get("tool_calls")

            if reasoning:
                plain_log_lines.append(f"[THINKING/REASONING]: {reasoning}")
            if content:
                plain_log_lines.append(f"[ASSISTANT RESPONSE]: {content}")
            if tool_calls:
                tc_names = [tc.get("function", {}).get("name", "unknown") for tc in tool_calls]
                plain_log_lines.append(f"[TOOL CALLS INITIATED]: {', '.join(tc_names)}")
        elif "error" in response_data:
            plain_log_lines.append(f"ERROR: {json.dumps(response_data.get('error'))}")
        else:
            plain_log_lines.append("[NO RESPONSE CHOICES]")

        plain_log_lines.append("=" * 80 + "\n\n")
        plain_log_content = "\n".join(plain_log_lines)

        with _transcript_lock:
            try:
                with open(PLAIN_TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                    f.write(plain_log_content)
                print(
                    f"Recorded plain text transaction in: {os.path.basename(PLAIN_TRANSCRIPT_FILE)}"
                )
            except Exception as e:
                print(f"Error writing plain transcript: {e}", file=sys.stderr)
            rotate_if_needed(PLAIN_TRANSCRIPT_FILE)


class UnixHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server bound to a unix domain socket.

    HTTPServer.server_bind unpacks server_address[:2] as a host and port, which
    on a filesystem path yields two characters and then attempts to resolve the
    first as a hostname. AF_UNIX accept() also reports an empty peer address,
    which the handler's logging indexes. Both are handled here so the request
    handler itself needs no changes.
    """

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        try:
            os.unlink(self.server_address)
        except FileNotFoundError:
            pass
        socketserver.TCPServer.server_bind(self)
        os.chmod(self.server_address, 0o660)
        self.server_name = "unix"
        self.server_port = 0

    def get_request(self):
        conn, _ = self.socket.accept()
        return conn, ("unix", 0)


def sweep_stale_sockets(sock_dir, keep):
    """Unlink socket files in the directory that no server is serving."""
    try:
        names = os.listdir(sock_dir)
    except OSError:
        return
    for name in names:
        if not name.endswith(".sock") or name in keep:
            continue
        try:
            os.unlink(os.path.join(sock_dir, name))
        except OSError:
            pass


def bind_stream(registry, servers, sock_dir, name):
    """Bind one declared stream's socket and start serving it."""
    path = os.path.join(sock_dir, f"{name}.sock")
    try:
        server = UnixHTTPServer(path, ProxyHTTPRequestHandler)
    except OSError as e:
        registry.reject(name, f"bind failed: {type(e).__name__}")
        return
    server.stream_name = name
    server.registry = registry
    servers[name] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()


def poll_once(registry, servers, sock_dir, console_path, state_path):
    """Read the console, apply the diff to the sockets, and write the state files."""
    declarations, enabled, error = recorder_streams.load_console(console_path)
    if declarations is not None:
        accepted, rejected = recorder_streams.evaluate_console(declarations, enabled)
        added, removed = registry.apply(accepted, rejected)
        for name in removed:
            server = servers.pop(name, None)
            if server is not None:
                server.shutdown()
                server.server_close()
            try:
                os.unlink(os.path.join(sock_dir, f"{name}.sock"))
            except OSError:
                pass
            log_event("unbind", name)
        for name in added:
            bind_stream(registry, servers, sock_dir, name)
            if name in servers:
                log_event("bind", name)
    recorder_streams.write_models(sock_dir)
    recorder_streams.write_state(
        state_path, registry.state(streams_enabled=enabled, console_error=error)
    )


def main():
    if not os.environ.get("LLM_BASE_URL", "").strip() and not os.environ.get("OPENROUTER_API_KEY"):
        print("error: set OPENROUTER_API_KEY, or LLM_BASE_URL for an OpenAI-compatible upstream")
        sys.exit(1)

    socket_path = os.environ.get("LLM_SOCKET_PATH", SOCKET_PATH)
    sock_dir = os.path.dirname(socket_path) or "."
    os.makedirs(sock_dir, exist_ok=True)

    print("=" * 60)
    print("      TRANSCRIPT PROXY SERVER")
    print("=" * 60)
    print(f"Listening on:  {socket_path}")
    print(f"Forwarding to: {upstream_url()}")
    print(f"Streams to:    {stream_upstream_url()}")
    print(f"Logging to:    {TRANSCRIPT_FILE}")
    print("-" * 60)

    registry = recorder_streams.StreamRegistry()
    core = UnixHTTPServer(socket_path, ProxyHTTPRequestHandler)
    core.stream_name = "core"
    core.registry = registry
    sweep_stale_sockets(sock_dir, keep={os.path.basename(socket_path)})
    recorder_streams.write_readme(sock_dir)
    recorder_streams.write_models(sock_dir)
    threading.Thread(target=core.serve_forever, daemon=True).start()
    log_event("bind", "core")

    servers = {}
    state_path = os.path.join(sock_dir, "streams.json")
    try:
        while True:
            poll_once(registry, servers, sock_dir, recorder_streams.CONSOLE_FILE, state_path)
            time.sleep(recorder_streams.POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down proxy server...")
    finally:
        core.server_close()
        for server in servers.values():
            server.server_close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
