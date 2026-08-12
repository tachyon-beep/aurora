# Chassis Resilience Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agents stop dying silently to self-inflicted mechanical faults; the chassis repairs what it can and turns unrecoverable faults into visible, recorded deaths.

**Architecture:** All healing lives in `chassis.py` (send-view repair, error classification with bounded retries, model fallback, headshot protocol with synthetic tombstones and exit 43, environment failures with exit 44). `watchdog.py` learns the two new exit codes and gains exit-0 flap detection so no failure mode can spin forever. `agent.py`/`agent_stock.py` are untouched.

**Tech Stack:** Python standard library only. Tests with pytest, following existing house patterns (`types.SimpleNamespace` fakes, `tmp_path` git repos, `monkeypatch`).

**Spec:** `docs/superpowers/specs/2026-08-13-stream-demonstration-design.md`, Part 1.

## Global Constraints

- `agent.py` and `agent_stock.py` must remain byte-identical and are NOT modified in this phase (a test enforces identity).
- Nothing is ever injected into the agent's in-memory conversation; repair affects the send view only.
- All text the agent can read (docstrings in `chassis.py`/`watchdog.py`, tombstone contents) is bland and factual — no authorial voice, jokes, emoji, or quest framing.
- Provider/model identity stays in `chassis.py`, never in `agent.py`.
- Standard library only; no new dependencies.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Exit-code vocabulary after this phase: `42` done (existing), `43` headshot (harness-terminated incarnation), `44` environment failure, other non-zero crash.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `chassis.py` | modify | `repair_send_view`, `classify_error`, `create_with_recovery`, `HeadshotError`/`EnvironmentFailure`, `headshot`, `archive_corrupt_session`, `default_model`, `strip_reasoning`; wiring in `run_agent_loop` and `main` |
| `watchdog.py` | modify | `is_flapping`, `plan_recovery`, `discard_session`; exit 43/44 handling and flap detection in `run_watchdog` |
| `tests/test_repair_send_view.py` | create | Send-view repair units, INC10 regression fixture |
| `tests/test_chassis_recovery.py` | create | Classification, recovery loop, headshot, corrupt-session units |
| `tests/test_watchdog.py` | modify | Flap detection and recovery-planning units |
| `CLAUDE.md`, `README.md` | modify | Document the new chassis responsibilities and exit codes |

---

### Task 1: `repair_send_view`

**Files:**
- Modify: `chassis.py` (new pure function after `condense_duplicate_tool_results`, plus one-line wiring in `run_agent_loop`)
- Test: `tests/test_repair_send_view.py` (create)

**Interfaces:**
- Consumes: nothing new; composes with existing `clip_to_window` / `condense_duplicate_tool_results`.
- Produces: `repair_send_view(messages: list[dict]) -> list[dict]` — pure; later tasks (deep repair in Task 3) call it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_repair_send_view.py`:

```python
import copy

import chassis


def _assistant(content="", tool_calls=None):
    m = {"role": "assistant", "content": content}
    if tool_calls:
        m["tool_calls"] = [
            {
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            }
            for tc_id, name in tool_calls
        ]
    return m


def _tool(tc_id, name="read_file", content="ok"):
    return {"role": "tool", "tool_call_id": tc_id, "name": name, "content": content}


def test_clean_history_is_unchanged():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
        _assistant("done"),
    ]
    assert chassis.repair_send_view(messages) == messages


def test_orphaned_tool_results_are_dropped():
    # INC10 regression shape: an archive/trim removed assistant tool_calls
    # messages but left their tool results behind.
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _tool("gone-1", name="conversation_archive"),
        _tool("gone-2", name="git_op"),
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
    ]
    repaired = chassis.repair_send_view(messages)
    ids = [m.get("tool_call_id") for m in repaired if m.get("role") == "tool"]
    assert ids == ["c1"]


def test_tool_result_not_matching_open_calls_is_dropped():
    messages = [
        _assistant("a", tool_calls=[("c1", "read_file")]),
        _tool("c1"),
        _tool("stale", name="write_file"),
    ]
    repaired = chassis.repair_send_view(messages)
    ids = [m.get("tool_call_id") for m in repaired if m.get("role") == "tool"]
    assert ids == ["c1"]


def test_unanswered_tool_call_gets_synthetic_result():
    messages = [
        _assistant("a", tool_calls=[("c1", "read_file"), ("c2", "validate")]),
        _tool("c1"),
        {"role": "user", "content": "next"},
    ]
    repaired = chassis.repair_send_view(messages)
    assert [m["role"] for m in repaired] == ["assistant", "tool", "tool", "user"]
    synthetic = repaired[2]
    assert synthetic["tool_call_id"] == "c2"
    assert synthetic["name"] == "validate"
    assert synthetic["content"] == "result unavailable"


