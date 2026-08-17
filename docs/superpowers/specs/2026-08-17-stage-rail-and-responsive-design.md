# Stage rail rebuild and responsive layout

Date: 2026-08-17. Status: implemented on branch `stage-rail-rebuild` (records `lives`, the
LINEAGE and NOW panels, the three layout tiers, the container check fixes, and the colour-line
call context below); approved 2026-08-17 (John: "resolve this issue … you're authorised to fully
adjust the layout and reach down into the containers").
Companion plan: `docs/superpowers/plans/2026-08-17-stage-rail-and-responsive.md`.
Supersedes the THE SUBJECT / THE STORY SO FAR / THE DEAD rulings in
`2026-08-16-stream-page-first-principles.md`. Everything else in that spec stands.

## Why the rail is being reopened

A design review of the live page (2026-08-17) found that the right-hand rail has never
told a viewer anything, and that the cause is structural rather than typographic: **its
content does not vary on the timescale anyone watches it.**

- The recap and pull-quote are generated once per life (`summary.py`: regenerated on a
  digest change or every 300 s).
- Every grave reads *ENDED ON ITS OWN NOTE*; the foot reads *14 of 14 chose to die*; every
  desk verdict opens *"The record is thin, but it shows a [brief | short | ten-turn]
  life…"* at two stars, because the desk's inputs are all `partial`/`tombstone_only` and
  the summariser has nothing to discriminate on.
- The subject's figures (alive 8m 47s · 9 turns · +208/−11) come with no scale; the rail
  holds the comparison set (every previous life) but renders it as prose.
- What is shown is the least informative prefix of longer text (147 of 1,608 characters
  of a note, the note's boilerplate opening), behind four `READ THE REST` affordances
  the primary audience — an OBS browser source — cannot click (the 2026-08-16 spec's own
  first governing finding).
- The same fact appears three times on one screen: incarnation 14's note (pull-quote,
  grave, desk row); "quiet 4m32s" (masthead, subject strip, play evidence); the
  play-by-play tool name 40 px from the monologue's own `CALL_MODEL` line. The `CA` play
  tag is `_tool_tag()`'s two-letter fallback for any agent-built tool name and has no
  legend.

The same review found the page has no rendering below 1920 px wide at all: `html, body`
are a fixed 1920×1080 canvas with `overflow: hidden` and no media queries, so a phone
shows the left 390 px of the monologue, cut mid-word, and no rail. The operator's brief
is now explicit: the page must be optimised for **1080p and mobile**; a richer telemetry
panel for at-home viewers comes later, separately.

## Governing rule

**The rail may hold only things that change while a viewer is looking, or that give
scale to the thing that is changing.** Reference prose — recaps, whole notes, verdicts —
is not rail material. It stays in the `/api/stream` snapshot for the future telemetry
panel and leaves the broadcast page.

Binding rules carried forward unchanged: the stream page is not an agent-readable
surface; facts on screen stay literally true; every generated line is bylined as the
stage's; every stage-side read of an agent-writable root goes through
`data.contained_file`; no type on the broadcast surface below 13 px; the typewriter,
pulse strip, feed pin, death beat, THE EYE and the ribbon are unchanged.

## The rail: two panels, not three

### THE LINEAGE (replaces THE SUBJECT, THE DEAD and the desk view)

One panel, top of the rail, height 536 px at 1080p. From top to bottom:

1. **Title bar**: `THE LINEAGE` · `N LIVES SO FAR` (N = current incarnation ordinal).
2. **Life chart** (220 px): one vertical bar per incarnation, oldest on the left, the
   current life on the right. Bar height is linear in lifetime; the y-scale's maximum
   is the longer of the longest recorded life and the current life's age, so the
   current bar can be the tallest and is never clipped. Bar colour is the ending kind
   using the grave palette already on the page — `declared` → `--chosen`, `harness` →
   `--taken`, `unknown` → `--broken` — and the current life is `--vital` with a
   breathing cap. A life with no datable span (the first life; a life whose tombstone
   epoch is unsane) renders as a 2 px stub so its slot is still counted. Bars are
   evenly spaced across the panel width; when more than 40 lives exist, only the newest
   40 are drawn and the foot line discloses `N earlier lives not shown`. An ordinal
   label row sits under the bars: labels are stepped by the width a column actually
   has — every label at 22 px per column or more (two mono digits fit), every fifth
   down to 12 px, every tenth below, always the newest — and a label overflows its
   empty neighbours rather than clipping (amended at the container check: the fixed
   "every fifth beyond 24" rule collided at phone widths). The chart is `aria-hidden`; the facts it draws are stated in text in the
   current-life block, the spotlight, and the foot.
3. **Current-life block**: `INCARNATION` eyebrow, the ordinal at 34 px (keeps the `bump`
   on change) with the model beside it; one 15 px mono line of figures —
   `alive 8m 47s · 9 turns · 0 self-edits · 0 reached out`; the existing `source`
   row (`+208 / −11 lines from seed`, or the unmodified / mirror-unavailable text).
   The self-edit `cut` flash and the `nosig` dimming that lived on `#subject` move to
   this panel.
4. **Spotlight** (three lines): eyebrow `INCARNATION 14 · ENDED ON ITS OWN NOTE · 8m
   ago`, facts `lived 7m 21s · 12+ turns`, and a two-line clamp of the note's first
   sentence — the existing grave content, one at a time. The spotlight walks across the
   lineage entries the snapshot already carries (newest five), advancing every 10 s;
   the bar it names carries a `.lit` outline. No click affordance, no `READ THE REST`:
   the walk *is* the pacing. Under reduced motion the walk continues (content) without
   the fade (decoration). When nothing has died, the spotlight reads *No one has died
   here yet.* On a death beat the spotlight jumps to the newly dead life and takes the
   existing `slide` entrance.
5. **Foot** (16 px): the existing record-book rotation (`by their own notes, 14 of 14
   chose to die` / `longest life: incarnation 12 · 15m` / `N earlier lives not shown`)
   on the existing 20 s cadence. Amended 2026-08-17 by
   `2026-08-17-life-census-and-notable-moments-design.md`: achievement nominations join
   the rotation, newest first, at most three, bylined `— the stage`, only when one exists.

The state strip (`● quiet · 4m 32s`) is removed from the rail: the masthead's state
cluster is the one home for that fact.

### NOW (replaces THE STORY SO FAR)

One panel, height 216 px at 1080p:

1. Title bar: `NOW`.
2. **The colour line** — the generated one-sentence read of the current beat — at
   22 px/30 px, three-line clamp (amended from two at implementation: the 216 px panel has
   the room and a 140-character sentence at 22 px needs it), `aria-live="polite"`. This is the one generated element
   on the page that changes turn to turn; it is promoted from third position in a panel
   to the panel's subject.
3. **Evidence line**, 13 px mono: the beat's counted fact when it has one
   (`run_shell ×3 in a row`), else the play phrase (`running call_model`), followed by
   `· 16s` age. The play tag is deleted.
4. No byline (operator ruling 2026-08-17: the box shows the line and its evidence only;
   the masthead rotation still suffixes the colour line with "— the stage" when it carries
   it, so the generated register stays attributed where it travels).

**Colour-line call context (added 2026-08-17, operator request during the container check —
"can we give the summariser the contents of the command as well so it can explain what it's
actually doing").** Every beat now carries `call` (the newest relevant tool name: the beat's
own tool when it names one, else the newest call of the newest turn) and `args` (that call's
arguments, passed through the same `_field` flattening and fence-stripping as every other
agent-controlled value, capped at `ARGS_CHARS = 200`). The prompt renders them as `call:` /
`args:` lines; the cache digest ignores them, so a change of arguments never drops the
displayed line to the template — the line simply picks the fresh arguments up when it next
regenerates (the existing 60 s floor). The system prompt tells the model it may use them to say
concretely what the agent is doing, quoting at most a few words.

Deleted from the page: `#play-tag`, `#recap-box`, `#pull-box`, `#byline`, `#graves`,
`#desk`, `#subj-strip`, `.more`/`READ THE REST` on every rail block, `fitRecap`,
`dropLede`, `fallbackRecap`, `renderStory`, `renderDead`, `renderDesk`, `deskCycle`.
`snap.story` and `snap.desk` stay in the API and are not rendered.

## Layout tiers

The page detects its tier from viewport width. Two CSS media ranges plus one JS scale.

| Tier | Width | Behaviour |
|---|---|---|
| **canvas** | ≥ 1920 | The existing 1920×1080 grid, pixel for pixel. This is what OBS loads. |
| **scaled** | 1200–1919 | The same canvas, `transform: scale(w/1920)` from the top-left, body height set to the scaled height. The broadcast composition is preserved; a laptop viewer sees the whole stage. |
| **flow** | < 1200 | Single-column, vertically scrolling, fluid. Media queries only; no fixed pixel canvas. |

The scale is applied by one `resize` handler that sets `--stage-scale` on `#stage`; at
≥ 1920 and < 1200 it sets nothing. `overflow: hidden` on `html, body` applies only in
the canvas tier.

### Flow tier (< 1200)

- `#stage` becomes a flex column with `gap: 14px; padding: 12px`; `#rail` is
  `display: contents` so its two panels take their own places in the column via
  `order`: masthead → **THE LINEAGE (compact)** → THE MONOLOGUE → **NOW** → ribbon.
- **Masthead** wraps: wordmark and state cluster on one row; the containment line
  under it; the legend chips and repo link on a third row; `#provenance` hidden under
  720 px.
- **THE LINEAGE, compact**: chart 84 px tall (amended from 56 at the container check —
  the bars need the height to read at all beside their labels), current-life figures on
  one wrapping line, the spotlight's eyebrow wrapping and its facts line kept (amended:
  the facts are one short line and the panel has the room), plus its two-line note; the
  whole panel roughly 350 px on a 390 px phone.
