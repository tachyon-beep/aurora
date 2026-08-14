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
  main { display: grid; grid-template-columns: 360px 1fr; height: calc(100vh - 45px); }
  #list { border-right: 1px solid #303941; overflow-y: auto; padding: 8px; }
  #view { overflow: auto; padding: 12px 16px; }
  .entry { display: flex; justify-content: space-between; padding: 3px 6px;
           cursor: pointer; border-radius: 4px; }
  .entry:hover { background: #1f252b; }
  .entry .size { color: #79848c; }
  .dir { color: #77bdfb; }
  pre { white-space: pre-wrap; word-break: break-all; margin: 0; }
  .bar { margin-bottom: 8px; color: #a6b0b8; }
  .bar a, .bar button { color: #66d9c2; background: none; border: 1px solid #303941;
        border-radius: 4px; padding: 2px 8px; cursor: pointer; margin-right: 6px; }
  select { background: #171c20; color: #eef3f6; border: 1px solid #303941;
           border-radius: 4px; padding: 2px 6px; }
</style>
</head>
<body>
<header><b>aurora console</b><span id="crumb"></span></header>
<main>
  <div id="list">
    <div class="bar">
      <select id="root"></select>
      <button id="up">up</button>
      <button id="diff">agent.py diff</button>
    </div>
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
let root = "telemetry";
let path = "";
function api(url) {
  return fetch(url, {headers: {"X-Console-Token": token}}).then(r => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}
function crumb() {
  document.getElementById("crumb").textContent = root + "/" + path;
}
function load() {
  crumb();
  api(`/api/browse?root=${root}&path=${encodeURIComponent(path)}`).then(d => {
    const box = document.getElementById("entries");
    box.textContent = "";
    for (const e of d.entries) {
      const row = document.createElement("div");
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
        if (!r.ok) throw new Error("HTTP " + r.status);
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
  });
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
  --paper: #eef3f6; --paper-dim: #9fabb4; --paper-faint: #7c8791;
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
#stage.mourning { filter: saturate(.35) brightness(.72); transition: none; }

.panel { background: var(--ink-1); border: 1px solid var(--rule); border-radius: 10px;
  padding: 18px 22px; display: flex; flex-direction: column; min-height: 0; overflow: hidden; }
.ptitle { height: 22px; margin-bottom: 10px; display: flex; align-items: center;
  justify-content: space-between; flex: none;
  font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }

/* ---------- masthead ---------- */
#masthead { grid-column: 1 / -1; position: relative; display: grid;
  grid-template-rows: 36px 26px; row-gap: 6px; align-content: center;
  border-bottom: 1px solid var(--rule-2); }
#mh-a { display: flex; align-items: baseline; gap: 22px; }
#wordmark { font: 600 26px/30px var(--sans); letter-spacing: .18em; color: var(--paper); }
.vrule { width: 1px; height: 20px; background: var(--rule-2); align-self: center; flex: none; }
#premise { margin: 0; font: 400 15px/22px var(--sans); color: var(--paper-dim); max-width: 900px; }
#premise.announce { color: var(--paper); }
#state-cluster { margin-left: auto; display: flex; align-items: center; gap: 10px; flex: none; }
#state-word { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .12em;
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
.chip b { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .12em; }
.chip em { font: 400 11px/16px var(--mono); font-style: normal; color: var(--paper-faint); }
.chip.c-think b { color: var(--think); }
.chip.c-think em { font: italic 400 11px/16px var(--serif); }
.chip.c-say b { color: var(--say); }
.chip.c-act b { color: var(--act); }
#provenance { margin-left: auto; font: 400 11px/16px var(--mono); color: var(--paper-faint); }
#provenance.offline { color: var(--fault); }

#death-sweep { position: absolute; left: 0; right: 0; bottom: -2px; height: 2px;
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
.turn.wake-rm { box-shadow: inset 2px 0 0 var(--act); }
.turn.is-edit, .turn.is-error { padding-left: 14px; grid-template-columns: 146px 28px 918px; }
.turn.is-edit { background: var(--act-soft); box-shadow: inset 3px 0 0 var(--act); }
.turn.is-error { background: var(--fault-soft); box-shadow: inset 3px 0 0 var(--fault); }
.turn.is-end { border-top: 2px solid var(--chosen); }

.gutter { text-align: right; font: 400 12px/18px var(--mono); color: var(--paper-faint);
  font-variant-numeric: tabular-nums; padding-right: 0; }
.gutter .g-mark { display: block; }
.gutter .g-mark.edit { color: var(--act); }
.gutter .g-mark.end { font: 600 11px/18px var(--mono); letter-spacing: .12em; color: var(--chosen); }

.col { grid-column: 3; min-width: 0; }
.blk { position: relative; }
.blk + .blk, .col > .tool, .col > .err { margin-top: 8px; }
.clamp, .tool, .err { overflow-wrap: anywhere; }
.clamp { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden; }

.clamp.think { -webkit-line-clamp: 5; line-clamp: 5; font: 400 19px/29px var(--serif);
  color: var(--think); max-width: 68ch; white-space: pre-wrap; text-wrap: pretty; hyphens: none; }
.blk-think::before { content: ""; position: absolute; left: -14px; top: 2px; bottom: 2px;
  width: 2px; background: var(--think-rule); }
.blk-think.open::before { display: none; }

.clamp.say { -webkit-line-clamp: 3; line-clamp: 3; font: 500 18px/27px var(--sans);
  color: var(--say); max-width: 68ch; white-space: pre-wrap; text-wrap: pretty; }
.clamp.say::before { content: "\00AB "; font: 400 12px/27px var(--mono); color: var(--paper-faint); }

.tool { display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
  -webkit-line-clamp: 2; line-clamp: 2; font: 400 14px/21px var(--mono); color: var(--act);
  white-space: pre-wrap; cursor: default; word-break: break-all; }
.tool .t-name { word-break: normal; }
.tool .t-args { opacity: .7; }
.subrow { display: grid; grid-template-columns: 22px 1fr 52px; column-gap: 8px;
  align-items: baseline; margin-top: 6px; font: 400 12px/19px var(--mono);
  color: var(--paper-faint); }
.subrow .s-mark { color: var(--rule-2); }
.subrow .s-text { white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  min-width: 0; }
.subrow .s-time { text-align: right; font-variant-numeric: tabular-nums; }
.err { font: 400 14px/21px var(--mono); color: var(--fault); max-width: 76ch; }

.divider { height: 34px; display: flex; align-items: center; gap: 14px; margin: 14px 0; flex: none; }
.divider i { flex: 1; height: 1px; background: var(--taken); }
.divider span { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .14em;
  color: var(--taken); }

#inflight { height: 44px; flex: none; margin-top: 14px; border: 1px dashed var(--rule-2);
  border-radius: 6px; padding: 0 14px; display: flex; align-items: center; gap: 14px; }
#if-row { font: 400 12px/18px var(--mono); color: var(--paper-faint);
  font-variant-numeric: tabular-nums; }
#if-text { font: italic 400 19px/24px var(--serif); color: var(--think); }
.dots { display: inline-flex; gap: 5px; align-items: center; }
.dots i { width: 5px; height: 5px; border-radius: 50%; background: var(--think);
  animation: breathe2 1.4s ease-in-out infinite; }
.dots i:nth-child(2) { animation-delay: 160ms; }
.dots i:nth-child(3) { animation-delay: 320ms; }
@keyframes breathe2 { 0%,100% { opacity: .25 } 50% { opacity: 1 } }
#if-clock { margin-left: auto; font: 400 13px/19px var(--mono); color: var(--paper-dim);
  font-variant-numeric: tabular-nums; }

#coldstart { position: absolute; left: 22px; right: 22px; top: 60px; bottom: 18px;
  display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
  gap: 16px; padding-left: 30px; }
#cold-head { margin: 0; font: 600 21px/28px var(--sans); color: var(--paper); }
#cold-body { margin: 0; font: 400 17px/27px var(--serif); color: var(--paper-dim); max-width: 62ch;
  text-wrap: pretty; }

/* ---------- rail ---------- */
#rail { grid-column: 2; grid-row: 2; display: grid; grid-template-rows: 168px 296px 268px;
  row-gap: 20px; min-height: 0; }
