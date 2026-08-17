# The video surface — search, transcript, and stills

Date: 2026-08-17. Status: design approved (operator), spec pending review.

Un-defers the `still` verb held back by
`docs/superpowers/specs/2026-08-17-on-demand-sense-design.md` ("Out of scope"), and
answers the open decision recorded there — the offset is caller-specified, not random.
That spec's conditions on any future `still` are carried here and met: the tree the
stage never renders, a ceiling separate from `grab`, and the ffmpeg protocol
allow-list. The sense volume and its command vocabulary are untouched by this design.

Deploy at an incarnation boundary: the agent-image change is one mountpoint line — do
not restart the live agent container to apply it.

## Problem

The agent can read arbitrary text from the world (`fetchhttp`, `arxiv`, `gutenberg`,
the news verbs) and can see images (`commons` media, sense frames, the `vision_1`
stream). It has no access to *recorded* material with a duration — the modality where
text and image are two views of one artifact, and where "where in it" is a question
that can be asked.

The sense surface is not that. `/sense` presents as still images of a place, now: an
ambient ring plus, under the on-demand design, a grab from a curated feed. Video stills
underlie it, but the modality it presents is the photograph. Nothing in the agent's
world presents as a recorded thing one can move around inside.

There is also no way to *find* material. Every existing retrieval verb needs an
identifier the agent already has — a URL, a title, a Gutenberg id. `gutensearch` is
the sole exception, and it is what makes the library reachable rather than theoretical.

## What this adds

A new service and volume, `/video`, carrying a closed command vocabulary through which
the agent searches recorded video, fetches timed transcripts, and samples single frames
at chosen offsets. It follows the diode's console/output protocol rather than inventing
a mechanism.

## Decisions taken

- **A separate service and volume, not an extension of the diode or of sense.** See
  *Approaches considered*.
- **The unit of input is an 11-character video id, never a URL.** This is the
  containment guarantee, and search is what makes it workable: the agent obtains ids
  from search results and never needs to hand over a URL. An id-only surface with no
  search would be unreachable rather than contained.
- **The volume is mounted into the agent and this service alone.** The stage does not
  mount it. This makes "third-party stills never reach the public page" a mount fact
  rather than a promise in rendering code — the exposure the `still` deferral named.
- **Budgets: one video per hour, twenty stills per hour, text on its own allowance.**
  Fixation is what is rationed; looking hard at a chosen subject is not. A still is an
  HTTP range seek against the media host, single-digit MB, so twenty is tens of MB an
  hour — cheap in bandwidth, and bounded in wall clock by being twenty separate ffmpeg
  invocations.
- **The hour rations switching videos, not the binding's lifetime.** See *Budget
  mechanics*.
- **Charging is at dispatch, once a command validates.** A malformed id, an unknown
  verb, or a closed gate causes no egress and costs nothing.
- **Third-party text is bounded, never laundered.** See *Ingest fidelity*.
- **Nothing on the agent's surfaces names YouTube.** See *Register*.
- **Audio remains out of scope**, as in the 2026-08-15 ambient sense design.

## Approaches considered

**Extending the diode.** Rejected. Transcript and search both need yt-dlp, and yt-dlp
needs a JavaScript runtime for extraction — so the diode image would gain deno and
roughly triple, and a JS runtime would land in the one agent-facing container that
holds a credential (`ELEVENLABS_API_KEY`). Splitting text into the diode and stills
into sense was considered as a variant and fails on the budget: the coupled video/still
allowance must be enforced in one process, and those two containers share no state.

**Extending sense.** Rejected on two stated sentences. Invariant 3 says frames, a
closed-vocabulary `status.json`, and their temporaries are the *only* contents of the
sense volume — transcripts and search results are not that. And the sense guarantee is
"an index into the operator's feed list, **never a URL**"; admitting an agent-supplied
video id weakens that sentence for the whole service, including the live-camera path.
It would also inherit that spec's unlanded prerequisites and its pending review.

**A separate service and volume.** Chosen. It is not a new protocol — it is the
diode's protocol pointed at a different world, so the agent learns no new mechanism.
The modality is distinct from sense's, the guarantee is its own and closes cleanly, and
no existing surface's guarantee sentence is weakened. The additional surface is a
benefit rather than a cost under the operator's standing ruling that the world is an
intellectual jungle gym until the agent finds its footing.

## Architecture

### The service

