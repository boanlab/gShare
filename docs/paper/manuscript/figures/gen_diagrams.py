#!/usr/bin/env python3
"""Generate the two schematic figures for the manuscript: hierarchy.png (Fig C, signature) and
arch.png (Fig B, system architecture). English labels only (no font-glyph issues)."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# ---------------------------------------------------------------- Fig C: occupancy memory hierarchy
def hierarchy():
    fig, ax = plt.subplots(figsize=(7.6, 5.2)); ax.set_xlim(0, 10); ax.set_ylim(0, 10); ax.axis("off")
    tiers = [  # (y, title, medium, prop, color)
        (8.2, "T0  VRAM", "executing on the physical card", "occupies the card", "#1565c0"),
        (6.0, "T1  host RAM", "in-place yield (cuda-checkpoint, pod alive)", "lossless • fast", "#2e7d32"),
        (3.8, "T2  disk / NVMe", "OS swap of pageable evicted buffer (pod alive)", "lossless • capacity", "#ef6c00"),
        (1.6, "T3  cold / durable", "app / container checkpoint to disk", "durable • survives node loss", "#6a1b9a"),
    ]
    for y, t, m, p, c in tiers:
        ax.add_patch(FancyBboxPatch((1.4, y-0.62), 5.4, 1.24, boxstyle="round,pad=0.04,rounding_size=0.12",
                                    linewidth=1.6, edgecolor=c, facecolor=c+"15"))
        ax.text(1.7, y+0.26, t, fontsize=12, fontweight="bold", color=c, va="center")
        ax.text(1.7, y-0.12, m, fontsize=8.2, color="#333", va="center")
        ax.text(6.6, y-0.40, p, fontsize=7.4, color=c, va="center", ha="right", style="italic")
    # transition arrows + measured costs (between adjacent tiers)
    costs = [(8.2, 6.0, "evict  ↓", "↑  restore 1.28 s/4GB", "lossless, pod alive"),
             (6.0, 3.8, "spill 6.34 s  ↓", "↑  restore 19.77 s/4GB", "OS swap, ~15× T1"),
             (3.8, 1.6, "demote  ↓", "↑  cold restart", "durable fallback")]
    for yt, yb, down, up, note in costs:
        ax.annotate("", xy=(4.6, yb+0.66), xytext=(4.6, yt-0.66), arrowprops=dict(arrowstyle="-|>", color="#c62828", lw=1.6))
        ax.annotate("", xy=(3.6, yt-0.66), xytext=(3.6, yb+0.66), arrowprops=dict(arrowstyle="-|>", color="#2e7d32", lw=1.6))
        ym = (yt+yb)/2
        ax.text(6.95, ym+0.16, up, fontsize=7.3, color="#2e7d32", va="center")
        ax.text(6.95, ym-0.16, down+"   ("+note+")", fontsize=7.3, color="#c62828", va="center")
    # demote/promote legend + capacity/speed axis
    ax.text(4.1, 9.45, "demote / promote = inter-tier paging policy", fontsize=9, ha="center", fontweight="bold", color="#333")
    ax.text(4.1, 9.05, "(red = demote/evict, green = promote/restore; costs measured on RTX-4090)", fontsize=7.2, ha="center", color="#666")
    ax.annotate("", xy=(9.1, 1.0), xytext=(9.1, 8.8), arrowprops=dict(arrowstyle="-|>", color="#888", lw=1.4))
    ax.text(9.35, 5.0, "capacity ↑,  speed ↓", fontsize=8.5, color="#888", rotation=90, va="center")
    fig.tight_layout(); fig.savefig("hierarchy.png", dpi=160, bbox_inches="tight"); print("[fig] hierarchy.png")

# ---------------------------------------------------------------- Fig B: system architecture
def arch():
    fig, ax = plt.subplots(figsize=(8.4, 4.8)); ax.set_xlim(0, 12); ax.set_ylim(0, 9); ax.axis("off")
    def box(x, y, w, h, title, lines, color):
        ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.04,rounding_size=0.12",
                                    linewidth=1.6, edgecolor=color, facecolor=color+"12"))
        ax.text(x+w/2, y+h-0.34, title, fontsize=10.5, fontweight="bold", color=color, ha="center")
        for i, ln in enumerate(lines):
            ax.text(x+w/2, y+h-0.78-0.42*i, ln, fontsize=7.8, color="#333", ha="center")
    # control plane (top), execution plane (bottom)
    box(0.4, 6.0, 5.2, 2.6, "Control plane (external)",
        ["ledger (single source of truth):", "placement • billing • lending state", "grace enforcer: yield / TTL / demote"], "#1565c0")
    box(6.4, 6.0, 5.2, 2.6, "API server",
        ["GShareSession CR", "admission webhook (lend-guard):", "reject non-yielded / already-lent pin"], "#6a1b9a")
    box(0.4, 1.0, 5.2, 3.2, "Operator (in-cluster)",
        ["reconcile CR → pod/service/secret", "idle reaper → trigger yield", "checkpointer (durable T3)", "borrow placement (device-plugin bypass)"], "#2e7d32")
    box(6.4, 1.0, 5.2, 3.2, "GPU node",
        ["node agent: cuda-checkpoint", "  (T0↔T1 evict/restore)", "resident pod (alive while yielded)", "spot pod (borrows freed card)"], "#ef6c00")
    # arrows
    def arr(x1,y1,x2,y2,label="",c="#555",off=0.15):
        ax.add_patch(FancyArrowPatch((x1,y1),(x2,y2), arrowstyle="-|>", mutation_scale=13, lw=1.3, color=c))
        if label: ax.text((x1+x2)/2, (y1+y2)/2+off, label, fontsize=6.8, color=c, ha="center")
    arr(3.0, 6.0, 3.0, 4.2, "decisions / phase reports", "#1565c0")
    arr(9.0, 6.0, 9.0, 4.2, "CR create / deny", "#6a1b9a")
    arr(5.6, 2.6, 6.4, 2.6, "create pods,\nrun agent", "#2e7d32", 0.25)
    arr(5.6, 7.3, 6.4, 7.3, "watch CR", "#1565c0")
    ax.text(6.0, 8.75, "GShare on a Kubernetes GPU stack (K8s 1.36 + HAMi, RTX-4090)", fontsize=9.5, ha="center", fontweight="bold", color="#333")
    fig.tight_layout(); fig.savefig("arch.png", dpi=160, bbox_inches="tight"); print("[fig] arch.png")

# ---------------------------------------------------------------- Fig D: occupancy state machine
def statemachine():
    fig, ax = plt.subplots(figsize=(8.6, 5.0)); ax.set_xlim(0, 12); ax.set_ylim(0, 7.6); ax.axis("off")
    nodes = {  # name: (x, y, label, color)
        "active":  (1.5, 5.0, "active\n(T0, VRAM)", "#1565c0"),
        "yielded": (5.4, 5.0, "yielded\n(T1/T2, reclaimable)", "#2e7d32"),
        "lent":    (9.4, 5.0, "lent\n(spot on card)", "#ef6c00"),
        "demoted": (5.4, 1.5, "demoted\n(T3, cold)", "#6a1b9a"),
    }
    r=1.05
    for n,(x,y,lab,c) in nodes.items():
        ax.add_patch(FancyBboxPatch((x-r, y-0.55), 2*r, 1.1, boxstyle="round,pad=0.03,rounding_size=0.18",
                                    linewidth=1.8, edgecolor=c, facecolor=c+"15"))
        ax.text(x, y, lab, fontsize=8.6, fontweight="bold", color=c, ha="center", va="center")
    def edge(a,b,label,rad=0.0,c="#444",lx=0,ly=0,fs=7.2):
        xa,ya=nodes[a][0],nodes[a][1]; xb,yb=nodes[b][0],nodes[b][1]
        ax.add_patch(FancyArrowPatch((xa,ya),(xb,yb), arrowstyle="-|>", mutation_scale=14, lw=1.4, color=c,
                     connectionstyle=f"arc3,rad={rad}", shrinkA=42, shrinkB=42))
        ax.text((xa+xb)/2+lx,(ya+yb)/2+ly,label,fontsize=fs,color=c,ha="center",va="center")
    edge("active","yielded","idle / credit-exhaust\n→ in-place yield (T0→T1)", rad=-0.22, c="#1565c0", ly=0.95)
    edge("yielded","lent","spot borrows\nfreed card", rad=-0.20, c="#ef6c00", ly=0.85)
    edge("lent","active","resident returns: preempt spot + lossless restore (T1→T0)", rad=0.45, c="#2e7d32", ly=1.85)
    edge("yielded","active","resident returns\n(lossless)", rad=-0.28, c="#2e7d32", ly=-0.85)
    edge("yielded","demoted","TTL expiry /\nhost-RAM pressure", rad=0.0, c="#6a1b9a", lx=-1.55)
    edge("lent","demoted","TTL / node loss\n→ durable demote", rad=0.12, c="#6a1b9a", lx=0.9, ly=0.55)
    edge("demoted","active","cold restart\n(progress preserved)", rad=0.30, c="#6a1b9a", lx=-0.3, ly=-0.5)
    fig.tight_layout(); fig.savefig("statemachine.png", dpi=160, bbox_inches="tight"); print("[fig] statemachine.png")

hierarchy(); arch(); statemachine()
