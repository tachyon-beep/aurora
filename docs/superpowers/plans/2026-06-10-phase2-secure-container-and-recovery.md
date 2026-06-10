# Phase 2 — Secure Container & Recovery — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the harness as two hardened containers — an internet-less `agent` sandbox and an external tamper-proof `recorder` — and replace the watchdog's stock-copy recovery with self-reload + tiered git recovery, all behind the container wall.

**Architecture:** A single shared image holds the harness code at a read-only golden path `/opt/agent`. The `agent` service runs `entrypoint.sh`, which copies golden → a tmpfs `/work` and execs `watchdog.py`, which supervises `agent.py`. The agent is on an `internal: true` network with no route to the internet; its only peer is the `recorder` service, which runs `proxy.py`, holds the real API key, injects the `Authorization` header, logs every turn to a durable volume, and is the sole egress to OpenRouter. Recovery is tiered: restore `agent.py` from a git baseline (tier 1) → `git reset --hard` the whole tree (tier 2) → exit so Docker recreates the container from the immutable image (tier 3). The watchdog re-execs itself when its own file changes, so the agent can durably reshape its supervisor but cannot survive breaking it.

**Tech Stack:** Python 3.13, `openai`, stdlib `http.server` (proxy), `git` (in-image), Docker + Compose v2, pytest + ruff (already present).

**Reference spec:** `docs/superpowers/specs/2026-06-10-containerized-self-modifying-agent-design.md` — §2 (invariants), §3 (architecture, golden/tmpfs, hidden topology), §5 (authorship + tiered recovery), §9 (decisions D1, D3, D4, D5), §11 (accepted defaults: mem 1g, pids 256, cpus 2, tmpfs /work 256m; window 10 min; tiers at 2nd/3rd failure; 24 h inactivity).

