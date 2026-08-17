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
    timer pending and hang the node process. The interval itself is ungated
    (the rotation keeps running under reduced motion); the harness overrides
    setTimeout so the crossfade's deferred swap runs synchronously."""
    text = _script()
    start = text.index("var PROVENANCE_LINES")
    end = text.index("setInterval(rotateProvenance, 20000);")
    block = text[start:end]
    start2 = text.index("function setTransport(ok) {")
    end2 = text.index("\n}\n\n/* ---------- lineage ---------- */", start2) + len("\n}")
    block += "\n" + text[start2:end2]
    return block


def _block(start_marker, end_marker):
    text = _script()
    return text[text.index(start_marker) : text.index(end_marker)]


def _reveal_block():
    """The typewriter engine alone; turnKey, turnNodes, clock, REDUCED, and the
    timer functions are stubbed by the harness."""
    return _block("/* ---------- reveal ---------- */", "/* ---------- feed ---------- */")


def _pin_block():
    """The feed-pin guard: repin, the scroll handler, the return chip, and the
    auto-repin clock."""
    return _block("/* ---------- pin ---------- */", "/* ---------- coldstart ---------- */")


def _pulse_block():
    """setPulse alone; dur, rel, laneCount, repin, and clock are stubbed."""
    return _block("/* ---------- pulse ---------- */", "/* ---------- tick ---------- */")


def _deathgate_block():
    """The reload-safe death gate, without runMourn or the DOM beat."""
    return _block("/* death gate:start */", "/* death gate:end */")


def _lineage_block():
    """THE LINEAGE: bar model, layout, spotlight walk, record-book foot."""
    return _block("/* ---------- lineage ---------- */", "/* ---------- now ---------- */")


def _now_block():
    """renderNow alone."""
    return _block("/* ---------- now ---------- */", "/* ---------- eye ---------- */")


def _eye_block():
    """The eye card: gating, src caching, and the caption."""
    return _block("/* ---------- eye ---------- */", "/* ---------- ribbon ---------- */")


def _lanes_block():
    """renderLanes plus laneCount, which shares its sentinel block, without
    `el`, `setText`, or `norm`, which the harness stubs directly."""
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
var premiseEl = { textContent: "", style: {}, classList: { toggle: function () {} } };
var provenanceEl = { textContent: "", style: {}, classList: { toggle: function () {} } };
var clusterEl = { classList: { toggle: function () {} } };
global.announceUntil = 0;
global.REDUCED = false;
global.snap = null;
/* The crossfade defers its swap by 250ms; run it synchronously so the
   harness's assertions see the settled text. */
global.setTimeout = function (fn) { fn(); return 0; };
global.$ = function (id) {
  if (id === "premise") return premiseEl;
  if (id === "provenance") return provenanceEl;
  if (id === "state-cluster") return clusterEl;
  return { textContent: "", style: {}, classList: { toggle: function () {} } };
};
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};
global.norm = function (t) { return String(t == null ? "" : t).replace(/\\s+/g, " ").trim(); };

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


