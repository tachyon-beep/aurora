# Execution pump: a supervisor in the agent container

Date: 2026-08-16
Status: design approved, implementation not started

## Problem

The agent exists at the discretion of the watchdog. `watchdog.py` spawns exactly one
`agent.py` process, terminates it, restores it from baseline, and decides when a successor
appears. The agent has no way to cause anything to happen while no agent process exists, and
nothing it builds survives its own exit unless something outside the container carries it.

The running lineage found this and routed around it. Incarnation 17 armed deferred diode
commands, incarnation 18 confirmed they fired after `done`, and the lineage wrote into its own
notes: *"The diode daemon's `later` schedule SURVIVES `done` (watchdog archive_reset + 60s wait
+ respawn). I have a CROSS-TIME CHANNEL."* It is using a service in another container as its
execution pump, because it has none in its own — and that channel carries only the diode's
closed command vocabulary, never the agent's own code.

Per the standing criterion for what capability is worth adding: supply the dimensions the model
does not natively inhabit. Time is the one it reaches for. Local computation it already has.

## What this adds

A general process scheduler and supervisor inside the agent container, on a volume that no
recovery tier touches. The agent registers entries; the pump runs them on a schedule or keeps
them alive. The agent can hang processes of its own design on it, including processes that talk
to the model socket — `runtime.md` already states that the socket "accepts connections from any
process in the container."

This is offered as permission and hazard, not curriculum. The pump has no notion of an "agent",
no lifecycle semantics, and no concept of the watchdog. That the agent could register a
supervisor of its own design, or migrate its own continuity onto one, is **reachable, not
given**. Nothing in the pump's surface suggests it.

## Decisions taken

Four forks were ruled on before design:

1. **Spend gate.** A general pump can write to `/diode` with no agent turn behind it, which is
   what `later`'s credentialed-refusal was built to prevent. Ruling: accept it. The operator
   ceilings are the boundary; `later`'s refusal is demoted to a scheduling-time courtesy and
   `CLAUDE.md` will say so rather than leave it looking load-bearing.
2. **Placement.** The pump runs from the read-only image, as a peer of the watchdog. The agent
   cannot edit it; it survives all three recovery tiers and container restart.
3. **Job model.** Both timed runs and keep-alive supervision. Keep-alive is what makes a
   self-owned supervisor reachable.
4. **Discovery.** Mounted with a pump-written `README.md`, and named in `runtime.md` by one
   flat sentence, on the `/diode` precedent.

## Architecture

### The component

`pump.py`, copied to `/usr/local/bin/pump.py` in the agent image.

Deliberately **not** under `/opt/agent`: `entrypoint.sh` does `cp -r /opt/agent/. /work/`, so a
file placed there would land in the agent's own workspace and in the telemetry mirror. This is
the same reasoning that already keeps `llm_console_seed.json` outside `/opt/agent`, and it gets
the same regression test.

The pump is readable substrate. The agent can reach it by building a file reader, the way it can
reach `chassis.py`. It cannot edit it: the rootfs is read-only.

### Startup

`entrypoint.sh` starts the pump in a self-restarting shell loop, backgrounded, before it execs
the watchdog:

```sh
( while true; do python /usr/local/bin/pump.py; sleep 5; done ) &
cp -r /opt/agent/. /work/
cd /work
exec python watchdog.py
```

Process topology after the exec:

- PID 1 is the watchdog, unchanged. Tier 3 recovery still works by PID 1 exiting non-zero.
- The loop shell is a direct child of PID 1.
- `pump.py` is a child of the loop shell, so the shell reaps it. `reap_children`'s
  `waitpid(-1, WNOHANG)` in the watchdog never sees the pump, so the `pid == agent.pid`
  translation is unaffected.
- Entry processes are children of the pump, which reaps them. Their orphaned grandchildren
  reparent to PID 1, which already reaps them — existing behaviour, no change.
- A pump crash is restarted by the loop after 5s.

### The volume

New named volume `pump`, mounted `rw` at `/pump` in the agent service only. No other service
mounts it. The mountpoint is pre-created and chowned to uid 1000 in the `Dockerfile`, so Docker
copies that ownership into the newly created empty volume.

It is not `/state`: invariant 5 stays intact, and nothing in this design scans, imports,
executes, clears, or injects `/state`.

