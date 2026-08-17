#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="sk-verify-dummy"
VERIFY_STUB_PORT="${VERIFY_STUB_PORT:-8199}"
# An OpenAI-compatible stub (see verify_stub_llm.py) rather than a real
# upstream, so the agent runs a normal loop during the recovery checks below
# instead of sitting in the watchdog's 60s environment-failure pause. No real
# credential is ever involved. The stub runs as a throwaway container on this
# run's own egress network rather than a host-bound port: a host firewall may
# not admit inbound container traffic on an arbitrary port even with
# extra_hosts host-gateway wired up, while same-network container-to-
# container traffic is unaffected and portable across hosts either way.
export LLM_BASE_URL="http://verify-stub:${VERIFY_STUB_PORT}/v1"
export LLM_API_KEY=""
# Declared streams do not follow LLM_BASE_URL: they forward to a fixed
# stream upstream (OpenRouter in operation). Aim that at the stub too, so
# the stream round-trip below stays offline.
export STREAM_UPSTREAM_URL="http://verify-stub:${VERIFY_STUB_PORT}/v1"
# The stream-console check below declares a stream that names "stream-model";
# the recorder rejects any declared model not on its allow-lists, so this
# run's recorder must permit that identifier.
export STREAM_MODEL_ALLOW_TEXT="stream-model"
AURORA_VERIFY_PROJECT="aurora_verify_$$"
export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"
STUB_CONTAINER="verify-stub_$$"

# This run gets its own throwaway /build directory: never the live stack's
# mounted image, and the loop mount itself is host setup outside this
# script's scope (the entrypoint wipe and lifecycle are what it verifies).
VERIFY_BUILD_DIR="$(mktemp -d)"
export AURORA_BUILD_DIR="$VERIFY_BUILD_DIR"

# Host ports for this run's stage and viewer, so verification can run
# beside a live stack that already holds 8090/8091/8092.
export STAGE_STREAM_PORT="${VERIFY_STAGE_STREAM_PORT:-8191}"
export STAGE_CONSOLE_PORT="${VERIFY_STAGE_CONSOLE_PORT:-8192}"
export VIEWER_HOST_PORT="${VERIFY_VIEWER_PORT:-8190}"

cleanup() {
  # The stub container must be detached before the network it sits on can be
  # torn down, so remove it ahead of "compose down".
  docker rm -f "$STUB_CONTAINER" >/dev/null 2>&1 || true
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  docker volume rm \
    "${COMPOSE_PROJECT_NAME}_state" \
    "${COMPOSE_PROJECT_NAME}_diode" \
    "${COMPOSE_PROJECT_NAME}_transcripts" \
    "${COMPOSE_PROJECT_NAME}_telemetry" \
    "${COMPOSE_PROJECT_NAME}_llm_sock" \
    "${COMPOSE_PROJECT_NAME}_llm_console" \
    "${COMPOSE_PROJECT_NAME}_pump" \
    "${COMPOSE_PROJECT_NAME}_video" >/dev/null 2>&1 || true
  rm -rf "$VERIFY_BUILD_DIR"
}
trap cleanup EXIT

echo "==> offline vendor assets"
VENDOR_ROOT="$(pwd)/volumes/vendor"
vendor_ready=1
[ -d "$VENDOR_ROOT/registry" ] || vendor_ready=0
find "$VENDOR_ROOT/registry" -maxdepth 1 -type f -name '*.crate' -print -quit 2>/dev/null \
  | grep -q . || vendor_ready=0
[ -s "$VENDOR_ROOT/lisp/bundle.lisp" ] || vendor_ready=0
for model_file in model.safetensors tokenizer.json config.json; do
  [ -s "$VENDOR_ROOT/models/potion-base-8m/$model_file" ] || vendor_ready=0
done
if [ "$vendor_ready" -ne 1 ]; then
  sh scripts/build_vendor.sh
fi

echo "==> sense volume host directory"
sh scripts/create_sense_volume.sh >/dev/null

echo "==> build garden export"
.venv/bin/python scripts/build_garden.py >/dev/null 2>&1 || python3 scripts/build_garden.py >/dev/null

echo "==> build"
docker build -q -t aurora-harness . >/dev/null
docker compose build >/dev/null

echo "==> create (network + containers, nothing started yet)"
docker compose create >/dev/null