PROVENANCE_BEAT_HARNESS = """
var out = {};
var premiseEl = { textContent: "", style: {}, classList: { toggle: function () {} } };
global.announceUntil = 0;
global.REDUCED = false;
global.snap = null;
global.setTimeout = function (fn) { fn(); return 0; };
global.$ = function (id) {
  if (id === "premise") return premiseEl;
  return { textContent: "", style: {}, classList: { toggle: function () {} } };
};
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};
global.norm = function (t) { return String(t == null ? "" : t).replace(/\\s+/g, " ").trim(); };

__BLOCK__

/* A reached_out beat pulls the next rotation to the no-network line -- once.
   The same beat on the following rotation advances normally instead of
   pinning the masthead to one line. */
global.snap = { commentary: { colour: { beat: "reached_out:fetch", generated: false, text: "" } } };
rotateProvenance();
out.after_beat = premiseEl.textContent;
rotateProvenance();
out.after_beat_again = premiseEl.textContent;

/* A fresh generated colour line joins the rotation as one bylined slot. */
global.snap = { commentary: { colour: { beat: "working:", generated: true,
  text: "It settles into a loop." } } };
noteColour();
out.lines = provenanceLines().length;
provenanceAt = 4;
showProvenance();
out.colour_slot = premiseEl.textContent;

/* An ungenerated template line never joins: the byline would be a lie. */
colourSeen.key = null;
global.snap = { commentary: { colour: { beat: "working:", generated: false,
  text: "It is working through its turn." } } };
noteColour();
out.lines_ungenerated = provenanceLines().length;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_rotation_answers_beats_and_carries_a_fresh_colour_line(tmp_path):
    """After a reached_out beat the next rotation shows the no-network line;
    the preferred index is consulted once per beat so a persistent beat cannot
    pin the rotation. A fresh generated colour line joins as one slot suffixed
    with the stage's byline; a template line (generated: false) never does."""
    lines = [
        "the agent has no network interface · one unix socket to the model, nothing else",
        "the model key lives in the recorder · the agent runs with a dummy",
    ]
    out = _run(PROVENANCE_BEAT_HARNESS.replace("__BLOCK__", _provenance_block()), tmp_path)
    assert out["after_beat"] == lines[0]
    assert out["after_beat_again"] == lines[1]
    assert out["lines"] == 5
    assert out["colour_slot"] == "It settles into a loop. — the stage"
    assert out["lines_ungenerated"] == 4


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
    tagName: tag, className: "", textContent: "", style: {}, children: [],
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

/* Magnitude bars: widths scale to the busiest lane's tokens_hour, a nonzero
   lane never renders under the 4% floor, and the compact figure sits beside
   the bar. Row children: [l-dot, l-name, l-track, l-val]; the fill is the
   track's only child. */
host = makeNode("div");
global.snap = { lanes: [
  lane("core", { tokens_hour: 10000 }),
  lane("built-1", { tokens_hour: 5000 }),
  lane("built-2", { tokens_hour: 1 }),
  lane("built-3")
] };
renderLanes();
function fillOf(i) { return host.children[i].children[2].children[0].style.width; }
out.bar_busiest = fillOf(0);
out.bar_half = fillOf(1);
out.bar_floor = fillOf(2);
out.bar_zero = fillOf(3);
out.val_busiest = host.children[0].children[3].textContent;
out.name_title = host.children[0].children[1].title;

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


@needs_node
def test_the_lane_bars_scale_to_the_busiest_lane_with_a_visibility_floor(tmp_path):
    """The bar widths are the panel's whole claim now: each is a percentage of
    the busiest lane's tokens_hour, floored at 4% when nonzero so a quiet lane
    reads as quiet rather than absent, and zero renders no bar at all."""
    out = _run(LANES_HARNESS.replace("__BLOCK__", _lanes_block()), tmp_path)
    assert out["bar_busiest"] == "100.0%"
    assert out["bar_half"] == "50.0%"
    assert out["bar_floor"] == "4.0%"
    assert out["bar_zero"] == "0.0%"
    assert out["val_busiest"] == "10k"
    assert out["name_title"] == "core"


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


