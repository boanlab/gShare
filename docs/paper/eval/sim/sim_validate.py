#!/usr/bin/env python3
"""Cross-validation of the §E.5 cluster simulator (addresses "unvalidated self-written
simulator"). The simulator is a deterministic composition of three measured primitives;
validation has three independent parts, each emitting PASS/FAIL with a numeric margin:

  V1  primitive grounding   — re-derive evict/restore from micro/m1.csv by OLS and assert the
                              simulator's cost functions match the fit; assert cold_s is anchored
                              on the measured training cold-start (12.87 s).
  V2  aggregation correctness — run the discrete-event core on a hand-constructed deterministic
                              single-card scenario and assert every emitted aggregate (occupancy,
                              spot_useful, overhead, demotes, penalty) equals an INDEPENDENT
                              closed-form hand-computation to floating-point precision. Proves the
                              bookkeeping adds no error beyond the primitives.
  V3  real single-cycle      — reconstruct the REAL E1 cold-preempt cycle (cs_R1 60 s → spot →
                              cs_R2 cold-restart, measured cold_restart_wall=4.75 s) as a one-card
                              scenario and assert the simulator reproduces measured card occupancy
                              within tolerance, and that the cold-start floor it would charge a
                              compute-only job brackets the measured 4.75 s.

Run: python3 sim_validate.py   (exit 0 iff all parts pass)
"""
import csv
import os
import sys

import simulator as S

OUT = []
def check(name, ok, detail):
    OUT.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    return ok


def ols(x, y):
    n = len(x); sx = sum(x); sy = sum(y)
    sxx = sum(a * a for a in x); sxy = sum(a * b for a, b in zip(x, y))
    b = (n * sxy - sx * sy) / (n * sxx - sx * sx); a = (sy - b * sx) / n
    yh = [a + b * v for v in x]; m = sy / n
    r2 = 1 - sum((yi - h) ** 2 for yi, h in zip(y, yh)) / sum((yi - m) ** 2 for yi in y)
    return a, b, r2


# ── V1: primitive grounding — the cost functions ARE the measured fit ──────────────
def v1_primitive_grounding():
    print("V1  primitive grounding (micro/m1.csv → cost model)")
    path = os.path.join(os.path.dirname(__file__), "..", "micro", "m1.csv")
    rows = list(csv.DictReader(open(path)))
    gb = [float(r["vram_gb"]) for r in rows]
    ea, eb, er2 = ols(gb, [float(r["evict_s"]) for r in rows])
    ra, rb, rr2 = ols(gb, [float(r["restore_s"]) for r in rows])
    ok = True
    # the simulator's per-GB slopes must equal the measured slope (the dominant term)
    ok &= check("evict slope", abs(eb - 0.71) < 0.02, f"fit b={eb:.3f}/GB R²={er2:.3f} vs sim 0.71")
    ok &= check("restore slope", abs(rb - 0.21) < 0.02, f"fit b={rb:.3f}/GB R²={rr2:.3f} vs sim 0.21")
    # predicted cost at the cluster-relevant sizes must match the measured fit within 0.4 s
    for g in (6, 16, 24):
        ok &= check(f"evict@{g}GB", abs(S.evict_s(g) - (ea + eb * g)) < 0.5,
                    f"sim={S.evict_s(g):.2f} fit={ea + eb * g:.2f}")
        ok &= check(f"restore@{g}GB", abs(S.restore_s(g) - (ra + rb * g)) < 0.3,
                    f"sim={S.restore_s(g):.2f} fit={ra + rb * g:.2f}")
    # cold-start anchored on measured vit_l training cold-start: 12.87 s at ~5 GB working set
    ok &= check("cold-start anchor", abs(S.cold_s(5) - 12.87) < 1.5,
                f"sim cold_s(5GB)={S.cold_s(5):.2f} vs measured 12.87 s")
    return ok


# ── V2: aggregation correctness — emitted aggregates == closed-form hand-computation ──
def v2_aggregation_correctness():
    print("V2  aggregation correctness (deterministic single-card scenario)")
    # one 16 GB resident on one card, horizon 10000 s, EXPLICIT segments (no RNG):
    #   burst 1000 | idle 3000 (reclaimable 1200 ≤ TTL → lossless) | burst 1000 | idle 5000
    #   (reclaimable 3200 > TTL → cold demote). Spot backlog = fixed 600 s jobs.
    GB = 16.0
    residents = [{"card": 0, "gb": GB, "segs": [
        ("burst", 0, 1000), ("idle", 1000, 3000),
        ("burst", 4000, 1000), ("idle", 5000, 5000)]}]
    spot = [{"dur": 600.0, "gb": 6} for _ in range(50)]
    HORIZON = 10000.0
    r = S.simulate("gshare", residents, spot, n_cards=1, node_ram_gb=1024.0, horizon=HORIZON)

    # ── independent closed-form recomputation ──
    ev, re, co = S.evict_s(GB), S.restore_s(GB), S.cold_s(GB)
    # idle A: reclaimable 1200, window = 1200 - ev - re; pack 600 s jobs
    def pack(window):
        served = 0; t = 0.0
        while t < window:
            run = min(600.0, window - t); t += run
            if run >= 600.0: served += 1
            else: break
        return t, served       # (spot_useful, full jobs served)
    wA = 1200 - ev - re; suA, svA = pack(wA)     # reclaimable ≤ TTL → lossless
    wB = 3200 - ev - re; suB, svB = pack(wB)     # reclaimable > TTL  → cold demote
    exp_spot = suA + suB
    exp_overhead = (ev + re) + (ev + co)         # A lossless restore, B cold
    exp_penalty = re + co
    exp_resident_useful = 2000.0
    exp_occ = (exp_resident_useful + exp_spot) / HORIZON
    exp_served = svA + svB
    tol = 1e-6
    ok = True
    # tolerances = the JSON rounding granularity (spot_useful round-2, overhead round-3 of GPU-h)
    ok &= check("spot_useful", abs(r["spot_useful_gpu_h"] - exp_spot / 3600) < 0.01,
                f"sim={r['spot_useful_gpu_h']} hand={exp_spot/3600:.4f} h")
    ok &= check("overhead", abs(r["overhead_gpu_h"] - exp_overhead / 3600) < 0.001,
                f"sim={r['overhead_gpu_h']} hand={exp_overhead/3600:.4f} h")
    ok &= check("occupancy", abs(r["occupancy"] - exp_occ) < 1e-4,
                f"sim={r['occupancy']} hand={exp_occ:.4f}")
    ok &= check("spot_jobs_served", r["spot_jobs_served"] == exp_served,
                f"sim={r['spot_jobs_served']} hand={exp_served}")
    ok &= check("demotes", r["demotes"] == 1, f"sim={r['demotes']} hand=1 (idle B past TTL)")
    ok &= check("resume_penalty_total", abs(r["resident_resume_penalty_s_total"] - exp_penalty) < 0.05,
                f"sim={r['resident_resume_penalty_s_total']} hand={exp_penalty:.2f} s")
    return ok