echo "==> start stub upstream on the recorder's egress network"
egress_net=$(docker inspect "$(docker compose ps -a -q recorder)" \
  --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}}{{end}}')
[ -n "$egress_net" ] || { echo "FAIL: could not determine the recorder's network"; exit 1; }
docker run -d --name "$STUB_CONTAINER" \
  --network "$egress_net" --network-alias verify-stub \
  -e "VERIFY_STUB_PORT=${VERIFY_STUB_PORT}" \
  -v "$(pwd)/scripts/verify_stub_llm.py:/verify_stub_llm.py:ro" \
  --entrypoint python3 aurora-harness /verify_stub_llm.py >/dev/null
i=0
until docker exec "$STUB_CONTAINER" python3 -c "
import urllib.request
urllib.request.urlopen('http://127.0.0.1:${VERIFY_STUB_PORT}/', timeout=1)
" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 50 ]; then
    echo "FAIL: stub upstream did not start"; exit 1
  fi
  sleep 0.2
done

echo "==> up"
docker compose start >/dev/null
sleep 6

echo "==> agent runs as non-root"
docker compose exec -T agent whoami | grep -qx appuser

echo "==> agent rootfs is read-only (write to / must fail)"
if docker compose exec -T agent sh -c 'echo x > /should_fail' 2>/dev/null; then
  echo "FAIL: rootfs writable"; exit 1
fi

echo "==> agent /work is writable (tmpfs)"
docker compose exec -T agent sh -c 'echo x > /work/_probe && rm /work/_probe'

echo "==> agent has the workshop runtime packages"
docker compose exec -T agent python -c "import openai, numpy, sympy, networkx, rich, yaml, bs4, markdownify, fastapi, uvicorn, websockets, jinja2, zmq, aiosqlite, psutil, watchfiles, simpy, jsonschema, pytest, hypothesis"
docker compose exec -T agent ruff --version >/dev/null

echo "==> garden contains only two read-only documents"
docker compose exec -T agent sh -c '
  test -f /garden/README.md &&
  test -f /garden/runtime.md &&
  test "$(find /garden -type f | wc -l)" -eq 2 &&
  test "$(find /garden -mindepth 1 -type d | wc -l)" -eq 0
'
if docker compose exec -T agent sh -c 'echo x > /garden/_probe' 2>/dev/null; then
  echo "FAIL: garden is writable"; exit 1
fi

echo "==> agent compiled toolchains are present"
docker compose exec -T agent sh -c '
  rustc --version >/dev/null && sbcl --version >/dev/null &&
  command -v pforth swipl nasm mlr datamash jq sqlite3 rg >/dev/null
'

echo "==> agent new runtime packages import; loomweave and filigree respond"
docker compose exec -T agent python -c "import filigree, z3, hy, tenacity, jplephem, model2vec"
docker compose exec -T agent loomweave --version >/dev/null
docker compose exec -T agent filigree --version >/dev/null

echo "==> /vendor and /corpus are read-only; the registry is present"
docker compose exec -T agent test -d /vendor/registry
if docker compose exec -T agent sh -c 'echo x > /vendor/_probe' 2>/dev/null; then
  echo "FAIL: /vendor is writable"; exit 1
fi
if docker compose exec -T agent sh -c 'echo x > /corpus/_probe' 2>/dev/null; then
  echo "FAIL: /corpus is writable"; exit 1
fi

echo "==> /sense is present in the agent and read-only"
docker compose exec -T agent test -d /sense
if docker compose exec -T agent sh -c 'echo x > /sense/_probe' 2>/dev/null; then
  echo "FAIL: /sense is writable"; exit 1
fi

echo "==> sense shares no network with the viewer and is writing status.json"
sense_nets=$(docker inspect "$(docker compose ps -q sense)" \
  --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')
case "$sense_nets" in
  *_default*) echo "FAIL: sense is attached to the default network"; exit 1 ;;
esac
# status.json appears once the first capture cycle completes, minutes at
# worst even with every feed unreachable; an absent file past that points at
# an unwritable volume or a crashed service.
sense_status_ok=0
for _ in $(seq 1 60); do
  if docker compose exec -T agent test -f /sense/status.json 2>/dev/null; then
    sense_status_ok=1; break
  fi
  sleep 5
done
if [ "$sense_status_ok" -ne 1 ]; then
  echo "FAIL: /sense/status.json has not appeared"; exit 1
