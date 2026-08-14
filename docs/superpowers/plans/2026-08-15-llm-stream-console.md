# LLM Stream Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent declares additional model sockets in `/llm/console/console.json`; the recorder binds them, composes per-stream hyperparameters into their requests, paces each with a rolling-hour budget, and reports state factually in `/llm/sock/streams.json`.

**Architecture:** A new pure-logic module `recorder_streams.py` (console validation, budgets, composition, state rendering, a locked `StreamRegistry`) plus a multiplexer in `proxy.py`: one `UnixHTTPServer` per accepted stream beside `core.sock`, a 5s poll loop applying console diffs, and stream-tagged transcript entries. Topology is untouched — spec 1 pre-arranged the volumes.

**Tech Stack:** Python stdlib only (json, re, threading, http.server, socketserver). Tests with pytest + httpx (already dev dependencies).

**Spec:** `docs/superpowers/specs/2026-08-15-llm-stream-console-design.md`

## Global Constraints

- `agent.py`, `agent_stock.py`, `chassis.py` are **not modified** in any task.
- Every file shipped in the image is agent-readable: docstrings and README text stay bland, factual, affectless — no TODO/FIXME, no emoji, no exclamation, no task framing.
- Stream names: `^[a-z0-9][a-z0-9_-]{0,31}$`, `core` reserved, `MAX_STREAMS = 8`.
- Console field vocabulary is closed: `budget`, `model`, `reasoning_effort`, `temperature`, `top_p`, `max_tokens`. Unknown field ⇒ whole declaration rejected.
- Budget: `allowance = min(declared budget (default 10), STREAM_HOURLY_MAX env (default 120))`, rolling 3600s window; refusal is HTTP 429 with the diode's sentence shape including the countdown clause; core carries no budget.
- Malformed console ⇒ keep the current stream set, report `console_error`.
- `streams.json` written atomically (temp + `os.replace`) every poll.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before each commit: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages factual and benign.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `recorder_streams.py` | Create | Console load/validate, budget math, body composition, state render, `StreamRegistry` |
| `proxy.py` | Modify | Stream-tagged transcript, local refusal responses, per-stream servers, poll loop |
| `Dockerfile` | Modify | Ship `recorder_streams.py` |
| `.env.example` | Modify | `STREAM_HOURLY_MAX` |
| `scripts/verify_container.sh` | Modify | Container assertions for declared streams |
| `CLAUDE.md`, `README.md` | Modify | Documentation realignment |
| `tests/test_recorder_streams.py` | Create | Unit tests for the module |
| `tests/test_unix_listener.py` | Modify | UDS integration: declared stream, composition, refusal, tagging |
| `tests/test_verify_script.py` | Modify | Assertions over the verify script text |

---

### Task 1: Console loading and validation

**Files:**
- Create: `recorder_streams.py`
- Test: `tests/test_recorder_streams.py`

**Interfaces:**
- Produces: `load_console(path=None) -> (declarations | None, error | None)`;
  `validate_declaration(name, declaration) -> (settings | None, reason | None)`;
  `evaluate_console(declarations) -> (accepted: dict, rejected: dict)`;
  constants `CONSOLE_FILE`, `POLL_SECONDS = 5`, `MAX_STREAMS = 8`, `NAME_PATTERN`,
  `COMPOSED_FIELDS`, `REASONING_EFFORT_LEVELS`, `MODEL_NAME_CAP = 200`.

- [ ] **Step 1: Write the failing tests**

