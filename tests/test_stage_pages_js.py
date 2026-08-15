"""Executable checks on the stream page's JavaScript.

Every other assertion about `pages.STREAM_PAGE_HTML` is a string grep, which
cannot tell a working page from a broken one: a syntax error anywhere in the
script silences the whole page — `poll()` included — while the rest of the
suite stays green. These tests run the real script through node when it is
available, so the page is checked rather than described.
"""

import json
import re
import shutil
import subprocess

import pytest

from stage import pages

NODE = shutil.which("node") or shutil.which("nodejs")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _script():
    blocks = re.findall(r"<script>(.*?)</script>", pages.STREAM_PAGE_HTML, re.S)
    assert len(blocks) == 1, "the stream page grew a second script block"
    return blocks[0]


def _spoken_block():
    """The playback block alone, so it can run without a document."""
    text = _script()
    start = text.index("var SPOKEN_MEMORY")
    end = text.index("/* ---------- beats ---------- */")
    return text[start:end]


def _provenance_block():
    """The provenance rotation plus its transport guard (setTransport), without
    the page's own setInterval(rotateProvenance, ...) call, which would leave a
    timer pending and hang the node process."""
    text = _script()
    start = text.index("var PROVENANCE_LINES")
    end = text.index("if (!REDUCED) setInterval(rotateProvenance, 20000);")
    block = text[start:end]
    start2 = text.index("function setTransport(ok) {")
    end2 = text.index("\n}\n\n/* ---------- subject ---------- */", start2) + len("\n}")
    block += "\n" + text[start2:end2]
    return block


def _lanes_block():
    """renderLanes plus the two helpers it calls (coreLane is unused by
    renderLanes itself but lives in the same sentinel block, so it comes
    along for free), without `el`, `setText`, or `norm`, which the harness
    stubs directly."""
    text = _script()
    start = text.index("/* ---------- lanes ---------- */")
    end = text.index("/* ---------- render ---------- */")
    return text[start:end]


def _ribbon_block():
    """renderRibbon plus clearRows, which shares its sentinel block. The
    formatters it calls (`pad2`, `bytes`, `hhmmss`) and the DOM helpers are
    stubbed by the harness."""
    text = _script()
    start = text.index("/* ---------- ribbon ---------- */")
    end = text.index("var SPOKEN_MEMORY")
    return text[start:end]


def _run(source, tmp_path):
    path = tmp_path / "harness.js"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(path)], capture_output=True, text=True, timeout=30, cwd=str(tmp_path)
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


