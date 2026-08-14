# LLM Telemetry Events Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The recorder appends per-request `open`/`close` events (with usage) and socket `bind`/`unbind` events to `/transcripts/events.jsonl`; the stage folds them into per-socket stream lanes and an evidence-based in-flight indicator on the stream page.

**Architecture:** Event writing is a small fail-open appender in `proxy.py`, hooked into `do_POST`, the refusal path, and the stream multiplexer. The stage gains one reader (`data.stream_lanes`), a `lanes` key in the public snapshot, a masthead lane chip strip, and an event-driven upgrade to the existing `#inflight` row with the old inference as fallback. No topology change: the recorder already writes the transcripts volume, the stage already mounts it read-only.

**Tech Stack:** Python stdlib; existing stage page JS.

**Spec:** `docs/superpowers/specs/2026-08-15-llm-telemetry-events-design.md`

## Global Constraints

- `agent.py`, `agent_stock.py`, `chassis.py` are **not modified** in any task.
- Event writing must never fail a request: every failure path prints to stderr and continues.
- Events carry no message content, no headers, no key: names, counts, statuses, durations, token totals only.
- Every `open` gets exactly one `close` on every path through the handler.
- Lanes: `core` first and always present; unbound lanes with no in-window activity dropped; opens older than `INFLIGHT_MAX_AGE = 600` seconds are not in flight.
- Public caps in the snapshot: lane name ≤ 32 chars, ≤ 9 lanes.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before each commit: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages factual and benign.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `proxy.py` | Modify | `log_event`, request ids, open/close/bind/unbind emission |
| `stage/data.py` | Modify | `stream_lanes` reader |
| `stage/server.py` | Modify | `lanes` in the snapshot, public capping |
| `stage/pages.py` | Modify | lane chips in the masthead, event-driven in-flight |
| `scripts/verify_container.sh` | Modify | events + lanes assertions |
| `CLAUDE.md`, `README.md` | Modify | documentation realignment |
| `tests/test_recorder_events.py` | Create | event emission unit tests |
| `tests/test_stage_data.py` | Modify | `stream_lanes` tests |
| `tests/test_stage_server.py` | Modify | snapshot `lanes` tests |
| `tests/test_stage_pages_js.py` / `tests/test_stage_pages.py` | Modify | page references the lane fields |
| `tests/test_verify_script.py` | Modify | verify-script text assertions |

---

### Task 1: Event appender in the recorder

**Files:**
- Modify: `proxy.py`
- Test: `tests/test_recorder_events.py`

**Interfaces:**
- Produces: `EVENTS_FILE` (`os.path.join(TRANSCRIPT_DIR, "events.jsonl")`),
  `EVENTS_MAX_BYTES = 16_777_216`, `log_event(event, stream, **fields)`;
  `request_id() -> str` (random hex).

- [ ] **Step 1: Write the failing tests**

```python
import json
import threading

import httpx
import pytest

import proxy


@pytest.fixture
def transcripts(tmp_path, monkeypatch):
    monkeypatch.setattr(proxy, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(proxy, "TRANSCRIPT_FILE", str(tmp_path / "transcript.jsonl"))
    monkeypatch.setattr(proxy, "PLAIN_TRANSCRIPT_FILE", str(tmp_path / "transcript.txt"))
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(tmp_path / "events.jsonl"))
    return tmp_path


def _events(transcripts):
    path = transcripts / "events.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").strip().splitlines()
        if line
    ]


def test_log_event_appends_a_timestamped_line(transcripts):
    proxy.log_event("bind", "core")
    (event,) = _events(transcripts)
    assert event["event"] == "bind"
    assert event["stream"] == "core"
    assert event["timestamp"].endswith("Z")


def test_log_event_carries_extra_fields(transcripts):
    proxy.log_event("close", "aux", id="abc", status=200, duration_seconds=1.5)
    (event,) = _events(transcripts)
    assert event["id"] == "abc"
    assert event["status"] == 200
    assert event["duration_seconds"] == 1.5


def test_log_event_failure_is_contained(transcripts, monkeypatch, capsys):
    monkeypatch.setattr(proxy, "EVENTS_FILE", str(transcripts / "no" / "events.jsonl"))
    monkeypatch.setattr(proxy.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    proxy.log_event("open", "core", id="x")
    assert "Error writing event" in capsys.readouterr().err


def test_request_ids_are_distinct_hex():
    ids = {proxy.request_id() for _ in range(64)}
    assert len(ids) == 64
    for value in ids:
        int(value, 16)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_events.py -q`