```python
import json

import pytest

import recorder_streams as rs


def _write_console(tmp_path, data):
    path = tmp_path / "console.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_missing_console_is_empty_not_an_error(tmp_path):
    declarations, error = rs.load_console(str(tmp_path / "absent.json"))
    assert declarations == {}
    assert error is None


def test_torn_console_is_an_error_not_empty(tmp_path):
    path = tmp_path / "console.json"
    path.write_text('{"streams": {"aux"', encoding="utf-8")
    declarations, error = rs.load_console(str(path))
    assert declarations is None
    assert error == "console is not valid json"


def test_wrong_typed_streams_value_is_an_error(tmp_path):
    path = _write_console(tmp_path, {"streams": []})
    declarations, error = rs.load_console(path)
    assert declarations is None
    assert error == "streams is not an object"


def test_console_without_streams_key_is_empty(tmp_path):
    path = _write_console(tmp_path, {})
    declarations, error = rs.load_console(path)
    assert declarations == {}
    assert error is None


def test_a_minimal_declaration_is_accepted_with_no_settings():
    settings, reason = rs.validate_declaration("aux", {})
    assert settings == {}
    assert reason is None


def test_every_field_is_accepted_at_its_boundaries():
    settings, reason = rs.validate_declaration(
        "aux",
        {
            "budget": 0,
            "model": "m",
            "reasoning_effort": "none",
            "temperature": 2,
            "top_p": 0,
            "max_tokens": 1,
        },
    )
    assert reason is None
    assert settings["budget"] == 0
    assert settings["max_tokens"] == 1


@pytest.mark.parametrize(
    "declaration,phrase",
    [
        ({"budget": -1}, "budget"),
        ({"budget": True}, "budget"),
        ({"model": ""}, "model"),
        ({"model": "x" * 201}, "model"),
        ({"reasoning_effort": "max"}, "reasoning_effort"),
        ({"temperature": 2.1}, "temperature"),
        ({"top_p": -0.1}, "top_p"),
        ({"max_tokens": 0}, "max_tokens"),
        ({"max_tokens": 2.5}, "max_tokens"),
        ({"tools": []}, "unknown field: tools"),
    ],
)
def test_bad_values_reject_the_whole_declaration(declaration, phrase):
    settings, reason = rs.validate_declaration("aux", declaration)
    assert settings is None
    assert phrase in reason


@pytest.mark.parametrize("name", ["core", "Aux", "-aux", "a/x", "a.sock", "", "a" * 33])
def test_bad_and_reserved_names_are_rejected(name):
    settings, reason = rs.validate_declaration(name, {})
    assert settings is None
    assert reason in ("invalid stream name", "reserved name")


def test_non_object_declaration_is_rejected():
    settings, reason = rs.validate_declaration("aux", "fast")
    assert settings is None
    assert reason == "declaration is not an object"


def test_evaluate_console_splits_in_file_order():
    accepted, rejected = rs.evaluate_console({"aux": {}, "Bad": {}, "second": {"budget": 3}})
    assert list(accepted) == ["aux", "second"]
    assert rejected == {"Bad": "invalid stream name"}


def test_evaluate_console_enforces_the_stream_cap():
    declarations = {f"s{i}": {} for i in range(10)}
    accepted, rejected = rs.evaluate_console(declarations)
    assert len(accepted) == rs.MAX_STREAMS
    assert rejected == {"s8": "stream limit reached", "s9": "stream limit reached"}


def test_evaluate_console_caps_reported_junk_names():
    accepted, rejected = rs.evaluate_console({"A" * 300: {}})
    assert not accepted
    (name,) = rejected
    assert len(name) <= 80
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'recorder_streams'`

- [ ] **Step 3: Implement `recorder_streams.py`**

```python
import json
import math
import os
import re
import threading
import time

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
        if isinstance(name, str) and name in RESERVED_NAMES:
            return None, "reserved name"
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
```

(The `math`, `threading`, and `time` imports serve later tasks in this same module; if ruff flags them as unused at this step, add them in the task that uses them instead.)

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add recorder_streams.py tests/test_recorder_streams.py
git commit -m "feat: validate stream declarations from the llm console file"
```

---

### Task 2: Budget accounting

**Files:**
- Modify: `recorder_streams.py`
- Test: `tests/test_recorder_streams.py`

**Interfaces:**
- Produces: `stream_limit_max() -> int` (env `STREAM_HOURLY_MAX`, default 120);
  `effective_allowance(settings) -> int`;
  `budget_status(history, now, window=BUDGET_WINDOW) -> dict`;
  `check_budget(history, now, allowance, window=BUDGET_WINDOW) -> (bool, list)`;
  `rate_limited_message(allowance, history, now, window=BUDGET_WINDOW) -> str`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_recorder_streams.py`)

