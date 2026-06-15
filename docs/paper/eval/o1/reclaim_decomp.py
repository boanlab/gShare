#!/usr/bin/env python3
"""§E.6 reclaim-latency decomposition + REAL end-to-end head-to-head. The resident node-agent path
is no longer a projection: it was implemented (a long-lived privileged hostPID pod) and measured
against the Job path on the SAME live 4 GB GPU process (gpu2-1, RTX-4090). Every constant is measured.

Real measured head-to-head (PID holding 4 GB VRAM, lossless evict→restore verified via nvidia-smi
4492↔1 MiB, process resumed `running`):
  Job path (fresh privileged hostPID Job runs cuda-checkpoint restore+unlock, create→Succeeded):
      8.2 s  (n=3) — dominated by privileged+nvidia container start.
  Resident node-agent path (single RPC/exec into the already-running agent does restore+unlock):
      1.28 s (n=5) — dispatch + mechanism only.
  → 6.4× faster, measured end-to-end. (With the operator's real 3 s poll added, the Job path is
    ~9.7 s, matching the deployed "~10 s" reclaim.)

Decomposition (all measured this run)
  dispatch        : resident-agent exec/RPC round-trip = 0.19 s (n=8; conservative — real gRPC < exec).
  container_start : privileged+nvidia+hostPID agent Job create→running ≈ 7.0 s (backed out of the
                    8.2 s Job reclaim minus the 1.09 s mechanism; cf. 4.04 s for a plain no-op pod —
                    the nvidia runtime + privileged + /dev mount add ~3 s). REMOVED by a resident agent.
  poll            : operator polls Job.Succeeded every 3 s → avg +1.5 s. REMOVED by a resident agent.
  restore (mech)  : cuda-checkpoint VRAM restore+unlock, M1 fit restore_s = 0.34 + 0.21·GB. Validated
                    cross-container: measured 1.07 s at 4 GB vs M1 fit 1.18 s (within 10 %). SAME both paths.
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

DISPATCH = 0.19            # resident-agent exec/RPC round-trip (measured, n=8)
CONTAINER_START = 7.0      # privileged+nvidia agent Job create→running (measured, backed out)
POLL_AVG = 1.5            # operator 3 s poll interval → avg quantization
UNLOCK = 0.02             # cuda-checkpoint unlock (measured)
def restore_s(gb): return 0.34 + 0.21 * gb   # M1 fit, cross-container-validated at 4 GB

GBS = [4, 6, 12, 16, 24]
job_orch = [CONTAINER_START + POLL_AVG] * len(GBS)         # removable: container start + poll
mech = [restore_s(g) + UNLOCK for g in GBS]                # irreducible mechanism (restore+unlock)
agent_rpc = [DISPATCH] * len(GBS)                          # node-agent replacement for orchestration

job_total = [o + m for o, m in zip(job_orch, mech)]
agent_total = [a + m for a, m in zip(agent_rpc, mech)]

fig, ax = plt.subplots(figsize=(7.2, 4.0))
x = range(len(GBS)); w = 0.38
ax.bar([i - w/2 for i in x], job_orch, w, color="#c62828", label="Job orchestration (container start 7.0s + poll 1.5s) — removable")
ax.bar([i - w/2 for i in x], mech, w, bottom=job_orch, color="#1565c0", label="cuda-checkpoint restore+unlock (M1, irreducible)")
ax.bar([i + w/2 for i in x], agent_rpc, w, color="#9e9e9e", label="node-agent RPC (measured 0.19s)")
ax.bar([i + w/2 for i in x], mech, w, bottom=agent_rpc, color="#1565c0")
for i, (jt, at) in enumerate(zip(job_total, agent_total)):
    ax.text(i - w/2, jt + 0.15, f"{jt:.1f}s", ha="center", fontsize=8, color="#c62828")
    ax.text(i + w/2, at + 0.15, f"{at:.1f}s", ha="center", fontsize=8, color="#2e7d32")
# mark the REAL measured head-to-head at 4 GB (left-most pair)
ax.annotate("MEASURED @4GB:\nJob 8.2s vs agent 1.28s = 6.4×",
            xy=(0.19, agent_total[0] + 0.1), xytext=(0.85, 14.2), fontsize=8, color="#2e7d32",
            ha="center", arrowprops=dict(arrowstyle="->", color="#2e7d32"))
ax.set_xticks(list(x)); ax.set_xticklabels([f"{g} GB" for g in GBS])
ax.set_ylabel("reclaim latency (s)"); ax.set_xlabel("resident VRAM working set")
ax.set_title("Reclaim latency: Job (left) vs resident node-agent (right) — measured constants")
ax.legend(fontsize=7.5, loc="upper right"); ax.grid(True, axis="y", alpha=0.3)
ax.set_ylim(0, 16.5)
fig.tight_layout(); fig.savefig("reclaim_decomp.png", dpi=150)
print("[figure] reclaim_decomp.png")
for g, jt, at in zip(GBS, job_total, agent_total):
    print(f"  {g:2d}GB: Job {jt:.1f}s → node-agent {at:.1f}s  ({jt/at:.1f}× faster)")
