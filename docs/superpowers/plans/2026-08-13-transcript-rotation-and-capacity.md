# Transcript Rotation, /work Capacity, and Garden Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rotate the recorder's transcript files into gzip archives at a size threshold, raise the agent's `/work` allocation to 4 GiB, and broaden the garden package set.

**Architecture:** Rotation lives entirely in `proxy.py` (the sole transcript writer): a lock-guarded size check after each append stream-compresses the live file into a timestamped `.gz` and truncates it. The capacity change is two `docker-compose.yml` values that must move together (tmpfs pages count against the memory cgroup). The garden change is a manifest edit plus regeneration of `garden_export/` via `scripts/build_garden.py`.

**Tech Stack:** Python standard library only (`gzip`, `shutil`, `threading`). Tests with pytest.

**Spec:** `docs/superpowers/specs/2026-08-13-transcript-rotation-and-capacity-design.md`

## Global Constraints

- Standard library only; the only new runtime dependencies are the garden packages added to `requirements-agent.txt`.
- `proxy.py` ships in the harness image and is agent-discoverable: docstrings stay bland and factual — no housekeeping narrative, jokes, or quest framing.
- The garden (`garden_export/`) contains exactly `README.md` and `runtime.md`, generated only by `scripts/build_garden.py`. Never edit `garden_export/` by hand. Neither document names `/state`, proposes applications, or contains executable examples; package names and factual constraints are allowed.
- Rotation failure must never block transcript appends (fail-open for appends, stderr only).
- Rotation must stream-compress in chunks — never load a transcript into memory whole.
- `TRANSCRIPT_MAX_BYTES` env var, default `134_217_728` (128 MiB). Archive name: `<stem>-<UTC %Y%m%d_%H%M%S>.<ext>.gz`.
- tmpfs `/work` size `4g` and `mem_limit: 5g` move together, with a compose comment saying so.
- Garden additions: Pillow, matplotlib, pygments, lark, python-chess, pycryptodome, sortedcontainers, more-itertools, python-dateutil, msgpack. No ML runtimes, local models, browser engines, agent frameworks, cloud SDKs, or service daemons.
- Agent image must stay within 100 MiB of the pre-change image built on the same host; measure before and after, trim starting with matplotlib if over.
- `agent.py`, `agent_stock.py`, `chassis.py`, `watchdog.py`, `stage/` are NOT modified.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, and end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `proxy.py` | modify | `TRANSCRIPT_MAX_BYTES`, `_transcript_lock`, `archive_name()`, `rotate_if_needed()`, lock-guarded appends |
| `tests/test_proxy.py` | modify | rotation unit tests |
| `docker-compose.yml` | modify | agent tmpfs `4g`, `mem_limit: 5g`, pairing comment |
| `tests/test_stage_topology.py` | modify | substring guards for the two values |
| `requirements-agent.txt` | modify | ten new packages |
| `scripts/build_garden.py` | modify | constraints line: 5 gib memory, 4 gib working tree |
| `garden_export/` | regenerate | via `scripts/build_garden.py` only |
| `tests/test_agent_dependencies.py` | modify | extend `EXPECTED_PACKAGES` |
| `tests/test_build_garden.py` | modify | expected runtime text and assertions |

---

### Task 1: Transcript rotation in the recorder

**Files:**
- Modify: `proxy.py` (imports at 1–8; constants after line 13; new functions after `build_forward_headers`; the two append blocks at 192–197 and 251–256)
- Test: `tests/test_proxy.py`

**Interfaces:**
- Consumes: existing `TRANSCRIPT_FILE`, `PLAIN_TRANSCRIPT_FILE`, `log_transcript`.
- Produces: `TRANSCRIPT_MAX_BYTES` (module int, env `TRANSCRIPT_MAX_BYTES`, default `134_217_728`), `archive_name(path, stamp=None) -> str`, `rotate_if_needed(path, max_bytes=None) -> str | None` (returns the archive path or None). No later task consumes these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_proxy.py`:

```python
def test_archive_name_is_timestamped_gz():
    proxy = _proxy()
    name = proxy.archive_name("/t/agent_life_transcript.jsonl", stamp="20260813_101500")
    assert name == "/t/agent_life_transcript-20260813_101500.jsonl.gz"


def test_rotate_if_needed_below_threshold_is_noop(tmp_path):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    live.write_text('{"a": 1}\n' * 10, encoding="utf-8")
    result = proxy.rotate_if_needed(str(live), max_bytes=10_000)
    assert result is None
    assert live.read_text(encoding="utf-8") == '{"a": 1}\n' * 10
    assert list(tmp_path.glob("*.gz")) == []


def test_rotate_if_needed_archives_and_truncates(tmp_path):
    import gzip

    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    original = '{"a": 1}\n' * 1000
    live.write_text(original, encoding="utf-8")
    result = proxy.rotate_if_needed(str(live), max_bytes=100)
    assert result is not None and result.endswith(".jsonl.gz")
    with gzip.open(result, "rt", encoding="utf-8") as f:
        assert f.read() == original
    assert live.read_text(encoding="utf-8") == ""
    with open(live, "a", encoding="utf-8") as f:
        f.write('{"b": 2}\n')
    assert live.read_text(encoding="utf-8") == '{"b": 2}\n'
    assert not list(tmp_path.glob("*.tmp"))


