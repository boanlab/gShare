#!/usr/bin/env python3
"""Trace-driven cluster simulator for §E.5 (bucket ③).

Models a pool of exclusive GPU cards shared between long-lived *resident* sessions
(interactive, bursty: compute bursts separated by idle gaps) and a backlog of
preemptible *spot* jobs that can borrow a card while its resident is idle.

Three reclaim policies, all parameterised by the REAL system constants
(backend/app/core/config.py) and the M1-measured evict/restore cost model:

  keep-idle    : resident holds the card for its whole lifetime; idle time is wasted,
                 spot never runs. (upper bound on waste, the status quo)
  cold-preempt : after IDLE_TIMEOUT the card is cold-STOPped and a spot job runs;
                 resident resume pays a cold-start penalty AND loses the in-flight
                 burst progress (must redo it). lossy.
  gshare       : after IDLE_TIMEOUT the resident yields in-place (evict, host-RAM hold);
                 a spot job borrows the full card; resident reclaim within
                 YIELD_RESERVATION_TTL is lossless (restore cost, no progress lost),
                 beyond TTL it is durably demoted (cold resume). host-RAM budget caps
                 concurrent yields per node.

Discrete-event sim, stdlib only. Deterministic given --seed. Emits per-policy
occupancy / goodput / spot-served / JCT stats as JSON for the plotter.
"""
import argparse
import heapq
import json
import random
import statistics as st

# ── real system constants (backend/app/core/config.py) ──
IDLE_TIMEOUT_SEC = 1800
YIELD_RESERVATION_TTL_SEC = 1800
GRACE_PERIOD_SEC = 600
SPOT_DISCOUNT = 0.3
YIELD_HOST_RAM_FRACTION = 0.5

# ── M1-measured cost model: round-trip = a + b*VRAM_GB (least-squares on micro/m1.csv) ──
# Exact OLS fit on 30 real RTX-4090 samples (2–16 GB): evict R²=0.958, restore R²=0.970.
# These coefficients ARE the fit (see sim_validate.py, which re-derives them from m1.csv and
# asserts agreement); they are not hand-chosen. Extrapolated to 24 GB inside the measured slope.
def evict_s(gb):   return 0.78 + 0.71 * gb           # lossy round-trip out: evict VRAM→host (M1)
def restore_s(gb): return 0.34 + 0.21 * gb           # lossless reclaim: VRAM restore only (M1)
# cold restart = framework init (CUDA ctx + torch import + model build) + checkpoint reload from
# disk into VRAM. Dominates the resident-side cost of cold preemption; lossless yield avoids it.
# Anchored on the MEASURED training cold-start: vit_l_16+Adam cold-start = 12.87 s at ~5 GB VRAM
# working set (ckpt reload 3.62 s of it) → COLD_A≈9.2 s framework, COLD_B≈0.73 s/GB reload.
# (The unmeasured 20+3/GB guess overstated cold cost 3×, inflating GShare's edge — corrected.)
# Swept ±in S3 (--cold-a/--cold-b) to bound residual uncertainty.
COLD_A, COLD_B = 9.2, 0.73
def cold_s(gb):    return COLD_A + COLD_B * gb


class Card:
    __slots__ = ("idx", "node", "busy_useful", "busy_overhead", "spot_useful")
    def __init__(self, idx, node):
        self.idx = idx; self.node = node
        self.busy_useful = 0.0      # resident useful GPU-seconds
        self.busy_overhead = 0.0    # evict/restore/cold overhead GPU-seconds
        self.spot_useful = 0.0      # spot useful GPU-seconds (only reclaimed by gshare/cold)


def gen_trace(n_cards, load, horizon, seed):
    """Synthetic interactive-resident trace + spot backlog.

    Each card hosts one resident session for the whole horizon. A resident alternates
    compute bursts ~Exp(mean burst) with idle gaps ~Exp(mean gap). `load` scales the
    burst duty cycle: load=duty fraction of time the resident actually wants the GPU.
    Spot backlog: a queue of fixed-size jobs (duration ~Exp) large enough to absorb
    all reclaimable idle. Model size (VRAM) drawn from a realistic mix.
    """
    rnd = random.Random(seed)
    vram_mix = [6, 8, 12, 16, 24]            # GB; interactive/training mix
    residents = []
    for c in range(n_cards):
        gb = rnd.choice(vram_mix)
        # duty cycle = load: burst/(burst+gap). fix mean burst=300s, derive gap.
        burst = 300.0
        gap = burst * (1 - load) / max(load, 1e-3)
        # build burst/idle segments across horizon
        segs = []
        t = 0.0
        while t < horizon:
            b = rnd.expovariate(1 / burst)
            segs.append(("burst", t, min(b, horizon - t))); t += b
            if t >= horizon: break
            g = rnd.expovariate(1 / gap)
            segs.append(("idle", t, min(g, horizon - t))); t += g
        residents.append({"card": c, "gb": gb, "segs": segs})
    # spot backlog: enough jobs to fill reclaimable idle; each 60–600s
    spot = [{"dur": rnd.uniform(60, 600), "gb": rnd.choice(vram_mix)} for _ in range(n_cards * 40)]
    return residents, spot


