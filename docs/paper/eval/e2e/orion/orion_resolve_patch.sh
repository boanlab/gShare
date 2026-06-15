#!/bin/bash
# Patch Orion's CUDA interceptor: replace dlsym(RTLD_NEXT, "X") — which fails to resolve
# the version-noded symbols (cublasSgemm_v2@@libcublas.so.12) in modern CUDA libs — with
# orion_resolve("X"), which dlsym's from explicit dlopen handles of the real libraries
# (explicit-handle dlsym resolves default-versioned symbols, unlike RTLD_NEXT here).
set -e
cd /root/orion/src/cuda_capture

# 1) define orion_resolve at end of utils_interc.cpp
cat >> utils_interc.cpp <<'CPP'

// ---- Orion symbol resolution fix (added) ----
#include <dlfcn.h>
void* orion_resolve(const char* name) {
    static void* h[6] = {0};
    static int init = 0;
    static const char* libs[6] = {
        "libcudart.so.12", "libcublasLt.so.12", "libcublas.so.12",
        "libcudnn.so.9", "libcudnn_graph.so.9", "libcuda.so.1"
    };
    if (!init) { for (int i = 0; i < 6; i++) h[i] = dlopen(libs[i], RTLD_NOW | RTLD_GLOBAL); init = 1; }
    for (int i = 0; i < 6; i++) { if (h[i]) { void* s = dlsym(h[i], name); if (s) return s; } }
    return 0;
}
CPP

# 2) declare it in the two intercept files (after the common-header include)
sed -i '/#include "intercept_temp.h"/a void* orion_resolve(const char* name);' \
    intercept_cublas.cpp intercept_cudnn.cpp

# 3) redirect the 13 RTLD_NEXT resolutions to orion_resolve
sed -i 's/dlsym(RTLD_NEXT, /orion_resolve(/g' intercept_cublas.cpp intercept_cudnn.cpp

echo "patched: $(grep -c orion_resolve intercept_cublas.cpp intercept_cudnn.cpp | paste -sd' ')"
grep -c 'dlsym(RTLD_NEXT' intercept_cublas.cpp intercept_cudnn.cpp || true
