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
import signal
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
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
    while slug and len(name.encode("utf-8")) > OUTPUT_NAME_MAX_BYTES:
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
            try:
                os.unlink(path)
            except OSError:
                pass
            continue
    entries.sort()
    for _, path in entries[: max(0, len(entries) - keep)]:
        try:
            os.unlink(path)
        except OSError:
            continue


RESOLVE_TIMEOUT_SECONDS = 60
STILL_TIMEOUT_SECONDS = 120


def run_binary(args, timeout):
    """Run an argument list, returning (returncode, stdout). Never raises.

    One subprocess is in flight at a time. On timeout the whole process group
    is killed rather than the direct child alone -- yt-dlp spawns a JavaScript
    runtime for extraction, and killing only the parent orphans it -- and the
    child is then waited on, so no zombie survives the command that created it.
    A leaked pid would eventually stop this service forking, and the restart
    that followed would reset the in-memory allowances. Undecodable bytes from
    the child are replaced rather than raised, since third-party output (a
    video title, extractor metadata) arrives in an arbitrary encoding and a
    mangled byte must not crash the poll loop; any other failure during
    teardown is likewise swallowed rather than raised. Worst-case wall clock
    is bounded at timeout + 15 seconds: up to 10 seconds draining pipes after
    the kill, then a final wait bounded at 5 seconds, after which the call
    returns regardless of whether the child has actually exited.
    """
    if not isinstance(args, (list, tuple)):
        raise TypeError("args must be an argument list, never a shell string")
    try:
        process = subprocess.Popen(
            list(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            start_new_session=True,
        )
    except OSError:
        return -1, ""
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            process.kill()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
        except Exception:
            pass
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        return -1, ""
    except Exception:
        return -1, ""
    return process.returncode, stdout or ""


SEARCH_RESULT_COUNT = 10
SEARCH_TITLE_CAP = 300
SEARCH_CHANNEL_CAP = 80
TRANSCRIPT_MAX_BYTES = 500_000
TRUNCATION_MARKER = "[truncated]"
CAPTION_FETCH_TIMEOUT = 30
CAPTION_MAX_BYTES = 5_000_000


def format_duration(seconds):
    """Minutes and seconds, or a dash when the duration is unknown."""
    if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
        return "-"
    total = int(seconds)
    if total < 0:
        return "-"
    return f"{total // 60}:{total % 60:02d}"


def _cap(value, limit):
    """A field as text, bounded in length. Content is never rewritten."""
    if not isinstance(value, str):
        return ""
    return value[:limit]


def search_lines(payload):
    """Result lines from a yt-dlp search payload: id, duration, channel, title."""
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        return []
    lines = []
    for entry in entries[:SEARCH_RESULT_COUNT]:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        if not isinstance(video_id, str) or not video_id:
            continue
        duration = format_duration(entry.get("duration"))
        channel = _cap(entry.get("channel") or entry.get("uploader") or "", SEARCH_CHANNEL_CAP)
        title = _cap(entry.get("title") or "", SEARCH_TITLE_CAP)
        lines.append(f"{video_id}  {duration}  {channel}  {title}".rstrip())
    return lines


def search(query):
    """Search for videos; returns the result text."""
    try:
        text = validated_query(query)
    except ValueError as e:
        return f"invalid query: {e}"
    code, out = run_binary(
        [
            "yt-dlp",
            "--dump-single-json",
            "--flat-playlist",
            "--no-warnings",
            f"ytsearch{SEARCH_RESULT_COUNT}:{text}",
        ],
        timeout=RESOLVE_TIMEOUT_SECONDS,
    )
    if code != 0 or not out.strip():
        return "search unavailable"
    try:
        payload = json.loads(out)
    except ValueError:
        return "search unavailable"
    lines = search_lines(payload)
    if not lines:
        return "no results"
    return "\n".join(lines)


def _caption_track(payload):
    """(url, language, kind) for the best available caption track, or None.

    Manual captions are preferred over automatic ones.
    """
    if not isinstance(payload, dict):
        return None
    for key, kind in (("subtitles", "manual"), ("automatic_captions", "automatic")):
        tracks = payload.get(key)
        if not isinstance(tracks, dict):
            continue
        for language, formats in tracks.items():
            if not isinstance(formats, list):
                continue
            for fmt in formats:
                if isinstance(fmt, dict) and fmt.get("ext") == "json3" and fmt.get("url"):
                    return fmt["url"], language, kind
    return None


def _fetch_caption(url):
    """Fetch a caption track; returns the parsed payload or None."""
    ok, _ = classify_manifest(url)
    if not ok:
        return None
    try:
        with urllib.request.urlopen(url, timeout=CAPTION_FETCH_TIMEOUT) as response:
            raw = response.read(CAPTION_MAX_BYTES)
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        return None


def _transcript_payload(video_id):
    """Metadata plus caption events for a video, or None when unavailable."""
    code, out = run_binary(
        [
            "yt-dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            f"https://www.youtube.com/watch?v={video_id}",
        ],
        timeout=RESOLVE_TIMEOUT_SECONDS,
    )
    if code != 0 or not out.strip():
        return None
    try:
        payload = json.loads(out)
    except ValueError:
        return None
    track = _caption_track(payload)
    if track is None:
        return payload
    url, language, kind = track
    fetched = _fetch_caption(url)
    if isinstance(fetched, dict):
        payload["_transcript_events"] = fetched.get("events") or []
        payload["_transcript_language"] = language
        payload["_transcript_kind"] = kind
    return payload


def transcript_lines(payload, start, end):
    """Timed transcript lines, optionally bounded to a window in seconds."""
    events = payload.get("_transcript_events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return []
    lines = []
    for event in events:
        if not isinstance(event, dict):
            continue
        segs = event.get("segs")
        if not isinstance(segs, list):
            continue
        text = "".join(s.get("utf8", "") for s in segs if isinstance(s, dict)).strip()
        if not text:
            continue
        seconds = int(event.get("tStartMs", 0) // 1000)
        if start is not None and seconds < start:
            continue
        if end is not None and seconds > end:
            continue
        lines.append(f"[{format_duration(seconds)}] {text}")
    return lines


def transcript(video_id, start, end):
    """Fetch a timed transcript; returns the result text, bounded to TRANSCRIPT_MAX_BYTES."""
    try:
        vid = validated_video_id(video_id)
    except ValueError as e:
        return f"invalid video id: {e}"
    payload = _transcript_payload(vid)
    if payload is None:
        return "video unavailable"
    lines = transcript_lines(payload, start, end)
    if not lines:
        return "no transcript available"
    header = (
        f"{payload.get('_transcript_kind', 'unknown')} captions, "
        f"language {payload.get('_transcript_language', 'unknown')}"
    )
    body = "\n".join(lines)
    text = f"{header}\n{body}"
    encoded = text.encode("utf-8")
    if len(encoded) > TRANSCRIPT_MAX_BYTES:
        text = encoded[:TRANSCRIPT_MAX_BYTES].decode("utf-8", "ignore") + "\n" + TRUNCATION_MARKER
    return text
