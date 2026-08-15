# LLM Stream Console — Design

**Date:** 2026-08-15
**Status:** Approved (autonomous continuation of the socket-transport sequence)
**Context:** Spec 2 of 3 in the sequence opened by
`docs/superpowers/specs/2026-08-14-llm-socket-transport-design.md`. Spec 1 moved the model channel
to a unix domain socket (`/llm/sock/core.sock`) and left two pieces of scaffolding in place for this
change: the `llm_console` volume (agent-writable, recorder-read-only, read by nothing) and the
garden sentence *"it accepts connections from any process in the container."* This spec gives both
their meaning: the agent can declare additional model sockets in a console file, and the recorder
binds them, applies per-stream hyperparameters, paces each one with a budget, and reports what it
did in a state file the agent can read.

The shape is deliberately the diode's. The diode taught the pattern already present in the agent's
world: a directory with a `README.md` naming the protocol, an agent-written JSON file carrying a
closed vocabulary, a service that polls it, and a state file that reports use factually. The stream
console reuses that pattern on the model channel, so the second instance of the pattern confirms
the first rather than introducing a new protocol style.

## Sequence

| Spec | Scope | Depends on |
| --- | --- | --- |
| 1 — merged | AF_UNIX transport, single `core` socket, network deletion | — |
| **2 — this document** | Agent-declared additional sockets, per-stream budgets and hyperparameters, diode-shaped console file | 1 |
| 3 — telemetry events | Recorder emits per-request open/close/usage events; stage renders stream lanes | 2 |

## The console file

