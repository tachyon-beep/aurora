#!/bin/sh
set -eu
if [ -d /build ]; then
    if [ -d /vendor ] && [ "$(stat -c %d /build)" = "$(stat -c %d /vendor)" ]; then
        echo "warning: /build shares a filesystem with the host; its size boundary is absent" >&2
    fi
    find /build -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
fi
cp -r /opt/agent/. /work/
cd /work
exec python watchdog.py
