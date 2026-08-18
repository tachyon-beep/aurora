import http.client
import json
import os
import threading
from urllib.parse import quote

import pytest

from stage import codewatch, commentary, server


@pytest.fixture()
def console(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work").mkdir(parents=True)
    (telemetry / "work" / "agent.py").write_text("CURRENT\n", encoding="utf-8")
    (telemetry / "work" / "agent_stock.py").write_text("STOCK\n", encoding="utf-8")
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    monkeypatch.setenv("STAGE_CONSOLE_TOKEN", "sekrit")
    httpd = server.make_server(0, server.ConsoleHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path, token="sekrit"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"X-Console-Token": token} if token is not None else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_console_requires_token(console):
    status, _ = _get(console, "/api/roots", token=None)
    assert status == 401
    status, _ = _get(console, "/api/roots", token="wrong")
    assert status == 401
    status, body = _get(console, "/api/roots")
    assert status == 200
    assert set(json.loads(body)) == {"telemetry", "transcripts", "diode"}


def test_console_rejects_non_ascii_token_without_crashing(console):
    status, _ = _get(console, "/api/roots?token=" + quote("café"), token=None)
    assert status == 401


def test_console_accepts_non_ascii_configured_token(console, monkeypatch):
    monkeypatch.setenv("STAGE_CONSOLE_TOKEN", "café")
    status, body = _get(console, "/api/roots?token=" + quote("café"), token=None)
    assert status == 200
    assert set(json.loads(body)) == {"telemetry", "transcripts", "diode"}


def test_console_accepts_non_ascii_configured_token_via_header(console, monkeypatch):
    monkeypatch.setenv("STAGE_CONSOLE_TOKEN", "café")
    status, body = _get(console, "/api/roots", token="café")
    assert status == 200
    assert set(json.loads(body)) == {"telemetry", "transcripts", "diode"}


def test_console_fails_closed_without_configured_token(console, monkeypatch):
    monkeypatch.delenv("STAGE_CONSOLE_TOKEN")
    status, _ = _get(console, "/api/roots")
    assert status == 403


def test_browse_and_file(console):
    status, body = _get(console, "/api/browse?root=telemetry&path=work")
    assert status == 200
    names = [e["name"] for e in json.loads(body)["entries"]]
    assert "agent.py" in names
    status, body = _get(console, "/api/file?root=telemetry&path=work/agent.py")
    assert status == 200
    assert json.loads(body)["content"] == "CURRENT\n"


def test_browse_rejects_escape_and_unknown_root(console):
    status, _ = _get(console, "/api/browse?root=telemetry&path=../..")
    assert status == 404
    status, _ = _get(console, "/api/browse?root=etc&path=")
    assert status == 404


def test_download_sets_attachment(console):
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request(
        "GET",
        "/download?root=telemetry&path=work/agent.py",
        headers={"X-Console-Token": "sekrit"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    assert "attachment" in resp.getheader("Content-Disposition", "")
    assert resp.read() == b"CURRENT\n"
    conn.close()


def test_download_sanitizes_crlf_in_filename(console, tmp_path):
    evil_name = 'evil\r\nX-Injected: 1\r\nContent-Disposition: attachment; filename="x'
    (tmp_path / "telemetry" / "work" / evil_name).write_text("payload", encoding="utf-8")
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request(
        "GET",
        "/download?root=telemetry&path=" + quote("work/" + evil_name),
        headers={"X-Console-Token": "sekrit"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    assert resp.getheader("X-Injected") is None
    content_disposition = resp.getheader("Content-Disposition", "")
    assert "\r" not in content_disposition and "\n" not in content_disposition
    resp.read()
    conn.close()


def test_diff_view(console):
    status, body = _get(console, "/api/diff")
    assert status == 200
    text = json.loads(body)["diff"]
    assert "-STOCK" in text and "+CURRENT" in text


@pytest.mark.parametrize("name", ["agent.py", "agent_stock.py"])
def test_diff_rejects_a_source_symlink_outside_telemetry(console, tmp_path, name):
    outside = tmp_path / f"outside-{name}"
    outside.write_text("OUTSIDE-DIFF-SENTINEL\n", encoding="utf-8")
    source = tmp_path / "telemetry" / "work" / name
    source.unlink()
    source.symlink_to(outside)

    status, body = _get(console, "/api/diff")

    assert status == 404
    assert b"OUTSIDE-DIFF-SENTINEL" not in body


def test_console_page_served(console):
    status, body = _get(console, "/")
    assert status == 200
    assert b"<!doctype html" in body.lower()


def test_console_rejects_post(console):
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request("POST", "/api/roots", headers={"X-Console-Token": "sekrit"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 405


@pytest.fixture()
def stream(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    tomb = telemetry / "work" / "tombstones"
    tomb.mkdir(parents=True)
    (tomb / "incarnation-1.txt").write_text("ended early. detail.\n", encoding="utf-8")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}],
                    }
                }
            ]
        },
    }
    (transcripts / "agent_life_transcript.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    httpd = server.make_server(0, server.StreamHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _plain_get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def call_stream_route(path):
    """Drive one GET through a real StreamHandler; returns (status, headers, body).

    The route runs over a loopback server, the way every other stream test in
    this file does, so headers and body are exactly what a browser would see.
    """
    httpd = server.make_server(0, server.StreamHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        conn.request("GET", path)
        resp = conn.getresponse()
        status, headers, body = resp.status, resp.headers, resp.read()
        conn.close()
    finally:
        httpd.shutdown()
    return status, headers, body


def test_stream_page_served_without_token(stream):
    status, body = _plain_get(stream, "/")
    assert status == 200
    assert b"<!doctype html" in body.lower()


def test_stream_snapshot_shape(stream):
    status, body = _plain_get(stream, "/api/stream")
    assert status == 200
    snap = json.loads(body)
    assert snap["stats"]["incarnation"] == 2
    assert snap["stats"]["model"] == "m"
    assert snap["turns"][-1]["content"] == "hello"
    assert snap["events"][-1]["name"] == "write_file"
    assert snap["lineage"][0]["summary"] == "ended early. detail."
    assert "diode" in snap
    assert "evidence" in snap["commentary"]["colour"]
    assert isinstance(snap["commentary"]["colour"]["evidence"], str)


def test_stream_snapshot_caps_and_slims_public_data(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work" / "tombstones").mkdir(parents=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "x" * 10_000,
                        "reasoning_content": "y" * 10_000,
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "write_file",
                                    "arguments": "z" * 5_000,
                                }
                            }
                        ],
                    }
                }
            ]
        },
    }
    (transcripts / "agent_life_transcript.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    diode_dir = tmp_path / "diode"
    diode_dir.mkdir()
    (diode_dir / "console.json").write_text("secret console body", encoding="utf-8")
    (diode_dir / "state.json").write_text("secret state body", encoding="utf-8")
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(diode_dir))
    httpd = server.make_server(0, server.StreamHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _plain_get(httpd.server_address[1], "/api/stream")
    finally:
        httpd.shutdown()
    assert status == 200
    snap = json.loads(body)
    assert "console" not in snap["diode"]
    assert "state" not in snap["diode"]
    turn = snap["turns"][-1]
    assert len(turn["content"]) <= 8000
    assert len(turn["reasoning"]) <= 8000
    assert len(turn["tool_calls"][0]["arguments"]) <= 400
    assert turn["content_chars"] == 10_000
    assert turn["content_truncated"] is True
    assert turn["reasoning_chars"] == 10_000
    assert turn["reasoning_truncated"] is True
    assert turn["tool_calls"][0]["arguments_chars"] == 5_000
    assert turn["tool_calls"][0]["arguments_truncated"] is True


def test_stream_port_has_no_mutating_routes(stream):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        conn = http.client.HTTPConnection("127.0.0.1", stream, timeout=5)
        conn.request(method, "/api/stream")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 405


def test_stream_unknown_route_404(stream):
    status, _ = _plain_get(stream, "/api/nope")
    assert status == 404


def test_audio_route_serves_a_real_utterance(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"ID3audio")
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    status, headers, body = call_stream_route("/audio/20260814_120000_000000.mp3")
    assert status == 200
    assert headers["Content-Type"] == "audio/mpeg"
    assert body == b"ID3audio"


def test_audio_route_rejects_traversal_and_unknown_names(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"ID3audio")
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    for route in (
        "/audio/../../etc/passwd",
        "/audio/%2e%2e%2fetc%2fpasswd",
        "/audio/../spoken/20260814_120000_000000.mp3",
        "/audio/nothing.mp3",
        "/audio/",
        "/audio/notes.txt",
    ):
        status, _, _ = call_stream_route(route)
        assert status == 404


def test_audio_route_carries_the_security_headers(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"ID3audio")
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    status, headers, _ = call_stream_route("/audio/20260814_120000_000000.mp3")
    assert status == 200
    assert headers["Content-Length"] == "8"
    for key, value in server.SECURITY_HEADERS.items():
        assert headers[key] == value


def test_audio_route_streams_without_buffering_the_whole_file(tmp_path, monkeypatch):
    # This route is on the publicly tunnelled stream port, so a request must not
    # hold a whole utterance in the stage's 256 MB of memory.
    import tracemalloc

    spoken = tmp_path / "spoken"
    spoken.mkdir()
    size = 56 * 65536
    with open(spoken / "20260814_120000_000000.mp3", "wb") as f:
        for _ in range(56):
            f.write(b"x" * 65536)
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    httpd = server.make_server(0, server.StreamHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    tracemalloc.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", "/audio/20260814_120000_000000.mp3")
        resp = conn.getresponse()
        received = 0
        while True:
            chunk = resp.read(65536)
            if not chunk:
                break
            received += len(chunk)
        peak = tracemalloc.get_traced_memory()[1]
        conn.close()
    finally:
        tracemalloc.stop()
        httpd.shutdown()
    assert resp.status == 200
    assert received == size
    assert resp.getheader("Content-Length") == str(size)
    assert peak < size


def test_stream_csp_allows_media_from_self():
    assert "media-src 'self'" in server.SECURITY_HEADERS["Content-Security-Policy"]


def test_download_streams_large_files_without_loading_them(console, tmp_path):
    import tracemalloc

    big = tmp_path / "telemetry" / "work" / "big.log"
    with open(big, "wb") as f:
        for _ in range(320):
            f.write(b"x" * 65536)
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=10)
    conn.request(
        "GET",
        "/download?root=telemetry&path=work/big.log",
        headers={"X-Console-Token": "sekrit"},
    )
    tracemalloc.start()
    resp = conn.getresponse()
    received = 0
    while True:
        chunk = resp.read(65536)
        if not chunk:
            break
        received += len(chunk)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    conn.close()
    assert resp.status == 200
    assert received == 320 * 65536
    assert resp.getheader("Content-Length") == str(320 * 65536)
    assert peak < 8_000_000


def _turn_entry(index, error=None, reasoning=None, tool_calls=None, timestamp="T"):
    message = {"content": f"c{index}"}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = [{"function": {"name": n, "arguments": a}} for n, a in tool_calls]
    response = {"error": error} if error is not None else {"choices": [{"message": message}]}
    return {"timestamp": timestamp, "request": {"model": "m"}, "response": response}


def _snapshot(tmp_path, monkeypatch, entries, tombstones=(), diode=()):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work" / "tombstones").mkdir(parents=True)
    for name, text in tombstones:
        (telemetry / "work" / "tombstones" / name).write_text(text, encoding="utf-8")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    with open(transcripts / "agent_life_transcript.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    diode_dir = tmp_path / "diode"
    diode_dir.mkdir()
    for relative, text in diode:
        target = diode_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(diode_dir))
    return server.stream_snapshot()


def test_stream_snapshot_carries_the_full_key_set(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert set(snap) == {
        "now",
        "stats",
        "code",
        "turns",
        "events",
        "lanes",
        "lanes_omitted",
        "pulse",
        "records",
        "desk",
        "achievements",
        "diode",
        "lineage",
        "story",
        "commentary",
    }
    assert set(snap["stats"]) == {
        "incarnation",
        "model",
        "transcript_turns",
        "turns_this_life",
        "turns_this_life_exact",
        "last_timestamp",
        "last_epoch",
        "started_epoch",
        "session_file_present",
        "lives_ended",
        "ended_by_choice",
        "error_count",
        "self_calls",
        "self_edits_this_life",
    }
    assert set(snap["diode"]) == {
        "outputs",
        "operations_total",
        "operations_life",
        "published",
        "published_total",
        "spoken",
        "spoken_total",
    }
    assert isinstance(snap["now"], float)
    assert snap["code"] == {"available": False, "added": 0, "removed": 0, "latest_edit": None}


def test_the_empty_snapshot_matches_a_live_one_on_the_new_keys(tmp_path, monkeypatch):
    codewatch._reset_for_tests()
    live = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    empty = server._empty_snapshot(1000.0)
    assert set(empty["pulse"]) == set(live["pulse"])
    assert len(empty["pulse"]["buckets"]) == len(live["pulse"]["buckets"]) == 20
    assert set(empty["records"]) == set(live["records"])
    assert empty["desk"] is None
    assert set(empty["code"]) == set(live["code"])


def test_the_desk_key_caps_and_rejects_malformed_shapes():
    assert server._public_desk(None) is None
    assert server._public_desk({"verdicts": []}) is None
    assert server._public_desk({"verdicts": [{"ordinal": 3, "stars": True}]}) is None
    package = server._public_desk(
        {
            "verdicts": [
                {
                    "ordinal": 3,
                    "stars": 9,
                    "line": "x" * 500,
                    "evidence": "y" * 500,
                    "depth": "full",
                }
            ],
            "generated_at": 12.0,
            "model": "m" * 200,
            "duration_seconds": 20,
        }
    )
    assert package["verdicts"][0]["stars"] == 5
    assert len(package["verdicts"][0]["line"]) == server.DESK_LINE_CAP
    assert len(package["verdicts"][0]["evidence"]) == server.DESK_EVIDENCE_CAP
    assert len(package["model"]) == server.MODEL_CAP


def test_snapshot_carries_a_commentary_block(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert set(snap["commentary"]) == {"play", "colour"}
    assert set(snap["commentary"]["play"]) == {"tag", "phrase", "epoch"}
    assert set(snap["commentary"]["colour"]) == {"text", "generated", "beat", "evidence"}
    assert snap["commentary"]["colour"]["text"].strip()


def test_the_empty_snapshot_carries_the_same_commentary_shape():
    snap = server._empty_snapshot(1000.0)
    assert set(snap["commentary"]) == {"play", "colour"}
    assert snap["commentary"]["colour"]["text"].strip()


def test_the_empty_snapshot_reports_the_same_beat_shape_as_a_live_one():
    """A consumer must not meet two shapes depending on whether the stage fell back."""
    empty = server._empty_snapshot(1000.0)["commentary"]["colour"]["beat"]
    live = commentary.colour_line(commentary._beat("working"))["beat"]
    assert empty == live


def test_the_empty_snapshot_play_matches_a_live_empty_play_by_play():
    """The empty snapshot's play block was a hand-duplicated copy of play_by_play's
    own empty-turns branch. play_by_play([], {}, {}) is inert (no lock, no I/O),
    so there is no reason for the copy to exist or to be able to drift."""
    empty = server._empty_snapshot(1000.0)["commentary"]["play"]
    live = commentary.play_by_play([], {}, {})
    assert empty == live


def test_beat_detection_reads_the_full_tail_not_the_display_slice(tmp_path, monkeypatch):
    """Handing detect_beat the 6-turn display slice would hide most of the evidence."""
    seen = {}
    real = server.commentary.detect_beat

    def spy(turns, stats, diode, published, now, spoken=None):
        seen["count"] = len(turns)
        return real(turns, stats, diode, published, now, spoken=spoken)

    monkeypatch.setattr(server.commentary, "detect_beat", spy)
    entries = [_turn_entry(i) for i in range(server.DISPLAY_TURNS * 3)]
    _snapshot(tmp_path, monkeypatch, entries)
    assert seen["count"] == len(entries)
    assert seen["count"] > server.DISPLAY_TURNS


def test_stream_snapshot_story_is_null_when_the_summariser_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert snap["story"] is None


def test_stream_snapshot_survives_a_missing_summary_module(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "summary", None)
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert snap["story"] is None


def test_stream_snapshot_caps_and_clips_a_generated_story(tmp_path, monkeypatch):
    class FakeSummary:
        @staticmethod
        def cached_story():
            return {"text": "s" * 4000, "generated_at": 1.0, "model": "m" * 200}

    monkeypatch.setattr(server, "summary", FakeSummary)
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert len(snap["story"]["text"]) == 1200
    assert len(snap["story"]["model"]) == 60
    assert snap["story"]["generated_at"] == 1.0


def test_stream_snapshot_ignores_a_broken_summary_module(tmp_path, monkeypatch):
    class Broken:
        @staticmethod
        def cached_story():
            raise RuntimeError("upstream is on fire")

    monkeypatch.setattr(server, "summary", Broken)
    assert _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])["story"] is None


def test_stream_snapshot_ignores_a_malformed_story(tmp_path, monkeypatch):
    class Malformed:
        @staticmethod
        def cached_story():
            return {"text": "", "generated_at": None, "model": None}

    monkeypatch.setattr(server, "summary", Malformed)
    assert _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])["story"] is None


