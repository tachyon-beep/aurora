"""The telemetry panel: the document beside the stage, for a viewer browsing along at home.

Served on the stream port as GET /telemetry. Renders only what /api/stream
and /api/lineage already publish; every value goes through textContent, and
the page holds no state beyond what it last fetched. The record table is
reconciled in place on every poll so a viewer's order, filter, open cards and
focus survive; under 720px the same rows lay out as stacked cards. The
JavaScript blocks are cut on their sentinel comments by the node tests.
"""

TELEMETRY_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>aurora — telemetry</title>
<style>
:root {
  color-scheme: dark;
  --serif: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --ink-0: #0b0e11; --ink-1: #12171b; --ink-2: #182027;
  --rule: #232c34; --rule-2: #35414b;
  --paper: #eef3f6; --paper-dim: #b8c2ca; --paper-faint: #97a2ab;
  --think: #b3bcff; --act: #f0bd68; --world: #6fc4ff; --vital: #66d9c2;
  --fault: #ff8d8d; --chosen: #7fd7b6; --taken: #ff9f6b; --broken: #ff8d8d;
  --seam: #4a5865; --control: #5a6b78;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html { scroll-padding-top: 64px; }
body { margin: 0; background: var(--ink-0); color: var(--paper); font: 400 16px/1.5 var(--sans); }
a { color: var(--vital); }
a:focus-visible, button:focus-visible, select:focus-visible, input:focus-visible,
[tabindex="0"]:focus-visible { outline: 2px solid var(--vital); outline-offset: 2px; }
.vh { position: absolute !important; width: 1px; height: 1px; margin: -1px; padding: 0;
  overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; border: 0; }
.skip { position: absolute; left: 8px; top: -60px; z-index: 30; padding: 8px 12px;
  background: var(--ink-2); color: var(--paper); border-radius: 6px; }
.skip:focus { top: 8px; }
.eyebrow { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }
.mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.byline { font: 400 14px/20px var(--mono); color: var(--paper-faint); }

/* ---------- strip ---------- */
#strip { position: sticky; top: 0; z-index: 20; min-height: 56px; display: flex; align-items: center;
  flex-wrap: wrap; gap: 6px 18px; padding: 6px 20px; background: var(--ink-1);
  border-bottom: 1px solid var(--rule-2); }
#wordmark { margin: 0; font: 600 16px/24px var(--sans); letter-spacing: .18em; color: var(--paper);
  white-space: nowrap; }
#to-stream { display: inline-flex; align-items: center; min-height: 44px; padding: 0 6px;
  font: 500 15px/20px var(--sans); text-decoration: none; }
#to-stream:hover { text-decoration: underline; }
#state-cluster { display: flex; align-items: center; gap: 10px; }
#state-word { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-dim); }
#state-clock { font: 400 13px/19px var(--mono); font-variant-numeric: tabular-nums; color: var(--paper-dim); }
#strip-life { margin-left: auto; font: 400 14px/20px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#pause { min-height: 44px; min-width: 44px; padding: 0 14px; background: none; color: var(--paper-dim);
  border: 1px solid var(--control); border-radius: 6px; font: 500 14px/20px var(--sans); cursor: pointer; }
#pause[aria-pressed="true"] { color: var(--act); border-color: var(--act); }
#offline { flex: 1 1 100%; margin: 0 -20px -6px; padding: 8px 20px;
  background: var(--ink-2); color: var(--fault); font: 600 14px/20px var(--mono);
  letter-spacing: .06em; border-top: 1px solid var(--fault); }
#strip.offline #state-word, #strip.offline #state-clock { color: var(--fault); }
#strip.offline .dot { background: var(--fault); border-color: var(--fault); animation: none; }
#strip.paused .dot { animation: none; }
.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.dot.think { background: var(--think); }
.dot.act { background: var(--act); }
.dot.vital { background: var(--vital); }
.dot.fault { background: var(--fault); }
.dot.taken { background: var(--taken); border-radius: 0; }
.dot.hollow { background: none; border: 1px solid var(--paper-faint); }
.dot.pulse { animation: pulse 2s ease-in-out infinite; }
.dot.breathe { animation: breathe 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
@keyframes breathe { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

/* ---------- document ---------- */
main { max-width: 1180px; margin: 0 auto; padding: 24px 20px 40px; display: flex;
  flex-direction: column; gap: 36px; }
section { background: var(--ink-1); border: 1px solid var(--rule); border-radius: 10px;
  padding: 18px 22px 20px; }
.shead { display: flex; align-items: center; justify-content: space-between; gap: 16px;
  flex-wrap: wrap; margin-bottom: 14px; }
h2 { margin: 0; font: 600 13px/24px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }
.controls { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
.controls label { font: 400 14px/20px var(--sans); color: var(--paper-dim); }
.controls .check { display: inline-flex; align-items: center; gap: 8px; min-height: 44px; cursor: pointer; }
.controls input[type="checkbox"] { width: 20px; height: 20px; margin: 0; accent-color: var(--vital); }
select { min-height: 44px; padding: 0 10px; background: var(--ink-2); color: var(--paper);
  border: 1px solid var(--control); border-radius: 6px; font: 400 14px/20px var(--sans); }
.sfoot { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; margin-top: 12px;
  font: 400 14px/20px var(--mono); color: var(--paper-faint); }
button.plain { min-height: 44px; padding: 0 12px; background: none; color: var(--vital);
  border: 1px solid var(--control); border-radius: 6px; font: 500 14px/20px var(--sans); cursor: pointer; }
.empty { margin: 4px 0 0; font: 400 17px/26px var(--serif); color: var(--paper-dim); }
.scroller { overflow-x: auto; }
.scroller:focus-visible { outline-offset: -2px; }
p.reading { margin: 0; font: 400 19px/30px var(--serif); color: var(--paper); max-width: 68ch;
  text-wrap: pretty; }
ul.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
ul.list li { display: grid; grid-template-columns: 1fr auto; gap: 4px 16px; padding: 8px 0;
  border-top: 1px solid var(--rule); font: 400 15px/22px var(--sans); }
ul.list li:first-child { border-top: 0; }
ul.list .age { font: 400 13px/22px var(--mono); color: var(--paper-faint); white-space: nowrap; }
ul.list .sub { grid-column: 1 / -1; font: 400 14px/20px var(--mono); color: var(--paper-faint);
  overflow-wrap: anywhere; }
ul.list audio { grid-column: 1 / -1; width: 100%; max-width: 420px; height: 40px; }
pre { margin: 0; padding: 12px 14px; background: var(--ink-0); border: 1px solid var(--rule);
  border-radius: 6px; font: 400 13px/19px var(--mono); color: var(--paper-dim); overflow-x: auto;
  white-space: pre; }
.figure { font: 400 15px/22px var(--mono); color: var(--paper-dim); font-variant-numeric: tabular-nums; }
.figure .add { color: var(--chosen); } .figure .rem { color: var(--taken); }
.k-declared { color: var(--chosen); } .k-harness { color: var(--taken); } .k-unknown { color: var(--broken); }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 20px 32px; }

/* ---------- the record ---------- */
#record-table { width: 100%; border-collapse: collapse; font: 400 14px/20px var(--mono);
  font-variant-numeric: tabular-nums; }
#record-table th, #record-table td { padding: 8px 10px; text-align: left; vertical-align: middle; }
#record-table th { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .1em;
  color: var(--paper-faint); border-bottom: 1px solid var(--rule-2); white-space: nowrap; }