`Dockerfile.video`, built from the toolchain `Dockerfile.sense` already proves:
`python:3.13-slim`, `deno` copied from the official image (yt-dlp's default JS runtime
for extraction), `ffmpeg` from apt, `yt-dlp` and `pillow` from pip. `read_only: true`,
`cap_drop: [ALL]`, `no-new-privileges`, tmpfs `/tmp`, `XDG_CACHE_HOME=/tmp` so yt-dlp's
cache lands off the read-only rootfs. `pids_limit: 128`, `mem_limit: 512m`.

It holds **no credential** — none of these operations need one — and mounts nothing
else of the agent's world. Network `video_egress` is its alone: sense keeps
`sense_egress` to itself and the viewer remains the sole occupant of the compose
default network.

### The volume

```
/video/console.json   {commands: [...], variables: {...}}   agent-writable
/video/state.json     published by the service
/video/HELP.md        written on the help command
/video/output/        search results, transcripts
/video/stills/        jpeg frames
```

One volume carrying console and results together — the `/diode` shape, not `/sense`'s
split — because the stage reads none of it and there is no read-only consumer to serve.

Mounted read-write in the agent and the video service, and **nowhere else**. The
service creates `console.json` when absent, as `diode.py` does, reads the command list,
executes in order, and atomically clears **only** `commands`, preserving `variables`.

Mountpoint ownership follows the same construction as `/diode`: both images pre-create
`/video` owned by the uid both services run as, so Docker seeds the fresh named volume
non-root and the cap-dropped services can write it.

### Command flow

```
agent writes console.json
  -> service polls, validates, charges, dispatches
    -> yt-dlp resolves (no media transfer)      [search, transcript, watch]
      -> manifest URL validated (SSRF discipline)          [watch]
        -> ffmpeg range-seeks one frame                    [still]
          -> Pillow re-encodes at bounded resolution
  -> result file in output/ or stills/, always
  -> state.json republished
```

## Containment

### Guarantee closing the new channel

> The video service accepts an **11-character video id** and a length- and
> charset-bounded search query — **never a URL**. Every upstream URL is composed by the
> service itself, so no agent-authored string reaches a host, scheme, or path. It holds
> no credential and mounts nothing else of the agent's world; **the stage does not
> mount its volume**, so nothing this service writes is rendered automatically on any
> outward-facing page. Rates are clamped by the operator-side `VIDEO_HOURLY_MAX`,
> `VIDEO_STILL_HOURLY_MAX` and `VIDEO_TEXT_HOURLY_MAX`, on the `min(console value,
> operator max)` pattern `DIODE_HOURLY_MAX` uses, and the counters those ceilings clamp
> live on a volume the agent does not mount.

Two things that sentence deliberately does **not** claim. It does not say a still can
never reach the public page: the agent writes `/diode`, the stage reads it, and an
agent that chose to relay something outward could. What the mount fact closes is
*automatic* publication with no agent decision and no human in the loop — precisely
what the `still` deferral named, where an eye following new frames would publish
third-party video stills by itself. Agent-initiated relay is the already-accepted
`commons` profile, unchanged by this design.

### The vocabulary is an allow-list

Every verb this service honours is an explicitly named member of the table below.
Anything else — an unknown word, a misspelling, a verb the agent composed — does
nothing and causes no network activity. There is no default-permit path and no
blocklist. This is the property that makes the container's interior survivable as
hostile space: code inside it can compose any string, but it cannot invent an egress
verb, and an unrecognised one is inert rather than merely discouraged.

### Why the diode's SSRF defenses cannot simply be reused, and what replaces them

yt-dlp performs its own HTTP, so no request it makes passes through the diode's
resolver checks; and media resolves to `googlevideo.com`, not to the site the id names.
A host allow-list applied at the agent's input would therefore be decorative. Three
mechanics carry the weight instead:

1. **Resolve and fetch are separate steps with a validated URL between them.**
   `yt-dlp -J`/`-g` returns a manifest URL without transferring media. That URL is the
   single place a URL exists in this design, and before ffmpeg sees it the service
   applies the diode's own discipline: https only, host suffix within
   `{googlevideo.com, youtube.com}`, resolved addresses rejected if private, loopback,
   link-local or otherwise reserved. The residual is named rather than papered over:
   ffmpeg resolves the hostname again when it connects, so the address check is
   best-effort against a rebind between validation and fetch. The host allow-list, not
   the address check, is what bounds where the fetch can go. Pinning the validated
   address is not available here — media hostnames vary per request — and this is the
   same residual the diode already carries on every fetch.
