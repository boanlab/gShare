# Getting started

> 📚 [Documentation home](./README.md)

Everything you need to do from a fresh clone, in order. There are two paths:

- **A. Local walkthrough** — bring up the control plane and console with Docker Compose,
  no Kubernetes and no GPU.
- **B. Real deployment** — an all-in-one install on a GPU Kubernetes cluster, running
  actual sessions.

> Related: [`README.md`](../README.md) for the overall shape,
> [`cluster-setup.md`](./cluster-setup.md) for building the cluster itself, and
> [`cluster-connect.md`](./cluster-connect.md) for registering clusters and attaching
> operators.

---

## 0. Clone and prerequisites

```bash
git clone https://github.com/boanlab/gShare.git && cd gShare
```

| Path | Requirements |
|---|---|
| A. Local walkthrough | Docker and Docker Compose |
| B. Real deployment | `kubectl` pointed at the target cluster, `helm` v3, and a Kubernetes cluster. For GPU sessions: the NVIDIA runtime, HAMi, a default StorageClass, and ingress-nginx |

You do not need to build images. The components are published publicly as
`boanlab/gshare-{backend,operator,frontend}:latest`. Build only when you have changed the
code — see the appendix.

---

## A. Local walkthrough (no Kubernetes, no GPU)

This runs the control plane and console so you can explore the API and the UI. It does not
include the operator or GPU scheduling, so by default **no real session pod can start** —
that is path B.

To run real sessions from a Compose-hosted control plane, build a GPU cluster with
`hack/cluster-bootstrap.sh up`, register it from the console, then bring up the data plane
with `make deploy-dataplane CLUSTER_ID=… CONTROL_PLANE_URL=http://<compose-host>:8080`.
The full procedure is in [`cluster-connect.md`](./cluster-connect.md) §C.

```bash
make compose-up        # build and start api, worker, frontend, postgres, redis
```

- Console: **http://localhost:8000**
- API: **http://localhost:8080** (`/healthz`, `/api/v1/...`)
- Login: **admin@example.com** / **change-me-please** — the Compose defaults. You are
  forced to change the password at first login.

> Changing the defaults is optional; the stack runs without a `.env` file. To override,
> set `GSHARE_BOOTSTRAP_ADMIN_EMAIL`, `GSHARE_BOOTSTRAP_ADMIN_PASSWORD`, or
> `GSHARE_SESSION_DOMAIN` in a repository-root `.env` (Compose loads it automatically) or
> in your shell environment before `make compose-up`. If you want strong secrets such as a
> real RS256 key, generate a `.env` with `hack/gen-secrets.sh` — also optional for Compose.

```bash
make smoke             # wait until the API reports healthy
make compose-down      # stop and drop volumes
```

---

## B. Real deployment (GPU Kubernetes cluster)

The cluster only ever receives **plain HTTP**. A reverse proxy you own terminates TLS and
forwards to the ingress-nginx NodePort on `:30080`.

> **You edit exactly one file.** Copy the template
> [`domain.example.yaml`](../deploy/values/domain.example.yaml) to
> `deploy/values/domain.yaml` (not committed), fill in the domain and the administrator
> identity, and run `make deploy-incluster`. Secrets, CRDs, namespaces, the database,
> Redis, and the admin password are all created by the chart. Skip step 0 if your cluster
> is already up; `hack/cluster-info` is only needed when building a new one.

### Step 0 — prepare the cluster

If you already have ingress-nginx, a default StorageClass, and — for GPU work — HAMi and
the NVIDIA runtime, go to step 1. Otherwise see [`cluster-setup.md`](./cluster-setup.md),
or take the automated path:

```bash
cp hack/cluster-info.example hack/cluster-info   # fill in MASTER_NODE, WORKER_NODES(:mode), CPU_WORKERS
./hack/cluster-bootstrap.sh up                   # kubeadm + flannel + HAMi + ingress-nginx(:30080) + local-path
```

`up` labels the nodes according to the modes in `cluster-info`; manual labelling is only
needed on the step-by-step path.

### Step 1 — decide the domain and TLS

1. Pick a console domain, for example `gshare.example.com`.
2. Point its DNS at **your reverse proxy**, and have the proxy terminate TLS and
   `proxy_pass` to `http://<any-node>:30080`, preserving the `Host` header.
3. Write the domain and administrator identity into the deployment-local overlay. This is
   the **only** configuration you touch on path B. It differs per deployment and must not
   be committed, so it lives in its own file (ignored by git; only the example is tracked):

   ```bash
   cp deploy/values/domain.example.yaml deploy/values/domain.yaml
   ```

   ```yaml
   # deploy/values/domain.yaml
   global:
     domains:
       console: gshare.example.com   # the real domain — one host for the console and path-based sessions
   bootstrapAdmin:
     email: admin@example.com        # first super_admin login (default: admin@example.com)
     password: change-me-please      # initial password; must be changed at first login
   ```

   `make deploy-incluster` and `make prod-deploy` apply this file with `-f` when it exists,
   and fall back to the chart defaults when it does not. That one domain value drives the
   console, every session URL (`/proxy/{cr}/{lab,terminal,code}`), and the ingress host.
   The administrator account is created on first start with this email and password. Set
   `password: ""` to have a random one generated instead — read it back in step 3. Display
   names are edited from the console after login.

