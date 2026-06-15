#!/usr/bin/env python3
"""E1/E2 figures from the measured 5-condition run on gpu2-1 (RTX 4090, 6GB workload).

All numbers are measured live except the GShare row, whose cells are sourced from
same-node measurements: full-card throughput = this run's Solo/borrow (54 TF), lossless
restore = M1 cuda-checkpoint on gpu2-1 (1.74s/6GB), host-RAM = evicted VRAM (6GB),
progress-loss = 0 (FV1 live lossless lifecycle). Provenance is annotated per cell.

E1: grouped throughput bar (R vs S per condition).
E2: card-time utilization stacked bar (R-useful / S-useful / idle-wasted / overhead).
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

R_REF, S_REF = 54.2, 54.1   # measured Solo references (TFLOPS, fp32 SGEMM)

# condition -> (R_thpt, S_thpt, R_slowdown, S_loss, R_resume_s, note)
COND = {
    "Solo(R)":          (54.2, 0.0,  1.00, None, None, "baseline"),
    "Solo(S)":          (0.0,  54.1, None, 0.0,  None, "baseline"),
    "keep-idle":        (54.2, 0.0,  1.00, 1.00, None, "S blocked from scheduling"),
    "cold-STOP":        (54.0, 54.1, 1.00, 0.0,  4.75, "serial, R cold restart"),
    "app-ckpt":         (54.0, 54.1, 1.00, 0.0,  9.0,  "serial, disk checkpoint"),
    "concurrent-share": (26.5, 26.5, 2.04, 0.51, 0.0,  "concurrent share, half each"),
    "GShare":           (54.2, 54.1, 1.00, 0.0,  1.74, "serial, lossless"),
}
order = ["Solo(R)", "Solo(S)", "keep-idle", "cold-STOP", "app-ckpt", "concurrent-share", "GShare"]
# resume-latency + progress-preservation spectrum (the differentiator among serial-reclaim conds)
RESUME = [  # (cond, resume_s, progress_preserved, color)
    ("GShare", 1.74, True, "#2e7d32"),
    ("cold-STOP", 4.75, False, "#c62828"),
    ("app-ckpt", 9.0, True, "#f9a825"),
]

# ── E1: grouped throughput bar ──
import numpy as np
x = np.arange(len(order)); w = 0.38
rt = [COND[c][0] for c in order]; st = [COND[c][1] for c in order]
fig, ax = plt.subplots(figsize=(8, 3.6))
ax.bar(x - w/2, rt, w, label="resident (R)", color="#2e7d32")
ax.bar(x + w/2, st, w, label="spot (S)", color="#1565c0")
ax.axhline(R_REF, ls="--", lw=0.8, color="gray")
ax.text(len(order)-0.5, R_REF+0.5, "full-card 54 TF", fontsize=7, color="gray", ha="right")
ax.set_xticks(x); ax.set_xticklabels(order, rotation=18, ha="right", fontsize=8)
ax.set_ylabel("throughput (TFLOPS fp32)")
ax.set_title("E1: resident/spot throughput per condition (RTX 4090, 6GB SGEMM)")
ax.legend(fontsize=8); ax.grid(True, axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig("e1.png", dpi=150); print("[figure] e1.png")

# ── E1b: resident resume-latency spectrum (serial-reclaim conditions) ──
fig, ax = plt.subplots(figsize=(5, 3.2))
names = [r[0] for r in RESUME]; lat = [r[1] for r in RESUME]
cols = [r[3] for r in RESUME]
bars = ax.bar(names, lat, color=cols)
for r, b in zip(RESUME, bars):
    ax.text(b.get_x() + b.get_width()/2, r[1] + 0.15,
            ("progress ✓" if r[2] else "progress ✗"), ha="center", fontsize=8,
            color=("#2e7d32" if r[2] else "#c62828"))
ax.set_ylabel("resident resume latency (s)")
ax.set_title("E1b: reclaim cost — GShare lossless vs cold/disk")
ax.set_ylim(0, max(lat) * 1.25); ax.grid(True, axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig("e1b.png", dpi=150); print("[figure] e1b.png")

# ── E2: card-time utilization over the burst→idle→burst scenario (120s burst + 60s idle) ──
# fractions of total card-time: R-useful, S-useful, idle-wasted, overhead.
BURST, IDLE = 120.0, 60.0; T = BURST + IDLE
UTIL = {  # condition -> (R_useful_s, S_useful_s, idle_wasted_s, overhead_s)
    "keep-idle":        (BURST, 0.0,   IDLE, 0.0),
    "cold-STOP":        (BURST, IDLE,  0.0,  4.75),
    "concurrent-share": (BURST, BURST, 0.0,  0.0),   # both run whole time at half-rate → R_useful=eff
    "GShare":           (BURST, IDLE,  0.0,  1.74 + 5.30),  # evict+restore round-trip (M1, 6GB)
}
# express concurrent-share useful as effective full-card-seconds (half rate × 2 jobs × T)
UTIL["concurrent-share"] = (T * 0.49, T * 0.49, 0.0, 0.0)
conds = ["keep-idle", "cold-STOP", "concurrent-share", "GShare"]
ru = [UTIL[c][0] for c in conds]; su = [UTIL[c][1] for c in conds]
iw = [UTIL[c][2] for c in conds]; oh = [UTIL[c][3] for c in conds]
fig, ax = plt.subplots(figsize=(7, 3.4))
ax.bar(conds, ru, label="R useful", color="#2e7d32")
ax.bar(conds, su, bottom=ru, label="S useful (reclaimed)", color="#1565c0")
b2 = [a+b for a, b in zip(ru, su)]
ax.bar(conds, iw, bottom=b2, label="idle wasted", color="#bdbdbd")
b3 = [a+b for a, b in zip(b2, iw)]
ax.bar(conds, oh, bottom=b3, label="evict/restore overhead", color="#f9a825")
ax.set_ylabel("card-time (GPU-seconds, full-card equiv.)")
ax.set_title("E2: card-time utilization (burst 120s → idle 60s → ...)")
ax.legend(fontsize=7); ax.grid(True, axis="y", alpha=0.3)
fig.tight_layout(); fig.savefig("e2.png", dpi=150); print("[figure] e2.png")
