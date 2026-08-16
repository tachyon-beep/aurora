"""Tests for the sense frame view and its stream-port route.

The sense ring is agent-readable but written only by the sense service; the
stage mounts it read-only and serves frames on the public stream port, so
every served name must be listing-matched, contained, and size-capped.
"""

import json
import os

from test_stage_server import call_stream_route

from stage import sensecam, server

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


def test_newest_frame_missing_dir_is_none(tmp_path):
    assert sensecam.newest_frame(str(tmp_path / "nope"), now=NOW) is None


def test_newest_frame_empty_ring_is_none(tmp_path):
    assert sensecam.newest_frame(str(_ring(tmp_path)), now=NOW) is None


def test_newest_frame_picks_the_newest_fresh_frame_across_slots(tmp_path):
    sense = _ring(tmp_path)
    _age(_frame(sense, "0", "001.jpg"), 900)
    _age(_frame(sense, "2", "140.jpg"), 60)

    got = sensecam.newest_frame(str(sense), now=NOW)

    assert got["slot"] == "2"
    assert got["name"] == "140.jpg"
    assert abs(got["captured_epoch"] - (NOW - 60)) < 1


def test_newest_frame_ignores_non_slot_entries_and_temporaries(tmp_path):
    sense = _ring(tmp_path)
    (sense / "status.json").write_text("{}", encoding="utf-8")
    _age(_frame(sense, "0", "notes.txt"), 5)
    _age(_frame(sense, "0", ".grab.jpg"), 5)
    _age(_frame(sense, "logs", "999.jpg"), 5)

    assert sensecam.newest_frame(str(sense), now=NOW) is None


def test_newest_frame_goes_stale(tmp_path):
    sense = _ring(tmp_path)
    _age(_frame(sense, "1", "010.jpg"), sensecam.FRESH_SECONDS + 1)

    assert sensecam.newest_frame(str(sense), now=NOW) is None


def test_newest_frame_rejects_a_symlinked_frame(tmp_path):
    sense = _ring(tmp_path)
    secret = tmp_path / "outside" / "secret.jpg"
    secret.parent.mkdir()
    secret.write_bytes(FRAME)
    _age(secret, 5)
    (sense / "0").mkdir()
    (sense / "0" / "001.jpg").symlink_to(secret)

    assert sensecam.newest_frame(str(sense), now=NOW) is None


def test_frame_bytes_path_serves_a_listed_jpg(tmp_path):
    sense = _ring(tmp_path)
    path = _frame(sense, "0", "001.jpg")

    assert sensecam.frame_bytes_path(str(sense), "0", "001.jpg") == str(path.resolve())


def test_frame_bytes_path_rejects_unlisted_and_traversal_names(tmp_path):
    sense = _ring(tmp_path)
    _frame(sense, "0", "001.jpg")
    _frame(sense, "0", ".grab.jpg")
    _frame(sense, "logs", "001.jpg")
    outside = tmp_path / "escape.jpg"
    outside.write_bytes(FRAME)

    for slot, name in (
        ("0", "../escape.jpg"),
        ("0", str(outside)),
        ("0", ""),
        ("0", "999.jpg"),
        ("0", "notes.txt"),
        ("0", ".grab.jpg"),
        ("..", "001.jpg"),
        ("", "001.jpg"),
        ("logs", "001.jpg"),
        ("1", "001.jpg"),
    ):
        assert sensecam.frame_bytes_path(str(sense), slot, name) is None, (slot, name)


def test_frame_bytes_path_rejects_a_symlink_out(tmp_path):
    sense = _ring(tmp_path)
    secret = tmp_path / "outside" / "secret.jpg"
    secret.parent.mkdir()
    secret.write_bytes(FRAME)
    (sense / "0").mkdir()
    (sense / "0" / "001.jpg").symlink_to(secret)

    assert sensecam.frame_bytes_path(str(sense), "0", "001.jpg") is None


def test_frame_bytes_path_rejects_an_oversized_frame(tmp_path):
    sense = _ring(tmp_path)
    _frame(sense, "0", "001.jpg", body=b"x" * (sensecam.FRAME_MAX_BYTES + 1))

    assert sensecam.frame_bytes_path(str(sense), "0", "001.jpg") is None


def test_frame_route_serves_a_real_frame(tmp_path, monkeypatch):
    sense = _ring(tmp_path)
    _frame(sense, "0", "001.jpg")
    monkeypatch.setattr(server, "SENSE_DIR", str(sense))

    status, headers, body = call_stream_route("/frame/0/001.jpg")

    assert status == 200
    assert headers["Content-Type"] == "image/jpeg"
    assert headers["Content-Length"] == str(len(FRAME))
    assert body == FRAME
    for key, value in server.SECURITY_HEADERS.items():
        assert headers[key] == value


def test_frame_route_rejects_traversal_and_unknown_paths(tmp_path, monkeypatch):
    sense = _ring(tmp_path)
    _frame(sense, "0", "001.jpg")
    monkeypatch.setattr(server, "SENSE_DIR", str(sense))

    for route in (
        "/frame/0/../001.jpg",
        "/frame/../0/001.jpg",
        "/frame/0/%2e%2e%2fescape.jpg",
        "/frame/0/001.jpg/extra",
        "/frame/0/",
        "/frame/0",
        "/frame/",
        "/frame/a/001.jpg",
        "/frame/0/999.jpg",
        "/frame/0/001.txt",
    ):
        status, _, body = call_stream_route(route)
        assert status == 404, route
        assert json.loads(body) == {"error": "not found"}


def test_frame_route_rejects_a_symlinked_frame(tmp_path, monkeypatch):
    sense = _ring(tmp_path)
    secret = tmp_path / "outside" / "secret.jpg"
    secret.parent.mkdir()
    secret.write_bytes(b"OUTSIDE_THE_MOUNT_c0ffee")
    (sense / "0").mkdir()
    (sense / "0" / "001.jpg").symlink_to(secret)
    monkeypatch.setattr(server, "SENSE_DIR", str(sense))

    status, _, body = call_stream_route("/frame/0/001.jpg")

    assert status == 404
    assert b"OUTSIDE_THE_MOUNT" not in body
