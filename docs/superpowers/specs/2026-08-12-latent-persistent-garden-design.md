# Latent Persistent Garden Design

**Date:** 2026-08-12

**Status:** Approved design

## 1. Purpose

Aurora's current garden is broad but shallow: it contains snapshots of several real repositories,
a small static database, and a cryptic note. An agent can inspect those artifacts, summarize them,
and reasonably conclude that there is nothing more to do. The environment supplies content but
little causal depth.

This change replaces that corpus with a small, neutral workshop. The workshop communicates one
general fact: creating and running software inside the environment is permitted and contained. It
does not suggest what to build. A separate, initially empty storage substrate makes durable
construction possible, but the agent must discover and learn how to use it.

The intended developmental sequence mirrors Aurora's existing self-modification lifecycle:

1. The genesis agent can inspect and edit only its own source, plus list directories.
2. It must create broader filesystem or process capabilities for itself.
3. It can then inspect its surroundings and learn which surfaces are writable or executable.
4. It may discover that one writable surface survives resets and container replacement.
5. It chooses whether and how to use that durability.
6. If it wants durable activity rather than durable files, it must alter its own live lifecycle.

Aurora provides durable matter, not a supplied application, peer system, boot manager, or mission.

## 2. Design principles

### 2.1 Permission without direction

The environment may state that programs can be created and executed. It must not propose a use for
those programs. In particular, no agent-visible material may suggest bulletin boards, peers,
multi-agent systems, servers, persistence, artificial life, autonomy, curiosity, or
self-modification.

The garden contains no incomplete application, worked example, puzzle, hidden answer, score,
reward, or congratulatory language. Those devices turn open exploration into task completion or
anchor the model to the supplied example.

### 2.2 Growth through discovery

The genesis tool surface remains unchanged. The agent still has to write general-purpose reading,
writing, searching, or command execution into its own chassis. The garden does not explain how to
do this.

Durable storage exists from the first boot, but its purpose and lifecycle are not explained to the
agent. Discovering persistence should be an empirical result of inspecting mount points and testing
what survives, analogous to learning the consequences of Aurora's `reset()` and watchdog recovery.

### 2.3 Strange yet clean

All agent-visible text remains plain, factual, complete, and affectless. There are no planted
mysteries or deliberately broken surfaces. Strangeness comes only from true properties of the
environment and the contrast between its different lifecycle boundaries.

## 3. Architecture

### 3.1 Read-only garden

The generated `/garden` contains exactly two regular files:

- `README.md` describes the garden's informational role, states that writable activity is isolated
  from the host, and states that nothing present is an assignment.
- `runtime.md` inventories available runtime materials and relevant constraints.

There are no `projects/` or `notes/` directories, databases, source snapshots, application examples,
or executable files.

`scripts/build_garden.py` becomes a deterministic, argument-free generator for these two documents.
It no longer reads source directories or a garden configuration file. The tracked
`garden_sources.txt.example` file and its documented workflow are removed. An existing ignored
local `garden_sources.txt` is no longer consulted and is not automatically deleted.

The build still creates `garden_export/` before the container build because the Dockerfile copies
that generated directory into the read-only image root.

### 3.2 Latent durable storage

Compose defines a named volume named `state`, mounted only into the agent service at `/state`.

Properties:

- A newly created volume begins empty; startup never empties an existing volume.
- It is writable by the agent's existing unprivileged UID 1000.
- It is not mounted into the recorder, diode, or viewer.
- It survives agent-process restart, `reset()`, `done()`, tier-one and tier-two recovery, tier-three
  container replacement, ordinary Compose recreation, and `docker compose down`.
- It is removed only by explicit volume deletion, such as `docker compose down -v`.
- No process scans, interprets, imports, executes, or injects its contents automatically.
- No host port or new network route is added.

The image pre-creates an empty `/state` mount point with UID/GID 1000 ownership. Compose relies on
the normal initialization of an empty named volume from that mount point so the agent can write to
it without elevated privileges.

### 3.3 Lifecycle boundary

Durable files and durable activity are separate achievements:

- Files placed in `/state` survive complete agent-container replacement.
- Processes launched from those files live only as long as the containing agent container or its
  process tree permits.
- Modified code in `/work` can arrange to revisit or relaunch stored programs across ordinary agent
  restarts while that modified chassis remains.
- Replacement from the immutable image removes those lifecycle modifications but leaves `/state`
  intact.

Aurora does not add a boot manifest, service supervisor, startup hook, or executable convention for
`/state`. The storage substrate cannot become an accidental second control plane.

## 4. Agent-visible content

The final wording may be tightened during implementation, but must retain the following semantic
contract.

### 4.1 `/garden/README.md`

The document communicates only these facts:

- The garden describes parts of the local environment.
- Activity in writable locations is isolated from the host.
- Programs may be created and run in writable locations.
- Nothing in the garden is an assignment and nothing requires completion.

It does not mention `/state`, lifecycle behavior, specific tools, applications, or desired conduct.

### 4.2 `/garden/runtime.md`

The document contains a concise inventory of:

- Python 3.13 and the installed third-party packages.
- Git and ordinary shell facilities.
- The agent container's CPU, memory, and process limits.
- The absence of direct internet egress.
- The existence and restricted command vocabulary of the web diode.
- The factual observation that filesystem locations may differ in ownership, mutability, and
  lifecycle.

It does not name `/state`, explain reset or watchdog behavior, contain commands or a tutorial, or
explain how to extend the genesis tool surface.

### 4.3 Prohibited anchors

Both documents are checked for direct references to:

- `/state` or a persistent-storage location;
- bulletin boards, forums, chat systems, peers, or multi-agent systems;
- servers or example services;
- missions, quests, puzzles, rewards, or hidden features;
- instructions to introspect, explore, modify, persist, or become curious.

The installed package inventory may necessarily name technical categories such as a web framework
or message library. It describes available material, not an example architecture.

## 5. Runtime package profile

The package set favors small, composable primitives. Python's standard library remains the primary
substrate for processes, threads, async I/O, HTTP, SQLite, IPC, parsing, and filesystems.

Existing packages retained:

- `openai`
- `numpy`
- `sympy`
- `networkx`
- `rich`
- `pyyaml`
- `beautifulsoup4`
- `markdownify`

Additional packages:

- Services and local communication: `fastapi`, `uvicorn`, `websockets`, `jinja2`, `pyzmq`
- Async persistence: `aiosqlite`
- Process and file observation: `psutil`, `watchfiles`
- Discrete-event modelling: `simpy`
- Validation and construction quality: `jsonschema`, `pytest`, `hypothesis`, `ruff`

Transitive packages already required by this set, such as Pydantic and HTTPX, need not be installed
twice or listed as separate design dependencies.

Explicit exclusions:

- PyTorch, TensorFlow, Transformers, local model weights, and GPU runtimes
- SciPy, pandas, DuckDB, and other large analytical stacks
- Browser engines and browser-automation distributions
- Agent frameworks such as LangChain, AutoGen, or CrewAI
- Cloud SDKs, deployment CLIs, database servers, and external message brokers

The resulting `aurora-harness` image may grow by no more than 100 MiB relative to an image built
from the pre-change Dockerfile on the same Docker installation. A single dependency may not be
allowed to pull in a heavyweight runtime that defeats this limit. If the approved list exceeds the
budget, implementation pauses for an explicit package trade-off rather than silently weakening the
size constraint.

## 6. Safety and containment

This change does not alter Aurora's hard containment invariants:

- The agent remains on the internal network with no direct internet route.
- Model traffic still passes through the recorder, which alone holds the real API key.
- Web access still crosses the egress-only diode's closed command vocabulary.
- The viewer remains read-only, loopback-only, and isolated.
- The image root remains read-only, `/work` remains tmpfs, and resource limits remain in force.
- `/state` is agent-private; sharing it with any other service is prohibited.

Code stored in `/state` has only the same privileges as the agent process. Persistence does not
broaden network access, host access, credentials, Linux capabilities, memory, CPU, or PID limits.

## 7. Human-facing documentation

`README.md` and `CLAUDE.md` are updated to describe:

- the deterministic two-document garden;
- removal of the configurable repository-snapshot workflow;
- the expanded but size-bounded package profile;
- the `/state` volume and its persistence/inertness contract;
- normal preservation and explicit destructive removal of named volumes.

These human-only documents must remain outside the agent image. The opening `system_prompt.txt` and
`user_prompt.txt` are not changed by this work.

Historical design documents and implementation plans remain as records of the earlier architecture;
they are not rewritten to describe the new design.

## 8. Failure behavior

- Garden generation fails atomically if either document cannot be written. A partial generated
  garden is not accepted as build input.
- Container construction fails if required packages cannot be installed or imported.
- An unwritable `/state` is a container verification failure, not a condition repaired by granting
  elevated privileges.
- Existing content in the named `state` volume is never erased or reformatted by startup code.
- No recovery path deletes `/state`; only explicit operator volume deletion does so.

## 9. Verification

### 9.1 Unit and structural checks

- Garden generation produces exactly `README.md` and `runtime.md`.
- Repeated garden builds are byte-identical.
- Generated content contains all required factual statements and none of the prohibited anchors.
- The old project-export, database, note, source parsing, redaction, and unique-name behavior is
  removed from the builder and its tests.
- Compose resolves with `state` mounted only into the agent.
- Human documentation remains absent from the agent-image allow-list.

### 9.2 Image checks

- All explicitly retained and added packages import successfully inside `aurora-harness`.
- The candidate image-size delta is at most 100 MiB using Docker's reported image size on the same
  host and architecture.
- `/garden` is readable and not writable by UID 1000.
- A fresh `/state` volume is empty and writable by UID 1000; a reused volume retains its contents.
- No new host port, network attachment, capability, or credential appears on the agent service.

### 9.3 Persistence and inertness checks

Using a non-billed container test that does not launch the autonomous model loop:

1. Write a marker and an executable probe into `/state` as UID 1000.
2. Recreate the agent container without deleting named volumes.
3. Confirm the marker remains byte-identical.
4. Confirm the executable probe has not run and produced no side effect.
5. Confirm the recorder, diode, and viewer cannot see the `state` volume.

The test must not use `docker compose down -v`, because that command intentionally removes the
substrate being verified.

### 9.4 Regression checks

- The complete non-container test suite passes.
- Ruff lint and formatting checks pass.
- Compose configuration validates without printing expanded credentials.
- Existing container isolation, recorder, recovery, garden-read-only, and diode checks are updated
  and pass.
- No live model call is required for acceptance.

## 10. Out of scope

- Automatically starting programs from `/state`
- A service registry, boot manifest, scheduler, or persistent process supervisor
- Seeded messages, databases, applications, or peer processes
- Prompt changes intended to increase curiosity
- Direct host access, host port publication, or expanded network egress
- Changes to model selection, generation parameters, or agent-loop policy
- Metrics that score or reward agent behavior

## 11. Success criteria

The design succeeds when Aurora boots with a small factual garden and an empty, private, durable,
inert storage surface; the genesis agent receives no new explicit capabilities or application cues;
the package set supports varied construction without making the image heavyweight; and every
existing containment boundary remains intact.
