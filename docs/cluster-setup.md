# Building the GPU Kubernetes cluster

> 📚 [Documentation home](./README.md)

GShare deploys onto an **existing** GPU Kubernetes cluster. This page covers building that
prerequisite cluster from scratch. The automation lives in
[`hack/cluster-bootstrap.sh`](../hack/cluster-bootstrap.sh).

The stack it produces:

| Component | Choice |
|---|---|
| Kubernetes | kubeadm **1.36** — one control plane, N GPU workers, optionally M CPU workers |
| Runtime | containerd with the **NVIDIA runtime** (RuntimeClass `nvidia`) |
| CNI | **flannel**, pod CIDR `10.244.0.0/16` |
| GPU partitioning | **HAMi** — device plugin and scheduler, `nvidia.com/gpumem` and `gpucores` |
| Ingress | **ingress-nginx** on NodePort 30080/30443, plain HTTP |
| TLS | Terminated by **a reverse proxy in front**; the cluster stays HTTP internally |
| Storage | **local-path** as the default StorageClass; optionally a **storage node** (ZFS + NFS behind democratic-csi) for user volumes with a real, df-visible quota |

## Recommended: one command from a control machine

Write `cluster-info` on a control machine — your laptop or a bastion — and let the script
set up every node over SSH.

**Prerequisites:** from the control machine to each node, (1) SSH key authentication
(`ssh-copy-id`) and (2) passwordless remote `sudo` must already work.

```bash
cp hack/cluster-info.example hack/cluster-info   # fill in MASTER_NODE and WORKER_NODES
./hack/cluster-bootstrap.sh up                   # prereqs → init → join → addons → label → verify
```

`cluster-info` format:

```sh
MASTER_NODE=ubuntu@10.0.0.10
# Workers are user@ip:MODE, where MODE is exclusive or fractional (default: fractional).
WORKER_NODES=ubuntu@10.10.0.196:exclusive,ubuntu@10.10.0.197:fractional
# Optional GPU-less workers, comma separated. Labelled `cpu`; the NVIDIA runtime is not installed.
# CPU_WORKERS=ubuntu@10.10.0.200,ubuntu@10.10.0.201
# Optional storage node: a ZFS pool on the given (empty) block device, exported over NFS through
# democratic-csi as StorageClass gshare-data. Labelled and tainted `storage`.
# STORAGE_NODE=ubuntu@10.10.0.194:/dev/vdb
# Optional plain-HTTP registry on the LAN, configured for containerd on every node.
# LOCAL_REGISTRY=10.10.0.191:5001
# SSH_OPTS="-i ~/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new"   # optional
```

`up` copies the script to every node and then runs, in order: prerequisites (control plane,
CPU workers, and GPU workers in parallel) → reboot and reconnect any GPU worker that just
got a driver → `kubeadm init` on the control plane, harvesting the join command → workers
join (GPU and CPU in parallel) → addons on the control plane → node labelling (control
plane and CPU workers as `cpu`, GPU workers with their configured mode) → verification.

`hack/cluster-info` contains real addresses and is git-ignored; only
`cluster-info.example` is tracked. So is `hack/storage-csi-key`, the SSH key pair the script
generates for democratic-csi when a storage node is configured.

> To run the steps by hand on each node, use the same subcommands directly — see below.

## Prerequisites

- **Node OS:** Ubuntu 22.04 or 24.04 (apt-based). One control plane, N GPU workers, and
  optionally M GPU-less CPU workers.
- **NVIDIA driver:** if `nvidia-smi` does not work on a GPU worker, `prereqs --gpu`
  installs one — `NVIDIA_DRIVER=auto` uses the `ubuntu-drivers` recommendation. Pin a
  version with `NVIDIA_DRIVER=nvidia-driver-550-server`, or skip installation entirely with
  `NVIDIA_DRIVER=skip`. A fresh install needs **a reboot** for the kernel module to load.
  `up` reboots the node itself and waits for it to come back (a changed `boot_id`) with a
  working `nvidia-smi` before continuing; the timeout is `REBOOT_WAIT_TRIES` × 5 s, ten
  minutes by default. Run standalone, `prereqs --gpu` only prints the instruction unless
  you pass `NVIDIA_DRIVER_REBOOT=1`. Nodes with a working driver are left alone.