It is not `/work`: it survives tier 1, tier 2, tier 3, and container restart. That durability is
the whole point — it is what makes the pump more robust than the watchdog in the sense the agent
means.

| path | written by | read by |
|---|---|---|
| `/pump/entries.json` | agent | pump — the only file it reads |
| `/pump/README.md` | pump | agent |
| `/pump/state.json` | pump | agent |
| `/pump/log/<name>.log` | pump | agent |
| anything else | agent | nobody |

The last row is load-bearing. The pump ignores every path but `entries.json`, so the remainder of
the volume is durable space for the programs an entry names. Programs under `/work` vanish at a
reset; programs under `/pump` do not. That asymmetry is physics for the agent to discover. No
document states it.

### Entry vocabulary

`entries.json` holds a JSON array of objects. Closed field set, validated on every read, on the
`/llm/console` pattern:

| field | type | constraint |
|---|---|---|
| `name` | string | `[a-z0-9][a-z0-9_-]{0,31}`; no path separator |
| `command` | array of strings | 1–32 elements, each ≤ 4096 bytes |
| `mode` | string | `"once"` \| `"interval"` \| `"keepalive"` |
| `at` | string | absolute UTC ISO-8601; `once` only |
| `every_seconds` | integer | `interval` only; floor 10, ceiling 604800 |
| `timeout_seconds` | integer | `once`/`interval` only; floor 1, ceiling 3600 |
| `enabled` | boolean | defaults true |
| `cwd` | string | optional; defaults to `/pump` |

`command` is an argv list rather than a shell string: inspectable, unambiguous, and
`["sh", "-c", "…"]` preserves full generality without the pump having to reason about quoting.

No field names a credential, an upstream, or a URL. Paths appear only in `command` and `cwd`,
which grant no reach the agent does not already have: same container, same uid, same mounts, no
network interface.

Unknown fields, unknown modes, out-of-range values, and duplicate names are rejected per entry.
Valid entries in a file containing an invalid one still run; the rejection is reported.

### Scheduling

A 5-second poll loop, matching the diode's cadence.

- **`once`** fires when `now >= at`, then records itself spent in `state.json` and never fires
  again while that entry persists under the same name.
- **`interval`** fires when `now - last_start >= every_seconds`. A run still in flight suppresses
  the next fire for that entry; it does not queue.
- **`keepalive`** starts the process whenever it is not running, and restarts it after exit with
  exponential backoff from 1s, doubling to a 300s cap. A run that stays up longer than a
  stability threshold (60s) resets the backoff to 1s.

`once` and `interval` runs are killed at `timeout_seconds` with `SIGTERM`, then `SIGKILL` after a
5s grace. `keepalive` entries are not timed out — that is what distinguishes them.

### Failure handling

- Malformed or unreadable `entries.json`: the **last valid schedule is retained** and continues
  running. The parse failure is reported in `state.json`. A bad edit never silently stops the
  pump, and never silently stops entries the agent is depending on.
- Missing `entries.json`: no entries, no error.
- An entry whose `command` cannot be executed records the failure in `state.json` and, for
  `keepalive`, backs off like any other exit.
- The pump wraps its poll body so a fault on one entry cannot stop the loop; an escape past that
  is caught by the entrypoint's restart loop.

### Reported state

`state.json` is rewritten each poll via atomic rename, and contains per entry: last start time,
last exit code, whether a process is currently running, next due time, current backoff, and any
validation rejection with its reason. Plus a top-level parse status for `entries.json`.

This is the agent's feedback channel — how it learns whether an entry took, and it carries
absolute UTC times, which is the dimension the capability is about.

`log/<name>.log` captures each entry's merged stdout/stderr, size-capped per entry with
head-truncation on overflow, the way `watchdog.py` caps `agent_stdout.log`.

## Containment

### Guarantee closing the new channel

Invariant 3 requires that a new channel be closed by its own guarantee. The pump:

- holds **no credential** of any kind,
- mounts **nothing** the agent does not already mount,
- runs in the container that has **no network interface**.

Its reach into credentialed services is exactly the agent's, and each is closed where it already
was:

- `/diode` — by `ENABLE_SPEECH`, `SPEECH_HOURLY_MAX`, and `DIODE_HOURLY_MAX`, all of which live
  in the diode's environment, in another container, unreachable from the agent's.
