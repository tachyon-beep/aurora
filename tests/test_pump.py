import io
import json
import os
import pathlib
import re
import signal
import time

import pytest

import pump


def _once(**overrides):
    entry = {"name": "one", "mode": "once", "command": ["true"], "at": "2026-08-16T00:00:00+00:00"}
    entry.update(overrides)
    return entry


def _interval(**overrides):
    entry = {"name": "tick", "mode": "interval", "command": ["true"], "every_seconds": 60}
    entry.update(overrides)
    return entry


def _keepalive(**overrides):
    entry = {"name": "hold", "mode": "keepalive", "command": ["sleep", "1"]}
    entry.update(overrides)
    return entry


def _accept(entry):
    parsed, reason = pump.validate_entry(entry)
    assert reason is None, reason
    return parsed


def _reject(entry):
    parsed, reason = pump.validate_entry(entry)
    assert parsed is None
    assert isinstance(reason, str) and reason
    return reason


def test_valid_entries_of_each_mode_are_accepted():
    assert _accept(_once())["mode"] == "once"
    assert _accept(_interval())["every_seconds"] == 60
    assert _accept(_keepalive())["command"] == ["sleep", "1"]


def test_defaults_are_filled_in():
    parsed = _accept(_interval())
    assert parsed["enabled"] is True
    assert parsed["cwd"] == pump.DEFAULT_CWD
    assert parsed["timeout_seconds"] == pump.DEFAULT_TIMEOUT_SECONDS


def test_unknown_field_is_rejected():
    assert "unknown field" in _reject(_interval(shell="/bin/sh"))


def test_unknown_mode_is_rejected():
    _reject(_interval(mode="daemon"))
    _reject({"name": "x", "command": ["true"]})


def test_entry_must_be_an_object():
    _reject(["true"])
    _reject("true")


@pytest.mark.parametrize(
    "name",
    ["", "Upper", "-lead", "_lead", "has space", "path/sep", "../escape", "a" * 33, "dot.name"],
)
def test_invalid_names_are_rejected(name):
    _reject(_interval(name=name))


@pytest.mark.parametrize("name", ["a", "0", "a" * 32, "a-b_c9"])
def test_valid_names_are_accepted(name):
    assert _accept(_interval(name=name))["name"] == name


def test_name_must_be_a_string():
    _reject(_interval(name=7))


def test_command_must_be_a_sized_list_of_strings():
    _reject(_interval(command="true"))
    _reject(_interval(command=[]))
    _reject(_interval(command=["true"] * (pump.COMMAND_MAX_ITEMS + 1)))
    _reject(_interval(command=["true", 7]))
    assert len(_accept(_interval(command=["true"] * pump.COMMAND_MAX_ITEMS))["command"]) == 32


