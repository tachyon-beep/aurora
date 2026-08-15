#!/bin/sh
# Create the host directory backing the sense volume with the ownership the
# sense service needs. The service runs as uid 1000 with cap_drop [ALL]; a
# missing bind source would be auto-created by Docker as root-owned, leaving
# the service unable to write and the agent's /sense permanently empty. The
# check reads the directory's owner rather than the caller's uid, so an
# existing directory owned by anyone else is corrected when this runs as root
# and reported with the remediation command otherwise.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DIR="${AURORA_SENSE_DIR:-$REPO_DIR/volumes/sense}"

mkdir -p "$DIR"
OWNER=$(stat -c %u "$DIR")
if [ "$OWNER" -ne 1000 ]; then
    if [ "$(id -u)" -eq 0 ]; then
        chown 1000:1000 "$DIR"
    else
        echo "owned by uid $OWNER; the sense service writes as uid 1000. run:" >&2
        echo "  sudo chown 1000:1000 $DIR" >&2
        exit 1
    fi
fi
echo "sense volume directory ready: $DIR"
