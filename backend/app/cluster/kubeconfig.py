"""Resolve a cluster's kubeconfig from a mounted file — never persisted in the DB.

The control plane never stores plaintext kubeconfig; it reads a file projected by external-secrets
(k8s) or mounted directly (compose / bare-metal). The base directory is ``CLUSTER_KUBECONFIG_DIR``
so non-external-secrets deploys can supply the file at ``<dir>/<cluster_id>/kubeconfig``.
"""
from __future__ import annotations

import os


def resolve_cluster_kubeconfig(secret_ref: str | None, base_dir: str) -> str | None:
    """Return the kubeconfig YAML for ``secret_ref``, or None if no mounted file exists.

    Resolution order (first existing file wins):
      1. ``secret_ref`` itself when it is an absolute path
      2. ``<base_dir>/<cluster_id>/kubeconfig`` — cluster_id parsed from ``secret://.../{id}``
         (compose / bare-metal friendly, keyed by the stable cluster id)
      3. ``<base_dir>/<secret_ref>/kubeconfig`` — legacy external-secrets projection convention
    """
    if not secret_ref:
        return None
    base = base_dir.rstrip("/")
    candidates: list[str] = []
    if os.path.isabs(secret_ref):
        candidates.append(secret_ref)
    cluster_id = secret_ref.rsplit("/", 1)[-1]
    if cluster_id and cluster_id != secret_ref:
        candidates.append(f"{base}/{cluster_id}/kubeconfig")
    candidates.append(f"{base}/{secret_ref}/kubeconfig")
    for path in candidates:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as fh:
                return fh.read()
    return None