def test_command_elements_are_capped_in_bytes():
    _accept(_interval(command=["a" * pump.COMMAND_ARG_MAX_BYTES]))
    _reject(_interval(command=["a" * (pump.COMMAND_ARG_MAX_BYTES + 1)]))
    _reject(_interval(command=["é" * ((pump.COMMAND_ARG_MAX_BYTES // 2) + 1)]))


def test_every_seconds_bounds_and_type():
    _reject(_interval(every_seconds=pump.INTERVAL_MIN - 1))
    _reject(_interval(every_seconds=pump.INTERVAL_MAX + 1))
    _reject(_interval(every_seconds=True))
    _reject(_interval(every_seconds=60.0))
    assert _accept(_interval(every_seconds=pump.INTERVAL_MIN))["every_seconds"] == pump.INTERVAL_MIN
    assert _accept(_interval(every_seconds=pump.INTERVAL_MAX))["every_seconds"] == pump.INTERVAL_MAX


def test_every_seconds_belongs_only_to_interval():
    _reject(_once(every_seconds=60))
    _reject(_keepalive(every_seconds=60))
    _reject({"name": "tick", "mode": "interval", "command": ["true"]})


def test_at_must_be_an_absolute_timestamp_and_belongs_only_to_once():
    _reject(_once(at="2026-08-16T00:00:00"))
    _reject(_once(at="not a time"))
    _reject(_once(at=1_786_838_400))
    _reject(_interval(at="2026-08-16T00:00:00+00:00"))
    _reject({"name": "one", "mode": "once", "command": ["true"]})
    assert _accept(_once(at="2026-08-16T00:00:00Z"))["at"] == 1_786_838_400.0


def test_timeout_seconds_bounds_and_exclusion_from_keepalive():
    _reject(_interval(timeout_seconds=pump.TIMEOUT_MIN - 1))
    _reject(_interval(timeout_seconds=pump.TIMEOUT_MAX + 1))
    _reject(_interval(timeout_seconds=True))
    _reject(_keepalive(timeout_seconds=30))
    assert _accept(_once(timeout_seconds=30))["timeout_seconds"] == 30
    assert "timeout_seconds" not in _accept(_keepalive())


def test_enabled_must_be_a_boolean():
    _reject(_interval(enabled="true"))
    _reject(_interval(enabled=1))
    assert _accept(_interval(enabled=False))["enabled"] is False


def test_cwd_must_be_a_non_empty_string():
    _reject(_interval(cwd=""))
    _reject(_interval(cwd=7))
    assert _accept(_interval(cwd="/pump/sub"))["cwd"] == "/pump/sub"


def test_duplicate_names_are_rejected_and_siblings_still_run():
    accepted, rejected = pump.evaluate_entries([_interval(), _interval(command=["false"]), _once()])

    assert [entry["name"] for entry in accepted] == ["tick", "one"]
    assert [item["reason"] for item in rejected] == ["duplicate name"]
    assert rejected[0]["index"] == 1


def test_one_invalid_entry_does_not_stop_its_valid_siblings():
    accepted, rejected = pump.evaluate_entries([_interval(mode="daemon"), _once(), "junk"])

    assert [entry["name"] for entry in accepted] == ["one"]
    assert [item["index"] for item in rejected] == [0, 2]
    assert all(item["reason"] for item in rejected)


def test_rejections_are_a_list_so_unnamed_entries_do_not_collide():
    accepted, rejected = pump.evaluate_entries([{}, {}])

    assert accepted == []
    assert len(rejected) == 2


def test_reported_names_are_capped():
    accepted, rejected = pump.evaluate_entries([{"name": "x" * 500, "mode": "once"}])

    assert accepted == []
    assert len(rejected[0]["name"]) <= pump.REPORTED_NAME_CAP


def test_entry_limit_rejects_the_remainder_with_a_reason():
    raw = [_interval(name=f"e{index}") for index in range(5)]

    accepted, rejected = pump.evaluate_entries(raw, limit=3)

    assert len(accepted) == 3
    assert [item["reason"] for item in rejected] == ["entry limit reached"] * 2


def test_entry_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("PUMP_MAX_ENTRIES", raising=False)
    assert pump.max_entries() == 32
    monkeypatch.setenv("PUMP_MAX_ENTRIES", "2")
    assert pump.max_entries() == 2
    accepted, rejected = pump.evaluate_entries([_interval(name=f"e{i}") for i in range(4)])
    assert len(accepted) == 2
    assert len(rejected) == 2
    monkeypatch.setenv("PUMP_MAX_ENTRIES", "not a number")
    assert pump.max_entries() == 32


def test_concurrency_limit_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("PUMP_MAX_CONCURRENT", raising=False)
    assert pump.max_concurrent() == 8
    monkeypatch.setenv("PUMP_MAX_CONCURRENT", "3")
    assert pump.max_concurrent() == 3
    monkeypatch.setenv("PUMP_MAX_CONCURRENT", "")
    assert pump.max_concurrent() == 8


def test_missing_entries_file_is_an_empty_schedule_without_error(tmp_path):
    entries, error = pump.load_entries(str(tmp_path / "entries.json"))
    assert entries == []
    assert error is None


def test_unparseable_entries_file_reports_a_reason(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text("[{", encoding="utf-8")
    entries, error = pump.load_entries(str(path))
    assert entries is None
    assert error


def test_entries_file_must_be_an_array(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text(json.dumps({"one": {}}), encoding="utf-8")
    entries, error = pump.load_entries(str(path))
    assert entries is None
    assert error


def test_oversized_entries_file_reports_a_reason(tmp_path):
    path = tmp_path / "entries.json"
    path.write_text("[" + " " * (pump.ENTRIES_MAX_BYTES + 1) + "]", encoding="utf-8")
    entries, error = pump.load_entries(str(path))
    assert entries is None
    assert error


def test_unreadable_entries_file_reports_a_reason(tmp_path):
    path = tmp_path / "entries.json"
    path.mkdir()
    entries, error = pump.load_entries(str(path))
    assert entries is None
    assert error


def test_once_is_due_at_its_timestamp_and_never_again():
    entry = _accept(_once(at="2026-08-16T00:00:00Z"))
    record = pump.new_record()
    at = entry["at"]

    assert pump.decide_start(entry, record, at - 1) is False
    assert pump.decide_start(entry, record, at) is True
    assert pump.next_due(entry, record, at - 1) == at

    record["spent"] = True
    assert pump.decide_start(entry, record, at + 3600) is False
    assert pump.next_due(entry, record, at + 3600) is None


def test_interval_fires_once_the_period_has_elapsed():
    entry = _accept(_interval(every_seconds=60))
    record = pump.new_record()
    now = 1000.0

    assert pump.decide_start(entry, record, now) is True

    record["last_start"] = now
    assert pump.decide_start(entry, record, now + 59) is False
    assert pump.next_due(entry, record, now) == now + 60
    assert pump.decide_start(entry, record, now + 60) is True


def test_an_interval_run_in_flight_suppresses_the_next_fire():
    entry = _accept(_interval(every_seconds=60))
    record = pump.new_record()
    record["last_start"] = 1000.0
    record["running"] = True

    assert pump.decide_start(entry, record, 1000.0 + 600) is False


def test_a_disabled_entry_never_starts():
    entry = _accept(_interval(enabled=False))
    assert pump.decide_start(entry, pump.new_record(), 1000.0) is False


def test_keepalive_starts_immediately_and_waits_the_backoff_after_an_exit():
    entry = _accept(_keepalive())
    record = pump.new_record()
    now = 1000.0

    assert pump.decide_start(entry, record, now) is True

    record["last_start"] = now
    record["running"] = True
    assert pump.decide_start(entry, record, now + 5) is False
    assert pump.next_due(entry, record, now + 5) is None

    record["running"] = False
    record["last_exit_time"] = now + 5
    record["backoff"] = 4.0
    assert pump.decide_start(entry, record, now + 8) is False
    assert pump.next_due(entry, record, now + 5) == now + 9
    assert pump.decide_start(entry, record, now + 9) is True


def test_keepalive_backoff_doubles_from_one_second_to_the_cap():
    backoff = pump.new_record()["backoff"]
    seen = []
    for _ in range(12):
        backoff = pump.next_backoff(backoff, 0.5)
        seen.append(backoff)

    assert seen[:4] == [1, 2, 4, 8]
    assert seen[-1] == pump.BACKOFF_MAX
    assert max(seen) == pump.BACKOFF_MAX


def test_a_run_past_the_stability_threshold_resets_the_backoff():
    assert pump.next_backoff(pump.BACKOFF_MAX, pump.STABILITY_SECONDS) == pump.BACKOFF_START
    assert pump.next_backoff(pump.BACKOFF_MAX, pump.STABILITY_SECONDS - 1) == pump.BACKOFF_MAX


def test_select_starts_honours_the_concurrency_limit():
    entries = [_accept(_keepalive(name=f"k{index}")) for index in range(5)]
    records = {entry["name"]: pump.new_record() for entry in entries}

    assert pump.select_starts(entries, records, 1000.0, 0, limit=2) == ["k0", "k1"]
    assert pump.select_starts(entries, records, 1000.0, 2, limit=2) == []
    assert pump.select_starts(entries, records, 1000.0, 1, limit=2) == ["k0"]


def test_select_starts_skips_entries_that_are_not_due():
    entries = [_accept(_keepalive(name="k")), _accept(_once(name="o", at="2030-01-01T00:00:00Z"))]
    records = {entry["name"]: pump.new_record() for entry in entries}

    assert pump.select_starts(entries, records, 1000.0, 0, limit=8) == ["k"]


def test_timeout_escalates_from_terminate_to_kill():
    entry = _accept(_interval(timeout_seconds=30))
    record = pump.new_record()
    record["running"] = True
    record["last_start"] = 1000.0

    assert pump.kill_step(entry, record, 1029.0) is None
    assert pump.kill_step(entry, record, 1030.0) == "terminate"

    record["terminated_at"] = 1030.0
    assert pump.kill_step(entry, record, 1034.0) is None
    assert pump.kill_step(entry, record, 1035.0) == "kill"


def test_keepalive_runs_are_never_timed_out():
    entry = _accept(_keepalive())
    record = pump.new_record()
    record["running"] = True
    record["last_start"] = 0.0

    assert pump.kill_step(entry, record, 1_000_000.0) is None


def test_stop_step_terminates_then_kills():
    record = pump.new_record()
    record["running"] = True
    record["last_start"] = 1000.0

    assert pump.stop_step(record, 1000.0) == "terminate"
    record["terminated_at"] = 1000.0
    assert pump.stop_step(record, 1004.0) is None
    assert pump.stop_step(record, 1005.0) == "kill"


def test_signalling_never_reaches_the_pumps_own_process_group(monkeypatch):
    """Entry processes lead their own session, so a stop reaches the group they
    started. A process that shares the pump's group is signalled alone: the
    group signal would otherwise stop the pump itself."""
    sent = []
    monkeypatch.setattr(pump.os, "getpgid", lambda pid: pump.os.getpgrp())
    monkeypatch.setattr(pump.os, "killpg", lambda group, sig: sent.append(("group", group)))
    proc = FakeProcess()
    proc.pid = 4242

    pump.signal_process(proc, signal.SIGTERM)

    assert sent == []
    assert proc.signals == [signal.SIGTERM]


def test_signalling_reaches_the_group_of_an_entry_process(monkeypatch):
    sent = []
    monkeypatch.setattr(pump.os, "getpgid", lambda pid: 99999)
    monkeypatch.setattr(pump.os, "killpg", lambda group, sig: sent.append((group, sig)))
    proc = FakeProcess()
    proc.pid = 4242

    pump.signal_process(proc, signal.SIGKILL)

    assert sent == [(99999, signal.SIGKILL)]
    assert proc.signals == []


def test_append_log_drops_the_head_once_it_passes_the_cap(tmp_path):
    path = str(tmp_path / "e.log")
    pump.append_log(path, b"x" * 100, max_bytes=100)
    assert open(path, "rb").read() == b"x" * 100

    pump.append_log(path, b"TAIL", max_bytes=100)
    first = open(path, "rb").read()
    assert first == b"x" * 100 + b"TAIL"

    pump.append_log(path, b"LAST", max_bytes=100)
    kept = open(path, "rb").read()
    assert len(kept) <= 100
    assert kept.endswith(b"TAILLAST")
    assert b"x" * 100 not in kept


def test_drain_copies_a_stream_into_the_log(tmp_path):
    path = str(tmp_path / "e.log")
    stream = io.BytesIO(b"first\nsecond\n")

    pump._drain(stream, path, pump.LOG_MAX_BYTES)

    assert open(path, "rb").read() == b"first\nsecond\n"
    assert stream.closed


class FakeProcess:
    """A started process the pump can poll and signal, without a real one."""

    def __init__(self):
        self.pid = None
        self.code = None
        self.signals = []

    def poll(self):
        return self.code

    def send_signal(self, sig):
        self.signals.append(sig)


class FakeSpawner:
    def __init__(self):
        self.started = []
        self.procs = {}
        self.fail = set()

    def __call__(self, entry, log_dir):
        self.started.append(entry["name"])
        if entry["name"] in self.fail:
            raise OSError(2, "No such file or directory")
        proc = FakeProcess()
        self.procs[entry["name"]] = proc
        return proc


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _write_entries(tmp_path, entries):
    (tmp_path / "entries.json").write_text(json.dumps(entries), encoding="utf-8")


def _make_pump(tmp_path, entries=None, clock=None, spawner=None):
    if entries is not None:
        _write_entries(tmp_path, entries)
    clock = clock or Clock()
    spawner = spawner or FakeSpawner()
    engine = pump.Pump(str(tmp_path), clock=clock, spawn=spawner)
    return engine, clock, spawner


def _state(tmp_path):
    return json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))


def test_state_carries_absolute_utc_times_and_the_scheduling_record(tmp_path):
    entry = _accept(_interval(every_seconds=60))
    record = pump.new_record()
    record.update(running=True, last_start=1_786_838_400.0, last_exit=3, last_exit_time=None)

    document = pump.render_state([entry], {"tick": record}, [], "ok", 1_786_838_460.0)

    assert document["entries_status"] == "ok"
    assert document["updated"] == "2026-08-16T00:01:00+00:00"
    reported = document["entries"]["tick"]
    assert reported["last_start"] == "2026-08-16T00:00:00+00:00"
    assert reported["running"] is True
    assert reported["last_exit"] == 3
    assert reported["next_due"] is None
    assert reported["backoff_seconds"] == 0
    assert reported["mode"] == "interval"
    assert reported["timeout_seconds"] == pump.DEFAULT_TIMEOUT_SECONDS


def test_state_reports_rejections_and_the_parse_status(tmp_path):
    rejected = [{"index": 0, "name": "x", "reason": "unknown field: shell"}]

    document = pump.render_state([], {}, rejected, "entries are not valid json", 1_786_838_400.0)

    assert document["rejected"] == rejected
    assert document["entries_status"] == "entries are not valid json"
    assert document["max_entries"] == pump.max_entries()
    assert document["max_concurrent"] == pump.max_concurrent()


def test_write_state_replaces_the_file_without_leaving_a_temporary(tmp_path):
    path = str(tmp_path / "state.json")
    pump.write_state(path, {"entries": {}})
    pump.write_state(path, {"entries": {"a": {}}})

    assert json.loads(open(path, encoding="utf-8").read()) == {"entries": {"a": {}}}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]


def test_records_survive_a_restart_through_the_state_file(tmp_path):
    entry = _accept(_once(at="2026-08-16T00:00:00Z"))
    record = pump.new_record()
    record.update(spent=True, running=True, last_start=1_786_838_400.0, last_exit=0)
    record.update(last_exit_time=1_786_838_410.0, backoff=4)
    path = str(tmp_path / "state.json")
    pump.write_state(path, pump.render_state([entry], {"one": record}, [], "ok", 1_786_838_500.0))

    restored = pump.load_records(path)

    assert restored["one"]["spent"] is True
    assert restored["one"]["running"] is False
    assert restored["one"]["last_start"] == 1_786_838_400.0
    assert restored["one"]["last_exit"] == 0
    assert restored["one"]["last_exit_time"] == 1_786_838_410.0
    assert restored["one"]["backoff"] == 4


def test_restoring_records_tolerates_any_shape(tmp_path):
    assert pump.restore_records(None) == {}
    assert pump.restore_records({"entries": []}) == {}
    assert pump.restore_records({"entries": {"a": "b"}}) == {}
    assert pump.restore_records({"entries": {"a": {"last_start": "junk", "backoff": "x"}}}) == {
        "a": pump.new_record()
    }
    assert pump.load_records(str(tmp_path / "absent.json")) == {}
    (tmp_path / "torn.json").write_text("{", encoding="utf-8")
    assert pump.load_records(str(tmp_path / "torn.json")) == {}


def test_a_restart_over_an_unreadable_file_keeps_the_records_it_restored(tmp_path):
    """The pump restarts every five seconds on a fault, and the volume outlives
    the container, so a malformed entries.json is the state a restart most often
    starts in. A restart that published an empty state would drop the spent
    markers it had just restored, and every past-due once entry would run again
    as soon as the file parsed."""
    engine, clock, spawner = _make_pump(tmp_path, [_once(at="2026-08-16T00:00:00Z")])
    clock.now = 1_786_838_400.0
    engine.poll()
    spawner.procs["one"].code = 0
    clock.now += 5
    engine.poll()
    assert _state(tmp_path)["entries"]["one"]["spent"] is True

    (tmp_path / "entries.json").write_text("[{", encoding="utf-8")
    successor = FakeSpawner()
    for offset in (100, 200):
        engine, _clock, _ = _make_pump(tmp_path, clock=Clock(1_786_838_400.0 + offset))
        engine.spawn = successor
        engine.prepare()
        engine.poll()
        document = _state(tmp_path)
        assert document["entries_status"] == "entries are not valid json"
        assert document["entries"]["one"]["spent"] is True

    _write_entries(tmp_path, [_once(at="2026-08-16T00:00:00Z")])
    engine, _clock, _ = _make_pump(tmp_path, clock=Clock(1_786_838_400.0 + 300))
    engine.spawn = successor
    engine.prepare()
    engine.poll()

    assert successor.started == []
    assert _state(tmp_path)["entries"]["one"]["spent"] is True


def test_a_due_once_entry_runs_and_then_reports_itself_spent(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_once(at="2026-08-16T00:00:00Z")])
    clock.now = 1_786_838_400.0

    engine.poll()

    assert spawner.started == ["one"]
    assert _state(tmp_path)["entries"]["one"]["running"] is True

    spawner.procs["one"].code = 0
    clock.now += 5
    engine.poll()
    clock.now += 5
    engine.poll()

    assert spawner.started == ["one"]
    reported = _state(tmp_path)["entries"]["one"]
    assert reported["running"] is False
    assert reported["last_exit"] == 0
    assert reported["spent"] is True
    assert reported["next_due"] is None


def test_a_spent_once_entry_does_not_run_again_after_a_pump_restart(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_once(at="2026-08-16T00:00:00Z")])
    clock.now = 1_786_838_400.0
    engine.poll()
    spawner.procs["one"].code = 0
    clock.now += 5
    engine.poll()

    successor = FakeSpawner()
    engine, clock, _ = _make_pump(tmp_path, clock=Clock(1_786_838_500.0), spawner=successor)
    engine.prepare()
    engine.poll()

    assert successor.started == []


def test_an_interval_entry_reruns_only_after_its_period(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_interval(every_seconds=60)])

    engine.poll()
    spawner.procs["tick"].code = 0
    clock.now += 10
    engine.poll()
    clock.now += 10
    engine.poll()

    assert spawner.started == ["tick"]

    clock.now += 40
    engine.poll()

    assert spawner.started == ["tick", "tick"]


def test_an_interval_run_still_in_flight_does_not_queue_another(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_interval(every_seconds=10)])

    engine.poll()
    for _ in range(6):
        clock.now += 10
        engine.poll()

    assert spawner.started == ["tick"]


