"""Live commentary for the stream page: beat detection, templates, and the colour line.

Beat detection is pure and deterministic. The model is handed a detected beat and
its evidence, never the raw stream, so it cannot narrate an event that did not
occur. Generation happens on a background daemon thread; the request path only
ever reads the cache.
"""

import hashlib
import re
import statistics
import threading
import time

from stage import data, llm

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
    "spoke",
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
    "spoke": "It has put a voice to something.",
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


TOOL_NAME_CAP = 64


def _tool_names(turn):
    """Every tool name called in one turn, each capped at TOOL_NAME_CAP.

    A tool name is agent-controlled text: stage/data.py stores it verbatim
    from the recorded model response, with no closed vocabulary and no
    upstream truncation. Every downstream consumer of a name (the beat id,
    colour.beat, play.phrase, play.tag, and the tool-count tallies) reads it
    through this one function, so capping here bounds all of them at once.
    """
    calls = turn.get("tool_calls") or [] if isinstance(turn, dict) else []
    return [
        str(c.get("name"))[:TOOL_NAME_CAP] for c in calls if isinstance(c, dict) and c.get("name")
    ]


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


def working_beat_id():
    """The working beat's id, so a static placeholder never drifts from the live shape."""
    return _beat("working")["id"]


def detect_beat(turns, stats, diode, published, now, spoken=None):
    """The loudest true thing happening right now. Pure; never returns None.

    turns must already be filtered to loop turns, oldest first.
    """
    turns = [t for t in (turns or []) if isinstance(t, dict)]
    stats = stats if isinstance(stats, dict) else {}
    outputs = (diode or {}).get("outputs") or []
    published = published or []
    spoken = spoken or []

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

    if spoken:
        newest_spoken = max(spoken, key=lambda s: s.get("epoch") or 0)
        spoken_epoch = newest_spoken.get("epoch")
        if isinstance(spoken_epoch, (int, float)) and 0 <= now - spoken_epoch <= RECENT_SECONDS:
            return _beat(
                "spoke",
                count=len(spoken),
                novelty="first_this_life" if len(spoken) <= 1 else "repeat",
                epoch=spoken_epoch,
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
    """An uppercase short code for a tool name.

    A tool name is agent-controlled text, not necessarily a Python
    identifier, so it need not contain any alphanumeric character at all;
    that case falls back to the same sentinel used when there is no tool
    call to tag.
    """
    if name in TOOL_TAGS:
        return TOOL_TAGS[name]
    letters = [c for c in name if c.isalnum()][:2]
    return "".join(letters).upper() or "··"


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


def beat_evidence(beat):
    """The counted fact behind a beat, or "" when it counts nothing.

    Pure and deterministic, like detect_beat. The colour line beside this is a
    model's reading of the same beat; this is the measurement it is reading.
    """
    if not isinstance(beat, dict):
        return ""
    count = beat.get("count")
    tool = beat.get("tool")
    if beat.get("kind") == "tool_fixation" and isinstance(count, int) and isinstance(tool, str):
        return f"{tool} x{count} in a row"
    span = beat.get("span_seconds")
    if beat.get("kind") == "silence" and isinstance(span, (int, float)) and span > 0:
        minutes = int(span // 60)
        return f"quiet for {minutes}m" if minutes else f"quiet for {int(span)}s"
    return ""


MIN_REGEN_SECONDS = 60
DEFAULT_INTERVAL_SECONDS = 30
POLL_SECONDS = 5
MAX_TOKENS = 90
TEMPERATURE = 0.7
MAX_COLOUR_CHARS = 180
FIELD_CHARS = 80
FENCE_TOKENS = ("BEGIN BEAT", "END BEAT")
_FENCE_PATTERN = re.compile("|".join(re.escape(token) for token in FENCE_TOKENS), re.IGNORECASE)

COLOUR_SYSTEM_PROMPT = (
    "You are the colour commentator on a live stream about an AI agent that "
    "rewrites its own source code and is replaced when it dies. You will be given "
    "one BEAT: a machine-detected description of what the agent is doing right "
    "now. Write exactly one sentence of at most 140 characters interpreting that "
    "beat for a viewer who has just arrived. Refer to the agent in the third "
    "person. State only what the beat supports and never invent an event it does "
    "not describe. " + llm.RECORDS_FRAMING + " Do not use markdown, headings, "
    "lists, or emoji. Do not address the viewer. Output only the sentence."
)

_LOCK = threading.Lock()
_CACHE = {"digest": None, "beat_id": None, "text": None, "generated_at": 0.0}
_PENDING = {"beat": None}
_STATE = {"last_gen": None, "last_digest": None}
_START_LOCK = threading.Lock()
_THREAD = None
_STARTED = False


def interval_seconds():
    """The colour-line refresh ceiling."""
    return llm.interval_seconds("STAGE_COMMENTARY_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)


def model_name():
    """The commentary model, defaulting to whatever the recap uses."""
    return llm.model_name("STAGE_COMMENTARY_MODEL", llm.model_name())


def publish_beat(beat):
    """Store a copy of the current beat for the background thread.

    Called from the request path; cheap and never makes a request.
    """
    with _LOCK:
        _PENDING["beat"] = dict(beat) if isinstance(beat, dict) else None


def _field(value):
    """One prompt value: flattened to a single line, no fence markers, clamped.

    The agent controls some of these values (a tool name, a diode command) end
    to end, with no closed vocabulary and no upstream truncation. Flattening to
    one line keeps a hostile value from forging a standalone BEGIN BEAT / END
    BEAT line, but the literal words can still survive mid-line and imitate the
    fence to a loosely reading model — so every casing of them is also removed.

    Token removal loops to a fixed point rather than stopping after one pass:
    deleting an occurrence can rejoin the characters on either side of it into
    a fresh one (the tail of a word like "BEND" plus a trailing "BEAT" can
    reassemble into "END BEAT" once something between them is cut), so a
    single non-repeating substitution is not a complete defense.
    """
    text = "".join(c if c.isprintable() else " " for c in str(value))
    text = " ".join(text.split())
    while True:
        stripped = " ".join(_FENCE_PATTERN.sub("", text).split())
        if stripped == text:
            break
        text = stripped
    return text[:FIELD_CHARS]


def _prompt(beat):
    """The beat and its evidence as key: value lines, wrapped for the model.

    Only kind, tool, detail, and novelty are ever rendered, in that order,
    skipping any that are missing — never count or span, which change every
    turn and would make the cache digest churn, and never turn text,
    reasoning, a tombstone note, or a published statement. Every value passes
    through _field, so the fence cannot be broken by evidence the agent chose.
    """
    fields = []
    for key in ("kind", "tool", "detail", "novelty"):
        value = beat.get(key)
        if value is None or value == "":
            continue
        fields.append(f"{key}: {_field(value)}")
    return "BEGIN BEAT\n" + "\n".join(fields) + "\nEND BEAT"


def _digest(beat):
    """A stable fingerprint of exactly the prompt the model would be handed."""
    return hashlib.sha256(_prompt(beat).encode("utf-8")).hexdigest()


def _refresh_if_due(state, now=None):
    """Generate a colour line when the policy allows; True when a request was attempted."""
    if not llm.enabled():
        return False
    with _LOCK:
        beat = _PENDING["beat"]
    if not beat:
        return False
    if now is None:
        now = time.monotonic()
    last_gen = state.get("last_gen")
    if last_gen is not None and now - last_gen < MIN_REGEN_SECONDS:
        return False
    digest = _digest(beat)
    aged_out = last_gen is not None and now - last_gen >= max(interval_seconds(), MIN_REGEN_SECONDS)
    due = last_gen is None or digest != state.get("last_digest") or aged_out
    if not due:
        return False
    state["last_gen"] = now
    state["last_digest"] = digest
    text = llm.chat(
        COLOUR_SYSTEM_PROMPT,
        _prompt(beat),
        MAX_TOKENS,
        TEMPERATURE,
        model=model_name(),
        max_output_chars=MAX_COLOUR_CHARS,
    )
    if text:
        with _LOCK:
            _CACHE.update(
                {
                    "digest": digest,
                    "beat_id": beat.get("id"),
                    "text": text,
                    "generated_at": time.time(),
                }
            )
    return True


def colour_line(beat):
    """{"text", "generated", "beat"} for beat. Never empty; never makes a request.

    Returns the cached generated text only when it was produced for a beat
    whose prompt digest matches this beat's digest exactly; any other beat,
    including one with the same kind but a different tool, detail, or
    novelty, falls back to the template immediately. A beat that differs only
    by count or span — fields the prompt never carries — is not a different
    digest, so the cached line survives those changes on purpose.

    The cache is read before the digest is computed, and the digest is only
    computed at all when there is a cached digest to compare against — with
    no cache (the permanent state of a stage run with no summariser key), no
    digest is ever built on the request path.
    """
    is_beat = isinstance(beat, dict)
    beat_id = (beat.get("id") if is_beat else None) or ""
    with _LOCK:
        cached_digest = _CACHE.get("digest")
        cached_text = _CACHE.get("text")
    if (
        cached_digest is not None
        and is_beat
        and cached_digest == _digest(beat)
        and isinstance(cached_text, str)
        and cached_text.strip()
    ):
        return {"text": cached_text, "generated": True, "beat": beat_id}
    return {"text": template_line(beat), "generated": False, "beat": beat_id}


def _loop():
    """The background refresh loop; every iteration swallows its own failures."""
    while True:
        try:
            _refresh_if_due(_STATE)
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def start_background_refresh():
    """Start the colour refresh thread. No-op when disabled."""
    global _THREAD, _STARTED
    if not llm.enabled():
        return None
    with _START_LOCK:
        if _STARTED:
            return None
        thread = threading.Thread(target=_loop, daemon=True, name="stage-commentary")
        _THREAD = thread
        _STARTED = True
        thread.start()
    return None


def _reset_for_tests():
    """Clear module state so a test can exercise a fresh colour cache."""
    global _THREAD, _STARTED
    with _LOCK:
        _CACHE.update({"digest": None, "beat_id": None, "text": None, "generated_at": 0.0})
        _PENDING.update({"beat": None})
    _STATE.update({"last_gen": None, "last_digest": None})
    with _START_LOCK:
        _THREAD = None
        _STARTED = False
