# Stage rail rebuild and responsive layout — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the stream page's three-panel rail with THE LINEAGE (per-life bar chart, live current life, paced spotlight, record foot) and NOW (the colour line promoted), and give the page three layout tiers — 1080p canvas, scaled, and single-column flow for phones.

**Architecture:** The stage page is one Python string, `stage/pages.py:STREAM_PAGE_HTML` (CSS + HTML + one `<script>`), served by `stage/server.py` with data from `/api/stream` (`server._assemble_snapshot`). Two changes: (1) `stage/records.py` gains a `lives` list in the memoized record book and `server._public_records` passes it through capped; (2) `pages.py` loses `#subject/#story/#dead` (markup, CSS, JS) and gains `#lineage` and `#now`, plus a `@media` flow tier and a JS `fitStage()` scale handler. Tests are string greps over `STREAM_PAGE_HTML` plus node-executed JS blocks cut between sentinel comments (see `tests/test_stage_pages_js.py`).

**Tech Stack:** Python 3 stdlib (`http.server`), vanilla JS/CSS in a Python string, pytest, node (v24 at `/home/john/.nvm/versions/node/v24.13.0/bin/node`) for JS block tests, Docker Compose for the container check, Playwright MCP for screenshots.

**Spec:** `docs/superpowers/specs/2026-08-17-stage-rail-and-responsive-design.md` (read it first; it carries the reasoning and the pixel budget). Amendment applied by this plan: the NOW colour line clamps to **three** lines (22px/30px), not two — the 216px panel has the room and a 140-char sentence at 22px sans in 658px needs it.

## Global Constraints

- Run tests with `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`; lint with `.venv/bin/ruff format . && .venv/bin/ruff check .` before every commit.
- **No `font`/`font-size` in the stream page stylesheet below 13px**, including inside `@media` blocks (`test_the_stream_page_stylesheet_declares_no_px_font_size_under_the_floor` scans the whole `<style>`).
- The rail's two rows plus one 20px gap must sum to 772px (`536px 216px`).
- Every generated line stays bylined "the stage, not the subject"; no `innerHTML` for commentary text; every text write goes through `setText`/`textContent`.
- The page keeps exactly one `<script>` block; the sentinel comments listed in Task 2 must exist verbatim because tests cut on them.
- Commit messages are factual and benign (no game/task framing). End each with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Do not touch anything the agent can read (`agent.py`, `Dockerfile` allow-list, garden, `/llm/sock` writers). This work is stage-only.
- Work on `main` directly (the session is configured to work in place); commit after each task.

---

### Task 1: `records.lives` in the record book and the public snapshot

**Files:**
- Modify: `stage/records.py:55-89` (`_compute`)
- Modify: `stage/server.py:544-562` (`_public_records`), add `LIVES_CAP` near the other caps at the top of `server.py`
- Test: `tests/test_stage_records.py`, `tests/test_stage_server.py`