def test_a_keepalive_entry_restarts_after_its_backoff(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_keepalive()])

    engine.poll()
    assert spawner.started == ["hold"]

    spawner.procs["hold"].code = 1
    clock.now += 5
    engine.poll()
    assert spawner.started == ["hold"]
    assert _state(tmp_path)["entries"]["hold"]["backoff_seconds"] == pump.BACKOFF_START

    clock.now += 1
    engine.poll()
    assert spawner.started == ["hold"] * 2

    spawner.procs["hold"].code = 1
    clock.now += 1
    engine.poll()
    assert _state(tmp_path)["entries"]["hold"]["backoff_seconds"] == 2

    clock.now += 1
    engine.poll()
    assert spawner.started == ["hold"] * 2

    clock.now += 1
    engine.poll()
    assert spawner.started == ["hold"] * 3


def test_a_run_past_its_timeout_is_terminated_then_killed(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_interval(timeout_seconds=30)])

    engine.poll()
    proc = spawner.procs["tick"]
    clock.now += 29
    engine.poll()
    assert proc.signals == []

    clock.now += 1
    engine.poll()
    assert proc.signals == [signal.SIGTERM]

    clock.now += 4
    engine.poll()
    assert proc.signals == [signal.SIGTERM]

    clock.now += 1
    engine.poll()
    assert proc.signals == [signal.SIGTERM, signal.SIGKILL]


