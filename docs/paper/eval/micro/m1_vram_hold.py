#!/usr/bin/env python3
"""M1 microbench workload — hold N GB of GPU VRAM in a live CUDA context, then idle.

No torch / CUDA toolkit needed: drives the CUDA Driver API via ctypes on libcuda.so.1
(present wherever the NVIDIA driver is). cuda-checkpoint --toggle can then evict/restore
this process's VRAM, so the driver (m1_run.sh) times the toggle.

Usage: python3 m1_vram_hold.py --gb 6 [--chunk-gb 1]
Prints "READY pid=<pid> ..." once VRAM is resident, then sleeps holding it.
"""
import argparse
import ctypes
import os
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("--gb", type=float, required=True)
ap.add_argument("--chunk-gb", type=float, default=1.0)  # allocate in chunks to stay under the single-cuMemAlloc ceiling
args = ap.parse_args()

cuda = ctypes.CDLL("libcuda.so.1")

# CUresult cuGetErrorString(CUresult, const char**)
cuda.cuGetErrorString.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
cuda.cuInit.argtypes = [ctypes.c_uint]
cuda.cuDeviceGet.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_int]
cuda.cuCtxCreate_v2.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint, ctypes.c_int]
cuda.cuMemAlloc_v2.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_size_t]
cuda.cuMemsetD8_v2.argtypes = [ctypes.c_uint64, ctypes.c_ubyte, ctypes.c_size_t]


def chk(r, what):
    if r != 0:
        s = ctypes.c_char_p()
        cuda.cuGetErrorString(r, ctypes.byref(s))
        sys.exit(f"[m1] {what} failed: CUresult={r} {s.value!r}")


chk(cuda.cuInit(0), "cuInit")
dev = ctypes.c_int()
chk(cuda.cuDeviceGet(ctypes.byref(dev), 0), "cuDeviceGet")
ctx = ctypes.c_void_p()
chk(cuda.cuCtxCreate_v2(ctypes.byref(ctx), 0, dev), "cuCtxCreate")

total = int(args.gb * (1024 ** 3))
chunk = int(args.chunk_gb * (1024 ** 3))
allocated = 0
ptrs = []
while allocated < total:
    sz = min(chunk, total - allocated)
    dptr = ctypes.c_uint64()
    chk(cuda.cuMemAlloc_v2(ctypes.byref(dptr), sz), f"cuMemAlloc({sz})")
    cuda.cuMemsetD8_v2(dptr, 1, sz)  # touch → resident
    ptrs.append(dptr)
    allocated += sz
cuda.cuCtxSynchronize()

print(f"READY pid={os.getpid()} gb={args.gb} allocated_bytes={allocated}", flush=True)
while True:
    time.sleep(3600)