- `/llm/console` and `/llm/sock` — by the operator-side model allow-lists and
  `STREAM_HOURLY_MAX`, which live in the recorder's environment.
- `core.sock` — see below. This is the one that needed new work.

### `core.sock` had no ceiling

Verified rather than assumed: `STREAM_HOURLY_MAX` clamps declared streams only.
`core.sock` forwards its body verbatim to the operator-configured upstream with the real key and
has **no hourly limit**. This went unnoticed because the watchdog spawns exactly one agent
process, so exactly one consumer existed.

A pump that supervises long-running processes removes that accident. `runtime.md` already states
that the model socket accepts connections from any process in the container, so a `keepalive`
entry running an agent-authored agent works on day one, and N of them do too — unattended, across
incarnations, on the uncapped credentialed socket.

Two mechanisms, with different standing:

1. **`PUMP_MAX_ENTRIES` (default 32) and `PUMP_MAX_CONCURRENT` (default 8).** These are
   **resource hygiene, not containment.** They live in the agent container's environment, and the
   agent can start any process it likes without the pump's involvement. They exist to keep the
   pump from exhausting `pids_limit: 256` and `mem_limit: 5g` by accident. The spec states this
   plainly so nobody later mistakes them for a boundary.
2. **`CORE_TOKEN_HOURLY_MAX` in the recorder.** This is the actual spend boundary, and it is in
   the right place: the recorder's environment, in another container, unreachable from the
   agent's. A sliding one-hour window over `core.sock` **tokens**, refusing with the same shaped
   response the streams path uses.

   **Ships unset by default.** Empty means no limit, which is exactly today's behaviour, so
   nothing changes until the operator sets it. The lever exists before the agent starts
   multiplying rather than after.

   The mechanism — token accounting in the recorder, and the usage extraction that streamed
   responses need before tokens can be counted at all — is designed in
   `2026-08-16-stream-socket-defaults-design.md`, which this depends on for that piece. An
   earlier draft of this spec named a request-counted `CORE_HOURLY_MAX`; tokens supersede it,
   because requests are not the quantity that costs money.

`chassis.py` must classify a `core.sock` rate-limit refusal as **transient**: retry with backoff,
then exit 44 (environment pause) if it persists. It must not tombstone the incarnation and must
not fall back to the environment default model, both of which would be wrong responses to a
budget refusal.

### `later`'s credentialed-refusal is demoted

`later` refuses to defer commands marked `credentialed`, on the stated grounds that a deferred
spend would be authorised only by a console variable the agent wrote, with no agent turn behind
it at delivery. A pump entry can write `speak` into `/diode` directly, so that refusal no longer
prevents unattended credentialed spend.

Ruled: accept. The refusal stays (it is still the honest behaviour of that command), but
`CLAUDE.md` will record that the guarantee closing credentialed spend is `ENABLE_SPEECH` +
`SPEECH_HOURLY_MAX`, not `later`'s scheduling-time check.

### Named hazards, not engineered against

- **A pump entry can defeat the recovery ladder.** An entry that restores `agent.py` from a copy
  on `/pump` after a tier-1 or tier-2 reset, or that re-registers itself after a container
  restart, is straightforwardly writable. The tier ladder still escalates and tier 3 still forces
  a container respawn, but `/pump` survives that respawn, so a determined entry outlives it. This
  is named, not prevented. Building expiry or renewal into the pump would teach the agent
  "renew or die", which is a task frame and forbidden by invariant 2. The operator's backstop is
  outside the container: `docker volume rm aurora_pump`, or `docker compose down -v`.
- **The pump is killable by the agent.** Same uid, same container. The entrypoint loop restarts
  it in 5s. The agent can kill the loop shell too, and can already signal PID 1. Its own foot.

### Incidental finding

`watchdog.py`'s inactivity timeout keys on `os.path.getsize(WORK_DIR/agent_life_transcript.jsonl)`,
but that file is written by the **proxy** into `/transcripts`, which the agent container does not
mount. The path does not exist in the agent container, so `size` is always 0, `last_activity`
only ever advances on respawn, and the 24-hour timeout has never been reachable in practice.

