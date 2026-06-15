# End-to-end assets for a real GPU cluster

Scripts that deploy and verify the GShare control plane on a kubeadm cluster with real
GPUs (RTX 4090 and similar), the NVIDIA runtime, **HAMi**, ingress-nginx, and a default
StorageClass. This is the only path that exercises real CUDA execution and hard VRAM
isolation.

## Layout

```
test/e2e/
├── backend/      # api, worker, postgres, redis, plus session lifecycle, billing, queue, idle e2e
│   ├── deploy.sh                 # docker build → push to a registry → apply stack.yaml
│   ├── stack.yaml                # api, worker, pg, redis (+ RBAC for GShareSession)
│   ├── seed.sql / seed-gpu.sql   # reference data: users, cluster, offerings, wallets
│   ├── seed-roles.sql            # four roles plus organization, group, and wallets (UI e2e seed)
│   ├── e2e-rbac-matrix.py        # per-role API authorization matrix
│   ├── provision-internal-jwt.sh # the internal RS256 JWT for operator↔api callbacks
│   ├── e2e-gpu-session.sh        # exclusive session: create → terminate → settle
│   ├── e2e-queue.sh              # VRAM oversubscription → enqueue → dequeue (real GPU)
│   ├── e2e-idle.sh               # automatic reaping of an idle session
│   └── e2e-session.sh            # the CPU-only (free) path
├── operator/     # operator deployment (CRD + RBAC), cordon e2e, sample sessions
├── frontend/     # console deployment and Playwright role and flow checks
│   ├── deploy.sh, frontend.yaml                                   # build, push, apply
│   └── e2e-roles.js, e2e-billing-report.js, e2e-topup-reject.js   # role, billing, top-up flows
└── ux/           # persona-driven console audit — no GPU, no cluster
    ├── audit.js, audits.js, flows.js   # driver, DOM audits, interaction probes
    ├── personas.js, routes.js          # eight personas, every screen
    ├── seed.js, fixture.sql            # content and a synthetic data plane to audit against
    └── screenshots.js                  # the captures in docs/screenshots
```

> Two suites need no GPU and no cluster, only `docker compose up`:
> the role-based RBAC and UI checks in [`README-e2e-roles.md`](./README-e2e-roles.md), seeded from
> `backend/seed-roles.sql`, and the persona UX audit in [`ux/`](./ux/README.md), which walks every
> screen as eight different people and reports what each of them runs into.

## Prerequisites

- Real GPU nodes labelled `gshare.io/gpu-mode=exclusive|fractional`, with the NVIDIA
  runtime and HAMi advertising `nvidia.com/gpumem` and `nvidia.com/gpucores`.
- `kubectl` pointed at the target context, `helm` v3, a default StorageClass, ingress-nginx.
- `docker` on the build host, logged in to a registry the cluster can pull from. Override
  the coordinates with `REGISTRY`, `ORG`, and `TAG`; the default is
  `docker.io/boanlab/*:latest`.

## Running

```bash
bash backend/deploy.sh                 # backend stack
kubectl -n gshare-system exec -i deploy/gshare-pg -- psql -U gshare -d gshare < backend/seed.sql
kubectl -n gshare-system exec -i deploy/gshare-pg -- psql -U gshare -d gshare < backend/seed-gpu.sql
bash operator/deploy.sh                # operator
bash backend/provision-internal-jwt.sh # internal JWT
bash frontend/deploy.sh                # console

# end-to-end
bash backend/e2e-gpu-session.sh
bash backend/e2e-queue.sh
bash backend/e2e-idle.sh
```