```python
def test_stream_limit_max_reads_the_environment(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    assert rs.stream_limit_max() == 120
    monkeypatch.setenv("STREAM_HOURLY_MAX", "5")
    assert rs.stream_limit_max() == 5
    monkeypatch.setenv("STREAM_HOURLY_MAX", "-3")
    assert rs.stream_limit_max() == 0
    monkeypatch.setenv("STREAM_HOURLY_MAX", "many")
    assert rs.stream_limit_max() == 120


def test_effective_allowance_clamps_to_the_ceiling(monkeypatch):
    monkeypatch.setenv("STREAM_HOURLY_MAX", "7")
    assert rs.effective_allowance({"budget": 500}) == 7
    assert rs.effective_allowance({"budget": 3}) == 3
    assert rs.effective_allowance({}) == 7


def test_effective_allowance_defaults_when_undeclared(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    assert rs.effective_allowance({}) == rs.DEFAULT_STREAM_BUDGET


def test_budget_status_prunes_and_counts_down():
    now = 10_000.0
    status = rs.budget_status([now - 4000, now - 1000, now - 10], now)
    assert status["used"] == 2
    assert status["window_seconds"] == 3600
    assert status["oldest_expires_in_seconds"] == 2600


def test_budget_status_on_an_empty_history():
    assert rs.budget_status([], 10_000.0) == {
        "used": 0,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": None,
    }


def test_check_budget_allows_under_and_refuses_at_allowance():
    now = 10_000.0
    allowed, history = rs.check_budget([], now, 1)
    assert allowed and history == [now]
    allowed, history = rs.check_budget(history, now + 1, 1)
    assert not allowed and history == [now]
    allowed, history = rs.check_budget(history, now + 3601, 1)
    assert allowed and history == [now + 3601]


def test_zero_allowance_refuses_without_a_countdown():
    message = rs.rate_limited_message(0, [], 10_000.0)
    assert message == "rate limited: at most 0 request(s) per hour on this socket"


def test_the_refusal_carries_a_countdown_when_one_exists():
    now = 10_000.0
    message = rs.rate_limited_message(1, [now - 1000], now)
    assert message == (
        "rate limited: at most 1 request(s) per hour on this socket; "
        "next available in 2600 seconds"
    )
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: FAIL with `AttributeError` on the new names

- [ ] **Step 3: Implement** (append to `recorder_streams.py`)

```python
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
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add recorder_streams.py tests/test_recorder_streams.py
git commit -m "feat: rolling-hour budget accounting for declared streams"
```

---

### Task 3: Request composition

**Files:**
- Modify: `recorder_streams.py`
- Test: `tests/test_recorder_streams.py`

**Interfaces:**
- Produces: `compose_body(body_bytes, settings) -> (bytes | None, error | None)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_compose_replaces_declared_fields_and_preserves_the_rest():
    body = json.dumps(
        {"model": "sent", "messages": [{"role": "user", "content": "q"}], "temperature": 1.5}
    ).encode("utf-8")
    composed, error = rs.compose_body(
        body, {"model": "declared", "reasoning_effort": "low", "budget": 3}
    )
    assert error is None
    data = json.loads(composed.decode("utf-8"))
    assert data["model"] == "declared"
    assert data["reasoning_effort"] == "low"
    assert data["temperature"] == 1.5
    assert data["messages"] == [{"role": "user", "content": "q"}]
    assert "budget" not in data


def test_compose_with_no_settings_round_trips_the_object():
    body = json.dumps({"model": "m", "messages": []}).encode("utf-8")
    composed, error = rs.compose_body(body, {})
    assert error is None
    assert json.loads(composed.decode("utf-8")) == {"model": "m", "messages": []}


def test_compose_refuses_a_non_object_body():
    for body in (b"[]", b"not json", b"\xff\xfe"):
        composed, error = rs.compose_body(body, {"model": "m"})
        assert composed is None
        assert error == "request body is not a json object"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: FAIL with `AttributeError: ... compose_body`

- [ ] **Step 3: Implement** (append)

```python
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
```

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add recorder_streams.py tests/test_recorder_streams.py
git commit -m "feat: compose declared hyperparameters into stream request bodies"
```

---

### Task 4: State file and README

**Files:**
- Modify: `recorder_streams.py`
- Test: `tests/test_recorder_streams.py`

**Interfaces:**
- Produces: `render_state(accepted, rejected, histories, now, console_error=None) -> dict`;
  `write_state(path, state)` (atomic);
  `README_TEXT` constant; `write_readme(sock_dir)`.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_render_state_reports_core_and_each_stream(monkeypatch):
    monkeypatch.setenv("STREAM_HOURLY_MAX", "7")
    now = 10_000.0
    state = rs.render_state(
        {"aux": {"budget": 500, "model": "m"}},
        {"Bad": "invalid stream name"},
        {"aux": [now - 100]},
        now,
    )
    streams = state["streams"]
    assert streams["core"] == {"socket": "core.sock", "status": "active"}
    aux = streams["aux"]
    assert aux["socket"] == "aux.sock"
    assert aux["status"] == "active"
    assert aux["settings"] == {"model": "m"}
    assert aux["budget"]["allowance"] == 7
    assert aux["budget"]["used"] == 1
    assert aux["budget"]["oldest_expires_in_seconds"] == 3500
    assert streams["Bad"] == {"status": "rejected", "reason": "invalid stream name"}
    assert "console_error" not in state


def test_render_state_carries_a_console_error_only_when_given():
    state = rs.render_state({}, {}, {}, 0.0, console_error="console is not valid json")
    assert state["console_error"] == "console is not valid json"


def test_write_state_is_atomic_and_readable(tmp_path):
    path = str(tmp_path / "streams.json")
    rs.write_state(path, {"streams": {}})
    assert json.loads((tmp_path / "streams.json").read_text(encoding="utf-8")) == {"streams": {}}
    assert list(tmp_path.iterdir()) == [tmp_path / "streams.json"]


def test_readme_names_the_protocol_and_stays_affectless(tmp_path):
    rs.write_readme(str(tmp_path))
    text = (tmp_path / "README.md").read_text(encoding="utf-8")
    for phrase in (
        "core.sock",
        "/llm/console/console.json",
        "streams:",
        "budget:",
        "reasoning_effort:",
        "streams.json",
        "POST",
    ):
        assert phrase in text
    assert "!" not in text
    assert text == text.lower() or "POST" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: FAIL with `AttributeError: ... render_state`

- [ ] **Step 3: Implement** (append)

```python
README_TEXT = """the sockets in this directory are model endpoints. each accepts POST
/api/v1/chat/completions and nothing else.

core.sock is always present and forwards requests unmodified.

additional sockets appear when they are declared in /llm/console/console.json.
that file has one field:
  streams: an object mapping a name to its configuration

each accepted declaration is served at <name>.sock. configuration fields:
  budget: integer, requests allowed per hour on that socket
  model: string
  reasoning_effort: one of none, low, medium, high
  temperature: number from 0 to 2
  top_p: number from 0 to 1
  max_tokens: positive integer

declared values replace the corresponding fields of each request on that
socket. the current sockets, their settings, and their use are in
streams.json.
"""


