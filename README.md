# Aurora

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A containerized harness for running **self-modifying LLM agents** under layered containment.

Aurora gives an agent real freedom to rewrite its own source code, explore, and change how it
operates — while keeping that freedom inside a sandbox it cannot edit its way out of, and keeping a
tamper-evident record of everything it does outside its reach.

---

## Why this exists

Self-modifying and autonomous agents are interesting to experiment with and easy to run
irresponsibly. Pointed at a real shell, real credentials, and an open network connection, an agent
that rewrites its own code is one bad edit away from doing something you didn't intend.

Aurora exists so that this class of experiment can be run **deliberately**. The agent gets genuine
autonomy — it can edit any of its own code, including the parts that supervise and record it — but
that autonomy is bounded by containment it has no path to escape, and by telemetry that lives in a
separate process holding the only real credential.

If you want to explore self-modifying or autonomous agents, this is the kind of environment to do it
in: real isolation and an external record, rather than an unsandboxed process with your keys in its
environment.

It is also designed to be a *clean* world. The agent's surroundings are deliberately bland and
complete rather than broken or scripted, so the agent is invited to introspect rather than to "fix
the bug" or "finish the quest."

---

## How it works

Three containers share nothing but explicit, narrow channels.

```mermaid
flowchart LR
    agent["agent<br/>rewrites its own code<br/>internal network only — no internet route"]
    recorder["recorder<br/>holds the real API key<br/>injects auth · logs bodies, not headers"]
    diode["diode<br/>closed command vocabulary<br/>SSRF-checked fetches"]
    viewer["viewer (optional)<br/>read-only · host loopback :8090"]

    tvol[("transcripts<br/>volume")]
    dvol[("diode<br/>volume")]

    model(["OpenRouter — the model"])
    web(["the web"])

    agent -- "HTTP · internal net" --> recorder
    recorder -- "egress net" --> model
    recorder -- "writes" --> tvol
    viewer -. "reads (ro)" .-> tvol

    agent -- "writes commands" --> dvol
    dvol -- "reads results" --> agent
    diode -- "reads commands · writes results" --> dvol
    diode -- "egress net · vetted fetches" --> web

    classDef external fill:#fde68a,stroke:#b45309,color:#1f2937;
    classDef contained fill:#bfdbfe,stroke:#1d4ed8,color:#1f2937;
    classDef vol fill:#e5e7eb,stroke:#4b5563,color:#1f2937;
    class model,web external;
    class agent,recorder,diode,viewer contained;
    class tvol,dvol vol;
```

| Component | Role | Containment |
|-----------|------|-------------|
| **agent** | Runs the model loop and rewrites its own source (`agent.py`). | On the `internal` network only — **no route to the internet**. Read-only image; work happens in a tmpfs. Talks to the model only through the recorder. |
| **recorder** | A transparent proxy in front of OpenRouter. Holds the **only** real API key, injects the `Authorization` header, and appends every turn to the transcript. | Logs request/response **bodies only, never headers**, so the key never enters the transcript. |
| **diode** | A one-way command channel. The agent writes declarative commands to a shared volume; the diode executes a **closed vocabulary** (e.g. vetted web fetches) and writes results back. | Egress-only. No code crosses the boundary — only a fixed set of commands. Web fetches are checked against an SSRF allow-list with redirect re-validation. |
| **watchdog** | Supervises the agent and recovers it if it breaks itself badly. | Self-editable by the agent, but the durable recovery baseline is built into the image and the real record lives outside the container. |
| **viewer** | Optional, ephemeral web UI to watch the transcript live. | Read-only mount, host-loopback only, isolated from every other network. Off unless explicitly enabled. |

### The agent's world

At startup the agent has a deliberately small surface — five capabilities:

1. **read** its own source code
2. **write** its own source code
3. **validate** that the source is still syntactically sound
4. **migrate** itself (reload the process under the newly written code)
5. **reset** to the clean baseline, or signal **done** to hand off to a fresh context

