import http.client
import json
import threading

import pytest

from stage import server
from tests.test_stage_diag import _entry, _events, _stamp, _tombstone, _write_jsonl


@pytest.fixture()
def console(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    work = telemetry / "work"
    work.mkdir(parents=True)
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    _write_jsonl(
        str(transcripts / "agent_life_transcript.jsonl"),
        [
            _entry(minute=1, content="early"),
            _entry(minute=11, content="late"),
            _entry(minute=12, stream="scout", tools=False, content="s"),
        ],
    )
    _events(
        str(transcripts / "events.jsonl"),
        [
            {"timestamp": _stamp(11), "event": "bind", "stream": "scout"},
            {"timestamp": _stamp(12), "event": "open", "stream": "scout", "id": "a1"},
            {
                "timestamp": _stamp(12, 30),
                "event": "close",
                "stream": "scout",
                "id": "a1",
                "status": 200,
                "usage": {"total_tokens": 9},
            },
        ],
    )
    _tombstone(str(work), "incarnation-1.txt", "ended deliberately. all quiet.", minute=10)
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
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


@pytest.mark.parametrize(
    "route",
    [
        "/diag",
        "/api/diag/incarnations",
        "/api/diag/incarnation?life=1",
        "/api/diag/streams",
        "/api/diag/stream?name=core",
        "/api/diag/entry?index=0",
    ],
)
def test_diag_routes_require_the_token(console, route):
    status, _ = _get(console, route, token=None)
    assert status == 401


def test_diag_page_served(console):
    status, body = _get(console, "/diag")
    assert status == 200
    assert b"<!doctype html" in body.lower()
    assert b"aurora diagnostics" in body.lower()


def test_diag_incarnations_route(console):
    status, body = _get(console, "/api/diag/incarnations")
    assert status == 200
    lives = json.loads(body)["incarnations"]
    assert [life["ordinal"] for life in lives] == [2, 1]
    assert lives[1]["summary"].startswith("ended deliberately.")


def test_diag_incarnation_route_paginates(console):
    status, body = _get(console, "/api/diag/incarnation?life=2&offset=0&limit=5")
    assert status == 200
    page = json.loads(body)
    assert page["life"] == 2
    assert [t["content"] for t in page["turns"]] == ["late"]


def test_diag_incarnation_route_defaults_to_the_current_life(console):
    status, body = _get(console, "/api/diag/incarnation")
    assert status == 200
    assert json.loads(body)["life"] == 2


def test_diag_streams_route(console):
    status, body = _get(console, "/api/diag/streams")
    assert status == 200
    lanes = json.loads(body)["streams"]
    assert lanes[0]["name"] == "core"
    scout = next(lane for lane in lanes if lane["name"] == "scout")
    assert scout["tokens_total"] == 9
    assert scout["transcript_entries"] == 1


def test_diag_stream_route(console):
    status, body = _get(console, "/api/diag/stream?name=scout")
    assert status == 200
    page = json.loads(body)
    assert page["name"] == "scout"
    assert page["total"] == 1
    assert page["requests"][0]["content_chars"] == 1


def test_diag_stream_route_without_a_name_404s(console):
    status, _ = _get(console, "/api/diag/stream")
    assert status == 404


def test_diag_entry_route(console):
    status, body = _get(console, "/api/diag/entry?index=1")
    assert status == 200
    record = json.loads(body)
    assert record["index"] == 1
    assert "late" in record["raw"]


def test_diag_entry_route_rejects_bad_indices(console):
    for query in ("index=99", "index=-1", "index=abc", ""):
        status, _ = _get(console, f"/api/diag/entry?{query}")
        assert status == 404


def test_diag_routes_reject_post(console):
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request("POST", "/api/diag/incarnations", headers={"X-Console-Token": "sekrit"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 405


def test_diag_isolated_state(tmp_path, monkeypatch):
    """A missing transcript dir still answers with empty shapes, not errors."""
    telemetry = tmp_path / "telemetry"
    (telemetry / "work").mkdir(parents=True)
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path / "absent"))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    monkeypatch.setenv("STAGE_CONSOLE_TOKEN", "sekrit")
    httpd = server.make_server(0, server.ConsoleHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = httpd.server_address[1]
        status, body = _get(port, "/api/diag/incarnations")
        assert status == 200
        assert json.loads(body)["incarnations"][0]["turns"] == 0
        status, body = _get(port, "/api/diag/streams")
        assert status == 200
        assert json.loads(body)["streams"][0]["name"] == "core"
    finally:
        httpd.shutdown()
