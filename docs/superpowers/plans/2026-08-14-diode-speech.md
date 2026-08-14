# Diode Speech Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One gated diode command, `speak <text>`, that renders text to audio through a third-party speech API and leaves it in the diode volume, plus the stage-side route, playback, caption, and commentary beat that surface it on the stream page.

**Architecture:** A credentialed request path in `diode.py` that is deliberately separate from the agent-URL-driven `_fetch` — no URL parameter, constant host, redirects refused, bytes not text. The command charges the existing shared `fetch_budget` pool, so speaking competes with fetching. Artifacts land in `/diode/spoken` as timestamp-named `.mp3` + `.txt` pairs; the stage reads them read-only, serves the audio on a new GET route on the stream port, captions the text, and fires a new commentary beat.

**Tech Stack:** Python standard library only (`urllib.request`, `json`, `re`, `datetime`). Tests with pytest following the existing direct-call + monkeypatch patterns in `tests/test_diode.py` and `tests/test_stage_*.py`. Vanilla ES5-style JS in `stage/pages.py`, matching the file's existing idiom.

**Spec:** `docs/superpowers/specs/2026-08-14-diode-speech-design.md`

## Global Constraints

- **Standard library only.** No new package in `requirements-agent.txt`, `Dockerfile.diode`, or anywhere else. The vendor's JavaScript SDK is not used; this is one HTTP POST.
- **Nothing enters the agent's world except one help line.** Do not modify `agent.py`, `agent_stock.py`, `chassis.py`, `Dockerfile`, `system_prompt.txt`, `user_prompt.txt`, the garden, or `scripts/build_garden.py`.
- **Agent-readable text is bland and factual.** The `speak` help line, the `enable_speech` gate line, and every result string must not contain "aloud", "voice", "audience", "stream", "listener", "hear", or any other word implying an audience or a performance. Stage-side prose (commentary templates, panel titles) is audience-facing and is NOT subject to this rule.
- **Exact help line:** `speak <text> -> make text available outside the container as audio`
- **Exact gate variable name:** `enable_speech`. The budget variable stays `fetch_budget` — do not rename it.
- **The credential lives only in the diode.** `ELEVENLABS_API_KEY` goes on the `diode` service in `docker-compose.yml` and in `.env.example`. It is never added to the `agent` service, the agent image, or any agent-readable file.
- **No exception may escape `handle_command`** for malformed arguments or transport failure — return a factual reason string instead.
- **`classify_url` is still called** on the constructed speech URL. The diode's convention is "fixed-host URLs are still classified — no exemptions."
- **Redirects on the speech request are refused, not revalidated** — `urllib` resends headers across redirects and this request carries a credential.
- Run tests: `.venv/bin/python -m pytest tests/test_diode.py -v`, then `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, with no game or task framing.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `diode.py` | modify | speech config helpers, `_speak_request`, `write_spoken`/`prune_spoken`, the `speak` command entry + dispatch, budget wording |
| `tests/test_diode.py` | modify | gate, budget sharing, cap, transport, artifact, retention tests |
| `docker-compose.yml` | modify | speech env on the `diode` service only |
| `.env.example` | modify | documents the three speech settings |
| `scripts/verify_container.sh` | modify | asserts no `ELEVENLABS_*` in the agent environment |
| `tests/test_verify_script.py` | modify | asserts that check exists |
| `stage/data.py` | modify | `diode_spoken` reader |
| `stage/server.py` | modify | snapshot wiring, `/audio/<name>` route, `media-src 'self'` |
| `stage/pages.py` | modify | caption element, `<audio>` element, `renderSpoken` |
| `stage/commentary.py` | modify | `spoke` beat kind, template, detection |
| `tests/test_stage_data.py`, `tests/test_stage_server.py`, `tests/test_stage_containment.py`, `tests/test_stage_commentary.py` | modify | stage-side tests |

---

### Task 1: Speech configuration and the credentialed request path

**Files:**
- Modify: `diode.py` (imports at line 1-9; constants near line 20-30; new functions after `_make_opener` / `_fetch`, around line 405)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: `classify_url`, `FETCH_TIMEOUT` (existing).
- Produces: `SPEECH_TEXT_CAP = 300`, `MAX_AUDIO_BYTES = 2_000_000`, `speech_key() -> str`, `speech_voice() -> str`, `speech_model() -> str`, `_NoRedirectHandler`, `_speak_request(text) -> (bool, bytes | str)`. Task 3 dispatches onto `_speak_request` and gates on `speech_key`/`speech_voice`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_diode.py`:

```python
def test_speech_helpers_read_the_environment(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "  k  ")
    monkeypatch.delenv("ELEVENLABS_VOICE_ID", raising=False)
    monkeypatch.delenv("ELEVENLABS_MODEL", raising=False)
    assert diode.speech_key() == "k"
    assert diode.speech_voice() == diode.SPEECH_VOICE_DEFAULT
    assert diode.speech_model() == diode.SPEECH_MODEL_DEFAULT


def test_speech_voice_rejects_a_malformed_id(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "../../etc/passwd")
    assert diode.speech_voice() == ""


def test_speak_request_without_a_key_is_refused(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert reason == "speech not configured"


def test_speak_request_takes_no_url_argument():
    import inspect

    assert list(inspect.signature(diode._speak_request).parameters) == ["text"]


def test_speak_request_refuses_a_redirect():
    handler = diode._NoRedirectHandler()
    assert handler.redirect_request(None, None, 302, "Found", {}, "https://evil.example/") is None


def test_speak_request_returns_audio_bytes(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    seen = {}

    class _Resp:
        def read(self, n):
            seen["cap"] = n
            return b"ID3audio"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class _Opener:
        def open(self, req, timeout=None):
            seen["url"] = req.full_url
            seen["key"] = req.get_header("Xi-api-key")
            seen["body"] = json.loads(req.data.decode("utf-8"))
            return _Resp()

    monkeypatch.setattr(diode.urllib.request, "build_opener", lambda *a: _Opener())
    ok, audio = diode._speak_request("hello")
    assert ok is True
    assert audio == b"ID3audio"
    assert seen["cap"] == diode.MAX_AUDIO_BYTES
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/text-to-speech/")
    assert seen["key"] == "k"
    assert seen["body"]["text"] == "hello"


def test_speak_request_contains_transport_errors(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")

    class _Opener:
        def open(self, req, timeout=None):
            raise OSError("boom")

    monkeypatch.setattr(diode.urllib.request, "build_opener", lambda *a: _Opener())
    ok, reason = diode._speak_request("hello")
    assert ok is False
    assert "speech error" in reason
```

`tests/test_diode.py` already imports `json` and `diode`; confirm both at the top of the file and add `import json` only if absent.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -k speech_or_speak -v` (or `-k "speech or speak"`)
Expected: FAIL with `AttributeError: module 'diode' has no attribute 'speech_key'`.

- [ ] **Step 3: Implement**

Add `import re` to the imports in `diode.py` (alphabetically, after `os`).

Add near the other constants (after `PUBLISH_TEXT_CAP`, around line 30):

```python
SPEECH_URL_TEMPLATE = (
    "https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
)
SPEECH_VOICE_DEFAULT = "JBFqnCBsd6RMkjVDRZzb"
SPEECH_MODEL_DEFAULT = "eleven_multilingual_v2"
SPEECH_TEXT_CAP = 300
MAX_AUDIO_BYTES = 2_000_000
VOICE_ID_PATTERN = re.compile(r"\A[A-Za-z0-9_-]{1,64}\Z")
```

Add after `_fetch` (around line 405):

```python
def speech_key():
    """The configured speech API key, or an empty string when unset."""
    return os.environ.get("ELEVENLABS_API_KEY", "").strip()


def speech_voice():
    """The configured voice id, or an empty string when it is malformed."""
    voice = os.environ.get("ELEVENLABS_VOICE_ID", "").strip() or SPEECH_VOICE_DEFAULT
    return voice if VOICE_ID_PATTERN.match(voice) else ""


