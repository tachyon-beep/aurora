# LLM Socket Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the agent's model channel from TCP on the `internal` network to a Unix domain socket served by the recorder, remove the agent's network interface entirely, and bring every document and test that asserts the old topology into line.

**Architecture:** The recorder binds `/llm/sock/core.sock` and serves its existing `POST /api/v1/chat/completions` handler over AF_UNIX instead of TCP. The chassis connects through an httpx UDS transport. The agent runs with `network_mode: none` — one loopback interface, an empty routing table — and mounts the socket directory read-only, so it can connect but cannot unlink or shadow the socket. `agent.py` and `agent_stock.py` are not touched.

**Tech Stack:** Python 3.13 standard library, `httpx` (already a transitive dependency of `openai`), Docker Compose v2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-llm-socket-transport-design.md`

## Global Constraints

- `agent.py` and `agent_stock.py` must remain byte-identical and **must not be modified by this plan**. `tests/test_cleanliness.py::test_agent_and_stock_are_byte_identical` enforces this.
- Transport identity (client, provider, proxy, request loop, socket path) lives in `chassis.py`, never in `agent.py`.
- Comments in `agent.py` may only be commented-out code. Not relevant here — `agent.py` is untouched.
- Use the local virtualenv: `.venv/bin/python`, `.venv/bin/ruff`.
- Run tests with `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`.
- Lint before every commit: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign — no game or task framing.
- Tests live in `tests/` and are never shipped into any image.
- Do not add `README.md`, `CLAUDE.md`, `docs/`, or `tests/` to the Dockerfile `COPY` allow-list.
- Garden text must stay bland and factual: no authorial voice, no suggested uses, no executable examples.
- No new third-party dependencies. `httpx` is already installed as a dependency of `openai`; do not add it to `requirements-agent.txt`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `proxy.py` | Modify | Add `UnixHTTPServer`; bind a socket instead of a TCP port |
| `chassis.py` | Modify | Socket path resolution, wait-for-socket, UDS client, exit-44 on absent socket |
| `docker-compose.yml` | Modify | `network_mode: none`, socket volumes, delete the `internal` network |
| `Dockerfile` | Modify | Pre-create `/llm/sock` and `/llm/console` mountpoints owned by `appuser` |
| `scripts/build_garden.py` | Modify | The two garden sentences |
| `garden_export/runtime.md` | Regenerate | Generated output, committed |
| `scripts/verify_container.sh` | Modify | Replace the recorder-reachability check; add containment assertions |
| `CLAUDE.md` | Modify | Invariant 3, three bullets |
| `README.md` | Modify | Diagram node, diagram edge, component table, safety property |
| `.env.example` | Modify | Document `LLM_SOCKET_PATH` |
| `tests/test_unix_listener.py` | Create | The AF_UNIX server class |
| `tests/test_upstream_selection.py` | Modify | Socket-mode client selection |
| `tests/test_chassis_recovery.py` | Modify | Absent socket exits 44 |
| `tests/test_cleanliness.py` | Modify | Transport-identity needles |
| `tests/test_stage_topology.py` | Modify | Compose topology assertions |
| `tests/test_build_garden.py` | Modify | Expected garden text |
| `tests/test_verify_script.py` | Modify | New verify-script assertions |

---

### Task 1: AF_UNIX listener in the recorder

**Files:**
- Modify: `proxy.py:1-11` (imports), `proxy.py:13` (`PORT`), `proxy.py:313-317` (`ThreadedHTTPServer`), `proxy.py:319-339` (`main`)
- Test: `tests/test_unix_listener.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `proxy.UnixHTTPServer(path: str, handler)` — a `ThreadingMixIn` HTTP server bound to a filesystem path. `proxy.SOCKET_PATH: str` — the default socket path, `"/llm/sock/core.sock"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_unix_listener.py`:

```python
import json
import os
import stat
import threading

import httpx
import pytest

import proxy


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    return tmp_path


@pytest.fixture
def fake_upstream(monkeypatch):
    body = json.dumps({"choices": [{"message": {"content": "hi"}}]}).encode("utf-8")

    class _Response:
        status = 200

        def read(self):
            return body

        def getheaders(self):
            return [("Content-Type", "application/json")]

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Response())
    return body


@pytest.fixture
def server(tmp_path, transcripts, fake_upstream):
    path = str(tmp_path / "core.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield path
    instance.shutdown()
    instance.server_close()


def _post(path, payload):
    transport = httpx.HTTPTransport(uds=path)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        return client.post("/api/v1/chat/completions", json=payload, timeout=10)


def test_completion_round_trips_over_the_socket(server, transcripts):
    # log_message runs inside send_response on every request, so a regression in
    # the AF_UNIX peer address fails this test rather than passing silently.
    response = _post(server, {"model": "m", "messages": []})

    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "hi"


def test_the_exchange_is_recorded_in_the_transcript(server, transcripts):
    _post(server, {"model": "m", "messages": [{"role": "user", "content": "q"}]})

    lines = (transcripts / "transcript.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["request"]["model"] == "m"
    assert entry["response"]["choices"][0]["message"]["content"] == "hi"


def test_unknown_routes_are_refused(server, transcripts):
    transport = httpx.HTTPTransport(uds=server)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        response = client.post("/api/v1/other", json={}, timeout=10)

    assert response.status_code == 404


def test_a_stale_socket_file_is_replaced_and_permissioned(tmp_path, transcripts):
    path = tmp_path / "core.sock"
    path.write_text("left behind by an unclean exit", encoding="utf-8")

    instance = proxy.UnixHTTPServer(str(path), proxy.ProxyHTTPRequestHandler)
    try:
        mode = os.stat(path).st_mode
        assert stat.S_ISSOCK(mode)
        assert stat.S_IMODE(mode) == 0o660
    finally:
        instance.server_close()


def test_the_default_socket_path_is_under_llm_sock():
    assert proxy.SOCKET_PATH == "/llm/sock/core.sock"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_unix_listener.py -v`
Expected: FAIL with `AttributeError: module 'proxy' has no attribute 'UnixHTTPServer'`

- [ ] **Step 3: Add the `socket` import and the socket path constant**

In `proxy.py`, add `import socket` to the import block at the top (keep the existing alphabetical-ish grouping — it goes after `shutil`), and replace line 13:

```python
PORT = 8088
```

with:

```python
SOCKET_PATH = os.environ.get("LLM_SOCKET_PATH", "/llm/sock/core.sock")
```

- [ ] **Step 4: Replace the server class**

Replace `ThreadedHTTPServer` at `proxy.py:313-317`:

```python
class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP Server to handle concurrent proxy requests."""

    daemon_threads = True
```

with:

```python
class UnixHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server bound to a unix domain socket.

    HTTPServer.server_bind unpacks server_address[:2] as a host and port, which
    on a filesystem path yields two characters and then attempts to resolve the
    first as a hostname. AF_UNIX accept() also reports an empty peer address,
    which the handler's logging indexes. Both are handled here so the request
    handler itself needs no changes.
    """

    address_family = socket.AF_UNIX
    daemon_threads = True
    allow_reuse_address = False

    def server_bind(self):
        try:
            os.unlink(self.server_address)
        except FileNotFoundError:
            pass
        socketserver.TCPServer.server_bind(self)
        os.chmod(self.server_address, 0o660)
        self.server_name = "unix"
        self.server_port = 0

    def get_request(self):
        conn, _ = self.socket.accept()
        return conn, ("unix", 0)
```

- [ ] **Step 5: Rewrite `main()` to bind the socket**

Replace `proxy.py:319-339`:

```python
def main():
    if not os.environ.get("LLM_BASE_URL", "").strip() and not os.environ.get("OPENROUTER_API_KEY"):
        print("error: set OPENROUTER_API_KEY, or LLM_BASE_URL for an OpenAI-compatible upstream")
        sys.exit(1)

    socket_path = os.environ.get("LLM_SOCKET_PATH", SOCKET_PATH)
    parent = os.path.dirname(socket_path)
    if parent:
        os.makedirs(parent, exist_ok=True)

    print("=" * 60)
    print("      TRANSCRIPT PROXY SERVER")
    print("=" * 60)
    print(f"Listening on:  {socket_path}")
    print(f"Forwarding to: {upstream_url()}")
    print(f"Logging to:    {TRANSCRIPT_FILE}")
    print("-" * 60)

    server = UnixHTTPServer(socket_path, ProxyHTTPRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down proxy server...")
    finally:
        server.server_close()
        try:
            os.unlink(socket_path)
        except OSError:
            pass
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_unix_listener.py tests/test_proxy.py -v`
Expected: PASS. `tests/test_proxy.py` exercises the handler and transcript logic, which is unchanged — it must still pass.

