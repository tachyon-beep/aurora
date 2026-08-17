"""Executable checks on the telemetry panel's JavaScript, run through node.

The helpers block (ordering, state, copy) runs bare; the record block runs
against a small fake DOM so the reconciliation can be checked: rows keep their
identity across snapshots, an open card survives a poll, a new life is
inserted at the top, and the order and filter controls reorder in place.
"""

import json
import re
import shutil
import subprocess

import pytest

from stage import telemetry_page

NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _scripts():
    blocks = re.findall(r"<script>(.*?)</script>", telemetry_page.TELEMETRY_PAGE_HTML, re.S)
    assert len(blocks) == 3, "the telemetry page has three script blocks"
    return blocks


def _helpers_block():
    return _scripts()[0]


def _record_block():
    return _scripts()[1]


def _run(source, tmp_path):
    path = tmp_path / "harness.js"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(path)], capture_output=True, text=True, timeout=30, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


FAKE_DOM = r"""
var byId = {};
function Node(tag) {
  this.tagName = tag; this.children = []; this.parentNode = null; this.hidden = false;
  this._text = ""; this.attrs = {}; this.style = {}; this.listeners = {}; this._cls = "";
  var self = this;
  this.classList = {
    toggle: function (c, on) {
      var has = self._cls.split(" ").indexOf(c) !== -1;
      if (on == null) on = !has;
      if (on && !has) self._cls = (self._cls + " " + c).trim();
      if (!on && has) self._cls = self._cls.split(" ").filter(function (x) { return x !== c; }).join(" ");
    },
    contains: function (c) { return self._cls.split(" ").indexOf(c) !== -1; }
  };
}
Object.defineProperty(Node.prototype, "className", {
  get: function () { return this._cls; }, set: function (v) { this._cls = v; } });
Object.defineProperty(Node.prototype, "textContent", {
  get: function () {
    if (this.children.length) return this.children.map(function (c) { return c.textContent; }).join("");
    return this._text;
  },
  set: function (v) { this._text = String(v); this.children.forEach(function (c) { c.parentNode = null; }); this.children = []; }
});
Object.defineProperty(Node.prototype, "id", {
  get: function () { return this.attrs.id || ""; }, set: function (v) { this.attrs.id = v; byId[v] = this; } });
Object.defineProperty(Node.prototype, "firstChild", { get: function () { return this.children[0] || null; } });
Object.defineProperty(Node.prototype, "nextSibling", { get: function () {
  if (!this.parentNode) return null;
  var i = this.parentNode.children.indexOf(this);
  return this.parentNode.children[i + 1] || null; } });
Node.prototype.appendChild = function (n) { return this.insertBefore(n, null); };
Node.prototype.insertBefore = function (n, ref) {
  if (n.parentNode) { var p = n.parentNode; p.children.splice(p.children.indexOf(n), 1); }
  n.parentNode = this;
  var at = ref ? this.children.indexOf(ref) : this.children.length;
  if (at < 0) at = this.children.length;
  this.children.splice(at, 0, n);
  return n;
};
Node.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); if (k === "id") byId[v] = this; };
Node.prototype.getAttribute = function (k) { return this.attrs[k] == null ? null : this.attrs[k]; };
Node.prototype.addEventListener = function (e, f) { (this.listeners[e] = this.listeners[e] || []).push(f); };
Node.prototype.click = function () { (this.listeners.click || []).forEach(function (f) { f(); }); };
global.document = { createElement: function (t) { return new Node(t); },
  getElementById: function (id) { return byId[id] || null; } };
["record-body", "record-empty", "show-all", "record-omitted", "record-count", "order-status"].forEach(function (id) {
  var n = new Node("div"); n.id = id; });
var select = new Node("select"); select.id = "order"; select.value = "newest";
select.options = ["newest", "lived", "turns", "edits", "verdict"].map(function (v) {
  var o = new Node("option"); o.value = v; o.disabled = false; return o; });
"""


