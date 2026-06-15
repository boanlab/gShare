#!/usr/bin/env python3
"""§E.5d trace-driven cluster sim — replace the synthetic uniform load sweep with the REAL per-GPU
utilization distribution from the Alibaba PAI 2020 trace (§E.5c, trace_impact.md), honestly bounded.

The trace gives lifetime-averaged per-GPU *utilization* u, not the *duty cycle* the sim needs: a card at
u=20% could be 20%-duty/100%-intensity (80% idle → reclaimable) or 100%-duty/20%-intensity (never idle →
0 reclaimable). The trace cannot resolve which (§E.5c). So we parameterise: let φ = fraction of a card's
NON-utilized time that is reclaimable idle gaps (vs sustained-low intensity). Effective duty fed to the
sim = u + (1-φ)(1-u). φ=0 → all sustained-low (duty=1, NO reclaim, conservative floor ≈ §E.5c near-idle);
φ=1 → all idle-gap (duty=u, max reclaim, optimistic). The real cluster sits in between, unresolvable from
this trace — so we report the BOUND, not a single flattering number.

Per-card utilization is sampled from the measured distribution (trace_impact.md, GPU-time weighted):
"""
import random
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import simulator as S

# measured per-GPU util distribution (bucket midpoint %, weight = fraction of GPU-time), trace_impact.md
DIST = [(2.5, 0.006), (7.5, 0.026), (17.5, 0.556), (37.5, 0.362), (62.5, 0.043), (82.5, 0.006), (95.0, 0.001)]
NCARDS, HORIZON, SEED = 64, 24 * 3600, 20260622

def sample_utils(n, seed):
    rnd = random.Random(seed)
    mids = [m for m, _ in DIST]; ws = [w for _, w in DIST]
    return [rnd.choices(mids, weights=ws)[0] / 100.0 for _ in range(n)]

def residents_for_duty(duties, seed):
    rnd = random.Random(seed); vram = [6, 8, 12, 16, 24]; res = []
    for c, d in enumerate(duties):
        gb = rnd.choice(vram)
        if d >= 0.999:                      # always-busy (φ=0 sustained-low): no idle, no reclaim
            res.append({"card": c, "gb": gb, "segs": [("burst", 0.0, HORIZON)]}); continue
        burst = 300.0; gap = burst * (1 - d) / max(d, 1e-3)
        segs = []; t = 0.0
        while t < HORIZON:
            b = rnd.expovariate(1 / burst); segs.append(("burst", t, min(b, HORIZON - t))); t += b
            if t >= HORIZON: break
            g = rnd.expovariate(1 / gap); segs.append(("idle", t, min(g, HORIZON - t))); t += g
        res.append({"card": c, "gb": gb, "segs": segs})
    spot = [{"dur": rnd.uniform(60, 600), "gb": rnd.choice(vram)} for _ in range(NCARDS * 60)]
    return res, spot

utils = sample_utils(NCARDS, SEED)
phis = [0.0, 0.25, 0.5, 0.75, 1.0]
reclaim_h, occ_keepidle, occ_gshare = [], [], []
for phi in phis:
    duties = [u + (1 - phi) * (1 - u) for u in utils]
    res, spot = residents_for_duty(duties, SEED)
    ki = S.simulate("keep-idle", *(residents_for_duty(duties, SEED)), NCARDS, 256.0, HORIZON)
    gs = S.simulate("gshare", *(residents_for_duty(duties, SEED)), NCARDS, 256.0, HORIZON)
    reclaim_h.append(gs["spot_useful_recovered_h"])
    occ_keepidle.append(ki["occupancy"] * 100)
    occ_gshare.append(gs["occupancy"] * 100)

fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 3.9))
ax.plot(phis, reclaim_h, marker="o", color="#2e7d32")
ax.axvspan(-0.02, 0.05, color="#ffebee", alpha=0.6); ax.text(0.02, max(reclaim_h)*0.5, "φ→0\nconservative\n(near-idle only)", fontsize=7.5, color="#c62828", ha="left")
ax.text(0.97, max(reclaim_h)*0.88, "φ=1\noptimistic\n(all idle-gap)", fontsize=7.5, color="#2e7d32", ha="right")
ax.set_xlabel("φ = fraction of non-utilized time that is idle gaps (trace cannot resolve)")
ax.set_ylabel("GShare recovered spot GPU-h / 24h")
ax.set_title(f"Trace-driven reclaim BOUND ({NCARDS} cards, real Alibaba util dist)")
ax.grid(True, alpha=0.3)
ax2.plot(phis, occ_keepidle, marker="s", color="#9e9e9e", label="keep-idle")
ax2.plot(phis, occ_gshare, marker="^", color="#2e7d32", label="GShare")
ax2.set_xlabel("φ (idle-gap fraction)"); ax2.set_ylabel("cluster occupancy (%)")
ax2.set_title("Occupancy under real util mix"); ax2.legend(fontsize=8); ax2.grid(True, alpha=0.3)
fig.tight_layout(); fig.savefig("trace_driven.png", dpi=150)
print("[figure] trace_driven.png")
print(f"sampled util mean = {sum(utils)/len(utils)*100:.1f}% over {NCARDS} cards")
for phi, r, ok, og in zip(phis, reclaim_h, occ_keepidle, occ_gshare):
    print(f"  φ={phi:.2f}: recovered {r:6.0f} GPU-h/24h ; occupancy keep-idle {ok:.1f}% → GShare {og:.1f}%")
