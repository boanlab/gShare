# The GShare lossless-pause node agent.
#
# The process dump is performed by the **host's containerd** (`ctr c checkpoint`), which is the path
# we validated, so this image deliberately does not bundle CRIU — CRIU has to be installed on the
# node itself, which is what the gshare.io/criu=ready label asserts.
#
# The agent (1) returns the GPU's VRAM with NVIDIA cuda-checkpoint, and (2) checkpoints the container
# by calling `ctr` over the containerd socket.
# Design: see docs/paper/manuscript, §Implementation.
FROM ubuntu:26.04

ARG CUDA_CHECKPOINT_URL=https://github.com/NVIDIA/cuda-checkpoint/raw/main/bin/x86_64_Linux/cuda-checkpoint
# A ctr client matching the host's containerd, taken from bin/ctr in the release tarball. Pinned to
# the nodes' containerd 2.2.x.
ARG CONTAINERD_VERSION=2.2.1

RUN apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      procps ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

# NVIDIA cuda-checkpoint, which pairs with driver R550 and later. Its version has to match the
# driver present at runtime.
RUN curl -fsSL -o /usr/bin/cuda-checkpoint "${CUDA_CHECKPOINT_URL}" \
 && chmod +x /usr/bin/cuda-checkpoint

# Extract only the ctr client from the containerd release; the dump itself runs in the host's
# containerd, and ctr is just the client.
RUN curl -fsSL "https://github.com/containerd/containerd/releases/download/v${CONTAINERD_VERSION}/containerd-${CONTAINERD_VERSION}-linux-amd64.tar.gz" \
      | tar -xz -C /usr/local bin/ctr \
 && /usr/local/bin/ctr --version

COPY agent.sh /usr/bin/gshare-lossless-agent
RUN chmod +x /usr/bin/gshare-lossless-agent

ENTRYPOINT ["/usr/bin/gshare-lossless-agent"]
