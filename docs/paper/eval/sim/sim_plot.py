#!/usr/bin/env python3
"""Plot §E.5 cluster-sim figures from sim_results.json (+ optional sweep files).

S1: occupancy vs offered load (keep-idle / cold-preempt / gshare).
S2: resident resume penalty vs load — gshare lossless restore vs cold-preempt cold-start.
S3: sensitivity of the gshare resident-cost advantage to cold-start cost (sweep files).
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sim_results.json"))
loads = [r["load"] for r in d["by_load"]]
def series(pol, key): return [r["policies"][pol][key] for r in d["by_load"]]

# ── S1: occupancy vs load ──
fig, ax = plt.subplots(figsize=(5, 3.4))
for pol, c, m in [("keep-idle", "#9e9e9e", "o"), ("cold-preempt", "#1565c0", "s"), ("gshare", "#2e7d32", "^")]:
    ax.plot(loads, [x * 100 for x in series(pol, "occupancy")], marker=m, color=c, label=pol)
ax.set_xlabel("offered load (resident duty cycle)")
ax.set_ylabel("cluster GPU occupancy (%)")
ax.set_title("S1: occupancy vs load (16 cards, 24h)")
ax.legend(fontsize=8); ax.grid(True, alpha=0.3)
fig.tight_layout(); fig.savefig("s1.png", dpi=150); print("[figure] s1.png")

# ── S2: resident resume penalty vs load (the lossless advantage) ──
fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.4))
cp = [x / 3600 for x in series("cold-preempt", "resident_resume_penalty_s_total")]
gs = [x / 3600 for x in series("gshare", "resident_resume_penalty_s_total")]
a1.plot(loads, cp, marker="s", color="#1565c0", label="cold-preempt (cold-start)")
a1.plot(loads, gs, marker="^", color="#2e7d32", label="gshare (lossless restore)")
a1.set_xlabel("offered load"); a1.set_ylabel("total resident resume penalty (GPU-h)")
a1.set_title("S2a: resident reclaim cost"); a1.legend(fontsize=8); a1.grid(True, alpha=0.3)
ratio = [(c / g if g > 1e-9 else float("nan")) for c, g in zip(cp, gs)]
a2.plot(loads, ratio, marker="o", color="#6a1b9a")
a2.axhline(1, color="gray", lw=0.6)
a2.set_xlabel("offered load"); a2.set_ylabel("cold-preempt / gshare penalty (×)")
a2.set_title("S2b: lossless advantage (×)"); a2.grid(True, alpha=0.3)
fig.tight_layout(); fig.savefig("s2.png", dpi=150); print("[figure] s2.png")

# ── S3: sensitivity to cold-start cost (optional sweep files: sim_cold_*.json) ──
sweep = sys.argv[2:] if len(sys.argv) > 2 else []
if sweep:
    fig, ax = plt.subplots(figsize=(5, 3.4))
    for path in sweep:
        s = json.load(open(path))
        b = s["params"]["cold_b"]
        ls = [r["load"] for r in s["by_load"]]
        adv = []
        for r in s["by_load"]:
            c = r["policies"]["cold-preempt"]["resident_resume_penalty_s_total"]
            g = r["policies"]["gshare"]["resident_resume_penalty_s_total"]
            adv.append(c / g if g > 1e-9 else float("nan"))
        ax.plot(ls, adv, marker=".", label=f"cold {s['params']['cold_a']:.0f}+{b:.0f}/GB")
    ax.set_xlabel("offered load"); ax.set_ylabel("lossless advantage (×)")
    ax.set_title("S3: sensitivity to cold-start cost")
    ax.legend(fontsize=7); ax.grid(True, alpha=0.3)
    fig.tight_layout(); fig.savefig("s3.png", dpi=150); print("[figure] s3.png")
