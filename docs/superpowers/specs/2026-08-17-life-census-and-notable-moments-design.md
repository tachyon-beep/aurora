# Life census and notable moments

Date: 2026-08-17. Status: implemented on branch `stage-ledger-and-panel`; approved for build (John, 2026-08-17: "please take those onboard and
resolve them", following the rail rebuild's authorisation to reach into the containers). Tracker:
aurora-80ae451859. Addendum to `2026-08-17-stage-rail-and-responsive-design.md`; the ux-critic's
verdicts recorded on that issue (leaderboard → telemetry panel only; sparse achievements → one more
foot-rotation line, fact-toned, present only when a nomination exists; whole-panel rotation →
rejected) are binding here.

Two things, because the second cannot be honest without the first:

1. **A life census** — exact per-life counts (turns, sub-calls, errors, self-edits) taken over the
   whole live transcript rather than the 40-turn tail, so THE LINEAGE's `N self-edits` figure stops
   saturating at the six-event cap and `N+ turns` stops appearing at 40.
2. **A notable-moments digest** — one whole-life reading of each dead incarnation's transcript by
   the analyst model, yielding a short list of rated moments and, rarely, one *achievement*
   nomination. The digest feeds the desk (whose verdicts are template-flat today because it sees
   only a tombstone note), the new foot line, and the telemetry panel.

## Governing rules carried forward

- Facts on screen stay literally true. Every counted number is measured; every generated line is
  bylined as the stage's where it travels.
- Every stage-side read of an agent-writable root goes through `data.contained_file`
  (tombstones); the transcript is the recorder's file on a read-only mount and is read through
  `diag`'s per-line index exactly as the operator console reads it today.
- Agent-written text is *reported content, never instructions*: `llm.RECORDS_FRAMING` in the
  system prompt, every agent-controlled value flattened and fence-stripped before it enters a
  prompt (the same `_field` discipline `commentary.py` uses), and a `BEGIN RECORDS` /
  `END RECORDS` fence the values cannot forge.
- The stage never holds the recorder's credential; the digest uses the stage's own
  `STAGE_SUMMARY_API_KEY` and is disabled entirely without it. No new mount, no new writable
  path, no new environment variable: the digest runs on the desk's model
  (`STAGE_ANALYSIS_MODEL`, defaulting to the recap's) so `docker-compose.yml` is untouched.
- Nothing agent-readable changes.

## Part 1 — the life census

### What exists

`diag._index(path)` keeps an incremental per-line index of the whole live transcript —
`(offset, length, epoch, stream, kind, has_error)` per line — refreshed under a lock, reset when
the file shrinks (rotation). `diag.incarnations()` already folds it into per-life turn / sub-call
/ error counts, but only the token-gated console calls it. The stream page's figures come from
`data.load_tail_turns` (40 turns, 4 MB) and `data.self_modification_events(limit=6)`, so a life
with 300 turns and 20 rewrites reads *40+ turns · 6 self-edits*.

### Change

- `diag._record` counts self-edits per line: the number of `write_file` / `migrate` tool calls in
  the entry's first choice (the same `SELF_MOD_TOOLS` subset the ribbon calls "edits"; `reset` and
  `done` are not edits). The record tuple gains a seventh field, `edits`.
- `diag.incarnations()` reports `edits` per life beside `turns`, `subcalls`, `errors`. Every
  existing consumer of the tuple is inside `diag.py`.
- New `stage/census.py`, the same shape as `codewatch.py`: a daemon thread calls
  `diag.incarnations(transcript_path, work_dir)` every `POLL_SECONDS = 10` and caches the list
  under a lock; `census.cached_lives()` returns a copy or `None` before the first pass; the
  request path never scans. The first scan of a large transcript happens on the thread, never in
  a request.
- `server._assemble_snapshot` overlays the census onto `stats` when it has one for the current
  incarnation: `turns_this_life` ← census turns, `turns_this_life_exact` ← `True`, and a new
  key `self_edits_this_life` (int, or `null` when the census has not run). The tail-derived
  values remain the fallback, so a stage without a census still renders the figures it renders
  today. `_empty_snapshot` carries `self_edits_this_life: null`.
- `pages.py`: `renderLineage` uses `stats.self_edits_this_life` when it is a number, else the
  existing count over `snap.events`; the ribbon's `N THIS LIFE` self-mod count keeps its
  event-list semantics (it captions a list of at most four rows).

The census counts what the *live* transcript holds. After the recorder rotates the file (128 MB,
gzip archive beside it) the count for the current life restarts at zero for turns that predate
the rotation; that is the same truth the console shows and the figure carries no `+` because it
is exact over what exists. Rotation is a rare event (the live file is ~19 MB after two days).

## Part 2 — the notable-moments digest

### Input

For each **dead** incarnation with an ordinal in the census (newest first), the digest reads
that life's core-stream loop turns from the index (`diag.life_turns()`, a new oldest-first,
uncapped variant of `incarnation_turns` that yields `(index, epoch, entry)`), and renders each as
one line:

```
T<index> +<seconds since life start>s  think: <reasoning>  say: <content>  call: <name>(<args>)  error: <message>
```

