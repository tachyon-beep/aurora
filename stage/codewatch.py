"""Per-edit diff excerpts over the mirrored agent source.

The stage remembers the previously observed agent.py lines in memory and, when
the mirror changes, keeps a capped unified-diff excerpt of that single edit.
The recorded epoch is when the stage saw the change, not the file mtime; the
page words it accordingly.
"""

import difflib
import os
import threading
import time

from stage import data

MAX_SOURCE_BYTES = 1_048_576
EXCERPT_LINES = 14
EXCERPT_LINE_CHARS = 120
EXCERPT_CAP = 1600

_LOCK = threading.Lock()
_MEMO = {"key": None, "lines": None, "edit": None}


def latest_edit(work_dir, now=None):
    """The most recently observed edit to the mirrored agent.py, or None.

    Observes work_dir/agent.py through data.contained_file. The first
    observation records state and returns the previous edit (None initially);
    a later observation whose (realpath, mtime_ns, size) key differs diffs the
    remembered lines against the current ones and replaces the stored edit.
    An unreadable or uncontained source leaves the state untouched.
    """
    path = data.contained_file(work_dir, os.path.join(work_dir, "agent.py"))
    with _LOCK:
        if path is None:
            return _current_edit()
        key = _key(path)
        if key is None or key == _MEMO["key"]:
            return _current_edit()
        lines = _read_lines(path)
        if lines is None:
            return _current_edit()
        previous = _MEMO["lines"]
        if previous is not None:
            edit = _describe(previous, lines, now)
            if edit is not None:
                _MEMO["edit"] = edit
        _MEMO["key"] = key
        _MEMO["lines"] = lines
        return _current_edit()


def _current_edit():
    """A copy of the stored edit, or None."""
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
    """
    added = removed = 0
    body = []
    for line in difflib.unified_diff(previous, current, lineterm="", n=1):
        if line.startswith("+++") or line.startswith("---"):
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


def _reset_for_tests():
    """Clear module state so a test can exercise a fresh watcher."""
    with _LOCK:
        _MEMO.update({"key": None, "lines": None, "edit": None})
