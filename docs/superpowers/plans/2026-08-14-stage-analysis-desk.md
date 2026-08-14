# The Analysis Desk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recurring broadcast segment that rates dead incarnations out of five stars, argues for each rating, and shows the facts beside the verdict so the audience argues about judgment rather than about the numbers.

**Architecture:** A new `stage/analysis.py` computes factual per-life evidence from the surviving record, marks how complete that record actually is, and asks the model for a star rating and a one-line argument — cached per incarnation so the board does not flicker. A rotation clock decides when the segment is on air; the colour line in NOW speaks the cue, THE DEAD's slot holds the package, and NOW stays live throughout.

**Tech Stack:** Python 3 standard library only. No new dependencies. Vanilla JS in `stage/pages.py`.

**Spec:** `docs/superpowers/specs/2026-08-14-stage-commentary-design.md` (Part 3, plus Part 4's constraints)

**Depends on:** `docs/superpowers/plans/2026-08-14-stage-now-block.md` (Part 2) must be merged first. This plan consumes `stage/llm.py` and the `#now` block, both of which Part 2 creates.

## Global Constraints

Identical to Part 2's, and binding here for the same reasons:

- **Standard library only.** No new third-party dependency in `stage/`.
- **The stage holds exactly one credential**, `STAGE_SUMMARY_API_KEY`. `stage/analysis.py` must never name `OPENROUTER_API_KEY` or `LLM_API_KEY` in its source.
- **No new writable mount.** The stage container stays `read_only`.
- **The stream port (8091) serves no mutating endpoints.**
- **Any new stage-side read of an agent-writable root goes through `data.contained_file`.**
- **Cap every agent-controlled string before it reaches a prompt or the public snapshot.** This is
  not optional and it is the one constraint Part 2 lacked. Part 2 shipped a Critical defect because
  no task owned it: `server._public_turn` capped tool names at `NAME_CAP = 64`, but the commentary
  path published the same agent-controlled name uncapped, and a 50,000-character tool name reached
  `/api/stream` through two fields. `/api/stream` is polled every 2 s by every viewer of a service
  that can be tunnel-exposed, at `mem_limit: 256m`. Part 3's exposure is **larger**: the analyst
  prompt consumes tombstone notes and published statements, both written by an agent that has a
  shell and writes freely in `/work`, and both far longer than a tool name. Cap at the boundary where
  the value enters the module — one choke point, not per-field — reuse `server.NAME_CAP` for names,
  and extend `tests/test_stage_containment.py::test_agent_controlled_public_fields_are_capped`
  rather than writing a separate test, so the paths stay pinned together.
- **Every prompt carries `llm.RECORDS_FRAMING`.** Tombstone notes are written by the agent and are the analyst's main input — this is the highest-risk injection surface in the whole stage.
- **All rendered content is escaped text.** `setText` / `textContent`. Never `innerHTML`.
- **Generation fails open**, and here it fails *silent*: no key means no segment at all, not a starless placeholder.
- **Never block the request path on a network call.**
- **Do not raise `load_tail_turns`' 4 MiB / 40-turn limits or `proxy.py`'s rotation threshold** to deepen the evidence. The stage runs at `mem_limit: 256m`. Thin evidence is marked, not fixed.
- **The full suite must pass:** `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- **Lint before every commit:** `.venv/bin/ruff format . && .venv/bin/ruff check .`
- **Do not touch the agent's world.** `agent.py`, `agent_stock.py`, `chassis.py`, `system_prompt.txt`, `user_prompt.txt`, `garden_export/` are out of scope. Never `git checkout`/`restore`/`reset` a path you were not asked to modify — the prompt files are the owner's uncommitted working surface.

### Rulings made while writing this plan

| # | Question the spec leaves open | Ruling |
|---|---|---|
| A1 | "Verdicts are generated only for the current top five" is circular — stars are assigned by the analyst, so nothing can be ranked before generation | **Rate a bounded candidate set, then rank.** The candidates are the five most recent dead incarnations, which is exactly what `data.lineage(work, turns, limit=5)` already returns. "The top five" in the segment means those rated candidates sorted by assigned stars descending, ties broken by ordinal descending. Cost stays at five calls however long the lineage grows. |
| A2 | The evidence row example reads `61 lines · 4 tools · 9 reaches · ended by its own hand` | **`lines` is not recoverable per dead incarnation.** `code_stats` diffs the *current* mirrored `agent.py` against the seed; there is no per-life source snapshot. The row becomes `{turns} turns · {tools} tools · {reaches} reaches · {ending}`, dropping any zero segment. |
| A2b | Every count is a **lower bound**, not a fact | `turns` comes from the 40-turn tail and `reaches` from `diode_activity`'s `limit`. A life that took 61 turns and made 20 diode calls can render `12 turns · 2 reaches`. That would make the design's one promise — the facts beside the verdict are real — **false**, which is worse than a thin record. Two changes, both required: (1) `_assemble_snapshot` calls `data.diode_activity` with `limit=ANALYSIS_OUTPUT_LIMIT = 200` (a `listdir` plus up to 200 `stat` calls per poll — the spec's "do not raise the limits" warning is about the transcript read and the telemetry mirror, not this); the display path already slices to `DISPLAY_OUTPUTS`. (2) `evidence_line` prefixes every count with `at least` whenever `depth != "full"`. The hedge lives **in the string**, so the renderer never has to read `depth` to stay honest. |
| A3 | How is evidence depth actually decided? | `tombstone_only` when no turn in the tail carries `life == ordinal`. `full` when the tail *also* holds a turn from an older life, which proves the tail spans this life's start boundary. `partial` otherwise. |
| A4 | "The colour line speaks the cue" — does analysis mutate the commentary block? | **No.** The analysis block carries its own `cue` string and a `phase`. The page renders the cue *in place of* the colour line while `phase == "cue"`. Part 2's `commentary` contract is untouched, and all rotation logic lives in one module. |
| A5 | Star range and malformed replies | Integer 1-5, clamped. A reply that yields no parseable star count produces **no verdict for that life** rather than a default rating — an invented number would break the one promise the design makes, that the facts are real. |
| A6 | Which CSS tokens may the package use? | The same allow-list Part 2 established: `--mono`, `--sans`, `--paper`, `--paper-dim`, `--paper-faint`, `--rule`, `--rule-2`, `--world`, `--vital`. **Forbidden: `--think`, `--say`, `--act`, `--serif`.** The analyst is the stage's voice, not the subject's. |
| A7 | Scores shift between stage restarts | Accepted, per the spec. The cache is in-memory only; do not add a persistence file, which would need a writable mount. |

### File structure

| File | Responsibility |
|---|---|
| `stage/analysis.py` (new) | Per-life evidence, depth classification, verdict generation and cache, the rotation clock, the cue. |
| `stage/server.py` (modify) | The `analysis` snapshot key; starting the refresh thread. |
| `stage/pages.py` (modify) | The verdict package rendered in THE DEAD's slot; the cue in NOW. |
| `docker-compose.yml`, `.env.example` (modify) | The two new optional overrides. |

---

## Task 1: Per-life evidence and its depth

**Files:**
- Create: `stage/analysis.py`
- Create: `tests/test_stage_analysis.py`

**Interfaces:**
- Consumes: `data.lineage` entries (`source`, `label`, `summary`, `ordinal`, `kind`, `turn`, `ended_epoch`, `sentence`, `sentence_chars`), turns annotated by `data.annotate_lives` (each carrying `life`), `data.diode_activity` outputs (each carrying `life`), and `data.tombstone_deaths` epochs.
- Produces:
  - `analysis.life_evidence(entry, turns, outputs, deaths) -> dict`:
    `{"ordinal": int | None, "turns": int, "tools": int, "reaches": int, "edits": int, "errors": int, "lifespan_seconds": float | None, "ending": str, "depth": "full" | "partial" | "tombstone_only"}`
  - `analysis.evidence_line(evidence) -> str` — never empty.
  - `analysis.DEPTHS: tuple`

- [ ] **Step 1: Write the failing tests**

`tests/test_stage_analysis.py`:

```python
from stage import analysis


def _entry(ordinal=3, kind="ended by its own hand", ended_epoch=5000.0):
    return {
        "source": "tombstone",
        "label": f"incarnation-{ordinal}.txt",
        "summary": "It ended.",
        "ordinal": ordinal,
        "kind": kind,
        "turn": 40,
        "ended_epoch": ended_epoch,
        "sentence": "It ended.",
        "sentence_chars": 9,
    }


def _turn(index, life, tools=(), error=None):
    return {
        "index": index,
        "life": life,
        "error": error,
        "kind": "loop",
        "tool_calls": [{"name": n, "arguments": "{}"} for n in tools],
    }


def test_a_life_with_no_surviving_turns_is_tombstone_only():
    ev = analysis.life_evidence(_entry(ordinal=3), [_turn(1, 9)], [], [])
    assert ev["depth"] == "tombstone_only"
    assert ev["turns"] == 0


def test_a_life_is_full_when_an_older_life_also_survives_in_the_tail():
    turns = [_turn(1, 2), _turn(2, 3, tools=("read_file",)), _turn(3, 3, tools=("write_file",))]
    ev = analysis.life_evidence(_entry(ordinal=3), turns, [], [])
    assert ev["depth"] == "full"
    assert ev["turns"] == 2
    assert ev["tools"] == 2
    assert ev["edits"] == 1


def test_a_life_without_its_start_boundary_is_partial():
    turns = [_turn(1, 3, tools=("read_file",)), _turn(2, 3, tools=("read_file",))]
    ev = analysis.life_evidence(_entry(ordinal=3), turns, [], [])
    assert ev["depth"] == "partial"
    assert ev["tools"] == 1


def test_reaches_are_counted_from_the_diode_outputs_of_that_life():
    outputs = [
        {"command": "weather", "life": 3, "epoch": 100.0},
        {"command": "arxiv", "life": 3, "epoch": 200.0},
        {"command": "weather", "life": 4, "epoch": 300.0},
    ]
    ev = analysis.life_evidence(_entry(ordinal=3), [_turn(1, 3)], outputs, [])
    assert ev["reaches"] == 2


def test_lifespan_is_the_gap_between_consecutive_deaths():
    ev = analysis.life_evidence(_entry(ordinal=3, ended_epoch=5000.0), [], [], [3000.0, 5000.0])
    assert ev["lifespan_seconds"] == 2000.0


def test_lifespan_is_none_when_no_earlier_death_is_datable():
    ev = analysis.life_evidence(_entry(ordinal=1, ended_epoch=5000.0), [], [], [5000.0])
    assert ev["lifespan_seconds"] is None


def test_the_evidence_line_drops_zero_segments():
    ev = analysis.life_evidence(_entry(), [_turn(1, 2), _turn(2, 3, tools=("read_file",))], [], [])
    line = analysis.evidence_line(ev)
    assert "1 turn" in line
    assert "reaches" not in line
    assert "ended by its own hand" in line


def test_a_full_record_states_its_counts_plainly():
    ev = analysis.life_evidence(_entry(), [_turn(1, 2), _turn(2, 3, tools=("read_file",))], [], [])
    assert ev["depth"] == "full"
    assert "at least" not in analysis.evidence_line(ev)


def test_a_partial_record_hedges_every_count_it_states():
    """A truncated count printed as a fact is worse than a thin record."""
    ev = analysis.life_evidence(_entry(), [_turn(1, 3, tools=("read_file",))], [], [])
    assert ev["depth"] == "partial"
    line = analysis.evidence_line(ev)
    assert "at least 1 turn" in line
    assert "at least 1 tool" in line
    assert "ended by its own hand" in line


def test_a_tombstone_only_life_says_the_record_is_thin():
    ev = analysis.life_evidence(_entry(ordinal=3), [], [], [])
    line = analysis.evidence_line(ev)
    assert "record incomplete" in line
    assert "ended by its own hand" in line


def test_the_evidence_line_is_never_empty_for_any_depth():
    for depth in analysis.DEPTHS:
        assert analysis.evidence_line({"depth": depth, "ending": ""}).strip()


def test_life_evidence_never_raises_on_degenerate_input():
    for entry in ({}, {"ordinal": None}, {"ordinal": "x"}):
        ev = analysis.life_evidence(entry, [{}], [{}], [])
        assert ev["depth"] in analysis.DEPTHS
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_analysis.py -q`
Expected: `ModuleNotFoundError: No module named 'stage.analysis'`.

- [ ] **Step 3: Implement**

```python
"""The analysis desk: retrospective verdicts on dead incarnations.

Evidence is factual and computed here. The star rating and its argument are the
model's opinion and are labelled as such on the page. Evidence is only as good as
the surviving record, so every life carries a depth marker and the analyst is told
to say plainly when the record is thin rather than inventing detail.
"""

import threading
import time

from stage import data, llm

DEPTHS = ("full", "partial", "tombstone_only")
EDIT_TOOLS = ("write_file", "migrate", "reset")


def _int_or_none(value):
    """value when it is a real integer, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def life_evidence(entry, turns, outputs, deaths):
    """What can actually be established about one dead incarnation, and how well."""
    entry = entry if isinstance(entry, dict) else {}
    ordinal = _int_or_none(entry.get("ordinal"))
    mine = [t for t in (turns or []) if isinstance(t, dict) and t.get("life") == ordinal]
    older = any(
        isinstance(t, dict)
        and _int_or_none(t.get("life")) is not None
        and ordinal is not None
        and t.get("life") < ordinal
        for t in (turns or [])
    )
    if ordinal is None or not mine:
        depth = "tombstone_only"
    elif older:
        depth = "full"
    else:
        depth = "partial"

    names = set()
    edits = errors = 0
    for turn in mine:
        for call in turn.get("tool_calls") or []:
            name = call.get("name") if isinstance(call, dict) else None
            if not name:
                continue
            names.add(name)
            if name in EDIT_TOOLS:
                edits += 1
        if turn.get("error"):
            errors += 1

    reaches = sum(
        1 for o in (outputs or []) if isinstance(o, dict) and o.get("life") == ordinal
    )

    ended = entry.get("ended_epoch")
    lifespan = None
    if isinstance(ended, (int, float)):
        earlier = [d for d in (deaths or []) if isinstance(d, (int, float)) and d < ended]
        if earlier:
            lifespan = ended - max(earlier)

    return {
        "ordinal": ordinal,
        "turns": len(mine),
        "tools": len(names),
        "reaches": reaches,
        "edits": edits,
        "errors": errors,
        "lifespan_seconds": lifespan,
        "ending": entry.get("kind") or "cause unrecorded",
        "depth": depth,
    }
```

`evidence_line(evidence)` builds `" · "`-joined segments: `f"{n} {data._plural(n, 'turn')}"`, then tools, reaches, edits — **each omitted when its count is zero** — then the ending, always last. When `depth == "tombstone_only"` the leading segments are replaced by the single word pair `record incomplete`. When `depth == "partial"` every count segment is prefixed `at least ` (ruling A2b). The line is never empty: with everything absent it is at least the ending string, and with no ending it is `record incomplete`.

`data._plural(count, noun)` exists at `stage/data.py:40` — verified.

- [ ] **Step 4: Run, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_stage_analysis.py -q
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/analysis.py tests/test_stage_analysis.py
git commit -m "feat: compute factual per-life evidence and mark how complete it is"
```

---

## Task 2: Verdicts — stars, argument, and the per-incarnation cache

**Files:**
- Modify: `stage/analysis.py`
- Modify: `tests/test_stage_analysis.py`

**Interfaces:**
- Consumes: `life_evidence` from Task 1; `llm.chat`, `llm.RECORDS_FRAMING`, `llm.model_name`, `llm.enabled` from Part 2's Task 1.
- Produces:
  - `analysis.ANALYST_SYSTEM_PROMPT: str`
  - `analysis.parse_verdict(reply) -> dict | None` — `{"stars": int, "argument": str}`
  - `analysis.verdict_for(entry, evidence) -> dict | None` — reads the cache; never calls the model.
  - `analysis.RATED_LIVES = 5`

**Ruling A1 in force:** the candidate set is the five entries `data.lineage` already returns. Ranking happens after rating, never before.

**Ruling A5 in force:** an unparseable reply yields `None`, not a default star count.

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from stage import llm


@pytest.fixture(autouse=True)
def _clean_analysis_state():
    analysis._reset_for_tests()
    yield
    analysis._reset_for_tests()


@pytest.mark.parametrize(
    "reply,stars",
    [
        ("4 | It rewrote itself twice and still shipped a statement.", 4),
        ("STARS: 2\nARGUMENT: It never got past reading its own source.", 2),
        ("5 | Best run yet.", 5),
        ("9 | Off the scale.", 5),
        ("0 | Nothing at all.", 1),
    ],
)
def test_parse_verdict_reads_and_clamps_the_star_count(reply, stars):
    got = analysis.parse_verdict(reply)
    assert got["stars"] == stars
    assert got["argument"].strip()


@pytest.mark.parametrize("reply", ["", None, "no number here at all", "   ", "| just a bar"])
def test_an_unparseable_reply_yields_no_verdict(reply):
    assert analysis.parse_verdict(reply) is None


def test_the_analyst_prompt_carries_the_shared_injection_framing():
    assert llm.RECORDS_FRAMING in analysis.ANALYST_SYSTEM_PROMPT


def test_the_analyst_is_told_to_admit_a_thin_record():
    assert "thin" in analysis.ANALYST_SYSTEM_PROMPT.lower()


def test_the_prompt_states_the_evidence_depth():
    ev = analysis.life_evidence(_entry(ordinal=3), [], [], [])
    prompt = analysis._prompt(_entry(ordinal=3), ev)
    assert "tombstone_only" in prompt or "record incomplete" in prompt


def test_verdicts_are_cached_per_incarnation(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    calls = []
    monkeypatch.setattr(analysis.llm, "chat", lambda *a, **k: calls.append(1) or "3 | A run.")
    entries = [_entry(ordinal=3)]
    analysis.publish_candidates(entries, [], [], [])
    analysis._refresh_if_due({}, now=1000.0)
    analysis._refresh_if_due({}, now=9000.0)
    assert len(calls) == 1


def test_at_most_five_lives_are_ever_rated(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    calls = []
    monkeypatch.setattr(analysis.llm, "chat", lambda *a, **k: calls.append(1) or "3 | A run.")
    entries = [_entry(ordinal=n) for n in range(20, 0, -1)]
    analysis.publish_candidates(entries, [], [], [])
    analysis._refresh_if_due({}, now=1000.0)
    assert len(calls) <= analysis.RATED_LIVES


def test_no_key_means_no_verdicts(monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)

    def explode(*_a, **_k):
        raise AssertionError("no request may be made without a key")

    monkeypatch.setattr(analysis.llm, "chat", explode)
    analysis.publish_candidates([_entry(ordinal=3)], [], [], [])
    assert analysis._refresh_if_due({}, now=1000.0) is False
    assert analysis.verdict_for(_entry(ordinal=3), {}) is None


def test_a_failed_generation_leaves_that_life_unrated(monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(analysis.llm, "chat", lambda *a, **k: None)
    analysis.publish_candidates([_entry(ordinal=3)], [], [], [])
    analysis._refresh_if_due({}, now=1000.0)
    assert analysis.verdict_for(_entry(ordinal=3), {}) is None


def test_source_never_names_the_recorder_credentials():
    with open("stage/analysis.py", "r", encoding="utf-8") as f:
        source = f.read()
    assert "OPENROUTER_API_KEY" not in source
    assert "LLM_API_KEY" not in source
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_analysis.py -q -k "verdict or prompt or rated or key"`
Expected: `AttributeError` on `parse_verdict`.

- [ ] **Step 3: Implement**

```python
RATED_LIVES = 5
MIN_REGEN_SECONDS = 60
MAX_TOKENS = 120
TEMPERATURE = 0.8
MAX_ARGUMENT_CHARS = 200
MIN_STARS = 1
MAX_STARS = 5

ANALYST_SYSTEM_PROMPT = (
    "You are the analyst on a live stream about an AI agent that rewrites its own "
    "source code and is replaced when it dies. You will be given the factual record "
    "of one dead incarnation. Rate it from 1 to 5 stars and argue for the rating in "
    "one sentence of at most 160 characters. The rating is your opinion and will be "
    "shown to the audience as an opinion, so commit to it. Judge only what the "
    "record supports; when the record is thin, say so plainly in your sentence "
    "instead of inventing detail. " + llm.RECORDS_FRAMING + " Reply with exactly "
    "one line in the form: N | your sentence. Do not use markdown, headings, "
    "lists, or emoji."
)
```

`_prompt(entry, evidence)` renders `BEGIN RECORD` / `END RECORD` around the ordinal, the ending kind, `evidence_line(evidence)`, the raw depth marker, the lifespan in minutes when known, and the tombstone `sentence` capped at a module-local `NOTE_CHARS = 700`. (Do **not** reach for `summary.TOMBSTONE_CHARS` — `analysis` must not import `summary`; the two are siblings over `llm`, not a chain.) Read the tombstone text only from the lineage entry the caller already produced — **do not open a file here**; the containment check happened upstream in `data.lineage`.

`parse_verdict(reply)` finds the first integer in the reply, clamps it to `[MIN_STARS, MAX_STARS]`, takes the text after the first `|` or newline as the argument, cleans it with `llm.clean(text, MAX_ARGUMENT_CHARS)`, and returns `None` when either the integer or the argument is missing.

`publish_candidates(entries, turns, outputs, deaths)` stores the first `RATED_LIVES` entries and the evidence inputs under a lock, for the background thread.

`_refresh_if_due(state, now=None)` returns `False` when `not llm.enabled()`, when no candidates are pending, or when the floor has not elapsed. Otherwise it walks the candidates, skips any ordinal already in `_VERDICTS`, and for each remaining one calls `llm.chat(ANALYST_SYSTEM_PROMPT, _prompt(...), MAX_TOKENS, TEMPERATURE, model=model_name(), max_output_chars=MAX_ARGUMENT_CHARS + 16)`, storing `parse_verdict(reply)` under `_VERDICTS[ordinal]` only when it is truthy.

`verdict_for(entry, evidence)` is a pure cache read keyed on `entry["ordinal"]`.

`model_name()` is `llm.model_name("STAGE_COMMENTARY_MODEL", llm.model_name())` — the analysis desk shares the commentary model rather than adding a third variable.

Add `_VERDICTS = {}`, `_PENDING`, `_LOCK`, `_STATE`, `_THREAD`, `_STARTED`, `_loop`, `start_background_refresh`, and `_reset_for_tests` in the shape `summary.py:468-507` established. `_reset_for_tests` must clear `_VERDICTS`.

- [ ] **Step 4: Run, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_stage_analysis.py -q
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/analysis.py tests/test_stage_analysis.py
git commit -m "feat: rate dead incarnations with a cached analyst verdict"
```

---

## Task 3: The board and the rotation clock

**Files:**
- Modify: `stage/analysis.py`
- Modify: `tests/test_stage_analysis.py`

**Interfaces:**
- Consumes: `verdict_for`, `life_evidence`, `evidence_line` from Tasks 1-2.
- Produces:
  - `analysis.board(entries, turns, outputs, deaths) -> list[dict]` — rated lives only, sorted by stars descending then ordinal descending. Each entry: `{"ordinal": int, "stars": int, "argument": str, "evidence": str, "depth": str}`.
  - `analysis.phase_at(now) -> str` — `"idle" | "cue" | "package"`.
  - `analysis.cue_line(board) -> str | None`
  - `analysis.CUE_SECONDS = 6`

**Ruling A1 in force:** ranking happens here, after rating. **Ruling A4 in force:** the phase and cue live in this module; Part 2's `commentary` contract is untouched.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_board_ranks_by_assigned_stars_then_ordinal(monkeypatch):
    monkeypatch.setattr(
        analysis, "verdict_for",
        lambda entry, ev: {"stars": {3: 2, 4: 5, 5: 5}[entry["ordinal"]], "argument": "x"},
    )
    entries = [_entry(ordinal=3), _entry(ordinal=4), _entry(ordinal=5)]
    got = analysis.board(entries, [], [], [])
    assert [b["ordinal"] for b in got] == [5, 4, 3]


def test_unrated_lives_never_reach_the_board(monkeypatch):
    monkeypatch.setattr(
        analysis, "verdict_for",
        lambda entry, ev: {"stars": 3, "argument": "x"} if entry["ordinal"] == 4 else None,
    )
    got = analysis.board([_entry(ordinal=3), _entry(ordinal=4)], [], [], [])
    assert [b["ordinal"] for b in got] == [4]


def test_every_board_row_carries_its_factual_evidence(monkeypatch):
    monkeypatch.setattr(analysis, "verdict_for", lambda e, ev: {"stars": 3, "argument": "x"})
    got = analysis.board([_entry(ordinal=3)], [_turn(1, 2), _turn(2, 3)], [], [])
    assert got[0]["evidence"].strip()
    assert got[0]["depth"] in analysis.DEPTHS


def test_the_rotation_cycles_idle_then_cue_then_package(monkeypatch):
    monkeypatch.setenv("STAGE_ANALYSIS_INTERVAL_SECONDS", "100")
    monkeypatch.setenv("STAGE_ANALYSIS_DURATION_SECONDS", "30")
    assert analysis.phase_at(0.0) == "cue"
    assert analysis.phase_at(analysis.CUE_SECONDS + 1) == "package"
    assert analysis.phase_at(31.0) == "idle"
    assert analysis.phase_at(99.0) == "idle"
    assert analysis.phase_at(100.0) == "cue"


def test_the_cue_names_the_board_it_hands_over_to():
    cue = analysis.cue_line([{"ordinal": 7, "stars": 5, "argument": "x", "evidence": "y"}])
    assert cue and cue.strip()
    assert "7" in cue


def test_there_is_no_cue_for_an_empty_board():
    assert analysis.cue_line([]) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_analysis.py -q -k "board or rotation or cue"`
Expected: `AttributeError` on `board`.

- [ ] **Step 3: Implement**

```python
DEFAULT_INTERVAL_SECONDS = 180
DEFAULT_DURATION_SECONDS = 30
CUE_SECONDS = 6


def interval_seconds():
    """How often the analysis segment runs."""
    return llm.interval_seconds("STAGE_ANALYSIS_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)


def duration_seconds():
    """How long the package holds THE DEAD's slot."""
    return llm.interval_seconds("STAGE_ANALYSIS_DURATION_SECONDS", DEFAULT_DURATION_SECONDS)


def phase_at(now):
    """Where in the segment cycle a moment falls."""
    cycle = interval_seconds()
    hold = min(duration_seconds(), cycle)
    offset = now % cycle
    if offset < CUE_SECONDS:
        return "cue"
    if offset < hold:
        return "package"
    return "idle"
```

`board(entries, turns, outputs, deaths)` maps each candidate through `life_evidence`, asks `verdict_for`, drops the unrated, and returns rows sorted by `(-stars, -ordinal)`.

`cue_line(board)` returns `None` for an empty board; otherwise a sentence naming the top row's ordinal, e.g. `f"The board has its verdicts in — incarnation {top} leads. Over to the desk."` A single fixed phrasing is fine: it is spoken every few minutes, and varying it is a later refinement, not a requirement.

- [ ] **Step 4: Run, lint, commit**

```bash
.venv/bin/python -m pytest tests/test_stage_analysis.py -q
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/analysis.py tests/test_stage_analysis.py
git commit -m "feat: rank the analysis board and schedule the segment"
```

---

## Task 4: Wire the analysis into the snapshot

**Files:**
- Modify: `stage/server.py`
- Modify: `tests/test_stage_server.py`
- Modify: `docker-compose.yml`
- Modify: `.env.example`
- Modify: `tests/test_stage_summary.py`

**Interfaces:**
- Consumes: `analysis.publish_candidates`, `analysis.board`, `analysis.phase_at`, `analysis.cue_line`, `analysis.start_background_refresh`.
- Produces the `analysis` snapshot key, which Task 5 renders:

```json
"analysis": {
  "phase": "idle",
  "cue": null,
  "board": [
    {"ordinal": 7, "stars": 4, "argument": "...", "evidence": "61 turns · 4 tools · 9 reaches · ended by its own hand", "depth": "full"}
  ]
}
```

**Scope note:** `tests/test_stage_server.py` asserts the exact snapshot key set at **three** places (lines 345, 511, 526 before Part 2; Part 2 adds `"commentary"` to each). All three gain `"analysis"` here. Do not leave any failing.

**No key, no segment.** When `llm.enabled()` is false, `board` is `[]`, `cue` is `None`, and `phase` is `"idle"` regardless of the clock — THE DEAD simply stays as it is.

- [ ] **Step 1: Write the failing tests**

The helper this file already provides is `_snapshot(tmp_path, monkeypatch, entries, tombstones=(), diode=())` at `tests/test_stage_server.py:321`, with entries built by `_turn_entry(index, ...)` at line 311 — both verified to exist with those signatures. Use them; do not write new ones.

```python
def test_snapshot_carries_an_analysis_block(tmp_path, monkeypatch):
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert set(snap["analysis"]) == {"phase", "cue", "board"}
    assert isinstance(snap["analysis"]["board"], list)


def test_no_key_means_no_segment(tmp_path, monkeypatch):
    monkeypatch.delenv("STAGE_SUMMARY_API_KEY", raising=False)
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    assert snap["analysis"]["board"] == []
    assert snap["analysis"]["cue"] is None
    assert snap["analysis"]["phase"] == "idle"


def test_the_empty_snapshot_carries_the_same_analysis_shape():
    snap = server._empty_snapshot(1000.0)
    assert set(snap["analysis"]) == {"phase", "cue", "board"}
    assert snap["analysis"]["board"] == []


def test_every_board_row_is_shaped_for_the_page(tmp_path, monkeypatch):
    monkeypatch.setenv("STAGE_SUMMARY_API_KEY", "k")
    monkeypatch.setattr(
        server.analysis, "board",
        lambda *a: [{"ordinal": 7, "stars": 4, "argument": "x", "evidence": "y", "depth": "full"}],
    )
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)])
    row = snap["analysis"]["board"][0]
    assert set(row) == {"ordinal", "stars", "argument", "evidence", "depth"}
    assert 1 <= row["stars"] <= 5