def test_stream_snapshot_story_always_carries_a_numeric_generated_at(tmp_path, monkeypatch):
    class NoTimestamp:
        @staticmethod
        def cached_story():
            return {"text": "a recap.", "generated_at": None, "model": "m"}

    monkeypatch.setattr(server, "summary", NoTimestamp)
    story = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])["story"]
    assert isinstance(story["generated_at"], float)


def test_stream_snapshot_publishes_only_the_newest_six_turns(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(i) for i in range(12)])
    assert len(snap["turns"]) == 6
    assert [t["index"] for t in snap["turns"]] == [6, 7, 8, 9, 10, 11]
    assert snap["stats"]["transcript_turns"] == 12


def test_stream_snapshot_error_field_is_capped_for_both_shapes(tmp_path, monkeypatch):
    entries = [
        _turn_entry(0, error={"message": "z" * 5000, "code": 400}),
        _turn_entry(1, error="a bare string failure " * 100),
        _turn_entry(2, error={"message": "x", "code": {"nested": "y" * 5000}}),
        _turn_entry(3, error={"message": "x", "code": "rate_limit_exceeded_" + "q" * 500}),
    ]
    snap = _snapshot(tmp_path, monkeypatch, entries)
    first, second, third, fourth = snap["turns"]
    assert third["error"]["code"] is None
    assert len(fourth["error"]["code"]) == 40
    assert len(first["error"]["message"]) == 600
    assert first["error"]["code"] == 400
    assert len(second["error"]["message"]) == 600
    assert second["error"]["code"] is None
    assert snap["stats"]["error_count"] == 4