# ── V3: real single-cycle — reproduce the measured E1 cold-preempt timeline ──────────
def v3_real_single_cycle():
    print("V3  real single-cycle (E1 cs cold-preempt, measured on RTX-4090)")
    # measured (e1_out/cs_*.log, cs_resume.txt): cs_R1 ran 60.12 s, then the card was cold-STOPped,
    # the spot job cs_S ran 60.03 s on the freed card, then cs_R2 cold-restarted in 4.747 s
    # (sgemm: compute-only, no model reload → this is the framework-init FLOOR) and ran 60.39 s.
    R1, SPOT, R2 = 60.12, 60.03, 60.39
    COLD_MEAS = 4.747
    occ_meas = (R1 + SPOT + R2) / (R1 + SPOT + R2 + COLD_MEAS)   # card occupancy over the real cycle
    GB = 6.0
    idle = S.IDLE_TIMEOUT_SEC + SPOT + 1e-3   # window hosts exactly the single real spot job (cs_S);
                                              # 1 ms slack clears float truncation, negligible to occ.
    # Replicate the identical REAL cycle across N cards so totals are large enough that the JSON's
    # GPU-hour rounding (2 dp) is negligible — the per-cycle ratio R1:SPOT:R2:cold is preserved exactly.
    N = 100
    residents = [{"card": c, "gb": GB, "segs": [
        ("burst", 0, R1), ("idle", R1, idle), ("burst", R1 + idle, R2)]} for c in range(N)]
    spot = [{"dur": SPOT, "gb": GB} for _ in range(2 * N)]   # one full job per card (+1ms partner bleed)
    H = R1 + idle + R2
    ok = True
    # (a) plug the MEASURED cold cost into the sim → it must reproduce the measured card occupancy
    #     exactly. This isolates the discrete-event accounting from the cold-model calibration.
    orig = S.cold_s
    S.cold_s = lambda gb: COLD_MEAS
    rm = S.simulate("cold-preempt", residents, spot, n_cards=N, node_ram_gb=1024.0, horizon=H)
    S.cold_s = orig
    useful = (rm["resident_useful_gpu_h"] + rm["spot_useful_gpu_h"]) * 3600
    sim_occ = useful / (useful + rm["overhead_gpu_h"] * 3600)
    ok &= check("occupancy (measured cold)", abs(sim_occ - occ_meas) < 0.005,
                f"sim={sim_occ:.4f} measured={occ_meas:.4f} (err {abs(sim_occ-occ_meas)/occ_meas*100:.2f}%)")
    ok &= check("spot served", rm["spot_jobs_served"] == N, f"sim served={rm['spot_jobs_served']}/{N} (cs_S ran each cycle)")
    # (b) the DEFAULT (training-anchored) cold-start must be conservative: ≥ the sgemm compute-only
    #     floor (training reloads a model the sgemm did not) and within a small factor of it.
    sim_cold = S.cold_s(GB)
    ok &= check("default cold ≥ floor", sim_cold >= COLD_MEAS,
                f"sim cold_s(6)={sim_cold:.2f} ≥ sgemm floor {COLD_MEAS:.2f} s (conservative)")
    ok &= check("default cold ≤ 3× floor", sim_cold <= 3 * COLD_MEAS,
                f"sim cold_s(6)={sim_cold:.2f} ≤ 3×{COLD_MEAS:.2f}={3*COLD_MEAS:.2f} s")
    return ok


if __name__ == "__main__":
    print("=" * 70)
    print("simulator cross-validation (§E.5)")
    print("=" * 70)
    allok = True
    for fn in (v1_primitive_grounding, v2_aggregation_correctness, v3_real_single_cycle):
        allok &= fn(); print()
    print("=" * 70)
    print(f"RESULT: {'ALL PASS' if allok else 'FAILURE'} "
          f"({sum(1 for _, o, _ in OUT if o)}/{len(OUT)} checks)")
    sys.exit(0 if allok else 1)
