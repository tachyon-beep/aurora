# LLM Telemetry Events — Design

**Date:** 2026-08-15
**Status:** Approved (autonomous continuation of the socket-transport sequence)
**Context:** Spec 3 of 3 in the sequence opened by
`docs/superpowers/specs/2026-08-14-llm-socket-transport-design.md`, building on
`docs/superpowers/specs/2026-08-15-llm-stream-console-design.md` (spec 2). The recorder begins
emitting a per-request event record — open, close, usage, and socket lifecycle — and the stage
renders it as stream lanes: one live activity indicator per model socket.

Spec 1 named the problem precisely: the stage's staleness is an event-model problem, not a
transport problem. `proxy.py` forwards with `urlopen` and writes the transcript only after the
response completes, so a long model request shows nothing on the stream page however fast the page
polls. The transcript is the wrong instrument for liveness — it records exchanges, not the state of
being mid-exchange. Events record the other half.

## Sequence

| Spec | Scope | Depends on |
| --- | --- | --- |
| 1 — merged | AF_UNIX transport, single `core` socket, network deletion | — |
| 2 — merged | Agent-declared additional sockets, per-stream budgets and hyperparameters, console file | 1 |
| **3 — this document** | Recorder emits per-request open/close/usage events; stage renders stream lanes | 2 |

## Why the channel is the transcripts volume

The events file is `/transcripts/events.jsonl`: written by the recorder, read by the stage through
the `transcripts` mount it already holds read-only. No compose change, no new mount, no new edge.

Spec 1 drew the boundary this rides on: telemetry sourced from the **watchdog** is agent-controlled
by construction (the watchdog runs from the agent-writable `/work` tmpfs) and is therefore never
rendered on the stream page; telemetry sourced from the **recorder** is trustworthy, because the
recorder runs from the read-only image in its own container and nothing the agent writes executes
there. Spec 1 explicitly reserved "recorder-sourced telemetry" for this spec. The transcripts
volume is the recorder's existing outward channel, so events belong beside the transcript, not on
the telemetry volume — which keeps invariant 3's telemetry bullet ("written only by the watchdog,
never rendered on the stream page") true without amendment.

The agent does not mount `/transcripts` and cannot see or influence the events file. The events
carry no message content, no headers, and no key — names, counts, statuses, durations, and token
totals only — so nothing in them widens what the public page can leak.

## The events

One JSON object per line, appended under the transcript lock's discipline (its own lock), rotated
by the existing `rotate_if_needed` machinery at `EVENTS_MAX_BYTES` (16 MiB — events are two orders
of magnitude smaller than transcript entries). Every event carries `timestamp` (ISO-8601 UTC,
matching the transcript) and `stream`.

| Event | Emitted | Extra fields |
| --- | --- | --- |
| `bind` | a socket is bound (core at startup; declared streams as the poll accepts them) | — |
| `unbind` | a declared socket is shut down and unlinked | — |
| `open` | a request body has been read, before the upstream forward | `id`, `model`, `messages` (count) |
| `close` | the response is about to be returned, whatever its status | `id`, `status`, `duration_seconds`, `usage` |

`id` is a per-request random hex token; `open` and `close` share it. `model` is the model the
forwarded body carries (post-composition on a declared stream). `usage` is copied from the upstream
response body's `usage` object when present — `prompt_tokens`, `completion_tokens`,
`total_tokens` — and omitted when the response carries none. A budget refusal closes with 429 and
no usage; a proxy error closes with its status. Every open gets exactly one close on every path
through the handler.

**Event writing is fail-open.** A failed write prints to stderr and the request proceeds; the
events file is an instrument, and a broken instrument must not become a broken model channel. This
is the same posture `rotate_if_needed` already takes.

## The stage: stream lanes

### Data

`stage/data.py` gains one reader:

```python
def stream_lanes(events_path, now=None, window=3600):
    """Per-socket activity derived from the recorder's event log."""
```

A bounded tail read (`EVENTS_TAIL_BYTES`, 512 KiB) parses the newest events and folds them into one
lane per stream:

```python
{
    "name": "core",
    "bound": True,
    "in_flight": 1,
    "in_flight_since": 1755230000.0,
    "last_epoch": 1755230000.0,
    "requests_hour": 12,
    "errors_hour": 1,
    "tokens_hour": 48213,
}
```

- `bound` follows the newest `bind`/`unbind` for the name.
- `in_flight` counts opens without a matching close, excluding opens older than
  `INFLIGHT_MAX_AGE` (600s): the upstream timeout is 60s and the chassis's bounded retries stay
  within a couple of minutes, so an unmatched open past ten minutes is a recorder that died
  mid-request, not a live call. `in_flight_since` is the oldest live open's epoch, `null` when
  nothing is in flight.
- `requests_hour`, `errors_hour` (status ≥ 400), and `tokens_hour` (summed `usage.total_tokens`)
  count closes inside the window.
- Lane order: `core` first, then the rest by name. `core` always appears, even from an empty or
  missing file. A lane that is unbound and has no close inside the window is dropped — a socket
  that no longer exists and did nothing recently is not activity.
- Malformed lines are skipped, the same posture every transcript reader takes; a torn final line is
  an expected state of an append-only file.

### Snapshot

`stage/server.py`'s snapshot gains a `lanes` key (present and empty in `_empty_snapshot`), each
lane capped for public output: name clipped to 32 characters, at most 9 lanes, integers passed as
integers. Like every snapshot field it is server-side capped and client-side rendered as text.

