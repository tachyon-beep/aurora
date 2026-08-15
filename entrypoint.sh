#!/bin/sh
set -eu
if [ -d /build ]; then
    if [ -d /vendor ] && [ "$(stat -c %d /build)" = "$(stat -c %d /vendor)" ]; then
        echo "warning: /build shares a filesystem with the host; its size boundary is absent" >&2
    fi
    chmod -R u+rwX /build 2>/dev/null || true
    find /build -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
fi
[ -e /llm/console/console.json ] || cp /usr/local/share/aurora/llm_console_seed.json /llm/console/console.json
cp -r /opt/agent/. /work/
cd /work
exec python watchdog.py
