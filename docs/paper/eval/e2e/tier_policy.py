#!/usr/bin/env python3
"""§E.6c tier-selection policy — when does the hierarchy demote to T2 (disk/swap) vs T3 (cold/durable)?
The T2 round-trip measurement (§E.6b) is fast-resume-poor, so T2 is NOT uniformly better than T3; the
policy must pick a tier. We make the boundary explicit from measured constants.

Measured anchors (gpu2-1, RTX-4090):
  T1 restore (host RAM)  : 0.34 + 0.21·GB s (M1; 4 GB ≈ 1.28 s). Lossless (0 redo). Bounded by host-RAM
                           (§E.6b: T1 capacity = floor(host_RAM·0.5 / GB) concurrent yields).
  T2 restore (disk swap) : swap-in + toggle. Measured 4 GB = 19.77 s → swap-in ≈ 221 MB/s on virtio
                           (/dev/vda1). Lossless (0 redo). Capacity = disk. NVMe (~2 GB/s) shown for ref.
  T3 cold/durable        : cold-start 12.87 s (§E.4b, vit_l) + progress redo. Redo = 0 if stateless or a
                           fresh app-checkpoint exists; else ~interval/2 (minutes for large models, §E.4b).
                           Durable (survives node failure), frees host-RAM AND swap.

Decision: minimize (resume latency + expected progress redo), subject to host-RAM/durability constraints.
  - T1 if it fits host-RAM (fast + lossless) — default for hot/likely-to-resume sessions.
  - Under host-RAM pressure, T2 vs T3:  T2 wins iff  redo_cost(T3) > [resume(T2) − resume(T3)].
    i.e. T2's slow swap-in is worth it ONLY when avoided progress-redo exceeds the extra resume time.
    → Large stateful, no cheap checkpoint: redo ~minutes ≫ swap-in ⇒ T2. Stateless / app-checkpointed /
      TTL-expired / node-failure-durability needed: ⇒ T3.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

GB = list(range(2, 49, 1))
def t1(g): return 0.34 + 0.21 * g                      # host RAM, lossless
def t2(g, bw): return g * 1024.0 / bw + (0.34 + 0.21 * g)  # swap-in(bw MB/s) + toggle, lossless
COLD = 12.87                                            # T3 cold-start latency (no redo), measured

fig, ax = plt.subplots(figsize=(7.4, 4.2))
ax.plot(GB, [t1(g) for g in GB], color="#1565c0", marker=".", label="T1 host-RAM (lossless) — until host-RAM full")
ax.plot(GB, [t2(g, 221) for g in GB], color="#c62828", marker=".", label="T2 disk/swap, virtio 221 MB/s (measured, lossless)")
ax.plot(GB, [t2(g, 2000) for g in GB], color="#ef6c00", ls="--", label="T2 disk/swap, NVMe ~2 GB/s (ref, lossless)")
ax.axhline(COLD, color="#2e7d32", lw=1.4, label="T3 cold-start 12.87 s (latency only; +redo if stateful)")
# T3 + minutes of redo for large stateful (shaded band above cold-start)
ax.axhspan(COLD, COLD + 270, color="#e8f5e9", alpha=0.5)
ax.text(33, COLD + 150, "T3 effective = cold-start + progress redo\n(large stateful: ~minutes, §E.4b)",
        fontsize=7.5, color="#2e7d32")
# crossover: where virtio-T2 latency meets T3 cold-start latency
import bisect
xs = [g + (t2(g, 221)) * 0 for g in GB]
cx = next(g for g in GB if t2(g, 221) >= COLD)
ax.axvline(cx, ls=":", color="gray")
ax.text(cx + 0.3, 40, f"T2(virtio) resume > T3 cold-start\nbeyond ~{cx} GB", fontsize=7.5, color="gray")
ax.set_xlabel("resident VRAM working set (GB)")
ax.set_ylabel("resume latency (s)  [T1/T2 lossless, 0 redo]")
ax.set_title("Tier selection: T2 (lossless, slow) beats T3 only when T3's progress-redo > T2's swap-in")
ax.set_yscale("log"); ax.set_ylim(0.8, 400)
ax.legend(fontsize=7.3, loc="upper left"); ax.grid(True, which="both", alpha=0.3)
fig.tight_layout(); fig.savefig("tier_policy.png", dpi=150)
print("[figure] tier_policy.png")
print(f"swap-in BW (measured, virtio) ≈ 221 MB/s; T2 vs T3 cold-start latency crossover ≈ {cx} GB")
for g in (4, 16, 24, 48):
    print(f"  {g:2d}GB: T1={t1(g):.1f}s  T2/virtio={t2(g,221):.0f}s  T2/NVMe={t2(g,2000):.1f}s  T3cold={COLD}s(+redo)")
