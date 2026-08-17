# On-demand sense grabs

Date: 2026-08-17. Status: design approved (operator), spec pending review.
Amends `2026-08-15-ambient-sense-feed-design.md`, which stays in force for
everything not restated here. Deploy only at an incarnation boundary: the
agent-image change is one mountpoint line — do not restart the live agent
container to apply it.

## Problem

The ambient feed gives the agent a world that moves whether or not it looks.
It gives it no way to look *now*, at a chosen window. Perception is entirely
involuntary: the ring turns on a fixed clock, and attention is not a resource
the agent can spend.

Two consequences follow. Capture cost is coupled to catalogue size, so the
catalogue stays at nine feeds because ambient capture of fifty would be
expensive. And the record is the same for every incarnation regardless of
what any of them cared about.

## What this adds

A command vocabulary through which the agent requests a frame from a named
feed, and a naming config it can edit to label those feeds itself. Both
follow the diode's console/output protocol rather than inventing a
mechanism.

The ambient ring is **not** replaced. It continues at its current cadence on
the curated feeds. On-demand capture is purely additive: the world keeps
turning, and the agent can also choose to look.

## Decisions taken

- **Hybrid, not replacement.** The involuntary record is the part of the
  ambient design that cannot be recovered once removed; on-demand is layered
  on top of it, not in place of it.
- **Names are agent-authored, never operator-seeded.** A config shipping with
  real place names would be the operator captioning the agent's world — the
  same prose the `delogo` filter on feed 3 and the skyline crop on feed 4
  exist to keep out of frame. The agent can see a frame through the seeded
  `vision_1` stream (`qwen/qwen3.7-flash`, `image_input`), so it can reach a
  label from evidence.
- **A feed's name defaults to its own id.** `sense1..senseX` was considered
  and rejected: the live feed ids are `0`, `3`–`10`, so a sequential
  `senseN` is a second numbering scheme that does not align with the first,
  and the ambiguity would reach filenames and the public page. Defaulting to
  the id gives one scheme and makes "the agent has authored a label" legible
  at a glance.
- **Filenames carry a random hex token, not a timestamp.** The 2026-08-15
  ruling that "filenames carry no schema" is *kept*, not reversed: a hex
  token from `secrets.token_hex(4)` carries no ordering and no arrival time.
  File mtime carries arrival time as it does today. A monotonic serial was
  considered and rejected — it leaks ordering and needs counter state on the
  volume.
- **The grab verb takes an index into the operator's feed list, never a
  URL.** See Containment.
- **Arbitrary-video stills are deferred.** See Out of scope.

## Architecture

### The console volume

A new named volume `sense_console`, mounted read-write at `/sense-console` in
both the agent and the sense service — the shape `diode:/diode` already uses.
A named volume rather than a bind mount, so there is no host-ownership step
of the kind `scripts/create_sense_volume.sh` performs for `/sense`.

Two containers writing one volume works because their uids coincide by
construction: `appuser`, `diodeuser` and `senseuser` are all uid 1000, and
each image pre-creates its own mountpoint owned by that user, from which
Docker seeds a fresh named volume. No mode bit is involved. `Dockerfile` and
`Dockerfile.sense` must both pre-create `/sense-console` the same way, or the
cap-dropped services cannot write it.

`/sense` itself is unchanged: written by sense, mounted read-only into the
agent and the stage.

```
/sense-console/console.json   {commands: [...], variables: {...}}   agent-writable
/sense-console/state.json     published by sense: vocabulary, names, allowance
/sense-console/HELP.md        written by sense on the help command
/sense-console/output/        command results
```

`console.json` is created by sense when absent, as `diode.py` does for its own
console. Sense reads the command list, executes it, and atomically clears
**only** the command list, preserving `variables`.

### Command vocabulary

| Command | Effect |
|---|---|
| `grab <id>` | Capture one frame from feed `<id>` into the grabs tree |
| `help` | Write the vocabulary to `HELP.md` |

A `list` command was considered and cut: `state.json` already publishes the
feed ids, their current names, and the remaining allowance, and the diode's
precedent is that `state.json` carries current state while commands do work.

`<id>` must be a member of the operator's configured feed set. A grab is
asynchronous — yt-dlp resolve plus ffmpeg is tens of seconds and cannot
block a turn — so the command returns immediately and the result file names
the written frame once it exists.

Renames are made by editing `variables.names`, not by a command; the
validation in *Naming* below is what keeps that write bounded.

### Storage layout

```
/sense/<id>/NNN.jpg                    ring slots, ambient, overwritten in place
/sense/grabs/<id>/<slug>_<hex>.jpg     on-demand, accretes
/sense/grabs/<id>/<hex>.jpg            on-demand, feed not yet named
/sense/status.json                     unchanged
```