def test_stream_snapshot_marks_edits_and_endings(tmp_path, monkeypatch):
    entries = [
        _turn_entry(0, tool_calls=[("validate", "{}")]),
        _turn_entry(1, tool_calls=[("write_file", '{"start_line": 1, "end_line": 2}')]),
        _turn_entry(2, tool_calls=[("done", '{"message": "enough. really."}')]),
    ]
    snap = _snapshot(tmp_path, monkeypatch, entries)
    assert [t["is_edit"] for t in snap["turns"]] == [False, True, False]
    assert [t["is_end"] for t in snap["turns"]] == [False, False, True]
    assert snap["events"][0]["headline"] == "REWROTE LINES 1-2"
    assert snap["events"][1]["summary"] == "enough."
    assert snap["events"][1]["quoted"] is True


def test_stream_snapshot_reports_diff_statistics_but_no_source(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work").mkdir(parents=True)
    (telemetry / "work" / "agent_stock.py").write_text("a\nb\n", encoding="utf-8")
    (telemetry / "work" / "agent.py").write_text("a\nSECRETLINE\nb\n", encoding="utf-8")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    (transcripts / "agent_life_transcript.jsonl").write_text(
        json.dumps(_turn_entry(0)) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    codewatch._reset_for_tests()
    snap = server.stream_snapshot()
    assert snap["code"] == {"available": True, "added": 1, "removed": 0, "latest_edit": None}
    assert "SECRETLINE" not in json.dumps(snap)
    assert "SECRETLINE" not in json.dumps(snap)


def test_stream_snapshot_carries_published_transmissions(tmp_path, monkeypatch):
    diode = [
        ("output/20260814T041050Z-fetchlinks.txt", "links"),
        ("output/20260814T040012Z-wikipedia.md", "summary"),
        ("published/20260814_041231_004411.txt", "p" * 900),
        ("published/20260814_041000_000001.txt", "older"),
        ("published/20260813_041000_000001.txt", "oldest"),
    ]
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)], diode=diode)
    assert snap["diode"]["published_total"] == 3
    assert len(snap["diode"]["published"]) == 2
    assert len(snap["diode"]["published"][0]["text"]) == 400
    assert snap["diode"]["published"][0]["chars"] == 900
    assert snap["diode"]["outputs"][0]["verb"] == "followed a link"
    assert "name" in snap["diode"]["outputs"][0]
    epochs = [o["epoch"] for o in snap["diode"]["outputs"]]
    assert epochs == sorted(epochs, reverse=True)


