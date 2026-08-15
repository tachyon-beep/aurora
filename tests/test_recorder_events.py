import json
import threading

import httpx
import pytest

import proxy
import recorder_streams
from stage import data as stage_data


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(proxy, "_active_bindings", set(), raising=False)
    return tmp_path


def _events(transcripts):
    path = transcripts / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines() if line
    ]


def test_log_event_appends_a_timestamped_line(transcripts):
    proxy.log_event("bind", "core")
    (event,) = _events(transcripts)
    assert event["event"] == "bind"
    assert event["stream"] == "core"
    assert event["timestamp"].endswith("Z")


def test_log_event_carries_extra_fields(transcripts):
    proxy.log_event("close", "aux", id="abc", status=200, duration_seconds=1.5)
    (event,) = _events(transcripts)
    assert event["id"] == "abc"
    assert event["status"] == 200
    assert event["duration_seconds"] == 1.5


def test_event_rotation_checkpoints_active_bindings_for_the_stage_fold(transcripts, monkeypatch):
    monkeypatch.setattr(proxy, "EVENTS_MAX_BYTES", 1_000_000)
    proxy.log_event("bind", "core")
    proxy.log_event("bind", "aux")

    monkeypatch.setattr(proxy, "EVENTS_MAX_BYTES", 1)
    proxy.log_event("close", "core", id="forced", status=200)

    events = _events(transcripts)
    assert [(event["event"], event["stream"]) for event in events] == [
        ("bind", "aux"),
        ("bind", "core"),
    ]
    assert [
        (lane["name"], lane["bound"])
        for lane in stage_data.stream_lanes(str(transcripts / "events.jsonl"))
    ] == [("core", True), ("aux", True)]


def test_log_event_failure_is_contained(transcripts, monkeypatch, capsys):
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(transcripts / "no" / "events.jsonl"))

    def broken_makedirs(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(proxy.os, "makedirs", broken_makedirs)
    proxy.log_event("open", "core", id="x")
    assert "Error writing event" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("event", "initial"),
    [("bind", frozenset()), ("unbind", frozenset({"aux"}))],
)
def test_failed_lifecycle_event_write_does_not_change_active_bindings(
    transcripts, monkeypatch, event, initial
):
    class BrokenWriter:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def write(self, text):
            raise OSError("disk full")

    expected = set(initial)
    monkeypatch.setattr(proxy, "_active_bindings", set(initial))
    monkeypatch.setattr(proxy, "open", lambda *args, **kwargs: BrokenWriter(), raising=False)

    proxy.log_event(event, "aux")

    assert proxy._active_bindings == expected


def test_request_ids_are_distinct_hex():
    ids = {proxy.request_id() for _ in range(64)}
    assert len(ids) == 64
    for value in ids:
        int(value, 16)


@pytest.fixture
def fake_upstream(monkeypatch):
    body = json.dumps(
        {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }
    ).encode("utf-8")

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


def test_a_completion_writes_a_paired_open_and_close(server, transcripts):
    _post(server, {"model": "m", "messages": [{"role": "user", "content": "q"}]})
    opens = [e for e in _events(transcripts) if e["event"] == "open"]
    closes = [e for e in _events(transcripts) if e["event"] == "close"]
    assert len(opens) == 1 and len(closes) == 1
    assert opens[0]["id"] == closes[0]["id"]
    assert opens[0]["stream"] == "core"
    assert opens[0]["model"] == "m"
    assert opens[0]["messages"] == 1
    assert closes[0]["status"] == 200
    assert closes[0]["duration_seconds"] >= 0
    assert closes[0]["usage"] == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


def test_a_budget_refusal_closes_with_429_and_no_usage(tmp_path, transcripts, fake_upstream):
    registry = recorder_streams.StreamRegistry()
    registry.apply({"aux": {"budget": 0}}, {})
    path = str(tmp_path / "aux.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    instance.stream_name = "aux"
    instance.registry = registry
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        response = _post(path, {"model": "m", "messages": []})
        assert response.status_code == 429
        closes = [e for e in _events(transcripts) if e["event"] == "close"]
        assert closes[0]["stream"] == "aux"
        assert closes[0]["status"] == 429
        assert "usage" not in closes[0]
        opens = [e for e in _events(transcripts) if e["event"] == "open"]
        assert opens[0]["id"] == closes[0]["id"]
    finally:
        instance.shutdown()
        instance.server_close()


def test_an_upstream_error_closes_with_its_status(server, transcripts, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("upstream gone")

    monkeypatch.setattr("urllib.request.urlopen", broken)
    _post(server, {"model": "m", "messages": []})
    closes = [e for e in _events(transcripts) if e["event"] == "close"]
    assert closes[0]["status"] == 500
    assert "usage" not in closes[0]


def test_poll_once_emits_bind_and_unbind(tmp_path, transcripts):
    registry = recorder_streams.StreamRegistry()
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(
        json.dumps({"enable_streams": True, "streams": {"aux": {}}}), encoding="utf-8"
    )
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        console.write_text(json.dumps({"enable_streams": True, "streams": {}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    finally:
        for instance in servers.values():
            instance.shutdown()
            instance.server_close()
    kinds = [(e["event"], e["stream"]) for e in _events(transcripts)]
    assert ("bind", "aux") in kinds
    assert ("unbind", "aux") in kinds
