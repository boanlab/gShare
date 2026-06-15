#!/usr/bin/env python3
"""O1 — scheduler decision latency microbenchmark (control-plane overhead).

Times the scheduler hot path `SchedulerService._reserve_target` (2D best-fit over a card
inventory) across cluster sizes. Pure-CPU, no DB/GPU. Run inside the backend test image so
the app package is importable:

  docker run --rm -v "$PWD":/app -w /app gshare-backend-test python docs/paper/eval/o1/scheduler_bench.py

Reports P50/P99 latency per card count → O1 overhead numbers (manuscript §Evaluation).
"""
import time
from types import SimpleNamespace as N

from app.domain.scheduler import SchedulerService


def dev(did, tm, um, tc, uc):
    return N(id=did, total_mem_mb=tm, used_mem_mb=um, total_cores=tc, used_cores=uc)


def mkdevs(n):
    # realistic mixed-occupancy inventory
    return [dev(f"c{i}", 24576, (i * 1300) % 20000, 100, (i * 7) % 90) for i in range(n)]


def bench(devs, exclusive, req_mem, req_cores, iters=20000):
    lat = []
    for _ in range(iters):
        t = time.perf_counter()
        SchedulerService._reserve_target(devs, req_mem, req_cores, exclusive)
        lat.append((time.perf_counter() - t) * 1e3)
    lat.sort()
    return lat[len(lat) // 2], lat[int(len(lat) * 0.99)]


if __name__ == "__main__":
    for n in (1, 8, 16, 64):
        p50, p99 = bench(mkdevs(n), False, 4096, 25)
        print(f"reserve_target frac  cards={n:3d}  P50={p50:.4f}ms  P99={p99:.4f}ms")
    p50, p99 = bench(mkdevs(16), True, 24576, 100)
    print(f"reserve_target excl  cards=16   P50={p50:.4f}ms  P99={p99:.4f}ms")
