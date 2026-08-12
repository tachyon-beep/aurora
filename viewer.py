import os
import json
import http.server
import socketserver
import urllib.parse

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
TRANSCRIPT_NAME = "agent_life_transcript.jsonl"
VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "8090"))
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'unsafe-inline'; "
        "style-src 'unsafe-inline'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "base-uri 'none'; "
        "form-action 'none'; "
        "frame-ancestors 'none'"
    ),
}


def _summarize_turn(entry, index):
    request = entry.get("request", {}) if isinstance(entry, dict) else {}
    response = entry.get("response", {}) if isinstance(entry, dict) else {}

    request_messages = []
    for msg in request.get("messages", []) or []:
        tool_calls = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
        request_messages.append(
            {
                "role": msg.get("role", "unknown"),
                "name": msg.get("name"),
                "content": msg.get("content"),
                "tool_calls": tool_calls,
            }
        )

    reasoning = None
    content = None
    resp_tool_calls = []
    error = response.get("error")
    choices = response.get("choices") or []
    if choices:
        message = choices[0].get("message", {}) or {}
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        content = message.get("content")
        for tc in message.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            resp_tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})

    return {
        "index": index,
        "timestamp": entry.get("timestamp") if isinstance(entry, dict) else None,
        "model": request.get("model"),
        "request_messages": request_messages,
        "response": {
            "reasoning": reasoning,
            "content": content,
            "tool_calls": resp_tool_calls,
            "error": error,
        },
    }


def load_turns(transcript_path, since):
    if not os.path.exists(transcript_path):
        return [], 0
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return [], 0
    total = len(lines)
    turns = []
    for index in range(since, total):
        line = lines[index].strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            if index == total - 1:
                total = index
                break
            continue
        turns.append(_summarize_turn(entry, index))
    return turns, total


PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<title>aurora transcript</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #101316;
    --panel: #171c20;
    --panel-2: #1f252b;
    --text: #eef3f6;
    --muted: #a6b0b8;
    --subtle: #79848c;
    --border: #303941;
    --focus: #77bdfb;
    --accent: #66d9c2;
    --tool: #f0bd68;
    --error: #ff8d8d;
    --error-bg: #2a1719;
    --shadow: rgb(0 0 0 / 0.22);
    --radius: 6px;
    --font-ui: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
    --font-code: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  }

  * { box-sizing: border-box; }

  body {
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 0.875rem/1.55 var(--font-ui);
  }

  .topbar {
    position: sticky;
    top: 0;
    z-index: 2;
    background: color-mix(in srgb, var(--panel) 94%, black);
    border-bottom: 1px solid var(--border);
    box-shadow: 0 8px 24px var(--shadow);
  }

  header {
    display: grid;
    gap: 0.75rem;
    padding: 0.875rem 1rem 0.625rem;
  }

  h1 {
    margin: 0;
    font-size: 1rem;
    line-height: 1.25;
    font-weight: 700;
  }

  .meta {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    align-items: center;
    color: var(--muted);
  }

  .pill {
    display: inline-flex;
    min-height: 1.75rem;
    align-items: center;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0.2rem 0.65rem;
    background: var(--panel-2);
    color: var(--muted);
  }

  #count { color: var(--accent); }

  #status.paused {
    border-color: color-mix(in srgb, var(--tool) 55%, var(--border));
    color: var(--tool);
  }

  .toolbar {
    display: grid;
    grid-template-columns: 1fr;
    gap: 0.625rem;
    padding: 0 1rem 0.875rem;
  }

  .control-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.625rem;
    align-items: center;
  }

  .searchbox { min-width: min(100%, 18rem); flex: 1 1 18rem; }

  input,
  select,
  button {
    min-height: 2.75rem;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    background: #11171b;
    color: var(--text);
    font: inherit;
  }

  input,
  select {
    width: 100%;
    padding: 0.5rem 0.7rem;
  }

  select { max-width: 12rem; }

  button {
    cursor: pointer;
    padding: 0.5rem 0.85rem;
    background: #22313b;
    color: var(--text);
  }

  button:hover,
  input:hover,
  select:hover { border-color: color-mix(in srgb, var(--focus) 55%, var(--border)); }

  button:focus-visible,
  input:focus-visible,
  select:focus-visible,
  details > summary:focus-visible {
    outline: 2px solid var(--focus);
    outline-offset: 2px;
  }

  button[hidden] { display: none; }

  #clearFiltersButton {
    min-width: 5.5rem;
  }

  .filter-label {
    color: var(--muted);
    font-size: 0.8125rem;
  }

  #feed {
    display: grid;
    gap: 0.625rem;
    padding: 1rem;
  }

  #empty,
  #noMatches {
    color: var(--muted);
    padding: 2rem 1rem;
  }

  .card {
    border: 1px solid var(--border);
    border-left: 4px solid var(--border);
    border-radius: var(--radius);
    background: var(--panel);
    padding: 0.75rem;
    overflow: hidden;
  }

  .card[hidden] { display: none; }

  .card.error {
    border-color: color-mix(in srgb, var(--error) 45%, var(--border));
    border-left-color: var(--error);
    background: color-mix(in srgb, var(--error-bg) 48%, var(--panel));
  }

  .card.latest { border-left-color: var(--focus); }

  .head {
    display: grid;
    grid-template-columns: auto auto auto auto minmax(0, 1fr);
    gap: 0.5rem;
    align-items: baseline;
  }

  .turnno,
  .ts,
  .model,
  .what {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .turnno { color: var(--subtle); font-family: var(--font-code); }
  .ts { color: var(--muted); font-family: var(--font-code); }
  .model { color: var(--focus); font-family: var(--font-code); }
  .what { color: var(--muted); }

  .badge {
    display: inline-flex;
    min-height: 1.35rem;
    align-items: center;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 0 0.45rem;
    font-size: 0.75rem;
    color: var(--muted);
    background: #12181d;
  }

  .badge.tool { color: var(--tool); border-color: color-mix(in srgb, var(--tool) 45%, var(--border)); }
  .badge.reply { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 45%, var(--border)); }
  .badge.error { color: var(--error); border-color: color-mix(in srgb, var(--error) 45%, var(--border)); }

  .msg,
  .reasoning,
  .content,
  .tool,
  .err,
  pre.raw {
    font-family: var(--font-code);
    font-size: 0.8125rem;
  }

  .msg { margin: 0.35rem 0; }
  .role { color: var(--accent); }
  .name { color: var(--subtle); }
  .reasoning { color: var(--muted); font-style: italic; white-space: pre-wrap; margin: 0.45rem 0; }
  .content { color: var(--text); white-space: pre-wrap; overflow-wrap: anywhere; }
  .tool { color: var(--tool); white-space: pre-wrap; overflow-wrap: anywhere; margin: 0.45rem 0; }
  .err { color: var(--error); white-space: pre-wrap; overflow-wrap: anywhere; margin: 0.45rem 0; }

  details { margin: 0.45rem 0 0; }

  details > summary {
    cursor: pointer;
    color: var(--muted);
    font-size: 0.8125rem;
    min-height: 1.75rem;
    padding: 0.25rem 0;
  }

  details .content,
  details .msg { margin-top: 0.35rem; }

  pre.raw {
    background: #0b0f12;
    color: #b1decf;
    padding: 0.75rem;
    overflow: auto;
    max-height: 22rem;
    margin: 0.35rem 0 0;
    border-radius: var(--radius);
    border: 1px solid #263038;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  @media (min-width: 48rem) {
    header {
      grid-template-columns: minmax(0, 1fr) auto;
      align-items: center;
    }

    .toolbar {
      grid-template-columns: minmax(18rem, 1fr) auto;
      align-items: center;
    }

    #feed { padding: 1rem 1.25rem; }
  }

  @media (max-width: 42rem) {
    .head {
      grid-template-columns: auto 1fr;
      align-items: center;
    }

    .head .ts { justify-self: end; }
    .head .badge { grid-column: 1; }
    .head .model { grid-column: 2; }
    .head .what { grid-column: 1 / -1; white-space: normal; }
    select { max-width: none; }
  }

  @media (prefers-reduced-motion: reduce) {
    * { scroll-behavior: auto !important; }
  }