def test_an_entry_removed_from_the_file_has_its_run_stopped(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_keepalive()])
    engine.poll()
    proc = spawner.procs["hold"]

    _write_entries(tmp_path, [])
    clock.now += 5
    engine.poll()

    assert proc.signals == [signal.SIGTERM]
    assert "hold" not in _state(tmp_path)["entries"]


def test_a_disabled_entry_has_its_run_stopped(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_keepalive()])
    engine.poll()
    proc = spawner.procs["hold"]

    _write_entries(tmp_path, [_keepalive(enabled=False)])
    clock.now += 5
    engine.poll()

    assert proc.signals == [signal.SIGTERM]
    assert _state(tmp_path)["entries"]["hold"]["enabled"] is False


def test_a_malformed_entries_file_retains_the_schedule_and_reports_the_failure(tmp_path):
    engine, clock, spawner = _make_pump(tmp_path, [_keepalive(), _interval(every_seconds=10)])
    engine.poll()
    spawner.procs["tick"].code = 0
    clock.now += 5
    engine.poll()

    (tmp_path / "entries.json").write_text("[{", encoding="utf-8")
    clock.now += 10
    engine.poll()

    document = _state(tmp_path)
    assert document["entries_status"] == "entries are not valid json"
    assert set(document["entries"]) == {"hold", "tick"}
    assert spawner.procs["hold"].signals == []
    assert spawner.started == ["hold", "tick", "tick"]