- **helm v3:** installed automatically during the `addons` step if missing (via
  `get-helm-3`). Disable with `HELM_SKIP_INSTALL=1`.
- **Network:** between nodes, open 6443 (API server), 10250 (kubelet), 8472/udp (flannel
  VXLAN), and the NodePort range 30000–32767.

## Step by step

### 1. Every node — prerequisites

```bash
# Control plane and CPU-only nodes
sudo ./hack/cluster-bootstrap.sh prereqs

# GPU workers — also installs nvidia-container-toolkit and the runtime,
# and the driver itself if nvidia-smi is missing.
sudo ./hack/cluster-bootstrap.sh prereqs --gpu
```

This installs containerd with `SystemdCgroup`, the required kernel modules and sysctls,
disables swap, and installs kubeadm, kubelet, and kubectl 1.36. When a driver is newly
installed the script asks for a reboot; add `NVIDIA_DRIVER_REBOOT=1` to have it reboot
itself.

### 2. Control plane — initialise

```bash
sudo ./hack/cluster-bootstrap.sh init
```

Runs `kubeadm init`, applies flannel, and prints the `kubeadm join …` command. Copy it.

### 3. Each worker — join

```bash
sudo ./hack/cluster-bootstrap.sh join "kubeadm join 10.x.x.x:6443 --token ... --discovery-token-ca-cert-hash sha256:..."
```

### 4. Control plane — addons

```bash
./hack/cluster-bootstrap.sh addons
```

Installs RuntimeClass `nvidia`, HAMi, ingress-nginx on a NodePort, and local-path as the
default StorageClass.

### 5. Control plane — label the nodes

HAMi needs `gpu=on` and GShare scheduling needs `gshare.io/gpu-mode`. **The `up` path has
already applied the modes from `cluster-info`** — this step is only for the manual path.

```bash
./hack/cluster-bootstrap.sh label <gpu-node-1> exclusive
./hack/cluster-bootstrap.sh label <gpu-node-2> fractional
./hack/cluster-bootstrap.sh label <control-plane> cpu
```

### 6. Verify

```bash
./hack/cluster-bootstrap.sh verify
```

Checks that nodes are Ready, that `nvidia.com/gpu` and `nvidia.com/gpumem` capacity is
advertised, that the HAMi, ingress, flannel, and local-path pods are Running, and that a
default StorageClass exists.

### Storage node (optional): volumes with a real quota

local-path serves RWO only and enforces nothing: a 50 GiB volume shows the node's whole disk in
`df` and can fill it. A storage node fixes both. `STORAGE_NODE=user@ip:/dev/<device>` in
`cluster-info` makes `up` run, after the addons:

1. **`storage <device>`** on that node — installs `zfsutils-linux` and `nfs-kernel-server`,
   creates the pool `gshare` on the device (taken whole; several devices as a mirror or raidz via
   `STORAGE_VDEVS`), the datasets `gshare/volumes` and `gshare/snapshots`, caps the ARC at
   `ZFS_ARC_MAX_MB` (3 GiB), and creates the `gshare-csi` account: SSH key only, sudo restricted to
   the zfs/exportfs/chown commands the driver runs.
2. **`storage-csi <ip> <key>`** on the control plane — installs democratic-csi
   (`zfs-generic-nfs`, values in `deploy/storage/democratic-csi-values.yaml`) and the
   StorageClass **`gshare-data`**: one dataset per PVC with `refquota` = the claim, so inside a
   session `df` shows exactly the quota and writes past it fail with `ENOSPC`; RWO, RWX, and ROX
   all come from this one class; `allowVolumeExpansion` for approved quota increases.
3. **`label <node> storage`** — `gshare.io/role=storage` plus a `NoSchedule` taint, so sessions and
   the control plane never land on it.

Every node gets `nfs-common` from `prereqs`. Then point the operator at the class:
`--set operator.volumeStorageClass=gshare-data` (or uncomment it in the values overlay). The
operator reports each volume PVC to the control plane every `operator.volumeSyncInterval` (5m):
used bytes for the ledger, approved quota growth applied to the claim, and PVCs of volumes deleted
more than `api.volumeReclaimGraceHours` (24h) ago reclaimed — dataset included.

