import gzip
import http.server
import shutil
import socketserver
import threading
import urllib.request
import urllib.error
import json
import os
import sys
import datetime

PORT = 8088
TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", os.path.dirname(os.path.abspath(__file__)))
TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")
PLAIN_TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.txt")
TRANSCRIPT_MAX_BYTES = int(os.environ.get("TRANSCRIPT_MAX_BYTES", str(134_217_728)))

_transcript_lock = threading.Lock()

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


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


def archive_name(path, stamp=None):
    """Return the timestamped gzip archive name for a transcript path."""
    if stamp is None:
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
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


class ProxyHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        sys.stdout.write(
            f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {self.client_address[0]} - {format % args}\n"
        )
        sys.stdout.flush()

    def do_POST(self):
        if self.path != "/api/v1/chat/completions":
            self.send_error(404, "Not Found")
            return

        content_length = int(self.headers.get("Content-Length", 0))
        req_body = self.rfile.read(content_length)

        try:
            req_data = json.loads(req_body.decode("utf-8"))
        except Exception:
            req_data = {"raw_body": req_body.decode("utf-8", errors="replace")}

        headers_to_forward = build_forward_headers(self.headers, upstream_api_key())

        req = urllib.request.Request(
            upstream_url(),
            data=req_body,
            headers=headers_to_forward,
            method="POST",
        )

        response_body = b""
        response_code = 500
        response_headers = []

        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                response_code = res.status
                response_body = res.read()
                for k, v in res.getheaders():
                    if k.lower() not in (
                        "content-length",
                        "transfer-encoding",
                        "connection",
                        "content-encoding",
                    ):
                        response_headers.append((k, v))
        except urllib.error.HTTPError as e:
            response_code = e.code
            response_body = e.read()
            for k, v in e.headers.items():
                if k.lower() not in (
                    "content-length",
                    "transfer-encoding",
                    "connection",
                    "content-encoding",
                ):
                    response_headers.append((k, v))
        except Exception as e:
            response_code = 500
            response_body = json.dumps({"error": {"message": f"Proxy error: {str(e)}"}}).encode(
                "utf-8"
            )
            response_headers.append(("Content-Type", "application/json"))

        try:
            res_data = json.loads(response_body.decode("utf-8"))
        except Exception:
            res_data = {"raw_body": response_body.decode("utf-8", errors="replace")}

        self.log_transcript(req_data, res_data)

        self.send_response(response_code)
        for k, v in response_headers:
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)

    def log_transcript(self, request_data, response_data):
        """Appends a new conversation step to the transcript file and dumps it to stdout."""
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)

        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "request": request_data,
            "response": response_data,
        }

        print("\n" + "=" * 80)
        print(f"PROXY INTERCEPTED REQUEST | Model: {request_data.get('model')}")
        print("=" * 80)
        for msg in request_data.get("messages", []):
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
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
        timestamp = entry.get("timestamp", datetime.datetime.utcnow().isoformat() + "Z")
        plain_log_lines.append("=" * 80)
        plain_log_lines.append(f"TRANSACTION | {timestamp} | Model: {request_data.get('model')}")
        plain_log_lines.append("=" * 80)

        plain_log_lines.append("--- REQUEST MESSAGES ---")
        for msg in request_data.get("messages", []):
            role = msg.get("role", "unknown").upper()
            content = msg.get("content", "")
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


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP Server to handle concurrent proxy requests."""

    daemon_threads = True


def main():
    if not os.environ.get("LLM_BASE_URL", "").strip() and not os.environ.get("OPENROUTER_API_KEY"):
        print("error: set OPENROUTER_API_KEY, or LLM_BASE_URL for an OpenAI-compatible upstream")
        sys.exit(1)

    print("=" * 60)
    print("      TRANSCRIPT PROXY SERVER")
    print("=" * 60)
    print(f"Listening on:  http://localhost:{PORT}")
    print(f"Forwarding to: {upstream_url()}")
    print(f"Logging to:    {TRANSCRIPT_FILE}")
    print("-" * 60)

    server = ThreadedHTTPServer(("0.0.0.0", PORT), ProxyHTTPRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down proxy server...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
