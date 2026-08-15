#!/bin/sh
# Build the offline package ecosystems served read-only to the agent at
# /vendor: a Rust local registry (index + .crate files) and a Quicklisp
# bundle (self-contained, loads through its own bundled ASDF setup).
#
# The lockfile is resolved by cargo 1.85 (the agent-side toolchain) so the
# MSRV-aware resolver picks versions the agent's compiler can build. The
# registry is then synced by cargo-local-registry inside rust:latest, which
# the tool needs to compile; the registry it produces has no such need.
set -eu

REPO_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
KITCHEN="$REPO_DIR/vendor/kitchen"
OUT="$REPO_DIR/volumes/vendor"
CACHE="$REPO_DIR/volumes/.vendor-cache"
LISP_SYSTEMS='"alexandria" "serapeum" "bordeaux-threads" "lparallel"
  "cl-ppcre" "iterate" "esrap" "trivia" "closer-mop" "usocket" "yason"
  "jsown" "fset" "local-time" "str" "fiveam" "cffi" "hunchentoot" "slynk"
  "sqlite"'

mkdir -p "$OUT" "$CACHE/cargo-home" "$CACHE/registry-tool"

echo "== rust: resolving kitchen lockfile with cargo 1.85"
docker run --rm -v "$KITCHEN":/kitchen -v "$CACHE/cargo-home":/cargo-home \
    -e CARGO_HOME=/cargo-home -w /kitchen rust:1.85-slim \
    cargo generate-lockfile

echo "== rust: syncing local registry"
docker run --rm -v "$KITCHEN":/kitchen -v "$OUT":/out \
    -v "$CACHE/registry-tool":/cargo-home -e CARGO_HOME=/cargo-home \
    -w /kitchen rust:latest sh -c '
        command -v cargo-local-registry >/dev/null 2>&1 \
            || cargo install -q --locked cargo-local-registry
        export PATH="$CARGO_HOME/bin:$PATH"
        rm -rf /out/registry.tmp
        cargo local-registry --sync Cargo.lock /out/registry.tmp
        rm -rf /out/registry
        mv /out/registry.tmp /out/registry'
echo "   $(ls "$OUT"/registry/*.crate | wc -l) crates"

echo "== lisp: building quicklisp bundle"
docker run --rm -v "$OUT":/out debian:trixie-slim sh -c "
    apt-get update -qq >/dev/null
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
        sbcl curl ca-certificates >/dev/null
    curl -sS -o /tmp/quicklisp.lisp https://beta.quicklisp.org/quicklisp.lisp
    rm -rf /out/lisp.tmp
    sbcl --non-interactive --no-userinit \
        --load /tmp/quicklisp.lisp \
        --eval '(quicklisp-quickstart:install :path \"/tmp/ql/\")' \
        --eval '(ql:bundle-systems (list $LISP_SYSTEMS) :to \"/out/lisp.tmp/\")'
    rm -rf /out/lisp
    mv /out/lisp.tmp /out/lisp"
echo "   bundle: $(ls "$OUT"/lisp | head -4 | tr '\n' ' ')"

echo "== done: $OUT"
du -sh "$OUT"/registry "$OUT"/lisp
