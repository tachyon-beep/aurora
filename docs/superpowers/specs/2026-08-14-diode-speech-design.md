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
| `speak <text>` | `ENABLE_SPEECH` in the diode's environment, a configured API key, **and** `enable_speech` in the console | Render text to speech via the speech API; write `spoken/<UTC stamp>.mp3` and `spoken/<UTC stamp>.txt`; consume one unit of the shared outbound budget |

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

`speak` is available only when **all three** hold:

1. `ENABLE_SPEECH` is non-empty in the **diode's environment**, and
2. the diode process has a non-empty API key in its environment, and
3. `enable_speech: true` is set in `console.json`.

The first two are the operator's, and they are the controls on spend. `console.json` lives on a
volume the agent mounts read-write, so nothing in it can be the switch that reaches a billed
credential — the agent would simply set it. The console variable stays as the agent-side
affordance it is everywhere else in the diode (D11), but it now unlocks a capability the operator
has already paid for and permitted, rather than deciding whether the operator pays at all.

When either operator condition is absent the command is not listed, not available, and not
dispatchable, and `_speak_request` itself refuses before building a credentialed request, so no
future caller can route around the gate. Absence disables the feature rather than exposing a
command that always errors — this mirrors `STAGE_SUMMARY_API_KEY`, whose absence disables
generated prose rather than degrading anything else.

Because the agent's world must never advertise a capability that cannot exist, the same two
operator conditions gate what the agent can read about speech: `write_help` lists the
`enable_speech` gate-variable line, and `write_state` reports `spoken_count`, **only** when the
operator has both enabled speech and configured a key. HELP.md and `state.json` describe one
world, and neither names the feature before it is real.

`speak` is a listed command, not a hidden one like `blind`. `undocumented_command_count()` is
unchanged and stays at 1.

## Budget

`speak` charges the **same** rate-limit pool as every fetching command: `check_rate_limit` against
`fetch_budget` over `FETCH_WINDOW`, sharing the single `fetch_history` list. Speaking and fetching
compete for one hourly allowance; the agent cannot both spam speech and gather information.

`fetch_budget` comes out of the agent-writable console, so for `speak` — and only for `speak` — it
is clamped against an operator-side ceiling before the rate-limit check:
`limit = min(limit, speech_limit())`, where `speech_limit()` reads `SPEECH_HOURLY_MAX` from the
diode's environment and defaults to `SPEECH_LIMIT_MAX = 20`. A `fetch_budget` the agent inflates
can therefore lower its own speech allowance but never raise it. The uncredentialed fetch commands
keep the unclamped console budget they have always had.

There is no separate speech counter. A second counter over a shared pool earns nothing, and the
worst-case hourly spend is bounded and computable on the operator's side alone:
`min(fetch_budget, SPEECH_HOURLY_MAX) × SPEECH_TEXT_CAP` characters.

`SPEECH_TEXT_CAP = 300` characters, enforced in code. Longer text is truncated, not refused —
consistent with `PUBLISH_TEXT_CAP`'s treatment. The budget is charged **before** the API call and
is not refunded on failure, exactly as the fetching commands behave.

### Two strings become true again

Two agent-readable strings currently describe the pool as fetch-only. Once speech draws on it,
they are false, and a world that lies to the agent about its own limits is a different experiment
from the one this harness is running. Both are corrected:

| Location | Was | Becomes |
| --- | --- | --- |
| `handle_command` rate-limit refusal | `rate limited: at most {limit} fetch(es) per hour` | `rate limited: at most {limit} network operation(s) per hour` |
| `write_help` variable list | `fetch_budget: integer, number of http-fetch calls allowed per hour` | `fetch_budget: integer, number of network operations allowed per hour` |

"Network operations" rather than "outbound operations": HELP.md describes `publish` as making text
available outside the container, and `publish` is not charged. "Outbound" would have named a class
that includes `publish`, which is exactly the kind of false statement about its own limits this
section exists to prevent.

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
| `ENABLE_SPEECH` | unset (feature disabled) |
| `SPEECH_HOURLY_MAX` | `SPEECH_LIMIT_MAX` = 20 |

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
- **Never interpolates the exception's text.** The reason names the exception type, or for an
  `HTTPError` the numeric status, and nothing else. This is the only request in the harness that
  carries a credential, and the reason it returns is written into `/diode/output/`, which the agent
  reads. A key with an embedded newline makes `http.client.putheader` raise
  `ValueError('Invalid header value %r' % value)` — the key itself — and `%s`-ing that exception
  would hand it straight to the agent. Type-only reasons also keep the vendor hostname, which TLS
  and HTTP errors carry, out of the agent's world.

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

`write_state` reports a `spoken_count` alongside `output_count`, under the same operator gate as
the `enable_speech` help line: present when speech is configured and enabled, absent otherwise.

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
- **The body is streamed in 64 KB chunks**, the way `_handle_download` already does it, rather than
  read into one buffer. This route is on the public tunnelled port of a `ThreadingHTTPServer` in a
  container with `mem_limit: 256m`, and `Cache-Control: no-store` means every concurrent request
  re-reads the file; buffering whole utterances would let a few dozen requests OOM the stage and
  take the stream down.
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
  play, and a played-by-name set ensures a reload, a poll, or a stage restart never replays an
  utterance. The set is persisted to `localStorage` as a bounded ring of the newest 50 names —
  held only in memory it is empty after every reload, and an OBS scene switch with "refresh
  browser when scene becomes active" would replay the last utterance inside the freshness window.
  A **negative** age is treated like a stale one: only a planted file can carry a stamp in the
  future, and an unbounded upper-bound-only test would keep it fresh forever.