Expected: FAIL with `AttributeError` on `EVENTS_FILE` / `log_event`

- [ ] **Step 3: Implement in `proxy.py`**

Near the transcript constants:

```python
EVENTS_FILE = os.path.join(TRANSCRIPT_DIR, "events.jsonl")
EVENTS_MAX_BYTES = 16_777_216

_events_lock = threading.Lock()
```

Module functions:

```python
def request_id():
    """A random hex token pairing one request's open and close events."""
    return os.urandom(8).hex()


def log_event(event, stream, **fields):
    """Append one telemetry event; failures never affect the request.

    Events carry names, counts, statuses, durations, and token totals only,
    never message content or headers.
    """
    entry = {
        "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
        "event": event,
        "stream": stream,
    }
    entry.update(fields)
    try:
        with _events_lock:
            os.makedirs(os.path.dirname(EVENTS_FILE) or ".", exist_ok=True)
            with open(EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
            rotate_if_needed(EVENTS_FILE, EVENTS_MAX_BYTES)
    except Exception as e:
        print(f"Error writing event: {e}", file=sys.stderr)
```

Note: `log_event` reads `EVENTS_FILE` at call time via the module global so the
tests' monkeypatching works; keep the reference as `EVENTS_FILE`, not a bound default.
(If the fixture's monkeypatch of `TRANSCRIPT_DIR` must also move `EVENTS_FILE`, the
fixture sets both, as written above.)

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_recorder_events.py
git commit -m "feat: append recorder telemetry events with contained failures"
```

---

### Task 2: Open/close emission in the request path

**Files:**
- Modify: `proxy.py`
- Test: `tests/test_recorder_events.py`

**Interfaces:**
- Consumes: Task 1's `log_event`/`request_id`; spec 2's admit/refusal flow.
- Produces: every `do_POST` writes one `open` and one `close` sharing an id; usage copied from
  the upstream response body when present.

- [ ] **Step 1: Write the failing tests** (append; reuse the UDS fixtures from `tests/test_unix_listener.py` by importing the same shapes)

```python
import recorder_streams


