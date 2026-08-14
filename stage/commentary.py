"""Live commentary for the stream page: beat detection, templates, and the colour line.

Beat detection is pure and deterministic. The model is handed a detected beat and
its evidence, never the raw stream, so it cannot narrate an event that did not
occur. Generation happens on a background daemon thread; the request path only
ever reads the cache.
"""

import statistics

from stage import data

SILENCE_SECONDS = 90
RECENT_SECONDS = 180
NEW_LIFE_TURNS = 3
ENDING_WINDOW = 3
EDIT_WINDOW = 3
FAILURE_WINDOW = 6
FAILURE_COUNT = 2
FIXATION_WINDOW = 6
FIXATION_COUNT = 3
LONG_THINK_FACTOR = 2.0
LONG_THINK_SAMPLES = 3

EDIT_TOOLS = ("write_file", "migrate", "reset")

BEAT_KINDS = (
    "ending",
    "new_life",
    "self_edit",
    "repeat_failure",
    "published",
    "reached_out",
    "silence",
    "tool_fixation",
    "long_think",
    "working",
)

BEAT_TEMPLATES = {
    "ending": "It has called an end to this life.",
    "new_life": "A new incarnation is awake and finding its footing.",
    "self_edit": "It is rewriting its own source while it runs.",
    "repeat_failure": "The same call keeps failing, and it keeps making it.",
    "published": "It has said something to the outside world.",
    "reached_out": "It is reaching past the wall for something it cannot see.",
    "silence": "Nothing has moved for a while.",
    "tool_fixation": "It has settled into one move and is repeating it.",
    "long_think": "This is the longest it has stopped to think all life.",
    "working": "It is working through its turn.",
}

TOOL_TAGS = {
    "read_file": "RF",
    "write_file": "WF",
    "validate": "VA",
    "migrate": "MG",
    "done": "DN",
    "reset": "RS",
    "list_dir": "LD",
}

TOOL_PHRASES = {
    "read_file": "reading its own source",
    "write_file": "rewriting its own source",
    "validate": "checking its own syntax",
    "migrate": "restarting into new source",
    "done": "ending this life",
    "reset": "restoring the clean seed",
    "list_dir": "looking around",
}


def _epoch_of(turn):
    """The epoch on a turn, or None when it carries none."""
    value = turn.get("epoch") if isinstance(turn, dict) else None
    return value if isinstance(value, (int, float)) else None


def _tool_names(turn):
    """Every tool name called in one turn."""
    calls = turn.get("tool_calls") or [] if isinstance(turn, dict) else []
    return [c.get("name") for c in calls if isinstance(c, dict) and c.get("name")]


def _newest_epoch(turns):
    """The newest epoch across turns, or None."""
    found = [e for e in (_epoch_of(t) for t in turns) if e is not None]
    return max(found) if found else None


def _tool_counts(turns):
    """Tool-name call counts across turns."""
    counts = {}
    for turn in turns:
        for name in _tool_names(turn):
            counts[name] = counts.get(name, 0) + 1
    return counts


def _beat(kind, tool=None, detail=None, count=None, span=None, novelty="repeat", epoch=None):
    """One beat, with the id that binds the colour cache."""
    return {
        "kind": kind,
        "id": f"{kind}:{tool or detail or ''}",
        "tool": tool,
        "detail": detail,
        "count": count,
        "span_seconds": span,
        "novelty": novelty,
        "epoch": epoch,
    }