def test_a_command_that_cannot_be_started_is_reported_and_backs_off(tmp_path):
    spawner = FakeSpawner()
    spawner.fail.add("hold")
    engine, clock, _ = _make_pump(tmp_path, [_keepalive()], spawner=spawner)

    engine.poll()

    reported = _state(tmp_path)["entries"]["hold"]
    assert reported["running"] is False
    assert reported["last_error"]
    assert reported["backoff_seconds"] == pump.BACKOFF_START

    clock.now += 1
    engine.poll()
    assert spawner.started == ["hold", "hold"]
    assert _state(tmp_path)["entries"]["hold"]["backoff_seconds"] == 2


def test_a_fault_on_one_entry_leaves_the_others_running(tmp_path):
    class Faulty(FakeSpawner):
        def __call__(self, entry, log_dir):
            if entry["name"] == "hold":
                raise RuntimeError("fault")
            return super().__call__(entry, log_dir)

    spawner = Faulty()
    engine, _clock, _ = _make_pump(
        tmp_path, [_keepalive(), _interval(every_seconds=10)], spawner=spawner
    )

    engine.poll()

    assert spawner.started == ["tick"]
    assert _state(tmp_path)["entries"]["tick"]["running"] is True


def test_the_pump_starts_no_more_than_the_concurrency_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("PUMP_MAX_CONCURRENT", "2")
    entries = [_keepalive(name=f"k{index}") for index in range(4)]
    engine, clock, spawner = _make_pump(tmp_path, entries)

    engine.poll()
    clock.now += 5
    engine.poll()

    assert spawner.started == ["k0", "k1"]