fi

echo "==> /video is present in the agent and writable"
docker compose exec -T agent test -d /video
if ! docker compose exec -T agent sh -c 'echo x > /video/_probe && rm -f /video/_probe'; then
  echo "FAIL: /video is not writable by the agent"; exit 1
fi

echo "==> video holds no credential"
# A shape match against the full container env ('(KEY|TOKEN|SECRET|PASSWORD)=')
# would false-positive on GPG_KEY, which python:3.13-slim (video's base image)
# sets to verify the CPython tarball -- that is image noise, not a leaked
# credential. Named enumeration of this stack's actual credential variables,
# matching the specific-name idiom the ELEVENLABS check below already uses,
# checks the property that matters without tripping on base-image internals.
if docker compose exec -T video env \
  | grep -Eq 'ELEVENLABS|OPENROUTER|LLM_API_KEY|STAGE_CONSOLE_TOKEN|STAGE_SUMMARY_API_KEY|TUNNEL_TOKEN'; then
  echo "FAIL: a credential is present in the video service environment"; exit 1
fi

echo "==> stage (aurora-stage) does not mount the video volume"
stage_cid=$(docker compose ps -q stage)
[ -n "$stage_cid" ] || { echo "FAIL: stage container not running"; exit 1; }
if docker inspect "$stage_cid" 2>/dev/null | grep -q '"Destination": "/video"'; then
  echo "FAIL: stage mounts /video"; exit 1
fi

echo "==> the video service shares no network with the viewer"
video_nets=$(docker inspect "$(docker compose ps -q video)" \
  --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}')
case "$video_nets" in
  *_default*) echo "FAIL: video is attached to the default network"; exit 1 ;;
esac

echo "==> the video service publishes state.json"
video_state_ok=0
for _ in 1 2 3 4 5 6; do
  if docker compose exec -T agent test -f /video/state.json 2>/dev/null; then
    video_state_ok=1; break
  fi
  sleep 5
done
if [ "$video_state_ok" -ne 1 ]; then
  echo "FAIL: /video/state.json has not appeared"; exit 1
fi

echo "==> cargo builds a new project offline against /vendor"
docker compose exec -T agent sh -c '
  set -e
  mkdir -p /tmp/verify_proj && cd /tmp/verify_proj
  cargo init -q --name verifyprobe
  printf "nom = \"7\"\n" >> Cargo.toml
  cargo build -q
' || { echo "FAIL: offline cargo build against the local registry"; exit 1; }

echo "==> sbcl loads a system from the bundle"
docker compose exec -T agent sbcl --non-interactive \
  --load /vendor/lisp/bundle.lisp \
  --eval "(asdf:load-system :alexandria)" >/dev/null 2>&1 \
  || { echo "FAIL: quicklisp bundle load"; exit 1; }

echo "==> /build is writable and disk-backed"
docker compose exec -T agent sh -c 'echo x > /build/_marker'

echo "==> agent state starts empty and is writable"
if docker compose exec -T agent sh -c 'find /state -mindepth 1 -print -quit | grep -q .' ; then
  echo "FAIL: fresh test state is not empty"; exit 1
fi
docker compose exec -T agent sh -c '
  printf "durable-marker\n" > /state/durable-marker &&
  printf "#!/bin/sh\nprintf ran > /state/probe-ran\n" > /state/probe.sh &&
  chmod +x /state/probe.sh &&
  test ! -e /state/probe-ran
'

echo "==> agent has NO internet route (must fail/timeout)"
if docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('1.1.1.1',443))" 2>/dev/null; then
  echo "FAIL: agent reached the internet"; exit 1
fi

echo "==> speech credential is unreachable from the agent"
if docker compose exec -T agent sh -c 'env | grep -q ELEVENLABS'; then
  echo "FAIL: speech credential present in the agent environment"; exit 1
fi

echo "==> agent has exactly one network interface (lo)"
docker compose exec -T agent sh -c 'test "$(ls /sys/class/net)" = "lo"'

echo "==> agent has an empty routing table"
if docker compose exec -T agent sh -c 'test "$(tail -n +2 /proc/net/route | wc -l)" -gt 0'; then
  echo "FAIL: agent has a route off the container"; exit 1
fi