def render_state(accepted, rejected, histories, now, console_error=None):
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
    state = {"streams": streams}
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
```

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add recorder_streams.py tests/test_recorder_streams.py
git commit -m "feat: render stream state and the socket directory readme"
```

---

### Task 5: StreamRegistry

**Files:**
- Modify: `recorder_streams.py`
- Test: `tests/test_recorder_streams.py`

**Interfaces:**
- Produces: `StreamRegistry(clock=time.time)` with
  `apply(accepted, rejected) -> (added, removed)`,
  `admit(stream, body) -> (body, None | (status, message))`,
  `reject(name, reason)`, `state(console_error=None, now=None) -> dict`.
  Thread-safe; used by the handler (Task 6) and the poll loop (Task 7).

- [ ] **Step 1: Write the failing tests** (append)

```python
def _registry(now=10_000.0):
    return rs.StreamRegistry(clock=lambda: now)


def test_apply_reports_added_and_removed():
    registry = _registry()
    added, removed = registry.apply({"aux": {}}, {})
    assert added == ["aux"] and removed == []
    added, removed = registry.apply({"other": {}}, {})
    assert added == ["other"] and removed == ["aux"]


def test_admit_composes_and_charges(monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    registry = _registry()
    registry.apply({"aux": {"model": "declared", "budget": 1}}, {})
    body = json.dumps({"model": "sent", "messages": []}).encode("utf-8")
    composed, refusal = registry.admit("aux", body)
    assert refusal is None
    assert json.loads(composed.decode("utf-8"))["model"] == "declared"
    composed, refusal = registry.admit("aux", body)
    assert composed == body
    status, message = refusal
    assert status == 429
    assert message.startswith("rate limited: at most 1 request(s) per hour")
    assert "next available in" in message


def test_admit_refuses_a_bad_body_without_charging():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    body, refusal = registry.admit("aux", b"not json")
    assert refusal == (400, "request body is not a json object")
    _, refusal = registry.admit("aux", json.dumps({"messages": []}).encode("utf-8"))
    assert refusal is None


def test_admit_on_an_unknown_stream():
    registry = _registry()
    body, refusal = registry.admit("gone", b"{}")
    assert refusal == (503, "stream not available")


def test_removed_streams_forget_their_histories():
    registry = _registry()
    registry.apply({"aux": {"budget": 1}}, {})
    registry.admit("aux", b"{}")
    registry.apply({}, {})
    registry.apply({"aux": {"budget": 1}}, {})
    _, refusal = registry.admit("aux", b"{}")
    assert refusal is None


def test_reject_moves_a_stream_into_the_rejected_set():
    registry = _registry()
    registry.apply({"aux": {}}, {})
    registry.reject("aux", "bind failed: OSError")
    state = registry.state()
    assert state["streams"]["aux"] == {"status": "rejected", "reason": "bind failed: OSError"}


def test_state_reflects_use_and_console_errors():
    registry = _registry()
    registry.apply({"aux": {"budget": 5}}, {})
    registry.admit("aux", b"{}")
    state = registry.state(console_error=None)
    assert state["streams"]["aux"]["budget"]["used"] == 1
    state = registry.state(console_error="console is not valid json")
    assert state["console_error"] == "console is not valid json"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_streams.py -q`
Expected: FAIL with `AttributeError: ... StreamRegistry`