HELPERS_HARNESS = """
__HELPERS__
var out = {};
var lives = [
  { ordinal: 5, current: true, began_epoch: 1000, turns: 3, edits: 1, subcalls: 0, errors: 0, digest: { state: "pending" } },
  { ordinal: 4, current: false, lived_seconds: 100, turns: 10, edits: 2, errors: 0, subcalls: 1, kind: "declared",
    verdict: { stars: 3, line: "Fine.", evidence: "lived 1m", depth: "full" }, achievement: null,
    ended_epoch: 900, digest: { state: "ready", turns_shown: 10, turns_total: 10, generated_at: 1000 } },
  { ordinal: 3, current: false, lived_seconds: 300, turns: 4, edits: null, errors: 1, kind: "harness",
    verdict: null, achievement: "first to fly", ended_epoch: 800, digest: { state: "pending" } },
  { ordinal: 2, current: false, lived_seconds: null, turns: 40, edits: 9, errors: 0, kind: "unknown",
    verdict: { stars: 5, line: "Great.", evidence: "", depth: "sampled" }, achievement: "first to swim",
    ended_epoch: 500, digest: { state: "ready", turns_shown: 30, turns_total: 40, generated_at: 400 } },
  { ordinal: 1, current: false, lived_seconds: 300, turns: null, edits: null, errors: null, kind: "declared",
    verdict: null, achievement: null, ended_epoch: 200, digest: { state: "skipped" } }
];
function ords(l) { return l.map(function (x) { return x.ordinal; }); }
out.newest = ords(orderLives(lives, "newest", false));
out.lived = ords(orderLives(lives, "lived", false));
out.turns = ords(orderLives(lives, "turns", false));
out.edits = ords(orderLives(lives, "edits", false));
out.verdict = ords(orderLives(lives, "verdict", false));
out.noted = ords(orderLives(lives, "newest", true));
out.noted_verdict = ords(orderLives(lives, "verdict", true));
out.empty = ords(orderLives(null, "lived", false));
out.state = [
  stateOf({ stats: { transcript_turns: 0 }, turns: [], lineage: [] }, 1),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 0 }, turns: [], lineage: [{}] }, 1),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 0 }, turns: [], lineage: [] }, 1),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 2 }, turns: [], lineage: [] }, null),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 2 }, turns: [], lineage: [] }, 4),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 2 }, turns: [], lineage: [] }, 60),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 2 }, turns: [], lineage: [] }, 200),
  stateOf({ stats: { transcript_turns: 5, turns_this_life: 2 }, turns: [], lineage: [] }, 900)
];
out.prov = [provenanceFor(lives[1], 0), provenanceFor(lives[2], 0), provenanceFor(lives[4], 0),
  provenanceFor({ digest: { state: "off" } }, 0),
  provenanceFor({ ended_epoch: 500, digest: { state: "ready", turns_shown: 30, turns_total: 40, generated_at: 400 } }, 0)];
out.measured = [measuredLine(lives[0], 1060 * 1000), measuredLine(lives[1], 0), measuredLine(lives[4], 0)];
out.pending = [verdictPending(lives[0]), verdictPending(lives[1]), verdictPending(lives[2]), verdictPending(lives[4]),
  verdictPending({ verdict: null, current: false, digest: { state: "ready" } })];
out.glyphs = [starGlyphs(0), starGlyphs(3), starGlyphs(5)];
out.dur = [dur(59), dur(61), dur(3661)];
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_ordering_state_and_copy_helpers(tmp_path):
    out = _run(HELPERS_HARNESS.replace("__HELPERS__", _helpers_block()), tmp_path)
    assert out["newest"] == [5, 4, 3, 2, 1]
    # The current life leads every order; ties by ordinal desc; nulls last.
    assert out["lived"] == [5, 3, 1, 4, 2]
    assert out["turns"] == [5, 2, 4, 3, 1]
    assert out["edits"] == [5, 2, 4, 3, 1]
    assert out["verdict"] == [5, 2, 4, 3, 1]
    assert out["noted"] == [5, 3, 2]
    assert out["noted_verdict"] == [5, 2, 3]
    assert out["empty"] == []
    assert out["state"] == [
        "standby",
        "between",
        "standby",
        "standby",
        "live",
        "thinking",
        "quiet",
        "nosignal",
    ]
    assert out["prov"] == [
        "read 10 of 10 turns · 1m 40s after death — the stage",
        "reading pending — the stage",
        "no reading of this life — the stage",
        "no reading of this life — the stage",
        "read 30 of 40 turns — the stage",
    ]
    assert out["measured"] == [
        "alive 1m 0s · 3 turns · 1 self-edit · 0 sub-calls · 0 errors",
        "lived 1m 40s · 10 turns · 2 self-edits · 1 sub-call · 0 errors · ended by its own note",
        "lived 5m 0s · turns not counted · ended by its own note",
    ]
    assert out["pending"] == [False, False, True, False, True]
    assert out["glyphs"] == ["☆☆☆☆☆", "★★★☆☆", "★★★★★"]
    assert out["dur"] == ["59s", "1m 1s", "1h 1m"]


RECORD_HARNESS = """
__DOM__
__HELPERS__
clock = function () { return 2000 * 1000; };
__RECORD__
var out = {};
function life(o, extra) {
  var l = { ordinal: o, current: false, lived_seconds: 100 * o, turns: o * 3, edits: o, errors: 0, subcalls: 0,
    kind: "declared", verdict: null, moments: [], achievement: null, ended_epoch: 1000 + o,
    digest: { state: "pending" }, note: "Note " + o + "." };
  Object.keys(extra || {}).forEach(function (k) { l[k] = extra[k]; });
  return l;
}
lineage = { lives: [life(4, { current: true, kind: null, began_epoch: 1900 }), life(3), life(2, { achievement: "first" }), life(1)],
  lives_omitted: 0, digest_model: "vendor/x" };
