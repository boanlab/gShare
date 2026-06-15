#!/usr/bin/env python3
"""Parse E1/E2 condition logs (docs/paper/eval/e2e/run_baremetal.sh + run_gshare.sh output)
into the §E.4 condition×metric matrix. Reads *.log DONE/PROG lines and *_resume.txt.

Emits a markdown table + e1.json. Missing conditions are left as '—' so the matrix can
be filled incrementally as each condition is measured.
"""
import glob
import json
import os
import re
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else "./e1_out"
os.makedirs(OUT, exist_ok=True)

def parse_log(path):
    """Return dict from a workload log: iters, wall, avg_gflops, tflops, prog series."""
    d = {"iters": None, "wall": None, "gflops": None, "tflops": None, "prog": []}
    if not os.path.exists(path):
        return d
    for ln in open(path):
        m = re.search(r"DONE iters=(\d+) wall=([\d.]+) avg_gflops=([\d.]+) tflops=([\d.]+)", ln)
        if m:
            d["iters"] = int(m.group(1)); d["wall"] = float(m.group(2))
            d["gflops"] = float(m.group(3)); d["tflops"] = float(m.group(4))
        m = re.search(r"PROG iter=(\d+) gflops=([\d.]+)", ln)
        if m:
            d["prog"].append(float(m.group(2)))
    return d

def f(x, nd=1):
    return "—" if x is None else (f"{x:.{nd}f}")

logs = {os.path.basename(p)[:-4]: parse_log(p) for p in glob.glob(os.path.join(OUT, "*.log"))}
def txt(name, key):
    p = os.path.join(OUT, name)
    if not os.path.exists(p): return None
    for ln in open(p):
        m = re.search(rf"{key}=([\d.]+)", ln)
        if m: return float(m.group(1))
    return None

soloR = logs.get("soloR", {})
soloS = logs.get("soloS", {})
R_ref_thpt = soloR.get("gflops")
S_ref_thpt = soloS.get("gflops")

rows = []
def addrow(cond, R=None, S=None, R_resume=None, host_ram="0", note=""):
    R = R or {}; S = S or {}
    r_thpt = R.get("gflops"); s_thpt = S.get("gflops")
    r_prog = (R.get("iters") / soloR["iters"]) if (R.get("iters") and soloR.get("iters")) else None
    slow = (R_ref_thpt / r_thpt) if (R_ref_thpt and r_thpt) else None
    s_loss = (1 - s_thpt / S_ref_thpt) if (S_ref_thpt and s_thpt) else None
    good = (R.get("gflops") or 0) * (R.get("wall") or 0) + (S.get("gflops") or 0) * (S.get("wall") or 0)
    rows.append([cond, f(r_prog, 2), f(r_thpt), f(R_resume, 2), f(slow, 2),
                 f(s_thpt), f(s_loss, 2), f(good/1000 if good else None), host_ram, note])

addrow("Solo(R)", soloR, None, note="baseline")
addrow("Solo(S)", None, soloS, note="baseline")
addrow("keep-idle", logs.get("ki_R"), logs.get("ki_S"), note="S blocked")
addrow("cold-STOP", logs.get("cs_R2"), logs.get("cs_S"), R_resume=txt("cs_resume.txt", "cold_restart_wall_s"))
addrow("app-ckpt", logs.get("ac_R"), logs.get("ac_S"),
       R_resume=txt("ac_resume.txt", "ac_restore_s"))
addrow("Orion", logs.get("orion_R"), logs.get("orion_S"), note="concurrent")
addrow("GShare", logs.get("gshare_R"), logs.get("gshare_S"),
       R_resume=txt("gshare_resume.txt", "lossless_restore_s"),
       host_ram="=VRAM", note="lossless")

hdr = ["condition", "R-prog", "R-thpt(GF)", "R-resume(s)", "slowdown(×)", "S-thpt(GF)", "S-loss", "goodput(TF·s)", "host-RAM", "notes"]
print("| " + " | ".join(hdr) + " |")
print("|" + "|".join("---" for _ in hdr) + "|")
for r in rows:
    print("| " + " | ".join(str(x) for x in r) + " |")

json.dump({"out": OUT, "ref": {"R_thpt": R_ref_thpt, "S_thpt": S_ref_thpt},
           "rows": [dict(zip(hdr, r)) for r in rows]}, open(os.path.join(OUT, "e1.json"), "w"), indent=2)
print(f"\n[json] {os.path.join(OUT, 'e1.json')}")