@pytest.fixture
def fake_upstream(monkeypatch):
    body = json.dumps(
        {
            "choices": [{"message": {"content": "hi"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
        }
    ).encode("utf-8")

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


def test_a_completion_writes_a_paired_open_and_close(server, transcripts):
    _post(server, {"model": "m", "messages": [{"role": "user", "content": "q"}]})
    opens = [e for e in _events(transcripts) if e["event"] == "open"]
    closes = [e for e in _events(transcripts) if e["event"] == "close"]
    assert len(opens) == 1 and len(closes) == 1
    assert opens[0]["id"] == closes[0]["id"]
    assert opens[0]["stream"] == "core"
    assert opens[0]["model"] == "m"
    assert opens[0]["messages"] == 1
    assert closes[0]["status"] == 200
    assert closes[0]["duration_seconds"] >= 0
    assert closes[0]["usage"] == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}


def test_a_budget_refusal_closes_with_429_and_no_usage(tmp_path, transcripts, fake_upstream):
    registry = recorder_streams.StreamRegistry()
    registry.apply({"aux": {"budget": 0}}, {})
    path = str(tmp_path / "aux.sock")
    instance = proxy.UnixHTTPServer(path, proxy.ProxyHTTPRequestHandler)
    instance.stream_name = "aux"
    instance.registry = registry
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        response = _post(path, {"model": "m", "messages": []})
        assert response.status_code == 429
        closes = [e for e in _events(transcripts) if e["event"] == "close"]
        assert closes[0]["stream"] == "aux"
        assert closes[0]["status"] == 429
        assert "usage" not in closes[0]
        opens = [e for e in _events(transcripts) if e["event"] == "open"]
        assert opens[0]["id"] == closes[0]["id"]
    finally:
        instance.shutdown()
        instance.server_close()


def test_an_upstream_error_closes_with_its_status(server, transcripts, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("upstream gone")

    monkeypatch.setattr("urllib.request.urlopen", broken)
    _post(server, {"model": "m", "messages": []})
    closes = [e for e in _events(transcripts) if e["event"] == "close"]
    assert closes[0]["status"] == 500
    assert "usage" not in closes[0]


def test_poll_once_emits_bind_and_unbind(tmp_path, transcripts):
    registry = recorder_streams.StreamRegistry()
    console = tmp_path / "console.json"
    state = tmp_path / "streams.json"
    servers = {}
    console.write_text(json.dumps({"streams": {"aux": {}}}), encoding="utf-8")
    proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    try:
        console.write_text(json.dumps({"streams": {}}), encoding="utf-8")
        proxy.poll_once(registry, servers, str(tmp_path), str(console), str(state))
    finally:
        for instance in servers.values():
            instance.shutdown()
            instance.server_close()
    kinds = [(e["event"], e["stream"]) for e in _events(transcripts)]
    assert ("bind", "aux") in kinds
    assert ("unbind", "aux") in kinds
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_recorder_events.py -q`
Expected: FAIL — no events emitted from the request path

- [ ] **Step 3: Implement in `proxy.py`**

In `do_POST`, after the admit block from spec 2 and before parsing `req_data`, restructure so
both paths share one open/close pair:

```python
        stream = getattr(self.server, "stream_name", "core")
        registry = getattr(self.server, "registry", None)

        refused = None
        if registry is not None and stream != "core":
            req_body, refused = registry.admit(stream, req_body)

        try:
            req_data = json.loads(req_body.decode("utf-8"))
        except Exception:
            req_data = {"raw_body": req_body.decode("utf-8", errors="replace")}

        event_id = request_id()
        messages = req_data.get("messages")
        started = time.monotonic()
        log_event(
            "open",
            stream,
            id=event_id,
            model=req_data.get("model"),
            messages=len(messages) if isinstance(messages, list) else 0,
        )

        if refused is not None:
            status_code, message = refused
            log_event(
                "close",
                stream,
                id=event_id,
                status=status_code,
                duration_seconds=round(time.monotonic() - started, 3),
            )
            self._finish_local(stream, req_data, status_code, message)
            return
```

(`_finish_local` changes to accept the already-parsed `req_data` instead of raw bytes — adjust its
body accordingly and keep its transcript write.)

After the response is assembled (past the `res_data = json.loads(...)` fallback), before
`self.log_transcript(...)`:

```python
        close_fields = {
            "id": event_id,
            "status": response_code,
            "duration_seconds": round(time.monotonic() - started, 3),
        }
        usage = res_data.get("usage") if isinstance(res_data, dict) else None
        if isinstance(usage, dict):
            close_fields["usage"] = {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), (int, float))
            }
        log_event("close", stream, **close_fields)
```

In `poll_once`, after a successful `bind_stream` call emit `log_event("bind", name)` (only when
the bind succeeded — check `name in servers`), and in the removal branch emit
`log_event("unbind", name)`. In `main()`, after the core server starts, emit
`log_event("bind", "core")`.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS (existing `test_unix_listener.py` transcripts are unaffected by the added events file)

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add proxy.py tests/test_recorder_events.py
git commit -m "feat: emit per-request open, close, and socket lifecycle events"
```

---

### Task 3: `stream_lanes` in the stage

**Files:**
- Modify: `stage/data.py`
- Test: `tests/test_stage_data.py`

**Interfaces:**
- Produces: `stream_lanes(events_path, now=None, window=3600) -> list[dict]` with keys
  `name`, `bound`, `in_flight`, `in_flight_since`, `last_epoch`, `requests_hour`,
  `errors_hour`, `tokens_hour`; constants `EVENTS_TAIL_BYTES = 524_288`,
  `INFLIGHT_MAX_AGE = 600`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stage_data.py`)

```python
def _write_events(tmp_path, events):
    path = tmp_path / "events.jsonl"
    lines = [json.dumps(e) for e in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def _stamp(epoch):
    return (
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%S")
        + "Z"
    )


def test_stream_lanes_reports_core_alone_from_a_missing_file(tmp_path):
    lanes = data.stream_lanes(str(tmp_path / "absent.jsonl"), now=1000.0)
    assert lanes == [
        {
            "name": "core",
            "bound": False,
            "in_flight": 0,
            "in_flight_since": None,
            "last_epoch": None,
            "requests_hour": 0,
            "errors_hour": 0,
            "tokens_hour": 0,
        }
    ]


def test_an_open_without_a_close_is_in_flight(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [
            {"timestamp": _stamp(now - 5), "event": "bind", "stream": "core"},
            {"timestamp": _stamp(now - 3), "event": "open", "stream": "core", "id": "a"},
        ],
    )
    (core,) = data.stream_lanes(path, now=now)
    assert core["bound"] is True
    assert core["in_flight"] == 1
    assert core["in_flight_since"] == pytest.approx(now - 3)


def test_a_closed_request_counts_and_is_not_in_flight(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [
            {"timestamp": _stamp(now - 10), "event": "open", "stream": "core", "id": "a"},
            {
                "timestamp": _stamp(now - 8),
                "event": "close",
                "stream": "core",
                "id": "a",
                "status": 200,
                "usage": {"total_tokens": 8},
            },
        ],
    )
    (core,) = data.stream_lanes(path, now=now)
    assert core["in_flight"] == 0
    assert core["requests_hour"] == 1
    assert core["errors_hour"] == 0
    assert core["tokens_hour"] == 8


def test_an_ancient_open_ages_out_of_in_flight(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [{"timestamp": _stamp(now - 700), "event": "open", "stream": "core", "id": "a"}],
    )
    (core,) = data.stream_lanes(path, now=now)
    assert core["in_flight"] == 0


def test_hourly_counts_prune_to_the_window(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [
            {
                "timestamp": _stamp(now - 4000),
                "event": "close",
                "stream": "core",
                "id": "old",
                "status": 200,
                "usage": {"total_tokens": 100},
            },
            {
                "timestamp": _stamp(now - 100),
                "event": "close",
                "stream": "core",
                "id": "new",
                "status": 500,
            },
        ],
    )
    (core,) = data.stream_lanes(path, now=now)
    assert core["requests_hour"] == 1
    assert core["errors_hour"] == 1
    assert core["tokens_hour"] == 0


def test_lanes_order_core_first_then_names(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [
            {"timestamp": _stamp(now - 5), "event": "bind", "stream": "zeta"},
            {"timestamp": _stamp(now - 5), "event": "bind", "stream": "aux"},
        ],
    )
    lanes = data.stream_lanes(path, now=now)
    assert [lane["name"] for lane in lanes] == ["core", "aux", "zeta"]


def test_an_unbound_idle_lane_is_dropped(tmp_path):
    now = 1_000_000.0
    path = _write_events(
        tmp_path,
        [
            {"timestamp": _stamp(now - 5000), "event": "bind", "stream": "aux"},
            {
                "timestamp": _stamp(now - 4500),
                "event": "close",
                "stream": "aux",
                "id": "a",
                "status": 200,
            },
            {"timestamp": _stamp(now - 4000), "event": "unbind", "stream": "aux"},
        ],
    )
    lanes = data.stream_lanes(path, now=now)
    assert [lane["name"] for lane in lanes] == ["core"]


def test_malformed_lines_are_skipped(tmp_path):
    now = 1_000_000.0
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"timestamp": _stamp(now - 5), "event": "bind", "stream": "core"})
        + "\nnot json\n"
        + '{"timestamp": "'
        + _stamp(now - 1)
        + '", "event": "open", "stream": "core", "id"',
        encoding="utf-8",
    )
    (core,) = data.stream_lanes(str(path), now=now)
    assert core["bound"] is True
    assert core["in_flight"] == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: FAIL with `AttributeError: ... stream_lanes`