renderRecord();
var body = $("record-body");
function visible() {
  return body.children.filter(function (n) { return !n.hidden; }).map(function (n) {
    return (n.className.indexOf("card-row") !== -1 ? "card:" : "") + (n.attrs.id || n.children[0].children[0].children[2].textContent);
  });
}
out.first = visible();
out.first_ordinals = Object.keys(rows).map(Number).sort();
out.card_ids = body.children.filter(function (n) { return n.className.indexOf("card-row") !== -1; }).map(function (n) { return n.attrs.id; });
out.count_line = $("record-count").textContent;
out.empty_hidden = $("record-empty").hidden;
/* Open life 3's card; then a new snapshot arrives with a new life on top. */
var row3 = rows[3];
row3.cells.disc.click();
out.open_after_click = [row3.open, row3.cells.disc.getAttribute("aria-expanded"), row3.cardTr.hidden,
  row3.cells.disc.getAttribute("aria-label")];
var tr3 = row3.tr, disc3 = row3.cells.disc;
lineage.lives.unshift(life(5, { current: true, kind: null, began_epoch: 1990 }));
lineage.lives[1].current = false; lineage.lives[1].kind = "harness"; lineage.lives[1].ended_epoch = 1990;
lineage.lives[1].verdict = { stars: 4, line: "Bold.", evidence: "lived 1m", depth: "full" };
lineage.lives[1].moments = [{ turn: 9, stars: 2, line: "later" }, { turn: 3, stars: 5, line: "earlier" }];
lineage.lives[1].digest = { state: "ready", turns_shown: 12, turns_total: 12, generated_at: 2000 };
renderRecord();
out.second = visible();
out.same_nodes = [rows[3].tr === tr3, rows[3].cells.disc === disc3];
out.still_open = [rows[3].open, rows[3].cardTr.hidden];
out.card4_moments = rows[4].card.moments.children.map(function (li) { return li.children[0].textContent + " " + li.children[2].textContent; });
out.card4_verdict = [rows[4].card.verdict.textContent, rows[4].card.evidence.textContent, rows[4].card.prov.textContent];
out.card4_eyebrow = rows[4].card.reyebrow.textContent;
out.stars4 = rows[4].cells.stars.textContent;
out.stars3 = rows[3].cells.stars.textContent;
out.stars1_pending = rows[1].cells.stars.textContent;
out.noted2 = rows[2].cells.noted.textContent;
out.live_cell = [rows[5].cells.live.textContent, rows[5].cells.lived.textContent, rows[5].cells.ending.textContent];
/* Order by most turns: 5 (current) first, then 4, 3, 2, 1 — with the filter only 2. */
orderMode = "turns"; renderRecord();
out.by_turns = visible().filter(function (v) { return v.indexOf("card:") !== 0; });
notedOnly = true; renderRecord();
out.filtered = visible().filter(function (v) { return v.indexOf("card:") !== 0; });
out.filtered_open_hidden = rows[3].cardTr.hidden;
notedOnly = false; orderMode = "newest"; renderRecord();
out.restored = visible().filter(function (v) { return v.indexOf("card:") !== 0; });
out.reopened = rows[3].cardTr.hidden;
/* Row limit: 30 dead lives show 25 until show all. */
lineage.lives = [life(40, { current: true, kind: null, began_epoch: 1990 })];
for (var o = 39; o >= 10; o--) lineage.lives.push(life(o));
renderRecord();
out.limited = visible().filter(function (v) { return v.indexOf("card:") !== 0; }).length;
out.show_all = [$("show-all").hidden, $("show-all").textContent];
showAll = true; renderRecord();
out.unlimited = visible().filter(function (v) { return v.indexOf("card:") !== 0; }).length;
lineage.lives_omitted = 7; renderRecord();
out.omitted = $("record-omitted").textContent;
/* An inexact census nulls every count: the orders that need them are disabled
   and a chosen one falls back to newest with an announcement. */