#record-table tr.groups th { padding-bottom: 4px; border-bottom: 0; color: var(--paper-dim); }
#record-table tr.groups th.seam, #record-table .seam { border-left: 1px solid var(--seam); }
#record-table tbody tr.life { border-top: 1px solid var(--rule); }
#record-table tbody tr.life.live td { color: var(--vital); }
#record-table td.num { text-align: right; }
#record-table th.num { text-align: right; }
#record-table td.ord { padding: 2px 4px; }
.disc { display: inline-flex; align-items: center; gap: 8px; min-height: 36px; min-width: 44px;
  padding: 0 8px; background: none; color: var(--paper); border: 1px solid transparent;
  border-radius: 6px; font: 400 14px/20px var(--mono); cursor: pointer; }
.disc:hover { border-color: var(--control); }
.disc .hash { display: none; }
.disc .tri { display: inline-block; width: 0; height: 0; border: 5px solid transparent;
  border-left: 7px solid var(--paper-faint); border-right: 0; transition: transform 150ms ease; }
.disc[aria-expanded="true"] .tri { transform: rotate(90deg); }
.live-tag { font: 600 13px/18px var(--mono); letter-spacing: .12em; color: var(--vital); }
td.stars { white-space: nowrap; }
td.stars .glyphs { letter-spacing: .05em; color: var(--act); }
td.stars .fig { margin-left: 8px; color: var(--paper-dim); }
td.stars .pending, td.stars .none { color: var(--paper-faint); }
td.noted { text-align: center; color: var(--act); font-size: 16px; }
#record-table .unit { display: none; }
tr.card-row td { padding: 0 0 18px; }
.card { display: grid; grid-template-columns: 1fr 1fr; gap: 16px 32px; padding: 14px 16px 16px;
  margin: 0 4px; background: var(--ink-2); border: 1px solid var(--rule); border-radius: 8px;
  font: 400 15px/22px var(--sans); }
.card .blk { min-width: 0; }
.card .blk.reading { border-left: 1px solid var(--seam); padding-left: 24px; }
.card .eyebrow { margin-bottom: 8px; }
.card .facts { font: 400 14px/20px var(--mono); color: var(--paper-dim); font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere; }
.card .span { font: 400 13px/20px var(--mono); color: var(--paper-faint); margin-top: 4px; }
.card .note { margin: 10px 0 0; font: 400 17px/26px var(--serif); color: var(--paper); text-wrap: pretty; }
.card .verdict { margin: 0; font: 400 17px/26px var(--serif); color: var(--paper); text-wrap: pretty; }
.card .evidence { margin-top: 4px; font: 400 13px/20px var(--mono); color: var(--paper-faint); }
.card .moments { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.card .moments li { display: grid; grid-template-columns: auto auto 1fr; gap: 0 12px; align-items: baseline;
  font: 400 15px/22px var(--sans); }
.card .moments .turn, .card .moments .fig { font: 400 13px/22px var(--mono); color: var(--paper-faint);
  white-space: nowrap; }
.card .noted-line { margin-top: 12px; font: 400 15px/22px var(--sans); color: var(--act); }
.card .noted-line b { font: 600 13px/18px var(--mono); letter-spacing: .12em; margin-right: 8px; }
.card .prov { margin-top: 12px; font: 400 13px/20px var(--mono); color: var(--paper-faint); }
.card .quiet { font: 400 15px/22px var(--serif); color: var(--paper-dim); font-style: italic; }
@media (prefers-reduced-motion: reduce) {
  .dot.pulse, .dot.breathe { animation: none; }
  .disc .tri { transition: none; }
}

/* ---------- streams ---------- */
#lanes-table { width: 100%; border-collapse: collapse; font: 400 14px/20px var(--mono);
  font-variant-numeric: tabular-nums; min-width: 640px; }
#lanes-table th, #lanes-table td { padding: 8px 10px; text-align: left; white-space: nowrap; }
#lanes-table th { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .1em;
  color: var(--paper-faint); border-bottom: 1px solid var(--rule-2); }
#lanes-table tbody tr { border-top: 1px solid var(--rule); }
#lanes-table td.num, #lanes-table th.num { text-align: right; }
#pulse { display: flex; align-items: flex-end; gap: 16px; margin-top: 16px; flex-wrap: wrap; }
#pulse-bars { display: flex; align-items: flex-end; gap: 3px; height: 40px; width: 220px; flex: none; }
#pulse-bars .bar { flex: 1; min-height: 2px; background: var(--world); border-radius: 1px 1px 0 0; }
#pulse-figs { font: 400 14px/20px var(--mono); color: var(--paper-dim); font-variant-numeric: tabular-nums; }