HARNESS = """
var store = {};
global.window = { localStorage: {
  getItem: function (k) { return k in store ? store[k] : null; },
  setItem: function (k, v) { store[k] = String(v); }
}};
var audio = {
  src: "", listeners: {}, played: [], pending: null,
  addEventListener: function (e, f) { (this.listeners[e] = this.listeners[e] || []).push(f); },
  fire: function (e) { (this.listeners[e] || []).forEach(function (f) { f({ type: e }); }); },
  play: function () {
    audio.played.push(audio.src);
    return { catch: function (fn) { audio.pending = fn; } };
  }
};
var NOW = 1000000;
var caption = { textContent: "", classList: { toggle: function () {} } };
var other = { textContent: "", classList: { toggle: function () {} } };
global.$ = function (id) {
  if (id === "speak-audio") return audio;
  if (id === "speak-caption") return caption;
  return other;
};
global.clock = function () { return NOW; };
global.norm = function (t) { return String(t == null ? "" : t).trim(); };
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};
global.snap = null;
function stamp(n) { return "2026081" + n + "_120000_000000.mp3"; }
function entry(n, ageSeconds) {
  return { name: stamp(n), epoch: NOW / 1000 - ageSeconds, text: "u" + n };
}
function reset() {
  audio.played = []; audio.src = ""; audio.pending = null;
  spokenQueue = []; spokenBusy = false; spokenCurrent = null;
}
function names() { return audio.played.map(function (s) { return s.slice(7); }); }

__BLOCK__

var out = {};

/* One diode cycle runs a whole console batch, so one snapshot can carry two
   utterances. Both must play, oldest first. */
global.snap = { diode: { spoken: [entry(2, 1), entry(1, 2)] } };
renderSpoken();
audio.fire("playing");
out.first_started = names();
out.first_caption = caption.textContent;
renderSpoken();
out.caption_during_first = caption.textContent;
audio.fire("ended");
audio.fire("playing");
out.after_ended = names();
out.second_caption = caption.textContent;
audio.fire("ended");
out.drained_queue = spokenQueue.length;

/* A load failure fires "error" and rejects play(); advancing on both would
   shift the next utterance out of the queue without ever playing it. */
reset();
global.snap = { diode: { spoken: [entry(5, 1), entry(4, 2), entry(3, 3)] } };
renderSpoken();
var stale = audio.pending;
audio.fire("error");
if (stale) stale();
audio.fire("playing");
out.after_failure = names();
out.still_queued = spokenQueue.length;

/* A reload restores the played set from storage and resumes with only the
   queued utterance that never started. */
reset();
out.stored = JSON.parse(store.spokenPlayed || "[]").length;
spokenPlayed = {};
JSON.parse(store.spokenPlayed || "[]").forEach(function (n) { spokenPlayed[n] = true; });
renderSpoken();
out.resumed_after_reload = names();

/* Only a planted file carries a stamp in the future; it must never play. */
reset();
spokenPlayed = {};
global.snap = { diode: { spoken: [entry(9, -86400)] } };
renderSpoken();
out.future_played = audio.played.length;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_stream_page_script_parses(tmp_path):
    path = tmp_path / "page.js"
    path.write_text(_script(), encoding="utf-8")
    result = subprocess.run(
        [NODE, "--check", str(path)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr


PROVENANCE_HARNESS = """
var out = {};
var premiseEl = { textContent: "", classList: { toggle: function () {} } };
var provenanceEl = { textContent: "", classList: { toggle: function () {} } };
var clusterEl = { classList: { toggle: function () {} } };
global.announceUntil = 0;
global.$ = function (id) {
  if (id === "premise") return premiseEl;
  if (id === "provenance") return provenanceEl;
  if (id === "state-cluster") return clusterEl;
  return { textContent: "", classList: { toggle: function () {} } };
};
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};

__BLOCK__

// The module-level calls already ran above: containment line 0 is on the
// masthead (#premise) and the premise sentence is on the secondary line.
out.initial = premiseEl.textContent;
out.initial_secondary = provenanceEl.textContent;

// A genuine 20s tick while online advances the rotation on the masthead.
rotateProvenance();
out.after_manual_rotate = premiseEl.textContent;
var indexAfterRotate = provenanceAt;

// 1. setTransport(true), called on every 2s poll, must not advance the
// rotation, and must keep the premise sentence on the secondary line.
setTransport(true);
out.after_ok_1 = premiseEl.textContent;
setTransport(true);
out.after_ok_2 = premiseEl.textContent;
out.secondary_after_ok = provenanceEl.textContent;

// 2. While offline, the OFFLINE notice replaces only the secondary line;
// rotateProvenance() must not advance the index or touch the masthead.
setTransport(false);
out.offline_text = provenanceEl.textContent;
rotateProvenance();
out.offline_masthead = premiseEl.textContent;
out.index_unchanged_offline = provenanceAt === indexAfterRotate;

// 3. On recovery, the premise sentence returns and the masthead still shows
// the line that was showing -- not index 0, not OFFLINE.
setTransport(true);
out.recovered_secondary = provenanceEl.textContent;
out.recovered_masthead = premiseEl.textContent;

