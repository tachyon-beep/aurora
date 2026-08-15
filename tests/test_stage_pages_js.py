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
out.first_started = names();
out.first_caption = caption.textContent;
renderSpoken();
out.caption_during_first = caption.textContent;
audio.fire("ended");
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
out.after_failure = names();
out.still_queued = spokenQueue.length;

/* A reload restores the played set from storage and replays nothing. */
reset();
out.stored = JSON.parse(store.spokenPlayed || "[]").length;
spokenPlayed = {};
JSON.parse(store.spokenPlayed || "[]").forEach(function (n) { spokenPlayed[n] = true; });
renderSpoken();
out.replayed_after_reload = audio.played.length;

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
var provenanceEl = { textContent: "", classList: { toggle: function () {} } };
var clusterEl = { classList: { toggle: function () {} } };
global.$ = function (id) {
  if (id === "provenance") return provenanceEl;
  if (id === "state-cluster") return clusterEl;
  return { textContent: "", classList: { toggle: function () {} } };
};
global.setText = function (node, value) { node.textContent = value; };
global.setClass = function () {};

__BLOCK__

// The module-level showProvenance() call already ran above: index 0 is showing.
out.initial = provenanceEl.textContent;

// A genuine 20s tick while online advances the rotation.
rotateProvenance();
out.after_manual_rotate = provenanceEl.textContent;
var indexAfterRotate = provenanceAt;

// 1. setTransport(true), called on every 2s poll, must not itself advance
// the rotation -- the currently displayed line must be unchanged.
setTransport(true);
out.after_ok_1 = provenanceEl.textContent;
setTransport(true);
out.after_ok_2 = provenanceEl.textContent;

// 2. While offline, rotateProvenance() must not advance the index, and the
// OFFLINE text must remain displayed.
setTransport(false);
out.offline_text = provenanceEl.textContent;
rotateProvenance();
out.offline_after_rotate = provenanceEl.textContent;
out.index_unchanged_offline = provenanceAt === indexAfterRotate;

// 3. On recovery, the previously displayed line is restored -- not reset to
// index 0, and not left showing OFFLINE.
setTransport(true);
out.recovered_text = provenanceEl.textContent;

process.stdout.write(JSON.stringify(out));
"""


@needs_node
def test_provenance_rotation_survives_the_transport_poll(tmp_path):
    """setTransport(true) fires on every successful 2s poll. Before this test
    existed, it unconditionally rewrote #provenance's text, which would have
    made the rotating line invisible in production (overwritten within 2s of
    every 20s tick) while every string-presence test stayed green."""
    lines = [
        "the agent has no network interface · one unix socket to the model, nothing else",
        "the model key lives in the recorder · the agent runs with a dummy",
    ]
    out = _run(PROVENANCE_HARNESS.replace("__BLOCK__", _provenance_block()), tmp_path)
    assert out["initial"] == lines[0]
    assert out["after_manual_rotate"] == lines[1]
    # setTransport(true) does not advance the rotation.
    assert out["after_ok_1"] == lines[1]
    assert out["after_ok_2"] == lines[1]
    # Offline: no advance, OFFLINE text stays displayed.
    assert out["offline_text"] == "STAGE OFFLINE · this page cannot reach the stage"
    assert out["offline_after_rotate"] == out["offline_text"]
    assert out["index_unchanged_offline"] is True
    # Recovery restores the line that was showing, not index 0 and not OFFLINE.
    assert out["recovered_text"] == lines[1]


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
    # A reload finds the names already marked and plays nothing again.
    assert out["stored"] > 0
    assert out["replayed_after_reload"] == 0
    # A future-dated stamp is as dead as a stale one.
    assert out["future_played"] == 0
