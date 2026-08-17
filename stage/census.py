"""Per-life counts over the whole live transcript, for the stream page.

The page's figures come from a bounded tail read, so a long life saturates
its turn and self-edit counts. The census walks the console's per-line
transcript index instead — every core-stream entry the live file holds — and
caches ``diag.incarnations`` on a background daemon thread; the request path
only ever reads the cache. The first scan of a large transcript therefore
happens on the thread, never inside a request. Counts cover only what the
live transcript holds: after the recorder rotates the file they restart from
its new content, which is the same truth the console shows.
"""

import threading
import time

from stage import diag

POLL_SECONDS = 10

_LOCK = threading.Lock()
_MEMO = {"lives": None, "taken_at": None}
_START_LOCK = threading.Lock()
_THREAD = None
_STARTED = False


def cached_lives():
    """A copy of the newest census (newest life first), or None before the first pass."""
    with _LOCK:
        lives = _MEMO["lives"]
        return [dict(life) for life in lives] if lives is not None else None


def cached_life(ordinal):
    """The cached census row for one ordinal, or None."""
    with _LOCK:
        lives = _MEMO["lives"]
        if lives is None:
            return None
        for life in lives:
            if life.get("ordinal") == ordinal:
                return dict(life)
    return None


def refresh_once(transcript_path, work_dir, now=None):
    """Take the census once and store it; returns the new list."""
    if now is None:
        now = time.time()
    lives = diag.incarnations(transcript_path, work_dir, now=now)
    with _LOCK:
        _MEMO["lives"] = [dict(life) for life in lives]
        _MEMO["taken_at"] = now
    return cached_lives()


def _loop(transcript_path, work_dir):
    """The background census; every iteration swallows its own failures."""
    while True:
        try:
            refresh_once(transcript_path, work_dir)
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def start_background_refresh(transcript_path, work_dir):
    """Start the census thread once."""
    global _THREAD, _STARTED
    with _START_LOCK:
        if _STARTED:
            return None
        thread = threading.Thread(
            target=_loop, args=(transcript_path, work_dir), daemon=True, name="stage-census"
        )
        _THREAD = thread
        _STARTED = True
        thread.start()
    return None


def _reset_for_tests():
    """Clear module state so a test can exercise a fresh census."""
    global _THREAD, _STARTED
    with _LOCK:
        _MEMO.update({"lives": None, "taken_at": None})
    with _START_LOCK:
        _THREAD = None
        _STARTED = False
