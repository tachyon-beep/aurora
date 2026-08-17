import json

import pytest
from PIL import Image

import sense


def flat(value, n=sense.THUMB_SIZE * sense.THUMB_SIZE):
    return [value] * n


# slot selection


def test_slot_index_starts_at_zero():
    assert sense.slot_index(0, 10, 288) == 0


def test_slot_index_interval_boundaries():
    assert sense.slot_index(599, 10, 288) == 0
    assert sense.slot_index(600, 10, 288) == 1
    assert sense.slot_index(1199, 10, 288) == 1
    assert sense.slot_index(1200, 10, 288) == 2


def test_slot_index_wraps_after_a_full_ring():
    period = 10 * 60 * 288
    assert sense.slot_index(period, 10, 288) == 0
    assert sense.slot_index(period + 600, 10, 288) == 1


def test_slot_index_is_restart_deterministic():
    # The slot depends only on the clock, never on prior history.
    now = 1_755_000_000
    first = sense.slot_index(now, 10, 288)
    for _ in range(5):
        assert sense.slot_index(now, 10, 288) == first
    assert sense.slot_index(now + 10 * 60, 10, 288) == (first + 1) % 288


def test_slot_name_is_zero_padded():
    assert sense.slot_name(0) == "000.jpg"
    assert sense.slot_name(5) == "005.jpg"
    assert sense.slot_name(287) == "287.jpg"


# thumbnails


def test_thumbnail_and_diff_from_images(tmp_path):
    black = tmp_path / "black.jpg"
    white = tmp_path / "white.jpg"
    Image.new("RGB", (64, 48), (0, 0, 0)).save(black)
    Image.new("RGB", (64, 48), (255, 255, 255)).save(white)
    a = sense.thumbnail(black)
    b = sense.thumbnail(white)
    assert len(a) == sense.THUMB_SIZE * sense.THUMB_SIZE
    assert sense.mean_abs_diff(a, a) == 0.0
    assert sense.mean_abs_diff(a, b) > 200.0


# freeze detection


def test_first_grab_is_not_static():
    state = sense.FeedState()
    sense.record_success(state, flat(10))
    assert state.active and state.static_run == 0


def test_static_run_marks_inactive_after_limit():
    state = sense.FeedState()
    sense.record_success(state, flat(10))
    for _ in range(sense.STATIC_LIMIT - 1):
        sense.record_success(state, flat(10))
        assert state.active
    sense.record_success(state, flat(10))
    assert not state.active


def test_changing_frames_reset_the_static_run():
    state = sense.FeedState()
    sense.record_success(state, flat(10))
    for _ in range(sense.STATIC_LIMIT - 1):
        sense.record_success(state, flat(10))
    sense.record_success(state, flat(100))
    assert state.active and state.static_run == 0


def test_diff_at_threshold_counts_as_changing():
    # diff < threshold is static; a diff of exactly the threshold is not.
    state = sense.FeedState(last_thumb=flat(10), static_run=sense.STATIC_LIMIT - 1)
    sense.record_success(state, flat(12), threshold=2.0)
    assert state.active and state.static_run == 0


def test_diff_just_under_threshold_counts_as_static():
    state = sense.FeedState(last_thumb=flat(10), static_run=sense.STATIC_LIMIT - 1)
    sense.record_success(state, flat(11), threshold=2.0)
    assert not state.active


# failure counting


def test_failures_mark_inactive_after_limit():
    state = sense.FeedState()
    for _ in range(sense.FAILURE_LIMIT - 1):
        sense.record_failure(state)
        assert state.active
    sense.record_failure(state)
    assert not state.active


def test_a_success_resets_the_failure_run():
    state = sense.FeedState()
    for _ in range(sense.FAILURE_LIMIT - 1):
        sense.record_failure(state)
    sense.record_success(state, flat(10))
    assert state.active and state.failure_run == 0


# the global guard


def test_all_feeds_failing_counts_nothing():
    states = [sense.FeedState(), sense.FeedState()]
    for _ in range(sense.FAILURE_LIMIT * 3):
        assert sense.apply_outcomes([(states[0], None), (states[1], None)]) is False
    assert all(s.active and s.failure_run == 0 for s in states)


def test_a_partial_failure_is_counted():
    failing, working = sense.FeedState(), sense.FeedState()
    assert sense.apply_outcomes([(failing, None), (working, flat(10))]) is True
    assert failing.failure_run == 1
    assert working.failure_run == 0