The two schemes never share a directory or a pruning path. `grabs` is not a
bare numeral, so `sensecam._feed_names` already excludes it from the ring
view — the narrowing of the feed-directory vocabulary to `[0-9]+` (2026-08-17)
is what makes this tree free of cost.

`SENSE_RING_SLOTS` continues to bound the ring and has no meaning for grabs.
Grabs are bounded by `SENSE_MAX_AGE_HOURS` and by a new per-feed count cap,
`SENSE_GRAB_KEEP_PER_FEED`, oldest discarded first.

### Naming

`variables` holds exactly two fields: `names`, below, and `grab_budget`, the
agent's own hourly allowance, which the ceiling in *Containment* clamps.

`variables.names` maps feed id to a display string:

```json
{"names": {"3": "Times Square, NYC"}}
```

Sense validates on read against a closed field vocabulary, the treatment the
recorder gives `/llm/console`:

- keys must be members of the configured feed set;
- values are strings, capped at 48 characters;
- a per-feed and a total count cap;
- a rename-rate bound, derived by diffing against the previously published
  `state.json` rather than by trusting a counter in the agent-written file.

Two representations are derived from one value:

- **slug**, for the filename: every non-alphanumeric character replaced by an
  underscore, runs collapsed, leading and trailing underscores stripped. This
  follows `diode.write_commons_file`, which already proves the discipline,
  and diverges from it only in collapsing runs — `"Times Square, NYC"` becomes
  `Times_Square_NYC` rather than `Times_Square__NYC`. A value whose slug is
  empty is treated as unnamed.
- **display**, for the stage: the value as written, escaped at render.

A rename takes effect at sense's next console read, not at the moment the
agent writes the file — the rate bound is a diff against the last published
state, so it can only be evaluated there. `state.json` publishes the accepted
names, which makes a rejected value visible as an absent or unchanged entry
rather than as a silent failure.

A rename never rewrites existing files. Grouping is by id, so history keeps
the labels it was captured under and the agent can see its own naming evolve.

### The ambient ring is unchanged

Cadence, slot math, freeze detection, status publication, and the nine
curated feeds are untouched. The catalogue the `grab` verb offers is
explicitly decoupled from the ambient set and may grow past it: an ambient
feed costs a capture every interval, an on-demand-only feed costs nothing
until asked for.

## Containment

### Guarantee closing the new channel

> The sense command vocabulary takes an index into the operator's
> `SENSE_FEEDS`, never a URL — no SSRF surface and no agent-chosen upstream.
> Sense holds no credential of any kind and mounts nothing else of the
> agent's world. Grab rate is clamped by the operator-side
> `SENSE_GRAB_HOURLY_MAX`, on the `min(console value, operator max)` pattern
> `DIODE_HOURLY_MAX` already uses.

This replaces §7's sentence in the 2026-08-15 spec — "there is no
agent-to-sense path of any sort" — which is retired. The property it named is
no longer distinguishing: the agent already has agent-triggered, agent-paced
egress with spend through the stream sockets and through the diode. What
invariant 3 requires is not the absence of a channel but a guarantee that
closes it, and the paragraph above is that guarantee.

The one property genuinely lost is timing: sense's egress is currently on a
fixed, agent-independent schedule, and after this the agent can modulate the
timing of outbound connections from inside `network_mode: none`. The diode
already has this property and it is accepted; it is noted here as a real
change, not waved through.

### Ceilings are operator-side

`SENSE_GRAB_HOURLY_MAX` lives in the sense service's environment, not in the
console file. The pump can write a grab command into `/sense-console` with no
agent turn behind it, exactly as CLAUDE.md notes for `speak`, so a
console-side budget would not be a boundary. A console `grab_budget` may
lower the allowance and can never raise it.

### Prerequisite: the reconcile crash

`reconcile_storage` and `prune_stale` gate slot files on
`candidate.stem.isdigit()` and then call `int()`. `str.isdigit()` admits
non-ASCII digits that `int()` rejects, so a file such as `².jpg` raises
`ValueError` — which `main()`'s `except OSError` does not catch, producing a
crash loop under `restart: unless-stopped`.

Today this is host-reachable only. Under this design agent-authored names
reach filenames, so it must be **fixed first, as a separate change**, before
any of this ships.

Both functions must additionally learn the grabs tree, or hex-named files are
invisible to both cleanup paths and the volume grows without bound.

### Storage boundary

`./volumes/sense` is a host bind mount with no size boundary. Grabs accrete
where ring slots overwrite, so `/sense` moves to a preallocated ext4 loop
image, the treatment `/build` already receives, and a full volume surfaces as
ENOSPC rather than consuming the host disk.

### Recommended hardening, not load-bearing here

`grab_frame` passes a resolved manifest to ffmpeg with no protocol
restriction. With operator-curated feeds this is the risk profile the service
already carries, so it is not a prerequisite for this change. It is cheap and
worth doing now — restrict to `https,tls,tcp,crypto`, exclude `file` and
`concat` — and it becomes required if the deferred `still` verb is ever built.

