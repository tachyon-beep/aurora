CONSOLE_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>aurora console</title>
<style>
  :root { color-scheme: dark; }
  body { margin: 0; background: #101316; color: #eef3f6;
         font: 14px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 10px 16px; border-bottom: 1px solid #303941; display: flex; gap: 16px; }
  header b { color: #66d9c2; }
  header a { color: #66d9c2; }
  main { display: grid; grid-template-columns: 360px 1fr; height: calc(100vh - 45px); }
  #list { border-right: 1px solid #303941; overflow-y: auto; padding: 8px; }
  #view { overflow: auto; padding: 12px 16px; }
  .entry { display: flex; justify-content: space-between; padding: 3px 6px;
           cursor: pointer; border-radius: 4px; width: 100%; text-align: left;
           background: none; border: 0; color: inherit; font: inherit; }
  .entry:hover { background: #1f252b; }
  .entry:focus-visible, .bar button:focus-visible, select:focus-visible {
           outline: 2px solid #66d9c2; outline-offset: 1px; }
  .entry .size { color: #79848c; }
  .dir { color: #77bdfb; }
  pre { white-space: pre-wrap; word-break: break-all; margin: 0; }
  .bar { margin-bottom: 8px; color: #a6b0b8; }
  .bar a, .bar button { color: #66d9c2; background: none; border: 1px solid #303941;
        border-radius: 4px; padding: 2px 8px; cursor: pointer; margin-right: 6px; }
  select { background: #171c20; color: #eef3f6; border: 1px solid #303941;
           border-radius: 4px; padding: 2px 6px; }
  #viewer-note { color: #79848c; padding: 3px 6px 8px; }
  #viewer-note a { color: #66d9c2; }
</style>
</head>
<body>
<header><b>aurora console</b><a id="diag-link" href="/diag">diagnostics</a><span id="crumb"></span></header>
<main>
  <div id="list">
    <div class="bar">
      <select id="root" aria-label="browse root"></select>
      <button id="up">up</button>
      <button id="diff">agent.py diff</button>
    </div>
    <div id="viewer-note" hidden>A turn-structured, searchable rendering of these
      transcripts is served by the viewer at
      <a href="http://localhost:8090">http://localhost:8090</a> (host loopback).</div>
    <div id="entries"></div>
  </div>
  <div id="view">
    <div class="bar" id="viewbar"></div>
    <pre id="content"></pre>
  </div>
</main>
<script>
const token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", location.pathname);
document.getElementById("diag-link").href = "/diag?token=" + encodeURIComponent(token);
let root = "telemetry";
let path = "";
function api(url) {
  return fetch(url, {headers: {"X-Console-Token": token}}).then(r => {
    if (r.ok) return r.json();
    return r.json().then(function (body) {
      var msg = (body && body.error) || ("request failed with status " + r.status);
      if (r.status === 401) msg += " — append ?token=<STAGE_CONSOLE_TOKEN> and reload";
      throw new Error(msg);
    }, function () {
      throw new Error("request failed with status " + r.status);
    });
  });
}
function crumb() {
  document.getElementById("crumb").textContent = root + "/" + path;
}
function load() {
  crumb();
  document.getElementById("viewer-note").hidden = !(root === "transcripts" && !path);
  api(`/api/browse?root=${root}&path=${encodeURIComponent(path)}`).then(d => {
    const box = document.getElementById("entries");
    const hadFocus = box.contains(document.activeElement);
    box.textContent = "";
    for (const e of d.entries) {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "entry" + (e.is_dir ? " dir" : "");
      const name = document.createElement("span");
      name.textContent = e.name + (e.is_dir ? "/" : "");
      const size = document.createElement("span");
      size.className = "size";
      size.textContent = e.is_dir ? "" : String(e.size);
      row.append(name, size);
      row.onclick = () => {
        if (e.is_dir) { path = path ? path + "/" + e.name : e.name; load(); }
        else { show(path ? path + "/" + e.name : e.name); }
      };
      box.appendChild(row);
    }
    if (hadFocus) {
      const first = box.querySelector(".entry");
      if (first) first.focus();
    }
  }).catch(err => { document.getElementById("content").textContent = String(err); });
}
function show(p, tail) {
  api(`/api/file?root=${root}&path=${encodeURIComponent(p)}${tail ? "&tail=1" : ""}`).then(d => {
    const bar = document.getElementById("viewbar");
    bar.textContent = "";
    const label = document.createElement("span");
    label.textContent = `${p} — ${d.size} bytes${d.truncated ? " (truncated)" : ""}${d.binary ? " (binary)" : ""} `;
    const tailBtn = document.createElement("button");
    tailBtn.textContent = "tail";
    tailBtn.onclick = () => show(p, true);
    const dl = document.createElement("a");
    dl.textContent = "download";
    dl.href = "#";
    dl.onclick = (ev) => {
      ev.preventDefault();
      const url = `/download?root=${root}&path=${encodeURIComponent(p)}`;
      fetch(url, {headers: {"X-Console-Token": token}}).then(r => {
        if (!r.ok) throw new Error("download failed with status " + r.status);
        return r.blob();
      }).then(blob => {
        const objectUrl = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = objectUrl;
        a.download = p.split("/").pop();
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(objectUrl);
      }).catch(err => { document.getElementById("content").textContent = String(err); });
    };
    bar.append(label, tailBtn, dl);
    document.getElementById("content").textContent = d.content;
  }).catch(err => { document.getElementById("content").textContent = String(err); });
}
document.getElementById("up").onclick = () => {
  path = path.includes("/") ? path.slice(0, path.lastIndexOf("/")) : "";
  load();
};
document.getElementById("diff").onclick = () => {
  api("/api/diff").then(d => {
    document.getElementById("viewbar").textContent = "agent.py vs agent_stock.py";
    document.getElementById("content").textContent = d.diff || "(no differences)";
  }).catch(err => { document.getElementById("content").textContent = String(err); });
};
const sel = document.getElementById("root");
api("/api/roots").then(roots => {
  for (const r of roots) {
    const o = document.createElement("option");
    o.value = r; o.textContent = r;
    sel.appendChild(o);
  }
  sel.value = root;
  load();
}).catch(err => { document.getElementById("content").textContent = String(err); });
sel.onchange = () => { root = sel.value; path = ""; load(); };
</script>
</body>
</html>
"""

STREAM_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<link rel="icon" href="data:,">
<title>aurora — the subject</title>
<style>
:root {
  color-scheme: dark;
  --serif: ui-serif, Georgia, "Iowan Old Style", "Palatino Linotype", "Times New Roman", serif;
  --sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace;
  --ink-0: #0b0e11; --ink-1: #12171b; --ink-2: #182027;
  --rule: #232c34; --rule-2: #35414b;
  --paper: #eef3f6; --paper-dim: #9fabb4; --paper-faint: #97a2ab;
  --think: #b3bcff; --think-rule: #4a52a8;
  --say: #ffffff;
  --act: #f0bd68; --act-soft: rgba(240,189,104,.10);
  --world: #6fc4ff; --vital: #66d9c2;
  --fault: #ff8d8d; --fault-soft: rgba(255,141,141,.08);
  --chosen: #7fd7b6; --taken: #ff9f6b; --broken: #ff8d8d;
  --flash: #fff4d6;
}
* { box-sizing: border-box; }
[hidden] { display: none !important; }
html, body { width: 1920px; height: 1080px; margin: 0; padding: 0; overflow: hidden;
  background: var(--ink-0); color: var(--paper); font-family: var(--sans); }

#stage { position: relative; display: grid; padding: 24px; height: 1080px;
  grid-template-columns: 1152px 700px; column-gap: 20px;
  grid-template-rows: 84px 772px 136px; row-gap: 20px;
  transition: filter 600ms ease-out; }
#stage.mourning { filter: saturate(.2) brightness(.55); transition: none; }

.panel { background: var(--ink-1); border: 1px solid var(--rule); border-radius: 10px;
  padding: 18px 22px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.ptitle { height: 24px; margin-bottom: 10px; display: flex; align-items: center;
  justify-content: space-between; flex: none;
  font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }

/* ---------- masthead ---------- */
#masthead { grid-column: 1 / -1; position: relative; display: grid;
  grid-template-rows: 36px 26px; row-gap: 6px; align-content: center;
  border-bottom: 1px solid var(--rule-2); }
#mh-a { display: flex; align-items: baseline; gap: 22px; }
#wordmark { font: 600 26px/30px var(--sans); letter-spacing: .18em; color: var(--paper); }
.vrule { width: 1px; height: 20px; background: var(--rule-2); align-self: center; flex: none; }
/* #premise carries the rotating containment facts (PROVENANCE_LINES): they are
   the page's load-bearing claims, so they hold the dominant masthead slot at
   full contrast. The aphoristic premise sentence moved down to #provenance. */
#premise { margin: 0; font: 500 15px/22px var(--sans); color: var(--paper); max-width: 900px;
  transition: opacity 250ms ease; }
#premise.announce { color: var(--taken); }
#state-cluster { margin-left: auto; display: flex; align-items: center; gap: 10px; flex: none; }
#state-word { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-dim); }
#state-clock { font: 400 13px/19px var(--mono); font-variant-numeric: tabular-nums;
  color: var(--paper-dim); min-width: 62px; }
#state-cluster.offline #state-word, #state-cluster.offline #state-clock { color: var(--paper-faint); }

.dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex: none; }
.dot.think { background: var(--think); }
.dot.say { background: var(--say); }
.dot.act { background: var(--act); }
.dot.vital { background: var(--vital); }
.dot.fault { background: var(--fault); }
.dot.taken { background: var(--taken); border-radius: 0; }
.dot.hollow { background: none; border: 1px solid var(--paper-faint); }
.dot.pulse { animation: pulse 2s ease-in-out infinite; }
.dot.breathe { animation: breathe 1.6s ease-in-out infinite; }
@keyframes pulse { 0%,100% { opacity: 1 } 50% { opacity: .35 } }
@keyframes breathe { 0%,100% { opacity: 1 } 50% { opacity: .3 } }

#mh-b { display: flex; align-items: center; gap: 26px; }
.chip { display: flex; align-items: center; gap: 8px; }
.chip b { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .12em; }
.chip.c-think b { color: var(--think); }
.chip.c-say b { color: var(--say); }
.chip.c-act b { color: var(--act); }
/* #provenance now carries the secondary premise sentence (or the offline
   notice). It ellipsizes rather than push #repo past the canvas edge: the
   compressed chips and #repo's 30 mono characters leave the premise most of
   #mh-b's 1872px before it has to shrink. */
#provenance { margin-left: auto; min-width: 0; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font: 400 13px/18px var(--sans); color: var(--paper-faint); }
#provenance.offline { color: var(--fault); }
#repo { flex: none; font: 400 13px/18px var(--mono); color: var(--paper-faint); }
/* The one link on the page: the at-home telemetry panel. Static text to an OBS
   source; a phone or laptop viewer can follow it. */
#to-telemetry { flex: none; font: 400 13px/18px var(--mono); color: var(--paper-faint);
  text-decoration: none; }
#to-telemetry:hover, #to-telemetry:focus-visible { color: var(--vital); text-decoration: underline; }
#death-sweep { position: absolute; left: 0; right: 0; bottom: -2px; height: 3px;
  background: var(--taken); z-index: 20; transform-origin: left; transform: scaleX(1); }
#death-sweep.sweeping { animation: sweep 900ms cubic-bezier(.22,.61,.36,1); }
@keyframes sweep { from { transform: scaleX(0) } to { transform: scaleX(1) } }

/* ---------- monologue ---------- */
#monologue { grid-column: 1; grid-row: 2; position: relative; }
#monologue-scroll { flex: 1; min-height: 0; overflow-y: auto; overflow-x: hidden;
  overscroll-behavior: contain;
  display: flex; flex-direction: column; scrollbar-width: none; }
#monologue-scroll::-webkit-scrollbar { width: 0; height: 0; }
#monologue-scroll.scrolled { -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 52px);
  mask-image: linear-gradient(to bottom, transparent 0, #000 52px); }
#monologue-scroll > .turn.is-first { margin-top: auto; border-top: none; }

.turn { display: grid; grid-template-columns: 160px 28px 918px; flex: none;
  padding: 12px 0 14px; border-top: 1px solid var(--rule); margin-top: 14px;
  border-radius: 6px; }
.turn.wake { animation: wake 420ms cubic-bezier(.22,.61,.36,1); }
@keyframes wake { from { opacity: 0; transform: translateY(10px) } to { opacity: 1; transform: none } }
.turn.wake .clamp.think { animation: surface 700ms ease 120ms both; }
@keyframes surface { from { opacity: 0 } to { opacity: 1 } }
/* Speech gets an entrance of its own: on arrival for a turn rendered whole,
   or the moment the typewriter reaches the spoken words. */
.turn.wake .clamp.say, .clamp.say.enter { animation: sayin 600ms cubic-bezier(.22,.61,.36,1); }
@keyframes sayin { 0% { filter: brightness(1.8); transform: translateY(5px) }
  100% { filter: none; transform: none } }
.turn.wake-rm { box-shadow: inset 2px 0 0 var(--act); }
.turn.depart { animation: depart 260ms ease-out forwards; }
@keyframes depart { to { opacity: 0; transform: translateY(-8px) } }
.turn.is-edit, .turn.is-error, .turn.is-end { padding-left: 14px;
  grid-template-columns: 146px 28px 918px; }
.turn.is-edit { background: var(--act-soft); box-shadow: inset 3px 0 0 var(--act); }
.turn.is-error { background: var(--fault-soft); box-shadow: inset 3px 0 0 var(--fault); }
.turn.is-end { border-top: 2px solid var(--chosen); background: rgba(127,215,182,.10);
  box-shadow: inset 3px 0 0 var(--chosen); }

.gutter { text-align: right; font: 400 13px/18px var(--mono); color: var(--paper-faint);
  font-variant-numeric: tabular-nums; padding-right: 0; }
.gutter .g-mark { display: block; }
.gutter .g-mark.edit { color: var(--act); }
.gutter .g-mark.end { font: 600 13px/18px var(--mono); letter-spacing: .12em; color: var(--chosen); }

.col { grid-column: 3; min-width: 0; }
.blk { position: relative; }
.blk + .blk, .col > .tool, .col > .err { margin-top: 8px; }
.clamp, .tool, .err { overflow-wrap: anywhere; }
.clamp { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }

.clamp.think { -webkit-line-clamp: 14; line-clamp: 14; font: 400 19px/29px var(--serif);
  color: var(--think); max-width: 68ch; white-space: pre-wrap; text-wrap: pretty; hyphens: none; }
.blk-think::before { content: ""; position: absolute; left: -14px; top: 2px; bottom: 2px;
  width: 2px; background: var(--think-rule); }
.blk-think.open::before { display: none; }

.clamp.say { -webkit-line-clamp: 6; line-clamp: 6; font: 500 18px/27px var(--sans);
  color: var(--say); max-width: 68ch; white-space: pre-wrap; text-wrap: pretty; }
.clamp.say::before { content: "\00AB "; font: 400 13px/27px var(--mono); color: var(--paper-faint); }

.tool { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
  -webkit-line-clamp: 3; line-clamp: 3; font: 400 14px/21px var(--mono); color: var(--act);
  white-space: pre-wrap; cursor: default; word-break: break-all; }
.tool .t-name { word-break: normal; }
.tool .t-args { opacity: .7; }
.subrow { display: grid; grid-template-columns: 22px 1fr 52px; column-gap: 8px;
  align-items: baseline; margin-top: 6px; font: 400 13px/20px var(--mono);
  color: var(--paper-faint); }
.subrow .s-mark { color: var(--rule-2); }
.subrow .s-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0; }
.subrow .s-time { text-align: right; font-variant-numeric: tabular-nums; }
.err { font: 400 14px/21px var(--mono); color: var(--fault); max-width: 76ch; }

.divider { height: 34px; display: flex; align-items: center; gap: 14px; margin: 14px 0; flex: none; }
.divider i { flex: 1; height: 1px; background: var(--taken); }
.divider span { font: 600 13px/18px var(--mono); text-transform: uppercase; letter-spacing: .14em;
  color: var(--taken); }

/* The metabolism strip: the one element that moves continuously and
   truthfully between turns. Every figure is derived from the recorder's own
   events (snap.pulse), never simulated. */
#pulse { height: 44px; flex: none; margin-top: 14px; border: 1px dashed var(--rule-2);
  border-radius: 6px; padding: 0 14px; display: flex; align-items: center; gap: 16px; }
#pulse-left { flex: none; font: 400 13px/18px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; white-space: nowrap; }
#pulse-spark { flex: none; width: 260px; margin-left: auto; display: flex;
  align-items: flex-end; gap: 3px; height: 24px; }
#pulse-spark .bar { flex: 1; min-width: 0; height: 0; background: var(--think);
  opacity: .8; border-radius: 1px 1px 0 0; }
#pulse-rate { flex: none; min-width: 120px; text-align: right; font: 400 13px/18px var(--mono);
  color: var(--paper-dim); font-variant-numeric: tabular-nums; }

#return-live { position: absolute; right: 22px; bottom: 18px; z-index: 15; cursor: pointer;
  background: var(--ink-2); border: 1px solid var(--rule-2); border-radius: 4px;
  padding: 4px 10px; font: 600 13px/18px var(--mono); letter-spacing: .12em;
  color: var(--vital); }
#return-live:focus-visible { outline: 2px solid var(--vital); outline-offset: 2px; }

/* THE EYE: the newest frame on the sense ring, floated in the monologue's
   right margin. pointer-events none so it never intercepts feed scrolling. */
#eye { position: absolute; top: 56px; right: 22px; width: 312px; z-index: 10;
  pointer-events: none; background: var(--ink-2); border: 1px solid var(--rule-2);
  border-radius: 8px; padding: 6px 6px 4px; box-shadow: 0 8px 28px rgba(0,0,0,.5); }
#eye img { display: block; width: 300px; height: 187px; object-fit: cover;
  background: var(--ink-0); border-radius: 4px; }
#eye-cap { font: 400 13px/18px var(--mono); color: var(--paper-faint); margin-top: 4px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  font-variant-numeric: tabular-nums; }

#coldstart { position: absolute; left: 22px; right: 22px; top: 60px; bottom: 18px;
  display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
  gap: 16px; padding-left: 30px; }
#cold-head { margin: 0; font: 600 21px/28px var(--sans); color: var(--paper); }
#cold-body { margin: 0; font: 400 17px/27px var(--serif); color: var(--paper-dim); max-width: 62ch;
  text-wrap: pretty; }

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
#bars .bar { flex: 1 1 0; min-width: 0; height: 2px; opacity: .8; border-radius: 2px 2px 0 0;
  position: relative; }
#bars .bar.k-declared { background: var(--chosen); }
#bars .bar.k-harness { background: var(--taken); }
#bars .bar.k-unknown { background: var(--broken); }
#bars .bar.now { background: var(--vital); opacity: 1; }
#bars .bar.now::after { content: ""; position: absolute; left: 0; right: 0; top: 0; height: 3px;
  background: var(--flash); animation: breathe 1.6s ease-in-out infinite; }
#bars .bar.lit { opacity: 1; outline: 1px solid var(--paper); outline-offset: 2px; }
#bar-labels { flex: none; height: 18px; display: flex; gap: 4px; }
.bl { flex: 1 1 0; min-width: 0; text-align: center; overflow: visible; white-space: nowrap;
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
   evidence sits under it. */
#now { display: flex; flex-direction: column; }
#now-colour { margin: 0; font: 500 22px/30px var(--sans); color: var(--paper);
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; line-clamp: 3;
  overflow: hidden; }
#now-evidence { margin-top: 8px; font: 400 13px/18px var(--mono); letter-spacing: .06em;
  text-transform: uppercase; color: var(--paper-dim); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; font-variant-numeric: tabular-nums; }
/* commentary:end */

/* ---------- ribbon ---------- */
#ribbon { grid-column: 1 / -1; grid-row: 3; display: grid; grid-template-columns: 1fr 1.6fr 1fr;
  gap: 20px; min-height: 0; }
#ribbon .panel { padding: 10px 18px; }
#ribbon .ptitle { margin-bottom: 8px; }
#stream-rows { flex: 1; min-height: 0; display: grid; grid-template-columns: repeat(2, 1fr);
  column-gap: 22px; align-content: start; overflow: hidden; }
.lane-row { display: grid; grid-template-columns: 12px 74px 1fr 44px; column-gap: 8px;
  align-items: center; height: 20px; font: 400 13px/20px var(--mono);
  font-variant-numeric: tabular-nums; }
.lane-row .l-dot { width: 6px; height: 6px; border-radius: 50%; align-self: center;
  background: none; border: 1px solid var(--paper-faint); }
.lane-row.live .l-dot { background: var(--act); border-color: var(--act); }
.lane-row.unbound { color: var(--paper-faint); }
.lane-row .l-name { color: var(--paper-dim); white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; }
.lane-row.given .l-name { color: var(--vital); }
.lane-row .l-track { height: 6px; min-width: 0; background: var(--rule); border-radius: 3px;
  overflow: hidden; }
.lane-row .l-fill { display: block; height: 100%; width: 0; background: var(--world); }
.lane-row.unbound .l-fill { background: var(--paper-faint); }
.lane-row .l-val { color: var(--paper-faint); text-align: right; white-space: nowrap;
  overflow: hidden; }
#stream-foot { margin-top: auto; font: 400 13px/18px var(--mono); color: var(--paper-faint);
  flex: none; }
.rows { flex: 1; min-height: 0; overflow: hidden; }
.rrow { display: grid; align-items: center; height: 21px; font: 400 14px/21px var(--mono); }
#selfmod-rows .rrow { grid-template-columns: 46px 1fr; }
#reached-rows .rrow { grid-template-columns: 96px 1fr 116px; column-gap: 14px; }
.rrow .rid { color: var(--paper-faint); font: 400 13px/21px var(--mono); }
.rrow .rdetail { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rrow .verb { text-transform: uppercase; }
.rrow .verb.v-act { color: var(--act); }
.rrow .verb.v-chosen { color: var(--chosen); }
.rrow .verb.v-fault { color: var(--fault); }
.rrow .rsum { color: var(--paper-dim); margin-left: 10px; }
.rrow .rsum.quoted { font: italic 400 14px/21px var(--serif); color: var(--think); }
.rrow .cmd { color: var(--world); text-transform: uppercase; display: flex;
  align-items: baseline; gap: 7px; min-width: 0; }
.rrow .cmd > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0; }
.ring { width: 6px; height: 6px; border-radius: 50%; border: 1px solid var(--world);
  display: inline-block; flex: none; align-self: center; }
.rrow .rverb { color: var(--paper-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rrow .rarg { color: var(--paper-faint); margin-left: 8px; }
.rows.is-sparse { display: flex; flex-direction: column; justify-content: center; }
.rrow.rowflash { animation: rowflash 900ms ease-out; }
@keyframes rowflash { 0% { background: var(--act-soft); box-shadow: inset 2px 0 0 var(--act) }
  100% { background: none; box-shadow: none } }
/* The per-edit diff view: #selfmod's rows yield to a capped excerpt of the
   actual unified diff for ~45s after the stage first sees an edit. Lines are
   rendered one span each via textContent, coloured only by their first
   character; the text itself is the agent's and is never marked up. */
/* The title carries the sighting caption during the diff view; it must
   never wrap out of its 24px box, and the count keeps its place beside it. */
#selfmod-title { min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#selfmod-count { flex: none; margin-left: 8px; }
/* 82px of panel below the title: 20px lines show four whole diff lines and
   clip on a line boundary rather than mid-glyph. */
#selfmod-diff { flex: 1; min-height: 0; overflow: hidden; font: 400 14px/20px var(--mono); }
#selfmod-diff .dline { white-space: pre; overflow: hidden; text-overflow: ellipsis;
  color: var(--paper-dim); }
#selfmod-diff .d-add { color: var(--vital); }
#selfmod-diff .d-rem { color: var(--fault); }
#selfmod-diff .d-hunk { color: var(--paper-faint); }
#reached.is-sparse #reached-foot { margin-top: 8px; }
.rrow .rmeta { text-align: right; font: 400 13px/21px var(--mono); color: var(--paper-faint);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.empty-mono { font: 400 14px/21px var(--mono); color: var(--paper-dim); }
#reached { position: relative; }
#reached.spoke { border-left: 2px solid var(--say); }
#said-stamp { font: 400 13px/18px var(--mono); color: var(--paper-faint); flex: none; }
#said-text { margin: 4px 0 0; font: 400 15px/23px var(--serif); color: var(--paper);
  display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
  -webkit-line-clamp: 2; line-clamp: 2; overflow-wrap: anywhere; flex: none; }
#reached.is-captioned #said-text { -webkit-line-clamp: 1; line-clamp: 1; }
#speak-caption { font: 400 13px/18px var(--mono); color: var(--paper-dim); flex: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#reached.is-captioned #speak-caption { margin-top: 4px; }
/* An utterance with no publication behind it: renderRibbon leaves #said-text
   empty rather than writing a placeholder that would contradict the caption
   directly below it, so the caption takes the panel's line instead. */
#reached.is-captioned { border-left: 2px solid var(--say); }
#reached.is-captioned:not(.spoke) #said-text { display: none; }
#reached.is-captioned:not(.spoke) #speak-caption { white-space: normal; overflow-wrap: anywhere;
  font: 400 15px/23px var(--serif); color: var(--paper); display: -webkit-box;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; }
#reached.is-sparse.is-captioned #reached-foot { margin-top: auto; }
#speak-audio { display: none; }
/* Absolutely positioned against #reached (not stacked in #reached-said's
   flow) so revealing it adds no height: a version that grew the panel's
   flow pushed #reached-foot below the panel's clip edge. */
#sound-on { position: absolute; right: 22px; bottom: 18px; cursor: pointer;
  background: none; border: 1px solid var(--rule-2); border-radius: 4px; padding: 2px 8px;
  font: 600 13px/18px var(--mono); letter-spacing: .12em; color: var(--say); }
#sound-on:focus-visible { outline: 2px solid var(--vital); outline-offset: 2px; }
#reached-foot { margin-top: auto; font: 400 13px/18px var(--mono); color: var(--paper-faint); flex: none;
  padding-right: 130px; }
#reached-said { flex: none; }
#reached-said:empty, #reached-said.is-quiet { display: none; }

/* Tail mode: during and after a typewriter reveal the think block is a
   fixed-height window kept scrolled to its end, so the newest words are
   always in motion and the turn ends with its conclusion visible. 406px is
   the clamp's own 14 lines x 29px. The class stays until eviction; the
   expansion rules below still override it while a block is open. */
.blk-think.tail .clamp { display: block; -webkit-line-clamp: none; line-clamp: none;
  max-height: 406px; overflow: hidden; }

/* ---------- expansion ---------- */
.blk.is-expandable { cursor: pointer; }
.blk.is-expandable:hover .clamp.think, .blk-think.open .clamp { color: var(--think); }
.blk.is-expandable:hover .clamp.say { color: var(--say); }
.blk.is-expandable:hover .more { opacity: 1; }
.blk.is-expandable:focus-visible { outline: 2px solid var(--vital); outline-offset: 4px; }
.more { height: 18px; margin-top: 6px; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; opacity: .78; }
.blk-think .more { color: var(--think); }
.blk-say .more { color: var(--say); }
.open-tail { margin-top: 8px; font: 600 13px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--paper-faint); }
.blk.open .clamp { -webkit-line-clamp: none; line-clamp: none; display: block;
  max-height: 420px; overflow-y: auto; overscroll-behavior: contain;
  background: var(--ink-2); border-radius: 6px; padding: 12px 16px; margin: 0 -16px;
  scrollbar-width: thin; scrollbar-color: var(--rule-2) transparent; }
.blk-think.open .clamp { border-left: 3px solid var(--think); }
.blk-say.open .clamp { border-left: 3px solid var(--say); }

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
  #life-figs { white-space: normal; }
  .g-eyebrow { white-space: normal; }
  #reached-rows .rrow { grid-template-columns: 82px 1fr 100px; column-gap: 8px; }
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
  #provenance { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>
</head>
<body>
<div id="stage">

  <header id="masthead">
    <div id="mh-a">
      <span id="wordmark">AURORA</span>
      <span class="vrule"></span>
      <p id="premise"></p>
      <div id="state-cluster">
        <span id="state-dot" class="dot hollow"></span>
        <span id="state-word">STANDING BY</span>
        <span id="state-clock"></span>
      </div>
    </div>
    <div id="mh-b">
      <span class="chip c-think"><i class="dot think"></i><b>THOUGHT</b></span>
      <span class="chip c-say"><i class="dot say"></i><b>SPEECH</b></span>
      <span class="chip c-act"><i class="dot act"></i><b>ACTION</b></span>
      <span id="provenance"></span>
      <span id="repo">github.com/tachyon-beep/aurora</span>
      <a id="to-telemetry" href="/telemetry">telemetry →</a>
    </div>
    <div id="death-sweep" hidden></div>
  </header>

  <section id="monologue" class="panel">
    <div class="ptitle"><span>THE MONOLOGUE</span><span>NEWEST AT THE BOTTOM</span></div>
    <div id="monologue-scroll">
      <div id="pulse" hidden>
        <span id="pulse-left"></span>
        <div id="pulse-spark" aria-hidden="true"></div>
        <span id="pulse-rate"></span>
      </div>
    </div>
    <button id="return-live" type="button" hidden>&#9662; RETURN TO LIVE</button>
    <div id="eye" hidden>
      <img id="eye-img" alt="">
      <div id="eye-cap"></div>
    </div>
    <div id="coldstart" hidden>
      <h2 id="cold-head"></h2>
      <p id="cold-body"></p>
    </div>
  </section>

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
    </section>
  </aside>

  <div id="ribbon">
    <section id="selfmod" class="panel">
      <div class="ptitle"><span id="selfmod-title">WHAT IT DID TO ITSELF</span><span id="selfmod-count"></span></div>
      <div class="rows" id="selfmod-rows"></div>
      <div id="selfmod-diff" hidden></div>
    </section>
    <section id="streams" class="panel">
      <div class="ptitle"><span>WHAT IT THINKS WITH</span><span id="stream-count"></span></div>
      <div id="stream-rows"></div>
      <div id="stream-foot"></div>
    </section>
    <section id="reached" class="panel">
      <div class="ptitle"><span>WHAT IT REACHED FOR</span><span id="reached-count"></span></div>
      <div id="reached-said" aria-live="polite">
        <div id="said-stamp"></div>
        <p id="said-text"></p>
        <div id="speak-caption"></div>
        <audio id="speak-audio" preload="auto"></audio>
        <button id="sound-on" type="button" hidden>▸ ENABLE SOUND</button>
      </div>
      <div class="rows" id="reached-rows"></div>
      <div id="reached-foot"></div>
    </section>
  </div>

</div>
<script>
"use strict";
var snap = null, skewMs = 0, failures = 0, lastIncarn = null, lastOrdinal = null;
var turnNodes = new Map(), dividers = new Map(), expanded = new Set();
var announceUntil = 0;
var REDUCED = false;
try { REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

var $ = function (id) { return document.getElementById(id); };
var mScroll = $("monologue-scroll"), pulseEl = $("pulse");

/* ---------- text helpers ---------- */
function setText(el, value) {
  if (!el) return false;
  value = value == null ? "" : String(value);
  if (el.__text === value) return false;
  el.textContent = value;
  el.__text = value;
  el.__dirty = true;
  return true;
}
function setClass(el, name, on) { if (el) el.classList.toggle(name, !!on); }

/* Each line is literally true of the running system and traceable to a hard
   invariant: the agent's container has network_mode none; the upstream key
   lives only in the recorder, which injects it; the agent's own key is a
   dummy; the proxy logs bodies and never headers. */
var PROVENANCE_LINES = [
  "the agent has no network interface · one unix socket to the model, nothing else",
  "the model key lives in the recorder · the agent runs with a dummy",
  "it can rewrite every line of itself · it cannot reach the machine it runs on",
  "the transcript is the proxy's, not the agent's · refreshed every 2s"
];
var PREMISE_LINE = "A language model has been given the file that runs it. " +
  "It cannot leave the box. It can end itself, and usually does.";
var provenanceAt = 0;
var transportOk = true;
var provenanceBeatShown = null;
var colourSeen = { key: null, atMs: 0, text: "" };
/* Beat kind -> the rotation line that answers it: a reach outside shows the
   no-network line next, a self-edit the rewrite line, an outward word the
   key line. Consulted once per beat, so the rotation never sticks. */
var BEAT_PREFERRED = { reached_out: 0, self_edit: 2, published: 1, spoke: 1 };
/* Records when the page first saw the current generated colour line, so the
   rotation can carry it only while that sighting is fresh. */
function noteColour() {
  var c = (snap && snap.commentary) || {}, colour = c.colour || {};
  if (!colour.generated || !colour.text) return;
  var key = String(colour.beat || "") + "|" + colour.text;
  if (colourSeen.key !== key) {
    colourSeen.key = key;
    colourSeen.atMs = Date.now();
    colourSeen.text = norm(colour.text);
  }
}
function provenanceLines() {
  var lines = PROVENANCE_LINES.slice();
  if (colourSeen.key && Date.now() - colourSeen.atMs < 120000) {
    lines.push(colourSeen.text + " — the stage");
  }
  return lines;
}
/* The containment facts hold the masthead slot (#premise); the premise
   sentence is the secondary line (#provenance). A death announcement borrows
   #premise for 4s, so the rotation stays silent while announceUntil is set
   and tick() calls showProvenance() when the announcement clears. */
function showProvenance() {
  if (announceUntil) return;
  var lines = provenanceLines();
  setText($("premise"), lines[provenanceAt % lines.length]);
}
/* The rotation is content, not decoration: under reduced motion the swap is
   instant but the rotation itself keeps running. */
function swapProvenance() {
  if (announceUntil) return;
  var lines = provenanceLines();
  var text = lines[provenanceAt % lines.length];
  var p = $("premise");
  if (REDUCED) { setText(p, text); return; }
  p.style.opacity = "0";
  setTimeout(function () {
    /* a death announcement that landed during the fade keeps the slot */
    if (!announceUntil) setText(p, text);
    p.style.opacity = "1";
  }, 250);
}
function rotateProvenance() {
  if (!transportOk) return;
  var c = (snap && snap.commentary) || {}, colour = c.colour || {};
  var beat = String(colour.beat || "");
  var pref = BEAT_PREFERRED[beat.split(":")[0]];
  if (beat && beat !== provenanceBeatShown && pref != null) {
    provenanceBeatShown = beat;
    provenanceAt = pref;
  } else {
    provenanceAt++;
  }
  swapProvenance();
}
showProvenance();
setText($("provenance"), PREMISE_LINE);
setInterval(rotateProvenance, 20000);
function fmt(n) {
  n = Math.max(0, Math.round(n));
  var s = String(n), out = "", c = 0;
  for (var i = s.length - 1; i >= 0; i--) {
    out = s[i] + out; c++;
    if (c % 3 === 0 && i > 0) out = "," + out;
  }
  return out;
}
function pad2(n) { n = String(n); return n.length < 2 ? "0" + n : n; }
function norm(t) { return (t == null ? "" : String(t)).replace(/\s+/g, " ").trim(); }
function clock() { return Date.now() + skewMs; }

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
function agoLong(ep, nowMs) {
  if (ep == null) return "";
  var s = Math.max(0, nowMs / 1000 - ep);
  var n, unit;
  if (s < 60) { n = Math.floor(s) || 1; unit = "second"; }
  else if (s < 3600) { n = Math.floor(s / 60); unit = "minute"; }
  else if (s < 86400) { n = Math.floor(s / 3600); unit = "hour"; }
  else { n = Math.floor(s / 86400); unit = "day"; }
  return n + " " + unit + (n === 1 ? "" : "s") + " ago";
}
var WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
  "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen",
  "eighteen", "nineteen", "twenty"];
function numWord(n, limit) {
  limit = limit == null ? 10 : limit;
  return (n >= 0 && n <= limit && n < WORDS.length) ? WORDS[n] : String(n);
}
function hhmmss(ts) {
  if (!ts) return "";
  var m = /(\d{2}):(\d{2}):(\d{2})/.exec(String(ts));
  return m ? m[0] + "Z" : "";
}
function bytes(n) {
  n = Number(n) || 0;
  if (n < 1024) return n + " B";
  if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
  return (n / 1048576).toFixed(1) + " MB";
}
function el(tag, cls, parent) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (parent) parent.appendChild(n);
  return n;
}

/* ---------- expansion ---------- */
function boxOf(clampEl) { return clampEl.closest(".blk"); }

function labelFor(box) {
  var total = Number(box.dataset.total || 0);
  var srv = box.dataset.strunc === "1";
  if (box.classList.contains("open")) return "▴ COLLAPSE";
  if (srv) return "▾ " + fmt(Math.max(1, total - 8000)) + " MORE — FIRST 8,000 SHOWN";
  return "▾ READ THE REST";
}
function setAffordanceLabel(box) {
  var more = box.querySelector(".more");
  if (more) setText(more.querySelector(".more-label"), labelFor(box));
  var tail = box.querySelector(".open-tail");
  if (tail) {
    var open = box.classList.contains("open"), srv = box.dataset.strunc === "1";
    if (open && srv) {
      tail.hidden = false;
      setText(tail, "SHOWING THE FIRST 8,000 OF " + fmt(Number(box.dataset.total || 0)) +
        " CHARACTERS — THE REST IS IN THE CONSOLE");
    } else { tail.hidden = true; }
  }
}
function toggle(box) {
  var key = box.dataset.ekey;
  if (!key) return;
  if (expanded.has(key)) { expanded.delete(key); box.classList.remove("open"); }
  else {
    expanded.add(key); box.classList.add("open");
    try { flagProgrammatic(); box.scrollIntoView({ block: "nearest" }); } catch (e) {}
  }
  setAffordanceLabel(box);
}
function bind(box) {
  if (box.__bound) return;
  box.__bound = true;
  box.addEventListener("click", function (ev) {
    if (!box.classList.contains("is-expandable")) return;
    ev.stopPropagation();
    toggle(box);
  });
  box.addEventListener("keydown", function (ev) {
    if (!box.classList.contains("is-expandable")) return;
    if (ev.key === "Enter" || ev.key === " " || ev.key === "Spacebar") {
      ev.preventDefault();
      toggle(box);
    }
  });
}
function setAffordance(clampEl, trunc) {
  var box = boxOf(clampEl);
  if (!box) return;
  var more = box.querySelector(".more");
  if (!trunc) {
    box.classList.remove("is-expandable");
    if (box.dataset.ekey) expanded.delete(box.dataset.ekey);
    box.classList.remove("open");
    box.removeAttribute("role");
    box.removeAttribute("tabindex");
    if (more) more.hidden = true;
    var t = box.querySelector(".open-tail");
    if (t) t.hidden = true;
    return;
  }
  box.classList.add("is-expandable");
  box.setAttribute("role", "button");
  box.setAttribute("tabindex", "0");
  if (more) more.hidden = false;
  bind(box);
  setAffordanceLabel(box);
}
function measureTruncation() {
  var list = document.querySelectorAll(".clamp");
  for (var i = 0; i < list.length; i++) {
    var c = list[i], box = boxOf(c);
    if (!box) continue;
    if (!c.__dirty && c.__measured) continue;
    c.__dirty = false; c.__measured = true;
    var wasOpen = box.classList.contains("open");
    if (wasOpen) box.classList.remove("open");
    var trunc = c.scrollHeight > c.clientHeight + 2;
    if (wasOpen) box.classList.add("open");
    setAffordance(c, trunc);
  }
}
function applyExpansion() {
  var list = document.querySelectorAll(".blk");
  for (var i = 0; i < list.length; i++) {
    var b = list[i];
    var want = !!(b.dataset.ekey && expanded.has(b.dataset.ekey) &&
      b.classList.contains("is-expandable"));
    b.classList.toggle("open", want);
    setAffordanceLabel(b);
  }
}
function markBlock(box, key, text, chars, truncated) {
  if (box.dataset.ekey !== key) box.dataset.ekey = key;
  var total = Math.max(Number(chars) || 0, text.length);
  box.dataset.total = String(total);
  box.dataset.shown = String(text.length);
  box.dataset.strunc = truncated ? "1" : "0";
}

/* ---------- reveal ---------- */
/* The typewriter: the newest turn's landed reasoning and speech appear word
   by word, paced to the measured cadence of recent turns. The engine only
   ever reveals text that already arrived — timestamps and state stay real —
   and a newer arrival fast-forwards the reveal to completion instantly. */
var revealState = { key: null, timer: null, node: null, thinkParts: null,
  sayParts: null, at: 0, total: 0, perStep: 0, budgetMs: 0, sayBegun: false };
/* The speech block stays out of view until the typewriter reaches it: an
   empty say clamp would otherwise sit under the thinking with only its
   opening mark showing. */
function enterSay(node) {
  if (revealState.sayBegun || !node || !node.__say) return;
  revealState.sayBegun = true;
  node.__say.hidden = false;
  var c = node.__say.__clamp;
  c.classList.add("enter");
  setTimeout(function () { c.classList.remove("enter"); }, 900);
}
function medianGap() {
  var list = (snap && snap.turns) || [], eps = [], i;
  for (i = 0; i < list.length; i++) {
    if (list[i].kind === "subcall" || list[i].epoch == null) continue;
    eps.push(list[i].epoch);
  }
  var gaps = [];
  for (i = Math.max(1, eps.length - 5); i < eps.length; i++) {
    gaps.push(Math.max(0, eps[i] - eps[i - 1]));
  }
  if (!gaps.length) return null;
  gaps.sort(function (a, b) { return a - b; });
  return gaps[Math.floor(gaps.length / 2)];
}
function revealActiveFor(key) { return revealState.timer != null && revealState.key === key; }
function renderRevealFrame() {
  var node = revealState.node;
  if (!node) return;
  var n = Math.min(revealState.total, Math.floor(revealState.at));
  var tp = revealState.thinkParts, sp = revealState.sayParts;
  if (node.__think && tp.length) {
    var c = node.__think.__clamp;
    setText(c, tp.slice(0, Math.min(n, tp.length)).join(""));
    c.scrollTop = c.scrollHeight;
  }
  if (node.__say && sp.length) {
    var shown = Math.max(0, n - tp.length);
    if (shown > 0) enterSay(node);
    setText(node.__say.__clamp, sp.slice(0, shown).join(""));
  }
  /* Each frame grows the newest turn; a pinned feed follows it so the
     leading edge never slides under the panel between polls. */
  repin();
}
function clearRevealWork() {
  revealState.node = null;
  revealState.thinkParts = null;
  revealState.sayParts = null;
  revealState.at = 0;
  revealState.total = 0;
  revealState.sayBegun = false;
}
function finishReveal() {
  if (revealState.timer != null) { clearInterval(revealState.timer); revealState.timer = null; }
  var node = revealState.node;
  if (node) {
    if (node.__think && revealState.thinkParts && revealState.thinkParts.length) {
      var c = node.__think.__clamp;
      setText(c, revealState.thinkParts.join(""));
      c.scrollTop = c.scrollHeight;
    }
    if (node.__say && revealState.sayParts && revealState.sayParts.length) {
      enterSay(node);
      setText(node.__say.__clamp, revealState.sayParts.join(""));
    }
    repin();
  }
  clearRevealWork();
}
function stopReveal() {
  if (revealState.timer != null) { clearInterval(revealState.timer); revealState.timer = null; }
  if (revealState.node && revealState.node.__say) revealState.node.__say.hidden = false;
  revealState.key = null;
  clearRevealWork();
}
function revealTick() {
  revealState.at += revealState.perStep;
  if (revealState.at >= revealState.total) { finishReveal(); return; }
  renderRevealFrame();
}
function beginReveal(node, t) {
  var thinkParts = (t.reasoning || "").match(/\s*\S+/g) || [];
  var sayParts = (t.content || "").match(/\s*\S+/g) || [];
  var total = thinkParts.length + sayParts.length;
  if (!total) return;
  var g = medianGap();
  var budgetMs = Math.max(3000, Math.min(30000, 600 * (g || 0)));
  revealState.node = node;
  revealState.thinkParts = thinkParts;
  revealState.sayParts = sayParts;
  revealState.total = total;
  revealState.at = 0;
  revealState.perStep = total / (budgetMs / 50);
  revealState.budgetMs = budgetMs;
  if (node.__think && thinkParts.length) node.__think.classList.add("tail");
  if (node.__say && sayParts.length && thinkParts.length) node.__say.hidden = true;
  renderRevealFrame();
  revealState.timer = setInterval(revealTick, 50);
}
function maybeStartReveal() {
  var list = (snap && snap.turns) || [], newest = null;
  for (var i = list.length - 1; i >= 0; i--) {
    if (list[i].kind !== "subcall") { newest = list[i]; break; }
  }
  if (!newest) return;
  var key = turnKey(newest);
  if (key === revealState.key) return;
  finishReveal();
  revealState.key = key;
  if (REDUCED) return;
  if (newest.epoch == null || clock() / 1000 - newest.epoch >= 90) return;
  var node = turnNodes.get(key);
  if (!node) return;
  beginReveal(node, newest);
}

/* ---------- feed ---------- */
function turnKey(t) { return snap.stats.incarnation + ":" + t.index; }

function makeBlk(col, kind) {
  var box = el("div", "blk blk-" + kind, col);
  var c = el("div", "clamp " + kind, box);
  el("div", "open-tail", box).hidden = true;
  var more = el("div", "more", box);
  el("span", "more-label", more);
  more.hidden = true;
  box.__clamp = c;
  return box;
}
function buildTurn() {
  var node = el("div", "turn");
  var g = el("div", "gutter", node);
  node.__gid = el("span", "g-id", g); el("br", null, g);
  node.__gtime = el("span", "g-time", g); el("br", null, g);
  node.__gdelta = el("span", "g-delta", g); el("br", null, g);
  node.__gmark = el("span", "g-mark", g);
  el("div", "spacer", node);
  node.__col = el("div", "col", node);
  node.__tools = [];
  node.__subs = [];
  return node;
}
function ensureThink(node) {
  if (!node.__think) node.__think = makeBlk(node.__col, "think");
  return node.__think;
}
function ensureSay(node) {
  if (!node.__say) {
    node.__say = makeBlk(node.__col, "say");
    if (node.__think) node.__col.insertBefore(node.__say, node.__think.nextSibling);
  }
  return node.__say;
}
function errLine(err) {
  var msg = "", code = null;
  if (err && typeof err === "object") { msg = err.message == null ? "" : String(err.message); code = err.code; }
  else if (err != null) { msg = String(err); }
  var n = typeof code === "number" ? code : parseInt(code, 10);
  var prefix = "REQUEST FAILED";
  if (n >= 400 && n < 500) prefix = "UPSTREAM REFUSED THE CONVERSATION";
  else if (n >= 500) prefix = "UPSTREAM FAILED";
  var head = prefix + (code == null || code === "" ? "" : " (" + code + ")");
  return msg ? head + " — " + msg : head + ".";
}
function updateTurn(node, t, prevEpoch, sameLife) {
  var st = snap.stats, key = turnKey(t);
  setText(node.__gid, (st.turns_this_life_exact ? "TURN " : "ROW ") + pad2(t.index));
  setText(node.__gtime, hhmmss(t.timestamp));
  var delta = "";
  if (sameLife && prevEpoch != null && t.epoch != null) {
    var d = Math.max(0, Math.round(t.epoch - prevEpoch));
    delta = "+" + d + "s";
  }
  setText(node.__gdelta, delta);
  var mark = "", mcls = "";
  if (t.is_end) { mark = "ENDING"; mcls = "end"; }
  else if (t.is_edit) { mark = "✎"; mcls = "edit"; }
  setText(node.__gmark, mark);
  node.__gmark.className = "g-mark" + (mcls ? " " + mcls : "");

  setClass(node, "is-edit", t.is_edit);
  setClass(node, "is-end", t.is_end);
  setClass(node, "is-error", !!t.error);

  /* While the reveal engine is animating this turn's blocks, their text is
     the engine's to write: setText here would jump the typewriter to the end
     on the next poll. The engine writes the same landed text, only later. */
  var revealing = revealActiveFor(key);
  var reasoning = t.reasoning || "";
  if (reasoning) {
    var tb = ensureThink(node);
    markBlock(tb, key + ":think", reasoning, t.reasoning_chars, t.reasoning_truncated);
    if (!revealing) setText(tb.__clamp, reasoning);
  }
  var content = t.content || "";
  if (content) {
    var sb = ensureSay(node);
    markBlock(sb, key + ":say", content, t.content_chars, t.content_truncated);
    if (!revealing) setText(sb.__clamp, content);
  }
  var calls = t.tool_calls || [];
  for (var i = 0; i < calls.length; i++) {
    var row = node.__tools[i];
    if (!row) {
      row = el("div", "tool", node.__col);
      row.__name = el("span", "t-name", row);
      row.__args = el("span", "t-args", row);
      node.__tools[i] = row;
    }
    setText(row.__name, String(calls[i].name || "tool").toUpperCase() + " ");
    setText(row.__args, calls[i].arguments || "");
  }
  if (t.error) {
    if (!node.__err) node.__err = el("div", "err", node.__col);
    setText(node.__err, errLine(t.error));
  }
}
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
    row.__text.title = prompt;
    setText(row.__time, hhmmss(s.timestamp));
  }
  for (var k = subs.length; k < node.__subs.length; k++) node.__subs[k].hidden = true;
}
function buildDivider(life) {
  var d = el("div", "divider");
  el("i", null, d);
  setText(el("span", null, d), "INCARNATION " + life + " ENDED HERE");
  el("i", null, d);
  return d;
}
function reconcileFeed() {
  var list = snap.turns || [], wanted = new Set(), i;
  for (i = 0; i < list.length; i++) {
    if (list[i].kind !== "subcall") wanted.add(turnKey(list[i]));
  }
  turnNodes.forEach(function (node, key) {
    if (!wanted.has(key)) {
      turnNodes.delete(key);
      expanded.delete(key + ":think"); expanded.delete(key + ":say");
      if (key === revealState.key) stopReveal();
      if (REDUCED) { node.remove(); return; }
      node.classList.add("depart");
      var gone = false;
      var drop = function () { if (!gone) { gone = true; node.remove(); } };
      node.addEventListener("animationend", drop, { once: true });
      setTimeout(drop, 400);
    }
  });
  dividers.forEach(function (node, key) {
    if (!wanted.has(key)) { node.remove(); dividers.delete(key); }
  });

  var prevLife = null, prevEpoch = null;
  for (i = 0; i < list.length; i++) {
    var t = list[i];
    if (t.kind === "subcall") continue;
    var key = turnKey(t), subs = [];
    for (var s = i + 1; s < list.length && list[s].kind === "subcall"; s++) subs.push(list[s]);
    var sameLife = !(t.life != null && prevLife != null && t.life !== prevLife);
    if (!sameLife && !dividers.has(key)) {
      var d = buildDivider(prevLife);
      mScroll.insertBefore(d, pulseEl);
      dividers.set(key, d);
    }
    var node = turnNodes.get(key);
    if (!node) {
      node = buildTurn();
      if (REDUCED) {
        node.classList.add("wake-rm");
        (function (n) { setTimeout(function () { n.classList.remove("wake-rm"); }, 3000); })(node);
      } else {
        node.classList.add("wake");
        (function (n) {
          n.addEventListener("animationend", function () { n.classList.remove("wake"); }, { once: true });
          setTimeout(function () { n.classList.remove("wake"); }, 900);
        })(node);
      }
      mScroll.insertBefore(node, pulseEl);
      turnNodes.set(key, node);
    }
    updateTurn(node, t, prevEpoch, sameLife);
    updateSubRows(node, subs);
    prevLife = t.life; prevEpoch = t.epoch;
  }
  maybeStartReveal();

  var olds = mScroll.querySelectorAll(".turn.is-first");
  for (i = 0; i < olds.length; i++) olds[i].classList.remove("is-first");
  var first = mScroll.querySelector(".turn:not(.depart)");
  if (first) first.classList.add("is-first");

  var alive = (snap.stats.turns_this_life || 0) > 0;
  $("coldstart").hidden = alive;
  mScroll.style.visibility = alive ? "" : "hidden";
  if (!alive) renderColdStart();
}
/* ---------- pin ---------- */
/* Scripted scrolls set `programmatic`, and the scroll listener consumes the
   flag instead of unpinning: only a person's own scroll releases the feed.
   An unpinned feed shows the RETURN TO LIVE chip and repins itself after
   three minutes with nothing expanded, so an unattended browser source can
   never silently freeze. */
var feedPinned = true, programmatic = false, lastUserScrollMs = 0;
/* The scroll event for a scripted scroll dispatches in the next rendering
   step, before that frame's animation callbacks; the frame callback clears
   the flag afterwards so a scroll that never fired an event (already in
   view) cannot leave the flag lingering to swallow a person's first scroll. */
function flagProgrammatic() {
  programmatic = true;
  try { requestAnimationFrame(function () { programmatic = false; }); } catch (e) {}
}
function repin() {
  if (feedPinned && mScroll.scrollTop + mScroll.clientHeight < mScroll.scrollHeight - 1) {
    flagProgrammatic();
    mScroll.scrollTop = mScroll.scrollHeight;
  }
  setClass(mScroll, "scrolled", mScroll.scrollTop > 0);
}
function updateReturnChip() { $("return-live").hidden = feedPinned; }
function onFeedScroll() {
  if (programmatic) { programmatic = false; return; }
  lastUserScrollMs = Date.now();
  feedPinned = mScroll.scrollTop + mScroll.clientHeight >= mScroll.scrollHeight - 8;
  setClass(mScroll, "scrolled", mScroll.scrollTop > 0);
  updateReturnChip();
}
function returnToLive() {
  feedPinned = true;
  repin();
  updateReturnChip();
}
function maybeAutoRepin() {
  if (feedPinned || !lastUserScrollMs) return;
  if (Date.now() - lastUserScrollMs <= 180000) return;
  if (expanded.size !== 0) return;
  returnToLive();
}

/* ---------- coldstart ---------- */
function renderColdStart() {
  var st = snap.stats, lin = snap.lineage || [], nowMs = clock();
  if (lin.length) {
    setText($("cold-head"), "NO ONE IS HOME.");
    var when = lin[0].ended_epoch != null
      ? "The last incarnation ended " + agoLong(lin[0].ended_epoch, nowMs) + "."
      : "The last incarnation has ended.";
    var before = Math.max(0, (st.incarnation || 1) - 1);
    var tail = before > 0
      ? " It will wake knowing nothing about the " + numWord(before, 20) +
        (before === 1 ? " that came before it." : " that came before it.")
      : "";
    setText($("cold-body"), when +
      " The harness is seeding a new one from agent_stock.py — the clean copy it is not allowed to edit." +
      tail);
  } else {
    setText($("cold-head"), "INCARNATION " + (st.incarnation || 1) + " HAS NOT SPOKEN YET.");
    setText($("cold-body"), "Nothing has ever happened here.");
  }
}

/* ---------- masthead / state ---------- */
function stateOf(age) {
  var st = snap.stats;
  var any = (st.transcript_turns || 0) > 0 || (snap.turns || []).length > 0;
  if (!any) return "standby";
  if ((st.turns_this_life || 0) === 0) return (snap.lineage || []).length ? "between" : "standby";
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

function setState(age) {
  var s = stateOf(age);
  var dot = $("state-dot");
  if (dot.className !== STATE_DOT[s]) dot.className = STATE_DOT[s];
  setText($("state-word"), STATE_WORD[s]);
  var clockText = "";
  if (s === "live" || s === "thinking" || s === "quiet" || s === "nosignal") {
    clockText = age == null ? "" : dur(age);
  }
  setText($("state-clock"), clockText);

  setClass($("lineage"), "nosig", s === "nosignal");
  return s;
}
function setTransport(ok) {
  /* Offline overwrites only the secondary line: the masthead's containment
     line was never disturbed, so recovery has nothing to restore there. */
  var p = $("provenance");
  transportOk = !!ok;
  setClass(p, "offline", !ok);
  setClass($("state-cluster"), "offline", !ok);
  if (!ok) setText(p, "STAGE OFFLINE · this page cannot reach the stage");
  else setText(p, PREMISE_LINE);
}

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
function labelStep(count, width) {
  if (!(width > 0)) return count > 24 ? 5 : 1;
  var per = width / count;
  return per >= 22 ? 1 : per >= 12 ? 5 : 10;
}
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
  var step = labelStep(barLives.length, $("bar-labels").clientWidth || 0);
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
  /* The census counts self-edits over the whole live transcript; the event
     list is a capped tail and only stands in before the census has run. */
  if (typeof st.self_edits_this_life === "number") {
    lifeEdits = st.self_edits_this_life;
  } else {
    var ev = snap.events || [];
    lifeEdits = 0;
    for (var e = 0; e < ev.length; e++) if (ev[e].kind === "write" || ev[e].kind === "migrate") lifeEdits++;
  }
  renderLifeFigs(nowMs);
  renderSource();

  var shownDead = Math.max(0, barLives.length - 1);
  lineageFootLines = lineageFootFor(st, rec, shownDead, snap.achievements);
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
  var kind = l ? kindOf(l.kind) : "unknown";
  setClass(spot, "k-declared", kind === "declared");
  setClass(spot, "k-harness", kind === "harness");
  setClass(spot, "k-unknown", kind === "unknown");
  setClass(spot, "empty", !l);
  if (!l) {
    setText($("spot-eyebrow"), "");
    setText($("spot-facts"), "");
    setText($("spot-note"), "No one has died here yet.");
    spotShown = null;
    litBar(null);
    return;
  }
  var ord = l.ordinal == null ? "?" : l.ordinal;
  var key = ord + ":" + kind;
  var eyebrow = "INCARNATION " + ord + " · " + KIND_LABEL[kind];
  if (l.ended_epoch != null) eyebrow += " · " + rel(l.ended_epoch, nowMs);
  setText($("spot-eyebrow"), eyebrow);
  setText($("spot-facts"), spotFacts(l));
  var note = norm(l.sentence || l.summary || "");
  setText($("spot-note"), note);
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
   only and is used just when the book is absent. Achievement nominations —
   the stage's own reading of a dead life's transcript, phrased as fact — join
   the rotation only when one exists, newest first, at most FOOT_ACHIEVEMENTS,
   each bylined because it is generated. */
var FOOT_ACHIEVEMENTS = 3;
function lineageFootFor(st, book, shown, achievements) {
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
  var ach = achievements || [];
  for (var i = 0; i < ach.length && i < FOOT_ACHIEVEMENTS; i++) {
    var a = ach[i];
    if (!a || a.ordinal == null || !a.line) continue;
    lines.push("incarnation " + a.ordinal + ": " + norm(a.line) + " — the stage");
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
   the beat's counted fact and falls back to the play phrase. */
function renderNow() {
  var c = (snap.commentary || {}), play = c.play || {}, colour = c.colour || {};
  setText($("now-colour"), colour.text || "");
  var line = colour.evidence || play.phrase || "waiting for the first word";
  var age = play.epoch == null ? null : Math.max(0, clock() / 1000 - play.epoch);
  setText($("now-evidence"), line + (age == null ? "" : " · " + dur(age)));
}

/* ---------- eye ---------- */
/* The newest frame on the sense ring. The caption claims only what is true:
   this is the ring its eye reads, aged from the frame's own capture time. */
function renderEye() {
  var eye = $("eye"), sense = snap.sense;
  if (!sense || !sense.url) { eye.hidden = true; return; }
  eye.hidden = false;
  var img = $("eye-img");
  if (img.__src !== sense.url) { img.__src = sense.url; img.src = sense.url; }
  eye.__feed = sense.feed;
  eye.__epoch = sense.captured_epoch;
  setEyeCaption(clock());
}
function setEyeCaption(nowMs) {
  var eye = $("eye");
  if (eye.hidden) return;
  setText($("eye-cap"), "THE EYE · feed " + eye.__feed +
    (eye.__epoch != null ? " · " + rel(eye.__epoch, nowMs) : ""));
}

/* ---------- ribbon ---------- */
function clearRows(host) { while (host.firstChild) host.removeChild(host.firstChild); }
/* The server counts every result the diode has filed and places each in a life;
   `snap.diode.outputs` is only the newest few rows, so counting those froze the
   figure once the display filled. */
function reachedThisLife() { return (snap.diode && snap.diode.operations_life) || 0; }
function renderRibbon() {
  var ev = (snap.events || []).slice(-4).reverse(), host = $("selfmod-rows");
  clearRows(host);
  setText($("selfmod-count"), (snap.events || []).length + " THIS LIFE");
  if (!ev.length) el("div", "empty-mono", host).textContent = "It has not altered its own source this life.";
  for (var i = 0; i < ev.length; i++) {
    var e = ev[i], row = el("div", "rrow", host);
    el("span", "rid", row).textContent = "t" + pad2(e.index);
    var d = el("span", "rdetail", row);
    var vcls = e.kind === "done" ? "v-chosen" : e.kind === "reset" ? "v-fault" : "v-act";
    el("span", "verb " + vcls, d).textContent = String(e.headline || "").toUpperCase();
    var sum = norm(e.summary || "");
    if (sum) {
      var s = el("span", "rsum" + (e.quoted ? " quoted" : ""), d);
      if (e.quoted) {
        el("span", "q", s).textContent = "“";
        el("span", null, s).textContent = sum;
        el("span", "q", s).textContent = "”";
      } else s.textContent = sum;
    }
  }
  setClass(host, "is-sparse", !ev.length);

  /* After a fresh edit the panel shows what actually changed: the capped
     diff excerpt, captioned with when the stage first saw it (the stage
     cannot truthfully claim when the agent made it; the age counts up from
     that first sighting). The rows return once the 45s window passes; the
     count in the title holds either way. */
  var code = snap.code || {}, edit = code.latest_edit;
  var editAge = edit && edit.epoch != null ? clock() / 1000 - edit.epoch : null;
  var showDiff = editAge != null && editAge < 45;
  var diffHost = $("selfmod-diff");
  clearRows(diffHost);
  if (showDiff) {
    setText($("selfmod-title"),
      (edit.restored ? "SOURCE RESTORED TO SEED · seen " : "WHAT IT JUST CHANGED · seen ") +
      Math.max(0, Math.floor(editAge)) + "s ago");
    var dlines = String(edit.excerpt || "").split("\n");
    for (var dl = 0; dl < dlines.length; dl++) {
      var ln = dlines[dl], dcls = "dline";
      if (ln.indexOf("@@") === 0) dcls += " d-hunk";
      else if (ln.charAt(0) === "+") dcls += " d-add";
      else if (ln.charAt(0) === "-") dcls += " d-rem";
      el("div", dcls, diffHost).textContent = ln;
    }
  } else {
    setText($("selfmod-title"), "WHAT IT DID TO ITSELF");
  }
  diffHost.hidden = !showDiff;
  host.hidden = showDiff;

  var outs = ((snap.diode && snap.diode.outputs) || []).slice(0, 4), ahost = $("reached-rows");
  clearRows(ahost);
  /* The rows are the newest few; the count is the server's, taken over every
     result the diode has filed, so it does not stop rising at the last row. */
  setText($("reached-count"), reachedThisLife() + " THIS LIFE");
  for (i = 0; i < outs.length; i++) {
    var o = outs[i], r = el("div", "rrow", ahost);
    var c = el("span", "cmd", r);
    el("i", "ring", c);
    el("span", null, c).textContent = String(o.command || o.slug || "").toUpperCase();
    var v = el("span", "rverb", r);
    v.title = norm(o.verb || "");
    el("span", null, v).textContent = o.verb || "";
    var arg = norm(o.argument || "");
    if (arg) {
      var argEl = el("span", "rarg", v);
      argEl.textContent = arg;
      argEl.title = arg;
    }
    var meta = el("span", "rmeta", r);
    meta.__epoch = o.epoch;
    meta.__size = bytes(o.size);
    meta.className = "rmeta agerow";
  }
  setClass(ahost, "is-sparse", outs.length < 2);

  var pub = (snap.diode && snap.diode.published) || [];
  var total = (snap.diode && snap.diode.published_total) || 0;
  setClass($("reached"), "spoke", total > 0);
  if (pub.length) {
    setText($("said-stamp"), "↗ PUBLISHED · " + hhmmss(new Date((pub[0].epoch || 0) * 1000).toISOString()));
    var p = $("said-text");
    p.textContent = ""; p.__text = null;
    el("span", "q", p).textContent = "“";
    el("span", null, p).textContent = norm(pub[0].text || "");
    el("span", "q", p).textContent = "”";
  } else {
    setText($("said-stamp"), "");
    setText($("said-text"), "");
  }
  setClass($("reached"), "is-sparse", $("said-text").scrollHeight <= 34);

  /* publish and speak file a result in output/ like every other command, so
     they are already inside operations_total; adding their own totals on top
     counted the same reach outside twice. */
  var everReached = (snap.diode && snap.diode.operations_total) || 0;
  setClass($("reached-said"), "is-quiet", !(snap.diode.published || []).length &&
    !(snap.diode.spoken || []).length);
  setText($("reached-foot"), everReached
    ? everReached + " time" + (everReached === 1 ? "" : "s") + " across every life"
    : "It has never reached outside the box.");
}

var SPOKEN_MEMORY = 50;
var spokenPlayed = (function () {
  /* Restored from storage so a reload cannot replay what was already played;
     the age gate below is the second guard, not the only one. */
  var seen = {};
  try {
    var list = JSON.parse(window.localStorage.getItem("spokenPlayed") || "[]");
    for (var i = 0; i < list.length; i++) seen[list[i]] = true;
  } catch (e) {}
  return seen;
})();
var spokenQueue = [], spokenBusy = false, spokenCurrent = null;
function setSpokenCaption(text) {
  var cap = $("speak-caption");
  if (!cap) return;
  var caption = norm(text || "");
  setText(cap, caption);
  setClass($("reached"), "is-captioned", !!caption);
}
function markSpokenPlayed(name) {
  spokenPlayed[name] = true;
  /* Names are UTC stamps, so lexical order is chronological and the tail is newest. */
  var names = Object.keys(spokenPlayed).sort().slice(-SPOKEN_MEMORY), kept = {};
  for (var i = 0; i < names.length; i++) kept[names[i]] = true;
  spokenPlayed = kept;
  try { window.localStorage.setItem("spokenPlayed", JSON.stringify(names)); } catch (e) {}
}
function spokenKnown(name) {
  if (spokenPlayed[name]) return true;
  if (spokenCurrent && spokenCurrent.name === name) return true;
  for (var i = 0; i < spokenQueue.length; i++) {
    if (spokenQueue[i].name === name) return true;
  }
  return false;
}
function attemptSpoken() {
  var a = $("speak-audio"), mine = spokenCurrent;
  if (!a || !mine) return;
  try {
    var p = a.play();
    if (p && p.catch) p.catch(function (e) {
      if (mine !== spokenCurrent) return;
      if (e && e.name === "NotAllowedError") {
        soundBlocked();
        return;
      }
      spokenAdvance();
    });
  } catch (e) {
    if (mine !== spokenCurrent) return;
    if (e && e.name === "NotAllowedError") soundBlocked();
    else spokenAdvance();
  }
}
function playNextSpoken() {
  var a = $("speak-audio");
  if (!a || spokenBusy || !spokenQueue.length) return;
  spokenBusy = true;
  spokenCurrent = spokenQueue.shift();
  setSpokenCaption(spokenCurrent.text);
  a.src = "/audio/" + encodeURIComponent(spokenCurrent.name);
  attemptSpoken();
}
function spokenAdvance() {
  /* Reached on end or an unrecoverable load/play failure. The current name is
     persisted before advancing so a reload retries only work that never
     started; autoplay refusal does not call this function. */
  if (spokenCurrent) markSpokenPlayed(spokenCurrent.name);
  spokenCurrent = null;
  spokenBusy = false;
  playNextSpoken();
}
/* An OBS browser source is allowed to autoplay, so this never runs there. A
   person opening the tunnelled page is not, and would otherwise watch every
   utterance drain past with no way to ask for it. */
function soundBlocked() {
  var b = $("sound-on");
  if (!b || !b.hidden) return;
  b.hidden = false;
  if (!b.__wired) {
    b.__wired = true;
    b.addEventListener("click", function () {
      b.hidden = true;
      attemptSpoken();
    });
  }
}
(function () {
  var a = $("speak-audio");
  if (!a) return;
  a.addEventListener("playing", function () {
    if (spokenCurrent) markSpokenPlayed(spokenCurrent.name);
  });
  a.addEventListener("ended", spokenAdvance);
  a.addEventListener("error", spokenAdvance);
})();
function renderSpoken() {
  var sp = (snap && snap.diode && snap.diode.spoken) || [];
  if (!sp.length) {
    if (!spokenBusy) setSpokenCaption("");
    return;
  }
  if (!spokenBusy && !spokenQueue.length) setSpokenCaption(sp[0].text);
  /* Oldest first, so a snapshot carrying more than one utterance queues them in
     the order they were made rather than playing only the newest. Current and
     queued names remain in memory without being persisted as played; that keeps
     refreshes from duplicating them while allowing a reload to recover anything
     autoplay blocked. Stale and future-dated names are persisted immediately so
     they can never become playable later. */
  for (var i = sp.length - 1; i >= 0; i--) {
    var name = sp[i].name || "";
    if (!name || spokenKnown(name)) continue;
    var ageMs = clock() - (sp[i].epoch || 0) * 1000;
    if (ageMs < 0 || ageMs > 180000) {
      markSpokenPlayed(name);
      continue;
    }
    spokenQueue.push({ name: name, text: norm(sp[i].text || "") });
  }
  playNextSpoken();
}

/* ---------- beats ---------- */
/* death gate:start */
/* Reload-safe: the beat fires off the newest ending's recency plus a stored
   marker, not solely a previous-poll comparison an OBS refresh can drop. The
   in-memory marker backstops a browser whose storage is unavailable, so a
   broken localStorage can never replay the beat every poll. */
var mournedMem = null;
function readMourned() {
  try { return window.localStorage.getItem("mournedEpoch"); } catch (e) { return null; }
}
function storeMourned(epoch) {
  mournedMem = String(epoch);
  try { window.localStorage.setItem("mournedEpoch", mournedMem); } catch (e) {}
}
function deathGate(prev) {
  var lin = snap.lineage || [];
  var bumped = !!(prev && snap.stats.incarnation > prev.stats.incarnation);
  var recent = null;
  if (lin.length && lin[0].ended_epoch != null &&
      clock() / 1000 - lin[0].ended_epoch < 90) {
    recent = lin[0].ended_epoch;
  }
  var fresh = recent != null && String(recent) !== readMourned() &&
    String(recent) !== mournedMem;
  if (!bumped && !fresh) return null;
  if (recent != null) storeMourned(recent);
  if (bumped) return snap.stats.incarnation - 1;
  return lin[0].ordinal != null ? lin[0].ordinal : snap.stats.incarnation - 1;
}
/* death gate:end */
function runMourn(endedOrdinal) {
  var stage = $("stage"), sweep = $("death-sweep");
  setText($("premise"), "INCARNATION " + endedOrdinal + " HAS ENDED.");
  $("premise").classList.add("announce");
  announceUntil = Date.now() + 4000;
  sweep.hidden = false;
  if (!REDUCED) {
    stage.classList.add("mourning");
    sweep.classList.remove("sweeping"); void sweep.offsetWidth; sweep.classList.add("sweeping");
    setTimeout(function () { stage.classList.remove("mourning"); }, 600);
  }
  setTimeout(function () { sweep.hidden = true; sweep.classList.remove("sweeping"); }, 4000);
  spotPin = { ordinal: endedOrdinal, untilMs: clock() + SPOT_SECONDS * 1000 };
  renderSpot(clock());
  if (!REDUCED) {
    var sp = $("spot");
    sp.classList.remove("slide"); void sp.offsetWidth; sp.classList.add("slide");
    setTimeout(function () { sp.classList.remove("slide"); }, 700);
  }
}
function maybeCut() {
  if (REDUCED) return;
  var ev = snap.events || [];
  if (!ev.length) return;
  var newest = ev[ev.length - 1], turns = snap.turns || [], epoch = null;
  for (var i = 0; i < turns.length; i++) if (turns[i].index === newest.index) epoch = turns[i].epoch;
  if (epoch == null) return;
  var age = clock() / 1000 - epoch;
  var stamp = newest.index + ":" + newest.name;
  if (age < 10 && maybeCut.last !== stamp) {
    maybeCut.last = stamp;
    var s = $("lineage");
    s.classList.remove("cut"); void s.offsetWidth; s.classList.add("cut");
    setTimeout(function () { s.classList.remove("cut"); }, 1000);
    var row = $("selfmod-rows").firstElementChild;
    if (row && row.classList.contains("rrow")) {
      row.classList.remove("rowflash"); void row.offsetWidth; row.classList.add("rowflash");
      setTimeout(function () { row.classList.remove("rowflash"); }, 1000);
    }
  }
}
function deathBeat(prev) {
  var gone = deathGate(prev);
  if (gone == null) return;
  if (prev && snap.stats.incarnation > prev.stats.incarnation) {
    var nodes = mScroll.querySelectorAll(".turn, .divider");
    for (var i = 0; i < nodes.length; i++) nodes[i].remove();
    turnNodes.clear(); dividers.clear(); expanded.clear();
    stopReveal();
  }
  runMourn(gone);
}

/* ---------- pulse ---------- */
/* The metabolism strip. Left: the real in-flight request, or how long since
   the last close. Middle: a 20-bucket token sparkline over the last ten
   minutes, twenty fixed bars updated in place. Right: tokens per minute over
   the same window. Every figure is the recorder's, none is simulated. */
function setPulse(state) {
  var p = (snap && snap.pulse) || {};
  var show = state === "live" || state === "thinking" ||
    state === "quiet" || state === "nosignal";
  var host = $("pulse");
  if (host.hidden !== !show) { host.hidden = !show; repin(); }
  if (!show) return;
  var rows = p.in_flight || [], left;
  if (rows.length && rows[0].since_epoch != null) {
    left = rows[0].lane + " · in flight " +
      dur(Math.max(0, clock() / 1000 - rows[0].since_epoch));
  } else if (p.last_close_epoch != null) {
    left = "idle · last call " + rel(p.last_close_epoch, clock());
  } else {
    left = "idle";
  }
  setText($("pulse-left"), left);
  var buckets = p.buckets || [], max = 0, i;
  for (i = 0; i < buckets.length; i++) if (buckets[i] > max) max = buckets[i];
  var bars = $("pulse-spark").children;
  for (i = 0; i < bars.length; i++) {
    var vtok = Number(buckets[i]) || 0;
    var h = vtok > 0 ? Math.max(2, Math.round(24 * vtok / max)) : 0;
    var hpx = h + "px";
    if (bars[i].style.height !== hpx) bars[i].style.height = hpx;
  }
  var perMin = (p.tokens_window || 0) / 10;
  setText($("pulse-rate"),
    perMin > 0 ? "≈" + laneCount(Math.round(perMin)) + " tok/min" : "");
}

/* ---------- tick ---------- */
function setRelativeTimes(nowMs) {
  var metas = document.querySelectorAll(".rmeta.agerow");
  for (var i = 0; i < metas.length; i++) {
    var m = metas[i];
    setText(m, m.__size + (m.__epoch != null ? " · " + rel(m.__epoch, nowMs) : ""));
  }
  setEyeCaption(nowMs);
}
function tick() {
  if (!snap) return;
  var nowMs = clock();
  var age = snap.stats.last_epoch != null
    ? Math.max(0, nowMs / 1000 - snap.stats.last_epoch) : null;
  var state = setState(age);
  var st = snap.stats;
  renderLifeFigs(nowMs);
  layoutBars(nowMs);
  setPulse(state);
  setRelativeTimes(nowMs);
  renderNow();
  renderSpot(nowMs);
  rotateLineageFoot(nowMs);
  maybeAutoRepin();
  if (announceUntil && Date.now() > announceUntil) {
    announceUntil = 0;
    $("premise").classList.remove("announce");
    showProvenance();
  }
  if ((st.turns_this_life || 0) === 0) renderColdStart();
}

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

/* ---------- lanes ---------- */
function laneCount(n) {
  n = Number(n) || 0;
  if (n >= 10000) return Math.round(n / 1000) + "k";
  if (n >= 1000) return (n / 1000).toFixed(1) + "k";
  return String(n);
}
function renderLanes() {
  var host = $("stream-rows"), lanes = (snap && snap.lanes) || [];
  if (!host) return;
  /* #stream-rows is a 2-column grid, 64px tall, of 20px rows: 3 rows x 2
     columns fit without clipping. A larger slice would render lanes into
     overflow that #stream-foot never discloses. */
  var shown = lanes.slice(0, 6), live = 0;
  /* The server ranks core first and caps the list it sends, reporting the rest
     as lanes_omitted; an omitted lane is therefore never core. */
  var omitted = (snap && snap.lanes_omitted) || 0;
  /* given/built are counted over every declared lane, not just the ones the
     grid can show: the figure is a claim about the whole set, and slicing it
     to the rendered rows would understate BUILT (or miss GIVEN, if core is
     not among the first six) once more lanes exist than the grid displays.
     The lanes the server capped are part of that set as much as the ones the
     grid has no room for. */
  var given = 0, built = omitted;
  for (var g = 0; g < lanes.length; g++) {
    if (lanes[g].name === "core") given++;
    else built++;
  }
  while (host.children.length > shown.length) host.removeChild(host.lastChild);
  /* Magnitude over digits: each lane carries a bar scaled to the busiest
     lane's tokens_hour across the whole declared set, with a compact figure
     beside it. A nonzero lane never renders under 4% — invisible activity
     would read as none. */
  var maxTok = 0;
  for (var m = 0; m < lanes.length; m++) {
    maxTok = Math.max(maxTok, Number(lanes[m].tokens_hour) || 0);
  }
  for (var i = 0; i < shown.length; i++) {
    var lane = shown[i], node = host.children[i];
    if (!node) {
      node = el("div", "lane-row", host);
      el("i", "l-dot", node);
      node.__name = el("span", "l-name", node);
      var track = el("div", "l-track", node);
      node.__fill = el("i", "l-fill", track);
      node.__val = el("span", "l-val", node);
    }
    if (lane.in_flight > 0) live++;
    node.className = "lane-row" + (lane.name === "core" ? " given" : "") +
      (lane.in_flight > 0 ? " live" : "") + (lane.bound ? "" : " unbound");
    setText(node.__name, norm(lane.name).toUpperCase());
    node.__name.title = norm(lane.name);
    var tok = Number(lane.tokens_hour) || 0;
    var pct = maxTok > 0 ? (tok / maxTok) * 100 : 0;
    if (tok > 0) pct = Math.max(4, pct);
    var w = pct.toFixed(1) + "%";
    if (node.__fill.style.width !== w) node.__fill.style.width = w;
    setText(node.__val, laneCount(tok));
  }
  setText($("stream-count"),
    lanes.length ? given + " GIVEN · " + built + " BUILT" : "");
  var hidden = lanes.length - shown.length + omitted;
  setText($("stream-foot"), hidden > 0
    ? hidden + " more stream" + (hidden === 1 ? "" : "s") + " not shown"
    : (lanes.length ? live + " in flight" : "It thinks with the one socket it was given."));
}

/* ---------- render ---------- */
function render(prev) {
  deathBeat(prev);
  noteColour();
  renderLineage();
  renderLanes();
  renderSpoken();
  renderRibbon();
  renderEye();
  reconcileFeed();
  tick();
  applyExpansion();
  repin();
  requestAnimationFrame(function () { measureTruncation(); repin(); });
  maybeCut();
}
function poll() {
  var ctl = null, timer = null;
  try { ctl = new AbortController(); } catch (e) {}
  if (ctl) timer = setTimeout(function () { try { ctl.abort(); } catch (e) {} }, 4000);
  fetch("/api/stream", { cache: "no-store", signal: ctl ? ctl.signal : undefined })
    .then(function (r) {
    if (timer) clearTimeout(timer);
    if (!r.ok) throw new Error("http");
    return r.json();
  }).then(function (data) {
    failures = 0;
    setTransport(true);
    if (typeof data.now === "number") skewMs = data.now * 1000 - Date.now();
    var prev = snap;
    snap = data;
    try { render(prev); } catch (e) {}
  }).catch(function () {
    if (timer) clearTimeout(timer);
    failures++;
    if (failures >= 3) setTransport(false);
  });
}
mScroll.addEventListener("scroll", onFeedScroll);
$("return-live").addEventListener("click", returnToLive);
(function () {
  var spark = $("pulse-spark");
  for (var i = 0; i < 20; i++) el("div", "bar", spark);
})();
window.addEventListener("load", function () {
  var list = document.querySelectorAll(".clamp");
  for (var i = 0; i < list.length; i++) list[i].__measured = false;
  requestAnimationFrame(measureTruncation);
});
fitStage();
poll();
setInterval(poll, 2000);
setInterval(tick, 250);
</script>
</body>
</html>
"""
