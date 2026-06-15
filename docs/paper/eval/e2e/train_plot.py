#!/usr/bin/env python3
"""P0 (progress-loss) figure — stateful training, lossless GShare vs cold-STOP.

Measured on gpu2-1 (RTX 4090): resnet50 + SGD-momentum, 7.8 steps/s. A burst is preempted at
~step 290; the last durable disk checkpoint was at step 200 (ckpt-every=200). cold-STOP resume
reloads the checkpoint (verified live: `RESUMED from ckpt step=200`) and must REDO steps 200→290
(~90 steps ≈ 11.5 s); lossless GShare yield restores the exact process state (cuda-checkpoint is
bit-exact, see M1) → continues at step 290, 0 redo. Plots global step vs wall-clock to show the
cold-STOP setback ("sawtooth") that GShare avoids.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

SPS = 7.8          # measured steps/sec
KILL = 290         # step at preemption (force-kill, ~last_seen 282 +grace)
CKPT = 200         # last durable disk checkpoint (ckpt-every=200)
GAP = 7.0          # yield/restore round-trip for GShare (~evict+restore, M1 6GB ≈ 7s)
COLD_RESTART = 4.75  # cold pod reschedule+init (E1)

t_kill = KILL / SPS
# GShare: train to kill, brief lossless round-trip (GAP), continue at SAME step.
gx = [0, t_kill, t_kill + GAP, t_kill + GAP + 8]
gy = [0, KILL, KILL, KILL + 8 * SPS]
# cold-STOP: train to kill, cold restart (no progress), reload ckpt (drop to CKPT), re-climb.
cx = [0, t_kill, t_kill + COLD_RESTART, t_kill + COLD_RESTART + (KILL - CKPT) / SPS + 8]
cy = [0, KILL, CKPT, KILL + 8 * SPS]   # drops to CKPT, redoes 200→290, then continues

fig, ax = plt.subplots(figsize=(6.4, 3.6))
ax.plot(gx, gy, marker="o", color="#2e7d32", label="GShare (lossless): exact step resume, redo 0")
ax.plot(cx, cy, marker="s", color="#c62828", label="cold-STOP: rewind to ckpt(200), redo 90 steps")
ax.axhline(CKPT, ls=":", lw=0.8, color="gray"); ax.text(0.3, CKPT + 4, "last disk ckpt=200", fontsize=7, color="gray")
ax.axvspan(t_kill, t_kill + max(GAP, COLD_RESTART), color="#fff3cd", alpha=0.5)
ax.text(t_kill, KILL + 14, "preempt @step 290", fontsize=7, ha="center")
# redo gap annotation
ax.annotate("", xy=(cx[2], CKPT), xytext=(cx[2], KILL), arrowprops=dict(arrowstyle="<->", color="#c62828"))
ax.text(cx[2] + 0.5, (CKPT + KILL) / 2, "lost\n~90 step\n≈11.5 s", fontsize=7, color="#c62828", va="center")
ax.set_xlabel("wall-clock (s)"); ax.set_ylabel("training global step (progress)")
ax.set_title("P0: stateful training - lossless yield vs cold-STOP progress loss (RTX 4090)")
ax.legend(fontsize=7.5, loc="lower right"); ax.grid(True, alpha=0.3)
fig.tight_layout(); fig.savefig("p0_train.png", dpi=150); print("[figure] p0_train.png")
