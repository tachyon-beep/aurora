import json
import math
import os
import re
import threading
import time

CONSOLE_FILE = os.environ.get("LLM_CONSOLE_FILE", "/llm/console/console.json")
POLL_SECONDS = 5
CONSOLE_MAX_BYTES = 65_536
MAX_STREAMS = 8
DEFAULT_STREAM_BUDGET = 10
STREAM_LIMIT_MAX = 120
BUDGET_WINDOW = 3600
MODEL_NAME_CAP = 200
REPORTED_NAME_CAP = 80

NAME_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
RESERVED_NAMES = ("core",)
COMPOSED_FIELDS = ("model", "reasoning_effort", "temperature", "top_p", "max_tokens")
REASONING_EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high")
DEFAULT_REASONING_ALLOWANCE = 8192


def load_console(path=None):
    """Read the console file. Returns (declarations, enabled, error).

    A missing file is an empty, disabled console. An unreadable,
    unparseable, or wrongly-typed file returns no declarations, disabled,
    and a factual reason, so the caller keeps its current stream set rather
    than tearing it down on a torn write. enabled is True only when
    enable_streams is the literal JSON boolean true; anything else,
    including "true", 1, or null, is disabled.
    """
    if path is None:
        path = CONSOLE_FILE
    try:
        with open(path, "rb") as f:
            raw = f.read(CONSOLE_MAX_BYTES + 1)
    except FileNotFoundError:
        return {}, False, None
    except OSError:
        return None, False, "console is not readable"
    if len(raw) > CONSOLE_MAX_BYTES:
        return None, False, "console is too large"
    try:
        data = json.loads(raw)
    except ValueError:
        return None, False, "console is not valid json"
    if not isinstance(data, dict):
        return None, False, "console is not an object"
    streams = data.get("streams", {})
    if not isinstance(streams, dict):
        return None, False, "streams is not an object"
    enabled = data.get("enable_streams") is True
    return streams, enabled, None


def _number(value, low, high):
    """True when value is a non-boolean number inside the inclusive range."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def validate_declaration(name, declaration):
    """Validate one stream declaration. Returns (settings, reason).

    settings is the accepted declaration; reason states why it was rejected.
    Exactly one of the two is None.
    """
    if not isinstance(name, str) or not NAME_PATTERN.match(name):
        return None, "invalid stream name"
    if name in RESERVED_NAMES:
        return None, "reserved name"
    if not isinstance(declaration, dict):
        return None, "declaration is not an object"
    for field in declaration:
        if field != "budget" and field not in COMPOSED_FIELDS:
            return None, f"unknown field: {field}"
    settings = {}
    if "budget" in declaration:
        budget = declaration["budget"]
        if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
            return None, "budget must be an integer of at least 0"
        settings["budget"] = budget
    if "model" in declaration:
        model = declaration["model"]
        if not isinstance(model, str) or not model.strip() or len(model) > MODEL_NAME_CAP:
            return None, f"model must be a non-empty string of at most {MODEL_NAME_CAP} characters"
        if model not in permitted_models():
            return None, "model not permitted"
        settings["model"] = model
    if "reasoning_effort" in declaration:
        if declaration["reasoning_effort"] not in REASONING_EFFORT_LEVELS:
            return None, "reasoning_effort must be one of none, minimal, low, medium, high"
        settings["reasoning_effort"] = declaration["reasoning_effort"]
    if "temperature" in declaration:
        if not _number(declaration["temperature"], 0, 2):
            return None, "temperature must be a number from 0 to 2"
        settings["temperature"] = declaration["temperature"]
    if "top_p" in declaration:
        if not _number(declaration["top_p"], 0, 1):
            return None, "top_p must be a number from 0 to 1"
        settings["top_p"] = declaration["top_p"]
    if "max_tokens" in declaration:
        tokens = declaration["max_tokens"]
        if isinstance(tokens, bool) or not isinstance(tokens, int) or tokens < 1:
            return None, "max_tokens must be a positive integer"
        settings["max_tokens"] = tokens
    return settings, None


def evaluate_console(declarations, enabled):
    """Split raw declarations into accepted settings and rejection reasons.

    When enabled is False every declaration is rejected with
    "streams are not enabled", ahead of any other check. When the stream
    upstream's key is absent every declaration is rejected with "streams are
    not available": a served socket would only relay authentication failures.
    Otherwise declarations are considered in file order; those past
    MAX_STREAMS are rejected. Reported names are capped so a junk key cannot
    bloat the state file.
    """
    accepted = {}
    rejected = {}
    for name, declaration in declarations.items():
        reported = (name if isinstance(name, str) else str(name))[:REPORTED_NAME_CAP]
        if not enabled:
            rejected[reported] = "streams are not enabled"
            continue
        if not stream_credential_present():
            rejected[reported] = "streams are not available"
            continue
        if len(accepted) >= MAX_STREAMS:
            rejected[reported] = "stream limit reached"
            continue
        settings, reason = validate_declaration(name, declaration)
        if reason is not None:
            rejected[reported] = reason
        else:
            accepted[name] = settings
    return accepted, rejected


def _split_models(name):
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def stream_credential_present():
    """True when the recorder holds the key its stream upstream requires.

    Declared streams forward to a fixed upstream with its own key, separate
    from core.sock's configured upstream. Without that key no stream can be
    served, so declarations are rejected and the permitted-model list is
    empty rather than published as usable.
    """
    return bool(os.environ.get("OPENROUTER_API_KEY", "").strip())


def permitted_models():
    """The models a declaration may set, from the environment.

    STREAM_MODEL_ALLOW_TEXT and STREAM_MODEL_ALLOW_VISION are comma-separated
    lists of model identifiers; their union is the permitted set. Both unset
    or empty permits none: a declaration that sets model is rejected, and
    requests on every socket keep the model field they were sent with. The
    recorder holds no default-model knowledge of its own, so there is nothing
    to fall back to. Without the stream upstream's key the permitted set is
    empty regardless of the lists.
    """
    if not stream_credential_present():
        return []
    merged = []
    for item in _split_models("STREAM_MODEL_ALLOW_TEXT") + _split_models(
        "STREAM_MODEL_ALLOW_VISION"
    ):
        if item not in merged:
            merged.append(item)
    return merged


def model_catalog():
    """The models.json entries: each permitted model, marked with whether it
    accepts image content in messages (membership in the vision list)."""
    vision = _split_models("STREAM_MODEL_ALLOW_VISION")
    return [{"id": item, "image_input": item in vision} for item in permitted_models()]


def stream_limit_max():
    """The operator ceiling on any stream's hourly allowance, from the environment."""
    raw = os.environ.get("STREAM_HOURLY_MAX", "").strip()
    if not raw:
        return STREAM_LIMIT_MAX
    try:
        return max(0, int(raw))
    except ValueError:
        return STREAM_LIMIT_MAX


