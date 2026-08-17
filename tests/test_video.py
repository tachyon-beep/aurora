import json as _json
import os as _os
import sys as _sys
import time as _time

import pytest

import video


# input validation


def test_valid_video_id_is_returned_unchanged():
    assert video.validated_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_video_id_accepts_hyphen_and_underscore():
    assert video.validated_video_id("a_b-c1234XY") == "a_b-c1234XY"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "short",
        "twelvechars1",
        "has space12",
        "dots.dots..",
        "https://youtu.be/dQw4w9WgXcQ",
        "../../etc/pas",
        "dQw4w9WgXcQ\n",
        None,
        123,
    ],
)
def test_invalid_video_ids_are_refused(value):
    with pytest.raises(ValueError):
        video.validated_video_id(value)


def test_valid_query_is_returned_stripped():
    assert video.validated_query("  tide pools  ") == "tide pools"


def test_query_at_the_cap_is_accepted():
    text = "a" * video.QUERY_MAX_CHARS
    assert video.validated_query(text) == text


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a" * (video.QUERY_MAX_CHARS + 1),
        "two\nlines",
        "bell\x07",
        "null\x00byte",
        None,
        5,
    ],
)
def test_invalid_queries_are_refused(value):
    with pytest.raises(ValueError):
        video.validated_query(value)


def test_offset_within_duration_is_accepted():
    assert video.validated_offset("120", 600) == 120


def test_offset_zero_is_accepted():
    assert video.validated_offset("0", 600) == 0


def test_offset_at_the_duration_boundary_is_accepted():
    assert video.validated_offset("600", 600) == 600


def test_offset_is_unbounded_when_duration_is_none():
    assert video.validated_offset("999999", None) == 999999


@pytest.mark.parametrize("value", ["-1", "601", "abc", "", "1.5", None, True, False])
def test_invalid_offsets_are_refused(value):
    with pytest.raises(ValueError):
        video.validated_offset(value, 600)


# manifest validation


def public(host):
    return ["93.184.216.34"]


def private(host):
    return ["10.0.0.5"]


def loopback(host):
    return ["127.0.0.1"]


def test_https_manifest_on_an_allowed_host_is_accepted():
    ok, reason = video.classify_manifest(
        "https://r1---sn-abc.googlevideo.com/videoplayback?x=1", resolver=public
    )
    assert ok is True
    assert reason == ""


def test_http_manifest_is_refused():
    ok, reason = video.classify_manifest("http://r1.googlevideo.com/videoplayback", resolver=public)
    assert ok is False
    assert "scheme" in reason


def test_manifest_outside_the_host_allow_list_is_refused():
    ok, reason = video.classify_manifest("https://evil.example.com/x.m3u8", resolver=public)
    assert ok is False
    assert "host not allowed" in reason


def test_host_suffix_match_is_label_bounded():
    # notgooglevideo.com must not pass by ending with the allowed suffix.
    ok, reason = video.classify_manifest("https://notgooglevideo.com/x", resolver=public)
    assert ok is False
    assert "host not allowed" in reason


def test_manifest_resolving_to_a_private_address_is_refused():
    ok, reason = video.classify_manifest(
        "https://r1.googlevideo.com/videoplayback", resolver=private
    )
    assert ok is False
    assert "private/loopback/reserved" in reason


def test_manifest_resolving_to_loopback_is_refused():
    ok, reason = video.classify_manifest(
        "https://r1.googlevideo.com/videoplayback", resolver=loopback
    )
    assert ok is False
    assert "private/loopback/reserved" in reason


def test_manifest_with_no_host_is_refused():
    ok, reason = video.classify_manifest("https:///videoplayback", resolver=public)
    assert ok is False


def test_manifest_that_fails_resolution_is_refused():
    def raises(host):
        raise OSError("no such host")

    ok, reason = video.classify_manifest("https://r1.googlevideo.com/x", resolver=raises)
    assert ok is False
    assert "resolution failed" in reason


def test_unparseable_manifest_is_refused():
    ok, reason = video.classify_manifest("://::::", resolver=public)
    assert ok is False


# allowances


def test_rate_limit_allows_up_to_the_limit():
    history = []
    for _ in range(3):
        allowed, history = video.check_rate_limit(history, 1000.0, 3, 3600)
        assert allowed is True
    allowed, history = video.check_rate_limit(history, 1000.0, 3, 3600)
    assert allowed is False


def test_rate_limit_forgets_entries_outside_the_window():
    history = [0.0, 1.0, 2.0]
    allowed, history = video.check_rate_limit(history, 4000.0, 3, 3600)
    assert allowed is True
    assert history == [4000.0]