/* ---------- foot ---------- */
footer { max-width: 1180px; margin: 0 auto; padding: 0 20px 48px; display: flex; flex-direction: column;
  gap: 6px; font: 400 14px/20px var(--mono); color: var(--paper-faint); }

/* ---------- narrow ---------- */
@media (max-width: 719px) {
  #strip { flex-wrap: wrap; gap: 4px 14px; padding: 6px 12px; }
  #wordmark { font-size: 14px; letter-spacing: .14em; flex: 1 1 auto; }
  #pause { order: 0; margin-left: auto; }
  #to-stream { order: 1; white-space: nowrap; }
  #state-cluster { order: 2; }
  #state-clock, #strip-life { display: none; }
  #record-table td.ending.is-live, #record-table td.stars.is-none { display: none; }
  main { padding: 16px 10px 32px; gap: 20px; }
  section { padding: 14px 14px 16px; }
  .two { grid-template-columns: 1fr; }
  #record-table, #record-table tbody, #record-table tr, #record-table td { display: block; }
  #record-table thead { display: none; }
  #record-table tbody tr.life { display: flex; flex-wrap: wrap; align-items: center; gap: 0 10px;
    padding: 6px 0; }
  #record-table td { padding: 0; border: 0; }
  #record-table td.seam { border-left: 0; }
  #record-table td.stars { border-left: 1px solid var(--seam); padding-left: 10px; }
  #record-table td.stars.sep::before { content: none; }
  .disc .hash { display: inline; }
  html { scroll-padding-top: 108px; }
  #offline { margin: 0 -12px -6px; padding: 8px 12px; }
  .card .blk.reading { border-left: 0; padding-left: 0; border-top: 1px solid var(--seam); padding-top: 14px; }
  #record-table td.narrow-hide { display: none; }
  #record-table td.num { text-align: left; }
  #record-table td.sep::before { content: "·"; color: var(--paper-faint); margin-right: 10px; }
  #record-table td.ord { padding: 0; }
  #record-table td.noted:empty { display: none; }
  #record-table .unit { display: inline; }
  .disc { min-height: 44px; }
  tr.card-row td { padding: 0 0 12px; }
  .card { grid-template-columns: 1fr; margin: 0; }
}
</style>
</head>
<body>
<a class="skip" href="#record">Skip to the record</a>
<header id="strip">
  <h1 id="wordmark">AURORA · TELEMETRY</h1>
  <a id="to-stream" href="/">← the stream</a>
  <div id="state-cluster" aria-label="stage state">
    <span id="state-dot" class="dot hollow" aria-hidden="true"></span>
    <span id="state-word">STANDING BY</span>
    <span id="state-clock"></span>
  </div>
  <div id="strip-life"></div>
  <button id="pause" type="button" aria-pressed="false">pause updates</button>
  <div id="offline" role="status" hidden>STAGE OFFLINE — this page cannot reach the stage</div>
</header>
<main>
  <section id="record" aria-labelledby="record-h">
    <div class="shead">
      <h2 id="record-h">The record</h2>
      <div class="controls">
        <label for="order">Order by</label>
        <select id="order">
          <option value="newest">newest first</option>
          <option value="lived">longest lived</option>
          <option value="turns">most turns</option>
          <option value="edits">most self-edits</option>
          <option value="verdict">highest verdict</option>
        </select>
        <label class="check"><input type="checkbox" id="noted-only"> only lives with a noted first</label>
      </div>
    </div>
    <div id="order-status" class="vh" role="status"></div>
    <table id="record-table" role="table">
      <caption class="vh">Every incarnation, newest first, with its measured figures and the stage's reading</caption>
      <colgroup><col><col><col><col><col><col><col></colgroup>
      <colgroup class="reading"><col><col></colgroup>
      <thead role="rowgroup">
        <tr class="groups" role="row">
          <th scope="colgroup" colspan="7" role="columnheader">Measured</th>
          <th scope="colgroup" colspan="2" class="seam" role="columnheader">The stage's reading</th>
        </tr>
        <tr role="row">
          <th scope="col" role="columnheader"><span aria-hidden="true">#</span><span class="vh">Incarnation</span></th>
          <th scope="col" role="columnheader">Ended</th>
          <th scope="col" role="columnheader">Lived</th>
          <th scope="col" class="num" role="columnheader">Turns</th>
          <th scope="col" class="num" role="columnheader">Edits</th>
          <th scope="col" class="num" role="columnheader">Errors</th>
          <th scope="col" role="columnheader">Ending</th>
          <th scope="col" class="seam" role="columnheader">Verdict</th>
          <th scope="col" role="columnheader">Noted</th>
        </tr>
      </thead>
      <tbody id="record-body" role="rowgroup"></tbody>
    </table>
    <p id="record-empty" class="empty" hidden>No one has died here yet.</p>
    <div class="sfoot">
      <button id="show-all" class="plain" type="button" hidden></button>
      <span id="record-omitted"></span>
      <span id="record-count"></span>
    </div>
  </section>

  <section id="story" aria-labelledby="story-h" hidden>
    <div class="shead"><h2 id="story-h">The story so far</h2></div>
    <p id="story-text" class="reading"></p>
    <p id="story-by" class="byline"></p>
  </section>

  <section id="source" aria-labelledby="source-h">
    <div class="shead"><h2 id="source-h">Source</h2><span id="source-delta" class="figure"></span></div>
    <div class="two">
      <div>
        <div class="eyebrow">Latest edit</div>
        <p id="edit-line" class="figure" style="margin: 6px 0 10px"></p>
        <pre id="edit-excerpt" tabindex="0" aria-label="latest edit excerpt" hidden></pre>
      </div>
      <div>
        <div class="eyebrow">Self-modification this life</div>
        <ul id="events" class="list" aria-label="self-modification events"></ul>
        <p id="events-empty" class="empty" hidden>No edits yet this life.</p>
      </div>
    </div>
  </section>

  <section id="diode" aria-labelledby="diode-h">
    <div class="shead"><h2 id="diode-h">The diode</h2></div>
    <div class="two">
      <div>
        <div class="eyebrow">Recent operations</div>
        <ul id="outputs" class="list" aria-label="recent diode operations"></ul>
        <p id="outputs-empty" class="empty" hidden>Nothing has crossed the diode yet.</p>
      </div>
      <div>
        <div class="eyebrow">Published</div>
        <ul id="published" class="list" aria-label="published items"></ul>
        <p id="published-empty" class="empty" hidden>Nothing published.</p>
        <div class="eyebrow" style="margin-top: 18px">Spoken</div>
        <ul id="spoken" class="list" aria-label="spoken utterances"></ul>
        <p id="spoken-empty" class="empty" hidden>Nothing spoken.</p>
      </div>
    </div>
    <div class="sfoot"><span id="diode-totals"></span></div>
  </section>

  <section id="streams" aria-labelledby="streams-h">
    <div class="shead"><h2 id="streams-h">Streams</h2></div>
    <div class="scroller" tabindex="0" aria-label="model lanes, scrollable">
      <table id="lanes-table">
        <caption class="vh">Every model lane the recorder has seen in the last hour</caption>
        <thead><tr>
          <th scope="col">Lane</th><th scope="col">Bound</th>
          <th scope="col" class="num">Requests / h</th><th scope="col" class="num">Tokens / h</th>
          <th scope="col" class="num">Errors / h</th><th scope="col">In flight</th><th scope="col">Last seen</th>
        </tr></thead>
        <tbody id="lanes-body"></tbody>
      </table>
    </div>
    <p id="lanes-empty" class="empty" hidden>No lane has carried a request in the last hour.</p>
    <div id="pulse">
      <div id="pulse-bars" aria-hidden="true"></div>
      <div id="pulse-figs"></div>
    </div>
    <div class="sfoot"><span id="lanes-omitted"></span></div>
  </section>