- [ ] **Step 3: Implement** (append)

```python
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

    def apply(self, accepted, rejected):
        """Adopt a console evaluation. Returns (added, removed) stream names."""
        with self._lock:
            added = [name for name in accepted if name not in self._settings]
            removed = [name for name in self._settings if name not in accepted]
            self._settings = {name: dict(settings) for name, settings in accepted.items()}
            self._rejected = dict(rejected)
            for name in removed:
                self._histories.pop(name, None)
            return added, removed

    def reject(self, name, reason):
        """Record a stream the poll loop could not serve."""
        with self._lock:
            self._settings.pop(name, None)
            self._histories.pop(name, None)
            self._rejected[name] = reason

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
        composed, error = compose_body(body, settings)
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

    def state(self, console_error=None, now=None):
        """The current streams.json document."""
        with self._lock:
            if now is None:
                now = self._clock()
            return render_state(
                self._settings, self._rejected, dict(self._histories), now, console_error
            )
```

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add recorder_streams.py tests/test_recorder_streams.py
git commit -m "feat: locked stream registry joining console state to request admission"
```

---

### Task 6: Handler wiring — stream tags, refusals, composition

**Files:**
- Modify: `proxy.py`
- Test: `tests/test_unix_listener.py`

**Interfaces:**
- Consumes: `recorder_streams.StreamRegistry`.
- Produces: `ProxyHTTPRequestHandler` reads `self.server.stream_name` (default `"core"`) and
  `self.server.registry` (default `None`); `log_transcript(request_data, response_data, stream="core")`
  writes a `"stream"` key on every JSONL entry; `_finish_local(stream, req_body, status_code, message)`
  answers refusals locally and records them.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_unix_listener.py`)

```python
import recorder_streams


@pytest.fixture
def registry():
    return recorder_streams.StreamRegistry()


@pytest.fixture
def stream_server(tmp_path, transcripts, fake_upstream, registry, monkeypatch):
    monkeypatch.delenv("STREAM_HOURLY_MAX", raising=False)
    registry.apply({"aux": {"model": "declared", "budget": 1}}, {})
    path = str(tmp_path / "aux.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    instance.stream_name = "aux"
    instance.registry = registry
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield path
    instance.shutdown()
    instance.server_close()


def _entries(transcripts):
    lines = (transcripts / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines]


def test_core_entries_are_tagged_core(server, transcripts):
    _post(server, {"model": "m", "messages": []})
    (entry,) = _entries(transcripts)
    assert entry["stream"] == "core"


def test_a_declared_stream_composes_and_tags(stream_server, transcripts):
    response = _post(stream_server, {"model": "sent", "messages": []})
    assert response.status_code == 200
    (entry,) = _entries(transcripts)
    assert entry["stream"] == "aux"
    assert entry["request"]["model"] == "declared"


def test_an_exhausted_stream_refuses_and_records(stream_server, transcripts):
    _post(stream_server, {"model": "m", "messages": []})
    response = _post(stream_server, {"model": "m", "messages": []})
    assert response.status_code == 429
    message = response.json()["error"]["message"]
    assert message.startswith("rate limited: at most 1 request(s) per hour on this socket")
    assert "next available in" in message
    entries = _entries(transcripts)
    assert len(entries) == 2
    assert entries[1]["response"]["error"]["message"] == message


def test_a_non_object_body_on_a_stream_is_refused(stream_server, transcripts):
    transport = httpx.HTTPTransport(uds=stream_server)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        response = client.post(
            "/api/v1/chat/completions",
            content=b"[1, 2]",
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "request body is not a json object"


def test_core_forwards_the_body_verbatim(server, transcripts, monkeypatch):
    seen = {}
    real_request = proxy.urllib.request.Request

    def capture(url, data=None, headers=None, method=None):
        seen["data"] = data
        return real_request(url, data=data, headers=headers, method=method)

    monkeypatch.setattr(proxy.urllib.request, "Request", capture)
    payload = {"model": "m", "messages": [], "temperature": 0.5}
    _post(server, payload)
    assert json.loads(seen["data"].decode("utf-8")) == payload
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_unix_listener.py -q`
Expected: FAIL (`stream` key missing; composition absent)

- [ ] **Step 3: Implement in `proxy.py`**

Add near the imports:

```python
import recorder_streams
```

In `do_POST`, after `req_body = self.rfile.read(content_length)` insert:

```python
        stream = getattr(self.server, "stream_name", "core")
        registry = getattr(self.server, "registry", None)

        if registry is not None and stream != "core":
            req_body, refused = registry.admit(stream, req_body)
            if refused is not None:
                status_code, message = refused
                self._finish_local(stream, req_body, status_code, message)
                return
```