### Step 2 — deploy

```bash
make deploy-incluster
```

That is the whole install. The chart configures everything with no manual prerequisites:
in-cluster Postgres and Redis, secrets, CRDs, namespaces, the operator's internal JWT
(issued and rotated by a Job and CronJob), and registration of the local cluster. It uses
the public `:latest` images.

> For the production variant — external CloudNativePG and Redis with external-secrets —
> use `make prod-deploy` with
> [`deploy/values/dockerhub.yaml`](../deploy/values/dockerhub.yaml).

**Optional add-ons** (each independent; enable via the overlay — every knob is a commented
example in [`domain.example.yaml`](../deploy/values/domain.example.yaml)):

- **Monitoring** — `make deploy-monitoring` installs Prometheus with the dcgm / node /
  kube-state exporters. It powers the admin monitoring page, the per-session live-usage
  panels, and (via `operator.prometheusUrl`) the idle reaper's utilisation source — on a
  cluster with more than one GPU node, set `prometheusUrl`; the HAMi-monitor fallback
  round-robins per-node pods and idle sessions are then never auto-paused.
- **Mixed GPU fleet** — different card models in one cluster need
  `operator.perCardMode: true`, which pins each session pod to the exact card the ledger
  reserved. Without it a session priced for one model can land on another.
- **LAN image registry** — `deploy/registry/registry.yaml` plus per-node trust; see
  [`cluster-setup.md` §Local registry](./cluster-setup.md#local-registry-optional). Set
  `api.buildRegistry` so console-driven image builds push there too.
- **Real volume quotas** — the ZFS storage node
  ([`cluster-setup.md` §Storage node](./cluster-setup.md#storage-node-optional-volumes-with-a-real-quota));
  then `operator.volumeStorageClass: gshare-data`.

### Step 3 — verify and log in

```bash
kubectl get pods -n gshare-system   # api, worker, operator, frontend, pg, redis all Running

# The admin password defaults to change-me-please. If you set a different one, or left it
# empty to get a random one, read it from the secret:
kubectl get secret -n gshare-system gshare-bootstrap-admin -o jsonpath='{.data.password}' | base64 -d
```

Open **https://gshare.example.com** through your reverse proxy and log in with
`bootstrapAdmin.email` and that password. You will be asked to change it immediately.

### Step 4 — check the catalogue (administrator)

A default catalogue is seeded idempotently at startup: GPU offerings (RTX, A100, H100),
base images, compute and GPU presets, a global resource policy, and the system wallet. As
an administrator you only edit and extend it, from the console or through
`POST /api/v1/offerings` and `POST /api/v1/images` / `/images/import`.

- An **offering** is what a user picks: a GPU model and tier with an hourly credit rate.
  The seeded rates are suggestions — adjust them in the console.
- An **image** is the session container image. The GShare session images ship JupyterLab,
  a web terminal, and code-server.
- Users are not limited to the shared catalogue: from **내 이미지 / My images** any member can
  build an image (an inline Dockerfile or a public git repository) or import a public registry
  reference. Those rows are private to their owner, and one member may hold at most 20 of them.

The operator reports GPU inventory automatically, so nodes and devices (for example
RTX 4090) appear on the infrastructure screens without further setup.

### Step 5 — create and connect to a session (user)

A user logs in, picks an offering and an image, and creates a session. Once it is
`running`, the console shows connect links — each carrying a single-use token — all under
the one domain:

- `https://gshare.example.com/proxy/{cr}/lab` → JupyterLab
- `https://gshare.example.com/proxy/{cr}/terminal` → web terminal (ttyd)
- `https://gshare.example.com/proxy/{cr}/code/` → code-server (VS Code)

The ingress forward-auth redeems the single-use `?gshare_cnx=…` token for a short-lived
cookie and then proxies to the app. There is no SSH; the shell is the web terminal or the
code-server terminal.

Sessions can be **paused and resumed**. Pausing tears down the pod, returns the GPU, and
stops billing; resuming re-acquires a GPU and starts it again.

---

## Summary

- **Local:** `make compose-up`, then the console on `:8000` (control plane only).
- **All-in-one:** prepare the cluster → set the domain and front the cluster with a
  TLS-terminating proxy → `make deploy-incluster` → log in as the administrator, review
  offerings and images → users create sessions → connect at
  `https://{domain}/proxy/{cr}/{lab|terminal|code}`.
- **Compose plus an external cluster:** `make compose-up` for the control plane →
  `cluster-bootstrap.sh up` on the GPU cluster → register it from the console →
  `make deploy-dataplane CLUSTER_ID=… CONTROL_PLANE_URL=…`. See
  [`cluster-connect.md`](./cluster-connect.md) §C.

---

## Appendix — building and pushing your own images

Only needed when you have changed a component; the default install uses the public images.

```bash
make images TAG=<tag>            # build backend, operator, frontend
make login                       # log in with DOCKERHUB_USERNAME / DOCKERHUB_TOKEN
make push TAG=<tag>
make deploy-incluster TAG=<tag>  # deploy that tag
```

- To inject secrets yourself instead of letting the chart generate them:
  `hack/gen-secrets.sh --k8s`.
- Tests: `make test` for unit and functional tests, `make ci` for the full gate.