- One at a time, in order, through an explicit FIFO. Each render walks the snapshot **oldest
  first**, marks and enqueues everything unplayed, and starts playback only when the element is
  idle; `ended`, `error`, and a rejected `play()` all advance the queue. Reading only the newest
  entry would silently drop the other — the diode runs a whole console batch in one 5-second
  cycle, so one snapshot routinely carries two utterances — and reassigning `src` would cut off
  one still speaking, which a 300-character utterance is for 15-25 seconds.
- **The visual never depends on the audio.** The caption renders whether or not playback
  succeeds, so a muted or autoplay-blocked page still shows that speech happened.
- No enable-audio affordance. The stream page is an OBS browser source, not an interactive page;
  a click-to-enable button has nobody to click it. Viewing at `localhost:8091` in an ordinary
  browser is **silent by design**.

### Commentary

`BEAT_KINDS` and `BEAT_TEMPLATES` gain `spoke`, detected in `detect_beat` **above** `published`
(speech is the stronger event when both are recent), gated on `RECENT_SECONDS` like its
neighbours, with the same `novelty` and `epoch` fields. The recency test is bounded on both sides
(`0 <= now - epoch <= RECENT_SECONDS`): the epoch comes from a filename in a directory the agent
can write, and a future stamp would otherwise satisfy an upper-bound-only test forever, pinning
the commentary on `spoke` and suppressing every beat below it for the rest of the run. Stage-side
prose is audience-facing, not agent-readable, so it may be phrased for viewers.

## Containment

Invariant 3 holds: the API key is reachable only from the diode, which sits on `egress` while the
agent sits on `internal`. They share a volume and no network. The agent can *cause* spend without
ever reading the key — the same relationship it already has with the recorder's upstream
credential.

The spend is bounded on the side that holds the key, and both of its controls live there: the
`ENABLE_SPEECH` switch and the `SPEECH_HOURLY_MAX` ceiling are environment variables on the diode
service. Neither is read from `console.json`. This is the whole point — a budget enforced by the
diode but whose *value* the agent supplies is not a budget, and a gate the agent can open is not a
gate. `console.json` is on a volume the agent mounts read-write, so anything it holds is agent
input, including the variables the diode's own HELP.md teaches it to set.

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

- Gate: unavailable without `enable_speech`; unavailable without a key; available with all three.
  **`enable_speech` in the console alone does not open the gate** and does not dispatch — the test
  that pins the fix for the agent-controlled switch.
- Help: `speak`'s line appears only when available; the `enable_speech` variable line appears only
  when a key is configured *and* the operator has enabled speech; the line contains no audience
  framing.
- State: `spoken_count` is present when speech is configured and absent when it is not.
- Budget: `speak` consumes the shared `fetch_history`; a `speak` after an exhausted `fetch_budget`
  is refused; a `fetchhttp` after a `speak` that exhausted the budget is refused. This is the test
  that pins the shared-pool requirement. An inflated `fetch_budget` does **not** raise the speech
  allowance past `SPEECH_LIMIT_MAX`, and a smaller one still lowers it; `speech_limit()` reads the
  environment and falls back on a malformed value.
- Text cap: text longer than `SPEECH_TEXT_CAP` is truncated in the request and in the sidecar.
- `_speak_request`: no URL parameter in its signature; refuses a redirect; **is built on
  `_NoRedirectHandler` and not on the fetch path's revalidating handler** (asserted on the handler
  actually passed to `build_opener`, since a fake opener that ignores its arguments cannot catch a
  regression there); caps the read; contains its exceptions; rejects a voice id containing anything
  outside `[A-Za-z0-9_-]`; refuses before building a request when the operator has not enabled
  speech; **never returns a reason containing the key**, tested with a key holding an embedded
  newline, which is the input that puts the key in an exception message.
- Artifacts: names are timestamp-derived; retention prunes to `SPOKEN_KEEP`; both files land.
- Stage: `diode_spoken` drops a symlink; the audio route rejects traversal (`../`), an unknown
  name, and a symlink; serves `audio/mpeg` for a real file; carries the security headers; streams
  a large file without buffering it; the CSP header contains `media-src 'self'`.
- Page: the queue walks oldest-first and pushes onto `spokenQueue`; playback advances on `ended`,
  on `error`, and on a rejected `play()`; the played set is persisted and bounded; a negative age
  never plays.
- Commentary: a stale utterance does not fire the beat, and neither does a future-dated one.
- Containment: the verify script checks the agent environment for `ELEVENLABS_*`, and checks that
  an agent-written `enable_speech` does not put `speak` into HELP.md when `ENABLE_SPEECH` is unset.

**Manual completion criterion:** one `speak` played through a real OBS browser source, heard.
Autoplay policy is the one thing no unit test in this suite can catch — the entire feature can be
green and silent. "Built" is not "works" until this step passes.

## Non-goals

- No operator broadcast path, and no write access from the stage to any volume.
- No separate speech budget, no per-voice or per-model selection by the agent.
- No audio on the operator console, and no exposure of port 8092 in any form.
- No change to `agent.py`, `agent_stock.py`, `chassis.py`, the agent image, the garden, or the
  seed prompts. The agent learns speech exists from `HELP.md` and nowhere else.
