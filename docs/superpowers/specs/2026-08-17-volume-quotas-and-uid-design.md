# Volume Quotas and Non-Colliding UID — Design

**Status:** approved design, ready for implementation planning
**Date:** 2026-08-17
**Origin:** `docs/security-audit-2026-08-17.md`, findings D1 and the structural addendum item 2.

## Problem

Two findings from the containment audit, both demonstrated against the running stack rather
than inferred from configuration.

### D1 — the agent can fill the host root filesystem

`/state`, `/diode`, `/telemetry`, `/pump`, `/llm/console` and `/transcripts` are Docker named
volumes under `/var/lib/docker/volumes/`, which is `/dev/nvme0n1p3` — the host root filesystem.
No quota is active (`/` is mounted plain `rw,relatime`; no `prjquota`, no `usrquota`) and the
agent container carries no block-I/O limit: `BlkioWeight: 0`, every `BlkioDevice*Bps/IOps`
unset, no `io.max` in its cgroup.

Measured:

```
$ docker exec aurora-agent-1 dd if=/dev/zero of=/state/.probe bs=1M count=1024 conv=fdatasync
1073741824 bytes (1.1 GB, 1.0 GiB) copied, 0.963092 s, 1.1 GB/s
```

That is sustained writeback, not page cache. 790 GiB of free space at 1.1 GB/s is roughly
twelve minutes. `df` inside the container truthfully reports the host's 1.7 T. Exhausting `/`
stops the Docker daemon, the recorder, the diode, the stage, and anything else the host runs.

The design already solved this once: `/build` is a preallocated loop-mounted ext4 image
precisely so that a full `/build` cannot consume main-disk space beyond a fixed footprint. That
reasoning was never extended to the six volumes the agent actually lives in.

### The uid collision

Container uid 1000 (`appuser`) **is** host uid 1000 (`john`), the repository owner. Files the
services write to host bind mounts appear on the host as `john`:

```
$ ls -la volumes/sense/
drwxrwxr-x 11 john john 4096 Aug 17 15:28 .
```

There is a concrete degradation chain, not merely a theoretical one. If `build.img` is not
loop-mounted — after a reboot with no fstab entry, which the entrypoint warns about and then
proceeds past — `/build` becomes a plain directory **inside the repository working tree**,
writable by the agent as `john`.

## Goals

1. No path from the agent to unbounded host disk consumption.
2. Container uids never equal the host repository owner.
3. Failure on exhaustion is clean and local, not a host-wide outage.
4. Reuse the pattern already in the repository rather than inventing a second one.

## Non-goals

- Changing container runtime (gVisor, Kata, Firecracker). Recorded in the audit addendum as
  items 3 and 4, deliberately on file. Neither addresses D1, which is a quota problem, nor the
  F1-class application bugs found earlier.
- Daemon-wide `userns-remap`. See Phase 2.
- Bounding `/work` or `/tmp`; both are tmpfs already bounded by the container's memory cgroup.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Quota mechanism | Loop-mounted ext4 images | ext4 project quotas would need `tune2fs -O project,quota` on the **live host root filesystem** — realistically a reboot, with real risk. Loop images need no host-fs surgery and reuse `create_build_volume.sh`. |
| Granularity | One image per volume | A runaway `/diode` cannot starve `/state`, which holds the agent's `memory.md`, `journal/` and `tools/`. |
| Allocation | Preallocated, not sparse | See below. |
| Volume wiring | Direct bind mounts | Matches the existing `/build` pattern and permits relative paths. `driver_opts` bind was measured and works, but its one advantage is illusory — see "Pump backstop". |
| Scope | All six volumes + archive retention | Leaves no unbounded path behind. |
| uid | 65532 | Free on this host (`nobody` is 65534); the conventional distroless "nonroot" uid. |
| userns-remap | Phase 2, separate daemon | Daemon-wide remap would orphan 88 unrelated volumes and the operator's other projects. |

### Why preallocated rather than sparse

A sparse image bounds consumption just as hard: the ext4 filesystem inside is sized to the image
and returns ENOSPC at that size regardless of backing allocation. The bound is not the
difference.

The difference is the failure mode. A sparse backing file must **grow** on each new write; if
the host filesystem fills from another source first, that growth fails, the loop device returns
EIO, and ext4 inside typically remounts read-only. Preallocation commits every block up front,
so writes fail only as honest ENOSPC. For a volume holding the agent's persistent memory, clean
ENOSPC beats a read-only remount mid-write.

Costs, both accepted:

- Backups balloon. `fallocate` allocates real extents (`du` reports 13 G on disk for a 12 G
  image), so the file is genuinely not sparse and `tar -S` / `rsync -S` / `cp --sparse` cannot
  help.
