import json

import pytest

import proxy


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
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


def test_log_event_failure_is_contained(transcripts, monkeypatch, capsys):
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(transcripts / "no" / "events.jsonl"))

    def broken_makedirs(*args, **kwargs):
        raise OSError("read-only")

    monkeypatch.setattr(proxy.os, "makedirs", broken_makedirs)
    proxy.log_event("open", "core", id="x")
    assert "Error writing event" in capsys.readouterr().err


def test_request_ids_are_distinct_hex():
    ids = {proxy.request_id() for _ in range(64)}
    assert len(ids) == 64
    for value in ids:
        int(value, 16)
