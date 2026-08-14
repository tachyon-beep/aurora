"""Standalone OpenAI-compatible stub used only by verify_container.sh.

Serves POST /v1/chat/completions on a container attached to the verify run's
own egress network, so the recorder has a reachable, credential-free upstream
during containment verification.

Almost every reply is a tool call (list_dir), so chassis.run_agent_loop keeps
looping instead of exiting after a single turn. Every TURNS_PER_INCARNATION
requests, one reply carries no tool call instead, which ends that incarnation
cleanly (chassis exits 0) rather than running until chassis's hardcoded
max_turns=1000. That bound matters: run_agent_loop re-processes the full,
ever-growing message history on every turn, and watchdog.py's "restart"
action (an isolated exit 0) never discards the saved session, so a resumed
incarnation keeps that history growing across restarts too. Left uncapped,
per-turn latency was measured degrading from ~150ms to ~480ms by turn 600, so
reaching turn 1000 takes minutes and gets slower every time the loop resumes
after a restart. Capping the incarnation at a small, fixed turn count keeps
each one's wall time bounded and roughly constant.

The cap must still keep incarnations well clear of watchdog.py's flapping
detector: ZERO_EXIT_FLAP_COUNT=3 clean exits inside
ZERO_EXIT_FLAP_WINDOW_SECONDS=120 makes the watchdog treat normal completions
as failures. Three exits land inside a 120s window only if consecutive exits
are under 60s apart; TURNS_PER_INCARNATION and REPLY_DELAY_SECONDS default to
values whose product is comfortably above that, so at most two exits ever
share a 120s window. See task-5-report.md for the measurement behind these
values.

Not copied into any image; not imported by any shipped module.
"""

import itertools
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("VERIFY_STUB_PORT", "8199"))
REPLY_DELAY_SECONDS = float(os.environ.get("VERIFY_STUB_DELAY_SECONDS", "2.0"))
TURNS_PER_INCARNATION = int(os.environ.get("VERIFY_STUB_TURNS_PER_INCARNATION", "40"))

_reply_ids = itertools.count(1)
_call_ids = itertools.count(1)
_completion_requests = itertools.count(1)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # noqa: A003 - stdlib override
        sys.stderr.write("verify_stub_llm: " + (fmt % args) + "\n")

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b"{}"
        try:
            request = json.loads(body or b"{}")
        except json.JSONDecodeError:
            request = {}

        if not self.path.endswith("/chat/completions"):
            self.send_response(404)
            self.end_headers()
            return

        time.sleep(REPLY_DELAY_SECONDS)

        model = request.get("model") or "stub-model"
        n = next(_completion_requests)
        if TURNS_PER_INCARNATION and n % TURNS_PER_INCARNATION == 0:
            message = {
                "role": "assistant",
                "content": "stub pausing this incarnation.",
            }
            finish_reason = "stop"
        else:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_stub_{next(_call_ids)}",
                        "type": "function",
                        "function": {
                            "name": "list_dir",
                            "arguments": json.dumps({"path": "."}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"

        reply = {
            "id": f"chatcmpl-{next(_reply_ids)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": finish_reason,
                    "message": message,
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        payload = json.dumps(reply).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        # Readiness probe target for the verify script.
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