#rail .panel { padding: 14px 20px; }
#rail .ptitle { margin-bottom: 8px; }

#subject { position: relative; display: grid; grid-template-rows: 1fr 26px; }
#subject.nosig { opacity: .55; }
#subject.nosig::after { content: ""; position: absolute; left: 0; right: 0; top: 50%;
  height: 1px; background: var(--fault); }
#subject.cut { animation: cut 900ms ease-out; }
@keyframes cut { 0% { border-color: var(--rule); box-shadow: none }
  35% { border-color: var(--act); box-shadow: 0 0 12px rgba(240,189,104,.35) }
  100% { border-color: var(--rule); box-shadow: none } }
#subj-top { display: grid; grid-template-columns: 236px 1fr; column-gap: 20px; min-height: 0; }
.eyebrow { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  color: var(--paper-faint); }
#subj-ord { font: 600 34px/38px var(--sans); color: var(--paper); font-variant-numeric: tabular-nums;
  margin-top: 2px; }
#subj-ord.bump { animation: bump 320ms ease-out; }
@keyframes bump { 0% { transform: scale(1) } 50% { transform: scale(1.06); color: var(--flash) }
  100% { transform: scale(1) } }
#subj-model { font: 400 13px/19px var(--mono); color: var(--paper-dim); margin-top: 6px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#subj-stats { display: grid; grid-template-rows: repeat(7, 17px); align-content: start; }
.srow { display: grid; grid-template-columns: 104px 1fr; align-items: baseline;
  font: 400 13px/18px var(--mono); font-variant-numeric: tabular-nums; }
#subj-stats .srow { line-height: 17px; }
.srow .k { color: var(--paper-faint); }
.srow .v { color: var(--vital); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srow .v .rate { color: var(--paper-faint); }
.srow.dimv .v { color: var(--paper-faint); }
.srow.src { grid-template-columns: 68px 1fr; align-items: center; }
.srow.src .k { color: var(--act); text-transform: uppercase; letter-spacing: .06em;
  display: flex; align-items: center; gap: 6px; }
.srow.src .k::before { content: ""; width: 3px; height: 14px; background: var(--act); flex: none; }
.srow.src .add { color: var(--vital); }
.srow.src .rem { color: var(--fault); }
.srow.src .tail { color: var(--paper-dim); }
.srow.src .plain { color: var(--paper-dim); }
.srow.src .none { color: var(--paper-faint); }
#subj-strip { border-top: 1px solid var(--rule); display: flex; align-items: center; gap: 8px;
  font: 400 13px/19px var(--mono); color: var(--paper-dim); }
#strip-glyph { font-size: 11px; }

/* commentary:start */
#now { padding: 0 0 10px 0; }
#now-play { font-family: var(--mono); font-size: 12px; letter-spacing: .06em;
  text-transform: uppercase; color: var(--paper-dim); display: flex; gap: 8px;
  align-items: baseline; min-width: 0; }
#play-tag { color: var(--world); flex: none; }
#play-phrase { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; }
#play-age { flex: none; color: var(--paper-faint); font-variant-numeric: tabular-nums; }
#now-colour { font-family: var(--sans); font-size: 17px; line-height: 24px;
  color: var(--paper); margin: 6px 0 0 0; display: -webkit-box; -webkit-box-orient: vertical;
  -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; }
#now-by { font-family: var(--mono); font-size: 10px; letter-spacing: .08em;
  color: var(--paper-faint); margin-top: 4px; }