### Page

Two renderings on the stream page, both fed from `lanes`:

- **The in-flight indicator becomes evidence rather than inference.** The monologue's `#inflight`
  row currently guesses "waiting for row N" from the age of the last transcript entry. When the
  `core` lane reports `in_flight > 0`, the row shows with elapsed time computed from
  `in_flight_since` — the page now knows a request is open rather than suspecting one. When events
  are absent (an older recorder, an empty file), the existing inference stands unchanged as the
  fallback.
- **A lanes strip in the masthead** (`#mh-b`, beside the provenance line): one compact chip per
  lane — name, a live/idle dot (live while `in_flight > 0`), and `requests_hour` /
  `tokens_hour` as short counts. With only `core` bound this reads as a single steady heartbeat
  chip; when the agent declares streams, new chips appear as the lanes do. The strip inherits the
  page's existing type and colour system and adds no new row to the fixed 1080px grid.

The stream page is not an agent-readable surface, so its copy is free to be framed; the numbers
under the framing are the recorder's and stay factual. Per the standing stage rule, the lane data
crosses from `data.py` already shaped and capped, and the client renders it with `setText` — no
markup from data.

## Error handling

| Condition | Behaviour |
| --- | --- |
| Events file missing or empty | Lanes report `core` alone, unbound state, zero counts; page falls back to inference |
| Torn final line | Skipped by the parser; complete on the next poll |
| Rotation mid-read | The tail read sees the fresh file; in-flight and hourly counts rebuild from new events (stated, accepted: a rotation forgets opens, and `INFLIGHT_MAX_AGE` bounds any stale residue either way) |
| Recorder dies mid-request | The unmatched open ages out of `in_flight` after 600s |
| Event write fails | stderr note; the request is unaffected |
| Stage cannot read the file | `stream_snapshot`'s existing never-raise posture: the empty snapshot renders |

## Containment

- **No new mount, network, port, or credential.** The recorder already writes the transcripts
  volume; the stage already mounts it read-only; the agent has never mounted it.
- **Events are recorder-sourced**, the one telemetry source spec 1 pre-cleared for the stream
  page. The watchdog's telemetry volume keeps its invariant untouched: still written only by the
  watchdog, still never rendered on the stream page.
- **Nothing sensitive enters the events.** No message content, no headers, no key — the
  body-logging/header-free discipline of the transcript applies a fortiori to a file that carries
  only counts and names. The one new fact class is token usage, which the transcript's response
  bodies already contain.
- The stage-side read is of a recorder-owned file on a volume the agent cannot write, so
  `contained_file` is not load-bearing here; the read still goes through the same bounded-read,
  skip-malformed posture as every other stage reader.

## Documentation realignment

| Location | Change |
| --- | --- |
| `CLAUDE.md` invariant 2, chassis/resilience notes | unchanged |
| `CLAUDE.md` invariant 3 | the proxy bullet gains one sentence: the recorder also appends per-request open/close/usage events (no content, no headers) to `events.jsonl` on the transcripts volume, which the stage renders as stream lanes |
| `README.md` component table, recorder row | mentions the event log |
| `README.md` stage row / viewing section | mentions the lanes strip |

## Testing

**Unit — no Docker required:**

- Recorder events (`tests/test_recorder_events.py` *(new)* or extending
  `tests/test_unix_listener.py`) — a completion writes an `open` and a `close` sharing an `id`,
  with the stream name, model, message count, status 200, a plausible duration, and the stubbed
  usage block; a budget refusal closes with 429 and no usage; an upstream failure closes with its
  status; a declared stream's `open` carries the composed model; `bind`/`unbind` events appear as
  the poll accepts and removes a stream; a failing events path does not fail the request.
- `tests/test_stage_data.py` *(extend)* — `stream_lanes`: open-without-close is in flight;
  open+close is not; an open past `INFLIGHT_MAX_AGE` ages out; hourly request/error/token sums
  prune to the window; `core` always present and first; unbound idle lanes dropped; malformed
  lines skipped; missing file yields the core-only shape.
- `tests/test_stage_server.py` *(extend)* — the snapshot carries `lanes`; `_empty_snapshot`
  carries `lanes: []`; caps applied (name clip, lane count).
- `tests/test_stage_pages_js.py` *(extend)* — the page script references the lanes fields it
  renders.
- `tests/test_verify_script.py` *(extend)* — covers the assertions added to the verify script.

**Container — `scripts/verify_container.sh` (extend):**

```
after the recorded completion:
events.jsonl exists in /transcripts            == contains an open and a close for it
/api/stream on the stage                       == carries a lanes array naming core
```

## Non-goals

- **Streaming responses (SSE).** Events give sub-turn liveness without changing how the recorder
  forwards; response streaming remains its own change if ever wanted.
- **Rendering events anywhere but the lanes and the in-flight row.** No event feed panel; the
  monologue stays the transcript's.
- **Watchdog- or agent-sourced events.** The event log is the recorder's alone; mixing sources
  would break the trust boundary that lets it onto the public page.
- **A spend dashboard or cost accounting.** Token counts render as activity, not as billing; the
  deferred global call cap is still deferred.
- **Any change to `agent.py`, `agent_stock.py`, or `chassis.py`.** The agent's world does not
  change in this spec at all: events and lanes live entirely on the operator's side of the glass.
