#!/usr/bin/env python3
"""M1 plot/stats — read m1.csv (from m1_run.sh), compute per-VRAM mean±std of
evict/restore latency + transfer GB/s, emit a Markdown table (for the M1 figure; see manuscript §Evaluation)
and a line figure (if matplotlib is available).

Usage: python3 m1_plot.py m1.csv [out.png]
"""
import csv
import statistics as st
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "m1.csv"
out_png = sys.argv[2] if len(sys.argv) > 2 else "m1.png"

rows = []
with open(path) as f:
    for r in csv.DictReader(f):
        try:
            rows.append({k: float(v) for k, v in r.items()})
        except ValueError:
            pass

by = {}
for r in rows:
    by.setdefault(r["vram_gb"], []).append(r)

def ms(xs):
    return (st.mean(xs), st.stdev(xs) if len(xs) > 1 else 0.0)

vrams = sorted(by)
print("\n| VRAM(GB) | n | evict(s) | restore(s) | evict GB/s | restore GB/s | init(s) |")
print("|---|---|---|---|---|---|---|")
agg = {}
for g in vrams:
    rs = by[g]
    ev_m, ev_s = ms([r["evict_s"] for r in rs])
    re_m, re_s = ms([r["restore_s"] for r in rs])
    in_m, _ = ms([r["init_s"] for r in rs])
    used_gib = st.mean([r["vram_used_mib"] for r in rs]) / 1024.0
    ev_bw = used_gib / ev_m if ev_m else 0
    re_bw = used_gib / re_m if re_m else 0
    agg[g] = dict(ev_m=ev_m, ev_s=ev_s, re_m=re_m, re_s=re_s, ev_bw=ev_bw, re_bw=re_bw, init=in_m)
    print(f"| {g:g} | {len(rs)} | {ev_m:.2f}±{ev_s:.2f} | {re_m:.2f}±{re_s:.2f} | {ev_bw:.2f} | {re_bw:.2f} | {in_m:.2f} |")

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(5, 3.2))
    ax.errorbar(vrams, [agg[g]["ev_m"] for g in vrams], yerr=[agg[g]["ev_s"] for g in vrams],
                marker="o", label="evict (yield)")
    ax.errorbar(vrams, [agg[g]["re_m"] for g in vrams], yerr=[agg[g]["re_s"] for g in vrams],
                marker="s", label="restore (resume)")
    ax.set_xlabel("VRAM held (GB)")
    ax.set_ylabel("latency (s)")
    ax.set_title("M1: cuda-checkpoint evict/restore vs VRAM")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_png, dpi=150)
    print(f"\n[figure] {out_png}")
except ImportError:
    print("\n[figure] matplotlib not available — table only")