def test_a_lone_attempted_failure_is_counted():
    # One attempted feed failing alone points at the feed, not the capture
    # side; the guard needs at least two attempted feeds to void a cycle.
    state = sense.FeedState()
    assert sense.apply_outcomes([(state, None)]) is True
    assert state.failure_run == 1


def test_a_lone_active_feed_can_still_go_inactive():
    state = sense.FeedState()
    for _ in range(sense.FAILURE_LIMIT):
        sense.apply_outcomes([(state, None)])
    assert not state.active


def test_guard_does_not_reset_an_existing_run():
    state = sense.FeedState(failure_run=3)
    sense.apply_outcomes([(state, None), (sense.FeedState(), None)])
    assert state.failure_run == 3
    sense.apply_outcomes([(state, None), (sense.FeedState(), flat(10))])
    assert state.failure_run == 4


# daily probe gating and reactivation


def test_active_feeds_are_always_attempted():
    assert sense.should_attempt(sense.FeedState(), now=0)


def test_inactive_feeds_are_probed_once_daily():
    state = sense.FeedState(active=False, last_probe=1000.0)
    assert not sense.should_attempt(state, now=1000.0 + sense.PROBE_INTERVAL_SECONDS - 1)
    assert sense.should_attempt(state, now=1000.0 + sense.PROBE_INTERVAL_SECONDS)


def test_a_differing_probe_frame_reactivates():
    state = sense.FeedState(active=False, last_thumb=flat(10), static_run=sense.STATIC_LIMIT)
    sense.record_success(state, flat(100))
    assert state.active and state.static_run == 0


def test_a_static_probe_frame_leaves_the_feed_inactive():
    state = sense.FeedState(active=False, last_thumb=flat(10), static_run=sense.STATIC_LIMIT)
    sense.record_success(state, flat(10))
    assert not state.active


def test_run_cycle_updates_last_probe_only_when_due(tmp_path, monkeypatch):
    monkeypatch.setattr(sense, "grab_feed", lambda *a, **k: flat(10))
    feeds = [{"dir": "0", "id": "x"}, {"dir": "1", "id": "y"}]
    states = {
        "0": sense.FeedState(active=False, last_probe=500.0),
        "1": sense.FeedState(active=False, last_probe=500.0),
    }
    due = 500.0 + sense.PROBE_INTERVAL_SECONDS
    sense.run_cycle(
        feeds[:1], states, tmp_path, now=due - 1, interval_minutes=10, slots=288, threshold=2.0
    )
    assert states["0"].last_probe == 500.0
    sense.run_cycle(feeds, states, tmp_path, now=due, interval_minutes=10, slots=288, threshold=2.0)
    assert states["0"].last_probe == due
    assert states["1"].last_probe == due


def test_a_voided_cycle_does_not_consume_the_probe(tmp_path, monkeypatch):
    # Every attempted feed failing is capture-side evidence; the daily probe
    # is not spent, so the feeds retry on the next cycle.
    monkeypatch.setattr(sense, "grab_feed", lambda *a, **k: None)
    feeds = [{"dir": "0", "id": "x"}, {"dir": "1", "id": "y"}]
    states = {
        "0": sense.FeedState(active=False, last_probe=500.0),
        "1": sense.FeedState(active=False, last_probe=500.0),
    }
    due = 500.0 + sense.PROBE_INTERVAL_SECONDS
    sense.run_cycle(feeds, states, tmp_path, now=due, interval_minutes=10, slots=288, threshold=2.0)
    assert states["0"].last_probe == 500.0
    assert states["1"].last_probe == 500.0
    assert sense.should_attempt(states["0"], now=due + 600)


def test_a_lone_failed_probe_is_counted_and_consumed(tmp_path, monkeypatch):
    monkeypatch.setattr(sense, "grab_feed", lambda *a, **k: None)
    feeds = [{"dir": "0", "id": "x"}]
    states = {"0": sense.FeedState(active=False, last_probe=500.0)}
    due = 500.0 + sense.PROBE_INTERVAL_SECONDS
    sense.run_cycle(feeds, states, tmp_path, now=due, interval_minutes=10, slots=288, threshold=2.0)
    assert states["0"].last_probe == due
    assert states["0"].failure_run == 1


# status.json


def test_status_is_written_atomically_with_exact_content(tmp_path):
    states = {"0": sense.FeedState(), "1": sense.FeedState(active=False)}
    sense.write_status(tmp_path, states)
    status = tmp_path / "status.json"
    assert status.read_text(encoding="utf-8") == '{"0":"active","1":"inactive"}'
    assert not (tmp_path / "status.json.tmp").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]
    assert json.loads(status.read_text(encoding="utf-8")) == {"0": "active", "1": "inactive"}