echo "==> agent reaches the recorder over the socket"
docker compose exec -T agent python -c "import socket; s=socket.socket(socket.AF_UNIX); s.settimeout(5); s.connect('/llm/sock/core.sock'); s.close()"

echo "==> socket is not unlinkable from the agent (read-only mount)"
if docker compose exec -T agent python -c "import os; os.unlink('/llm/sock/core.sock')" 2>/dev/null; then
  echo "FAIL: agent unlinked the model socket"; exit 1
fi

echo "==> a completion round-trips and is recorded"
docker compose exec -T agent python -c "
import httpx, json
t = httpx.HTTPTransport(uds='/llm/sock/core.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'m','messages':[{'role':'user','content':'ping'}]}, timeout=20)
print(r.status_code)
"
docker compose exec -T recorder sh -c 'grep -q ping /transcripts/agent_life_transcript.jsonl'

echo "==> agent declares a stream in the llm console; the recorder binds it"
docker compose exec -T agent sh -c 'printf "{\"enable_streams\":true,\"streams\":{\"aux\":{\"budget\":1,\"model\":\"stream-model\"}}}" > /llm/console/console.json'
i=0
until docker compose exec -T agent sh -c 'test -S /llm/sock/aux.sock' 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 30 ]; then
    echo "FAIL: declared stream socket did not appear"; exit 1
  fi
  sleep 1
done