</main>
<footer>
  <div>the agent has no network interface · one unix socket to the model, nothing else</div>
  <div>the model key lives in the recorder · the agent runs with a dummy</div>
  <div>it can rewrite every line of itself · it cannot reach the machine it runs on</div>
  <div>the transcript is the proxy's, not the agent's · refreshed every 2s</div>
  <div>This page is read-only. Nothing here can reach the agent.</div>
</footer>
<script>
/* ---------- helpers ---------- */
function $(id) { return document.getElementById(id); }
function el(tag, cls, parent) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (parent) parent.appendChild(n);
  return n;
}
function setText(node, value) {
  value = value == null ? "" : String(value);
  if (node.textContent === value) return false;
  node.textContent = value;
  return true;
}
function setClass(node, name, on) { if (node) node.classList.toggle(name, !!on); }
function setHidden(node, hide) { if (node && node.hidden !== !!hide) node.hidden = !!hide; }
function norm(t) { return (t == null ? "" : String(t)).replace(/\s+/g, " ").trim(); }
function dur(s) {
  s = Math.max(0, Math.floor(s));
  if (s < 60) return s + "s";
  var m = Math.floor(s / 60);
  if (m < 60) return m + "m " + (s % 60) + "s";
  var h = Math.floor(m / 60);
  return h + "h " + (m % 60) + "m";
}
function rel(ep, nowMs) {
  if (ep == null) return "";
  var s = Math.max(0, nowMs / 1000 - ep);
  if (s < 10) return "just now";
  if (s < 60) return Math.floor(s) + "s ago";
  if (s < 3600) return Math.floor(s / 60) + "m ago";
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m ago";
  return Math.floor(s / 86400) + "d ago";
}
function iso(ep) {
  if (ep == null) return "";
  try { return new Date(ep * 1000).toISOString().replace(/\.\d+Z$/, "Z"); } catch (e) { return ""; }
}
function plural(n, word) { return n + " " + word + (n === 1 ? "" : "s"); }
function lifeCount(n) { return n + (n === 1 ? " life" : " lives"); }
function count(n) { return typeof n === "number" ? String(n) : "—"; }
var KIND_WORD = { declared: "declared", harness: "harness", unknown: "unknown" };
var KIND_LONG = { declared: "ended by its own note", harness: "ended by the harness",
  unknown: "ending unrecorded" };
var skewMs = 0;
function clock() { return Date.now() + skewMs; }

/* ---------- order ---------- */
/* orderLives is the whole ordering rule: pure, so the node test pins it. The
   default is chronological, newest first; every other order is a viewer's
   choice, ties break by ordinal descending, and a life with no figure for the
   chosen key sorts last. The current life stays first in every order and
   survives the filter: it is the row that is still changing. */
var ORDER_KEY = { lived: "lived_seconds", turns: "turns", edits: "edits" };
function orderLives(lives, mode, notedOnly) {
  var out = (lives || []).filter(function (l) { return l && (l.current || !notedOnly || l.achievement); });
  function key(l) {
    if (mode === "verdict") return l.verdict ? l.verdict.stars : null;
    var v = l[ORDER_KEY[mode]];
    return typeof v === "number" ? v : null;
  }
  out.sort(function (a, b) {
    if (a.current !== b.current) return a.current ? -1 : 1;
    if (mode && mode !== "newest") {
      var ka = key(a), kb = key(b);
      if (ka == null && kb != null) return 1;
      if (kb == null && ka != null) return -1;
      if (ka != null && kb != null && ka !== kb) return kb - ka;
    }
    return (b.ordinal || 0) - (a.ordinal || 0);
  });
  return out;
}
var ORDER_WORD = { newest: "newest first", lived: "longest lived", turns: "most turns",
  edits: "most self-edits", verdict: "highest verdict" };

