import json
import os
import stat
import threading

import httpx
import pytest

import proxy


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    return tmp_path


@pytest.fixture
def fake_upstream(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode("utf-8")

    class _Response:
        status = 200

        def read(self):
            return body

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response())
    return body


@pytest.fixture
def server(tmp_path, transcripts, fake_upstream):
    path = str(tmp_path / "core.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield path
    instance.shutdown()
    instance.server_close()


def _post(path, payload):
    transport = httpx.HTTPTransport(uds=path)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        return client.post("/api/v1/chat/completions", json=payload, timeout=10)


def test_completion_round_trips_over_the_socket(server, transcripts):
    # log_message runs inside send_response on every request, so a regression in
    # the AF_UNIX peer address fails this test rather than passing silently.
    response = _post(server, {"model": "m", "messages": []})

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hi"


def test_the_exchange_is_recorded_in_the_transcript(server, transcripts):
    _post(server, {"model": "m", "messages": [{"role": "user", "content": "q"}]})

    lines = (transcripts / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["request"]["model"] == "m"
    assert entry["response"]["choices"][0]["message"]["content"] == "hi"


def test_unknown_routes_are_refused(server, transcripts):
    transport = httpx.HTTPTransport(uds=server)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        response = client.post("/api/v1/other", json={}, timeout=10)

    assert response.status_code == 404


def test_a_stale_socket_file_is_replaced_and_permissioned(tmp_path, transcripts):
    path = tmp_path / "core.sock"
    path.write_text("left behind by an unclean exit", encoding="utf-8")

    instance = proxy.UnixHTTPServer(str(path), proxy.ProxyHTTPRequestHandler)
    try:
        mode = os.stat(path).st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o660
    finally:
        instance.server_close()


def test_the_default_socket_path_is_under_llm_sock():
    assert proxy.SOCKET_PATH == "/llm/sock/core.sock"
