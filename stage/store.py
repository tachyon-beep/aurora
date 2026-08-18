"""Stage-private persistence for generated prose.

The stage's LLM-generated records — per-life digests, desk verdicts, the
recap — are otherwise held only in memory and cost spend to regenerate; a
life whose transcript entries have rotated away can never be re-digested at
all. This module writes them to the stage-state volume, which is mounted
into the stage alone: nothing here is a channel to any other container, and
nothing here is served that the page does not already show.

Documents are single JSON objects, written atomically beside their final
name and renamed into place, so a reader never sees a partial write and a
failed write never damages the previous document. Every entry point returns
rather than raises: persistence is a convenience layered under the page,
and losing it must never take the page down.
"""

import json
import os

DEFAULT_STATE_DIR = "/stage-state"
MAX_STATE_BYTES = 1_048_576


def state_dir():
    """The state directory named by STAGE_STATE_DIR, defaulting to the volume."""
    return (os.environ.get("STAGE_STATE_DIR") or "").strip() or DEFAULT_STATE_DIR


def save(name, payload):
    """Atomically replace one named document; False on any failure."""
    directory = state_dir()
    path = os.path.join(directory, name + ".json")
    temporary = path + ".tmp"
    try:
        raw = json.dumps(payload)
    except (TypeError, ValueError):
        return False
    if len(raw.encode("utf-8")) > MAX_STATE_BYTES:
        return False
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(temporary, path)
    except OSError:
        try:
            os.remove(temporary)
        except OSError:
            pass
        return False
    return True


def load(name):
    """One named document as a dict, or None when absent, oversized, or damaged."""
    path = os.path.join(state_dir(), name + ".json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read(MAX_STATE_BYTES + 1)
    except OSError:
        return None
    if len(raw) > MAX_STATE_BYTES:
        return None
    try:
        payload = json.loads(raw)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None
