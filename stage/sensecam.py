"""Read-only view over the sense service's frame ring for the stream page.

The ring is a set of digit-named feed directories under the sense mount, each
holding numbered .jpg slot files. Dot-prefixed names are the capture service's
short-lived temporaries and are never treated as frames. Every path handed
back resolves through data.contained_file, so a link out of the mount is
rejected before anything is read.
"""

import json
import os
import re

from stage import data

FRESH_SECONDS = 2700
FRAME_MAX_BYTES = 2_000_000
STATUS_MAX_BYTES = 65_536
STATUS_STATES = ("active", "inactive")

# A copy of the vocabulary sense.validated_feed_dir writes. The capture
# service and the stage are separate images and share no import, so the
# pattern is duplicated here rather than reached for; a test binds the two,
# because widening one side alone strands frames the other never serves.
# str.isdigit is not equivalent: it also admits non-ASCII digits.
FEED_DIR_PATTERN = re.compile(r"[0-9]+")


def _feed_names(sense_dir):
    """The numeral-named child directories of sense_dir, from its own listing."""
    try:
        listing = os.listdir(sense_dir)
    except OSError:
        return []
    feeds = []
    for name in sorted(listing):
        if FEED_DIR_PATTERN.fullmatch(name) is None:
            continue
        full = os.path.join(sense_dir, name)
        if os.path.isdir(full) and not os.path.islink(full):
            feeds.append(name)
    return feeds


def _is_frame_name(name):
    """Whether a listed name is a finished frame rather than a temporary."""
    return name.endswith(".jpg") and not name.startswith(".")


def frame_bytes_path(sense_dir, feed, name):
    """A servable path for one frame, or None.

    Both segments are matched against directory listings before any path is
    joined, so a request string never reaches the filesystem; the joined path
    still resolves through contained_file, and the size is capped.

    This is a pre-filter, not the boundary. The caller serves the frame through
    a descriptor opened by data.open_contained, which repeats the containment,
    regular-file and size tests against the object it actually holds, so a name
    swapped between this check and that open changes nothing.
    """
    if feed not in _feed_names(sense_dir):
        return None
    feed_dir = os.path.join(sense_dir, feed)
    try:
        names = os.listdir(feed_dir)
    except OSError:
        return None
    if not _is_frame_name(name) or name not in names:
        return None
    target = data.contained_file(sense_dir, os.path.join(feed_dir, name))
    if target is None:
        return None
    try:
        if os.path.getsize(target) > FRAME_MAX_BYTES:
            return None
    except OSError:
        return None
    return target


def _newest_in_feed(sense_dir, feed):
    """The newest frame in one feed directory, or None."""
    feed_dir = os.path.join(sense_dir, feed)
    try:
        names = os.listdir(feed_dir)
    except OSError:
        return None
    best = None
    for name in names:
        if not _is_frame_name(name):
            continue
        target = data.contained_file(sense_dir, os.path.join(feed_dir, name))
        if target is None:
            continue
        try:
            mtime = os.path.getmtime(target)
        except OSError:
            continue
        if best is None or mtime > best[1]:
            best = (name, mtime)
    if best is None:
        return None
    return {"name": best[0], "captured_epoch": best[1]}


def newest_frames(sense_dir):
    """The newest frame in every feed directory, keyed by feed name.

    No freshness bound: the senses page shows a stale feed as stale rather
    than dropping it, so the caller gets every feed's last capture and its
    age, and decides how to present it.
    """
    out = {}
    for feed in _feed_names(sense_dir):
        newest = _newest_in_feed(sense_dir, feed)
        if newest is not None:
            out[feed] = newest
    return out


def feed_status(sense_dir):
    """The capture service's per-feed status, validated field by field.

    status.json carries a closed vocabulary ({feed: {"state", "lively"}}),
    but the mount is treated as untrusted anyway: the file is size-capped,
    feed names must match the ring's own pattern, and each field is kept
    only in its published shape — anything else becomes None or is dropped.
    """
    target = data.contained_file(sense_dir, os.path.join(sense_dir, "status.json"))
    if target is None:
        return {}
    try:
        if os.path.getsize(target) > STATUS_MAX_BYTES:
            return {}
        with open(target, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for feed, entry in raw.items():
        if not isinstance(feed, str) or FEED_DIR_PATTERN.fullmatch(feed) is None:
            continue
        if not isinstance(entry, dict):
            continue
        state = entry.get("state")
        if state not in STATUS_STATES:
            state = None
        lively = entry.get("lively")
        if isinstance(lively, bool) or not isinstance(lively, (int, float)):
            lively = None
        out[feed] = {"state": state, "lively": lively}
    return out
