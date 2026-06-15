#!/usr/bin/env python3
"""Seed the GPU offerings, a demo wallet, and a per-user policy. Rates are proportional, anchored on
the PRO 6000.

Usage:
  GSHARE_ADMIN_TOKEN=<super_admin access token> \
  GSHARE_API=http://10.10.0.196:30080/api/v1 \
  python3 hack/seed_resources.py

For the token: sign in to the console as a super_admin, open the browser's network inspector, and
copy the `Authorization: Bearer <...>` value from the request headers of any /api call.

What it does, all of it idempotent:
  1. Creates GPU offerings as card (4090, 5090, PRO 5000, PRO 6000, A100, H100) times fractional
     flavor (a quarter, a half, the whole card): VRAM is the card times the fraction, cores are
     100 times the fraction (which keeps them balanced), with matching cpu, mem, and disk. The rate is
     the full-card rate, since occupancy scales it down at runtime. Adds two free CPU flavors.
  2. Marks any other existing GPU offering — end-to-end test leftovers and the like — inactive.
  3. Adjusts the demo wallet to 10,000 credits.
  4. Seeds a user-scoped resource policy for the caller: max_concurrent 2, idle 30 minutes, runtime
     24 hours.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("GSHARE_API", "http://10.10.0.196:30080/api/v1").rstrip("/")
TOKEN = os.environ.get("GSHARE_ADMIN_TOKEN", "").strip()
if not TOKEN:
    sys.exit("GSHARE_ADMIN_TOKEN is required: a super_admin access token.")

# The card catalogue: model, full-card VRAM in MB, and the full-card rate in credits per hour. Adding
# a card to your fleet means extending this list and nothing else.
# `model` is the nvidia-smi product name, which the scheduler matches against GpuDevice.model, and
# the VRAM is the card's nominal full capacity.
CARDS = [
    {"label": "RTX 4090 (24GB)",     "model": "NVIDIA GeForce RTX 4090",       "vram_mb": 24564, "credit": 40},
    {"label": "RTX 5090 (32GB)",     "model": "NVIDIA GeForce RTX 5090",       "vram_mb": 32768, "credit": 55},
    {"label": "RTX PRO 5000 (48GB)", "model": "NVIDIA RTX PRO 5000 Blackwell", "vram_mb": 49152, "credit": 65},
    {"label": "RTX PRO 6000 (96GB)", "model": "NVIDIA RTX PRO 6000 Blackwell", "vram_mb": 98304, "credit": 100},
    {"label": "A100 40GB (PCIe)",    "model": "NVIDIA A100-PCIE-40GB",         "vram_mb": 40960, "credit": 120},
    {"label": "A100 80GB (PCIe)",    "model": "NVIDIA A100 80GB PCIe",         "vram_mb": 81920, "credit": 150},
    {"label": "A100 40GB (SXM4)",    "model": "NVIDIA A100-SXM4-40GB",         "vram_mb": 40960, "credit": 140},
    {"label": "A100 80GB (SXM4)",    "model": "NVIDIA A100-SXM4-80GB",         "vram_mb": 81920, "credit": 170},
    {"label": "H100 80GB (PCIe)",    "model": "NVIDIA H100 PCIe",              "vram_mb": 81920, "credit": 320},
    {"label": "H100 80GB (SXM5)",    "model": "NVIDIA H100 80GB HBM3",         "vram_mb": 81920, "credit": 400},
    {"label": "H100 94GB (NVL)",     "model": "NVIDIA H100 NVL",               "vram_mb": 96256, "credit": 440},
]
# Fractional GPU flavors scale VRAM and cores by the same fraction, so the VRAM and core shares are
# always equal and _validate_balance passes on any card. gpu_mem_mb is the card's VRAM times the
# fraction, and the rate is the full-card rate, which occupancy scales down at runtime.
#
# mem_gb is Guaranteed (request equals limit). A flavor larger than any single node's allocatable is
# rejected by the single-node feasibility check in _validate_capacity, so on small nodes the large
# flavors simply never admit.
GPU_FLAVORS = [
    {"suffix": "¼ (frac)",  "frac": 0.25, "cpu": 4,  "mem_gb": 16, "disk_gb": 50},
    {"suffix": "½ (frac)",  "frac": 0.50, "cpu": 8,  "mem_gb": 32, "disk_gb": 100},
    {"suffix": "1× (full)", "frac": 1.00, "cpu": 16, "mem_gb": 64, "disk_gb": 200},
]
# CPU flavors: free, with a rate of 0 and no GPU requested.
CPU_FLAVORS = [
    {"name": "CPU Small", "cpu": 2, "mem_gb": 8,  "disk_gb": 20},
    {"name": "CPU Large", "cpu": 8, "mem_gb": 32, "disk_gb": 50},
]


def _build_offerings() -> list[dict]:
    out: list[dict] = []
    for c in CARDS:
        for f in GPU_FLAVORS:
            out.append({
                "name": f"{c['label']} · {f['suffix']}",
                "resource_class": "gpu",
                "gpu_model": c["model"],
                "gpu_mem_mb": round(c["vram_mb"] * f["frac"]),
                "gpu_cores": round(100 * f["frac"]),
                "cpu": f["cpu"], "mem_gb": f["mem_gb"], "disk_gb": f["disk_gb"],
                "credit_per_hour": str(c["credit"]),
            })
    for f in CPU_FLAVORS:
        out.append({
            "name": f["name"], "resource_class": "cpu",
            "cpu": f["cpu"], "mem_gb": f["mem_gb"], "disk_gb": f["disk_gb"],
            "credit_per_hour": "0",
        })
    return out


OFFERINGS = _build_offerings()
KEEP_NAMES = {o["name"] for o in OFFERINGS}
PER_USER_CREDIT = 10_000

POLICY = {
    "max_concurrent": 2,
    "max_queued": 5,
    "max_runtime_min": 24 * 60,      # 24h
    "idle_timeout_sec": 30 * 60,     # 30m
    "cpu_session_max_concurrent": 4,
    "cpu_session_max_runtime_min": 240,
    "cpu_session_idle_timeout_sec": 1800,
    "limits": {},
}


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Content-Type", "application/json")
    r.add_header("Idempotency-Key", f"seed-{method}-{path}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main() -> None:
    # whoami
    st, me = req("GET", "/auth/me")
    if st != 200:
        sys.exit(f"authentication failed ({st}): {me}. Check that the token belongs to a super_admin.")
    uid = me.get("id")
    print(f"▶ authenticated as {me.get('email')} ({uid}), global_role={me.get('global_role')}")

    # 1. List the existing offerings.
    st, lst = req("GET", "/offerings?page=1&size=100")
    existing = {o["name"]: o for o in lst.get("data", [])}

    # 1a. Create the offerings, skipping any name that already exists.
    for o in OFFERINGS:
        if o["name"] in existing:
            print(f"  = offering exists, skipping: {o['name']}")
            continue
        st, res = req("POST", "/offerings", o)
        print(f"  {'+' if st in (200,201) else '!'} created offering {o['name']} -> {st} {res.get('id', res)}")

    # 2. Deactivate any other GPU offering.
    for name, o in existing.items():
        if o.get("resource_class") == "gpu" and name not in KEEP_NAMES and o.get("status") != "inactive":
            st, res = req("PATCH", f"/offerings/{o['id']}", {"status": "inactive"})
            print(f"  ~ deactivated {name} ({o['id']}) -> {st}")

    # 3. Adjust the demo wallet to 10,000 credits.
    st, w = req("GET", "/credits/wallets/me")
    if st == 200 and w.get("id"):
        bal = float(w.get("balance", 0))
        delta = PER_USER_CREDIT - bal
        if abs(delta) >= 0.01:
            st, res = req("POST", f"/credits/wallets/{w['id']}/adjust",
                          {"amount": delta, "reason": "seed: per-user 10000 C baseline"})
            print(f"  $ wallet {w['id']} {bal:.0f} -> {PER_USER_CREDIT} C ({delta:+.0f}) -> {st}")
        else:
            print(f"  = wallet already holds {bal:.0f} C")
    else:
        print(f"  ! could not read my wallet: {st} {w}")

    # 4. Seed a user-scoped policy for the caller.
    if uid:
        st, res = req("POST", "/resource-policies", {"scope": "user", "scope_id": uid, **POLICY})
        print(f"  P policy for user={uid}: max_concurrent=2, idle 30m, runtime 24h -> {st} {res.get('id', res.get('error', ''))}")

    print("✔ seeding complete.")


if __name__ == "__main__":
    main()
