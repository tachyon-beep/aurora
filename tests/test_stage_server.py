import http.client
import json
import threading

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
