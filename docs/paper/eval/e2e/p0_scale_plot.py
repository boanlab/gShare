#!/usr/bin/env python3
"""P0 at scale — why cold-STOP progress loss is *minutes* for large models, and why lossless
yield's advantage grows with model size. All constants measured on gpu2-1 (RTX 4090).

Measured (vit_l_16 + Adam, batch 32): checkpoint = 3.48 GB, disk WRITE ≈ 27 s (write > the
inter-checkpoint gap, so frequent checkpointing is infeasible), reload = 3.62 s, full cold-start
(framework+build+reload) = 12.87 s, 2.7 steps/s. Lossless GShare restore for the working-set VRAM
(~M1: 16 GB → 3.58 s) + 0 redo.

The amortization argument: to keep checkpoint overhead ≤ ρ of training time, the interval must be
≥ write/ρ. write=27 s → ρ=5% ⇒ interval ≥ 9 min; ρ=10% ⇒ ≥ 4.5 min. cold-STOP at a random
preemption redoes ~interval/2 of training + pays cold-start; GShare pays only the lossless restore.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

WRITE = 27.0          # checkpoint write (s), measured
COLD_START = 12.87    # cold restart: framework+build+reload (s), measured
GS_RESTORE = 3.58     # lossless restore for ~16 GB working set (s), M1
ttl_min = [i / 60 for i in range(60, 901, 30)]  # interval 1..15 min

# expected progress lost per preemption (minutes): cold-STOP = interval/2 (redo) + cold-start; GShare = restore.
cold_loss = [(t / 2) + COLD_START / 60 for t in ttl_min]
gs_loss = [GS_RESTORE / 60 for _ in ttl_min]

fig, ax = plt.subplots(figsize=(6.6, 3.7))
ax.plot(ttl_min, cold_loss, marker="s", color="#c62828", label="cold-STOP: redo (interval/2) + cold start")
ax.plot(ttl_min, gs_loss, marker="^", color="#2e7d32", label=f"GShare lossless: exact resume (restore {GS_RESTORE:.1f}s), redo 0")
# feasible-interval band: interval must be ≥ write/ρ for overhead ≤ ρ.
fmin5 = WRITE / 0.05 / 60   # ρ=5% → 9 min
fmin10 = WRITE / 0.10 / 60  # ρ=10% → 4.5 min
ax.axvspan(fmin10, 15, color="#e8f5e9", alpha=0.5)
ax.axvline(fmin5, ls=":", color="gray"); ax.text(fmin5 + 0.1, 4.5, f"feasible interval\n(ckpt write 27s ->\noverhead <=5% => >={fmin5:.0f}min)", fontsize=7, color="gray")
ax.annotate("", xy=(fmin5, cold_loss[ttl_min.index(min(ttl_min, key=lambda t: abs(t-fmin5)))]),
            xytext=(fmin5, gs_loss[0]), arrowprops=dict(arrowstyle="<->", color="#c62828"))
ax.text(fmin5 - 2.0, 2.6, "minutes\nvs seconds", fontsize=8, color="#c62828", ha="center")
ax.set_xlabel("checkpoint interval (min) - larger checkpoints push it out")
ax.set_ylabel("expected progress lost per preemption (min)")
ax.set_title("P0 at scale: vit_l_16+Adam (3.5GB ckpt, 27s write) - cold-STOP loses minutes, GShare seconds")
ax.legend(fontsize=7.5, loc="upper left"); ax.grid(True, alpha=0.3); ax.set_ylim(0, 8)
fig.tight_layout(); fig.savefig("p0_scale.png", dpi=150); print("[figure] p0_scale.png")