- Loop double-caching: pages cached once for the backing file and once for the inner
  filesystem. Relevant mainly to `transcripts`. Mitigable with `losetup --direct-io=on` if it
  ever shows up in practice; not pre-optimised.

Two suspected costs were measured and dismissed: `fallocate -l 12G` takes **0.114s**
(extent-based, no zeroing), and loop-device headroom is a non-issue — `max_loop` reads `8` but
**17** are already in use by snapd, because the kernel creates them on demand via
`/dev/loop-control`.

## Design

### Phase 1a — uid 65532

Every image moves off uid 1000. The complete reference set, verified by grep:

| Location | Change |
|---|---|
| `Dockerfile:26` | `useradd --uid 1000 appuser` → `65532` |
| `Dockerfile.diode:5` | `useradd --uid 1000 diodeuser` → `65532` |
| `Dockerfile.stage:3` | `useradd --uid 1000 stageuser` → `65532` |
| `Dockerfile.sense:14` | `useradd --uid 1000 senseuser` → `65532` |
| `Dockerfile.viewer:3` | `useradd --uid 1000 vieweruser` → `65532` |
| **`docker-compose.yml:105`** | **`/work:size=8g,uid=1000,gid=1000` → `65532`** |
| `scripts/create_sense_volume.sh:16,18,20` | ownership check and `chown` target |
| `scripts/create_build_volume.sh:24` | `mkfs -E root_owner=1000:1000` |
| Comments in `Dockerfile.diode`, `Dockerfile.sense`, `docker-compose.yml:177` | text updated to match |

The compose `/work` tmpfs line is the one that fails loudly and confusingly if missed: the agent
would be unable to write its own workspace.

Constraint that must survive: the **agent and diode share `/diode`** and must therefore keep a
*shared* uid — both become 65532, exactly as both are 1000 today.

Read-only operator mounts (`/vendor`, `/corpus`, `books`) are mode `drwxrwxr-x`, so uid 65532
reads them without change. Verified:

```
$ docker run --rm --user 65532:65532 -v .../volumes/corpus:/corpus:ro alpine ls /corpus
  uid 65532 CAN read /corpus
```

### Phase 1b — six loop images

Generalise `scripts/create_build_volume.sh` into `scripts/create_volume_image.sh <name> <size>`,
creating and mounting `volumes/<name>.img` at `volumes/<name>/` with
`mkfs.ext4 -m 0 -E root_owner=65532:65532,nodiscard`. `scripts/prepare_host.sh` calls it for each
volume. `create_build_volume.sh` is kept as a thin caller that delegates to the generic script,
so the command named in existing operator notes and fstab guidance keeps working.

`docker-compose.yml` replaces the six named volumes with bind mounts on those directories,
following the `/build` precedent:

```yaml
- ${AURORA_STATE_DIR:-./volumes/state}:/state
```

Sizes:

| volume | size | reasoning |
|---|---|---|
| `transcripts` | 8 G | 128 MiB live file plus gzip archives under the new retention cap |
| `telemetry` | 12 G | see note below |
| `diode` | 4 G | mp3 utterances and fetched media accumulate |
| `state` | 2 G | agent memory; currently 696 K, grows slowly, sized generously |
| `pump` | 1 G | entries plus per-entry capped logs, `PUMP_MAX_ENTRIES` 32 |
| `llm_console` | 64 M | a single JSON declaration file |

Roughly 27 GiB preallocated on top of `/build`'s existing image.

**Telemetry sizing is deliberately under the worst case.** `watchdog.mirror_work` can
transiently hold `work` + `work.tmp` + `work.old` ≈ 3× an 8 GiB `/work` = 24 GiB. 12 G is
chosen anyway because `mirror_work` already catches `OSError` and returns cleanly, so a full
telemetry volume degrades to *the stage's telemetry going stale* rather than a crash. That is
the correct failure mode and is preferable to committing 24 GiB against a pathological case.

### Phase 1c — transcript archive retention

`proxy.py:rotate_if_needed` gzips the transcript at `TRANSCRIPT_MAX_BYTES` (128 MiB) and renames
the archive into place, but nothing ever deletes old archives. Add a retention cap — keep the N
newest archives, remove the oldest — read from `TRANSCRIPT_ARCHIVE_KEEP`, defaulting to `20`,
with unit tests alongside the existing rotation tests. At roughly 10:1 gzip on JSONL, 20
archives is on the order of 260 MiB, comfortably inside the 8 G bound.

`proxy.py` ships in the agent image, so any new text stays bland and factual per invariant 2.

### Migration and cutover

`/state` holds the live agent's `memory.md`, `journal/`, `tools/` and `sense_labels.json`. The
cutover must not lose them.

1. `docker compose down`.
2. Create and mount the six images.
3. `cp -a /var/lib/docker/volumes/aurora_<name>/_data/. volumes/<name>/` for each.
4. `chown -R 65532:65532` the new mounts, plus existing `volumes/sense` and `volumes/build`.
5. Rebuild images, `docker compose up`.
6. Verify (below).
7. **Only then** remove the old named volumes.