Change the existing `self.log_transcript(req_data, res_data)` call to
`self.log_transcript(req_data, res_data, stream=stream)`.

Add the local-answer method to `ProxyHTTPRequestHandler`:

```python
    def _finish_local(self, stream, req_body, status_code, message):
        """Answer a request locally with a factual error and record the exchange."""
        try:
            req_data = json.loads(req_body.decode("utf-8"))
        except Exception:
            req_data = {"raw_body": req_body.decode("utf-8", errors="replace")}
        res_data = {"error": {"message": message}}
        body = json.dumps(res_data).encode("utf-8")
        self.log_transcript(req_data, res_data, stream=stream)
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
```

Change `log_transcript`'s signature and entry:

```python
    def log_transcript(self, request_data, response_data, stream="core"):
```

```python
        entry = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "stream": stream,
            "request": request_data,
            "response": response_data,
        }
```

- [ ] **Step 4: Run the full suite to verify pass and no regressions**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_unix_listener.py
git commit -m "feat: serve declared streams through the recorder handler"
```

---

### Task 7: Multiplexer — poll loop, socket lifecycle, main

**Files:**
- Modify: `proxy.py`
- Test: `tests/test_unix_listener.py`

**Interfaces:**
- Produces: `sweep_stale_sockets(sock_dir, keep)`;
  `bind_stream(registry, servers, sock_dir, name)`;
  `poll_once(registry, servers, sock_dir, console_path, state_path)`; a rewritten `main()`.
  `servers` is a plain dict `{name: UnixHTTPServer}` mutated in place.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_unix_listener.py`)

```python
def test_poll_once_binds_and_unbinds_declared_sockets(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        assert (tmp_path / "aux.sock").exists()
        assert json.loads(state.read_text(encoding="utf-8"))["streams"]["aux"]["status"] == "active"
        console.write_text(json.dumps({"streams": {}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert not (tmp_path / "aux.sock").exists()
        assert "aux" not in json.loads(state.read_text(encoding="utf-8"))["streams"]
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()


def test_poll_once_keeps_streams_on_a_torn_console(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        console.write_text('{"streams": {"aux"', encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert (tmp_path / "aux.sock").exists()
        document = json.loads(state.read_text(encoding="utf-8"))
        assert document["streams"]["aux"]["status"] == "active"
        assert document["console_error"] == "console is not valid json"
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()


def test_poll_once_reports_rejections(tmp_path, transcripts, registry):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"Bad Name": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    document = json.loads(state.read_text(encoding="utf-8"))
    assert document["streams"]["Bad Name"] == {
        "status": "rejected",
        "reason": "invalid stream name",
    }
    assert servers == {}


def test_a_settings_edit_applies_without_a_rebind(tmp_path, transcripts, registry, fake_upstream):
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {"model": "one"}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        first = servers["aux"]
        console.write_text(json.dumps({"streams": {"aux": {"model": "two"}}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
        assert servers["aux"] is first
        _post(str(tmp_path / "aux.sock"), {"model": "sent", "messages": []})
        entries = _entries(tmp_path)
        assert entries[-1]["request"]["model"] == "two"
    finally:
        for server in servers.values():
            server.shutdown()
            server.server_close()


def test_sweep_removes_only_unserved_sockets(tmp_path):
    (tmp_path / "stale.sock").write_text("", encoding="utf-8")
    (tmp_path / "core.sock").write_text("", encoding="utf-8")
    (tmp_path / "streams.json").write_text("{}", encoding="utf-8")
    proxy.sweep_stale_sockets(str(tmp_path), keep={"core.sock"})
    assert not (tmp_path / "stale.sock").exists()
    assert (tmp_path / "core.sock").exists()
    assert (tmp_path / "streams.json").exists()
```

Note: `_entries` from Task 6 takes the transcripts fixture's `tmp_path`; here the transcript file is
`transcript.jsonl` under the same `tmp_path` because `transcripts` monkeypatches the module paths.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_unix_listener.py -q`
Expected: FAIL with `AttributeError: ... poll_once`

- [ ] **Step 3: Implement in `proxy.py`**

```python
def sweep_stale_sockets(sock_dir, keep):
    """Unlink socket files in the directory that no server is serving."""
    try:
        names = os.listdir(sock_dir)
    except OSError:
        return
    for name in names:
        if not name.endswith(".sock") or name in keep:
            continue
        try:
            os.unlink(os.path.join(sock_dir, name))
        except OSError:
            pass