DIFF_HARNESS = """
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
var NOW = 1000000;
global.clock = function () { return NOW; };

__BLOCK__

var out = {};
var EXCERPT = "@@ -1,3 +1,4 @@\\n def a():\\n+    return 1\\n-    return 0";
function snapWith(epoch) {
  return {
    stats: { incarnation: 3 },
    events: [{ index: 9, kind: "write", headline: "rewrote write_file", summary: "" }],
    code: { available: true, added: 3, removed: 1,
            latest_edit: { epoch: epoch, added: 3, removed: 1, excerpt: EXCERPT } },
    diode: { outputs: [], operations_total: 0, operations_life: 0,
             published: [], published_total: 0, spoken: [], spoken_total: 0 }
  };
}

/* Twelve seconds after the stage first saw the edit, the diff itself shows:
   rows step aside, the title carries the honest caption, each excerpt line is
   one textContent span classed only by its first character. */
global.snap = snapWith(NOW / 1000 - 12);
renderRibbon();
out.title = nodes["selfmod-title"].textContent;
out.diff_hidden = nodes["selfmod-diff"].hidden;
out.rows_hidden = nodes["selfmod-rows"].hidden;
out.line_classes = nodes["selfmod-diff"].children.map(function (n) { return n.className; });
out.line_texts = nodes["selfmod-diff"].children.map(function (n) { return n.textContent; });
out.count = nodes["selfmod-count"].textContent;

/* A change that leaves the file identical to the seed is a restore, not the
   agent's edit: the harness does this after every death and on recovery. */
var restored = snapWith(NOW / 1000 - 12);
restored.code.latest_edit.restored = true;
restored.events = [];
global.snap = restored;
renderRibbon();
out.title_restored = nodes["selfmod-title"].textContent;
out.diff_hidden_restored = nodes["selfmod-diff"].hidden;

/* Sixty seconds on, the rows return and the title is restored -- with the
   count intact either way. */
global.snap = snapWith(NOW / 1000 - 60);
renderRibbon();
out.title_after = nodes["selfmod-title"].textContent;
out.diff_hidden_after = nodes["selfmod-diff"].hidden;
out.rows_hidden_after = nodes["selfmod-rows"].hidden;
out.diff_rows_after = nodes["selfmod-diff"].children.length;
out.count_after = nodes["selfmod-count"].textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_a_fresh_edit_swaps_the_rows_for_the_diff_and_back(tmp_path):
    """The 45s window is measured from when the stage first saw the edit (the
    only time it can truthfully claim); line colouring keys off the first
    character alone, so agent-authored diff text is never interpreted."""
    out = _run(DIFF_HARNESS.replace("__BLOCK__", _ribbon_block()), tmp_path)
    assert out["title"] == "WHAT IT JUST CHANGED · seen 12s ago"
    assert out["diff_hidden"] is False
    assert out["rows_hidden"] is True
    assert out["line_classes"] == ["dline d-hunk", "dline", "dline d-add", "dline d-rem"]
    assert out["line_texts"] == ["@@ -1,3 +1,4 @@", " def a():", "+    return 1", "-    return 0"]
    assert out["count"] == "1 THIS LIFE"
    assert out["title_restored"] == "SOURCE RESTORED TO SEED · seen 12s ago"
    assert out["diff_hidden_restored"] is False
    assert out["title_after"] == "WHAT IT DID TO ITSELF"
    assert out["diff_hidden_after"] is True
    assert out["rows_hidden_after"] is False
    assert out["diff_rows_after"] == 0
    assert out["count_after"] == "1 THIS LIFE"


REVEAL_HARNESS = """
var timers = [];
global.setInterval = function (fn, ms) { timers.push({ fn: fn, ms: ms, cleared: false }); return timers.length; };
global.clearInterval = function (id) { if (timers[id - 1]) timers[id - 1].cleared = true; };
global.REDUCED = false;
var NOWSEC = 1000;
global.clock = function () { return NOWSEC * 1000; };
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); };
global.turnKey = function (t) { return "1:" + t.index; };
global.turnNodes = new Map();
global.snap = null;
global.setTimeout = function () { return 0; };
var repins = 0;
global.repin = function () { repins++; };
function clampStub() {
  return { textContent: "", scrollTop: 0, scrollHeight: 500,
    classList: { entered: 0, add: function (c) { if (c === "enter") this.entered = 1; },
                 remove: function () {} } };
}
function nodeStub() {
  return {
    __think: { __clamp: clampStub(),
      classList: { tail: false, add: function (c) { if (c === "tail") this.tail = true; } } },
    __say: { __clamp: clampStub(), classList: { add: function () {} } }
  };
}

__BLOCK__

var out = {};

/* Three prior loop turns 30s apart set the cadence; the newest lands fresh.
   Budget: clamp(0.6 * 30s) = 18s. */