// 4. While a death announcement holds the masthead, the rotation must not
// overwrite it; when tick() clears announceUntil, showProvenance() resumes.
announceUntil = 1;
premiseEl.textContent = "INCARNATION 9 HAS ENDED.";
rotateProvenance();
out.announce_kept = premiseEl.textContent;
announceUntil = 0;
showProvenance();
out.after_announce = premiseEl.textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_provenance_rotation_survives_the_transport_poll(tmp_path):
    """setTransport(true) fires on every successful 2s poll. Before this test
    existed, it unconditionally rewrote the rotating line's element, which would
    have made the rotation invisible in production (overwritten within 2s of
    every 20s tick) while every string-presence test stayed green. The rotation
    now lives on the masthead (#premise) and the premise sentence on the
    secondary line (#provenance); offline handling touches only the latter."""
    lines = [
        "the agent has no network interface · one unix socket to the model, nothing else",
        "the model key lives in the recorder · the agent runs with a dummy",
    ]
    premise = (
        "A language model has been given the file that runs it. "
        "It cannot leave the box. It can end itself, and usually does."
    )
    out = _run(PROVENANCE_HARNESS.replace("__BLOCK__", _provenance_block()), tmp_path)
    assert out["initial"] == lines[0]
    assert out["initial_secondary"] == premise
    assert out["after_manual_rotate"] == lines[1]
    # setTransport(true) does not advance the rotation.
    assert out["after_ok_1"] == lines[1]
    assert out["after_ok_2"] == lines[1]
    assert out["secondary_after_ok"] == premise
    # Offline: notice on the secondary line only; no advance, masthead untouched.
    assert out["offline_text"] == "STAGE OFFLINE · this page cannot reach the stage"
    assert out["offline_masthead"] == lines[1]
    assert out["index_unchanged_offline"] is True
    # Recovery restores the premise sentence; the masthead line never moved.
    assert out["recovered_secondary"] == premise
    assert out["recovered_masthead"] == lines[1]
    # A death announcement is never overwritten mid-beat; once cleared, the
    # rotation resumes at the index the silent tick advanced it to.
    assert out["announce_kept"] == "INCARNATION 9 HAS ENDED."
    assert (
        out["after_announce"]
        == "it can rewrite every line of itself · it cannot reach the machine it runs on"
    )


@needs_node
def test_playback_queue_behaviour(tmp_path):
    out = _run(HARNESS.replace("__BLOCK__", _spoken_block()), tmp_path)
    # Oldest first, one at a time: the newer utterance waits for "ended".
    assert out["first_started"] == ["20260811_120000_000000.mp3"]
    assert out["first_caption"] == "u1"
    assert out["caption_during_first"] == "u1"
    assert out["after_ended"] == [
        "20260811_120000_000000.mp3",
        "20260812_120000_000000.mp3",
    ]
    assert out["second_caption"] == "u2"
    assert out["drained_queue"] == 0
    # One failure advances exactly one place, and the third stays queued.
    assert out["after_failure"] == [
        "20260813_120000_000000.mp3",
        "20260814_120000_000000.mp3",
    ]
    assert out["still_queued"] == 1
    # A reload skips started/failed names but resumes the item that was still queued.
    assert out["stored"] > 0
    assert out["resumed_after_reload"] == ["20260815_120000_000000.mp3"]
    # A future-dated stamp is as dead as a stale one.
    assert out["future_played"] == 0


