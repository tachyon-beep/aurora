"""The senses page: every feed's newest frame, its status, and the page itself.

The page shows one tile per feed directory rather than one global newest
frame, merged with the closed-vocabulary status.json the capture service
publishes. Both readers treat the mount as untrusted: names are
listing-matched, paths contained, the status file size-capped and its
fields validated before anything reaches the snapshot.
"""

import json
import os

from test_stage_server import call_stream_route

from stage import sense_page, sensecam, server

FRAME = b"\xff\xd8\xff\xe0frame"

NOW = 1_755_300_000.0


def _ring(tmp_path):
    sense = tmp_path / "sense"
    sense.mkdir()
    return sense


def _frame(sense, slot, name, body=FRAME):
    slot_dir = sense / slot
    slot_dir.mkdir(exist_ok=True)
    path = slot_dir / name
    path.write_bytes(body)
    return path


def _age(path, seconds):
    os.utime(path, (NOW - seconds, NOW - seconds))


def test_newest_frames_missing_dir_is_empty(tmp_path):
    assert sensecam.newest_frames(str(tmp_path / "nope")) == {}


def test_newest_frames_picks_the_newest_per_feed(tmp_path):
    sense = _ring(tmp_path)
    _age(_frame(sense, "1", "001.jpg"), 900)
    _age(_frame(sense, "1", "002.jpg"), 60)
    _age(_frame(sense, "2", "140.jpg"), 4000)

    got = sensecam.newest_frames(str(sense))

    assert set(got) == {"1", "2"}
    assert got["1"]["name"] == "002.jpg"
    assert abs(got["1"]["captured_epoch"] - (NOW - 60)) < 1
    assert got["2"]["name"] == "140.jpg"


def test_newest_frames_ignores_temporaries_and_non_slot_entries(tmp_path):
    sense = _ring(tmp_path)
    _frame(sense, "1", ".grab.jpg")
    _frame(sense, "1", "notes.txt")
    (sense / "status.json").write_text("{}")
    (sense / "x").mkdir()
    _frame(sense, "x", "001.jpg")

    assert sensecam.newest_frames(str(sense)) == {}


def test_feed_status_missing_file_is_empty(tmp_path):
    assert sensecam.feed_status(str(_ring(tmp_path))) == {}


def test_feed_status_reads_state_and_lively(tmp_path):
    sense = _ring(tmp_path)
    (sense / "status.json").write_text(
        json.dumps(
            {
                "1": {"state": "active", "lively": 1755000000},
                "2": {"state": "inactive", "lively": None},
            }
        )
    )

    got = sensecam.feed_status(str(sense))

    assert got["1"] == {"state": "active", "lively": 1755000000}
    assert got["2"] == {"state": "inactive", "lively": None}


def test_feed_status_drops_malformed_entries(tmp_path):
    sense = _ring(tmp_path)
    (sense / "status.json").write_text(
        json.dumps(
            {
                "1": {"state": "on fire", "lively": "soon"},
                "x": {"state": "active", "lively": 1},
                "2": "active",
                "3": {"state": "active", "lively": True},
            }
        )
    )

    got = sensecam.feed_status(str(sense))

    assert got == {"1": {"state": None, "lively": None}, "3": {"state": "active", "lively": None}}


def test_feed_status_rejects_an_oversized_file(tmp_path):
    sense = _ring(tmp_path)
    (sense / "status.json").write_text("[" + " " * (sensecam.STATUS_MAX_BYTES + 10) + "]")

    assert sensecam.feed_status(str(sense)) == {}


def test_sense_snapshot_merges_frames_and_status_in_feed_order(tmp_path, monkeypatch):
    sense = _ring(tmp_path)
    _age(_frame(sense, "2", "010.jpg"), 60)
    _age(_frame(sense, "10", "011.jpg"), 120)
    (sense / "status.json").write_text(
        json.dumps(
            {
                "2": {"state": "active", "lively": 1755000000},
                "3": {"state": "inactive", "lively": None},
            }
        )
    )
    monkeypatch.setattr(server, "SENSE_DIR", str(sense))

    snap = server.sense_snapshot(now=NOW)

    assert snap["fresh_seconds"] == sensecam.FRESH_SECONDS
    feeds = snap["feeds"]
    assert [f["feed"] for f in feeds] == ["2", "3", "10"]
    assert feeds[0]["url"] == "/frame/2/010.jpg"
    assert feeds[0]["state"] == "active"
    assert feeds[0]["lively"] == 1755000000
    assert abs(feeds[0]["captured_epoch"] - (NOW - 60)) < 1
    assert feeds[1]["url"] is None
    assert feeds[1]["captured_epoch"] is None
    assert feeds[2]["state"] is None


def test_sense_api_route_serves_the_snapshot(tmp_path, monkeypatch):
    sense = _ring(tmp_path)
    _frame(sense, "1", "001.jpg")
    monkeypatch.setattr(server, "SENSE_DIR", str(sense))

    status, headers, body = call_stream_route("/api/sense")

    assert status == 200
    snap = json.loads(body)
    assert snap["feeds"][0]["feed"] == "1"
    assert snap["feeds"][0]["url"] == "/frame/1/001.jpg"


def test_sense_page_route_serves_the_page(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SENSE_DIR", str(_ring(tmp_path)))

    status, headers, body = call_stream_route("/sense")

    assert status == 200
    assert "text/html" in headers["Content-Type"]
    text = body.decode("utf-8")
    assert 'id="masthead"' in text
    assert 'id="ring"' in text


def test_sense_page_carries_the_masthead_and_the_other_senses():
    text = sense_page.SENSE_PAGE_HTML
    assert 'id="page-name">SENSES' in text
    assert 'id="ring"' in text
    for heading in (
        "THE DIODE",
        "THE VIDEO CONSOLE",
        "THE MODEL STREAMS",
        "THE BOOKS",
        "THE GARDEN",
        "ITS OWN SOURCE",
    ):
        assert heading in text
    assert 'href="/"' in text
    assert 'href="/blog"' in text
    assert 'href="/telemetry"' in text


def test_stream_snapshot_no_longer_carries_a_sense_key(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SENSE_DIR", str(_ring(tmp_path)))
    assert "sense" not in server._empty_snapshot(NOW)