- **THE MONOLOGUE**: `height: 70dvh; min-height: 420px`, its own scroll region as
  today, so a phone viewer's thumb scrolls the feed, not the page. `.turn` collapses to
  one column: the gutter becomes a single left-aligned line above the text (`TURN 101 ·
  19:26:49Z · +310s`) and the marks inline. Type: think 17/26, say 16/24, tool 13/19;
  `.clamp` line clamps unchanged. THE EYE is hidden (it would float over the text).
  `#return-live` grows to a 44 px target.
- **NOW**: as at 1080p, `min-height` instead of a fixed height.
- **Ribbon**: one column under 720 px, three columns from 720 px, panels `min-height:
  136px` instead of a fixed height; content unchanged.
- Nothing is removed from the phone that the 1080p page shows except THE EYE.

## Data

`records.record_book()` gains a `lives` list — every tombstone, oldest first, memoized
with the rest of the book on the tombstone set: `{ordinal, kind, seconds | null,
ended_epoch | null}`. `seconds` is the gap from the previous death (the same derivation
`_derive_lives` uses); the first life has none. `server._public_records` passes it
through capped at `LIVES_CAP = 200` newest entries, with `lives_omitted` for the rest.
(`lives_omitted` is telemetry for API consumers; the page derives its own "N earlier lives
not shown" from `stats.lives_ended` minus the bars it drew, which covers both the 200-entry
cap and the 40-bar cap in one number.)
Everything else the panels need — `stats.started_epoch`, `stats.incarnation`,
`stats.model`, `stats.turns_this_life`, `code`, `lineage[0..4]` (sentence, facts,
kind, ended_epoch), `records.longest_life`, `records.chose`, `commentary.colour`,
`commentary.play` — is already in the snapshot. No new file is read; no agent-readable
surface changes.

