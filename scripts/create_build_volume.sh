#!/bin/sh
# Create the preallocated ext4 image backing the agent's /build volume.
#
# The file is fully allocated up front (never sparse): a full /build cannot
# consume main-disk space beyond this fixed footprint. The filesystem root is
# owned by uid 1000 (the agent user) via mkfs, so no mount-time chown is
# needed. Records the image path as AURORA_BUILD_IMG in .env for compose.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMG="${AURORA_BUILD_IMG:-$REPO_DIR/volumes/build.img}"
SIZE="${AURORA_BUILD_SIZE:-5G}"

if [ -e "$IMG" ]; then
    echo "already exists: $IMG"
else
    mkdir -p "$(dirname "$IMG")"
    fallocate -l "$SIZE" "$IMG"
    mkfs.ext4 -q -F -m 0 -E root_owner=1000:1000 "$IMG"
    echo "created $IMG ($SIZE, preallocated)"
fi

ENV_FILE="$REPO_DIR/.env"
if ! grep -qs '^AURORA_BUILD_IMG=' "$ENV_FILE"; then
    printf 'AURORA_BUILD_IMG=%s\n' "$IMG" >> "$ENV_FILE"
    echo "recorded AURORA_BUILD_IMG in .env"
fi