/* ---------- state ---------- */
function stateOf(snap, age) {
  var st = (snap && snap.stats) || {};
  var any = (st.transcript_turns || 0) > 0 || ((snap && snap.turns) || []).length > 0;
  if (!any) return "standby";
  if ((st.turns_this_life || 0) === 0) return ((snap && snap.lineage) || []).length ? "between" : "standby";
  if (age == null) return "standby";
  if (age < 5) return "live";
  if (age < 90) return "thinking";
  if (age < 300) return "quiet";
  return "nosignal";
}
var STATE_WORD = { live: "LIVE", thinking: "THINKING", quiet: "QUIET", nosignal: "NO SIGNAL",
  between: "BETWEEN LIVES", standby: "STANDING BY" };
var STATE_DOT = { live: "dot vital pulse", thinking: "dot think breathe", quiet: "dot act",
  nosignal: "dot fault", between: "dot taken", standby: "dot hollow" };

/* ---------- copy ---------- */
function starGlyphs(n) {
  var s = "";
  for (var i = 1; i <= 5; i++) s += i <= n ? "★" : "☆";
  return s;
}
function provenanceFor(life, nowMs) {
  var d = life.digest || {};
  if (d.state === "ready") {
    var parts = ["read " + (d.turns_shown != null ? d.turns_shown : "?") + " of " +
      (d.turns_total != null ? d.turns_total : "?") + " turns"];
    if (d.generated_at != null && life.ended_epoch != null && d.generated_at >= life.ended_epoch) {
      parts.push(dur(d.generated_at - life.ended_epoch) + " after death");
    }
    return parts.join(" · ") + " — the stage";
  }
  if (d.state === "pending") return "reading pending — the stage";
  return "no reading of this life — the stage";
}
/* A verdict is pending while the digest is still to come or has just landed:
   the desk rates a life only after its digest settles. */
function verdictPending(life) {
  var state = (life.digest || {}).state;
  return !life.verdict && !life.current && (state === "pending" || state === "ready");
}
var DEPTH_WORD = { full: "whole life read", sampled: "life sampled", partial: "partial record",
  tombstone_only: "note only" };
function measuredLine(life, nowMs) {
  var f = [];
  if (life.current) f.push("alive " + (life.began_epoch != null ? dur(nowMs / 1000 - life.began_epoch) : "—"));
  else if (life.lived_seconds != null) f.push("lived " + dur(life.lived_seconds));
  f.push(typeof life.turns === "number" ? plural(life.turns, "turn") : "turns not counted");
  if (typeof life.edits === "number") f.push(plural(life.edits, "self-edit"));
  if (typeof life.subcalls === "number") f.push(plural(life.subcalls, "sub-call"));
  if (typeof life.errors === "number") f.push(plural(life.errors, "error"));
  if (!life.current && life.kind) f.push(KIND_LONG[life.kind] || life.kind);
  return f.join(" · ");
}
</script>
<script>
/* ---------- record ---------- */
var snap = null, lineage = null, digestModel = "";
var rows = {};            /* ordinal -> { tr, cardTr, cells, card, open } */
var orderMode = "newest", notedOnly = false, showAll = false;
var ROW_LIMIT = 25;

function makeRow(life) {
  var tr = el("tr", "life");
  tr.setAttribute("role", "row");
  var cells = {};
  var ord = el("td", "ord", tr); ord.setAttribute("role", "cell");
  var disc = el("button", "disc", ord);
  disc.type = "button";
  el("span", "tri", disc).setAttribute("aria-hidden", "true");
  el("span", "hash", disc).textContent = "#";
  var ordText = el("span", "ordn", disc);
  cells.disc = disc; cells.ordText = ordText;
  var live = el("span", "live-tag", ord); cells.live = live;
  cells.ended = el("td", "ended narrow-hide", tr);
  cells.lived = el("td", "lived sep", tr);
  cells.turns = el("td", "num turns sep", tr);
  cells.edits = el("td", "num edits narrow-hide", tr);
  cells.errors = el("td", "num errors narrow-hide", tr);
  cells.ending = el("td", "ending sep", tr);
  cells.stars = el("td", "stars seam sep", tr);
  cells.noted = el("td", "noted", tr);
  ["ended", "lived", "turns", "edits", "errors", "ending", "stars", "noted"].forEach(function (k) {
    cells[k].setAttribute("role", "cell");
  });
  var cardTr = el("tr", "card-row");
  cardTr.setAttribute("role", "row");
  cardTr.hidden = true;
  cardTr.id = "life-" + life.ordinal + "-card";
  var cardTd = el("td", "", cardTr); cardTd.colSpan = 9; cardTd.setAttribute("role", "cell");
  var card = buildCard(cardTd);
  disc.setAttribute("aria-expanded", "false");
  disc.setAttribute("aria-controls", cardTr.id);
  disc.setAttribute("aria-label", "Expand incarnation " + life.ordinal);
  var row = { tr: tr, cardTr: cardTr, cells: cells, card: card, open: false, ordinal: life.ordinal };
  disc.addEventListener("click", function () { toggleRow(row); });
  return row;
}
function buildCard(td) {
  var card = el("div", "card", td), c = {};
  var m = el("div", "blk measured", card);
  el("div", "eyebrow", m).textContent = "MEASURED";
  c.facts = el("div", "facts", m);
  c.span = el("div", "span", m);
  c.note = el("p", "note", m);
  var r = el("div", "blk reading", card);
  c.reyebrow = el("div", "eyebrow", r);
  c.verdict = el("p", "verdict", r);
  c.evidence = el("div", "evidence", r);
  c.moments = el("ul", "moments", r);
  c.moments.setAttribute("aria-label", "notable moments, in turn order");
  c.noted = el("div", "noted-line", r);
  var b = el("b", "", c.noted); b.textContent = "NOTED FIRST";
  c.notedText = el("span", "", c.noted);
  c.prov = el("div", "prov", r);
  return c;
}
function toggleRow(row, force) {
  var open = force == null ? !row.open : !!force;
  row.open = open;
  row.cells.disc.setAttribute("aria-expanded", open ? "true" : "false");
  row.cells.disc.setAttribute("aria-label", (open ? "Collapse" : "Expand") + " incarnation " + row.ordinal);
  setHidden(row.cardTr, !open || row.tr.hidden);
}
function figureCell(td, value, unit) {
  td.textContent = "";
  el("span", "", td).textContent = count(value);
  if (typeof value === "number") el("span", "unit", td).textContent = " " + unit + (value === 1 ? "" : "s");
}
/* The clock-dependent texts of one row: refreshed every second for the rows
   on screen, without touching anything else in the row. */
