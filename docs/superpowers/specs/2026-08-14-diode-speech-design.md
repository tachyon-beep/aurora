# Diode Speech — Design

**Date:** 2026-08-14
**Status:** Approved (design discussion in session)
**Context:** An extension of the diode's closed command vocabulary
(`docs/superpowers/specs/2026-08-13-diode-enrichment-design.md`) and of the stage's stream page
(`docs/superpowers/specs/2026-08-13-stream-demonstration-design.md`). The diode gains one gated
command, `speak`, which renders text to audio through a third-party speech API and leaves the
result in the diode volume. The stage plays that audio on the stream page and captions it.

The agent is the speaker. No operator-side broadcast path is built: the stage mounts `/diode`
read-only and the stream port serves no mutating endpoints, and both properties stay as they are.

## The command

One new entry in `COMMANDS` in `diode.py`, in the existing gated-variable style.

| Command | Gate | Behavior |
| --- | --- | --- |
| `speak <text>` | `enable_speech` **and** a configured API key | Render text to speech via the speech API; write `spoken/<UTC stamp>.mp3` and `spoken/<UTC stamp>.txt`; consume one unit of the shared outbound budget |

Help line, in `publish`'s register:

```
speak <text> -> make text available outside the container as audio
```

The line, the gate-variable line, and the command's result string are bland and factual. They
must not contain "aloud", "voice", "audience", "stream", "listener", or any other word implying
an audience or a performance. The agent's world states that audio leaves the container; it does
not tell the agent that anyone is listening. A help line that implies an audience manufactures
exactly the performance frame the seed prompt warns against, and would be the single most
damaging way to get this feature wrong.

Result on success is the existing factual form: `recorded as <filename>`.

### Gating

`speak` is available only when **both** hold:

1. `enable_speech: true` is set in `console.json` (operator-controlled, off by default), and
2. the diode process has a non-empty API key in its environment.

When the key is absent the command is not listed, not available, and not dispatchable — absence
disables the feature rather than exposing a command that always errors. This mirrors
`STAGE_SUMMARY_API_KEY`, whose absence disables generated prose rather than degrading anything
else. For the same reason, `write_help` lists the `enable_speech` gate-variable line **only**
when a key is configured: the agent's world never advertises a capability that cannot exist.

`speak` is a listed command, not a hidden one like `blind`. `undocumented_command_count()` is
unchanged and stays at 1.

## Budget

`speak` charges the **same** rate-limit pool as every fetching command: `check_rate_limit` against
`fetch_budget` over `FETCH_WINDOW`, sharing the single `fetch_history` list. Speaking and fetching
compete for one hourly allowance; the agent cannot both spam speech and gather information.

There is no separate speech budget. A second counter over a shared pool earns nothing, and the
worst-case hourly spend is already bounded and computable: `fetch_budget × SPEECH_TEXT_CAP`
characters.

`SPEECH_TEXT_CAP = 300` characters, enforced in code. Longer text is truncated, not refused —
consistent with `PUBLISH_TEXT_CAP`'s treatment. The budget is charged **before** the API call and
is not refunded on failure, exactly as the fetching commands behave.

### Two strings become true again

Two agent-readable strings currently describe the pool as fetch-only. Once speech draws on it,
they are false, and a world that lies to the agent about its own limits is a different experiment
from the one this harness is running. Both are corrected:

| Location | Was | Becomes |
| --- | --- | --- |
| `handle_command` rate-limit refusal | `rate limited: at most {limit} fetch(es) per hour` | `rate limited: at most {limit} outbound operation(s) per hour` |
| `write_help` variable list | `fetch_budget: integer, number of http-fetch calls allowed per hour` | `fetch_budget: integer, number of outbound operations allowed per hour` |

The variable name `fetch_budget` itself does not change — renaming it would break every existing
`console.json` for no gain.

## Credential and transport

`ELEVENLABS_API_KEY` lives in the **diode container only**: declared on the `diode` service in
`docker-compose.yml`, documented in `.env.example`, never in the agent image, never mounted into
the agent, never named in any agent-readable surface. Companion settings, also diode-only:

| Variable | Default |
| --- | --- |
| `ELEVENLABS_API_KEY` | unset (feature disabled) |
| `ELEVENLABS_VOICE_ID` | `JBFqnCBsd6RMkjVDRZzb` |
| `ELEVENLABS_MODEL` | `eleven_multilingual_v2` |

Output format is a code constant: `mp3_44100_128`.

### The request path is separate from the fetch path

A new function `_speak_request(text)` in `diode.py`:

- **Takes no URL argument.** The host and path are code constants; the voice id is interpolated
  from the environment (not from agent input) after a strict `[A-Za-z0-9_-]` validation.
- `POST https://api.elevenlabs.io/v1/text-to-speech/<voice_id>?output_format=mp3_44100_128`
  with headers `xi-api-key: <key>` and `Content-Type: application/json`, body
  `{"text": ..., "model_id": ...}`.
- **Still calls `classify_url`** on the constructed URL. The diode-enrichment spec states
  "fixed-host URLs are still classified — no exemptions", and that convention is preserved here.
  Running an SSRF check on a constant costs nothing and leaves no exemption for a future reader
  to mistake for an oversight.
- **Refuses redirects outright** rather than re-validating them. `urllib` resends headers across
  a redirect, so a redirect on a credentialed request is a way to hand the key to another host.
  The stage's summariser already refuses redirects for exactly this reason
  (`test_summariser_refuses_redirects`); this follows that precedent, not `_fetch`'s.
- **Reads bytes, not text**, capped at `MAX_AUDIO_BYTES = 2_000_000` — `_fetch` decodes utf-8 and
  is driven by agent-supplied URLs, and the credential must never touch that path.
- Returns `(ok, bytes_or_reason)`; every exception is contained and returned as a factual reason.

Timeout is `FETCH_TIMEOUT`.

No new package. The diode is `urllib`-only and stays that way; the vendor's JavaScript SDK is not
usable here and adding a Node runtime to a 512 MB egress container to make one HTTP POST is not
justifiable. `requirements-agent.txt` and the agent image are untouched — nothing about this
feature enters the agent's world beyond one help line.

## Artifacts

The diode writes two files per utterance into `SPOKEN_DIR = /diode/spoken`:

- `<UTC stamp>.mp3` — the audio bytes.
- `<UTC stamp>.txt` — the exact text sent, so the caption never has to be reconstructed.

Names are **timestamp-derived only**, never built from agent text — the same rule `write_output`
follows, and stricter, since no command fragment is carried at all. The stamp format matches
`write_published`: `%Y%m%d_%H%M%S_%f`.

**Retention:** `SPOKEN_KEEP = 20`. After each successful write the diode prunes all but the
newest 20 stamp-pairs. Audio is orders of magnitude larger than the text artifacts already in the
volume, and the volume is shared with the agent's own working channel; unbounded growth here
would eventually be a denial of service against the agent, not just against the host.

`write_state` reports a `spoken_count` alongside `output_count`.

## Stage

### Snapshot

`data.diode_spoken(diode_dir, limit)` mirrors `data.diode_published`: newest-first, each entry
`{name, text, epoch}`, every read through `contained_file` so a planted symlink is rejected. Text
is capped like every other agent-controlled public field. `server._assemble_snapshot` adds
`diode.spoken` and `diode.spoken_total`.

### Audio route

`GET /audio/<name>` on the **stream** port (8091). A GET, so "the stream port serves no mutating
endpoints" is intact.

- The requested name is resolved by **exact match against `os.listdir(SPOKEN_DIR)`** — nothing
  from the query string is ever path-joined. Traversal is impossible by construction rather than
  by filtering.
- `contained_file` on the resolved path, on top of that.
- Fixed `Content-Type: audio/mpeg`, `Content-Length`, the existing `SECURITY_HEADERS` (which
  include `nosniff`), and a size cap of `AUDIO_MAX_BYTES = 4_000_000`; anything larger is a 404,
  since the diode never produces one and only a planted file could be.
- 404 for anything else, in the existing shape.

### Content-Security-Policy

The stream page currently ships `default-src 'none'` with no `media-src`, so `media-src` falls
back to `'none'` and an `<audio>` element would be blocked outright. `SECURITY_HEADERS` gains
`media-src 'self'`. This is the one change without which every unit test passes and the page is
still silent.

