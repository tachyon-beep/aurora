# Ambient sense feed

Date: 2026-08-15. Status: approved (operator), design from filigree
aurora-3158d63ba1 and its comment thread. Build alongside the garden
expansion; deploy only at an incarnation boundary (the agent-image change is
one mountpoint line — do not restart the live agent container to apply this).

## Motivation

Every sensory channel the agent has today is pull: it reads files, calls the
model, issues diode commands. The enrichment review found no channel through
which the world arrives unbidden — nothing changes in the agent's environment
unless the agent (or the operator) acts. This feed is the first non-pull
sense: a host-side service deposits camera frames into a read-only volume on
a fixed clock, and the agent finds a world that moves whether or not it looks.
The original issue proposed a PCM-audio ring; the built variant is visual
(the containment shape is identical, and the audio lane can be added later
under the same volume without a new design).

## 1. Sources (all tested live 2026-08-15, one frame grabbed from each)

Five public YouTube live streams were evaluated. Verdicts:

- **Bondi Icebergs pool cam** (`53ILEi6FMaA`) — ocean, surf, weather,
  swimmers; burned-in camera clock; essentially prose-free. INCLUDE, feed
  dir `0`.
- **Sunshine Railcam, Melbourne** (`eFcNsQASSO8`) — street/rail scene,
  event-rich; timestamp overlay only. INCLUDE, feed dir `1`.
- **Melbourne City Skyline cam** (`Kl_L2zZ4xj8`) — skyline and weather,
  rotating camera views; carries a branded lower-third banner (~64 px: LIVE
  chip, city name, temperature/pressure, time). INCLUDE with the banner
  cropped at capture (`-vf crop=in_w:in_h-64:0:0`, verified). Ruling
  (operator): crop it — the caption names the city and collapses the
  discovery gradient the other windows preserve. Feed dir `2`.
- **Monterey Bay Aquarium sea otter cam** (`abbR-Ttd-cA`) — INCLUDE, feed
  dir `3`. First opposite-hemisphere window: it gives the feed set
  phase-shifted day/night and seasons, a temporal texture no single-region
  set has. A wordmark sits inside the scene (top-right), so a strip-crop
  would cut content; a `delogo` filter over the logo box
  (`delogo=x=910:y=40:w=330:h=80`) blurs it at capture instead. This is also
  the **reference feed** (see freeze detection) — an institutional cam with a
  multi-year 24/7 track record.
- **NSW public-safety scanner stream** (`f_cSmY8bcpM`) — EXCLUDE. The video
  is a dashboard of branding, a QR code, subscribe/donation chrome, and
  call-queue text: dense reader-addressed prose, hostile to invariant 2. Its
  real content is audio, and transcription would reintroduce human voice.
  (The embedded traffic cam it carries is available more cleanly as direct
  public JPEG endpoints from NSW Live Traffic, a possible future source.)

Sources were chosen for multi-year longevity; real permanent feed death is
expected to be rare, which is why the inactive-marking below is tuned slow.

Amended 2026-08-17 (operator): the set was widened for geographic range and
the two Melbourne feeds dropped as redundant with each other and Bondi. Kept:
Monterey Bay Aquarium (`abbR-Ttd-cA`, dir `3`, still the reference feed) and
Bondi Icebergs (`53ILEi6FMaA`, dir `0`, the southern-hemisphere window).
Added, one frame grabbed and inspected from each on 2026-08-17, none needing
a crop or delogo filter (in-scene signage and burned-in clocks are world
content, as with Bondi): Washington Monument, D.C. (`oDCAAfOSqvA`, dir `4`),
Times Square (`JQ_jwk_7OVE`, `5`), New Orleans French Quarter
(`QhFYcPBmkcI`, `6`), Tamariu, Spain (`PMhVgTcDd1o`, `7`), Western Wall,
Jerusalem (`77akujLn4k8`, `8`), Shinjuku, Tokyo (`6dp-bvQ7RWo`, `9`), Koh
Samui beach, Thailand (`kkVrj2cr9Ko`, `10`). Dirs `1` and `2` are removed by
reconciliation on the first start; the retained feeds keep their dirs so no
frame of one camera ever sits in another's directory.