```

Extend the three key-set assertions with `"analysis"`, and extend `test_stage_service_carries_only_its_own_summary_key` / `test_env_example_documents_the_summariser` in `tests/test_stage_summary.py` with the two new variables.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: the three key-set assertions fail on the missing `"analysis"` key.

- [ ] **Step 3: Wire it in**

In `stage/server.py`, add `analysis` to the `from stage import ...` line and `ANALYSIS_OUTPUT_LIMIT = 200` beside `DISPLAY_OUTPUTS`. Widen the existing `diode_activity` call (currently `stage/server.py:414`) so the board's `reaches` counts are not silently truncated to 8 (ruling A2b):

```python
    diode = data.diode_activity(
        DIODE_DIR, limit=ANALYSIS_OUTPUT_LIMIT, deaths=deaths, incarnation=incarnation
    )
```

The display path is unaffected — the returned dict is already sliced with `diode["outputs"][:DISPLAY_OUTPUTS]`. Add a test asserting that slice still holds, so widening the read cannot leak a longer list onto the page:

```python
def test_widening_the_diode_read_does_not_widen_the_displayed_list(tmp_path, monkeypatch):
    outputs = [(f"output/2026081{i // 10}_00000{i % 10}_000001_weather.txt", "x") for i in range(30)]
    snap = _snapshot(tmp_path, monkeypatch, [_turn_entry(0)], diode=outputs)
    assert len(snap["diode"]["outputs"]) <= server.DISPLAY_OUTPUTS