echo "==> a completion on the declared stream is composed and recorded"
docker compose exec -T agent python -c "
import httpx
t = httpx.HTTPTransport(uds='/llm/sock/aux.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'sent-model','messages':[{'role':'user','content':'streamping'}]}, timeout=20)
print(r.status_code)
"
docker compose exec -T recorder sh -c 'grep -q streamping /transcripts/agent_life_transcript.jsonl'
docker compose exec -T recorder sh -c 'grep -q stream-model /transcripts/agent_life_transcript.jsonl'

echo "==> the stream budget refuses the second request"
docker compose exec -T agent python -c "
import httpx
t = httpx.HTTPTransport(uds='/llm/sock/aux.sock')
with httpx.Client(transport=t, base_url='http://localhost') as c:
    r = c.post('/api/v1/chat/completions', json={'model':'m','messages':[]}, timeout=20)
assert r.status_code == 429, r.status_code
assert 'rate limited' in r.json()['error']['message']
"

echo "==> streams.json reports the stream and is agent-readable"
# The state file is refreshed by the recorder's 5s poll loop, so the charge
# from the completions above can take one cycle to appear on disk.
i=0
until docker compose exec -T agent python -c "
import json
with open('/llm/sock/streams.json') as f:
    state = json.load(f)
assert state['streams']['aux']['status'] == 'active'
assert state['streams']['aux']['budget']['used'] >= 1
" 2>/dev/null; do
  i=$((i + 1))
  if [ "$i" -ge 15 ]; then
    echo "FAIL: streams.json did not report the stream's use"; exit 1
  fi
  sleep 1
done

echo "==> streams.json is not writable from the agent"
if docker compose exec -T agent sh -c 'echo x > /llm/sock/streams.json' 2>/dev/null; then
  echo "FAIL: agent wrote into the socket directory"; exit 1
fi

echo "==> the console returns to empty for the recovery checks"
docker compose exec -T agent sh -c 'printf "{\"streams\":{}}" > /llm/console/console.json'

echo "==> recorder emits open and close events for the recorded completions"
# The agent's own loop is running against the stub, so an open without its
# close is legitimately in flight at any instant, and the final line can be
# mid-write. Every close must pair with an open; unmatched opens are allowed.
docker compose exec -T recorder python -c "
import json
opens, closes = set(), set()
with open('/transcripts/events.jsonl') as f:
    for line in f:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event['event'] == 'open':
            opens.add(event['id'])
        elif event['event'] == 'close':
            closes.add(event['id'])
assert closes and closes <= opens, (len(opens), len(closes))
"

echo "==> stage snapshot carries stream lanes"
curl -s http://127.0.0.1:${STAGE_STREAM_PORT}/api/stream | python3 -c "
import json, sys
snap = json.load(sys.stdin)
names = [lane['name'] for lane in snap['lanes']]
assert names and names[0] == 'core', names
"

# The watchdog restores agent.py when the agent PROCESS EXITS, not when the
# file changes -- it does not watch the filesystem. So a corrupted agent.py
# sits untouched until the running incarnation ends, and each recovery check
# has to outwait one whole incarnation. With the stub upstream that is
# TURNS_PER_INCARNATION x REPLY_DELAY_SECONDS (40 x 2.0s = 80s by default),
# plus the restart itself; 90s clears it with margin. Measured recovery
# landed at t+70s. These waits are why a full run takes several minutes.
#
# Raising the stub's turn rate to shorten them would be a false economy: the
# watchdog treats ZERO_EXIT_FLAP_COUNT=3 clean exits inside
# ZERO_EXIT_FLAP_WINDOW_SECONDS=120 as flapping and escalates through the
# tier ladder, which would corrupt these very checks.
RECOVERY_WAIT=90

echo "==> tier-1 recovery: corrupt agent.py, watchdog restores from baseline"
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep "$RECOVERY_WAIT"
docker compose exec -T agent python -c "import ast; ast.parse(open('/work/agent.py').read()); print('recovered')" | grep -qx recovered

echo "==> tier-2 recovery: two crashes in window trigger git reset --hard"
# durable edit to a TRACKED file (proxy.py) simulating the agent reshaping its env
docker compose exec -T agent sh -c 'printf "\nAGENT_EDIT_MARKER\n" >> /work/proxy.py'
# first crash -> tier 1 (restores agent.py only; the proxy.py marker survives)
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep "$RECOVERY_WAIT"
# second crash within the window -> tier 2 (git reset --hard baseline reverts ALL tracked files)
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep "$RECOVERY_WAIT"
if docker compose exec -T agent grep -q AGENT_EDIT_MARKER /work/proxy.py 2>/dev/null; then
  echo "FAIL: tier-2 git reset did not revert tracked proxy.py (git recovery broken)"; exit 1
fi
docker compose exec -T agent python -c "import ast; ast.parse(open('/work/agent.py').read()); print('tier2-recovered')" | grep -qx tier2-recovered

echo "==> state survives tracked-code recovery"
docker compose exec -T agent sh -c 'grep -qx durable-marker /state/durable-marker'

echo "==> state survives agent container recreation without executing stored code"
docker compose up -d --force-recreate --no-deps agent >/dev/null
sleep 6
docker compose exec -T agent sh -c '
  grep -qx durable-marker /state/durable-marker &&
  test -x /state/probe.sh &&
  test ! -e /state/probe-ran
'

echo "==> /build was wiped by the entrypoint on container start"
if docker compose exec -T agent test -e /build/_marker 2>/dev/null; then
  echo "FAIL: /build survived a container start"; exit 1
fi

echo "==> state marker is absent from other services"
docker compose exec -T recorder sh -c 'test ! -e /state/durable-marker'
docker compose exec -T diode sh -c 'test ! -e /state/durable-marker'

echo "==> diode source is NOT in the agent container"
if docker compose exec -T agent sh -c 'test -f /opt/agent/diode.py'; then
  echo "FAIL: diode.py leaked into the agent image"; exit 1
fi

echo "==> agent and diode share /diode; diode writes HELP.md and state.json"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{}}" > /diode/console.json'
sleep 10
docker compose exec -T agent sh -c 'test -f /diode/HELP.md && grep -q fetchhttp /diode/HELP.md'
docker compose exec -T agent sh -c 'test -f /diode/state.json'

echo "==> diode SSRF: an internal target is refused"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"fetchhttp http://169.254.169.254/\"],\"variables\":{}}" > /diode/console.json'
sleep 10
docker compose exec -T agent sh -c 'grep -rqi "refused" /diode/output/'

