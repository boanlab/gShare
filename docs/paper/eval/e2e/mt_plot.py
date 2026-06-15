#!/usr/bin/env python3
"""§E.4c real 2-node multi-tenant yield→borrow→reclaim. Two resident sessions (one per node,
gpu2-1/gpu2-2, RTX-4090, 4 GB each) idle-hold their cards; GShare yields both in-place and lends each
freed card to a spot session, then reclaims losslessly. All numbers measured live (nvidia-smi +
gpu_sgemm throughput); addresses the "cluster claims are simulator-only" gap with a real measurement.

Measured timeline (per card):
  keep-idle baseline : resident holds 4492 MiB, util 0% → 0 useful TFLOPS (occupied, billed, idle).
  yield (evict)      : cuda-checkpoint evict → VRAM 4492→1 MiB (card freed).
  borrow (spot)      : spot gpu_sgemm placed device-plugin-bypass (NVIDIA_VISIBLE_DEVICES=<uuid>,
                       no nvidia.com/gpu request — the operator's borrow path) runs at util 100%.
                       gpu2-1: 54.53 TFLOPS (273 iters/60.1 s); gpu2-2: 54.59 TFLOPS (273/60.0 s).
  reclaim (lossless) : restore+unlock → VRAM 1→4492 MiB, state `running`. 1.67 s / 1.26 s.

Simulator tie-in (M5 break-even): recovered spot GPU-time per borrow = max(0, W − W*), W*=evict+restore.
4 GB: W*≈ evict 3.1 s + restore 1.1 s = 4.2 s; W=60 s window → sim predicts 55.8 s useful/card. Measured
spot ran the full ~60 s at 100% util on each freed card → consistent with the sim's cost model at
scenario scale (extends the §E.5 single-cycle cross-validation to a 2-node multi-event timeline).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

nodes = ["gpu2-1", "gpu2-2"]
keepidle = [0.0, 0.0]                 # idle-held → 0 useful TFLOPS
gshare = [54.53, 54.59]              # spot full-card throughput recovered (measured)

fig, (ax, ax2) = plt.subplots(1, 2, figsize=(9.6, 3.8), gridspec_kw={"width_ratios": [1.1, 1]})

x = range(len(nodes)); w = 0.38
ax.bar([i - w/2 for i in x], keepidle, w, color="#9e9e9e", label="keep-idle (idle-held)")
ax.bar([i + w/2 for i in x], gshare, w, color="#2e7d32", label="GShare (yield→borrow)")
for i, g in enumerate(gshare):
    ax.text(i + w/2, g + 1, f"{g:.1f}", ha="center", fontsize=9, color="#2e7d32")
    ax.text(i - w/2, 1.5, "0", ha="center", fontsize=9, color="#616161")
ax.set_xticks(list(x)); ax.set_xticklabels(nodes)
ax.set_ylabel("useful GPU throughput (TFLOPS)")
ax.set_title("Real 2-node multi-tenant: idle-card occupancy recovered")
ax.legend(fontsize=8, loc="upper center"); ax.grid(True, axis="y", alpha=0.3); ax.set_ylim(0, 66)

# right: per-card VRAM timeline across the cycle (one representative card)
phases = ["keep-idle", "yield", "borrow", "reclaim"]
res_vram = [4492, 1, 1, 4492]         # resident VRAM held (MiB)
spot_vram = [0, 0, 4194, 0]           # spot VRAM on freed card (MiB)
xp = range(len(phases))
ax2.plot(xp, res_vram, marker="o", color="#1565c0", label="resident VRAM (held)")
ax2.plot(xp, spot_vram, marker="s", color="#2e7d32", label="spot VRAM (borrow)")
ax2.set_xticks(list(xp)); ax2.set_xticklabels(phases, fontsize=8)
ax2.set_ylabel("VRAM (MiB)")
ax2.set_title("Per-card VRAM: yield→borrow→lossless reclaim")
ax2.annotate("lossless: state=running,\nVRAM restored (1.3–1.7 s)", xy=(3, 4492), xytext=(1.3, 3200),
             fontsize=7.5, color="#1565c0", arrowprops=dict(arrowstyle="->", color="#1565c0"))
ax2.legend(fontsize=7.5, loc="center left"); ax2.grid(True, alpha=0.3); ax2.set_ylim(-200, 5200)

fig.tight_layout(); fig.savefig("mt_occupancy.png", dpi=150)
print("[figure] mt_occupancy.png")
print(f"keep-idle useful = 0 TFLOPS (2 cards idle-held); "
      f"GShare recovered = {gshare[0]+gshare[1]:.1f} TFLOPS aggregate (2 nodes), lossless reclaim.")