</style>
</head>
<body>
<div class="topbar">
  <header>
    <div>
      <h1>aurora transcript</h1>
      <div class="meta">
        <span id="count" class="pill" aria-live="polite">0 turns</span>
        <span id="visibleCount" class="pill" aria-live="polite">0 shown</span>
        <span id="status" class="pill" aria-live="polite">following live</span>
      </div>
    </div>
    <button type="button" id="followButton" hidden>Follow live</button>
  </header>
  <div class="toolbar" aria-label="Transcript controls">
    <div class="searchbox" role="search">
      <label for="searchInput" class="sr-only">Search turns</label>
      <input id="searchInput" type="search" autocomplete="off" placeholder="Search turns" aria-keyshortcuts="/">
    </div>
    <div class="control-row">
      <label class="filter-label" for="typeFilter">Type</label>
      <select id="typeFilter" aria-label="Filter turns">
        <option value="all">All turns</option>
        <option value="tool">Tools</option>
        <option value="reply">Replies</option>
        <option value="error">Errors</option>
      </select>
      <button type="button" id="clearFiltersButton" hidden>Clear</button>
    </div>
  </div>
</div>
<main id="feed" aria-label="Transcript turns"></main>
<div id="empty">waiting for the agent...</div>
<div id="noMatches" hidden>No matching turns</div>
<div id="live" class="sr-only" aria-live="polite"></div>
<script>
let have = 0;
let following = true;
let polling = false;
let newWhilePaused = 0;
const feed = document.getElementById('feed');
const statusEl = document.getElementById('status');
const countEl = document.getElementById('count');
const visibleCountEl = document.getElementById('visibleCount');
const emptyEl = document.getElementById('empty');
const noMatchesEl = document.getElementById('noMatches');
const liveEl = document.getElementById('live');
const followButton = document.getElementById('followButton');
const searchInput = document.getElementById('searchInput');
const typeFilter = document.getElementById('typeFilter');
const clearFiltersButton = document.getElementById('clearFiltersButton');

function esc(s){
  return (s==null?'':String(s)).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
}

function nearBottom(){
  return window.innerHeight + window.scrollY >= document.body.scrollHeight - 48;
}

function fmtTs(s){
  const m = /T(\d\d:\d\d:\d\d)/.exec(s||'');
  return m ? m[1] : (s||'');
}

function response(t){ return (t && t.response) || {}; }
function toolCalls(r){ return (r && r.tool_calls) || []; }
function messages(t){ return (t && t.request_messages) || []; }

function turnKind(t){
  const r = response(t);
  if (r.error) return 'error';
  if (toolCalls(r).length) return 'tool';
  if (r.content) return 'reply';
  return 'turn';
}

function kindLabel(kind){
  return ({error:'error', tool:'tool', reply:'reply', turn:'turn'})[kind] || 'turn';
}

function summaryText(t){
  const r = response(t);
  const calls = toolCalls(r);
  if (r.error) return 'error';
  if (calls.length) return 'assistant: tool ' + calls.map(c=>c.name||'(unnamed)').join(', ');
  if (r.reasoning && !r.content) return 'assistant: reasoning';
  if (r.content) return 'assistant: ' + preview(r.content, 90);
  return 'turn';
}

function preview(s, n){
  s = (s==null?'':String(s)).replace(/\s+/g,' ').trim();
  return s.length>n ? s.slice(0,n)+'...' : s;
}