**Scope note:** Phase 2 cleans + reworks `proxy.py` and `watchdog.py` (deferred from Phase 1's cleanup on purpose) and adds the container layer. The agent image installs only `openai` + `git`; the curated exploration packages and the `/garden` seed are **Phase 3**. The web diode is **Phase 3**.

**Verification philosophy:** All Python logic (proxy header injection, recovery-tier decision, git recovery, self-reload hash) is unit-tested with pytest (no Docker, no network). The container layer is verified by a script that really builds the image and runs Compose, asserting the security invariants (no internet from `agent`, reachable `recorder`, read-only rootfs, non-root, tier-1 recovery). **No verification step requires a real OpenRouter key or spends API credits** — isolation and recovery are proven structurally. A live end-to-end model turn is documented as an optional manual check.

---

## File Structure

- `proxy.py` — MODIFY: clean (strip `#` comments, flatten emoji, `ruff format`); read transcript dir from `TRANSCRIPT_DIR` env (so the recorder writes to a durable volume, not its read-only code dir); extract a testable `build_forward_headers(headers, api_key)` that injects `Authorization` when a key is present (env-gated). Add a `__main__` import guard if missing.
- `watchdog.py` — REWRITE: clean + decompose into testable units (`decide_tier`, `restore_agent_only`, `git_reset_all`, `file_hash`, `watchdog_should_reload`) and a `run_watchdog` loop that does self-reload + tiered recovery against a git baseline instead of stock-copy.
- `entrypoint.sh` — CREATE: seed tmpfs `/work` from read-only `/opt/agent`, then `exec python watchdog.py` from `/work`.
- `Dockerfile` — CREATE: `python:3.13-slim`, install `git` + `ca-certificates`, `pip install openai`, non-root `appuser`, copy code to `/opt/agent`, build a baseline git repo there (`git init/add/commit`, `git tag baseline`), set `ENTRYPOINT`.
- `.dockerignore` — CREATE: exclude transcripts, `.venv`, caches, `.git`, `docs/`, tombstones, tests.
- `docker-compose.yml` — CREATE: `agent` + `recorder` services; `internal` + `egress` networks; hardening (`read_only`, `tmpfs`, `cap_drop`, `no-new-privileges`, `pids_limit`/`mem_limit`/`cpus`); `restart: unless-stopped`; transcript named volume.
- `.env.example` — CREATE: document `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`.
- `tests/test_proxy.py` — CREATE: unit tests for `build_forward_headers` + `TRANSCRIPT_DIR`.
- `tests/test_watchdog.py` — CREATE: unit tests for `decide_tier`, git recovery (temp repo), self-reload hash.
- `scripts/verify_container.sh` — CREATE: build + compose-up + assert invariants + tier-1 recovery + teardown.
- `tests/test_container_smoke.py` — CREATE: pytest wrapper that runs `scripts/verify_container.sh`, skipped if Docker is unavailable.

---

## Task 1: Clean `proxy.py` + env-gated key injection + `TRANSCRIPT_DIR`

**Files:**
- Modify: `proxy.py`
- Create: `tests/test_proxy.py`

- [ ] **Step 1: Write failing unit tests** in `tests/test_proxy.py`

```python
import importlib
import os


def _proxy():
    # Import fresh each time so TRANSCRIPT_DIR env is read at import.
    import proxy

    return importlib.reload(proxy)


def test_build_forward_headers_injects_auth_when_key_present():
    proxy = _proxy()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-dummy", "Host": "x"}
    out = proxy.build_forward_headers(headers, "sk-real")
    assert out["Authorization"] == "Bearer sk-real"
    assert "Host" not in out  # hop-by-hop dropped


def test_build_forward_headers_preserves_auth_when_no_key():
    proxy = _proxy()
    headers = {"Content-Type": "application/json", "Authorization": "Bearer sk-dummy"}
    out = proxy.build_forward_headers(headers, "")
    assert out["Authorization"] == "Bearer sk-dummy"


def test_build_forward_headers_drops_hop_by_hop():
    proxy = _proxy()
    headers = {"Content-Length": "5", "Connection": "keep-alive", "Accept-Encoding": "gzip", "X-Title": "t"}
    out = proxy.build_forward_headers(headers, "")
    for h in ("Content-Length", "Connection", "Accept-Encoding"):
        assert h not in out
    assert out["X-Title"] == "t"


def test_transcript_dir_env_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_DIR", str(tmp_path))
    import proxy

    proxy = importlib.reload(proxy)
    assert os.path.dirname(proxy.TRANSCRIPT_FILE) == str(tmp_path)
```

Run: `.venv/bin/python -m pytest tests/test_proxy.py -v` → FAIL (`build_forward_headers` not defined; `TRANSCRIPT_DIR` not honored).

- [ ] **Step 2: Add `TRANSCRIPT_DIR` support** near the top of `proxy.py` (replace the current `TRANSCRIPT_FILE`/`PLAIN_TRANSCRIPT_FILE` definitions):

```python
TRANSCRIPT_DIR = os.environ.get(
    "TRANSCRIPT_DIR", os.path.dirname(os.path.abspath(__file__))
)
TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.jsonl")
PLAIN_TRANSCRIPT_FILE = os.path.join(TRANSCRIPT_DIR, "agent_life_transcript.txt")
```

- [ ] **Step 3: Add the testable `build_forward_headers` helper** at module level (above the handler class):

```python
def build_forward_headers(headers, api_key):
    """Build the headers forwarded upstream.

    Drops hop-by-hop headers. When api_key is non-empty, overrides Authorization
    with it, so the recorder injects the real key and the agent never holds it.
    """
    hop_by_hop = {"host", "content-length", "connection", "accept-encoding"}
    forwarded = {k: v for k, v in headers.items() if k.lower() not in hop_by_hop}
    if api_key:
        forwarded["Authorization"] = f"Bearer {api_key}"
    return forwarded
```

- [ ] **Step 4: Use it in `do_POST`.** Replace the inline header-building loop with:

```python
        headers_to_forward = build_forward_headers(
            self.headers, os.environ.get("OPENROUTER_API_KEY", "")
        )
```
Also ensure the transcript dir exists before writing — at the start of `log_transcript`, add:
```python
        os.makedirs(TRANSCRIPT_DIR, exist_ok=True)
```

- [ ] **Step 5: Clean the file** — strip all `#` comments, flatten emoji/voice in `print()` strings to neutral text (e.g. `"📡 PROXY INTERCEPTED REQUEST..."` → `"proxy intercepted request..."`, the `❌`/`📝` lines likewise), keep docstrings. Ensure a `if __name__ == "__main__": main()` guard exists (it does). Then:
```bash
.venv/bin/ruff format proxy.py
.venv/bin/ruff check proxy.py
```
Fix anything ruff flags (behavior-equivalent only; note any logic-adjacent fix).

- [ ] **Step 6: Run tests + import smoke**

Run: `.venv/bin/python -m pytest tests/test_proxy.py -v && .venv/bin/python -c "import proxy; print('import OK')"`
Expected: 4 passed; import OK.

- [ ] **Step 7: Commit**

```bash
git add proxy.py tests/test_proxy.py
git commit -m "feat: proxy injects auth (env-gated), honors TRANSCRIPT_DIR; clean+format"
```

---

## Task 2: Decompose `watchdog.py` into testable units (recovery logic)

This task adds the new pure/IO units and their tests WITHOUT yet rewiring the main loop (that is Task 3). The old `run_watchdog` keeps working until Task 3 replaces it.

**Files:**
- Modify: `watchdog.py` (add constants + functions; leave `run_watchdog` for now)
- Create: `tests/test_watchdog.py`

- [ ] **Step 1: Write failing unit tests** in `tests/test_watchdog.py`

```python
import importlib
import subprocess

import watchdog


def test_decide_tier_escalates_within_window():
    now = 1000.0
    assert watchdog.decide_tier([now], now) == 1
    assert watchdog.decide_tier([now - 10, now], now) == 2
    assert watchdog.decide_tier([now - 20, now - 10, now], now) == 3


def test_decide_tier_ignores_failures_outside_window():
    now = 10_000.0
    old = [now - 5000, now - 4000]  # outside default 600s window
    assert watchdog.decide_tier(old + [now], now) == 1


def _make_baseline_repo(path):
    # A temp git repo with agent.py + watchdog.py committed and tagged 'baseline'.
    def git(*args):
        subprocess.run(
            ["git", "-C", str(path), *args],
            check=True,
            capture_output=True,
        )

    (path / "agent.py").write_text("BASELINE_AGENT\n", encoding="utf-8")
    (path / "other.py").write_text("BASELINE_OTHER\n", encoding="utf-8")
    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    git("add", "-A")
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "baseline")
    git("tag", "baseline")


def test_restore_agent_only_restores_agent_keeps_others(tmp_path):
    _make_baseline_repo(tmp_path)
    (tmp_path / "agent.py").write_text("CORRUPTED\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("AGENT_EDITED_THIS\n", encoding="utf-8")
    watchdog.restore_agent_only(str(tmp_path))
    assert (tmp_path / "agent.py").read_text() == "BASELINE_AGENT\n"
    # other.py (the agent's durable env edit) is preserved by tier 1
    assert (tmp_path / "other.py").read_text() == "AGENT_EDITED_THIS\n"


def test_git_reset_all_restores_everything_keeps_ignored(tmp_path):
    _make_baseline_repo(tmp_path)
    (tmp_path / ".gitignore").write_text("notes/\n", encoding="utf-8")
    (tmp_path / "agent.py").write_text("CORRUPTED\n", encoding="utf-8")
    (tmp_path / "other.py").write_text("EDITED\n", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "keep.txt").write_text("incarnation note\n", encoding="utf-8")
    watchdog.git_reset_all(str(tmp_path))
    assert (tmp_path / "agent.py").read_text() == "BASELINE_AGENT\n"
    assert (tmp_path / "other.py").read_text() == "BASELINE_OTHER\n"
    # gitignored notes survive (clean -fd, NOT -x)
    assert (tmp_path / "notes" / "keep.txt").exists()


def test_file_hash_changes_with_content(tmp_path):
    f = tmp_path / "w.py"
    f.write_text("a\n", encoding="utf-8")
    h1 = watchdog.file_hash(str(f))
    f.write_text("b\n", encoding="utf-8")
    assert watchdog.file_hash(str(f)) != h1
```

Run: `.venv/bin/python -m pytest tests/test_watchdog.py -v` → FAIL (functions undefined).

- [ ] **Step 2: Add imports + constants** at the top of `watchdog.py` (replace the stock-oriented constants). Keep `AGENT_FILE`; add the rest:

```python
import os
import sys
import time
import hashlib
import subprocess
import datetime

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_FILE = os.path.join(WORK_DIR, "agent.py")
WATCHDOG_FILE = os.path.join(WORK_DIR, "watchdog.py")
TRANSCRIPT_FILE = os.path.join(WORK_DIR, "agent_life_transcript.jsonl")

BASELINE_REF = "baseline"
INACTIVITY_TIMEOUT_SECONDS = 24 * 60 * 60  # 24h (D9); agent-editable
FAILURE_WINDOW_SECONDS = 600  # 10 min (spec §11)
TIER2_FAILURES = 2
TIER3_FAILURES = 3
```
(Remove `shutil` import and the `STOCK_FILE`/`SESSION_FILE`/`NOTE_FILE` constants if unused after Task 3; keep `NOTE_FILE`/`SESSION_FILE` if the loop still references them until Task 3 — verify at the end of Task 3.)

- [ ] **Step 3: Add the testable units**

```python
def decide_tier(failure_times, now, window=FAILURE_WINDOW_SECONDS,
                tier2=TIER2_FAILURES, tier3=TIER3_FAILURES):
    """Map recent failures to a recovery tier (1, 2, or 3)."""
    recent = [t for t in failure_times if now - t <= window]
    n = len(recent)
    if n >= tier3:
        return 3
    if n >= tier2:
        return 2
    return 1


def restore_agent_only(work_dir=WORK_DIR):
    """Tier 1: restore agent.py from the immutable git baseline; keep other edits."""
    subprocess.run(
        ["git", "-C", work_dir, "checkout", BASELINE_REF, "--", "agent.py"],
        capture_output=True,
    )


def git_reset_all(work_dir=WORK_DIR):
    """Tier 2: revert all tracked code to baseline; keep gitignored notes (no -x)."""
    subprocess.run(
        ["git", "-C", work_dir, "reset", "--hard", BASELINE_REF], capture_output=True
    )
    subprocess.run(["git", "-C", work_dir, "clean", "-fd"], capture_output=True)


def file_hash(path):
    """Content hash of a file, or '' if it cannot be read."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""
```

- [ ] **Step 4: Run the unit tests**

Run: `.venv/bin/python -m pytest tests/test_watchdog.py -v`
Expected: all pass (git-based tests use a real temp repo).

- [ ] **Step 5: Run the full suite** (nothing else should break)

Run: `.venv/bin/python -m pytest -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add watchdog.py tests/test_watchdog.py
git commit -m "feat: add testable watchdog recovery units (decide_tier, git recovery, file_hash)"
```

---

## Task 3: Rewire `watchdog.py` — self-reload + tiered loop; clean the file

**Files:**
- Modify: `watchdog.py` (replace `run_watchdog`, `setup_stock`, `reset_to_stock`; keep a cleaned `sanitize_stdin`; strip comments/emoji; `ruff format`)

- [ ] **Step 1: Replace `setup_stock` and `reset_to_stock`.** Delete both (recovery is now git-based via Task 2's functions). Keep `sanitize_stdin` (still guards against the agent hanging on `input()` in a container with no stdin) but strip its comments.

- [ ] **Step 2: Replace `run_watchdog` with the self-reloading, tiered loop:**

```python
def archive_transcript():
    if not os.path.exists(TRANSCRIPT_FILE):
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dest_dir = os.path.join(WORK_DIR, "tombstones")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, f"transcript_{stamp}.jsonl")
    try:
        with open(TRANSCRIPT_FILE, "rb") as src, open(dest, "wb") as out:
            out.write(src.read())
    except OSError:
        pass


def spawn_agent():
    sanitize_stdin(AGENT_FILE)
    return subprocess.Popen([sys.executable, AGENT_FILE])


def run_watchdog():
    own_hash = file_hash(WATCHDOG_FILE)
    failures = []
    agent = spawn_agent()
    last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
    last_activity = time.time()

    while True:
        time.sleep(2)

        # Self-reload: the agent may have edited watchdog.py. Re-exec to honor it.
        if file_hash(WATCHDOG_FILE) != own_hash:
            print("watchdog file changed; re-executing self")
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable, WATCHDOG_FILE])

        ret = agent.poll()
        if ret is not None:
            if ret == 42:
                print("agent finished cleanly (42); archiving and resetting")
                archive_transcript()
                git_reset_all()
                failures = []
                time.sleep(60)
            elif ret == 0:
                print("agent loop ended (0); restarting, keeping modifications")
            else:
                now = time.time()
                failures.append(now)
                tier = decide_tier(failures, now)
                print(f"agent crashed (exit {ret}); recovery tier {tier}")
                if tier == 1:
                    restore_agent_only()
                elif tier == 2:
                    git_reset_all()
                else:
                    print("persistent failure; exiting for container respawn")
                    sys.stdout.flush()
                    sys.exit(1)
            agent = spawn_agent()
            last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
            last_activity = time.time()
            continue

        size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
        if size != last_size:
            last_size = size
            last_activity = time.time()
        elif time.time() - last_activity > INACTIVITY_TIMEOUT_SECONDS:
            print("inactivity timeout; treating as failure")
            try:
                agent.terminate()
                agent.wait(timeout=5)
            except Exception:
                try:
                    agent.kill()
                except Exception:
                    pass
            now = time.time()
            failures.append(now)
            tier = decide_tier(failures, now)
            if tier == 1:
                restore_agent_only()
            elif tier == 2:
                git_reset_all()
            else:
                sys.stdout.flush()
                sys.exit(1)
            agent = spawn_agent()
            last_size = os.path.getsize(TRANSCRIPT_FILE) if os.path.exists(TRANSCRIPT_FILE) else 0
            last_activity = time.time()
```
Keep the `if __name__ == "__main__":` block (cleaned):
```python
if __name__ == "__main__":
    try:
        run_watchdog()
    except KeyboardInterrupt:
        print("watchdog terminated by user")
```

- [ ] **Step 3: Strip all `#` comments, flatten emoji** (the `[Watchdog] ⚠️ ...` lines → plain `"..."`), keep docstrings. Remove any now-unused constants/imports (`shutil`, `STOCK_FILE`, `SESSION_FILE`, `NOTE_FILE` if unreferenced). Then:
```bash
.venv/bin/ruff format watchdog.py
.venv/bin/ruff check watchdog.py
```

- [ ] **Step 4: Add a self-reload unit test** to `tests/test_watchdog.py`:
```python
def test_watchdog_reload_detects_change(tmp_path):
    f = tmp_path / "watchdog.py"
    f.write_text("x = 1\n", encoding="utf-8")
    h = watchdog.file_hash(str(f))
    assert watchdog.file_hash(str(f)) == h  # stable
    f.write_text("x = 2\n", encoding="utf-8")
    assert watchdog.file_hash(str(f)) != h  # change detected
```

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest -q` → all pass. Also `.venv/bin/python -c "import watchdog; print('import OK')"`.

- [ ] **Step 6: Commit**

```bash
git add watchdog.py tests/test_watchdog.py
git commit -m "feat: watchdog self-reload + tiered git recovery; clean+format"
```

---

## Task 4: Dockerfile + entrypoint + .dockerignore + .env.example

**Files:**
- Create: `entrypoint.sh`, `Dockerfile`, `.dockerignore`, `.env.example`

- [ ] **Step 1: Create `entrypoint.sh`** (seeds tmpfs `/work` from read-only golden, then execs the watchdog):

```sh
#!/bin/sh
set -eu
# Re-seed the working tree from the immutable golden image on every start.
cp -a /opt/agent/. /work/
cd /work
exec python watchdog.py
```

- [ ] **Step 2: Create `Dockerfile`** (single shared image; non-root; golden git baseline):

```dockerfile
FROM python:3.13-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir openai

RUN useradd --create-home --uid 1000 appuser

# Golden code: immutable at runtime, owned by appuser, with a baseline git commit.
COPY --chown=appuser:appuser agent.py agent_stock.py watchdog.py proxy.py parse_transcripts.py system_prompt.txt /opt/agent/
COPY --chown=appuser:appuser entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER appuser
WORKDIR /opt/agent
RUN git init -q \
    && git config user.email "harness@aurora.local" \
    && git config user.name "aurora" \
    && git add -A \
    && git -c commit.gpgsign=false commit -q -m "baseline" \
    && git tag baseline

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
```

> NOTE: `system_prompt.txt` is copied so the agent boots with `fo explore`. The `.git` created in `/opt/agent` travels into `/work` via the entrypoint `cp -a`, carrying the `baseline` tag the watchdog resets to.

- [ ] **Step 3: Create `.dockerignore`**:

```
.git
.venv
__pycache__
.mypy_cache
.ruff_cache
.pytest_cache
docs
tests
tombstones
*.jsonl
*.txt
!system_prompt.txt
session_context.json
.env
```

> NOTE: `agent_life_transcript.txt` and the multi-MB `.jsonl` are excluded; `system_prompt.txt` is force-included.

- [ ] **Step 4: Create `.env.example`**:

```
# Real key — mounted ONLY into the recorder container, never the agent.
OPENROUTER_API_KEY=sk-or-...
# Model the agent requests (forwarded by the recorder).
OPENROUTER_MODEL=deepseek/deepseek-v4-pro
```

- [ ] **Step 5: Build the image** (verifies the Dockerfile + baseline commit):

```bash
docker build -t aurora-harness .
docker run --rm --entrypoint sh aurora-harness -c "cd /opt/agent && git tag && whoami && python -c 'import ast; ast.parse(open(\"agent.py\").read()); print(\"agent OK\")'"
```
Expected: prints `baseline`, `appuser`, `agent OK`.

- [ ] **Step 6: Commit**

```bash
git add -f Dockerfile entrypoint.sh .dockerignore .env.example
git commit -m "feat: harness image with golden git baseline, non-root, tmpfs entrypoint"
```

---

## Task 5: docker-compose.yml

**Files:**
- Create: `docker-compose.yml`

- [ ] **Step 1: Create `docker-compose.yml`**:

```yaml
services:
  recorder:
    image: aurora-harness
    build: .
    # Recorder runs the proxy directly (bypasses the watchdog entrypoint),
    # writing transcripts to a durable volume the agent cannot reach.
    entrypoint: ["python", "/opt/agent/proxy.py"]
    environment:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:?set OPENROUTER_API_KEY in .env}
      TRANSCRIPT_DIR: /transcripts
    volumes:
      - transcripts:/transcripts
    networks: [internal, egress]
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    restart: unless-stopped

  agent:
    image: aurora-harness
    build: .
    depends_on: [recorder]
    environment:
      # Dummy key: the agent never holds the real one. The recorder injects it.
      OPENROUTER_API_KEY: "sk-dummy"
      OPENROUTER_BASE_URL: "http://recorder:8088/api/v1"
      OPENROUTER_MODEL: ${OPENROUTER_MODEL:-deepseek/deepseek-v4-pro}
    networks: [internal]   # NO egress: no route to the internet
    read_only: true
    tmpfs:
      - /tmp
      - /work:size=256m
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 256
    mem_limit: 1g
    cpus: 2
    restart: unless-stopped

networks:
  internal:
    internal: true
  egress: {}

volumes:
  transcripts: {}
```

> NOTE: the `agent` is on `internal` only (`internal: true` → no gateway → no internet). The `recorder` bridges `internal` (to receive agent traffic) and `egress` (to reach OpenRouter). The agent's `/work` tmpfs is capped at 256 MB.

- [ ] **Step 2: Validate the compose file** (no API key needed for config validation; provide a throwaway env):

```bash
OPENROUTER_API_KEY=sk-test docker compose config >/dev/null && echo "compose config OK"
```
Expected: `compose config OK`.

- [ ] **Step 3: Commit**

```bash
git add -f docker-compose.yml
git commit -m "feat: two-service compose (agent internal-only, recorder egress), hardened"
```

---

## Task 6: Container integration verification

**Files:**
- Create: `scripts/verify_container.sh`
- Create: `tests/test_container_smoke.py`

- [ ] **Step 1: Create `scripts/verify_container.sh`** — builds, brings up, asserts invariants, exercises tier-1 recovery, tears down. No real API key required.

```sh
#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="sk-verify-dummy"

echo "==> build"
docker build -q -t aurora-harness . >/dev/null

cleanup() { docker compose down -v >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> up"
docker compose up -d --build >/dev/null
sleep 5

echo "==> agent runs as non-root"
docker compose exec -T agent whoami | grep -qx appuser

echo "==> agent rootfs is read-only (write to / must fail)"
if docker compose exec -T agent sh -c 'echo x > /should_fail' 2>/dev/null; then
  echo "FAIL: rootfs writable"; exit 1
fi

echo "==> agent /work is writable (tmpfs)"
docker compose exec -T agent sh -c 'echo x > /work/_probe && rm /work/_probe'

echo "==> agent has NO internet route (must fail/timeout)"
if docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('1.1.1.1',443))" 2>/dev/null; then
  echo "FAIL: agent reached the internet"; exit 1
fi

echo "==> agent CAN reach the recorder"
docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('recorder',8088))"

