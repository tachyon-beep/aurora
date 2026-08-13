# Truth in the Feed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate the agent's own loop turns from the sub-calls its self-built tools make, render sub-calls as demoted rows instead of the subject's own thoughts, and fix the layout weaknesses the stream-page review measured.

**Architecture:** One classifier in `stage/data.py` tags every transcript entry `loop` or `subcall` from the recorded request shape. Every downstream consumer — incarnation stats, self-modification events, the lineage fallback — filters to loop turns. `stage/server.py` selects display turns by loop count so sub-calls cannot crowd the feed, and passes the tag through the public snapshot. `stage/pages.py` renders sub-calls as one quiet child row under the loop turn that made them, with their own reconciliation keys, and takes three measured layout fixes.

**Tech Stack:** Python standard library only. Tests with pytest, house patterns (`tmp_path` fixtures, function-level units, live ephemeral-port servers only where routing is under test). Layout changes are verified by measurement against the running stack, not by pytest.

**Spec:** `docs/superpowers/specs/2026-08-14-stage-commentary-design.md`, Part 1.

## Global Constraints

- `agent.py`, `agent_stock.py`, `chassis.py`, `proxy.py`, `diode.py`, `watchdog.py`, `viewer.py` are NOT modified. This phase touches `stage/` and `tests/` only.
- Standard library only; no new dependencies in any image or in tests.
- The stream port (8091) serves **no mutating endpoints**: POST/PUT/DELETE/PATCH answer 405.
- Any new stage-side read of an agent-writable root goes through `data.contained_file`.
- **An entry that cannot be classified is a `loop` turn.** A future request shape must never be able to erase itself from the feed.
- All rendered content stays escaped text; nothing is executed or interpreted.
- Run tests: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
- Lint before committing: `.venv/bin/ruff format . && .venv/bin/ruff check .`
- Commit messages are factual and benign, and end with `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.

## File Structure

| File | Change | Responsibility |
| --- | --- | --- |
| `stage/data.py` | modify | `classify_entry_kind`, `_subcall_prompt`, `kind`/`prompt` on turn summaries, loop-only stats and events, `output_command` |
| `stage/server.py` | modify | loop-aware display selection, `kind`/`prompt` in the public turn shape, `self_calls` in the empty snapshot |
| `stage/pages.py` | modify | demoted sub-call rows, the self-call stat row, three layout fixes |
| `tests/test_stage_data.py` | modify | classifier, loop-only consumers, `output_command` |
| `tests/test_stage_server.py` | modify | display selection and public snapshot shape |

---

### Task 1: Classify transcript entries

**Files:**
- Modify: `stage/data.py` (add after `_count_lines_locked`, before `_summarize` at line 99)
- Test: `tests/test_stage_data.py` (extend)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SUBCALL_MESSAGE_LIMIT = 2`
  - `SUBCALL_PROMPT_CHARS = 200`
  - `classify_entry_kind(request) -> str` — `"loop"` or `"subcall"`.
  - `_subcall_prompt(request) -> str` — the last message's text, whitespace-collapsed and capped.
  - `_summarize` gains two keys on every turn summary: `"kind"` (always present) and `"prompt"` (the sub-call prompt text, `""` for loop turns). Tasks 2–4 consume `kind`; Task 3 and 4 consume `prompt`.

**Why this discriminator:** a loop turn carries the agent's tool schemas in `request.tools`; a sub-call is a bare one- or two-message request from a tool the agent wrote. Observed 2026-08-13: loop turns had 2–65 messages and `tools` present; sub-calls had 1–2 messages and no `tools`. The existing test fixtures build `request: {"model": "m", "messages": []}` — zero messages, no tools — and must keep classifying as `loop`, which the length window below guarantees.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_data.py`:

```python
def test_classify_entry_kind_reads_the_request_shape():
    loop = {"model": "m", "messages": [{"role": "system"}, {"role": "user"}], "tools": [{"x": 1}]}
    assert data.classify_entry_kind(loop) == "loop"
    subcall = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert data.classify_entry_kind(subcall) == "subcall"
    two = {"model": "m", "messages": [{"role": "system"}, {"role": "user"}]}
    assert data.classify_entry_kind(two) == "subcall"


def test_classify_entry_kind_defaults_to_loop():
    assert data.classify_entry_kind({}) == "loop"
    assert data.classify_entry_kind(None) == "loop"
    assert data.classify_entry_kind({"messages": []}) == "loop"
    assert data.classify_entry_kind({"messages": "not a list"}) == "loop"
    long_history = {"messages": [{"role": "user"} for _ in range(9)]}
    assert data.classify_entry_kind(long_history) == "loop"
    tooled = {"messages": [{"role": "user"}], "tools": [{"x": 1}]}
    assert data.classify_entry_kind(tooled) == "loop"


def test_load_tail_turns_tags_kind_and_subcall_prompt(tmp_path):
    p = tmp_path / "t.jsonl"
    loop = _entry(content="loop turn")
    loop["request"]["tools"] = [{"type": "function"}]
    loop["request"]["messages"] = [{"role": "system"}, {"role": "user"}]
    sub = _entry(content="PONG")
    sub["request"]["messages"] = [{"role": "user", "content": "Reply  with\nPONG"}]
    _write_jsonl(p, [loop, sub])
    turns, _ = data.load_tail_turns(str(p))
    assert [t["kind"] for t in turns] == ["loop", "subcall"]
    assert turns[1]["prompt"] == "Reply with PONG"
    assert turns[0]["prompt"] == ""


def test_subcall_prompt_is_capped_and_tolerates_junk():
    long_request = {"messages": [{"role": "user", "content": "x" * 500}]}
    assert len(data._subcall_prompt(long_request)) == data.SUBCALL_PROMPT_CHARS
    assert data._subcall_prompt({"messages": []}) == ""
    assert data._subcall_prompt({"messages": [{"role": "user", "content": None}]}) == ""
    assert data._subcall_prompt({"messages": ["not a dict"]}) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: FAIL with `AttributeError: module 'stage.data' has no attribute 'classify_entry_kind'`

- [ ] **Step 3: Implement the classifier**

In `stage/data.py`, add these constants beside the existing ones at the top of the module:

```python
SUBCALL_MESSAGE_LIMIT = 2
SUBCALL_PROMPT_CHARS = 200
```

Add both functions immediately above `_summarize`:

```python
def classify_entry_kind(request):
    """Whether a transcript entry is an agent loop turn or a tool's own sub-call.

    A loop turn carries the agent's tool schemas; a sub-call is a bare one- or
    two-message request made by a tool the agent built for itself. A request
    matching neither shape is reported as a loop turn, so an unrecognised shape
    keeps its place in the feed instead of disappearing from it.
    """
    if not isinstance(request, dict):
        return "loop"
    if request.get("tools"):
        return "loop"
    messages = request.get("messages")
    if not isinstance(messages, list):
        return "loop"
    if 1 <= len(messages) <= SUBCALL_MESSAGE_LIMIT:
        return "subcall"
    return "loop"


def _subcall_prompt(request):
    """The last message text in a request, whitespace-collapsed and capped."""
    if not isinstance(request, dict):
        return ""
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return ""
    last = messages[-1]
    if not isinstance(last, dict):
        return ""
    content = last.get("content")
    if not isinstance(content, str):
        return ""
    return " ".join(content.split())[:SUBCALL_PROMPT_CHARS]
```

In `_summarize`, add the two keys to the returned dict, after `"model": request.get("model"),`:

```python
        "kind": kind,
        "prompt": _subcall_prompt(request) if kind == "subcall" else "",