def test_status_replaces_a_previous_file(tmp_path):
    sense.write_status(tmp_path, {"0": sense.FeedState()})
    sense.write_status(tmp_path, {"0": sense.FeedState(active=False)})
    assert (tmp_path / "status.json").read_text(encoding="utf-8") == '{"0":"inactive"}'


def test_run_cycle_writes_status_without_network(tmp_path, monkeypatch):
    monkeypatch.setattr(sense, "grab_feed", lambda *a, **k: flat(10))
    feeds = [{"dir": "0", "id": "x"}]
    states = {}
    sense.run_cycle(feeds, states, tmp_path, now=0.0, interval_minutes=10, slots=288, threshold=2.0)
    assert json.loads((tmp_path / "status.json").read_text(encoding="utf-8")) == {"0": "active"}


# feed directory validation


def test_feed_dir_accepts_plain_names():
    for name in ("0", "3", "otter-cam_1", "A"):
        assert sense.validated_feed_dir(name) == name


def test_feed_dir_rejects_separators_traversal_and_empties():
    for name in ("../tmp/3", "/etc/foo", "a/b", "..", ".", "", "a\\b", "a b"):
        with pytest.raises(ValueError):
            sense.validated_feed_dir(name)


def test_grab_feed_rejects_a_dir_outside_the_volume(tmp_path):
    root = tmp_path / "sense"
    root.mkdir()
    with pytest.raises(ValueError):
        sense.grab_feed({"dir": "../escape", "id": "x"}, root, 0.0, 10, 288)
    assert not (tmp_path / "escape").exists()


# temporary file hygiene


def test_a_stale_grab_tmp_is_removed_on_the_next_attempt(tmp_path, monkeypatch):
    monkeypatch.setattr(sense, "resolve_manifest", lambda *a, **k: None)
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    (feed_dir / ".grab.jpg").write_bytes(b"partial")
    assert sense.grab_feed({"dir": "0", "id": "x"}, tmp_path, 0.0, 10, 288) is None
    assert not (feed_dir / ".grab.jpg").exists()


# startup reconciliation


def test_reconcile_storage_prunes_slots_outside_the_configured_ring(tmp_path):
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    for name in ("000.jpg", "001.jpg", "002.jpg", "999.jpg"):
        (feed_dir / name).write_bytes(name.encode())
    (feed_dir / "operator-note.txt").write_text("keep", encoding="utf-8")

    sense.reconcile_storage(tmp_path, [{"dir": "0", "id": "x"}], slots=2)

    assert sorted(path.name for path in feed_dir.iterdir()) == [
        "000.jpg",
        "001.jpg",
        "operator-note.txt",
    ]


def test_reconcile_storage_removes_unconfigured_feed_directories(tmp_path):
    configured = tmp_path / "0"
    configured.mkdir()
    removed = tmp_path / "old-feed"
    removed.mkdir()
    (removed / "001.jpg").write_bytes(b"stale")
    (tmp_path / "status.json").write_text("{}", encoding="utf-8")

    sense.reconcile_storage(tmp_path, [{"dir": "0", "id": "x"}], slots=2)

    assert configured.is_dir()
    assert not removed.exists()
    assert (tmp_path / "status.json").is_file()


def test_main_reconciles_storage_before_the_first_capture_cycle(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setenv("SENSE_DIR", str(tmp_path))
    monkeypatch.setenv("SENSE_FEEDS", '[{"dir":"cam","id":"x"}]')
    monkeypatch.setenv("SENSE_RING_SLOTS", "7")
    monkeypatch.setattr(sense, "SENSE_DIR", str(tmp_path))
    monkeypatch.setattr(
        sense,
        "reconcile_storage",
        lambda root, feeds, slots: calls.append((root, feeds, slots)),
    )
    monkeypatch.setattr(sense, "run_cycle", lambda *args, **kwargs: calls.append("cycle"))
    monkeypatch.setattr(sense.time, "time", lambda: 0.0)
    monkeypatch.setattr(sense.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopIteration))

    with pytest.raises(StopIteration):
        sense.main()

    assert calls[0] == (tmp_path, [{"dir": "cam", "id": "x"}], 7)
    assert calls[1] == "cycle"


