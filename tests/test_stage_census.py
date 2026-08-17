import json
import os

from stage import census


def _write(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def _entry(tool_calls=None):
    message = {"content": "x"}
    if tool_calls:
        message["tool_calls"] = [{"function": {"name": n, "arguments": "{}"}} for n in tool_calls]
    return {
        "timestamp": "2026-01-01T00:00:00Z",
        "request": {"model": "m", "tools": [{}], "messages": []},
        "response": {"choices": [{"message": message}]},
    }


def test_census_is_none_before_a_pass_and_a_copy_after(tmp_path):
    census._reset_for_tests()
    try:
        assert census.cached_lives() is None
        assert census.cached_life(1) is None
        transcript = str(tmp_path / "t.jsonl")
        work = str(tmp_path / "work")
        os.makedirs(work)
        _write(transcript, [_entry(["write_file"]), _entry(), _entry(["migrate", "reset"])])
        lives = census.refresh_once(transcript, work, now=1.8e9)
        assert lives[0]["ordinal"] == 1
        assert lives[0]["turns"] == 3
        assert lives[0]["edits"] == 2
        row = census.cached_life(1)
        row["turns"] = 99
        assert census.cached_life(1)["turns"] == 3
        assert census.cached_life(2) is None
    finally:
        census._reset_for_tests()


def test_refresh_replaces_the_previous_census(tmp_path):
    census._reset_for_tests()
    try:
        transcript = str(tmp_path / "t.jsonl")
        work = str(tmp_path / "work")
        os.makedirs(work)
        _write(transcript, [_entry()])
        census.refresh_once(transcript, work, now=1.8e9)
        with open(transcript, "a", encoding="utf-8") as f:
            f.write(json.dumps(_entry(["write_file"])) + "\n")
        census.refresh_once(transcript, work, now=1.8e9)
        assert census.cached_life(1)["turns"] == 2
        assert census.cached_life(1)["edits"] == 1
    finally:
        census._reset_for_tests()


def test_start_background_refresh_runs_once(monkeypatch):
    census._reset_for_tests()
    started = []

    class FakeThread:
        def __init__(self, **kwargs):
            started.append(kwargs["name"])

        def start(self):
            pass

    monkeypatch.setattr(census.threading, "Thread", FakeThread)
    try:
        census.start_background_refresh("/nowhere", "/nowhere")
        census.start_background_refresh("/nowhere", "/nowhere")
        assert started == ["stage-census"]
    finally:
        census._reset_for_tests()
