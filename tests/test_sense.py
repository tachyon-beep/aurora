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
