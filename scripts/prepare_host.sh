#!/bin/sh
# Prepare every ignored host artifact required by the documented build and
# startup path. Safe to rerun: complete vendor assets are retained, the mount
# helpers are idempotent, and the garden is replaced atomically.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
REGISTRY="$REPO_DIR/volumes/vendor/registry"
LISP_BUNDLE="$REPO_DIR/volumes/vendor/lisp/bundle.lisp"
MODEL_WEIGHTS="$REPO_DIR/volumes/vendor/models/potion-base-8m/model.safetensors"
MODEL_TOKENIZER="$REPO_DIR/volumes/vendor/models/potion-base-8m/tokenizer.json"
MODEL_CONFIG="$REPO_DIR/volumes/vendor/models/potion-base-8m/config.json"

vendor_ready() {
    [ -d "$REGISTRY" ] \
        && find "$REGISTRY" -maxdepth 1 -type f -name '*.crate' -print -quit | grep -q . \
        && [ -s "$LISP_BUNDLE" ] \
        && [ -s "$MODEL_WEIGHTS" ] \
        && [ -s "$MODEL_TOKENIZER" ] \
        && [ -s "$MODEL_CONFIG" ]
}

sh "$REPO_DIR/scripts/create_build_volume.sh"
sh "$REPO_DIR/scripts/create_sense_volume.sh"

if vendor_ready; then
    echo "vendor assets ready: $REPO_DIR/volumes/vendor"
else
    sh "$REPO_DIR/scripts/build_vendor.sh"
fi

if [ -x "$REPO_DIR/.venv/bin/python" ]; then
    "$REPO_DIR/.venv/bin/python" "$REPO_DIR/scripts/build_garden.py"
else
    python3 "$REPO_DIR/scripts/build_garden.py"
fi
