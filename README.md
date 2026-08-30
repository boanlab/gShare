# GShare

[![CI](https://github.com/boanlab/gShare/actions/workflows/ci.yml/badge.svg)](https://github.com/boanlab/gShare/actions/workflows/ci.yml)
[![Publish images](https://github.com/boanlab/gShare/actions/workflows/publish.yml/badge.svg)](https://github.com/boanlab/gShare/actions/workflows/publish.yml)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](./LICENSE)

**A GPU sharing platform for interactive workloads on Kubernetes.**

GShare lets several people work on one GPU at the same time. Its scheduler decides
admission — splitting VRAM and compute cores, holding and settling credits, and queueing
what does not fit — and Kubernetes runs the result as interactive session pods with
VS Code, JupyterLab, or a web terminal.

> New here? Read [Architecture and concepts](./docs/architecture.md) for the big picture,
> then [Getting started](./docs/getting-started.md) to run it. The full index lives in
> [`docs/`](./docs/README.md).

## Why

A GPU handed to one person is idle most of the time — an interactive notebook uses it in
bursts between long stretches of thinking and typing. Handing the whole card to one
tenant is simple and wasteful; splitting it naively means one tenant's runaway allocation
starves everyone else.

GShare takes the middle path. Sessions get a fraction of a physical card with hard VRAM
isolation (via [HAMi](https://github.com/Project-HAMi/HAMi)), the platform accounts for
what they use in credits, and a paused session returns its GPU to the pool instead of
sitting on it.

## Features

- **Fractional and exclusive GPU sessions.** Per-model tiers carve a full card into
  fractions with enforced VRAM limits, or hand over the whole card.
- **Credit accounting.** Hierarchical allocation from super-admin down to the user,
  a hold at admission, metered consumption while running, settlement on stop, and
  optional monthly refills.
- **Admission queueing.** When VRAM is oversubscribed, requests queue instead of failing,
  and are dequeued as capacity frees up.
- **Pause that actually frees the GPU.** Pausing a session tears the pod down and returns
  the card; compute billing stops. Resuming re-acquires a card and rebuilds the pod. Retained
  volumes keep billing for their provisioned capacity.
- **Idle reaping.** Sessions that stop using their GPU can be paused automatically, driven
  by real per-GPU utilisation from DCGM or the HAMi device-plugin monitor.
- **Multi-tenancy with three role planes.** Organization → group → user, with `super_admin`,
  `org_admin`, `group_admin`, and `member`/`guest` memberships.
- **Multi-cluster.** One control plane can drive several GPU clusters, each running its own
  operator, registered with a kubeconfig that is never stored in the database.
- **A console, not a CLI.** Everything is done from the web UI. There is deliberately no
  API key, CLI, SDK, or batch-job surface.

## Repository layout

| Path | Contents |
|---|---|
| `backend/` | `gshare-api` (FastAPI control plane) and `gshare-worker` (billing, queue, and reaper batch). Python 3.12 |
| `operator/` | `gshare-operator`, the per-cluster controller that reconciles `GShareSession` resources. Go 1.22 + controller-runtime |
| `frontend/` | The console: a React + TypeScript single-page app with live updates over SSE |
| `charts/gshare/` | The Helm chart for the whole platform |
| `deploy/` | Values overlays (`values/`), security baselines, monitoring, supply-chain policy, secret examples |
| `build/` | Image build contexts: catalogue session images (`images/`) and the patched HAMi scheduler (`hami-fork/`) |
| `hack/` | Developer and operator scripts: cluster bootstrap, secret generation, OpenAPI generation, catalogue seeding |
| `test/e2e/` | End-to-end assets: real-GPU suites, plus role and UX checks that need only the control plane |
| `docs/` | User manual, administrator manual, deployment and design documentation |

## Architecture

```
        console (SPA) ──REST + SSE──▶ gshare-api ──────┐  Python control plane.
                                                       │  Issues every identifier,
                                                       │  publishes the internal JWKS.
                                                       │
                             applies GShareSession CR  ▼
                                              gshare-operator (Go, one per cluster)
                                                       │
                                                       ▼
                                    Pod / Service / Ingress in `gshare-sessions`
                                                       │
        signed status callback (RS256 JWT, aud=gshare-internal)
                                                       │
                                                       └──────────▶ gshare-api

  gshare-system:  api · worker · operator · postgres (CNPG) · redis · ingress-nginx
  gshare-infra:   privileged DaemonSets — node-problem-detector · Spegel · image pre-puller
```

The custom resource is the source of truth for desired session state. The API decides
*whether* a session may run; the operator decides *how* it runs, and reports back.

### Namespaces

| Namespace | Purpose | Pod Security Admission |
|---|---|---|
| `gshare-system` | Control plane: api, worker, operator, Postgres, Redis, ingress | `baseline` |
| `gshare-sessions` | Tenant session pods | **`restricted` (enforce)** |
| `gshare-infra` | Privileged DaemonSets | `privileged` |

### Reference production configuration

These are the assumptions the charts and documentation are written against. All of them
can be swapped, but this is the combination that is tested.

| Concern | Choice |
|---|---|
| Image registry | Harbor at `registry.gshare.internal` |
| Storage | CephFS (RWX/ROX) for shared volumes, NFS (RWO) for home directories |
| Ingress | ingress-nginx |
| Database | CloudNativePG, three nodes |
| Object store | MinIO or Ceph RGW (S3-compatible) |
| Autoscaling | Cluster Autoscaler with Cluster API |
| Secrets | external-secrets backed by Vault or a cloud KMS |
| TLS | Terminated by a reverse proxy in front; the cluster receives plain HTTP on an ingress-nginx NodePort |
| Internal authentication | RS256 JWT verified against the JWKS the API publishes at `/.well-known/gshare-internal-jwks.json` |
| Supply chain | Kaniko builds with cosign verification enforced by Kyverno |

## Tenancy, authentication, and credits

**Authentication** is local: email and password. An administrator registers the user with
an initial password, and the user must change it at first login. The first `super_admin` is
created at startup from `GSHARE_BOOTSTRAP_ADMIN_EMAIL` and `GSHARE_BOOTSTRAP_ADMIN_PASSWORD`
— read from `.env` locally, generated by the chart on Kubernetes.

**Tenancy** is organization → group → user. Roles are `super_admin` (everything),
`org_admin` (their organization), `group_admin` (their group), and the `member` / `guest`
memberships. The top bar has an admin-mode toggle that switches between the two consoles.

**Credits** start at zero for a new user. They are allocated down the hierarchy —
super-admin to organization to group to user — and no level can hand out more than it
received. Monthly refills are supported and are use-it-or-lose-it. A session is always
billed to the requester's own personal wallet; charging someone else's wallet or a group
wallet is rejected. Volumes bill continuously for their provisioned capacity, whether or not
a session is running.

**Resource limits.** GPUs are allocated in per-model full-card fraction tiers — `fractional`
for shared, `exclusive` for the whole card. MIG is operated as a per-card POOL (admins move
cards between hami-core and MIG; profile-aligned fractional tiers land on MIG instances), and
asymmetric allocations across a card are rejected. Storage is capped by the `storage_gb`
policy. Policies resolve most-specific first: user, then group, then organization, then
global.

## Quickstart

### Local stack, no GPU required

```bash
make compose-up    # Postgres, Redis, api, worker, and console via Docker Compose
make smoke         # wait for the API to report healthy
# console: http://localhost:8000   API: http://localhost:8080
make compose-down  # stop and drop volumes
```

This runs the full control plane and lets you explore the console. Sessions need a GPU
cluster to actually land on — see below.

### Real GPU cluster

Prerequisites: GPU nodes with the NVIDIA container runtime, [HAMi](https://github.com/Project-HAMi/HAMi),
ingress-nginx, and a default StorageClass. If you do not have a cluster yet, build one with
[`docs/cluster-setup.md`](./docs/cluster-setup.md) and
[`hack/cluster-bootstrap.sh`](./hack/cluster-bootstrap.sh).

```bash
# All-in-one. The chart provisions the data tier, secrets, CRDs, namespaces, the
# operator's internal JWT, and registers the local cluster. In practice the only value
# you need to edit is global.domains.console.
make deploy-incluster

# Production, against externally managed Postgres, Redis, and external-secrets.
make prod-deploy
```

Registering additional clusters — including clusters that a Compose-hosted control plane
drives remotely — is covered in [`docs/cluster-connect.md`](./docs/cluster-connect.md).

### End-to-end verification on real hardware

```bash
bash test/e2e/backend/e2e-gpu-session.sh   # exclusive session lifecycle and billing
bash test/e2e/backend/e2e-queue.sh         # VRAM oversubscription, queue, dequeue
bash test/e2e/backend/e2e-idle.sh          # automatic pause of an idle GPU session
```

Only this path exercises real CUDA execution, hard VRAM isolation, and numerical
correctness under HAMi-core. Onboarding details are in
[`test/e2e/README.md`](./test/e2e/README.md).

## Development

```bash
make test-docker   # backend and frontend tests inside containers — nothing installed on the host
make test          # all component tests with local toolchains
make lint          # all component linters
make helm-lint     # chart lint plus a render of every values overlay
make ci            # everything CI gates on
```

`make test-docker` runs each component's Dockerfile `test` stage in a `--rm` container, so
pytest caches, coverage data, `node_modules`, and vitest output disappear with the
container. The operator's unit tests need the Go envtest binaries and are therefore not
part of that path — run `make test`.

See [CONTRIBUTING.md](./CONTRIBUTING.md) for the full workflow.

## Documentation

| Document | Audience |
|---|---|
| [Architecture and concepts](./docs/architecture.md) | Everyone — read this first |
| [Getting started](./docs/getting-started.md) | Operators bringing GShare up |
| [Cluster setup](./docs/cluster-setup.md) | Building a kubeadm + HAMi GPU cluster from scratch |
| [Connecting a cluster](./docs/cluster-connect.md) | Registering clusters and attaching operators |
| [User manual](./docs/user-manual.md) | People running sessions |
| [Administrator manual](./docs/admin-manual.md) | People running the platform |
| [Console UX standards](./docs/console-ux.md) | Anyone adding or changing a screen |

## Contributing

Issues and pull requests are welcome. Please read [CONTRIBUTING.md](./CONTRIBUTING.md)
and the [Code of Conduct](./CODE_OF_CONDUCT.md). Security reports go through
[SECURITY.md](./SECURITY.md), not the public issue tracker.

## License

Apache License 2.0 — see [LICENSE](./LICENSE).

GShare vendors [HAMi](https://github.com/Project-HAMi/HAMi) under `third_party/hami` as a
git submodule; it carries its own license.