## 2. Capture mechanism (prototyped, headless)

Per feed, per cycle: `yt-dlp -g -f 'best[height<=720]' <url>` resolves the
live HLS manifest (manifests expire, so re-resolve every grab), then ffmpeg
grabs a single frame:
`ffmpeg -hide_banner -loglevel error -i <manifest> -frames:v 1 -q:v 3
[-vf <filter>] <tmpfile>`, followed by an atomic rename into the ring slot.
About 3 s per frame, no browser. Playwright was considered and rejected as
the primary mechanism (a full Chromium per snapshot, consent dialogs, ads);
it remains a fallback only for a future feed that is a webpage rather than a
stream. The 10-minute default cadence is modest and polite to the sources.

## 3. Service shape

- New host-side service `sense`: `sense.py` at the repo root (peer of
  `diode.py`), image `Dockerfile.sense` (python:3.13-slim, apt ffmpeg, pip
  yt-dlp + pillow), compose service `sense`, non-root user, default bridge
  network. It holds zero credentials — public streams need none — and no env
  secrets of any kind.
- It mounts exactly one volume: `./volumes/sense` at `/sense`, read-write.
  Nothing else of the agent's world (no diode, state, llm, transcripts, or
  telemetry volumes) is visible to it.
- The agent service gains one mount: `./volumes/sense:/sense:ro`. The agent
  image's mountpoint `mkdir -p` line gains `/sense`.
- Feeds are configured by `SENSE_FEEDS` (JSON array of
  `{id, [dir], [vf]}`; `dir` defaults to the entry's position). Amended
  2026-08-17: the list lives in `.env` (the four included feeds are the
  `.env.example` value), and compose passes it through as
  `${SENSE_FEEDS:-[]}` — unset captures nothing. It is read once at start,
  so a change is applied by recreating the sense service alone
  (`docker compose up -d sense`). Cadence is `SENSE_INTERVAL_MINUTES`
  (default 10). Amended 2026-08-17: within each interval the feeds are
  grabbed one at a time at evenly spaced offsets from the interval boundary
  (list order; feed k of N at k/N of the interval) rather than together at
  the boundary, so the volume changes throughout the interval. Every offset
  falls inside the interval, so each frame still lands in the slot the
  cycle began in; detector bookkeeping (probe consumption, the global
  guard) stays per cycle.
- `sense.py` is standard library + Pillow; the yt-dlp/ffmpeg subprocess
  calls are isolated in small functions so unit tests can exercise slot
  math, the freeze detector, status transitions, and the global guard with
  no network or binaries. Host-side code: normal factual comments apply
  (the comments-only-code rule is `agent.py` only).

## 4. Ring and slots (no schema, no index, no filenames)

Per-feed directory `/sense/<dir>/` holding a fixed ring of slots
`000.jpg`..`287.jpg` (`SENSE_RING_SLOTS=288` — 48 h at 10 min; amended
2026-08-17 to a default of 144, one day, alongside the 24 h max age below),
overwritten in place. Slot selection is deterministic and restart-safe:
`(minutes_since_unix_epoch_utc // interval) % slots`, so a restarted service
resumes writing where the clock says, not where it left off. No other files
appear in feed directories and no timestamp enters a filename: the honoured
ruling from the issue is that filenames carry no schema — file mtimes carry
arrival time without inventing one, and the burned-in camera clocks carry
world time in-band. (ISO-timestamp filenames were considered and rejected as
schema.) Feed directories get bland numeric names, no captions — the agent
works out what its windows show.

## 5. Freeze and failure detection

Frozen-stream detection cannot use byte checksums: JPEG noise and burned-in
clocks make bytes always differ on a live feed, and a frozen HLS edge repeats
exact segments anyway. Instead, each new frame is downscaled to 32×32
grayscale (Pillow) and compared to the previous grab's thumbnail by mean
absolute pixel difference; a diff below `SENSE_STATIC_THRESHOLD` (default
2.0) counts as static, and 12 consecutive static grabs (~2 h at the default
cadence) mark the feed inactive. The long window and low threshold guard
against still night scenes — the otter cam's night-IR sleeping-otter frames
are genuinely near-static and set the bar here — while the skyline stream's
view rotation makes false freezes on that feed near-impossible.

Grab failure (yt-dlp or ffmpeg nonzero exit or timeout) skips the slot and
leaves the ring as-is; 6 consecutive failures mark the feed inactive.

**Global guard:** if every feed fails in the same cycle, nothing is counted
that cycle. The reasoning, from the operator's ruling: these institutional
cams rarely die, but yt-dlp rot (a YouTube-side change breaking extraction)
is the single most likely failure mode and it hits all feeds at once — that
is capture-side evidence, not feed-death evidence, and must not mark feeds
inactive. The Monterey feed's reliability is what makes the distinction
readable: the reference feed failing alone points at the feed; everything
failing together points at the capture side.

Inactive feeds keep their last ring contents (stale mtimes remain honest
data) and are probed once daily; a successful grab that differs from the
last frame reactivates the feed.

Amended 2026-08-17: `SENSE_MAX_AGE_HOURS` (default 24) removes, each cycle,
any ring frame of a configured feed older than that many hours, so nothing
older than a day remains and a feed that stops goes dark rather than holding
its last frames until the ring wraps. 0 keeps the behaviour above.

Detector state (counters, previous thumbnails) is kept in memory only. The
only writable location is `/sense`, which the agent reads, so even a dotfile
state cache would become an agent-visible surface; on restart the counters
reset to zero, which merely delays marking a feed inactive — a conservative
failure the design accepts.

## 6. Status surface

The operator's original proposal — a "down for now" note through the diode —
was rejected: it would couple the sense container into the diode volume and
put an operator-voiced message onto a command-vocabulary surface. The
adopted form follows the recorder's `/llm/sock` precedent (`README.md` and
`streams.json` as agent-readable operator-side writes kept within invariant
2): `/sense/status.json` at the volume root, written atomically (tmp +
`os.replace`), containing exactly a map of feed directory to state —
`{"0": "active", "1": "inactive", ...}`. No reasons, no prose, no timestamps
beyond the file's own mtime. This file and the frames are the only things
sense ever writes.