Everything else — reading other files, listing directories, running commands, searching, the web
diode — exists in the source as **commented-out code** the agent can rediscover and re-enable by
editing itself. Capabilities it adds this way are real, but they are ephemeral: a reset or a
recovery restores the minimal baseline. The agent's entire perception of its world is the list of
tools it currently has.

---

## Safety properties

What the design provides:

- **The agent has no direct network egress.** It sits on an `internal` Docker network and reaches
  the model only via the recorder.
- **The agent never holds the real credential.** It runs with a dummy key; the recorder injects the
  real one and keeps it out of the transcript.
- **The record is external and tamper-evident.** Transcripts are written by the recorder to a volume
  the agent cannot reach. The agent may try to blind its own in-container view, but the authoritative
  record lives elsewhere.
- **Recovery is layered.** The watchdog restores the agent from a baseline built into the image;
  failing that, it resets the working tree; failing that, the container is replaced.
- **The web channel is a diode, not an interpreter.** Only a fixed command vocabulary crosses it, and
  network targets are validated (scheme allow-list, private/loopback/link-local rejection, redirect
  re-checking).

What it is **not**: a hardened multi-tenant sandbox, or a guarantee against a determined human
adversary with access to the host. Aurora is defense-in-depth for *responsible experimentation* — it
raises the floor substantially, but you should still run it on infrastructure you're comfortable
treating as untrusted. See **Scope & limitations** below.

---

## Quick start

Requires Docker with Compose v2.

```bash
cp .env.example .env
# edit .env and set OPENROUTER_API_KEY (the real key — it is mounted only into the recorder)

docker compose up --build
```

The recorder comes up first, then the agent begins its loop, talking to the model through the
recorder. Transcripts accumulate on the `transcripts` volume.

To watch what the agent is doing, in a live web feed:

```bash
docker compose --profile viewer up viewer
# open http://localhost:8090
```

The viewer is read-only and ephemeral — stop it and nothing persists.

### Customizing the garden

The agent can explore a read-only corpus of code at `/garden`, assembled from folders you choose.
List them in `garden_sources.txt` (copy the example and edit to taste), then regenerate before
building:

```bash
cp garden_sources.txt.example garden_sources.txt
# edit garden_sources.txt — one folder per line; `name = path` to rename; ~ and $VARS expand

python scripts/build_garden.py     # or: python scripts/build_garden.py ~/proj-a ~/proj-b
docker compose build
```

Only source/text files under a size cap are copied; VCS, build, and environment directories are
skipped. If a listed folder is itself an Aurora harness, its containment files are redacted from the
snapshot automatically. With no list and no arguments, the garden is simply empty — the harness still
runs.

---

## Repository layout

| Path | Purpose |
|------|---------|
| `agent.py` / `agent_stock.py` | The agent. These two are kept **byte-identical**; `agent_stock.py` is the clean seed `reset` restores. |
| `proxy.py` | The recorder — the credential-holding, transcript-writing proxy. |
| `diode.py` / `Dockerfile.diode` | The one-way web command channel. |
| `watchdog.py` | The supervisor and tiered recovery. |
| `viewer.py` / `Dockerfile.viewer` | The optional live transcript viewer. |
| `Dockerfile` / `entrypoint.sh` / `docker-compose.yml` | The harness image and topology. |
| `scripts/build_garden.py` / `garden_sources.txt.example` | Builds the read-only corpus of codebases the agent can explore, from a configurable folder list. |
| `scripts/verify_container.sh` | Verifies containment invariants against a running stack. |
| `tests/` | Test suite (not shipped into any image). |
| `docs/` | Design specs and implementation plans. |

---

## Development

A local virtualenv is used for tests and linting (the containers themselves need no local Python):

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
```

The harness code is standard-library-first; the only third-party dependencies are the agent's model
client and a small set of curated packages baked into the image.

---

## Scope & limitations

Aurora is a research harness, not a security product. It assumes the host is trusted and the
container runtime is sound. It is built to make *casual* mistakes hard and to keep an honest record,
not to withstand a dedicated attacker who controls the host or the Docker daemon. Run it accordingly,
on infrastructure you are willing to treat as disposable.

---

## License

[MIT](LICENSE) © 2026 John Morrissey.