def simulate(policy, residents, spot_backlog, n_cards, node_ram_gb, horizon):
    cards = [Card(c, node=c // 4) for c in range(n_cards)]  # 4 cards/node
    # node host-RAM budget for concurrent yields (gshare only)
    node_ram_used = {}
    spot_iter = iter(spot_backlog)
    spot_jcts = []          # completion times of spot jobs that ran
    resident_extra = []     # per-resident added latency (resume penalties) — JCT inflation
    spot_served = 0
    idle_reclaim_total = 0.0  # reclaimable idle GPU-time (workload property; same across policies)
    demotes = 0               # gshare yields that exceeded TTL → durable demote (card+priority returned)

    for r in residents:
        card = cards[r["card"]]
        gb = r["gb"]
        node = card.node
        extra = 0.0
        for kind, start, dur in r["segs"]:
            if kind == "burst":
                card.busy_useful += dur
                continue
            # idle segment of length `dur`
            if dur <= IDLE_TIMEOUT_SEC:
                continue                          # short idle: never reclaimed by any policy
            reclaimable = dur - IDLE_TIMEOUT_SEC
            idle_reclaim_total += reclaimable     # economic: this idle GPU-time is reclaimable
            if policy == "keep-idle":
                continue                          # resident holds card → billed for idle (waste, see econ)
            if policy == "cold-preempt":
                # cold STOP: spot can use the reclaimable window; resident resume = cold-start
                # AND loses progress of the burst it was mid-way (model as redo of next burst's ramp)
                served = _fill_spot(spot_iter, reclaimable, card)
                if served > 0:
                    extra += cold_s(gb)              # resident pays cold resume
                    card.busy_overhead += cold_s(gb)
                    spot_served += served
            elif policy == "gshare":
                # host-RAM gate: can we hold this yield?
                budget = node_ram_gb * YIELD_HOST_RAM_FRACTION
                if node_ram_used.get(node, 0) + gb > budget:
                    continue                          # RAM pressure → cannot yield, idle wasted
                # demand-driven: only yield if a borrower will actually use the card (window>0).
                window = reclaimable - evict_s(gb) - restore_s(gb)
                if window <= 0:
                    continue
                before = card.spot_useful
                node_ram_used[node] = node_ram_used.get(node, 0) + gb
                served = _fill_spot(spot_iter, window, card)
                node_ram_used[node] -= gb
                if card.spot_useful == before:        # no borrower ran → no actual yield
                    continue
                spot_served += served
                card.busy_overhead += evict_s(gb)     # round-trip charged only when yield happened
                # reclaim: within TTL → lossless restore (no progress lost); else cold demote
                if reclaimable <= YIELD_RESERVATION_TTL_SEC:
                    card.busy_overhead += restore_s(gb); extra += restore_s(gb)
                else:
                    card.busy_overhead += cold_s(gb); extra += cold_s(gb)
                    demotes += 1                  # past TTL → durable demote
        resident_extra.append(extra)

    gpu_seconds = n_cards * horizon
    resident_useful = sum(c.busy_useful for c in cards)
    spot_useful = sum(c.spot_useful for c in cards)
    overhead = sum(c.busy_overhead for c in cards)
    occupancy = (resident_useful + spot_useful) / gpu_seconds
    goodput = resident_useful + spot_useful
    # ── economic model (C3), utilization-based (no pricing): the resident is billed only for
    # active GPU-time (yield/stop stops billing). keep-idle bills the reclaimable idle for which
    # NO useful work is done (paid-but-idle waste) and recovers 0 work; GShare/cold eliminate that
    # billed-idle and recover useful spot GPU-time on the freed card. The time-limited resume
    # reservation (TTL) bounds the hold: yields exceeding TTL are demoted (card+priority returned).
    resident_idle_billed_h = round(idle_reclaim_total / 3600, 2) if policy == "keep-idle" else 0.0
    return {
        "policy": policy,
        "occupancy": round(occupancy, 4),
        "resident_useful_gpu_h": round(resident_useful / 3600, 2),
        "spot_useful_gpu_h": round(spot_useful / 3600, 2),
        "overhead_gpu_h": round(overhead / 3600, 3),
        "spot_jobs_served": spot_served,
        "resident_resume_penalty_s_mean": round(st.mean(resident_extra) if resident_extra else 0, 2),
        "resident_resume_penalty_s_total": round(sum(resident_extra), 1),
        # economic (C3) — utilization, no pricing
        "idle_reclaimable_h": round(idle_reclaim_total / 3600, 2),
        "resident_idle_billed_h": resident_idle_billed_h,   # GPU-h resident pays for idle (waste); GShare=0
        "spot_useful_recovered_h": 0.0 if policy == "keep-idle" else round(spot_useful / 3600, 2),
        "demotes": demotes,                                 # TTL-bounded reservation: yields demoted past TTL
    }


def _fill_spot(spot_iter, window, card):
    """Pack spot jobs into a reclaimed window of `window` seconds. Returns #jobs served."""
    served = 0; t = 0.0
    while t < window:
        try:
            job = next(spot_iter)
        except StopIteration:
            break
        run = min(job["dur"], window - t)
        card.spot_useful += run
        t += run
        if run >= job["dur"]:
            served += 1
        else:
            break  # window exhausted mid-job
    return served


def main():
    global COLD_A, COLD_B, YIELD_RESERVATION_TTL_SEC
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", type=int, default=16)
    ap.add_argument("--node-ram-gb", type=float, default=256.0)
    ap.add_argument("--horizon", type=float, default=24 * 3600)
    ap.add_argument("--seed", type=int, default=20260620)
    ap.add_argument("--loads", default="0.1,0.2,0.3,0.4,0.5,0.65,0.8")
    ap.add_argument("--cold-a", type=float, default=COLD_A)
    ap.add_argument("--cold-b", type=float, default=COLD_B)
    ap.add_argument("--ttl", type=float, default=YIELD_RESERVATION_TTL_SEC)  # C3 resume-reservation TTL
    ap.add_argument("--out", default="sim_results.json")
    args = ap.parse_args()
    COLD_A, COLD_B = args.cold_a, args.cold_b
    YIELD_RESERVATION_TTL_SEC = args.ttl

    loads = [float(x) for x in args.loads.split(",")]
    out = {"params": {"cards": args.cards, "node_ram_gb": args.node_ram_gb,
                       "horizon_h": args.horizon / 3600, "seed": args.seed,
                       "IDLE_TIMEOUT_SEC": IDLE_TIMEOUT_SEC,
                       "YIELD_RESERVATION_TTL_SEC": YIELD_RESERVATION_TTL_SEC,
                       "YIELD_HOST_RAM_FRACTION": YIELD_HOST_RAM_FRACTION,
                       "cold_a": COLD_A, "cold_b": COLD_B},
           "by_load": []}
    for load in loads:
        residents, spot = gen_trace(args.cards, load, args.horizon, args.seed)
        row = {"load": load, "policies": {}}
        for pol in ("keep-idle", "cold-preempt", "gshare"):
            # regenerate trace per policy so all see identical workload
            residents, spot = gen_trace(args.cards, load, args.horizon, args.seed)
            row["policies"][pol] = simulate(pol, residents, spot, args.cards,
                                            args.node_ram_gb, args.horizon)
        out["by_load"].append(row)
    with open(args.out, "w") as f:
        json.dump(out, f, indent=2)
    # console summary
    print(f"# cluster sim: {args.cards} cards, {args.horizon/3600:.0f}h, seed={args.seed}")
    print(f"{'load':>5} | {'keep-idle':>10} {'cold-preempt':>12} {'gshare':>8}   (occupancy%)")
    for row in out["by_load"]:
        p = row["policies"]
        print(f"{row['load']:>5.2f} | {p['keep-idle']['occupancy']*100:>9.1f}% "
              f"{p['cold-preempt']['occupancy']*100:>11.1f}% {p['gshare']['occupancy']*100:>7.1f}%")
    print(f"\n[json] {args.out}")


if __name__ == "__main__":
    main()