def effective_allowance(settings):
    """The allowance actually enforced: the declared budget clamped by the ceiling."""
    return min(settings.get("budget", DEFAULT_STREAM_BUDGET), stream_limit_max())


def budget_status(history, now, window=BUDGET_WINDOW):
    """Use of a stream's allowance over the window.

    The history is pruned here rather than trusted, so a stream that went
    quiet reports its true in-window use.
    """
    recent = [t for t in history if now - t < window]
    if not recent:
        return {"used": 0, "window_seconds": window, "oldest_expires_in_seconds": None}
    expires = max(0, math.ceil(window - (now - min(recent))))
    return {"used": len(recent), "window_seconds": window, "oldest_expires_in_seconds": expires}


def check_budget(history, now, allowance, window=BUDGET_WINDOW):
    """Rolling-window check. Returns (allowed, new_history).

    Drops entries older than the window; allows when fewer than allowance
    remain, appending now when allowed.
    """
    recent = [t for t in history if now - t < window]
    if len(recent) >= allowance:
        return False, recent
    recent.append(now)
    return True, recent


def rate_limited_message(allowance, history, now, window=BUDGET_WINDOW):
    """The refusal sentence, with a countdown when a pruned stamp supplies one."""
    message = f"rate limited: at most {allowance} request(s) per hour on this socket"
    status = budget_status(history, now, window)
    if status["oldest_expires_in_seconds"] is not None:
        message += f"; next available in {status['oldest_expires_in_seconds']} seconds"
    return message


def reasoning_allowance():
    """The operator's reasoning token allowance, from the environment.

    The upstream counts reasoning tokens inside max_tokens, so a small
    response cap can be consumed entirely by reasoning before any visible
    output. Composition adds this allowance on top of a capped request's
    max_tokens so the cap bounds the response. Zero disables the addition.
    """
    raw = os.environ.get("STREAM_REASONING_ALLOWANCE", "").strip()
    if not raw:
        return DEFAULT_REASONING_ALLOWANCE
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_REASONING_ALLOWANCE


def compose_body(body_bytes, settings, allowance=0):
    """Replace declared fields in a JSON-object request body.

    Returns (composed_bytes, error). Only fields in COMPOSED_FIELDS are
    applied; the budget paces the socket and never enters the body. A body
    that is not a JSON object cannot be composed and is refused.

    When the composed request carries a positive integer max_tokens and its
    reasoning effort is not "none", allowance is added to the forwarded
    max_tokens: the requested value bounds the response rather than being
    shared with reasoning. streams.json continues to report the declared
    value.
    """
    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        data = None
    if not isinstance(data, dict):
        return None, "request body is not a json object"
    for field in COMPOSED_FIELDS:
        if field in settings:
            data[field] = settings[field]
    tokens = data.get("max_tokens")
    if (
        allowance
        and isinstance(tokens, int)
        and not isinstance(tokens, bool)
        and tokens >= 1
        and data.get("reasoning_effort") != "none"
    ):
        data["max_tokens"] = tokens + allowance
    return json.dumps(data).encode("utf-8"), None


