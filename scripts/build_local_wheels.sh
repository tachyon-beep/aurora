#!/bin/sh
# Build wheels for filigree and loomweave from the operator's local source
# trees into vendor/wheels/, where the agent Dockerfile installs them ahead
# of requirements-agent.txt. Local trees carry fixes not yet on PyPI.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$REPO_DIR/vendor/wheels"
FILIGREE_SRC="${FILIGREE_SRC:-$HOME/filigree}"
LOOMWEAVE_SRC="${LOOMWEAVE_SRC:-$HOME/loomweave}"

rm -rf "$OUT"
mkdir -p "$OUT"
pip wheel -q --no-deps -w "$OUT" "$FILIGREE_SRC"
pip wheel -q --no-deps -w "$OUT" "$LOOMWEAVE_SRC/plugins/python"
pip wheel -q --no-deps -w "$OUT" "$LOOMWEAVE_SRC/packaging/rust-plugin-dist"
pip wheel -q --no-deps -w "$OUT" "$LOOMWEAVE_SRC/crates/loomweave-cli"
ls -l "$OUT"