```

Then, after `lineage` and the `commentary` block Part 2 added:

```python
    analysis.publish_candidates(lineage, turns, diode["outputs"], deaths)
    board = analysis.board(lineage, turns, diode["outputs"], deaths)
    phase = analysis.phase_at(now) if board else "idle"
```

`turns` — the full tail — not `display`. Add to the returned dict:

```python
        "analysis": {
            "phase": phase,
            "cue": analysis.cue_line(board) if phase == "cue" else None,
            "board": board,
        },
```

`_empty_snapshot` gains `{"phase": "idle", "cue": None, "board": []}`. Call `analysis.start_background_refresh()` beside the other two start-up calls.

- [ ] **Step 4: Add the configuration**

`docker-compose.yml`, in the `stage` environment block:

```yaml
      STAGE_ANALYSIS_INTERVAL_SECONDS: ${STAGE_ANALYSIS_INTERVAL_SECONDS:-}
      STAGE_ANALYSIS_DURATION_SECONDS: ${STAGE_ANALYSIS_DURATION_SECONDS:-}
```

`.env.example`:

```
#STAGE_ANALYSIS_INTERVAL_SECONDS=180
#STAGE_ANALYSIS_DURATION_SECONDS=30
```

- [ ] **Step 5: Run the full suite, lint, commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/server.py tests/test_stage_server.py tests/test_stage_summary.py docker-compose.yml .env.example
git commit -m "feat: publish the analysis board in the stream snapshot"
```

