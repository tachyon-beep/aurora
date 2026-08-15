#!/bin/sh
# Create and mount the preallocated ext4 image backing the agent's /build.
#
# The file is fully allocated up front (never sparse): a full /build cannot
# consume main-disk space beyond this fixed footprint. mkfs is given nodiscard
# so formatting does not release the blocks fallocate reserved, which would
# make the image sparse again. The filesystem root is
# owned by uid 1000 (the agent user) via mkfs. The image is loop-mounted on
# the host at volumes/build and compose bind-mounts that directory; docker's
# local volume driver cannot loop-mount an image file directly (its o=loop
# is passed verbatim to the mount syscall and rejected).
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
IMG="${AURORA_BUILD_IMG:-$REPO_DIR/volumes/build.img}"
MNT="${AURORA_BUILD_DIR:-$REPO_DIR/volumes/build}"
SIZE="${AURORA_BUILD_SIZE:-5G}"

if [ -e "$IMG" ]; then
    echo "image exists: $IMG"
else
    mkdir -p "$(dirname "$IMG")"
    fallocate -l "$SIZE" "$IMG"
    mkfs.ext4 -q -F -m 0 -E root_owner=1000:1000,nodiscard "$IMG"
    echo "created $IMG ($SIZE, preallocated)"
fi

mkdir -p "$MNT"
if mountpoint -q "$MNT"; then
    echo "mounted: $MNT"
elif sudo -n mount -o loop "$IMG" "$MNT" 2>/dev/null; then
    echo "mounted $IMG at $MNT"
else
    echo "not mounted. run:"
    echo "  sudo mount -o loop $IMG $MNT"
    echo "for persistence across reboots add to /etc/fstab:"
    echo "  $IMG $MNT ext4 loop 0 2"
    exit 1
fi