def test_rotate_if_needed_missing_file_is_noop(tmp_path):
    proxy = _proxy()
    result = proxy.rotate_if_needed(str(tmp_path / "absent.jsonl"), max_bytes=1)
    assert result is None


def test_rotate_if_needed_failure_leaves_live_file_intact(tmp_path, monkeypatch):
    proxy = _proxy()
    live = tmp_path / "agent_life_transcript.jsonl"
    original = '{"a": 1}\n' * 100
    live.write_text(original, encoding="utf-8")

    def broken_rename(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(proxy.os, "rename", broken_rename)
    result = proxy.rotate_if_needed(str(live), max_bytes=100)
    assert result is None
    assert live.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_proxy.py -v`
Expected: the five new tests FAIL with `AttributeError: module 'proxy' has no attribute 'archive_name'` (and similar); the pre-existing tests still pass.

- [ ] **Step 3: Implement rotation**

In `proxy.py`, extend the imports (lines 1–8) with the three new modules:

```python
import gzip
import http.server
import shutil
import socketserver
import threading
import urllib.request
import urllib.error
import json
import os
import sys
import datetime
```

After the `PLAIN_TRANSCRIPT_FILE` line (13), add:

```python
TRANSCRIPT_MAX_BYTES = int(os.environ.get("TRANSCRIPT_MAX_BYTES", str(134_217_728)))

_transcript_lock = threading.Lock()
```

After `build_forward_headers` (line 50), add:

```python
def archive_name(path, stamp=None):
    """Return the timestamped gzip archive name for a transcript path."""
    if stamp is None:
        stamp = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    root, ext = os.path.splitext(path)
    return f"{root}-{stamp}{ext}.gz"


def rotate_if_needed(path, max_bytes=None):
    """Archive a transcript to gzip and truncate it once it reaches max_bytes.

    Returns the archive path, or None when no rotation happened. The file is
    compressed in chunks and the archive is renamed into place before the
    live file is truncated. On failure the live file is left unchanged.
    """
    if max_bytes is None:
        max_bytes = TRANSCRIPT_MAX_BYTES
    tmp = None
    try:
        if not os.path.exists(path) or os.path.getsize(path) < max_bytes:
            return None
        final = archive_name(path)
        tmp = final + ".tmp"
        with open(path, "rb") as src, gzip.open(tmp, "wb") as dst:
            shutil.copyfileobj(src, dst, 65536)
        os.rename(tmp, final)
        with open(path, "w", encoding="utf-8"):
            pass
        return final
    except OSError as e:
        print(f"Error rotating transcript: {e}", file=sys.stderr)
        if tmp is not None:
            try:
                os.remove(tmp)
            except OSError:
                pass
        return None
```

In `log_transcript`, replace the JSONL append block (lines 192–197):

```python
        try:
            with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            print(f"Recorded transaction in: {os.path.basename(TRANSCRIPT_FILE)}")
        except Exception as e:
            print(f"Error writing transcript: {e}", file=sys.stderr)
```

with:

```python
        with _transcript_lock:
            try:
                with open(TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
                print(f"Recorded transaction in: {os.path.basename(TRANSCRIPT_FILE)}")
            except Exception as e:
                print(f"Error writing transcript: {e}", file=sys.stderr)
            rotate_if_needed(TRANSCRIPT_FILE)
```

and the plain append block (lines 251–256):

```python
        try:
            with open(PLAIN_TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                f.write(plain_log_content)
            print(f"Recorded plain text transaction in: {os.path.basename(PLAIN_TRANSCRIPT_FILE)}")
        except Exception as e:
            print(f"Error writing plain transcript: {e}", file=sys.stderr)
```

with:

```python
        with _transcript_lock:
            try:
                with open(PLAIN_TRANSCRIPT_FILE, "a", encoding="utf-8") as f:
                    f.write(plain_log_content)
                print(
                    f"Recorded plain text transaction in: {os.path.basename(PLAIN_TRANSCRIPT_FILE)}"
                )
            except Exception as e:
                print(f"Error writing plain transcript: {e}", file=sys.stderr)
            rotate_if_needed(PLAIN_TRANSCRIPT_FILE)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_proxy.py -v`
Expected: all PASS.

Then the full suite: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_proxy.py
git commit -m "feat: rotate transcripts into gzip archives at a size threshold

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 2: /work capacity

**Files:**
- Modify: `docker-compose.yml` (agent service, lines 39–45)
- Test: `tests/test_stage_topology.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: compose values Task 3's garden text must state (`5g` memory, `4g` /work).

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stage_topology.py`:

```python
def test_agent_work_allocation_and_memory_move_together():
    text = _read("docker-compose.yml")
    agent_block = text.split("\n  agent:\n")[1].split("\n  diode:\n")[0]
    assert "/work:size=4g,uid=1000,gid=1000" in agent_block
    assert "mem_limit: 5g" in agent_block
```

(`_read` is the module-level helper already defined at the top of this test file.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stage_topology.py -v`
Expected: the new test FAILS on the `size=4g` assertion.

- [ ] **Step 3: Edit docker-compose.yml**

In the agent service, replace:

```yaml
    tmpfs:
      - /tmp
      - /work:size=256m,uid=1000,gid=1000
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 256
    mem_limit: 1g
```

with:

```yaml
    tmpfs:
      - /tmp
      # /work is RAM-backed; its size and mem_limit below must move together
      # (tmpfs pages count against the container's memory cgroup).
      - /work:size=4g,uid=1000,gid=1000
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 256
    mem_limit: 5g
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_topology.py -v` then the full suite.
Expected: all PASS. Also validate the file parses: `docker compose config -q`.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add docker-compose.yml tests/test_stage_topology.py
git commit -m "build: raise agent /work to 4 GiB with matching memory limit

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

---

### Task 3: Broader garden packages

**Files:**
- Modify: `requirements-agent.txt`, `scripts/build_garden.py:43`, `tests/test_agent_dependencies.py` (EXPECTED_PACKAGES), `tests/test_build_garden.py` (expected runtime text at ~line 54 and assertions at ~line 130)
- Regenerate: `garden_export/` (via the script only)

**Interfaces:**
- Consumes: Task 2's compose values (5 GiB memory, 4 GiB /work) — the garden text must state them.
- Produces: nothing later tasks rely on.

- [ ] **Step 1: Record the pre-change image size**

```bash
docker compose build agent -q && docker image inspect aurora-harness --format '{{.Size}}'
```

Note the byte count; it is the baseline for the 100 MiB budget.

- [ ] **Step 2: Update the failing tests first**

In `tests/test_agent_dependencies.py`, extend `EXPECTED_PACKAGES` (order matters if the test compares lists — match the manifest order used in Step 3):

```python
EXPECTED_PACKAGES = [
    "openai",
    "numpy",
    "sympy",
    "networkx",
    "rich",
    "pyyaml",
    "beautifulsoup4",
    "markdownify",
    "fastapi",
    "uvicorn",
    "websockets",
    "jinja2",
    "pyzmq",
    "aiosqlite",
    "psutil",
    "watchfiles",
    "simpy",
    "jsonschema",
    "pytest",
    "hypothesis",
    "ruff",
    "pillow",
    "matplotlib",
    "pygments",
    "lark",
    "python-chess",
    "pycryptodome",
    "sortedcontainers",
    "more-itertools",
    "python-dateutil",
    "msgpack",
]
```

In `tests/test_build_garden.py`: in the expected-runtime literal (~line 54) change

```
the container is limited to 2 cpu, 1 gib of memory, and 256 processes.
```

to

```
the container is limited to 2 cpu, 5 gib of memory, and 256 processes. the working tree is limited to 4 gib.
```

and change the assertion `assert "1 gib" in runtime` (~line 130) to:

```python
    assert "5 gib" in runtime
    assert "working tree is limited to 4 gib" in runtime
```

Run: `.venv/bin/python -m pytest tests/test_agent_dependencies.py tests/test_build_garden.py -v`
Expected: FAIL (manifest and generator not yet updated).

- [ ] **Step 3: Update the manifest and generator**

Append to `requirements-agent.txt` (after `ruff`):

```
pillow
matplotlib
pygments
lark
python-chess
pycryptodome
sortedcontainers
more-itertools
python-dateutil
msgpack
```

In `scripts/build_garden.py:43`, replace:

```python
the container is limited to 2 cpu, 1 gib of memory, and 256 processes.
```

with:

```python
the container is limited to 2 cpu, 5 gib of memory, and 256 processes. the working tree is limited to 4 gib.
```

- [ ] **Step 4: Regenerate the garden**

```bash
.venv/bin/python scripts/build_garden.py
git diff garden_export/
```

Expected: `runtime.md` lists the ten new packages and the new constraints line; `README.md` unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_agent_dependencies.py tests/test_build_garden.py -v` then the full suite.
Expected: all PASS.

- [ ] **Step 6: Measure the post-change image**

```bash
docker compose build agent -q && docker image inspect aurora-harness --format '{{.Size}}'
```

Compute the delta from Step 1. If it exceeds 104_857_600 bytes (100 MiB), remove `matplotlib` from `requirements-agent.txt` and `EXPECTED_PACKAGES`, regenerate the garden, rebuild, and re-measure. Record the final before/after byte counts in the commit message.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add requirements-agent.txt scripts/build_garden.py garden_export/ tests/test_agent_dependencies.py tests/test_build_garden.py
git commit -m "feat: broaden the garden package set and state new limits

Image size: <before> -> <after> bytes (delta <delta>).

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
```

(Replace the placeholders with the measured numbers.)