function collectSearchText(t){
  const r = response(t);
  const parts = [fmtTs(t.timestamp), t.model, summaryText(t), turnKind(t), r.reasoning, r.content];
  for (const c of toolCalls(r)) parts.push(c.name, c.arguments);
  for (const m of messages(t)){
    parts.push(m.role, m.name, m.content);
    for (const c of m.tool_calls || []) parts.push(c.name, c.arguments);
  }
  if (r.error) parts.push(JSON.stringify(r.error));
  return parts.filter(Boolean).join(' ').toLowerCase();
}

function turnCountText(total){
  return total + (total===1 ? ' turn' : ' turns');
}

function shownCountText(visible, total){
  if (total === 0) return '0 shown';
  return visible === total ? total + ' shown' : visible + ' of ' + total + ' shown';
}

function render(t){
  const r = response(t);
  const kind = turnKind(t);
  const card = document.createElement('article');
  card.className = 'card' + (kind === 'error' ? ' error' : '');
  card.dataset.kind = kind;
  card.dataset.search = collectSearchText(t);
  const prev = feed.querySelector('.card.latest');
  if (prev) prev.classList.remove('latest');
  card.classList.add('latest');

  let html = '<div class="head"><span class="turnno">#'+esc((t.index||0)+1)+'</span>'
    + '<span class="ts">'+esc(fmtTs(t.timestamp))+'</span>'
    + '<span class="badge '+esc(kind)+'">'+esc(kindLabel(kind))+'</span>'
    + '<span class="model">'+esc(t.model||'')+'</span>'
    + '<span class="what">'+esc(summaryText(t))+'</span></div>';

  if (r.reasoning) html += '<div class="reasoning">'+esc(r.reasoning)+'</div>';
  for (const c of toolCalls(r)){
    html += '<div class="tool">'+esc(c.name||'(unnamed)')+' '+esc(c.arguments||'')+'</div>';
  }
  if (r.error) html += '<div class="err">'+esc(JSON.stringify(r.error))+'</div>';

  if (r.content){
    const p = preview(r.content, 72);
    html += '<details><summary>reply'+(p?': '+esc(p):'')+'</summary>'
      + '<div class="content">'+esc(r.content)+'</div></details>';
  }

  if (messages(t).length){
    let ctx = '';
    for (const m of messages(t)){
      let line = '<div class="msg"><span class="role">'+esc(m.role)+'</span>'
        + (m.name?' <span class="name">('+esc(m.name)+')</span>':'') + ' ';
      if (m.content) line += '<span class="content">'+esc(m.content)+'</span>';
      for (const c of m.tool_calls || []){
        line += '<span class="tool">'+esc(c.name||'(unnamed)')+' '+esc(c.arguments||'')+'</span>';
      }
      line += '</div>';
      ctx += line;
    }
    html += '<details><summary>context - '+messages(t).length+' messages</summary>'+ctx+'</details>';
  }

  html += '<details><summary>raw json</summary><pre class="raw">'+esc(JSON.stringify(t, null, 2))+'</pre></details>';

  card.innerHTML = html;
  feed.appendChild(card);
}

function filtersActive(){
  return Boolean(searchInput.value.trim()) || typeFilter.value !== 'all';
}

function applyFilters(){
  const q = searchInput.value.trim().toLowerCase();
  const type = typeFilter.value;
  let visible = 0;
  const total = feed.querySelectorAll('.card').length;
  for (const card of feed.querySelectorAll('.card')){
    const okType = type === 'all' || card.dataset.kind === type;
    const okSearch = !q || card.dataset.search.includes(q);
    const show = okType && okSearch;
    card.hidden = !show;
    if (show) visible += 1;
  }
  visibleCountEl.textContent = shownCountText(visible, total);
  clearFiltersButton.hidden = !filtersActive();
  noMatchesEl.hidden = visible > 0 || total === 0;
}