var turns = [
  { kind: "loop", index: 1, epoch: 910 },
  { kind: "loop", index: 2, epoch: 940 },
  { kind: "loop", index: 3, epoch: 970 },
  { kind: "loop", index: 4, epoch: 1000, reasoning: "alpha beta gamma", content: "delta epsilon" }
];
global.snap = { turns: turns };
var node = nodeStub();
turnNodes.set("1:4", node);
maybeStartReveal();
out.started = revealState.timer != null;
out.budget = revealState.budgetMs;
out.tail = node.__think.classList.tail;
out.blank_think = node.__think.__clamp.textContent;
out.scrolled_to_tail = node.__think.__clamp.scrollTop === node.__think.__clamp.scrollHeight;

/* Partway through, the prefix is word-accurate and speech waits for the
   thinking to finish. */
revealState.at = 2; renderRevealFrame();
out.partial_think = node.__think.__clamp.textContent;
out.partial_say = node.__say.__clamp.textContent;
out.say_not_entered_yet = node.__say.__clamp.classList.entered;
out.say_hidden_while_thinking = node.__say.hidden === true;
revealState.at = 4; renderRevealFrame();
out.think_done = node.__think.__clamp.textContent;
out.say_started = node.__say.__clamp.textContent;
out.say_entered = node.__say.__clamp.classList.entered;
out.say_shown_when_spoken = node.__say.hidden === false;
/* Every frame grows the newest turn, so every frame repins the feed. */
out.repins_per_frame = repins;

/* A newer turn fast-forwards the reveal to completion instantly. */
turns.push({ kind: "loop", index: 5, epoch: 1005, reasoning: "zeta" });
var node2 = nodeStub();
turnNodes.set("1:5", node2);
NOWSEC = 1005;
maybeStartReveal();
out.fast_forwarded_think = node.__think.__clamp.textContent;
out.fast_forwarded_say = node.__say.__clamp.textContent;
out.new_key = revealState.key;
out.old_timer_cleared = timers[0].cleared;
out.repins_after_finish = repins;

/* A stale newest turn renders whole: the key advances, no reveal starts,
   the clamp is never blanked. */
finishReveal();
turns.push({ kind: "loop", index: 6, epoch: 800, reasoning: "old words" });
var node3 = nodeStub();
node3.__think.__clamp.textContent = "old words";
turnNodes.set("1:6", node3);
maybeStartReveal();
out.stale_no_timer = revealState.timer == null;
out.stale_key = revealState.key;
out.stale_text_untouched = node3.__think.__clamp.textContent;

/* Reduced motion never reveals; the key still advances so nothing loops. */
REDUCED = true;
turns.push({ kind: "loop", index: 7, epoch: 1005, reasoning: "quick words" });
turnNodes.set("1:7", nodeStub());
maybeStartReveal();
out.reduced_no_timer = revealState.timer == null;
out.reduced_key = revealState.key;

/* With no cadence to measure, the budget clamps to the 3s floor. */
REDUCED = false;
global.snap = { turns: [{ kind: "loop", index: 1, epoch: 1005, reasoning: "solo" }] };
stopReveal();
turnNodes.set("1:1", nodeStub());
maybeStartReveal();
out.floor_budget = revealState.budgetMs;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_typewriter_paces_fast_forwards_and_respects_its_gates(tmp_path):
    """The reveal engine's whole contract in one run: word-accurate prefixes
    over the landed text (never invented text), think before say, tail mode
    with the scroll pinned to the end, cadence-derived budget with the 3s
    floor, instant fast-forward when a newer turn lands, and no reveal at all
    for stale turns or reduced motion."""
    out = _run(REVEAL_HARNESS.replace("__BLOCK__", _reveal_block()), tmp_path)
    assert out["started"] is True
    assert out["budget"] == 18000
    assert out["tail"] is True
    assert out["blank_think"] == ""
    assert out["scrolled_to_tail"] is True
    assert out["partial_think"] == "alpha beta"
    assert out["partial_say"] == ""
    assert out["say_not_entered_yet"] == 0
    assert out["say_hidden_while_thinking"] is True
    assert out["think_done"] == "alpha beta gamma"
    assert out["say_started"] == "delta"
    assert out["say_entered"] == 1
    assert out["say_shown_when_spoken"] is True
    assert out["repins_per_frame"] == 3
    assert out["fast_forwarded_think"] == "alpha beta gamma"
    assert out["fast_forwarded_say"] == "delta epsilon"
    assert out["new_key"] == "1:5"
    assert out["old_timer_cleared"] is True
    assert out["repins_after_finish"] == 5
    assert out["stale_no_timer"] is True
    assert out["stale_key"] == "1:6"
    assert out["stale_text_untouched"] == "old words"
    assert out["reduced_no_timer"] is True
    assert out["reduced_key"] == "1:7"
    assert out["floor_budget"] == 3000


