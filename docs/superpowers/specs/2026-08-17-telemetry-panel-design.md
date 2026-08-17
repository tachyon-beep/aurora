# The telemetry panel

Date: 2026-08-17. Status: approved for build after ux-critic review (verdict "build with
changes", 2026-08-17; every change adopted below). Operator brief (John, 2026-08-17): "we'll also
offer a more elaborate telemetry panel for people browsing along at home"; "please take those
onboard and resolve them". Companion to `2026-08-17-stage-rail-and-responsive-design.md` (which
moved reference prose out of the broadcast rail for this page) and
`2026-08-17-life-census-and-notable-moments-design.md` (which produces the per-life digest this
page is the home of).

## Purpose

The broadcast page (`/`) is a stage: one composition, paced for a viewer who is *watching* and
cannot click. Its rule — only things that change while a viewer looks, or give scale to what is
changing — pushed the recap, the desk's verdicts, whole tombstone notes, the stream lanes and
the diode's history out of the frame. They still exist: `/api/stream` generates them every poll,
and the digest adds a per-life reading of the whole transcript. The telemetry panel is the page
for a viewer who is *browsing along at home*: a second tab beside the stream, or a phone, with a
thumb and time to read. It is a document, not a stage.

Audience and job: someone who has watched a while and has a question the stage cannot answer —
*how does this life compare with the others? what did incarnation 12 actually do? what has been
built across all of them? what is it talking to?* — and wants to read, order and expand rather
than wait for a rotation.

## Governing rules

- **Same truth, same discipline.** Every number is measured; every generated sentence is bylined
  as the stage's, with the model named. The record/reading split holds *structurally*, not just
  spatially: the table has a `MEASURED` header group and a `THE STAGE'S READING` header group with
  a visible seam; every card has a `MEASURED` eyebrow and a `THE STAGE'S READING · <model>`
  eyebrow.
- **Not a scoreboard.** The page never ranks unasked: the default order is chronological, newest
  first; ordering is a viewer's choice through one labelled control; rows are never numbered by
  position (`#` is always the incarnation ordinal); no aggregate score across lives exists;
  moments render in turn order, not by stars; stars appear once per life at scannable size (the
  table cell) and as a mono figure (`4/5`) elsewhere; the words *leaderboard*, *achievement*,
  *score*, *rank* and *winner* appear in no copy, id, class or comment. The digest's nomination is
  called a **noted first**.
- **Read-only, public, loopback-bound like the stream.** Served on the stream port (8091, the
  one the tunnel exposes) as `GET /telemetry` and `GET /api/lineage`. No mutating endpoint, no
  query that names a path, no file browsing, no token, no link to the console. The console (8092)
  stays what it is.
