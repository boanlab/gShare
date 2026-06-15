#!/usr/bin/env python3
"""Stateful training workload for P0 (progress-loss) measurement — real optimizer/momentum
state in VRAM, periodic disk checkpoint, resume-from-checkpoint.

Unlike the stateless SGEMM workload, this accumulates *progress* (a global step counter +
SGD-momentum state + a monotonically improving loss on a fixed synthetic target). Cold-STOP
preemption loses everything since the last disk checkpoint (must redo those steps on resume);
lossless GShare yield preserves the exact step (cuda-checkpoint is bit-exact, see M1) → 0 redo.

  python3 gpu_train.py --secs 120 --batch 96 --ckpt-every 200 --ckpt /ckpt/s.pt [--resume]

Emits READY/PROG/CKPT/DONE with the global step, loss, and steps/sec → an external orchestrator
measures throughput, the cold-STOP redo (step_at_kill − step_at_last_ckpt), and verifies the
loss curve continues (lossless) vs jumps back (cold restart).
"""
import argparse
import os
import time

import torch
import torch.nn as nn
from torchvision import models


def now():
    return time.time()


ap = argparse.ArgumentParser()
ap.add_argument("--secs", type=float, default=120)
ap.add_argument("--batch", type=int, default=96)        # sized to ~6 GB on a 4090 w/ resnet50
ap.add_argument("--arch", default="resnet50")
ap.add_argument("--ckpt-every", type=int, default=200)  # disk checkpoint interval (steps)
ap.add_argument("--ckpt", default="/ckpt/state.pt")
ap.add_argument("--opt", default="sgd")                 # sgd | adam (adam = larger optimizer state)
ap.add_argument("--resume", action="store_true")
ap.add_argument("--tag", default="train")
a = ap.parse_args()

dev = torch.device("cuda:0")
torch.manual_seed(0)
# proc_start: process entry — used to measure cold-start (framework import + model build + ckpt load).
proc_start = float(os.environ.get("PROC_START", now()))
model = models.__dict__[a.arch](num_classes=1000).to(dev)
opt = (torch.optim.Adam(model.parameters(), lr=1e-4) if a.opt == "adam"
       else torch.optim.SGD(model.parameters(), lr=0.05, momentum=0.9))  # stateful optimizer
crit = nn.CrossEntropyLoss()
# fixed synthetic batch (a learnable target → loss decreases = real, measurable progress)
x = torch.randn(a.batch, 3, 224, 224, device=dev)
y = torch.randint(0, 1000, (a.batch,), device=dev)

step = 0
if a.resume and os.path.exists(a.ckpt):
    tl = now()
    ck = torch.load(a.ckpt, map_location=dev)
    model.load_state_dict(ck["model"]); opt.load_state_dict(ck["opt"]); step = ck["step"]
    torch.cuda.synchronize()
    # load_s = checkpoint load; cold_start_s = process entry → state ready (framework+build+load).
    print(f"{a.tag} RESUMED step={step} load_s={now()-tl:.2f} cold_start_s={now()-proc_start:.2f} "
          f"ckpt_mb={os.path.getsize(a.ckpt)/1048576:.0f} t={now():.3f}", flush=True)

free, total = torch.cuda.mem_get_info()
print(f"{a.tag} READY step={step} vram_used_mib={(total-free)/1048576:.0f} t={now():.3f}", flush=True)

t0 = now(); last = t0; step0 = step
while now() - t0 < a.secs:
    opt.zero_grad(set_to_none=True)
    loss = crit(model(x), y)
    loss.backward(); opt.step()
    step += 1
    if a.ckpt_every and step % a.ckpt_every == 0:
        os.makedirs(os.path.dirname(a.ckpt) or ".", exist_ok=True)
        tw = now()
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "step": step}, a.ckpt)
        # write_s = model+optimizer → disk; size_mb = checkpoint size (forces infrequent cadence).
        print(f"{a.tag} CKPT step={step} write_s={now()-tw:.2f} size_mb={os.path.getsize(a.ckpt)/1048576:.0f} t={now():.3f}", flush=True)
    if now() - last >= 5.0:
        torch.cuda.synchronize()
        sps = (step - step0) / (now() - last)
        print(f"{a.tag} PROG step={step} loss={loss.item():.4f} sps={sps:.1f} t={now():.3f}", flush=True)
        last = now(); step0 = step
torch.cuda.synchronize()
print(f"{a.tag} DONE step={step} wall={now()-t0:.2f} loss={loss.item():.4f} t={now():.3f}", flush=True)