function refreshTimes(row, life, nowMs) {
  var c = row.cells;
  setText(c.ended, life.current ? "—" : rel(life.ended_epoch, nowMs));
  if (life.current) {
    setText(c.lived, "alive " + (life.began_epoch != null ? dur(nowMs / 1000 - life.began_epoch) : "—"));
    if (row.open) setText(row.card.facts, measuredLine(life, nowMs));
  }
}
function fillRow(row, life, nowMs) {
  var c = row.cells;
  row.life = life;
  setText(c.ordText, String(life.ordinal));
  setText(c.live, life.current ? "LIVE" : "");
  setClass(row.tr, "live", !!life.current);
  c.ended.title = life.current ? "" : iso(life.ended_epoch);
  if (!life.current) setText(c.lived, life.lived_seconds != null ? dur(life.lived_seconds) : "—");
  refreshTimes(row, life, nowMs);
  figureCell(c.turns, life.turns, "turn");
  figureCell(c.edits, life.edits, "edit");
  figureCell(c.errors, life.errors, "error");
  var kind = life.current ? null : (life.kind || "unknown");
  setText(c.ending, kind ? KIND_WORD[kind] || kind : "live");
  c.ending.className = "ending sep" + (kind ? " k-" + kind : " is-live");
  /* verdict cell */
  c.stars.textContent = "";
  setClass(c.stars, "is-none", life.current || (!life.verdict && !verdictPending(life)));
  if (life.current) {
    el("span", "none", c.stars).textContent = "—";
  } else if (life.verdict) {
    var g = el("span", "glyphs", c.stars); g.textContent = starGlyphs(life.verdict.stars);
    g.setAttribute("aria-hidden", "true");
    el("span", "fig", c.stars).textContent = life.verdict.stars + "/5";
  } else if (verdictPending(life)) {
    el("span", "pending", c.stars).textContent = "pending";
  } else {
    el("span", "none", c.stars).textContent = "—";
  }
  c.noted.textContent = "";
  if (life.achievement) {
    var dot = el("span", "", c.noted); dot.textContent = "●"; dot.setAttribute("aria-hidden", "true");
    el("span", "vh", c.noted).textContent = "noted first";
  }
  fillCard(row.card, life, nowMs);
}
function fillCard(c, life, nowMs) {
  setText(c.facts, measuredLine(life, nowMs));
  var span = "";
  if (life.began_epoch != null) span += "began " + iso(life.began_epoch);
  if (!life.current && life.ended_epoch != null) span += (span ? " → " : "") + "ended " + iso(life.ended_epoch);
  setText(c.span, span);
  setText(c.note, life.current ? "" : norm(life.note));
  setHidden(c.note, !c.note.textContent);
  setText(c.reyebrow, "THE STAGE'S READING" + (digestModel ? " · " + digestModel : ""));
  c.moments.textContent = "";
  if (life.current) {
    c.verdict.className = "verdict quiet";
    setText(c.verdict, "Still being lived.");
    setText(c.evidence, "");
    setHidden(c.noted, true);
    setText(c.prov, "");
    return;
  }
  if (life.verdict) {
    c.verdict.className = "verdict";
    setText(c.verdict, norm(life.verdict.line));
    setText(c.evidence, life.verdict.stars + "/5 · " + norm(life.verdict.evidence) +
      (life.verdict.depth ? " · " + (DEPTH_WORD[life.verdict.depth] || life.verdict.depth) : ""));
  } else {
    c.verdict.className = "verdict quiet";
    setText(c.verdict, verdictPending(life) ? "The desk's verdict is still to come." : "No verdict.");
    setText(c.evidence, "");
  }
  var moments = (life.moments || []).slice().sort(function (a, b) { return a.turn - b.turn; });
  moments.forEach(function (m) {
    var li = el("li", "", c.moments);
    el("span", "turn", li).textContent = "turn " + m.turn;
    el("span", "fig", li).textContent = m.stars + "/5";
    el("span", "line", li).textContent = norm(m.line);
  });
  setHidden(c.noted, !life.achievement);
  setText(c.notedText, norm(life.achievement || ""));
  setText(c.prov, provenanceFor(life, nowMs));
}

/* Reconcile: rows are keyed by ordinal and updated in place; only a changed
   order moves nodes, so an open card and the focused control survive a poll. */
/* An order the record cannot serve — no life carries the figure, as when the
   census cannot place entries exactly — is disabled rather than left to do
   nothing under a label that says it did something. */