## Testing

- `tests/test_stage_records.py`: `lives` shape, ordering, first-life null span,
  memoisation, cap and `lives_omitted`.
- `tests/test_stage_pages.py`: the rail is two panels of the stated heights summing to
  772 with one 20 px gap; the removed ids are absent; `#now-colour` carries
  `aria-live`; the play tag is gone; the flow-tier media query exists and lists the
  order; the font-floor test still passes over the whole stylesheet including media
  blocks; the state strip is gone from the rail; the chart is `aria-hidden`.
- `tests/test_stage_pages_js.py` (node): the lineage block scales bars against the
  right maximum, colours by kind, stubs undated lives, caps at 40 with disclosure,
  walks the spotlight on the 10 s cadence and jumps on death; the NOW block prefers
  evidence over phrase and lights the fresh dot only for a generated line; the tier
  handler sets scale in [1200, 1920) only.
- Container check: `docker compose build stage && docker compose up -d --no-deps
  stage`; screenshots at 1920×1080, 1440×900, 390×844; no horizontal page scroll at
  any width; the chart bar for the current life visibly grows between two polls.

## Explicitly not doing

- No new mount, route or file read for the stage.
- No inbound viewer channel; no llm.sock mount (unchanged rulings).
- The desk and recap keep generating into the API for the future telemetry panel; only
  their rendering leaves this page. Reconsidering their cost is a separate decision.
- No fluid re-composition of the 1080p canvas between 1200 and 1919 px: the scaled tier
  is a deliberate choice to keep one broadcast composition testable.