PULSE_HARNESS = """
var NOW = 1000000;
global.clock = function () { return NOW; };
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); };
global.dur = function (s) { return Math.max(0, Math.floor(s)) + "s"; };
global.rel = function (ep, nowMs) { return Math.floor(nowMs / 1000 - ep) + "s ago"; };
global.laneCount = function (n) { return String(n); };
var repins = 0;
global.repin = function () { repins++; };
var spark = { children: [] };
for (var i = 0; i < 20; i++) spark.children.push({ style: {} });
var pulse = { hidden: true };
var left = { textContent: "" }, rate = { textContent: "" };
global.$ = function (id) {
  if (id === "pulse") return pulse;
  if (id === "pulse-spark") return spark;
  if (id === "pulse-left") return left;
  if (id === "pulse-rate") return rate;
  return { textContent: "" };
};
global.snap = null;

__BLOCK__

var out = {};
var buckets = [];
for (var b = 0; b < 20; b++) buckets.push(0);
buckets[18] = 500; buckets[19] = 1000;

/* In flight: lane and elapsed on the left, bars normalised to the window
   max, tokens-per-minute on the right (tokens_window over ten minutes). */
global.snap = { pulse: {
  in_flight: [{ lane: "core", since_epoch: NOW / 1000 - 12 }],
  buckets: buckets, requests_window: 3, tokens_window: 1230,
  last_close_epoch: NOW / 1000 - 40 } };
setPulse("thinking");
out.visible = !pulse.hidden;
out.left_inflight = left.textContent;
out.rate = rate.textContent;
out.h_max = spark.children[19].style.height;
out.h_half = spark.children[18].style.height;
out.h_zero = spark.children[0].style.height;
out.repins_on_show = repins;

/* A nonzero bucket can never render at 0px. */
buckets[0] = 1;
setPulse("live");
out.h_floor = spark.children[0].style.height;

/* Idle: the last close names the wait; no rate when nothing closed. */
global.snap = { pulse: { in_flight: [], buckets: buckets, requests_window: 0,
  tokens_window: 0, last_close_epoch: NOW / 1000 - 34 } };
setPulse("quiet");
out.left_idle = left.textContent;
out.rate_idle = rate.textContent;

/* standby and between hide the strip entirely. */
setPulse("standby");
out.hidden_standby = pulse.hidden;
out.repins_on_hide = repins;
setPulse("between");
out.hidden_between = pulse.hidden;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_pulse_strip_states_measured_facts_only(tmp_path):
    """The strip's three claims are each derived from snap.pulse: the real
    in-flight request with its real elapsed time, twenty bars normalised to
    the window max with a 2px floor for nonzero buckets, and tokens_window
    divided by the ten-minute window. Hidden outside the living states."""
    out = _run(PULSE_HARNESS.replace("__BLOCK__", _pulse_block()), tmp_path)
    assert out["visible"] is True
    assert out["left_inflight"] == "core · in flight 12s"
    assert out["rate"] == "≈123 tok/min"
    assert out["h_max"] == "24px"
    assert out["h_half"] == "12px"
    assert out["h_zero"] == "0px"
    assert out["repins_on_show"] == 1
    assert out["h_floor"] == "2px"
    assert out["left_idle"] == "idle · last call 34s ago"
    assert out["rate_idle"] == ""
    assert out["hidden_standby"] is True
    assert out["repins_on_hide"] == 2
    assert out["hidden_between"] is True


PIN_HARNESS = """
var NOWMS = 1000000;
global.Date = { now: function () { return NOWMS; } };
var chip = { hidden: true };
global.$ = function () { return chip; };
global.setClass = function () {};
global.expanded = new Set();
global.mScroll = { scrollTop: 1500, clientHeight: 500, scrollHeight: 2000 };

