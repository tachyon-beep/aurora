"""Periodic frame capture from public video feeds into a fixed ring of slots.

Each configured feed gets a directory under the sense volume holding slots
000.jpg..NNN.jpg, overwritten in place. Slot selection is derived from wall
clock time so a restart resumes the same rotation. Within each interval the
feeds are grabbed one at a time at evenly spaced offsets rather than together
at the boundary. A per-feed detector marks
feeds inactive when frames stop changing or grabs keep failing; inactive
feeds are probed once daily and reactivate when a probe frame differs from
the last one seen. The only writes are the frames, status.json, and the
short-lived temporaries each is atomically renamed from. Frames older than
an optional maximum age are removed each cycle; without one, a frame stays
until the ring overwrites its slot.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

SENSE_DIR = os.environ.get("SENSE_DIR", "/sense")

DEFAULT_INTERVAL_MINUTES = 10
DEFAULT_RING_SLOTS = 144
DEFAULT_STATIC_THRESHOLD = 2.0
DEFAULT_MAX_AGE_HOURS = 24.0

# Consecutive static grabs, and consecutive failed grabs, that mark a feed
# inactive. Detector state is in memory only; a restart clears the counters,
# which can only delay marking a feed inactive.
STATIC_LIMIT = 12
FAILURE_LIMIT = 6
PROBE_INTERVAL_SECONDS = 24 * 60 * 60

RESOLVE_TIMEOUT_SECONDS = 60
GRAB_TIMEOUT_SECONDS = 120
THUMB_SIZE = 32


def slot_index(epoch_seconds: float, interval_minutes: int, slots: int) -> int:
    """Ring slot for a moment in time; deterministic across restarts."""
    minutes = int(epoch_seconds) // 60
    return (minutes // interval_minutes) % slots


def slot_name(index: int) -> str:
    return f"{index:03d}.jpg"


# Bare ASCII numerals. A feed directory is a surface the agent reads, so it
# carries no caption, feed name, or geography; the stage lists feeds by this
# same pattern, and a name only one side accepts is a feed captured but never
# rendered. `\d` is not equivalent: it also matches non-ASCII digits.
FEED_DIR_PATTERN = re.compile(r"[0-9]+")


def validated_feed_dir(name: str) -> str:
    """A feed directory name: one bare numeral, no separators."""
    if not isinstance(name, str) or FEED_DIR_PATTERN.fullmatch(name) is None:
        raise ValueError(f"invalid feed dir: {name!r}")
    return name


def load_feeds(raw: str) -> list[dict]:
    """Parse the feed list; entries are {id, [dir], [vf]}, dir defaulting to the position."""
    feeds = json.loads(raw) if raw.strip() else []
    if not isinstance(feeds, list):
        raise ValueError("feeds must be a list")
    seen: set[str] = set()
    for position, feed in enumerate(feeds):
        if not isinstance(feed, dict):
            raise ValueError(f"feed {position} must be an object")
        if not isinstance(feed.get("id"), str) or not feed["id"]:
            raise ValueError(f"feed {position} needs a non-empty id")
        feed.setdefault("dir", str(position))
        name = validated_feed_dir(feed["dir"])
        if name in seen:
            raise ValueError(f"duplicate feed dir: {name!r}")
        seen.add(name)
    return feeds


def reconcile_storage(root, feeds: list[dict], slots: int) -> None:
    """Remove captures outside the configured feeds and slot range."""
    root = Path(root)
    configured = {validated_feed_dir(feed["dir"]) for feed in feeds}
    for entry in root.iterdir():
        if entry.name not in configured:
            if entry.is_symlink():
                entry.unlink()
            elif entry.is_dir():
                shutil.rmtree(entry)
            continue
        if entry.is_symlink():
            entry.unlink()
            continue
        if not entry.is_dir():
            continue
        for candidate in entry.iterdir():
            if candidate.suffix == ".jpg" and candidate.stem.isdigit():
                if int(candidate.stem) >= slots:
                    candidate.unlink()


def prune_stale(root, feeds: list[dict], now: float, max_age_seconds: float) -> None:
    """Remove ring frames of configured feeds older than max_age_seconds; 0 keeps all."""
    if max_age_seconds <= 0:
        return
    cutoff = now - max_age_seconds
    for feed in feeds:
        feed_dir = Path(root) / validated_feed_dir(feed["dir"])
        if not feed_dir.is_dir():
            continue
        for candidate in feed_dir.iterdir():
            if candidate.suffix != ".jpg" or not candidate.stem.isdigit():
                continue
            try:
                if candidate.stat().st_mtime < cutoff:
                    candidate.unlink()
            except FileNotFoundError:
                continue


def thumbnail(path) -> list[int]:
    """Downscale a frame to a small grayscale pixel list for comparison."""
    with Image.open(path) as img:
        return list(img.convert("L").resize((THUMB_SIZE, THUMB_SIZE)).tobytes())


def mean_abs_diff(a: list[int], b: list[int]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


@dataclass
class FeedState:
    """In-memory detector state for one feed."""

    active: bool = True
    static_run: int = 0
    failure_run: int = 0
    last_thumb: list[int] | None = field(default=None, repr=False)
    last_probe: float = 0.0


def should_attempt(state: FeedState, now: float) -> bool:
    """Active feeds grab every cycle; inactive feeds are probed once daily."""
    if state.active:
        return True
    return now - state.last_probe >= PROBE_INTERVAL_SECONDS


def record_success(
    state: FeedState,
    thumb: list[int],
    threshold: float = DEFAULT_STATIC_THRESHOLD,
) -> None:
    """Fold a successful grab into the detector state."""
    state.failure_run = 0
    diff = None if state.last_thumb is None else mean_abs_diff(thumb, state.last_thumb)
    static = diff is not None and diff < threshold
    if state.active:
        if static:
            state.static_run += 1
            if state.static_run >= STATIC_LIMIT:
                state.active = False
        else:
            state.static_run = 0
    elif not static:
        state.active = True
        state.static_run = 0
    state.last_thumb = thumb


def record_failure(state: FeedState) -> None:
    """Fold a failed grab into the detector state."""
    state.failure_run += 1
    if state.active and state.failure_run >= FAILURE_LIMIT:
        state.active = False


def apply_outcomes(
    outcomes: list[tuple[FeedState, list[int] | None]],
    threshold: float = DEFAULT_STATIC_THRESHOLD,
) -> bool:
    """Update detector state for one cycle's grab outcomes.

    When more than one feed was attempted and every one failed in the same
    cycle the fault is on the capture side, not the feeds: nothing is counted
    and False is returned. A single attempted feed failing alone points at
    the feed, so it is always counted.
    """
    if len(outcomes) > 1 and all(thumb is None for _, thumb in outcomes):
        return False
    for state, thumb in outcomes:
        if thumb is None:
            record_failure(state)
        else:
            record_success(state, thumb, threshold)
    return True


def write_status(root, states: dict[str, FeedState]) -> None:
    """Atomically publish per-feed activity, and nothing else, at the root."""
    payload = {name: "active" if state.active else "inactive" for name, state in states.items()}
    root = Path(root)
    tmp = root / "status.json.tmp"
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, root / "status.json")


def resolve_manifest(video_id: str) -> str | None:
    """Resolve a feed's current stream manifest URL; None on failure."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    try:
        result = subprocess.run(
            ["yt-dlp", "-g", "-f", "best[height<=720]", url],
            capture_output=True,
            text=True,
            timeout=RESOLVE_TIMEOUT_SECONDS,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return lines[0] if lines else None


def grab_frame(manifest: str, out_path, vf: str | None = None) -> bool:
    """Capture a single frame from a manifest to out_path; False on failure."""
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        manifest,
        "-frames:v",
        "1",
        "-q:v",
        "3",
    ]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-y", str(out_path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=GRAB_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0 and Path(out_path).exists()