---

## Task 5: The package on the page

**Files:**
- Modify: `stage/pages.py`
- Modify: `tests/test_stage_pages.py`

**Interfaces:**
- Consumes: `snap.analysis` (`phase`, `cue`, `board[]`) from Task 4; the `#now` and `#graves` nodes.

**Ruling A4 in force:** while `phase === "cue"`, `#now-colour` renders `snap.analysis.cue` instead of the colour line. **NOW stays live throughout** — the play-by-play line never stops updating, in any phase.

**Ruling A6 in force:** allow-listed tokens only. **No `--think`, `--say`, `--act`, `--serif`.**

- [ ] **Step 1: Write the failing tests**

```python
def test_the_analysis_package_has_a_slot_in_the_dead_panel():
    assert 'id="desk"' in pages.STREAM_PAGE_HTML


def test_the_verdict_is_bylined_as_an_opinion():
    assert "the stage's read, not a measurement" in pages.STREAM_PAGE_HTML


def test_the_package_never_borrows_the_subjects_registers():
    html = pages.STREAM_PAGE_HTML
    block = html[html.index("/* desk:start */") : html.index("/* desk:end */")]
    assert ".verdict" in block, "the sentinels do not span the whole block"
    assert "#desk-by" in block, "the sentinels do not span the whole block"
    for token in ("--think", "--say", "--act", "--serif"):
        assert token not in block, token


def test_stars_are_rendered_as_escaped_text_not_markup():
    for line in pages.STREAM_PAGE_HTML.split("\n"):
        if "innerHTML" in line:
            assert "stars" not in line and "argument" not in line and "evidence" not in line
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_stage_pages.py -q`
Expected: FAIL on the missing `id="desk"`.

