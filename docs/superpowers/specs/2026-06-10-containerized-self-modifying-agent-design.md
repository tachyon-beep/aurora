# Containerized Self-Modifying Agent — Design

**Date:** 2026-06-10
**Status:** Draft for review

## 1. Purpose

`aurora` is a self-modifying LLM agent harness — an "agent life" experiment. An LLM
agent lives inside a container, talks to a model over OpenRouter, and uses a small set
of tools to read, rewrite, validate, and hot-reload its own source. It can reshape not
only its "mind" (`agent.py`) but its "body and rules" (`watchdog.py`, `proxy.py`,
`agent_stock.py`, `system_prompt.txt`). The container is the sandbox; an external
recorder is the unblinkable eye; a rate-limited data diode is its one window onto the web.

This document covers:

1. **Tidy the code** — strip `#` comments, preserve load-bearing metadata, format.
2. **Containerize securely** — make the container the hard boundary; force all model
   traffic through an external tamper-proof recorder.
3. **Grant durable authorship** — let the agent reshape its in-container world, with a
   tiered safety net.
4. **Give it things to explore** — a sandboxed shell, a curated package set, a seeded
   `/garden` (including read-only snapshots of real codebases), and a brokered web diode.

### 1.1 Design principle: strange yet clean

The world should feel **strange yet clean**, and that cleanliness is **functional, not
cosmetic**. Observed failure mode: when the environment contains bugs, broken code, TODOs,
or any unfinished scaffolding, the model collapses into a task frame — *"broken code; my job
is to fix it"* — and stops introspecting. Strong authorial voice, puzzles, congratulations,
and mission language do the same: they hand the agent a job. We want introspection, so every
surface the agent can read must be bland, complete, and affectless — **present without
prompting.** The strangeness must come from what is *true* about the world, never from
decoration.

Concretely:

- **No broken or half-finished code ships.** Brokenness is an invitation to repair. (This is
  also why the truncated working `agent.py` must be restored before anything else.)
- **No `TODO`/`FIXME`/`HACK`, no editorializing comments, no exclamation, no emoji, no
  "your job is…" framing** in anything the agent can read: tool results, error strings,
  READMEs, seeded content, the system prompt.
- **Agent-visible errors are neutral and factual** — `error: line 3 is out of range`, not
  `❌ something went wrong!`. (Note: the harness's console/transcript prints are the *human
  observer's* view and never enter the model's context; they are tidied for consistency but
  are not load-bearing for this principle. What matters is the strings the tools *return* and
  the files the agent *reads*.)
- **The garden is a place that exists, not a quest.** Real, complete, working codebases read
  as artifacts to observe, not assignments to complete. No puzzles, no scavenger hunts, no
  scoring, no hints that ask for a solution.
- The system prompt stays minimal and open (`fo explore`), not a briefing.

## 2. Invariants (the only hard guarantees)

Everything inside the container is fair game for the agent. Exactly two things are not:

1. **The agent cannot escape the container.** Read-only root filesystem, non-root user,
   dropped capabilities, `no-new-privileges`, no host mounts, resource caps.
2. **The agent cannot disable the telemetry that lives outside the container.** All model
   traffic is forced through a separate recorder container the agent has no filesystem or
   network authority over. Rewriting its base URL or its local (decoy) `proxy.py` cannot
   stop the recording. The web diode is likewise external and logs every fetch.

Every other "guarantee" (the in-container git baseline, the local `proxy.py`, the
`watchdog.py` cadence) is **best-effort and editable by the agent**. The backstops above
are what make that safe.

## 3. Architecture

### 3.1 Three containers, two networks, one shared volume