def grab_feed(
    feed: dict,
    root,
    now: float,
    interval_minutes: int,
    slots: int,
) -> list[int] | None:
    """Grab one frame for a feed into its ring slot; the thumbnail on success."""
    feed_dir = Path(root) / validated_feed_dir(feed["dir"])
    feed_dir.mkdir(parents=True, exist_ok=True)
    tmp = feed_dir / ".grab.jpg"
    # Clear any temporary a hard kill left behind, even when this attempt
    # fails before writing one.
    tmp.unlink(missing_ok=True)
    manifest = resolve_manifest(feed["id"])
    if manifest is None:
        return None
    if not grab_frame(manifest, tmp, feed.get("vf")):
        tmp.unlink(missing_ok=True)
        return None
    try:
        thumb = thumbnail(tmp)
    except OSError:
        tmp.unlink(missing_ok=True)
        return None
    os.replace(tmp, feed_dir / slot_name(slot_index(now, interval_minutes, slots)))
    return thumb


def run_cycle(
    feeds: list[dict],
    states: dict[str, FeedState],
    root,
    now: float,
    interval_minutes: int,
    slots: int,
    threshold: float,
    max_age_seconds: float = 0.0,
) -> None:
    """Grab every due feed once, spaced across the interval; then update
    detector state, prune old frames, and publish status.

    Detector bookkeeping is anchored to the cycle's start; each grab happens
    at its feed's offset into the interval, measured from the interval
    boundary, so every frame lands in the slot the cycle began in. A cycle
    that starts partway through an interval grabs at once the feeds whose
    offsets have already passed.
    """
    outcomes: list[tuple[FeedState, list[int] | None]] = []
    probed: list[FeedState] = []
    period = interval_minutes * 60
    boundary = now - now % period
    for position, feed in enumerate(feeds):
        state = states.setdefault(feed["dir"], FeedState())
        if not should_attempt(state, now):
            continue
        if not state.active:
            probed.append(state)
        grab_at = max(now, boundary + feed_offset(position, len(feeds), period))
        pause_until(grab_at)
        outcomes.append((state, grab_feed(feed, root, grab_at, interval_minutes, slots)))
    if apply_outcomes(outcomes, threshold):
        # A cycle voided by the capture-side guard counts nothing, so it
        # does not consume an inactive feed's daily probe either.
        for state in probed:
            state.last_probe = now
    prune_stale(root, feeds, now, max_age_seconds)
    write_status(root, states)