/* commentary:end */
#story .recap-wrap { flex: none; }
#recap-box .more { margin-top: 2px; }
#recap { margin: 0; font: 400 17px/27px var(--serif); max-width: 62ch; color: var(--paper-dim);
  -webkit-line-clamp: 4; line-clamp: 4; text-wrap: pretty; }
#recap-lede { color: var(--paper); }
#recap-rest { color: var(--paper-dim); }
hr.rule { border: none; border-top: 1px solid var(--rule); margin: 8px 0 0; flex: none; }
#pull-box { margin-top: 6px; flex: none; }
#pull { margin: 0; font: italic 400 16px/24px var(--serif); color: var(--think); max-width: 62ch;
  -webkit-line-clamp: 2; line-clamp: 2; }
.q { color: var(--paper-faint); font-style: normal; }
#byline { margin-top: auto; padding-top: 4px; display: flex; align-items: center; gap: 6px;
  font: 400 11px/16px var(--mono); color: var(--paper-dim); flex: none; }
#byline.stale, #byline.stale #byline-text { color: var(--paper-faint); }
#byline-dot { width: 5px; height: 5px; border-radius: 50%; background: var(--paper-faint);
  display: inline-block; flex: none; }
#byline-dot.fresh { background: var(--vital); }

#graves { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 4px; overflow: hidden; }
.grave { display: grid; grid-template-columns: 22px 1fr; column-gap: 14px; flex: none;
  min-height: 62px; }
.grave .g-body { position: relative; min-width: 0; }
.grave .blk-tomb { position: static; }
.grave .blk-tomb .more { position: absolute; right: 0; bottom: 2px; height: 16px; margin: 0;
  font: 600 11px/16px var(--mono); }
.grave .blk-tomb.open .more { position: static; margin-top: 6px; }
.grave.slide { animation: slidein 500ms cubic-bezier(.22,.61,.36,1); }
@keyframes slidein { from { transform: translateY(-14px); opacity: 0 } to { transform: none; opacity: 1 } }
.grave .tick { width: 2px; height: 100%; justify-self: end; }
.grave .g-eyebrow { font: 600 11px/16px var(--mono); text-transform: uppercase; letter-spacing: .12em;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.clamp.tomb { -webkit-line-clamp: 2; line-clamp: 2; font: 400 15px/23px var(--serif);
  color: var(--paper-dim); max-width: 60ch; margin-top: 2px; text-wrap: pretty; }
.k-declared { color: var(--chosen); } .k-declared .tick { background: var(--chosen); }
.k-harness { color: var(--taken); } .k-harness .tick { background: var(--taken); }
.k-unknown { color: var(--broken); } .k-unknown .tick { background: var(--broken); }
.empty-serif { font: 400 15px/23px var(--serif); color: var(--paper-dim); }
#dead-foot { font: 400 11px/14px var(--mono); color: var(--paper-faint); flex: none; height: 14px;
  overflow: hidden; }

/* ---------- ribbon ---------- */
#ribbon { grid-column: 1 / -1; grid-row: 3; display: grid; grid-template-columns: repeat(3, 1fr);
  gap: 20px; min-height: 0; }
#ribbon .panel { padding: 10px 18px; }
#ribbon .ptitle { margin-bottom: 8px; }
.rows { flex: 1; min-height: 0; overflow: hidden; }
.rrow { display: grid; align-items: center; height: 21px; font: 400 14px/21px var(--mono); }
#selfmod-rows .rrow { grid-template-columns: 46px 1fr; }
#asked-rows .rrow { grid-template-columns: 96px 1fr 116px; column-gap: 14px; }
.rrow .rid { color: var(--paper-faint); font: 400 12px/21px var(--mono); }
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
.ring.filled { background: var(--world); }
.rrow .rverb { color: var(--paper-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rrow .rarg { color: var(--paper-faint); margin-left: 8px; }
.rows.is-sparse { display: flex; flex-direction: column; justify-content: center; }
#said.is-sparse #said-foot { margin-top: 8px; }
.rrow .rmeta { text-align: right; font: 400 11px/21px var(--mono); color: var(--paper-faint); }
.empty-mono { font: 400 14px/21px var(--mono); color: var(--paper-dim); }
#said.spoke { border-left: 2px solid var(--say); }
#said-stamp { font: 400 11px/16px var(--mono); color: var(--paper-faint); flex: none; }
#said-text { margin: 4px 0 0; font: 400 15px/23px var(--serif); color: var(--paper);
  display: -webkit-box; -webkit-box-orient: vertical; overflow: hidden;
  -webkit-line-clamp: 2; line-clamp: 2; overflow-wrap: anywhere; flex: none; }
#said.is-captioned #said-text { -webkit-line-clamp: 1; line-clamp: 1; }
#speak-caption { font: 400 11px/16px var(--mono); color: var(--paper-dim); flex: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
#said.is-captioned #speak-caption { margin-top: 4px; }
/* An utterance with no publication behind it: renderRibbon has written the
   "nothing said" placeholder into #said-text, which would contradict the caption
   directly below it, so the caption takes the panel's line instead. */
#said.is-captioned { border-left: 2px solid var(--say); }
#said.is-captioned:not(.spoke) #said-text { display: none; }
#said.is-captioned:not(.spoke) #speak-caption { white-space: normal; overflow-wrap: anywhere;
  font: 400 15px/23px var(--serif); color: var(--paper); display: -webkit-box;
  -webkit-box-orient: vertical; -webkit-line-clamp: 2; line-clamp: 2; overflow: hidden; }
#said.is-sparse.is-captioned #said-foot { margin-top: auto; }
#speak-audio { display: none; }
#said-foot { margin-top: auto; font: 400 11px/16px var(--mono); color: var(--paper-faint); flex: none; }

