import datetime
import json

from stage import data

NOW = 1_000_000.0


def _write_events(tmp_path, events):
    path = tmp_path / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _event_stamp(epoch):
    moment = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") + "Z"


def _open(stream, ident, epoch):
    return {"timestamp": _event_stamp(epoch), "event": "open", "stream": stream, "id": ident}


def _close(stream, ident, epoch, tokens=0, status=200):
    return {
        "timestamp": _event_stamp(epoch),
        "event": "close",
        "stream": stream,
        "id": ident,
        "status": status,
        "usage": {"total_tokens": tokens},
    }


def test_an_absent_file_yields_the_empty_shape(tmp_path):
    pulse = data.request_pulse(str(tmp_path / "absent.jsonl"), now=NOW)
    assert pulse == {
        "in_flight": [],
        "buckets": [0] * 20,
        "requests_window": 0,
        "tokens_window": 0,
        "last_close_epoch": None,
    }


def test_closes_bucket_and_total_across_lanes(tmp_path):
    path = _write_events(
        tmp_path,
        [
            _open("core", "a", NOW - 10),
            _close("core", "a", NOW - 5, tokens=7),
            _close("aux", "b", NOW - 45, tokens=3),
            _close("core", "c", NOW - 599, tokens=11),
        ],
    )
    pulse = data.request_pulse(path, now=NOW)
    assert len(pulse["buckets"]) == 20
    assert pulse["buckets"][19] == 7
    assert pulse["buckets"][18] == 3
    assert pulse["buckets"][0] == 11
    assert sum(pulse["buckets"]) == 21
    assert pulse["requests_window"] == 3
    assert pulse["tokens_window"] == 21
    assert pulse["last_close_epoch"] == NOW - 5
    assert pulse["in_flight"] == []


def test_a_close_outside_the_window_counts_nowhere_but_keeps_the_last_epoch(tmp_path):
    path = _write_events(tmp_path, [_close("core", "old", NOW - 700, tokens=100)])
    pulse = data.request_pulse(path, now=NOW)
    assert pulse["buckets"] == [0] * 20
    assert pulse["requests_window"] == 0
    assert pulse["tokens_window"] == 0
    assert pulse["last_close_epoch"] == NOW - 700


def test_an_unmatched_open_is_in_flight_with_its_lane_capped(tmp_path):
    path = _write_events(tmp_path, [_open("s" * 40, "a", NOW - 12)])
    pulse = data.request_pulse(path, now=NOW)
    assert pulse["in_flight"] == [{"lane": "s" * 32, "since_epoch": NOW - 12}]


def test_a_matched_open_is_not_in_flight(tmp_path):
    path = _write_events(
        tmp_path,
        [_open("core", "a", NOW - 10), _close("core", "a", NOW - 5, tokens=2)],
    )
    pulse = data.request_pulse(path, now=NOW)
    assert pulse["in_flight"] == []


def test_an_open_older_than_the_in_flight_age_is_dropped(tmp_path):
    path = _write_events(
        tmp_path,
        [_open("core", "stale", NOW - 700), _open("core", "live", NOW - 5)],
    )
    pulse = data.request_pulse(path, now=NOW)
    assert pulse["in_flight"] == [{"lane": "core", "since_epoch": NOW - 5}]


def test_in_flight_rows_cap_to_the_four_newest(tmp_path):
    events = [_open("core", f"r{i}", NOW - 10 * (i + 1)) for i in range(6)]
    path = _write_events(tmp_path, events)
    pulse = data.request_pulse(path, now=NOW)
    assert [row["since_epoch"] for row in pulse["in_flight"]] == [
        NOW - 10,
        NOW - 20,
        NOW - 30,
        NOW - 40,
    ]


def test_bucket_count_follows_window_and_bucket_seconds(tmp_path):
    path = _write_events(tmp_path, [_close("core", "a", NOW - 50, tokens=5)])
    pulse = data.request_pulse(path, now=NOW, window=120, bucket_seconds=60)
    assert pulse["buckets"] == [0, 5]
    assert pulse["requests_window"] == 1
    assert pulse["tokens_window"] == 5
