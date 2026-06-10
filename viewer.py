import os
import json
import http.server
import socketserver

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
TRANSCRIPT_NAME = "agent_life_transcript.jsonl"
VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "8090"))


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
            continue
        turns.append(_summarize_turn(entry, index))
    return turns, total


PAGE_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aurora transcript</title>
<style>
  body { background:#111; color:#e6e6e6; font:13px/1.5 ui-monospace,Menlo,Consolas,monospace; margin:0; }
  header { position:sticky; top:0; z-index:1; background:#1a1a1a; border-bottom:1px solid #333;
           padding:8px 12px; display:flex; gap:14px; align-items:center; }
  header strong { color:#fff; }
  #count { color:#9bbf9b; }
  #status { color:#6c6; }
  #status.paused { color:#e0a85a; cursor:pointer; text-decoration:underline; }
  #feed { padding:12px; }
  #empty { color:#8a8a8a; padding:24px 12px; font-style:italic; }
  .card { border:1px solid #333; border-left:3px solid #333; border-radius:4px; margin:6px 0;
          background:#181818; padding:8px 10px; }
  .card.error { border-color:#a33; border-left-color:#a33; }
  .card.latest { border-left-color:#7ab4e6; }
  .head { display:flex; gap:10px; align-items:baseline; white-space:nowrap;
          overflow:hidden; text-overflow:ellipsis; }
  .head .ts { color:#8a8a8a; } .head .model { color:#7ab4e6; }
  .head .what { color:#999; overflow:hidden; text-overflow:ellipsis; }
  .msg { margin:4px 0; } .role { color:#9bbf9b; } .name { color:#9a9a9a; }
  .reasoning { color:#9a9a9a; font-style:italic; white-space:pre-wrap; margin:4px 0; }
  .content { color:#e6e6e6; white-space:pre-wrap; }
  .tool { color:#e0a85a; white-space:pre-wrap; margin:4px 0; }
  .err { color:#ff9090; white-space:pre-wrap; margin:4px 0; }
  details { margin:4px 0; }
  details > summary { cursor:pointer; color:#7e8aa0; font-size:12px; list-style:none; }
  details > summary::-webkit-details-marker { display:none; }
  details > summary::before { content:"\25B8 "; color:#777; }
  details[open] > summary::before { content:"\25BE "; }
  details > summary:focus-visible, #status:focus-visible { outline:2px solid #7ab4e6; outline-offset:1px; }
  details .content, details .msg { margin-top:4px; }
  pre.raw { background:#0c0c0c; color:#9a9; padding:8px; overflow:auto; max-height:300px; margin:4px 0 0; }
  .sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }
</style>
</head>
<body>
<header>
  <strong>aurora transcript</strong>
  <span id="count" aria-live="polite">0 turns</span>
  <span id="status" role="button" tabindex="0">following live</span>
</header>
<div id="feed"></div>
<div id="empty">waiting for the agent…</div>
<div id="live" class="sr-only" aria-live="polite"></div>
<script>
let have = 0;
let following = true;
let newWhilePaused = 0;
const feed = document.getElementById('feed');
const statusEl = document.getElementById('status');
const countEl = document.getElementById('count');
const emptyEl = document.getElementById('empty');
const liveEl = document.getElementById('live');

function esc(s){ return (s==null?'':String(s)).replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function nearBottom(){ return window.innerHeight + window.scrollY >= document.body.scrollHeight - 40; }
function fmtTs(s){ const m = /T(\d\d:\d\d:\d\d)/.exec(s||''); return m ? m[1] : (s||''); }

function summaryText(t){
  const r = t.response;
  if (r.error) return 'error';
  if (r.tool_calls.length) return 'assistant: tool ' + r.tool_calls.map(c=>c.name||'(unnamed)').join(', ');
  if (r.reasoning && !r.content) return 'assistant: (reasoning)';
  if (r.content) return 'assistant: ' + r.content.slice(0,80);
  return 'turn';
}

function preview(s, n){
  s = (s==null?'':String(s)).replace(/\s+/g,' ').trim();
  return s.length>n ? s.slice(0,n)+'…' : s;
}

function render(t){
  const card = document.createElement('div');
  card.className = 'card' + (t.response.error ? ' error' : '');
  const prev = feed.querySelector('.card.latest');
  if (prev) prev.classList.remove('latest');
  card.classList.add('latest');
  const r = t.response;

  let html = '<div class="head"><span class="ts">'+esc(fmtTs(t.timestamp))+'</span>'
    + '<span class="model">'+esc(t.model||'')+'</span>'
    + '<span class="what">'+esc(summaryText(t))+'</span></div>';

  // always visible: what it thought, and what it did
  if (r.reasoning) html += '<div class="reasoning">'+esc(r.reasoning)+'</div>';
  for (const c of r.tool_calls) html += '<div class="tool">'+esc(c.name||'(unnamed)')+' '+esc(c.arguments||'')+'</div>';
  if (r.error) html += '<div class="err">'+esc(JSON.stringify(r.error))+'</div>';

  // collapsed by default: the model's text reply
  if (r.content){
    const p = preview(r.content, 60);
    html += '<details><summary>reply'+(p?': '+esc(p):'')+'</summary>'
      + '<div class="content">'+esc(r.content)+'</div></details>';
  }

  // collapsed by default: the request context and prior tool results
  if (t.request_messages && t.request_messages.length){
    let ctx = '';
    for (const m of t.request_messages){
      let line = '<div class="msg"><span class="role">'+esc(m.role)+'</span>'
        + (m.name?' <span class="name">('+esc(m.name)+')</span>':'') + ' ';
      if (m.content) line += '<span class="content">'+esc(m.content)+'</span>';
      for (const c of m.tool_calls) line += '<span class="tool">'+esc(c.name||'(unnamed)')+' '+esc(c.arguments||'')+'</span>';
      line += '</div>';
      ctx += line;
    }
    html += '<details><summary>context · '+t.request_messages.length+' messages</summary>'+ctx+'</details>';
  }

  // collapsed by default: the full raw turn
  html += '<details><summary>raw json</summary><pre class="raw">'+esc(JSON.stringify(t, null, 2))+'</pre></details>';

  card.innerHTML = html;
  feed.appendChild(card);
}

function setStatus(){
  if (following){ statusEl.textContent = 'following live'; statusEl.className = ''; }
  else { statusEl.textContent = newWhilePaused ? '▲ ' + newWhilePaused + ' new — click to follow' : 'paused — click to follow'; statusEl.className = 'paused'; }
}

async function poll(){
  try {
    const res = await fetch('/api/turns?since='+have);
    const data = await res.json();
    if (typeof data.total === 'number'){
      if (data.total > 0) emptyEl.style.display = 'none';
      countEl.textContent = data.total + (data.total===1 ? ' turn' : ' turns');
    }
    if (data.turns && data.turns.length){
      const wasNear = nearBottom();
      for (const t of data.turns) render(t);
      have = data.total;
      liveEl.textContent = data.total + ' turns';
      if (following && wasNear) window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' });
      else if (!following){ newWhilePaused += data.turns.length; setStatus(); }
    } else if (typeof data.total === 'number') {
      have = data.total;
    }
  } catch (e) { /* try again next tick */ }
}

function follow(){ following = true; newWhilePaused = 0; setStatus(); window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }); }

window.addEventListener('scroll', ()=>{
  if (nearBottom()){ if(!following){ following = true; newWhilePaused = 0; setStatus(); } }
  else if (following){ following = false; setStatus(); }
});
statusEl.addEventListener('click', ()=>{ if(!following) follow(); });
statusEl.addEventListener('keydown', e=>{ if((e.key==='Enter'||e.key===' ') && !following){ e.preventDefault(); follow(); }});

poll();
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
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/":
            self._send(200, PAGE_HTML, "text/html; charset=utf-8")
            return
        if path == "/api/turns":
            since = 0
            if "?" in self.path:
                query = self.path.split("?", 1)[1]
                for part in query.split("&"):
                    if part.startswith("since="):
                        try:
                            since = int(part[len("since=") :])
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