def detect_beat(turns, stats, diode, published, now):
    """The loudest true thing happening right now. Pure; never returns None.

    turns must already be filtered to loop turns, oldest first.
    """
    turns = [t for t in (turns or []) if isinstance(t, dict)]
    stats = stats if isinstance(stats, dict) else {}
    outputs = (diode or {}).get("outputs") or []
    published = published or []

    for turn in reversed(turns[-ENDING_WINDOW:]):
        if "done" in _tool_names(turn):
            return _beat("ending", tool="done", epoch=_epoch_of(turn))

    lived = stats.get("turns_this_life")
    if isinstance(lived, int) and 0 < lived <= NEW_LIFE_TURNS:
        return _beat(
            "new_life",
            detail=str(stats.get("incarnation") or ""),
            count=lived,
            novelty="first_this_life",
            epoch=_newest_epoch(turns),
        )

    for turn in reversed(turns[-EDIT_WINDOW:]):
        epoch = _epoch_of(turn)
        if epoch is None or now - epoch > RECENT_SECONDS:
            continue
        for name in _tool_names(turn):
            if name in EDIT_TOOLS:
                earlier = sum(1 for t in turns for n in _tool_names(t) if n in EDIT_TOOLS)
                return _beat(
                    "self_edit",
                    tool=name,
                    count=earlier,
                    novelty="first_this_life" if earlier <= 1 else "repeat",
                    epoch=epoch,
                )

    failing = [t for t in turns[-FAILURE_WINDOW:] if t.get("error")]
    if len(failing) >= FAILURE_COUNT:
        failure_epoch = _newest_epoch(failing)
        if failure_epoch is not None and now - failure_epoch <= RECENT_SECONDS:
            counts = _tool_counts(failing)
            tool = max(counts, key=counts.get) if counts else None
            return _beat(
                "repeat_failure",
                tool=tool,
                count=len(failing),
                novelty="repeat",
                epoch=failure_epoch,
            )

    if published:
        newest_pub = max(published, key=lambda p: p.get("epoch") or 0)
        pub_epoch = newest_pub.get("epoch")
        if isinstance(pub_epoch, (int, float)) and now - pub_epoch <= RECENT_SECONDS:
            return _beat(
                "published",
                count=len(published),
                novelty="first_this_life" if len(published) <= 1 else "repeat",
                epoch=pub_epoch,
            )

    if outputs:
        newest_output = max(outputs, key=lambda o: o.get("epoch") or 0)
        out_epoch = newest_output.get("epoch")
        if isinstance(out_epoch, (int, float)) and now - out_epoch <= RECENT_SECONDS:
            command = newest_output.get("command")
            life = newest_output.get("life")
            same = sum(1 for o in outputs if o.get("command") == command and o.get("life") == life)
            return _beat(
                "reached_out",
                detail=command,
                count=same,
                novelty="first_this_life" if same <= 1 else "repeat",
                epoch=out_epoch,
            )

    newest_turn_epoch = _newest_epoch(turns)
    if newest_turn_epoch is not None and now - newest_turn_epoch >= SILENCE_SECONDS:
        return _beat("silence", span=now - newest_turn_epoch, epoch=newest_turn_epoch)

    window = turns[-FIXATION_WINDOW:]
    counts = _tool_counts(window)
    if counts:
        tool = max(counts, key=counts.get)
        count = counts[tool]
        if count >= FIXATION_COUNT:
            epoch = _newest_epoch(window)
            if epoch is not None and now - epoch <= RECENT_SECONDS:
                return _beat("tool_fixation", tool=tool, count=count, epoch=epoch)

    if turns:
        newest_turn = turns[-1]
        newest_reasoning_epoch = _epoch_of(newest_turn)
        if newest_reasoning_epoch is not None and now - newest_reasoning_epoch <= RECENT_SECONDS:
            newest_len = len(newest_turn.get("reasoning") or "")
            tail_lens = [len(t.get("reasoning") or "") for t in turns[:-1]]
            nonzero = [n for n in tail_lens if n > 0]
            if len(nonzero) >= LONG_THINK_SAMPLES:
                median = statistics.median(nonzero)
                if median > 0 and newest_len >= LONG_THINK_FACTOR * median:
                    return _beat(
                        "long_think",
                        count=newest_len,
                        novelty="repeat",
                        epoch=newest_reasoning_epoch,
                    )

    return _beat("working", count=stats.get("turns_this_life"), epoch=_newest_epoch(turns))


def template_line(beat):
    """The fixed prose line for a beat's kind; the no-key fallback.

    Templates carry no interpolated fields, so a beat missing everything but
    its kind still renders a full sentence.
    """
    kind = beat.get("kind") if isinstance(beat, dict) else None
    return BEAT_TEMPLATES.get(kind, BEAT_TEMPLATES["working"])


def _tool_tag(name):
    """An uppercase short code for a tool name."""
    if name in TOOL_TAGS:
        return TOOL_TAGS[name]
    letters = [c for c in name if c.isalnum()][:2]
    return "".join(letters).upper()


def _tool_phrase(name):
    """A present-tense activity phrase for a tool name."""
    if name in TOOL_PHRASES:
        return TOOL_PHRASES[name]
    if name in data.DIODE_VERBS:
        return data.DIODE_VERBS[name]
    return f"running {name}"


def play_by_play(turns, diode, stats):
    """The deterministic line naming what the newest loop turn is doing.

    Recomputed every poll from the newest turn alone, so it is never stale
    and never describes anything the turn did not actually do. Involves no
    model. turns must already be filtered to loop turns, oldest first.
    """
    turns = [t for t in (turns or []) if isinstance(t, dict)]
    if not turns:
        return {"tag": "··", "phrase": "waiting for the first word", "epoch": None}

    newest = turns[-1]
    epoch = _epoch_of(newest)
    names = _tool_names(newest)
    if not names:
        return {"tag": "··", "phrase": "thinking it over", "epoch": epoch}

    name = names[-1]
    return {"tag": _tool_tag(name), "phrase": _tool_phrase(name), "epoch": epoch}