def test_stream_snapshot_carries_operation_counts_past_the_display_slice(tmp_path, monkeypatch):
    """`outputs` is sliced to DISPLAY_OUTPUTS rows, so a page counting the rows it
    was sent stops counting at four. publish files a result in output/ like every
    other command, so published_total is already inside the operation count and
    adding it would count one reach outside twice."""
    diode = [(f"output/20260814_0410{i:02d}_000000_weather_x.txt", "x") for i in range(9)]
    diode.append(("output/20260814_041100_000000_publish_hello.txt", "published"))
    diode.append(("published/20260814_041100_000001.txt", "hello"))
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)], diode=diode)
    assert len(snap["diode"]["outputs"]) == server.DISPLAY_OUTPUTS
    assert snap["diode"]["operations_total"] == 10
    assert snap["diode"]["operations_life"] == 10
    assert snap["diode"]["published_total"] == 1


def test_stream_snapshot_carries_spoken_utterances(tmp_path, monkeypatch):
    diode = [
        ("spoken/20260814_120000_000000.mp3", "ID3audio"),
        ("spoken/20260814_120000_000000.txt", "hello there"),
        ("spoken/20260814_110000_000000.mp3", "ID3audio"),
        ("spoken/20260814_110000_000000.txt", "older"),
        ("spoken/20260814_100000_000000.mp3", "ID3audio"),
        ("spoken/20260814_100000_000000.txt", "oldest"),
    ]
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)], diode=diode)
    assert snap["diode"]["spoken_total"] == 3
    assert len(snap["diode"]["spoken"]) == 2
    assert snap["diode"]["spoken"][0]["name"] == "20260814_120000_000000.mp3"
    assert snap["diode"]["spoken"][0]["text"] == "hello there"


def test_the_empty_snapshot_carries_the_same_diode_keys_as_a_live_one(tmp_path, monkeypatch):
    live = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert set(server._empty_snapshot(1000.0)["diode"]) == set(live["diode"])


def test_stream_snapshot_limits_diode_outputs_to_four(tmp_path, monkeypatch):
    diode = [(f"output/2026081{i}T041050Z-weather.txt", "x") for i in range(9)]
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)], diode=diode)
    assert len(snap["diode"]["outputs"]) == 4
    assert snap["diode"]["published"] == []
    assert snap["diode"]["published_total"] == 0


def test_stream_snapshot_never_raises_on_missing_directories(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path / "nope" / "telemetry"))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path / "nope" / "transcripts"))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "nope" / "diode"))
    snap = server.stream_snapshot()
    assert set(snap) == {
        "now",
        "stats",
        "code",
        "turns",
        "events",
        "lanes",
        "lanes_omitted",
        "pulse",
        "records",
        "desk",
        "achievements",
        "diode",
        "lineage",
        "story",
        "commentary",
    }
    assert snap["turns"] == []
    assert snap["stats"]["incarnation"] == 1
    assert snap["lineage"] == []