def speech_model():
    """The configured speech model name."""
    return os.environ.get("ELEVENLABS_MODEL", "").strip() or SPEECH_MODEL_DEFAULT


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _speak_request(text):
    """Render text to audio bytes. Returns (ok, audio_bytes_or_reason).

    Takes no URL: the host and path are constants and the voice id comes from the
    environment, so no agent input reaches the request target. Redirects are
    refused rather than revalidated because a redirect would resend the key.
    """
    key = speech_key()
    voice = speech_voice()
    if not key or not voice:
        return False, "speech not configured"
    url = SPEECH_URL_TEMPLATE.format(voice=voice)
    ok, reason = classify_url(url)
    if not ok:
        return False, f"refused: {reason}"
    payload = json.dumps({"text": text, "model_id": speech_model()}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "User-Agent": "aurora-diode/1",
        },
        method="POST",
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(request, timeout=FETCH_TIMEOUT) as resp:
            audio = resp.read(MAX_AUDIO_BYTES)
    except Exception as e:
        return False, f"speech error: {e}"
    if not audio:
        return False, "speech error: empty response"
    return True, audio
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: PASS, including every pre-existing test.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: add the diode's credentialed speech request path"
```

---

### Task 2: Utterance artifacts and retention

**Files:**
- Modify: `diode.py` (constants near line 16; new functions after `write_published`, around line 256; `write_state` at line 213-227)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `SPOKEN_DIR`, `SPOKEN_KEEP = 20`, `write_spoken(text, audio) -> str` (the mp3 path), `prune_spoken(keep=SPOKEN_KEEP) -> None`, and a `spoken_count` key in `write_state`'s output. Task 3 calls `write_spoken`.

- [ ] **Step 1: Write the failing tests**

```python
def test_write_spoken_names_files_from_the_timestamp_only(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    path = diode.write_spoken("../../etc/passwd", b"ID3audio")
    name = os.path.basename(path)
    assert name.endswith(".mp3")
    assert "passwd" not in name and "/" not in name and ".." not in name
    stem = name[: -len(".mp3")]
    assert (tmp_path / "spoken" / (stem + ".txt")).read_text(encoding="utf-8") == "../../etc/passwd"
    assert (tmp_path / "spoken" / name).read_bytes() == b"ID3audio"


def test_prune_spoken_keeps_only_the_newest(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(spoken))
    for i in range(5):
        stem = f"2026081{i}_000000_000000"
        (spoken / (stem + ".mp3")).write_bytes(b"a")
        (spoken / (stem + ".txt")).write_text("t", encoding="utf-8")
    diode.prune_spoken(keep=2)
    remaining = sorted(p.name for p in spoken.iterdir())
    assert remaining == [
        "20260813_000000_000000.mp3",
        "20260813_000000_000000.txt",
        "20260814_000000_000000.mp3",
        "20260814_000000_000000.txt",
    ]


def test_state_reports_the_spoken_count(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_000000_000000.mp3").write_bytes(b"a")
    (spoken / "20260814_000000_000000.txt").write_text("t", encoding="utf-8")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(spoken))
    monkeypatch.setattr(diode, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(diode, "OUTPUT_DIR", str(tmp_path / "output"))
    diode.write_state({}, [])
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["spoken_count"] == 1
```

`tests/test_diode.py` already imports `os` and `json`; confirm before adding.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -k "spoken" -v`
Expected: FAIL with `AttributeError: module 'diode' has no attribute 'write_spoken'`.

- [ ] **Step 3: Implement**

Add beside `PUBLISHED_DIR` (line 16):

```python
SPOKEN_DIR = os.path.join(DIODE_DIR, "spoken")
```

Add beside the other caps (near line 30):

```python
SPOKEN_KEEP = 20
```

Add after `write_published` (around line 256):

```python
def prune_spoken(keep=SPOKEN_KEEP):
    """Delete all but the newest keep utterances from SPOKEN_DIR."""
    try:
        stamps = sorted({os.path.splitext(n)[0] for n in os.listdir(SPOKEN_DIR)}, reverse=True)
    except OSError:
        return
    for stamp in stamps[keep:]:
        for extension in (".mp3", ".txt"):
            try:
                os.remove(os.path.join(SPOKEN_DIR, stamp + extension))
            except OSError:
                pass


def write_spoken(text, audio):
    """Write an utterance's text and audio to SPOKEN_DIR, return the audio path.

    Names carry only a timestamp, so no part of the argument reaches the filesystem.
    """
    os.makedirs(SPOKEN_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    with open(os.path.join(SPOKEN_DIR, f"{stamp}.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    path = os.path.join(SPOKEN_DIR, f"{stamp}.mp3")
    with open(path, "wb") as f:
        f.write(audio)
    prune_spoken()
    return path
```

In `write_state` (line 213), after the `output_count` block, add:

```python
    try:
        spoken_count = len([n for n in os.listdir(SPOKEN_DIR) if n.endswith(".mp3")])
    except OSError:
        spoken_count = 0
```

and add `"spoken_count": spoken_count,` to the `state` dict after `"output_count": output_count,`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: record diode utterances with timestamp names and a retention cap"
```

---

### Task 3: The `speak` command

**Files:**
- Modify: `diode.py` (`COMMANDS` at line 133-142; `write_help` at line 190-210; `handle_command` at line 443-527)
- Test: `tests/test_diode.py`

**Interfaces:**
- Consumes: `speech_key`, `speech_voice`, `_speak_request`, `SPEECH_TEXT_CAP` (Task 1); `write_spoken` (Task 2); `check_rate_limit`, `DEFAULT_FETCH_LIMIT`, `FETCH_WINDOW` (existing).
- Produces: the `speak` entry in `COMMANDS`; the reworded rate-limit string; the conditional `enable_speech` help line. Nothing later depends on these.

- [ ] **Step 1: Write the failing tests**

```python
def test_speak_is_unavailable_without_the_gate_or_the_key(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert "speak" not in diode.available_commands({})
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    assert "speak" not in diode.available_commands({"enable_speech": True})


def test_speak_is_available_with_both(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert "speak" in diode.available_commands({"enable_speech": True})


def test_speak_stays_a_listed_command(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    assert diode.undocumented_command_count() == 1


def test_speak_help_line_names_no_audience(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({"enable_speech": True})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "speak <text> -> make text available outside the container as audio" in text
    assert "enable_speech: true, makes the speak command available" in text
    for word in ("aloud", "voice", "audience", "stream", "listener", "hear"):
        assert word not in text.lower()


def test_speech_gate_line_is_absent_without_a_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "")
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    assert "enable_speech" not in (tmp_path / "HELP.md").read_text(encoding="utf-8")


def test_help_describes_the_budget_as_outbound_operations(tmp_path, monkeypatch):
    monkeypatch.setattr(diode, "HELP_FILE", str(tmp_path / "HELP.md"))
    diode.write_help({})
    text = (tmp_path / "HELP.md").read_text(encoding="utf-8")
    assert "fetch_budget: integer, number of outbound operations allowed per hour" in text


def test_speak_writes_an_utterance_and_charges_the_shared_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    text, history = diode.handle_command(
        "speak hello there", {"enable_speech": True, "fetch_budget": 2}, []
    )
    assert text.startswith("recorded as ")
    assert len(history) == 1


def test_speak_and_fetch_share_one_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 1}
    first, history = diode.handle_command("speak hello", variables, [])
    assert first.startswith("recorded as ")
    second, history = diode.handle_command("fetchhttp http://example.com", variables, history)
    assert second.startswith("rate limited")
    assert "outbound operation" in second


def test_a_fetch_spends_the_budget_speak_would_have_used(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (True, b"ID3audio"))
    variables = {"enable_speech": True, "fetch_budget": 1}
    text, _ = diode.handle_command("speak hello", variables, [_t.time()])
    assert text.startswith("rate limited")


def test_speak_truncates_at_the_text_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    seen = {}

    def _fake(text):
        seen["text"] = text
        return True, b"ID3audio"

    monkeypatch.setattr(diode, "_speak_request", _fake)
    diode.handle_command(
        "speak " + "x" * 5000, {"enable_speech": True, "fetch_budget": 2}, []
    )
    assert len(seen["text"]) == diode.SPEECH_TEXT_CAP


def test_speak_without_text_returns_usage(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    text, history = diode.handle_command("speak", {"enable_speech": True}, [])
    assert text == "usage: speak <text>"
    assert history == []


def test_speak_returns_the_transport_reason_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setattr(diode, "SPOKEN_DIR", str(tmp_path / "spoken"))
    monkeypatch.setattr(diode, "_speak_request", lambda text: (False, "speech error: boom"))
    text, history = diode.handle_command(
        "speak hello", {"enable_speech": True, "fetch_budget": 2}, []
    )
    assert text == "speech error: boom"
    assert len(history) == 1
```

`tests/test_diode.py` already imports `time as _t` (used at line 156); confirm the alias before using it.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_diode.py -k speak -v`
Expected: FAIL — `speak` is not in `COMMANDS`, so `handle_command` returns `unknown command: speak`.

- [ ] **Step 3: Implement**

Add a named gate function beside `_gate_always` (line 90) — a lambda would exceed the
100-character line limit:

```python
def _speech_gate(variables):
    """Gate function for speak: the operator variable and a configured key."""
    return bool(variables.get("enable_speech")) and bool(speech_key()) and bool(speech_voice())
```

`_speech_gate` is defined above `COMMANDS` but calls `speech_key`/`speech_voice`, which Task 1
defined further down the module. That is fine — the calls resolve at gate-invocation time, not at
definition time.

In `COMMANDS`, after the `publish` entry and before `blind`:

```python
    "speak": {
        "gate": _speech_gate,
        "help": "speak <text> -> make text available outside the container as audio",
    },
```

In `write_help`, change the `fetch_budget` line to:

```python
    lines.append("  fetch_budget: integer, number of outbound operations allowed per hour")
```

and after the `enable_publishing` line add:

```python
    if speech_key() and speech_voice():
        lines.append("  enable_speech: true, makes the speak command available")
```

In `handle_command`, after the `publish` block (line 447) add:

```python
    if name == "speak":
        if not arg:
            return "usage: speak <text>", fetch_history
        try:
            limit = int(variables.get("fetch_budget", DEFAULT_FETCH_LIMIT))
        except (TypeError, ValueError):
            limit = DEFAULT_FETCH_LIMIT
        allowed, fetch_history = check_rate_limit(fetch_history, time.time(), limit, FETCH_WINDOW)
        if not allowed:
            return f"rate limited: at most {limit} outbound operation(s) per hour", fetch_history
        text = arg[:SPEECH_TEXT_CAP]
        ok, audio = _speak_request(text)
        if not ok:
            return audio, fetch_history
        path = write_spoken(text, audio)
        return f"recorded as {os.path.basename(path)}", fetch_history
```

Change **both** existing rate-limit returns (lines 487 and 521) from
`f"rate limited: at most {limit} fetch(es) per hour"` to
`f"rate limited: at most {limit} outbound operation(s) per hour"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_diode.py -v` then `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS. The pre-existing rate-limit tests assert only the `"rate limited"` prefix, so the reworded tail does not break them.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add diode.py tests/test_diode.py
git commit -m "feat: add the gated speak command on the shared outbound budget"
```

---

### Task 4: Wiring and the containment check

**Files:**
- Modify: `docker-compose.yml` (the `diode` service, lines 52-69)
- Modify: `.env.example`
- Modify: `scripts/verify_container.sh`
- Test: `tests/test_verify_script.py`, `tests/test_stage_topology.py`

**Interfaces:**
- Consumes: the env variable names from Task 1.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_verify_script.py`, matching that file's existing file-reading idiom:

```python
def test_verifier_checks_the_speech_credential_is_agent_unreachable():
    text = pathlib.Path("scripts/verify_container.sh").read_text(encoding="utf-8")
    assert "ELEVENLABS" in text
    assert "speech credential" in text
```

Add to `tests/test_stage_topology.py`:

```python
def test_speech_credential_is_declared_only_on_the_diode_service():
    compose = pathlib.Path("docker-compose.yml").read_text(encoding="utf-8")
    diode_block = compose.split("  diode:", 1)[1].split("\n  viewer:", 1)[0]
    assert "ELEVENLABS_API_KEY" in diode_block
    agent_block = compose.split("  agent:", 1)[1].split("\n  diode:", 1)[0]
    assert "ELEVENLABS" not in agent_block
    assert pathlib.Path(".env.example").read_text(encoding="utf-8").count("ELEVENLABS_API_KEY") >= 1


def test_speech_credential_is_absent_from_the_agent_image():
    dockerfile = pathlib.Path("Dockerfile").read_text(encoding="utf-8")
    assert "ELEVENLABS" not in dockerfile
```

Both test files may use a different import for path handling — match whatever the file already imports rather than adding `pathlib` if another idiom is in use.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py tests/test_stage_topology.py -v`
Expected: FAIL on the missing `ELEVENLABS` strings.

- [ ] **Step 3: Implement**

In `docker-compose.yml`, the `diode` service environment becomes:

```yaml
    environment:
      DIODE_DIR: /diode
      # Speech credential. Lives only here: the diode is on the egress network
      # and the agent is on internal, so the agent can cause spend but can
      # never read the key. Empty disables the speak command.
      ELEVENLABS_API_KEY: ${ELEVENLABS_API_KEY:-}
      ELEVENLABS_VOICE_ID: ${ELEVENLABS_VOICE_ID:-}
      ELEVENLABS_MODEL: ${ELEVENLABS_MODEL:-}
```

Do **not** add these to the `agent` service.

In `.env.example`, after the `STAGE_*` block:

```bash
# Optional speech for the diode's `speak` command. This key is mounted ONLY
# into the diode container, never the agent: the agent can cause spend through
# the gated, budgeted command but has no route to the diode and never sees the
# key. Leave empty to disable the command entirely.
ELEVENLABS_API_KEY=
#ELEVENLABS_VOICE_ID=JBFqnCBsd6RMkjVDRZzb
#ELEVENLABS_MODEL=eleven_multilingual_v2
```

In `scripts/verify_container.sh`, add beside the other agent-environment checks (after the "agent has NO internet route" block, around line 72):

```bash
echo "==> speech credential is unreachable from the agent"
if docker compose exec -T agent sh -c 'env | grep -q ELEVENLABS'; then
  echo "FAIL: speech credential present in the agent environment"; exit 1
fi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_verify_script.py tests/test_stage_topology.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add docker-compose.yml .env.example scripts/verify_container.sh tests/test_verify_script.py tests/test_stage_topology.py
git commit -m "feat: mount the speech credential into the diode only, and verify it"
```

---

### Task 5: Stage reads utterances

**Files:**
- Modify: `stage/data.py` (caps near line 15; new function after `diode_published`, around line 755)
- Modify: `stage/server.py` (`DISPLAY_*` near line 25; `_assemble_snapshot` around line 423-436; `_empty_snapshot` at line 387)
- Test: `tests/test_stage_data.py`, `tests/test_stage_containment.py`

**Interfaces:**
- Consumes: `contained_file`, `_read_capped`, `_filename_epoch` (existing in `stage/data.py`).
- Produces: `SPOKEN_TEXT_CAP = 400`, `SPOKEN_READ_BYTES = 4096`, `diode_spoken(diode_dir, limit=2) -> (list[dict], int)` where each dict is `{"name": str, "epoch": float, "text": str}`; snapshot keys `diode.spoken` and `diode.spoken_total`. Tasks 6, 7 and 8 consume these.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stage_data.py`:

```python
def test_diode_spoken_reads_the_sidecar_text_and_stamp(tmp_path):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"ID3audio")
    (spoken / "20260814_120000_000000.txt").write_text("hello there", encoding="utf-8")
    entries, total = data.diode_spoken(str(tmp_path))
    assert total == 1
    assert entries[0]["name"] == "20260814_120000_000000.mp3"
    assert entries[0]["text"] == "hello there"
    assert entries[0]["epoch"] > 0


def test_diode_spoken_is_newest_first_and_limited(tmp_path):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    for stamp in ("20260814_120000_000000", "20260814_130000_000000", "20260814_140000_000000"):
        (spoken / (stamp + ".mp3")).write_bytes(b"a")
        (spoken / (stamp + ".txt")).write_text(stamp, encoding="utf-8")
    entries, total = data.diode_spoken(str(tmp_path), limit=2)
    assert total == 3
    assert [e["name"] for e in entries] == [
        "20260814_140000_000000.mp3",
        "20260814_130000_000000.mp3",
    ]


def test_diode_spoken_missing_directory_is_empty(tmp_path):
    assert data.diode_spoken(str(tmp_path)) == ([], 0)


def test_diode_spoken_caps_the_text(tmp_path):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"a")
    (spoken / "20260814_120000_000000.txt").write_text("x" * 5000, encoding="utf-8")
    entries, _ = data.diode_spoken(str(tmp_path))
    assert len(entries[0]["text"]) == data.SPOKEN_TEXT_CAP
```

Add to `tests/test_stage_containment.py`, matching `test_published_does_not_follow_a_symlink`:

```python
def test_spoken_does_not_follow_a_symlink(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp3").write_bytes(b"secret")
    root = tmp_path / "diode"
    spoken = root / "spoken"
    spoken.mkdir(parents=True)
    os.symlink(outside / "secret.mp3", spoken / "20260814_120000_000000.mp3")
    entries, _ = data.diode_spoken(str(root))
    assert entries == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py tests/test_stage_containment.py -k spoken -v`
Expected: FAIL with `AttributeError: module 'stage.data' has no attribute 'diode_spoken'`.

- [ ] **Step 3: Implement**

In `stage/data.py`, beside `PUBLISHED_TEXT_CAP` (line 15-16):

```python
SPOKEN_READ_BYTES = 4096
SPOKEN_TEXT_CAP = 400
```

After `diode_published`:

```python
def diode_spoken(diode_dir, limit=2):
    """(newest utterances, total count) from the diode's spoken directory."""
    spoken_dir = os.path.join(diode_dir, "spoken")
    try:
        names = sorted(
            (n for n in os.listdir(spoken_dir) if n.endswith(".mp3")), reverse=True
        )
    except OSError:
        return [], 0
    total = len(names)
    out = []
    for name in names[:limit]:
        full = contained_file(diode_dir, os.path.join(spoken_dir, name))
        if full is None:
            continue
        try:
            stat = os.stat(full)
        except OSError:
            continue
        stem = name[: -len(".mp3")]
        sidecar = contained_file(diode_dir, os.path.join(spoken_dir, stem + ".txt"))
        text = _read_capped(sidecar, SPOKEN_READ_BYTES) if sidecar else None
        epoch = _filename_epoch(name)
        if epoch is None:
            epoch = stat.st_mtime
        out.append({"name": name, "epoch": epoch, "text": (text or "")[:SPOKEN_TEXT_CAP]})
    return out, total
```

In `stage/server.py`, add `DISPLAY_SPOKEN = 2` beside `DISPLAY_PUBLISHED`. In `_assemble_snapshot`, after the `published` line:

```python
    spoken, spoken_total = data.diode_spoken(DIODE_DIR, limit=DISPLAY_SPOKEN)
```

and add to the same dict that carries `"published"`:

```python
            "spoken": spoken,
            "spoken_total": spoken_total,
```

In `_empty_snapshot` (line 387), extend the diode dict to
`{"outputs": [], "published": [], "published_total": 0, "spoken": [], "spoken_total": 0}` so the
snapshot never omits a key.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py tests/test_stage_containment.py tests/test_stage_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/data.py stage/server.py tests/test_stage_data.py tests/test_stage_containment.py
git commit -m "feat: read diode utterances into the stream snapshot"
```

---

### Task 6: The audio route

**Files:**
- Modify: `stage/server.py` (`SECURITY_HEADERS` at line 35-44; constants near line 25; `StreamHandler.do_GET` at line 456-463)
- Test: `tests/test_stage_server.py`, `tests/test_stage_containment.py`

**Interfaces:**
- Consumes: `data.contained_file`, `DIODE_DIR` (existing); the `spoken/` layout from Task 2.
- Produces: `AUDIO_MAX_BYTES = 4_000_000`; the `GET /audio/<name>` route. Task 7 fetches this URL.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stage_server.py`, matching the file's existing handler-invocation idiom (build the handler without a socket the way the other route tests do — copy the nearest existing example rather than inventing one):

```python
def test_audio_route_serves_a_real_utterance(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"ID3audio")
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    status, headers, body = call_stream_route("/audio/20260814_120000_000000.mp3")
    assert status == 200
    assert headers["Content-Type"] == "audio/mpeg"
    assert body == b"ID3audio"


def test_audio_route_rejects_traversal_and_unknown_names(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    for route in (
        "/audio/../../etc/passwd",
        "/audio/%2e%2e%2fetc%2fpasswd",
        "/audio/nothing.mp3",
        "/audio/",
        "/audio/notes.txt",
    ):
        status, _, _ = call_stream_route(route)
        assert status == 404


def test_stream_csp_allows_media_from_self():
    assert "media-src 'self'" in server.SECURITY_HEADERS["Content-Security-Policy"]
```

Add to `tests/test_stage_containment.py`:

```python
def test_audio_route_rejects_a_symlinked_utterance(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.mp3").write_bytes(b"secret")
    root = tmp_path / "diode"
    spoken = root / "spoken"
    spoken.mkdir(parents=True)
    os.symlink(outside / "secret.mp3", spoken / "20260814_120000_000000.mp3")
    monkeypatch.setattr(server, "DIODE_DIR", str(root))
    status, _, _ = call_stream_route("/audio/20260814_120000_000000.mp3")
    assert status == 404


def test_audio_route_refuses_an_oversized_file(tmp_path, monkeypatch):
    spoken = tmp_path / "spoken"
    spoken.mkdir()
    (spoken / "20260814_120000_000000.mp3").write_bytes(b"a" * (server.AUDIO_MAX_BYTES + 1))
    monkeypatch.setattr(server, "DIODE_DIR", str(tmp_path))
    status, _, _ = call_stream_route("/audio/20260814_120000_000000.mp3")
    assert status == 404
```

If no shared `call_stream_route` helper exists in these test files, write one in `tests/test_stage_server.py` that drives `server.StreamHandler` the way the existing stream-route tests do, and import it where needed.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py tests/test_stage_containment.py -k audio -v`
Expected: FAIL — the route 404s for a real file, and `media-src` is absent from the CSP.

- [ ] **Step 3: Implement**

In `stage/server.py`, add beside the other caps:

```python
AUDIO_MAX_BYTES = 4_000_000
```

Add `media-src 'self'; ` to the `Content-Security-Policy` value in `SECURITY_HEADERS`, immediately after `img-src 'self' data:; `.

Add `unquote` to the existing `from urllib.parse import ...` line.

In `StreamHandler`:

```python
    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/":
            self._send(200, pages.STREAM_PAGE_HTML, content_type="text/html; charset=utf-8")
        elif route == "/api/stream":
            self._send(200, json.dumps(stream_snapshot()))
        elif route.startswith("/audio/"):
            self._handle_audio(unquote(route[len("/audio/") :]))
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def _handle_audio(self, name):
        """Serve one utterance's audio. The name is matched against the directory
        listing rather than joined from the request, so traversal is impossible."""
        spoken_dir = os.path.join(DIODE_DIR, "spoken")
        try:
            listing = os.listdir(spoken_dir)
        except OSError:
            listing = []
        if not name.endswith(".mp3") or name not in listing:
            self._send(404, json.dumps({"error": "not found"}))
            return
        target = data.contained_file(DIODE_DIR, os.path.join(spoken_dir, name))
        if target is None:
            self._send(404, json.dumps({"error": "not found"}))
            return
        try:
            if os.path.getsize(target) > AUDIO_MAX_BYTES:
                raise OSError("too large")
            with open(target, "rb") as f:
                body = f.read(AUDIO_MAX_BYTES)
        except OSError:
            self._send(404, json.dumps({"error": "not found"}))
            return
        self._send(200, body, content_type="audio/mpeg")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py tests/test_stage_containment.py tests/test_stage_pages.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/server.py tests/test_stage_server.py tests/test_stage_containment.py
git commit -m "feat: serve utterance audio read-only on the stream port"
```

---

### Task 7: Caption and playback on the stream page

**Files:**
- Modify: `stage/pages.py` (CSS near line 443; `#said` markup at line 578-584; new function near `renderRibbon`, around line 1320; `render` at line 1427)
- Test: `tests/test_stage_pages.py`

**Interfaces:**
- Consumes: `snap.diode.spoken` (Task 5), `GET /audio/<name>` (Task 6).
- Produces: nothing later depends on it.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_stage_pages.py`, matching that file's existing "assert on the page source" idiom:

```python
def test_stream_page_has_an_audio_element_and_caption():
    html = pages.STREAM_PAGE_HTML
    assert 'id="speak-audio"' in html
    assert 'id="speak-caption"' in html


def test_stream_page_plays_each_utterance_once_and_only_when_fresh():
    html = pages.STREAM_PAGE_HTML
    assert "spokenPlayed" in html
    assert "/audio/" in html
    assert "180000" in html
    assert "renderSpoken(" in html
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py -k speak -v`
Expected: FAIL on the missing ids.

- [ ] **Step 3: Implement**

In the `#said` section markup (line 578-584), after `<div id="said-foot"></div>`:

```html
      <div id="speak-caption"></div>
      <audio id="speak-audio" preload="auto"></audio>
```

Add CSS beside the `#said-stamp` rule (line 443):

```css
#speak-caption { font: 400 13px/19px var(--mono); color: var(--paper-dim); margin-top: 6px; }
#speak-audio { display: none; }
```

Add near the other `render*` functions (after `renderRibbon`, around line 1320):

```js
var spokenPlayed = {};
function renderSpoken() {
  var sp = (snap && snap.diode && snap.diode.spoken) || [];
  var cap = $("speak-caption");
  if (!cap) return;
  if (!sp.length) { setText(cap, ""); return; }
  var newest = sp[0];
  setText(cap, norm(newest.text || ""));
  var name = newest.name || "";
  if (!name || spokenPlayed[name]) return;
  spokenPlayed[name] = true;
  var ageMs = Date.now() + skewMs - (newest.epoch || 0) * 1000;
  if (ageMs > 180000) return;
  var a = $("speak-audio");
  if (!a) return;
  a.src = "/audio/" + encodeURIComponent(name);
  try {
    var p = a.play();
    if (p && p.catch) p.catch(function () {});
  } catch (e) {}
}
```

Marking `spokenPlayed[name]` **before** the freshness check is deliberate: an utterance that was
already stale on arrival must never play later, and a reload starts with an empty set but a stale
snapshot, so the age gate is what stops a replay there.

Call it from `render` (line 1427), after `renderRibbon();`:

```js
  renderSpoken();
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py -v` then `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/pages.py tests/test_stage_pages.py
git commit -m "feat: caption and play the newest utterance on the stream page"
```

---

### Task 8: The `spoke` commentary beat

**Files:**
- Modify: `stage/commentary.py` (`BEAT_KINDS` line 32-43; `BEAT_TEMPLATES` line 45-56; `detect_beat` line 136-199)
- Modify: `stage/server.py` (the `commentary.detect_beat(...)` call around line 424)
- Test: `tests/test_stage_commentary.py`

**Interfaces:**
- Consumes: `snapshot`-shaped `spoken` entries from Task 5 (`{"name", "epoch", "text"}`).
- Produces: `detect_beat(turns, stats, diode, published, now, spoken=None)` — the new parameter is keyword-with-default so existing callers and tests keep working.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_recent_utterance_beats_a_recent_publish(monkeypatch):
    now = 1_000_000.0
    beat = commentary.detect_beat(
        [],
        {},
        {"outputs": []},
        [{"name": "p.txt", "epoch": now - 5, "text": "written"}],
        now,
        spoken=[{"name": "20260814_120000_000000.mp3", "epoch": now - 5, "text": "said"}],
    )
    assert beat["kind"] == "spoke"


def test_a_stale_utterance_does_not_fire():
    now = 1_000_000.0
    beat = commentary.detect_beat(
        [],
        {},
        {"outputs": []},
        [],
        now,
        spoken=[{"name": "a.mp3", "epoch": now - commentary.RECENT_SECONDS - 1, "text": "old"}],
    )
    assert beat["kind"] != "spoke"


def test_spoke_is_a_known_beat_kind():
    assert "spoke" in commentary.BEAT_KINDS
    assert commentary.BEAT_TEMPLATES["spoke"]
```

Match the surrounding tests' construction of `stats`/`diode` — copy the nearest existing
`detect_beat` test rather than assuming the shapes above are complete; the assertion on
`beat["kind"]` is the part that matters.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_commentary.py -k spoke -v`
Expected: FAIL — `detect_beat` takes no `spoken` argument.

- [ ] **Step 3: Implement**

Add `"spoke",` to `BEAT_KINDS` immediately before `"published",` and to `BEAT_TEMPLATES`:

```python
    "spoke": "It has put a voice to something.",
```

Change the signature to:

```python
def detect_beat(turns, stats, diode, published, now, spoken=None):
```

and normalise beside the existing `published = published or []`:

```python
    spoken = spoken or []
```

Add immediately **before** the `if published:` block (line 189):

```python
    if spoken:
        newest_spoken = max(spoken, key=lambda s: s.get("epoch") or 0)
        spoken_epoch = newest_spoken.get("epoch")
        if isinstance(spoken_epoch, (int, float)) and now - spoken_epoch <= RECENT_SECONDS:
            return _beat(
                "spoke",
                count=len(spoken),
                novelty="first_this_life" if len(spoken) <= 1 else "repeat",
                epoch=spoken_epoch,
            )
```

In `stage/server.py`, pass the new argument:

```python
    beat = commentary.detect_beat(
        data.loop_turns(turns), stats, diode, published, now, spoken=spoken
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: PASS — all pre-existing tests included.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/commentary.py stage/server.py tests/test_stage_commentary.py
git commit -m "feat: add the spoke commentary beat"
```

---

## Completion criteria

- [ ] `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` passes.
- [ ] `.venv/bin/ruff format . && .venv/bin/ruff check .` is clean.
- [ ] `docker compose build` succeeds.
- [ ] `grep -ri elevenlabs agent.py agent_stock.py chassis.py Dockerfile system_prompt.txt user_prompt.txt garden_export/` returns nothing.
- [ ] **Manual, and required:** with `ELEVENLABS_API_KEY` set and `enable_speech: true` in `console.json`, one `speak` command produces audio that is **heard through a real OBS browser source** pointed at `http://localhost:8091`. Autoplay policy is invisible to every unit test in this suite — the feature can be entirely green and entirely silent. This step is what distinguishes "built" from "works", and it is the operator's to run.
