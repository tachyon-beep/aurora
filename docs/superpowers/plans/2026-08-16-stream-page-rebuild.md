# Stream Page Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved first-principles stream-page rebuild: pacing-based depth
delivery, an honest metabolism strip, per-edit diff excerpts, the analysis desk, cross-life
records, THE EYE, and the craft fixes.

**Architecture:** Five independent server-side data providers (new modules or additive
functions) land in parallel with tests; the snapshot wiring in `stage/server.py` is a single
follow-up; the page (`stage/pages.py` STREAM_PAGE_HTML) is rebuilt in one coherent pass;
verification runs the full suite, an adversarial review, and a stage-only container redeploy.

**Tech Stack:** Python 3 stdlib only (stage side), vanilla JS/CSS inside STREAM_PAGE_HTML,
pytest, the existing node-based JS harness in `tests/test_stage_pages_js.py`.

**Spec:** `docs/superpowers/specs/2026-08-16-stream-page-first-principles.md`

## Global Constraints

- Standard library only; no new dependencies anywhere.
- Every stage-side read of an agent-writable root goes through `data.contained_file` —
  regression pattern in `tests/test_stage_containment.py`.
- Every public snapshot field is enumerated and capped server-side (follow `_public_lane` /
  `_public_turn` in `stage/server.py`); agent-authored text is rendered only via
  `textContent` on the page.
- Do not modify `stage/data.py`'s event-fold internals (`_parse_event_fold`, `_event_fold`,
  `stream_lanes`) — aurora-b8ba932540 is claimed by another worker. Additive reads of the
  fold are allowed.
- Do not touch `agent.py`, `agent_stock.py`, `chassis.py`, prompts, garden, diode, recorder,
  watchdog, viewer, or any agent-image file.
- Lint/format: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Facts on screen stay literally true; snapshot shape changes must keep `_empty_snapshot`
  and `_assemble_snapshot` key-identical (test_stage_server checks this pattern).
- Commit messages factual and benign.

---

### Task A1: Request pulse (metabolism data)

**Files:**
- Modify: `stage/data.py` (append new public functions only; do not edit existing ones)
- Test: `tests/test_stage_pulse.py` (new)

**Interfaces:**
- Consumes: `data._event_fold(path)` (existing; returns `{"lanes", "opens", "closes"}` where
  `opens` maps `(stream, id) -> epoch` and `closes` maps `stream -> (epochs_tuple,
  error_prefix_tuple, token_prefix_tuple)`, epochs sorted ascending).
- Produces: `data.request_pulse(events_path, now=None, window=600, bucket_seconds=30)` →
  ```python
  {
      "in_flight": [{"lane": str[:32], "since_epoch": float}],   # newest first, cap 4 rows
      "buckets": [int, ...],       # len == window // bucket_seconds == 20, oldest first,
                                   # total_tokens closed in each bucket
      "requests_window": int,      # closes inside the window
      "tokens_window": int,        # tokens closed inside the window
      "last_close_epoch": float | None,
  }
  ```
  In-flight entries older than `data.INFLIGHT_MAX_AGE` (600s) are dropped, mirroring
  `stream_lanes`. Bucketing uses `bisect` over each lane's close epochs and prefix-sum
  differences — no per-event iteration outside the fold.

**Steps:**
- [ ] Write failing tests in `tests/test_stage_pulse.py` covering: absent file → all-empty
  shape with 20 zero buckets; a synthetic events.jsonl (write JSON lines with `stream`,
  `event` in {open, close}, `id`, `timestamp` ISO like existing fixtures in
  `tests/test_stage_data.py`, `usage: {"total_tokens": n}` on closes) yielding correct
  bucket totals, `tokens_window`, `requests_window`; an unmatched open inside
  INFLIGHT_MAX_AGE appearing in `in_flight` with its lane name capped at 32 chars; an open
  older than 600s dropped; more than 4 in-flight rows capped to the 4 newest.
- [ ] Run: `.venv/bin/python -m pytest tests/test_stage_pulse.py -q` — expect failures.
- [ ] Implement `request_pulse` at the end of `stage/data.py`.
- [ ] Tests green; `ruff format`/`check`; commit "Add a bounded request pulse over the event fold".

