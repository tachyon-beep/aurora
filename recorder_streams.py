import json
import math
import os
import re

CONSOLE_FILE = os.environ.get("LLM_CONSOLE_FILE", "/llm/console/console.json")
POLL_SECONDS = 5
MAX_STREAMS = 8
DEFAULT_STREAM_BUDGET = 10
STREAM_LIMIT_MAX = 120
BUDGET_WINDOW = 3600
MODEL_NAME_CAP = 200
REPORTED_NAME_CAP = 80

NAME_PATTERN = re.compile(r"\A[a-z0-9][a-z0-9_-]{0,31}\Z")
RESERVED_NAMES = ("core",)
COMPOSED_FIELDS = ("model", "reasoning_effort", "temperature", "top_p", "max_tokens")
REASONING_EFFORT_LEVELS = ("none", "low", "medium", "high")


def load_console(path=None):
    """Read the console file. Returns (declarations, error).

    A missing file is an empty console. An unreadable, unparseable, or
    wrongly-typed file returns no declarations and a factual reason, so the
    caller keeps its current stream set rather than tearing it down on a
    torn write.
    """
    if path is None:
        path = CONSOLE_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}, None
    except OSError:
        return None, "console is not readable"
    except ValueError:
        return None, "console is not valid json"
    if not isinstance(data, dict):
        return None, "console is not an object"
    streams = data.get("streams", {})
    if not isinstance(streams, dict):
        return None, "streams is not an object"
    return streams, None


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
        settings["model"] = model
    if "reasoning_effort" in declaration:
        if declaration["reasoning_effort"] not in REASONING_EFFORT_LEVELS:
            return None, "reasoning_effort must be one of none, low, medium, high"
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


def evaluate_console(declarations):
    """Split raw declarations into accepted settings and rejection reasons.

    Declarations are considered in file order; those past MAX_STREAMS are
    rejected. Reported names are capped so a junk key cannot bloat the state
    file.
    """
    accepted = {}
    rejected = {}
    for name, declaration in declarations.items():
        reported = (name if isinstance(name, str) else str(name))[:REPORTED_NAME_CAP]
        if len(accepted) >= MAX_STREAMS:
            rejected[reported] = "stream limit reached"
            continue
        settings, reason = validate_declaration(name, declaration)
        if reason is not None:
            rejected[reported] = reason
        else:
            accepted[name] = settings
    return accepted, rejected


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


def compose_body(body_bytes, settings):
    """Replace declared fields in a JSON-object request body.

    Returns (composed_bytes, error). Only fields in COMPOSED_FIELDS are
    applied; the budget paces the socket and never enters the body. A body
    that is not a JSON object cannot be composed and is refused.
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
    return json.dumps(data).encode("utf-8"), None