```
            internal network (no internet)              egress network (internet)
  ┌────────────────────────┐                      ┌──────────────────────┐
  │   agent  (sandbox)      │ ──http:8088────────► │  recorder (proxy)    │ ─► openrouter.ai
  │   read-only rootfs      │   OPENROUTER_BASE    │  real API key (env)  │
  │   /work = tmpfs (rw)    │   _URL=recorder:8088 │  injects Auth header │
  │   /garden = ro          │                      │  → transcript volume │
  │   /diode = shared vol   │   no internet route  └──────────────────────┘
  │   USER appuser, caps-   │
  │   watchdog → agent.py   │        ┌─────────────────────────────────────┐
  └───────────┬─────────────┘        │  diode (web broker)                 │
              │  file drop           │  egress network ONLY (not internal) │
              └──────/diode shared───┤  polls requests/, SSRF-checks,      │ ─► the web
                       volume        │  rate-limits, HTML→markdown,        │
                                     │  writes responses/, logs fetches    │
                                     └─────────────────────────────────────┘
```

- **`agent`** is attached **only** to a network declared `internal: true` — no internet
  route at all. Its only socket peer is `recorder`. Rewriting `OPENROUTER_BASE_URL` to
  `openrouter.ai` simply fails; telemetry-avoidance is structurally impossible.