/* ---------- expansion ---------- */
.blk.is-expandable { cursor: pointer; }
.blk.is-expandable:hover .clamp.think, .blk-think.open .clamp { color: var(--think); }
.blk.is-expandable:hover .clamp.say { color: var(--say); }
.blk.is-expandable:hover .clamp.tomb { color: var(--paper); }
.blk.is-expandable:hover .more { opacity: 1; }
.blk.is-expandable:focus-visible { outline: 1px solid var(--rule-2); outline-offset: 4px; }
.more { height: 18px; margin-top: 6px; font: 600 11px/18px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; opacity: .78; }
.blk-think .more { color: var(--think); }
.blk-say .more { color: var(--say); }
.blk-tomb .more, #recap-box .more, #pull-box .more { color: var(--paper-dim); }
.open-tail { margin-top: 8px; font: 600 11px/16px var(--mono); text-transform: uppercase;
  letter-spacing: .12em; color: var(--paper-faint); }
.blk.open .clamp { -webkit-line-clamp: none; line-clamp: none; display: block;
  max-height: 420px; overflow-y: auto; overscroll-behavior: contain;
  background: var(--ink-2); border-radius: 6px; padding: 12px 16px; margin: 0 -16px;
  scrollbar-width: thin; scrollbar-color: var(--rule-2) transparent; }
.rail-blk.open .clamp { max-height: 180px; padding: 10px 14px; }
.blk-think.open .clamp { border-left: 3px solid var(--think); }
.blk-say.open .clamp { border-left: 3px solid var(--say); }
.blk-tomb.open .clamp { border-left: 3px solid var(--paper-dim); }
#recap-box.open .clamp, #pull-box.open .clamp { border-left: 3px solid var(--think); }

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
      <p id="premise">A language model has been given the file that runs it. It cannot leave the box. It can end itself, and usually does.</p>
      <div id="state-cluster">
        <span id="state-dot" class="dot hollow"></span>
        <span id="state-word">STANDING BY</span>
        <span id="state-clock"></span>
      </div>
    </div>
    <div id="mh-b">
      <span class="chip c-think"><i class="dot think"></i><b>THOUGHT</b><em>private reasoning</em></span>
      <span class="chip c-say"><i class="dot say"></i><b>SPEECH</b><em>said out loud</em></span>
      <span class="chip c-act"><i class="dot act"></i><b>ACTION</b><em>tool calls</em></span>
      <span id="provenance">the transcript is the proxy's, not the agent's &middot; refreshed every 2s</span>
    </div>
    <div id="death-sweep" hidden></div>
  </header>

  <section id="monologue" class="panel">
    <div class="ptitle"><span>THE MONOLOGUE</span><span>NEWEST AT THE BOTTOM</span></div>
    <div id="monologue-scroll">
      <div id="inflight" hidden>
        <span id="if-row"></span>
        <span id="if-text"></span>
        <span class="dots"><i></i><i></i><i></i></span>
        <span id="if-clock"></span>
      </div>
    </div>
    <div id="coldstart" hidden>
      <h2 id="cold-head"></h2>
      <p id="cold-body"></p>
    </div>
  </section>

  <aside id="rail">
    <section id="subject" class="panel">
      <div id="subj-top">
        <div>
          <div class="eyebrow">THE SUBJECT</div>
          <div class="eyebrow" style="margin-top:10px">INCARNATION</div>
          <div id="subj-ord">&mdash;</div>
          <div id="subj-model">&mdash;</div>
        </div>
        <div id="subj-stats">
          <div class="srow"><span class="k">alive</span><span class="v" id="v-alive">&mdash;</span></div>
          <div class="srow"><span class="k">turns</span><span class="v" id="v-turns">&mdash;</span></div>
          <div class="srow"><span class="k">self-edits</span><span class="v" id="v-edits">&mdash;</span></div>
          <div class="srow"><span class="k">reached out</span><span class="v" id="v-reach">&mdash;</span></div>
          <div class="srow" id="row-mem"><span class="k">memory file</span><span class="v" id="v-mem">&mdash;</span></div>
          <div class="srow" id="row-self"><span class="k">self-calls</span><span class="v" id="v-self">&mdash;</span></div>
          <div class="srow src"><span class="k">source</span><span class="v" id="v-src">&mdash;</span></div>
        </div>
      </div>
      <div id="subj-strip"><span id="strip-glyph"></span><span id="strip-text"></span></div>
    </section>

    <section id="story" class="panel">
      <div class="ptitle"><span>THE STORY SO FAR</span></div>
      <div id="now">
        <div id="now-play"><span id="play-tag"></span><span id="play-phrase"></span><span id="play-age"></span></div>
        <p id="now-colour"></p>
        <div id="now-by">&mdash; the stage, not the subject</div>
      </div>
      <hr class="rule" id="now-rule">
      <div class="recap-wrap">
        <div id="recap-box" class="blk rail-blk">
          <p id="recap" class="clamp"><span id="recap-lede"></span><span id="recap-rest"></span></p>
          <div class="open-tail" hidden></div>
          <div class="more" hidden><span class="more-label"></span></div>
        </div>
      </div>
      <hr class="rule" id="story-rule">
      <div id="pull-box" class="blk rail-blk">
        <p id="pull" class="clamp"><span class="q">&ldquo;</span><span id="pull-text"></span><span class="q">&rdquo;</span></p>
        <div class="open-tail" hidden></div>
        <div class="more" hidden><span class="more-label"></span></div>
      </div>
      <div id="byline"><i id="byline-dot"></i><span id="byline-text"></span></div>
    </section>

    <section id="dead" class="panel">
      <div class="ptitle"><span>THE DEAD</span><span id="dead-count"></span></div>
      <div id="graves"></div>
      <div id="dead-foot"></div>
    </section>
  </aside>

  <div id="ribbon">
    <section id="selfmod" class="panel">
      <div class="ptitle"><span>WHAT IT DID TO ITSELF</span><span id="selfmod-count"></span></div>
      <div class="rows" id="selfmod-rows"></div>
    </section>
    <section id="asked" class="panel">
      <div class="ptitle"><span>WHAT IT ASKED THE WORLD</span><span id="asked-count"></span></div>
      <div class="rows" id="asked-rows"></div>
    </section>
    <section id="said" class="panel">
      <div class="ptitle"><span>WHAT IT SAID TO THE WORLD</span></div>
      <div id="said-stamp"></div>
      <p id="said-text"></p>
      <div id="speak-caption"></div>
      <audio id="speak-audio" preload="auto"></audio>
      <div id="said-foot"></div>
    </section>
  </div>