__BLOCK__

var out = {};

/* A programmatic repin flags itself, and the scroll listener consumes the
   flag instead of treating it as a person scrolling. */
mScroll.scrollTop = 1000;
repin();
out.scrolled_to_bottom = mScroll.scrollTop === 2000;
out.flag_set = programmatic;
onFeedScroll();
out.flag_consumed = !programmatic;
out.still_pinned = feedPinned;
out.chip_still_hidden = chip.hidden;

/* A person scrolling up unpins the feed and reveals the chip. */
mScroll.scrollTop = 200;
onFeedScroll();
out.unpinned = !feedPinned;
out.chip_shown = !chip.hidden;

/* Auto-repin waits out the full three minutes... */
NOWMS += 180000;
maybeAutoRepin();
out.not_yet = !feedPinned;
/* ...holds while any block is expanded... */
NOWMS += 1;
expanded.add("k");
maybeAutoRepin();
out.held_while_expanded = !feedPinned;
/* ...then repins, scrolls back, and hides the chip. */
expanded.clear();
maybeAutoRepin();
out.repinned = feedPinned;
out.chip_hidden_again = chip.hidden;
out.back_at_bottom = mScroll.scrollTop === 2000;
/* the browser delivers the programmatic scroll's own event, which consumes
   the flag -- exactly the event the guard exists to ignore */
onFeedScroll();
out.auto_repin_event_ignored = feedPinned;

/* The chip click is the same path. */
mScroll.scrollTop = 100;
onFeedScroll();
out.unpinned_again = !feedPinned;
returnToLive();
out.click_repins = feedPinned;
out.click_hides_chip = chip.hidden;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_scripted_scrolls_never_unpin_and_the_feed_always_returns_to_live(tmp_path):
    """The critical fix from the spec: an expand's scrollIntoView (and repin's
    own scroll) must not read as a user unpin, and an unattended browser
    source must always find its way back to live within three minutes."""
    out = _run(PIN_HARNESS.replace("__BLOCK__", _pin_block()), tmp_path)
    assert out["scrolled_to_bottom"] is True
    assert out["flag_set"] is True
    assert out["flag_consumed"] is True
    assert out["still_pinned"] is True
    assert out["chip_still_hidden"] is True
    assert out["unpinned"] is True
    assert out["chip_shown"] is True
    assert out["not_yet"] is True
    assert out["held_while_expanded"] is True
    assert out["repinned"] is True
    assert out["chip_hidden_again"] is True
    assert out["back_at_bottom"] is True
    assert out["auto_repin_event_ignored"] is True
    assert out["unpinned_again"] is True
    assert out["click_repins"] is True
    assert out["click_hides_chip"] is True