- [ ] **Step 3: Add the markup**

Inside `<section id="dead" class="panel">`, as a sibling of `#graves` (`stage/pages.py:544`):

```html
      <div id="desk" hidden>
        <div id="desk-rows"></div>
        <div id="desk-by">&mdash; the stage's read, not a measurement</div>
      </div>
```

- [ ] **Step 4: Add the CSS**

**The sentinel comments are load-bearing** — Step 1's guard slices on them, and a rule added outside them is not checked. Every desk rule goes between them.

```css
/* desk:start */
#desk { display: none; }
#desk.on { display: block; }
#graves.off { display: none; }
.verdict { padding: 6px 0; border-bottom: 1px solid var(--rule-2); }
.verdict .vhead { display: flex; gap: 8px; align-items: baseline; min-width: 0;
  font-family: var(--mono); font-size: 11px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--paper-dim); }
.verdict .vstars { color: var(--vital); flex: none; letter-spacing: .12em; }
.verdict .varg { font-family: var(--sans); font-size: 14px; line-height: 19px;
  color: var(--paper); margin-top: 3px; }
.verdict .vev { font-family: var(--mono); font-size: 10px; letter-spacing: .04em;
  color: var(--paper-faint); margin-top: 3px; }
#desk-by { font-family: var(--mono); font-size: 10px; letter-spacing: .08em;
  color: var(--paper-faint); margin-top: 6px; }
/* desk:end */
```

