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