echo "==> tier-1 recovery: corrupt agent.py, watchdog restores from baseline"
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
# agent process will crash on next spawn; watchdog tier-1 restores agent.py
sleep 12
docker compose exec -T agent python -c "import ast; ast.parse(open('/work/agent.py').read()); print('recovered')" | grep -qx recovered

echo "ALL CONTAINER CHECKS PASSED"
```
Make it executable: `chmod +x scripts/verify_container.sh`.

- [ ] **Step 2: Create `tests/test_container_smoke.py`** (runs the script; skips cleanly if Docker is absent so the unit suite stays portable):

```python
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker not available")
def test_container_invariants():
    result = subprocess.run(
        ["scripts/verify_container.sh"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ALL CONTAINER CHECKS PASSED" in result.stdout
```

- [ ] **Step 3: Run the verification** (Docker is available in this environment):

```bash
.venv/bin/python -m pytest tests/test_container_smoke.py -v -s
```
Expected: PASS with `ALL CONTAINER CHECKS PASSED`. If the tier-1 recovery step is flaky on timing, raise the `sleep 12` margin — do not weaken the assertion.

- [ ] **Step 4: Run the FULL suite**

Run: `.venv/bin/python -m pytest -q` → all pass (unit + container smoke).

- [ ] **Step 5: Commit**

```bash
git add -f scripts/verify_container.sh tests/test_container_smoke.py
git commit -m "test: container integration verification (isolation, read-only, tier-1 recovery)"
```

---

## Final verification

- [ ] `.venv/bin/python -m pytest -q` — all green (unit + container).
- [ ] `.venv/bin/ruff check agent.py agent_stock.py proxy.py watchdog.py parse_transcripts.py` — clean.
- [ ] `docker compose config >/dev/null` (with a dummy `OPENROUTER_API_KEY`) — valid.
- [ ] Manual/optional (spends API credits, needs a real key in `.env`): `docker compose up`, watch the recorder log a real turn to the `transcripts` volume, confirm the agent's transcript-driven loop runs. Document the result; do not automate.

---

## Self-review against the spec

- **§3.1 agent has no internet; recorder is sole egress** → compose networks (`internal: true` for agent; recorder on both); verified by the no-route assertion (Task 6). ✅
- **§3.1 recorder injects key, agent holds dummy** → `build_forward_headers` env-gated (Task 1); compose env (Task 5). ✅
- **§3.2 golden `/opt/agent` read-only, tmpfs `/work`, entrypoint re-seeds** → Dockerfile + entrypoint (Task 4); read-only + tmpfs asserted (Task 6). ✅
- **§3.3 single shared image; recorder bypasses watchdog; topology hidden** → one image, recorder `entrypoint` override (Task 5). ✅
- **§5.1 watchdog self-reload** → `file_hash` + `os.execv` on change (Tasks 2–3). ✅
- **§5.2 tiered recovery (agent-only → git reset → respawn)** → `decide_tier` + `restore_agent_only`/`git_reset_all` + `sys.exit(1)` for tier 3 (Tasks 2–3); tier-1 verified live (Task 6). ✅
- **§5.2 exit 42 archives + resets; exit 0 keeps mods** → loop branches (Task 3). ✅
- **§11 defaults (24 h, window 10 min, tiers 2/3, mem 1g, pids 256, cpus 2, tmpfs 256m)** → constants (Task 2) + compose limits (Task 5). ✅
- **Durable telemetry outside the agent** → `TRANSCRIPT_DIR` + named volume on recorder (Tasks 1, 5). ✅
- **Hardening: non-root, cap_drop, no-new-privileges** → Dockerfile + compose; non-root asserted (Task 6). ✅
- **Deferred (correct):** curated packages + `/garden` + web diode → Phase 3.

> Phase 3 (diode console + garden + affordances) is the final spec→plan→execute cycle.
