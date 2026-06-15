# Multi-stage: compile in the CUDA devel image, ship only the binary on the slim runtime.
# Toolchain (nvcc, cuBLAS-dev) stays in the build stage — no GPU-node host installs.
# Build:  docker build -t boanlab/gshare-e2e-workload docs/paper/eval/e2e
# Run:    docker run --rm --gpus '"device=0"' boanlab/gshare-e2e-workload --mem-gb 6 --secs 60 --tag S
FROM nvidia/cuda:12.2.2-devel-ubuntu22.04 AS build
WORKDIR /w
COPY gpu_sgemm.cu .
RUN nvcc -O3 gpu_sgemm.cu -lcublas -o /gpu_sgemm

FROM nvidia/cuda:12.2.2-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y --no-install-recommends libcublas-12-2 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /gpu_sgemm /usr/local/bin/gpu_sgemm
ENTRYPOINT ["/usr/local/bin/gpu_sgemm"]
