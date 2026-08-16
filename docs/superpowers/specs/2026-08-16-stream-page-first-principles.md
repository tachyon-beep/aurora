# Stream page first principles: pacing over clicking

Date: 2026-08-16. Status: approved for implementation (John, 2026-08-16).
Companion plan: `docs/superpowers/plans/2026-08-16-stream-page-rebuild.md`.
Tracker: aurora-4b713bfe9f.

Two SME reviews (first-principles needs derivation; multi-competency craft critique) plus live
observation of the running stack produced this synthesis. The operator's brief: question every
panel, consider scrolling text over static blocks, make the page more fun, nothing off the table.

## The two governing findings

**1. The primary consumer has no cursor.** The page's stated delivery is an OBS browser source.
Every click-to-expand affordance is dead weight for the broadcast audience; only the rare
tunnelled visitor can click. The page already knew this for one surface (the `#sound-on`
autoplay comment) and never applied it elsewhere. Consequence: *depth must arrive through
pacing, not interaction.* Click-to-expand stays as a free enhancement; it stops being the plan.

**2. The page has two tempos and is built for one.** Young incarnations reason slowly (30–120s
gaps, long reasoning); mature ones running self-built tool loops emit turns every 3–15s, some
with no reasoning or speech at all (observed live: incarnation 19, `exec_python` loops).
At the slow end the page shows dead air and narrates the wait ("waiting for row N"); at the
fast end blocks pop in animated and are evicted with no exit, reading as glitch. Every pacing
mechanism below adapts to the measured cadence rather than assuming either regime.

## Rulings by surface

Binding rules carried forward unchanged: the stream page is not an agent-readable surface
(drama is wanted); facts on screen stay literally true; tool calls never expand; depth over
breadth; contestability over calibration ("arbitrariness is the feature"); every stage-side
read of an agent-writable root goes through `data.contained_file`.

### Masthead — keep, rework

- Legend chips: **compress** to dot + word (drop the explainer captions). A broadcast has
  first-time viewers arriving continuously, so the legend must stay — but localStorage expiry
  (proposed in review) is wrong for OBS, where the page loads once for thousands of viewers.
- Containment-fact rotation: **becomes beat-responsive.** After a `reached_out` beat the next
  rotation shows the no-network line; after `self_edit`, the rewrite line. A fresh generated
  colour line (bylined) joins the rotation as one slot. Line swaps crossfade. Reduced-motion
  keeps the rotation (it is content, not decoration) and drops only the transition — the
  current code wrongly freezes rotation entirely under reduced motion.
- Premise sentence, state cluster, repo line, death sweep: keep.

### THE MONOLOGUE — keep, new delivery

- **Typewriter reveal (the headline change).** When a turn lands, its reasoning and speech
  reveal progressively, teleprompter-style: text appends word by word into a fixed-height
  window that stays scrolled to its tail, so the newest thought is always in motion and ends
  with its conclusion visible. Reveal duration adapts to the median of recent inter-turn gaps
  (clamped 3–30s); a new arrival fast-forwards the previous reveal; turns older than 90s render
  whole (a reload never fakes liveness); reduced-motion renders instantly. Revealed turns keep
  tail-view until evicted. Gutter timestamps and the state cluster always report real times —
  the reveal never misrepresents when a turn happened.
- **`#inflight` dies.** "Waiting for row N" narrated absence. Its slot becomes the metabolism
  strip (below), which also fixes the critique finding that the only liveness cue hides
  exactly in the 90s+ dead-air tail.
- **Feed-pin fix (critical).** Programmatic scrolls (`scrollIntoView` on expand) no longer
  unpin; an unpinned feed shows a RETURN TO LIVE chip; auto-repin after 3 minutes with no user
  scroll and no block expanded. The monologue must never silently freeze for the life of an
  unattended browser source.
- **Moment hierarchy rebalanced.** The self-ending turn (`is-end`) gets the loudest treatment
  (tinted wash + inset bar in the chosen colour), above `is-edit` — the current page renders
  self-editing louder than self-termination, inverting the premise. Speech blocks get an
  entrance of their own. Evicted turns exit with a brief fade instead of vanishing.
- Cold-start overlay: keep as is.

### Metabolism strip — new, replaces `#inflight`

A one-line strip at the monologue's foot, always present while anything is alive: real
in-flight request (lane + elapsed), a 10-minute token sparkline, and a tokens-per-minute
figure. Every number is real, derived from the recorder's own events (`events.jsonl`) via the
existing memoized event fold — no new file scanning (aurora-b8ba932540 stays another worker's
fix, untouched). This is the one element that can move continuously and truthfully between
turns, in both tempos.

