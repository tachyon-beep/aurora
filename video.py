"""A closed command vocabulary for searching, transcribing, and sampling recorded video.

The agent writes commands to a JSON console on a shared volume; this service
executes them one at a time and writes each result to a file. Input is a video
identifier, an integer offset, or a bounded query -- never a URL. Every upstream
URL is composed here, and the one URL that is not (the media manifest yt-dlp
resolves) is validated before ffmpeg receives it. No credential is present in
this service's environment.
"""

import ipaddress
import math
import os
import re
import socket
from urllib.parse import urlparse

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
    """An offset in seconds: a non-negative integer, bounded by the duration when one is known."""
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


def _default_resolver(host):
    """Resolve a hostname to its IP address strings."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def classify_manifest(url, resolver=_default_resolver):
    """Return (ok, reason) for a resolved media manifest URL.

    The single URL in this service that is not composed here: yt-dlp resolves
    it, and it is checked before ffmpeg receives it. https only, host within
    the allow-list, and no loopback, link-local, private, reserved, multicast,
    or unspecified address. ffmpeg resolves the host again when it connects,
    so the address check is best-effort against a rebind between validation
    and fetch; the host allow-list is what bounds where the fetch can go.
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable url: {e}"
    if parsed.scheme != "https":
        return False, f"scheme not allowed: {parsed.scheme or '(none)'}"
    host = parsed.hostname
    if not host:
        return False, "no host"
    if not any(host == s or host.endswith("." + s) for s in MANIFEST_HOST_SUFFIXES):
        return False, f"host not allowed: {host}"
    try:
        addrs = resolver(host)
    except Exception as e:
        return False, f"resolution failed: {e}"
    if not addrs:
        return False, "no addresses"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"bad address: {addr}"
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"private/loopback/reserved target: {addr}"
    return True, ""


BUDGET_WINDOW = 3600
DEFAULT_VIDEO_LIMIT = 1
DEFAULT_STILL_LIMIT = 20
DEFAULT_TEXT_LIMIT = 20


def check_rate_limit(history, now, limit, window):
    """Token-bucket-ish check. Returns (allowed, new_history).

    history is a list of prior timestamps. Drops entries older than window;
    allows if fewer than limit remain, appending now when allowed.
    """
    recent = [t for t in history if now - t < window]
    if len(recent) >= limit:
        return False, recent
    recent.append(now)
    return True, recent


def budget_status(history, now, window):
    """Use of an allowance over the window.

    Prunes the history to the window rather than trusting the caller's list, so
    a quiet period lowers the count with no command having run.
    """
    recent = [t for t in history if now - t < window]
    oldest = None
    if recent:
        oldest = max(0, math.ceil(window - (now - min(recent))))
    return {
        "used": len(recent),
        "window_seconds": window,
        "oldest_expires_in_seconds": oldest,
    }


def console_limit(variables, key, default):
    """An hourly limit from the console, or the default when unusable."""
    try:
        return int(variables.get(key, default))
    except (TypeError, ValueError):
        return default


def env_limit(name, default):
    """An operator ceiling from the environment; not settable from the console."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def effective_limit(variables, console_key, env_name, default):
    """min(console value, operator ceiling): the console may lower, never raise."""
    ceiling = env_limit(env_name, default)
    return max(0, min(console_limit(variables, console_key, default), ceiling))


def rate_limited_message(kind, limit, history, now, window):
    """The refusal text for an exhausted allowance, carrying the wait when known."""
    text = f"rate limited: at most {limit} {kind} operation(s) per hour"
    seconds = budget_status(history, now, window)["oldest_expires_in_seconds"]
    if seconds is None:
        return text
    return f"{text}; next available in {seconds} seconds"
