#!/usr/bin/env python3
"""§E.5c host-RAM bound on in-place yield. cuda-checkpoint evicts a resident's VRAM working set into
HOST RAM while the process stays alive; the held host-RAM footprint ≈ the toggled VRAM (the driver
relocates device allocations to host memory ~1:1). So the number of CONCURRENT in-place yields a node
can hold is bounded:

    max_concurrent_yields(node) = floor( host_RAM * YIELD_HOST_RAM_FRACTION / VRAM_per_session )

This is a genuine limit of the approach (unlike cold-STOP, which frees the process and uses no host
RAM): a node cannot yield more cards than its host RAM can stage. We quantify it for the real testbed
(gpu2-*: 64 GiB host RAM, measured `kubectl get node`) and for typical GPU-server RAM configs, at the
deployed fraction 0.5. Honest framing: at small VRAM the bound is loose (many concurrent yields); at
24 GB the testbed holds only 1 — in-place yield trades host RAM for losslessness.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

FRACTION = 0.5                       # YIELD_HOST_RAM_FRACTION (config.py)
VRAM = list(range(2, 25, 1))         # GB per session
HOST_RAM = [(64, "#c62828", "64 GiB (testbed gpu2-*)"),
            (128, "#ef6c00", "128 GiB"),
            (256, "#1565c0", "256 GiB (sim default)"),
            (512, "#2e7d32", "512 GiB")]

fig, ax = plt.subplots(figsize=(6.6, 3.8))
for ram, c, lab in HOST_RAM:
    budget = ram * FRACTION
    ax.plot(VRAM, [int(budget // v) for v in VRAM], marker=".", color=c, label=lab)
ax.axhline(4, ls=":", color="gray", lw=0.8)
ax.text(2.2, 4.3, "4 cards/node (no host-RAM headroom needed above this)", fontsize=7, color="gray")
# mark the testbed worst case
ax.annotate("24 GB on 64 GiB → only 1 concurrent yield", xy=(24, 1), xytext=(13, 6),
            fontsize=7.5, color="#c62828", arrowprops=dict(arrowstyle="->", color="#c62828"))
ax.set_xlabel("resident VRAM working set per session (GB)")
ax.set_ylabel("max concurrent in-place yields / node")
ax.set_title(f"Host-RAM bound on in-place yield (fraction={FRACTION})")
ax.set_yscale("log"); ax.set_ylim(0.8, 200)
ax.legend(fontsize=8); ax.grid(True, which="both", alpha=0.3)
fig.tight_layout(); fig.savefig("hostram_bound.png", dpi=150)
print("[figure] hostram_bound.png")
for ram, _, lab in HOST_RAM:
    b = ram * FRACTION
    print(f"  {lab:28s}: 6GB→{int(b//6):3d}  16GB→{int(b//16):2d}  24GB→{int(b//24):2d} concurrent yields")
