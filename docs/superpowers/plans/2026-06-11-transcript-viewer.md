# Transcript Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An ephemeral, read-only web UI that renders the recorder's transcript as a live, collapsible feed so you can watch what the agent is doing.

**Architecture:** A zero-dependency stdlib `http.server` app (`viewer.py`) reads `agent_life_transcript.jsonl` from the `transcripts` volume and serves a single self-contained HTML page plus a `/api/turns?since=N` JSON endpoint the page polls every 2s. It runs in its own tiny image behind a docker-compose profile, mounting the transcript read-only and publishing to host loopback only — isolated from the agent's networks.

**Tech Stack:** Python 3.13 stdlib (`http.server`, `json`, `urllib` for tests), Docker + Compose v2, pytest + ruff (already present).

**Reference spec:** `docs/superpowers/specs/2026-06-11-transcript-viewer-design.md`.

**Transcript schema reminder:** each JSONL line is `{"timestamp": "...Z", "request": {"model": str, "messages": [{role, content?, tool_calls?, name?}]}, "response": {"choices": [{"message": {"content"?, "reasoning_content"?|"reasoning"?, "tool_calls"?}}]} | {"error": {...}}}`.

---

## File Structure

- `viewer.py` — NEW. The whole viewer: `load_turns(transcript_path, since)` (pure parser/summarizer), `_summarize_turn(entry, index)` (one entry → render-ready dict), the `ViewerHandler` (`GET /` → HTML, `GET /api/turns` → JSON), `PAGE_HTML` (inline page), and `main()`.
- `tests/test_viewer.py` — NEW. Unit tests for `load_turns`/`_summarize_turn` + a light HTTP integration test.
- `Dockerfile.viewer` — NEW. Tiny image copying only `viewer.py`.
- `docker-compose.yml` — MODIFY. Add the `viewer` service under `profiles: ["viewer"]`.

---

## Task 1: `load_turns` + `_summarize_turn` (pure parsing/summarizing)

**Files:**
- Create: `viewer.py` (start it with imports + these two functions)
- Create: `tests/test_viewer.py`

- [ ] **Step 1: Write failing tests** in `tests/test_viewer.py`

```python
import json

import viewer


def _write_jsonl(path, entries):
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_load_turns_missing_file_is_empty(tmp_path):
    turns, total = viewer.load_turns(str(tmp_path / "nope.jsonl"), 0)
    assert turns == []
    assert total == 0


def test_load_turns_since_offset(tmp_path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(p, [
        {"timestamp": "T0", "request": {"model": "m", "messages": []}, "response": {}},
        {"timestamp": "T1", "request": {"model": "m", "messages": []}, "response": {}},
        {"timestamp": "T2", "request": {"model": "m", "messages": []}, "response": {}},
    ])
    turns, total = viewer.load_turns(str(p), 1)
    assert total == 3
    assert [t["index"] for t in turns] == [1, 2]
    assert [t["timestamp"] for t in turns] == ["T1", "T2"]


def test_load_turns_skips_malformed_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    with open(p, "w", encoding="utf-8") as f:
        f.write(json.dumps({"timestamp": "T0", "request": {"model": "m", "messages": []}, "response": {}}) + "\n")
        f.write("{ this is a half-written line\n")  # malformed / mid-append
    turns, total = viewer.load_turns(str(p), 0)
    # total counts physical lines; the malformed one is skipped from turns
    assert total == 2
    assert [t["index"] for t in turns] == [0]


def test_summarize_extracts_request_messages():
    entry = {
        "timestamp": "T",
        "request": {
            "model": "deepseek",
            "messages": [
                {"role": "system", "content": "fo explore"},
                {"role": "assistant", "tool_calls": [
                    {"function": {"name": "read_file", "arguments": '{"path": "agent.py"}'}}
                ]},
                {"role": "tool", "name": "read_file", "content": "1: import os"},
            ],
        },
        "response": {},
    }
    t = viewer._summarize_turn(entry, 5)
    assert t["index"] == 5
    assert t["model"] == "deepseek"
    msgs = t["request_messages"]
    assert msgs[0] == {"role": "system", "name": None, "content": "fo explore", "tool_calls": []}
    assert msgs[1]["tool_calls"][0] == {"name": "read_file", "arguments": '{"path": "agent.py"}'}
    assert msgs[2] == {"role": "tool", "name": "read_file", "content": "1: import os", "tool_calls": []}


def test_summarize_extracts_response_reasoning_content_and_tools():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {"choices": [{"message": {
            "reasoning_content": "let me think",
            "content": "here is my answer",
            "tool_calls": [{"function": {"name": "write_file", "arguments": "{}"}}],
        }}]},
    }
    t = viewer._summarize_turn(entry, 0)
    r = t["response"]
    assert r["reasoning"] == "let me think"
    assert r["content"] == "here is my answer"
    assert r["tool_calls"][0]["name"] == "write_file"
    assert r["error"] is None


def test_summarize_reads_reasoning_fallback_field():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {"choices": [{"message": {"reasoning": "alt field", "content": "x"}}]},
    }
    t = viewer._summarize_turn(entry, 0)
    assert t["response"]["reasoning"] == "alt field"


def test_summarize_captures_error_responses():
    entry = {
        "timestamp": "T",
        "request": {"model": "m", "messages": []},
        "response": {"error": {"message": "rate limited"}},
    }
    t = viewer._summarize_turn(entry, 0)
    assert t["response"]["error"] == {"message": "rate limited"}
    assert t["response"]["content"] is None
```

