#!/usr/bin/env python3
"""§E.5b economic figure (C3, utilization-based — NO pricing). From sim_results.json (+ TTL
sweep files for the reservation-policy panel).

S4a: keep-idle bills the resident for reclaimable idle on which NO work is done (paid-but-idle
     waste); GShare eliminates that billed-idle and recovers useful spot GPU-time on the freed
     card. Plotted as GPU-hours vs offered load.
S4b: the time-limited resume reservation (TTL) bounds the hold — yields exceeding TTL are
     demoted (card+priority returned). #demotes vs TTL shows the lossless↔durable safety valve.
"""
import json
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

d = json.load(open(sys.argv[1] if len(sys.argv) > 1 else "sim_results.json"))
loads = [r["load"] for r in d["by_load"]]
ki_waste = [r["policies"]["keep-idle"]["resident_idle_billed_h"] for r in d["by_load"]]
gs_recover = [r["policies"]["gshare"]["spot_useful_recovered_h"] for r in d["by_load"]]

fig, (a1, a2) = plt.subplots(1, 2, figsize=(9.2, 3.4))

# S4a: paid-but-idle waste vs recovered useful work
a1.plot(loads, ki_waste, marker="s", color="#c62828", label="keep-idle: idle-billed (no work) waste")
a1.plot(loads, gs_recover, marker="^", color="#2e7d32", label="GShare: reclaimed useful GPU-hours")
a1.set_xlabel("offered load (resident duty cycle)")
a1.set_ylabel("GPU-hours / 24h·16-card cluster")
a1.set_title("S4a: idle waste to reclaimed useful work (price-independent)")
a1.legend(fontsize=7.5); a1.grid(True, alpha=0.3)

# S4b: TTL reservation safety valve (demotes vs TTL) — from sweep files
ttl_files = sys.argv[2:]
if ttl_files:
    pts = []
    for f in [sys.argv[1]] + ttl_files:
        s = json.load(open(f))
        ttl = s["params"]["YIELD_RESERVATION_TTL_SEC"]
        # demotes at the low-load (long-idle) regime where TTL matters most
        dem = [r["policies"]["gshare"]["demotes"] for r in s["by_load"] if r["load"] == 0.1]
        if dem:
            pts.append((ttl, dem[0]))
    pts = sorted(set(pts))
    a2.plot([p[0] / 60 for p in pts], [p[1] for p in pts], marker="o", color="#1565c0")
    a2.set_xlabel("resume-reservation TTL (min)")
    a2.set_ylabel("#demotes (demotion to durable, load=0.1)")
    a2.set_title("S4b: TTL as the lossless-to-durable safety valve")
    a2.grid(True, alpha=0.3)
else:
    a2.axis("off")
fig.tight_layout()
fig.savefig("s4.png", dpi=150)
print("[figure] s4.png")
