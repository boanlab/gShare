# kubeconfigs for externally registered clusters

When the control plane runs under Docker Compose and drives an **external GPU cluster**,
drop that cluster's kubeconfig here. The control plane (api and worker) reads it to apply
custom resources. The kubeconfig is never stored in the database in plaintext — this file
is the only copy the control plane sees.

```
deploy/clusters/<cluster_id>/kubeconfig
```

- `<cluster_id>` is the `clu_…` id returned by `POST /api/v1/clusters`, or by registering
  the cluster from the console.
- Compose mounts this directory read-only at `/run/gshare/clusters`, and the backend reads
  `<cluster_id>/kubeconfig` under `GSHARE_CLUSTER_KUBECONFIG_DIR`.
- The kubeconfig files are **not committed** (see `.gitignore`). Only this README is tracked.

The full procedure is in [`docs/cluster-connect.md`](../../docs/cluster-connect.md).