By hand, in that order: `sudo STORAGE_CSI_PUBKEY="$(cat key.pub)" bash cluster-bootstrap.sh storage /dev/vdb`
on the storage node, then `bash cluster-bootstrap.sh storage-csi <ip> key` and
`bash cluster-bootstrap.sh label <node> storage` on the control plane.

The pool is a single point of failure by design (no replication); take ZFS snapshots on the node
and replicate with `zfs send` off-box for durability.

### Local registry (optional)

Session images are large (5–15 GiB); on a LAN a local registry beats pulling from Docker Hub on
every node. Two pieces, both optional:

1. **The registry itself** — `deploy/registry/registry.yaml` runs two plain-HTTP registries on the
   storage node (`gshare.io/role=storage`): a push target on `:5000` for locally built session
   images, and a docker.io **pull-through mirror** on `:5001` so public catalogue images are
   fetched from the LAN after the first pull. `kubectl apply -f deploy/registry/registry.yaml`
   (adjust the nodeSelector / storageClassName in the header if your layout differs).
2. **Node trust** — containerd on every node must be told about them:

   ```bash
   sudo LOCAL_REGISTRY=<storage-ip>:5000 LOCAL_REGISTRY_MIRROR=<storage-ip>:5001 \
        bash cluster-bootstrap.sh registry     # per node; idempotent
   ```

   This writes `/etc/containerd/certs.d/<host:port>/hosts.toml` (and a `docker.io` entry routing
   pulls through the mirror, upstream as fallback) **and** sets `config_path` in `config.toml` —
   containerd 2.x silently ignores `certs.d` without it, and every pull ends in
   "server gave HTTP response to HTTPS client". The `up` path runs this on all nodes when
   `LOCAL_REGISTRY` is set in `cluster-info`. Moving to TLS later means dropping the CA next to
   `hosts.toml`; the layout stays.

Build and push the catalogue images to it from any docker host that lists the registry under
`"insecure-registries"`:

```bash
REG=<storage-ip>:5000 build/images/build.sh push   # builds all catalogue images, pushes to the LAN
```

then register them in the console (Images → the registry reference is
`<storage-ip>:5000/gshare-session:<tag>`); nodes pull straight from the LAN.

## Next — deploy GShare

With the cluster ready, the all-in-one install is the simplest path. The chart brings up
the data tier, secrets, CRDs, namespaces, the operator's internal JWT, and the local
cluster registration, with no manual prerequisites:

```bash
make deploy-incluster                          # deploy/values/incluster.yaml
ADMIN_JWT=$(./hack/bootstrap-superadmin.sh)    # optional: a super_admin token for admin API calls
```

Front it with your reverse proxy, terminating TLS for the console domain and forwarding to
the ingress-nginx NodePort (`:30080`, HTTP). For a production install against external
CloudNativePG, Redis, and external-secrets, use `make prod-deploy` with
`deploy/values/dockerhub.yaml`.

## Caveats

- This script **reproduces one known-good stack**. It is not guaranteed to work unmodified
  everywhere — driver versions, kernels, corporate proxies and registries, and firewall
  policy all force adjustments. Run `verify` after each stage.
- **Re-running is safe.** `prereqs`, `init`, `join`, and `addons` are idempotent: keyrings
  are overwritten, the swap line is not commented twice, and addons use `apply` or
  `helm upgrade -i`. `init` probes the API server's `/healthz` to decide what to do — if it
  is healthy the step is skipped; if only leftovers from a previous init are present
  (occupied ports, manifests, etcd data) it runs `kubeadm reset -f` and re-initialises. Set
  `INIT_FORCE_RESET=0` to be told about it instead. `join` is skipped when `kubelet.conf`
  exists. One exception: the containerd configuration is regenerated from defaults every
  time, so hand edits there are not preserved.
- The HAMi scheduler image tag is set to match the cluster version automatically
  (`scheduler.kubeScheduler.imageTag`), but check it against the HAMi compatibility matrix.
- Production hardening — multiple control planes, CephFS or NFS for RWX volumes,
  external-secrets with Vault or a KMS, and cosign verification with Kyverno — is out of
  scope for this script. See the reference configuration in the root
  [`README.md`](../README.md).