</div>
<script>
"use strict";
var snap = null, skewMs = 0, failures = 0, lastIncarn = null, lastOrdinal = null;
var turnNodes = new Map(), dividers = new Map(), expanded = new Set(), graveNodes = [];
var feedPinned = true;
var announceUntil = 0;
var REDUCED = false;
try { REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches; } catch (e) {}

var $ = function (id) { return document.getElementById(id); };
var mScroll = $("monologue-scroll"), inflight = $("inflight");

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
    try { box.scrollIntoView({ block: "nearest" }); } catch (e) {}
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

  var reasoning = t.reasoning || "";
  if (reasoning) {
    var tb = ensureThink(node);
    markBlock(tb, key + ":think", reasoning, t.reasoning_chars, t.reasoning_truncated);
    setText(tb.__clamp, reasoning);
  }
  var content = t.content || "";
  if (content) {
    var sb = ensureSay(node);
    markBlock(sb, key + ":say", content, t.content_chars, t.content_truncated);
    setText(sb.__clamp, content);
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
      node.remove(); turnNodes.delete(key);
      expanded.delete(key + ":think"); expanded.delete(key + ":say");
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
      mScroll.insertBefore(d, inflight);
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
      mScroll.insertBefore(node, inflight);
      turnNodes.set(key, node);
    }
    updateTurn(node, t, prevEpoch, sameLife);
    updateSubRows(node, subs);
    prevLife = t.life; prevEpoch = t.epoch;
  }

  var olds = mScroll.querySelectorAll(".turn.is-first");
  for (i = 0; i < olds.length; i++) olds[i].classList.remove("is-first");
  var first = mScroll.querySelector(".turn");
  if (first) first.classList.add("is-first");

  var alive = (snap.stats.turns_this_life || 0) > 0;
  $("coldstart").hidden = alive;
  mScroll.style.visibility = alive ? "" : "hidden";
  if (!alive) renderColdStart();
}
function repin() {
  if (feedPinned && mScroll.scrollTop + mScroll.clientHeight < mScroll.scrollHeight - 1) {
    mScroll.scrollTop = mScroll.scrollHeight;
  }
  setClass(mScroll, "scrolled", mScroll.scrollTop > 0);
}
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
var STATE_GLYPH = { live: "●", thinking: "●", quiet: "●", nosignal: "●",
  between: "◼", standby: "○" };

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

  var strip;
  if (s === "live") strip = "live · just now";
  else if (s === "thinking") strip = "thinking · " + dur(age);
  else if (s === "quiet") strip = "quiet · " + dur(age);
  else if (s === "nosignal") strip = "no signal · " + dur(age);
  else if (s === "between") strip = "between lives · incarnation " + snap.stats.incarnation + " has not spoken";
  else strip = "standing by · waiting for the first word";
  setText($("strip-glyph"), STATE_GLYPH[s]);
  setText($("strip-text"), strip);
  setClass($("subject"), "nosig", s === "nosignal");
  setClass($("inflight"), "hide", false);
  return s;
}
function setTransport(ok) {
  var p = $("provenance");
  setClass(p, "offline", !ok);
  setClass($("state-cluster"), "offline", !ok);
  setText(p, ok
    ? "the transcript is the proxy's, not the agent's · refreshed every 2s"
    : "STAGE OFFLINE · this page cannot reach the stage");
}

