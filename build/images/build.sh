#!/usr/bin/env bash
set -euo pipefail
# Build the catalogue session images, and optionally push them.
#   ./build.sh             # build all four images
#   REG=myreg ./build.sh   # registry namespace (default: boanlab)
#   ./build.sh push        # build (using the cache) then push to Docker Hub; run docker login first
#   ./build.sh push-only   # skip the build and push what already exists
#
# Each image starts from an official ML image, which guarantees a matching CUDA and cuDNN pair, and
# adds the three connection tools (code-server, JupyterLab, ttyd) plus the entrypoint. The images are
# independent of one another. The build context is this directory, so session-base/entrypoint.sh can
# be shared.
cd "$(dirname "$0")"                       # build/images
REG="${REG:-boanlab}"
REPO="${REPO:-$REG/gshare-session}"
AGENT="${AGENT:-$REG/gshare-lossless-agent:latest}"   # the lossless-pause node agent (CRIU + cuda-checkpoint)
MODE="${1:-build}"

TAGS=(ml-ubuntu24.04 ml-cuda12.4-cudnn9 pytorch2.6-cuda12.4-cudnn9 tensorflow2.18-cuda12.5-cudnn9)

build(){ local tag="$1"; shift; echo "── build: $tag"; docker build -t "$tag" "$@"; }

if [ "$MODE" != "push-only" ]; then
  # Lightest first, which validates the connection layer, then the heavy framework images.
  build "$REPO:ml-ubuntu24.04"            -f ml/Dockerfile.cpu                 .  # CPU (Ubuntu 24.04)
  build "$REPO:ml-cuda12.4-cudnn9"        -f ml/Dockerfile.ml-gpu              .  # base ML on GPU, with CRIU
  build "$REPO:pytorch2.6-cuda12.4-cudnn9"    -f ml/Dockerfile.pytorch-gpu     .  # PyTorch on GPU, with CRIU
  build "$REPO:tensorflow2.18-cuda12.5-cudnn9" -f ml/Dockerfile.tensorflow-gpu .  # TensorFlow on GPU, with CRIU
  # The lossless-pause node agent; its context is lossless-agent, which copies in agent.sh.
  build "$AGENT" lossless-agent
  echo "── build complete:"; docker images "$REPO" --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}"; docker images "$AGENT" --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}"
fi

if [ "$MODE" = push ] || [ "$MODE" = push-only ]; then
  docker info 2>/dev/null | grep -q "Username:" || { echo "⚠ log in to Docker Hub first: docker login -u <user>"; exit 1; }
  for t in "${TAGS[@]}"; do echo "── push: $REPO:$t"; docker push "$REPO:$t"; done
  echo "── push: $AGENT"; docker push "$AGENT"
fi
