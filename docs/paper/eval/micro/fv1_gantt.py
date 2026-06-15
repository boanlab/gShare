#!/usr/bin/env python3
"""FV1 lifecycle Gantt — resident yield -> spot borrow -> reclaim, representative run.
Phase boundaries are illustrative; the yield/reclaim transition latencies are measured
live (operator logs, gpu2-1): yield(stop->Yielded)=~12s, reclaim(resume->lossless restore)=~10s,
both including the lossless-agent Job spin-up. Output: fv1.png
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# (start, dur, label, color) per lane
resident = [
    (0, 60, "active", "#2e7d32"),
    (60, 12, "yield (evict, 12s)", "#f9a825"),
    (72, 70, "yielded (VRAM in host RAM, Pod alive)", "#bdbdbd"),
    (142, 10, "reclaim (lossless restore, 10s)", "#f9a825"),
    (152, 48, "active (lossless, step continues)", "#2e7d32"),
]
spot = [
    (80, 62, "spot borrow (full card)", "#1565c0"),
    (142, 4, "preempted (SIGTERM)", "#c62828"),
]
card = [
    (0, 60, "owner", "#2e7d32"),
    (60, 20, "yielded (lend pool)", "#bdbdbd"),
    (80, 62, "lent", "#1565c0"),
    (142, 10, "reclaiming", "#f9a825"),
    (152, 48, "owner", "#2e7d32"),
]
lanes = [("card", card), ("spot", spot), ("resident", resident)]

fig, ax = plt.subplots(figsize=(8, 2.8))
for i, (name, segs) in enumerate(lanes):
    for s, d, lab, c in segs:
        ax.broken_barh([(s, d)], (i * 10, 8), facecolors=c, edgecolor="white")
        if d >= 18:
            ax.text(s + d / 2, i * 10 + 4, lab, ha="center", va="center", fontsize=6.5, color="white")
ax.set_yticks([4, 14, 24])
ax.set_yticklabels(["GPU card", "spot session", "resident"])
ax.set_xlabel("time (s, representative)")
ax.set_title("FV1: in-place GPU yield → preemptible borrow → lossless reclaim (gpu2-1, RTX 4090)")
ax.set_xlim(0, 200)
fig.tight_layout()
fig.savefig("fv1.png", dpi=150)
print("[figure] fv1.png")
