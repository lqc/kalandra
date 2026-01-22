#!/bin/bash

# This script is used to test the Docker image entrypoint. It verifies that the image can run
# and perform basic operations without errors.
set -e

UPSTREAM_BARE="/tmp/upstream.git"
MIRROR_BARE="/tmp/mirror.git"

WORKDIR="/tmp/workdir"
mkdir -p "$WORKDIR" "$UPSTREAM_BARE" "$MIRROR_BARE"
git init -b main --bare "$UPSTREAM_BARE" >&2
git init -b main --bare "$MIRROR_BARE" >&2

git init -b main "$WORKDIR" >&2
git -C "$WORKDIR" config user.email "test@example.com" >&2
git -C "$WORKDIR" config user.name "Test" >&2

echo "hello" > "$WORKDIR/README.md" >&2

git -C "$WORKDIR" add "README.md" >&2
git -C "$WORKDIR" commit -m "initial" >&2

git -C "$WORKDIR" remote add origin "$UPSTREAM_BARE" >&2
git -C "$WORKDIR" push origin main >&2
git -C "$WORKDIR" tag v0 >&2
git -C "$WORKDIR" push origin v0 >&2

kalandra --source "file://$UPSTREAM_BARE" --target "file://$MIRROR_BARE" >&2
echo "SUCCESS"
