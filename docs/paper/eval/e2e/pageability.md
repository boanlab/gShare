# Pageability of the evicted VRAM buffer — enabling the T2 (disk/swap) tier

Verifies the key enabler of the GPU-state memory hierarchy's disk tier (T2; see §Design hierarchy and §Evaluation host-RAM in the manuscript): when
cuda-checkpoint evicts a session's VRAM to host RAM (T1), *where* does that data live, and is it
swappable? If the evicted buffer is anonymous, non-pinned, pageable memory, the OS swap mechanism can
page it to disk under host-RAM pressure for free — realizing T2 losslessly, with the pod alive
(C2 avoided), without any explicit spill logic. Measured live on gpu2-1 (RTX-4090).

## Method
A 4 GB ctypes/libcuda holder (host PID 54616) on gpu2-1; a privileged hostPID agent reads the holder's
`/proc/<pid>/status`, `/proc/<pid>/smaps_rollup`, and the pod cgroup `memory.current` before/after a
confirmed evict (`cuda-checkpoint --action lock`+`checkpoint`).

## Measured
| | before evict | after evict |
|---|---|---|
| GPU VRAM (nvidia-smi) | 4492 MiB | **1 MiB** (yielded) |
| holder `VmRSS` | 123,436 kB | **4,659,776 kB** (+4.5 GB) |
| pod cgroup `memory.current` | 48,623,616 | **4,766,212,096** (+4.7 GB) |
| `VmLck` (mlock) | 0 | **0** |
| `VmPin` (kernel GUP pin) | 0 | **0** |
| smaps_rollup `Anonymous` | — | **4,635,840 kB** |
| smaps_rollup `Locked` | — | **0** |
| state | running | checkpointed |

## Conclusion
The evicted VRAM lands in the **checkpointed (suspended) process's own address space as anonymous,
non-pinned, non-locked, swappable pages** (charged to the pod cgroup). Therefore:
- **T2 = OS swap, for free.** Under host-RAM pressure the kernel demand-pages these anonymous pages to
  disk/NVMe; the process is suspended so it does not touch them until restore. No explicit spill code;
  the pod stays alive (no CRI restore → C2 not re-introduced).
- The §E.6b host-RAM concurrency bound is the **T1 capacity**, not a fundamental limit: the bound
  becomes `host-RAM + swap`, i.e. *hot working-set size* rather than a concurrency cap.

## Real T2 (disk/swap) round-trip — measured
The pageability result predicts the OS swap path applies. We then measured the full T1→T2→T0 cycle
end-to-end on gpu2-1: enabled an 8 GB swapfile on the host (`swapon`), overrode the holder pod's
`memory.swap.max` (0→max, since kubelet disables pod swap by default), evicted the 4 GB session, then
forced the evicted anonymous buffer to disk by lowering the pod's `memory.high` (→300 MB), and finally
restored. All node changes reverted afterward (swapoff + rm swapfile + namespace delete).

| stage | metric | value |
|---|---|---|
| evict | host buffer (cgroup memory.current) | 48 MB → **4.77 GB** |
| **T1 restore** (host RAM) | restore+unlock latency | **1.28 s**, lossless (state running, VRAM→4492) |
| **T1→T2 spill** (demote) | memory.current 4.77 GB→**210 MB**; swap.current→**4.2 GB on disk** | **6.34 s** (~660 MB/s write) |
| **T2 restore** (swap-in + toggle) | restore+unlock latency | **19.77 s**, lossless (state running, VRAM→4492) |

**Result:** the disk tier works — the evicted VRAM spilled to disk (4.2 GB on swap), freeing host RAM
(4.77 GB→210 MB), and restored **losslessly** (state `running`, VRAM 4492 MiB). T2 reclaim is ~15×
slower than T1 (19.77 s vs 1.28 s for 4 GB): exactly the *capacity-not-speed* tradeoff of a lower
hierarchy tier. So the §E.6b host-RAM concurrency bound is the **T1 capacity**, with T2 extending it to
`host-RAM + swap` at a slower reclaim — measured, not projected.

Honest note: T2 restore (19.77 s) is far above a sequential-read projection (~1.5 s at 2.6 GB/s)
because swap-in is random page-fault I/O on this node's virtio disk (`/dev/vda1`), not a sequential
read. On local NVMe it would be faster but still ≫ T1. The point stands: T2 is a slow capacity tier.

## Reproduce
Evict a holder via the agent (lock+checkpoint, verify VRAM→1); read the holder's `/proc/<pid>/status`
(VmRSS/VmLck/VmPin/VmSwap) and `smaps_rollup` (Anonymous/Locked) plus its pod cgroup `memory.current`.
For the T2 round-trip: enable host swap, set the pod cgroup `memory.swap.max=max`, evict, lower
`memory.high` to force the anonymous buffer to swap (observe swap.current rise / memory.current drop),
reset `memory.high`, then restore and time it. Revert: swapoff + rm swapfile. Requires authorization
for privileged hostPID + GPU pods and node swap reconfiguration.
