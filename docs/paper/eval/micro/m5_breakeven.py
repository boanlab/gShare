#!/usr/bin/env python3
"""M5 break-even — derived from M1 (no new measurement).

A yield cycle spends evict+restore of "unproductive" GPU time; the spot session only
nets useful work if the yield window W exceeds that round-trip overhead. Break-even
window W*(vram) = evict + restore. Net recovered spot GPU-time = max(0, W - W*).
Reads m1.csv. Emits a table + figure m5.png.
"""
import csv
import statistics as st
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "m1.csv"
by = {}
for r in csv.DictReader(open(path)):
    try:
        g = float(r["vram_gb"]); by.setdefault(g, []).append((float(r["evict_s"]), float(r["restore_s"])))
    except ValueError:
        pass
vrams = sorted(by)
wstar = {g: st.mean(e for e, _ in by[g]) + st.mean(r for _, r in by[g]) for g in by}

print("\n| VRAM(GB) | round-trip W*=evict+restore (s) |")
print("|---|---|")
for g in vrams:
    print(f"| {g:g} | {wstar[g]:.1f} |")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(9, 3.2))
    a1.bar([str(int(g)) for g in vrams], [wstar[g] for g in vrams], color="#6a1b9a")
    a1.set_xlabel("VRAM (GB)"); a1.set_ylabel("break-even window W* (s)")
    a1.set_title("M5a: min yield window for net spot gain")
    W = list(range(0, 61, 2))
    for g in (2, 6, 16):
        if g in wstar:
            a2.plot(W, [max(0, w - wstar[g]) for w in W], marker=".", label=f"{int(g)}GB (W*={wstar[g]:.1f}s)")
    a2.axhline(0, color="gray", lw=0.6)
    a2.set_xlabel("yield window W (s)"); a2.set_ylabel("net recovered spot GPU-time (s)")
    a2.set_title("M5b: net benefit vs window"); a2.legend(fontsize=7); a2.grid(True, alpha=0.3)
    fig.tight_layout(); fig.savefig("m5.png", dpi=150)
    print("\n[figure] m5.png")
except ImportError:
    print("\n[figure] matplotlib unavailable — table only")