lineage = { lives: [life(3, { current: true, kind: null, began_epoch: 1990, turns: null, edits: null }),
  life(2, { turns: null, edits: null, lived_seconds: null }), life(1, { turns: null, edits: null, lived_seconds: null })],
  lives_omitted: 0 };
orderMode = "turns"; $("order").value = "turns";
renderRecord();
out.disabled = $("order").options.map(function (o) { return o.value + ":" + o.disabled; });
out.fell_back = [orderMode, $("order").value, $("order-status").textContent];
out.inexact_cells = [rows[2].cells.turns.textContent, rows[2].cells.edits.textContent];
/* Nothing dead. */
lineage = { lives: [life(1, { current: true, kind: null, began_epoch: 1990 })], lives_omitted: 0 };
renderRecord();
out.empty_shown = !$("record-empty").hidden;
out.empty_count = $("record-count").textContent;
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_record_reconciles_rows_in_place(tmp_path):
    source = (
        RECORD_HARNESS.replace("__DOM__", FAKE_DOM)
        .replace("__HELPERS__", _helpers_block())
        .replace("__RECORD__", _record_block())
    )
    out = _run(source, tmp_path)
    assert out["first"] == ["4", "3", "2", "1"]
    assert out["first_ordinals"] == [1, 2, 3, 4]
    assert out["card_ids"] == ["life-4-card", "life-3-card", "life-2-card", "life-1-card"]
    assert out["count_line"] == "3 dead lives on record"
    assert out["empty_hidden"] is True
    assert out["open_after_click"] == [True, "true", False, "Collapse incarnation 3"]
    assert out["second"] == ["5", "4", "3", "card:life-3-card", "2", "1"]
    assert out["same_nodes"] == [True, True]
    assert out["still_open"] == [True, False]
    assert out["card4_moments"] == ["turn 3 earlier", "turn 9 later"]
    assert out["card4_verdict"] == [
        "Bold.",
        "4/5 · lived 1m · whole life read",
        "read 12 of 12 turns · 10s after death — the stage",
    ]
    assert out["card4_eyebrow"] == "THE STAGE'S READING · vendor/x"
    assert out["stars4"] == "★★★★☆4/5"
    assert out["stars3"] == "pending"
    assert out["stars1_pending"] == "pending"
    assert out["noted2"] == "●noted first"
    assert out["live_cell"] == ["LIVE", "alive 10s", "live"]
    assert out["by_turns"] == ["5", "4", "3", "2", "1"]
    assert out["filtered"] == ["5", "2"]
    assert out["filtered_open_hidden"] is True
    assert out["restored"] == ["5", "4", "3", "2", "1"]
    assert out["reopened"] is False
    assert out["limited"] == 25
    assert out["show_all"] == [False, "show all 31 lives"]
    assert out["unlimited"] == 31
    assert out["omitted"] == "7 earlier lives are not listed"
    assert out["disabled"] == [
        "newest:false",
        "lived:true",
        "turns:true",
        "edits:true",
        "verdict:true",
    ]
    assert out["fell_back"] == [
        "newest",
        "newest",
        "no measured most turns to order by; newest first",
    ]
    assert out["inexact_cells"] == ["—", "—"]
    assert out["empty_shown"] is True
    assert out["empty_count"] == "0 dead lives on record"
