"""A closed command vocabulary for searching, transcribing, and sampling recorded video.

The agent writes commands to a JSON console on a shared volume; this service
executes them one at a time and writes each result to a file. Input is a video
identifier, an integer offset, or a bounded query -- never a URL. Every upstream
URL is composed here, and the one URL that is not (the media manifest yt-dlp
resolves) is validated before ffmpeg receives it. No credential is present in
this service's environment.
"""

import os
import re

VIDEO_DIR = os.environ.get("VIDEO_DIR", "/video")
CONSOLE_FILE = os.path.join(VIDEO_DIR, "console.json")
STATE_FILE = os.path.join(VIDEO_DIR, "state.json")
HELP_FILE = os.path.join(VIDEO_DIR, "HELP.md")
OUTPUT_DIR = os.path.join(VIDEO_DIR, "output")
STILLS_DIR = os.path.join(VIDEO_DIR, "stills")

POLL_SECONDS = 5

# Eleven characters of the URL-safe alphabet. The unit of input is this
# identifier; no host, scheme, or path is ever accepted.
VIDEO_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{11}\Z")

QUERY_MAX_CHARS = 200
# Printable characters only: control characters and newlines cannot reach an
# extractor argument.
QUERY_FORBIDDEN = re.compile(r"[\x00-\x1f\x7f]")

# The only hosts a resolved manifest may name.
MANIFEST_HOST_SUFFIXES = ("googlevideo.com", "youtube.com")


def validated_video_id(value):
    """A video identifier: eleven URL-safe characters, nothing else."""
    if not isinstance(value, str) or VIDEO_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"invalid video id: {value!r}")
    return value


def validated_query(value):
    """A search query: non-empty, within the cap, no control characters."""
    if not isinstance(value, str):
        raise ValueError("query must be text")
    text = value.strip()
    if not text:
        raise ValueError("empty query")
    if len(text) > QUERY_MAX_CHARS:
        raise ValueError(f"query longer than {QUERY_MAX_CHARS} characters")
    if QUERY_FORBIDDEN.search(text) is not None:
        raise ValueError("query contains control characters")
    return text


def validated_offset(value, duration):
    """An offset in seconds: a non-negative integer within the video's duration."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"invalid offset: {value!r}")
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"invalid offset: {value!r}") from None
    if seconds < 0:
        raise ValueError("offset before the start")
    if duration is not None and seconds > duration:
        raise ValueError("offset past the end")
    return seconds