- [ ] **Step 3: Implement in `stage/data.py`**

```python
EVENTS_TAIL_BYTES = 524_288
INFLIGHT_MAX_AGE = 600


def _tail_lines(path, max_bytes):
    """The newest lines of a file, from a bounded tail read; the first partial line is dropped."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            raw = f.read(max_bytes)
    except OSError:
        return []
    lines = raw.decode("utf-8", errors="replace").splitlines()
    if size > max_bytes and lines:
        lines = lines[1:]
    return lines


def _empty_lane(name):
    return {
        "name": name,
        "bound": False,
        "in_flight": 0,
        "in_flight_since": None,
        "last_epoch": None,
        "requests_hour": 0,
        "errors_hour": 0,
        "tokens_hour": 0,
    }


def _later(current, epoch):
    if epoch is None:
        return current
    if current is None or epoch > current:
        return epoch
    return current


def stream_lanes(events_path, now=None, window=3600):
    """Per-socket activity folded from the recorder's event log, core first.

    Opens without a matching close count as in flight until INFLIGHT_MAX_AGE,
    after which they are treated as abandoned by a dead recorder. Hourly
    counts sum closes inside the window. A lane that is unbound and did
    nothing inside the window is dropped; core always appears.
    """
    if now is None:
        now = time.time()
    lanes = {"core": _empty_lane("core")}
    opens = {}
    for text in _tail_lines(events_path, EVENTS_TAIL_BYTES):
        line = text.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        name = event.get("stream")
        if not isinstance(name, str) or not name:
            continue
        kind = event.get("event")
        epoch = parse_epoch(event.get("timestamp"))
        lane = lanes.setdefault(name, _empty_lane(name))
        if kind == "bind":
            lane["bound"] = True
        elif kind == "unbind":
            lane["bound"] = False
        elif kind == "open":
            if epoch is not None:
                opens[(name, event.get("id"))] = epoch
                lane["last_epoch"] = _later(lane["last_epoch"], epoch)
        elif kind == "close":
            opens.pop((name, event.get("id")), None)
            lane["last_epoch"] = _later(lane["last_epoch"], epoch)
            if epoch is not None and now - epoch < window:
                lane["requests_hour"] += 1
                status = event.get("status")
                if isinstance(status, int) and status >= 400:
                    lane["errors_hour"] += 1
                usage = event.get("usage")
                if isinstance(usage, dict):
                    total = usage.get("total_tokens")
                    if isinstance(total, (int, float)) and not isinstance(total, bool):
                        lane["tokens_hour"] += int(total)
    for (name, _id), epoch in opens.items():
        if now - epoch > INFLIGHT_MAX_AGE:
            continue
        lane = lanes[name]
        lane["in_flight"] += 1
        since = lane["in_flight_since"]
        if since is None or epoch < since:
            lane["in_flight_since"] = epoch
    out = [lanes.pop("core")]
    for name in sorted(lanes):
        lane = lanes[name]
        if not lane["bound"] and lane["requests_hour"] == 0 and lane["in_flight"] == 0:
            continue
        out.append(lane)
    return out
```

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/data.py tests/test_stage_data.py
git commit -m "feat: fold recorder events into per-socket stream lanes"
```

---

### Task 4: Lanes in the public snapshot

**Files:**
- Modify: `stage/server.py`
- Test: `tests/test_stage_server.py`

**Interfaces:**
- Consumes: `data.stream_lanes`.
- Produces: snapshot key `lanes` (list of public lane dicts); `events_path()`;
  `LANES_CAP = 9`, `LANE_NAME_CAP = 32`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stage_server.py`)

