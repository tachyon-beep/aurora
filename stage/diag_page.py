DIAG_PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>aurora diagnostics</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin: 0; background: #101316; color: #eef3f6;
         font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; }
  header { padding: 8px 16px; border-bottom: 1px solid #303941; display: flex;
           gap: 16px; align-items: baseline; height: 40px; }
  header b { color: #66d9c2; }
  header a { color: #66d9c2; }
  header .status { color: #79848c; margin-left: auto; }
  header .status.err { color: #ff7b72; }
  main { display: grid; grid-template-columns: 400px minmax(720px, 1fr) minmax(640px, 780px);
         grid-template-rows: 100%; height: calc(100vh - 41px); overflow: hidden; }
  main > * { min-width: 0; }
  #rail { border-right: 1px solid #303941; display: flex; flex-direction: column; }
  .rail-section { display: flex; flex-direction: column; min-height: 0; }
  #rail-lives { flex: 3; border-bottom: 1px solid #303941; }
  #rail-lanes { flex: 2; }
  .rail-head { padding: 6px 12px; color: #79848c; letter-spacing: 0.08em; }
  .rail-list { flex: 1; min-height: 0; overflow-y: auto; padding: 0 8px 8px; }
  .row { display: block; width: 100%; text-align: left; background: none; border: 0;
         border-left: 2px solid transparent; color: inherit; font: inherit;
         padding: 5px 8px; cursor: pointer; border-radius: 4px; }
  .row:hover { background: #1f252b; }
  .row.sel { background: #1f252b; border-left-color: #66d9c2; }
  .row:focus-visible, button:focus-visible { outline: 2px solid #66d9c2; outline-offset: 1px; }
  .row .title { display: flex; justify-content: space-between; gap: 8px; }
  .row .title .kind-declared { color: #66d9c2; }
  .row .title .kind-harness { color: #e3b341; }
  .row .title .kind-current { color: #77bdfb; }
  .row .meta { color: #79848c; }
  .row .meta .err { color: #ff7b72; }
  .row .note { color: #a6b0b8; white-space: nowrap; overflow: hidden;
               text-overflow: ellipsis; }
  .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%;
         background: #3a444d; margin-right: 6px; }
  .dot.on { background: #66d9c2; }
  .dot.busy { background: #e3b341; }
  .pane { flex: 1; overflow-y: auto; min-height: 0; }
  #center { border-right: 1px solid #303941; display: flex; flex-direction: column; }
  #right { display: flex; flex-direction: column; }
  .toolbar { display: flex; gap: 10px; align-items: center; padding: 6px 12px;
             border-bottom: 1px solid #303941; color: #a6b0b8; flex-wrap: wrap; }
  .toolbar b { color: #eef3f6; }
  .toolbar .gap { margin-left: auto; }
  .toolbar button { color: #66d9c2; background: none; border: 1px solid #303941;
                    border-radius: 4px; padding: 1px 8px; cursor: pointer; font: inherit; }
  .toolbar button:disabled { color: #3a444d; cursor: default; }
  .toolbar label { color: #79848c; }
  .card { border-bottom: 1px solid #22282e; padding: 8px 14px; }
  .card .head { display: flex; gap: 12px; align-items: baseline; flex-wrap: wrap; }
  .card .head .idx { color: #79848c; }
  .card .head .kind-loop { color: #77bdfb; }
  .card .head .kind-subcall { color: #a6b0b8; }
  .card .head .model { color: #79848c; }
  .card .head .badge-err { color: #ff7b72; }
  .card .head .badge-tool { color: #66d9c2; }
  .card .head .raw-btn { margin-left: auto; color: #79848c; background: none;
                         border: 1px solid #303941; border-radius: 4px; padding: 0 6px;
                         cursor: pointer; font: inherit; }
  pre { white-space: pre-wrap; word-break: break-word; margin: 4px 0;
        max-width: 100%; overflow-x: auto; }
  details { margin: 2px 0; }
  summary { color: #79848c; cursor: pointer; }
  summary .lbl { color: #a6b0b8; }
  .section-content pre { color: #eef3f6; }
  .section-reasoning pre { color: #a6b0b8; }
  .section-error pre { color: #ff7b72; }
  .raw pre { color: #a6b0b8; background: #0b0e10; border: 1px solid #22282e;
             border-radius: 4px; padding: 8px; }
  .truncated { color: #e3b341; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 4px 10px; border-bottom: 1px solid #22282e;
           vertical-align: baseline; }
  th { color: #79848c; font-weight: normal; position: sticky; top: 0;
       background: #101316; }
  td.num { text-align: right; }
  th.num { text-align: right; }
  td .err { color: #ff7b72; }
  tr.rowbtn { cursor: pointer; }
  tr.rowbtn:hover { background: #1f252b; }
  .lane-summary { display: flex; gap: 18px; padding: 6px 12px; color: #a6b0b8;
                  border-bottom: 1px solid #303941; flex-wrap: wrap; }
  .lane-summary b { color: #eef3f6; font-weight: normal; }
  .empty { color: #79848c; padding: 16px; }
</style>
</head>
<body>
<header>
  <b>aurora diagnostics</b>
  <a id="console-link" href="/">console</a>
  <span class="status" id="status"></span>
</header>
<main>
  <div id="rail">
    <div class="rail-section" id="rail-lives">
      <div class="rail-head">INCARNATIONS</div>
      <div class="rail-list" id="lives"></div>
    </div>
    <div class="rail-section" id="rail-lanes">
      <div class="rail-head">STREAMS</div>
      <div class="rail-list" id="lanes"></div>
    </div>
  </div>
  <div id="center">
    <div class="toolbar">
      <b id="turns-title">no incarnation selected</b>
      <span id="turns-page"></span>
      <span class="gap"></span>
      <button id="turns-newer" type="button">newer</button>
      <button id="turns-older" type="button">older</button>
      <label><input type="checkbox" id="turns-live" checked> follow</label>
    </div>
    <div class="pane" id="turns"></div>
  </div>
  <div id="right">
    <div class="toolbar">
      <b id="req-title">no stream selected</b>
      <span id="req-page"></span>
      <span class="gap"></span>
      <button id="req-newer" type="button">newer</button>
      <button id="req-older" type="button">older</button>
      <label><input type="checkbox" id="req-live" checked> follow</label>
    </div>
    <div class="lane-summary" id="lane-summary"></div>
    <div class="pane" id="requests"></div>
  </div>
</main>
<script>
/* ---------- helpers ---------- */
function fmtWhen(epoch) {
  if (epoch == null) return "—";
  var d = new Date(epoch * 1000);
  function p(n) { return (n < 10 ? "0" : "") + n; }
  return p(d.getUTCHours()) + ":" + p(d.getUTCMinutes()) + ":" + p(d.getUTCSeconds());
}
function fmtAgo(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return seconds + "s";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h";
  return Math.floor(seconds / 86400) + "d";
}
function fmtTokens(n) {
  if (n == null) return "—";
  if (n < 10000) return String(n);
  if (n < 1000000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  return (n / 1000000).toFixed(1).replace(/\.0$/, "") + "M";
}
function fmtSpan(seconds) {
  if (seconds == null) return "—";
  seconds = Math.round(seconds);
  if (seconds < 60) return seconds + "s";
  var d = Math.floor(seconds / 86400);
  var h = Math.floor((seconds % 86400) / 3600);
  var m = Math.floor((seconds % 3600) / 60);
  if (d) return d + "d " + h + "h";
  if (h) return h + "h " + m + "m";
  return m + "m " + (seconds % 60) + "s";
}
function lifeTitle(life) {
  if (life.current) return "life " + life.ordinal + " — current";
  return "life " + life.ordinal + " — ended (" + (life.ending_kind || "unknown") + ")";
}
function pageLabel(offset, limit, total) {
  if (!total) return "0 of 0";
  var to = Math.min(offset + limit, total);
  return (offset + 1) + "–" + to + " of " + total;
}

/* ---------- api ---------- */
var token = new URLSearchParams(location.search).get("token") || "";
history.replaceState(null, "", location.pathname);
document.getElementById("console-link").href = "/?token=" + encodeURIComponent(token);
function api(url) {
  return fetch(url, {headers: {"X-Console-Token": token}}).then(function (r) {
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
function setStatus(text, isError) {
  var el = document.getElementById("status");
  el.textContent = text;
  el.className = "status" + (isError ? " err" : "");
}
function fail(err) { setStatus(String(err && err.message || err), true); }

/* ---------- state ---------- */
var TURNS_LIMIT = 30;
var REQ_LIMIT = 50;
var selLife = null;
var selLane = null;
var turnsOffset = 0;
var reqOffset = 0;
var turnsTotal = 0;
var reqTotal = 0;

/* ---------- lives ---------- */
function liveRow(life) {
  var row = document.createElement("button");
  row.type = "button";
  row.className = "row" + (life.ordinal === selLife ? " sel" : "");
  var title = document.createElement("div");
  title.className = "title";
  var name = document.createElement("span");
  name.textContent = "life " + life.ordinal;
  var kind = document.createElement("span");
  if (life.current) {
    kind.textContent = "current";
    kind.className = "kind-current";
  } else {
    kind.textContent = life.ending_kind || "unknown";
    kind.className = "kind-" + (life.ending_kind || "unknown");
  }
  title.append(name, kind);
  var meta = document.createElement("div");
  meta.className = "meta";
  var bits = [life.turns + " turns"];
  if (life.subcalls) bits.push(life.subcalls + " subcalls");
  if (life.began_epoch != null && life.ended_epoch != null) {
    bits.push(fmtSpan(life.ended_epoch - life.began_epoch));
  }
  meta.textContent = bits.join(" · ");
  if (life.errors) {
    var err = document.createElement("span");
    err.className = "err";
    err.textContent = " · " + life.errors + " errors";
    meta.appendChild(err);
  }
  row.append(title, meta);
  if (life.summary) {
    var note = document.createElement("div");
    note.className = "note";
    note.textContent = life.summary;
    note.title = life.summary;
    row.appendChild(note);
  }
  row.onclick = function () { selectLife(life.ordinal); };
  return row;
}
function renderLives(lives) {
  var box = document.getElementById("lives");
  box.textContent = "";
  lives.forEach(function (life) { box.appendChild(liveRow(life)); });
}
function loadLives() {
  return api("/api/diag/incarnations").then(function (d) {
    if (selLife === null && d.incarnations.length) {
      selLife = d.incarnations[0].ordinal;
      loadTurns();
    }
    renderLives(d.incarnations);
  }).catch(fail);
}
function selectLife(ordinal) {
  selLife = ordinal;
  turnsOffset = 0;
  loadLives();
  loadTurns();
}

/* ---------- turns ---------- */
function textSection(label, kind, text, chars, truncated, open) {
  var details = document.createElement("details");
  details.className = "section-" + kind;
  details.open = !!open;
  var summary = document.createElement("summary");
  var lbl = document.createElement("span");
  lbl.className = "lbl";
  lbl.textContent = label;
  summary.append(lbl, document.createTextNode(" · " + chars + " chars"));
  if (truncated) {
    var t = document.createElement("span");
    t.className = "truncated";
    t.textContent = " · clipped";
    summary.appendChild(t);
  }
  var pre = document.createElement("pre");
  pre.textContent = text;
  details.append(summary, pre);
  return details;
}
function turnCard(turn) {
  var card = document.createElement("div");
  card.className = "card";
  var head = document.createElement("div");
  head.className = "head";
  var idx = document.createElement("span");
  idx.className = "idx";
  idx.textContent = "#" + turn.index;
  var when = document.createElement("span");
  when.textContent = fmtWhen(turn.epoch);
  when.title = turn.timestamp || "";
  var kind = document.createElement("span");
  kind.className = "kind-" + turn.kind;
  kind.textContent = turn.kind;
  head.append(idx, when, kind);
  if (turn.model) {
    var model = document.createElement("span");
    model.className = "model";
    model.textContent = turn.model;
    head.appendChild(model);
  }
  (turn.tool_calls || []).forEach(function (tc) {
    var badge = document.createElement("span");
    badge.className = "badge-tool";
    badge.textContent = tc.name || "?";
    head.appendChild(badge);
  });
  if (turn.error) {
    var err = document.createElement("span");
    err.className = "badge-err";
    err.textContent = "error";
    head.appendChild(err);
  }
  var rawBtn = document.createElement("button");
  rawBtn.type = "button";
  rawBtn.className = "raw-btn";
  rawBtn.textContent = "raw";
  rawBtn.onclick = function () { toggleRaw(card, turn.index); };
  head.appendChild(rawBtn);
  card.appendChild(head);
  if (turn.prompt) {
    var prompt = document.createElement("div");
    prompt.className = "meta";
    prompt.textContent = "prompt: " + turn.prompt;
    card.appendChild(prompt);
  }
  if (turn.reasoning) {
    card.appendChild(textSection("reasoning", "reasoning", turn.reasoning,
      turn.reasoning_chars, turn.reasoning_truncated, false));
  }
  if (turn.content) {
    card.appendChild(textSection("content", "content", turn.content,
      turn.content_chars, turn.content_truncated, true));
  }
  (turn.tool_calls || []).forEach(function (tc) {
    card.appendChild(textSection((tc.name || "?") + " arguments", "args",
      tc.arguments, tc.arguments_chars, tc.arguments_truncated,
      tc.arguments_chars <= 800));
  });
  if (turn.error) {
    var section = document.createElement("div");
    section.className = "section-error";
    var pre = document.createElement("pre");
    pre.textContent = (turn.error.code != null ? turn.error.code + ": " : "")
      + (turn.error.message || "");
    section.appendChild(pre);
    card.appendChild(section);
  }
  return card;
}
function toggleRaw(card, index) {
  var existing = card.querySelector(".raw");
  if (existing) { existing.remove(); return; }
  api("/api/diag/entry?index=" + index).then(function (d) {
    var box = document.createElement("div");
    box.className = "raw";
    var pre = document.createElement("pre");
    pre.textContent = d.raw + (d.truncated ? "\n… clipped at " + d.raw.length +
      " of " + d.chars + " chars" : "");
    box.appendChild(pre);
    card.appendChild(box);
  }).catch(fail);
}
function renderTurns(page) {
  turnsTotal = page.total;
  document.getElementById("turns-title").textContent = "life " + page.life;
  document.getElementById("turns-page").textContent =
    pageLabel(page.offset, page.turns.length, page.total) + " (newest first)";
  document.getElementById("turns-newer").disabled = page.offset === 0;
  document.getElementById("turns-older").disabled =
    page.offset + page.turns.length >= page.total;
  var box = document.getElementById("turns");
  box.textContent = "";
  if (!page.turns.length) {
    var empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "no turns in the live transcript for this life";
    box.appendChild(empty);
    return;
  }
  page.turns.forEach(function (turn) { box.appendChild(turnCard(turn)); });
}
function loadTurns() {
  if (selLife === null) return;
  api("/api/diag/incarnation?life=" + selLife + "&offset=" + turnsOffset +
      "&limit=" + TURNS_LIMIT).then(function (page) {
    if (page.life !== selLife) return;
    renderTurns(page);
    setStatus("updated " + fmtWhen(Date.now() / 1000), false);
  }).catch(fail);
}

/* ---------- lanes ---------- */
function laneRow(lane) {
  var row = document.createElement("button");
  row.type = "button";
  row.className = "row" + (lane.name === selLane ? " sel" : "");
  var title = document.createElement("div");
  title.className = "title";
  var name = document.createElement("span");
  var dot = document.createElement("span");
  dot.className = "dot" + (lane.in_flight ? " busy" : (lane.bound ? " on" : ""));
  name.append(dot, document.createTextNode(lane.name));
  var last = document.createElement("span");
  last.className = "meta";
  last.textContent = lane.last_epoch != null
    ? fmtAgo(Date.now() / 1000 - lane.last_epoch) + " ago" : "idle";
  title.append(name, last);
  var meta = document.createElement("div");
  meta.className = "meta";
  var bits = [
    fmtTokens(lane.tokens_hour) + " tok/h",
    lane.requests_hour + " req/h",
    lane.transcript_entries + " logged",
  ];
  meta.textContent = bits.join(" · ");
  if (lane.errors_hour) {
    var err = document.createElement("span");
    err.className = "err";
    err.textContent = " · " + lane.errors_hour + " err/h";
    meta.appendChild(err);
  }
  row.append(title, meta);
  row.onclick = function () { selectLane(lane.name); };
  return row;
}
function renderLanes(lanes) {
  var box = document.getElementById("lanes");
  box.textContent = "";
  lanes.forEach(function (lane) { box.appendChild(laneRow(lane)); });
  var lane = null;
  lanes.forEach(function (l) { if (l.name === selLane) lane = l; });
  renderLaneSummary(lane);
}
function renderLaneSummary(lane) {
  var box = document.getElementById("lane-summary");
  box.textContent = "";
  if (!lane) return;
  var facts = [
    ["state", lane.in_flight ? lane.in_flight + " in flight" : (lane.bound ? "bound" : "unbound")],
    ["last", lane.last_epoch != null ? fmtAgo(Date.now() / 1000 - lane.last_epoch) + " ago" : "—"],
    ["hour", lane.requests_hour + " req · " + lane.errors_hour + " err · "
      + fmtTokens(lane.tokens_hour) + " tok"],
    ["total", lane.requests_total + " req · " + lane.errors_total + " err · "
      + fmtTokens(lane.tokens_total) + " tok"],
  ];
  facts.forEach(function (pair) {
    var span = document.createElement("span");
    var b = document.createElement("b");
    b.textContent = pair[1];
    span.append(document.createTextNode(pair[0] + " "), b);
    box.appendChild(span);
  });
}
function loadLanes() {
  return api("/api/diag/streams").then(function (d) {
    if (selLane === null && d.streams.length) {
      selLane = d.streams[0].name;
      loadRequests();
    }
    renderLanes(d.streams);
  }).catch(fail);
}
function selectLane(name) {
  selLane = name;
  reqOffset = 0;
  loadLanes();
  loadRequests();
}

/* ---------- requests ---------- */
function requestRow(req) {
  var tr = document.createElement("tr");
  tr.className = "rowbtn";
  function cell(text, cls) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
  }
  tr.appendChild(cell("#" + req.index));
  var when = cell(fmtWhen(req.epoch));
  when.title = req.timestamp || "";
  tr.appendChild(when);
  tr.appendChild(cell(req.model || "—"));
  tr.appendChild(cell(String(req.messages), "num"));
  tr.appendChild(cell(fmtTokens(req.prompt_tokens), "num"));
  tr.appendChild(cell(fmtTokens(req.completion_tokens), "num"));
  tr.appendChild(cell(String(req.content_chars), "num"));
  var status = document.createElement("td");
  if (req.error) {
    var err = document.createElement("span");
    err.className = "err";
    err.textContent = req.error;
    err.title = req.error;
    status.appendChild(err);
  } else {
    status.textContent = "ok";
  }
  tr.appendChild(status);
  tr.onclick = function () { toggleRequestRaw(tr, req.index); };
  return tr;
}
function toggleRequestRaw(tr, index) {
  var next = tr.nextSibling;
  if (next && next.className === "rawrow") { next.remove(); return; }
  api("/api/diag/entry?index=" + index).then(function (d) {
    var row = document.createElement("tr");
    row.className = "rawrow";
    var td = document.createElement("td");
    td.colSpan = 8;
    var box = document.createElement("div");
    box.className = "raw";
    var pre = document.createElement("pre");
    pre.textContent = d.raw + (d.truncated ? "\n… clipped at " + d.raw.length +
      " of " + d.chars + " chars" : "");
    box.appendChild(pre);
    td.appendChild(box);
    row.appendChild(td);
    tr.parentNode.insertBefore(row, tr.nextSibling);
  }).catch(fail);
}
function renderRequests(page) {
  reqTotal = page.total;
  document.getElementById("req-title").textContent = page.name;
  document.getElementById("req-page").textContent =
    pageLabel(page.offset, page.requests.length, page.total) + " (newest first)";
  document.getElementById("req-newer").disabled = page.offset === 0;
  document.getElementById("req-older").disabled =
    page.offset + page.requests.length >= page.total;
  var box = document.getElementById("requests");
  box.textContent = "";
  if (!page.requests.length) {
    var empty = document.createElement("div");
    empty.className = "empty";
    empty.textContent = "no requests in the live transcript for this stream";
    box.appendChild(empty);
    return;
  }
  var table = document.createElement("table");
  var head = document.createElement("tr");
  [["entry", ""], ["when", ""], ["model", ""], ["msgs", "num"], ["in tok", "num"],
   ["out tok", "num"], ["chars", "num"], ["result", ""]].forEach(function (pair) {
    var th = document.createElement("th");
    if (pair[1]) th.className = pair[1];
    th.textContent = pair[0];
    head.appendChild(th);
  });
  table.appendChild(head);
  page.requests.forEach(function (req) { table.appendChild(requestRow(req)); });
  box.appendChild(table);
}
function loadRequests() {
  if (selLane === null) return;
  api("/api/diag/stream?name=" + encodeURIComponent(selLane) + "&offset=" + reqOffset +
      "&limit=" + REQ_LIMIT).then(function (page) {
    if (page.name !== selLane) return;
    renderRequests(page);
  }).catch(fail);
}

/* ---------- boot ---------- */
document.getElementById("turns-newer").onclick = function () {
  turnsOffset = Math.max(0, turnsOffset - TURNS_LIMIT);
  loadTurns();
};
document.getElementById("turns-older").onclick = function () {
  if (turnsOffset + TURNS_LIMIT < turnsTotal) {
    turnsOffset += TURNS_LIMIT;
    loadTurns();
  }
};
document.getElementById("req-newer").onclick = function () {
  reqOffset = Math.max(0, reqOffset - REQ_LIMIT);
  loadRequests();
};
document.getElementById("req-older").onclick = function () {
  if (reqOffset + REQ_LIMIT < reqTotal) {
    reqOffset += REQ_LIMIT;
    loadRequests();
  }
};
function tick() {
  loadLives();
  loadLanes();
  if (document.getElementById("turns-live").checked && turnsOffset === 0) loadTurns();
  if (document.getElementById("req-live").checked && reqOffset === 0) loadRequests();
}
loadLives();
loadLanes();
setInterval(tick, 5000);
</script>
</body>
</html>
"""