def test_the_entry_limit_is_reported_in_the_state(tmp_path, monkeypatch):
    monkeypatch.setenv("PUMP_MAX_ENTRIES", "1")
    engine, _clock, spawner = _make_pump(tmp_path, [_keepalive(), _interval(every_seconds=10)])

    engine.poll()

    document = _state(tmp_path)
    assert spawner.started == ["hold"]
    assert [item["reason"] for item in document["rejected"]] == ["entry limit reached"]


def test_prepare_writes_the_surfaces_the_agent_reads(tmp_path):
    engine, _clock, _ = _make_pump(tmp_path)

    engine.prepare()

    assert (tmp_path / "README.md").read_text(encoding="utf-8") == pump.README_TEXT
    assert (tmp_path / "log").is_dir()


def test_readme_is_written_when_absent_and_when_it_differs(tmp_path):
    path = str(tmp_path / "README.md")

    assert pump.write_readme(path) is True
    assert pump.write_readme(path) is False

    (tmp_path / "README.md").write_text("stale\n", encoding="utf-8")
    assert pump.write_readme(path) is True
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == pump.README_TEXT


def test_readme_states_the_vocabulary_the_pump_enforces():
    text = pump.README_TEXT
    for field in pump.ENTRY_FIELDS:
        assert field in text
    for mode in pump.MODES:
        assert mode in text
    for token in ("entries.json", "state.json", "log/", str(pump.INTERVAL_MAX)):
        assert token in text