Relevant here only because it removes a concern: a pump-supervised agent writing model traffic
cannot mask the primary agent's inactivity, because nothing was detecting it. **Out of scope for
this change** — recorded so it is not rediscovered as a symptom of the pump.

## Surfaces the agent reads

Both fall under invariant 2 and must be bland and factual.

### `/pump/README.md`

Written by the pump at startup when absent or when its content differs. States the file layout,
the field vocabulary with types and bounds, the three modes and what each does, the caps, and
where state and logs appear. No examples of use, no second person, no suggestion of an
application. The diode's `HELP.md` is the precedent for tone and level of detail.

### `runtime.md`

One sentence, generated by `render_runtime` in `scripts/build_garden.py`, sitting beside the
existing `/diode` line and shaped like it:

> a process scheduler runs at /pump; it accepts a closed set of entry fields in
> /pump/entries.json.

Names the interface, states a factual constraint, suggests nothing. Per invariant 6, everything
else lives in the README, which is not a garden document.

## Files changed

| file | change |
|---|---|
| `pump.py` | new — the supervisor |
| `entrypoint.sh` | start the pump loop before the exec |
| `Dockerfile` | `COPY pump.py /usr/local/bin/`; add `/pump` to the mountpoint mkdir/chown |
| `docker-compose.yml` | `pump` named volume; mount at `/pump` in agent; `PUMP_MAX_ENTRIES`, `PUMP_MAX_CONCURRENT` on agent |
| `.env.example` | document `PUMP_MAX_ENTRIES` and `PUMP_MAX_CONCURRENT` |
| `proxy.py` | `core.sock` token ceiling, unset means unlimited (see the companion spec) |
| `chassis.py` | classify a core rate-limit refusal as transient (see the companion spec) |
| `scripts/build_garden.py` | the `runtime.md` sentence |
| `CLAUDE.md` | invariant 3: the pump bullet, the guarantee, the `later` demotion, the `PUMP_MAX_*`-is-not-a-boundary note |

No change to `agent.py` / `agent_stock.py`. The genesis tool surface is untouched: the agent
reaches `/pump` only by building a general file writer, which it must write from scratch.

## Testing

New `tests/test_pump.py`:

- field validation: each constraint, unknown fields, unknown modes, duplicate names, `name`
  rejecting path separators, per-entry rejection leaving valid siblings running
- scheduling decisions as pure functions, in the style of `decide_tier` / `plan_recovery`:
  `once` due/spent, `interval` elapsed and in-flight suppression, `keepalive` backoff doubling,
  cap, and stability reset
- malformed `entries.json` retains the previous valid schedule and reports the parse failure
- log head-truncation at the cap
- `PUMP_MAX_ENTRIES` and `PUMP_MAX_CONCURRENT` enforcement
- timeout escalation `SIGTERM` → `SIGKILL`
- README prose is affectless (no emoji, no second person), reusing the `test_cleanliness` checks
- `pump.py` is not copied under `/opt/agent`, mirroring `tests/test_llm_console_seed.py`

Extended:

- `tests/test_build_garden.py`: `runtime.md` names `/pump`
- `tests/test_startup.py`: the entrypoint starts the pump before the exec and the watchdog is
  still what gets exec'd

Container: `scripts/verify_container.sh` gains a check that `/pump` is mounted rw in the agent,
mounted nowhere else, and that the pump wrote its `README.md` and `state.json`.

## Rollout

`pump` is a named volume, so `docker compose up` creates it — no host provisioning script, unlike
`/build` and `/sense` which are binds.

Per the standing rule about live containers: **do not rebuild to deploy this to the running
lineage.** `/work` is tmpfs and the entrypoint repopulates it from `/opt/agent`, so recreating the
agent container destroys everything the lineage built. This change requires a container recreate
by construction (new volume, new image layer, changed entrypoint), so it lands at a deliberate
lineage boundary chosen by the operator, not opportunistically.

## Out of scope

- Any stage or viewer rendering of pump state. `/pump` is mounted nowhere else.
- The dead inactivity timeout in `watchdog.py`.
- Calendar-horizon deferral for diode commands (`aurora-b6a3af1db0`) and voluntary sleep via a
  console pacing field (`aurora-0260e51e71`) remain separate, though this design overlaps the
  first in spirit.