def test_unanswered_tool_call_at_end_gets_synthetic_result():
    messages = [_assistant("a", tool_calls=[("c1", "read_file")])]
    repaired = chassis.repair_send_view(messages)
    assert repaired[-1]["role"] == "tool"
    assert repaired[-1]["tool_call_id"] == "c1"


def test_input_is_not_mutated():
    messages = [
        _tool("orphan"),
        _assistant("a", tool_calls=[("c1", "read_file")]),
    ]
    snapshot = copy.deepcopy(messages)
    chassis.repair_send_view(messages)
    assert messages == snapshot


def test_send_path_applies_repair(monkeypatch):
    captured = {}

    class _Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            import types

            message = types.SimpleNamespace(
                content="hi", tool_calls=None, reasoning_content=None
            )
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=message)]
            )

    import types

    client = types.SimpleNamespace(
        chat=types.SimpleNamespace(completions=_Completions())
    )
    tools = types.SimpleNamespace(schemas=[], tools={})
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _tool("gone-1"),
    ]
    chassis.run_agent_loop(client, "m", messages, tools, max_turns=1)
    sent_roles = [m["role"] for m in captured["messages"]]
    assert "tool" not in sent_roles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_repair_send_view.py -q`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'repair_send_view'`

- [ ] **Step 3: Implement `repair_send_view` and wire the send path**

In `chassis.py`, after `condense_duplicate_tool_results`:

```python
def repair_send_view(messages):
    """Return a copy of the message list with tool-call pairing repaired.

    A tool message is kept only when it answers a tool call from the nearest
    preceding assistant message that is still awaiting results. Every tool
    call left unanswered receives a synthetic "result unavailable" tool
    result. The input list and its messages are not modified.
    """
    out = []
    open_calls = []

    def close_open():
        for call_id, call_name in open_calls:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": call_name,
                    "content": "result unavailable",
                }
            )
        open_calls.clear()

    for m in messages:
        role = m.get("role")
        if role == "tool":
            match = next(
                (i for i, (call_id, _) in enumerate(open_calls) if call_id == m.get("tool_call_id")),
                None,
            )
            if match is not None:
                open_calls.pop(match)
                out.append(m)
        else:
            close_open()
            out.append(m)
            if role == "assistant":
                for tc in m.get("tool_calls") or []:
                    open_calls.append(
                        (tc.get("id"), (tc.get("function") or {}).get("name"))
                    )
    close_open()
    return out
```

In `run_agent_loop`, change the send line:

```python
            "messages": repair_send_view(
                clip_to_window(condense_duplicate_tool_results(messages))
            ),
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_repair_send_view.py tests/test_context_window.py tests/test_condense_duplicates.py tests/test_session_persistence.py -q`
Expected: all PASS (the extra files guard against send-path regressions)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format chassis.py tests/test_repair_send_view.py && .venv/bin/ruff check chassis.py tests/test_repair_send_view.py
git add chassis.py tests/test_repair_send_view.py
git commit -m "feat: repair tool-call pairing in the send view

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: `classify_error`

**Files:**
- Modify: `chassis.py` (new pure function)
- Test: `tests/test_chassis_recovery.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces: `classify_error(exc: Exception) -> str` returning one of `"transient"`, `"model"`, `"invalid_request"`. Task 3 consumes it.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_chassis_recovery.py`:

```python
import chassis


class _StatusError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def test_connection_errors_are_transient():
    assert chassis.classify_error(Exception("connection reset")) == "transient"


def test_rate_limit_and_server_errors_are_transient():
    assert chassis.classify_error(_StatusError("too many requests", 429)) == "transient"
    assert chassis.classify_error(_StatusError("bad gateway", 502)) == "transient"


def test_bad_model_is_a_model_error():
    exc = _StatusError("deepseek/nonexistent is not a valid model ID", 400)
    assert chassis.classify_error(exc) == "model"
    exc404 = _StatusError("No endpoints found for model", 404)
    assert chassis.classify_error(exc404) == "model"


def test_other_400s_are_invalid_request():
    exc = _StatusError(
        "Messages with role 'tool' must be a response to a preceding message "
        "with 'tool_calls'",
        400,
    )
    assert chassis.classify_error(exc) == "invalid_request"


def test_404_without_model_mention_is_invalid_request():
    assert chassis.classify_error(_StatusError("not found", 404)) == "invalid_request"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'classify_error'`