def test_budget_status_counts_only_the_window():
    status = video.budget_status([0.0, 3999.0], 4000.0, 3600)
    assert status["used"] == 1
    assert status["window_seconds"] == 3600


def test_budget_status_expiry_follows_the_oldest_entry():
    # min, not max: the earliest entry is the one that frees a slot first.
    status = video.budget_status([1000.0, 2000.0], 2500.0, 3600)
    assert status["oldest_expires_in_seconds"] == 2100


def test_budget_status_of_an_empty_history_reports_no_wait():
    status = video.budget_status([], 1000.0, 3600)
    assert status["used"] == 0
    assert status["oldest_expires_in_seconds"] is None


def test_console_limit_reads_an_integer():
    assert video.console_limit({"still_budget": 5}, "still_budget", 20) == 5


def test_console_limit_falls_back_on_unusable_values():
    assert video.console_limit({"still_budget": "many"}, "still_budget", 20) == 20
    assert video.console_limit({}, "still_budget", 20) == 20
    assert video.console_limit({"still_budget": None}, "still_budget", 20) == 20


def test_console_value_can_lower_the_allowance(monkeypatch):
    monkeypatch.setenv("VIDEO_STILL_HOURLY_MAX", "20")
    limit = video.effective_limit({"still_budget": 3}, "still_budget", "VIDEO_STILL_HOURLY_MAX", 20)
    assert limit == 3


def test_console_value_cannot_raise_the_allowance(monkeypatch):
    monkeypatch.setenv("VIDEO_STILL_HOURLY_MAX", "20")
    limit = video.effective_limit(
        {"still_budget": 9999}, "still_budget", "VIDEO_STILL_HOURLY_MAX", 20
    )
    assert limit == 20


def test_operator_ceiling_of_zero_permits_nothing(monkeypatch):
    monkeypatch.setenv("VIDEO_HOURLY_MAX", "0")
    assert video.effective_limit({"video_budget": 5}, "video_budget", "VIDEO_HOURLY_MAX", 1) == 0


def test_unusable_operator_ceiling_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("VIDEO_HOURLY_MAX", "lots")
    assert video.effective_limit({}, "video_budget", "VIDEO_HOURLY_MAX", 1) == 1


def test_the_video_ceiling_comes_from_the_environment(monkeypatch):
    monkeypatch.delenv("VIDEO_HOURLY_MAX", raising=False)
    assert video.env_limit("VIDEO_HOURLY_MAX", 1) == 1
    monkeypatch.setenv("VIDEO_HOURLY_MAX", "3")
    assert video.env_limit("VIDEO_HOURLY_MAX", 1) == 3
    monkeypatch.setenv("VIDEO_HOURLY_MAX", "not a number")
    assert video.env_limit("VIDEO_HOURLY_MAX", 1) == 1
    monkeypatch.setenv("VIDEO_HOURLY_MAX", "-5")
    assert video.env_limit("VIDEO_HOURLY_MAX", 1) == 0


def test_rate_limited_message_names_the_kind_and_the_wait():
    text = video.rate_limited_message("still", 20, [1000.0], 1600.0, 3600)
    assert "20" in text
    assert "still" in text
    assert "3000 seconds" in text


def test_rate_limited_message_omits_the_wait_when_the_history_is_empty():
    text = video.rate_limited_message("still", 20, [], 1600.0, 3600)
    assert "20" in text
    assert "still" in text
    assert "next available" not in text


# console cycle


