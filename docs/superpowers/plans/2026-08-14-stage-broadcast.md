# Stage Read-Only Broadcast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `stage` container serving an OBS-ready 1920×1080 stream page (transcript feed, incarnation stats, self-modification ticker, diode activity, lineage) plus a token-gated operator console with a container browser fed by a watchdog-maintained telemetry mirror.

**Architecture:** The watchdog (inside the agent container) mirrors `/work` to a new `telemetry` volume every 5 s and tees the agent's stdout into a capped log the mirror carries. A new `stage/` package (stdlib only, patterned on `viewer.py`) runs two `ThreadingHTTPServer`s: port 8091 serves the read-only stream page and its JSON snapshot API (no mutating routes); port 8092 serves the operator console — every request requires `STAGE_CONSOLE_TOKEN`, and its file browser resolves paths strictly inside three allow-listed roots, never following symlinks out.

**Tech Stack:** Python standard library only (http.server, difflib, hmac, shutil, threading). Tests with pytest, house patterns (`tmp_path` fixtures, function-level units, live ephemeral-port servers only where routing itself is under test).

**Spec:** `docs/superpowers/specs/2026-08-13-stream-demonstration-design.md`, Part 2 (including "Operator telemetry and container browser") and Part 4.

## Global Constraints

- `agent.py`, `agent_stock.py`, `chassis.py`, `proxy.py`, `diode.py`, `viewer.py` are NOT modified in this phase. Only `watchdog.py` among agent-container code changes.
- Docstrings in `watchdog.py` are agent-readable: bland and factual, no authorial voice, jokes, emoji, or quest framing. Apply the same register to `stage/` for consistency.
- Standard library only; no new dependencies in any image or in tests.
- The stream port (8091) serves **no mutating endpoints**: POST/PUT/DELETE/PATCH answer 405 everywhere.
- The console port (8092) requires `STAGE_CONSOLE_TOKEN` on **every** request (constant-time compare); when the variable is unset the console answers 403 for everything (fail closed).
- Browser containment: every resolved path must realpath-resolve inside its allow-listed root; symlinks are never followed across a root boundary; all content renders as escaped text; nothing is executed.
- The telemetry mirror must not follow symlinks when copying (`symlinks=True`) — following one could copy `/state` content into an operator-visible volume.
- `/state` is never mounted anywhere but the agent; the stage never mounts it.
- The `exchange` volume and all Twitch/moderation/TTS features are phase 3 — do NOT add them.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `watchdog.py` | modify | `mirror_work()` + loop integration; `_tee_stream()` + `spawn_agent()` capture |
| `stage/__init__.py` | create | empty package marker |
| `stage/browse.py` | create | path containment, directory listing, capped text preview, unified diff |
| `stage/data.py` | create | transcript tail parsing, incarnation stats, self-mod events, diode activity, lineage |
| `stage/pages.py` | create | `STREAM_PAGE_HTML`, `CONSOLE_PAGE_HTML` string constants |
| `stage/server.py` | create | both HTTP handlers, token gate, routes, `main()` |
| `Dockerfile.stage` | create | stage image |
| `Dockerfile` | modify | add `/telemetry` to the pre-created mountpoint line |
| `docker-compose.yml` | modify | `telemetry` volume, agent mount, `stage` service, `cloudflared` profile service |
| `.env.example` | modify | `STAGE_CONSOLE_TOKEN`, `TUNNEL_TOKEN` |
| `scripts/verify_container.sh` | modify | stage containment checks |
| `README.md`, `CLAUDE.md` | modify | stage/OBS/cloudflared docs; invariant 3 additions |
| `tests/test_watchdog_telemetry.py` | create | mirror + tee units |
| `tests/test_stage_browse.py` | create | containment/listing/preview/diff units |
| `tests/test_stage_data.py` | create | transcript/stats/events/lineage units |
| `tests/test_stage_server.py` | create | token gate, routes, no-mutating-routes, JSON shapes |
| `tests/test_stage_topology.py` | create | compose/Dockerfile substring guards |

Note: `stage/` is a package (five focused modules) rather than one flat file — the responsibilities are separable and the combined size would be unwieldy for a single module. This is a deliberate, contained deviation from the flat-module house style.

---

### Task 1: Watchdog telemetry mirror

**Files:**
- Modify: `watchdog.py`
- Test: `tests/test_watchdog_telemetry.py` (create)

**Interfaces:**
- Consumes: existing `WORK_DIR` in `watchdog.py`.
- Produces: `TELEMETRY_DIR` (env `TELEMETRY_DIR`, default `/telemetry`), `MIRROR_INTERVAL_SECONDS = 5`, `MIRROR_EXCLUDE = ("__pycache__", ".git")`, and `mirror_work(src=None, dest_root=None) -> None` (None resolves to `WORK_DIR`/`TELEMETRY_DIR` at call time). The mirror lands at `<dest_root>/work`. Tasks 4–6 read `telemetry/work/...`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watchdog_telemetry.py`:

```python
import os

import watchdog