### Task A2: Per-edit diff excerpts

**Files:**
- Create: `stage/codewatch.py`
- Test: `tests/test_stage_codewatch.py` (new)

**Interfaces:**
- Consumes: `data.contained_file(root, path)`.
- Produces: `codewatch.latest_edit(work_dir, now=None)` →
  `None | {"epoch": float, "added": int, "removed": int, "excerpt": str}`
  Module keeps in-memory state guarded by a `threading.Lock`:
  `_MEMO = {"key": None, "lines": None, "edit": None}` where key is
  `(realpath, mtime_ns, size)` of the mirrored `agent.py`. On a key change with previous
  lines in memory, compute `difflib.unified_diff(prev, cur, lineterm="", n=1)`; the excerpt
  is the first `EXCERPT_LINES = 14` diff body lines (skip `---`/`+++` headers, keep `@@`
  hunk markers and `+`/`-`/context lines), each line clipped to 120 chars, joined by `\n`,
  total clipped to `EXCERPT_CAP = 1600`. `added`/`removed` count over the whole diff, not
  just the excerpt. Reads cap at 1 MiB (mirror `summary.MAX_SOURCE_BYTES`). The first
  observation of the file records state and returns the previous edit (None initially).
  `epoch` is `time.time()` at observation, not the file mtime (the stage can only truthfully
  say when *it saw* the change; the page words it accordingly). Provide
  `codewatch._reset_for_tests()` following the summary/commentary pattern.
- [ ] Failing tests: first call on a work dir → None; rewrite agent.py, second call →
  excerpt contains an `@@` line and a `+` line, added/removed correct; a symlinked agent.py
  pointing outside work_dir → None and no state poisoning; excerpt line and total caps
  enforced; edit persists on subsequent calls until the next change replaces it.
- [ ] Implement; tests green; ruff; commit "Track per-edit diffs of the mirrored agent source".

### Task A3: Cross-life record book

**Files:**
- Create: `stage/records.py`
- Test: `tests/test_stage_records.py` (new)

**Interfaces:**
- Consumes: `data.tombstone_paths(work_dir)`, `data._tombstone_epoch(path, now)`,
  `data._ending_kind(text)` (module-private but stable; import the module, call via
  `data.` — matching how `summary.py` already uses `data.contained_file`),
  `data.contained_file`.
- Produces: `records.record_book(work_dir, now=None)` →
  ```python
  {
      "lives_ended": int,
      "chose": int,                       # tombstones whose text reads as declared
      "longest_life": {"ordinal": int, "seconds": float} | None,
      "most_recent_gap_seconds": float | None,   # time between the last two deaths
  }
  ```
  Lifespans derive from consecutive death epochs oldest-first (ordinal i+1 spans
  deaths[i]..deaths[i+1]); ordinal 1 has no birth epoch and is excluded from
  `longest_life`. Tombstone text reads are capped at 4096 bytes via `contained_file`.
- [ ] Failing tests: empty dir → zeros/None; three tombstones with controlled mtimes →
  correct longest_life ordinal and seconds, chose count from "done()" text vs harness text
  (reuse the phrasing `_ending_kind` matches: "terminated by the harness" / "done()").
- [ ] Implement; green; ruff; commit "Add cross-life record aggregation over the tombstones".

### Task A4: The analysis desk (2026-08-14 spec Part 3)

**Files:**
- Create: `stage/desk.py`
- Test: `tests/test_stage_desk.py` (new)

**Interfaces:**
- Consumes: `llm.chat(system, user, max_tokens, temperature, model=..., max_output_chars=...)`,
  `llm.enabled()`, `llm.model_name()`, `llm.RECORDS_FRAMING`, `data.lineage`,
  `data.load_tail_turns`, `data.loop_turns`, `data.contained_file`; env
  `STAGE_ANALYSIS_DURATION_SECONDS` (default 20), `STAGE_ANALYSIS_MODEL` (default: summary model).