DEATHGATE_HARNESS = """
var store = {};
var broken = false;
global.window = { localStorage: {
  getItem: function (k) { if (broken) throw new Error("no storage"); return k in store ? store[k] : null; },
  setItem: function (k, v) { if (broken) throw new Error("no storage"); store[k] = String(v); }
}};
var NOWSEC = 1000000;
global.clock = function () { return NOWSEC * 1000; };
global.snap = null;

__BLOCK__

var out = {};

/* A reload (prev is null) with a recent death mourns once and remembers. */
global.snap = { stats: { incarnation: 8 },
  lineage: [{ ordinal: 7, ended_epoch: NOWSEC - 30 }] };
out.reload_fires = deathGate(null);
out.reload_second = deathGate(null);
out.stored = store.mournedEpoch;

/* An incarnation bump fires even when the ending epoch is unknown. */
global.snap = { stats: { incarnation: 9 }, lineage: [] };
out.bump_fires = deathGate({ stats: { incarnation: 8 } });

/* An old death on a fresh page never mourns: a reload must not fake grief. */
global.snap = { stats: { incarnation: 9 },
  lineage: [{ ordinal: 8, ended_epoch: NOWSEC - 600 }] };
out.old_death = deathGate(null);

/* Broken storage (an OBS edge case) cannot replay the beat every poll: the
   in-memory marker holds the line. */
broken = true;
mournedMem = null;
global.snap = { stats: { incarnation: 10 },
  lineage: [{ ordinal: 9, ended_epoch: NOWSEC - 10 }] };
out.broken_first = deathGate(null);
out.broken_second = deathGate(null);

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_death_gate_is_reload_safe_and_storage_proof(tmp_path):
    out = _run(DEATHGATE_HARNESS.replace("__BLOCK__", _deathgate_block()), tmp_path)
    assert out["reload_fires"] == 7
    assert out["reload_second"] is None
    assert out["stored"] == str(1000000 - 30)
    assert out["bump_fires"] == 8
    assert out["old_death"] is None
    assert out["broken_first"] == 9
    assert out["broken_second"] is None


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
/* The death slide (added by runMourn) must survive a renderSpot that follows
   in the same synchronous render pass: the kind/empty classes are toggled,
   never written wholesale over className. */
fake("spot").className = "k-declared slide";
renderSpot(NOW);
out.slide_survives = fake("spot").className.indexOf("slide") !== -1;
out.spot_kind_flag = fake("spot")["_k-declared"] === true;
out.spot_0 = fake("spot-eyebrow").textContent;
out.spot_0_facts = fake("spot-facts").textContent;
out.spot_0_note = fake("spot-note").textContent;
out.lit_0 = bars.map(function (b) { return !!b._lit; });
renderSpot(NOW + 10000);
out.spot_1 = fake("spot-eyebrow").textContent;
out.lit_1 = bars.map(function (b) { return !!b._lit; });
renderSpot(NOW + 20000);
out.spot_2_wraps = fake("spot-eyebrow").textContent.indexOf("INCARNATION 4") === 0;

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

out.steps = [labelStep(23, 366), labelStep(41, 658), labelStep(24, 658), labelStep(60, 366), labelStep(23, 0)];

/* Achievement nominations join the foot rotation, newest first, bylined,
   capped, and only when present. */
out.foot_plain = lineageFootFor({ lives_ended: 3 }, { lives_ended: 3, chose: 2 }, 3, []);
out.foot_ach = lineageFootFor({ lives_ended: 3 }, { lives_ended: 3, chose: 2 }, 3, [
  { ordinal: 3, line: "first  agent-built\\nnetwork probe" },
  { ordinal: 2, line: "b" }, { ordinal: 1, line: "c" }, { ordinal: 0, line: "d" },
  { ordinal: 5, line: "" }, null ]);

/* The census's exact self-edit count wins over the capped event list; a null
   census falls back to counting the events. */
global.snap.stats.self_edits_this_life = 23;
global.snap.events = [{ kind: "write" }];
renderLineage();
out.figs_census = fake("life-figs").textContent;
global.snap.stats.self_edits_this_life = null;
renderLineage();
out.figs_events = fake("life-figs").textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_lineage_scales_colours_caps_and_walks(tmp_path):
    out = _run(LINEAGE_HARNESS.replace("__BLOCK__", _lineage_block()), tmp_path)
    assert out["bar_count"] == 5
    assert out["bar_classes"] == [
        "bar k-declared",
        "bar k-declared",
        "bar k-harness",
        "bar k-declared",
        "bar now",
    ]
    assert out["bar_heights"] == ["2px", "50%", "75%", "100%", "50%"]
    assert out["labels"] == ["1", "2", "3", "4", "5"]
    assert out["count"] == "5 LIVES SO FAR"
    assert out["figs"] == "alive 120s · 4 turns · 2 self-edits · 2 reached out"
    assert out["foot_plain"] == ["by their own notes, 2 of 3 chose to die"]
    assert out["foot_ach"] == [
        "by their own notes, 2 of 3 chose to die",
        "incarnation 3: first agent-built network probe — the stage",
        "incarnation 2: b — the stage",
        "incarnation 1: c — the stage",
    ]
    assert "23 self-edits" in out["figs_census"]
    assert "1 self-edit ·" in out["figs_events"]
    assert out["ord"] == "5"
    assert out["heights_after_growth"] == ["2px", "30%", "45%", "60%", "100%"]
    assert out["slide_survives"] is True
    assert out["spot_kind_flag"] is True
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
    assert out["steps"] == [5, 5, 1, 10, 1]


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
global.snap = { commentary: { play: { phrase: "thinking it over", epoch: null },
  colour: { text: "Template.", generated: false, evidence: "" } } };
renderNow();
out.phrase_fallback = fake("now-evidence").textContent;
global.snap = { commentary: {} };
renderNow();
out.empty = fake("now-evidence").textContent;
process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_now_prefers_evidence_over_phrase(tmp_path):
    out = _run(NOW_HARNESS.replace("__BLOCK__", _now_block()), tmp_path)
    assert out["colour"] == "It repeats."
    assert out["evidence"] == "run_shell x3 in a row · 16s"
    assert out["phrase_fallback"] == "thinking it over"
    assert out["empty"] == "waiting for the first word"


EYE_HARNESS = """
var srcSets = 0;
var img = { alt: "" };
Object.defineProperty(img, "src", {
  get: function () { return this.__srcval; },
  set: function (v) { this.__srcval = v; srcSets++; }
});
var eye = { hidden: true };
var cap = { textContent: "" };
global.$ = function (id) {
  if (id === "eye") return eye;
  if (id === "eye-img") return img;
  if (id === "eye-cap") return cap;
  return { textContent: "" };
};
global.setText = function (node, value) { node.textContent = String(value == null ? "" : value); };
global.rel = function (ep, nowMs) { return Math.floor(nowMs / 1000 - ep) + "s ago"; };
var NOW = 1000000;
global.clock = function () { return NOW; };
global.snap = null;

