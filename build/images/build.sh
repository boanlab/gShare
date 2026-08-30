#!/usr/bin/env bash
set -euo pipefail
# Build the catalogue session images, and optionally push them.
#   ./build.sh             # build all four images
#   REG=myreg ./build.sh   # registry namespace (default: boanlab)
#   REG=10.0.0.5:5000 ./build.sh push   # push to a LAN registry instead of Docker Hub
#                                       # (plain HTTP: add it to the build host's docker
#                                       #  "insecure-registries" first; the login check is skipped)
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
MIGAGENT="${MIGAGENT:-$REG/gshare-mig-agent:latest}"  # the MIG-mode node agent (nvidia-smi -mig)
MODE="${1:-build}"

TAGS=(ml-ubuntu24.04 ml-cuda12.4-cudnn9 pytorch2.6-cuda12.4-cudnn9 tensorflow2.18-cuda12.5-cudnn9
      ml-cuda12.8-cudnn9 pytorch2.7-cuda12.8-cudnn9 pytorch2.8-cuda12.9-cudnn9 tensorflow-ngc25.02-cuda12.8)

build(){ local tag="$1"; shift; echo "── build: $tag"; docker build -t "$tag" "$@"; }

if [ "$MODE" != "push-only" ]; then
  # Lightest first, which validates the connection layer, then the heavy framework images.
  build "$REPO:ml-ubuntu24.04"            -f ml/Dockerfile.cpu                 .  # CPU (Ubuntu 24.04)
  build "$REPO:ml-cuda12.4-cudnn9"        -f ml/Dockerfile.ml-gpu              .  # base ML on GPU, with CRIU
  build "$REPO:pytorch2.6-cuda12.4-cudnn9"    -f ml/Dockerfile.pytorch-gpu     .  # PyTorch on GPU, with CRIU
  build "$REPO:tensorflow2.18-cuda12.5-cudnn9" -f ml/Dockerfile.tensorflow-gpu .  # TensorFlow on GPU, with CRIU
  # Blackwell-capable line (CUDA >= 12.8; RTX PRO 5000/6000 and friends refuse the 12.4 images).
  build "$REPO:ml-cuda12.8-cudnn9"            -f ml/Dockerfile.ml-cu128           .  # base ML, CUDA 12.8
  build "$REPO:pytorch2.7-cuda12.8-cudnn9"    -f ml/Dockerfile.pytorch-blackwell  .  # PyTorch 2.7 cu12.8
  build "$REPO:pytorch2.8-cuda12.9-cudnn9"    -f ml/Dockerfile.pytorch28          .  # PyTorch 2.8 cu12.9
  build "$REPO:tensorflow-ngc25.02-cuda12.8"  -f ml/Dockerfile.tensorflow-ngc     .  # TF (NGC 25.02), Blackwell
  # The lossless-pause node agent; its context is lossless-agent, which copies in agent.sh.
  build "$AGENT" lossless-agent
  # The MIG-mode node agent (GpuModeChange card transitions).
  build "$MIGAGENT" mig-agent
  echo "── build complete:"; docker images "$REPO" --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}"; docker images "$AGENT" --format "  {{.Repository}}:{{.Tag}}\t{{.Size}}"
fi

if [ "$MODE" = push ] || [ "$MODE" = push-only ]; then
  case "$REG" in
    *:*) echo "── LAN registry push ($REG): skipping the Docker Hub login check" ;;
    *) docker info 2>/dev/null | grep -q "Username:" || { echo "⚠ log in to Docker Hub first: docker login -u <user>"; exit 1; } ;;
  esac
  for t in "${TAGS[@]}"; do echo "── push: $REPO:$t"; docker push "$REPO:$t"; done
  echo "── push: $AGENT"; docker push "$AGENT"
  echo "── push: $MIGAGENT"; docker push "$MIGAGENT"
fi