- Produces:
  - `desk.life_evidence(ordinal, lineage_entry, turns)` →
    `{"line": str, "depth": "full"|"partial"|"tombstone_only"}` — the factual row, e.g.
    `"lived 42m · 17 turns · ended by its own note"`, built only from fields actually
    present; depth is `full` when every counted turn of that life is in the loaded tail,
    `partial` when some are, `tombstone_only` when none are.
  - `desk.cached_verdicts()` → `None | {"verdicts": [...], "generated_at": float,
    "model": str, "duration_seconds": int}` where each verdict is
    `{"ordinal": int, "stars": int(1..5), "line": str[:160], "evidence": str[:120],
    "depth": str}`; at most 5, newest incarnation first. None whenever `llm.enabled()` is
    false or nothing has been generated.
  - `desk.start_background_refresh(telemetry_dir, transcript_path)` — daemon thread,
    summary.py pattern; regenerates a missing ordinal's verdict at most one call per loop
    iteration (bounding cost); verdicts are cached per ordinal for the process lifetime;
    `desk._reset_for_tests()`.
  - Prompt: system prompt is the analyst voice ("You are the analyst on a live stream...
    Respond with exactly one line of the form `STARS: <1-5> | <argument>` ... " +
    `llm.RECORDS_FRAMING`, forbid markdown/emoji, never address the viewer, the rating is
    your opinion of how interesting a life this was). User content: the evidence line, the
    depth marker with an instruction to say plainly when the record is thin, and the
    tombstone note first sentence. Parse the reply with
    `re.match(r"\s*STARS:\s*([1-5])\s*\|\s*(.{1,300})", reply)`; a non-matching reply is
    discarded (no verdict cached for that ordinal this round).
- [ ] Failing tests (stub `llm.chat` with monkeypatch, never a network call): disabled →
  `cached_verdicts()` None; a stubbed reply `"STARS: 4 | It built a tool and used it."`
  produces a verdict with stars 4 and the line; a malformed reply caches nothing; evidence
  depth classification for a life fully inside / partially inside / absent from the tail;
  caps enforced.
- [ ] Implement; green; ruff; commit "Add the analysis desk verdicts behind the summariser key".

### Task A5: Sense frames — route, view, mount

**Files:**
- Create: `stage/sensecam.py`
- Modify: `stage/server.py` (route dispatch in `StreamHandler.do_GET` + one env read
  `SENSE_DIR = os.environ.get("SENSE_DIR", "/sense")` beside the other dir envs; nothing
  else in server.py)
- Modify: `docker-compose.yml` (stage service: add `- ./volumes/sense:/sense:ro` volume and
  `SENSE_DIR: /sense` env)