The old named volumes remain untouched until verification passes, so rollback is reverting
`docker-compose.yml` and bringing the stack back up.

### The hazard this introduces

A loop image that is not mounted degrades **silently** to the host root filesystem: the bound
vanishes and nothing reports it. This is the same chain that already exists for `/build`, where
the entrypoint warns and proceeds.

Mitigations, all required:

- `entrypoint.sh` generalises its existing `/build`-vs-`/vendor` device comparison into a check
  across every bounded mount, warning per mount that is not its own filesystem. Text stays bland
  and factual — the agent can reach `entrypoint.sh` by re-enabling a file tool.
- `scripts/verify_container.sh` gains an assertion that each bounded mount reports a distinct
  device from the host root.
- `prepare_host.sh` emits the fstab lines needed for reboot persistence.

Without these, one reboot quietly undoes the entire exercise.

## Invariant and documentation impacts

**The pump backstop changes.** CLAUDE.md invariant 3 documents `docker volume rm aurora_pump` as
the out-of-container backstop against a pump entry that outlives the recovery ladder. Measured:
`docker volume rm` on a bind-backed volume **does not delete the data**.

```
docker volume rm: succeeded
RESULT: data SURVIVED -> 'docker volume rm aurora_pump' would NO LONGER be a backstop
```

This is true for both bind mounts and `driver_opts` bind volumes, which is why `driver_opts`
carried no real advantage. CLAUDE.md must be updated to name clearing the host directory as the
backstop instead. **This is a containment-relevant documentation change, not cosmetic** — the
backstop is the only defence against a pump entry that survives every recovery tier.

Other documentation edits:

- `docker-compose.yml` says `/build` is "a preallocated 10 GiB ext4 loop image"; the image in
  service is 5 GiB (audit finding D2). Reconcile the comment with the enforced size.
- CLAUDE.md invariant 5 (`/state` is latent, private, inert) should note the new backing without
  implying anything scans it. Nothing in the running system reads `/state`; migration is an
  operator action taken with the stack down, so the invariant holds unchanged in substance.
- CLAUDE.md's containment section gains the volume bounds as a stated property.

## Testing

- **Unit:** transcript retention (archives pruned oldest-first, cap respected, no pruning below
  the cap), and the generalised mount check.
- **Host scripts:** extend `tests/test_host_scripts.py` for `create_volume_image.sh` argument
  handling and idempotence — an existing image must not be reformatted.
- **Container:** `scripts/verify_container.sh` asserts each bounded mount is its own device, and
  that every service runs as uid 65532.
- **Manual, once, at cutover:** repeat the D1 probe and confirm it now stops at the volume bound
  instead of consuming host space:
  ```
  docker exec aurora-agent-1 dd if=/dev/zero of=/state/.probe bs=1M count=4096
  # expect: "No space left on device" well before the host is affected
  ```
  Then confirm the host's free space is unchanged, and remove the probe file.
- **Regression:** full suite green
  (`.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`) and
  `ruff format` / `ruff check` clean.

Note: two tests in `tests/test_stage_server.py` are already red for unrelated reasons (audit
finding F5 — `DISPLAY_TURNS` was raised 6→10 without updating the assertions). They are out of
scope here; the plan should not fix them silently, but should not be blocked by them either.

## Phase 2 — on file, not built

Separate Docker daemon for aurora with `userns-remap`:

- A second `dockerd` with its own `data-root` and a `dockremap` subuid/subgid range, reached via
  a dedicated `docker context`.
- Gains root-in-container ≠ root-on-host, which Phase 1a's uid change does not provide. That
  matters only if the agent defeats `cap_drop: ALL` plus `no-new-privileges` — i.e. a kernel
  bug — but it is the correct end state.
- Deferred because daemon-wide remap would orphan 88 unrelated volumes and disrupt the
  operator's other projects, and because a second daemon duplicates image storage.
- Prerequisite already partly present: `/etc/subuid` and `/etc/subgid` carry
  `john:100000:65536`; a `dockremap` entry would need creating.

## Risks

| Risk | Mitigation |
|---|---|
| Migration loses agent state | Old named volumes retained until verification passes; rollback is a compose revert |
| A uid reference is missed | Complete grep-verified reference table above; `verify_container.sh` asserts uid 65532 across all services |
| An image is not mounted after reboot | fstab lines from `prepare_host.sh`; entrypoint warning; `verify_container.sh` device assertion |
| Sizes wrong in practice | ext4 images can be grown later (`fallocate` + `resize2fs`) without recreating |
| Pump backstop silently lost | Explicit CLAUDE.md edit is a required deliverable, not a follow-up |