def test_stream_snapshot_returns_the_full_key_set_when_a_reader_fails(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("mirror vanished mid-read")

    monkeypatch.setattr(server.data, "load_tail_turns", explode)
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    snap = server.stream_snapshot()
    assert set(snap) == {
        "now",
        "stats",
        "code",
        "turns",
        "events",
        "lanes",
        "lanes_omitted",
        "pulse",
        "records",
        "desk",
        "achievements",
        "diode",
        "lineage",
        "story",
        "commentary",
    }
    assert snap["story"] is None


def test_stream_endpoint_returns_200_when_every_source_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "absent"))
    httpd = server.make_server(0, server.StreamHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        status, body = _plain_get(httpd.server_address[1], "/api/stream")
    finally:
        httpd.shutdown()
    assert status == 200
    assert json.loads(body)["turns"] == []


def test_stream_snapshot_stays_small_with_the_longest_legal_payload(tmp_path, monkeypatch):
    entries = [
        _turn_entry(
            i,
            reasoning="r" * 12_000,
            tool_calls=[("write_file", "z" * 5_000) for _ in range(5)],
        )
        for i in range(10)
    ]
    snap = _snapshot(tmp_path, monkeypatch, entries)
    body = json.dumps(snap)
    assert len(snap["turns"]) == 6
    assert all(t["reasoning_chars"] == 12_000 for t in snap["turns"])
    assert all(len(t["reasoning"]) == 8_000 for t in snap["turns"])
    assert len(body) < 130_000


def test_stream_snapshot_first_boot_does_not_look_like_a_dead_life(tmp_path, monkeypatch):
    entries = [
        _turn_entry(0, timestamp="2026-08-14T04:11:02Z"),
        _turn_entry(1, timestamp="2026-08-14T04:12:02Z"),
    ]
    snap = _snapshot(tmp_path, monkeypatch, entries)
    assert snap["stats"]["incarnation"] == 1
    assert snap["stats"]["lives_ended"] == 0
    assert [t["life"] for t in snap["turns"]] == [1, 1]
    assert snap["stats"]["turns_this_life"] == 2
    assert snap["lineage"] == []


def test_stream_snapshot_turns_are_ascending_and_carry_no_model(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(i) for i in range(3)])
    indexes = [t["index"] for t in snap["turns"]]
    assert indexes == sorted(indexes)
    assert set(snap["turns"][0]) == {
        "index",
        "kind",
        "prompt",
        "timestamp",
        "epoch",
        "life",
        "reasoning",
        "reasoning_chars",
        "reasoning_truncated",
        "content",
        "content_chars",
        "content_truncated",
        "tool_calls",
        "error",
        "is_edit",
        "is_end",
    }


def test_select_display_counts_loop_turns_only():
    turns = [
        {"index": 0, "kind": "loop"},
        {"index": 1, "kind": "subcall"},
        {"index": 2, "kind": "loop"},
        {"index": 3, "kind": "subcall"},
        {"index": 4, "kind": "subcall"},
        {"index": 5, "kind": "loop"},
    ]
    got = server.select_display(turns, count=2)
    assert [t["index"] for t in got] == [2, 3, 4, 5]
    got = server.select_display(turns, count=1)
    assert [t["index"] for t in got] == [5]


def test_select_display_drops_orphan_leading_subcalls():
    turns = [{"index": 0, "kind": "subcall"}, {"index": 1, "kind": "loop"}]
    assert [t["index"] for t in server.select_display(turns, count=5)] == [1]


def test_select_display_caps_subcalls_per_parent_to_the_newest_three():
    turns = [{"index": 0, "kind": "loop"}]
    turns += [{"index": i, "kind": "subcall"} for i in range(1, 6)]
    got = server.select_display(turns, count=5)
    assert [t["index"] for t in got] == [0, 3, 4, 5]


def test_select_display_caps_subcalls_in_every_run_independently():
    turns = [
        {"index": 0, "kind": "loop"},
        {"index": 1, "kind": "subcall"},
        {"index": 2, "kind": "subcall"},
        {"index": 3, "kind": "subcall"},
        {"index": 4, "kind": "subcall"},
        {"index": 5, "kind": "loop"},
        {"index": 6, "kind": "subcall"},
        {"index": 7, "kind": "subcall"},
    ]
    got = server.select_display(turns, count=5)
    assert [t["index"] for t in got] == [0, 2, 3, 4, 5, 6, 7]


def test_public_turn_carries_kind_and_capped_prompt():
    public = server._public_turn(
        {"index": 1, "kind": "subcall", "prompt": "y" * 500, "tool_calls": []}
    )
    assert public["kind"] == "subcall"
    assert len(public["prompt"]) == server.PROMPT_CAP
    loop = server._public_turn({"index": 2, "kind": "loop", "prompt": "", "tool_calls": []})
    assert loop["kind"] == "loop"
    assert loop["prompt"] == ""


def test_public_turn_slims_a_subcall_but_not_a_loop_turn():
    subcall = server._public_turn(
        {
            "index": 1,
            "kind": "subcall",
            "prompt": "reply",
            "timestamp": "T",
            "epoch": 1.0,
            "life": 2,
            "reasoning": "should never surface",
            "content": "should never surface either",
            "tool_calls": [{"name": "write_file", "arguments": "{}"}],
            "error": None,
        }
    )
    assert set(subcall) == {"index", "kind", "prompt", "timestamp", "epoch", "life"}
    assert "reasoning" not in subcall
    assert "content" not in subcall

    loop = server._public_turn(
        {
            "index": 2,
            "kind": "loop",
            "prompt": "",
            "timestamp": "T",
            "epoch": 1.0,
            "life": 2,
            "reasoning": "thinking",
            "content": "saying",
            "tool_calls": [],
            "error": None,
        }
    )
    assert "reasoning" in loop
    assert "content" in loop
    assert loop["reasoning"] == "thinking"
    assert loop["content"] == "saying"


def test_empty_snapshot_reports_self_calls():
    assert server._empty_snapshot(0.0)["stats"]["self_calls"] == 0


def test_stream_snapshot_demotes_a_tool_sub_call(tmp_path, monkeypatch):
    loop = _turn_entry(0)
    loop["request"]["messages"] = [{"role": "system"}, {"role": "user"}]
    loop["request"]["tools"] = [{"type": "function"}]
    sub = _turn_entry(1)
    sub["request"] = {
        "model": "other/sub",
        "messages": [{"role": "user", "content": "Reply  with\nPONG"}],
    }
    snap = _snapshot(tmp_path, monkeypatch, [loop, sub, _turn_entry(2)])
    assert [t["kind"] for t in snap["turns"]] == ["loop", "subcall", "loop"]
    assert snap["turns"][1]["prompt"] == "Reply with PONG"
    assert snap["stats"]["turns_this_life"] == 2
    assert snap["stats"]["self_calls"] == 1
    assert snap["stats"]["model"] == "m"


def test_the_snapshot_and_empty_snapshot_carry_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    snap = server.stream_snapshot()
    assert isinstance(snap["lanes"], list)
    assert snap["lanes"][0]["name"] == "core"
    assert server._empty_snapshot(1000.0)["lanes"] == []