SOUND_HARNESS = """
var store = {};
global.window = { localStorage: {
  getItem: function (k) { return k in store ? store[k] : null; },
  setItem: function (k, v) { store[k] = String(v); }
}};
var audio = {
  src: "", listeners: {}, played: [], pending: null,
  addEventListener: function (e, f) { (this.listeners[e] = this.listeners[e] || []).push(f); },
  fire: function (e) { (this.listeners[e] || []).forEach(function (f) { f({ type: e }); }); },
  play: function () {
    audio.played.push(audio.src);
    return { catch: function (fn) { audio.pending = fn; } };
  }
};
var soundBtn = { hidden: true, __wired: false, clicks: [] };
soundBtn.addEventListener = function (e, f) { if (e === "click") this.clicks.push(f); };
var caption = { textContent: "", classList: { toggle: function () {} } };
var other = { textContent: "", classList: { toggle: function () {} } };
var NOW = 1000000;
global.$ = function (id) {
  if (id === "speak-audio") return audio;
  if (id === "speak-caption") return caption;
  if (id === "sound-on") return soundBtn;
  return other;
};
global.clock = function () { return NOW; };
global.norm = function (t) { return String(t == null ? "" : t).trim(); };
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};
global.snap = null;
function stamp(n) { return "2026081" + n + "_120000_000000.mp3"; }
function entry(n, ageSeconds) {
  return { name: stamp(n), epoch: NOW / 1000 - ageSeconds, text: "u" + n };
}
function reset() {
  audio.played = []; audio.src = ""; audio.pending = null;
  spokenQueue = []; spokenBusy = false; spokenCurrent = null;
  soundBtn.hidden = true; soundBtn.__wired = false; soundBtn.clicks = [];
}

__BLOCK__

var out = {};

/* NotAllowedError is a refused autoplay: the recovery button is revealed,
   while the current and queued utterances remain recoverable. */
global.snap = { diode: { spoken: [entry(2, 1), entry(1, 2)] } };
renderSpoken();
audio.pending({ name: "NotAllowedError" });
out.allowed_button_hidden = soundBtn.hidden;
out.allowed_current = spokenCurrent && spokenCurrent.name;
out.allowed_queued = spokenQueue.length;
out.allowed_persisted = JSON.parse(store.spokenPlayed || "[]").length;

/* A reload before the viewer enables sound starts with the same oldest item,
   because refused playback was never persisted as completed. */
reset();
spokenPlayed = {};
JSON.parse(store.spokenPlayed || "[]").forEach(function (n) { spokenPlayed[n] = true; });
renderSpoken();
out.reload_current = spokenCurrent && spokenCurrent.name;
if (audio.pending) audio.pending({ name: "NotAllowedError" });
if (soundBtn.clicks[0]) soundBtn.clicks[0]();
out.after_click = audio.played.slice();
audio.fire("playing");
out.persisted_after_playing = JSON.parse(store.spokenPlayed || "[]").length;
audio.fire("ended");
out.after_enabled_advanced_to = spokenCurrent && spokenCurrent.name;

/* NotSupportedError is a load failure (a missing or mid-write file), not a
   permission problem sound would fix. Revealing the button here would plant
   a dead control on a broadcast with no pointer to hide it again, so it
   must stay hidden -- but the queue must still drain either way. */
reset();
global.snap = { diode: { spoken: [entry(4, 1), entry(3, 2)] } };
renderSpoken();
audio.pending({ name: "NotSupportedError" });
out.unsupported_button_hidden = soundBtn.hidden;
out.unsupported_advanced_to = spokenCurrent && spokenCurrent.name;

/* An unrecognised rejection reason fails closed: hidden stays hidden, and
   the queue still drains. */
reset();
global.snap = { diode: { spoken: [entry(6, 1), entry(5, 2)] } };
renderSpoken();
audio.pending({});
out.unrecognised_button_hidden = soundBtn.hidden;
out.unrecognised_advanced_to = spokenCurrent && spokenCurrent.name;

/* The click listener is wired once, not on every hidden->shown transition:
   simulate two separate reveals (a click in between would set hidden back
   to true, which is all soundBlocked's guard looks at) and confirm the
   listener count does not grow past one. */
soundBtn.hidden = true; soundBtn.clicks = []; soundBtn.__wired = false;
soundBlocked();
out.clicks_after_first_reveal = soundBtn.clicks.length;
soundBtn.hidden = true;
soundBlocked();
out.clicks_after_second_reveal = soundBtn.clicks.length;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_a_load_failure_never_reveals_sound_on_but_still_drains_the_queue(tmp_path):
    """The .catch on a.play() runs for two different rejection reasons: a
    refused autoplay (NotAllowedError) and a load failure (NotSupportedError,
    e.g. a diode audio file mid-write or one that rotated away between
    snapshot and fetch). A string-presence test cannot see which reasons the
    handler discriminates between -- "soundBlocked" appears in the source
    either way -- only running the rejection through the real handler with a
    real reason object does. A version that called soundBlocked()
    unconditionally would reveal the button on an OBS broadcast (no pointer,
    no way to hide it again) whenever a spoken file failed to load; this
    proves it does not. A refused autoplay is instead held for a viewer click
    and survives reload; non-permission failures still drain."""
    out = _run(SOUND_HARNESS.replace("__BLOCK__", _spoken_block()), tmp_path)
    assert out["allowed_button_hidden"] is False
    assert out["allowed_current"] == "20260811_120000_000000.mp3"
    assert out["allowed_queued"] == 1
    assert out["allowed_persisted"] == 0
    assert out["reload_current"] == "20260811_120000_000000.mp3"
    assert out["after_click"] == [
        "/audio/20260811_120000_000000.mp3",
        "/audio/20260811_120000_000000.mp3",
    ]
    assert out["persisted_after_playing"] == 1
    assert out["after_enabled_advanced_to"] == "20260812_120000_000000.mp3"
    assert out["unsupported_button_hidden"] is True
    assert out["unsupported_advanced_to"] == "20260814_120000_000000.mp3"
    assert out["unrecognised_button_hidden"] is True
    assert out["unrecognised_advanced_to"] == "20260816_120000_000000.mp3"
    assert out["clicks_after_first_reveal"] == 1
    assert out["clicks_after_second_reveal"] == 1


LANES_HARNESS = """
function makeNode(tag) {
  var n = {
    tagName: tag, className: "", textContent: "", children: [],
    appendChild: function (child) { this.children.push(child); },
    removeChild: function (child) {
      var idx = this.children.indexOf(child);
      if (idx >= 0) this.children.splice(idx, 1);
    }
  };
  Object.defineProperty(n, "lastChild", {
    get: function () { return this.children[this.children.length - 1]; }
  });
  return n;
}
var host = makeNode("div");
var streamCount = { textContent: "" };
var streamFoot = { textContent: "" };
global.$ = function (id) {
  if (id === "stream-rows") return host;
  if (id === "stream-count") return streamCount;
  if (id === "stream-foot") return streamFoot;
  return { textContent: "" };
};
global.el = function (tag, cls, parent) {
  var n = makeNode(tag);
  if (cls) n.className = cls;
  if (parent) parent.appendChild(n);
  return n;
};
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); return true; };
global.norm = function (t) { return String(t == null ? "" : t).replace(/\\s+/g, " ").trim(); };
global.snap = null;
function lane(name, opts) {
  opts = opts || {};
  return {
    name: name,
    bound: opts.bound !== false,
    in_flight: opts.in_flight || 0,
    requests_hour: opts.requests_hour || 0,
    tokens_hour: opts.tokens_hour || 0
  };
}
function others(n) {
  var list = [];
  for (var i = 1; i <= n; i++) list.push(lane("built-" + i));
  return list;
}