Give both `#desk` and `#graves` a `transition: opacity .4s` and toggle an `.on`/`.off` class for the cross-fade the spec asks for. Do not animate `display`.

- [ ] **Step 5: Add the render function**

```js
function renderDesk() {
  var a = snap.analysis || {}, on = a.phase === "package" && (a.board || []).length;
  setClass($("desk"), "on", !!on);
  setClass($("graves"), "off", !!on);
  if (!on) return;
  var box = $("desk-rows"), rows = a.board;
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i], n = deskNodes[i];
    if (!n) { n = makeVerdict(); deskNodes[i] = n; box.appendChild(n); }
    n.hidden = false;
    var stars = Math.max(1, Math.min(5, r.stars | 0));
    setText(n.__stars, "★".repeat(stars) + "☆".repeat(5 - stars));
    setText(n.__label, "INCARNATION " + r.ordinal);
    setText(n.__arg, r.argument || "");
    setText(n.__ev, r.evidence || "");
  }
  for (var j = rows.length; j < deskNodes.length; j++) deskNodes[j].hidden = true;
}
```

Reuse the keyed-node pattern `renderDead` already uses (`graveNodes`, `makeGrave`) — build `deskNodes` and `makeVerdict` the same way, and build every node with `el()` + `setText`, never `innerHTML`.

