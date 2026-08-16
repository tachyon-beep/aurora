"""Cross-life records aggregated from the mirrored tombstones.

Consecutive death epochs bound a life: the life with ordinal i+1 ran from
deaths[i] to deaths[i+1], oldest first. The first life has no recorded birth,
so it is excluded from the longest-life record.
"""

import time

from stage import data

TOMBSTONE_READ_BYTES = 4096


def record_book(work_dir, now=None):
    """Records across every mirrored tombstone: counts, longest life, last gap."""
    if now is None:
        now = time.time()
    paths = list(reversed(data.tombstone_paths(work_dir)))
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
