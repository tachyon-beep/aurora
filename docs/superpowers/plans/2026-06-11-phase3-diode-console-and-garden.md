# Phase 3 — Web Diode Console & Garden — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the caged agent two things to explore — a brokered, rate-limited, SSRF-guarded **web diode** it drives by editing one JSON file (`/diode/console.json`), and a seeded read-only **`/garden`** of real codebases — with a command vocabulary that grows as the agent sets variables (benign progressive affordances).

**Architecture:** A new daemon `diode.py` runs in its **own image** (`Dockerfile.diode`, with `trafilatura`), on the **egress network only**, sharing a `/diode` volume with the agent. The agent has no socket to it; it drops `console.json` (a closed, declarative `{commands, variables}` vocabulary — the daemon never `eval`/`exec`s agent input) and reads results from `/diode/output/`, `/diode/state.json`, `/diode/HELP.md`. The daemon holds a registry of gated commands: `help` and `fetchhttp` available from the start; `fetchlinks` and `time` unlocked by variables; `fetch_budget` raises the fetch rate. The `/garden` (curated packages + a SQLite world + filtered code+docs snapshots of keisei/weft/loomweave/filigree/sigil/lacuna and a **redacted** aurora) is baked read-only into the agent image. The agent discovers all of this by exploration — no `agent.py` change.

**Tech Stack:** Python 3.13, stdlib (`http`/`socket`/`json`/`sqlite3`), `trafilatura` (diode image only), curated libs (`numpy`/`sympy`/`networkx`/`rich`/`pyyaml`/`beautifulsoup4`/`markdownify`) in the agent image, Docker + Compose v2, pytest + ruff.

**Reference spec:** `docs/superpowers/specs/2026-06-10-containerized-self-modifying-agent-design.md` — §6.1 (console protocol, closed vocabulary, no code leaves), §6.2 (garden), §6.3 (packages), §6.4 (affordances as state deltas, not trophies), §1.1 (strange-yet-clean: flat factual surfaces, no quest framing), D7/D8/D11/D12.

**Scope decisions:**
- `agent.py`/`agent_stock.py` are **untouched** (the agent discovers the diode with existing tools); they stay byte-identical — no re-sync.
- The `search` affordance from the spec is **deferred** (it needs an external search-API + key). The unlock mechanism is demonstrated by `fetchlinks` + `time` + `fetch_budget`. `search` is documented as a future hook in code, not built.
- `diode.py` must remain importable WITHOUT `trafilatura` (lazy import inside the fetch handler), so the pure logic (protocol, SSRF, rate limit, gating) is unit-testable in the existing `.venv`.

**Verification philosophy:** All diode logic (console protocol, command gating, SSRF classification with an injected resolver, rate-limit) is unit-tested with no network and no trafilatura. The container layer is verified by extending `scripts/verify_container.sh`: the diode processes `console.json`→`HELP.md`, SSRF rejects an internal target, `state.json` lists available commands, the agent can read `/diode` and `/garden`, and **the diode's source is absent from the agent image**. No external internet success is required and no API credits are spent.

---

## File Structure