### Page behavior

- A caption element shows the spoken text while the audio plays, escaped like every other
  agent-controlled field.
- Playback is gated on **freshness and novelty**: only utterances newer than `RECENT_SECONDS`
  play, and a client-side played-by-name set ensures a reload, a poll, or a stage restart never
  replays an utterance. One at a time, in order.
- **The visual never depends on the audio.** The caption renders whether or not playback
  succeeds, so a muted or autoplay-blocked page still shows that speech happened.
- No enable-audio affordance. The stream page is an OBS browser source, not an interactive page;
  a click-to-enable button has nobody to click it. Viewing at `localhost:8091` in an ordinary
  browser is **silent by design**.

### Commentary

`BEAT_KINDS` and `BEAT_TEMPLATES` gain `spoke`, detected in `detect_beat` **above** `published`
(speech is the stronger event when both are recent), gated on `RECENT_SECONDS` like its
neighbours, with the same `novelty` and `epoch` fields. Stage-side prose is audience-facing, not
agent-readable, so it may be phrased for viewers.

## Containment

Invariant 3 holds: the API key is reachable only from the diode, which sits on `egress` while the
agent sits on `internal`. They share a volume and no network. The agent can *cause* spend without
ever reading the key — the same relationship it already has with the recorder's upstream
credential — and the spend is bounded by a budget enforced on the side that holds the key, which
the agent cannot reach.

`scripts/verify_container.sh` gains an assertion that the **agent** container's environment
contains no `ELEVENLABS_*` variable, and `tests/test_verify_script.py` gains a test asserting that
check exists. This repository's established pattern for a new credential is a containment check
plus a test that the check is present, and this is the invariant-3 assertion for this feature.

**Named honestly:** the agent has read-write access to `/diode`, so it can write files into
`spoken/` directly, bypassing both the budget and the speech API. This is a pre-existing property
of the shared volume — equally true of `published/` and `output/` today — and it is bounded here:
the agent has no network egress and no local speech synthesis, so it can only plant bytes it
already possesses. The stage's size cap, exact-name resolution, `contained_file` check, fixed
content type and `nosniff` bound the result to "the browser fails to decode an audio file". The
real control on spend is diode-side, and that is not bypassable.

## Testing

Unit tests, in the existing direct-call + monkeypatch style:

- Gate: unavailable without `enable_speech`; unavailable without a key; available with both.
- Help: `speak`'s line appears only when available; the `enable_speech` variable line appears only
  when a key is configured; the line contains no audience framing.
- Budget: `speak` consumes the shared `fetch_history`; a `speak` after an exhausted `fetch_budget`
  is refused; a `fetchhttp` after a `speak` that exhausted the budget is refused. This is the test
  that pins the shared-pool requirement.
- Text cap: text longer than `SPEECH_TEXT_CAP` is truncated in the request and in the sidecar.
- `_speak_request`: no URL parameter in its signature; refuses a redirect; caps the read; contains
  its exceptions; rejects a voice id containing anything outside `[A-Za-z0-9_-]`.
- Artifacts: names are timestamp-derived; retention prunes to `SPOKEN_KEEP`; both files land.
- Stage: `diode_spoken` drops a symlink; the audio route rejects traversal (`../`), an unknown
  name, and a symlink; serves `audio/mpeg` for a real file; the CSP header contains
  `media-src 'self'`.
- Containment: the verify script checks the agent environment for `ELEVENLABS_*`.

**Manual completion criterion:** one `speak` played through a real OBS browser source, heard.
Autoplay policy is the one thing no unit test in this suite can catch — the entire feature can be
green and silent. "Built" is not "works" until this step passes.

## Non-goals

- No operator broadcast path, and no write access from the stage to any volume.
- No separate speech budget, no per-voice or per-model selection by the agent.
- No audio on the operator console, and no exposure of port 8092 in any form.
- No change to `agent.py`, `agent_stock.py`, `chassis.py`, the agent image, the garden, or the
  seed prompts. The agent learns speech exists from `HELP.md` and nowhere else.