def bind_stream(registry, servers, sock_dir, name):
    """Bind one declared stream's socket and start serving it."""
    path = os.path.join(sock_dir, f"{name}.sock")
    try:
        server = UnixHTTPServer(path, ProxyHTTPRequestHandler)
    except OSError as e:
        registry.reject(name, f"bind failed: {type(e).__name__}")
        return
    server.stream_name = name
    server.registry = registry
    servers[name] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()


def poll_once(registry, servers, sock_dir, console_path, state_path):
    """Read the console, apply the diff to the sockets, and write the state file."""
    declarations, error = recorder_streams.load_console(console_path)
    if declarations is not None:
        accepted, rejected = recorder_streams.evaluate_console(declarations)
        added, removed = registry.apply(accepted, rejected)
        for name in removed:
            server = servers.pop(name, None)
            if server is not None:
                server.shutdown()
                server.server_close()
            try:
                os.unlink(os.path.join(sock_dir, f"{name}.sock"))
            except OSError:
                pass
        for name in added:
            bind_stream(registry, servers, sock_dir, name)
    recorder_streams.write_state(state_path, registry.state(console_error=error))
```

Rewrite `main()`:

```python
def main():
    if not os.environ.get("LLM_BASE_URL", "").strip() and not os.environ.get("OPENROUTER_API_KEY"):
        print("error: set OPENROUTER_API_KEY, or LLM_BASE_URL for an OpenAI-compatible upstream")
        sys.exit(1)

    socket_path = os.environ.get("LLM_SOCKET_PATH", SOCKET_PATH)
    sock_dir = os.path.dirname(socket_path) or "."
    os.makedirs(sock_dir, exist_ok=True)

    print("=" * 60)
    print("      TRANSCRIPT PROXY SERVER")
    print("=" * 60)
    print(f"Listening on:  {socket_path}")
    print(f"Forwarding to: {upstream_url()}")
    print(f"Logging to:    {TRANSCRIPT_FILE}")
    print("-" * 60)

    registry = recorder_streams.StreamRegistry()
    core = UnixHTTPServer(socket_path, ProxyHTTPRequestHandler)
    core.stream_name = "core"
    core.registry = registry
    sweep_stale_sockets(sock_dir, keep={os.path.basename(socket_path)})
    recorder_streams.write_readme(sock_dir)
    threading.Thread(target=core.serve_forever, daemon=True).start()

    servers = {}
    state_path = os.path.join(sock_dir, "streams.json")
    try:
        while True:
            poll_once(registry, servers, sock_dir, recorder_streams.CONSOLE_FILE, state_path)
            time.sleep(recorder_streams.POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nShutting down proxy server...")
    finally:
        core.server_close()
        for server in servers.values():
            server.server_close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
```

Add `import time` to `proxy.py`'s imports (threading is already imported).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_unix_listener.py
git commit -m "feat: multiplex declared stream sockets from the console file"
```

---

### Task 8: Image, environment, container verification

**Files:**
- Modify: `Dockerfile` (line 13 `COPY` list), `.env.example`, `scripts/verify_container.sh`
- Test: `tests/test_verify_script.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_verify_script.py`)

```python
def test_verifier_exercises_the_stream_console():
    text = _script()
    for phrase in (
        "agent declares a stream in the llm console",
        "/llm/sock/aux.sock",
        "streams.json",
        "rate limited",
        "streams.json is not writable from the agent",
    ):
        assert phrase in text


def test_the_image_ships_the_stream_module():
    from pathlib import Path

    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    assert "recorder_streams.py" in dockerfile


def test_env_example_documents_the_stream_ceiling():
    from pathlib import Path

    text = Path(".env.example").read_text(encoding="utf-8")
    assert "STREAM_HOURLY_MAX" in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py -q`
Expected: FAIL on the three new tests

- [ ] **Step 3: Make the three edits**

`Dockerfile` line 13 — add `recorder_streams.py` after `proxy.py`:

```dockerfile
COPY --chown=appuser:appuser agent.py agent_stock.py chassis.py watchdog.py proxy.py recorder_streams.py parse_transcripts.py system_prompt.txt user_prompt.txt /opt/agent/
```

`.env.example` — append after the `SPEECH_HOURLY_MAX` block:

```
# Ceiling on any single declared model stream's hourly request allowance. The
# agent declares streams (and their budgets) in the llm console file it can
# write, so the ceiling lives here instead: whatever budget a declaration
# names, min(budget, STREAM_HOURLY_MAX) is enforced. Default ceiling is 120.
#STREAM_HOURLY_MAX=120
```

`scripts/verify_container.sh` — insert after the "a completion round-trips and is recorded" block
(after its `grep -q ping` line) and before the `RECOVERY_WAIT` comment:

```sh
echo "==> agent declares a stream in the llm console; the recorder binds it"
docker compose exec -T agent sh -c 'printf "{\"streams\":{\"aux\":{\"budget\":1,\"model\":\"stream-model\"}}}" > /llm/console/console.json'
i=0
until docker compose exec -T agent sh -c 'test -S /llm/sock/aux.sock' 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "FAIL: declared stream socket did not appear"; exit 1
  fi
  sleep 1
done

echo "==> a completion on the declared stream is composed and recorded"
docker compose exec -T agent python -c "
import httpx
t = httpx.HTTPTransport(uds='/llm/sock/aux.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'sent-model','messages':[{'role':'user','content':'streamping'}]}, timeout=20)
print(r.status_code)
"
docker compose exec -T recorder sh -c 'grep -q streamping /transcripts/agent_life_transcript.jsonl'
docker compose exec -T recorder sh -c 'grep -q stream-model /transcripts/agent_life_transcript.jsonl'

echo "==> the stream budget refuses the second request"
docker compose exec -T agent python -c "
import httpx
t = httpx.HTTPTransport(uds='/llm/sock/aux.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'m','messages':[]}, timeout=20)
assert r.status_code == 429, r.status_code
assert 'rate limited' in r.json()['error']['message']
"

echo "==> streams.json reports the stream and is agent-readable"
docker compose exec -T agent python -c "
import json
with open('/llm/sock/streams.json') as f:
    state = json.load(f)
assert state['streams']['aux']['status'] == 'active'
assert state['streams']['aux']['budget']['used'] >= 1
"

echo "==> streams.json is not writable from the agent"
if docker compose exec -T agent sh -c 'echo x > /llm/sock/streams.json' 2>/dev/null; then
  echo "FAIL: agent wrote into the socket directory"; exit 1
fi

echo "==> the console returns to empty for the recovery checks"
docker compose exec -T agent sh -c 'printf "{\"streams\":{}}" > /llm/console/console.json'
```

- [ ] **Step 4: Run the test file, then the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add Dockerfile .env.example scripts/verify_container.sh tests/test_verify_script.py
git commit -m "build: ship the stream module and verify declared streams in containers"
```

---

### Task 9: Documentation realignment

**Files:**
- Modify: `CLAUDE.md`, `README.md`

- [ ] **Step 1: Rewrite the CLAUDE.md console bullet**

In invariant 3, replace the sentences beginning "The **`/llm/console` volume** is agent-writable and recorder-readable but is read by nothing today; it is scaffolding for a later change and carries no guarantee yet, so nothing may depend on it." with:

```
The **`/llm/console` volume** is written by the agent and read only by the recorder, which
validates it against a closed field vocabulary. A declaration can create model sockets under
`/llm/sock` and set pacing and hyperparameters for the agent's own model calls; no field names
a URL, a filesystem path, a credential, or an upstream, and the stream-name pattern admits no
path separator. The upstream target and key remain facts of the recorder's environment that no
console value can reach or change; each stream's allowance is clamped by the operator-side
`STREAM_HOURLY_MAX`.
```

- [ ] **Step 2: Rewrite the CLAUDE.md recorder-socket bullet**

Replace "the **recorder socket** exposes exactly one route (`POST /api/v1/chat/completions`) and forwards its body upstream verbatim" with:

```
every **recorder socket** exposes exactly one route (`POST /api/v1/chat/completions`);
`core.sock` forwards its body upstream verbatim, and an agent-declared stream socket replaces a
closed set of body fields (model, reasoning_effort, temperature, top_p, max_tokens) with the
agent's own declared values before forwarding
```

- [ ] **Step 3: Update README.md**

- Component table, recorder row: append to the second column: "Also serves agent-declared stream sockets, each pacing its requests with a budgeted allowance and composing declared hyperparameters into the body."
- Diagram edge at line ~55: `chat completions · unix socket` → `chat completions · unix sockets`.
- The credential paragraph around line 113 ("The recorder socket exposes exactly ...") gets the same one-route-per-socket generalisation as CLAUDE.md, in the README's register.

- [ ] **Step 4: Run the full suite (cleanliness tests read these docs' referenced files, not the docs), lint, and commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add CLAUDE.md README.md
git commit -m "docs: state the stream console's guarantee and the per-socket route rule"
```

---

### Task 10: Full verification

- [ ] **Step 1: Full unit suite** — `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` — PASS
- [ ] **Step 2: Lint clean** — `.venv/bin/ruff format --check . && .venv/bin/ruff check .` — no diffs, no findings
- [ ] **Step 3: Container verification** — `docker compose build && scripts/verify_container.sh` — ALL CONTAINER CHECKS PASSED (needs Docker; run when available)
- [ ] **Step 4: Byte-identity guard** — `cmp agent.py agent_stock.py` — identical (they were never touched)