@pytest.fixture
def volume(tmp_path, monkeypatch):
    """A /video volume rooted in tmp_path, with the module's paths pointed at it."""
    monkeypatch.setattr(video, "VIDEO_DIR", str(tmp_path))
    monkeypatch.setattr(video, "CONSOLE_FILE", str(tmp_path / "console.json"))
    monkeypatch.setattr(video, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(video, "HELP_FILE", str(tmp_path / "HELP.md"))
    monkeypatch.setattr(video, "OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setattr(video, "STILLS_DIR", str(tmp_path / "stills"))
    video.ensure_dirs()
    return tmp_path


def test_load_console_reads_commands_and_variables(volume):
    (volume / "console.json").write_text(
        _json.dumps({"commands": ["search tide pools"], "variables": {"enable_frames": True}})
    )
    commands, variables = video.load_console()
    assert commands == ["search tide pools"]
    assert variables == {"enable_frames": True}


def test_load_console_tolerates_a_missing_file(volume):
    assert video.load_console() == ([], {})


def test_load_console_tolerates_malformed_json(volume):
    (volume / "console.json").write_text("{not json")
    assert video.load_console() == ([], {})


def test_load_console_tolerates_wrong_types(volume):
    (volume / "console.json").write_text(_json.dumps({"commands": "no", "variables": 7}))
    assert video.load_console() == ([], {})


def test_consume_batch_clears_commands_and_keeps_variables(volume):
    (volume / "console.json").write_text(
        _json.dumps({"commands": ["search x"], "variables": {"text_budget": 5}})
    )
    video.consume_batch()
    data = _json.loads((volume / "console.json").read_text())
    assert data["commands"] == []
    assert data["variables"] == {"text_budget": 5}


def test_write_output_names_the_command_and_returns_the_path(volume):
    path = video.write_output("search tide pools", "one line")
    assert _os.path.dirname(path) == str(volume / "output")
    assert "search" in _os.path.basename(path)
    assert open(path, encoding="utf-8").read() == "one line"


def test_write_output_bounds_the_filename(volume):
    path = video.write_output("search " + "a" * 500, "x")
    assert len(_os.path.basename(path).encode("utf-8")) <= video.OUTPUT_NAME_MAX_BYTES


def test_write_output_does_not_collide_within_one_second(volume):
    first = video.write_output("search tide pools", "FIRST")
    second = video.write_output("search tide pools", "SECOND")
    assert first != second
    assert open(first, encoding="utf-8").read() == "FIRST"
    assert open(second, encoding="utf-8").read() == "SECOND"


@pytest.mark.parametrize(
    "command",
    [
        "search ../../etc/passwd",
        "search /etc/passwd",
        "search ..\\..\\windows",
        "search a\x00b",
        "search ／．．",
    ],
)
def test_write_output_cannot_escape_the_output_directory(command, volume):
    path = video.write_output(command, "x")
    name = _os.path.basename(path)
    assert _os.path.dirname(path) == video.OUTPUT_DIR
    assert "/" not in name and "\\" not in name
    assert ".." not in name
    assert "\x00" not in name


# pruning


def test_prune_tree_keeps_the_newest(volume):
    stills = volume / "stills"
    for i in range(5):
        f = stills / f"frame{i}.jpg"
        f.write_bytes(b"x")
        _os.utime(f, (1000 + i, 1000 + i))
    video.prune_tree(str(stills), keep=2, suffix=".jpg")
    remaining = sorted(p.name for p in stills.iterdir())
    assert remaining == ["frame3.jpg", "frame4.jpg"]


def test_prune_tree_ignores_other_suffixes(volume):
    stills = volume / "stills"
    (stills / "a.jpg").write_bytes(b"x")
    (stills / "keep.txt").write_text("x")
    video.prune_tree(str(stills), keep=0, suffix=".jpg")
    assert [p.name for p in stills.iterdir()] == ["keep.txt"]


def test_prune_tree_tolerates_a_missing_directory(volume):
    video.prune_tree(str(volume / "absent"), keep=1, suffix=".jpg")


# subprocess hygiene


def test_run_binary_returns_output_of_a_successful_command():
    code, out = video.run_binary([_sys.executable, "-c", "print('hello')"], timeout=10)
    assert code == 0
    assert out.strip() == "hello"


def test_run_binary_reports_a_non_zero_exit():
    code, out = video.run_binary([_sys.executable, "-c", "raise SystemExit(3)"], timeout=10)
    assert code == 3


def test_run_binary_times_out_without_raising():
    code, out = video.run_binary([_sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert code == -1
    assert out == ""


def test_run_binary_tolerates_a_missing_binary():
    code, out = video.run_binary(["/nonexistent/binary/xyz"], timeout=5)
    assert code == -1
    assert out == ""


def test_a_timed_out_child_is_reaped():
    # A killed-but-unreaped child holds a pid; under pids_limit a leak
    # eventually stops the service forking at all.
    video.run_binary([_sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    # No zombie remains: waitpid finds no unreaped child.
    with pytest.raises(ChildProcessError):
        _os.waitpid(-1, _os.WNOHANG)


def test_a_grandchild_is_killed_with_the_group(tmp_path):
    # yt-dlp spawns deno; killing only the parent orphans the helper. A
    # grandchild that outlives the timeout would prove the group was not
    # killed, only the direct child -- so the grandchild writes a marker
    # file after a delay, and the test asserts the marker never appears.
    marker = tmp_path / "grandchild_survived"
    grandchild_code = "import time; time.sleep(4); open(%r, 'w').write('alive')" % str(marker)
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', %r]); "
        "time.sleep(30)" % grandchild_code
    )
    code, out = video.run_binary([_sys.executable, "-c", script], timeout=2)
    assert code == -1
    _time.sleep(6)
    assert not marker.exists()


def test_run_binary_rejects_a_string_command():
    # Never a shell string: an argument list is the boundary.
    with pytest.raises(TypeError):
        video.run_binary("echo hello", timeout=5)