def test_main_carries_on_when_storage_cannot_be_reconciled(tmp_path, monkeypatch, capsys):
    """A volume the service cannot read reports and continues into the cycle loop.

    Reconciliation is startup tidying, not a precondition. Raising out of main here
    would exit before the loop whose own guard exists to keep a misconfigured volume
    from becoming a silent crash-restart loop under `restart: unless-stopped`.
    """
    calls = []
    monkeypatch.setattr(sense, "SENSE_DIR", str(tmp_path / "absent"))
    monkeypatch.setenv("SENSE_FEEDS", '[{"dir":"cam","id":"x"}]')
    monkeypatch.setattr(sense, "run_cycle", lambda *args, **kwargs: calls.append("cycle"))
    monkeypatch.setattr(sense.time, "time", lambda: 0.0)
    monkeypatch.setattr(sense.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopIteration))

    with pytest.raises(StopIteration):
        sense.main()

    assert calls == ["cycle"]
    assert "reconcile failed" in capsys.readouterr().err


# cycle scheduling


def test_next_wake_is_just_past_the_next_boundary():
    assert sense.next_wake(0.0, 600) == 601.0
    assert sense.next_wake(599.0, 600) == 601.0
    assert sense.next_wake(600.0, 600) == 1201.0


def test_an_overrunning_cycle_is_followed_immediately():
    # A cycle starting at 590 that finishes at 700 gets only the minimum
    # sleep, so the next cycle lands in the slot the overrun would
    # otherwise skip.
    wake = sense.next_wake(590.0, 600)
    assert wake == 601.0
    assert max(wake - 700.0, 1) == 1


# feed configuration


def test_load_feeds_defaults_dir_to_the_list_position():
    feeds = sense.load_feeds('[{"id":"a"},{"id":"b","vf":"crop=1:1:0:0"},{"dir":"z","id":"c"}]')

    assert feeds == [
        {"id": "a", "dir": "0"},
        {"id": "b", "vf": "crop=1:1:0:0", "dir": "1"},
        {"dir": "z", "id": "c"},
    ]


def test_load_feeds_accepts_an_empty_list():
    assert sense.load_feeds("[]") == []
    assert sense.load_feeds("") == []


@pytest.mark.parametrize(
    "raw",
    [
        '{"id":"a"}',
        '["a"]',
        '[{"dir":"0"}]',
        '[{"id":""}]',
        '[{"id":"a","dir":"../x"}]',
        '[{"id":"a"},{"id":"b","dir":"0"}]',
    ],
)
def test_load_feeds_rejects_malformed_entries(raw):
    with pytest.raises(ValueError):
        sense.load_feeds(raw)


# frame age


def _aged_slot(feed_dir, name, age_seconds, now):
    path = feed_dir / name
    path.write_bytes(b"x")
    import os

    os.utime(path, (now - age_seconds, now - age_seconds))
    return path


def test_prune_stale_removes_slots_older_than_the_max_age(tmp_path):
    now = 1_000_000.0
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    old = _aged_slot(feed_dir, "000.jpg", 7200, now)
    fresh = _aged_slot(feed_dir, "001.jpg", 60, now)

    sense.prune_stale(tmp_path, [{"dir": "0", "id": "x"}], now, max_age_seconds=3600)

    assert not old.exists()
    assert fresh.exists()


def test_prune_stale_is_a_no_op_when_no_max_age_is_set(tmp_path):
    now = 1_000_000.0
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    old = _aged_slot(feed_dir, "000.jpg", 10**7, now)

    sense.prune_stale(tmp_path, [{"dir": "0", "id": "x"}], now, max_age_seconds=0)

    assert old.exists()


def test_prune_stale_touches_only_slot_files_of_configured_feeds(tmp_path):
    now = 1_000_000.0
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    other_dir = tmp_path / "1"
    other_dir.mkdir()
    tmp = _aged_slot(feed_dir, ".grab.jpg", 7200, now)
    other = _aged_slot(other_dir, "000.jpg", 7200, now)
    status = _aged_slot(tmp_path, "status.json", 7200, now)

    sense.prune_stale(tmp_path, [{"dir": "0", "id": "x"}], now, max_age_seconds=3600)

    assert tmp.exists()
    assert other.exists()
    assert status.exists()


def test_prune_stale_tolerates_a_feed_directory_that_does_not_exist_yet(tmp_path):
    sense.prune_stale(tmp_path, [{"dir": "0", "id": "x"}], 0.0, max_age_seconds=3600)


def test_run_cycle_prunes_stale_frames_after_grabbing(tmp_path, monkeypatch):
    now = 1_000_000.0
    feed_dir = tmp_path / "0"
    feed_dir.mkdir()
    old = _aged_slot(feed_dir, "000.jpg", 7200, now)
    monkeypatch.setattr(sense, "grab_feed", lambda *args, **kwargs: None)

    sense.run_cycle(
        [{"dir": "0", "id": "x"}], {}, tmp_path, now, 10, 288, 2.0, max_age_seconds=3600
    )

    assert not old.exists()