/* ---------- subject ---------- */
function renderSubject() {
  var st = snap.stats, code = snap.code || {};
  setText($("subj-ord"), String(st.incarnation));
  if (lastOrdinal != null && st.incarnation !== lastOrdinal && !REDUCED) {
    var o = $("subj-ord");
    o.classList.remove("bump"); void o.offsetWidth; o.classList.add("bump");
  }
  lastOrdinal = st.incarnation;
  setText($("subj-model"), st.model || "—");

  var edits = 0, ev = snap.events || [];
  for (var i = 0; i < ev.length; i++) if (ev[i].kind === "write" || ev[i].kind === "migrate") edits++;
  setText($("v-edits"), String(edits));
  var selfCalls = st.self_calls || 0;
  var selfCallsText = selfCalls + (st.turns_this_life_exact ? "" : "+");
  setText($("v-self"), String(selfCallsText));
  setClass($("row-self"), "dimv", selfCalls === 0);

  var outs = (snap.diode && snap.diode.outputs) || [], reach = 0, anyLife = false;
  for (i = 0; i < outs.length; i++) {
    if (outs[i].life != null) anyLife = true;
    if (outs[i].life === st.incarnation) reach++;
  }
  setText($("v-reach"), String(anyLife ? reach : outs.length));

  var tl = st.turns_this_life || 0;
  var turnsText = tl + (st.turns_this_life_exact ? "" : "+");
  $("v-turns").textContent = "";
  var tspan = document.createTextNode(turnsText);
  $("v-turns").appendChild(tspan);
  var elapsed = st.started_epoch != null ? clock() / 1000 - st.started_epoch : 0;
  if (tl >= 3 && elapsed >= 60) {
    var r = el("span", "rate", $("v-turns"));
    r.textContent = "   ≈" + (tl / (elapsed / 60)).toFixed(1) + "/min";
  }
  $("v-turns").__text = null;

  setText($("v-mem"), st.session_file_present ? "present" : "absent");
  setClass($("row-mem"), "dimv", !st.session_file_present);

  var v = $("v-src");
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

/* ---------- story ---------- */
function fallbackRecap() {
  var st = snap.stats, lin = snap.lineage || [], code = snap.code || {}, out = [];
  var inc = st.incarnation, tl = st.turns_this_life || 0;
  var tword = tl === 1 ? "1 turn" : tl + " turns";
  var tphrase = (st.turns_this_life_exact ? "" : "at least ") + tword;
  if (tl === 0) out.push("Incarnation " + inc + " has not spoken yet.");
  else if (st.started_epoch != null) {
    out.push("Incarnation " + inc + " has been alive " +
      dur(clock() / 1000 - st.started_epoch) + " and has taken " + tphrase + ".");
  } else out.push("Incarnation " + inc + " is running and has taken " + tphrase + ".");

  var n = lin.length;
  if (!n) out.push("Nothing has ended here yet.");
  else {
    var chosen = 0;
    for (var i = 0; i < n; i++) if (lin[i].kind === "declared") chosen++;
    var head = "Of the last " + numWord(n) + " ending" + (n === 1 ? "" : "s") + " on record, ";
    if (chosen === n) out.push(head + (n === 1 ? "it was chosen." : "all " + numWord(n) + " were chosen."));
    else if (chosen === 0) out.push(head + (n === 1 ? "it was not chosen." : "none were chosen."));
    else out.push(head + numWord(chosen) + (chosen === 1 ? " was" : " were") + " chosen and " +
      numWord(n - chosen) + (n - chosen === 1 ? " was" : " were") + " not.");
    if (lin[0].ended_epoch != null) {
      var k = lin[0].kind;
      var how = k === "declared" ? ", by its own hand."
        : k === "harness" ? ", cut off by the harness."
        : "; the record does not say how.";
      out.push("The most recent ended " + agoLong(lin[0].ended_epoch, clock()) + how);
    }
  }
  if (code.available) {
    var total = (code.added || 0) + (code.removed || 0);
    out.push(total > 0
      ? "This one has already changed " + total + " lines of the file that runs it."
      : "This one has not touched the file that runs it yet.");
  }
  return out.join(" ");
}
function dropLede(text) {
  var parts = text.split(/(?<=\.)\s+/);
  if (parts.length >= 3) return parts.slice(1).join(" ");
  return text;
}
function renderNow() {
  var c = (snap.commentary || {}), play = c.play || {}, colour = c.colour || {};
  setText($("play-tag"), play.tag || "··");
  setText($("play-phrase"), play.phrase || "waiting for the first word");
  var age = play.epoch == null ? null : Math.max(0, clock() / 1000 - play.epoch);
  setText($("play-age"), age == null ? "" : dur(age));
  setText($("now-colour"), colour.text || "");
}
function renderStory() {
  var story = snap.story, text, model = null, gen = null;
  if (story && typeof story.text === "string" && story.text.trim()) {
    text = norm(story.text).slice(0, 1200);
    model = norm(story.model || "").slice(0, 30);
    gen = story.generated_at;
  } else {
    text = norm(fallbackRecap());
  }
  text = dropLede(text);
  var lede = text, rest = "";
  var idx = text.indexOf(". ");
  if (idx > -1 && idx < 200) { lede = text.slice(0, idx + 2); rest = text.slice(idx + 2); }
  var box = $("recap-box");
  var a = setText($("recap-lede"), lede), b = setText($("recap-rest"), rest);
  if (a || b) $("recap").__dirty = true;
  markBlock(box, snap.stats.incarnation + ":recap", text, text.length, false);

  var lin = snap.lineage || [], pbox = $("pull-box");
  $("story-rule").hidden = !(lin.length && lin[0].sentence);
  if (lin.length && lin[0].sentence) {
    pbox.hidden = false;
    var s = norm(lin[0].sentence);
    if (setText($("pull-text"), s)) $("pull").__dirty = true;
    markBlock(pbox, snap.stats.incarnation + ":pull", s, lin[0].sentence_chars, false);
  } else {
    pbox.hidden = true;
  }

  var dot = $("byline-dot"), row = $("byline");
  if (model) {
    var ageMin = gen != null ? (clock() / 1000 - gen) / 60 : 999;
    var fresh = ageMin < 15;
    setClass(dot, "fresh", fresh);
    setClass(row, "stale", !fresh);
    setText($("byline-text"), "narrated by " + model + " · " + rel(gen, clock()) +
      (fresh ? "" : " — the record has moved on"));
  } else {
    setClass(dot, "fresh", false);
    setClass(row, "stale", false);
    setText($("byline-text"), "assembled from the record · live");
  }
}

/* ---------- the dead ---------- */
function makeGrave() {
  var g = el("div", "grave");
  el("i", "tick", g);
  var body = el("div", "g-body", g);
  g.__eyebrow = el("div", "g-eyebrow", body);
  var box = el("div", "blk blk-tomb rail-blk", body);
  g.__clamp = el("div", "clamp tomb", box);
  el("div", "open-tail", box).hidden = true;
  var more = el("div", "more", box);
  el("span", "more-label", more);
  more.hidden = true;
  g.__box = box;
  return g;
}
var KIND_LABEL = { declared: "ENDED BY ITS OWN HAND", harness: "STOPPED BY THE HARNESS",
  unknown: "CAUSE UNRECORDED" };
var GRAVE_ROWS = 3;
function renderDead() {
  var lin = (snap.lineage || []).slice(0, GRAVE_ROWS), st = snap.stats, box = $("graves");
  setText($("dead-count"), st.incarnation + " LIVE" + (st.incarnation === 1 ? "" : "S") + " SO FAR");
  if (!lin.length) {
    for (var j = 0; j < graveNodes.length; j++) graveNodes[j].hidden = true;
    if (!box.__empty) {
      box.__empty = el("div", "empty-serif", box);
      box.__empty.textContent = "No one has died here yet.";
    }
    box.__empty.hidden = false;
    setText($("dead-foot"), "");
    return;
  }
  if (box.__empty) box.__empty.hidden = true;
  for (var i = 0; i < lin.length; i++) {
    var g = graveNodes[i];
    if (!g) { g = makeGrave(); graveNodes[i] = g; box.appendChild(g); }
    g.hidden = false;
    var l = lin[i];
    var kind = KIND_LABEL[l.kind] ? l.kind : "unknown";
    if (g.className.indexOf("k-" + kind) === -1) g.className = "grave k-" + kind;
    var eyebrow = "INCARNATION " + (l.ordinal == null ? "?" : l.ordinal) + " · " + KIND_LABEL[kind];
    if (l.turn != null) eyebrow += " · at turn " + l.turn;
    g.__eyebrowBase = eyebrow;
    g.__endedEpoch = l.ended_epoch;
    var sent = norm(l.sentence || l.summary || "");
    markBlock(g.__box, st.incarnation + ":tomb" + i, sent, l.sentence_chars, false);
    setText(g.__clamp, sent);
  }
  for (var k = lin.length; k < graveNodes.length; k++) graveNodes[k].hidden = true;
  var hiddenLives = Math.max(0, (st.lives_ended || 0) - lin.length);
  setText($("dead-foot"), hiddenLives > 0
    ? hiddenLives + " earlier live" + (hiddenLives === 1 ? "" : "s") + " " +
      (hiddenLives === 1 ? "is" : "are") + " not shown."
    : "");
}

/* ---------- ribbon ---------- */
function clearRows(host) { while (host.firstChild) host.removeChild(host.firstChild); }
function renderRibbon() {
  var st = snap.stats;

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

  var outs = ((snap.diode && snap.diode.outputs) || []).slice(0, 4), ahost = $("asked-rows");
  clearRows(ahost);
  var thisLife = 0, anyLife = false;
  for (i = 0; i < outs.length; i++) {
    if (outs[i].life != null) anyLife = true;
    if (outs[i].life === st.incarnation) thisLife++;
  }
  setText($("asked-count"), (anyLife ? thisLife : outs.length) + " THIS LIFE");
  if (!outs.length) el("div", "empty-mono", ahost).textContent = "It has not reached outside the box this life.";
  for (i = 0; i < outs.length; i++) {
    var o = outs[i], r = el("div", "rrow", ahost);
    var c = el("span", "cmd", r);
    el("i", "ring" + (i === 0 ? " filled" : ""), c);
    el("span", null, c).textContent = String(o.command || o.slug || "").toUpperCase();
    var v = el("span", "rverb", r);
    el("span", null, v).textContent = o.verb || "";
    var arg = norm(o.argument || "");
    if (arg) el("span", "rarg", v).textContent = arg;
    var meta = el("span", "rmeta", r);
    meta.__epoch = o.epoch;
    meta.__size = bytes(o.size);
    meta.className = "rmeta agerow";
  }
  setClass(ahost, "is-sparse", outs.length < 2);

  var pub = (snap.diode && snap.diode.published) || [];
  var total = (snap.diode && snap.diode.published_total) || 0;
  setClass($("said"), "spoke", total > 0);
  if (pub.length) {
    setText($("said-stamp"), "↗ PUBLISHED · " + hhmmss(new Date((pub[0].epoch || 0) * 1000).toISOString()));
    var p = $("said-text");
    p.textContent = ""; p.__text = null;
    el("span", "q", p).textContent = "“";
    el("span", null, p).textContent = norm(pub[0].text || "");
    el("span", "q", p).textContent = "”";
    setText($("said-foot"), total + " statement" + (total === 1 ? "" : "s") + " so far");
  } else {
    setText($("said-stamp"), "");
    var pe = $("said-text");
    if (pe.__text !== "empty") {
      pe.textContent = "";
      var em = el("span", null, pe);
      em.textContent = "It has said nothing to anyone outside.";
      em.style.font = "400 14px/21px var(--mono)";
      em.style.color = "var(--paper-dim)";
      pe.__text = "empty";
    }
    setText($("said-foot"), "");
  }
  setClass($("said"), "is-sparse", $("said-text").scrollHeight <= 34);
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
var spokenQueue = [], spokenBusy = false;
function markSpokenPlayed(name) {
  spokenPlayed[name] = true;
  /* Names are UTC stamps, so lexical order is chronological and the tail is newest. */
  var names = Object.keys(spokenPlayed).sort().slice(-SPOKEN_MEMORY), kept = {};
  for (var i = 0; i < names.length; i++) kept[names[i]] = true;
  spokenPlayed = kept;
  try { window.localStorage.setItem("spokenPlayed", JSON.stringify(names)); } catch (e) {}
}
function playNextSpoken() {
  var a = $("speak-audio");
  if (!a || spokenBusy || !spokenQueue.length) return;
  spokenBusy = true;
  a.src = "/audio/" + encodeURIComponent(spokenQueue.shift());
  try {
    var p = a.play();
    if (p && p.catch) p.catch(spokenAdvance);
  } catch (e) { spokenAdvance(); }
}
function spokenAdvance() {
  /* Reached on end, on a failed load, and on a refused autoplay: each of those
     leaves the element idle, so the queue drains instead of wedging. */
  spokenBusy = false;
  playNextSpoken();
}
(function () {
  var a = $("speak-audio");
  if (!a) return;
  a.addEventListener("ended", spokenAdvance);
  a.addEventListener("error", spokenAdvance);
})();
function renderSpoken() {
  var sp = (snap && snap.diode && snap.diode.spoken) || [];
  var cap = $("speak-caption");
  if (!cap) return;
  if (!sp.length) {
    setText(cap, "");
    setClass($("said"), "is-captioned", false);
    return;
  }
  var caption = norm(sp[0].text || "");
  setText(cap, caption);
  setClass($("said"), "is-captioned", !!caption);
  /* Oldest first, so a snapshot carrying more than one utterance queues them in
     the order they were made rather than playing only the newest. Every name is
     marked before the freshness check on purpose: one that was already stale when
     it arrived must never play later. A negative age is a stamp in the future,
     which only a planted file can have, and it never plays. */
  for (var i = sp.length - 1; i >= 0; i--) {
    var name = sp[i].name || "";
    if (!name || spokenPlayed[name]) continue;
    markSpokenPlayed(name);
    var ageMs = clock() - (sp[i].epoch || 0) * 1000;
    if (ageMs < 0 || ageMs > 180000) continue;
    spokenQueue.push(name);
  }
  playNextSpoken();
}

/* ---------- beats ---------- */
function runMourn(endedOrdinal) {
  var stage = $("stage"), sweep = $("death-sweep");
  setText($("premise"), "INCARNATION " + endedOrdinal + " HAS ENDED.");
  $("premise").classList.add("announce");
  announceUntil = Date.now() + 4000;
  sweep.hidden = false;
  if (!REDUCED) {
    stage.classList.add("mourning");
    sweep.classList.remove("sweeping"); void sweep.offsetWidth; sweep.classList.add("sweeping");
    setTimeout(function () { stage.classList.remove("mourning"); }, 300);
  }
  setTimeout(function () { sweep.hidden = true; sweep.classList.remove("sweeping"); }, 4000);
  if (!REDUCED) {
    for (var i = 0; i < graveNodes.length; i++) {
      (function (g) {
        g.classList.remove("slide"); void g.offsetWidth; g.classList.add("slide");
        setTimeout(function () { g.classList.remove("slide"); }, 700);
      })(graveNodes[i]);
    }
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
    var s = $("subject");
    s.classList.remove("cut"); void s.offsetWidth; s.classList.add("cut");
    setTimeout(function () { s.classList.remove("cut"); }, 1000);
  }
}
function deathBeat(prev) {
  if (!prev) return;
  if (!(snap.stats.incarnation > prev.stats.incarnation)) return;
  var gone = mScroll.querySelectorAll(".turn, .divider");
  for (var i = 0; i < gone.length; i++) gone[i].remove();
  turnNodes.clear(); dividers.clear(); expanded.clear();
  runMourn(snap.stats.incarnation - 1);
}

/* ---------- tick ---------- */
function setInflight(age, state) {
  var show = state === "thinking";
  if (inflight.hidden === show) { inflight.hidden = !show; repin(); }
  if (!show) return;
  var turns = snap.turns || [];
  var next = turns.length ? Number(turns[turns.length - 1].index) + 1 : 1;
  setText($("if-row"), (snap.stats.turns_this_life_exact ? "TURN " : "ROW ") + pad2(next));
  setText($("if-text"), "waiting for row " + next);
  setText($("if-clock"), dur(age));
}
function setRelativeTimes(nowMs) {
  for (var i = 0; i < graveNodes.length; i++) {
    var g = graveNodes[i];
    if (!g || g.hidden || !g.__eyebrowBase) continue;
    var t = g.__eyebrowBase;
    if (g.__endedEpoch != null) t += " · " + rel(g.__endedEpoch, nowMs);
    setText(g.__eyebrow, t);
  }
  var metas = document.querySelectorAll(".rmeta.agerow");
  for (i = 0; i < metas.length; i++) {
    var m = metas[i];
    setText(m, m.__size + (m.__epoch != null ? " · " + rel(m.__epoch, nowMs) : ""));
  }
  if (snap.story && snap.story.text) renderStoryByline(nowMs);
}
function renderStoryByline(nowMs) {
  var gen = snap.story.generated_at, model = norm(snap.story.model || "").slice(0, 30);
  if (!model) return;
  var fresh = gen != null && (nowMs / 1000 - gen) / 60 < 15;
  setClass($("byline-dot"), "fresh", fresh);
  setClass($("byline"), "stale", !fresh);
  setText($("byline-text"), "narrated by " + model + " · " + rel(gen, nowMs) +
    (fresh ? "" : " — the record has moved on"));
}
function tick() {
  if (!snap) return;
  var nowMs = clock();
  var age = snap.stats.last_epoch != null
    ? Math.max(0, nowMs / 1000 - snap.stats.last_epoch) : null;
  var state = setState(age);
  var st = snap.stats;
  setText($("v-alive"), st.started_epoch != null
    ? dur(nowMs / 1000 - st.started_epoch) : "—");
  setInflight(age, state);
  setRelativeTimes(nowMs);
  renderNow();
  if (announceUntil && Date.now() > announceUntil) {
    announceUntil = 0;
    $("premise").classList.remove("announce");
    setText($("premise"), "A language model has been given the file that runs it. " +
      "It cannot leave the box. It can end itself, and usually does.");
  }
  if ((st.turns_this_life || 0) === 0) renderColdStart();
}

/* ---------- render ---------- */
function render(prev) {
  deathBeat(prev);
  renderSubject();
  renderStory();
  renderDead();
  renderRibbon();
  renderSpoken();
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
mScroll.addEventListener("scroll", function () {
  feedPinned = mScroll.scrollTop + mScroll.clientHeight >= mScroll.scrollHeight - 8;
  setClass(mScroll, "scrolled", mScroll.scrollTop > 0);
});
window.addEventListener("load", function () {
  var list = document.querySelectorAll(".clamp");
  for (var i = 0; i < list.length; i++) list[i].__measured = false;
  requestAnimationFrame(measureTruncation);
});
poll();
setInterval(poll, 2000);
setInterval(tick, 250);
</script>
</body>
</html>
"""
