import datetime
import json
import os

import pytest

from stage import census, store


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


# persisted figures


T0 = 1.8e9


@pytest.fixture
def _state_dir(tmp_path, monkeypatch):
    state = tmp_path / "stage-state"
    state.mkdir()
    monkeypatch.setenv("STAGE_STATE_DIR", str(state))
    return state


def _iso(epoch):
    stamp = datetime.datetime.fromtimestamp(epoch, tz=datetime.timezone.utc)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_at(epoch, tool_calls=None, timestamp=None):
    entry = _entry(tool_calls)
    entry["timestamp"] = timestamp if timestamp is not None else _iso(epoch)
    return entry


def _tree(tmp_path, entries, deaths):
    work = tmp_path / "work"
    tombs = work / "tombstones"
    tombs.mkdir(parents=True)
    for ordinal, epoch in enumerate(deaths, start=1):
        path = tombs / f"incarnation-{ordinal:04d}.txt"
        path.write_text(f"Incarnation ended by done() at turn {ordinal}.", encoding="utf-8")
        os.utime(path, (epoch, epoch))
    transcript = tmp_path / "t.jsonl"
    _write(str(transcript), entries)
    return str(transcript), str(work)


def _full_life(tmp_path):
    entries = [
        _entry_at(T0 + 10, ["write_file"]),
        _entry_at(T0 + 20),
        _entry_at(T0 + 200),
    ]
    return _tree(tmp_path, entries, deaths=[T0 + 100])


def test_a_dead_lifes_figures_are_persisted_by_label(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        transcript, work = _full_life(tmp_path)
        census.refresh_once(transcript, work, now=T0 + 300)
        doc = store.load("census")
        assert doc["figures"] == {
            "incarnation-0001.txt": {"turns": 2, "subcalls": 0, "errors": 0, "edits": 1}
        }
    finally:
        census._reset_for_tests()


def test_rotation_does_not_zero_a_dead_lifes_figures(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        transcript, work = _full_life(tmp_path)
        census.refresh_once(transcript, work, now=T0 + 300)
        _write(transcript, [_entry_at(T0 + 200)])
        lives = census.refresh_once(transcript, work, now=T0 + 400)
        dead = [life for life in lives if life["ordinal"] == 1][0]
        assert dead["turns"] == 2
        assert dead["edits"] == 1
    finally:
        census._reset_for_tests()


def test_figures_survive_a_stage_restart(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        transcript, work = _full_life(tmp_path)
        census.refresh_once(transcript, work, now=T0 + 300)
        census._reset_for_tests()
        _write(transcript, [_entry_at(T0 + 200)])
        census.refresh_once(transcript, work, now=T0 + 400)
        assert census.cached_life(1)["turns"] == 2
    finally:
        census._reset_for_tests()


def test_an_inexact_census_is_not_persisted(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        entries = [
            _entry_at(T0 + 10, ["write_file"]),
            _entry_at(T0 + 20, timestamp="not a timestamp"),
            _entry_at(T0 + 200),
        ]
        transcript, work = _tree(tmp_path, entries, deaths=[T0 + 100])
        census.refresh_once(transcript, work, now=T0 + 300)
        assert store.load("census") is None
    finally:
        census._reset_for_tests()


def test_labels_missing_from_the_mirror_are_pruned_on_save(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        store.save(
            "census",
            {
                "figures": {
                    "incarnation-9999.txt": {"turns": 7, "subcalls": 0, "errors": 0, "edits": 0}
                }
            },
        )
        transcript, work = _full_life(tmp_path)
        census.refresh_once(transcript, work, now=T0 + 300)
        assert "incarnation-9999.txt" not in store.load("census")["figures"]
    finally:
        census._reset_for_tests()


def test_malformed_stored_figures_are_ignored(tmp_path, _state_dir):
    census._reset_for_tests()
    try:
        transcript, work = _full_life(tmp_path)
        store.save(
            "census", {"figures": {"incarnation-0001.txt": {"turns": "many", "edits": True}}}
        )
        _write(transcript, [_entry_at(T0 + 200)])
        lives = census.refresh_once(transcript, work, now=T0 + 400)
        dead = [life for life in lives if life["ordinal"] == 1][0]
        assert dead["turns"] == 0
    finally:
        census._reset_for_tests()
