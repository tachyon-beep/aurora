#!/bin/sh
# Create the host directory backing the sense volume with the ownership the
# sense service needs. The service runs as uid 1000 with cap_drop [ALL]; a
# missing bind source would be auto-created by Docker as root-owned, leaving
# the service unable to write and the agent's /sense permanently empty.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DIR="${AURORA_SENSE_DIR:-$REPO_DIR/volumes/sense}"

mkdir -p "$DIR"
if [ "$(id -u)" -eq 0 ]; then
    chown 1000:1000 "$DIR"
elif [ "$(id -u)" -ne 1000 ]; then
    echo "warning: $DIR is owned by uid $(id -u); the sense service writes as uid 1000" >&2
fi
echo "sense volume directory ready: $DIR"