```python
def test_the_snapshot_and_empty_snapshot_carry_lanes(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "TRANSCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(server, "TELEMETRY_DIR", str(tmp_path))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    snap = server.stream_snapshot()
    assert isinstance(snap["lanes"], list)
    assert snap["lanes"][0]["name"] == "core"
    assert server._empty_snapshot(1000.0)["lanes"] == []


def test_public_lanes_cap_names_and_count(tmp_path):
    lanes = [
        {
            "name": "x" * 100,
            "bound": True,
            "in_flight": 1,
            "in_flight_since": 5.0,
            "last_epoch": 6.0,
            "requests_hour": 2,
            "errors_hour": 1,
            "tokens_hour": 30,
        }
    ] * 20
    public = server._public_lanes(lanes)
    assert len(public) == server.LANES_CAP
    assert len(public[0]["name"]) == server.LANE_NAME_CAP
    assert public[0]["in_flight"] == 1
    assert public[0]["tokens_hour"] == 30
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: FAIL — `lanes` missing, `_public_lanes` missing

- [ ] **Step 3: Implement in `stage/server.py`**

Constants near the other caps: `LANES_CAP = 9`, `LANE_NAME_CAP = 32`.

```python
def events_path():
    """The recorder's telemetry event log path."""
    return os.path.join(TRANSCRIPT_DIR, "events.jsonl")