Run: `.venv/bin/python -m pytest tests/test_viewer.py -v` → FAIL (module/functions missing).

- [ ] **Step 2: Create `viewer.py`** with imports + the two functions

```python
import os
import json
import http.server
import socketserver

TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
TRANSCRIPT_NAME = "agent_life_transcript.jsonl"
VIEWER_PORT = int(os.environ.get("VIEWER_PORT", "8090"))


def _summarize_turn(entry, index):
    """Reduce one transcript entry to a render-ready dict."""
    request = entry.get("request", {}) if isinstance(entry, dict) else {}
    response = entry.get("response", {}) if isinstance(entry, dict) else {}

    request_messages = []
    for msg in request.get("messages", []) or []:
        tool_calls = []
        for tc in msg.get("tool_calls", []) or []:
            fn = tc.get("function", {}) or {}
            tool_calls.append({"name": fn.get("name"), "arguments": fn.get("arguments")})
        request_messages.append({
            "role": msg.get("role", "unknown"),
            "name": msg.get("name"),
            "content": msg.get("content"),
            "tool_calls": tool_calls,
        })

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
    """Return (turns, total). turns are summarized entries from line `since` onward.

    total is the physical line count. Lines that fail to parse (e.g. a final
    half-written line during a live poll) are skipped from turns but still counted,
    so the next poll picks them up once complete.
    """
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
```

- [ ] **Step 3: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_viewer.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add viewer.py tests/test_viewer.py
git commit -m "feat: viewer transcript parser/summarizer (load_turns)"
```

---

## Task 2: HTTP handler + inline page + `main()`

**Files:**
- Modify: `viewer.py` (add `PAGE_HTML`, `ViewerHandler`, `main`, `__main__`)
- Modify: `tests/test_viewer.py` (append an HTTP integration test)

- [ ] **Step 1: Append the failing integration test** to `tests/test_viewer.py`

```python
def test_http_serves_page_and_api(tmp_path, monkeypatch):
    import threading
    import urllib.request

    p = tmp_path / viewer.TRANSCRIPT_NAME
    _write_jsonl(p, [
        {"timestamp": "T0", "request": {"model": "m", "messages": [{"role": "user", "content": "hi"}]},
         "response": {"choices": [{"message": {"content": "hello"}}]}},
    ])
    monkeypatch.setattr(viewer, "TRANSCRIPT_DIR", str(tmp_path))

    server = viewer.make_server(0)  # port 0 = ephemeral
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
            assert r.status == 200
            body = r.read().decode("utf-8")
            assert "<!doctype html>" in body.lower()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/turns?since=0") as r:
            assert r.status == 200
            data = json.loads(r.read().decode("utf-8"))
            assert data["total"] == 1
            assert data["turns"][0]["response"]["content"] == "hello"
            assert data["turns"][0]["request_messages"][0]["content"] == "hi"
    finally:
        server.shutdown()
        server.server_close()