2. **ffmpeg runs `-protocol_whitelist https,tls,tcp,crypto`** — no `file`, no `concat`
   — the hardening the on-demand sense design named as required if `still` were ever
   built. Every frame is then re-encoded through Pillow at a bounded resolution, so
   what lands on the volume is a jpeg this service produced rather than bytes a remote
   server chose.
3. **Subprocesses are argument lists, never shell strings**, each with a timeout (60s
   resolve, 120s still) and killed past it. stderr is captured and never written to
   `output/` verbatim — it contains the signed manifest URL.

### Allowance counters are in memory, never on the volume

`/video` is agent-writable by necessity. A counter file there would be an allowance the
agent could restore by deleting a file. The diode already threads `fetch_history`
in-process for this reason and this service does the same. `state.json` publishes what
remains; nothing the agent can write restores it. Counters reset on service restart,
which is operator action, matching the diode's existing profile.

One consequence of in-memory counters is worth naming: the service runs
`restart: unless-stopped`, so a crash resets every allowance. That is not a path the
agent controls — it cannot signal or reach the process — but it does mean a repeated
crash would silently restore budget. The failure discipline below (one command fails,
never the poll loop) is therefore load-bearing for the budget and not only for
availability.

### Ceilings are operator-side

`VIDEO_HOURLY_MAX` (1), `VIDEO_STILL_HOURLY_MAX` (20) and `VIDEO_TEXT_HOURLY_MAX` (20)
live in the service's environment. The pump can write a video command with no agent
turn behind it, exactly as CLAUDE.md notes for `speak`, so a console-side budget alone
would not be a boundary. Console `video_budget`, `still_budget` and `text_budget` may
lower an allowance and can never raise it.

### Storage boundary

`2026-08-17-volume-quotas-and-uid-design.md` is approved and unbuilt; it moves every
agent volume to a preallocated loop image and container uid 65532. `/video` joins that
spec's phase 1b list rather than being provisioned the old way. If that spec lands
first, `/video` is provisioned that way from the start; if not, this is one more volume
carrying the same known debt, and `Dockerfile.video` pre-creates its mountpoint at
whichever uid is current when it ships.

Independently of that, `stills/` is bounded by `VIDEO_STILL_KEEP` (200, oldest
discarded first — roughly ten hours of maximal use, ~40 MB at the bounded resolution)
and `output/` by a file-count cap on the same discipline.

## Command vocabulary

| Command | Gate | Charges | Effect |
|---|---|---|---|
| `help` | always | — | write the current command list to `HELP.md` |
| `search <query>` | always | text | return video ids, durations, channels and titles for a query |
| `transcript <id> [start] [end]` | `enable_transcript` | text | return the timed transcript, optionally between two offsets in seconds |
| `watch <id>` | `enable_frames` | video | bind a video and return its duration |
| `still <seconds>` | `enable_frames` | still | return one frame from the bound video at an offset |

`search` is ungated because it is the only route to an id, and id-only input is the
containment guarantee. An agent that finds this volume has one verb that works
immediately and a factual `HELP.md` naming what the others require.

Splitting `watch` from `still` is what makes the coupled budget legible: the rationed
verb is choosing a video, the frequent verb is looking into it, and the resolved
manifest is cached behind the binding.

Deliberately cut: channel and uploads listing, related-video traversal, playlists,
comments, clip extraction, audio. Find, read, look is a complete loop; traversal verbs
are what turn a rationed surface into a crawler, and any of them can be added later
against a vocabulary that already exists.

## Budget mechanics

Three independent hourly allowances, each `min(console value, operator max)`, each a
rolling window as the diode's is.

- **video** (1/hour) — charged by `watch` on a new id.
- **still** (20/hour) — charged by `still`.
- **text** (20/hour) — charged by `search` and `transcript`. Text never touches the
  video allowance: with a ceiling of one video an hour, charging transcript to it would
  make reading a video as precious as watching it, and the agent could not survey
  before committing.

**Charging is at dispatch, once the command validates.** A malformed id, an
out-of-range offset, an unknown verb or a closed gate costs nothing and causes no
egress — so a brute-force guessing run against this surface burns no allowance. A
well-formed id that proves private, deleted or geo-blocked *does* spend, and the
refusal says so factually.

Three rulings inside the coupling:

1. **The binding persists until replaced; the hour rations switching, not looking.** A
   video bound at 23:59 is still stillable at 00:30. The alternative — a binding that
   dies with the hour — would leave a video watched at :59 unreachable a minute later
   with nineteen stills unspent, teaching the agent to fear clock edges rather than to
   choose subjects.
