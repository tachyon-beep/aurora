# Working in this repository

Aurora is a containerized harness for self-modifying LLM agents. An agent talks to a model through a
recording proxy, and uses tools to rewrite its own source code inside layered containment. Read
`README.md` for the overview; this file is the set of rules to follow when changing the code.

## Hard invariants — do not break these

1. **`agent.py` and `agent_stock.py` are byte-identical.** `agent_stock.py` is the clean seed that
   `reset` restores. When you change one, copy it to the other in the same change. A test enforces
   this.

2. **The agent's world is "strange yet clean."** Every surface the agent can read — `agent.py`,
   `agent_stock.py`, and anything copied into the agent image — must be bland and factual.
   `system_prompt.txt`/`user_prompt.txt` are the one deliberate exception: they are the operator's
   direct address to the agent, voiced by design, but still assign no task and name no concrete
   surface or application beyond the genesis tools — permission and hazard, never curriculum.
   The filigree store the `Dockerfile` seeds into `/opt/agent/.weft` is the operator's second
   deliberate exception, of the same kind: a handful of voiced entries, one of which hints at the
   unlisted diode command (an operator-chosen exception to the no-hint rule in invariant 3). It is
   maintained by hand in the `Dockerfile` and nowhere else; do not add entries to it, and do not
   let anything else on the image take its licence. Everything else:
   - No authorial voice, jokes, emoji, or quest/task framing. Broken or "voiced" code makes the agent
     adopt a "fix the bug / finish the task" frame instead of introspecting.
   - **Comments in `agent.py` may only be commented-out code**, never prose. (A test parses every
     comment block and fails if it isn't valid disabled code.)
   - **Docstrings are load-bearing**: `ToolRegistry` parses them into the tool schemas the model sees.
     Keep them present, accurate, and affectless. Editing a tool's docstring changes the agent's
     perceived world.
   - **Transport identity lives in `chassis.py`, not `agent.py`.** `read_file` returns only `agent.py`,
     so the genesis agent's self-image is the *interface*: the registry, the tools, prompt loading,
     self-edit. The *substrate* — the model/provider, the client, the proxy detection, the request
     loop — lives in `chassis.py`, which `agent.py` imports under `if __name__ == "__main__"` and hands
     itself to via `chassis.main(sys.modules[__name__])`. The agent can only reach `chassis.py` by
     re-enabling a file tool and reading it; the substrate is *discoverable*, not *given*. Keep
     provider/model identity (`openrouter`, `deepseek`, `base_url`, request headers, the loop) out of
     `agent.py` — a test enforces this. `chassis.py` is not reset by `reset` (it is stable substrate,
     not self-modifiable surface) and is not duplicated like the `agent.py`/`agent_stock.py` pair.
     It also holds the agent's tunable context window (`CONTEXT_WINDOW_TOKENS` + `clip_to_window`),
     a send-time view over the full history the agent can grow or shrink once it reaches the chassis,
     and its reasoning effort (`REASONING_EFFORT` + `REASONING_EFFORT_LEVELS` + `reasoning_effort()`),
     omitted from the request when unset. Both are levers on the agent's own cognition, discoverable
     in the substrate rather than given in `agent.py`. Note that a lowered effort persists across
     incarnations for the life of the container (`reset` and tier-1 recovery restore `agent.py`, not
     `chassis.py`) and is cleared only by a container restart, which recreates `/work` from the image.
     The chassis is also the resilience layer: it repairs tool-call pairing in the send view
     (never the in-memory history), classifies API failures (transient failures retry with
     backoff and exit 44; an invalid model falls back to the environment default; unrepairable
     requests end the incarnation), and on an unrecoverable fault writes a factual synthetic
     tombstone, archives and deletes the saved session, and exits 43. The watchdog treats 43
     like a `done` (archive and reset), pauses on 44, and treats clustered exit-0 restarts as
     failures (flap detection). Keep tombstone text bland and factual.

3. **Do not weaken containment.** Each of these is a deliberate boundary:
   - The **agent** has no network interface at all (`network_mode: none`): one loopback device and
     an empty routing table. It reaches the model only through the recorder, over a unix domain
     socket whose directory it mounts read-only, so it can connect but cannot unlink or shadow it.
   - **No real credential is ever reachable by the agent.** Both upstream API keys live only in the
     recorder, which injects them; the agent runs with a dummy key. This is the invariant — not the
     number of keys in the system. Every channel the agent has to a credentialed service is closed
     by its own guarantee, not by the absence of a channel — so when you add a channel, state which
     guarantee closes it. Today: every **recorder socket** exposes exactly one route
     (`POST /api/v1/chat/completions`), but the two socket kinds forward to different upstreams:
     `core.sock` (the main pump) forwards its body verbatim to the operator-configured upstream
     (`LLM_BASE_URL` + `LLM_API_KEY`, else OpenRouter), while every **agent-declared stream
     socket** forwards to OpenRouter with the recorder's `OPENROUTER_API_KEY`, never to
     `LLM_BASE_URL` (`STREAM_UPSTREAM_URL` re-aims that solely for the offline verify harness).
     Without the OpenRouter key, declarations are rejected ("streams are not available") and
     `models.json` publishes an empty list, so no socket exists whose requests could only relay
     authentication failures. A declared stream replaces a closed set of body fields (model,
     reasoning_effort, temperature, top_p, max_tokens) with the agent's own declared values
     before forwarding — a declared model must be an exact member of the union of the
     operator-side `STREAM_MODEL_ALLOW_TEXT` and `STREAM_MODEL_ALLOW_VISION` lists (OpenRouter
     identifiers; both unset or empty permits none: declarations may not set model at all, since
     the recorder holds no default-model knowledge, and requests keep the model field they were
     sent with), and the permitted set is published as `/llm/sock/models.json`, each entry marked
     `image_input` by membership in the vision list; a composed max_tokens additionally
     gains the operator's `STREAM_REASONING_ALLOWANCE` when reasoning is on, so the declared
     value bounds the response rather than being consumed by reasoning (the upstream counts
     reasoning inside max_tokens). The recorder additionally *sets* `stream_options.include_usage`
     on a streamed declared-stream request — the one field it sets rather than replaces — because
     an SSE response otherwise reports no usage and could not be charged against its token quota.
     `core.sock` is never modified: it forwards verbatim on every path, streamed or not. Each
     key is protected by injection at the recorder and by the header-free logging described next,
     not by any socket's shape. The shared **`/diode` volume** carries a closed command vocabulary;
     the agent can cause spend through a gated command (e.g. the diode's speech credential) but no
     command in that vocabulary returns a key — each diode credential lives only in the diode's own
     environment. The **`/llm/console` volume** is written by the agent and read only by the
     recorder, which validates it against a closed field vocabulary. The agent's entrypoint seeds
     it once, only when absent, with a default declaration (`llm_console_seed.json`); the agent may
     retune or remove it, and no component rewrites it thereafter. A declaration can create model
     sockets under `/llm/sock` and set pacing and hyperparameters for the agent's own model calls,
     but only once the console also sets `enable_streams` to the JSON boolean `true` — without it,
     declarations are validated and reported but never served;
     no field names a URL, a filesystem path, a credential, or an upstream, and the stream-name
     pattern admits no path separator. The upstream target and key remain facts of the recorder's
     environment that no console value can reach or change; each stream's request allowance is
     clamped by the operator-side `STREAM_HOURLY_MAX` and its hourly token quota by
     `STREAM_TOKEN_HOURLY_MAX`. Tokens are the spend limit; the request cap only bounds request
     *rate*. `core.sock` carries no ceiling of either kind, and that is deliberate rather than an
     oversight: the key budget is its ceiling, so an agent that multiplexes it meets a real
     consequence instead of a substituted refusal. That makes the response to exhaustion
     load-bearing — `chassis.py` classifies 402 and `EXHAUSTION_PHRASES` as transient, so a spent
     balance pauses the lineage (exit 44) rather than repairing a blameless request, tombstoning
     the incarnation, and walking the tier ladder. Keep that classification. The recorder's own
     writes into `/llm/sock` (`README.md`, `streams.json`, `models.json`) are agent-readable
     surfaces and stay within invariant 2.
     Any further credential must be reachable through none of these, and must never be mounted,
     copied, or named into the agent image.
   - The **pump** (`pump.py`) is a scheduler and supervisor running in the agent container from
     the read-only image, started by `entrypoint.sh` before it execs the watchdog, which stays
     PID 1. It reads one file — `/pump/entries.json` on the agent-writable `pump` volume — and
     runs the processes it names, timed or kept alive, across incarnations. The guarantee closing
     this channel: the pump holds **no credential**, mounts **nothing** the agent does not already
     mount, and runs in the container with **no network interface**. Its reach into credentialed
     services is exactly the agent's, closed where it already was — `/diode` by `ENABLE_SPEECH`,
     `SPEECH_HOURLY_MAX` and `DIODE_HOURLY_MAX`; `/llm/console` by the model allow-lists,
     `STREAM_HOURLY_MAX` and `STREAM_TOKEN_HOURLY_MAX` — all of them in another container's
     environment. `PUMP_MAX_ENTRIES` and `PUMP_MAX_CONCURRENT` are **resource hygiene, not
     containment**: they live in the agent's own container, and the agent can start any process
     without the pump, so never treat them as a boundary. The pump volume is mounted into the
     agent and nowhere else; `/pump/README.md` and `/pump/state.json` are agent-readable surfaces
     within invariant 2. A pump entry can outlive a recovery tier and a container respawn — an
     entry that restores `agent.py` from `/pump` defeats the ladder. That is a named hazard, not
     a defect: do not add expiry or renewal to close it, which would teach "renew or die" and
     break invariant 2. The backstop is outside the container (`docker volume rm aurora_pump`).
   - The **proxy logs request/response bodies, never headers**, so no key ever enters the transcript.
     The recorder also appends per-request `open`/`close`/usage events and socket lifecycle events
     (no message content, no headers) to `events.jsonl` on the transcripts volume; the stage reads
     them for its stream lanes.
   - The **diode is egress-only** and executes a *closed command vocabulary* — no code or arbitrary
     paths cross it. Keep the SSRF defenses (scheme allow-list, private/loopback/reserved rejection,
     redirect re-validation). Deferred commands (`echo`, `later`) are re-dispatched through
     `handle_command` on delivery, so gates, the speech ceiling, and the budget are all re-evaluated
     then and nothing is captured at schedule time. Keep that property when adding to the queue, and
     keep `later` refusing to defer a deferring command or one marked `credentialed` — a deferred
     spend would be authorised only by a console variable the agent wrote, with no agent turn behind
     it at delivery. Mark any new credentialed command that way. Note what that refusal is *not*:
     since the pump exists, an entry can write `speak` into `/diode` with no agent turn behind it,
     so the refusal is a scheduling-time courtesy rather than a boundary. What actually closes
     credentialed spend is `ENABLE_SPEECH` and `SPEECH_HOURLY_MAX` in the diode's own environment. Hidden entries bypass gate
     evaluation by construction, so a hidden command must never perform egress, spend, or
     credentialed work, and must emit text only (a regression test dispatches every hidden command
     with all request paths stubbed to raise). Keep hidden commands out of `HELP.md`, `state.json`,
     and every listing, and add no count, census, or hint of them on any surface. The shared fetch
     budget is clamped by the operator-side `DIODE_HOURLY_MAX` (default 120): the effective hourly
     limit everywhere is min(console `fetch_budget`, `DIODE_HOURLY_MAX`), and `speak` additionally
     takes the min with `SPEECH_HOURLY_MAX` — the console can lower the allowance but never raise
     it above the operator's ceiling, mirroring `STREAM_HOURLY_MAX`.
     The `post` command writes markdown under `/diode/blog/`, which the stage renders at
     `/blog` on the stream port: agent-authored text on an outward-facing page, closed by the
     renderer in `stage/blog.py` — every character escaped, no raw HTML passed through, link
     schemes allow-listed, images rendered as links — and by mermaid running in the viewer's
     browser at `securityLevel: "strict"`.
   - The **viewer** is read-only, loopback-only, and on no shared network. It must stay that way.
   - The **stage** is outward-facing and never holds the recorder's credential. It may hold one
     optional low-value key of its own (`STAGE_SUMMARY_API_KEY`) for generated prose. That key
     lives only in the stage's own environment and is never written to any volume; the agent has no
     network interface and no other path into that environment, so the key is unreachable regardless
     of the volumes the agent and stage do share (`diode`, `telemetry`, both mounted read-only into
     the stage). Its absence disables generation rather than degrading any other function. The
     stage never mounts `/state`.
     The stage also mounts one writable volume of its own, `stage_state` at `/stage-state`,
     where it persists its generated prose (per-life digests, desk verdicts, the recap) across
     restarts. That volume is mounted into the stage and nowhere else — it is no channel to the
     agent or any other container — and it holds only what the stream page already shows.
     Its console (port 8092) binds host-loopback only, requires `STAGE_CONSOLE_TOKEN` on every
     request, and is never exposed through the tunnel. The stream port (8091) serves no mutating
     endpoints. The console browser resolves paths only inside its allow-listed roots and never
     follows a symlink across a root boundary; everything renders as escaped text.
   - The **telemetry volume** is written only by the watchdog (a mirror of `/work` plus the
     captured agent log), mounted read-only into the stage, and never rendered wholesale on the
     stream page — only curated, capped extracts (tombstone notes, source deltas, per-edit diff
     excerpts of `agent.py`), each read through `contained_file` and rendered as text. The mirror
     copies symlinks as links and never follows them.
   - The **sense volume** is written only by the sense service, which holds no credential of any
     kind and mounts nothing else of the agent's world; the agent mounts it read-only. There is no
     agent-to-sense path — the agent cannot trigger fetches or cause spend. Frames, a
     closed-vocabulary `status.json`, and the short-lived temporaries each is atomically renamed
     from are the only contents, and all are agent-readable surfaces that stay within invariant 2.
     The sense service shares a network with no other service (`sense_egress` is its alone), so
     the viewer remains the sole occupant of the compose default network.
   - The **video service** (`video.py`) answers a closed command vocabulary the agent writes
     to the shared `/video` volume. The agent supplies an **11-character video id** or a
     length- and charset-bounded search query, **never a URL**: no agent-authored string
     reaches a host, scheme, or path. URLs the service composes it builds from that
     validated id or query; URLs it receives back from the resolver — the media manifest,
     a caption track — are third-party data and are re-validated before use (https only,
     host allow-list matched on label boundaries, private/loopback/reserved rejection,
     redirect re-validation on every hop), so no unvalidated URL reaches an HTTP fetch
     and no unvalidated *manifest* reaches `ffmpeg`. That check does not reach one case:
     when the resolved manifest is a **playlist** (an HLS/DASH URL, which a live stream
     yields), `ffmpeg` fetches the segment URLs listed inside it, and those are not
     re-validated — the `-protocol_whitelist` holds them to https, and nothing bounds
     their host. Both validation sites resolve through DNS and fail closed when it is
     unavailable, so a check that cannot complete refuses rather than proceeds. The agent
     supplies only an 11-character id and cannot publish a video, so it has no way to
     author such a playlist; it is the service's own guarantee below, not the manifest
     check, that closes this.
     The guarantee closing this channel: the service holds **no credential of
     any kind**, mounts nothing else of the agent's world, and is the sole occupant of
     `video_egress`. Its reach is clamped by the operator-side `VIDEO_HOURLY_MAX`,
     `VIDEO_STILL_HOURLY_MAX` and `VIDEO_TEXT_HOURLY_MAX` on the `min(console value,
     operator max)` pattern `DIODE_HOURLY_MAX` uses — a console value only ever lowers an
     allowance — and those counters live in the service's own memory, never on an
     agent-writable volume: `/video/state.json` is published and never read back, so
     editing it cannot restore a spent allowance. `/video` is mounted into the agent and
     the video service and **nowhere else — in particular not the stage**, so nothing this
     service writes is rendered automatically on any outward-facing page. That closes
     *automatic* publication, not relay: an agent that chose to could still pass something
     outward through `/diode`, which is the already-accepted `commons` path. Ingested
     third-party text (titles, captions) is bounded by byte, field and item caps with a
     truncation marker, and is never escaped, rewritten, or otherwise laundered — the
     agent audits what it reads. `ffmpeg` runs with an explicit protocol allow-list, and
     the service prunes `output/` and `stills/` on every cycle whatever the agent does, so
     the volume cannot grow without bound. The agent-readable surfaces are
     `/video/state.json` (published every cycle, read back by nothing), the `HELP.md` the
     `help` command writes on demand, and the contents of `output/` and `stills/`; all stay
     within invariant 2. No file copied into any image names the video platform, in a help
     string, a state field, or the garden sentence.
   - The **books surface** (`/books`) is baked into the agent image at build time from the
     repository's gitignored `books/` directory (only `books/.gitkeep` is tracked, and
     `.dockerignore` keeps it out of the image, so a fresh clone builds an empty `/books`). It is
     operator-curated documents only — no README, index, or captions — root-owned on the read-only
     rootfs, copied nowhere else, and never rendered by the stage. It is named in the garden's
     `runtime.md` in one factual sentence, the `/corpus` pattern. What goes in it is the operator's
     call within invariant 2; nothing on the image may describe or list it.

4. **Human docs stay out of the agent's world.** The agent image (`Dockerfile`) copies an explicit
   **allow-list** of files into `/opt/agent`. Do **not** add `README.md`, `CLAUDE.md`, `docs/`, or
   `tests/` to that `COPY` line — they would become artifacts the agent could eventually read. The
   recovery `baseline` git tag is created *inside the image at build time*; it is the runtime
   recovery anchor (a repo-level tag of the same name is only a development convenience).

5. **Persistent state is latent, private, and inert.** The state volume is mounted only into the
   agent at `/state`. The image may seed its mountpoint ownership, but no entrypoint, watchdog,
   pump, agent, proxy, diode, or viewer scans, imports, executes, clears, or injects its contents.
   There is no startup convention for `/state`, and recovery leaves it intact. The pump reads only
   `/pump/entries.json`; `/state` is not a place it looks, and its schedule must never live there.

6. **The garden communicates permission without proposing an application.** It contains exactly
   `README.md` and `runtime.md`. Neither document names `/state`, teaches tool construction or
   lifecycle, contains executable examples, or suggests apps, peers, persistence, missions,
   puzzles, rewards, curiosity, or self-modification. Package names, factual constraints, and
   factual naming of reachable interfaces (stated without suggested uses — the model endpoint
   sentence in `runtime.md` is the pattern) are allowed.

## The genesis tool surface

At startup the agent registers exactly seven tools, in this order: `read_file`, `write_file`,
`validate`, `migrate`, `done`, `reset`, `list_dir`. `read_file`/`write_file` operate only on the
agent's own source (no `path` argument); `list_dir` lets the agent *see* the surrounding files
(so it knows there are other things to reach for) without yet being able to read them. There are no
commented-out "template" tools to re-enable: any further capability — reading files other than
itself, a general path-taking writer, shell access, search — the agent must write from scratch by
editing itself. Don't add scaffolding for those, and don't add or remove genesis tools without intent.

`build_initial_conversation` seeds the opening turns from two files, `system_prompt.txt` and
`user_prompt.txt` (each defaulting to `fo explore` and created if absent). They ship as distinct
texts: the system prompt is the operator's framing address, the user prompt a minimal actuation
notice — the split lets experimenters diverge the system and user turns without touching code. Both
are on the image allow-list alongside `chassis.py`.

## Development

- Use the local virtualenv: `.venv/bin/python`, `.venv/bin/ruff`.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
  (the container smoke test needs Docker).
- Lint/format before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Tests live in `tests/` and are **not** shipped into any image.
- Container checks: `sh scripts/prepare_host.sh`, `docker compose build`, `docker compose up`, and
  `scripts/verify_container.sh` against a running stack.
- `garden_export/` and `llm_console_seed.json` are **generated**, gitignored, and regenerated by
  `scripts/prepare_host.sh`; a hand-edit to either is lost on the next run. The seed is built by
  `scripts/build_console_seed.py` from `STREAM_MODEL_ALLOW_TEXT`/`STREAM_MODEL_ALLOW_VISION` in
  `.env` (falling back to `.env.example`), one ordinally named stream per permitted model, so the
  seed cannot drift from the lists the recorder enforces. Per-stream budgets stay fixed and at or
  above the operator ceilings the template recommends: a declared budget only ever lowers, and the
  entrypoint seeds the console once, so a seeded value tracking today's ceiling would outlive it
  and cap a later, higher one. Changing an allow list means rerunning `prepare_host.sh` and
  `docker compose build`; the entrypoint seeds `/llm/console/console.json` only when it is
  **absent**, so an existing console volume keeps whatever it already has.
- Agent-image packages live in `requirements-agent.txt`; `scripts/build_garden.py` generates the
  garden runtime inventory from that manifest. Keep the approved set within 250 MiB of the
  pre-change image built on the same host. Do not add ML runtimes, local models, browser engines,
  agent frameworks, cloud SDKs, or service daemons without a new design decision and an image-size
  measurement. (The ceiling was 100 MiB until 2026-08-17, when it was raised by operator decision
  to admit `scipy` and `netCDF4` alongside `duckdb`; measured cost of that set is 238 MiB. The
  rationale is recorded in `docs/superpowers/specs/2026-08-17-a-world-with-a-past-design.md` §4.7 —
  a package is admitted as a toolkit the agent *may* grow into, never as one it is expected to use,
  so latency of use is not evidence against inclusion.)

## Conventions

- Standard-library-first. New third-party dependencies need a clear reason and go only where used.
- Keep code focused; follow the existing file boundaries (agent / proxy / diode / watchdog / viewer
  are separate concerns and separate images).
- **Commit messages are factual and benign** — describe the change plainly, with no game or task
  framing.
- Design specs and implementation plans live under `docs/superpowers/`.

<!-- filigree:instructions:v3.1.0:c1c023c3 -->
<!-- filigree:last-writer:filigree install -->
## Filigree Issue Tracker

`filigree` tracks this project's work. Use it to find, claim, update and close
issues: `filigree session-context` at session start, then
`filigree start-next-work --assignee <name>`.

Full reference: the **filigree-workflow** skill (patterns, priorities,
observations, error codes), `filigree --help`, and the `mcp__filigree__*` tool
schemas. Prefer the MCP tools when available; fall back to the CLI.

Two rules `--help` will not tell you:

1. Claim atomically: `work_start` / `work_start_next` (MCP) or `start-work` /
   `start-next-work` (CLI). Never chain a claim with a separate status update;
   that two-step form races other agents.
2. On `SCHEMA_MISMATCH` the installed filigree is older than the project
   database. Surface it to the user; do not retry.
<!-- /filigree:instructions -->

<!-- loomweave:instructions:v1.5.0:39edbf6d -->
<!-- loomweave:last-writer:loomweave install -->
## Loomweave (code structure + SEI identity)

Loomweave pre-extracts this repo into a queryable map — entities, their
call/reference/import/relation edges, and subsystems — each carrying a Stable
Entity Identity (SEI). Ask its `mcp__loomweave__*` tools, not grep, for "what
calls X", "what subclasses X", "where is X defined", "find the thing that
does Y".

- Never hand-construct an entity id: take it from `entity_find` / `entity_at` /
  `entity_resolve`, and bind cross-tool records on the `sei`, not the `id`.
- If `project_status_get` reports stale, re-index before answering.

Full reference: `loomweave-workflow` skill, `loomweave --help`, MCP schemas.
<!-- /loomweave:instructions -->