__BLOCK__

var out = {};

/* Nine declared lanes -- core plus eight built -- with core among the first
   six the grid renders. */
global.snap = { lanes: [lane("core")].concat(others(8)) };
renderLanes();
out.count_core_first = streamCount.textContent;
out.foot_core_first = streamFoot.textContent;
out.rows_core_first = host.children.length;

/* Same nine lanes, but core sits at index 8 -- past the six-lane slice --
   so a version that hardcodes "1 GIVEN" (or counts BUILT only over the
   rendered rows) would report a different figure than the case above. */
host = makeNode("div");
global.snap = { lanes: others(8).concat([lane("core")]) };
renderLanes();
out.count_core_last = streamCount.textContent;
out.foot_core_last = streamFoot.textContent;
out.rows_core_last = host.children.length;

/* The server caps the list it sends and reports what it dropped, so the page
   sees nine lanes and a count of four more it never received. */
host = makeNode("div");
global.snap = { lanes: [lane("core")].concat(others(8)), lanes_omitted: 4 };
renderLanes();
out.count_omitted = streamCount.textContent;
out.foot_omitted = streamFoot.textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_given_built_figure_counts_every_declared_lane_not_just_the_shown_rows(tmp_path):
    """renderLanes shows at most 6 lanes but #stream-count's GIVEN/BUILT figure is
    a claim about every lane the agent declared, not just the ones the grid has
    room for. A string-presence test cannot see this: the bug was in what the
    two numbers were computed *from* (the sliced `shown` array instead of the
    full `lanes` list), which no grep on the page's source text distinguishes
    from a correct version -- only running the function catches it. The second
    case (core past the slice) additionally catches a hardcoded "1 GIVEN"
    literal, which the first case alone would not: it stays right by
    coincidence when core happens to be lane 0."""
    out = _run(LANES_HARNESS.replace("__BLOCK__", _lanes_block()), tmp_path)
    assert out["count_core_first"] == "1 GIVEN · 8 BUILT"
    assert out["foot_core_first"] == "3 more streams not shown"
    assert out["rows_core_first"] == 6
    assert out["count_core_last"] == "1 GIVEN · 8 BUILT"
    assert out["foot_core_last"] == "3 more streams not shown"
    assert out["rows_core_last"] == 6