**Interfaces:**
- Produces: `records.record_book(work_dir)["lives"]` — list of `{"ordinal": int, "kind": "declared"|"harness"|"unknown", "seconds": float|None, "ended_epoch": float|None}` oldest first, one per tombstone; `_public_records(book)` returns those keys plus `"lives"` (newest `LIVES_CAP=200`, same shape, values coerced) and `"lives_omitted": int`. The page (Task 2) reads `snap.records.lives` and `snap.records.lives_omitted`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_records.py`:

```python
def test_lives_lists_every_tombstone_oldest_first_with_kind_and_span(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "done() at turn 3. more detail.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "terminated by the harness after a fault.", 400)
    _tombstone(tmp_path, "incarnation-c.txt", "done() reached. closing note.", 100)
    lives = records.record_book(str(tmp_path))["lives"]
    assert [life["ordinal"] for life in lives] == [1, 2, 3]
    assert [life["kind"] for life in lives] == ["declared", "harness", "declared"]
    assert lives[0]["seconds"] is None, "the first life has no recorded birth"
    assert lives[1]["seconds"] == pytest.approx(600, abs=1)
    assert lives[2]["seconds"] == pytest.approx(300, abs=1)
    assert lives[2]["ended_epoch"] == pytest.approx(time.time() - 100, abs=2)


def test_lives_is_empty_for_an_empty_dir(tmp_path):
    assert records.record_book(str(tmp_path))["lives"] == []


def test_an_undatable_death_gives_no_span_on_either_side(tmp_path):
    _tombstone(tmp_path, "incarnation-a.txt", "first note.", 1000)
    _tombstone(tmp_path, "incarnation-b.txt", "second note.", 90 * 86400)
    _tombstone(tmp_path, "incarnation-c.txt", "third note.", 100)
    lives = records.record_book(str(tmp_path))["lives"]
    assert lives[1]["seconds"] is None and lives[1]["ended_epoch"] is None
    assert lives[2]["seconds"] is None
```

Also update `test_empty_dir_yields_zeros_and_nones` to expect `"lives": []` in the dict.

Append to `tests/test_stage_server.py` (it already imports `server`; check the top of the file and follow its import style):

```python
def test_public_records_passes_lives_through_capped_and_coerced():
    lives = [{"ordinal": i, "kind": "declared", "seconds": 10.0 * i, "ended_epoch": 1000.0 + i}
             for i in range(1, server.LIVES_CAP + 4)]
    lives[0]["kind"] = "bogus"
    lives[1]["seconds"] = None
    out = server._public_records({"lives_ended": len(lives), "chose": 1, "lives": lives})
    assert out["lives_omitted"] == 3
    assert len(out["lives"]) == server.LIVES_CAP
    assert out["lives"][0]["ordinal"] == 4, "the newest LIVES_CAP survive"
    assert out["lives"][-1] == {
        "ordinal": server.LIVES_CAP + 3, "kind": "declared",
        "seconds": 10.0 * (server.LIVES_CAP + 3), "ended_epoch": 1000.0 + server.LIVES_CAP + 3,
    }


def test_public_records_normalises_kind_and_absent_lives():
    out = server._public_records({"lives": [{"ordinal": "1", "kind": "weird", "seconds": "x"}]})
    assert out["lives"] == [{"ordinal": 1, "kind": "unknown", "seconds": None, "ended_epoch": None}]
    assert server._public_records({})["lives"] == []
    assert server._public_records({})["lives_omitted"] == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_records.py tests/test_stage_server.py -q -k "lives or public_records"`
Expected: FAIL (`KeyError: 'lives'`, `AttributeError: LIVES_CAP`).

- [ ] **Step 3: Implement**

`stage/records.py` — replace `_compute` with:

```python
def _compute(work_dir, paths, now):
    """The record book over paths, oldest first."""
    deaths = [data._tombstone_epoch(path, now) for path in paths]
    kinds = []
    for path in paths:
        real = data.contained_file(work_dir, path)
        text = ""
        if real is not None:
            try:
                with open(real, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read(TOMBSTONE_READ_BYTES)
            except OSError:
                text = ""
        kinds.append(data._ending_kind(text))
    chose = sum(1 for kind in kinds if kind == "declared")
    lives = []
    longest = None
    for index, ended in enumerate(deaths):
        began = deaths[index - 1] if index > 0 else None
        seconds = None
        if began is not None and ended is not None and ended > began:
            seconds = float(ended - began)
            if longest is None or seconds > longest["seconds"]:
                longest = {"ordinal": index + 1, "seconds": seconds}
        lives.append(
            {"ordinal": index + 1, "kind": kinds[index], "seconds": seconds, "ended_epoch": ended}
        )
    gap = None
    if len(deaths) >= 2:
        last, prior = deaths[-1], deaths[-2]
        if last is not None and prior is not None and last > prior:
            gap = float(last - prior)
    return {
        "lives_ended": len(paths),
        "chose": chose,
        "longest_life": longest,
        "most_recent_gap_seconds": gap,
        "lives": lives,
    }
```

Note the behaviour change from the old loop: an unreadable tombstone now counts as `"unknown"` (previously it was skipped for `chose` but still counted in `lives_ended`); `chose` is unchanged for readable files. `_ending_kind("")` already returns `"unknown"`.

`stage/server.py` — add near the other caps (search for `DESK_LINE_CAP` to find them):

```python
LIVES_CAP = 200
LIFE_KINDS = ("declared", "harness", "unknown")
```

and replace `_public_records`:

```python
def _public_records(book):
    """The record book with every field enumerated for public display."""
    book = book if isinstance(book, dict) else {}
    longest = book.get("longest_life")
    if isinstance(longest, dict) and isinstance(longest.get("seconds"), (int, float)):
        longest = {
            "ordinal": int(longest.get("ordinal") or 0),
            "seconds": float(longest["seconds"]),
        }
    else:
        longest = None
    gap = book.get("most_recent_gap_seconds")
    raw_lives = [life for life in (book.get("lives") or []) if isinstance(life, dict)]
    lives = []
    for life in raw_lives[-LIVES_CAP:]:
        kind = life.get("kind")
        seconds = life.get("seconds")
        ended = life.get("ended_epoch")
        try:
            ordinal = int(life.get("ordinal") or 0)
        except (TypeError, ValueError):
            ordinal = 0
        lives.append(
            {
                "ordinal": ordinal,
                "kind": kind if kind in LIFE_KINDS else "unknown",
                "seconds": float(seconds) if isinstance(seconds, (int, float)) else None,
                "ended_epoch": float(ended) if isinstance(ended, (int, float)) else None,
            }
        )
    return {
        "lives_ended": int(book.get("lives_ended") or 0),
        "chose": int(book.get("chose") or 0),
        "longest_life": longest,
        "most_recent_gap_seconds": float(gap) if isinstance(gap, (int, float)) else None,
        "lives": lives,
        "lives_omitted": max(0, len(raw_lives) - LIVES_CAP),
    }
```

`bool` is a subclass of `int`; that is acceptable here (a `True` seconds would coerce to 1.0) — the producer never emits booleans.

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_stage_records.py tests/test_stage_server.py -q`
Expected: PASS. Then the full suite: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` — expect PASS (an `_empty_records()` shape test may need the two new keys; fix by expectation, not by removing keys).

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/records.py stage/server.py tests/test_stage_records.py tests/test_stage_server.py
git commit -m "Add every life's span and ending kind to the stage record book"
```

---

### Task 2: THE LINEAGE and NOW replace the three rail panels

**Files:**
- Modify: `stage/pages.py` — CSS `/* ---------- rail ---------- */` block (lines 368–500), rail markup (lines 683–737), JS from `/* ---------- subject ---------- */` (line 1470) through the end of the desk block (line 1774), plus `runMourn`, `maybeCut`, `setState`, `setRelativeTimes`, `tick`, `render`, and the `graveNodes` global.
- Modify: `tests/test_stage_pages.py`, `tests/test_stage_pages_js.py`

**Interfaces:**
- Consumes: `snap.records.lives[]` / `snap.records.lives_omitted` (Task 1); everything else already in the snapshot: `snap.stats.{incarnation, model, started_epoch, turns_this_life, turns_this_life_exact}`, `snap.code`, `snap.events`, `snap.lineage[]` (`ordinal, kind, ended_epoch, lifespan_seconds, turns_lived, turns_partial, sentence, summary`), `snap.records.{lives_ended, chose, longest_life}`, `snap.commentary.{colour:{text,generated,evidence}, play:{phrase,epoch}}`, `snap.diode.operations_life`.
- Produces: element ids `#lineage #lineage-count #chart #bars #bar-labels #life-now #life-head #subj-ord #subj-model #life-figs #v-src #spot #spot-eyebrow #spot-facts #spot-note #lineage-foot #now #now-colour #now-evidence #now-by #now-dot`; JS functions `renderLineage() layoutBars(nowMs) renderLifeFigs(nowMs) renderSpot(nowMs) spotEntry(nowMs) lineageFootFor(st, book, shown) rotateLineageFoot(nowMs) renderNow()`; sentinel comments `/* ---------- lineage ---------- */`, `/* ---------- now ---------- */` (Task 3 adds `/* ---------- tiers ---------- */`).

- [ ] **Step 1: Retire the tests that describe the old panels and write the new ones**

In `tests/test_stage_pages.py` **delete** these tests (they assert markup that is leaving): `test_the_now_block_exists_above_the_recap`, `test_the_recap_drops_its_opening_sentence`, the five `test_drop_lede_*`, `test_a_grave_shows_derived_facts_above_the_note`, `test_the_pull_quote_is_attributed_to_the_dead_incarnations_note`, `test_the_recap_clamp_is_refitted_to_the_space_the_wrap_has`, `test_the_subject_counters_are_set_larger_than_the_labels`, `test_the_recap_is_the_region_that_absorbs_a_full_story_panel`, `test_the_byline_and_pull_quote_are_never_the_thing_that_shrinks`, `test_the_byline_is_still_pinned_to_the_panel_floor`, `test_the_subject_panel_slimmed_to_five_rows`. Keep `test_a_window_capped_turn_count_is_rendered_as_a_lower_bound`, `test_the_dead_panel_counts_how_many_chose`, `test_the_dead_foot_reads_as_sourced_from_the_notes`, `test_the_grave_labels_attribute_the_ending_to_the_note`, `test_the_commentary_never_borrows_the_subjects_registers`, `test_the_commentator_is_bylined_as_not_the_subject` — they still hold. Rewrite `test_the_rail_rows_still_fill_the_rail` to:

```python
def test_the_rail_is_two_panels_that_fill_the_rail():
    block = HTML[HTML.index("#rail {") : HTML.index("}", HTML.index("#rail {"))]
    declaration = block.split("grid-template-rows:")[1].split(";")[0]
    rows = [int(n) for n in re.findall(r"(\d+)px", declaration)]
    assert rows == [536, 216], rows
    assert sum(rows) + 20 == 772
```

Add:

```python
def test_the_rail_holds_only_the_lineage_and_now():
    rail = HTML[HTML.index('<aside id="rail">') : HTML.index("</aside>")]
    assert 'id="lineage"' in rail and 'id="now"' in rail
    for gone in ('id="subject"', 'id="story"', 'id="dead"', 'id="graves"', 'id="desk"',
                 'id="recap-box"', 'id="pull-box"', 'id="byline"', 'id="play-tag"',
                 'id="subj-strip"', "READ THE REST", 'class="more"'):
        assert gone not in rail, gone


def test_the_removed_renderers_are_gone_from_the_script():
    for name in ("function renderStory", "function renderDead", "function renderDesk",
                 "function deskCycle", "function fitRecap", "function dropLede",
                 "function fallbackRecap", "function renderSubject", "function makeGrave",
                 "function renderStoryByline", "graveNodes"):
        assert name not in HTML, name


def test_the_lineage_chart_is_decorative_and_its_facts_are_stated_in_text():
    assert '<div id="chart" aria-hidden="true">' in HTML
    assert 'id="life-figs"' in HTML
    assert 'id="spot-eyebrow"' in HTML and 'id="spot-facts"' in HTML and 'id="spot-note"' in HTML
    assert 'id="lineage-foot"' in HTML


def test_the_colour_line_is_the_now_panels_subject_and_is_announced():
    assert '<p id="now-colour" aria-live="polite"></p>' in HTML
    block = HTML[HTML.index("\n#now-colour {") :]
    block = block[: block.index("}")]
    assert "22px/30px" in block, block
    assert "line-clamp: 3" in block, block
    assert 'id="now-evidence"' in HTML
    assert 'id="now-dot"' in HTML


def test_the_state_strip_left_the_rail_for_the_masthead():
    assert 'id="strip-text"' not in HTML
    assert 'id="strip-glyph"' not in HTML
    assert 'id="state-word"' in HTML


def test_the_lineage_reuses_the_grave_palette_for_bar_kinds():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    assert ".bar.k-declared { background: var(--chosen); }" in css
    assert ".bar.k-harness { background: var(--taken); }" in css
    assert ".bar.k-unknown { background: var(--broken); }" in css
    assert ".bar.now { background: var(--vital);" in css


def test_the_spotlight_and_foot_are_driven_by_the_tick_not_by_clicks():
    tick = HTML[HTML.index("function tick()") :]
    tick = tick[: tick.index("\n}")]
    for call in ("renderLifeFigs(nowMs);", "layoutBars(nowMs);", "renderSpot(nowMs);",
                 "rotateLineageFoot(nowMs);", "renderNow();"):
        assert call in tick, call
    assert "deskCycle" not in tick and "fitRecap" not in tick
```

In `tests/test_stage_pages_js.py`: change `_provenance_block`'s second end marker from `"\n}\n\n/* ---------- subject ---------- */"` to `"\n}\n\n/* ---------- lineage ---------- */"`; replace `_desk_block` with

```python
def _lineage_block():
    """THE LINEAGE: bar model, layout, spotlight walk, record-book foot."""
    return _block("/* ---------- lineage ---------- */", "/* ---------- now ---------- */")


def _now_block():
    """renderNow alone."""
    return _block("/* ---------- now ---------- */", "/* ---------- eye ---------- */")
```

Delete `DESK_HARNESS` and `test_the_desk_rotation_stars_and_record_book_foot`; add:

```python
LINEAGE_HARNESS = """
var nodes = {};
function fake(id) {
  if (!nodes[id]) nodes[id] = { id: id, textContent: "", className: "", hidden: false,
    style: {}, children: [], classList: {
      add: function (c) { nodes[id].className += " " + c; },
      remove: function () {}, toggle: function (c, on) { nodes[id]["_" + c] = !!on; },
      contains: function () { return false; } },
    setAttribute: function () {}, title: "" };
  return nodes[id];
}
global.$ = fake;
global.el = function (tag, cls, parent) {
  var n = fake("el" + Math.random());
  n.className = cls || "";
  if (parent) parent.children.push(n);
  return n;
};
global.setText = function (node, value) {
  value = value == null ? "" : String(value);
  if (node.textContent === value) return false;
  node.textContent = value; return true;
};
global.setClass = function (node, name, on) { if (node) node["_" + name] = !!on; };
global.norm = function (t) { return String(t == null ? "" : t).replace(/\\s+/g, " ").trim(); };
global.dur = function (s) { return Math.round(s) + "s"; };
global.rel = function (ep, nowMs) { return Math.floor(nowMs / 1000 - ep) + "s ago"; };
global.reachedThisLife = function () { return 2; };
global.REDUCED = true;
global.lastOrdinal = null;
var NOW = 1000000;
global.clock = function () { return NOW; };
global.snap = null;

__BLOCK__

var out = {};
function lives(n) {
  var l = [];
  for (var i = 1; i <= n; i++) l.push({ ordinal: i, kind: i % 3 === 0 ? "harness" : "declared",
    seconds: i === 1 ? null : 60 * i, ended_epoch: NOW / 1000 - 1000 * (n - i + 1) });
  return l;
}
global.snap = {
  stats: { incarnation: 5, model: "m", started_epoch: NOW / 1000 - 120, turns_this_life: 4,
    turns_this_life_exact: true, lives_ended: 4, ended_by_choice: 3 },
  code: { available: true, added: 10, removed: 2 },
  events: [{ kind: "write" }, { kind: "migrate" }, { kind: "done" }],
  records: { lives_ended: 4, chose: 3, longest_life: { ordinal: 4, seconds: 240 },
    lives: lives(4), lives_omitted: 0 },
  lineage: [
    { ordinal: 4, kind: "declared", ended_epoch: NOW / 1000 - 1000, lifespan_seconds: 240,
      turns_lived: 12, turns_partial: true, sentence: "Fourth note." },
    { ordinal: 3, kind: "harness", ended_epoch: NOW / 1000 - 2000, lifespan_seconds: 180,
      turns_lived: 7, turns_partial: false, sentence: "Third note." }
  ]
};
renderLineage();
var bars = fake("bars").children, labels = fake("bar-labels").children;
out.bar_count = bars.length;
out.bar_classes = bars.map(function (b) { return b.className; });
/* 240s is the max: bar 4 is 100%, bar 2 (120s) 50%, bar 1 undated is a stub, the
   live bar (120s alive) 50%. */
out.bar_heights = bars.map(function (b) { return b.style.height; });
out.labels = labels.map(function (l) { return l.textContent; });
out.count = fake("lineage-count").textContent;
out.figs = fake("life-figs").textContent;
out.ord = fake("subj-ord").textContent;

/* The live bar outgrows the record: after 400s alive it is the max and bar 4 rescales. */
NOW += 280000;
layoutBars(NOW);
out.heights_after_growth = bars.map(function (b) { return b.style.height; });

/* Spotlight: 10s cadence over the lineage entries, newest first; the lit bar follows. */
NOW = 1000000;
renderSpot(NOW);
out.spot_0 = fake("spot-eyebrow").textContent;
out.spot_0_facts = fake("spot-facts").textContent;
out.spot_0_note = fake("spot-note").textContent;
out.lit_0 = bars.map(function (b) { return !!b._lit; });
renderSpot(NOW + 10000);
out.spot_1 = fake("spot-eyebrow").textContent;
out.lit_1 = bars.map(function (b) { return !!b._lit; });
renderSpot(NOW + 20000);
out.spot_2_wraps = fake("spot-eyebrow").textContent === out.spot_0;

/* A death pins the spotlight on the newly dead life for one cadence. */
spotPin = { ordinal: 3, untilMs: NOW + 10000 };
renderSpot(NOW);
out.pinned = fake("spot-eyebrow").textContent;
/* 20001ms on: the pin has lapsed and the clock window (102, even) names ordinal 4. */
renderSpot(NOW + 20001);
out.unpinned = fake("spot-eyebrow").textContent;

/* Cap: with 45 dead lives only the newest 40 draw and the foot discloses the rest. */
global.snap.records.lives = lives(45);
global.snap.records.lives_ended = 45;
global.snap.stats.lives_ended = 45;
global.snap.stats.incarnation = 46;
renderLineage();
out.capped_bars = fake("bars").children.filter(function (b) { return !b.hidden; }).length;
out.capped_labels = fake("bar-labels").children.filter(function (l) { return !l.hidden; })
  .map(function (l) { return l.textContent; }).filter(Boolean);
out.foot_lines = lineageFootLines;

/* Nothing dead yet. */
global.snap.records.lives = []; global.snap.records.lives_ended = 0;
global.snap.stats.lives_ended = 0; global.snap.stats.incarnation = 1; global.snap.lineage = [];
renderLineage();
out.empty_note = fake("spot-note").textContent;
out.empty_bars = fake("bars").children.filter(function (b) { return !b.hidden; }).length;

/* Foot rotation on a 20s clock. */
lineageFootLines = ["a", "b", "c"];
rotateLineageFoot(0); out.foot_0 = fake("lineage-foot").textContent;
rotateLineageFoot(20000); out.foot_1 = fake("lineage-foot").textContent;
rotateLineageFoot(60000); out.foot_wraps = fake("lineage-foot").textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_lineage_scales_colours_caps_and_walks(tmp_path):
    out = _run(LINEAGE_HARNESS.replace("__BLOCK__", _lineage_block()), tmp_path)
    assert out["bar_count"] == 5
    assert out["bar_classes"] == ["bar k-declared", "bar k-declared", "bar k-harness",
                                  "bar k-declared", "bar now"]
    assert out["bar_heights"] == ["2px", "50%", "75%", "100%", "50%"]
    assert out["labels"] == ["1", "2", "3", "4", "5"]
    assert out["count"] == "5 LIVES SO FAR"
    assert out["figs"] == "alive 120s · 4 turns · 2 self-edits · 2 reached out"
    assert out["ord"] == "5"
    assert out["heights_after_growth"] == ["2px", "30%", "45%", "60%", "100%"]
    assert out["spot_0"] == "INCARNATION 4 · ENDED ON ITS OWN NOTE · 1000s ago"
    assert out["spot_0_facts"] == "lived 240s · 12+ turns"
    assert out["spot_0_note"] == "Fourth note."
    assert out["lit_0"] == [False, False, False, True, False]
    assert out["spot_1"].startswith("INCARNATION 3 · ENDED ON A HARNESS NOTE")
    assert out["lit_1"] == [False, False, True, False, False]
    assert out["spot_2_wraps"] is True
    assert out["pinned"].startswith("INCARNATION 3")
    assert out["unpinned"].startswith("INCARNATION 4")
    assert out["capped_bars"] == 41
    assert out["capped_labels"] == ["10", "15", "20", "25", "30", "35", "40", "45", "46"]
    assert "5 earlier lives not shown" in out["foot_lines"]
    assert "by their own notes, 3 of 45 chose to die" in out["foot_lines"]
    assert out["empty_note"] == "No one has died here yet."
    assert out["empty_bars"] == 1
    assert (out["foot_0"], out["foot_1"], out["foot_wraps"]) == ("a", "b", "a")


NOW_HARNESS = """
var nodes = {};
function fake(id) { if (!nodes[id]) nodes[id] = { textContent: "" }; return nodes[id]; }
global.$ = fake;
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); };
global.setClass = function (node, name, on) { node["_" + name] = !!on; };
global.dur = function (s) { return Math.round(s) + "s"; };
var NOW = 1000000;
global.clock = function () { return NOW; };
global.snap = null;

__BLOCK__

var out = {};
global.snap = { commentary: { play: { phrase: "running call_model", epoch: NOW / 1000 - 16 },
  colour: { text: "It repeats.", generated: true, evidence: "run_shell x3 in a row" } } };
renderNow();
out.colour = fake("now-colour").textContent;
out.evidence = fake("now-evidence").textContent;
out.fresh = fake("now-dot")._fresh;
global.snap = { commentary: { play: { phrase: "thinking it over", epoch: null },
  colour: { text: "Template.", generated: false, evidence: "" } } };
renderNow();
out.phrase_fallback = fake("now-evidence").textContent;
out.not_fresh = fake("now-dot")._fresh;
global.snap = { commentary: {} };
renderNow();
out.empty = fake("now-evidence").textContent;
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_now_prefers_evidence_over_phrase_and_lights_only_generated_lines(tmp_path):
    out = _run(NOW_HARNESS.replace("__BLOCK__", _now_block()), tmp_path)
    assert out["colour"] == "It repeats."
    assert out["evidence"] == "run_shell x3 in a row · 16s"
    assert out["fresh"] is True
    assert out["phrase_fallback"] == "thinking it over"
    assert out["not_fresh"] is False
    assert out["empty"] == "waiting for the first word"
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py tests/test_stage_pages_js.py -q`
Expected: the new tests FAIL (ids missing, block markers missing); the deleted tests are gone.

- [ ] **Step 3: Replace the rail CSS**

In `stage/pages.py`, replace everything from the line `/* ---------- rail ---------- */` up to (not including) `/* ---------- ribbon ---------- */` with:

```css
/* ---------- rail ---------- */
#rail { grid-column: 2; grid-row: 2; display: grid; grid-template-rows: 536px 216px;
  row-gap: 20px; min-height: 0; }
#rail .panel { padding: 14px 20px; }
#rail .ptitle { margin-bottom: 8px; }

/* THE LINEAGE: every life as a bar, oldest left, the current life on the right
   growing with every tick. Bar height is linear in seconds against the longer
   of the longest recorded life and the current life's age, so the live bar is
   never clipped; undated lives keep a 2px stub so every slot is counted. */
#lineage { position: relative; display: flex; flex-direction: column; gap: 12px; }
#lineage.nosig { opacity: .55; }
#lineage.nosig::after { content: ""; position: absolute; left: 0; right: 0; top: 50%;
  height: 1px; background: var(--fault); }
#lineage.cut { animation: cut 900ms ease-out; }
@keyframes cut { 0% { border-color: var(--rule); box-shadow: none }
  35% { border-color: var(--act); box-shadow: 0 0 12px rgba(240,189,104,.35) }
  100% { border-color: var(--rule); box-shadow: none } }
#chart { flex: none; height: 220px; display: flex; flex-direction: column; gap: 6px; }
#bars { flex: 1; min-height: 0; display: flex; align-items: flex-end; gap: 4px;
  border-bottom: 1px solid var(--rule-2); }
.bar { flex: 1 1 0; min-width: 0; height: 2px; opacity: .8; border-radius: 2px 2px 0 0;
  position: relative; }
.bar.k-declared { background: var(--chosen); }
.bar.k-harness { background: var(--taken); }
.bar.k-unknown { background: var(--broken); }
.bar.now { background: var(--vital); opacity: 1; }
.bar.now::after { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px;
  background: var(--flash); animation: breathe 1.6s ease-in-out infinite; }
.bar.lit { opacity: 1; outline: 1px solid var(--paper); outline-offset: 2px; }
#bar-labels { flex: none; height: 18px; display: flex; gap: 4px; }
.bl { flex: 1 1 0; min-width: 0; text-align: center; overflow: hidden;
  font: 400 13px/18px var(--mono); color: var(--paper-faint);
  font-variant-numeric: tabular-nums; }
.bl.now { color: var(--vital); }

#life-now { flex: none; display: flex; flex-direction: column; gap: 4px; }
#life-head { display: flex; align-items: baseline; gap: 12px; min-width: 0; }
.eyebrow { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }
#subj-ord { font: 600 34px/38px var(--sans); color: var(--paper); font-variant-numeric: tabular-nums; }
#subj-ord.bump { animation: bump 320ms ease-out; }
@keyframes bump { 0% { transform: scale(1) } 50% { transform: scale(1.06); color: var(--flash) }
  100% { transform: scale(1) } }
#subj-model { font: 400 13px/19px var(--mono); color: var(--paper-dim); min-width: 0;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#life-figs { font: 400 15px/20px var(--mono); color: var(--vital); font-variant-numeric: tabular-nums;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srow { display: grid; grid-template-columns: 68px 1fr; align-items: center;
  font: 400 15px/20px var(--mono); font-variant-numeric: tabular-nums; }
.srow .k { color: var(--act); text-transform: uppercase; letter-spacing: .06em;
  display: flex; align-items: center; gap: 6px; }
.srow .k::before { content: ""; width: 3px; height: 14px; background: var(--act); flex: none; }
.srow .v { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srow .add { color: var(--vital); }
.srow .rem { color: var(--fault); }
.srow .tail { color: var(--paper-dim); }
.srow .plain { color: var(--paper-dim); }
.srow .none { color: var(--paper-faint); }

/* The spotlight walks the recent dead one at a time on a 10s cadence — the
   pacing is the disclosure; there is nothing to click. */
#spot { flex: 1; min-height: 0; display: grid; grid-template-columns: 22px 1fr; column-gap: 14px;
  overflow: hidden; }
#spot .tick { width: 2px; justify-self: end; }
#spot .spot-body { min-width: 0; }
#spot .spot-body.swap { animation: surface 400ms ease; }
#spot.slide { animation: slidein 500ms cubic-bezier(.22,.61,.36,1); }
@keyframes slidein { from { transform: translateY(-14px); opacity: 0 } to { transform: none; opacity: 1 } }
.g-eyebrow { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.g-facts { font: 400 13px/18px var(--mono); color: var(--paper-faint); margin-top: 2px;
  font-variant-numeric: tabular-nums; }
.clamp.tomb { -webkit-line-clamp: 2; line-clamp: 2; font: 400 15px/23px var(--serif);
  color: var(--paper-dim); max-width: 60ch; margin-top: 2px; text-wrap: pretty; }
#spot.empty .clamp.tomb { color: var(--paper-dim); }
.k-declared { color: var(--chosen); } .k-declared .tick { background: var(--chosen); }
.k-harness { color: var(--taken); } .k-harness .tick { background: var(--taken); }
.k-unknown { color: var(--broken); } .k-unknown .tick { background: var(--broken); }
#lineage-foot { font: 400 13px/16px var(--mono); color: var(--paper-faint); flex: none;
  height: 16px; overflow: hidden; }

/* commentary:start */
/* NOW: the generated read of the current beat is the panel's subject; its
   evidence sits under it; the byline names it as the stage's. */
#now { display: flex; flex-direction: column; }
#now-colour { margin: 0; font: 500 22px/30px var(--sans); color: var(--paper);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; line-clamp: 3;
  overflow: hidden; }
#now-evidence { margin-top: 8px; font: 400 13px/18px var(--mono); letter-spacing: .06em;
  text-transform: uppercase; color: var(--paper-dim); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
#now-by { margin-top: auto; display: flex; align-items: center; gap: 6px;
  font: 400 13px/18px var(--mono); letter-spacing: .08em; color: var(--paper-faint); }
#now-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--paper-faint);
  display: inline-block; flex: none; }
#now-dot.fresh { background: var(--vital); }
/* commentary:end */

```

Also delete these now-orphaned rules from the expansion section near the end of the stylesheet: `.blk-tomb .more, #recap-box .more, #pull-box .more { … }` (keep `.blk-think .more`, `.blk-say .more`), `.rail-blk.open .clamp { … }`, `.blk-tomb.open .clamp { … }`, `#recap-box.open .clamp, #pull-box.open .clamp { … }`, and `.blk.is-expandable:hover .clamp.tomb { … }`.

- [ ] **Step 4: Replace the rail markup**

Replace the whole `<aside id="rail"> … </aside>` with:

```html
  <aside id="rail">
    <section id="lineage" class="panel">
      <div class="ptitle"><span>THE LINEAGE</span><span id="lineage-count"></span></div>
      <div id="chart" aria-hidden="true">
        <div id="bars"></div>
        <div id="bar-labels"></div>
      </div>
      <div id="life-now">
        <div id="life-head">
          <span class="eyebrow">INCARNATION</span>
          <span id="subj-ord">&mdash;</span>
          <span id="subj-model">&mdash;</span>
        </div>
        <div id="life-figs">&mdash;</div>
        <div class="srow"><span class="k">source</span><span class="v" id="v-src">&mdash;</span></div>
      </div>
      <div id="spot" class="k-unknown empty">
        <i class="tick"></i>
        <div class="spot-body">
          <div id="spot-eyebrow" class="g-eyebrow"></div>
          <div id="spot-facts" class="g-facts"></div>
          <div id="spot-note" class="clamp tomb"></div>
        </div>
      </div>
      <div id="lineage-foot"></div>
    </section>

    <section id="now" class="panel">
      <div class="ptitle"><span>NOW</span></div>
      <p id="now-colour" aria-live="polite"></p>
      <div id="now-evidence"></div>
      <div id="now-by"><i id="now-dot"></i><span>&mdash; the stage, not the subject</span></div>
    </section>
  </aside>
```

- [ ] **Step 5: Replace the JS**

(a) In the globals line `var turnNodes = new Map(), dividers = new Map(), expanded = new Set(), graveNodes = [];` drop `, graveNodes = []`.

(b) In `setState(age)`: delete the `var strip; … setText($("strip-text"), strip);` lines (from `var strip;` through `setText($("strip-text"), strip);`) and change `setClass($("subject"), "nosig", s === "nosignal");` to `setClass($("lineage"), "nosig", s === "nosignal");`.

(c) Delete everything from the line `/* ---------- subject ---------- */` through the closing brace of `rotateDeadFoot` (the line before `/* ---------- eye ---------- */`), and insert in its place:

```js
/* ---------- lineage ---------- */
/* THE LINEAGE. One bar per life, oldest left, the current life on the right,
   growing with every tick. Heights are linear in seconds against the longer
   of the longest recorded life and the current life's age, so the live bar is
   never clipped. Undated lives keep a stub so every slot is counted. Every
   number here is a snapshot fact or clock arithmetic on one. */
var BAR_CAP = 40, STUB_PX = "2px", SPOT_SECONDS = 10, FOOT_SECONDS = 20;
var KIND_LABEL = { declared: "ENDED ON ITS OWN NOTE", harness: "ENDED ON A HARNESS NOTE",
  unknown: "ENDED WITHOUT A NOTE" };
var barNodes = [], labelNodes = [], barLives = [];
var lineageFootLines = [""];
var lifeEdits = 0;
var spotPin = { ordinal: null, untilMs: 0 };
var spotShown = null;

function kindOf(kind) { return KIND_LABEL[kind] ? kind : "unknown"; }
function ensureBars(count) {
  var host = $("bars"), labels = $("bar-labels");
  while (barNodes.length < count) {
    barNodes.push(el("div", "bar", host));
    labelNodes.push(el("span", "bl", labels));
  }
  for (var i = 0; i < barNodes.length; i++) {
    barNodes[i].hidden = i >= count;
    labelNodes[i].hidden = i >= count;
  }
}
/* The newest BAR_CAP dead lives, oldest first, then the current one. */
function barModel() {
  var rec = snap.records || {}, lives = (rec.lives || []).slice(-BAR_CAP), out = [];
  for (var i = 0; i < lives.length; i++) {
    var l = lives[i] || {};
    out.push({ ordinal: l.ordinal, kind: kindOf(l.kind), seconds: l.seconds, now: false });
  }
  out.push({ ordinal: snap.stats.incarnation, kind: null, seconds: null, now: true });
  return out;
}
function aliveSeconds(nowMs) {
  var st = snap.stats;
  if (st.started_epoch == null || !((st.turns_this_life || 0) > 0)) return 0;
  return Math.max(0, nowMs / 1000 - st.started_epoch);
}
function labelStep(count) { return count > 24 ? 5 : 1; }
function layoutBars(nowMs) {
  var alive = aliveSeconds(nowMs), max = alive, i;
  for (i = 0; i < barLives.length; i++) {
    if (barLives[i].seconds > max) max = barLives[i].seconds;
  }
  for (i = 0; i < barLives.length; i++) {
    var b = barLives[i], sec = b.now ? alive : b.seconds, h;
    if (!(sec > 0) || !(max > 0)) h = STUB_PX;
    else h = Math.max(1, Math.round(100 * sec / max)) + "%";
    if (barNodes[i].style.height !== h) barNodes[i].style.height = h;
  }
}
function litBar(ordinal) {
  for (var i = 0; i < barLives.length; i++) {
    setClass(barNodes[i], "lit", ordinal != null && !barLives[i].now && barLives[i].ordinal === ordinal);
  }
}
function renderLifeFigs(nowMs) {
  var st = snap.stats, parts = [];
  parts.push("alive " + (st.started_epoch != null ? dur(nowMs / 1000 - st.started_epoch) : "—"));
  var tl = st.turns_this_life || 0;
  parts.push(tl + (st.turns_this_life_exact ? "" : "+") + " turn" +
    (tl === 1 && st.turns_this_life_exact ? "" : "s"));
  parts.push(lifeEdits + " self-edit" + (lifeEdits === 1 ? "" : "s"));
  parts.push(reachedThisLife() + " reached out");
  setText($("life-figs"), parts.join(" · "));
}
function renderSource() {
  var code = snap.code || {}, v = $("v-src");
  v.textContent = ""; v.__text = null;
  if (!code.available) {
    el("span", "none", v).textContent = "the mirror is not available";
  } else if (!(code.added || code.removed)) {
    el("span", "plain", v).textContent = "unmodified — still the seed it woke up as";
  } else {
    el("span", "add", v).textContent = "+" + (code.added || 0);
    el("span", "tail", v).textContent = " / ";
    el("span", "rem", v).textContent = "−" + (code.removed || 0);
    el("span", "tail", v).textContent = " lines from seed";
  }
}
function renderLineage() {
  var st = snap.stats, rec = snap.records || {}, nowMs = clock();
  setText($("lineage-count"), st.incarnation + " LIVE" + (st.incarnation === 1 ? "" : "S") + " SO FAR");
  barLives = barModel();
  ensureBars(barLives.length);
  var step = labelStep(barLives.length);
  for (var i = 0; i < barLives.length; i++) {
    var b = barLives[i], node = barNodes[i], cls = "bar" + (b.now ? " now" : " k-" + b.kind);
    if (node.className !== cls) node.className = cls;
    var last = i === barLives.length - 1;
    var show = b.ordinal != null && (last || step === 1 || b.ordinal % step === 0);
    setText(labelNodes[i], show ? String(b.ordinal) : "");
    var lcls = "bl" + (b.now ? " now" : "");
    if (labelNodes[i].className !== lcls) labelNodes[i].className = lcls;
  }
  layoutBars(nowMs);

  setText($("subj-ord"), String(st.incarnation));
  if (lastOrdinal != null && st.incarnation !== lastOrdinal && !REDUCED) {
    var o = $("subj-ord");
    o.classList.remove("bump"); void o.offsetWidth; o.classList.add("bump");
  }
  lastOrdinal = st.incarnation;
  setText($("subj-model"), st.model || "—");
  $("subj-model").title = st.model || "";
  var ev = snap.events || [];
  lifeEdits = 0;
  for (var e = 0; e < ev.length; e++) if (ev[e].kind === "write" || ev[e].kind === "migrate") lifeEdits++;
  renderLifeFigs(nowMs);
  renderSource();

  var shownDead = Math.max(0, barLives.length - 1);
  lineageFootLines = lineageFootFor(st, rec, shownDead);
  renderSpot(nowMs);
}

/* The spotlight: the lineage entries the snapshot carries (newest first), one
   at a time on a SPOT_SECONDS cadence keyed to the clock, so a reload lands on
   the same entry. A death pins the newly dead life for one cadence. */
function spotIndexFor(nowMs, count) {
  return count ? Math.floor(nowMs / (SPOT_SECONDS * 1000)) % count : -1;
}
function spotEntry(nowMs) {
  var lin = snap.lineage || [];
  if (!lin.length) return null;
  if (spotPin.ordinal != null && nowMs < spotPin.untilMs) {
    for (var i = 0; i < lin.length; i++) if (lin[i].ordinal === spotPin.ordinal) return lin[i];
  }
  return lin[spotIndexFor(nowMs, lin.length)];
}
function spotFacts(l) {
  var facts = [];
  if (l.lifespan_seconds != null) facts.push("lived " + dur(l.lifespan_seconds));
  /* turns_lived is counted over the transcript window, which the oldest life in
     it can outrun; turns_partial says the count is a floor. */
  if (l.turns_lived != null) {
    var lived = l.turns_lived + (l.turns_partial ? "+" : "");
    facts.push(lived + " turn" + (l.turns_lived === 1 && !l.turns_partial ? "" : "s"));
  }
  return facts.join(" · ");
}
function renderSpot(nowMs) {
  var spot = $("spot"), l = spotEntry(nowMs);
  if (!l) {
    if (spot.className !== "k-unknown empty") spot.className = "k-unknown empty";
    setText($("spot-eyebrow"), "");
    setText($("spot-facts"), "");
    setText($("spot-note"), "No one has died here yet.");
    spotShown = null;
    litBar(null);
    return;
  }
  var kind = kindOf(l.kind), ord = l.ordinal == null ? "?" : l.ordinal;
  var key = ord + ":" + kind;
  if (spot.className !== "k-" + kind) spot.className = "k-" + kind;
  var eyebrow = "INCARNATION " + ord + " · " + KIND_LABEL[kind];
  if (l.ended_epoch != null) eyebrow += " · " + rel(l.ended_epoch, nowMs);
  setText($("spot-eyebrow"), eyebrow);
  setText($("spot-facts"), spotFacts(l));
  var note = norm(l.sentence || l.summary || "");
  if (setText($("spot-note"), note)) $("spot-note").__dirty = true;
  if (spotShown !== key) {
    spotShown = key;
    if (!REDUCED) {
      var body = spot.querySelector ? spot.querySelector(".spot-body") : null;
      if (body) { body.classList.remove("swap"); void body.offsetWidth; body.classList.add("swap"); }
    }
  }
  litBar(l.ordinal);
}

/* The record book foot rotates cross-life records so a returning viewer has
   something to track. The chose count comes from the record book, which is
   taken over every tombstone; stats counts it over the five lineage entries
   only and is used just when the book is absent. */
function lineageFootFor(st, book, shown) {
  st = st || {}; book = book || {};
  var ended = st.lives_ended || 0;
  var chose = book.lives_ended ? (book.chose || 0) : (st.ended_by_choice || 0);
  var hiddenLives = Math.max(0, ended - (shown || 0));
  var lines = [];
  if (ended) lines.push("by their own notes, " + chose + " of " + ended + " chose to die");
  var longest = book.longest_life;
  if (longest && longest.seconds != null) {
    lines.push("longest life: incarnation " + longest.ordinal + " · " + dur(longest.seconds));
  }
  if (hiddenLives > 0) {
    lines.push(hiddenLives + " earlier " + (hiddenLives === 1 ? "life" : "lives") + " not shown");
  }
  return lines.length ? lines : [""];
}
function rotateLineageFoot(nowMs) {
  var lines = lineageFootLines.length ? lineageFootLines : [""];
  setText($("lineage-foot"), lines[Math.floor(nowMs / (FOOT_SECONDS * 1000)) % lines.length]);
}

/* ---------- now ---------- */
/* NOW: the generated colour line is the subject; the evidence line prefers
   the beat's counted fact and falls back to the play phrase; the dot is lit
   only for a generated line, never for the no-key template. */
function renderNow() {
  var c = (snap.commentary || {}), play = c.play || {}, colour = c.colour || {};
  setText($("now-colour"), colour.text || "");
  var line = colour.evidence || play.phrase || "waiting for the first word";
  var age = play.epoch == null ? null : Math.max(0, clock() / 1000 - play.epoch);
  setText($("now-evidence"), line + (age == null ? "" : " · " + dur(age)));
  setClass($("now-dot"), "fresh", !!colour.generated);
}

```

Notes for the implementer: `spot.querySelector` is guarded because the node test harness's fake elements have none; in the browser it always exists. `setText` returns `true` on change (used for `__dirty`).

(d) `runMourn(endedOrdinal)`: replace the `if (!REDUCED) { for (var i = 0; i < graveNodes.length; i++) { … } }` block with:

```js
  spotPin = { ordinal: endedOrdinal, untilMs: clock() + SPOT_SECONDS * 1000 };
  renderSpot(clock());
  if (!REDUCED) {
    var sp = $("spot");
    sp.classList.remove("slide"); void sp.offsetWidth; sp.classList.add("slide");
    setTimeout(function () { sp.classList.remove("slide"); }, 700);
  }
```

(e) `maybeCut()`: change `var s = $("subject");` to `var s = $("lineage");`.

(f) `setRelativeTimes(nowMs)`: delete the `for (var i = 0; i < graveNodes.length; i++) { … }` loop and the `if (snap.story && snap.story.text) renderStoryByline(nowMs);` line; the `var metas` loop needs `var i` declared — write `var metas = …; for (var i = 0; …`.

(g) `tick()`: replace the body between `var st = snap.stats;` and `if (announceUntil …` with:

```js
  var st = snap.stats;
  renderLifeFigs(nowMs);
  layoutBars(nowMs);
  setPulse(state);
  setRelativeTimes(nowMs);
  renderNow();
  renderSpot(nowMs);
  rotateLineageFoot(nowMs);
  maybeAutoRepin();
```

and delete the trailing `fitRecap();` line. (`st` is still used by the cold-start check below.)

(h) `render(prev)`: replace `renderSubject(); renderLanes(); renderStory(); renderDead(); renderDesk();` with `renderLineage(); renderLanes();`.

- [ ] **Step 6: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py tests/test_stage_pages_js.py -q`
Expected: PASS. If `test_the_stream_page_script_parses` fails, read node's stderr in the assertion message — it names the line. If a grep test you did not expect fails, it is asserting old markup; check the list in Step 1 before deleting anything else.

- [ ] **Step 7: Full suite, lint, commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/pages.py tests/test_stage_pages.py tests/test_stage_pages_js.py
git commit -m "Replace the stream page rail with the lineage chart and the now panel"
```

---

### Task 3: Layout tiers — canvas, scaled, flow

**Files:**
- Modify: `stage/pages.py` — append CSS before `@media (prefers-reduced-motion: reduce)`, append JS before `poll();` at the end of the script
- Test: `tests/test_stage_pages.py`, `tests/test_stage_pages_js.py`

**Interfaces:**
- Consumes: the ids from Task 2.
- Produces: `fitStage()` and `tierFor(width)` in a `/* ---------- tiers ---------- */` … `/* ---------- render ---------- */` block; wait — `/* ---------- render ---------- */` already exists earlier in the file, so place the tiers block **immediately before** `/* ---------- lanes ---------- */` (which precedes render) and end it with that marker. `document.documentElement` carries `data-tier="canvas"|"scaled"|"flow"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_stage_pages.py`:

```python
def test_the_page_has_three_layout_tiers():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    assert "@media (max-width: 1919px)" in css
    assert "@media (max-width: 1199px)" in css
    flow = css[css.index("@media (max-width: 1199px)") :]
    assert "#rail { display: contents; }" in flow
    for rule in ("#lineage { order: 1;", "#monologue { order: 2;", "#now { order: 3;",
                 "#ribbon { order: 4;"):
        assert rule in flow, rule
    assert "#eye { display: none; }" in flow
    assert "70dvh" in flow
    assert "min-height: 44px" in flow, "the return-to-live chip must be a touch target"


def test_the_flow_tier_collapses_the_turn_gutter_into_a_row():
    css = HTML[HTML.index("@media (max-width: 1199px)") : HTML.index("</style>")]
    assert ".turn { grid-template-columns: 1fr;" in css
    assert ".col { grid-column: 1; }" in css
    assert ".gutter { grid-column: 1; text-align: left;" in css


def test_the_canvas_tier_alone_hides_overflow():
    css = HTML[HTML.index("<style>") : HTML.index("</style>")]
    base = css[: css.index("@media (max-width: 1919px)")]
    assert "html, body { width: 1920px; height: 1080px;" in base
    scaled = css[css.index("@media (max-width: 1919px)") : css.index("@media (max-width: 1199px)")]
    assert "html, body { width: auto; height: auto; overflow: auto; }" in scaled
```

`tests/test_stage_pages_js.py`:

```python
def _tiers_block():
    return _block("/* ---------- tiers ---------- */", "/* ---------- lanes ---------- */")


TIERS_HARNESS = """
var root = { attrs: {}, setAttribute: function (k, v) { this.attrs[k] = v; } };
var stage = { style: {} }, body = { style: {} };
global.document = { documentElement: root, body: body };
global.window = { innerWidth: 1920, addEventListener: function () {} };
global.$ = function (id) { return id === "stage" ? stage : null; };

__BLOCK__

var out = {};
function at(w) { global.window.innerWidth = w; return fitStage(); }
out.t1920 = at(1920); out.s1920 = stage.style.transform; out.h1920 = body.style.height;
out.t2560 = at(2560); out.s2560 = stage.style.transform;
out.t1440 = at(1440); out.s1440 = stage.style.transform; out.h1440 = body.style.height;
out.t1200 = at(1200); out.s1200 = stage.style.transform;
out.t1199 = at(1199); out.s1199 = stage.style.transform; out.h1199 = body.style.height;
out.t390 = at(390); out.attr = root.attrs["data-tier"];
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_tier_handler_scales_only_between_1200_and_1919(tmp_path):
    out = _run(TIERS_HARNESS.replace("__BLOCK__", _tiers_block()), tmp_path)
    assert (out["t1920"], out["s1920"], out["h1920"]) == ("canvas", "", "")
    assert (out["t2560"], out["s2560"]) == ("canvas", "")
    assert out["t1440"] == "scaled" and out["s1440"] == "scale(0.7500)" and out["h1440"] == "810px"
    assert out["t1200"] == "scaled" and out["s1200"] == "scale(0.6250)"
    assert (out["t1199"], out["s1199"], out["h1199"]) == ("flow", "", "")
    assert out["t390"] == "flow" and out["attr"] == "flow"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py tests/test_stage_pages_js.py -q -k "tier or flow or canvas"`
Expected: FAIL.

- [ ] **Step 3: Add the CSS**

Insert immediately before the line `@media (prefers-reduced-motion: reduce) {`:

```css
/* ---------- layout tiers ---------- */
/* canvas (>=1920): the 1920x1080 grid above, pixel for pixel — what OBS loads.
   scaled (1200-1919): the same canvas scaled to the viewport width by
   fitStage(), so a laptop sees the whole broadcast composition.
   flow (<1200): a single fluid column that scrolls; media queries only. */
@media (max-width: 1919px) {
  html, body { width: auto; height: auto; overflow: auto; }
  #stage { width: 1920px; transform-origin: 0 0; }
}
@media (max-width: 1199px) {
  #stage { width: auto; height: auto; transform: none !important; display: flex;
    flex-direction: column; gap: 14px; padding: 12px; }
  #masthead { display: flex; flex-direction: column; gap: 6px; padding-bottom: 8px; order: 0; }
  #mh-a { flex-wrap: wrap; gap: 10px 14px; }
  #wordmark { font-size: 22px; line-height: 28px; letter-spacing: .14em; }
  .vrule { display: none; }
  #premise { order: 3; width: 100%; max-width: none; font-size: 14px; line-height: 20px; }
  #mh-b { flex-wrap: wrap; gap: 8px 18px; }
  #provenance { white-space: normal; flex: 1 1 100%; margin-left: 0; }
  #rail { display: contents; }
  #lineage { order: 1; flex: none; gap: 8px; }
  #chart { height: 84px; }
  #spot { flex: none; }
  #monologue { order: 2; height: 70dvh; min-height: 420px; }
  #now { order: 3; min-height: 150px; }
  #ribbon { order: 4; display: grid; grid-template-columns: 1fr; gap: 14px; }
  #ribbon .panel { min-height: 136px; }
  #eye { display: none; }
  #coldstart { padding-left: 12px; }
  .turn { grid-template-columns: 1fr; padding: 10px 0 12px; }
  .turn.is-edit, .turn.is-error, .turn.is-end { grid-template-columns: 1fr; padding-left: 10px; }
  .gutter { grid-column: 1; text-align: left; display: flex; flex-wrap: wrap; gap: 0 10px;
    margin-bottom: 4px; }
  .gutter .g-mark { display: inline; }
  .col { grid-column: 1; }
  .clamp.think { font-size: 17px; line-height: 26px; }
  .blk-think::before { left: -8px; }
  .blk-think.tail .clamp { max-height: 364px; }
  .clamp.say { font-size: 16px; line-height: 24px; }
  .tool { font-size: 13px; line-height: 19px; }
  #pulse-spark { width: 140px; }
  #return-live { min-height: 44px; padding: 10px 14px; }
}
@media (min-width: 720px) and (max-width: 1199px) {
  #ribbon { grid-template-columns: 1fr 1.6fr 1fr; }
}
@media (max-width: 719px) {
  #provenance, #repo { display: none; }
}
```

`.blk-think.tail .clamp` at 364px is 14 lines × 26px, matching the flow-tier think line-height (the canvas rule is 406 = 14 × 29). Percent bar heights (Task 2) make the 84px chart work without JS changes.

- [ ] **Step 4: Add the JS**

Insert immediately before the line `/* ---------- lanes ---------- */`:

```js
/* ---------- tiers ---------- */
/* Three layouts from one page: the 1920x1080 canvas OBS loads, the same
   canvas scaled to a laptop, and a single scrolling column for a phone. The
   CSS media queries carry the flow tier; this handler only applies the scale
   in between and stamps the tier for anything that needs to know. */
var CANVAS_W = 1920, CANVAS_H = 1080, FLOW_MAX = 1199;
function tierFor(width) {
  return width >= CANVAS_W ? "canvas" : width > FLOW_MAX ? "scaled" : "flow";
}
function fitStage() {
  var w = window.innerWidth, tier = tierFor(w), stage = $("stage");
  document.documentElement.setAttribute("data-tier", tier);
  if (tier === "scaled") {
    var s = w / CANVAS_W;
    stage.style.transform = "scale(" + s.toFixed(4) + ")";
    document.body.style.height = Math.round(CANVAS_H * s) + "px";
  } else {
    stage.style.transform = "";
    document.body.style.height = "";
  }
  return tier;
}
window.addEventListener("resize", fitStage);

```

and, just before `poll();` near the end of the script, add the line `fitStage();`.

- [ ] **Step 5: Run the tests, then the full suite, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_stage_pages.py tests/test_stage_pages_js.py -q
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/pages.py tests/test_stage_pages.py tests/test_stage_pages_js.py
git commit -m "Give the stream page a scaled tier and a single-column flow tier"
```

---

### Task 4: Container verification and visual check

**Files:** none modified unless a defect is found (then fix in `stage/pages.py` with a test).

- [ ] **Step 1: Rebuild and restart only the stage**

```bash
docker compose build stage && docker compose up -d --no-deps stage
sleep 3 && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8091/
curl -s http://127.0.0.1:8091/api/stream | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d['records']['lives']), d['records']['lives_omitted'], d['stats']['incarnation'])"
```

Expected: `200`, and a lives count equal to `incarnation - 1`.

- [ ] **Step 2: Screenshots at three viewports**

Use the Playwright MCP tools against `http://127.0.0.1:8091/` (or the public URL after the tunnel picks it up): resize to 1920×1080, 1440×900, 390×844; screenshot each; for each run

```js
() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
         tier: document.documentElement.getAttribute("data-tier"),
         bars: document.querySelectorAll("#bars .bar:not([hidden])").length,
         nowBar: document.querySelector("#bars .bar.now").style.height,
         spot: document.getElementById("spot-eyebrow").textContent })
```

Expected: `sw <= cw` at every width (no horizontal page scroll); tier `canvas`/`scaled`/`flow`; bars = incarnation; the `now` bar height differs between two evaluations ~30 s apart while the agent is alive; on the 1080p shot the rail reads THE LINEAGE over NOW with no clipped text; on the phone shot the order is masthead → lineage → monologue → now → ribbon and the monologue's own scroll works.

- [ ] **Step 3: Fix anything found**

Each fix gets a grep or node test in the Task 2/3 files first, then the change, then `docker compose build stage && docker compose up -d --no-deps stage` and a re-shot.

- [ ] **Step 4: Commit any fixes**

```bash
git add -A stage tests && git commit -m "Adjust the stream page after the container check"
```

---

### Task 5: Docs and tracker

**Files:**
- Modify: `docs/superpowers/specs/2026-08-17-stage-rail-and-responsive-design.md` (NOW colour line: three-line clamp; add "Status: implemented" with the commit range)
- Modify: `README.md:199-219` "Streaming the stage" — one sentence noting the page serves a 1920×1080 composition to OBS and reflows for phones.

- [ ] **Step 1: Edit the two docs as above; keep README wording factual and short.**
- [ ] **Step 2: Commit**

```bash
git add docs README.md && git commit -m "Record the stage rail rebuild and the page's layout tiers"
```

- [ ] **Step 3: Close the tracker issue** `aurora-1bcb867c23` with `mcp__filigree__issue_close` (reason: implemented and verified in the container; commit `<sha>`), and add a comment to `aurora-4b713bfe9f` noting the rail rulings it carried are superseded by the 2026-08-17 spec.

---

## Self-review

- Spec coverage: THE LINEAGE (chart, current block, spotlight, foot) → Task 2; NOW → Task 2; deletions → Task 2 step 5; state strip removal → Task 2 (b); tiers → Task 3; data `lives` → Task 1; testing list → Tasks 1–3; container check → Task 4; docs → Task 5. The spec's "the walk continues under reduced motion without the fade" → `renderSpot` advances by clock regardless of `REDUCED` and only skips the `swap` class ✓. Death jump → `runMourn` pins ✓. Chart `aria-hidden` ✓. Cap 40 + disclosure via `lineageFootFor(shownDead)` ✓ (`hidden = lives_ended - shownDead`).
- Placeholders: none.
- Names: `renderLineage/layoutBars/renderLifeFigs/renderSpot/spotEntry/lineageFootFor/rotateLineageFoot/renderNow/fitStage/tierFor/lineageFootLines/spotPin` used consistently across Tasks 2–3 and the harnesses; ids consistent between markup, CSS, JS, and greps.