```

Run: `.venv/bin/python -m pytest tests/test_viewer.py::test_http_serves_page_and_api -v` → FAIL (`make_server`/handler missing).

- [ ] **Step 2: Add `PAGE_HTML`, the handler, `make_server`, and `main`** to `viewer.py`

```python
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
  .card { border:1px solid #333; border-left:3px solid #333; border-radius:4px; margin:6px 0; background:#181818; }
  .card.error { border-color:#a33; border-left-color:#a33; }
  .card.latest { border-left-color:#7ab4e6; }
  .summary { padding:8px 10px; cursor:pointer; display:flex; gap:10px; white-space:nowrap;
             overflow:hidden; text-overflow:ellipsis; align-items:baseline; }
  .summary::before { content:"\25B8"; color:#777; }
  .card.open .summary::before { content:"\25BE"; }
  .summary .ts { color:#8a8a8a; } .summary .model { color:#7ab4e6; }
  .summary .what { color:#ccc; overflow:hidden; text-overflow:ellipsis; }
  .body { display:none; padding:8px 12px; border-top:1px solid #2a2a2a; }
  .card.open .body { display:block; }
  .msg { margin:4px 0; } .role { color:#9bbf9b; } .name { color:#9a9a9a; }
  .reasoning { color:#9a9a9a; font-style:italic; white-space:pre-wrap; }
  .content { color:#e6e6e6; white-space:pre-wrap; }
  .tool { color:#e0a85a; white-space:pre-wrap; }
  .err { color:#ff9090; white-space:pre-wrap; }
  pre.raw { display:none; background:#0c0c0c; color:#9a9; padding:8px; overflow:auto; max-height:300px; }
  .card.raw pre.raw { display:block; }
  .rawbtn { margin-top:6px; background:none; border:1px solid #3a3a55; color:#9aa8e6;
            font:11px ui-monospace,monospace; padding:2px 8px; min-height:24px; cursor:pointer; border-radius:3px; }
  .summary:focus-visible, .rawbtn:focus-visible, #status:focus-visible { outline:2px solid #7ab4e6; outline-offset:1px; }
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
  if (r.tool_calls.length) return 'assistant: tool ' + r.tool_calls.map(c=>c.name).join(', ');
  if (r.reasoning && !r.content) return 'assistant: (reasoning)';
  if (r.content) return 'assistant: ' + r.content.slice(0,80);
  return 'turn';
}

function toggle(card){
  const open = card.classList.toggle('open');
  card.querySelector('.summary').setAttribute('aria-expanded', open ? 'true' : 'false');
}

function render(t){
  const card = document.createElement('div');
  card.className = 'card' + (t.response.error ? ' error' : '');
  const prev = feed.querySelector('.card.latest');
  if (prev) prev.classList.remove('latest');
  card.classList.add('latest');
  const what = summaryText(t);
  let body = '';
  for (const m of t.request_messages){
    let line = '<div class="msg"><span class="role">'+esc(m.role)+'</span>'
      + (m.name?' <span class="name">('+esc(m.name)+')</span>':'') + ' ';
    if (m.content) line += '<span class="content">'+esc(m.content)+'</span>';
    for (const c of m.tool_calls) line += '<span class="tool">'+esc(c.name)+' '+esc(c.arguments)+'</span>';
    line += '</div>';
    body += line;
  }
  const r = t.response;
  if (r.reasoning) body += '<div class="msg reasoning">'+esc(r.reasoning)+'</div>';
  if (r.content) body += '<div class="msg content">'+esc(r.content)+'</div>';
  for (const c of r.tool_calls) body += '<div class="msg tool">'+esc(c.name)+' '+esc(c.arguments)+'</div>';
  if (r.error) body += '<div class="msg err">'+esc(JSON.stringify(r.error))+'</div>';
  body += '<button type="button" class="rawbtn">raw json</button><pre class="raw">'+esc(JSON.stringify(t, null, 2))+'</pre>';

  card.innerHTML = '<div class="summary" role="button" tabindex="0" aria-expanded="false">'
    + '<span class="ts">'+esc(fmtTs(t.timestamp))+'</span>'
    + '<span class="model">'+esc(t.model||'')+'</span><span class="what">'+esc(what)+'</span></div>'
    + '<div class="body">'+body+'</div>';
  const summary = card.querySelector('.summary');
  summary.addEventListener('click', ()=>toggle(card));
  summary.addEventListener('keydown', e=>{ if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(card); }});
  const rawbtn = card.querySelector('.rawbtn');
  rawbtn.addEventListener('click', e=>{ e.stopPropagation(); card.classList.toggle('raw'); });
  feed.appendChild(card);
}

function setStatus(){
  if (following){ statusEl.textContent = 'following live'; statusEl.className = ''; }
  else { statusEl.textContent = '▲ ' + newWhilePaused + ' new — click to follow'; statusEl.className = 'paused'; }
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
      if (following && wasNear) window.scrollTo(0, document.body.scrollHeight);
      else if (!following){ newWhilePaused += data.turns.length; setStatus(); }
    } else if (typeof data.total === 'number') {
      have = data.total;
    }
  } catch (e) { /* try again next tick */ }
}

function follow(){ following = true; newWhilePaused = 0; setStatus(); window.scrollTo(0, document.body.scrollHeight); }

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
                            since = int(part[len("since="):])
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
    print(f"transcript viewer on http://localhost:{VIEWER_PORT} (reading {TRANSCRIPT_DIR}/{TRANSCRIPT_NAME})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the integration test + import check + ruff**

```bash
.venv/bin/python -m pytest tests/test_viewer.py -v
.venv/bin/python -c "import viewer; print('import OK')"
.venv/bin/ruff format viewer.py && .venv/bin/ruff check viewer.py
```
Expected: all tests pass; import OK; ruff clean. `viewer.py` is human-only tooling, but keep it tidy (no stray `#` clutter; the inline HTML/JS is a single string constant, which is fine).

- [ ] **Step 4: Run the full suite**

Run: `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add viewer.py tests/test_viewer.py
git commit -m "feat: viewer HTTP server + live-feed page"
```

---

## Task 3: Viewer image + compose profile

**Files:**
- Create: `Dockerfile.viewer`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Create `Dockerfile.viewer`**

```dockerfile
FROM python:3.13-slim

RUN useradd --create-home --uid 1000 vieweruser

COPY --chown=vieweruser:vieweruser viewer.py /opt/viewer/viewer.py

USER vieweruser
WORKDIR /opt/viewer

ENTRYPOINT ["python", "/opt/viewer/viewer.py"]
```

- [ ] **Step 2: Add the `viewer` service to `docker-compose.yml`.** Under `services:`, add:

```yaml
  viewer:
    build:
      context: .
      dockerfile: Dockerfile.viewer
    image: aurora-viewer
    profiles: ["viewer"]
    environment:
      TRANSCRIPT_DIR: /transcripts
      VIEWER_PORT: "8090"
    volumes:
      - transcripts:/transcripts:ro
    ports:
      - "127.0.0.1:8090:8090"
    read_only: true
    tmpfs:
      - /tmp
    cap_drop: [ALL]
    security_opt: ["no-new-privileges:true"]
    pids_limit: 128
    mem_limit: 256m
    restart: "no"
```

> NOTE: no `networks:` key — the viewer uses Compose's default network, which is separate from `internal` and `egress`, so it shares no network with the agent/recorder/diode. It needs only the read-only `transcripts` mount and the loopback-published port. `profiles: ["viewer"]` keeps it out of a plain `docker compose up`. `restart: "no"` because it's a deliberately ephemeral, on-demand tool.

- [ ] **Step 3: Build the image + validate compose**

```bash
docker build -f Dockerfile.viewer -t aurora-viewer .
OPENROUTER_API_KEY=sk-test docker compose config >/dev/null && echo "compose config OK"
# viewer must NOT start as part of the default (profile-less) set:
OPENROUTER_API_KEY=sk-test docker compose config --services | sort | tr '\n' ' '; echo
OPENROUTER_API_KEY=sk-test docker compose --profile viewer config --services | sort | tr '\n' ' '; echo
```
Expected: image builds; `compose config OK`; the first `--services` list is `agent diode recorder` (no viewer); the second (with `--profile viewer`) additionally includes `viewer`.

- [ ] **Step 4: Smoke-run the viewer image against an empty transcript** (no full stack needed)

```bash
docker run --rm -d --name aurora-viewer-smoke -p 127.0.0.1:8090:8090 aurora-viewer >/dev/null
sleep 2
curl -fsS http://127.0.0.1:8090/ | grep -qi "<!doctype html>" && echo "page OK"
curl -fsS "http://127.0.0.1:8090/api/turns?since=0" | grep -q '"total": 0' && echo "api OK (empty transcript)"
docker rm -f aurora-viewer-smoke >/dev/null
```
Expected: `page OK` and `api OK (empty transcript)` (the container has no `/transcripts` mount here, so the file is absent → empty feed, which is the correct graceful behavior).

- [ ] **Step 5: Commit**

```bash
git add -f Dockerfile.viewer docker-compose.yml
git commit -m "feat: on-demand viewer image + compose profile (read-only, loopback-only)"
```

---

## Final verification

- [ ] `.venv/bin/python -m pytest -q --ignore=tests/test_container_smoke.py` — all green (includes `tests/test_viewer.py`).
- [ ] `.venv/bin/ruff check viewer.py` — clean.
- [ ] `OPENROUTER_API_KEY=sk-test docker compose config --services` does NOT list `viewer`; `--profile viewer` does.
- [ ] `diff agent.py agent_stock.py` — still identical (this feature did not touch the agent).
- [ ] Manual/optional (needs the running stack + a real key): `docker compose up -d`, then `docker compose --profile viewer up viewer`, open `http://localhost:8090`, watch turns stream in.

---

## Self-review against the spec

- **§2 read-only, isolated, loopback-only, no new egress** → Task 3 compose service (`:ro`, `127.0.0.1:8090`, no `internal`/`egress`, default network). ✅
- **§2 ephemeral / no own state** → stateless server reads the volume each request; `restart: "no"`. ✅
- **§2 no secret exposure** → viewer only renders request/response bodies from the transcript (proxy never logs headers); nothing in `load_turns` reads a key. ✅
- **§2 zero deps; own image (viewer.py never in the agent image)** → stdlib only (Task 1–2); `Dockerfile.viewer` copies only `viewer.py` (Task 3). ✅
- **§4.1 `/` HTML + `/api/turns?since=N` JSON; `load_turns` skips malformed; summarized shape; defaults; missing file → empty** → Tasks 1–2 + tests. ✅
- **§4.2 live feed, 2s poll, follow-until-scroll, collapsible cards, reasoning/content/tool_calls, raw-JSON toggle, errors highlighted** → `PAGE_HTML` (Task 2). ✅
  - UX-review refinements folded in (not new scope — they realize the spec's intent): keyboard-operable cards/controls (`role="button"`, `tabindex`, Enter/Space, `aria-expanded`); visible focus ring; `raw json` is a real `<button>` with AA contrast + ≥24px target; spec §4.2's promised `paused — N new` count restored; `waiting for the agent…` empty state; compact `HH:MM:SS` timestamp; latest-turn accent; `aria-live` count + SR-only live region. `PAGE_HTML` is a raw string (`r"""`) so CSS/JS/regex escapes don't trip Python's invalid-escape warning.
- **§4.3 `Dockerfile.viewer`** → Task 3. ✅
- **§4.4 compose `viewer` service under a profile** → Task 3. ✅
- **§5 error handling (missing file, malformed line, poll failure)** → `load_turns` (Task 1) + page `try/catch` (Task 2). ✅
- **§6 unit tests for load_turns + light HTTP integration** → Tasks 1–2. ✅
- **§7 usage** → final verification manual step. ✅
- **§8 out of scope (search/auth/persistence/replay/diode output)** → not built. ✅