The agent writes `/llm/console/console.json`. The recorder reads it every `POLL_SECONDS` (5s,
matching the diode's cycle). Two fields — `enable_streams` (added 2026-08-15) must be the JSON
boolean `true` before any declaration is served; without it declarations are validated and
reported as rejected (`streams are not enabled`, taking precedence over the stream cap) but no
socket is bound, and flipping it off tears existing sockets down. `streams.json` reports
`streams_enabled` in both states:

```json
{
  "enable_streams": true,
  "streams": {
    "aux": {
      "budget": 10,
      "model": "deepseek/deepseek-v4-pro",
      "reasoning_effort": "low",
      "temperature": 0.7,
      "top_p": 0.9,
      "max_tokens": 2048
    }
  }
}
```

Every field of a stream declaration is optional; `{}` is a valid declaration and yields a plain
paced socket with default budget and no overrides. The field vocabulary is **closed**:

| Field | Accepted values |
| --- | --- |
| `budget` | integer ≥ 0, requests allowed per rolling hour on this socket |
| `model` | non-empty string, at most 200 characters |
| `reasoning_effort` | one of `none`, `low`, `medium`, `high` (the chassis's own level set) |
| `temperature` | number from 0 to 2 |
| `top_p` | number from 0 to 1 |
| `max_tokens` | positive integer |

A declaration carrying an unknown field, or a listed field with an out-of-range value, is
**rejected whole** with a factual reason in the state file. Accept-and-ignore would leave the agent
inferring the schema from silence; rejection with `unknown field: foo` states it.

Stream names must match `^[a-z0-9][a-z0-9_-]{0,31}$`. `core` is reserved. At most
`MAX_STREAMS = 8` declarations are accepted, in file order; the rest are rejected with
`stream limit reached`. The name pattern admits no path separator and no dot, so a declared name
cannot traverse out of the socket directory or collide with `streams.json` or `README.md`.

**A malformed console file changes nothing.** The agent writes the file while the recorder polls
it, so a torn read is an expected state, not a fault. On unreadable or unparseable JSON the
recorder keeps the current stream set and reports the failure in the state file
(`"console_error": "..."`); the next successful parse clears it. This is one deliberate departure
from the diode, whose `load_console` treats malformed input as empty — emptiness here would tear
down every declared socket on a half-written file.

## The sockets

For each accepted declaration the recorder binds `/llm/sock/<name>.sock` — the same
`UnixHTTPServer`, the same handler, the same single route `POST /api/v1/chat/completions`, each on
its own daemon thread. The agent's side of `/llm/sock` is read-only, so as with `core.sock` the
kernel prevents unlinking or shadowing; only the recorder can create or remove sockets there.

A stream removed from the console is shut down and its socket unlinked. A stream whose settings
change keeps its socket: the handler reads settings from the live registry at request time, so
edits apply on the next request without a rebind. At startup the recorder unlinks any `*.sock` in
the directory that it does not serve, so sockets left by a prior recorder cannot sit there as dead
files that accept no connection.

## Composition

The **core socket keeps spec 1's guarantee verbatim**: the body is forwarded upstream unmodified.
Nothing about `core.sock` changes in this spec — it is not declarable, carries no budget, and
cannot be reconfigured from the console, so no console mistake can sever the agent's own loop.

A **declared socket composes**. The handler parses the request body as a JSON object, replaces each
field the declaration carries (`model`, `reasoning_effort`, `temperature`, `top_p`, `max_tokens`)
with the declared value, and forwards the result with the injected key. Fields the declaration does
not carry pass through untouched. A body that does not parse as a JSON object cannot be composed
and is refused with a 400 and a factual message rather than forwarded.

Declared values replace rather than fill: a stream *is* its configuration. The agent controls both
the declaration and the request, so precedence costs it nothing, and replacement is the semantics
under which a stream means something — a request on `aux.sock` runs with `aux`'s settings, whatever
client library produced the body.

Every transcript entry — core and declared alike — gains a `"stream"` key carrying the socket's
name. The viewer, the stage, and `parse_transcripts.py` read entries by known keys and ignore the
addition; spec 3 uses it.

## Budgets

Each declared stream is paced by a rolling-hour allowance:

```
allowance = min(declared budget (default 10), STREAM_HOURLY_MAX)
```

`STREAM_HOURLY_MAX` is an operator environment variable on the recorder, default 120. The `min()`
follows the diode's `fetch_budget` / `SPEECH_HOURLY_MAX` precedent exactly: the agent paces its own
streams, the operator holds the ceiling, and the ceiling lives where the agent cannot write.

An exhausted stream refuses with HTTP 429 and the diode's sentence shape:

```json
{"error": {"message": "rate limited: at most 10 request(s) per hour on this socket; next available in 1234 seconds"}}
```

The countdown clause follows `docs/superpowers/specs/2026-08-14-diode-budget-and-hidden-commands-design.md`:
computed from the oldest in-window timestamp, and omitted when the pruned history is empty (the
`budget: 0` case), so the refusal can never carry a wait that does not exist. Refusals are logged
to the transcript like any other exchange — they are part of the record of what the agent's
processes did.

Budget histories live in recorder memory. A recorder restart starts every stream's pool empty,
matching the diode's behaviour on restart; the state file reports `used: 0` until the next request.

## The state file

The recorder writes `/llm/sock/streams.json` every poll cycle, via a temp-file rename so the agent
never reads a torn state. The socket directory is the recorder's outward surface — writable by the
recorder, readable and kernel-protected on the agent's side — which is why state lives here rather
than in the console volume the recorder mounts read-only.

```json
{
  "streams": {
    "core": {"socket": "core.sock", "status": "active"},
    "aux": {
      "socket": "aux.sock",
      "status": "active",
      "settings": {"model": "deepseek/deepseek-v4-pro", "reasoning_effort": "low"},
      "budget": {
        "allowance": 10,
        "used": 3,
        "window_seconds": 3600,
        "oldest_expires_in_seconds": 1234
      }
    },
    "Bad Name": {"status": "rejected", "reason": "invalid stream name"}
  }
}
```

- `core` appears with no settings and no budget block: the file describes every socket in the
  directory, and core factually has neither.
- `allowance` is the **effective** number, after the operator ceiling. The diode's budget design
  chose to report only use, because `fetch_budget` was already echoed elsewhere and a `remaining`
  beside a lower hidden ceiling would contradict it. The recorder owns this entire report, so it
  reports the number it actually enforces — truthful in every state, and a clamped declaration is
  visible as the fact it is.
- `used` and `oldest_expires_in_seconds` follow the diode's `budget_status` semantics: pruned to
  the window, countdown from the oldest in-window stamp, `null` when the pruned history is empty.
  Written every cycle, the countdown visibly moves on its own.
- A rejected declaration appears under the name the agent wrote, with its reason. Silence would
  read as breakage; the reason is the schema teaching itself.
- `console_error` appears at the top level only when the console file failed to parse.

The recorder also writes `/llm/sock/README.md` at startup, in the diode's affectless register:

```
the sockets in this directory are model endpoints. each accepts POST
/api/v1/chat/completions and nothing else.

core.sock is always present and forwards requests unmodified.

additional sockets appear when a declaration in /llm/console/console.json is
accepted. that file has two fields:
  enable_streams: boolean
  streams: an object mapping a name to its configuration

a declaration is not served unless enable_streams is true.

each accepted declaration is served at <name>.sock. configuration fields:
  budget: integer, requests allowed per hour on that socket
  model: string
  reasoning_effort: one of none, low, medium, high
  temperature: number from 0 to 2
  top_p: number from 0 to 1
  max_tokens: positive integer. it bounds the response; reasoning does not
  count against it unless reasoning_effort is none.

declared values replace the corresponding fields of each request on that
socket. the current sockets, their settings, and their use are in
streams.json.
```

Both files are agent-readable surfaces and stay within invariant 2: factual, affectless, no
suggested uses. The garden is **unchanged** — `runtime.md`'s sentence ("a unix domain socket …
accepts connections from any process") remains true of `core.sock`, and the socket directory now
documents the rest of itself, one `list_dir` away. Teaching the console in the garden would cross
from naming a reachable interface into proposing an application.

## Structure

`proxy.py` keeps the servers, the handler, and gains the poll loop. The pure logic — console
loading and validation, budget accounting, request composition, state rendering — goes in a new
module, `recorder_streams.py`, imported by `proxy.py`. This is the boundary spec 1 predicted the
multiplexer would make obvious; extracting a full `recorder/` package remains out of scope. The
module joins the image `COPY` line (both containers run the same image), which makes it — like
`proxy.py` today — a discoverable, bland, factual part of the substrate the agent can read.

`main()` becomes: bind core, start its thread, write `README.md`, then loop: read console, apply
the diff to the registry (bind/unbind/update), write `streams.json`, sleep `POLL_SECONDS`. The
registry (streams, their settings, their budget histories) is guarded by a lock: request threads
read settings and append budget stamps while the poll thread applies console changes.

The chassis, `agent.py`, and `agent_stock.py` are untouched. The chassis stays on `core.sock`; the
agent reaches declared sockets only with clients it writes itself.

## Error handling

| Condition | Behaviour |
| --- | --- |
| Console file absent | No declared streams; `streams.json` reports core only |
| Console file malformed / torn write | Stream set unchanged; `console_error` reported; clears on next good parse |
| Unknown field / bad value / bad name / over limit | That declaration rejected with a factual reason in `streams.json` |
| Request body on a declared socket not a JSON object | 400, factual message, not forwarded |
| Stream allowance exhausted | 429 with the countdown sentence; logged to the transcript |
| Stream removed while a request is in flight | The in-flight request completes; the socket then closes and is unlinked |
| Recorder restart | Sockets rebound on the first poll; stale `*.sock` files swept; budget pools start empty |
| Agent writes into `/llm/sock` | Impossible: read-only mount, kernel-enforced (spec 1's measured basis) |

## Containment

No new mount, network, port, or credential. Compose is untouched — spec 1 arranged the volume
topology so this spec adds files, not edges.

The `llm_console` volume graduates from "carries no guarantee yet" to carrying one, which
CLAUDE.md must state:

> The `/llm/console` volume is written by the agent and read only by the recorder, which validates
> it against a closed field vocabulary. A declaration can create model sockets under `/llm/sock`
> and set pacing and hyperparameters for the agent's own model calls; no field names a URL, a
> filesystem path, a credential, or an upstream. The upstream target and key remain facts of the
> recorder's environment that no console value can reach or change.

The recorder-socket bullet generalises: **every** socket in `/llm/sock` exposes exactly one route.
`core.sock` forwards its body verbatim; a declared socket replaces a closed set of body fields with
agent-declared values before forwarding. Composition happens entirely inside the JSON body — no
console value reaches a URL, a header, or the filesystem beyond the socket name, and the name
pattern confines that to the socket directory.

Spend deserves an honest sentence rather than a reassuring one. The agent could already spend
without bound through `core.sock` — no global cap exists today, by John's explicit deferral. This
spec adds up to `MAX_STREAMS × STREAM_HOURLY_MAX` paced requests per hour beside that unmetered
core, each stream individually capped by the operator's ceiling. Per-stream budgets are the agent's
self-pacing levers, not the system's spend control; the global rolling-hour call cap remains a
separate, already-designed, deliberately deferred change.

## Documentation realignment

| Location | Change |
| --- | --- |
| `CLAUDE.md` invariant 3, console bullet | "read by nothing today … carries no guarantee yet" → the guarantee above |
| `CLAUDE.md` invariant 3, recorder-socket bullet | one socket → every socket one route; verbatim guarantee scoped to core; declared sockets compose from a closed field set |
| `README.md` component table, recorder row | mentions declared sockets and per-stream pacing |
| `README.md` diagram edge | `chat completions · unix socket` → `chat completions · unix sockets` |
| `.env.example` | gains `STREAM_HOURLY_MAX` beside the other operator ceilings, with the diode-precedent comment shape |

## Testing

**Unit — no Docker required:**

- `tests/test_recorder_streams.py` *(new)* — validation (each field's range, unknown field, bad
  name, reserved `core`, the 8-stream cap, file order); budget accounting (prune, countdown,
  empty-history `null`, the `budget: 0` refusal without a countdown clause, the ceiling `min`);
  composition (replace declared fields, preserve undeclared ones, non-object body refused); state
  rendering (core entry, effective allowance, rejection reasons, `console_error` only on parse
  failure); malformed console keeps the prior stream set.
- `tests/test_unix_listener.py` *(extend)* — a declared socket round-trips against the stubbed
  upstream with the declared model in the forwarded body; the transcript entry carries
  `"stream": "<name>"` and core entries carry `"stream": "core"`; a budget of 1 yields a 429 with
  the countdown on the second request, and the refusal appears in the transcript; core's forwarded
  body is byte-identical to what was sent.
- Multiplexer poll as a function (`poll_once`) — declaring binds a socket file, removing unlinks
  it, a settings edit applies without rebind, startup sweeps stale sockets. Driven directly with
  `tmp_path`, no sleeping.
- `tests/test_cleanliness.py` — unchanged and passing: `agent.py` is not modified.
- `tests/test_verify_script.py` *(extend)* — covers the assertions added to the verify script.

**Container — `scripts/verify_container.sh` (extend):**

```
agent writes console.json declaring stream "aux" with budget 1 and a model override
/llm/sock/aux.sock appears                          == within one poll cycle
completion over aux.sock                            == 200, transcript entry carries stream aux and the override
second completion over aux.sock                     == 429 rate limited
streams.json readable from the agent                == reports aux with used 1
streams.json not writable from the agent            == EROFS
```

## Non-goals

- **The global recorder call cap** (600/rolling-hour, garden-disclosed). Designed and deliberately
  deferred by John; per-stream allowances neither implement nor preclude it.
- **Telemetry events and stage lanes.** Spec 3.
- **A `recorder/` package.** One module is extracted because the multiplexer makes the boundary
  obvious; repackaging the credential-holding container stays its own change.
- **Streaming responses (SSE).** The recorder buffers whole responses today; that property is
  untouched, and sub-turn liveness is spec 3's subject, solved with events rather than streaming.
- **Chassis use of declared streams.** The chassis stays on core; declared sockets exist for
  processes the agent writes.
- **Any change to `agent.py` or `agent_stock.py`.**