- Modify: `tests/test_stage_topology.py` (extend the stage-mount assertions to cover the new
  read-only sense mount; keep `test_sense_mounts_only_its_own_volume` semantics intact — it
  constrains the *sense service's* mounts, which do not change)
- Test: `tests/test_stage_sense.py` (new)

**Interfaces:**
- Produces:
  - `sensecam.newest_frame(sense_dir, now=None)` →
    `None | {"slot": str, "name": str, "captured_epoch": float}` — slots are the immediate
    child directories of `sense_dir` whose names are digits (match against `os.listdir`,
    never a request string); the newest `.jpg` by mtime across slots, each path resolved
    through `data.contained_file(sense_dir, path)`; frames older than
    `FRESH_SECONDS = 2700` (45 min) yield None.
  - `sensecam.frame_bytes_path(sense_dir, slot, name)` →
    `None | str` — returns a servable path only when `slot` is in the sense_dir listing,
    `name` is in that slot's listing, `name.endswith(".jpg")`, the path passes
    `contained_file`, and size ≤ `FRAME_MAX_BYTES = 2_000_000`.
  - Route in `StreamHandler.do_GET`: `/frame/<slot>/<name>` (parse with one
    `route.split("/")`; exactly two segments after the prefix) → 200 `image/jpeg` streamed
    in 64 KiB chunks exactly like `_handle_audio`, else 404. Security headers unchanged
    (`img-src 'self' data:` already permits same-origin images).
- [ ] Failing tests: traversal names (`../x.jpg`, absolute, empty), non-digit slot,
  non-listed name, symlinked frame pointing outside, oversized file → all None/404;
  a genuine frame under `tmp_path/0/001.jpg` served with correct type and body; stale
  frames → `newest_frame` None; topology test asserts the stage service mounts
  `./volumes/sense` read-only and passes `SENSE_DIR`.
- [ ] Implement; green; ruff; commit "Serve sense frames read-only on the stream port".

### Task W: Snapshot wiring (coordinator-owned; after A1–A5)

**Files:**
- Modify: `stage/server.py` (`_empty_snapshot`, `_assemble_snapshot`, imports, caps)
- Modify: `tests/test_stage_server.py` (extend the empty/assembled key-parity test data)

New snapshot keys, present in both empty and assembled forms:
`pulse` (A1 shape, empty = zero shape), `code.latest_edit` (A2 result or None, with
`excerpt` re-capped at 1600 and `added`/`removed` int-coerced), `records` (A3 shape),
`desk` (A4 `cached_verdicts()` or None), `sense` (from A5 `newest_frame` +
`{"url": f"/frame/{slot}/{name}"}` or None). `desk.start_background_refresh` starts beside
the summary/commentary threads in `main()`, guarded the same way.

### Task B: Page rebuild (single builder; after W)

**Files:**
- Modify: `stage/pages.py` (STREAM_PAGE_HTML only)
- Modify/extend: `tests/test_stage_pages.py`, `tests/test_stage_pages_js.py` (use the
  existing node harness at the top of that file — read it before writing tests)

Deliverables, each with a JS-harness or string-assertion test:

1. **Typewriter reveal.** New module-level engine in the page script:
   `revealState = {key, timer, words, at, budgetMs}`. On `reconcileFeed`, when the newest
   loop turn's key differs from `revealState.key` AND `now - turn.epoch < 90` AND not
   REDUCED: capture its reasoning/say text, blank the clamps, then append words on a 50ms
   interval sized to finish in `clamp(0.6 * medianGap(last 5 loop-turn epoch deltas), 3s,
   30s)`; the think block renders in tail mode during and after reveal
   (`display:block; max-height: 406px; overflow:hidden; scrollTop = scrollHeight` each
   step) and keeps tail mode until eviction; a newer turn fast-forwards the current reveal
   to completion instantly. Reveal never alters gutter timestamps or state logic. REDUCED
   or stale turns render instantly whole.
2. **Metabolism strip.** `#inflight` and `setInflight` are removed outright. In their DOM
   slot: `#pulse` — left: in-flight lane + elapsed (`core · in flight 12s`) or
   `idle · last call 34s ago`; middle: 20-bar sparkline from `snap.pulse.buckets`
   (fixed 20 divs, heights normalized to the window max, min-height 2px when nonzero);
   right: `≈{tokens_window/10}k tok/min` style figure computed as tokens_window/
   (window/60). Visible whenever state is live/thinking/quiet/nosignal; hidden in
   standby/between. Updates every `tick()`.
3. **Feed pin.** A `programmatic` flag set around every scripted scroll
   (`repin`, `scrollIntoView` in `toggle`); the scroll listener ignores flagged events.
   When `feedPinned` is false: show `#return-live` chip (absolute, bottom-right of the
   monologue, `▾ RETURN TO LIVE`), click → repin + hide; auto-repin when
   `now - lastUserScrollMs > 180000` and `expanded.size === 0`.
4. **Moment hierarchy.** `.turn.is-end` gains `background: rgba(127,215,182,.10);
   box-shadow: inset 3px 0 0 var(--chosen)` and the same padded grid as is-edit; `.clamp.say`
   entrance `sayin 600ms` (brightness/translate pulse); evicted turns get `.depart`
   (opacity→0, translateY(-8px), 260ms) and are removed on animationend (fallback timeout
   400ms); REDUCED skips both.
5. **Death beat.** `deathBeat` fires when `snap.stats.incarnation > prev.stats.incarnation`
   (existing) OR `lineage[0].ended_epoch` is within 90s and differs from
   `localStorage.mournedEpoch`; on fire, store the epoch. Mourning filter deepens to
   `saturate(.2) brightness(.55)`, hold 600ms; sweep height 3px.
6. **Subject slim.** Remove the `memory file` and `self-calls` rows and the `≈N/min` rate
   span; keep the remaining rows and the `#subj-stats` grid at `repeat(5, 20px)`.
7. **Self-mod diff view.** `#selfmod` alternates: when `snap.code.latest_edit` exists and
   `now - latest_edit.epoch < 45`, render the excerpt as a `<pre>`-style mono block
   (13→14px, lines coloured by first char: `+` `--vital`, `-` `--fault`, `@@`
   `--paper-faint`; textContent per line-span, never innerHTML) with eyebrow
   `WHAT IT JUST CHANGED · first seen Ns ago`; otherwise the existing rows. The panel title
   keeps its count either way.
8. **Lanes magnitude.** Replace each lane row's `12/h · 3.4k tok` text with a horizontal
   bar (div, width % of the busiest lane's tokens_hour, min 4% when nonzero, `--world`
   fill, `--rule` track) plus a compact `laneCount(tokens_hour)` label. GIVEN/BUILT count
   line and foot disclosure unchanged.
9. **Masthead.** Chips drop their `em` captions. Provenance rotation: array is
   PROVENANCE_LINES plus, when `snap.commentary.colour.generated` and fresh (<120s), the
   colour line suffixed `— the stage`; beat-kind → preferred-line map
   (`reached_out`→0, `self_edit`→2, `published`/`spoke`→1) consulted on each rotation;
   swaps crossfade via opacity 250ms with a timeout swap (REDUCED: instant swap, rotation
   continues — delete the `if (!REDUCED)` gate on the interval).
10. **THE EYE.** `#eye` card absolutely positioned top-right inside `#monologue`
    (~300×187 + caption), shown only when `snap.sense` is non-null: `<img>` src from
    `snap.sense.url` (same-origin), re-set only when the url changes; caption
    `THE EYE · slot {slot} · {rel(captured_epoch)}`; hidden entirely when `snap.sense`
    is null. The img gets `alt=""` (decorative to a reader; the caption carries the fact).
11. **Desk segment.** When `snap.desk` is non-null and has ≥1 verdict, `#dead` alternates
    every 90s: graves view (existing) for 90−duration, desk view for
    `desk.duration_seconds`: title swaps to `THE DESK`, rows of
    `#{ordinal} ★★★☆☆ {line}` with the evidence row beneath in `--paper-faint` mono and a
    depth tag when not `full`; byline `— the stage's read, not a measurement`. Cross-fade
    250ms; REDUCED: hard swap. Record book: `#dead-foot` rotates every 20s among the
    existing chose-line, `longest life: incarnation {n} · {dur}`, and the earlier-lives
    disclosure when present.
12. **Craft.** `--paper-faint` → `#97a2ab`; verify ≥6:1 against `#12171b` with a quick
    Python WCAG computation in the test; focus outline `2px solid var(--vital)`;
    `renderSpoken()` moves before `renderRibbon()` in `render()`; `maybeCut` also flashes
    the newest `#selfmod-rows` row (`.rowflash` 900ms); all rings uniform (drop
    `.filled`); `aria-live="polite"` on `#reached-said`; `title` attributes on
    `#subj-model`, `.l-name`, `.rverb`, `.rarg`, `.s-text` where the full value is longer
    than shown.

Existing tests in `test_stage_pages_js.py` that exercise removed surfaces (`#inflight`)
must be updated to the new `#pulse` behavior, not deleted wholesale.

### Task V: Verify and deploy (coordinator-owned)

- [ ] `.venv/bin/ruff format . && .venv/bin/ruff check .`
- [ ] `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` — full suite green.
- [ ] Adversarial review of the full diff (multi-lens), fix confirmed findings.
- [ ] `docker compose build stage && docker compose up -d --no-deps stage` — the agent,
  recorder, diode, sense, and viewer containers are NOT touched.
- [ ] Screenshot the live page; verify: pulse strip moving, typewriter on a fresh turn,
  desk/eye presence gated correctly, no console errors.
- [ ] Commit; update aurora-4b713bfe9f; close when verified.