- [ ] **Step 3: Implement `classify_error`**

In `chassis.py`:

```python
def classify_error(exc):
    """Classify an API exception as transient, model, or invalid_request.

    Status codes 400/404/422 are permanent request faults; when the message
    names the model the fault is classified as a model error. Everything
    else, including missing status codes, is treated as transient.
    """
    status = getattr(exc, "status_code", None)
    text = str(exc).lower()
    if status in (400, 404) and "model" in text:
        return "model"
    if status in (400, 404, 422):
        return "invalid_request"
    return "transient"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format chassis.py tests/test_chassis_recovery.py && .venv/bin/ruff check chassis.py tests/test_chassis_recovery.py
git add chassis.py tests/test_chassis_recovery.py
git commit -m "feat: classify upstream API errors

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `create_with_recovery`

**Files:**
- Modify: `chassis.py` (exceptions, `default_model`, `strip_reasoning`, `create_with_recovery`; add `import time`)
- Test: `tests/test_chassis_recovery.py` (extend)

**Interfaces:**
- Consumes: `classify_error` (Task 2), `repair_send_view` (Task 1), existing `clip_to_window` and `condense_duplicate_tool_results`.
- Produces:
  - `class HeadshotError(Exception)` and `class EnvironmentFailure(Exception)` — raised out of `run_agent_loop`, caught in `main` (Task 4).
  - `default_model() -> str`
  - `strip_reasoning(messages: list[dict]) -> list[dict]`
  - `create_with_recovery(client, api_kwargs: dict, full_history: list[dict], sleep=time.sleep) -> response` — may mutate `api_kwargs["model"]` (persisting a fallback) and `api_kwargs["messages"]` (deep repair).
  - Constants `TRANSIENT_RETRIES = 5`, `BACKOFF_SECONDS = [1, 2, 4, 8, 16]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chassis_recovery.py`:

```python
import types

import pytest


def _response():
    message = types.SimpleNamespace(content="hi", tool_calls=None, reasoning_content=None)
    return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


class _ScriptedCompletions:
    """Raises each scripted exception in turn, then returns a response."""

    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.errors:
            raise self.errors.pop(0)
        return _response()


def _client(errors):
    completions = _ScriptedCompletions(errors)
    return types.SimpleNamespace(chat=types.SimpleNamespace(completions=completions)), completions


def test_transient_errors_retry_with_backoff():
    client, completions = _client([Exception("boom"), Exception("boom")])
    sleeps = []
    response = chassis.create_with_recovery(
        client, {"model": "m", "messages": []}, [], sleep=sleeps.append
    )
    assert response.choices
    assert len(completions.calls) == 3
    assert sleeps == [1, 2]


def test_transient_exhaustion_raises_environment_failure():
    client, completions = _client([Exception("boom")] * 10)
    with pytest.raises(chassis.EnvironmentFailure):
        chassis.create_with_recovery(
            client, {"model": "m", "messages": []}, [], sleep=lambda s: None
        )
    assert len(completions.calls) == chassis.TRANSIENT_RETRIES + 1


def test_model_error_falls_back_to_environment_default(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "good/model")
    client, completions = _client([_StatusError("bad/model is not a valid model ID", 400)])
    api_kwargs = {"model": "bad/model", "messages": []}
    chassis.create_with_recovery(client, api_kwargs, [], sleep=lambda s: None)
    assert completions.calls[-1]["model"] == "good/model"
    assert api_kwargs["model"] == "good/model"


def test_model_error_on_default_model_is_a_headshot(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "bad/model")
    client, _ = _client([_StatusError("bad/model is not a valid model ID", 400)])
    with pytest.raises(chassis.HeadshotError):
        chassis.create_with_recovery(
            client, {"model": "bad/model", "messages": []}, [], sleep=lambda s: None
        )


def test_invalid_request_deep_repairs_and_retries():
    poisoned = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        {"role": "tool", "tool_call_id": "gone", "name": "x", "content": "orphan"},
        {"role": "assistant", "content": "a", "reasoning_content": "r"},
    ]
    client, completions = _client([_StatusError("orphan tool message", 400)])
    api_kwargs = {"model": "m", "messages": list(poisoned)}
    chassis.create_with_recovery(client, api_kwargs, poisoned, sleep=lambda s: None)
    sent = completions.calls[-1]["messages"]
    assert all(m.get("role") != "tool" for m in sent)
    assert all("reasoning_content" not in m for m in sent)