def test_public_lanes_cap_names_and_count():
    lanes = [
        {
            "name": "x" * 100,
            "bound": True,
            "in_flight": 1,
            "in_flight_since": 5.0,
            "last_epoch": 6.0,
            "requests_hour": 2,
            "errors_hour": 1,
            "tokens_hour": 30,
        }
    ] * 20
    public = server._public_lanes(lanes)
    assert len(public) == server.LANES_CAP
    assert len(public[0]["name"]) == server.LANE_NAME_CAP
    assert public[0]["in_flight"] == 1
    assert public[0]["tokens_hour"] == 30


def _lane(name, **fields):
    lane = {
        "name": name,
        "bound": False,
        "in_flight": 0,
        "in_flight_since": None,
        "last_epoch": None,
        "requests_hour": 0,
        "errors_hour": 0,
        "tokens_hour": 0,
    }
    lane.update(fields)
    return lane


def test_public_lanes_cap_drops_retired_lanes_before_bound_ones():
    """stream_lanes keeps a recently active unbound lane for an hour and sorts by
    name, so after a round of renaming the list outruns the cap with retired lanes
    sorted ahead of live ones. Slicing that order drops sockets the agent is using
    in favour of sockets that no longer exist."""
    retired = [_lane("aaa-%d" % i, requests_hour=1) for i in range(8)]
    bound = [_lane("zzz-%d" % i, bound=True) for i in range(3)]
    public = server._public_lanes([_lane("core", bound=True)] + retired + bound)
    names = [lane["name"] for lane in public]
    assert names[0] == "core"
    assert set(names) >= {"zzz-0", "zzz-1", "zzz-2"}
    assert len(public) == server.LANES_CAP


def test_public_lanes_order_survives_a_lane_that_has_never_been_used():
    """last_epoch is None until a lane opens or closes a request, and comparing
    None with a float raises. stream_snapshot swallows the error into the empty
    snapshot, so the whole page would blank rather than fail loudly."""
    public = server._public_lanes(
        [_lane("quiet", bound=True), _lane("busy", bound=True, last_epoch=5.0)]
    )
    assert [lane["name"] for lane in public] == ["busy", "quiet"]


def test_public_lanes_ranks_in_flight_above_idle_and_recent_above_stale():
    public = server._public_lanes(
        [
            _lane("stale", bound=True, last_epoch=1.0),
            _lane("recent", bound=True, last_epoch=9.0),
            _lane("busy", bound=True, in_flight=1, last_epoch=1.0),
        ]
    )
    assert [lane["name"] for lane in public] == ["busy", "recent", "stale"]


def test_the_snapshot_reports_the_lanes_the_cap_dropped(tmp_path, monkeypatch):
    """A cap that silently shortens the list leaves the page's "N more streams not
    shown" understating what it hides."""
    assert server._lanes_omitted([_lane("l-%d" % i) for i in range(server.LANES_CAP + 4)]) == 4
    assert server._lanes_omitted([_lane("core")]) == 0
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    assert server.stream_snapshot()["lanes_omitted"] == 0
    assert server._empty_snapshot(1000.0)["lanes_omitted"] == 0


def test_public_records_passes_lives_through_capped_and_coerced():
    lives = [
        {"ordinal": i, "kind": "declared", "seconds": 10.0 * i, "ended_epoch": 1000.0 + i}
        for i in range(1, server.LIVES_CAP + 4)
    ]
    lives[0]["kind"] = "bogus"
    lives[1]["seconds"] = None
    out = server._public_records({"lives_ended": len(lives), "chose": 1, "lives": lives})
    assert out["lives_omitted"] == 3
    assert len(out["lives"]) == server.LIVES_CAP
    assert out["lives"][0]["ordinal"] == 4, "the newest LIVES_CAP survive"
    assert out["lives"][-1] == {
        "ordinal": server.LIVES_CAP + 3,
        "kind": "declared",
        "seconds": 10.0 * (server.LIVES_CAP + 3),
        "ended_epoch": 1000.0 + server.LIVES_CAP + 3,
    }


def test_public_records_normalises_kind_and_absent_lives():
    out = server._public_records({"lives": [{"ordinal": "1", "kind": "weird", "seconds": "x"}]})
    assert out["lives"] == [{"ordinal": 1, "kind": "unknown", "seconds": None, "ended_epoch": None}]
    assert server._public_records({})["lives"] == []
    assert server._public_records({})["lives_omitted"] == 0


def test_the_census_overlays_exact_life_figures_when_it_has_a_row(tmp_path, monkeypatch):
    from stage import census

    census._reset_for_tests()
    try:
        snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0), _turn_entry(1)])
        assert snap["stats"]["self_edits_this_life"] is None
        census._MEMO["lives"] = [
            {
                "ordinal": 1,
                "current": True,
                "turns": 250,
                "subcalls": 0,
                "errors": 0,
                "edits": 19,
                "exact": True,
            }
        ]
        snap = server.stream_snapshot()
        assert snap["stats"]["turns_this_life"] == 250
        assert snap["stats"]["turns_this_life_exact"] is True
        assert snap["stats"]["self_edits_this_life"] == 19
        census._MEMO["lives"] = [
            {"ordinal": 7, "current": True, "turns": 1, "edits": 1, "exact": True}
        ]
        snap = server.stream_snapshot()
        assert snap["stats"]["self_edits_this_life"] is None
        assert snap["stats"]["turns_this_life"] == 2
        # An inexact census (an undatable tombstone, an unplaceable entry) is not overlaid.
        census._MEMO["lives"] = [
            {"ordinal": 1, "current": True, "turns": 250, "edits": 19, "exact": False}
        ]
        snap = server.stream_snapshot()
        assert snap["stats"]["self_edits_this_life"] is None
        assert snap["stats"]["turns_this_life"] == 2
    finally:
        census._reset_for_tests()


def test_the_empty_snapshot_carries_a_null_self_edit_count():
    assert server._empty_snapshot(1.0)["stats"]["self_edits_this_life"] is None