## 7. Invariant-3 statement

The channel and the guarantee that closes it: the sense container holds no
credential of any kind, so nothing reachable through it can leak one; the
volume is one-way host-to-agent and mounted read-only into the agent; there
is no agent-to-sense path of any sort — the agent cannot trigger a fetch,
influence cadence, or cause spend. It is the diode's mirror image: scheduled
ingress where the diode is gated egress. The sense service mounts no other
volume, so a compromise of it (it does talk to the public internet) reaches
nothing but its own frame directory.

## 8. Invariant-2/6 conformance

Feed directories are bare numerals; no captions, feed names, or geography
enter the agent's world — the skyline banner crop and the wordmark delogo
exist precisely to keep source-identifying prose out of frame. `runtime.md`
gains exactly one sentence, in a paragraph after the `/corpus` line, in the
established factual register: "image files appear at /sense at intervals."
No suggested use, no naming of the sources. `requirements-agent.txt` is
untouched — yt-dlp and sense's Pillow belong to the sense image only (the
agent manifest's existing pillow line is unrelated and unchanged).

## 9. Future composition and caveat

- The frames are computable today with the agent's existing pillow/numpy
  (day/night brightness, motion diffs, weather) even though the core model is
  text-only. A vision-capable model added to a stream allow-list
  (the proposed `STREAM_MODEL_ALLOW` enrichment) would let the agent
  construct an eye for its windows; that is a separate spec and no part of
  this one.
- Public cams capture people. This is acceptable while frames stay
  in-container and unpublished; any future path that sends frames to a
  hosted vision model must weigh it again before opening.

## 10. Verification

- unit tests: slot math (determinism across restart boundaries), freeze
  detector thresholds, inactive/reactivate transitions, the all-feeds-fail
  global guard, status.json content shape and atomic write
- garden test: the single runtime.md sentence, register unchanged
- compose review: sense mounts only `./volumes/sense`; the agent's mount is
  `:ro`; no new env secret anywhere
- live check at deploy time: one manual cycle against the four feeds,
  confirming crop and delogo geometry against current stream layouts