2. **Re-`watch` of the already-bound id is free and causes no egress**; it reports the
   existing binding.
3. **A manifest expiry mid-hour re-resolves the same id transparently and uncharged.**
   Signed media URLs are short-lived and can be address-bound, so this will happen in
   normal use. One `watch` was dispatched; the service's retry is the service's
   business.

## Outputs

**Search** — up to 10 results, each a line carrying id, duration, channel and title,
each field capped. Written to `output/`.

The query is validated before dispatch: a length cap (200 characters) and a charset
that excludes control characters and newlines. yt-dlp receives it as a single
`ytsearch10:` argument in an argument list, never a shell string, so the cap and
charset are hygiene against a malformed extractor argument rather than the boundary —
the boundary is that it is an argument, not a command line.

**Transcript** — timed lines (`[MM:SS] text`), capped at 500 KB with an explicit
truncation marker; the `start`/`end` window is how a long video is read within the cap.
Manual captions are preferred over auto-generated, and the output states which was
obtained and in what language. A video with neither yields "no transcript available",
not an empty file.

**Stills** — `<id>_<seconds>_<hex>.jpg` in `stills/`, bounded resolution, hex token from
`secrets.token_hex(4)` following the on-demand sense ruling that filenames carry no
schema. The agent reads them as it reads sense frames: base64 into the `vision_1`
stream.

### Ingest fidelity

Third-party text is **bounded, never laundered**. Byte caps, field caps, item counts
and an explicit truncation marker; no stripping, rewriting or filtering of content that
looks like an injection attempt, and no mention of sanitizing on any surface.

The reason is specific rather than ideological: the agent audits incoming text as an
external threat surface itself, and a service that silently altered titles or
transcripts would hand it tampered evidence and let it reason about a world other than
the one on the wire. Escaping belongs at the render boundary, where it protects a
viewer; here it would protect nothing and hide the evidence.

Search results are attacker-influenced text — titles and channel names chosen by
strangers — landing in a file the agent reads as data. That is the accepted profile
already carried by `fetchrss`, `arxiv` and the news verbs, not a new one. What is new
is only that a query the agent chose shapes what arrives.

## Surfaces the agent reads

### `HELP.md`

One line per open verb in the `COMMANDS` help-string form, plus a factual statement of
which allowances exist and what they are — a refusal the agent cannot predict reads as
a broken world rather than a bounded one. No examples, no suggested subjects, no sample
query.

### `state.json`

The open vocabulary, the three remaining allowances, the bound video id and its
duration, and the newest output names. Nothing about closed verbs; no count, census or
hint of anything unlisted.

### `output/` and `stills/`

Third-party material, unedited beyond bounding. Invariant 2 governs what the operator
authors, not what the world says.

### `runtime.md`

One sentence, generated by `scripts/build_garden.py`, alongside the existing `/diode`,
`/pump` and `/sense` lines:

> recorded video can be searched, transcribed and sampled through /video, which accepts
> a closed command vocabulary.

This names a reachable interface without suggesting a use — the pattern invariant 6
permits and the model endpoint sentence establishes. Nothing goes in the garden
`README.md`, which is the permission document and names no vocabulary.

### Register

Two rulings, both load-bearing for invariant 2:

- **No verb, help line, state field or example on the image names YouTube**, and no
  example URL appears anywhere. The agent reaches "these are YouTube ids" from the
  evidence in its own search results, as the on-demand sense design has it reach feed
  names from frames.
- **`search` is never seeded.** No default query, no `variables.topics`, no starter
  list, no worked example. Search is the verb that most invites the operator to plant
  interests, and planting them would be curriculum. The surface says recorded video can
  be searched, and stops.

## Failure

Every command produces a result file whatever happens: a missing file is
indistinguishable from a service that died. Unavailable video, absent transcript, failed
resolve, ffmpeg timeout, offset past the duration, spent allowance — each yields a plain
factual sentence. No apology, no suggestion of what to try instead, no advice on when to
retry; `state.json` carries what remains, and a refusal that coaches is the operator
talking.

A crashed or hung subprocess fails one command, not the poll loop.

### Subprocess and pid hygiene

The service runs under `pids_limit: 128` and is the only component in this design that
spawns processes. Twenty stills an hour is twenty ffmpeg invocations, each with a
timeout, and a resolve may run alongside one. A killed-but-unreaped child holds a pid,
so a leak accumulates until the service cannot fork — at which point every command
fails, the in-memory counters are lost on the ensuing restart, and the budget resets.
Pid hygiene is therefore load-bearing for containment here and not only for
availability:

- One subprocess in flight at a time; commands execute in order, as the diode's do.
- Every invocation is `kill`ed on timeout and then **waited on**, so no zombie survives
  the command that created it.
- The process group is terminated, not just the direct child — ffmpeg and yt-dlp both
  spawn helpers (yt-dlp invokes deno for extraction), and killing only the parent
  orphans them.
- A test asserts the child count returns to its baseline after a timeout, a non-zero
  exit, and a successful run.

This is stated rather than left to implementation because resource-exhaustion bugs are
the class the agent has already demonstrated it finds and repairs on its own, inside a
run.

## Files changed

| File | Change |
|---|---|
| `video.py` | new: poll loop, vocabulary, validation, budgets, resolve/fetch, outputs |
| `Dockerfile.video` | new: deno + ffmpeg + yt-dlp + pillow, mountpoint pre-create |
| `docker-compose.yml` | `video` service, `video` volume, `video_egress` network, `/video` in the agent, the three ceilings |
| `Dockerfile` | `/video` mountpoint pre-create on the agent image |
| `scripts/build_garden.py` | the `runtime.md` sentence |
| `.env.example` | `VIDEO_HOURLY_MAX`, `VIDEO_STILL_HOURLY_MAX`, `VIDEO_TEXT_HOURLY_MAX`, `VIDEO_STILL_KEEP` |
| `scripts/verify_container.sh` | `/video` is agent-writable, holds no credential, and the service has no credential in its environment |
| `CLAUDE.md` | invariant 3: the guarantee paragraph above |

No change to `diode.py`, the stage, `sense.py`, or the agent's own source.

## Testing

Unit tests run with no network and no binaries, as the sense tests do: `yt-dlp` and
`ffmpeg` behind an injected runner, HTTP behind an injected opener.

**Containment**
- No agent-authored string reaches a subprocess argument that is not a validated id or
  an integer.
- A manifest URL resolving to a private, loopback, link-local or reserved address is
  refused before ffmpeg is invoked.
- A non-https manifest URL, or one outside the host allow-list, is refused.
- The still tree lives on a volume no other compose service mounts — asserted against
  `docker-compose.yml`, so a later mount edit fails a test. This is what keeps the mount
  fact load-bearing.
- The ffmpeg invocation carries the protocol allow-list.
- A search query containing shell metacharacters, newlines or control characters is
  refused or reaches yt-dlp as one inert argument; no invocation is a shell string.
- The compose service definition carries no credential-shaped environment variable —
  the same check `verify_container.sh` makes of the agent.

**Budget**
- Dispatch charges; malformed input, unknown verbs and closed gates do not.
- A console budget above the operator ceiling clamps down.
- Rewriting or deleting anything on `/video` does not restore an allowance.
- Re-`watch` of the bound id is free and causes no egress.
- A mid-hour re-resolve of the bound id is free.
- A binding survives an hour boundary.

**Vocabulary**
- `still` with nothing bound refuses without egress.
- Unknown verbs refuse without egress — asserted against a generated set of words that
  are not table members, including near-misses of the real verbs, with every network
  and subprocess path stubbed to raise. The allow-list is the property under test, not
  the specific unknown words.
- `HELP.md` and `state.json` name no closed verb.

**Ingest fidelity**
- A transcript containing prompt-injection-shaped text arrives unmodified apart from
  the byte cap, with the truncation marker present. This test makes the no-laundering
  ruling permanent, so a later well-meaning sanitizer fails CI.

**Register**
- No file copied into any image names YouTube in a help string, state field or the
  garden sentence.

## Rollout

1. Build the `video` service and bring it up alone. It is inert until the agent writes
   a command.
2. Rebuild the agent image for the `/video` mountpoint and apply **at an incarnation
   boundary** — do not restart the live agent container.
3. `scripts/prepare_host.sh` regenerates the garden with the new sentence.
4. `/video` joins the volume-quota spec's loop-image list when that lands.

## Out of scope

- **Audio**, as in the 2026-08-15 ambient sense design.
- **Traversal verbs** — channels, playlists, related videos, comments.
- **Clip extraction**; the surface samples frames, it does not transfer media.
- **Any stage rendering of this volume.** Reaching it would require mounting the volume
  into the stage, which is the fact this design's containment rests on.
- **A dedicated per-video-id rate limit.** With one video an hour, id-level abuse is
  already bounded by the coarser ceiling; a second counter would add state without
  closing anything the hourly allowance leaves open.