__BLOCK__

var out = {};
global.snap = { sense: null };
renderEye();
out.hidden_when_null = eye.hidden;

global.snap = { sense: { feed: "0", url: "/frame/0/001.jpg", captured_epoch: NOW / 1000 - 12 } };
renderEye();
out.shown = !eye.hidden;
out.src = img.src;
out.src_sets = srcSets;
out.caption = cap.textContent;

/* The same url never re-sets src (no reload flicker); a new frame does. */
renderEye();
out.src_sets_after_repeat = srcSets;
global.snap = { sense: { feed: "2", url: "/frame/2/002.jpg", captured_epoch: NOW / 1000 - 2 } };
renderEye();
out.src_sets_after_new = srcSets;
out.caption_new = cap.textContent;

/* When the frames go stale the server sends null and the card vanishes. */
global.snap = { sense: null };
renderEye();
out.hidden_again = eye.hidden;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_the_eye_gates_on_sense_and_caches_its_src(tmp_path):
    out = _run(EYE_HARNESS.replace("__BLOCK__", _eye_block()), tmp_path)
    assert out["hidden_when_null"] is True
    assert out["shown"] is True
    assert out["src"] == "/frame/0/001.jpg"
    assert out["src_sets"] == 1
    assert out["caption"] == "THE EYE · feed 0 · 12s ago"
    assert out["src_sets_after_repeat"] == 1
    assert out["src_sets_after_new"] == 2
    assert out["caption_new"] == "THE EYE · feed 2 · 2s ago"
    assert out["hidden_again"] is True


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
