# Transcript Rotation, /work Capacity, and Garden Expansion — Design

**Date:** 2026-08-13
**Status:** Approved (design discussion in session; three sections approved together)
**Context:** The live transcript grew to 943 MB over two months and OOM-killed the stage
container the moment the stream page polled `/api/stream` (fixed by bounded reads,
commit 5175917). The file itself still grows without bound — this design adds rotation
at the writer. Bundled with it: a larger `/work` allocation and a broader garden package
set, so the agent has room and material for more elaborate tooling.

## Part 1: Transcript rotation in the recorder

**Where:** `proxy.py` only. It is the sole writer of both transcript files
(`agent_life_transcript.jsonl` and `agent_life_transcript.txt`). No stage or viewer
changes: the stage already tolerates the live file shrinking (incremental line counter
rescans on truncation), and archives appear in the operator console's `transcripts`
browse root, downloadable via the streaming download route.

**Mechanism:**

- A module-level `threading.Lock` guards every transcript append (the proxy is a
  `ThreadingMixIn` server; append + rotation check must not interleave).
- `TRANSCRIPT_MAX_BYTES` env var, default `134_217_728` (128 MiB).
- After each append, `rotate_if_needed(path)` checks `os.path.getsize(path)` against
  the threshold. When exceeded:
  1. Stream-compress the live file in 64 KiB chunks (never loading it whole) into
     `<stem>-<UTC %Y%m%d_%H%M%S>.<ext>.gz` in the same directory
     (e.g. `agent_life_transcript-20260813_101500.jsonl.gz`).
  2. Write to a `.tmp` name first, then `os.rename` to the final name — a crash never
     leaves a half-written archive under a final name.
  3. Truncate the live file to empty.
- Timestamped names sort lexicographically = chronologically.
- Archives are kept forever (they compress ~20:1; the full life record stays
  browsable from the console). Manual deletion is the only pruning.

**Failure handling:** if compression or rename fails (e.g. disk full), the live file is
left untouched, a factual error is printed to stderr, and the next append retries.
Rotation failure never blocks appends.

**Known consequence:** the stream page's "transcript turns" stat counts the live file,
so it resets at each rotation. Accepted as-is ("turns this segment").

**Register:** `proxy.py` ships in the harness image and is agent-discoverable.
Docstrings stay bland and factual; no housekeeping narrative.

## Part 2: /work capacity

In `docker-compose.yml`, agent service:

- tmpfs `/work` size: `256m` → `4g`.
- `mem_limit`: `1g` → `5g` (tmpfs pages count against the container's memory cgroup;
  4 GiB file headroom + 1 GiB process. The two values must move together — noted in a
  compose comment beside them).
- `cpus: 2` and `pids_limit: 256` unchanged.
- `/work` stays tmpfs: ephemeral wipe-on-respawn recovery semantics are untouched, and
  tmpfs consumes RAM only for bytes actually written (host has 61 GiB).

## Part 3: Broader garden packages

Additions to `requirements-agent.txt` (none from the forbidden categories — no ML
runtimes, local models, browser engines, agent frameworks, cloud SDKs, or service
daemons):

| Package | ~Installed size | Opens |
| --- | --- | --- |
| Pillow | 12 MiB | raster image creation and manipulation |
| matplotlib | 40 MiB | plotting/figures (pairs with existing numpy) |
| pygments | 5 MiB | syntax highlighting and lexers |
| lark | 3 MiB | grammars and parser construction |
| python-chess | 5 MiB | a closed formal game world |
| pycryptodome | 8 MiB | ciphers, hashing, signatures |
| sortedcontainers | <1 MiB | ordered collections |
| more-itertools | <1 MiB | iteration utilities |
| python-dateutil | 2 MiB | date parsing/arithmetic |
| msgpack | 1 MiB | compact binary serialization |

Estimated ~78 MiB total. The implementation measures the agent image before and after
on the same host per CLAUDE.md; if the delta exceeds 100 MiB, trim starting with
matplotlib. pandas was considered and dropped (≈65 MiB; with matplotlib it breaches the
budget, and numpy + sqlite cover tabular work).

**Knock-ons in the same change:**

- Regenerate `garden_export/` via `scripts/build_garden.py` so `runtime.md` lists the
  new packages factually (garden invariant: names and factual constraints only, no
  proposed applications).
- The `runtime.md` constraints line updates to the new truth: 5 GiB memory, 4 GiB
  `/work`. The garden must not misstate the agent's limits.

## Error handling summary

- Rotation: fail-open for appends, fail-safe for archives (tmp + rename), stderr only.
- Compose: no runtime error paths; values are static.
- Garden: `build_garden.py` failure fails the build, not the runtime.

## Testing

- Unit tests for `rotate_if_needed` (tmp_path): below threshold no-op; above threshold
  produces a `.gz` whose decompressed bytes equal the original, live file truncated to
  0, appends continue afterward; unwritable archive destination leaves the live file
  intact and appends still succeed.
- Compose/topology: substring guards for `size=4g` and `mem_limit: 5g`.
- Garden: existing generation test (if present) plus assertion that runtime.md contains
  the new package names and the updated memory/`/work` figures.
- Image-size measurement recorded in the implementation notes (not a unit test).