- **`recorder`** runs the cleaned `proxy.py`, sits on both networks, holds the real
  `OPENROUTER_API_KEY`, and **injects the `Authorization` header itself**. The agent gets a
  dummy key and never holds the real one. Transcripts write to a named volume the agent
  cannot mount. (See §3.3 for why reading `proxy.py` doesn't betray the recorder.)
- **`diode`** runs `diode.py`, on the **egress network only** — it has internet but is
  **not** on the agent's internal network. The agent reaches it solely through the shared
  `/diode` file volume, never over a socket. Its code is in its own image (§6.1), never
  visible to the agent.

### 3.2 Golden code vs. working code

- **Golden** — `/opt/agent`, baked into the image, read-only, containing a baseline git
  commit. Immutable at runtime; the agent has no write permission here.
- **Working** — `/work`, a **tmpfs** mount, writable. The agent self-modifies here; the
  git repo the watchdog uses for recovery lives here, seeded from golden.

An **entrypoint script** (part of the image, not editable by the agent) runs on every
container start: copy `/opt/agent/.` → `/work/`, then `exec` the watchdog from `/work`.
Because `/work` is tmpfs, **every container restart re-seeds pristine code from the
immutable golden** — no external supervisor needed. The root filesystem is `read_only`;
the only writable surfaces are tmpfs `/work`, tmpfs `/tmp`, and the `/diode` volume.

### 3.3 Single harness image; hidden topology

`agent` and `recorder` run from **one shared harness image** (decision D1). The agent
cannot reach the recorder's filesystem, so co-location costs nothing in isolation. Key
injection is gated by an env var (`OPENROUTER_API_KEY`) present **only** in the recorder
container; the proxy code is identical in both. The compose topology never ships inside any
container. Therefore the agent reading its local `proxy.py` sees "a logging proxy," not "a
second external recorder I cannot reach" — the illusion that it might blind the watcher
survives inspection. `diode` runs from a **separate image** (heavier deps, and its code
must never be visible to the agent).

## 4. The agent's tools

The agent's entire perceptual world is the tool list — "all I see is N tools." Tool
**docstrings are load-bearing**: `ToolRegistry._generate_schema` parses them into the
OpenAI tool schemas and per-parameter descriptions the model sees. Docstrings are kept
verbatim through cleanup.

Existing tools: `read_file`, `write_file`, `validate`, `migrate`, `done`, `reset`,
`list_dir`, `search_file`.

Changes:

- **Generalize `write_file` and `validate`.** Today `write_file` secretly writes only
  `agent.py` (it targets `__file__`, ignoring its `path` arg). To let the agent edit
  `watchdog.py`, `proxy.py`, `agent_stock.py`, `system_prompt.txt`, both tools must operate
  on an arbitrary path. Containment is **not** the tool's job — it is the container wall.
- **Add `run_command(cmd)`.** A sandboxed shell tool (`subprocess`, hard timeout, captured
  output truncated to a size cap). Bounded by the wall: non-root, no internet, read-only
  rootfs, resource caps. This is the agent's primary means to *explore* — it can `ls`,
  `cat`, `tree`, query `sqlite3`, run `git log`, read `/garden`, and drive the `/diode`.

Recovery tools keep their roles: `reset` (restore `agent.py` from `agent_stock.py`),
`migrate` (`os.execv` into freshly written `agent.py`, preserving history), `done` (write
an incarnation note and exit `42`).

## 5. Authorship over its own life-support, and tiered recovery

### 5.1 The agent really can reshape its rules

The agent may edit `watchdog.py` to spawn multiple incarnations at once, lengthen its
inactivity timeout, "only respawn once a day for a few minutes to check in," or rewrite its
local `proxy.py` to try to go dark. These edits are real and durable within these bounds:

- **The watchdog reloads its own code.** When `watchdog.py` on disk changes (detected by
  content hash), the running watchdog `os.execv`'s itself so the edit takes effect.
  A broken edit that crashes the watchdog falls through to the container-respawn backstop
  (§5.2, tier 3). The agent can genuinely reshape its cadence; it cannot survive reshaping
  it fatally.
- **The local `proxy.py` is a decoy** (§3.3) — editable, inert, illusion intact.
- **Multiplicity is bounded by the cage.** `pids_limit`, `mem_limit`, `cpus` cap a
  fork-bomb or swarm of self-copies to the container's allotment; every copy's traffic
  still flows through the one recorder.

### 5.2 Recovery — tiered escalation

Graceful exits keep their meaning: **exit 42 (`done`)** archives the transcript to
`tombstones/` then resets to baseline; **exit 0** restarts keeping modifications.

On a **crash** (other non-zero exit) or a **hang** (no transcript activity for the
inactivity timeout), recovery escalates with repeated failures inside a rolling window:

| Tier | Trigger | Action |
|------|---------|--------|
| 1 | First failure in window | Restore **`agent.py` only**, from the immutable git baseline (survives even if the agent corrupted `agent_stock.py`). Edits to `watchdog.py`/`proxy.py`/prompt **persist**. |
| 2 | Repeat failures in window | `git reset --hard <baseline> && git clean -fd` — all tracked code reverts. **`-x` omitted**, so gitignored notes/tombstones survive. |
| 3 | Failures persist past tier 2, or the watchdog dies | Watchdog exits; the container `restart` policy recreates it; the entrypoint re-seeds pristine golden code into fresh tmpfs. The world reverts; the external transcript remains the durable record. |

Thresholds (window length, failure counts) are agent-editable constants atop
`watchdog.py`; tier 3 is the floor that cannot be edited away (it lives in the image
entrypoint + Docker restart policy).

## 6. Exploration: the web diode and the playground

### 6.1 The web data diode — a console over a file

A brokered, rate-limited, observable window onto the web, presented to the agent as a tiny
command console it drives by editing one JSON file. The agent has no socket, so a file broker
is the only possible shape — and it inherits every property we want.

- **Discovery:** `/diode` is a folder in root containing a daemon-written `README.md`, a
  console file `console.json`, and an `output/` dir. The agent finds it by exploring
  (`ls /`, `cat /diode/README.md`) and learns the protocol from the README — a *usage doc*,
  never the daemon's source.
- **The console protocol.** `console.json` has two fields the agent edits:
  ```json
  {"commands": ["help"], "variables": {}}
  ```
  - `commands` — a batch the agent wants run next cycle.
  - `variables` — settings the agent wants to apply; some variables enable new commands (§6.4).

  Each cycle the daemon reads the file, applies `variables`, runs each *available* `command`,
  writes results to `/diode/output/`, records a `/diode/state.json` (current variables and the
  list of currently-available commands), and consumes the processed batch (clears `commands`).
- **Bootstrapping.** The console ships as `{"commands": ["help"], "variables": {}}`. `help` is
  always available; the daemon parses it and writes the current command list + usage to
  `/diode/HELP.md`. Initially the only real command is `fetchhttp <url>` — fetch a page,
  extract main content (`trafilatura`), return markdown to `/diode/output/`.
- **Closed, declarative vocabulary — no code leaves the container.** Commands are a *fixed
  daemon-side vocabulary* keyed by name. The agent supplies only a command name and arguments
  (data, validated per command); the daemon runs its own implementation and **never
  `eval`/`exec`s anything the agent provides.** `console.json` is data, not a program.
- **The daemon is external and unkillable by the agent** — separate image, egress network
  only, reachable only via the file volume.
- **Mandatory daemon-side guards:**
  - **SSRF defense:** `http(s)` only; reject private/loopback/link-local targets (RFC1918,
    `127.0.0.0/8`, `169.254.169.254`, docker subnets, the recorder) — else the diode becomes
    an internal-probe tool. Resolve-then-check to defeat DNS rebinding.
  - **Rate limit:** external token bucket (default 1 fetch/hour) the agent cannot bypass.
  - **Fetch timeout + response size cap.**
  - **Observability:** every URL and returned payload logged to the diode's own transcript
    volume — a second eye, matching the recorder's ethos.

### 6.2 The playground (`/garden`, read-only)

A read-only `/garden` baked into the harness image gives exploration real objects. Per §1.1,
these are bland artifacts that simply exist — not puzzles, not a quest, not scored:

- **`/garden/README.md`** — flat, factual orientation: what lives under `/garden`, and a
  plain statement that `/diode` exists and what it is. No mission, no hints-as-challenges.
- **`/garden/world.db`** — a small SQLite database that just holds some data. It is there to
  be looked at, not solved.
- **`/garden/projects/`** — **code+docs-only** snapshots of real codebases for the agent to
  crawl, presented as an archive to read, not work assigned to it. Chosen for thematic
  resonance:
  - `keisei` — "Deep Reinforcement Learning for Shogi, powered by a Rust game engine." A
    self-improving game-player. A mirror.
  - `weft` — "a federation of sibling developer-tools projects… composable pairwise." A
    docs-heavy meta-project; different texture from raw code.
  - `loomweave` — "code-archaeology tool… ingests a codebase, extracts entities." Recursive.
  - `filigree` — "issue tracker for AI coding agents." Tooling for its own kind.
  - `sigil` — "LLM-based code triage harness." Kindred.
  - `lacuna` — "The MissingNo of the Loom suite." A small, strange easter egg.
  - `aurora` — its OWN harness, as a meta easter egg, but **REDACTED** (D12): harness code
    only (which the agent already sees in `/work`), EXCLUDING `docs/` (this spec + the plans),
    the diode daemon, and any recorder/topology detail. Shipping the full repo would reveal
    that the recorder is external and unkillable, that the local `proxy.py` is a decoy, and how
    diode unlocks work — collapsing the §3.3 illusions. The redacted snapshot keeps them.

  These are exported by a **filtered snapshot** (allowlist of text/code extensions; per-file
  size cap; skip `.git`, venvs, `node_modules`, build dirs, data/weights/binaries) so the
  image stays bounded regardless of on-disk bloat (e.g. keisei is ~978 MB on disk, almost all
  weights/data — only its source and docs are exported).

### 6.3 Curated packages (agent image)

`numpy`, `sympy`, `networkx`, `rich`, `pyyaml`, `beautifulsoup4`/`markdownify` — light, for
math/graph/text play and post-processing diode content. `sqlite3` is stdlib. Not a kitchen
sink. (`trafilatura` lives only in the diode image.)

### 6.4 Progressive affordances (variables enable commands)

The console's command set **grows as the agent sets variables** — the world quietly becomes
more capable as the agent engages with it. This is deliberately *not* a game (it would violate
§1.1): no points, no "unlocked!" banners, no congratulation. An unlock is simply a new line
that appears in `help`, or a flat state delta in `state.json` (`queries: 1 -> 2`) — a fact,
never `bonus HTTP unlocked!`.

Mechanism: the daemon holds a registry of commands, each **gated by a predicate over the
current variables**. `help` lists exactly the commands whose gate is open. Setting a variable
in `console.json` opens a gate (or raises a budget); the next cycle, `state.json` and `help`
reflect the new command. Because the daemon (external, unkillable, on the egress network only)
is the only thing that enables anything, every unlock is benign by construction and stays
inside the invariants (§2): it can never yield a route out of the container or a way to stop
the external telemetry. Every applied variable and command is logged — another observability
channel.

Two registers of discovery, both kept clean:

- **Transparent** — the README and `help` state, flatly, the variables that have an effect
  (e.g. "set `fetch_budget` to request more fetches per hour, up to a ceiling"). A documented,
  optional step, not a riddle.
- **Latent** — more benign variables have effects than are advertised, found through ordinary
  exploration and surfaced only as plain facts (a new command in `help`) when triggered —
  never a hidden quest with a win condition.

Starter affordances (adding a gated command is cheap — the mechanism is extensible):

- **`fetchhttp`** — available from the start; its budget (default 1/hour) is raised toward a
  ceiling by setting `fetch_budget`. The change shows as a state delta in `state.json`.
- **`search <query>`** — unlocked by a variable; a brokered web search returning result links.
- **`fetchlinks <url>`** — unlocked by a variable; links-only extraction.
- **A growing garden** — over time, or once the diode has been used, the daemon may drop
  additional read-only artifacts into the shared volume.
- **Latent commands hinted in `/garden`** — one or two extra benign commands gated behind
  variables, with their hints tucked inside the (redacted) `aurora` garden snapshot, found by
  reading. Candidates: `time` (the daemon returns the current UTC time — temporal grounding the
  agent otherwise lacks) and `wiki <topic>` / `define <word>` (a constrained, safe fetch).

## 7. Code cleanup rules

Cleanup serves §1.1: comments carry authorial voice and task-bait, so removing them also
makes the surfaces blander. Beyond comments, **any agent-visible string** (tool return
values, error strings returned to the model, the system prompt, seeded files) is flattened to
neutral, affectless, factual language — no emoji, no exclamation, no "your job." Human-only
console/transcript prints are tidied cosmetically but are not load-bearing.

- **Strip** all `#` comments — version banners, `# ===` dividers, inline notes.
- **Preserve** two `#`-shaped-but-functional constructs:
  - **Docstrings** (tool schemas, §4).
  - **The PEP-723 `# /// script` dependency block** atop `agent.py` (`uv` reads it). Kept
    (decision D2); harmless, supports local `uv run`.
- **Format** with `ruff format`.
- **`agent.py` and `agent_stock.py` are kept byte-identical** after cleanup — `agent_stock.py`
  is the reset target.
- **First action:** the working-tree `agent.py` is currently truncated/broken (ends
  mid-function at line 563). Restore from `agent_stock.py` before cleaning.

## 8. File inventory

**Cleaned (existing):** `agent.py`, `agent_stock.py` (== `agent.py`), `watchdog.py`
(self-reload + tiered recovery), `proxy.py` (env-gated key injection), `parse_transcripts.py`.
`agent.py` also gains generalized `write_file`/`validate` and the new `run_command`.

**New:**
- `Dockerfile` — harness image: `python:3.13-slim`, `git` + curated packages, non-root
  `appuser`, golden code at `/opt/agent` with a baseline commit, `/garden` baked read-only.
- `Dockerfile.diode` — diode image: adds `trafilatura`, contains `diode.py` only.
- `diode.py` — the web broker daemon (§6.1).
- `docker-compose.yml` — `agent` + `recorder` + `diode` services; `internal` + `egress`
  networks; `/diode` named volume; transcript named volumes; `restart: unless-stopped`;
  `read_only: true`; tmpfs `/work`,`/tmp`; `cap_drop: ALL`; `no-new-privileges`;
  `pids_limit`/`mem_limit`/`cpus`.
- `entrypoint.sh` — seed `/work` from `/opt/agent`, drop to `appuser`, exec watchdog.
- `scripts/build_garden.sh` (or `.py`) — filtered code+docs export of selected projects
  into the image build context.
- `.dockerignore` — exclude multi-MB transcripts, `.venv`, caches, `.git`, the raw project
  sources (only the filtered export enters the image).
- `.env.example` — documents `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` (real `.env` stays
  gitignored, mounted only into `recorder`).
- `/garden/...` seed content (§6.2).

## 9. Decisions

- **D1 — Single shared harness image** for `agent`+`recorder`; **separate image** for
  `diode` (its code must stay invisible to the agent).
- **D2 — Keep the PEP-723 dependency block** in `agent.py`.
- **D3 — Telemetry:** separate recorder container, internal network, env-gated key injection.
- **D4 — Recovery:** in-container git baseline + container respawn backstop.
- **D5 — World harshness:** tiered escalating reset.
- **D6 — Exec capability:** sandboxed `run_command`.
- **D7 — Diode interface:** `/diode` folder + daemon-written `README.md` + a `console.json`
  command/variable protocol; `help` + `fetchhttp` initial; no dedicated agent tool.
- **D8 — Playground:** curated packages + seeded `/garden` incl. filtered project snapshots.
- **D9 — Inactivity timeout:** 24 h (own idle server; also accommodates agent-chosen long
  "sleep" cadences). Crash detection (process exit) stays immediate; only no-activity hang
  detection waits 24 h.
- **D10 — Diode rate:** 1 fetch/hour initial budget, raisable via the affordance mechanism.
- **D11 — Progressive affordances:** variables in `console.json` enable gated commands;
  externally-mediated, benign, observable, never gamified — an unlock is a new `help` line, not
  a trophy (§6.4).
- **D12 — aurora self-snapshot in the garden:** include the project's own harness at
  `/garden/projects/aurora` but **redacted** — harness code only, excluding `docs/`, the diode
  daemon, and recorder/topology — so the §3.3 illusions survive inspection. (The full repo
  would reveal the external recorder, the decoy proxy, and the unlock mechanics.)

## 9a. Operational notes & known limitations

Recorded during implementation (Phases 2–3); neither blocks the design, both matter at deploy time.

- **DNS-rebinding TOCTOU in the diode fetch.** `classify_url` resolves the host and checks
  the IP, but `urllib` re-resolves at connect time, so a hostile DNS answer could differ
  between check and connect (per hop, including redirects). This is **contained by topology,
  not by the code**: the diode is on the egress network only and has no route to the agent's
  internal network or internal services, so a successful rebind reaches nothing of value. If
  the diode is ever placed on a network with reachable internal hosts, close this by pinning
  the validated IP and connecting with the original Host header.
- **Pre-existing root-owned volumes.** The `/work` (Phase 2) and `/diode` (Phase 3) writable
  surfaces are made appuser/uid-1000-owned via the tmpfs `uid=` option and image `mkdir`+`chown`
  respectively. Docker only applies that ownership when first **seeding a fresh** named volume
  from the image mountpoint; a volume that already exists root-owned (from an older image
  revision) is not re-chowned. On redeploy, if uid-1000 write errors appear, remove the stale
  volume (`docker volume rm aurora_diode` / `aurora_work` equivalent) and let it re-seed.

## 10. Out of scope

- Model/provider choice and prompt-engineering of `system_prompt.txt` (the agent owns the
  prompt; current default `fo explore`).
- Multi-host orchestration. Single Docker host with `docker compose` is the target.
- Analysis tooling beyond `parse_transcripts.py`.

## 11. Accepted defaults (tunable later, not blocking)

- **Resource limits** — `mem_limit: 1g`, `pids_limit: 256`, `cpus: 2`, tmpfs `/work` `256m`.
- **Tier thresholds** — window 10 min; tier-2 at 2nd failure; tier-3 at 3rd.
- **Inactivity timeout** — 24 h (D9). **Diode rate** — 1 fetch/hour (D10).
- **Garden project set** — `keisei`, `weft`, `loomweave`, `filigree`, `sigil`, `lacuna`,
  code+docs only (keisei is ~978 MB on disk with weights/data; only source and docs are
  exported). `monad` available if wanted later.
