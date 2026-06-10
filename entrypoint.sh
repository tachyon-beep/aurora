#!/bin/sh
set -eu
cp -r /opt/agent/. /work/
cd /work
exec python watchdog.py