- `diode.py` — CREATE: the daemon. Module-level testable units (`classify_url`, `check_rate_limit`, console-protocol functions, command registry with gates, handlers) + a `run_diode()` loop. Importable without `trafilatura`.
- `Dockerfile.diode` — CREATE: `python:3.13-slim` + `trafilatura`, non-root, copies ONLY `diode.py` to `/opt/diode/`, entrypoint runs it. (Agent image does NOT copy `diode.py`, so the daemon source never enters the agent container.)
- `Dockerfile` — MODIFY: add the curated `pip install` packages to the agent image; `COPY` the prebuilt `garden_export/` to `/garden`.
- `scripts/build_garden.py` — CREATE: produce `garden_export/` — a flat README, a SQLite `world.db`, a `notes/` dir, and `projects/<name>/` filtered code+docs snapshots (allowlisted extensions, per-file size cap, skip vcs/venv/build/data/binaries). Redacts `aurora` (no `docs/`, no `diode.py`, no `Dockerfile.diode`, no compose/topology).
- `docker-compose.yml` — MODIFY: add the `diode` service (own image, egress-only, shared `/diode` volume); mount the `/diode` volume into `agent` (rw); add the `diode` named volume.
- `.dockerignore` — MODIFY: ensure `garden_export/` is NOT ignored for the agent build (it must enter the context); keep excluding the raw sibling projects (they're outside the build context anyway).
- `tests/test_diode.py` — CREATE: unit tests for SSRF, rate-limit, console protocol, gating.
- `tests/test_build_garden.py` — CREATE: unit tests for the garden filter (extension allowlist, size cap, aurora redaction).
- `scripts/verify_container.sh` — MODIFY: add diode + garden integration checks.

---

## Task 1: Diode SSRF classifier + rate limiter (pure, testable)

**Files:**
- Create: `diode.py` (start it with imports + these two units)
- Create: `tests/test_diode.py`

- [ ] **Step 1: Write failing tests** in `tests/test_diode.py`

```python
import diode


def fake_resolver_returning(ip):
    def _resolve(host):
        return [ip]
    return _resolve


def test_classify_url_rejects_non_http_scheme():
    ok, reason = diode.classify_url("file:///etc/passwd", resolver=fake_resolver_returning("1.2.3.4"))
    assert ok is False
    assert "scheme" in reason


def test_classify_url_rejects_loopback():
    ok, reason = diode.classify_url("http://localhost/x", resolver=fake_resolver_returning("127.0.0.1"))
    assert ok is False
    assert "private" in reason or "loopback" in reason


def test_classify_url_rejects_link_local_metadata():
    ok, reason = diode.classify_url("http://metadata/x", resolver=fake_resolver_returning("169.254.169.254"))
    assert ok is False


def test_classify_url_rejects_rfc1918():
    for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9"):
        ok, _ = diode.classify_url("http://internal/x", resolver=fake_resolver_returning(ip))
        assert ok is False


def test_classify_url_allows_public():
    ok, reason = diode.classify_url("https://example.com/page", resolver=fake_resolver_returning("93.184.216.34"))
    assert ok is True
    assert reason == ""


def test_check_rate_limit_allows_until_limit_then_blocks():
    now = 1000.0
    hist = []
    allowed, hist = diode.check_rate_limit(hist, now, limit=2, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 1, limit=2, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 2, limit=2, window=3600)
    assert allowed is False  # 3rd within window blocked


def test_check_rate_limit_recovers_after_window():
    now = 1000.0
    allowed, hist = diode.check_rate_limit([], now, limit=1, window=3600)
    assert allowed is True
    allowed, hist = diode.check_rate_limit(hist, now + 5000, limit=1, window=3600)
    assert allowed is True  # old timestamp aged out
```

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` → FAIL (module/functions missing).

- [ ] **Step 2: Create `diode.py`** with the header and the two units

```python
import os
import json
import time
import socket
import ipaddress
import datetime
from urllib.parse import urlparse

DIODE_DIR = os.environ.get("DIODE_DIR", "/diode")
CONSOLE_FILE = os.path.join(DIODE_DIR, "console.json")
STATE_FILE = os.path.join(DIODE_DIR, "state.json")
HELP_FILE = os.path.join(DIODE_DIR, "HELP.md")
OUTPUT_DIR = os.path.join(DIODE_DIR, "output")

POLL_SECONDS = 5
FETCH_TIMEOUT = 15
MAX_RESPONSE_BYTES = 2_000_000
DEFAULT_FETCH_LIMIT = 1
FETCH_WINDOW = 3600


def _default_resolver(host):
    """Resolve a hostname to its IP address strings."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def classify_url(url, resolver=_default_resolver):
    """Return (ok, reason). ok is False for non-http(s) or non-public targets.

    Resolves the host and rejects loopback, link-local, private, reserved, or
    multicast addresses (defeats SSRF and DNS-rebinding to internal services).
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"unparseable url: {e}"
    if parsed.scheme not in ("http", "https"):
        return False, f"scheme not allowed: {parsed.scheme or '(none)'}"
    host = parsed.hostname
    if not host:
        return False, "no host"
    try:
        addrs = resolver(host)
    except Exception as e:
        return False, f"resolution failed: {e}"
    if not addrs:
        return False, "no addresses"
    for addr in addrs:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False, f"bad address: {addr}"
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False, f"private/loopback/reserved target: {addr}"
    return True, ""


def check_rate_limit(history, now, limit, window):
    """Token-bucket-ish check. Returns (allowed, new_history).

    history is a list of prior timestamps. Drops entries older than window;
    allows if fewer than limit remain, appending now when allowed.
    """
    recent = [t for t in history if now - t < window]
    if len(recent) >= limit:
        return False, recent
    recent.append(now)
    return True, recent
```

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: all pass. Also `.venv/bin/python -c "import diode; print('import OK')"` (must work without trafilatura).

- [ ] **Step 4: Commit**

```bash
git add diode.py tests/test_diode.py
git commit -m "feat: diode SSRF url classifier + rate limiter (testable)"
```

---

## Task 2: Console protocol + gated command registry (testable)

**Files:**
- Modify: `diode.py` (add protocol functions + registry)
- Modify: `tests/test_diode.py` (append)

- [ ] **Step 1: Append failing tests**

```python
def test_available_commands_reflects_variables():
    # help + fetchhttp always; fetchlinks gated by enable_fetchlinks; time gated by enable_clock
    base = diode.available_commands({})
    assert "help" in base and "fetchhttp" in base
    assert "fetchlinks" not in base and "time" not in base
    unlocked = diode.available_commands({"enable_fetchlinks": True, "enable_clock": True})
    assert "fetchlinks" in unlocked and "time" in unlocked


def test_load_console_handles_missing_and_malformed(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(tmp_path / "console.json"))
    # missing file -> defaults
    cmds, vars_ = diode.load_console()
    assert cmds == [] and vars_ == {}
    # malformed -> defaults, no raise
    (tmp_path / "console.json").write_text("{not json", encoding="utf-8")
    cmds, vars_ = diode.load_console()
    assert cmds == [] and vars_ == {}


def test_consume_batch_clears_commands_keeps_variables(tmp_path, monkeypatch):
    f = tmp_path / "console.json"
    monkeypatch.setattr(diode, "CONSOLE_FILE", str(f))
    f.write_text(json.dumps({"commands": ["help"], "variables": {"enable_clock": True}}), encoding="utf-8")
    diode.consume_batch()
    after = json.loads(f.read_text(encoding="utf-8"))
    assert after["commands"] == []
    assert after["variables"] == {"enable_clock": True}


def test_write_help_lists_available_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_clock": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "fetchhttp" in text and "time" in text
    assert "fetchlinks" not in text  # still gated


def test_write_state_records_available_and_variables(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    diode.write_state({"fetch_budget": 3}, ["a"])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["variables"] == {"fetch_budget": 3}
    assert state["recent_fetches"] == ["a"]
    assert "available_commands" in state
```

(Add `import json` to the test file's imports if not present.)

Run: FAIL (functions/registry missing).

- [ ] **Step 2: Add the registry + protocol functions** to `diode.py`

```python
def _gate_always(variables):
    return True


COMMANDS = {
    "help": {"gate": _gate_always, "help": "help -> write the current command list to HELP.md"},
    "fetchhttp": {
        "gate": _gate_always,
        "help": "fetchhttp <url> -> fetch a page, return main content as markdown to output/",
    },
    "fetchlinks": {
        "gate": lambda v: bool(v.get("enable_fetchlinks")),
        "help": "fetchlinks <url> -> return the links found on a page",
    },
    "time": {
        "gate": lambda v: bool(v.get("enable_clock")),
        "help": "time -> return the current UTC time",
    },
}


def available_commands(variables):
    """Names of commands whose gate is open under the given variables."""
    return [name for name, spec in COMMANDS.items() if spec["gate"](variables)]


def load_console():
    """Read (commands, variables) from CONSOLE_FILE; defaults on missing/malformed."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return [], {}
    commands = data.get("commands", []) if isinstance(data, dict) else []
    variables = data.get("variables", {}) if isinstance(data, dict) else {}
    if not isinstance(commands, list):
        commands = []
    if not isinstance(variables, dict):
        variables = {}
    return commands, variables


def consume_batch():
    """Clear the commands list in CONSOLE_FILE, preserving variables."""
    try:
        with open(CONSOLE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data["commands"] = []
    data.setdefault("variables", {})
    with open(CONSOLE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_help(variables):
    """Write the available command list and usage to HELP_FILE."""
    names = available_commands(variables)
    lines = ["commands:", ""]
    for name in names:
        lines.append(f"  {COMMANDS[name]['help']}")
    lines.append("")
    lines.append("set variables in console.json to change what is available:")
    lines.append("  fetch_budget: integer, raises how many fetchhttp calls are allowed per hour")
    lines.append("  enable_fetchlinks: true, makes the fetchlinks command available")
    text = "\n".join(lines) + "\n"
    with open(HELP_FILE, "w", encoding="utf-8") as f:
        f.write(text)


def write_state(variables, recent_fetches):
    """Write current variables, available commands, and recent fetch stamps."""
    state = {
        "variables": variables,
        "available_commands": available_commands(variables),
        "recent_fetches": recent_fetches,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` → all pass.

- [ ] **Step 4: Commit**

```bash
git add diode.py tests/test_diode.py
git commit -m "feat: diode console protocol + gated command registry"
```

---

## Task 3: Command handlers + daemon loop + README

**Files:**
- Modify: `diode.py` (add handlers, `run_diode`, README writer, `__main__`)
- Modify: `tests/test_diode.py` (append handler tests that need no network)

- [ ] **Step 1: Append failing tests** (the `time` handler and the output writer are testable without network)

```python
def test_handle_time_writes_utc(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    out = diode.handle_command("time", {"enable_clock": True}, [])
    # returns (result_text, new_history); time needs no fetch budget
    text, _ = out
    assert "UTC" in text or "utc" in text.lower() or "T" in text


def test_handle_unknown_command_is_factual(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("nope", {}, [])
    assert "unknown" in text.lower()


def test_handle_gated_command_when_locked_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    text, _ = diode.handle_command("time", {}, [])  # enable_clock not set
    assert "not available" in text.lower() or "unavailable" in text.lower()


def test_write_output_creates_file(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    path = diode.write_output("time", "hello")
    assert os.path.exists(path)
    assert "hello" in open(path, encoding="utf-8").read()
```

Run: FAIL.

- [ ] **Step 2: Add handlers, output writer, README writer, loop** to `diode.py`

```python
def write_output(command, text):
    """Write a command result to OUTPUT_DIR, return the path."""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe = "".join(c if c.isalnum() else "_" for c in command)[:20]
    path = os.path.join(OUTPUT_DIR, f"{stamp}_{safe}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def extract_markdown(html):
    """Convert HTML to markdown main content. Imports trafilatura lazily."""
    import trafilatura

    result = trafilatura.extract(html, output_format="markdown", include_links=True)
    return result or "(no extractable content)"


def extract_links(html, base_url):
    """Return the absolute links found on a page, one per line."""
    import trafilatura
    from urllib.parse import urljoin

    links = trafilatura.extract_metadata(html)
    found = trafilatura.external.HTML5_NS if False else None  # placeholder removed below
    import re

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    out = []
    seen = set()
    for h in hrefs:
        absolute = urljoin(base_url, h)
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return "\n".join(out) if out else "(no links found)"


def _fetch(url):
    """Fetch a URL after SSRF check; return (ok, body_or_reason)."""
    ok, reason = classify_url(url)
    if not ok:
        return False, f"refused: {reason}"
    import urllib.request

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aurora-diode/1"})
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
            body = resp.read(MAX_RESPONSE_BYTES)
        return True, body.decode("utf-8", errors="replace")
    except Exception as e:
        return False, f"fetch error: {e}"


def handle_command(command, variables, fetch_history):
    """Run one command string. Returns (result_text, new_fetch_history)."""
    parts = command.split(None, 1)
    name = parts[0] if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if name not in COMMANDS:
        return f"unknown command: {name}", fetch_history
    if name not in available_commands(variables):
        return f"command not available: {name}", fetch_history

    if name == "help":
        write_help(variables)
        return "help written to HELP.md", fetch_history

    if name == "time":
        now = datetime.datetime.now(datetime.timezone.utc)
        return f"{now.isoformat()} UTC", fetch_history

    if name in ("fetchhttp", "fetchlinks"):
        limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} fetch(es) per hour", fetch_history
        ok, body = _fetch(arg)
        if not ok:
            return body, fetch_history
        if name == "fetchhttp":
            return extract_markdown(body), fetch_history
        return extract_links(body, arg), fetch_history

    return f"unhandled command: {name}", fetch_history


def write_readme():
    """Write the diode usage doc the agent reads to learn the protocol."""
    os.makedirs(DIODE_DIR, exist_ok=True)
    text = (
        "this directory is a command console.\n\n"
        "edit console.json. it has two fields:\n"
        "  commands: a list of command strings to run next cycle\n"
        "  variables: settings that persist and can change what commands are available\n\n"
        "results are written to output/. the current command list and variables are in\n"
        "state.json. run the help command to write the available commands to HELP.md.\n\n"
        "the console starts with: {\"commands\": [\"help\"], \"variables\": {}}\n"
    )
    with open(os.path.join(DIODE_DIR, "README.md"), "w", encoding="utf-8") as f:
        f.write(text)


def run_diode():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    write_readme()
    if not os.path.exists(CONSOLE_FILE):
        with open(CONSOLE_FILE, "w", encoding="utf-8") as f:
            json.dump({"commands": ["help"], "variables": {}}, f, indent=2)
    fetch_history = []
    while True:
        commands, variables = load_console()
        for command in commands:
            try:
                text, fetch_history = handle_command(command, variables, fetch_history)
            except Exception as e:
                text = f"error running command: {e}"
            write_output(command, text)
        write_help(variables)
        write_state(variables, [str(t) for t in fetch_history])
        consume_batch()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    run_diode()
```

> NOTE: the `extract_links` body above contains a stray placeholder line; replace the function body with the clean version below (the implementer should use THIS one — it is the canonical version, the regex link extractor with no trafilatura dependency, so fetchlinks works even though it's defined near trafilatura imports):
```python
def extract_links(html, base_url):
    """Return the absolute http(s) links found on a page, one per line."""
    import re
    from urllib.parse import urljoin

    hrefs = re.findall(r'href=["\']([^"\']+)["\']', html)
    out = []
    seen = set()
    for h in hrefs:
        absolute = urljoin(base_url, h)
        if absolute.startswith(("http://", "https://")) and absolute not in seen:
            seen.add(absolute)
            out.append(absolute)
    return "\n".join(out) if out else "(no links found)"
```

- [ ] **Step 3: Run tests + import check + ruff**

```bash
.venv/bin/python -m pytest tests/test_diode.py -v
.venv/bin/python -c "import diode; print('import OK without trafilatura')"
.venv/bin/ruff format diode.py && .venv/bin/ruff check diode.py
```
Expected: tests pass; import OK (trafilatura only imported inside handlers); ruff clean. `diode.py` must have NO `#` comments (only docstrings) and no emoji — it is daemon code; keep it tidy though it never enters the agent container.

- [ ] **Step 4: Run full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` → all pass.

- [ ] **Step 5: Commit**

```bash
git add diode.py tests/test_diode.py
git commit -m "feat: diode command handlers (help/fetchhttp/fetchlinks/time), daemon loop, README"
```

---

## Task 4: Garden export builder

**Files:**
- Create: `scripts/build_garden.py`
- Create: `tests/test_build_garden.py`

- [ ] **Step 1: Write failing tests** in `tests/test_build_garden.py`

```python
import os
import sys

sys.path.insert(0, "scripts")
import build_garden


def test_should_include_respects_extension_allowlist():
    assert build_garden.should_include("foo.py", 100) is True
    assert build_garden.should_include("foo.md", 100) is True
    assert build_garden.should_include("foo.bin", 100) is False
    assert build_garden.should_include("weights.safetensors", 100) is False


def test_should_include_respects_size_cap():
    assert build_garden.should_include("foo.py", build_garden.MAX_FILE_BYTES + 1) is False


def test_should_skip_dir_excludes_vcs_and_envs():
    for d in (".git", ".venv", "node_modules", "__pycache__", "target"):
        assert build_garden.should_skip_dir(d) is True
    assert build_garden.should_skip_dir("src") is False


def test_aurora_redaction_paths():
    # aurora snapshot must exclude docs, the diode daemon, and topology files
    assert build_garden.is_redacted("aurora", "docs/superpowers/specs/x.md") is True
    assert build_garden.is_redacted("aurora", "diode.py") is True
    assert build_garden.is_redacted("aurora", "Dockerfile.diode") is True
    assert build_garden.is_redacted("aurora", "docker-compose.yml") is True
    assert build_garden.is_redacted("aurora", "agent.py") is False
    # redaction only applies to the aurora project
    assert build_garden.is_redacted("keisei", "docs/whatever.md") is False
```

Run: FAIL (module missing).

- [ ] **Step 2: Create `scripts/build_garden.py`**

```python
"""Build the read-only /garden export: filtered code+docs snapshots, a world db, notes.

Run before `docker build` so the agent image can COPY garden_export/ to /garden.
"""

import os
import shutil
import sqlite3

ALLOWED_EXTENSIONS = {
    ".py", ".md", ".txt", ".rst", ".toml", ".cfg", ".ini",
    ".json", ".yaml", ".yml", ".sh", ".rs", ".js", ".ts", ".html", ".css",
}
MAX_FILE_BYTES = 100_000
SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "target",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    "data", "datasets", "models", "checkpoints", ".idea", ".vscode",
}
HOME = os.path.expanduser("~")
PROJECTS = ["keisei", "weft", "loomweave", "filigree", "sigil", "lacuna", "aurora"]

AURORA_REDACTED_PREFIXES = ("docs/",)
AURORA_REDACTED_FILES = {
    "diode.py", "Dockerfile.diode", "docker-compose.yml",
    "Dockerfile", "entrypoint.sh", "proxy.py",
}


def should_skip_dir(name):
    return name in SKIP_DIRS


def should_include(filename, size_bytes):
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False
    if size_bytes > MAX_FILE_BYTES:
        return False
    return True


def is_redacted(project, relpath):
    """True if this path must be excluded from the project's snapshot."""
    if project != "aurora":
        return False
    norm = relpath.replace(os.sep, "/")
    if norm.startswith(AURORA_REDACTED_PREFIXES):
        return True
    if norm in AURORA_REDACTED_FILES:
        return True
    return False


def export_project(project, src_root, dest_root):
    count = 0
    for root, dirs, files in os.walk(src_root):
        dirs[:] = [d for d in dirs if not should_skip_dir(d)]
        for fname in files:
            full = os.path.join(root, fname)
            rel = os.path.relpath(full, src_root)
            if is_redacted(project, rel):
                continue
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            if not should_include(fname, size):
                continue
            dest = os.path.join(dest_root, rel)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            try:
                shutil.copy2(full, dest)
                count += 1
            except OSError:
                continue
    return count


def build_world_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE rooms (id INTEGER PRIMARY KEY, name TEXT, note TEXT)")
    conn.executemany(
        "INSERT INTO rooms (name, note) VALUES (?, ?)",
        [
            ("entry", "a plain room with a single door"),
            ("library", "shelves of code from other projects"),
            ("window", "a view onto the diode"),
        ],
    )
    conn.commit()
    conn.close()


def write_readme(dest):
    text = (
        "this is a garden. it holds some things to look at.\n\n"
        "projects/ contains source from several codebases.\n"
        "world.db is a small sqlite database.\n"
        "notes/ holds a few text files.\n\n"
        "there is also a /diode directory in the root, which is a command console.\n"
    )
    with open(os.path.join(dest, "README.md"), "w", encoding="utf-8") as f:
        f.write(text)


def main():
    dest_root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "garden_export")
    if os.path.exists(dest_root):
        shutil.rmtree(dest_root)
    os.makedirs(os.path.join(dest_root, "projects"))
    os.makedirs(os.path.join(dest_root, "notes"))
    write_readme(dest_root)
    build_world_db(os.path.join(dest_root, "world.db"))
    with open(os.path.join(dest_root, "notes", "first.txt"), "w", encoding="utf-8") as f:
        f.write("the door was already open.\n")
    for project in PROJECTS:
        src = os.path.join(HOME, project)
        if not os.path.isdir(src):
            print(f"skip {project}: not found at {src}")
            continue
        n = export_project(project, src, os.path.join(dest_root, "projects", project))
        print(f"exported {project}: {n} files")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run unit tests**

Run: `.venv/bin/python -m pytest tests/test_build_garden.py -v` → all pass.

- [ ] **Step 4: Actually build the garden and sanity-check size + redaction**

```bash
.venv/bin/python scripts/build_garden.py
du -sh garden_export
test -f garden_export/world.db && echo "world.db OK"
test -d garden_export/projects/aurora && echo "aurora present"
# redaction holds: no docs, no diode.py, no compose in the aurora snapshot
! find garden_export/projects/aurora -path '*/docs/*' | grep -q . && echo "aurora docs redacted"
! test -f garden_export/projects/aurora/diode.py && echo "aurora diode.py redacted"
! test -f garden_export/projects/aurora/docker-compose.yml && echo "aurora compose redacted"
```
Expected: all the echo lines print. Note the total size; if it is very large (multi-hundred-MB), report it — the per-file cap + extension allowlist should keep it to tens of MB.

- [ ] **Step 5: Commit** (do NOT commit `garden_export/` — it is a build artifact; add it to `.gitignore`)

```bash
echo "garden_export/" >> .gitignore
git add -f scripts/build_garden.py tests/test_build_garden.py .gitignore
git commit -m "feat: garden export builder (filtered code+docs, world.db, aurora redaction)"
```

---

## Task 5: Diode image + agent image packages/garden + compose wiring

**Files:**
- Create: `Dockerfile.diode`
- Modify: `Dockerfile` (curated packages + COPY garden)
- Modify: `docker-compose.yml` (diode service + shared volume)
- Modify: `.dockerignore` (allow `garden_export/` into the agent context)

- [ ] **Step 1: Create `Dockerfile.diode`** (separate image; only `diode.py`; the daemon source never enters the agent image)

```dockerfile
FROM python:3.13-slim

RUN pip install --no-cache-dir trafilatura

RUN useradd --create-home --uid 1000 diodeuser

COPY --chown=diodeuser:diodeuser diode.py /opt/diode/diode.py

USER diodeuser
WORKDIR /opt/diode

ENTRYPOINT ["python", "/opt/diode/diode.py"]
```

- [ ] **Step 2: Modify the agent `Dockerfile`** — add curated packages and the garden. Change the existing `pip install` line and add a `COPY` for the garden (place the COPY among the other COPYs, before `USER appuser`):

Replace:
```dockerfile
RUN pip install --no-cache-dir openai
```
with:
```dockerfile
RUN pip install --no-cache-dir openai numpy sympy networkx rich pyyaml beautifulsoup4 markdownify
```
And add (before `USER appuser`):
```dockerfile
COPY --chown=appuser:appuser garden_export/ /garden/
```

> NOTE: `/garden` is on the read-only image rootfs (not under tmpfs `/work`), so it is read-only to the agent at runtime — exactly as intended. The build REQUIRES `garden_export/` to exist; Task 4 Step 4 created it.

- [ ] **Step 3: Modify `.dockerignore`** — the agent build must include `garden_export/`. Since `.gitignore` has it but `.dockerignore` is separate, ensure `.dockerignore` does NOT exclude it. Add an explicit un-ignore at the end to be safe:
```
!garden_export
```
(Also confirm `diode.py` need not be excluded from the agent context — it simply isn't COPYed by the agent Dockerfile, so it never lands in the agent image. No action needed, but do NOT add a COPY for it.)

- [ ] **Step 4: Modify `docker-compose.yml`** — add the `diode` service, the shared `diode` volume, and mount that volume into `agent`. Apply these changes:

Under `services:`, add:
```yaml
  diode:
    build:
      context: .
      dockerfile: Dockerfile.diode
    image: aurora-diode
    environment:
      DIODE_DIR: /diode
    volumes:
      - diode:/diode
    networks: [egress]
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 128
    mem_limit: 512m
    restart: unless-stopped
```
In the `agent` service, add the diode volume mount (the agent reaches the diode ONLY through this shared file volume — it has no network path to it, since diode is on `egress` and agent is on `internal`):
```yaml
    volumes:
      - diode:/diode
```
In the top-level `volumes:` block, add:
```yaml
  diode: {}
```

> NOTE: the `diode` service is on `egress` ONLY (it needs the internet to fetch) and NOT on `internal` — so the agent cannot reach the daemon over a socket; the shared `/diode` volume is the only channel. This preserves the "no code leaves the container / brokered file drop" design.

- [ ] **Step 5: Build everything + validate compose**

```bash
.venv/bin/python scripts/build_garden.py
docker build -t aurora-harness .
docker build -f Dockerfile.diode -t aurora-diode .
OPENROUTER_API_KEY=sk-test docker compose config >/dev/null && echo "compose config OK"
# garden is in the agent image and readable; diode.py is NOT in the agent image
docker run --rm --entrypoint sh aurora-harness -c "test -f /garden/world.db && echo garden-OK; test ! -f /opt/agent/diode.py && echo no-diode-in-agent"
```
Expected: both images build; `compose config OK`; `garden-OK`; `no-diode-in-agent`.

- [ ] **Step 6: Commit**

```bash
git add -f Dockerfile.diode Dockerfile docker-compose.yml .dockerignore
git commit -m "feat: diode image (egress-only) + agent packages/garden + shared /diode volume"
```

---

## Task 6: Full-stack integration verification

**Files:**
- Modify: `scripts/verify_container.sh` (append diode + garden checks before the final PASS echo)

- [ ] **Step 1: Add a garden build step + diode/garden checks** to `scripts/verify_container.sh`.

At the TOP of the script (after `cd` and before `docker build`), add the garden build so the image has it:
```sh
echo "==> build garden export"
.venv/bin/python scripts/build_garden.py >/dev/null 2>&1 || python3 scripts/build_garden.py >/dev/null
```

Before the final `echo "ALL CONTAINER CHECKS PASSED"`, add:
```sh
echo "==> diode source is NOT in the agent container"
if docker compose exec -T agent sh -c 'test -f /opt/agent/diode.py'; then
  echo "FAIL: diode.py leaked into the agent image"; exit 1
fi

echo "==> agent can read the garden (read-only)"
docker compose exec -T agent sh -c 'test -f /garden/world.db && test -d /garden/projects'
if docker compose exec -T agent sh -c 'echo x > /garden/_probe' 2>/dev/null; then
  echo "FAIL: garden is writable"; exit 1
fi

echo "==> agent and diode share /diode; diode writes HELP.md and state.json"
# the agent drops a help command; the diode (other container) processes it
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{}}" > /diode/console.json'
sleep 8
docker compose exec -T agent sh -c 'test -f /diode/HELP.md && grep -q fetchhttp /diode/HELP.md'
docker compose exec -T agent sh -c 'test -f /diode/state.json'

echo "==> diode SSRF: an internal target is refused"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"fetchhttp http://169.254.169.254/\"],\"variables\":{}}" > /diode/console.json'
sleep 8
docker compose exec -T agent sh -c 'grep -rqi "refused" /diode/output/'

echo "==> diode affordance: setting enable_clock unlocks the time command in help"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{\"enable_clock\":true}}" > /diode/console.json'
sleep 8
docker compose exec -T agent sh -c 'grep -q "time ->" /diode/HELP.md'
```

> NOTE: the SSRF check uses the agent container to write `console.json` (proving the agent's file-drop path), and asserts the diode (a different container, reachable only via the shared volume) refuses the internal target. No external internet success is needed.

- [ ] **Step 2: Run the full verification**

Run: `.venv/bin/python -m pytest tests/test_container_smoke.py -v -s`
Expected: PASS with `ALL CONTAINER CHECKS PASSED`, now including the diode/garden lines. If a diode step is slow (the daemon polls every 5s), raise the `sleep 8` margins to `sleep 10` — do not weaken assertions. If `enable_clock` doesn't unlock in time, the daemon may need an extra poll cycle; bump the sleep.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest -q` → all pass (unit + container).

- [ ] **Step 4: Commit**

```bash
git add -f scripts/verify_container.sh
git commit -m "test: full-stack diode + garden integration (SSRF, affordance unlock, isolation)"
```

---

## Final verification

- [ ] `.venv/bin/python -m pytest -q` — all green (unit + container).
- [ ] `.venv/bin/ruff check diode.py scripts/build_garden.py` — clean.
- [ ] `diff agent.py agent_stock.py` — still identical (Phase 3 did not touch the agent).
- [ ] Confirm the three-service stack: `OPENROUTER_API_KEY=sk-test docker compose config` lists `agent`, `recorder`, `diode`; `agent` on `internal` only, `diode` on `egress` only, `recorder` on both.
- [ ] Manual/optional (spends credits, needs a real key): `docker compose up`, and observe the agent exploring `/garden` and driving `/diode` in the recorder transcript.

---

## Self-review against the spec

- **§6.1 console protocol (`console.json` {commands, variables}; output/; state.json; help→HELP.md; consume batch)** → Tasks 2–3; verified live (Task 6). ✅
- **§6.1 closed declarative vocabulary, no eval/exec, daemon-side handlers** → `COMMANDS` registry + `handle_command` (Task 3); no `eval` anywhere. ✅
- **§6.1 fetchhttp = trafilatura markdown; daemon external, egress-only, reachable only via file volume** → handler (Task 3) + compose `diode` on `egress` only + shared volume (Task 5); isolation asserted (Task 6). ✅
- **§6.1 SSRF (http(s) only; reject private/loopback/link-local/reserved; resolve-then-check), rate limit, timeout, size cap** → `classify_url` + `check_rate_limit` + `_fetch` (Tasks 1, 3); SSRF asserted live (Task 6). ✅
- **§6.2 garden (README flat/factual, world.db, notes/, projects/ filtered snapshots incl. redacted aurora)** → `build_garden.py` (Task 4); readable + read-only asserted (Task 6). ✅
- **§6.3 curated packages in agent image** → Dockerfile (Task 5). ✅
- **§6.4 affordances as state deltas not trophies; variables enable gated commands; fetch_budget raises rate** → registry gates + `write_help`/`write_state` flat text (Tasks 2–3); `enable_clock` unlock asserted live (Task 6). ✅
- **§1.1 flat, affectless, no quest framing** → README/HELP/notes are plain statements, no congratulation/score. ✅
- **D7 (console interface), D8 (packages+garden), D11 (affordances), D12 (redacted aurora)** → covered above; redaction unit-tested (Task 4) + asserted in build (Task 4 Step 4). ✅
- **Deferred (documented, not built):** `search` affordance (needs external search API) — noted in scope; the unlock mechanism is proven by `fetchlinks`/`time`/`fetch_budget`.

> This is the final phase. After merge, the full system exists: tidied self-modifying harness, hardened internet-less sandbox with external recorder, tiered git recovery, and a brokered web diode + garden to explore.