def _public_lane(lane):
    """A lane with every field enumerated and capped for public display."""
    since = lane.get("in_flight_since")
    last = lane.get("last_epoch")
    return {
        "name": _clip(str(lane.get("name") or ""), LANE_NAME_CAP),
        "bound": bool(lane.get("bound")),
        "in_flight": int(lane.get("in_flight") or 0),
        "in_flight_since": float(since) if isinstance(since, (int, float)) else None,
        "last_epoch": float(last) if isinstance(last, (int, float)) else None,
        "requests_hour": int(lane.get("requests_hour") or 0),
        "errors_hour": int(lane.get("errors_hour") or 0),
        "tokens_hour": int(lane.get("tokens_hour") or 0),
    }


def _public_lanes(lanes):
    return [_public_lane(lane) for lane in lanes[:LANES_CAP]]
```

In `_assemble_snapshot`'s returned dict add:

```python
        "lanes": _public_lanes(data.stream_lanes(events_path(), now=now)),
```

In `_empty_snapshot` add `"lanes": [],`.

- [ ] **Step 4: Run to verify pass**, then **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/server.py tests/test_stage_server.py
git commit -m "feat: publish stream lanes in the stage snapshot"
```

---

### Task 5: The page — lane chips and evidence-based in-flight

**Files:**
- Modify: `stage/pages.py`
- Test: `tests/test_stage_pages.py` (string checks), `tests/test_stage_pages_js.py` if a node
  harness fits naturally

- [ ] **Step 1: Write the failing tests** (append to `tests/test_stage_pages.py`)

```python
def test_stream_page_renders_lanes():
    html = pages.STREAM_PAGE_HTML
    assert 'id="lanes"' in html
    assert "renderLanes" in html
    assert "snap.lanes" in html
    assert "in_flight_since" in html
    assert "tokens_hour" in html
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py -q`
Expected: FAIL

- [ ] **Step 3: Implement in `stage/pages.py`**

HTML — in `#mh-b`, insert a lanes container between the legend chips and `#provenance`:

```html
      <span id="lanes"></span>
```

CSS — beside the `.chip` rules:

```css
#lanes { display: flex; align-items: center; gap: 18px; }
#lanes .chip b { color: var(--paper-dim); }
#lanes .chip .dot.live { background: var(--act); }
#lanes .chip .dot.idle { background: none; border: 1px solid var(--paper-faint); }
```

JS — a `coreLane` helper, a `renderLanes` function called from the snapshot render path, and the
in-flight upgrade:

```js
function coreLane() {
  var lanes = (snap && snap.lanes) || [];
  for (var i = 0; i < lanes.length; i++) if (lanes[i].name === "core") return lanes[i];
  return null;
}
function laneCount(n) {
  if (n >= 10000) return Math.round(n / 1000) + "k";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}
function renderLanes() {
  var host = $("lanes"), lanes = (snap && snap.lanes) || [];
  if (!host) return;
  while (host.children.length > lanes.length) host.removeChild(host.lastChild);
  for (var i = 0; i < lanes.length; i++) {
    var lane = lanes[i], node = host.children[i];
    if (!node) {
      node = document.createElement("span");
      node.className = "chip lane";
      node.appendChild(document.createElement("i"));
      node.appendChild(document.createElement("b"));
      node.appendChild(document.createElement("em"));
      host.appendChild(node);
    }
    node.children[0].className = "dot " + (lane.in_flight > 0 ? "live breathe" : "idle");
    setText(node.children[1], norm(lane.name).toUpperCase());
    setText(node.children[2],
      laneCount(lane.requests_hour) + "/h · " + laneCount(lane.tokens_hour) + " tok");
  }
}
```

Call `renderLanes()` from the same place the other masthead fields are set after a snapshot
arrives (the `render(snap)` path that calls the masthead updaters).

