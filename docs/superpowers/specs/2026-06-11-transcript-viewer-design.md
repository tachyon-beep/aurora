# Transcript Viewer — Design

**Date:** 2026-06-11
**Status:** Approved

## 1. Purpose

A small, ephemeral web UI to watch the traffic the agent sends through the recorder proxy —
to "flick through what it's doing" live. The recorder already appends every turn to
`agent_life_transcript.jsonl` on the `transcripts` named volume; this viewer only **reads**
that file and renders it as a live, collapsible feed in the browser.

## 2. Constraints & invariants

- **Read-only and isolated.** The viewer mounts the `transcripts` volume read-only, publishes
  to host loopback only (`127.0.0.1:8090`), and is on its own network — NOT on the agent's
  `internal` network or the `egress` network. The agent cannot reach it; it cannot reach the
  agent, recorder, or diode. It adds no new egress path and does not weaken any Phase-2/3
  invariant.
- **Ephemeral.** The viewer holds no state of its own. Stop the container and nothing persists;
  the durable record remains the `transcripts` volume written by the recorder.
- **No secret exposure.** The proxy logs only request/response *bodies*, never headers, so the
  real `OPENROUTER_API_KEY` is not present in the transcript and the viewer cannot surface it.
- **Zero dependencies.** Stdlib `http.server` only, matching `proxy.py`/`watchdog.py`/`diode.py`.
- **Its own image.** Like the diode, the viewer ships in a separate image that copies only
  `viewer.py`, so this human-only tool never lands in the agent's container or `/garden`.

## 3. Data source

Each line of `agent_life_transcript.jsonl` is one turn:
`{"timestamp": "...Z", "request": {model, messages[...]}, "response": {choices[0].message: {content, reasoning|reasoning_content, tool_calls}} | {error}}`.
The viewer parses these lines; it never writes them.

## 4. Components

### 4.1 `viewer.py` (new)

A stdlib HTTP server with two routes:

- `GET /` → a single self-contained HTML page (inline CSS/JS, no external assets).
- `GET /api/turns?since=N` → `{"turns": [...], "total": M}` where `turns` are the entries from
  line index `N` onward and `total` is the current line count (so the page knows its next
  `since`).

Core logic is a pure function `load_turns(path, since)`:
- Reads the JSONL; iterates lines from index `since`.
- **Skips any line that fails to parse** (a half-written final line during a live poll, or a
  corrupt entry) — it is retried on the next poll once complete.
- Summarizes each turn into a render-ready dict:
  `{index, timestamp, model, request_messages: [{role, name, content, tool_calls}], response: {reasoning, content, tool_calls, error}}`.
  `reasoning` reads `reasoning_content` or `reasoning`; `error` is set when the response carries
  an `error` instead of `choices`.

`main()` reads `VIEWER_PORT` (default `8090`) and `TRANSCRIPT_DIR` (default `/transcripts`);
the transcript filename is `agent_life_transcript.jsonl`. A missing transcript file yields an
empty feed, not an error.

### 4.2 The page (inline in `viewer.py`)

- A single scrolling column; one collapsible card per turn, oldest to newest.
- Polls `/api/turns?since=<have>` every ~2s and appends new cards.
- **Follows live** (auto-scrolls to newest) until the user scrolls up; then shows a
  `● paused — N new ↓` affordance that resumes following on click.
- A card collapses to a one-line summary (time · model · short description of what the turn did
  — e.g. "assistant: tool read_file", "tool result", "error") and expands to: the request
  messages (role-tagged), the model's reasoning (de-emphasized), its reply content, tool calls
  (name + arguments), and a per-card **raw-JSON toggle**.
- Errors render highlighted.
- Plain, dependency-free vanilla JS; readable monospace styling.

### 4.3 `Dockerfile.viewer` (new)

`python:3.13-slim`, a non-root user, `COPY viewer.py` only, `ENTRYPOINT ["python", "/opt/viewer/viewer.py"]`.

### 4.4 `docker-compose.yml` (modify)

Add a `viewer` service:
- `build: { context: ., dockerfile: Dockerfile.viewer }`, `profiles: ["viewer"]` (so a plain
  `docker compose up` does NOT start it).
- `volumes: [ transcripts:/transcripts:ro ]`, `environment: { TRANSCRIPT_DIR: /transcripts }`.
- `ports: [ "127.0.0.1:8090:8090" ]`.
- Its own default network only (omit `internal`/`egress`); read-only rootfs, tmpfs `/tmp`,
  `cap_drop: [ALL]`, `no-new-privileges`, modest `mem_limit`/`pids_limit`.

## 5. Error handling

- Missing transcript file → empty feed.
- Malformed / partially-written line → skipped, retried next poll.
- The page tolerates an empty or error response from a poll by simply trying again next tick.

## 6. Testing

- Unit tests for `load_turns`: `since` offset correctness; malformed-line tolerance; extraction
  of `reasoning` (both field names), `content`, `tool_calls`, and `error`; request-message
  shaping; missing-file → empty.
- A light HTTP integration: start the server bound to an ephemeral port against a temp
  `TRANSCRIPT_DIR` holding a small sample JSONL, then fetch `/` (200, HTML) and
  `/api/turns?since=0` (JSON with the expected turns) via `urllib`.
- No Docker required for tests. `docker compose --profile viewer up viewer` is the manual smoke.

## 7. Usage

```
docker compose --profile viewer up viewer
# open http://localhost:8090
```
Stop with Ctrl-C / `docker compose --profile viewer down`.

## 8. Out of scope

- Filtering/search across turns, persistence, auth (loopback-only, single-user, ephemeral).
- Editing or replaying turns (read-only by design).
- Viewing the diode's `/diode/output` (separate channel; could be a future addition).
