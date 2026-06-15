#!/usr/bin/env bash
# Build + push the GShare-patched HAMi scheduler-extender (thin overlay over the stock HAMi
# image — only the `scheduler` binary changes). Deploy by swapping the vgpu-scheduler-extender
# container image. See build/hami-fork/README.md.
set -euo pipefail
IMG="${IMG:-docker.io/boanlab/hami:v2.9.0-gshare}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PATCH="$ROOT/build/hami-fork/0001-gshare-yield-borrow.patch"

git -C "$ROOT" submodule update --init "$ROOT/third_party/hami"
# deterministic: reset the submodule to its pinned commit, then apply the patch
git -C "$ROOT/third_party/hami" checkout -q . && git -C "$ROOT/third_party/hami" clean -fdq
docker run --rm -v "$ROOT/third_party/hami":/src -v "$PATCH":/patch.patch \
  -w /src golang:1.26 sh -c \
  'apt-get update -qq && apt-get install -y -qq patch >/dev/null && patch -p1 < /patch.patch && go build -o /src/bin/scheduler ./cmd/scheduler'
docker build -f "$ROOT/build/hami-fork/Dockerfile.scheduler" -t "$IMG" "$ROOT/third_party/hami/bin"
docker push "$IMG"
git -C "$ROOT/third_party/hami" checkout -q . && git -C "$ROOT/third_party/hami" clean -fdq
echo "Pushed $IMG"
echo "Deploy: kubectl -n kube-system set image deploy/hami-scheduler vgpu-scheduler-extender=$IMG"