var ORDER_NEEDS = { lived: "lived_seconds", turns: "turns", edits: "edits", verdict: "verdict" };
function usableOrders(lives) {
  var usable = { newest: true };
  Object.keys(ORDER_NEEDS).forEach(function (mode) {
    var key = ORDER_NEEDS[mode];
    usable[mode] = (lives || []).some(function (l) {
      return l && !l.current && (key === "verdict" ? !!l.verdict : typeof l[key] === "number");
    });
  });
  return usable;
}
function syncOrderControl(lives) {
  var usable = usableOrders(lives), select = $("order");
  if (!select) return usable;
  var options = select.options || select.children || [];
  for (var i = 0; i < options.length; i++) {
    var opt = options[i];
    if (opt.disabled !== !usable[opt.value]) opt.disabled = !usable[opt.value];
  }
  if (!usable[orderMode]) {
    var was = orderMode;
    orderMode = "newest";
    if (select.value !== "newest") select.value = "newest";
    setText($("order-status"), "no measured " + (ORDER_WORD[was] || was) + " to order by; newest first");
  }
  return usable;
}
function renderRecord() {
  if (!lineage) return;
  var nowMs = clock();
  digestModel = lineage.digest_model || "";
  syncOrderControl(lineage.lives);
  var lives = orderLives(lineage.lives, orderMode, notedOnly);
  var body = $("record-body");
  var seen = {}, limit = showAll ? Infinity : ROW_LIMIT, shownCount = 0;
  var sequence = [];
  lives.forEach(function (life, i) {
    var row = rows[life.ordinal];
    if (!row) { row = makeRow(life); rows[life.ordinal] = row; }
    seen[life.ordinal] = true;
    fillRow(row, life, nowMs);
    var hide = i >= limit;
    setHidden(row.tr, hide);
    setHidden(row.cardTr, hide || !row.open);
    if (!hide) shownCount++;
    sequence.push(row.tr, row.cardTr);
  });
  Object.keys(rows).forEach(function (o) {
    if (!seen[o]) { setHidden(rows[o].tr, true); setHidden(rows[o].cardTr, true); }
  });
  /* Move only what is out of place: compare the desired sequence with the DOM. */
  var cursor = body.firstChild;
  sequence.forEach(function (node) {
    if (node !== cursor) body.insertBefore(node, cursor);
    else cursor = cursor.nextSibling;
  });
  var dead = (lineage.lives || []).filter(function (l) { return l && !l.current; }).length;
  setHidden($("record-empty"), dead > 0);
  var showAllBtn = $("show-all");
  if (lives.length > ROW_LIMIT && !showAll) {
    setHidden(showAllBtn, false);
    setText(showAllBtn, "show all " + lifeCount(lives.length));
  } else {
    setHidden(showAllBtn, true);
  }
  var omitted = lineage.lives_omitted || 0;
  setText($("record-omitted"), omitted > 0 ? omitted + " earlier " + (omitted === 1 ? "life is" : "lives are") + " not listed" : "");
  setText($("record-count"), (dead === 1 ? "1 dead life" : dead + " dead lives") + " on record");
}
function tickRecord() {
  var nowMs = clock();
  Object.keys(rows).forEach(function (o) {
    var row = rows[o];
    if (row.life && !row.tr.hidden) refreshTimes(row, row.life, nowMs);
  });
}
</script>
<script>
/* ---------- live sections ---------- */
function renderStrip() {
  var st = (snap && snap.stats) || {};
  var nowMs = clock();
  var age = st.last_epoch != null ? Math.max(0, nowMs / 1000 - st.last_epoch) : null;
  var s = stateOf(snap, age);
  var dot = $("state-dot");
  if (dot.className !== STATE_DOT[s]) dot.className = STATE_DOT[s];
  setText($("state-word"), STATE_WORD[s]);
  setText($("state-clock"), (s === "live" || s === "thinking" || s === "quiet" || s === "nosignal") && age != null ? dur(age) : "");
  var parts = [];
  if (st.incarnation) parts.push("INCARNATION " + st.incarnation);
  if (st.model) parts.push(st.model);
  if (st.started_epoch != null) parts.push("alive " + dur(nowMs / 1000 - st.started_epoch));
  setText($("strip-life"), parts.join(" · "));
}
function renderStory() {
  var story = snap && snap.story;
  setHidden($("story"), !story || !story.text);
  if (!story || !story.text) return;
  setText($("story-text"), norm(story.text));
  var by = ["— the stage"];
  if (story.model) by.push(story.model);
  if (story.generated_at != null) by.push(rel(story.generated_at, clock()));
  setText($("story-by"), by.join(" · "));
}
function renderSource() {
  var code = (snap && snap.code) || {};
  var delta = $("source-delta");
  delta.textContent = "";
  if (!code.available) {
    delta.textContent = "the mirror is not available";
  } else if (!(code.added || code.removed)) {
    delta.textContent = "unmodified — still the seed it woke up as";
  } else {
    el("span", "add", delta).textContent = "+" + (code.added || 0);
    el("span", "", delta).textContent = " / ";
    el("span", "rem", delta).textContent = "−" + (code.removed || 0);
    el("span", "", delta).textContent = " lines from seed";
  }
  var edit = code.latest_edit;
  if (edit) {
    var line = "+" + (edit.added || 0) + " / −" + (edit.removed || 0);
    if (edit.epoch != null) line += " · seen " + rel(edit.epoch, clock());
    if (edit.restored) line += " · restored to the seed";
    setText($("edit-line"), line);
    setText($("edit-excerpt"), edit.excerpt || "");
    setHidden($("edit-excerpt"), !edit.excerpt);
  } else {
    setText($("edit-line"), "no edit observed since the stage started");
    setHidden($("edit-excerpt"), true);
  }
  var ev = (snap && snap.events) || [];
  var host = $("events");
  host.textContent = "";
  ev.slice().reverse().forEach(function (e) {
    var li = el("li", "", host);
    el("span", "", li).textContent = norm(e.headline || e.name || "");
    el("span", "age", li).textContent = e.index != null ? "turn " + e.index : "";
    /* The summary is a phrase for a placed edit and the raw arguments for an
       unplaced one; only the phrase is shown here — tool arguments stay off
       this page. */
    var sub = norm(e.summary || "");
    if (sub && !e.quoted && /^[\[{]/.test(sub)) sub = "";
    if (sub) el("span", "sub", li).textContent = sub;
  });
  setHidden($("events-empty"), ev.length > 0);
}
function renderDiode() {
  var d = (snap && snap.diode) || {};
  var nowMs = clock();
  var outputs = d.outputs || [], host = $("outputs");
  host.textContent = "";
  outputs.forEach(function (o) {
    var li = el("li", "", host);
    el("span", "", li).textContent = norm(o.verb || o.command || "") + (o.argument ? " · " + norm(o.argument) : "");
    el("span", "age", li).textContent = rel(o.epoch, nowMs);
  });
  setHidden($("outputs-empty"), outputs.length > 0);
  var pub = d.published || [], ph = $("published");
  ph.textContent = "";
  pub.forEach(function (p) {
    var li = el("li", "", ph);
    el("span", "", li).textContent = norm(p.text || p.name || "");
    el("span", "age", li).textContent = rel(p.epoch, nowMs);
  });
  setHidden($("published-empty"), pub.length > 0);
  var spoken = d.spoken || [], sh = $("spoken");
  sh.textContent = "";
  spoken.forEach(function (s, i) {
    var li = el("li", "", sh);
    var text = el("span", "", li); text.id = "spoken-text-" + i;
    text.textContent = norm(s.text || "") || "(no transcript)";
    el("span", "age", li).textContent = rel(s.epoch, nowMs);
    if (s.name) {
      var a = el("audio", "", li);
      a.controls = true; a.preload = "none";
      a.setAttribute("aria-labelledby", text.id);
      a.src = "/audio/" + encodeURIComponent(s.name);
    }
  });
  setHidden($("spoken-empty"), spoken.length > 0);
  var totals = [];
  totals.push("reached out " + plural(d.operations_total || 0, "time") + " · " + (d.operations_life || 0) + " this life");
  if (d.published_total) totals.push(plural(d.published_total, "item") + " published");
  if (d.spoken_total) totals.push(plural(d.spoken_total, "utterance") + " spoken");
  setText($("diode-totals"), totals.join(" · "));
}
function renderStreams() {
  var lanes = ((snap && snap.lanes) || []).slice(), nowMs = clock();
  lanes.sort(function (a, b) {
    if (a.name === "core") return -1;
    if (b.name === "core") return 1;
    return (b.requests_hour || 0) - (a.requests_hour || 0);
  });
  var body = $("lanes-body");
  body.textContent = "";
  lanes.forEach(function (l) {
    var tr = el("tr", "", body);
    el("td", "", tr).textContent = l.name;
    el("td", "", tr).textContent = l.bound ? "bound" : "unbound";
    el("td", "num", tr).textContent = String(l.requests_hour || 0);
    el("td", "num", tr).textContent = String(l.tokens_hour || 0);
    el("td", "num", tr).textContent = String(l.errors_hour || 0);
    el("td", "", tr).textContent = l.in_flight ? (l.in_flight + (l.in_flight_since != null ? " · " + dur(nowMs / 1000 - l.in_flight_since) : "")) : "—";
    el("td", "", tr).textContent = l.last_epoch != null ? rel(l.last_epoch, nowMs) : "—";
  });
  setHidden($("lanes-empty"), lanes.length > 0);
  var pulse = (snap && snap.pulse) || {}, buckets = pulse.buckets || [], bars = $("pulse-bars");
  bars.textContent = "";
  var max = 0;
  buckets.forEach(function (b) { if (b > max) max = b; });
  buckets.forEach(function (b) {
    var bar = el("div", "bar", bars);
    bar.style.height = max > 0 ? Math.max(5, Math.round(100 * b / max)) + "%" : "5%";
  });
  setText($("pulse-figs"), plural(pulse.requests_window || 0, "request") + " · " +
    (pulse.tokens_window || 0) + " tokens in the last 10 minutes" +
    (pulse.in_flight && pulse.in_flight.length ? " · " + pulse.in_flight.length + " in flight" : ""));
  var omitted = (snap && snap.lanes_omitted) || 0;
  setText($("lanes-omitted"), omitted > 0 ? plural(omitted, "more lane") + " not listed" : "");
}
function renderAll() {
  if (!snap) return;
  renderStrip();
  renderStory();
  renderSource();
  renderDiode();
  renderStreams();
}

/* ---------- polling ---------- */
var paused = false, offline = false;
function setOffline(off) {
  offline = !!off;
  setHidden($("offline"), !offline);
  setClass($("strip"), "offline", offline);
}
function fetchStream() {
  if (paused) return;
  fetch("/api/stream", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }).then(function (data) {
    snap = data;
    if (typeof data.now === "number") skewMs = data.now * 1000 - Date.now();
    setOffline(false);
    renderAll();
  }).catch(function () { setOffline(true); });
}
function fetchLineage() {
  if (paused) return;
  fetch("/api/lineage", { cache: "no-store" }).then(function (r) {
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  }).then(function (data) {
    lineage = data;
    renderRecord();
  }).catch(function () { setOffline(true); });
}
function tick() {
  if (paused) return;
  if (snap) renderStrip();
  if (lineage) tickRecord();
}
$("order").addEventListener("change", function () {
  orderMode = $("order").value;
  renderRecord();
  setText($("order-status"), "ordered by " + (ORDER_WORD[orderMode] || orderMode));
});
$("noted-only").addEventListener("change", function () {
  notedOnly = $("noted-only").checked;
  renderRecord();
  setText($("order-status"), notedOnly ? "showing only lives with a noted first" : "showing every life");
});
$("show-all").addEventListener("click", function () {
  showAll = true;
  renderRecord();
  /* The button hides itself; hand focus to the first disclosure it revealed. */
  var body = $("record-body");
  for (var i = 0, seen = 0; i < body.children.length; i++) {
    var tr = body.children[i];
    if (tr.className.indexOf("card-row") !== -1 || tr.hidden) continue;
    if (seen++ === ROW_LIMIT) {
      var b = tr.querySelector ? tr.querySelector(".disc") : null;
      if (b && b.focus) b.focus();
      break;
    }
  }
});
$("pause").addEventListener("click", function () {
  paused = !paused;
  $("pause").setAttribute("aria-pressed", paused ? "true" : "false");
  $("pause").textContent = paused ? "resume updates" : "pause updates";
  setClass($("strip"), "paused", paused);
  if (!paused) { fetchStream(); fetchLineage(); }
});
fetchStream();
fetchLineage();
setInterval(fetchStream, 5000);
setInterval(fetchLineage, 30000);
setInterval(tick, 1000);
</script>
</body>
</html>
"""