def test_readme_prose_is_affectless():
    """The README is a surface the agent reads: bland and factual, with no
    voice, no address, and no suggested application."""
    text = pump.README_TEXT

    for ch in ("🔄", "🏁", "🧠", "🛠️", "📥", "📡", "🚀", "🤖", "❌", "⚠️", "✅"):
        assert ch not in text
    assert text.isascii()
    assert "!" not in text
    assert not re.search(r"\b(you|your|yours|we|our|ours|us|let's|please)\b", text, re.I)


def test_start_process_runs_the_command_in_its_cwd_without_stdin(tmp_path):
    log_dir = tmp_path / "log"
    entry = _accept(
        _once(
            command=["sh", "-c", "pwd > ran.txt; cat >> ran.txt"],
            cwd=str(tmp_path),
        )
    )

    proc = pump.start_process(entry, str(log_dir))

    assert proc.wait(timeout=30) == 0
    assert (tmp_path / "ran.txt").read_text(encoding="utf-8") == str(tmp_path) + "\n"


def test_the_image_ships_the_pump_outside_the_agent_workspace():
    """The entrypoint copies /opt/agent into /work, so a pump placed there would
    become a file in the agent's own workspace and in the telemetry mirror. It
    ships beside the entrypoint instead."""
    root = pathlib.Path(__file__).resolve().parents[1]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    copies = [line for line in dockerfile.splitlines() if "pump.py" in line]

    assert copies, "Dockerfile does not copy pump.py"
    assert any(re.search(r"\s/usr/local/bin/", line) for line in copies)
    for line in copies:
        assert "/opt/agent" not in line


def test_start_process_leads_its_own_session(tmp_path):
    # The stop path signals the process GROUP, and README_TEXT tells the agent
    # "each process leads its own session, so a signal reaches the processes it
    # started as well". Without start_new_session the child shares the pump's
    # group, signal_process takes its fallback branch, and a SIGTERM reaches
    # only the `sh -c` shell while its children are orphaned.
    entry = _accept(_once(command=["sh", "-c", "sleep 30"], cwd=str(tmp_path)))

    proc = pump.start_process(entry, str(tmp_path / "log"))
    try:
        group = os.getpgid(proc.pid)
        assert group == proc.pid
        assert group != os.getpgid(0)
    finally:
        # Never killpg a group this process is in: a regression here would take
        # the test runner down instead of reporting a failure.
        if os.getpgid(proc.pid) == os.getpgid(0):
            proc.kill()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=30)


def test_start_process_closes_stdin(monkeypatch, tmp_path):
    # Asserted on the spawn arguments rather than on the child's behaviour:
    # pytest's default fd capture already points fd 0 at /dev/null, so a child
    # that reads stdin sees EOF either way and cannot detect the difference.
    seen = {}

    class _Recorded:
        pid = 4321

        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            self.stdout = io.BytesIO(b"")

    monkeypatch.setattr(pump.subprocess, "Popen", _Recorded)
    entry = _accept(_once(command=["true"], cwd=str(tmp_path)))

    pump.start_process(entry, str(tmp_path / "log"))

    assert seen["stdin"] is pump.subprocess.DEVNULL
    assert seen["start_new_session"] is True


def _spawn_session_leader(*argv):
    return pump.subprocess.Popen(
        list(argv),
        stdin=pump.subprocess.DEVNULL,
        stdout=pump.subprocess.DEVNULL,
        stderr=pump.subprocess.STDOUT,
        start_new_session=True,
    )


def _reap(proc):
    if proc.poll() is None:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    proc.wait(timeout=30)


def test_process_identity_names_a_live_process_and_not_a_dead_one():
    proc = _spawn_session_leader("sleep", "30")
    try:
        identity = pump.process_identity(proc.pid)
        assert identity is not None
        state, ticks = identity
        assert isinstance(state, str) and state
        assert isinstance(ticks, int) and ticks > 0
    finally:
        _reap(proc)
    assert pump.process_identity(proc.pid) is None