### THE SUBJECT — slim

`memory file` and `self-calls` rows die (ops telemetry with no audience; the console serves
them). The `≈N/min` rate dies (the metabolism strip carries tempo now). Keeps: incarnation
ordinal, model, alive, turns, self-edits, reached out, source ±N. More whitespace is fine at
TV distance.

### THE STORY SO FAR — keep

Play-by-play, colour line, recap, pull-quote, byline all stay. The colour line additionally
takes a masthead rotation slot when fresh (promotion for drop-in viewers without removing it
here).

### THE DEAD — keep, plus the analysis desk

- Implements the approved 2026-08-14 commentary spec Part 3, previously unbuilt: one-to-five
  star verdicts on the last five dead incarnations, generated by the stage's summariser model,
  each with a one-line argument and a factual evidence row (`61 lines · 4 tools · 9 reaches ·
  ended by its own hand`), depth-marked (`full`/`partial`/`tombstone_only`). The panel
  cross-fades between graves and the desk on a timer; no key, no segment. The verdict is an
  opinion and is bylined as one — the split between loud judgment and factual evidence *is*
  the design.
- **Death beat becomes reload-safe**: fired off `lineage[0].ended_epoch` recency plus a
  localStorage marker, not solely a previous-poll comparison an OBS refresh can drop.
  Amplitude raised (deeper mourning filter, thicker sweep).
- **Record book foot**: the foot line rotates cross-life records (longest life, most turns
  where derivable, N of M chose to die) computed over every tombstone, giving returning
  viewers something to track.

### Ribbon

- WHAT IT DID TO ITSELF: keep rows; after a fresh edit the panel shows a capped excerpt of the
  actual diff for ~45s before returning to rows. The show's premise is a model rewriting its
  own file; the page finally shows the file changing. The diff is computed stage-side by
  remembering the previous poll's mirrored `agent.py` in memory — no new mounts, and it names
  what *this* edit changed rather than the cumulative delta from seed.
- WHAT IT THINKS WITH: reframe from rate table to magnitude — per-lane activity bars replace
  the `12/h · 3.4k tok` digit strings a casual viewer cannot read. GIVEN/BUILT framing stays.
- WHAT IT REACHED FOR: keep everything; `aria-live="polite"` on the said block; the
  unexplained filled-ring distinction goes; ellipsized rows gain `title` attributes.

### THE EYE — new, conditional

A picture-in-picture card floating in the monologue's empty right margin showing the newest
frame on the sense ring, captioned with slot and age, shown only while frames are fresh. The
caption claims only what is true: this is the ring its eye reads, not "what it looked at"
(the stage cannot know which frame a vision call carried). Requires mounting
`./volumes/sense` read-only into the stage.

**Containment note.** The sense volume is written only by the sense service, which holds no
credential; its contents are already agent-readable surfaces under invariant 2. Mounting it
`:ro` into the stage opens no write path and no new channel to any credentialed service; the
new `/frame/` route serves only names matched against a directory listing (traversal
impossible by construction), resolved through `contained_file`, size-capped. What is new is
*public exposure* of the captured frames on the stream port — deliberate, operator-approved.

**CLAUDE.md follow-up (proposal, not yet applied):** invariant 3's telemetry sentence
("never rendered on the stream page") predates the graves and source-delta features and now
also predates the diff excerpt; it should read "never rendered wholesale — only curated,
capped extracts (tombstone notes, source deltas, per-edit diff excerpts), each read through
`contained_file`". To be approved by the operator, not silently edited.

### Craft fixes (from the critique)

`--paper-faint` lifted off the AA floor (target ≥ 6:1 on panel ink) for the 13px chrome it
carries everywhere; focus outline to 2px `--vital` (currently 1.73:1, fails non-text
contrast); `renderSpoken` ordered before the ribbon's sparse measurement; the self-edit flash
on THE SUBJECT also flashes the ribbon row that names the edit; provenance crossfade;
TURN/ROW vocabulary drift dies with `#inflight`.

## Explicitly not doing

- No inbound viewer channel (superseded by prior ruling; not relitigated).
- No live token stream from the recorder (a recorder change; simulated reveal of landed text
  achieves the effect page-side).
- No localStorage expiry of the legend; no type-size reductions to fit more content.
- No llm.sock mount into the stage (its sockets are credentialed channels; the modality of a
  lane therefore stays unknown to the stage, and THE EYE's caption is worded accordingly).