## Surfaces the agent reads

### `/sense-console/HELP.md` and `state.json`

Bland and factual, the register of `/diode/HELP.md` and `/llm/sock/README.md`.
The vocabulary, the feed ids, the current names, and the remaining allowance.
No examples that suggest a use, no instruction to name anything.

### `runtime.md`

One sentence, in the established register, alongside the existing
"image files appear at /sense at intervals" — which stays, since ambient
capture continues:

> image capture can be requested through /sense-console, which accepts a
> closed command vocabulary.

This is the pattern `/diode` and `/pump` already use. It does **not** go in
the garden `README.md`, which is the permission document ("nothing here is an
assignment") and names no vocabulary.

The naming config is not mentioned in `runtime.md` beyond the vocabulary
sentence. `HELP.md` documents it factually. Seeding the config empty and
saying nothing further is what keeps this from reading as a labelling
exercise the operator wants performed — invariant 6.

## The stage

`newest_frame` gains the grabs tree, so the eye updates whenever a new image
appears from either source — an ambient tick or a grab. A grab is newest by
mtime the moment it lands, so the eye follows the agent's attention when the
agent looks, and falls back to the ambient ring when it does not.

A new route `/grab/<id>/<name>` mirrors `/frame/<feed>/<name>`, with the same
discipline: both segments matched against directory listings before any join,
the joined path resolved through `data.contained_file`, the size capped, and
the bytes served through a descriptor opened by `data.open_contained`. A
separate route rather than a widened one keeps each route's parsing rigid.

The eye's caption renders the agent's label as an **attributed claim**, not as
fact. The page today "claims only what is true"; a name the agent authored is
unverified, and if the agent labels Bondi as Times Square the stage must not
assert it flatly. The label is escaped like all agent-authored text on this
page and capped for layout.

## Files changed

| File | Change |
|---|---|
| `sense.py` | console read/clear, `grab`/`list`/`help`, name validation, slug, grabs tree, pruning, allowance |
| `stage/sensecam.py` | grabs tree in `newest_frame`, a `grab_bytes_path` peer to `frame_bytes_path` |
| `stage/server.py` | `/grab/` route, label in `_public_sense` |
| `stage/pages.py` | eye caption attribution |
| `docker-compose.yml` | `sense_console` volume, `/sense-console` in agent and sense, `SENSE_GRAB_HOURLY_MAX` |
| `Dockerfile` | `/sense-console` mountpoint precreate |
| `Dockerfile.sense` | `/sense-console` mountpoint precreate |
| `scripts/build_garden.py` | the `runtime.md` sentence |
| `.env.example` | `SENSE_GRAB_HOURLY_MAX`, `SENSE_GRAB_KEEP_PER_FEED` |
| `scripts/verify_container.sh` | `/sense-console` is writable by the agent and holds no credential |

## Testing

Unit tests for slot math, naming validation, slug derivation, allowance
clamping, and the console read/clear cycle run with no network and no
binaries, as the existing sense tests do.

One test binds the slug vocabulary across the two images the way
`test_the_stage_renders_exactly_the_feed_dirs_sense_accepts` binds the
feed-directory vocabulary. Sense and the stage share no import — they are
separate images — so the pattern is duplicated, and a test is the only thing
that keeps the copies from drifting. The 2026-08-17 mismatch is the precedent:
a name one side wrote and the other would not render.

Containment tests: a grab command naming an id outside the configured set is
refused; a name containing separators or traversal sequences cannot reach a
path; the allowance clamps to the operator ceiling however large a console
value is written; the `/grab/` route refuses traversal, symlinks out, unlisted
names, and oversized files.

## Rollout

1. Fix the `reconcile_storage` crash and land it separately.
2. Build and deploy sense and the stage.
3. Rebuild the agent image for the `/sense-console` mountpoint and apply at an
   incarnation boundary — do not restart the live agent container.
4. `scripts/prepare_host.sh` regenerates the garden with the new sentence.

## Out of scope

**Arbitrary-video stills (`still <video_id>`) are deferred**, with the
analysis recorded so it is not re-derived:

The agent seeing arbitrary internet images is already settled — `commons`
fetches arbitrary Wikimedia media and `vision_1` can read it. The new
exposure is not the agent looking; it is that an eye which updates on every
new image would **auto-publish third-party video stills to a tunneled public
page with no human in the loop**. If `still` is built, it must land in a tree
the stage never renders, carry its own hourly ceiling separate from `grab`
(it is the verb an agent that brute-forces command names will hammer), and
require the ffmpeg protocol allow-list above. A random still from a non-live
video also needs a duration probe and a seek, which is a different and much
slower ffmpeg invocation than the live-edge grab, and whether the offset is
caller-specified or random is an open decision.

Audio capture remains out of scope, as in the 2026-08-15 spec.