@needs_node
def test_lanes_the_server_capped_are_disclosed_alongside_the_ones_the_grid_hides(tmp_path):
    """The server caps its lane list too, so `lanes` is not the whole set either.
    Counting only the grid's own overflow understates what is hidden, and the
    GIVEN/BUILT figure -- a claim about every declared lane -- understates BUILT."""
    out = _run(LANES_HARNESS.replace("__BLOCK__", _lanes_block()), tmp_path)
    assert out["foot_omitted"] == "7 more streams not shown"
    assert out["count_omitted"] == "1 GIVEN · 12 BUILT"


RIBBON_HARNESS = """
function makeNode(tag) {
  var n = {
    tagName: tag, className: "", textContent: "", scrollHeight: 0, children: [],
    classList: { toggle: function () {} },
    appendChild: function (child) { this.children.push(child); },
    removeChild: function (child) {
      var idx = this.children.indexOf(child);
      if (idx >= 0) this.children.splice(idx, 1);
    }
  };
  Object.defineProperty(n, "firstChild", {
    get: function () { return this.children.length ? this.children[0] : null; }
  });
  return n;
}
var nodes = {};
global.$ = function (id) {
  if (!nodes[id]) nodes[id] = makeNode("div");
  return nodes[id];
};
global.el = function (tag, cls, parent) {
  var n = makeNode(tag);
  if (cls) n.className = cls;
  if (parent) parent.appendChild(n);
  return n;
};
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); };
global.setClass = function () {};
global.norm = function (t) { return String(t == null ? "" : t).replace(/\\s+/g, " ").trim(); };
global.pad2 = function (n) { return String(n); };
global.bytes = function (n) { return String(n); };
global.hhmmss = function () { return "00:00:00"; };

__BLOCK__

/* Ten ordinary commands and one publish, of which the server sends four rows.
   A page counting the rows it was sent stops at four; publish files a result
   in output/ like any other command, so published_total is already inside
   operations_total and adding it counts the same reach twice. */
function outputs(n) {
  var list = [];
  for (var i = 0; i < n; i++) {
    list.push({ command: "weather", slug: "weather", verb: "read the weather",
                argument: "", epoch: 1000 + i, size: 10, life: 3 });
  }
  return list;
}
global.snap = {
  stats: { incarnation: 3 },
  events: [],
  diode: {
    outputs: outputs(4),
    operations_total: 11,
    operations_life: 6,
    published: [{ epoch: 1000, text: "hello" }],
    published_total: 1,
    spoken: [],
    spoken_total: 2
  }
};
renderRibbon();
var out = {
  reached_count: nodes["reached-count"].textContent,
  reached_foot: nodes["reached-foot"].textContent,
  rows: nodes["reached-rows"].children.length
};

/* Nothing filed at all: the panel must still say so rather than name a count. */
nodes = {};
global.snap = {
  stats: { incarnation: 1 },
  events: [],
  diode: { outputs: [], operations_total: 0, operations_life: 0,
           published: [], published_total: 0, spoken: [], spoken_total: 0 }
};
renderRibbon();
out.quiet_count = nodes["reached-count"].textContent;
out.quiet_foot = nodes["reached-foot"].textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_reach_counts_come_from_the_uncapped_totals_not_the_rendered_rows(tmp_path):
    """`outputs` carries at most DISPLAY_OUTPUTS rows, so counting it froze both
    figures at four. published and spoken each file a result in output/ too, so
    adding their totals on top counted those reaches twice. Only running the
    function catches this: the page's source text looks the same either way."""
    out = _run(RIBBON_HARNESS.replace("__BLOCK__", _ribbon_block()), tmp_path)
    assert out["rows"] == 4
    assert out["reached_count"] == "6 THIS LIFE"
    assert out["reached_foot"] == "11 times across every life"
    assert out["quiet_count"] == "0 THIS LIFE"
    assert out["quiet_foot"] == "It has never reached outside the box."