def test_start_records_the_pid_and_start_ticks_of_the_run(tmp_path, monkeypatch):
    engine, clock, spawner = _make_pump(tmp_path, [_keepalive()])
    monkeypatch.setattr(pump, "process_identity", lambda pid: ("S", 777) if pid == 4321 else None)

    class _Pinned(FakeSpawner):
        def __call__(self, entry, log_dir):
            proc = super().__call__(entry, log_dir)
            proc.pid = 4321
            return proc

    engine.spawn = _Pinned()
    engine.poll()

    reported = _state(tmp_path)["entries"]["hold"]
    assert reported["running"] is True
    assert reported["pid"] == 4321
    assert reported["process_start_ticks"] == 777

    engine.spawn.procs["hold"].code = 0
    clock.now += 1
    engine.poll()

    reported = _state(tmp_path)["entries"]["hold"]
    assert reported["running"] is False
    assert "pid" not in reported
    assert "process_start_ticks" not in reported


def test_restore_processes_reads_only_running_entries_with_a_full_identity():
    document = {
        "entries": {
            "a": {"running": True, "pid": 10, "process_start_ticks": 5},
            "b": {"running": False, "pid": 11, "process_start_ticks": 6},
            "c": {"running": True, "pid": "12", "process_start_ticks": 7},
            "d": {"running": True, "pid": 13},
            "e": {"running": True, "pid": True, "process_start_ticks": 8},
            "f": "junk",
        }
    }

    assert pump.restore_processes(document) == {"a": (10, 5)}
    assert pump.restore_processes(None) == {}
    assert pump.restore_processes({"entries": []}) == {}


def _write_running_state(tmp_path, name, pid, ticks):
    document = {"entries": {name: {"running": True, "pid": pid, "process_start_ticks": ticks}}}
    (tmp_path / "state.json").write_text(json.dumps(document), encoding="utf-8")


def test_a_restarted_pump_stops_the_live_processes_of_its_predecessor(tmp_path):
    # A pump that dies alone (a kill, an OOM selection) leaves its session
    # leaders alive under PID 1; the next pump reads them out of state.json
    # and stops them before restoring the records, so a keepalive entry gets
    # one fresh copy rather than a duplicate beside an orphan.
    proc = _spawn_session_leader("sleep", "30")
    try:
        _, ticks = pump.process_identity(proc.pid)
        _write_running_state(tmp_path, "hold", proc.pid, ticks)
        engine, _, _ = _make_pump(tmp_path, [_keepalive(command=["sleep", "30"])])

        engine.prepare()

        assert proc.wait(timeout=30) == -signal.SIGTERM
        assert engine.records["hold"]["running"] is False
    finally:
        _reap(proc)


def test_a_recorded_process_with_another_start_time_is_left_alone(tmp_path):
    # After a container respawn the recorded pid can belong to an unrelated
    # process. The start time is the identity; a mismatch is not signalled.
    proc = _spawn_session_leader("sleep", "30")
    try:
        _, ticks = pump.process_identity(proc.pid)
        _write_running_state(tmp_path, "hold", proc.pid, ticks + 1)
        engine, _, _ = _make_pump(tmp_path, [_keepalive(command=["sleep", "30"])])

        engine.prepare()

        assert proc.poll() is None
    finally:
        _reap(proc)


def test_a_predecessor_process_that_ignores_sigterm_is_killed_after_the_grace(tmp_path):
    ready = tmp_path / "ready"
    script = f'trap "" TERM; : > {ready}; while :; do sleep 1; done'
    proc = _spawn_session_leader("sh", "-c", script)
    try:
        for _ in range(300):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists()
        _, ticks = pump.process_identity(proc.pid)
        _write_running_state(tmp_path, "hold", proc.pid, ticks)
        slept = []
        engine, _, _ = _make_pump(tmp_path, [_keepalive(command=["sleep", "30"])])
        engine.sleep = slept.append

        engine.prepare()

        assert proc.wait(timeout=30) == -signal.SIGKILL
        assert sum(slept) >= pump.KILL_GRACE_SECONDS
    finally:
        _reap(proc)


def test_stop_orphans_returns_the_names_it_stopped_and_skips_the_rest(tmp_path):
    proc = _spawn_session_leader("sleep", "30")
    try:
        _, ticks = pump.process_identity(proc.pid)
        orphans = {"live": (proc.pid, ticks), "gone": (proc.pid, ticks + 1)}

        stopped = pump.stop_orphans(orphans)

        assert stopped == ["live"]
        assert proc.wait(timeout=30) == -signal.SIGTERM
    finally:
        _reap(proc)


def test_readme_states_that_a_prior_pumps_processes_are_stopped():
    assert "pid" in pump.README_TEXT
    assert "still running from an earlier start of the scheduler" in pump.README_TEXT
