#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="sk-verify-dummy"
export LLM_BASE_URL="http://127.0.0.1:1"
export LLM_API_KEY=""
AURORA_VERIFY_PROJECT="aurora_verify_$$"
export COMPOSE_PROJECT_NAME="$AURORA_VERIFY_PROJECT"

echo "==> build garden export"
.venv/bin/python scripts/build_garden.py >/dev/null 2>&1 || python3 scripts/build_garden.py >/dev/null

echo "==> build"
docker build -q -t aurora-harness . >/dev/null

cleanup() {
  docker compose down --remove-orphans >/dev/null 2>&1 || true
  docker volume rm \
    "${COMPOSE_PROJECT_NAME}_state" \
    "${COMPOSE_PROJECT_NAME}_diode" \
    "${COMPOSE_PROJECT_NAME}_transcripts" \
    "${COMPOSE_PROJECT_NAME}_telemetry" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "==> up"
docker compose up -d --build >/dev/null
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

echo "==> agent CAN reach the recorder"
docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('recorder',8088))"

echo "==> tier-1 recovery: corrupt agent.py, watchdog restores from baseline"
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep 12
docker compose exec -T agent python -c "import ast; ast.parse(open('/work/agent.py').read()); print('recovered')" | grep -qx recovered

echo "==> tier-2 recovery: two crashes in window trigger git reset --hard"
# durable edit to a TRACKED file (proxy.py) simulating the agent reshaping its env
docker compose exec -T agent sh -c 'printf "\nAGENT_EDIT_MARKER\n" >> /work/proxy.py'
# first crash -> tier 1 (restores agent.py only; the proxy.py marker survives)
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep 10
# second crash within the window -> tier 2 (git reset --hard baseline reverts ALL tracked files)
docker compose exec -T agent sh -c 'printf "def (:\n" > /work/agent.py'
sleep 16
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
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST http://127.0.0.1:8091/api/stream || true)
[ "$code" = "405" ] || { echo "FAIL: stream port accepted POST ($code)"; exit 1; }

echo "==> console fails closed without a token"
code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8092/api/roots || true)
[ "$code" = "401" ] || [ "$code" = "403" ] || { echo "FAIL: console served without token ($code)"; exit 1; }

echo "ALL CONTAINER CHECKS PASSED"
