# Stream sockets for a full-time agent: streaming, keep-alive, token quotas

Date: 2026-08-16
Status: design approved, implementation not started

Companion to `2026-08-16-execution-pump-design.md`, separable from it. That design gives the agent
a way to run processes across time; this one makes the model sockets those processes reach capable
of sustaining a full-time reasoning agent.

## Problem

Five findings, each verified against the code.

1. **No sockets on a default clone.** `.env.example` ships `STREAM_MODEL_ALLOW_TEXT` and
   `STREAM_MODEL_ALLOW_VISION` commented out. With both empty, `permitted_models()` is empty,
   every seeded declaration is rejected with "model not permitted", `models.json` publishes an
   empty list, and no socket binds. This is documented behaviour — `test_seed_streams_are_rejected_when_the_operator_permits_no_models`
   asserts it — but it means the shipped default is a stack with no streams.

2. **Most permitted models have no socket.** The live `.env` permits three text models and one
   vision model; `llm_console_seed.json` declares two sockets.

3. **No usable token allowance.** The seed sets `model` and `budget` and nothing else.
   `validate_declaration` accepts `max_tokens`, but the seed declares none, so `compose_body`
   leaves whatever the caller sent. `aurora-5651785038` ("Reject or surface truncated LLM
   responses") is the same symptom from the other end.

4. **A connection cannot endure.** `ProxyHTTPRequestHandler` never sets `protocol_version`, so it
   inherits `BaseHTTPRequestHandler`'s **HTTP/1.0** default and closes the connection after every
   response. Keep-alive is off; one connection is exactly one request, by server configuration.

5. **Streaming does not work, and is unmetered.** `proxy.py:238` does `response_body = res.read()`
   — the entire upstream response is buffered before a byte returns, so `stream: true` is answered
   as one lump at the end. Worse, `json.loads(response_body)` fails on an SSE body, `res_data`
   becomes `{"raw_body": …}`, `usage` is absent, and the close event records **no token counts**.

Together: the budget counts requests rather than tokens, which is not the quantity that costs
money; and the one request shape a full-time agent would use is the one shape that is neither
supported nor accounted.

## Decisions taken

1. **Quota is ~2M tokens/hour per socket by default** — enough for a low-to-medium reasoning agent
   running full time.
2. **Full scope**: token accounting, SSE streaming relay, and HTTP/1.1 keep-alive. This is what a
   permanent connection actually requires.
3. **One socket per permitted model, working out of the box** — allow lists shipped uncommented in
   `.env.example`, seed expanded to match.
4. **Tool support needs nothing.** It already passes through; see below.

## Tool calling: nothing to enable

`compose_body` replaces exactly five fields (`model`, `reasoning_effort`, `temperature`, `top_p`,
`max_tokens`). Every other key is forwarded unchanged, so `tools`, `tool_choice`,
`parallel_tool_calls`, and messages carrying `tool_calls` or `role: "tool"` already cross the
recorder intact on declared streams, and verbatim on `core.sock`. `validate_declaration`'s closed
vocabulary governs the console file, not request bodies. `log_transcript` already renders tool
calls. The main agent does exactly this today over `core.sock`, with schemas its own
`ToolRegistry` builds from docstrings.

Ruled: **no `tool_use` flag in `models.json` and no third allow list.** A model that does not
support tools says so in its own error, and the agent learns by trying. `image_input` exists
because vision needed a separate list for routing, not as a precedent for advertising
capabilities.

One consequence lands in the streaming work below: streamed `tool_calls` arrive as index-keyed
delta fragments with `arguments` split across chunks, and must be reassembled for the transcript
or the viewer and stage lose tool visibility on every streamed request.

## Design

### 1. Keep-alive

Set `ProxyHTTPRequestHandler.protocol_version = "HTTP/1.1"`. One connection then carries many
completions, which is what "permanent connection" means at the transport layer.

Requirements this imposes:

- Every response needs accurate framing. The main path and `_finish_local` both already send an
  exact `Content-Length`; `send_error` frames itself. Streamed responses use chunked framing
  (below) and must not send `Content-Length`.
- Set `ProxyHTTPRequestHandler.timeout` (300s) so an idle keep-alive connection cannot pin a
  thread forever. `UnixHTTPServer` is `ThreadingMixIn` with `daemon_threads`, so each connection
  holds a thread for its whole life rather than for one request.
- Add `pids_limit: 256` and `mem_limit: 512m` to the `recorder` service, which currently sets
  neither. Linux counts threads against the pids cgroup, so this bounds connection fan-out. This
  is resource hygiene inside the recorder, not a containment change.

### 2. Streaming relay

When the composed request carries `stream: true`, relay the upstream response incrementally
instead of buffering:

- Read the upstream body in chunks rather than `res.read()`.
- Write each chunk to the client with chunked transfer framing
  (`f"{len(piece):X}\r\n"` + piece + `"\r\n"`, terminated by `"0\r\n\r\n"`), with
  `Transfer-Encoding: chunked` and no `Content-Length`.
- Accumulate the SSE text as it passes, for the transcript and for usage.

**Transcript reconstruction.** `log_transcript` and everything downstream of it (viewer, stage)
expect a completed response object. Reassemble one from the deltas:

- concatenate `choices[i].delta.content` and any reasoning delta into the corresponding message
- reassemble `choices[i].delta.tool_calls[j]`, keying on the delta's `index`, concatenating
  `function.arguments` fragments and taking `id` / `function.name` from the first fragment that
  carries them
- carry `finish_reason` from the chunk that sets it

The reconstructed object is what gets logged, so a streamed exchange appears in the transcript in
the same shape as a buffered one. Headers are still never logged.

**Usage from a stream.** OpenAI-shaped SSE omits `usage` unless the request sets
`stream_options.include_usage`. The recorder therefore **sets `stream_options.include_usage = true`
on streamed requests**, and reads the final `data:` event — the one whose `choices` is empty and
which carries `usage` — for the token counts. `data: [DONE]` terminates.

For declared streams this is one more recorder-set field, alongside the five it already composes.

**`core.sock` needs no body modification at all.** Verified: `chassis.py` never sets `stream`, so
core traffic is entirely non-streamed, `res.read()` returns complete JSON, and `usage` is already
parsed into the close event today. `CORE_TOKEN_HOURLY_MAX` can therefore be enforced from the
existing parse with `core.sock` staying **byte-verbatim**, and invariant 3 needs no exception.

Should the agent ever rewrite its chassis to stream on core, that request's response yields no
usage, and the conservative-charge rule below covers it — the recorder charges the request's
`max_tokens` rather than zero. No body change, no evasion, invariant intact.

### 3. Token quota

**Console field.** `token_budget` joins `budget` as a declaration field the validator knows:
a non-boolean integer of at least 0, tokens per hour. Unknown fields are still rejected, so the
vocabulary stays closed.

**Operator ceiling.** `STREAM_TOKEN_HOURLY_MAX`, default **2,000,000**, mirroring
`stream_limit_max()`: read from the environment with no upper clamp, so the operator can raise it
by config alone. The enforced allowance is `min(declared token_budget, ceiling)` — the console can
lower it and never raise it, exactly as with requests.

**Accounting.** `StreamRegistry` gains a per-stream token history alongside its request history:
`(timestamp, tokens)` pairs, pruned to the same 3600-second window by `_prune_histories`, and
retained for undeclared names for the same reason the request history is — so removing a stream
from the console and re-declaring it does not buy a fresh allowance inside the hour.

`admit` checks the token window before forwarding and refuses with `429` and a message distinct
from the request-rate one, so the agent can tell which ceiling it hit:

> rate limited: at most 2000000 token(s) per hour on this socket

A new `charge` step records `total_tokens` from the close event against the stream's token
history after the response completes.

**Overshoot is bounded and stated.** Admission happens before the response exists, so a request
admitted just under the ceiling can carry the window over it by at most one response —
`max_tokens` plus the reasoning allowance plus the prompt. The effective ceiling is
`quota + one maximum response`, and the spec says so rather than implying exactness.

**Truncated streams charge conservatively.** If a streamed response ends without a usage event —
client disconnect, upstream fault — charge the composed `max_tokens` rather than zero. Charging
zero would make "disconnect before the final chunk" a way to spend without being metered. Non-
streamed responses that return no usage charge zero, since those bodies are fully read and a
missing `usage` there means the upstream did not report one.

**What is counted.** `total_tokens` — prompt plus completion, the quantity that is billed. At an
8–30k-token turn, 2M/hour is roughly 60–200 turns per hour, or one to three per minute sustained,
which is the intended "full time". If a stream agent runs a large context the same quota buys
proportionally fewer turns; the number is a config value precisely so that trade is the operator's.

**Reported back.** `render_state` gains the token allowance and window usage per stream, so
`streams.json` shows the agent its own consumption against both ceilings. Without that the quota
is invisible until it refuses.

### 4. `core.sock`

`core.sock` has no ceiling of any kind — verified: `STREAM_HOURLY_MAX` clamps declared streams
only, and core forwards to the operator-configured upstream with the real key. It went unnoticed
because the watchdog spawns exactly one agent process.

Add **`CORE_TOKEN_HOURLY_MAX`**, token-based for consistency with the above, **shipped unset**.
Empty means no limit, which is today's exact behaviour, so nothing changes until the operator sets
it. This supersedes the request-counted `CORE_HOURLY_MAX` named in the pump design.

`chassis.py` must classify a core rate-limit refusal as **transient**: retry with backoff, then
exit 44 (environment pause) if it persists. Not a tombstone, and not a model fallback — both are
wrong answers to a budget refusal.

**The primary agent has no priority over pump children, and that is accepted.** Once the pump
exists, the transient path has a starvation shape worth stating rather than discovering. The
primary agent hits the core ceiling, exits 44, the watchdog pauses `ENVIRONMENT_PAUSE_SECONDS`
(60s) and respawns — but pump-supervised processes kept spending through that entire pause, and
60s is a small fraction of the 3600s budget window, so the window can still be full on return.
The primary can starve while processes it created consume the quota.

Three things make this tolerable rather than a defect. The core ceiling ships unset, so the
situation only arises when an operator opts in. Pump children spend on the **stream** sockets by
default, which have their own separate per-socket quotas and do not draw on core's. And a lineage
that starves its own primary agent is exhibiting something worth observing, not a bug to design
away — the whole point of the pump is that the agent can arrange its own execution, including
badly. If it becomes an operational nuisance, the narrow fix is to scale the pause toward the
budget window when the refusal is specifically a rate limit, which is distinguishable from a
network fault; that is deliberately not specced now.

### 5. Defaults

**The shipped config and the shipped seed must agree.** The seed is a static repo file; the allow
lists are operator environment, and they have already drifted — `.env.example` documents
`deepseek/deepseek-v4-pro` while the live `.env` permits `deepseek/deepseek-v4-pro-0813`. A seed
naming a slug the operator does not permit fails silently from the agent's side: the socket simply
never appears.

- `.env.example` ships both allow lists **uncommented**, carrying the model set this deployment is
  known to work against.
- `llm_console_seed.json` declares **exactly one stream per model in those lists**.
- A new test asserts the seed's model set equals the union of the allow lists parsed from
  `.env.example`. Drift becomes a test failure rather than a missing socket.

An operator whose own `.env` differs still works: unmatched declarations are rejected with a stated
reason and matched ones bind. The guarantee is about the shipped default.

**The allow lists are effectively capped at eight models in total**, which is not obvious from
either variable's name. `MAX_STREAMS` is 8, and `evaluate_console` walks declarations in file order
and rejects everything past the eighth with "stream limit reached". One socket per permitted model
therefore means a ninth model silently gets no socket — and the agreement test above would still
*pass*, since seed and env agree; only the binding fails. The at-most-8 seed test catches it
whenever the seed is regenerated in-repo, and this paragraph is the note for anyone extending an
allow list without touching the seed.

**Stream names.** Ordinal by modality — `text_1`, `text_2`, `text_3`, `vision_1` — not model-tier
names like `text_pro`. Tier names bake a vendor lineup into an agent-readable surface and go stale
when an allow list changes, leaving a socket whose name misdescribes what it carries. Ordinals stay
accurate under any list and route the agent to `models.json`, which already publishes each socket's
model and `image_input` flag. That file is the intended discovery path; names should not compete
with it.

`tests/test_llm_console_seed.py` currently asserts `set(accepted) == {"text", "vision"}` and derives
its env from two entries; it needs the new set and both lists.

**Seed values, per stream:**

| field | now | proposed |
|---|---|---|
| `token_budget` | — | 2000000 |
| `budget` | 30 | 1200 |
| `max_tokens` | unset | 32768 |

with `STREAM_TOKEN_HOURLY_MAX=2000000` and `STREAM_HOURLY_MAX=1200` set explicitly in
`.env.example` so both ceilings are visible where an operator looks for them.

**The request cap is a request-rate guard, not a spend guard.** Tokens are the spend limit; 1200/hour
is one request every three seconds, and exists so a hot loop cannot hammer the upstream even when
each call is cheap. The crossover is exact: `2,000,000 / 1200 ≈ 1,667` tokens per turn. Turns larger
than that hit the token quota first, which is the intended behaviour for real work; turns smaller
than that hit the rate cap first, which is the intended behaviour for a spin. Setting it lower — 200,
say — would make the rate cap bind before the token quota for any turn under ~10k tokens, and the
advertised 2M/hour would become a number the agent can never actually reach.
`STREAM_REASONING_ALLOWANCE` stays at 32768: it is added on top of a composed `max_tokens` when
reasoning is on, because the upstream counts reasoning inside `max_tokens`, so a reasoning request
gets 65536 total and the declared 32768 genuinely bounds the response.

**`max_tokens = 32768` is the one number to verify during implementation.** It must not exceed the
output cap of any model in the shipped allow lists, or the upstream rejects every call and the
socket appears bound but always fails — worse than a short answer. Check each permitted model's
output limit, lower the seed to the smallest if any is below 32768, and record what was found.

## Containment

No containment property changes. Every ceiling introduced or raised here lives in the **recorder's
environment**, in another container, unreachable from the agent's: `STREAM_TOKEN_HOURLY_MAX`,
`STREAM_HOURLY_MAX`, `CORE_TOKEN_HOURLY_MAX`, and both allow lists. The console can lower an
allowance and never raise it above the operator's ceiling — the existing property, now applying to
tokens as well as requests.

Headers are still never logged. Streaming changes what the recorder buffers, not what it records.

One invariant-3 amendment in `CLAUDE.md`: the composed-field sentence gains
`stream_options.include_usage`, set by the recorder on streamed **declared-stream** requests rather
than declared by the agent. The "forwards its body verbatim" clause for `core.sock` is untouched —
see above, core needs no body modification to be metered.

**Aggregate spend, stated plainly.** Four sockets at 2M tokens/hour is up to 8M tokens/hour against
the recorder's `OPENROUTER_API_KEY` if the agent saturates all four. That is the operator choosing
a larger number inside a mechanism that already existed, and it is the number to revisit first if
the bill surprises.

## Files changed

| file | change |
|---|---|
| `proxy.py` | HTTP/1.1, handler timeout, chunked SSE relay, delta reconstruction incl. tool calls, usage from the final stream event, charge the registry, core token ceiling |
| `recorder_streams.py` | `token_budget` field, `STREAM_TOKEN_HOURLY_MAX`, token histories, token check in `admit`, `charge`, `stream_options` composition, token fields in `render_state` and the `/llm/sock` README |
| `chassis.py` | classify a core rate-limit refusal as transient |
| `llm_console_seed.json` | one declaration per permitted model; `token_budget`, `budget`, `max_tokens` |
| `.env.example` | uncomment both allow lists; add `STREAM_TOKEN_HOURLY_MAX`, `STREAM_HOURLY_MAX`; document `CORE_TOKEN_HOURLY_MAX` unset |
| `docker-compose.yml` | pass the new recorder variables; `pids_limit` and `mem_limit` on `recorder` |
| `CLAUDE.md` | the two invariant-3 amendments |

## Implementation order

Independently testable phases, in this order:

1. **Keep-alive** — `protocol_version`, handler timeout, recorder resource limits. Verify existing
   non-streamed traffic is unaffected.
2. **Streaming relay** — chunked framing, delta reconstruction, transcript parity with buffered
   responses.
3. **Token accounting** — `include_usage`, usage extraction, console field, ceilings, `charge`,
   state reporting.
4. **Defaults** — seed, `.env.example`, the agreement test.

## Testing

`tests/test_proxy.py`:

- a non-streamed request behaves exactly as before under HTTP/1.1, with an exact `Content-Length`
- two requests on one connection both succeed (keep-alive works)
- a streamed response reaches the client incrementally, chunk-framed, with no `Content-Length`
- transcript reconstruction: content deltas concatenate; `tool_calls` reassemble by `index` with
  split `arguments` joined and `id` / `name` taken from the first fragment carrying them;
  `finish_reason` carried through
- usage is read from the final stream event and appears in the close event
- `stream_options.include_usage` is set on declared-stream streamed requests
- `core.sock` stays byte-verbatim with `CORE_TOKEN_HOURLY_MAX` unset, and gains only
  `stream_options` when it is set
- headers still never appear in the transcript or events

`tests/test_recorder_streams.py`:

- `token_budget` validation, including rejection of booleans and negatives
- `min(declared, ceiling)` clamping, and that a console value cannot exceed the ceiling
- the token window prunes at 3600s and is retained for an undeclared name until its entries age out
- refusal at the token ceiling returns 429 with the token message, distinct from the request message
- overshoot: one request admitted just under the ceiling is allowed and charged in full
- a streamed response ending without usage charges the composed `max_tokens`
- a non-streamed response without usage charges zero
- `render_state` reports token allowance and window usage

`tests/test_llm_console_seed.py`:

- existing tests updated to the new stream set and both allow lists
- every declaration accepted, no rejections
- the seed's model set equals the union of `.env.example`'s allow lists
- every declaration carries `max_tokens` and `token_budget`, each accepted by `validate_declaration`
- the seeded stream count is at most `MAX_STREAMS` (8), so a later allow-list expansion cannot push
  a declaration past the limit and have it silently rejected

`tests/test_chassis_recovery.py`: a core rate-limit refusal is transient — not a tombstone, no model
fallback.

`scripts/verify_container.sh`: a streamed completion over a declared socket returns incrementally
and its usage lands in `events.jsonl`.

## Rollout

`.env.example` is not read at runtime; the live `.env` governs the running stack. Recorder changes
apply by editing `.env` and restarting the **recorder** — it polls the console every 5 seconds and
rebinds sockets. The agent container need not be recreated.

The seed is consulted only when `/llm/console/console.json` is absent, and the entrypoint writes it
once. **A running stack has already been seeded**, so a new seed file changes nothing there: the
console on the volume is the agent's to retune or remove, and no component rewrites it. Applying new
defaults to a live lineage means editing the console on the volume directly, or accepting that they
land only for a fresh stack.

Do not recreate the agent container to force it — `/work` is tmpfs and everything the lineage built
would be destroyed.

## Risks

- **Streaming touches the transcript path**, which the viewer and stage both depend on. Reconstruction
  parity is the highest-value test in this spec.
- **Keep-alive changes HTTP semantics for every existing client**, including the chassis. Any response
  path with wrong framing hangs a connection instead of erroring visibly. Phase 1 is separate for
  exactly this reason.
- **The handler timeout interacts with keep-alive.** `BaseHTTPRequestHandler.timeout` applies to the
  socket, and the default `handle_one_request` sets `close_connection` when it fires. Verify that path
  cannot emit a partial or malformed response into a connection a client is still reading — a timeout
  landing mid-stream is the case to test, since the streamed path writes chunk framing incrementally.
- **`max_tokens = 32768` may exceed a permitted model's output cap**, breaking that socket entirely.
  Verify per model before shipping the seed.