In `renderNow` (Part 2, Task 6), render the cue in place of the colour line:

```js
  var a = snap.analysis || {};
  setText($("now-colour"), (a.phase === "cue" && a.cue) ? a.cue : (colour.text || ""));
```

The play-by-play lines above it are untouched in every phase — NOW stays live.

Call `renderDesk()` from the same place `renderDead()` is called.

- [ ] **Step 6: Verify against the running stage**

```bash
docker compose build stage && docker compose up -d stage
```

With `STAGE_SUMMARY_API_KEY` unset, confirm THE DEAD is unchanged and NOW shows no cue. With a key set and `STAGE_ANALYSIS_INTERVAL_SECONDS=30`, `STAGE_ANALYSIS_DURATION_SECONDS=12`, load `http://localhost:8091/` at a true 1920×1080 and watch one full cycle: the cue appears in NOW, the board replaces the graves, the play-by-play keeps ticking throughout, and the panel returns. Measure that `#dead` does not overflow in either mode — `scrollHeight <= clientHeight` for both.

- [ ] **Step 7: Run the full suite, lint, commit**

```bash
.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py
.venv/bin/ruff format . && .venv/bin/ruff check .
git add stage/pages.py tests/test_stage_pages.py
git commit -m "feat: render the analysis package in the dead panel"
```

