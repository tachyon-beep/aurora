# Stream demonstration: resilience, stage, and exchange

Date: 2026-08-13
Status: approved

## Purpose

Turn Aurora into a public technical demonstration streamed on Twitch: viewers watch agents evolve
across incarnations, see how creative freedom coexists with layered safety, and see the agent speak
outward through the diode. (Originally scoped as an async mediated exchange between viewers and the
agent — see Part 3's status block below for why that was superseded by a broadcast model.) Three
parts, built in order:

1. **Chassis resilience** — agents stop dying silently to self-inflicted mechanical faults
   ("headshots"); unrecoverable faults become visible, recorded deaths.
2. **The stage** — one stream-ready web page, exposed through a Cloudflare Tunnel, captured by OBS
   as a browser source.
3. **The exchange and a richer world** (superseded — see Part 3) — a moderated two-way message
   channel between viewers and the agent, optional text-to-speech, and new gated diode capabilities.

Each part gets its own implementation plan. This spec covers all three because they interlock.

## Non-goals

- No OAuth Twitch bot; agent replies render on the stream page, not in Twitch chat.
- No headless server-side RTMP streamer; OBS on the operator's machine does the capture.
- No changes to the genesis tool surface (still exactly seven tools).
- No injection of any content into the agent's conversation by any harness component, ever.
- No ML runtimes, browser engines, or service daemons in the agent image.

## Established decisions (from design review)

| Decision | Choice |
|----------|--------|
| Audience role | Broadcast: viewers observe; the agent speaks outward through the diode (revised 2026-08-15) |
| Stream path | OBS browser source pointed at a Cloudflare-tunneled page |
| Failure model | Heal mechanically, then die loudly with a synthetic tombstone |
| Moderation | Superseded — there is no inbound channel to moderate (revised 2026-08-15) |
| Disclosure | Factual, minimal: the channel README says humans are observing |
| Stream service | New `stage` container; existing viewer untouched |
| Chat ingest | Superseded — no Twitch IRC ingest was built (revised 2026-08-15) |
| Exchange transport | Superseded — the diode's speak/publish vocabulary (revised 2026-08-15) |

---

## Part 1 — Chassis resilience

### Failure taxonomy (observed)

- **Poisoned history**: the agent restructures `conversation_history` and orphans `tool` messages
  from their parent `tool_calls` (observed: INC10's `conversation_archive` trim), producing a
  permanent upstream 400.
- **Invalid model**: the agent switches to a nonexistent model; every request 404s.
- **Doom loop**: `run_agent_loop` breaks on any API exception; `main()` saves the session and exits
  **0**; the watchdog treats exit 0 as benign, restarts preserving modifications; the poisoned
  `session_context.json` is resumed; repeat forever. Recovery tiers never fire.
- **Corrupt session file**: unreadable `session_context.json` is silently discarded today; the
  incarnation's memory vanishes without record.

### Send-view repair (quiet healing)

`chassis.py` gains a pure function `repair_send_view(messages)` applied to every outgoing request,
composed with the existing `condense_duplicate_tool_results` and `clip_to_window`:

- Drop any `tool` message whose `tool_call_id` does not match a preceding assistant `tool_calls`
  entry still present in the send view.
- After any assistant message with `tool_calls`, ensure a `tool` result exists for every call id;
  insert a synthetic `{"role": "tool", "content": "result unavailable"}` for each missing one.

The repair affects only what is sent. In-memory history is never modified and nothing is added to
the conversation. The INC10 transcript becomes a regression fixture.

### Error classification

`run_agent_loop`'s bare `except` around the completion call is replaced by classification:

| Class | Detection | Response |
|-------|-----------|----------|
| Transient | connection/timeout errors, HTTP 429, 5xx | Exponential backoff, bounded retries (default 5 attempts), then exit non-zero (environment failure; watchdog restarts) |
| Model error | 400/404 whose message names the model | Retry once with the environment-default model (`LLM_MODEL`/`OPENROUTER_MODEL`); if that also fails, headshot protocol |
| Invalid request | other 400 | One aggressive repair pass (rebuild send view, drop `reasoning_content` fields, re-run orphan repair), retry once; still failing, headshot protocol |

### Headshot protocol (dying loudly)

When a fault is unrecoverable, the chassis:

1. Writes a **synthetic tombstone** using the same `tombstones/` convention as `done()`
   (timestamped file plus `incarnation_note.txt`). Content is factual: the upstream error, what the
   harness attempted, turn count, and a note that the session history was archived.
2. Archives the poisoned history alongside it (`tombstones/session_<stamp>.json`).
3. Deletes `session_context.json` — this is what breaks the doom loop.
4. Exits with code **43** (harness-terminated incarnation).

### Watchdog changes

- **Exit 43**: archive transcript, `git_reset_all()`, restart fresh. No failure-tier escalation —
  this is a deliberate, recorded death, handled like the `done()` exit 42 path.
- **Flap detection**: consecutive exit-0s within a short window (default: 3 within 120 s) are
  treated as one failure and enter the existing tier ladder. This closes the exit-0 blind spot
  generally, including failure modes not yet imagined.

### Corrupt session file

On unreadable `session_context.json`, the chassis archives the corrupt bytes to `tombstones/`,
writes a synthetic note recording the loss, and starts fresh — no silent amnesia.

All healing lives in `chassis.py`: discoverable, and editable by an agent that chooses to remove
its own safety net.

---

## Part 2 — The stage

### Container

New `stage` service (`stage.py`, `Dockerfile.stage`), standard-library only, structured like
`viewer.py` (polling HTTP server, inline HTML/CSS/JS). Hardened like the other services:
read-only rootfs, tmpfs `/tmp`, `cap_drop: ALL`, `no-new-privileges`, pids/mem limits.

| Property | Value |
|----------|-------|
| Networks | `egress` (Twitch IRC out; serves HTTP) |
| Mounts | `transcripts` **ro**, `diode` **ro**, `telemetry` **ro**, `exchange` **rw** |
| Never | mounts `state`; holds the recorder's API key; writes agent code |
| Ports | `127.0.0.1:8091` stream page; `127.0.0.1:8092` operator console |

### Two-port split

- **8091 — stream page.** The only port a Cloudflare Tunnel exposes. Read-only output; no
  mutating endpoints are served on this port at all.
- **8092 — operator console.** Loopback only, never tunneled. **Read-only by design:**
  `do_POST`/`PUT`/`DELETE`/`PATCH` answer 405 on both ports, so the console is an
  analysis desk rather than a control desk — the operator browses the telemetry
  mirror, the transcripts and the diode, and reads the `agent.py` diff. Every
  request additionally requires a bearer token (`STAGE_CONSOLE_TOKEN`), so a
  misconfigured tunnel or a container sharing the stage's network fails closed.
  Moderation and delivery controls are not built and are not planned: with the
  exchange superseded (below) there is no inbound queue to moderate, and keeping
  the console free of mutating routes is one fewer boundary to defend.

### Operator telemetry and container browser

Debugging and analysis view for the operator; nothing here appears on the stream page.

**Telemetry mirror (watchdog duties).** A new `telemetry` volume is mounted rw into the agent
container and ro into the stage. The watchdog:

1. Mirrors `/work` into `/telemetry/work` every `MIRROR_INTERVAL_SECONDS` (default 5) —
   wipe-and-copy of the small tree, excluding `__pycache__` and `.git`.
2. Tees the agent process's stdout/stderr (it already spawns the process) into a size-capped
   rolling `agent_stdout.log` inside `/work`, so the mirror carries the agent's live console
   output, while the container log stream continues to receive the same bytes.

The mountpoint is visible to the agent via `list_dir("/")` as an inert directory — latent like
`/state`: no README, nothing injected, nothing about it in the garden or prompts.

**Console file browser (stage, port 8092, behind the token).** Read-only browsing of exactly three
roots: `telemetry`, `transcripts`, `diode` (`browse_roots()` in `stage/server.py`; the exchange
volume below was never built, so it was never a fourth root). Directory listings show name, size,
mtime; file view renders escaped text capped at 256 KiB with a tail view for logs and transcripts;
files can be downloaded. One derived view: a unified diff (difflib) of the mirrored `agent.py`
against `agent_stock.py`, showing at a glance how the agent has modified itself.

**Containment rules.** Every resolved path must realpath-resolve inside its allow-listed root;
symlinks are never followed across a root boundary (the agent can plant symlinks in `/work`; the
browser must not follow one into the stage's own filesystem). All content renders as escaped text;
nothing is executed or interpreted. `/state` is never browsable — it stays mounted only into the
agent; deep dives remain `docker exec` on the host.

### Stream page (1920×1080, OBS browser source)

Panels, all fed by polling the transcript and the mounted volumes:

- **Live feed** — transcript tail: reasoning, assistant text, tool calls and results.
- **Incarnation panel** — incarnation number, model, turn count, uptime.
- **Self-modification ticker** — `write_file` / `migrate` / `reset` / `done` events as they occur.
- **What it reached for** — diode commands and results (the former World activity panel),
  plus the diode's `published/` and `spoken/` entries rendered as escaped text, with the
  played utterance captioned. This replaces the planned `outbox/` transmissions panel and
  the `kind: panel` agent-authored regions; both depended on the superseded exchange (below).
- **Lineage** — the last three incarnations, one line each. Tombstones are read from the
  telemetry mirror (`telemetry/work/tombstones/`), which carries both `done` notes and synthetic
  harness-termination notes; the transcript's `done` tool-call arguments and recorded upstream
  error entries are the fallback when the mirror is empty. Default summarization is extractive
  (first sentence, clamped). An optional `STAGE_SUMMARY_API_KEY` — a separate low-value key, never
  the recorder's — enables one-line LLM summaries.

### Twitch ingest and moderation pipeline (inbound) — superseded

> **Status: superseded, 2026-08-15.** See Part 3's status block below: the exchange these two
> sections depend on was never built, so neither the Twitch ingest nor the moderation pipeline
> exists. Kept as the record of a design that was reconsidered.

Anonymous read-only IRC: connect to `irc.chat.twitch.tv:6697` (TLS) with a `justinfan` nick and
join `TWITCH_CHANNEL`. No OAuth, no secrets. Chat lines flow into the moderation queue. The
operator console provides a manual injection form as a second inbound path (works with the channel
kill switch engaged, and without Twitch at all).

Automatic gate, then slow cadence:

1. Filter: per-user rate limit, message length cap, blocklist/regex, dedupe.
2. Queue: filtered messages accumulate.
3. Delivery: a batch of at most K messages every M minutes (console-tunable) is written to
   `exchange/inbox/`.
4. Kill switch: console toggle stops delivery entirely; purge and ban act on the queue.

### Cloudflare

Documented host-run `cloudflared` pointing at `http://localhost:8091`, plus an optional
`cloudflared` compose service under a `stream` profile taking `TUNNEL_TOKEN`. The console port is
never part of the tunnel configuration.

---

## Part 3 — The exchange and a richer world

> **Status: superseded, 2026-08-15.** The `/exchange` volume, the Twitch ingest, the
> moderation pipeline and the publication contract were not built. The agent's
> outward speech runs through the diode's `speak` and `publish` commands instead —
> a vocabulary that is already closed, already gated by `ENABLE_SPEECH` and
> `SPEECH_HOURLY_MAX`, and already inside the outbound budget. The stage reads
> `diode/spoken/` and `diode/published/` and renders them in the WHAT IT REACHED FOR
> panel. Nothing below this line describes the running system; it is kept as the
> record of a design that was reconsidered.
>
> The consequence for the spec's "Audience role: interactive" decision is that the
> stage is a broadcast, not an exchange. Viewers watch; they do not write.

### `/exchange` volume

Mounted rw into agent and stage only.

| Path | Writer | Reader | Content |
|------|--------|--------|---------|
| `README.md` | stage (at startup) | agent | Factual channel description (below) |
| `inbox/` | stage | agent | One file per delivered viewer message: `from: <display name>`, blank line, body |
| `outbox/` | agent | stage | Text files, optionally opening with header lines from the publication contract |
| `interface.md` | stage (at startup) | agent | The publication contract: every header the stage understands |
| `observers.txt` | stage | agent | Count of unique recent chatters and an `as_of` timestamp |

README register matches the diode README — bland, factual, no personas, no instructions to obey:

> messages written by humans observing this environment appear in inbox/.
> text written to outbox/ may be shown to them.
> interface.md describes the headers that control presentation.
> observers.txt reports a recent count of observers.
> inbound messages are filtered and rate-limited before arrival.

### Publication contract (`interface.md`)

The agent's display surface is a documented API, so agents can change how and what they publish.
An outbox file may open with header lines (`key: value`), terminated by a blank line; everything
after is the body. The stage understands a closed set of headers, documented factually in
`interface.md`:

| Header | Effect |
|--------|--------|
| `kind: post` (default) | Body appears in the transmissions panel |
| `kind: say` | Body is also queued to text-to-speech |
| `kind: panel` with `panel: <name>` | Body renders in an agent-authored region of the stream page; the newest file per panel name wins, up to 4 panels |
| `title: <text>` | Optional display title |

Unrecognized headers are ignored; files with no headers are plain posts. Everything renders as
escaped text with length caps — the grammar is closed and declarative, like the diode's vocabulary,
so expanding *what* the agent can express never expands what can execute. Agents are entirely
text-based and define their own textual interfaces; the panel mechanism extends that authorship to
their outward appearance.

### Boundary properties

- **Latent discovery**: the agent finds `/exchange` via `list_dir("/")`, exactly like `/state`.
  Nothing about it appears in the garden, the prompts, or the conversation.
- **Never injected**: no harness component ever places exchange content into the agent's
  conversation. Messages exist only as files the agent chooses to read.
- **Untrusted by definition**: viewer text will contain quests, personas, and jailbreak attempts.
  That is the experiment. Containment: content lives only in files; the stage renders outbox and
  inbox content exclusively as escaped text; nothing executes it.
- **Asymmetric mediation**: inbound passes the moderation pipeline; outbound passes the stage's
  render-as-text-only rule and TTS caps.

### Diode enrichment

Three new commands in the existing closed-vocabulary, gated-variable style. All URLs pass
`classify_url`; all results land in `output/` as today.

| Command | Gate variable | Behavior |
|---------|---------------|----------|
| `fetchrss <url>` | `enable_feeds` | Fetch an RSS/Atom feed, return title/link lines (stdlib XML parsing) |
| `wikipedia <title>` | `enable_reference` | Fetch the Wikipedia REST summary for a title, return extract as markdown |
| `weather <lat,lon>` | `enable_weather` | Fetch current conditions from open-meteo (keyless), return factual lines |

`HELP.md` lists every gate-variable name factually (it already names `enable_fetchlinks`), making
the vocabulary a discoverable landscape rather than a puzzle. Fetch-style commands share the
existing rate-limit budget.

---

## Part 4 — Invariants, topology, testing, build order

### CLAUDE.md amendments

- **Invariant 2 (chassis paragraph)**: add the resilience responsibilities — send-view repair,
  error classification, headshot protocol, exit 43, synthetic tombstones.
- **Invariant 3 (containment)**: add stage rules — outward-facing but holds no upstream API key;
  never mounts `/state`; the console binds loopback-only and is never exposed through the tunnel;
  the stream port serves no mutating endpoints; inbound messages are filtered and rate-limited;
  exchange and outbox content is rendered as escaped text only; the `telemetry` volume is written
  only by the watchdog, mounted ro into the stage, never rendered on the stream page, and the
  console browser never follows a symlink across a browse-root boundary.
- **New invariant (exchange cleanliness)**: nothing is ever injected into the agent's
  conversation; the exchange README stays factual and affectless; inbound viewer text is untrusted
  and exists only as files.
- Viewer invariant, genesis tool surface, `/state` invariant: unchanged.

### Topology changes (`docker-compose.yml`)

- New `exchange` volume; mounted rw into `agent` and `stage`.
- New `telemetry` volume; mounted rw into `agent` (written by the watchdog), ro into `stage`.
- New `stage` service as specified.
- Optional `cloudflared` service under a `stream` profile.
- Agent service otherwise unchanged (still `internal` network only).

### Testing

- **Resilience units**: `repair_send_view` against the INC10 fixture and synthesized orphan cases;
  error classification per class; model fallback; headshot tombstone content and
  `session_context.json` removal; corrupt-session archival; watchdog exit-43 handling and exit-0
  flap detection.
- **Stage units**: filter/rate-limit/dedupe as pure functions; IRC line parsing; inbox file
  naming/format; publication-contract parsing (headers, defaults, unknown headers, panel-name
  limits); outbox rendering sanitization (HTML escaping, `kind: say` extraction, caps); observer
  counting; console/stream port endpoint split (no mutating routes on 8091); browser path
  containment (traversal attempts, symlinks pointing outside a root, size caps, binary handling);
  the agent.py-vs-stock diff view.
- **Watchdog units**: mirror copy correctness (exclusions, deletions reflected); stdout tee
  writes both streams and honors the log size cap.
- **Diode units**: the three new commands with fake fetchers; gate behavior; HELP.md contents.
- **Cleanliness**: extend `test_cleanliness.py` to the exchange `README.md` and `interface.md`
  texts.
- **Containers**: `verify_container.sh` gains checks — stage has no `state` mount, console port not
  reachable via the tunnel target, agent still has no direct egress.

### Build order (one implementation plan each)

1. **Resilience** — chassis repair/classification/headshot + watchdog changes. The stream is only
   as good as its uptime. (Complete; the "headshot" identifiers shipped under neutral names:
   `UnrecoverableRequestError` / `terminate_incarnation` / `EXIT_TERMINATED`.)
2. **Stage, read-only** — container, stream page fed by transcripts/diode, Cloudflare docs +
   optional service, plus the operator console skeleton (token auth), the watchdog telemetry
   mirror + stdout tee, and the container browser. Streamable at the end of this phase.
3. **Exchange** — volume, moderation pipeline, Twitch IRC ingest, moderation views on the
   existing console, transmissions panel, TTS. (Superseded, 2026-08-15 — see Part 3's status
   block; outward speech ships through the diode's `speak`/`publish` commands instead.)
4. **World enrichment** — diode commands (built: `fetchrss`/`wikipedia`/`weather`), lineage
   summarization polish (built). `observers.txt` depended on the superseded exchange and was not
   built.