def test_achievements_are_projected_newest_first_capped_and_validated():
    rows = [
        {"ordinal": i, "line": "did a thing " + str(i), "generated_at": 5.0}
        for i in range(30, 0, -1)
    ]
    rows.insert(0, {"ordinal": True, "line": "bool ordinal"})
    rows.insert(0, {"ordinal": 99, "line": ""})
    rows.insert(0, "junk")
    out = server._public_achievements(rows)
    assert len(out) == server.ACHIEVEMENTS_CAP
    assert out[0] == {"ordinal": 30, "line": "did a thing 30", "generated_at": 5.0}
    long = server._public_achievements([{"ordinal": 1, "line": "x" * 500, "generated_at": "no"}])
    assert len(long[0]["line"]) == server.ACHIEVEMENT_CHARS
    assert long[0]["generated_at"] is None
    assert server._public_achievements(None) == []
    assert server._empty_snapshot(1.0)["achievements"] == []


def _lineage_tree(tmp_path, monkeypatch, entries, tombstones=()):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work" / "tombstones").mkdir(parents=True)
    for name, text, epoch in tombstones:
        path = telemetry / "work" / "tombstones" / name
        path.write_text(text, encoding="utf-8")
        import os

        os.utime(path, (epoch, epoch))
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    with open(transcripts / "agent_life_transcript.jsonl", "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    return str(transcripts / "agent_life_transcript.jsonl"), str(telemetry / "work")


LIFE_KEYS = {
    "ordinal",
    "current",
    "began_epoch",
    "ended_epoch",
    "lived_seconds",
    "kind",
    "turns",
    "subcalls",
    "errors",
    "edits",
    "note",
    "verdict",
    "moments",
    "achievement",
    "digest",
}


def test_lineage_snapshot_from_the_census_with_verdicts_and_digests(tmp_path, monkeypatch):
    import datetime
    import time as _time

    from stage import census, desk, moments

    census._reset_for_tests()
    desk._reset_for_tests()
    moments._reset_for_tests()
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "sk-stage-0002")
    monkeypatch.setenv("STAGE_ANALYSIS_MODEL", "vendor/analyst")
    now = _time.time()

    def iso(epoch):
        return datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc).isoformat()

    def turn(epoch, calls=()):
        message = {"content": "c"}
        if calls:
            message["tool_calls"] = [{"function": {"name": n, "arguments": "{}"}} for n in calls]
        return {
            "timestamp": iso(epoch),
            "request": {"model": "m", "tools": [{}], "messages": []},
            "response": {"choices": [{"message": message}]},
        }

    entries = [
        turn(now - 900, ("write_file",)),
        turn(now - 800),
        turn(now - 700, ("write_file", "migrate")),
        turn(now - 500),
        turn(now - 100),
    ]
    tombs = [
        (
            "incarnation-0001.txt",
            "Incarnation ended by done() at turn 3. It wrote a tool.",
            now - 600,
        ),
        ("incarnation-0002.txt", "The harness terminated by the harness note.", now - 400),
    ]
    try:
        transcript, work = _lineage_tree(tmp_path, monkeypatch, entries, tombs)
        census.refresh_once(transcript, work, now=now)
        desk._store(2, 4, "A bold life.", {"line": "lived 3m", "depth": "full"})
        moments._store(
            2,
            [
                {"turn": 3, "stars": 5, "line": "Rewrote itself."},
                {"turn": 4, "stars": 2, "line": "Sat."},
            ],
            "first to do a thing",
            2,
            2,
        )
        snap = server.lineage_snapshot()
        assert set(snap) == {
            "now",
            "incarnation",
            "digests_enabled",
            "desk_model",
            "digest_model",
            "lives",
            "lives_omitted",
        }
        assert snap["incarnation"] == 3
        assert snap["digests_enabled"] is True
        assert snap["desk_model"] == "vendor/analyst"
        assert snap["digest_model"] == "vendor/analyst"
        assert snap["lives_omitted"] == 0
        lives = snap["lives"]
        assert [life["ordinal"] for life in lives] == [3, 2, 1]
        assert all(set(life) == LIFE_KEYS for life in lives)
        current = lives[0]
        assert current["current"] is True
        assert current["kind"] is None and current["note"] == ""
        assert current["turns"] == 1 and current["edits"] == 0
        assert (
            current["verdict"] is None
            and current["moments"] == []
            and current["achievement"] is None
        )
        assert current["began_epoch"] == lives[1]["ended_epoch"]
        second = lives[1]
        assert second["kind"] == "harness"
        assert second["turns"] == 1
        assert second["lived_seconds"] == second["ended_epoch"] - second["began_epoch"]
        assert second["verdict"] == {
            "stars": 4,
            "line": "A bold life.",
            "evidence": "lived 3m",
            "depth": "full",
        }
        assert second["moments"] == [
            {"turn": 3, "stars": 5, "line": "Rewrote itself."},
            {"turn": 4, "stars": 2, "line": "Sat."},
        ]
        assert second["achievement"] == "first to do a thing"
        assert second["digest"]["state"] == "ready"
        assert second["digest"]["turns_shown"] == 2 and second["digest"]["turns_total"] == 2
        assert isinstance(second["digest"]["generated_at"], float)
        first = lives[2]
        assert first["kind"] == "declared"
        assert first["turns"] == 3 and first["edits"] == 3
        inexact = server._public_life(dict(census.cached_life(1), exact=False), None, None, True)
        assert inexact["turns"] is None and inexact["edits"] is None
        assert first["note"].startswith("Incarnation ended by done() at turn 3.")
        assert first["verdict"] is None and first["moments"] == []
        assert first["digest"]["state"] == "pending"
        assert first["digest"]["turns_shown"] is None
        moments._SKIPPED.add(1)
        assert server.lineage_snapshot()["lives"][2]["digest"]["state"] == "skipped"
        monkeypatch.delenv("STAGE_SUMMARY_API_KEY")
        off = server.lineage_snapshot()
        assert off["digests_enabled"] is False and off["desk_model"] == ""
        assert off["lives"][1]["digest"]["state"] == "off"
        assert off["lives"][1]["verdict"] is None and off["lives"][1]["moments"] == []
    finally:
        census._reset_for_tests()
        desk._reset_for_tests()
        moments._reset_for_tests()


