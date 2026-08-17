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


def test_rate_limited_message_names_the_kind_and_the_wait():
    text = video.rate_limited_message("still", 20, [1000.0], 1600.0, 3600)
    assert "20" in text
    assert "still" in text
    assert "3000 seconds" in text
