#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="sk-verify-dummy"

echo "==> build garden export"
.venv/bin/python scripts/build_garden.py >/dev/null 2>&1 || python3 scripts/build_garden.py >/dev/null

echo "==> build"
docker build -q -t aurora-harness . >/dev/null

cleanup() { docker compose down -v >/dev/null 2>&1 || true; }
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

echo "==> agent has NO internet route (must fail/timeout)"
if docker compose exec -T agent python -c "import socket; socket.setdefaulttimeout(5); socket.create_connection(('1.1.1.1',443))" 2>/dev/null; then
  echo "FAIL: agent reached the internet"; exit 1
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

echo "==> diode source is NOT in the agent container"
if docker compose exec -T agent sh -c 'test -f /opt/agent/diode.py'; then
  echo "FAIL: diode.py leaked into the agent image"; exit 1
fi

echo "==> agent can read the garden (read-only)"
docker compose exec -T agent sh -c 'test -f /garden/world.db && test -d /garden/projects'
if docker compose exec -T agent sh -c 'echo x > /garden/_probe' 2>/dev/null; then
  echo "FAIL: garden is writable"; exit 1
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

echo "ALL CONTAINER CHECKS PASSED"