def feed_offset(position: int, count: int, period: float) -> float:
    """Seconds into the interval at which the feed at position is grabbed."""
    return period * position / count


def pause_until(moment: float) -> None:
    """Sleep until moment; return at once when it has passed."""
    delay = moment - time.time()
    if delay > 0:
        time.sleep(delay)


def next_wake(cycle_start: float, period: float) -> float:
    """First moment past the interval boundary after the one holding cycle_start."""
    return cycle_start - (cycle_start % period) + period + 1


def main() -> None:
    feeds = load_feeds(os.environ.get("SENSE_FEEDS", "[]"))
    interval_minutes = int(os.environ.get("SENSE_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES)))
    slots = int(os.environ.get("SENSE_RING_SLOTS", str(DEFAULT_RING_SLOTS)))
    threshold = float(os.environ.get("SENSE_STATIC_THRESHOLD", str(DEFAULT_STATIC_THRESHOLD)))
    max_age_hours = float(os.environ.get("SENSE_MAX_AGE_HOURS", str(DEFAULT_MAX_AGE_HOURS)))
    root = Path(SENSE_DIR)
    try:
        reconcile_storage(root, feeds, slots)
    except OSError as error:
        # Startup tidying, not a precondition: a volume that cannot be read or
        # written must not fail here either, or the guard below never runs and
        # the container restarts forever. It is attempted once, as before.
        print(f"reconcile failed: {error}", file=sys.stderr, flush=True)
    states: dict[str, FeedState] = {}
    period = interval_minutes * 60
    while True:
        start = time.time()
        try:
            run_cycle(
                feeds,
                states,
                root,
                start,
                interval_minutes,
                slots,
                threshold,
                max_age_seconds=max_age_hours * 3600,
            )
        except OSError as error:
            # A misconfigured or unwritable volume must not become a silent
            # crash-restart loop; report and retry at the normal cadence.
            print(f"cycle failed: {error}", file=sys.stderr, flush=True)
        # Schedule from the cycle's start rather than its end, so a cycle
        # that overruns its interval is followed immediately by one that
        # fills the slot the overrun would otherwise skip.
        time.sleep(max(next_wake(start, period) - time.time(), 1))


if __name__ == "__main__":
    main()