def test_lineage_snapshot_falls_back_to_the_tombstones_without_a_census(tmp_path, monkeypatch):
    import time as _time

    from stage import census

    census._reset_for_tests()
    now = _time.time()
    tombs = [
        ("incarnation-0001.txt", "Incarnation ended by done() at turn 3.", now - 600),
        ("incarnation-0002.txt", "The harness terminated by the harness note.", now - 400),
    ]
    try:
        _lineage_tree(tmp_path, monkeypatch, [], tombs)
        snap = server.lineage_snapshot()
        lives = snap["lives"]
        assert [life["ordinal"] for life in lives] == [3, 2, 1]
        assert lives[0]["current"] is True
        assert lives[1]["turns"] is None and lives[1]["edits"] is None
        assert lives[1]["kind"] == "harness"
        assert lives[1]["lived_seconds"] == 200.0
        assert lives[1]["note"] == "The harness terminated by the harness note."
        assert lives[2]["kind"] == "declared"
        assert lives[2]["lived_seconds"] is None
        assert lives[1]["digest"]["state"] == "off"
    finally:
        census._reset_for_tests()


def test_public_life_caps_and_rejects_malformed_shapes():
    life = server._public_life(
        {
            "ordinal": "7",
            "current": False,
            "began_epoch": True,
            "ended_epoch": "x",
            "turns": True,
            "summary": "n" * 500,
            "ending_kind": "weird",
        },
        {"stars": 9, "line": "l" * 400, "evidence": "e" * 400, "depth": "d" * 40},
        {
            "moments": [
                {"turn": 1, "stars": 0, "line": "z" * 300},
                {"turn": True, "stars": 3, "line": "no"},
                {"turn": 2, "stars": 3, "line": ""},
            ]
            + [{"turn": i, "stars": 3, "line": "m"} for i in range(10, 30)],
            "achievement": "a" * 300,
            "turns_shown": "3",
            "turns_total": 4.5,
            "generated_at": "soon",
        },
        True,
    )
    assert life["ordinal"] == 7
    assert (
        life["began_epoch"] is None
        and life["ended_epoch"] is None
        and life["lived_seconds"] is None
    )
    assert life["turns"] is None
    assert life["kind"] is None
    assert len(life["note"]) == server.NOTE_CAP
    assert life["verdict"]["stars"] == 5
    assert len(life["verdict"]["line"]) == server.DESK_LINE_CAP
    assert len(life["moments"]) == server.MOMENTS_CAP
    assert life["moments"][0] == {"turn": 1, "stars": 1, "line": "z" * server.MOMENT_LINE_CAP}
    assert len(life["achievement"]) == server.ACHIEVEMENT_CHARS
    assert life["digest"] == {
        "state": "ready",
        "turns_shown": None,
        "turns_total": None,
        "generated_at": None,
    }
    assert server._public_life("junk", None, None, False)["ordinal"] == 0


def test_lineage_route_is_served_on_the_stream_port_without_a_token(stream):
    status, body = _plain_get(stream, "/api/lineage")
    assert status == 200
    payload = json.loads(body)
    assert set(payload) >= {"lives", "incarnation", "lives_omitted"}


def test_telemetry_page_is_served_without_a_token_with_the_stream_headers(stream):
    conn = http.client.HTTPConnection("127.0.0.1", stream, timeout=5)
    conn.request("GET", "/telemetry")
    resp = conn.getresponse()
    body = resp.read().decode("utf-8")
    headers = dict(resp.getheaders())
    conn.close()
    assert resp.status == 200
    assert "aurora — telemetry" in body
    assert headers.get("Content-Type", "").startswith("text/html")
    assert (
        headers.get("Content-Security-Policy") == server.SECURITY_HEADERS["Content-Security-Policy"]
    )
    assert headers.get("Cache-Control") == "no-store"


def test_blog_route_serves_posts_and_widens_only_script_src(tmp_path, monkeypatch):
    diode = tmp_path / "diode"
    (diode / "blog").mkdir(parents=True)
    (diode / "blog" / "20260817_120000_000000.md").write_text(
        "# First\n\n```mermaid\ngraph TD; A-->B\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "DIODE_DIR", str(diode))
    status, headers, body = call_stream_route("/blog")
    assert status == 200
    assert headers.get("Content-Type", "").startswith("text/html")
    assert b'<article id="post-20260817_120000_000000"' in body
    assert b'<pre class="mermaid">graph TD; A--&gt;B</pre>' in body
    csp = headers.get("Content-Security-Policy")
    assert csp == server.BLOG_CSP
    assert f"script-src 'unsafe-inline' {server.blog_page.MERMAID_URL}" in csp
    assert (
        csp.replace(f" {server.blog_page.MERMAID_URL}", "")
        == (server.SECURITY_HEADERS["Content-Security-Policy"])
    )
    assert headers.get_all("Content-Security-Policy") == [csp]


def test_blog_route_paginates_and_rejects_bad_pages(tmp_path, monkeypatch):
    diode = tmp_path / "diode"
    (diode / "blog").mkdir(parents=True)
    for i in range(12):
        (diode / "blog" / f"20260817_1200{i:02d}_000000.md").write_text(f"# P{i}", encoding="utf-8")
    monkeypatch.setattr(server, "DIODE_DIR", str(diode))
    status, _, body = call_stream_route("/blog")
    assert status == 200 and b"P11" in body and b"P1</a>" not in body
    status, _, body = call_stream_route("/blog?page=2")
    assert status == 200 and b"P1</a>" in body and b"P11" not in body
    for bad in ("/blog?page=3", "/blog?page=0", "/blog?page=x", "/blog?page=-1"):
        status, _, _ = call_stream_route(bad)
        assert status == 404, bad


def test_blog_route_empty_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    status, _, body = call_stream_route("/blog")
    assert status == 200 and b"Nothing posted." in body
    status, _, _ = call_stream_route("/blog?page=2")
    assert status == 404


def test_blog_route_survives_an_undecodable_filename(tmp_path, monkeypatch):
    diode = tmp_path / "diode"
    (diode / "blog").mkdir(parents=True)
    (diode / "blog" / (os.fsdecode(b"\xff-post") + ".md")).write_text("# Odd\n", encoding="utf-8")
    (diode / "blog" / "20260817_120000_000000.md").write_text("# Fine\n", encoding="utf-8")
    monkeypatch.setattr(server, "DIODE_DIR", str(diode))
    status, _, body = call_stream_route("/blog")
    assert status == 200
    assert b"Fine" in body and b"Odd" in body


def test_blog_route_returns_500_not_a_dropped_connection_when_rendering_raises(monkeypatch):
    def boom(_diode_dir):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(server.blog, "list_posts", boom)
    status, headers, body = call_stream_route("/blog")
    assert status == 500
    assert b"could not be rendered" in body
    assert headers.get("Content-Security-Policy") == server.BLOG_CSP
