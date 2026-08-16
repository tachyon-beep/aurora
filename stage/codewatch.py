"""Per-edit diff excerpts over the mirrored agent source.

The stage remembers the previously observed agent.py lines in memory and, when
the mirror changes, keeps a capped unified-diff excerpt of that single edit.
The recorded epoch is when the stage saw the change, not the file mtime; the
page words it accordingly. A change that leaves agent.py identical to the
mirrored seed is marked restored: the harness rewrites the file to the seed
after a death and on recovery, and the agent's own reset tool does the same,
so the page names the outcome rather than an author.

Observation runs on a background daemon thread on the mirror's cadence; the
request path only ever reads the cached edit. The diff itself is computed
outside the lock, over a capped read, so agent-authored source of any shape
can neither block a request nor hold other observers.
"""

import difflib
import os
import threading
import time

from stage import data

MAX_SOURCE_BYTES = 262_144
EXCERPT_LINES = 14
EXCERPT_LINE_CHARS = 120
EXCERPT_CAP = 1600
POLL_SECONDS = 5

_LOCK = threading.Lock()
_MEMO = {"key": None, "lines": None, "edit": None}
_START_LOCK = threading.Lock()
_THREAD = None
_STARTED = False


def cached_edit():
    """A copy of the most recently observed edit, or None. Never reads a file."""
    with _LOCK:
        return _current_edit()


def latest_edit(work_dir, now=None):
    """Observe work_dir/agent.py once and return the most recent edit, or None.

    Observes through data.contained_file. The first observation records state
    and returns the previous edit (None initially); a later observation whose
    (realpath, mtime_ns, size) key differs diffs the remembered lines against
    the current ones and replaces the stored edit. An unreadable or
    uncontained source leaves the state untouched. The diff runs outside the
    lock; a concurrent observation of a newer version wins the store.
    """
    path = data.contained_file(work_dir, os.path.join(work_dir, "agent.py"))
    if path is None:
        return cached_edit()
    key = _key(path)
    with _LOCK:
        if key is None or key == _MEMO["key"]:
            return _current_edit()
        previous = _MEMO["lines"]
    lines = _read_lines(path)
    if lines is None:
        return cached_edit()
    edit = None
    if previous is not None:
        edit = _describe(previous, lines, now)
        if edit is not None:
            edit["restored"] = lines == _seed_lines(work_dir)
    with _LOCK:
        if _MEMO["lines"] is not previous:
            return _current_edit()
        if edit is not None:
            _MEMO["edit"] = edit
        _MEMO["key"] = key
        _MEMO["lines"] = lines
        return _current_edit()


def _seed_lines(work_dir):
    """The mirrored agent_stock.py lines, or None when they cannot be read."""
    seed = data.contained_file(work_dir, os.path.join(work_dir, "agent_stock.py"))
    if seed is None:
        return None
    return _read_lines(seed)


def _current_edit():
    """A copy of the stored edit, or None. Callers hold _LOCK."""
    edit = _MEMO["edit"]
    return dict(edit) if edit is not None else None


def _key(path):
    """(realpath, mtime_ns, size) for path, or None when it cannot be stated."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return (path, stat.st_mtime_ns, stat.st_size)


def _read_lines(path):
    """The capped source lines of path, or None when it cannot be read."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(MAX_SOURCE_BYTES).splitlines()
    except OSError:
        return None


def _describe(previous, current, now):
    """A capped description of the change from previous to current, or None.

    added and removed count over the whole diff; the excerpt keeps the first
    EXCERPT_LINES body lines (hunk markers, additions, removals, context),
    each clipped to EXCERPT_LINE_CHARS, the join clipped to EXCERPT_CAP.
    Only the two file-header lines unified_diff always emits first are
    skipped, so a source line that itself begins with -- or ++ still counts.
    """
    added = removed = 0
    body = []
    diff = difflib.unified_diff(previous, current, lineterm="", n=1)
    for position, line in enumerate(diff):
        if position < 2:
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
        if len(body) < EXCERPT_LINES:
            body.append(line[:EXCERPT_LINE_CHARS])
    if not body:
        return None
    return {
        "epoch": now if now is not None else time.time(),
        "added": added,
        "removed": removed,
        "excerpt": "\n".join(body)[:EXCERPT_CAP],
    }


def _loop(work_dir):
    """The background observer; every iteration swallows its own failures."""
    while True:
        try:
            latest_edit(work_dir)
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def start_background_refresh(work_dir):
    """Start the observer thread once."""
    global _THREAD, _STARTED
    with _START_LOCK:
        if _STARTED:
            return None
        thread = threading.Thread(
            target=_loop, args=(work_dir,), daemon=True, name="stage-codewatch"
        )
        _THREAD = thread
        _STARTED = True
        thread.start()
    return None


def _reset_for_tests():
    """Clear module state so a test can exercise a fresh watcher."""
    global _THREAD, _STARTED
    with _LOCK:
        _MEMO.update({"key": None, "lines": None, "edit": None})
    with _START_LOCK:
        _THREAD = None
        _STARTED = False