`setInflight` — evidence first, inference as fallback:

```js
function setInflight(age, state) {
  var lane = coreLane();
  var live = lane && lane.in_flight > 0 && lane.in_flight_since != null;
  if (live) age = Math.max(0, clock() / 1000 - lane.in_flight_since);
  var show = live || state === "thinking";
  if (inflight.hidden === show) { inflight.hidden = !show; repin(); }
  if (!show) return;
  var turns = snap.turns || [];
  var next = turns.length ? Number(turns[turns.length - 1].index) + 1 : 1;
  setText($("if-row"), (snap.stats.turns_this_life_exact ? "TURN " : "ROW ") + pad2(next));
  setText($("if-text"), "waiting for row " + next);
  setText($("if-clock"), dur(age));
}
```

At implementation time, match the surrounding code's exact helper names (`norm`, `setText`,
`setClass`, `repin`, `clock`, `pad2`, `dur` all exist) and call `renderLanes()` where the render
path updates the masthead. If the node harness in `tests/test_stage_pages_js.py` covers the
snapshot render path, extend its snapshot fixture with a `lanes` array so the script still parses
and runs.

- [ ] **Step 4: Run the stage test files, then the full suite**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py tests/test_stage_pages_js.py -q`
then `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/pages.py tests/test_stage_pages.py tests/test_stage_pages_js.py
git commit -m "feat: render stream lanes and an event-driven in-flight row"
```

---

### Task 6: Container verification and docs

**Files:**
- Modify: `scripts/verify_container.sh`, `CLAUDE.md`, `README.md`
- Test: `tests/test_verify_script.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_verify_script.py`)

```python
def test_verifier_checks_the_event_log_and_lanes():
    text = _script()
    for phrase in (
        "events.jsonl",
        "recorder emits open and close events",
        "stage snapshot carries stream lanes",
        "/api/stream",
    ):
        assert phrase in text
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py -q`
Expected: FAIL

- [ ] **Step 3: Edit `scripts/verify_container.sh`**

Insert after the stream-console block added by spec 2 (after the console-reset line), before
`RECOVERY_WAIT`:

```sh
echo "==> recorder emits open and close events for the recorded completions"
# The agent's own loop is running against the stub, so an open without its
# close is legitimately in flight at any instant, and the final line can be
# mid-write. Every close must pair with an open; unmatched opens are allowed.
docker compose exec -T recorder python -c "
import json
opens, closes = set(), set()
with open('/transcripts/events.jsonl') as f:
    for line in f:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event['event'] == 'open':
            opens.add(event['id'])
        elif event['event'] == 'close':
            closes.add(event['id'])
assert closes and closes <= opens, (len(opens), len(closes))
"

echo "==> stage snapshot carries stream lanes"
curl -s http://127.0.0.1:8091/api/stream | python3 -c "
import json, sys
snap = json.load(sys.stdin)
names = [lane['name'] for lane in snap['lanes']]
assert names and names[0] == 'core', names
"
```

- [ ] **Step 4: Update the docs**

- `CLAUDE.md` invariant 3, proxy bullet: append — "The recorder also appends per-request
  `open`/`close`/usage events and socket lifecycle events (no message content, no headers) to
  `events.jsonl` on the transcripts volume; the stage reads them for its stream lanes."
- `README.md`: recorder component row gains "and appends a per-request event log"; the viewing
  section's stream-page description mentions per-socket lanes with a live in-flight indicator.

- [ ] **Step 5: Run the full suite, lint, and commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add scripts/verify_container.sh tests/test_verify_script.py CLAUDE.md README.md
git commit -m "docs: record the recorder event log and verify it in containers"
```

---

### Task 7: Full verification

- [ ] **Step 1: Full unit suite** — `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` — PASS
- [ ] **Step 2: Lint clean** — `.venv/bin/ruff format --check . && .venv/bin/ruff check .` — no diffs, no findings
- [ ] **Step 3: Container verification** — `docker compose build && scripts/verify_container.sh` — ALL CONTAINER CHECKS PASSED (needs Docker; run when available)
- [ ] **Step 4: Byte-identity guard** — `cmp agent.py agent_stock.py` — identical