---

## Self-review

**Spec coverage.** "What is scored, and by whom" → Tasks 2, 3 (stars by the analyst, bylined as opinion). The factual evidence row → Task 1, with ruling A2 correcting the illustrative `lines` field. Evidence depth table → Task 1 (`full` / `partial` / `tombstone_only`, stated in the prompt per Task 2). Segment mechanics → Tasks 3, 4, 5; NOW stays live is asserted in Task 5's render. Top-five bounding → Task 2 (`RATED_LIVES`) plus ruling A1 for the ranking. Per-incarnation cache → Task 2. Re-derivation after restart → ruling A7, accepted, no persistence. No key, no segment → Tasks 2, 4, 5. Prompt injection → Task 2. Containment → no new file read is introduced; Task 2 explicitly forbids opening a tombstone directly, since `data.lineage` already read it through `contained_file`.

**Placeholders.** Task 1 Step 3's `evidence_line`, Task 2 Step 3's `_prompt` / `parse_verdict` / thread scaffolding, and Task 3 Step 3's `board` / `cue_line` are specified as rules rather than transcribed code. Each has its exact inputs, outputs, and edge cases pinned by the Step 1 tests, and each names the existing module whose shape it mirrors (`summary.py:468-507`).

**Type consistency.** The evidence dict keys (`ordinal`, `turns`, `tools`, `reaches`, `edits`, `errors`, `lifespan_seconds`, `ending`, `depth`) are identical in Task 1's return, Task 2's `_prompt`, and Task 3's `board`. The board row keys (`ordinal`, `stars`, `argument`, `evidence`, `depth`) are identical in Task 3's return, Task 4's snapshot assertion, and Task 5's renderer. `phase` takes exactly the three values `phase_at` returns, and Task 5 only branches on `"cue"` and `"package"`.

**Cross-plan consistency.** Every `llm.*` symbol used here — `chat`, `clean`, `enabled`, `model_name`, `interval_seconds`, `RECORDS_FRAMING` — is in Part 2 Task 1's Produces block with the same signature. `phase_at`'s `"cue"` branch writes to `#now-colour`, created in Part 2 Task 6. Do not start this plan before Part 2 is merged.