def test_main_reads_feeds_and_max_age_from_the_environment(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sense, "SENSE_DIR", str(tmp_path))
    monkeypatch.setenv("SENSE_FEEDS", '[{"id":"x"}]')
    monkeypatch.setenv("SENSE_MAX_AGE_HOURS", "1.5")
    monkeypatch.setattr(sense, "reconcile_storage", lambda *args: None)
    monkeypatch.setattr(sense, "run_cycle", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr(sense.time, "time", lambda: 0.0)
    monkeypatch.setattr(sense.time, "sleep", lambda _seconds: (_ for _ in ()).throw(StopIteration))

    with pytest.raises(StopIteration):
        sense.main()

    ((args, kwargs),) = calls
    assert args[0] == [{"id": "x", "dir": "0"}]
    assert kwargs == {"max_age_seconds": 5400.0}


# grab spacing within a cycle


def test_feed_offsets_spread_evenly_across_the_period():
    assert [sense.feed_offset(k, 4, 600) for k in range(4)] == [0.0, 150.0, 300.0, 450.0]
    assert sense.feed_offset(0, 1, 600) == 0.0


def test_run_cycle_grabs_each_feed_at_its_offset(tmp_path, monkeypatch):
    pauses, grabs = [], []
    monkeypatch.setattr(sense, "pause_until", lambda moment: pauses.append(moment))
    monkeypatch.setattr(
        sense,
        "grab_feed",
        lambda feed, root, now, *a, **k: grabs.append((feed["dir"], now)) or flat(10),
    )
    feeds = [{"dir": "a", "id": "x"}, {"dir": "b", "id": "y"}, {"dir": "c", "id": "z"}]

    sense.run_cycle(feeds, {}, tmp_path, now=1200.0, interval_minutes=10, slots=144, threshold=2.0)

    assert pauses == [1200.0, 1400.0, 1600.0]
    assert grabs == [("a", 1200.0), ("b", 1400.0), ("c", 1600.0)]


def test_run_cycle_does_not_pause_for_feeds_it_skips(tmp_path, monkeypatch):
    pauses = []
    monkeypatch.setattr(sense, "pause_until", lambda moment: pauses.append(moment))
    monkeypatch.setattr(sense, "grab_feed", lambda *a, **k: flat(10))
    feeds = [{"dir": "a", "id": "x"}, {"dir": "b", "id": "y"}]
    states = {"a": sense.FeedState(active=False, last_probe=1000.0), "b": sense.FeedState()}

    sense.run_cycle(
        feeds, states, tmp_path, now=1200.0, interval_minutes=10, slots=144, threshold=2.0
    )

    assert pauses == [1500.0]


def test_a_mid_interval_start_grabs_passed_offsets_at_once(tmp_path, monkeypatch):
    pauses, grabs = [], []
    monkeypatch.setattr(sense, "pause_until", lambda moment: pauses.append(moment))
    monkeypatch.setattr(
        sense,
        "grab_feed",
        lambda feed, root, now, *a, **k: grabs.append((feed["dir"], now)) or flat(10),
    )
    feeds = [{"dir": "a", "id": "x"}, {"dir": "b", "id": "y"}, {"dir": "c", "id": "z"}]

    sense.run_cycle(feeds, {}, tmp_path, now=1450.0, interval_minutes=10, slots=144, threshold=2.0)

    assert pauses == [1450.0, 1450.0, 1600.0]
    assert [g[1] for g in grabs] == [1450.0, 1450.0, 1600.0]
    assert {sense.slot_index(g[1], 10, 144) for g in grabs} == {sense.slot_index(1450.0, 10, 144)}


def test_offset_grabs_land_in_the_cycle_slot():
    # The last of many feeds still grabs before the period ends, so every
    # feed's frame lands in the slot the cycle began in.
    now = 1200.0
    last = now + sense.feed_offset(11, 12, 600)
    assert sense.slot_index(last, 10, 144) == sense.slot_index(now, 10, 144)


def test_pause_until_sleeps_only_into_the_future(monkeypatch):
    slept = []
    monkeypatch.setattr(sense.time, "time", lambda: 100.0)
    monkeypatch.setattr(sense.time, "sleep", lambda s: slept.append(s))
    sense.pause_until(130.0)
    sense.pause_until(90.0)
    assert slept == [30.0]