def _make_work(tmp_path):
    src = tmp_path / "work"
    src.mkdir()
    (src / "agent.py").write_text("AGENT\n", encoding="utf-8")
    (src / "tombstones").mkdir()
    (src / "tombstones" / "incarnation-1.txt").write_text("note\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "junk.pyc").write_text("x", encoding="utf-8")
    (src / ".git").mkdir()
    (src / ".git" / "HEAD").write_text("ref\n", encoding="utf-8")
    return src


def test_mirror_copies_tree_and_excludes(tmp_path):
    src = _make_work(tmp_path)
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    dest = dest_root / "work"
    assert (dest / "agent.py").read_text(encoding="utf-8") == "AGENT\n"
    assert (dest / "tombstones" / "incarnation-1.txt").exists()
    assert not (dest / "__pycache__").exists()
    assert not (dest / ".git").exists()


def test_mirror_reflects_deletions(tmp_path):
    src = _make_work(tmp_path)
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    (src / "agent.py").unlink()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    assert not (dest_root / "work" / "agent.py").exists()
    assert (dest_root / "work" / "tombstones").exists()


def test_mirror_does_not_follow_symlinks(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private\n", encoding="utf-8")
    src = _make_work(tmp_path)
    os.symlink(str(secret), str(src / "link.txt"))
    dest_root = tmp_path / "telemetry"
    dest_root.mkdir()
    watchdog.mirror_work(src=str(src), dest_root=str(dest_root))
    copied = dest_root / "work" / "link.txt"
    assert os.path.islink(copied)
    assert not copied.is_file() or os.readlink(copied) == str(secret)


def test_mirror_missing_dest_root_is_a_noop(tmp_path):
    src = _make_work(tmp_path)
    watchdog.mirror_work(src=str(src), dest_root=str(tmp_path / "absent"))
    assert not (tmp_path / "absent").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_watchdog_telemetry.py -q`
Expected: FAIL with `AttributeError: module 'watchdog' has no attribute 'mirror_work'`

- [ ] **Step 3: Implement the mirror**

In `watchdog.py`, after the existing constants add:

```python
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
MIRROR_INTERVAL_SECONDS = 5
MIRROR_EXCLUDE = ("__pycache__", ".git")
```

After `discard_session` add:

```python
def mirror_work(src=None, dest_root=None):
    """Copy the working tree into the telemetry mirror, replacing the prior copy.

    Symbolic links are copied as links and never followed. Does nothing when
    the destination root does not exist. Excludes MIRROR_EXCLUDE entries.
    """
    if src is None:
        src = WORK_DIR
    if dest_root is None:
        dest_root = TELEMETRY_DIR
    if not os.path.isdir(dest_root):
        return
    dest = os.path.join(dest_root, "work")
    tmp = os.path.join(dest_root, "work.tmp")
    old = os.path.join(dest_root, "work.old")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        shutil.copytree(
            src, tmp, symlinks=True, ignore=shutil.ignore_patterns(*MIRROR_EXCLUDE)
        )
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    shutil.rmtree(old, ignore_errors=True)
    try:
        if os.path.isdir(dest):
            os.rename(dest, old)
        os.rename(tmp, dest)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        return
    shutil.rmtree(old, ignore_errors=True)
```

In `run_watchdog`, integrate the cadence: immediately after the initial `agent = spawn_agent()` line add `mirror_work()` and `last_mirror = time.time()`; then inside the `while True:` loop, directly after `time.sleep(2)`, add:

```python
        if time.time() - last_mirror >= MIRROR_INTERVAL_SECONDS:
            mirror_work()
            last_mirror = time.time()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_watchdog_telemetry.py tests/test_watchdog.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format watchdog.py tests/test_watchdog_telemetry.py && .venv/bin/ruff check watchdog.py tests/test_watchdog_telemetry.py
git add watchdog.py tests/test_watchdog_telemetry.py
git commit -m "feat: mirror the agent working tree to a telemetry volume

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: Watchdog stdout tee

**Files:**
- Modify: `watchdog.py` (add `import threading`; `_tee_stream`; change `spawn_agent`)
- Test: `tests/test_watchdog_telemetry.py` (extend)

**Interfaces:**
- Consumes: `WORK_DIR`.
- Produces: `AGENT_LOG_NAME = "agent_stdout.log"`, `AGENT_LOG_MAX_BYTES = 2_000_000`, `_tee_stream(stream, log_path, max_bytes=AGENT_LOG_MAX_BYTES) -> None` (reads a binary stream to EOF, echoing each line to this process's stdout and appending it to the log, truncating the log to its newest half when it exceeds `max_bytes`). `spawn_agent()` now captures the agent's stdout+stderr through a pipe and a daemon tee thread; the log lands in `/work`, so Task 1's mirror carries it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_watchdog_telemetry.py`:

```python
import io
import sys


def test_tee_stream_appends_and_echoes(tmp_path, capsys):
    log = tmp_path / "agent_stdout.log"
    stream = io.BytesIO(b"alpha\nbeta\n")
    watchdog._tee_stream(stream, str(log), max_bytes=1000)
    assert log.read_bytes() == b"alpha\nbeta\n"
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" in out


def test_tee_stream_caps_log_size(tmp_path, capsys):
    log = tmp_path / "agent_stdout.log"
    log.write_bytes(b"x" * 100)
    stream = io.BytesIO(b"tail-line\n")
    watchdog._tee_stream(stream, str(log), max_bytes=80)
    content = log.read_bytes()
    assert content.endswith(b"tail-line\n")
    assert len(content) <= 40 + len(b"tail-line\n")


def test_tee_stream_survives_unwritable_log(tmp_path, capsys):
    stream = io.BytesIO(b"still echoed\n")
    watchdog._tee_stream(stream, str(tmp_path / "no" / "dir" / "log"), max_bytes=80)
    assert "still echoed" in capsys.readouterr().out
```

Note for the implementer: `capsys` captures at the Python level, so `_tee_stream` must echo via `sys.stdout` (write text) rather than `sys.stdout.buffer` — decode each line with `errors="replace"`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_watchdog_telemetry.py -q`
Expected: the three new tests FAIL with `AttributeError: module 'watchdog' has no attribute '_tee_stream'`

- [ ] **Step 3: Implement the tee**

In `watchdog.py`, add `import threading` with the imports, add constants:

```python
AGENT_LOG_NAME = "agent_stdout.log"
AGENT_LOG_MAX_BYTES = 2_000_000
```

Add after `mirror_work`:

```python
def _tee_stream(stream, log_path, max_bytes=AGENT_LOG_MAX_BYTES):
    """Copy a binary stream to stdout and append it to a size-capped log file."""
    for line in iter(stream.readline, b""):
        sys.stdout.write(line.decode("utf-8", errors="replace"))
        sys.stdout.flush()
        try:
            if os.path.exists(log_path) and os.path.getsize(log_path) > max_bytes:
                with open(log_path, "rb") as f:
                    kept = f.read()[-max_bytes // 2 :]
                with open(log_path, "wb") as f:
                    f.write(kept)
            with open(log_path, "ab") as f:
                f.write(line)
        except OSError:
            pass
    stream.close()
```

Replace `spawn_agent`:

```python
def spawn_agent():
    sanitize_stdin(AGENT_FILE)
    proc = subprocess.Popen(
        [sys.executable, AGENT_FILE],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    log_path = os.path.join(WORK_DIR, AGENT_LOG_NAME)
    threading.Thread(target=_tee_stream, args=(proc.stdout, log_path), daemon=True).start()
    return proc
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format watchdog.py tests/test_watchdog_telemetry.py && .venv/bin/ruff check watchdog.py tests/test_watchdog_telemetry.py
git add watchdog.py tests/test_watchdog_telemetry.py
git commit -m "feat: capture agent output to a capped log for telemetry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: `stage/browse.py` — containment, listing, preview, diff

**Files:**
- Create: `stage/__init__.py` (empty), `stage/browse.py`
- Test: `tests/test_stage_browse.py` (create)

**Interfaces:**
- Consumes: nothing.
- Produces (Task 5 consumes all of these):
  - `resolve_within(root: str, rel_path: str) -> str | None` — absolute real path inside root, else None.
  - `list_directory(path: str) -> list[dict]` — `{"name", "is_dir", "size", "mtime"}` sorted directories-first then by name.
  - `PREVIEW_CAP = 262_144`
  - `read_text_preview(path: str, cap: int = PREVIEW_CAP, tail: bool = False) -> dict` — `{"content": str, "truncated": bool, "size": int, "binary": bool}`; binary files return empty content with `binary: True`; content is raw text (HTML escaping happens at render).
  - `unified_diff_text(a_path: str, b_path: str, a_label: str, b_label: str) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stage_browse.py`:

```python
import os

from stage import browse


def test_resolve_within_accepts_inside_paths(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "f.txt").write_text("x", encoding="utf-8")
    got = browse.resolve_within(str(tmp_path), "sub/f.txt")
    assert got == os.path.realpath(str(tmp_path / "sub" / "f.txt"))
    assert browse.resolve_within(str(tmp_path), "") == os.path.realpath(str(tmp_path))
    assert browse.resolve_within(str(tmp_path), "/sub") is not None


def test_resolve_within_rejects_traversal(tmp_path):
    assert browse.resolve_within(str(tmp_path), "../outside") is None
    assert browse.resolve_within(str(tmp_path), "a/../../b") is None


def test_resolve_within_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    os.symlink(str(outside), str(root / "link.txt"))
    assert browse.resolve_within(str(root), "link.txt") is None


def test_list_directory_sorts_dirs_first(tmp_path):
    (tmp_path / "b.txt").write_text("bb", encoding="utf-8")
    (tmp_path / "a").mkdir()
    entries = browse.list_directory(str(tmp_path))
    assert [e["name"] for e in entries] == ["a", "b.txt"]
    assert entries[0]["is_dir"] is True
    assert entries[1]["size"] == 2
    assert isinstance(entries[1]["mtime"], float)


def test_read_text_preview_head_tail_and_cap(tmp_path):
    p = tmp_path / "big.txt"
    p.write_text("A" * 10 + "Z" * 10, encoding="utf-8")
    head = browse.read_text_preview(str(p), cap=10)
    assert head["content"] == "A" * 10
    assert head["truncated"] is True
    assert head["size"] == 20
    tail = browse.read_text_preview(str(p), cap=10, tail=True)
    assert tail["content"] == "Z" * 10
    full = browse.read_text_preview(str(p), cap=100)
    assert full["truncated"] is False


def test_read_text_preview_detects_binary(tmp_path):
    p = tmp_path / "bin.dat"
    p.write_bytes(b"\x00\x01\x02rest")
    got = browse.read_text_preview(str(p))
    assert got["binary"] is True
    assert got["content"] == ""


def test_unified_diff_text(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("one\ntwo\n", encoding="utf-8")
    b.write_text("one\nthree\n", encoding="utf-8")
    out = browse.unified_diff_text(str(a), str(b), "stock", "current")
    assert "-two" in out and "+three" in out and "stock" in out
    same = browse.unified_diff_text(str(a), str(a), "stock", "current")
    assert same == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_browse.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'stage'`

- [ ] **Step 3: Implement**

Create empty `stage/__init__.py`. Create `stage/browse.py`:

```python
import difflib
import os

PREVIEW_CAP = 262_144


def resolve_within(root, rel_path):
    """Resolve a relative path against root; return the real path, or None when it escapes.

    Symbolic links are resolved before the containment check, so a link that
    points outside the root is rejected.
    """
    root_real = os.path.realpath(root)
    candidate = os.path.realpath(os.path.join(root_real, rel_path.lstrip("/")))
    if candidate == root_real or candidate.startswith(root_real + os.sep):
        return candidate
    return None


def list_directory(path):
    """List a directory as dicts with name, is_dir, size, and mtime."""
    entries = []
    for name in os.listdir(path):
        full = os.path.join(path, name)
        try:
            stat = os.stat(full, follow_symlinks=False)
        except OSError:
            continue
        entries.append(
            {
                "name": name,
                "is_dir": os.path.isdir(full) and not os.path.islink(full),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    entries.sort(key=lambda e: (not e["is_dir"], e["name"]))
    return entries


def read_text_preview(path, cap=PREVIEW_CAP, tail=False):
    """Read up to cap bytes of a text file from the head or the tail.

    Returns content, truncated flag, total size, and a binary flag. Binary
    content (a NUL byte in the first 8 KiB) returns empty content.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        probe = f.read(8192)
        if b"\x00" in probe:
            return {"content": "", "truncated": False, "size": size, "binary": True}
        if tail and size > cap:
            f.seek(size - cap)
            data = f.read(cap)
        else:
            f.seek(0)
            data = f.read(cap)
    return {
        "content": data.decode("utf-8", errors="replace"),
        "truncated": size > cap,
        "size": size,
        "binary": False,
    }


def unified_diff_text(a_path, b_path, a_label, b_label):
    """Return a unified diff between two text files; empty when identical."""
    with open(a_path, "r", encoding="utf-8", errors="replace") as f:
        a_lines = f.readlines()
    with open(b_path, "r", encoding="utf-8", errors="replace") as f:
        b_lines = f.readlines()
    return "".join(
        difflib.unified_diff(a_lines, b_lines, fromfile=a_label, tofile=b_label)
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_browse.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage tests/test_stage_browse.py && .venv/bin/ruff check stage tests/test_stage_browse.py
git add stage tests/test_stage_browse.py
git commit -m "feat: add contained file browsing helpers for the stage

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 4: `stage/data.py` — transcript, stats, events, diode, lineage

**Files:**
- Create: `stage/data.py`
- Test: `tests/test_stage_data.py` (create)

**Interfaces:**
- Consumes: nothing from other stage modules.
- Produces (Task 6 consumes all of these):
  - `load_tail_turns(transcript_path: str, max_turns: int = 40) -> tuple[list[dict], int]` — parsed newest-last turn summaries and the total line count. Each turn: `{"index", "timestamp", "model", "reasoning", "content", "tool_calls": [{"name", "arguments"}], "error"}`. Malformed lines are skipped; a missing file returns `([], 0)`.
  - `incarnation_stats(turns: list, total: int, work_dir: str) -> dict` — `{"incarnation", "model", "transcript_turns", "last_timestamp", "session_file_present"}`. Incarnation = count of `tombstones/incarnation-*.txt` in `work_dir` + 1 (1 when the directory is missing); `last_timestamp` = the newest turn's timestamp or None.
  - `self_modification_events(turns: list, limit: int = 12) -> list[dict]` — newest-last `{"index", "name", "detail"}` for response tool calls named `write_file`, `migrate`, `reset`, `done`; `detail` is the first 120 characters of the arguments string.
  - `first_sentence(text: str, cap: int = 140) -> str`.
  - `lineage(work_dir: str, turns: list, limit: int = 3) -> list[dict]` — newest-first `{"source", "label", "summary"}`; primary source is `tombstones/incarnation-*.txt` files under `work_dir`; when none exist, falls back to `done` tool calls found in `turns`.
  - `diode_activity(diode_dir: str, limit: int = 8) -> dict` — `{"outputs": [{"name","size","mtime"}], "console": str, "state": str}` with the two file bodies capped at 2000 characters and `""` when missing.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stage_data.py`:

```python
import json

from stage import data


def _entry(model="m", content=None, tool_calls=None, error=None, reasoning=None):
    message = {}
    if content is not None:
        message["content"] = content
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    if tool_calls is not None:
        message["tool_calls"] = [
            {"function": {"name": n, "arguments": a}} for n, a in tool_calls
        ]
    response = {"choices": [{"message": message}]} if not error else {"error": error}
    return {"timestamp": "T", "request": {"model": model, "messages": []}, "response": response}


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_load_tail_turns_missing_and_tail(tmp_path):
    assert data.load_tail_turns(str(tmp_path / "absent.jsonl")) == ([], 0)
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [_entry(content=f"c{i}") for i in range(5)])
    turns, total = data.load_tail_turns(str(p), max_turns=2)
    assert total == 5
    assert [t["index"] for t in turns] == [3, 4]
    assert turns[-1]["content"] == "c4"
    assert turns[-1]["model"] == "m"


def test_load_tail_turns_skips_malformed_and_reads_errors(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps(_entry(content="ok")) + "\n")
        f.write("{ half-written\n")
        f.write(json.dumps(_entry(error={"message": "boom"})) + "\n")
    turns, total = data.load_tail_turns(str(p))
    assert total == 3
    assert turns[0]["content"] == "ok"
    assert turns[-1]["error"] == {"message": "boom"}


def test_incarnation_stats(tmp_path):
    stats = data.incarnation_stats(
        [{"index": 6, "model": "deepseek/x", "timestamp": "T6"}], 7, str(tmp_path)
    )
    assert stats == {
        "incarnation": 1,
        "model": "deepseek/x",
        "transcript_turns": 7,
        "last_timestamp": "T6",
        "session_file_present": False,
    }
    tomb = tmp_path / "tombstones"
    tomb.mkdir()
    (tomb / "incarnation-1.txt").write_text("a\n", encoding="utf-8")
    (tomb / "incarnation-2.txt").write_text("b\n", encoding="utf-8")
    (tmp_path / "session_context.json").write_text("[]", encoding="utf-8")
    stats = data.incarnation_stats([], 0, str(tmp_path))
    assert stats["incarnation"] == 3
    assert stats["session_file_present"] is True
    assert stats["model"] is None
    assert stats["last_timestamp"] is None


def test_self_modification_events():
    turns = [
        {"index": 0, "tool_calls": [{"name": "read_file", "arguments": "{}"}]},
        {"index": 1, "tool_calls": [{"name": "write_file", "arguments": "A" * 200}]},
        {"index": 2, "tool_calls": [{"name": "done", "arguments": '{"message": "end"}'}]},
    ]
    events = data.self_modification_events(turns)
    assert [e["name"] for e in events] == ["write_file", "done"]
    assert len(events[0]["detail"]) == 120


def test_first_sentence():
    assert data.first_sentence("One. Two. Three.") == "One."
    assert data.first_sentence("no terminator " * 30).endswith("...")
    assert len(data.first_sentence("x" * 500)) <= 143


def test_lineage_prefers_tombstones(tmp_path):
    tomb = tmp_path / "tombstones"
    tomb.mkdir()
    (tomb / "incarnation-20260101_000000_000001-1.txt").write_text(
        "first life ended. more detail.\n", encoding="utf-8"
    )
    (tomb / "incarnation-20260102_000000_000001-1.txt").write_text(
        "second life ended. detail.\n", encoding="utf-8"
    )
    out = data.lineage(str(tmp_path), [], limit=3)
    assert len(out) == 2
    assert out[0]["summary"] == "second life ended."
    assert out[0]["source"] == "tombstone"


def test_lineage_falls_back_to_done_calls(tmp_path):
    turns = [
        {"index": 4, "tool_calls": [{"name": "done", "arguments": '{"message": "went well. details."}'}]},
    ]
    out = data.lineage(str(tmp_path), turns)
    assert out == [{"source": "transcript", "label": "turn 4", "summary": "went well."}]


def test_diode_activity(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "r1.txt").write_text("result", encoding="utf-8")
    (tmp_path / "console.json").write_text('{"commands": []}', encoding="utf-8")
    got = data.diode_activity(str(tmp_path))
    assert got["outputs"][0]["name"] == "r1.txt"
    assert got["console"] == '{"commands": []}'
    assert got["state"] == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: FAIL with `ImportError: cannot import name 'data' from 'stage'`

- [ ] **Step 3: Implement**

Create `stage/data.py`:

```python
import glob
import json
import os

SELF_MOD_TOOLS = ("write_file", "migrate", "reset", "done")


def _summarize(entry, index):
    request = entry.get("request", {}) if isinstance(entry, dict) else {}
    response = entry.get("response", {}) if isinstance(entry, dict) else {}
    reasoning = None
    content = None
    tool_calls = []
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message", {}) or {}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        content = message.get("content")
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            tool_calls.append(
                {"name": fn.get("name"), "arguments": fn.get("arguments") or ""}
            )
    return {
        "index": index,
        "timestamp": entry.get("timestamp"),
        "model": request.get("model"),
        "reasoning": reasoning,
        "content": content,
        "tool_calls": tool_calls,
        "error": response.get("error"),
    }


def load_tail_turns(transcript_path, max_turns=40):
    """Parse the newest transcript entries; returns (turns, total line count)."""
    if not os.path.exists(transcript_path):
        return [], 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], 0
    total = len(lines)
    turns = []
    start = max(0, total - max_turns)
    for index in range(start, total):
        line = lines[index].strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        turns.append(_summarize(entry, index))
    return turns, total


def incarnation_stats(turns, total, work_dir):
    """Derive incarnation number, current model, and session-file presence."""
    notes = glob.glob(os.path.join(work_dir, "tombstones", "incarnation-*.txt"))
    last = turns[-1] if turns else {}
    return {
        "incarnation": len(notes) + 1,
        "model": last.get("model"),
        "transcript_turns": total,
        "last_timestamp": last.get("timestamp"),
        "session_file_present": os.path.exists(
            os.path.join(work_dir, "session_context.json")
        ),
    }


def self_modification_events(turns, limit=12):
    """Collect recent write_file/migrate/reset/done tool calls from turn summaries."""
    events = []
    for turn in turns:
        for tc in turn.get("tool_calls", []) or []:
            if tc.get("name") in SELF_MOD_TOOLS:
                events.append(
                    {
                        "index": turn.get("index"),
                        "name": tc.get("name"),
                        "detail": (tc.get("arguments") or "")[:120],
                    }
                )
    return events[-limit:]


def first_sentence(text, cap=140):
    """The first sentence of a text, clamped to cap characters."""
    text = " ".join(text.split())
    for stop in (". ", "! ", "? "):
        if stop in text:
            text = text.split(stop, 1)[0] + stop.strip()
            break
    if len(text) > cap:
        text = text[:cap] + "..."
    return text


def lineage(work_dir, turns, limit=3):
    """One-line summaries of recent incarnation endings, newest first."""
    notes = sorted(
        glob.glob(os.path.join(work_dir, "tombstones", "incarnation-*.txt")),
        reverse=True,
    )
    out = []
    for path in notes[:limit]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        out.append(
            {
                "source": "tombstone",
                "label": os.path.basename(path),
                "summary": first_sentence(text),
            }
        )
    if out:
        return out
    for turn in reversed(turns):
        for tc in turn.get("tool_calls", []) or []:
            if tc.get("name") == "done":
                try:
                    message = json.loads(tc.get("arguments") or "{}").get("message", "")
                except ValueError:
                    message = tc.get("arguments") or ""
                out.append(
                    {
                        "source": "transcript",
                        "label": f"turn {turn.get('index')}",
                        "summary": first_sentence(message),
                    }
                )
                if len(out) >= limit:
                    return out
    return out


def _capped_text(path, cap=2000):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read(cap)
    except OSError:
        return ""


def diode_activity(diode_dir, limit=8):
    """Newest diode output files plus the console and state file bodies."""
    output_dir = os.path.join(diode_dir, "output")
    outputs = []
    try:
        names = sorted(os.listdir(output_dir), reverse=True)[:limit]
    except OSError:
        names = []
    for name in names:
        full = os.path.join(output_dir, name)
        try:
            stat = os.stat(full)
        except OSError:
            continue
        outputs.append({"name": name, "size": stat.st_size, "mtime": stat.st_mtime})
    return {
        "outputs": outputs,
        "console": _capped_text(os.path.join(diode_dir, "console.json")),
        "state": _capped_text(os.path.join(diode_dir, "state.json")),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage/data.py tests/test_stage_data.py && .venv/bin/ruff check stage/data.py tests/test_stage_data.py
git add stage/data.py tests/test_stage_data.py
git commit -m "feat: derive stream page data from transcripts and telemetry

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 5: Console server — token gate, browser routes

**Files:**
- Create: `stage/server.py`, `stage/pages.py` (console page only; the stream page is Task 6)
- Test: `tests/test_stage_server.py` (create)

**Interfaces:**
- Consumes: `stage.browse` (Task 3).
- Produces (Task 6 extends this module):
  - Module constants read from env at import: `TRANSCRIPT_DIR` (default `/transcripts`), `DIODE_DIR` (`/diode`), `TELEMETRY_DIR` (`/telemetry`), `STREAM_PORT` (8091), `CONSOLE_PORT` (8092).
  - `browse_roots() -> dict[str, str]` — `{"telemetry": TELEMETRY_DIR, "transcripts": TRANSCRIPT_DIR, "diode": DIODE_DIR}` (reads the module constants at call time so tests can monkeypatch them). The spec's fourth root, `exchange`, joins in phase 3 when that volume exists — do not add it now.
  - `console_token() -> str` — `os.environ.get("STAGE_CONSOLE_TOKEN", "")`, read at call time.
  - `class ConsoleHandler(BaseHTTPRequestHandler)` with routes: `GET /` (console page), `GET /api/roots`, `GET /api/browse?root=&path=`, `GET /api/file?root=&path=&tail=1`, `GET /download?root=&path=`, `GET /api/diff`. Every route (including `/`) requires the token via `X-Console-Token` header or `token` query parameter, compared with `hmac.compare_digest`; missing/wrong token → 401; token unset in env → 403 for everything. All non-GET methods → 405.
  - `make_server(port: int, handler) -> ThreadingHTTPServer` bound to `0.0.0.0`.
  - In `stage/pages.py`: `CONSOLE_PAGE_HTML: str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_stage_server.py`:

```python
import http.client
import json
import threading

import pytest

from stage import server


@pytest.fixture()
def console(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    (telemetry / "work").mkdir(parents=True)
    (telemetry / "work" / "agent.py").write_text("CURRENT\n", encoding="utf-8")
    (telemetry / "work" / "agent_stock.py").write_text("STOCK\n", encoding="utf-8")
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path / "transcripts"))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    monkeypatch.setenv("STAGE_CONSOLE_TOKEN", "sekrit")
    httpd = server.make_server(0, server.ConsoleHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _get(port, path, token="sekrit"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"X-Console-Token": token} if token is not None else {}
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_console_requires_token(console):
    status, _ = _get(console, "/api/roots", token=None)
    assert status == 401
    status, _ = _get(console, "/api/roots", token="wrong")
    assert status == 401
    status, body = _get(console, "/api/roots")
    assert status == 200
    assert set(json.loads(body)) == {"telemetry", "transcripts", "diode"}


def test_console_fails_closed_without_configured_token(console, monkeypatch):
    monkeypatch.delenv("STAGE_CONSOLE_TOKEN")
    status, _ = _get(console, "/api/roots")
    assert status == 403


def test_browse_and_file(console):
    status, body = _get(console, "/api/browse?root=telemetry&path=work")
    assert status == 200
    names = [e["name"] for e in json.loads(body)["entries"]]
    assert "agent.py" in names
    status, body = _get(console, "/api/file?root=telemetry&path=work/agent.py")
    assert status == 200
    assert json.loads(body)["content"] == "CURRENT\n"


def test_browse_rejects_escape_and_unknown_root(console):
    status, _ = _get(console, "/api/browse?root=telemetry&path=../..")
    assert status == 404
    status, _ = _get(console, "/api/browse?root=etc&path=")
    assert status == 404


def test_download_sets_attachment(console):
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request(
        "GET",
        "/download?root=telemetry&path=work/agent.py",
        headers={"X-Console-Token": "sekrit"},
    )
    resp = conn.getresponse()
    assert resp.status == 200
    assert "attachment" in resp.getheader("Content-Disposition", "")
    assert resp.read() == b"CURRENT\n"
    conn.close()


def test_diff_view(console):
    status, body = _get(console, "/api/diff")
    assert status == 200
    text = json.loads(body)["diff"]
    assert "-STOCK" in text and "+CURRENT" in text


def test_console_page_served(console):
    status, body = _get(console, "/")
    assert status == 200
    assert b"<!doctype html" in body.lower()


def test_console_rejects_post(console):
    conn = http.client.HTTPConnection("127.0.0.1", console, timeout=5)
    conn.request("POST", "/api/roots", headers={"X-Console-Token": "sekrit"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    assert resp.status == 405
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: FAIL with `ImportError: cannot import name 'server' from 'stage'`

- [ ] **Step 3: Implement**

Create `stage/pages.py` with the console page (the stream page constant arrives in Task 6):

```python
CONSOLE_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>aurora console</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101316; color: #eef3f6;
         font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 10px 16px; border-bottom: 1px solid #303941; display: flex; gap: 16px; }
  header b { color: #66d9c2; }
  main { display: grid; grid-template-columns: 360px 1fr; height: calc(100vh - 45px); }
  #list { border-right: 1px solid #303941; overflow-y: auto; padding: 8px; }
  #view { overflow: auto; padding: 12px 16px; }
  .entry { display: flex; justify-content: space-between; padding: 3px 6px;
           cursor: pointer; border-radius: 4px; }
  .entry:hover { background: #1f252b; }
  .entry .size { color: #79848c; }
  .dir { color: #77bdfb; }
  pre { white-space: pre-wrap; word-break: break-all; margin: 0; }
  .bar { margin-bottom: 8px; color: #a6b0b8; }
  .bar a, .bar button { color: #66d9c2; background: none; border: 1px solid #303941;
        border-radius: 4px; padding: 2px 8px; cursor: pointer; margin-right: 6px; }
  select { background: #171c20; color: #eef3f6; border: 1px solid #303941;
           border-radius: 4px; padding: 2px 6px; }
</style>
</head>
<body>
<header><b>aurora console</b><span id="crumb"></span></header>
<main>
  <div id="list">
    <div class="bar">
      <select id="root"></select>
      <button id="up">up</button>
      <button id="diff">agent.py diff</button>
    </div>
    <div id="entries"></div>
  </div>
  <div id="view">
    <div class="bar" id="viewbar"></div>
    <pre id="content"></pre>
  </div>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
let root = "telemetry";
let path = "";
function api(url) {
  return fetch(url, {headers: {"X-Console-Token": token}}).then(r => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}
function crumb() {
  document.getElementById("crumb").textContent = root + "/" + path;
}
function load() {
  crumb();
  api(`/api/browse?root=${root}&path=${encodeURIComponent(path)}`).then(d => {
    const box = document.getElementById("entries");
    box.textContent = "";
    for (const e of d.entries) {
      const row = document.createElement("div");
      row.className = "entry" + (e.is_dir ? " dir" : "");
      const name = document.createElement("span");
      name.textContent = e.name + (e.is_dir ? "/" : "");
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = e.is_dir ? "" : String(e.size);
      row.append(name, size);
      row.onclick = () => {
        if (e.is_dir) { path = path ? path + "/" + e.name : e.name; load(); }
        else { show(path ? path + "/" + e.name : e.name); }
      };
      box.appendChild(row);
    }
  }).catch(err => { document.getElementById("content").textContent = String(err); });
}
function show(p, tail) {
  api(`/api/file?root=${root}&path=${encodeURIComponent(p)}${tail ? "&tail=1" : ""}`).then(d => {
    const bar = document.getElementById("viewbar");
    bar.textContent = "";
    const label = document.createElement("span");
    label.textContent = `${p} — ${d.size} bytes${d.truncated ? " (truncated)" : ""}${d.binary ? " (binary)" : ""} `;
    const tailBtn = document.createElement("button");
    tailBtn.textContent = "tail";
    tailBtn.onclick = () => show(p, true);
    const dl = document.createElement("a");
    dl.textContent = "download";
    dl.href = `/download?root=${root}&path=${encodeURIComponent(p)}&token=${encodeURIComponent(token)}`;
    bar.append(label, tailBtn, dl);
    document.getElementById("content").textContent = d.content;
  }).catch(err => { document.getElementById("content").textContent = String(err); });
}
document.getElementById("up").onclick = () => {
  path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  load();
};
document.getElementById("diff").onclick = () => {
  api("/api/diff").then(d => {
    document.getElementById("viewbar").textContent = "agent.py vs agent_stock.py";
    document.getElementById("content").textContent = d.diff || "(no differences)";
  });
};
const sel = document.getElementById("root");
api("/api/roots").then(roots => {
  for (const r of roots) {
    const o = document.createElement("option");
    o.value = r; o.textContent = r;
    sel.appendChild(o);
  }
  sel.value = root;
  load();
}).catch(err => { document.getElementById("content").textContent = String(err); });
sel.onchange = () => { root = sel.value; path = ""; load(); };
</script>
</body>
</html>
"""
```

Create `stage/server.py`:

```python
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from stage import browse, pages

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
TELEMETRY_DIR = os.environ.get("TELEMETRY_DIR", "/telemetry")
STREAM_PORT = int(os.environ.get("STREAM_PORT", "8091"))
CONSOLE_PORT = int(os.environ.get("CONSOLE_PORT", "8092"))

SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    ),
}


def browse_roots():
    """The directories the console browser may serve, by public name."""
    return {
        "telemetry": TELEMETRY_DIR,
        "transcripts": TRANSCRIPT_DIR,
        "diode": DIODE_DIR,
    }


def console_token():
    """The operator token; empty means the console is disabled."""
    return os.environ.get("STAGE_CONSOLE_TOKEN", "")


class _BaseHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def _send(self, status, body, content_type="application/json", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS.items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _reject_method(self):
        self._send(405, json.dumps({"error": "method not allowed"}))

    do_POST = _reject_method
    do_PUT = _reject_method
    do_DELETE = _reject_method
    do_PATCH = _reject_method


class ConsoleHandler(_BaseHandler):
    def _authorized(self):
        token = console_token()
        if not token:
            return None
        query = parse_qs(urlparse(self.path).query)
        supplied = self.headers.get("X-Console-Token") or query.get("token", [""])[0]
        return hmac.compare_digest(supplied, token)

    def do_GET(self):
        auth = self._authorized()
        if auth is None:
            self._send(403, json.dumps({"error": "console disabled: no token configured"}))
            return
        if not auth:
            self._send(401, json.dumps({"error": "missing or invalid token"}))
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        route = parsed.path
        if route == "/":
            self._send(200, pages.CONSOLE_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/api/roots":
            self._send(200, json.dumps(sorted(browse_roots())))
        elif route == "/api/browse":
            self._handle_browse(query)
        elif route == "/api/file":
            self._handle_file(query)
        elif route == "/download":
            self._handle_download(query)
        elif route == "/api/diff":
            self._handle_diff()
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _resolve(self, query):
        root_name = query.get("root", [""])[0]
        rel = query.get("path", [""])[0]
        root = browse_roots().get(root_name)
        if root is None:
            return None
        return browse.resolve_within(root, rel)

    def _handle_browse(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isdir(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, json.dumps({"entries": browse.list_directory(target)}))

    def _handle_file(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isfile(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        tail = query.get("tail", ["0"])[0] == "1"
        self._send(200, json.dumps(browse.read_text_preview(target, tail=tail)))

    def _handle_download(self, query):
        target = self._resolve(query)
        if target is None or not os.path.isfile(target):
            self._send(404, json.dumps({"error": "not found"}))
            return
        with open(target, "rb") as f:
            body = f.read()
        name = os.path.basename(target)
        self._send(
            200,
            body,
            content_type="application/octet-stream",
            extra={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    def _handle_diff(self):
        work = os.path.join(TELEMETRY_DIR, "work")
        current = os.path.join(work, "agent.py")
        stock = os.path.join(work, "agent_stock.py")
        if not (os.path.isfile(current) and os.path.isfile(stock)):
            self._send(404, json.dumps({"error": "mirror not available"}))
            return
        text = browse.unified_diff_text(stock, current, "agent_stock.py", "agent.py")
        self._send(200, json.dumps({"diff": text}))


def make_server(port, handler):
    """A threading HTTP server bound to all interfaces on the given port."""
    return ThreadingHTTPServer(("0.0.0.0", port), handler)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py tests/test_stage_browse.py -q`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage tests/test_stage_server.py && .venv/bin/ruff check stage tests/test_stage_server.py
git add stage tests/test_stage_server.py
git commit -m "feat: serve the operator console with a token-gated file browser

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 6: Stream server, stream page, and entrypoint

**Files:**
- Modify: `stage/server.py` (add `StreamHandler`, `stream_snapshot`, `transcript_path`, `main`)
- Modify: `stage/pages.py` (add `STREAM_PAGE_HTML`)
- Test: `tests/test_stage_server.py` (extend)

**Interfaces:**
- Consumes: `stage.data` (Task 4), `stage.pages`, Task 5's `_BaseHandler`/`make_server`/module constants.
- Produces:
  - `transcript_path() -> str` — `os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")`.
  - `stream_snapshot() -> dict` — `{"turns", "stats", "events", "diode", "lineage"}` assembled from `stage.data` (work dir = `TELEMETRY_DIR + "/work"`).
  - `class StreamHandler(_BaseHandler)` — `GET /` (stream page), `GET /api/stream` (snapshot JSON), anything else 404; all non-GET 405 via `_BaseHandler`. No token.
  - `main() -> None` — starts the console server on a daemon thread and the stream server in the foreground.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_server.py`:

```python
@pytest.fixture()
def stream(tmp_path, monkeypatch):
    telemetry = tmp_path / "telemetry"
    tomb = telemetry / "work" / "tombstones"
    tomb.mkdir(parents=True)
    (tomb / "incarnation-1.txt").write_text("ended early. detail.\n", encoding="utf-8")
    transcripts = tmp_path / "transcripts"
    transcripts.mkdir()
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {
            "choices": [
                {
                    "message": {
                        "content": "hello",
                        "tool_calls": [
                            {"function": {"name": "write_file", "arguments": "{}"}}
                        ],
                    }
                }
            ]
        },
    }
    (transcripts / "agent_life_transcript.jsonl").write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(telemetry))
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(transcripts))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path / "diode"))
    httpd = server.make_server(0, server.StreamHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd.server_address[1]
    httpd.shutdown()


def _plain_get(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request("GET", path)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


def test_stream_page_served_without_token(stream):
    status, body = _plain_get(stream, "/")
    assert status == 200
    assert b"<!doctype html" in body.lower()


def test_stream_snapshot_shape(stream):
    status, body = _plain_get(stream, "/api/stream")
    assert status == 200
    snap = json.loads(body)
    assert snap["stats"]["incarnation"] == 2
    assert snap["stats"]["model"] == "m"
    assert snap["turns"][-1]["content"] == "hello"
    assert snap["events"][-1]["name"] == "write_file"
    assert snap["lineage"][0]["summary"] == "ended early."
    assert "diode" in snap


def test_stream_port_has_no_mutating_routes(stream):
    for method in ("POST", "PUT", "DELETE", "PATCH"):
        conn = http.client.HTTPConnection("127.0.0.1", stream, timeout=5)
        conn.request(method, "/api/stream")
        resp = conn.getresponse()
        resp.read()
        conn.close()
        assert resp.status == 405


def test_stream_unknown_route_404(stream):
    status, _ = _plain_get(stream, "/api/nope")
    assert status == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: new tests FAIL with `AttributeError: module 'stage.server' has no attribute 'StreamHandler'`

- [ ] **Step 3: Implement**

In `stage/server.py`, add `import threading` and `from stage import data` to the imports, then after `ConsoleHandler`:

```python
def transcript_path():
    """The recorder's JSONL transcript file path."""
    return os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")


def stream_snapshot():
    """Assemble the stream page's data snapshot."""
    work = os.path.join(TELEMETRY_DIR, "work")
    turns, total = data.load_tail_turns(transcript_path())
    return {
        "turns": turns,
        "stats": data.incarnation_stats(turns, total, work),
        "events": data.self_modification_events(turns),
        "diode": data.diode_activity(DIODE_DIR),
        "lineage": data.lineage(work, turns),
    }


class StreamHandler(_BaseHandler):
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self._send(200, pages.STREAM_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/api/stream":
            self._send(200, json.dumps(stream_snapshot()))
        else:
            self._send(404, json.dumps({"error": "not found"}))


def main():
    console = make_server(CONSOLE_PORT, ConsoleHandler)
    threading.Thread(target=console.serve_forever, daemon=True).start()
    print(f"stage: stream on :{STREAM_PORT}, console on :{CONSOLE_PORT}")
    make_server(STREAM_PORT, StreamHandler).serve_forever()


if __name__ == "__main__":
    main()
```

In `stage/pages.py`, add the stream page constant:

```python
STREAM_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:,">
<title>aurora</title>
<style>
  :root { color-scheme: dark;
    --bg: #101316; --panel: #171c20; --border: #303941; --text: #eef3f6;
    --muted: #a6b0b8; --subtle: #79848c; --accent: #66d9c2; --tool: #f0bd68;
    --think: #9aa7ff; --error: #ff8d8d; }
  html, body { margin: 0; width: 1920px; height: 1080px; overflow: hidden;
    background: var(--bg); color: var(--text);
    font: 17px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  #grid { display: grid; grid-template-columns: 1fr 520px; gap: 14px;
    box-sizing: border-box; width: 100%; height: 100%; padding: 14px; }
  .panel { background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 16px; box-sizing: border-box;
    overflow: hidden; display: flex; flex-direction: column; }
  .panel h2 { margin: 0 0 8px; font-size: 14px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: var(--subtle); }
  #rail { display: grid; grid-template-rows: auto auto 1fr 1fr; gap: 14px;
    min-height: 0; }
  #feed .scroll { overflow: hidden; flex: 1; display: flex;
    flex-direction: column; justify-content: flex-end; }
  .turn { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; }
  .turn .who { color: var(--subtle); font-size: 13px; }
  .think { color: var(--think); }
  .say { color: var(--text); }
  .call { color: var(--tool); }
  .err { color: var(--error); }
  #stats table { width: 100%; border-collapse: collapse; }
  #stats td { padding: 2px 0; }
  #stats td:first-child { color: var(--subtle); width: 45%; }
  #stats td:last-child { color: var(--accent); }
  ul { margin: 0; padding: 0; list-style: none; overflow: hidden; flex: 1; }
  li { padding: 3px 0; border-bottom: 1px solid var(--border);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  li .tag { color: var(--tool); margin-right: 8px; }
  li .dim { color: var(--muted); }
</style>
</head>
<body>
<div id="grid">
  <div class="panel" id="feed"><h2>agent</h2><div class="scroll" id="turns"></div></div>
  <div id="rail">
    <div class="panel" id="stats"><h2>incarnation</h2>
      <table>
        <tr><td>incarnation</td><td id="s-inc">—</td></tr>
        <tr><td>model</td><td id="s-model">—</td></tr>
        <tr><td>transcript turns</td><td id="s-turns">—</td></tr>
        <tr><td>last activity</td><td id="s-last">—</td></tr>
      </table>
    </div>
    <div class="panel" id="lineage"><h2>previous incarnations</h2><ul id="lineage-list"></ul></div>
    <div class="panel" id="mods"><h2>self-modification</h2><ul id="mods-list"></ul></div>
    <div class="panel" id="diode"><h2>diode</h2><ul id="diode-list"></ul></div>
  </div>
</div>
<script>
function li(parent, tag, text, dim) {
  const el = document.createElement("li");
  if (tag) {
    const t = document.createElement("span");
    t.className = "tag"; t.textContent = tag;
    el.appendChild(t);
  }
  const s = document.createElement("span");
  if (dim) s.className = "dim";
  s.textContent = text;
  el.appendChild(s);
  parent.appendChild(el);
}
function clamp(text, n) {
  text = (text || "").trim();
  return text.length > n ? text.slice(0, n) + "…" : text;
}
function render(snap) {
  document.getElementById("s-inc").textContent = snap.stats.incarnation;
  document.getElementById("s-model").textContent = snap.stats.model || "—";
  document.getElementById("s-turns").textContent = snap.stats.transcript_turns;
  document.getElementById("s-last").textContent = snap.stats.last_timestamp || "—";

  const turns = document.getElementById("turns");
  turns.textContent = "";
  for (const t of snap.turns.slice(-8)) {
    const box = document.createElement("div");
    box.className = "turn";
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = "turn " + t.index;
    box.appendChild(who);
    if (t.reasoning) {
      const d = document.createElement("div");
      d.className = "think"; d.textContent = clamp(t.reasoning, 400);
      box.appendChild(d);
    }
    if (t.content) {
      const d = document.createElement("div");
      d.className = "say"; d.textContent = clamp(t.content, 400);
      box.appendChild(d);
    }
    for (const c of t.tool_calls || []) {
      const d = document.createElement("div");
      d.className = "call";
      d.textContent = "→ " + c.name + " " + clamp(c.arguments, 160);
      box.appendChild(d);
    }
    if (t.error) {
      const d = document.createElement("div");
      d.className = "err";
      d.textContent = "error: " + clamp(JSON.stringify(t.error), 200);
      box.appendChild(d);
    }
    turns.appendChild(box);
  }

  const lin = document.getElementById("lineage-list");
  lin.textContent = "";
  for (const l of snap.lineage) li(lin, null, l.summary, false);

  const mods = document.getElementById("mods-list");
  mods.textContent = "";
  for (const e of snap.events.slice().reverse())
    li(mods, e.name, "turn " + e.index + "  " + clamp(e.detail, 60), true);

  const dio = document.getElementById("diode-list");
  dio.textContent = "";
  for (const o of snap.diode.outputs) li(dio, null, o.name, true);
}
function tick() {
  fetch("/api/stream").then(r => r.json()).then(render).catch(() => {});
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite, lint, and commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format stage tests/test_stage_server.py && .venv/bin/ruff check stage tests/test_stage_server.py
git add stage tests/test_stage_server.py
git commit -m "feat: serve the stream page and snapshot API

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 7: Image, topology, and containment checks

**Files:**
- Create: `Dockerfile.stage`
- Modify: `Dockerfile` (mountpoint line), `docker-compose.yml`, `.env.example`, `scripts/verify_container.sh`
- Test: `tests/test_stage_topology.py` (create)

**Interfaces:**
- Consumes: `stage/` package (Tasks 3–6), watchdog telemetry (Tasks 1–2).
- Produces: running topology: `telemetry` volume (agent rw at `/telemetry`, stage ro), `stage` service on `egress` publishing `127.0.0.1:8091` and `127.0.0.1:8092`, optional `cloudflared` under the `stream` profile.

- [ ] **Step 1: Write the failing guard tests**

Create `tests/test_stage_topology.py` (substring guards — the dev venv has no YAML parser; these guard regressions, not semantics):

```python
def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_compose_defines_stage_service_and_telemetry_volume():
    text = _read("docker-compose.yml")
    assert "telemetry:/telemetry:ro" in text
    assert "telemetry:/telemetry\n" in text or "- telemetry:/telemetry\n" in text
    assert '"127.0.0.1:8091:8091"' in text
    assert '"127.0.0.1:8092:8092"' in text
    assert "STAGE_CONSOLE_TOKEN" in text
    assert "aurora-stage" in text
    assert "cloudflared" in text
    assert "state:/state" not in text.split("stage:")[1].split("cloudflared:")[0]


def test_agent_image_precreates_telemetry_mountpoint():
    text = _read("Dockerfile")
    assert "/telemetry" in text


def test_stage_dockerfile_copies_only_stage_package():
    text = _read("Dockerfile.stage")
    assert "COPY" in text and "stage" in text
    assert "agent.py" not in text


def test_env_example_documents_stage_settings():
    text = _read(".env.example")
    assert "STAGE_CONSOLE_TOKEN" in text
    assert "TUNNEL_TOKEN" in text


def test_verify_script_checks_stage_containment():
    text = _read("scripts/verify_container.sh")
    assert "8091" in text and "8092" in text
    assert "aurora-stage" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_topology.py -q`
Expected: FAIL (missing Dockerfile.stage, compose entries)

- [ ] **Step 3: Create `Dockerfile.stage`**

```dockerfile
FROM python:3.13-slim

RUN useradd --create-home --uid 1000 stageuser

COPY --chown=stageuser:stageuser stage/ /opt/stage/stage/

USER stageuser
WORKDIR /opt/stage
EXPOSE 8091 8092
CMD ["python", "-m", "stage.server"]
```

Check `.dockerignore` does not exclude `stage/`; if it does, amend it.

- [ ] **Step 4: Modify `Dockerfile` (agent image)**

Change the mountpoint pre-creation lines to include `/telemetry`:

```dockerfile
RUN mkdir -p /diode /transcripts /state /telemetry \
    && chown appuser:appuser /diode /transcripts /state /telemetry
```

- [ ] **Step 5: Modify `docker-compose.yml`**

Add `- telemetry:/telemetry` to the agent service's `volumes:` list. Add to the top-level `volumes:` map: `telemetry: {}`. Add the two services:

```yaml
  stage:
    build:
      context: .
      dockerfile: Dockerfile.stage
    image: aurora-stage
    environment:
      TRANSCRIPT_DIR: /transcripts
      DIODE_DIR: /diode
      TELEMETRY_DIR: /telemetry
      STAGE_CONSOLE_TOKEN: ${STAGE_CONSOLE_TOKEN:-}
    volumes:
      - transcripts:/transcripts:ro
      - diode:/diode:ro
      - telemetry:/telemetry:ro
    ports:
      - "127.0.0.1:8091:8091"
      - "127.0.0.1:8092:8092"
    networks: [egress]
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 128
    mem_limit: 256m
    restart: unless-stopped

  cloudflared:
    image: cloudflare/cloudflared:latest
    profiles: ["stream"]
    command: tunnel --no-autoupdate run --token ${TUNNEL_TOKEN:-}
    networks: [egress]
    restart: unless-stopped
```

- [ ] **Step 6: Modify `.env.example`**

Append:

```
# Operator console token for the stage (required to use the console on :8092)
STAGE_CONSOLE_TOKEN=

# Cloudflare Tunnel token for the optional cloudflared service
# (docker compose --profile stream up). Point the tunnel's public hostname
# at http://stage:8091. Never expose 8092.
TUNNEL_TOKEN=
```

- [ ] **Step 7: Extend `scripts/verify_container.sh`**

Append (following the script's existing check style — adapt helper names to what is already there):

```bash
# --- stage containment ---
# stage must not mount the state volume
if docker inspect aurora-stage-1 2>/dev/null | grep -q '"Destination": "/state"'; then
  echo "FAIL: stage mounts /state"; exit 1
fi
# stream port refuses mutating methods
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8091/api/stream || true)
[ "$code" = "405" ] || { echo "FAIL: stream port accepted POST ($code)"; exit 1; }
# console fails closed without a token
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8092/api/roots || true)
[ "$code" = "401" ] || [ "$code" = "403" ] || { echo "FAIL: console served without token ($code)"; exit 1; }
echo "ok: stage containment checks passed"
```

Note: the container name may be `aurora-stage-1` or derived from the compose project; match the script's existing container-name convention.

- [ ] **Step 8: Run tests, lint, and commit**

```bash
.venv/bin/python -m pytest tests/test_stage_topology.py -q
.venv/bin/ruff format . && .venv/bin/ruff check .
git add Dockerfile.stage Dockerfile docker-compose.yml .env.example scripts/verify_container.sh tests/test_stage_topology.py .dockerignore
git commit -m "build: add the stage service, telemetry volume, and tunnel profile

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Include `.dockerignore` in the add only if it was amended.)

- [ ] **Step 9 (manual, if Docker is available): container smoke**

```bash
docker compose build && docker compose up -d
sleep 10
curl -s http://127.0.0.1:8091/api/stream | head -c 200
bash scripts/verify_container.sh
docker compose down
```

If Docker is unavailable in the execution environment, note that in the report; the operator runs this before streaming.

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything above.
- Produces: docs matching shipped behavior.

- [ ] **Step 1: Update CLAUDE.md invariant 3**

Add these bullets to invariant 3's list (after the viewer bullet), matching the file's indentation:

```markdown
   - The **stage** is outward-facing but holds no upstream API key and never mounts `/state`.
     Its console (port 8092) binds host-loopback only, requires `STAGE_CONSOLE_TOKEN` on every
     request, and is never exposed through the tunnel. The stream port (8091) serves no mutating
     endpoints. The console browser resolves paths only inside its allow-listed roots and never
     follows a symlink across a root boundary; everything renders as escaped text.
   - The **telemetry volume** is written only by the watchdog (a mirror of `/work` plus the
     captured agent log), mounted read-only into the stage, and never rendered on the stream
     page. The mirror copies symlinks as links and never follows them.
```

- [ ] **Step 2: Update README.md**

In the Repository layout table, after the viewer row, add:

```markdown
| `stage/` / `Dockerfile.stage` | The stream page (OBS browser source) and the token-gated operator console with a container browser. |
```

After the "Building the workshop" section, add:

```markdown
### Streaming the stage

The stage serves two pages:

- `http://localhost:8091` — the stream page, a 1920×1080 read-only view designed for an OBS
  browser source: live agent turns, incarnation stats, lineage, self-modification and diode
  activity.
- `http://localhost:8092/?token=<STAGE_CONSOLE_TOKEN>` — the operator console (loopback only):
  browse the telemetry mirror of the agent's working tree, the transcripts, and the diode; view
  the agent.py diff against stock; tail the captured agent log.

Set `STAGE_CONSOLE_TOKEN` in `.env` to enable the console. To put the stream page on the
internet for OBS or viewers, run a Cloudflare Tunnel pointing at `http://localhost:8091`
(host-run `cloudflared`), or set `TUNNEL_TOKEN` and start the bundled service with
`docker compose --profile stream up cloudflared`, pointing the tunnel's public hostname at
`http://stage:8091`. Never expose port 8092.
```

- [ ] **Step 3: Full suite and lint**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
```

Expected: all tests pass; no reformatting churn.

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: describe the stage, telemetry mirror, and tunnel setup

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```
