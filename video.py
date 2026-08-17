"""A closed command vocabulary for searching, transcribing, and sampling recorded video.

The agent writes commands to a JSON console on a shared volume; this service
executes them one at a time and writes each result to a file. Input is a video
identifier, an integer offset, or a bounded query -- never a URL. Every upstream
URL is composed here, and the one URL that is not (the media manifest yt-dlp
resolves) is validated before ffmpeg receives it. No credential is present in
this service's environment.
"""

import datetime
import ipaddress
import json
import math
import os
import re
import socket
import tempfile
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


OUTPUT_NAME_MAX_BYTES = 160
VIDEO_STILL_KEEP = 200
VIDEO_OUTPUT_KEEP = 200


def ensure_dirs():
    """Create the volume's directories; safe to call on every cycle."""
    for path in (OUTPUT_DIR, STILLS_DIR):
        os.makedirs(path, exist_ok=True)


def _replace_json(path, data):
    """Write data to path as JSON through a temporary file and one os.replace.

    A write interrupted partway leaves the existing file as it was, so a
    reader after a crash sees the previous contents rather than a truncated
    file it would have to discard.
    """
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    finally:
        try:
            os.remove(tmp)
        except FileNotFoundError:
            pass


def load_console():
    """Read (commands, variables) from CONSOLE_FILE; defaults on missing/malformed."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], {}
    commands = data.get("commands", []) if isinstance(data, dict) else []
    variables = data.get("variables", {}) if isinstance(data, dict) else {}
    if not isinstance(commands, list):
        commands = []
    if not isinstance(variables, dict):
        variables = {}
    return commands, variables


def consume_batch():
    """Atomically clear the commands list in CONSOLE_FILE, preserving variables."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["commands"] = []
    data.setdefault("variables", {})
    _replace_json(CONSOLE_FILE, data)


def _output_slug(command):
    """A filesystem-safe stem from a command string."""
    slug = re.sub(r"[^A-Za-z0-9]+", "_", command).strip("_").lower()
    return slug or "result"


def write_output(command, text):
    """Write a command result into OUTPUT_DIR; returns the path written."""
    ensure_dirs()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    slug = _output_slug(command)
    name = f"{stamp}_{slug}.txt"
    while len(name.encode("utf-8")) > OUTPUT_NAME_MAX_BYTES:
        slug = slug[:-1]
        name = f"{stamp}_{slug}.txt"
    path = os.path.join(OUTPUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def prune_tree(directory, keep, suffix):
    """Keep the newest `keep` files with `suffix`; remove the rest, oldest first.

    Runs on the poll cycle regardless of console state. A cleanup command would
    be unreachable exactly when it is needed: an agent whose volume is full
    cannot write the command that would clean it.
    """
    try:
        names = [n for n in os.listdir(directory) if n.endswith(suffix)]
    except OSError:
        return
    entries = []
    for name in names:
        path = os.path.join(directory, name)
        try:
            entries.append((os.stat(path).st_mtime, path))
        except OSError:
            continue
    entries.sort()
    for _, path in entries[: max(0, len(entries) - keep)]:
        try:
            os.unlink(path)
        except OSError:
            continue
