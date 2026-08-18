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

from stage import diag, store

FIGURE_KEYS = ("turns", "subcalls", "errors", "edits")

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


def _valid_figures(entry):
    """A stored figure row, or None when any field is malformed."""
    if not isinstance(entry, dict):
        return None
    cleaned = {}
    for key in FIGURE_KEYS:
        value = entry.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        cleaned[key] = value
    return cleaned


def _merge_figures(lives):
    """Overlay persisted figures onto dead lives; persist what this pass measured.

    A dead life's true counts are fixed, so the highest measurement wins: the
    live transcript either still covers the life or has rotated it down to
    zero. Figures are keyed by tombstone label, which survives renumbering;
    labels no longer in the mirror are pruned on save. A pass whose counts are
    inexact (an undatable tombstone, an unplaceable entry) overlays but never
    persists, so misplaced counts are not frozen.
    """
    doc = store.load("census")
    stored = (
        doc.get("figures") if isinstance(doc, dict) and isinstance(doc.get("figures"), dict) else {}
    )
    exact = bool(lives) and all(life.get("exact") for life in lives)
    figures = {}
    for life in lives:
        label = life.get("label")
        if life.get("current") or not isinstance(label, str) or not label:
            continue
        known = _valid_figures(stored.get(label))
        if known:
            for key in FIGURE_KEYS:
                life[key] = max(life[key], known[key])
        if exact:
            figures[label] = {key: life[key] for key in FIGURE_KEYS}
    if exact and figures != stored:
        store.save("census", {"figures": figures})
    return lives


def refresh_once(transcript_path, work_dir, now=None):
    """Take the census once and store it; returns the new list.

    Dead lives' figures are backed by the stage's own store, so a life whose
    transcript entries have rotated into an archive keeps the counts that
    were measured while they were still readable.
    """
    if now is None:
        now = time.time()
    lives = diag.incarnations(transcript_path, work_dir, now=now)
    try:
        lives = _merge_figures(lives)
    except Exception:
        pass
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
