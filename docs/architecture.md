# Architecture and concepts

> 📚 [Documentation home](./README.md) — this page is the system overview. For how to
> drive the screens, see the [user](user-manual.md) and [administrator](admin-manual.md)
> manuals.

## In one paragraph

GShare does not split GPUs itself. The actual partitioning is done by
[HAMi](https://github.com/Project-HAMi/HAMi) — vGPUs bounded by VRAM and core limits —
or by handing over a full card. GShare is the **control plane on top**: it decides who
gets what, for how long, and at what cost. On ordinary Kubernetes plus HAMi it adds
session admission, occupancy-based billing, hierarchical limits, and reclamation of idle
resources, so that many organizations, groups, and users can share one cluster.

## Components and flow

```
  users and administrators
      │  browser
      ▼
  console (frontend, React SPA)          ── user and admin screens, REST + SSE
      │  REST / SSE
      ▼
  control plane (backend, Python/FastAPI) ── owns state and money: authentication,
      │   ├ gshare-api    : admission, CRUD, token issuing      authorization, credits,
      │   └ gshare-worker : billing, budget rollup, queue,      budgets, catalogue,
      │                     refills                             policy, scheduling
      │  applies GShareSession CR (via kubeconfig)   ▲ signed status and inventory
      ▼                                             │ callbacks (internal JWT)
  operator (Go, one per cluster) ──▶ Pod / Service / Ingress / Secret (gshare-sessions)
      │                                  session pod = interactive environment
      ▼                                  (VS Code, JupyterLab, web terminal)
  HAMi + GPU nodes                    ── real partitioning (gpumem / gpucores) or a full card

  postgres (state and ledger) · redis (queue, tokens, locks)
```

| Component | Language and location | Responsibility | Details |
|---|---|---|---|
| Console | React + TypeScript · `frontend/` | User and administrator SPA over REST and SSE | [frontend/README](../frontend/README.md) |
| Control plane | Python + FastAPI · `backend/` | Authority on money and session state: admission, CRUD, tokens | [backend/README](../backend/README.md) |
| Operator | Go · `operator/` | Reconciles `GShareSession` resources into pods, services, and ingress; reports status back | [operator/README](../operator/README.md) |
| GPU partitioning | HAMi (external) | vGPUs bounded by VRAM and cores, or exclusive full cards | — |

The control plane is cluster-agnostic: it applies custom resources to external GPU
clusters through their kubeconfig, and each cluster's operator reconciles them and
replies with status and inventory over a **signed internal JWT callback**. Decisions
about money and state are made only in the control plane.

## Core concepts

- **Offering** — the catalogue of GPU models. One row per full-card model, carrying the
  hourly credit rate. Chosen when a session is created.
- **Preset** — the catalogue of session sizes: compute (CPU, memory, disk) plus a GPU
  fraction tier (XL ½, L ¼, M ⅛, S 1/16, SS 1/32) or an exclusive full card. VRAM and
  core limits are derived by applying the tier fraction to the offering's full-card VRAM.
- **Session** — an interactive working environment, backed by a pod. Its mode is either
  **fractional** (a share of VRAM and cores) or **exclusive** (the whole card). MIG is a
  per-card pool, not a session mode: profile-aligned fractional requests may land on a MIG
  instance transparently.
- **Occupancy** — `max(VRAM fraction, core fraction)`. Billing is
  `rate × occupancy × runtime`; an exclusive session has occupancy 1.0.
- **Credits, wallets, budgets** — credits are allocated down a hierarchy of wallets
  (system → organization → group → individual), but a session is billed **only to the
  requester's own personal wallet**. A budget is a separate per-scope limit gate that can
  either warn or block.
- **Resource policy** — limits on concurrent sessions, total allocated resources, and idle
  timeout. Resolved most-specific first: **user → group → organization → global**.
- **Pause and resume** — pausing tears the pod down so the operator **returns the GPU**
  (another session can take it immediately) and **compute billing stops**, while the
  session itself is preserved. Retained volumes keep billing for their capacity. Resuming
  **re-acquires a GPU** and rebuilds the pod. Idle GPU sessions can be paused
  automatically by policy, which is how capacity is reclaimed.
- **Queue and priority** — when capacity is exhausted a session enters the queue instead
  of failing, and is admitted in priority order as resources come back.
- **Volumes and snapshots** — persistent personal or group storage, read-only or
  read-write, shareable and snapshottable. Billed continuously against the owner's wallet
  for the provisioned quota at `STORAGE_CREDIT_PER_GB_HOUR`, whether or not a session is
  running.
- **RBAC** — `super_admin` (everything), `org_admin` (one organization), `group_admin`
  (one group), and `member` / `guest`. The user console and the administrator console are
  separate surfaces.
- **CUDA compatibility** — an image whose CUDA version is below the offering's `min_cuda`
  is rejected.

## Session admission

A create-session request has to pass these gates, in order, before anything starts:

1. **References and CUDA** — the offering, image, and wallet exist, and the image's CUDA
   version satisfies the offering.
2. **Mount permissions** — the requested volumes are accessible in the requested mode.
3. **Billing wallet** — the target wallet is the requester's own personal wallet.
4. **Resource policy quota** — concurrency and total-resource limits (user → group →
   organization → global).
5. **Budget gate** — the scope budget; a blocking budget rejects the request when exceeded.
6. **Credit hold** — the estimated cost is reserved from the balance; insufficient balance
   is rejected.
7. **VRAM reservation** — an occupancy-aware two-dimensional best fit picks a card.
   If none fits, the session is **queued**.
8. **Handoff to the operator** — the custom resource is applied, the pod starts, the
   running callback arrives, and billing begins.

CPU-only sessions are free and skip the budget, hold, VRAM, queue, and GPU steps entirely.

## What makes it different

On top of a partitioning mechanism (HAMi), GShare combines occupancy-aware placement and
billing, automatic reclamation of idle GPUs through pause, in-place GPU yield with
preemptive lending (lossless hand-off and resume), and hierarchical limit management. The
[design notes](paper/) cover the reasoning and the measurements.

## Namespaces

| Namespace | Purpose |
|---|---|
| `gshare-system` | Control plane: api, worker, operator, Postgres, Redis, ingress |
| `gshare-sessions` | Tenant session pods, under `restricted` Pod Security Admission |
| `gshare-infra` | Privileged DaemonSets: node-problem-detector, Spegel, image pre-puller |
