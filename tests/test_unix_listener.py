import json
import os
import stat
import threading

import httpx
import pytest

import proxy
import recorder_streams


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
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


@pytest.fixture
def registry():
    return recorder_streams.StreamRegistry()


@pytest.fixture
def stream_server(tmp_path, transcripts, fake_upstream, registry, monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    registry.apply({"aux": {"model": "declared", "budget": 1}}, {})
    path = str(tmp_path / "aux.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    instance.stream_name = "aux"
    instance.registry = registry
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield path
    instance.shutdown()
    instance.server_close()


def _entries(transcripts):
    lines = (transcripts / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_core_entries_are_tagged_core(server, transcripts):
    _post(server, {"model": "m", "messages": []})
    (entry,) = _entries(transcripts)
    assert entry["stream"] == "core"


def test_a_declared_stream_composes_and_tags(stream_server, transcripts):
    response = _post(stream_server, {"model": "sent", "messages": []})
    assert response.status_code == 200
    (entry,) = _entries(transcripts)
    assert entry["stream"] == "aux"
    assert entry["request"]["model"] == "declared"


def test_an_exhausted_stream_refuses_and_records(stream_server, transcripts):
    _post(stream_server, {"model": "m", "messages": []})
    response = _post(stream_server, {"model": "m", "messages": []})
    assert response.status_code == 429
    message = response.json()["error"]["message"]
    assert message.startswith("rate limited: at most 1 request(s) per hour on this socket")
    assert "next available in" in message
    entries = _entries(transcripts)
    assert len(entries) == 2
    assert entries[1]["response"]["error"]["message"] == message


def test_a_non_object_body_on_a_stream_is_refused(stream_server, transcripts):
    transport = httpx.HTTPTransport(uds=stream_server)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/chat/completions",
            content=b"[1, 2]",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "request body is not a json object"


def test_core_forwards_the_body_verbatim(server, transcripts, monkeypatch):
    seen = {}
    real_request = proxy.urllib.request.Request

    def capture(url, data=None, headers=None, method=None):
        seen["data"] = data
        return real_request(url, data=data, headers=headers, method=method)

    monkeypatch.setattr(proxy.urllib.request, "Request", capture)
    payload = {"model": "m", "messages": [], "temperature": 0.5}
    _post(server, payload)
    assert json.loads(seen["data"].decode("utf-8")) == payload


def test_poll_once_binds_and_unbinds_declared_sockets(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        assert (tmp_path / "aux.sock").exists()
        assert json.loads(state.read_text(encoding="utf-8"))["streams"]["aux"]["status"] == "active"
        console.write_text(json.dumps({"streams": {}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert not (tmp_path / "aux.sock").exists()
        assert "aux" not in json.loads(state.read_text(encoding="utf-8"))["streams"]
    finally:
        for server_instance in servers.values():
            server_instance.shutdown()
            server_instance.server_close()


def test_poll_once_keeps_streams_on_a_torn_console(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        console.write_text('{"streams": {"aux"', encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert (tmp_path / "aux.sock").exists()
        document = json.loads(state.read_text(encoding="utf-8"))
        assert document["streams"]["aux"]["status"] == "active"
        assert document["console_error"] == "console is not valid json"
    finally:
        for server_instance in servers.values():
            server_instance.shutdown()
            server_instance.server_close()


def test_poll_once_reports_rejections(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"Bad Name": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    document = json.loads(state.read_text(encoding="utf-8"))
    assert document["streams"]["Bad Name"] == {
        "status": "rejected",
        "reason": "invalid stream name",
    }
    assert servers == {}


def test_a_settings_edit_applies_without_a_rebind(tmp_path, transcripts, registry, fake_upstream):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {"model": "one"}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        first = servers["aux"]
        console.write_text(json.dumps({"streams": {"aux": {"model": "two"}}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert servers["aux"] is first
        _post(str(tmp_path / "aux.sock"), {"model": "sent", "messages": []})
        entries = _entries(tmp_path)
        assert entries[-1]["request"]["model"] == "two"
    finally:
        for server_instance in servers.values():
            server_instance.shutdown()
            server_instance.server_close()


def test_sweep_removes_only_unserved_sockets(tmp_path):
    (tmp_path / "stale.sock").write_text("", encoding="utf-8")
    (tmp_path / "core.sock").write_text("", encoding="utf-8")
    (tmp_path / "streams.json").write_text("{}", encoding="utf-8")
    proxy.sweep_stale_sockets(str(tmp_path), keep={"core.sock"})
    assert not (tmp_path / "stale.sock").exists()
    assert (tmp_path / "core.sock").exists()
    assert (tmp_path / "streams.json").exists()