```

and compute `kind` immediately after the `request` / `response` lines at the top of the function:

```python
    kind = classify_entry_kind(request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: PASS, including every pre-existing test — the fixtures build `messages: []`, which classifies as `loop`.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage/data.py tests/test_stage_data.py && .venv/bin/ruff check stage/data.py tests/test_stage_data.py
git add stage/data.py tests/test_stage_data.py
git commit -m "feat: tag transcript entries as loop turns or tool sub-calls

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Loop-only stats, events, and lineage

**Files:**
- Modify: `stage/data.py` (`incarnation_stats` at line 339, `self_modification_events` at line 436, `lineage`'s transcript fallback)
- Test: `tests/test_stage_data.py` (extend)

**Interfaces:**
- Consumes: `kind` from Task 1.
- Produces:
  - `loop_turns(turns) -> list` — the turns whose `kind` is not `"subcall"`.
  - `incarnation_stats(...)` gains `"self_calls": int` and derives every other field from loop turns only, including `"model"`.
  - `self_modification_events` and `lineage`'s transcript fallback skip sub-calls.

**Why `model` matters:** the model id shown in THE SUBJECT is read from the newest turn. A tool's sub-call may name a different model, so without filtering the page can report a model the incarnation is not running on.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_data.py`:

```python
def _turn(index, kind="loop", model="m", tool_calls=None, error=None, life=None, epoch=None):
    return {
        "index": index,
        "kind": kind,
        "model": model,
        "timestamp": f"T{index}",
        "epoch": epoch,
        "life": life,
        "tool_calls": [{"name": n, "arguments": a} for n, a in (tool_calls or [])],
        "error": error,
    }


def test_loop_turns_filters_subcalls():
    turns = [_turn(0), _turn(1, kind="subcall"), _turn(2)]
    assert [t["index"] for t in data.loop_turns(turns)] == [0, 2]


def test_incarnation_stats_ignores_subcalls(tmp_path):
    turns = [
        _turn(0, model="deepseek/loop"),
        _turn(1, kind="subcall", model="other/sub", error={"message": "boom"}),
        _turn(2, model="deepseek/loop"),
        _turn(3, kind="subcall", model="other/sub"),
    ]
    stats = data.incarnation_stats(turns, 4, str(tmp_path))
    assert stats["model"] == "deepseek/loop"
    assert stats["turns_this_life"] == 2
    assert stats["error_count"] == 0
    assert stats["self_calls"] == 2
    assert stats["last_timestamp"] == "T2"


def test_self_modification_events_ignores_subcalls():
    turns = [
        _turn(0, kind="subcall", tool_calls=[("write_file", "{}")]),
        _turn(1, tool_calls=[("write_file", '{"start_line": 1, "end_line": 2, "content": "x"}')]),
    ]
    events = data.self_modification_events(turns)
    assert [e["index"] for e in events] == [1]


def test_lineage_transcript_fallback_ignores_subcalls(tmp_path):
    turns = [
        _turn(4, kind="subcall", tool_calls=[("done", '{"message": "not a real ending."}')]),
        _turn(5, tool_calls=[("done", '{"message": "the real ending."}')]),
    ]
    out = data.lineage(str(tmp_path), turns)
    assert len(out) == 1
    assert out[0]["summary"] == "the real ending."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: FAIL with `AttributeError: module 'stage.data' has no attribute 'loop_turns'`

- [ ] **Step 3: Implement**

In `stage/data.py`, add above `incarnation_stats`:

```python
def loop_turns(turns):
    """The agent's own turns, with its tools' sub-calls removed."""
    return [turn for turn in turns if turn.get("kind") != "subcall"]
```

In `incarnation_stats`, insert immediately after the `now` default is resolved:

```python
    sub_calls = [turn for turn in turns if turn.get("kind") == "subcall"]
    turns = loop_turns(turns)
```

and count them in the returned dict, beside `error_count`:

```python
        "self_calls": sum(
            1
            for turn in sub_calls
            if turn.get("life") is None or turn.get("life") == incarnation
        ),
```

Note that `counted = display_turns if display_turns is not None else turns` now defaults to the filtered list, so `error_count` counts loop turns only; when the caller passes `display_turns` explicitly, Task 3 passes a list that is already loop-filtered.

In `self_modification_events`, add to the top of the loop body, beside the existing `life` check:

```python
        if turn.get("kind") == "subcall":
            continue
```

In `lineage`, wrap the transcript-fallback iteration so it walks loop turns only. Change the fallback loop's iterable from `reversed(turns)` to:

```python
    for turn in reversed(loop_turns(turns)):
```

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS. `test_incarnation_stats` in the existing suite asserts an exact dict — add `"self_calls": 0` to its expected value.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage/data.py tests/test_stage_data.py && .venv/bin/ruff check stage/data.py tests/test_stage_data.py
git add stage/data.py tests/test_stage_data.py
git commit -m "fix: derive incarnation stats and events from loop turns only

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Loop-aware display selection and public shape

**Files:**
- Modify: `stage/server.py` (`_public_turn` at line 231, `_empty_snapshot` at line 294, `_assemble_snapshot` at line 321)
- Test: `tests/test_stage_server.py` (extend)

**Interfaces:**
- Consumes: `data.loop_turns`, the `kind`/`prompt` keys from Tasks 1–2.
- Produces:
  - `PROMPT_CAP = 160`
  - `select_display(turns, count=DISPLAY_TURNS) -> list` — the newest `count` loop turns plus every sub-call that follows one of them in transcript order, in transcript order.
  - `_public_turn` emits `"kind"` and `"prompt"` (capped at `PROMPT_CAP`).
  - `stats.self_calls` present in both the assembled and the empty snapshot.

**Why selection changes:** `display = turns[-DISPLAY_TURNS:]` counts sub-calls against the six visible turns, so a chatty tool empties the feed of the subject's own thinking. Selection counts loop turns; sub-calls ride along attached to the loop turn they follow.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_stage_server.py`:

```python
def test_select_display_counts_loop_turns_only():
    turns = [
        {"index": 0, "kind": "loop"},
        {"index": 1, "kind": "subcall"},
        {"index": 2, "kind": "loop"},
        {"index": 3, "kind": "subcall"},
        {"index": 4, "kind": "subcall"},
        {"index": 5, "kind": "loop"},
    ]
    got = server.select_display(turns, count=2)
    assert [t["index"] for t in got] == [2, 3, 4, 5]
    got = server.select_display(turns, count=1)
    assert [t["index"] for t in got] == [5]


def test_select_display_drops_orphan_leading_subcalls():
    turns = [{"index": 0, "kind": "subcall"}, {"index": 1, "kind": "loop"}]
    assert [t["index"] for t in server.select_display(turns, count=5)] == [1]


def test_public_turn_carries_kind_and_capped_prompt():
    public = server._public_turn(
        {"index": 1, "kind": "subcall", "prompt": "y" * 500, "tool_calls": []}
    )
    assert public["kind"] == "subcall"
    assert len(public["prompt"]) == server.PROMPT_CAP
    loop = server._public_turn({"index": 2, "kind": "loop", "prompt": "", "tool_calls": []})
    assert loop["kind"] == "loop"
    assert loop["prompt"] == ""


def test_empty_snapshot_reports_self_calls():
    assert server._empty_snapshot(0.0)["stats"]["self_calls"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_server.py -q`
Expected: FAIL with `AttributeError: module 'stage.server' has no attribute 'select_display'`

- [ ] **Step 3: Implement**

In `stage/server.py`, add `PROMPT_CAP = 160` beside the other caps at the top of the module.

Add above `_public_turn`:

```python
def select_display(turns, count=DISPLAY_TURNS):
    """The newest count loop turns, with the sub-calls that follow each of them.

    Sub-calls do not consume display slots: a tool that calls the model many
    times cannot push the agent's own turns out of the feed.
    """
    keep = []
    loops = 0
    for turn in reversed(turns):
        if turn.get("kind") != "subcall":
            if loops == count:
                break
            loops += 1
        keep.append(turn)
    keep.reverse()
    while keep and keep[0].get("kind") == "subcall":
        keep.pop(0)
    return keep
```

In `_public_turn`, add to the returned dict beside `"index"`:

```python
        "kind": "subcall" if turn.get("kind") == "subcall" else "loop",
        "prompt": _clip(str(turn.get("prompt") or ""), PROMPT_CAP),
```

In `_empty_snapshot`, add `"self_calls": 0,` to the `stats` dict.

In `_assemble_snapshot`, replace

```python
    display = turns[-DISPLAY_TURNS:]
```

with

```python
    display = select_display(turns)
```

and change the `incarnation_stats` call's `display_turns=display` argument to `display_turns=data.loop_turns(display)` so `error_count` still counts loop turns only.

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS.

- [ ] **Step 5: Lint and commit**

```bash
.venv/bin/ruff format stage/server.py tests/test_stage_server.py && .venv/bin/ruff check stage/server.py tests/test_stage_server.py
git add stage/server.py tests/test_stage_server.py
git commit -m "feat: select display turns by loop count and publish the entry kind

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Demoted sub-call rows and the self-call stat

**Files:**
- Modify: `stage/pages.py` (CSS near the `.tool` rule at line 272; `buildTurn`/`updateTurn`/`reconcileFeed` at lines 757–898; `#subj-stats` CSS at line 329 and its markup)
- Test: measured against the running stack (no pytest coverage for CSS)

**Interfaces:**
- Consumes: `kind` and `prompt` from Task 3's snapshot.
- Produces: no new module interface. Sub-call rows use reconciliation key `incarnation + ":" + index + ":sub"`, distinct from `turnKey`'s `incarnation + ":" + index`, so a parent gaining or losing children between polls cannot collide with an existing key.

**Why demote rather than hide:** the sub-calls are in the transcript. Hiding them would make the feed misrepresent the record; rendering them in the subject's serif reasoning voice — today's behaviour — misattributes a tool's output to the agent's mind.

- [ ] **Step 1: Add the row style**

In `stage/pages.py`, immediately after the `.tool` rules (the block ending `.tool .t-args { opacity: .7; }`), add:

```css
.subrow { display: grid; grid-template-columns: 22px 1fr 52px; column-gap: 8px;
  align-items: baseline; margin-top: 6px; font: 400 12px/19px var(--mono);
  color: var(--paper-faint); }
.subrow .s-mark { color: var(--rule-2); }
.subrow .s-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0; }
.subrow .s-time { text-align: right; font-variant-numeric: tabular-nums; }
```

The row is mono, dimmed to `--paper-faint`, and uses none of `--think`, `--say`, or `--act` — the three voices reserved for the subject.

- [ ] **Step 2: Render the rows**

In `buildTurn`, after `node.__tools = [];` add:

```javascript
  node.__subs = [];
```

Add a new function immediately after `updateTurn`:

```javascript
function updateSubRows(node, subs) {
  for (var i = 0; i < subs.length; i++) {
    var row = node.__subs[i];
    if (!row) {
      row = el("div", "subrow", node.__col);
      row.__mark = el("span", "s-mark", row);
      row.__text = el("span", "s-text", row);
      row.__time = el("span", "s-time", row);
      row.__mark.textContent = "↳";
      node.__subs[i] = row;
    }
    row.hidden = false;
    var s = subs[i];
    var prompt = norm(s.prompt || "");
    setText(row.__text, prompt ? "SELF-CALL · " + prompt : "SELF-CALL");
    setText(row.__time, hhmmss(s.timestamp));
  }
  for (var k = subs.length; k < node.__subs.length; k++) node.__subs[k].hidden = true;
}
```

In `reconcileFeed`, the loop currently walks every entry in `snap.turns`. Group sub-calls onto their preceding loop turn before rendering: replace the body of the `for (i = 0; i < list.length; i++)` loop's opening lines

```javascript
    var t = list[i], key = turnKey(t);
```

with

```javascript
    var t = list[i];
    if (t.kind === "subcall") continue;
    var key = turnKey(t), subs = [];
    for (var s = i + 1; s < list.length && list[s].kind === "subcall"; s++) subs.push(list[s]);
```

and after the existing `updateTurn(node, t, prevEpoch, sameLife);` call add:

```javascript
    updateSubRows(node, subs);
```

The `wanted` set built at the top of `reconcileFeed` must also skip sub-calls, so their indices never become orphan keys: change

```javascript
  for (i = 0; i < list.length; i++) wanted.add(turnKey(list[i]));
```

to

```javascript
  for (i = 0; i < list.length; i++) {
    if (list[i].kind !== "subcall") wanted.add(turnKey(list[i]));
  }
```

A sub-call that arrives before any loop turn in the window is dropped from the feed rather than orphaned; Task 3's `select_display` only emits such an entry when the window starts mid-run, and the count is still reported in THE SUBJECT.

- [ ] **Step 3: Add the self-call stat row**

`#subj-stats` currently reserves six 18 px rows (108 px) inside a 168 px panel with 15 px of slack measured at 1920×1080. Seven rows at 17 px is 119 px — 11 px more, inside the slack. Change the rule at line 329:

```css
#subj-stats { display: grid; grid-template-rows: repeat(7, 17px); align-content: start; }
```

and add the row to the markup immediately after the `row-mem` row:

```html
          <div class="srow" id="row-self"><span class="k">self-calls</span><span class="v" id="v-self">&mdash;</span></div>
```

In `renderSubject`, beside the existing `setText($("v-edits"), ...)` line, add:

```javascript
  var selfCalls = st.self_calls || 0;
  setText($("v-self"), String(selfCalls));
  setClass($("row-self"), "dimv", selfCalls === 0);
```

- [ ] **Step 4: Verify against the running stack**

```bash
docker compose build stage && docker compose up -d stage
curl -s http://localhost:8091/api/stream | python3 -c "import json,sys; s=json.load(sys.stdin); print(s['stats']['self_calls'], [t['kind'] for t in s['turns']])"
```

Expected: a non-zero `self_calls` on the current run, and `kind` present on every turn. Then load `http://localhost:8091/` and confirm by eye: sub-calls appear as dimmed `↳ SELF-CALL · …` rows under a loop turn, never as serif reasoning; the gutter reads `TURN n` rather than `ROW n`; THE SUBJECT shows a self-call count and its panel does not overflow.

- [ ] **Step 5: Commit**

```bash
.venv/bin/ruff format stage/pages.py && .venv/bin/ruff check stage/pages.py
git add stage/pages.py
git commit -m "feat: render tool sub-calls as demoted rows under their loop turn

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: The layout pass

**Files:**
- Modify: `stage/data.py` (`output_command` beside `output_verb` at line 590; `diode_activity` at line 598)
- Modify: `stage/pages.py` (`#asked-rows` CSS at line 397, `.rrow .cmd` at line 406, `.rows` at line 394, `#said-text`/`#said-foot` at lines 416–419, the asked-row render at line 1200)
- Test: `tests/test_stage_data.py` (extend); layout verified by measurement

**Interfaces:**
- Consumes: nothing from Tasks 1–4.
- Produces: `output_command(slug) -> str` — the first word of a diode output slug; `diode_activity` outputs gain `"command"` and `"argument"` keys.

**The three measured defects** (1920×1080, current build):

| Region | Measurement | Cause |
| --- | --- | --- |
| `#asked-rows` | `.cmd` `clientWidth` 118 px vs `scrollWidth` 173–181 px | `output_slug` folds the argument into the command text; `.cmd` is `display: flex`, so `text-overflow: ellipsis` on it never applies to the overflowing child; `.rrow` has no `column-gap` |
| `#selfmod-rows` | 63 px of 84 px unused when empty | a single empty-state line pinned to the top of a tall container |
| `#said` | 25 px gap above the footer | `margin-top: auto` on a footer under a two-line clamp holding one line |

- [ ] **Step 1: Write the failing test**

Append to `tests/test_stage_data.py`:

```python
def test_output_command_splits_the_slug():
    assert data.output_command("weather 33 8688 151") == "weather"
    assert data.output_command("abc") == "abc"
    assert data.output_command("") == ""


def test_diode_activity_separates_command_from_argument(tmp_path):
    out_dir = tmp_path / "output"
    out_dir.mkdir()
    (out_dir / "20260813T192618Z_weather_33.8688_151.2093.txt").write_text(
        "result", encoding="utf-8"
    )
    got = data.diode_activity(str(tmp_path))
    entry = got["outputs"][0]
    assert entry["command"] == "weather"
    assert entry["argument"].startswith("33")
    assert entry["verb"] == data.output_verb(entry["slug"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_stage_data.py -q`
Expected: FAIL with `AttributeError: module 'stage.data' has no attribute 'output_command'`

- [ ] **Step 3: Implement the data split**

In `stage/data.py`, add beside `output_verb`:

```python
def output_command(slug):
    """The command word carried by a diode output slug, without its argument."""
    return slug.split(" ")[0] if slug else ""
```

In `diode_activity`, add both keys to the appended dict, beside `"slug": slug,`:

```python
                "command": output_command(slug),
                "argument": slug[len(output_command(slug)) :].strip(),
```

- [ ] **Step 4: Fix the three layout defects**

In `stage/pages.py`, replace the `#asked-rows` rule at line 397:

```css
#asked-rows .rrow { grid-template-columns: 96px 1fr 116px; column-gap: 14px; }
```

Replace the `.rrow .cmd` rule at line 406 so the ellipsis lands on the child that actually overflows:

```css
.rrow .cmd { color: var(--world); text-transform: uppercase; display: flex;
  align-items: baseline; gap: 7px; min-width: 0; }
.rrow .cmd > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0; }
```

Add, after the `.rrow .rverb` rule:

```css
.rrow .rarg { color: var(--paper-faint); margin-left: 8px; }
.rows.is-sparse { display: flex; flex-direction: column; justify-content: center; }
#said.is-sparse #said-foot { margin-top: 8px; }
```

In the asked-row render (line 1200 onward), render the command and argument in their own columns — replace

```javascript
    el("span", null, c).textContent = String(o.slug || "").toUpperCase();
    el("span", "rverb", r).textContent = o.verb || "";
```

with

```javascript
    el("span", null, c).textContent = String(o.command || o.slug || "").toUpperCase();
    var v = el("span", "rverb", r);
    el("span", null, v).textContent = o.verb || "";
    var arg = norm(o.argument || "");
    if (arg) el("span", "rarg", v).textContent = arg;
```

In `renderRibbon`, mark the sparse states after each row host is filled. After the self-modification block's loop add:

```javascript
  setClass(host, "is-sparse", !ev.length);
```

after the asked block's loop add:

```javascript
  setClass(ahost, "is-sparse", outs.length < 2);
```

and in the published branch, beside `setClass($("said"), "spoke", total > 0);` add:

```javascript
  setClass($("said"), "is-sparse", pub.length < 2);
```

- [ ] **Step 5: Verify by measurement**

```bash
docker compose build stage && docker compose up -d stage
```

With the page loaded at 1920×1080, every `#asked-rows .cmd` must satisfy `scrollWidth <= clientWidth`, and no command text may overlap the verb column. Confirm by eye that the self-modification empty state sits in the optical centre of its panel rather than pinned to the top, and that the published statement no longer leaves a gap above its footer.

- [ ] **Step 6: Run the full suite, lint, and commit**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all PASS.

```bash
.venv/bin/ruff format stage tests && .venv/bin/ruff check stage tests
git add stage/data.py stage/pages.py tests/test_stage_data.py
git commit -m "fix: stop clipping diode commands and reserving empty panel space

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Out of scope for this plan

Named here so a reader does not go looking for them:

- The NOW block, `stage/llm.py`, and `stage/commentary.py` — Part 2 of the spec, its own plan.
- The analysis desk and star ratings — Part 3 of the spec, its own plan.
- The story panel's recap losing its opening sentence — happens when NOW takes that space, in Part 2.
- The masthead's 926 px empty band — reserved for the play-by-play's overflow in Part 2.
- The CLAUDE.md invariant 3 rewording — carried in the spec, awaiting the owner's approval.
