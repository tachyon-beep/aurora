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
<title>aurora</title>
<style>
  :root { color-scheme: dark;
    --bg: #101316; --panel: #171c20; --border: #303941; --text: #eef3f6;
    --muted: #a6b0b8; --subtle: #79848c; --accent: #66d9c2; --tool: #f0bd68;
    --think: #9aa7ff; --error: #ff8d8d; }
  html, body { margin: 0; width: 1920px; height: 1080px; overflow: hidden;
    background: var(--bg); color: var(--text);
    font: 17px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  #grid { display: grid; grid-template-columns: 1fr 520px; gap: 14px;
    box-sizing: border-box; width: 100%; height: 100%; padding: 14px; }
  .panel { background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 12px 16px; box-sizing: border-box;
    overflow: hidden; display: flex; flex-direction: column; }
  .panel h2 { margin: 0 0 8px; font-size: 14px; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: var(--subtle); }
  #rail { display: grid; grid-template-rows: auto auto 1fr 1fr; gap: 14px;
    min-height: 0; }
  #feed .scroll { overflow: hidden; flex: 1; display: flex;
    flex-direction: column; justify-content: flex-end; }
  .turn { margin-top: 10px; border-top: 1px solid var(--border); padding-top: 8px; }
  .turn .who { color: var(--subtle); font-size: 13px; }
  .think { color: var(--think); }
  .say { color: var(--text); }
  .call { color: var(--tool); }
  .err { color: var(--error); }
  #stats table { width: 100%; border-collapse: collapse; }
  #stats td { padding: 2px 0; }
  #stats td:first-child { color: var(--subtle); width: 45%; }
  #stats td:last-child { color: var(--accent); }
  ul { margin: 0; padding: 0; list-style: none; overflow: hidden; flex: 1; }
  li { padding: 3px 0; border-bottom: 1px solid var(--border);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  li .tag { color: var(--tool); margin-right: 8px; }
  li .dim { color: var(--muted); }
</style>
</head>
<body>
<div id="grid">
  <div class="panel" id="feed"><h2>agent</h2><div class="scroll" id="turns"></div></div>
  <div id="rail">
    <div class="panel" id="stats"><h2>incarnation</h2>
      <table>
        <tr><td>incarnation</td><td id="s-inc">—</td></tr>
        <tr><td>model</td><td id="s-model">—</td></tr>
        <tr><td>transcript turns</td><td id="s-turns">—</td></tr>
        <tr><td>last activity</td><td id="s-last">—</td></tr>
      </table>
    </div>
    <div class="panel" id="lineage"><h2>previous incarnations</h2><ul id="lineage-list"></ul></div>
    <div class="panel" id="mods"><h2>self-modification</h2><ul id="mods-list"></ul></div>
    <div class="panel" id="diode"><h2>diode</h2><ul id="diode-list"></ul></div>
  </div>
</div>
<script>
function li(parent, tag, text, dim) {
  const el = document.createElement("li");
  if (tag) {
    const t = document.createElement("span");
    t.className = "tag"; t.textContent = tag;
    el.appendChild(t);
  }
  const s = document.createElement("span");
  if (dim) s.className = "dim";
  s.textContent = text;
  el.appendChild(s);
  parent.appendChild(el);
}
function clamp(text, n) {
  text = (text || "").trim();
  return text.length > n ? text.slice(0, n) + "…" : text;
}
function render(snap) {
  document.getElementById("s-inc").textContent = snap.stats.incarnation;
  document.getElementById("s-model").textContent = snap.stats.model || "—";
  document.getElementById("s-turns").textContent = snap.stats.transcript_turns;
  document.getElementById("s-last").textContent = snap.stats.last_timestamp || "—";

  const turns = document.getElementById("turns");
  turns.textContent = "";
  for (const t of snap.turns.slice(-8)) {
    const box = document.createElement("div");
    box.className = "turn";
    const who = document.createElement("div");
    who.className = "who";
    who.textContent = "turn " + t.index;
    box.appendChild(who);
    if (t.reasoning) {
      const d = document.createElement("div");
      d.className = "think"; d.textContent = clamp(t.reasoning, 400);
      box.appendChild(d);
    }
    if (t.content) {
      const d = document.createElement("div");
      d.className = "say"; d.textContent = clamp(t.content, 400);
      box.appendChild(d);
    }
    for (const c of t.tool_calls || []) {
      const d = document.createElement("div");
      d.className = "call";
      d.textContent = "→ " + c.name + " " + clamp(c.arguments, 160);
      box.appendChild(d);
    }
    if (t.error) {
      const d = document.createElement("div");
      d.className = "err";
      d.textContent = "error: " + clamp(JSON.stringify(t.error), 200);
      box.appendChild(d);
    }
    turns.appendChild(box);
  }

  const lin = document.getElementById("lineage-list");
  lin.textContent = "";
  for (const l of snap.lineage) li(lin, null, l.summary, false);

  const mods = document.getElementById("mods-list");
  mods.textContent = "";
  for (const e of snap.events.slice().reverse())
    li(mods, e.name, "turn " + e.index + "  " + clamp(e.detail, 60), true);

  const dio = document.getElementById("diode-list");
  dio.textContent = "";
  for (const o of snap.diode.outputs) li(dio, null, o.name, true);
}
function tick() {
  fetch("/api/stream").then(r => r.json()).then(render).catch(() => {});
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>
"""