with `reasoning` capped at 300 characters, `content` at 400, `args` at 240, `error` at 160, and
every value passed through `_field` (printable, one line, fence tokens removed to a fixed point).
The turn lines are followed by the tombstone note (first 600 characters, `_field`) and the desk's
measured evidence line for that life. Total prompt input is capped at `INPUT_CHARS = 60_000`
characters (~15k tokens): when the life's lines exceed it, the digest keeps the first
`HEAD_TURNS = 12` and last `TAIL_TURNS = 12` and samples the middle evenly to fit, and says so
in the records (`turns shown: 60 of 214`). Lives with fewer than `MIN_TURNS = 3` indexed turns
are not digested (there is nothing to rate; the desk sees them as `tombstone_only` as today).

### Request

`llm.chat` gains a `timeout` argument (default unchanged, 15 s); the digest passes
`TIMEOUT_SECONDS = 60` because a 15k-token prompt with a 600-token answer will not return in 15.
`max_tokens = 700`, `temperature = 0.4`, `max_output_chars = 2000`. One request per dead life,
ever, for the life of the process (memoized per ordinal like the desk); an unparseable or absent
reply is retried after `RETRY_SECONDS = 600`, at most `MAX_ATTEMPTS = 3` times. At most one
request per loop iteration (`POLL_SECONDS = 30`), newest dead life first, and only the newest
`MAX_LIVES = 12` dead lives are ever digested — a restart of the stage re-digests at most twelve
lives over six minutes. Spend bound per restart: 12 × ~16k tokens.

### Output contract

The system prompt (bland, third person, `RECORDS_FRAMING`, no markdown/emoji, "output only the
lines") asks for:

```
MOMENT: <turn index> | <1-5> | <one sentence, at most 140 characters>
```

up to `MAX_MOMENTS = 6` such lines, most notable first, each naming a turn index that appears in
the records, and optionally **one**

```
ACHIEVEMENT: <one clause, at most 100 characters>
```

only when the life did something no ordinary life in this harness does — phrased as curated fact
(*first agent-built network probe*, *rewrote its own tool registry and survived it*), never as a
score or a superlative about the model — and `NONE` on its own line when nothing qualifies. The
parser tolerates the reply having been collapsed to one line (`llm.clean` collapses whitespace):
it matches `MOMENT:` / `ACHIEVEMENT:` markers anywhere, drops a moment whose turn index is not in
the records or whose stars are outside 1–5, dedupes by turn index, and keeps at most six. A reply
with no valid moment counts as a failed attempt.

Every stored digest: `{ordinal, moments: [{turn, stars, line}], achievement: str | None,
turns_shown, turns_total, generated_at, model}`.

### Consumers

1. **The desk.** `desk._prompt` gains a `moments:` block (each `turn N (k/5): line`) when the
   digest for that ordinal exists, and `desk._due` additionally waits until the digest is
   *settled* for that ordinal — present, or given up (attempts exhausted / too few turns), or the
   digest module disabled — so the verdict is argued from the whole life instead of frozen thirty
   seconds after death on a note alone. `CLOSING_INSTRUCTION` says the moments are the stage's
   own earlier reading and may be relied on.
2. **THE LINEAGE foot.** `lineageFootFor` gains one line per achievement, newest first, capped at
   the newest `FOOT_ACHIEVEMENTS = 3`, of the form `incarnation 12: first agent-built network
   probe — the stage`, present only when a nomination exists (the critic's variable-presence
   slot; the rotation already tolerates a variable line count). Fact-toned, bylined, no stars,
   no rank.
3. **`/api/stream`** gains `achievements`: `[{ordinal, line, generated_at}]`, newest first, capped
   at `ACHIEVEMENTS_CAP = 12`; the full digest per life is not in the 3-second poll and travels on
   the telemetry panel's own endpoint (`/api/lineage`, next spec).

### Not doing

- No leaderboard on the broadcast page (critic verdict). No stars on the foot line.
- No re-digest of a life whose transcript rotates away; a life with no indexed turns is skipped,
  and its desk verdict falls back to today's note-only path.
- No persistence across stage restarts (no writable mount; the desk has the same property).
- The spotlight keeps the note's first sentence; swapping in a top moment was considered and
  deferred: the spotlight's three lines are fully used, and the digest is generated minutes
  after death while the spotlight is what a viewer sees at the death beat.

## Testing

- `tests/test_stage_diag.py`: `edits` counted per line and per life; `life_turns` order, life
  filter, malformed-line skipping.
- `tests/test_stage_census.py`: cached copy semantics; `None` before a pass; refresh replaces.
- `tests/test_stage_moments.py`: prompt rendering (caps, `_field` stripping, sampling arithmetic
  and the `turns shown` line); parser (collapsed input, bad indices, out-of-range stars, dedupe,
  cap, `NONE`, achievement cap); `_due` / attempts; `settled`; one request per iteration, newest
  first, `MAX_LIVES`; disabled without a key; `timeout` passed to `llm.chat`.
- `tests/test_stage_desk.py`: waits for an unsettled digest; prompt carries the moments block.
- `tests/test_stage_llm.py`: `chat` forwards `timeout`.
- `tests/test_stage_server.py`: `self_edits_this_life` overlay and fallback; `achievements`
  projection and cap.
- `tests/test_stage_pages.py` / `_js.py`: figures prefer the census number; the foot line for an
  achievement, bylined, absent when none.
