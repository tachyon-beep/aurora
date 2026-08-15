import http.client
import json
import threading
from urllib.parse import quote

import pytest

from stage import commentary, server


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
    assert snap["lineage"][0]["summary"] == "ended early."
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
    }
    assert set(snap["diode"]) == {
        "outputs",
        "published",
        "published_total",
        "spoken",
        "spoken_total",
    }
    assert isinstance(snap["now"], float)
    assert snap["code"] == {"available": False, "added": 0, "removed": 0}


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
    snap = server.stream_snapshot()
    assert snap["code"] == {"available": True, "added": 1, "removed": 0}
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
