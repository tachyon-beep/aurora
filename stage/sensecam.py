"""Read-only view over the sense service's frame ring for the stream page.

The ring is a set of digit-named feed directories under the sense mount, each
holding numbered .jpg slot files. Dot-prefixed names are the capture service's
short-lived temporaries and are never treated as frames. Every path handed
back resolves through data.contained_file, so a link out of the mount is
rejected before anything is read.
"""

import os
import re
import time

from stage import data

FRESH_SECONDS = 2700
FRAME_MAX_BYTES = 2_000_000

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


def newest_frame(sense_dir, now=None):
    """The newest fresh frame across the ring, or None.

    Freshness is bounded by FRESH_SECONDS so a stalled capture service stops
    being shown rather than presenting an old frame as current.
    """
    now = time.time() if now is None else now
    best = None
    for feed in _feed_names(sense_dir):
        feed_dir = os.path.join(sense_dir, feed)
        try:
            names = os.listdir(feed_dir)
        except OSError:
            continue
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
            if best is None or mtime > best[2]:
                best = (feed, name, mtime)
    if best is None or now - best[2] > FRESH_SECONDS:
        return None
    return {"feed": best[0], "name": best[1], "captured_epoch": best[2]}


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