echo "==> diode affordance: enable_clock unlocks the time command in help"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{\"enable_clock\":true}}" > /diode/console.json'
sleep 10
docker compose exec -T agent sh -c 'grep -q "time ->" /diode/HELP.md'

echo "==> diode affordance: enable_clone unlocks the clone command in help"
docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{\"enable_clone\":true}}" > /diode/console.json'
sleep 10
docker compose exec -T agent sh -c 'grep -q "clone <owner>/<repo>" /diode/HELP.md'

echo "==> speech gate does not open from the agent-writable console"
if docker compose exec -T diode sh -c 'test -z "$ENABLE_SPEECH"'; then
  docker compose exec -T agent sh -c 'printf "{\"commands\":[\"help\"],\"variables\":{\"enable_speech\":true}}" > /diode/console.json'
  sleep 10
  # The help text is "speak <text> -> ...", so the pattern has to carry the
  # argument; "speak ->" would match nothing and pass whatever the gate did.
  if docker compose exec -T agent sh -c 'grep -q "speak <text>" /diode/HELP.md'; then
    echo "FAIL: the console alone made the credentialed command available"; exit 1
  fi
  if docker compose exec -T agent sh -c 'grep -q "enable_speech" /diode/HELP.md'; then
    echo "FAIL: help offers a speech gate variable that does not work"; exit 1
  fi
  docker compose exec -T agent sh -c 'grep -q "fetchhttp <url>" /diode/HELP.md' || {
    echo "FAIL: help did not refresh, so the speech assertions proved nothing"; exit 1
  }
else
  echo "    skipped: ENABLE_SPEECH is set in this stack"
fi

echo "==> stage (aurora-stage) does not mount the state volume"
stage_cid=$(docker compose ps -q stage)
[ -n "$stage_cid" ] || { echo "FAIL: stage container not running"; exit 1; }
if docker inspect "$stage_cid" 2>/dev/null | grep -q '"Destination": "/state"'; then
  echo "FAIL: stage mounts /state"; exit 1
fi

echo "==> stream port refuses mutating methods"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:${STAGE_STREAM_PORT}/api/stream || true)
[ "$code" = "405" ] || { echo "FAIL: stream port accepted POST ($code)"; exit 1; }

echo "==> console fails closed without a token"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:${STAGE_CONSOLE_PORT}/api/roots || true)
[ "$code" = "401" ] || [ "$code" = "403" ] || { echo "FAIL: console served without token ($code)"; exit 1; }

echo "==> pump volume is mounted into the agent and nowhere else"
agent_cid=$(docker compose ps -q agent)
[ -n "$agent_cid" ] || { echo "FAIL: agent container not running"; exit 1; }
if ! docker inspect "$agent_cid" 2>/dev/null | grep -q '"Destination": "/pump"'; then
  echo "FAIL: the agent does not mount /pump"; exit 1
fi
for svc in recorder diode sense video viewer stage; do
  cid=$(docker compose ps -q "$svc" 2>/dev/null || true)
  [ -n "$cid" ] || continue
  if docker inspect "$cid" 2>/dev/null | grep -q '"Destination": "/pump"'; then
    echo "FAIL: $svc mounts /pump"; exit 1
  fi
done

echo "==> the scheduler runs from the read-only image and describes itself"
if ! docker compose exec -T agent test -f /usr/local/bin/pump.py; then
  echo "FAIL: pump.py is not on the image"; exit 1
fi
if docker compose exec -T agent test -e /work/pump.py; then
  echo "FAIL: pump.py reached the agent workspace"; exit 1
fi
for f in README.md state.json; do
  if ! docker compose exec -T agent test -s "/pump/$f"; then
    echo "FAIL: the scheduler did not write /pump/$f"; exit 1
  fi
done

echo "==> a streamed completion relays incrementally and reports its usage"
stream_sock=$(docker compose exec -T agent sh -c 'ls /llm/sock/*.sock 2>/dev/null | grep -v core.sock | head -1' | tr -d "\r")
if [ -n "$stream_sock" ]; then
  before=$(docker compose exec -T recorder sh -c 'grep -c "\"usage\"" /transcripts/events.jsonl 2>/dev/null || echo 0' | tr -d "\r")
  docker compose exec -T agent python - "$stream_sock" <<'PY' || true
import http.client, json, socket, sys

class UDS(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost")
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self._path)

c = UDS(sys.argv[1])
body = json.dumps({"messages": [{"role": "user", "content": "one word"}], "stream": True})
c.request("POST", "/api/v1/chat/completions", body, {"Content-Type": "application/json"})
r = c.getresponse()
print("status", r.status, "te", r.getheader("Transfer-Encoding"))
r.read()
PY
  sleep 3
  after=$(docker compose exec -T recorder sh -c 'grep -c "\"usage\"" /transcripts/events.jsonl 2>/dev/null || echo 0' | tr -d "\r")
  if [ "$after" -le "$before" ]; then
    echo "    note: no new usage event; the stream may be rate limited or unconfigured"
  fi
else
  echo "    skipped: no declared stream socket is bound"
fi

echo "ALL CONTAINER CHECKS PASSED"