function resetFeed(){
  feed.replaceChildren();
  have = 0;
  newWhilePaused = 0;
  emptyEl.style.display = '';
  countEl.textContent = turnCountText(0);
  liveEl.textContent = turnCountText(0);
  applyFilters();
  setStatus();
}

function focusSearch(){
  searchInput.focus();
  searchInput.select();
}

function clearFilters(){
  searchInput.value = '';
  typeFilter.value = 'all';
  applyFilters();
}

function isTypingTarget(el){
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

function setStatus(){
  if (following){
    statusEl.textContent = 'following live';
    statusEl.className = 'pill';
    followButton.hidden = true;
    followButton.textContent = 'Follow live';
  } else {
    statusEl.textContent = newWhilePaused ? newWhilePaused + ' new turns' : 'paused';
    statusEl.className = 'pill paused';
    followButton.hidden = false;
    followButton.textContent = newWhilePaused ? 'Follow ' + newWhilePaused + ' new' : 'Follow live';
  }
}

async function fetchTurns(since){
  const res = await fetch('/api/turns?since='+since);
  if (!res.ok) return null;
  return await res.json();
}

async function poll(){
  if (polling) return;
  polling = true;
  try {
    let data = await fetchTurns(have);
    if (!data) return;
    if (typeof data.total === 'number' && data.total < have){
      resetFeed();
      data = await fetchTurns(0);
      if (!data) return;
    }
    if (typeof data.total === 'number'){
      emptyEl.style.display = data.total > 0 ? 'none' : '';
      countEl.textContent = turnCountText(data.total);
    }
    if (data.turns && data.turns.length){
      const wasNear = nearBottom();
      for (const t of data.turns) render(t);
      have = data.total;
      liveEl.textContent = turnCountText(data.total);
      applyFilters();
      if (following && wasNear) window.scrollTo({ top: document.body.scrollHeight, behavior: 'auto' });
      else if (!following){ newWhilePaused += data.turns.length; setStatus(); }
    } else if (typeof data.total === 'number') {
      have = data.total;
      applyFilters();
    }
  } catch (e) {
    return;
  } finally {
    polling = false;
  }
}

function follow(){
  following = true;
  newWhilePaused = 0;
  setStatus();
  window.scrollTo({ top: document.body.scrollHeight, behavior: 'auto' });
}

window.addEventListener('scroll', ()=>{
  if (nearBottom()){
    if(!following){ following = true; newWhilePaused = 0; setStatus(); }
  } else if (following){
    following = false;
    setStatus();
  }
});
followButton.addEventListener('click', follow);
searchInput.addEventListener('input', applyFilters);
typeFilter.addEventListener('change', applyFilters);
clearFiltersButton.addEventListener('click', clearFilters);
document.addEventListener('keydown', e=>{
  if (e.key === '/' && !e.ctrlKey && !e.metaKey && !e.altKey && !isTypingTarget(e.target)){
    e.preventDefault();
    focusSearch();
  } else if (e.key === 'Escape' && filtersActive()){
    clearFilters();
  }
});

poll();
setStatus();
applyFilters();
setInterval(poll, 2000);
</script>
</body>
</html>
"""


class ViewerHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, status, body, content_type):
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self._send(200, PAGE_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/turns":
            since = 0
            query = urllib.parse.parse_qs(parsed.query)
            if query.get("since"):
                try:
                    since = int(query["since"][0])
                except ValueError:
                    since = 0
            transcript_path = os.path.join(TRANSCRIPT_DIR, TRANSCRIPT_NAME)
            turns, total = load_turns(transcript_path, max(0, since))
            self._send(200, json.dumps({"turns": turns, "total": total}), "application/json")
            return
        self._send(404, "not found", "text/plain")


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


def make_server(port):
    return _Server(("0.0.0.0", port), ViewerHandler)


def main():
    server = make_server(VIEWER_PORT)
    print(
        f"transcript viewer on http://localhost:{VIEWER_PORT} (reading {TRANSCRIPT_DIR}/{TRANSCRIPT_NAME})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
