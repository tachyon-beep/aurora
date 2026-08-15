#!/bin/sh
set -eu
if [ -d /build ]; then
    find /build -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true
fi
cp -r /opt/agent/. /work/
cd /work
exec python watchdog.py
