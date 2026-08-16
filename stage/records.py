"""Cross-life records aggregated from the mirrored tombstones.

Consecutive death epochs bound a life: the life with ordinal i+1 ran from
deaths[i] to deaths[i+1], oldest first. The first life has no recorded birth,
so it is excluded from the longest-life record.

The book is memoized on the tombstone set: tombstones are write-once and only
accumulate, so the per-file reads and stats run once per new death rather
than once per public request.
"""

import os
import threading
import time

from stage import data

TOMBSTONE_READ_BYTES = 4096

_LOCK = threading.Lock()
_MEMO = {"key": None, "value": None}


def _memo_key(work_dir, paths):
    """The tombstone set's identity: directory mtime, count, and newest name."""
    try:
        dir_mtime = os.stat(os.path.join(work_dir, "tombstones")).st_mtime_ns
    except OSError:
        dir_mtime = None
    return (dir_mtime, len(paths), paths[-1] if paths else None)


def record_book(work_dir, now=None):
    """Records across every mirrored tombstone: counts, longest life, last gap."""
    if now is None:
        now = time.time()
    paths = list(reversed(data.tombstone_paths(work_dir)))
    key = _memo_key(work_dir, paths)
    with _LOCK:
        if _MEMO["key"] == key and _MEMO["value"] is not None:
            return dict(_MEMO["value"])
    value = _compute(work_dir, paths, now)
    with _LOCK:
        _MEMO["key"] = key
        _MEMO["value"] = value
    return dict(value)


def _reset_for_tests():
    """Clear the memo so a test can exercise a fresh book."""
    with _LOCK:
        _MEMO.update({"key": None, "value": None})


def _compute(work_dir, paths, now):
    """The record book over paths, oldest first."""
    deaths = [data._tombstone_epoch(path, now) for path in paths]
    chose = 0
    for path in paths:
        real = data.contained_file(work_dir, path)
        if real is None:
            continue
        try:
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(TOMBSTONE_READ_BYTES)
        except OSError:
            continue
        if data._ending_kind(text) == "declared":
            chose += 1
    longest = None
    for index in range(1, len(deaths)):
        began = deaths[index - 1]
        ended = deaths[index]
        if began is None or ended is None or ended <= began:
            continue
        seconds = float(ended - began)
        if longest is None or seconds > longest["seconds"]:
            longest = {"ordinal": index + 1, "seconds": seconds}
    gap = None
    if len(deaths) >= 2:
        last, prior = deaths[-1], deaths[-2]
        if last is not None and prior is not None and last > prior:
            gap = float(last - prior)
    return {
        "lives_ended": len(paths),
        "chose": chose,
        "longest_life": longest,
        "most_recent_gap_seconds": gap,
    }
