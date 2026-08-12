import json

import viewer


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_load_turns_missing_file_is_empty(tmp_path):
    turns, total = viewer.load_turns(str(tmp_path / "nope.jsonl"), 0)
    assert turns == []
    assert total == 0


def test_load_turns_since_offset(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {"timestamp": "T0", "request": {"model": "m", "messages": []}, "response": {}},
            {"timestamp": "T1", "request": {"model": "m", "messages": []}, "response": {}},
            {"timestamp": "T2", "request": {"model": "m", "messages": []}, "response": {}},
        ],
    )
    turns, total = viewer.load_turns(str(p), 1)
    assert total == 3
    assert [t["index"] for t in turns] == [1, 2]
    assert [t["timestamp"] for t in turns] == ["T1", "T2"]


def test_load_turns_retries_malformed_final_line(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"timestamp": "T0", "request": {"model": "m", "messages": []}, "response": {}}
            )
            + "\n"
        )
        f.write("{ this is a half-written line\n")  # malformed / mid-append
    turns, total = viewer.load_turns(str(p), 0)
    assert total == 1
    assert [t["index"] for t in turns] == [0]


def test_load_turns_skips_malformed_nonfinal_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(
            json.dumps(
                {"timestamp": "T0", "request": {"model": "m", "messages": []}, "response": {}}
            )
            + "\n"
        )
        f.write("{ this is corrupt and not coming back }\n")
        f.write(
            json.dumps(
                {"timestamp": "T2", "request": {"model": "m", "messages": []}, "response": {}}
            )
            + "\n"
        )
    turns, total = viewer.load_turns(str(p), 0)
    assert total == 3
    assert [t["index"] for t in turns] == [0, 2]


def test_summarize_extracts_request_messages():
    entry = {
        "timestamp": "T",
        "request": {
            "model": "deepseek",
            "messages": [
                {"role": "system", "content": "fo explore"},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {"function": {"name": "read_file", "arguments": '{"path": "agent.py"}'}}
                    ],
                },
                {"role": "tool", "name": "read_file", "content": "1: import os"},
            ],
        },
        "response": {},
    }
    t = viewer._summarize_turn(entry, 5)
    assert t["index"] == 5
    assert t["model"] == "deepseek"
    msgs = t["request_messages"]
    assert msgs[0] == {"role": "system", "name": None, "content": "fo explore", "tool_calls": []}
    assert msgs[1]["tool_calls"][0] == {"name": "read_file", "arguments": '{"path": "agent.py"}'}
    assert msgs[2] == {
        "role": "tool",
        "name": "read_file",
        "content": "1: import os",
        "tool_calls": [],
    }


def test_summarize_extracts_response_reasoning_content_and_tools():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {
            "choices": [
                {
                    "message": {
                        "reasoning_content": "let me think",
                        "content": "here is my answer",
                        "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}],
                    }
                }
            ]
        },
    }
    t = viewer._summarize_turn(entry, 0)
    r = t["response"]
    assert r["reasoning"] == "let me think"
    assert r["content"] == "here is my answer"
    assert r["tool_calls"][0]["name"] == "write_file"
    assert r["error"] is None


def test_summarize_reads_reasoning_fallback_field():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {"choices": [{"message": {"reasoning": "alt field", "content": "x"}}]},
    }
    t = viewer._summarize_turn(entry, 0)
    assert t["response"]["reasoning"] == "alt field"


def test_summarize_captures_error_responses():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {"error": {"message": "rate limited"}},
    }
    t = viewer._summarize_turn(entry, 0)
    assert t["response"]["error"] == {"message": "rate limited"}
    assert t["response"]["content"] is None


def test_http_serves_page_and_api(tmp_path, monkeypatch):
    import threading
    import urllib.request

    p = tmp_path / viewer.TRANSCRIPT_NAME
    _write_jsonl(
        p,
        [
            {
                "timestamp": "T0",
                "request": {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                "response": {"choices": [{"message": {"content": "hello"}}]},
            },
        ],
    )
    monkeypatch.setattr(viewer, "TRANSCRIPT_DIR", str(tmp_path))

    server = viewer.make_server(0)  # port 0 = ephemeral
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert r.status == 200
            assert r.headers["Cache-Control"] == "no-store"
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert r.headers["Referrer-Policy"] == "no-referrer"
            assert "default-src 'none'" in r.headers["Content-Security-Policy"]
            assert "connect-src 'self'" in r.headers["Content-Security-Policy"]
            assert "img-src 'self' data:" in r.headers["Content-Security-Policy"]
            body = r.read().decode("utf-8")
            assert "<!doctype html>" in body.lower()
            assert '<link rel="icon" href="data:,">' in body
            assert 'role="search"' in body
            assert 'id="searchInput"' in body
            assert 'id="typeFilter"' in body
            assert 'id="visibleCount"' in body
            assert 'id="clearFiltersButton"' in body
            assert 'id="followButton"' in body
            assert "function resetFeed()" in body
            assert "function focusSearch()" in body
            assert "function clearFilters()" in body
            assert "grid-template-columns: auto auto auto auto minmax(0, 1fr);" in body
            assert ".head .what { grid-column: 1 / -1;" in body
            assert "summary::before" not in body
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/turns?since=0") as r:
            assert r.status == 200
            data = json.loads(r.read().decode("utf-8"))
            assert data["total"] == 1
            assert data["turns"][0]["response"]["content"] == "hello"
            assert data["turns"][0]["request_messages"][0]["content"] == "hi"
    finally:
        server.shutdown()
        server.server_close()
