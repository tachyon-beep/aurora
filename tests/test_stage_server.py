import http.client
import json
import threading
from urllib.parse import quote

import pytest

from stage import server


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