- **No new data class crosses to the public.** The panel renders only what `/api/stream` already
  publishes plus the census figures and the digest — generated prose *about* the transcript and
  counts *of* it. Raw transcript text is not published (the console's job). Tombstone notes appear
  as today's `sentence` (first sentence, 320 chars) — no whole notes.
- **Rendering.** Every value goes through `textContent`; no `innerHTML` with data; the stream
  port's CSP and security headers apply unchanged. Escaped text everywhere.
- **Nothing agent-readable changes.** The panel is not an agent-facing surface. The stage still
  holds no recorder credential and makes no request it did not already make.

## Routes and data

### `GET /telemetry`
`stage/telemetry_page.py:TELEMETRY_PAGE_HTML`, one string like the stream page (CSS + markup +
one script), served by `StreamHandler` with the stream's headers. Polls `/api/stream` every 5 s
(strip, source, diode, streams, story) and `/api/lineage` every 30 s (the record). A `pause
updates` toggle in the strip stops both polls (WCAG 2.2.2); the page renders whatever it last
had.

### `GET /api/lineage` (built; `server.lineage_snapshot`)

```
{
  "now": <epoch>, "incarnation": <int>,
  "digests_enabled": <bool>,                     # the stage has a key; verdicts and digests may exist
  "desk_model": <str>, "digest_model": <str>,     # "" when disabled
  "lives": [ ...newest first, the current life first, at most LIVES_CAP + 1 ... ],
  "lives_omitted": <int>
}
```

Each life (`server._public_life`), every field enumerated and capped:

```
{ "ordinal", "current", "began_epoch", "ended_epoch", "lived_seconds",
  "kind": "declared"|"harness"|"unknown"|null,
  "turns", "subcalls", "errors", "edits",          # census over the live transcript; null before it runs
  "note": first sentence ≤ 320 ("" for the current life),
  "verdict": {"stars", "line" ≤160, "evidence" ≤120, "depth"} | null,
  "moments": [{"turn", "stars", "line" ≤140}] ≤ 6,
  "achievement": str ≤100 | null,                # the API keeps the digest's field name; the page says "noted first"
  "digest": {"state": "ready"|"pending"|"skipped"|"off", "turns_shown", "turns_total", "generated_at"} }
```

Sources: `census.cached_lives()`, `desk.cached_verdict(ordinal)` (desk cap raised to 12 to match
`moments.MAX_LIVES`), `moments.cached_digest` / `moments.settled`. Before the census has run the
list is built from the tombstone mirror (`records.record_book().lives` + `data.lineage`) with
null counts, so the page is never empty.

## The page

One scrolling document, `<main>` at `max-width: 1180px`, centred; the stream page's tokens
(`--ink-*`, `--paper*`, `--vital`, `--chosen/--taken/--broken`, `--serif/--sans/--mono`); dark;
body 16 px `--paper`; table body 14 px mono minimum; no type under 13 px anywhere. `<title>aurora
— telemetry</title>`; a skip link to `#record`; `scroll-padding-top: 64px` for the sticky strip.
Sections, in this order (the critic's ruling: record before reading, operator-shaped last):

1. **Strip** (sticky, 56 px; the page's `<header>` with its `<h1>`): `AURORA · TELEMETRY` ·
   `← the stream` (a 44 px-tall link) · state cluster (dot, word, clock — the stream page's
   `stateOf` thresholds) · `INCARNATION N · <model> · alive 8m` (hidden under 720 px; the LIVE
   row carries it) · `pause updates` toggle button (`aria-pressed`). Unreachable stage: the strip
   reads `STAGE OFFLINE — this page cannot reach the stage` and *brightens* to `--fault`, never
   dims.
2. **THE RECORD** (`#record`) — every life, newest first, from `/api/lineage`.
   - **Order control**: one labelled `<select id="order">` — *newest first* (default) · *longest
     lived* · *most turns* · *most self-edits* · *highest verdict* — at every width. On change a
     visually-hidden `role="status"` region announces `ordered by longest lived`. Order is not
     persisted. Ties break by ordinal descending; lives with a null figure sort last.
   - **Filter**: one checkbox `only lives with a noted first`, off by default.
   - **Table** (`≥ 720 px`): `<colgroup>` and a two-row `<thead>`; the first header row spans
     `MEASURED` over `# · ENDED · LIVED · TURNS · EDITS · ERRORS · ENDING` and `THE STAGE'S
     READING` over `VERDICT · NOTED`, with a 1 px `--rule-2` seam between the groups drawn down
     the body. `#` is the ordinal; `ENDED` a relative age with `title` = ISO time; `ENDING`
     colour-coded *and* worded (declared / harness / unknown); `VERDICT` five glyphs plus the
     figure `4/5` in mono (`—` when none, `pending` while the digest waits); `NOTED` a `●` with
     `aria-label="noted first"` when the life has one, else empty. The current life is the first
     row, `LIVE`, with `alive 8m` in `LIVED`, its counts, and `—` in the reading group.
     Row 1 of the body is the current life; each dead life is two `<tr>`s: the data row, whose
     `#` cell holds the **one** disclosure `<button aria-expanded aria-controls="life-12-card">`
     named `Expand incarnation 12`, and the card row (`<tr id="life-12-card" hidden>`), always
     present, toggled by `hidden`. No whole-row click.
   - **Cards** (`< 720 px`): the table is not rendered; each life is a stacked card: `#12 · 7m
     21s · 12 turns · declared · 4/5 ●` on one line, the same disclosure button (44 px), the same
     card body under it.
   - **Card body**, two blocks with eyebrows: `MEASURED` — `lived · turns · self-edits · sub-calls
     · errors · ended by …`, `began → ended` (ISO, mono), the note's first sentence in serif;
     `THE STAGE'S READING · <model>` — the verdict's argued line in serif with its evidence row
     beneath in mono and `4/5` as a mono figure; **notable moments** in **turn order** (`turn 214
     · 4/5 · line`); the noted first as `NOTED FIRST · <line>`; and the provenance line,
     unconditional: `read 60 of 214 turns · 3m after death — the stage` / `reading pending — the
     stage` / `no reading of this life — the stage` (skipped or off; the page never mentions a
     key). Under 720 px the blocks stack, MEASURED first.
   - **Volume**: newest 25 lives rendered; a `show all N lives` button reveals the rest;
     `lives_omitted` > 0 → foot line `N earlier lives are not listed`.
   - **Reconciliation**: polls never rebuild the table. Rows are keyed by ordinal and updated in
     place (`textContent` only where changed); a new life is inserted at the top; sort, filter,
     open cards and focus survive every poll.
   - Empty state: no dead lives → the LIVE row and *No one has died here yet.*
3. **THE STORY SO FAR** — `story.text` in serif at reading measure (`max-width: 68ch`), byline
   `— the stage · <model> · Nm ago`. Hidden when `story` is null.
4. **SOURCE** — `+A / −R lines from seed` (or the unmodified / mirror-unavailable text); the
   latest edit (`added/removed`, `restored`, its excerpt in a `<pre tabindex="0"
   aria-label="latest edit excerpt">` at 13 px mono, horizontal scroll inside); the
   self-modification events (`snap.events`) as a list.
5. **THE DIODE** — three lists from the snapshot: recent outputs (verb, argument, result, age),
   published items (title, age), spoken utterances (text, age, `<audio controls preload="none"
   aria-labelledby=<utterance id>>` for `/audio/<name>`). Totals in a foot line (`N operations · N
   this life`), not the title.
6. **STREAMS** — a table of `lanes` (name, `bound` dot + word, requests/hour, tokens/hour,
   errors/hour, in-flight since, last seen), core first, in a `tabindex="0"` scroll container
   with an accessible name; `lanes_omitted` foot; the pulse as a 20-bar sparkline with the
   window figures beside it as text.
7. **Foot** — the four containment lines (the stream page's `PROVENANCE_LINES`) and *This page
   is read-only. Nothing here can reach the agent.*

### Responsive
Fluid from 360 px. Under 720 px: the strip carries wordmark, back-link, state dot/word and the
pause toggle only; the record is cards; the streams table scrolls inside its container; targets
≥ 44 px. Every width: targets ≥ 24 px, no page-level horizontal scroll.

### Motion and a11y
`prefers-reduced-motion` disables the pulse dot animation and the card's open transition. Focus
rings visible on every control. Tables have a visually-hidden `<caption>` and `<th scope>`.
There is **no `aria-live` prose region**; the only live regions are the `role="status"` order
announcement and the strip's offline notice. Sorting, filtering, expanding and pausing are
keyboard operable.

## Not doing
- No NOW section (the stream's job; and an `aria-live` region on a reading page interrupts).
- No standalone list of noted firsts (a laurel list is the scoreboard shape); the `NOTED` column,
  the filter and the card line are its home.
- No raw transcript, no tool arguments, no reasoning text; no ranking by default; no writes, no
  cookies, no localStorage, no query parameters; no link to the console; no new environment
  variable, mount or credential.
- The stream masthead gains one link, `telemetry →`, beside `#repo` (a phone/laptop viewer can
  click it; OBS ignores it).

## Testing
- `tests/test_stage_server.py`: `/api/lineage` shape, caps, states, fallback (done);
  `/telemetry` served without a token with the stream headers; the stream port still has no
  mutating routes.
- `tests/test_stage_telemetry_page.py`: string greps — `<title>`, `<h1>`, skip link, `<main>`,
  the sections in order (`#record`, `#story`, `#source`, `#diode`, `#streams`), no `#now`,
  `MEASURED` / `THE STAGE'S READING` header groups, `<colgroup>`, `id="order"` select with the
  five options and its status region, the filter checkbox, `aria-expanded` + `aria-controls`,
  `aria-pressed` on the pause toggle, `scroll-padding-top`, `prefers-reduced-motion`, no
  `innerHTML`, no `/api/browse` / `/api/file` / `token` / `console` references, no forbidden
  words (`leaderboard`, `achievement`, `score`, `rank`, `winner`) in the page, no type under
  13 px, `preload="none"` on audio, the byline `— the stage`.
- `tests/test_stage_telemetry_js.py` (node): `orderLives` — chronological default, each order,
  ties by ordinal desc, nulls last, filter; `stateOf` thresholds; card copy for ready / pending /
  skipped / off; `reconcile` keeps an open card and a row's identity across a new snapshot and
  inserts a new life at the top; moments render in turn order.
- Container check: `docker compose build stage && docker compose up -d --no-deps stage`; open
  `/telemetry` at 1440 and 390 wide; order, filter, expand, pause; the masthead link resolves;
  a11y pass with a screenshot review.