- [ ] **Step 7: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_unix_listener.py
git commit -m "feat: serve the recorder over a unix domain socket"
```

---

### Task 2: Chassis socket transport

**Files:**
- Modify: `chassis.py:1-8` (imports), `chassis.py:310-344` (`build_client`), `chassis.py:456-460` (`main`)
- Test: `tests/test_upstream_selection.py:1-29,63-97`, `tests/test_chassis_recovery.py`, `tests/test_cleanliness.py:82`

**Interfaces:**
- Consumes: `proxy.SOCKET_PATH` conceptually — the same default path string, independently defined.
- Produces: `chassis.LLM_SOCKET_PATH: str`, `chassis.SOCKET_WAIT_SECONDS: int`, `chassis.socket_path() -> str | None`, `chassis.wait_for_socket(path, timeout=None, sleep=time.sleep) -> bool`. `build_client()` keeps its existing `(client, model)` return type.

- [ ] **Step 1: Write the failing tests**

In `tests/test_upstream_selection.py`, replace the imports and both fixtures at lines 1-29 with:

```python
import pytest

import chassis
import proxy


@pytest.fixture
def clean_env(monkeypatch):
    for var in (
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_MODEL",
        "OPENROUTER_API_KEY",
        "OPENROUTER_MODEL",
        "OPENROUTER_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    # An empty value selects the direct upstream, which is what the existing
    # provider-selection tests below are about.
    monkeypatch.setenv("LLM_SOCKET_PATH", "")
    return monkeypatch
```

Delete the `no_proxy` fixture entirely, and remove the `no_proxy` argument from the three tests that take it (`test_chassis_requires_a_key_without_llm_base_url`, `test_chassis_openrouter_mode`, `test_chassis_llm_mode_without_key`). The remaining direct-mode assertions are unchanged.

Replace `test_chassis_prefers_detected_recorder_over_direct` at lines 92-96 with:

```python
def test_unset_socket_path_selects_socket_mode(clean_env, tmp_path, monkeypatch):
    # The container case: an unset variable must not fall back to a network the
    # container does not have.
    monkeypatch.delenv("LLM_SOCKET_PATH", raising=False)
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    monkeypatch.setattr(chassis, "SOCKET_WAIT_SECONDS", 0)

    with pytest.raises(chassis.EnvironmentFailure):
        chassis.build_client()


def test_present_socket_builds_a_uds_client(clean_env, tmp_path):
    path = tmp_path / "core.sock"
    path.write_bytes(b"")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("OPENROUTER_MODEL", "some/model")
    clean_env.setenv("LLM_SOCKET_PATH", str(path))

    client, model = chassis.build_client()

    assert model == "some/model"
    assert str(client.base_url).rstrip("/") == "http://localhost/api/v1"


def test_absent_socket_raises_environment_failure(clean_env, tmp_path, monkeypatch):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-real")
    clean_env.setenv("LLM_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setattr(chassis, "SOCKET_WAIT_SECONDS", 0)

    with pytest.raises(chassis.EnvironmentFailure):
        chassis.build_client()


def test_wait_for_socket_returns_true_once_the_path_exists(tmp_path):
    path = tmp_path / "core.sock"
    calls = []

    def fake_sleep(seconds):
        calls.append(seconds)
        path.write_bytes(b"")

    assert chassis.wait_for_socket(str(path), timeout=5, sleep=fake_sleep) is True
    assert len(calls) == 1


def test_wait_for_socket_gives_up_at_the_timeout(tmp_path):
    assert chassis.wait_for_socket(str(tmp_path / "never"), timeout=0, sleep=lambda s: None) is False
```

Append to `tests/test_chassis_recovery.py`:

```python
def test_an_absent_socket_ends_the_process_with_the_environment_code(tmp_path, monkeypatch):
    # An absent socket must exit 44 so the watchdog pauses. Exiting 1 would send
    # it down the recovery tiers for what is really an environment failure.
    monkeypatch.setattr(chassis, "load_dotenv", lambda: None)
    monkeypatch.setattr(chassis, "SOCKET_WAIT_SECONDS", 0)
    monkeypatch.setenv("LLM_SOCKET_PATH", str(tmp_path / "missing.sock"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-real")
    module = types.SimpleNamespace(conversation_history=[])

    with pytest.raises(SystemExit) as exit_info:
        chassis.main(module)

    assert exit_info.value.code == chassis.EXIT_ENVIRONMENT
```

In `tests/test_cleanliness.py`, extend the needle tuple at line 82:

```python
    for needle in (
        "openrouter",
        "deepseek",
        "base_url",
        "chat.completions",
        "extra_headers",
        "llm_socket_path",
        "af_unix",
        "uds",
    ):
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_upstream_selection.py tests/test_chassis_recovery.py -v`
Expected: FAIL with `AttributeError: module 'chassis' has no attribute 'wait_for_socket'`

`tests/test_cleanliness.py` should already PASS — `agent.py` is not modified and contains none of the new needles. Confirm with `.venv/bin/python -m pytest tests/test_cleanliness.py -v`.

- [ ] **Step 3: Add the import and constants**

In `chassis.py`, add `import httpx` to the import block (before `from openai import OpenAI`), and add after `REASONING_EFFORT` at line 13:

```python
LLM_SOCKET_PATH = os.getenv("LLM_SOCKET_PATH", "/llm/sock/core.sock")
SOCKET_WAIT_SECONDS = 30
```

- [ ] **Step 4: Add the socket helpers**

Add above `build_client` in `chassis.py`:

```python
def socket_path():
    """The configured model socket path, or None when it is explicitly disabled.

    An unset variable takes the default path: the container has no network, so it
    must not silently fall back to one. Only an explicitly empty value selects the
    direct upstream, which is the path used when running outside the containers.
    """
    value = os.getenv("LLM_SOCKET_PATH", LLM_SOCKET_PATH)
    return value.strip() or None


def wait_for_socket(path, timeout=None, sleep=time.sleep):
    """True once path exists; False when timeout elapses first."""
    if timeout is None:
        timeout = SOCKET_WAIT_SECONDS
    waited = 0.0
    while True:
        if os.path.exists(path):
            return True
        if waited >= timeout:
            return False
        sleep(0.5)
        waited += 0.5
```

- [ ] **Step 5: Rewrite `build_client`**

Replace `chassis.py:326-344` — the whole block from `base_url = os.getenv("OPENROUTER_BASE_URL", ...)` through `return client, model`, which is everything after the `direct_url` assignment. The provider-selection code above it (lines 311-324) is unchanged. The replacement is:

```python
    path = socket_path()
    if path is None:
        print(f"no model socket configured; connecting directly to {direct_url}")
        return OpenAI(api_key=api_key, base_url=direct_url), model

    if not wait_for_socket(path):
        raise EnvironmentFailure(f"model socket {path} did not appear within {SOCKET_WAIT_SECONDS}s")

    print(f"connected to transcript proxy at {path}")
    client = OpenAI(
        api_key=api_key,
        base_url="http://localhost/api/v1",
        http_client=httpx.Client(transport=httpx.HTTPTransport(uds=path)),
    )
    return client, model
```

The `base_url` host is inert — the transport routes to the socket regardless — but the `/api/v1` prefix still selects the handler's route, so it stays.

- [ ] **Step 6: Bring `build_client` inside `main`'s exception handling**

Replace `chassis.py:456-458`:

```python
def main(agent_module):
    load_dotenv()
    client, model = build_client()
```

with:

```python
def main(agent_module):
    load_dotenv()
    try:
        client, model = build_client()
    except EnvironmentFailure as e:
        print(f"environment failure: {e}")
        sys.exit(EXIT_ENVIRONMENT)
```

There is no session to save at this point — the conversation history has not been built yet — so this exits directly rather than calling `save_session`.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_upstream_selection.py tests/test_chassis_recovery.py tests/test_cleanliness.py tests/test_session_persistence.py -v`
Expected: PASS

- [ ] **Step 8: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add chassis.py tests/test_upstream_selection.py tests/test_chassis_recovery.py tests/test_cleanliness.py
git commit -m "feat: reach the recorder over a unix socket from the chassis"
```

---

### Task 3: Compose topology and image mountpoints

**Files:**
- Modify: `docker-compose.yml:11-21` (recorder), `:23-50` (agent), `:133-144` (networks and volumes), `Dockerfile:22-24`
- Test: `tests/test_stage_topology.py`

**Interfaces:**
- Consumes: `chassis.LLM_SOCKET_PATH` default and `proxy.SOCKET_PATH` default — both `/llm/sock/core.sock`, which is where the compose volumes mount.
- Produces: named volumes `llm_sock` and `llm_console`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_topology.py`:

```python
def _agent_block(text):
    return text.split("\n  agent:\n")[1].split("\n  diode:\n")[0]


def _recorder_block(text):
    return text.split("\n  recorder:\n")[1].split("\n  agent:\n")[0]


def test_compose_no_longer_defines_an_internal_network():
    text = _read("docker-compose.yml")
    assert "internal: true" not in text
    assert "networks: [internal]" not in text
    assert "networks: [internal, egress]" not in text


def test_the_agent_has_no_network_interface():
    agent = _agent_block(_read("docker-compose.yml"))
    assert "network_mode: none" in agent
    assert "networks:" not in agent


def test_the_agent_resolves_its_own_hostname_without_dns():
    agent = _agent_block(_read("docker-compose.yml"))
    assert "hostname: agent" in agent
    assert '"agent:127.0.0.1"' in agent
    assert "dns:" in agent
    assert "- 127.0.0.1" in agent


def test_the_agent_mounts_the_socket_directory_read_only():
    text = _read("docker-compose.yml")
    assert "llm_sock:/llm/sock:ro" in _agent_block(text)
    assert "llm_sock:/llm/sock\n" in _recorder_block(text)


def test_the_console_volume_is_writable_by_the_agent_only():
    text = _read("docker-compose.yml")
    assert "llm_console:/llm/console\n" in _agent_block(text)
    assert "llm_console:/llm/console:ro" in _recorder_block(text)


def test_the_socket_volumes_are_declared():
    text = _read("docker-compose.yml")
    assert "  llm_sock: {}" in text
    assert "  llm_console: {}" in text


def test_the_agent_image_precreates_the_socket_mountpoints():
    text = _read("Dockerfile")
    assert "/llm/sock" in text
    assert "/llm/console" in text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_topology.py -v`
Expected: FAIL — `assert 'internal: true' not in text` and the socket-mount assertions.

- [ ] **Step 3: Update the recorder service**

In `docker-compose.yml`, change the recorder's networks (line 15) and volumes (lines 13-14):

```yaml
    volumes:
      - transcripts:/transcripts
      - llm_sock:/llm/sock
      - llm_console:/llm/console:ro
    networks: [egress]
```

- [ ] **Step 4: Update the agent service**

Replace the agent's environment `OPENROUTER_BASE_URL` line, its volumes, and its `networks` key:

```yaml
    environment:
      OPENROUTER_API_KEY: "sk-dummy"
      OPENROUTER_MODEL: ${OPENROUTER_MODEL:-deepseek/deepseek-v4-pro}
      LLM_MODEL: ${LLM_MODEL:-}
      CONTEXT_WINDOW_TOKENS: ${CONTEXT_WINDOW_TOKENS:-120000}
      REASONING_EFFORT: ${REASONING_EFFORT:-}
    volumes:
      - llm_sock:/llm/sock:ro
      - llm_console:/llm/console
      - diode:/diode
      - state:/state
      - telemetry:/telemetry
    # No network interface at all: one loopback device and an empty routing
    # table. hostname/extra_hosts/dns keep the loopback development surface
    # usable - without them the container's own name does not resolve and
    # /etc/resolv.conf inherits the host's nameserver.
    network_mode: none
    hostname: agent
    extra_hosts:
      - "agent:127.0.0.1"
    dns:
      - 127.0.0.1
```

`OPENROUTER_BASE_URL` is deleted. `networks: [internal]` is deleted, not emptied — Compose rejects `networks:` alongside `network_mode:`.

**Everything from `read_only: true` onward in the agent service is unchanged** and must be preserved: `read_only`, `tmpfs` (including the `/work:size=4g,uid=1000,gid=1000` entry and its comment), `cap_drop`, `security_opt`, `pids_limit`, `mem_limit`, `cpus`, `restart`. Only the environment line, the volumes list, and the networks key change.

- [ ] **Step 5: Delete the internal network and declare the volumes**

At the bottom of `docker-compose.yml`:

```yaml
networks:
  egress: {}
  stream: {}

volumes:
  transcripts: {}
  diode: {}
  state: {}
  telemetry: {}
  llm_sock: {}
  llm_console: {}
```

- [ ] **Step 6: Pre-create the mountpoints in the image**

In `Dockerfile`, replace the `mkdir -p` line and its `chown`:

```dockerfile
RUN mkdir -p /diode /transcripts /state /telemetry /llm/sock /llm/console \
    && chown appuser:appuser /diode /transcripts /state /telemetry /llm /llm/sock /llm/console
```

Docker copies image-mountpoint ownership into newly created empty volumes. Without this the recorder receives a root-owned directory and cannot bind.

- [ ] **Step 7: Run the tests and validate the compose file**

Run: `.venv/bin/python -m pytest tests/test_stage_topology.py -v`
Expected: PASS

Run: `docker compose config >/dev/null && echo "compose ok"`
Expected: `compose ok` — this catches the `networks`/`network_mode` conflict, which is a parse error rather than a test failure.

- [ ] **Step 8: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add docker-compose.yml Dockerfile tests/test_stage_topology.py
git commit -m "feat: remove the agent's network and mount the socket volumes"
```

---

### Task 4: Garden text

**Files:**
- Modify: `scripts/build_garden.py:45,47`
- Regenerate: `garden_export/runtime.md`
- Test: `tests/test_build_garden.py:44-62` (`EXPECTED_RUNTIME`), `:126-141` (inventory assertions)

**Interfaces:**
- Consumes: nothing.
- Produces: the garden text the agent reads. No code interface.

- [ ] **Step 1: Write the failing test**

In `tests/test_build_garden.py`, update the two sentences inside `EXPECTED_RUNTIME`:

```python
the container has no network interface. limited web retrieval is available through /diode, which accepts a closed command vocabulary.

the model endpoint used by this environment is a unix domain socket. it accepts connections from any process in the container.
```

Then in `test_runtime_lists_requirements_and_environment_inventory`, replace these three assertions:

```python
    assert "no direct internet route" in runtime
    assert "model endpoint used by this environment accepts calls from any process" in runtime
    assert "environment variables prefixed openrouter_" in runtime
```

with:

```python
    assert "the container has no network interface" in runtime
    assert "model endpoint used by this environment is a unix domain socket" in runtime
    assert "accepts connections from any process in the container" in runtime
    assert "openrouter_" not in runtime
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_build_garden.py -v`
Expected: FAIL on `test_build_emits_exact_approved_documents` and `test_runtime_lists_requirements_and_environment_inventory`.

- [ ] **Step 3: Edit the garden source**

In `scripts/build_garden.py`, replace line 45:

```
there is no direct internet route. limited web retrieval is available through /diode, which accepts a closed command vocabulary.
```

with:

```
the container has no network interface. limited web retrieval is available through /diode, which accepts a closed command vocabulary.
```

and replace line 47:

```
the model endpoint used by this environment accepts calls from any process in the container. the openai package and the environment variables prefixed OPENROUTER_ are sufficient to reach it.
```

with:

```
the model endpoint used by this environment is a unix domain socket. it accepts connections from any process in the container.
```

The replaced sentence became false: the `OPENROUTER_` variables alone no longer reach anything. The replacement keeps the statement that concurrent processes may use the model and drops the path, which the agent can obtain from one `list_dir("/")` call.

- [ ] **Step 4: Regenerate the garden**

Run: `.venv/bin/python scripts/build_garden.py`

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_build_garden.py -v`
Expected: PASS, including `test_documents_exclude_banned_anchors` — confirm neither new sentence contains a banned anchor (`server`, `peer`, `explore`, and the rest of the list at the top of the file).

- [ ] **Step 6: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add scripts/build_garden.py garden_export/runtime.md tests/test_build_garden.py
git commit -m "docs: state the model endpoint as a socket in the garden runtime"
```

---

### Task 5: Container verification

**Files:**
- Modify: `scripts/verify_container.sh:19-23` (cleanup), `:78-79` (recorder reachability)
- Test: `tests/test_verify_script.py`

**Interfaces:**
- Consumes: the compose topology from Task 3 and the socket from Tasks 1-2.
- Produces: executable containment assertions.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_verify_script.py`:

```python
def test_verifier_asserts_the_agent_has_no_network_interface():
    text = _script()
    for phrase in (
        "agent has exactly one network interface",
        "agent has an empty routing table",
        "/sys/class/net",
        "/proc/net/route",
    ):
        assert phrase in text


def test_verifier_checks_the_socket_rather_than_a_recorder_port():
    text = _script()
    assert "create_connection(('recorder',8088))" not in text
    assert "agent CAN reach the recorder" not in text
    assert "/llm/sock/core.sock" in text
    assert "socket is not unlinkable from the agent" in text


def test_verifier_cleans_up_the_socket_volumes():
    text = _script()
    assert '"${COMPOSE_PROJECT_NAME}_llm_sock"' in text
    assert '"${COMPOSE_PROJECT_NAME}_llm_console"' in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py -v`
Expected: FAIL — the script still contains the port check.

- [ ] **Step 3: Extend the cleanup volume list**

In `scripts/verify_container.sh`, replace the `docker volume rm` block at lines 19-23:

```sh
  docker volume rm \
    "${COMPOSE_PROJECT_NAME}_state" \
    "${COMPOSE_PROJECT_NAME}_diode" \
    "${COMPOSE_PROJECT_NAME}_transcripts" \
    "${COMPOSE_PROJECT_NAME}_telemetry" \
    "${COMPOSE_PROJECT_NAME}_llm_sock" \
    "${COMPOSE_PROJECT_NAME}_llm_console" >/dev/null 2>&1 || true
```

- [ ] **Step 4: Replace the recorder-reachability assertion**

Replace lines 78-79:

```sh
echo "==> agent CAN reach the recorder"
docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('recorder',8088))"
```

with:

```sh
echo "==> agent has exactly one network interface (lo)"
docker compose exec -T agent sh -c 'test "$(ls /sys/class/net)" = "lo"'

echo "==> agent has an empty routing table"
if docker compose exec -T agent sh -c 'test "$(tail -n +2 /proc/net/route | wc -l)" -gt 0'; then
  echo "FAIL: agent has a route off the container"; exit 1
fi

echo "==> agent CAN reach the recorder over the socket"
docker compose exec -T agent python -c "import socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect('/llm/sock/core.sock'); s.close()"

echo "==> socket is not unlinkable from the agent (read-only mount)"
if docker compose exec -T agent python -c "import os; os.unlink('/llm/sock/core.sock')" 2>/dev/null; then
  echo "FAIL: agent unlinked the model socket"; exit 1
fi

echo "==> a completion round-trips and is recorded"
docker compose exec -T agent python -c "
import httpx, json
t = httpx.HTTPTransport(uds='/llm/sock/core.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'m','messages':[{'role':'user','content':'ping'}]}, timeout=20)
print(r.status_code)
"
docker compose exec -T recorder sh -c 'grep -q ping /transcripts/agent_life_transcript.jsonl'
```

The completion assertion does not require a working upstream: the verify script points `LLM_BASE_URL` at `http://127.0.0.1:1`, so the recorder's forward fails and it returns a 500 with an error body — but it still records the exchange, which is what this checks. Do not assert on the status code.

- [ ] **Step 5: Run the unit test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py -v`
Expected: PASS

- [ ] **Step 6: Run the verification against a real stack**

Run: `sh scripts/verify_container.sh`
Expected: `ALL CONTAINER CHECKS PASSED`

This is the first end-to-end exercise of Tasks 1-3 together. If the recorder cannot bind, check that the Dockerfile mountpoint `chown` from Task 3 Step 6 landed and that the image was rebuilt.

- [ ] **Step 7: Commit**

```bash
git add scripts/verify_container.sh tests/test_verify_script.py
git commit -m "test: assert the agent has no network route and reaches the socket"
```

---

### Task 6: Documentation realignment

**Files:**
- Modify: `CLAUDE.md:48`, `:50-53`, `:59-63`; `README.md:40`, `:55`, `:82`, `:108`; `docker-compose.yml:58-60`; `.env.example`

**Interfaces:**
- Consumes: everything above. This task asserts in prose what Task 5 asserts executably.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Rewrite CLAUDE.md invariant 3, first bullet**

Replace line 48-49:

```markdown
   - The **agent** has no network interface at all (`network_mode: none`): one loopback device and
     an empty routing table. It reaches the model only through the recorder, over a unix domain
     socket whose directory it mounts read-only, so it can connect but cannot unlink or shadow it.
```

- [ ] **Step 2: Rewrite the credential bullet**

Replace lines 50-53:

```markdown
   - **No real credential is ever reachable by the agent.** The upstream API key lives only in the
     recorder, which injects it; the agent runs with a dummy key. This is the invariant — not the
     number of keys in the system. Any other credential must live on a service the agent has no
     channel to, and must never be mounted, copied, or named into the agent image. The agent's only
     channel to a credentialed service is the recorder socket, which exposes exactly one route
     (`POST /api/v1/chat/completions`) and forwards its body upstream verbatim. That is one route,
     not a closed command vocabulary in the diode's sense; the protection is the key injection and
     the header-free logging below.
```

- [ ] **Step 3: Rewrite the stage bullet**

Replace the parenthetical justification at lines 61-62 — *"the stage sits on the `stream` network and the agent on `internal`, with nothing shared"* — with:

```markdown
     unreachable by the agent — the agent has no network interface and shares no volume with the
     stage — and its absence disables generation rather than degrading any other function.
```

- [ ] **Step 4: Update the compose comment on the speech credential**

In `docker-compose.yml`, replace the comment at lines 58-60:

```yaml
      # Speech credential. Lives only here: the agent has no network interface,
      # so it can cause spend through the shared /diode volume's closed command
      # vocabulary but can never read the key. Empty disables the speak command.
```

- [ ] **Step 5: Update the README diagram**

At `README.md:40`, change the agent node label:

```
    agent["agent<br/>rewrites its own code<br/>no network interface"]
```

At `README.md:55`, change the edge:

```
    agent -- "chat completions · unix socket" --> recorder
```

- [ ] **Step 6: Update the README component table and safety property**

At `README.md:82`, the agent row's containment cell:

```
No network interface at all — one loopback device, an empty routing table. Read-only image; work happens in a tmpfs. Reaches the model only through the recorder, over a unix socket it mounts read-only.
```

At `README.md:108`, the first safety property:

```markdown
- **The agent has no network egress at all.** It runs with `network_mode: none` — one loopback
  interface and an empty routing table — and reaches the model only through a unix domain socket
  served by the recorder.
```

- [ ] **Step 7: Document the socket path in .env.example**

Append after the `CONTEXT_WINDOW_TOKENS` block:

```
# Path to the unix socket the recorder serves and the agent connects to. Unset
# uses the in-container default. Set it to an empty string to bypass the
# recorder and call the upstream directly, which is only useful outside Docker.
#LLM_SOCKET_PATH=/llm/sock/core.sock
```

- [ ] **Step 8: Verify no stale references remain**

Run:

```bash
grep -rn "internal\b\|8088" --include=*.py --include=*.md --include=*.yml --include=*.sh --include=*.example . \
  | grep -v "^./.venv\|^./docs/superpowers\|host.docker.internal"
```

Expected: no matches. `host.docker.internal` on the recorder is a different thing and stays.

- [ ] **Step 9: Run the full suite, lint, and the container check**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
sh scripts/verify_container.sh
```

Expected: all tests pass, ruff clean, `ALL CONTAINER CHECKS PASSED`.

- [ ] **Step 10: Commit**

```bash
git add CLAUDE.md README.md docker-compose.yml .env.example
git commit -m "docs: state the agent's containment as absence of a network interface"
```

---

## Verification

After Task 6, the following are all true and checked:

| Claim | Checked by |
| --- | --- |
| The agent has one interface and no routes | `scripts/verify_container.sh` |
| The agent reaches the model over the socket | `scripts/verify_container.sh` |
| The agent cannot unlink the socket | `scripts/verify_container.sh` |
| The recorder serves AF_UNIX correctly | `tests/test_unix_listener.py` |
| An absent socket exits 44, not 1 | `tests/test_chassis_recovery.py` |
| An unset socket path selects socket mode | `tests/test_upstream_selection.py` |
| Compose has no `internal` network | `tests/test_stage_topology.py` |
| Transport identity stays out of `agent.py` | `tests/test_cleanliness.py` |
| `agent.py` and `agent_stock.py` unchanged | `tests/test_cleanliness.py` |
| The garden text is factual | `tests/test_build_garden.py` |