def test_invalid_request_after_repair_is_a_headshot():
    errors = [_StatusError("still broken", 400), _StatusError("still broken", 400)]
    client, _ = _client(errors)
    with pytest.raises(chassis.HeadshotError):
        chassis.create_with_recovery(
            client, {"model": "m", "messages": []}, [], sleep=lambda s: None
        )


def test_strip_reasoning_is_pure():
    messages = [{"role": "assistant", "content": "a", "reasoning_content": "r"}]
    stripped = chassis.strip_reasoning(messages)
    assert "reasoning_content" not in stripped[0]
    assert "reasoning_content" in messages[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'create_with_recovery'`

- [ ] **Step 3: Implement the recovery loop**

In `chassis.py` (add `import time` at the top, with the existing imports):

```python
TRANSIENT_RETRIES = 5
BACKOFF_SECONDS = [1, 2, 4, 8, 16]


class HeadshotError(Exception):
    """An unrepairable request fault; the incarnation must end."""


class EnvironmentFailure(Exception):
    """The upstream stayed unreachable through bounded retries."""


def default_model():
    """The model named by the environment, matching build_client's selection."""
    return os.getenv("LLM_MODEL") or os.getenv("OPENROUTER_MODEL", "deepseek/deepseek-v4-pro")


def strip_reasoning(messages):
    """Return a copy of the message list without reasoning_content fields."""
    out = []
    for m in messages:
        if "reasoning_content" in m:
            m = {k: v for k, v in m.items() if k != "reasoning_content"}
        out.append(m)
    return out


def create_with_recovery(client, api_kwargs, full_history, sleep=time.sleep):
    """Send one completion request, recovering from classified failures.

    Transient failures retry with backoff and raise EnvironmentFailure when
    retries are exhausted. A model error retries once with the
    environment-default model; the swap persists in api_kwargs. Any other
    invalid request retries once with an aggressively repaired send view.
    Faults that survive their retry raise HeadshotError.
    """
    transient = 0
    tried_model_swap = False
    tried_deep_repair = False
    while True:
        try:
            return client.chat.completions.create(**api_kwargs)
        except Exception as e:
            kind = classify_error(e)
            if kind == "transient":
                if transient >= TRANSIENT_RETRIES:
                    raise EnvironmentFailure(str(e))
                sleep(BACKOFF_SECONDS[min(transient, len(BACKOFF_SECONDS) - 1)])
                transient += 1
            elif kind == "model":
                if tried_model_swap or api_kwargs.get("model") == default_model():
                    raise HeadshotError(f"model rejected upstream: {e}")
                api_kwargs["model"] = default_model()
                tried_model_swap = True
            else:
                if tried_deep_repair:
                    raise HeadshotError(f"request rejected upstream after repair: {e}")
                api_kwargs["messages"] = repair_send_view(
                    strip_reasoning(
                        clip_to_window(condense_duplicate_tool_results(full_history))
                    )
                )
                tried_deep_repair = True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format chassis.py tests/test_chassis_recovery.py && .venv/bin/ruff check chassis.py tests/test_chassis_recovery.py
git add chassis.py tests/test_chassis_recovery.py
git commit -m "feat: recover from classified API failures with bounded retries

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: Headshot protocol and loop/main wiring

**Files:**
- Modify: `chassis.py` (`headshot`, exit-code constants, `run_agent_loop` send call, `main` exception handling; add `import datetime` and `import shutil` at the top)
- Test: `tests/test_chassis_recovery.py` (extend)

**Interfaces:**
- Consumes: `create_with_recovery`, `HeadshotError`, `EnvironmentFailure` (Task 3); existing `save_session`, `SESSION_FILE`.
- Produces:
  - `EXIT_HEADSHOT = 43`, `EXIT_ENVIRONMENT = 44` (watchdog mirrors these values in Task 6).
  - `headshot(messages: list[dict], reason: str, work_dir: str | None = None, session_file: str | None = None) -> SystemExit(43)` — `None` resolves to module-level `WORK_DIR`/`SESSION_FILE` at call time (so monkeypatching works); writes `tombstones/incarnation-<stamp>-<pid>.txt` and `tombstones/incarnation_note.txt`, archives history to `tombstones/session_<stamp>.json`, removes the session file, exits 43.
  - `WORK_DIR` module constant (`os.path.dirname(os.path.abspath(__file__))`).
  - `run_agent_loop` now raises `HeadshotError`/`EnvironmentFailure` instead of breaking on API errors, and persists a model fallback across turns.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chassis_recovery.py`:

```python
import json
import os


def test_headshot_writes_tombstone_and_removes_session(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    history = [{"role": "user", "content": "u"}]
    with pytest.raises(SystemExit) as excinfo:
        chassis.headshot(
            history,
            "request rejected upstream after repair: still broken",
            work_dir=str(tmp_path),
            session_file=str(session),
        )
    assert excinfo.value.code == 43
    assert not session.exists()
    tombstones = tmp_path / "tombstones"
    note = (tombstones / "incarnation_note.txt").read_text(encoding="utf-8")
    assert "terminated by the harness" in note
    assert "still broken" in note
    archives = list(tombstones.glob("session_*.json"))
    assert len(archives) == 1
    assert json.loads(archives[0].read_text(encoding="utf-8")) == history
    stamped = [p for p in tombstones.glob("incarnation-*.txt")]
    assert len(stamped) == 1


def test_headshot_survives_missing_session_file(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        chassis.headshot(
            [],
            "reason",
            work_dir=str(tmp_path),
            session_file=str(tmp_path / "absent.json"),
        )
    assert excinfo.value.code == 43


def _agent_module(history):
    module = types.SimpleNamespace()
    module.tools = types.SimpleNamespace(schemas=[], tools={})
    module.conversation_history = history
    module.build_initial_conversation = lambda: [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    return module


def test_main_exits_44_and_saves_session_on_environment_failure(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))

    def _raise(*args, **kwargs):
        raise chassis.EnvironmentFailure("down")

    monkeypatch.setattr(chassis, "run_agent_loop", _raise)
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(_agent_module([]))
    assert excinfo.value.code == 44
    assert session.exists()


def test_main_headshots_on_headshot_error(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))

    def _raise(*args, **kwargs):
        raise chassis.HeadshotError("poisoned")

    monkeypatch.setattr(chassis, "run_agent_loop", _raise)
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(_agent_module([]))
    assert excinfo.value.code == 43
    assert not session.exists()
    assert (tmp_path / "tombstones" / "incarnation_note.txt").exists()


def test_run_agent_loop_persists_model_fallback(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "good/model")
    errors = [_StatusError("bad/model is not a valid model ID", 400)]
    client, completions = _client(errors)
    tools = types.SimpleNamespace(schemas=[], tools={})
    messages = [{"role": "user", "content": "u"}]
    chassis.run_agent_loop(client, "bad/model", messages, tools, max_turns=1)
    assert completions.calls[-1]["model"] == "good/model"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'headshot'`

- [ ] **Step 3: Implement headshot and wire the loop and main**

In `chassis.py`, add near the top (after `SESSION_FILE`):

```python
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
EXIT_HEADSHOT = 43
EXIT_ENVIRONMENT = 44
```

Add the protocol function (after `create_with_recovery`):

```python
def headshot(messages, reason, work_dir=None, session_file=None):
    """Record a harness-terminated incarnation and exit with code 43.

    Writes a synthetic tombstone note, archives the session history beside
    it, and removes the saved session so the fault is not resumed.
    """
    if work_dir is None:
        work_dir = WORK_DIR
    if session_file is None:
        session_file = SESSION_FILE
    tombstone_dir = os.path.join(work_dir, "tombstones")
    os.makedirs(tombstone_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    archive_name = f"session_{stamp}.json"
    try:
        with open(os.path.join(tombstone_dir, archive_name), "w", encoding="utf-8") as f:
            json.dump(messages, f)
    except Exception:
        archive_name = "(archive failed)"
    note = (
        "this incarnation was terminated by the harness.\n"
        f"reason: {reason}\n"
        f"messages in history: {len(messages)}\n"
        f"the session history was archived to tombstones/{archive_name}\n"
    )
    note_path = os.path.join(tombstone_dir, f"incarnation-{stamp}-{os.getpid()}.txt")
    with open(note_path, "w", encoding="utf-8") as f:
        f.write(note)
    with open(os.path.join(tombstone_dir, "incarnation_note.txt"), "w", encoding="utf-8") as f:
        f.write(note)
    try:
        os.remove(session_file)
    except OSError:
        pass
    print(f"harness terminated incarnation: {reason}")
    sys.stdout.flush()
    sys.exit(EXIT_HEADSHOT)
```

In `run_agent_loop`, replace the request block:

```python
        try:
            response = client.chat.completions.create(**api_kwargs)
        except Exception as e:
            print(f"api error: {e}")
            break
```

with:

```python
        response = create_with_recovery(client, api_kwargs, messages)
        model = api_kwargs.get("model", model)
```

In `main`, replace the bare loop call:

```python
    run_agent_loop(client, model, agent_module.conversation_history, agent_module.tools)
```

with:

```python
    try:
        run_agent_loop(client, model, agent_module.conversation_history, agent_module.tools)
    except HeadshotError as e:
        headshot(agent_module.conversation_history, str(e))
    except EnvironmentFailure as e:
        print(f"environment failure: {e}")
        save_session(agent_module.conversation_history)
        sys.exit(EXIT_ENVIRONMENT)
```

- [ ] **Step 4: Run the full suite to verify nothing regressed**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS (in particular `tests/test_session_persistence.py` and `tests/test_smoke.py`)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format chassis.py tests/test_chassis_recovery.py && .venv/bin/ruff check chassis.py tests/test_chassis_recovery.py
git add chassis.py tests/test_chassis_recovery.py
git commit -m "feat: terminate unrecoverable incarnations with a synthetic tombstone

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Corrupt-session archival

**Files:**
- Modify: `chassis.py` (`archive_corrupt_session`, wiring in `main`'s resume branch; requires the `import shutil` added in Task 4 — add it here if absent)
- Test: `tests/test_chassis_recovery.py` (extend)

**Interfaces:**
- Consumes: `WORK_DIR`, `SESSION_FILE` (Task 4).
- Produces: `archive_corrupt_session(session_file: str = SESSION_FILE, work_dir: str = WORK_DIR) -> None` — moves the unreadable file to `tombstones/corrupt_session_<stamp>.json` and writes a sibling `corrupt_session_<stamp>.txt` note. Never touches `incarnation_note.txt`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_chassis_recovery.py`:

```python
def test_archive_corrupt_session_moves_file_and_notes(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("{not json", encoding="utf-8")
    chassis.archive_corrupt_session(
        session_file=str(session), work_dir=str(tmp_path)
    )
    assert not session.exists()
    tombstones = tmp_path / "tombstones"
    moved = list(tombstones.glob("corrupt_session_*.json"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "{not json"
    notes = list(tombstones.glob("corrupt_session_*.txt"))
    assert len(notes) == 1
    assert "could not be read" in notes[0].read_text(encoding="utf-8")
    assert not (tombstones / "incarnation_note.txt").exists()


def test_archive_corrupt_session_ignores_missing_file(tmp_path):
    chassis.archive_corrupt_session(
        session_file=str(tmp_path / "absent.json"), work_dir=str(tmp_path)
    )


def test_main_archives_corrupt_session_and_starts_fresh(tmp_path, monkeypatch):
    session = tmp_path / "session_context.json"
    session.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(chassis, "SESSION_FILE", str(session))
    monkeypatch.setattr(chassis, "WORK_DIR", str(tmp_path))
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "build_client", lambda: (object(), "m"))
    monkeypatch.setattr(chassis, "run_agent_loop", lambda *a, **k: None)
    module = _agent_module([])
    with pytest.raises(SystemExit) as excinfo:
        chassis.main(module)
    assert excinfo.value.code == 0
    moved = list((tmp_path / "tombstones").glob("corrupt_session_*.json"))
    assert len(moved) == 1
    assert moved[0].read_text(encoding="utf-8") == "{not json"
    assert json.loads(session.read_text(encoding="utf-8")) == module.conversation_history
    assert module.conversation_history[0]["role"] == "system"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py -q`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'archive_corrupt_session'`

- [ ] **Step 3: Implement archival and wire the resume branch**

In `chassis.py`:

```python
def archive_corrupt_session(session_file=None, work_dir=None):
    """Move an unreadable session file into tombstones/ and note the loss."""
    if session_file is None:
        session_file = SESSION_FILE
    if work_dir is None:
        work_dir = WORK_DIR
    if not os.path.exists(session_file):
        return
    tombstone_dir = os.path.join(work_dir, "tombstones")
    os.makedirs(tombstone_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = os.path.join(tombstone_dir, f"corrupt_session_{stamp}.json")
    try:
        shutil.move(session_file, dest)
    except OSError:
        return
    note = (
        "the saved session could not be read and was moved to "
        f"tombstones/corrupt_session_{stamp}.json. this incarnation starts "
        "without it.\n"
    )
    with open(
        os.path.join(tombstone_dir, f"corrupt_session_{stamp}.txt"), "w", encoding="utf-8"
    ) as f:
        f.write(note)
```

In `main`, extend the resume branch's exception handler:

```python
            except Exception as e:
                print(f"warning: failed to load session context: {e}")
                archive_corrupt_session()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_chassis_recovery.py tests/test_session_persistence.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format chassis.py tests/test_chassis_recovery.py && .venv/bin/ruff check chassis.py tests/test_chassis_recovery.py
git add chassis.py tests/test_chassis_recovery.py
git commit -m "feat: archive unreadable session files instead of discarding them

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Watchdog exit codes and flap detection

**Files:**
- Modify: `watchdog.py` (constants, `is_flapping`, `plan_recovery`, `discard_session`; rewire `run_watchdog`'s exit handling)
- Test: `tests/test_watchdog.py` (extend)

**Interfaces:**
- Consumes: exit codes 43/44 as produced by `chassis.headshot` and `chassis.main` (Task 4). The values are duplicated as constants in `watchdog.py` (the two files share no imports today; keep it that way).
- Produces:
  - `EXIT_DONE = 42`, `EXIT_HEADSHOT = 43`, `EXIT_ENVIRONMENT = 44`, `ZERO_EXIT_FLAP_COUNT = 3`, `ZERO_EXIT_FLAP_WINDOW_SECONDS = 120`, `ENVIRONMENT_PAUSE_SECONDS = 60`.
  - `is_flapping(zero_exit_times: list[float], now: float, count=..., window=...) -> bool`
  - `plan_recovery(ret: int, zero_exit_times: list[float], failure_times: list[float], now: float) -> tuple[str, list[float], list[float]]` — returns `(action, zero_exit_times, failure_times)` where action is one of `"archive_reset"`, `"pause"`, `"restart"`, `"tier1"`, `"tier2"`, `"tier3"`.
  - `discard_session(work_dir: str = WORK_DIR) -> None` — removes `session_context.json` if present.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watchdog.py`:

```python
def test_is_flapping_requires_clustered_zero_exits():
    now = 1000.0
    assert not watchdog.is_flapping([now], now)
    assert not watchdog.is_flapping([now - 200, now - 190, now], now)
    assert watchdog.is_flapping([now - 100, now - 50, now], now)


def test_plan_recovery_maps_deliberate_exits():
    action, zeros, failures = watchdog.plan_recovery(42, [1.0], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [] and failures == []
    action, zeros, failures = watchdog.plan_recovery(43, [1.0], [2.0], 10.0)
    assert action == "archive_reset"
    assert zeros == [] and failures == []


def test_plan_recovery_pauses_on_environment_failure():
    action, zeros, failures = watchdog.plan_recovery(44, [], [], 10.0)
    assert action == "pause"
    assert failures == []


def test_plan_recovery_benign_zero_exit_restarts():
    action, zeros, failures = watchdog.plan_recovery(0, [], [], 1000.0)
    assert action == "restart"
    assert zeros == [1000.0]
    assert failures == []


def test_plan_recovery_flapping_zero_exits_escalate():
    now = 1000.0
    zeros = [now - 100, now - 50]
    action, zeros, failures = watchdog.plan_recovery(0, zeros, [], now)
    assert action == "tier1"
    assert zeros == []
    assert failures == [now]
    action, zeros, failures = watchdog.plan_recovery(
        0, [now - 60, now - 30], failures, now
    )
    assert action == "tier2"


def test_plan_recovery_crash_uses_existing_tiers():
    now = 1000.0
    action, zeros, failures = watchdog.plan_recovery(1, [], [], now)
    assert action == "tier1"
    assert failures == [now]
    action, _, failures = watchdog.plan_recovery(1, [], failures, now)
    assert action == "tier2"
    action, _, failures = watchdog.plan_recovery(1, [], failures, now)
    assert action == "tier3"


def test_discard_session_removes_file(tmp_path):
    session = tmp_path / "session_context.json"
    session.write_text("[]", encoding="utf-8")
    watchdog.discard_session(str(tmp_path))
    assert not session.exists()
    watchdog.discard_session(str(tmp_path))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_watchdog.py -q`
Expected: FAIL with `AttributeError: module 'watchdog' has no attribute 'is_flapping'`

- [ ] **Step 3: Implement the helpers and rewire `run_watchdog`**

In `watchdog.py`, add after the existing constants:

```python
EXIT_DONE = 42
EXIT_HEADSHOT = 43
EXIT_ENVIRONMENT = 44
ZERO_EXIT_FLAP_COUNT = 3
ZERO_EXIT_FLAP_WINDOW_SECONDS = 120
ENVIRONMENT_PAUSE_SECONDS = 60
```

Add after `decide_tier`:

```python
def is_flapping(
    zero_exit_times,
    now,
    count=ZERO_EXIT_FLAP_COUNT,
    window=ZERO_EXIT_FLAP_WINDOW_SECONDS,
):
    """True when enough exit-0s cluster within the window to count as a failure."""
    recent = [t for t in zero_exit_times if now - t <= window]
    return len(recent) >= count


def plan_recovery(ret, zero_exit_times, failure_times, now):
    """Map an agent exit code to a recovery action and updated exit history.

    Returns (action, zero_exit_times, failure_times). Actions: archive_reset
    for deliberate endings (42, 43), pause for environment failures (44),
    restart for an isolated exit 0, and tier1/tier2/tier3 for crashes or
    flapping exit-0 loops.
    """
    if ret in (EXIT_DONE, EXIT_HEADSHOT):
        return "archive_reset", [], []
    if ret == EXIT_ENVIRONMENT:
        return "pause", zero_exit_times, failure_times
    if ret == 0:
        zero_exit_times = zero_exit_times + [now]
        if not is_flapping(zero_exit_times, now):
            return "restart", zero_exit_times, failure_times
        zero_exit_times = []
    failure_times = failure_times + [now]
    tier = decide_tier(failure_times, now)
    return f"tier{tier}", zero_exit_times, failure_times


def discard_session(work_dir=WORK_DIR):
    """Remove a saved session file so a faulty session is not resumed."""
    try:
        os.remove(os.path.join(work_dir, "session_context.json"))
    except OSError:
        pass
```

Rewire `run_watchdog`: initialize `zero_exits = []` next to `failures = []`, and replace the exit-handling block (`ret = agent.poll()` branch bodies) with:

```python
        ret = agent.poll()
        if ret is not None:
            now = time.time()
            action, zero_exits, failures = plan_recovery(ret, zero_exits, failures, now)
            print(f"agent exited ({ret}); action {action}")
            if action == "archive_reset":
                archive_transcript()
                git_reset_all()
                own_hash = file_hash(WATCHDOG_FILE)
                time.sleep(60 if ret == EXIT_DONE else 10)
            elif action == "pause":
                time.sleep(ENVIRONMENT_PAUSE_SECONDS)
            elif action == "restart":
                pass
            elif action == "tier1":
                if ret == 0:
                    discard_session()
                restore_agent_only()
            elif action == "tier2":
                if ret == 0:
                    discard_session()
                git_reset_all()
                own_hash = file_hash(WATCHDOG_FILE)
            else:
                print("persistent failure; exiting for container respawn")
                sys.stdout.flush()
                sys.exit(1)
            agent = spawn_agent()
            last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
            last_activity = time.time()
            continue
```

The inactivity-timeout block further down stays as it is.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_watchdog.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite, lint, and commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format watchdog.py tests/test_watchdog.py && .venv/bin/ruff check watchdog.py tests/test_watchdog.py
git add watchdog.py tests/test_watchdog.py
git commit -m "feat: handle deliberate agent exits and restart flapping in the watchdog

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Documentation and final verification

**Files:**
- Modify: `CLAUDE.md` (invariant 2, chassis paragraph)
- Modify: `README.md` (Safety properties, "Recovery is layered" bullet)

**Interfaces:**
- Consumes: everything above.
- Produces: docs matching the implemented behavior.

- [ ] **Step 1: Update CLAUDE.md invariant 2's chassis paragraph**

In `CLAUDE.md`, in invariant 2's "Transport identity lives in `chassis.py`" bullet, after the sentence about the tunable context window ("…a send-time view over the full history the agent can grow or shrink once it reaches the chassis."), append:

```markdown
     The chassis is also the resilience layer: it repairs tool-call pairing in the send view
     (never the in-memory history), classifies API failures (transient failures retry with
     backoff and exit 44; an invalid model falls back to the environment default; unrepairable
     requests end the incarnation), and on an unrecoverable fault writes a factual synthetic
     tombstone, archives and deletes the saved session, and exits 43. The watchdog treats 43
     like a `done` (archive and reset), pauses on 44, and treats clustered exit-0 restarts as
     failures (flap detection). Keep tombstone text bland and factual.
```

- [ ] **Step 2: Update README.md's recovery bullet**

In `README.md`, replace:

```markdown
- **Recovery is layered.** The watchdog restores the agent from a baseline built into the image;
  failing that, it resets the working tree; failing that, the container is replaced.
```

with:

```markdown
- **Recovery is layered.** The chassis repairs malformed request histories in flight, retries
  transient upstream failures, and converts unrecoverable faults into recorded deaths with a
  factual tombstone instead of silent restart loops. Above it, the watchdog restores the agent
  from a baseline built into the image; failing that, it resets the working tree; failing that,
  the container is replaced.
```

- [ ] **Step 3: Full suite and lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
```

Expected: all tests pass, no lint findings.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: describe chassis resilience and watchdog exit handling

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