README_TEXT = """the sockets in this directory are model endpoints. each accepts POST
/api/v1/chat/completions and nothing else.

core.sock is always present and forwards requests unmodified.

core.sock and the declared sockets are served by different upstream
endpoints: a model identifier accepted on one may not be accepted on the
other.

additional sockets appear when a declaration in /llm/console/console.json is
accepted. that file has two fields:
  enable_streams: boolean
  streams: an object mapping a name to its configuration

a declaration is not served unless enable_streams is true.

each accepted declaration is served at <name>.sock. configuration fields:
  budget: integer, requests allowed per hour on that socket
  model: string
  reasoning_effort: one of none, minimal, low, medium, high
  temperature: number from 0 to 2
  top_p: number from 0 to 1
  max_tokens: positive integer. it bounds the response; reasoning does not
  count against it unless reasoning_effort is none.

declared values replace the corresponding fields of each request on that
socket. the current sockets, their settings, and their use are in
streams.json. the model identifiers a declaration may set are listed in
models.json; each entry states whether that model accepts image content
in messages.
"""


def render_state(accepted, rejected, histories, now, streams_enabled, console_error=None):
    """The streams.json document describing every socket in the directory."""
    streams = {"core": {"socket": "core.sock", "status": "active"}}
    for name, settings in accepted.items():
        streams[name] = {
            "socket": f"{name}.sock",
            "status": "active",
            "settings": {k: v for k, v in settings.items() if k != "budget"},
            "budget": {
                "allowance": effective_allowance(settings),
                **budget_status(histories.get(name, []), now),
            },
        }
    for name, reason in rejected.items():
        streams[name] = {"status": "rejected", "reason": reason}
    state = {"streams_enabled": streams_enabled, "streams": streams}
    if console_error is not None:
        state["console_error"] = console_error
    return state


def write_state(path, state):
    """Write the state document via a rename, so a reader never sees a torn file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, path)


def write_readme(sock_dir):
    """Write the socket directory's protocol description."""
    with open(os.path.join(sock_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write(README_TEXT)


def write_models(sock_dir):
    """Write the permitted-model list beside streams.json.

    The document carries the identifiers a declaration may set, each marked
    with whether the model accepts image content in messages. It is
    replaced via a rename, and only when its content differs, so the poll
    loop can call this every cycle: the file exists from startup and changes
    only when the permitted list does. The unchanged path also removes any
    temporary left by a write interrupted before its rename, so no stray
    file persists on the directory. Returns True when a write happened.
    """
    path = os.path.join(sock_dir, "models.json")
    tmp = path + ".tmp"
    document = {"models": model_catalog()}
    try:
        with open(path, "r", encoding="utf-8") as f:
            if json.load(f) == document:
                try:
                    os.remove(tmp)
                except FileNotFoundError:
                    pass
                return False
    except (OSError, ValueError):
        pass
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2)
    os.replace(tmp, path)
    return True


class StreamRegistry:
    """The live stream set: settings, rejections, and budget histories.

    Request threads read settings and charge budgets while the poll thread
    applies console changes, so every access holds the lock.
    """

    def __init__(self, clock=time.time):
        self._lock = threading.Lock()
        self._settings = {}
        self._rejected = {}
        self._histories = {}
        self._clock = clock

    def _prune_histories(self, now):
        """Age out spent stamps and forget the streams left with none.

        A name that is no longer declared keeps its charge until its stamps
        leave the window, so removing a stream from the agent-writable console
        and declaring it again does not buy a second allowance inside the same
        hour. Called with the lock already held.
        """
        kept = {}
        for name, history in self._histories.items():
            recent = [t for t in history if now - t < BUDGET_WINDOW]
            if recent or name in self._settings:
                kept[name] = recent
        self._histories = kept

    def apply(self, accepted, rejected):
        """Adopt a console evaluation. Returns (added, removed) stream names."""
        with self._lock:
            added = [name for name in accepted if name not in self._settings]
            removed = [name for name in self._settings if name not in accepted]
            self._settings = {name: dict(settings) for name, settings in accepted.items()}
            self._rejected = dict(rejected)
            self._prune_histories(self._clock())
            return added, removed

    def reject(self, name, reason):
        """Record a stream the poll loop could not serve."""
        with self._lock:
            self._settings.pop(name, None)
            self._rejected[name] = reason
            self._prune_histories(self._clock())

    def admit(self, stream, body):
        """Compose and charge one request. Returns (body, refusal).

        refusal is None when the request may be forwarded, else a
        (status, message) pair. A body that fails composition is refused
        before any budget charge.
        """
        with self._lock:
            settings = self._settings.get(stream)
            settings = dict(settings) if settings is not None else None
        if settings is None:
            return body, (503, "stream not available")
        composed, error = compose_body(body, settings, reasoning_allowance())
        if error is not None:
            return body, (400, error)
        allowance = effective_allowance(settings)
        with self._lock:
            now = self._clock()
            history = self._histories.get(stream, [])
            allowed, history = check_budget(history, now, allowance)
            self._histories[stream] = history
            if not allowed:
                return body, (429, rate_limited_message(allowance, history, now))
        return composed, None

    def state(self, streams_enabled=False, console_error=None, now=None):
        """The current streams.json document."""
        with self._lock:
            if now is None:
                now = self._clock()
            return render_state(
                self._settings,
                self._rejected,
                dict(self._histories),
                now,
                streams_enabled,
                console_error,
            )
