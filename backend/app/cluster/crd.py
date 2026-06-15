"""GShareSession CR serialization + apply.

CRD-primary: serialize the admitted session to a GShareSession.spec and apply it to the target
cluster's apiserver via the kubernetes-asyncio CustomObjects API — **CR apply only**. No workload
K8s API calls. The operator reconciles spec -> Pod/Service/Ingress and writes.status.

Pod mode rules carried in spec (operator builds the Pod):
  fractional -> schedulerName=hami-scheduler + nvidia.com/gpumem + nvidia.com/gpucores
  exclusive -> nvidia.com/gpu:1 only, NO hami-scheduler (bypasses HAMi)
  mig -> nvidia.com/mig-*
  cpu -> no GPU, nodeSelector gshare.io/node-type=cpu
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFound
from app.db.models import Cluster, Project, ResourcePolicy

GROUP = "gshare.io"
VERSION = "v1"  # matches the operator CRD: gshare.io/v1, a camelCase structural schema
PLURAL = "gsharesessions"

# Operator-managed namespace that owns reconciled workloads.
SESSION_NAMESPACE = "gshare-sessions"
# W3C traceparent is carried over the async CR boundary as an annotation.
TRACEPARENT_ANNOTATION = f"{GROUP}/traceparent"
HAMI_SCHEDULER = "hami-scheduler"


# Maps the internal snake_case spec (to_session_spec) onto the operator's camelCase structural CRD
# schema (gshare.io/v1). The CRD rejects unknown fields, so only permitted keys are emitted, and
# anything the operator computes itself is left out: session_id becomes metadata.name, and
# node_selector, scheduler_name, and the gpu object are the operator's to derive.
_CRD_KEY_MAP = {
    "cluster_id": "clusterId",
    "resource_class": "resourceClass",
    "cluster_mode": "clusterMode",
    "offering_id": "offeringId",
    "group_id": "projectId",
    "billing_wallet_id": "billingWalletId",
    "gpu_mem_mb": "gpuMemMb",
    "gpu_cores": "gpuCores",
    "cpu": "cpu",
    "mem_gb": "memGb",
    "disk_gb": "diskGb",
    "image": "image",
    "owner": "owner",
    "mode": "mode",
    "paused": "paused",
    "lossless_pause": "losslessPause",
    "pause_mode": "pauseMode",
    "preemptible": "preemptible",
    "borrowed_gpu_uuid": "borrowedGpuUuid",
    "borrowed_node": "borrowedNode",
}


def _cr_name(session_id: str) -> str:
    """Convert a session id to a Kubernetes object name (RFC 1123).

    A session id (ses_<ULID>) contains uppercase characters and an underscore, so it is lower-cased
    and `_` becomes `-`. apply, get, patch, and delete all use this same conversion."""
    return session_id.lower().replace("_", "-")


def _to_crd_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for snake, camel in _CRD_KEY_MAP.items():
        if spec.get(snake) is not None:
            out[camel] = spec[snake]
    # MIG profile: the internal spec encodes it as gpu.resource=nvidia.com/mig-<profile>, which is
    # unpacked back into migProfile.
    gpu = spec.get("gpu") or {}
    res = str(gpu.get("resource", ""))
    if res.startswith("nvidia.com/mig-"):
        out["migProfile"] = res[len("nvidia.com/mig-"):]
    conn = spec.get("connect") or {}
    connect: dict[str, Any] = {}
    if conn.get("proxy_token_secret_ref"):
        connect["proxyTokenSecretRef"] = conn["proxy_token_secret_ref"]
    if connect:
        out["connect"] = connect
    vols = []
    for v in spec.get("volumes") or []:
        # Translate the mount mode (rw or ro) into the CRD's Kubernetes access-mode enum. The
        # operator treats only ReadOnlyMany as read-only, so ro maps to ReadOnlyMany and rw to
        # ReadWriteMany.
        vols.append({
            "name": v.get("volume_id"),
            "mountPath": v.get("mount_path"),
            "mode": "ReadOnlyMany" if v.get("mode") == "ro" else "ReadWriteMany",
        })
    if vols:
        out["volumes"] = vols
    return out


class CustomObjectsClient(Protocol):
    """Minimal async surface of kubernetes_asyncio.client.CustomObjectsApi we depend on.

    Kept narrow so tests can substitute a fake. Only namespaced custom-object
    verbs against gshare.io/v1 gsharesessions are used — never workload K8s APIs.
    """

    async def get_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str
    ) -> dict[str, Any]: ...

    async def patch_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str, body: Any,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    async def patch_namespaced_custom_object_status(
        self, group: str, version: str, namespace: str, plural: str, name: str, body: Any,
        **kwargs: Any,
    ) -> dict[str, Any]: ...

    async def delete_namespaced_custom_object(
        self, group: str, version: str, namespace: str, plural: str, name: str, **kwargs: Any,
    ) -> dict[str, Any]: ...


# A factory resolves a per-cluster CustomObjects client (async ctx mgr) from a Cluster row.
ClientFactory = Callable[[Cluster], Awaitable["_ClientSession"]]


class _ClientSession:
    """Async context manager yielding a CustomObjectsClient + closing the underlying ApiClient."""

    def __init__(self, api: CustomObjectsClient, closer: Callable[[], Awaitable[None]] | None):
        self._api = api
        self._closer = closer

    async def __aenter__(self) -> CustomObjectsClient:
        return self._api

    async def __aexit__(self, *exc: Any) -> None:
        if self._closer is not None:
            await self._closer()


class GShareSessionCRD:
    """Builds and applies GShareSession custom resources."""

    def __init__(
        self,
        db: AsyncSession | None = None,
        client_factory: ClientFactory | None = None,
    ):
        # db is needed to resolve the target Cluster row (kubeconfig/SA secret ref). It is optional
        # so pure serialization (to_session_spec) and payload tests work without a session.
        self.db = db
        # Injectable for tests/offline; defaults to the real kubeconfig-backed factory.
        self._client_factory = client_factory or self._default_client_factory

    # ── serialization ──
    def to_session_spec(self, sess, req, image_ref: str | None = None) -> dict[str, Any]:
        """Serialize a session + request into a GShareSession.spec dict.

        Returns a plain dict (snake_case) carrying ONLY the desired-state fields the operator
        needs to build the Pod. Mode rules are encoded so payload tests can
        assert them directly:
        - exclusive: no scheduler_name, no gpu_mem_mb/gpu_cores; gpu.resource=nvidia.com/gpu count 1.
        - fractional: scheduler_name=hami-scheduler; gpu_mem_mb and gpu_cores present.
        - mig: gpu.resource=nvidia.com/mig-*; no hami-scheduler.
        - cpu: no GPU; node_selector gshare.io/node-type=cpu.
        """
        resource_class = getattr(req, "resource_class", None) or sess.resource_class
        # CPU sessions carry no GPU mode; GPU sessions prefer the persisted session
        # mode, falling back to the request.
        mode = None
        if resource_class != "cpu":
            mode = getattr(sess, "mode", None) or getattr(req, "mode", None)

        spec: dict[str, Any] = {
            "cluster_id": getattr(req, "cluster_id", None) or sess.cluster_id,
            "session_id": sess.id,
            "resource_class": resource_class,
            "cluster_mode": getattr(req, "cluster_mode", None) or sess.cluster_mode,
            "offering_id": getattr(req, "offering_id", None) or sess.offering_id,
            "image": image_ref or self._image_ref(sess, req),
            "owner": sess.owner_user_id,
            "group_id": getattr(req, "group_id", None) or sess.group_id,
            # CPU, RAM, and disk, snapshotted from the offering flavor. Common to GPU and CPU
            # sessions; the pod sets request = limit.
            "cpu": sess.cpu,
            "mem_gb": sess.mem_gb,
            "disk_gb": sess.disk_gb,
            "volumes": self._volumes(req),
            # Connect credentials live in a per-session Secret (ses-<cr-name>-secret) the operator
            # creates and references. proxyTokenSecretRef is only set when pointing at an existing
            # external Secret, and stays empty on the default path.
            "connect": {},
        }
        # Mark sessions eligible for lossless pause, so an operator-driven pause — from the console
        # or the idle reaper — attempts a checkpoint.
        if getattr(sess, "lossless_pause", False):
            spec["lossless_pause"] = True
        # in-place yield: on pause the operator keeps the pod and evicts VRAM, so the resume is
        # lossless. preemptible marks the session as spot-eligible.
        if getattr(sess, "pause_mode", "cold") == "yield":
            spec["pause_mode"] = "yield"
        if getattr(sess, "preemptible", False):
            spec["preemptible"] = True

        if resource_class == "cpu":
            # CPU sessions request no GPU and pin to the cpu node pool.
            spec["node_selector"] = {"gshare.io/node-type": "cpu"}
            return spec

        # GPU session — mode decides scheduler + resource shape.
        spec["mode"] = mode
        if mode == "fractional":
            # HAMi hard-isolation: hami-scheduler + gpumem + gpucores.
            spec["scheduler_name"] = HAMI_SCHEDULER
            spec["gpu_mem_mb"] = sess.gpu_mem_mb if sess.gpu_mem_mb is not None else req.gpu_mem_mb
            spec["gpu_cores"] = sess.gpu_cores if sess.gpu_cores is not None else req.gpu_cores
            spec["gpu"] = {"resource": "nvidia.com/gpu", "count": 1}
        elif mode == "exclusive":
            # Standard NVIDIA device-plugin, bypasses HAMi: nvidia.com/gpu:1 only, NO
            # scheduler_name, NO gpu_mem_mb/gpu_cores.
            spec["gpu"] = {"resource": "nvidia.com/gpu", "count": 1}
        elif mode == "mig":
            # MIG profile via nvidia.com/mig-*; no hami-scheduler.
            profile = self._mig_profile(sess, req)
            spec["gpu"] = {"resource": f"nvidia.com/mig-{profile}", "count": 1}
        return spec

    def build_object(self, spec: dict[str, Any], traceparent: str | None = None) -> dict[str, Any]:
        """Wrap a spec dict into a full GShareSession custom-object body."""
        from app.core.config import settings  # lazy: keep module import-light/sandbox-safe

        annotations: dict[str, str] = {}
        if traceparent:
            annotations[TRACEPARENT_ANNOTATION] = traceparent
        # Stamp the idle reaper policy onto the custom resource, where the operator's IdleReaper
        # reads it. The resolved policy value (spec.idle_timeout_sec) wins, falling back to the
        # global default.
        idle_sec = spec.get("idle_timeout_sec") or settings.IDLE_TIMEOUT_SEC
        if idle_sec and int(idle_sec) > 0:
            annotations[f"{GROUP}/idle-timeout-sec"] = str(int(idle_sec))
        # Stamp the absolute lifetime cap (max runtime), which the operator reaper reads for
        # maxRuntimeExceeded. Only a resolved policy value is stamped (spec.max_runtime_sec,
        # converted from ResourcePolicy.max_runtime in minutes). Unset means no cap and no
        # annotation — unlike idle, there is deliberately no global fallback.
        max_sec = spec.get("max_runtime_sec")
        if max_sec and int(max_sec) > 0:
            annotations[f"{GROUP}/max-runtime-sec"] = str(int(max_sec))
        metadata: dict[str, Any] = {
            "name": _cr_name(spec["session_id"]),
            "namespace": SESSION_NAMESPACE,
            "labels": {f"{GROUP}/session": _cr_name(spec["session_id"])},
        }
        if annotations:
            metadata["annotations"] = annotations
        return {
            "apiVersion": f"{GROUP}/{VERSION}",
            "kind": "GShareSession",
            "metadata": metadata,
            "spec": _to_crd_spec(spec),
        }

    # ── apply / get / patch-status / delete ──
    async def _resolve_idle_timeout_sec(self, spec: dict[str, Any]) -> int | None:
        """Resolve the idle timeout through the policy hierarchy: user, group, organization, global.

        Takes the most specific policy with idle_timeout_sec > 0. None means build_object falls back
        to the global default, settings.IDLE_TIMEOUT_SEC.
        """
        if self.db is None:
            return None

        async def _scoped(scope: str, scope_id: str) -> int | None:
            pol = (
                await self.db.scalars(
                    select(ResourcePolicy).where(
                        ResourcePolicy.scope == scope, ResourcePolicy.scope_id == scope_id
                    )
                )
            ).first()
            val = getattr(pol, "idle_timeout", None) if pol is not None else None
            return int(val) if val and int(val) > 0 else None

        owner = spec.get("owner")
        group_id = spec.get("group_id")
        if owner:
            v = await _scoped("user", owner)
            if v is not None:
                return v
        if group_id:
            v = await _scoped("group", group_id)
            if v is not None:
                return v
            project = await self.db.get(Project, group_id)
            if project is not None and project.org_id:
                v = await _scoped("org", project.org_id)
                if v is not None:
                    return v
        return await _scoped("global", "*")

    async def _resolve_max_runtime_sec(self, spec: dict[str, Any]) -> int | None:
        """Resolve the absolute lifetime cap through the policy hierarchy: user, group,
        organization, global.

        Takes the most specific policy with max_runtime > 0 (ResourcePolicy.max_runtime is in
        **minutes**) and converts it to **seconds**, since the operator's gshare.io/max-runtime-sec is
        in seconds. Unlike idle, there is no global fallback: when no scope sets a cap the result is
        None and no annotation is attached.
        """
        if self.db is None:
            return None

        async def _scoped(scope: str, scope_id: str) -> int | None:
            pol = (
                await self.db.scalars(
                    select(ResourcePolicy).where(
                        ResourcePolicy.scope == scope, ResourcePolicy.scope_id == scope_id
                    )
                )
            ).first()
            val = getattr(pol, "max_runtime", None) if pol is not None else None
            return int(val) * 60 if val and int(val) > 0 else None

        owner = spec.get("owner")
        group_id = spec.get("group_id")
        if owner:
            v = await _scoped("user", owner)
            if v is not None:
                return v
        if group_id:
            v = await _scoped("group", group_id)
            if v is not None:
                return v
            project = await self.db.get(Project, group_id)
            if project is not None and project.org_id:
                v = await _scoped("org", project.org_id)
                if v is not None:
                    return v
        return await _scoped("global", "*")

    async def set_paused(
        self, cluster_id: str, session_id: str, paused: bool,
        *, pause_mode: str | None = None, preemptible: bool | None = None,
        graceful_demote: bool | None = None,
    ) -> None:
        """Patch spec.paused (+ pauseMode/preemptible) on the session CR.

        Merge-patch keeps every other spec field intact — used for pause(true)/resume(false) without
        re-serializing the whole desired state. pauseMode is re-asserted alongside it so the
        operator's pause branch (spec.pauseMode == yield) agrees with the backend's yield decision
        in the database. The operator reports phase Paused / Running back. """
        cluster = await self._load_cluster(cluster_id)
        name = _cr_name(session_id)
        # JSON Patch `add`: the default content type is json-patch, and `add` also works on fields
        # that are not yet set.
        body = [{"op": "add", "path": "/spec/paused", "value": bool(paused)}]
        if pause_mode is not None:
            body.append({"op": "add", "path": "/spec/pauseMode", "value": str(pause_mode)})
        if preemptible is not None:
            body.append({"op": "add", "path": "/spec/preemptible", "value": bool(preemptible)})
        if graceful_demote is not None:
            # yield→cold demotion: operator toggles VRAM back + lets the job checkpoint on SIGTERM
            # before deleting the Pod (fresh checkpoint). Set only when the card is not lent.
            body.append({"op": "add", "path": "/spec/gracefulDemote", "value": bool(graceful_demote)})
        async with await self._client_factory(cluster) as api:
            await api.patch_namespaced_custom_object(
                group=GROUP, version=VERSION, namespace=SESSION_NAMESPACE, plural=PLURAL,
                name=name, body=body,
            )

    async def apply(
        self, cluster_id: str, spec: dict[str, Any], traceparent: str | None = None
    ) -> None:
        """Apply (create/patch) the GShareSession CR to the target cluster.

        Loads the cluster's bootstrap kubeconfig/SA (from secret ref), builds an async
        CustomObjectsApi, and server-side-applies gshare.io/v1 gsharesessions. CR apply ONLY.
        Idempotent: server-side apply (force=True, our field manager) creates or patches the same
        named object, so repeated handoffs converge to the desired spec.
        """
        cluster = await self._load_cluster(cluster_id)
        # Resolve the idle timeout and lifetime cap through the policy hierarchy (user, group,
        # organization, global) and stamp them onto the custom resource.
        spec = {
            **spec,
            "idle_timeout_sec": await self._resolve_idle_timeout_sec(spec),
            "max_runtime_sec": await self._resolve_max_runtime_sec(spec),
        }
        body = self.build_object(spec, traceparent)
        name = body["metadata"]["name"]
        async with await self._client_factory(cluster) as api:
            # Idempotent create-or-patch: create when new, merge-patch on a 409. Server-side apply
            # is not used because it requires the apply content type, which merge patch rejects with
            # 422.
            try:
                await api.create_namespaced_custom_object(
                    group=GROUP, version=VERSION, namespace=SESSION_NAMESPACE, plural=PLURAL,
                    body=body,
                )
            except Exception as exc:  # noqa: BLE001
                if getattr(exc, "status", None) == 409:   # AlreadyExists: update the existing object
                    await api.patch_namespaced_custom_object(
                        group=GROUP, version=VERSION, namespace=SESSION_NAMESPACE, plural=PLURAL,
                        name=name, body=body,
                    )
                else:
                    raise

    async def get(self, cluster_id: str, session_id: str) -> dict[str, Any] | None:
        """Read the live GShareSession CR (incl..status) from the target cluster."""
        cluster = await self._load_cluster(cluster_id)
        async with await self._client_factory(cluster) as api:
            try:
                return await api.get_namespaced_custom_object(
                    group=GROUP, version=VERSION, namespace=SESSION_NAMESPACE, plural=PLURAL,
                    name=_cr_name(session_id),
                )
            except Exception as exc:  # noqa: BLE001
                if _is_not_found(exc):
                    return None
                raise

    async def delete(self, cluster_id: str, session_id: str) -> None:
        """Delete the GShareSession CR; the operator finalizer tears down the workload."""
        cluster = await self._load_cluster(cluster_id)
        async with await self._client_factory(cluster) as api:
            try:
                await api.delete_namespaced_custom_object(
                    group=GROUP, version=VERSION, namespace=SESSION_NAMESPACE, plural=PLURAL,
                    name=_cr_name(session_id),
                )
            except Exception as exc:  # noqa: BLE001
                if _is_not_found(exc):
                    return
                raise

    # ── internal helpers ──
    async def _load_cluster(self, cluster_id: str) -> Cluster:
        if self.db is None:
            raise RuntimeError("GShareSessionCRD requires a db session to apply CRs")
        row = (
            await self.db.execute(select(Cluster).where(Cluster.id == cluster_id))
        ).scalar_one_or_none()
        if row is None or getattr(row, "deleted_at", None) is not None:
            raise NotFound("cluster", {"cluster_id": cluster_id})
        return row

    async def _default_client_factory(self, cluster: Cluster) -> _ClientSession:
        """Build an async CustomObjectsApi for the cluster from its stored kubeconfig/SA secret ref.

        kubernetes_asyncio is imported lazily so the module compiles/serializes without a live
        cluster or the client installed in the sandbox. The kubeconfig/SA token is
        resolved from ``cluster.kubeconfig_secret_ref`` (never stored in plaintext)
        and used ONLY for CR apply — never for workload K8s APIs.
        """
        from kubernetes_asyncio import client, config  # lazy import (sandbox-safe)

        configuration = client.Configuration()
        await self._load_cluster_credentials(cluster, configuration, config)
        api_client = client.ApiClient(configuration)
        api = client.CustomObjectsApi(api_client)

        async def _close() -> None:
            await api_client.close()

        return _ClientSession(api, _close)

    async def _load_cluster_credentials(self, cluster: Cluster, configuration, config) -> None:
        """Populate ``configuration`` (host + bearer/CA) from the cluster's secret-ref kubeconfig.

        The kubeconfig YAML lives in the secret referenced by ``cluster.kubeconfig_secret_ref``
        (resolved by external-secrets); we never persist plaintext kubeconfig in the DB.
        """
        kubeconfig_yaml = await self._read_secret(cluster.kubeconfig_secret_ref)
        if kubeconfig_yaml:
            import yaml  # lazy
            loader = config.kube_config.KubeConfigLoader(
                config_dict=yaml.safe_load(kubeconfig_yaml)
            )
            await loader.load_and_set(configuration)
        else:
            # In-cluster SA fallback for a per-cluster agent / single-cluster dev.
            config.load_incluster_config(client_configuration=configuration)
        # Always honor the registered apiserver endpoint when present.
        if cluster.api_server:
            configuration.host = cluster.api_server

    async def _read_secret(self, secret_ref: str | None) -> str | None:
        """Resolve a secret reference to its kubeconfig payload (mounted file; never in DB).

        See app.cluster.kubeconfig.resolve_cluster_kubeconfig — external-secrets (k8s) or a host
        mount (compose/bare-metal) projects the file under CLUSTER_KUBECONFIG_DIR; we only read it.
        Returns None when no file exists (-> in-cluster SA fallback).
        """
        from app.cluster.kubeconfig import resolve_cluster_kubeconfig
        from app.core.config import settings

        return resolve_cluster_kubeconfig(secret_ref, settings.CLUSTER_KUBECONFIG_DIR)

    def _image_ref(self, sess, req) -> str:
        """Fallback container ref when the handoff didn't resolve Image.registry (offline/tests).

        The operator pulls this verbatim, so the resolved registry ref (passed as image_ref to
        to_session_spec) is strongly preferred; the raw image id here is only a last resort.
        """
        for attr in ("image_ref", "image"):
            val = getattr(sess, attr, None)
            if val:
                return str(val)
        return str(getattr(sess, "image_id", None) or getattr(req, "image_id", ""))

    def _volumes(self, req) -> list[dict[str, Any]]:
        mounts = getattr(req, "volume_mounts", None) or []
        out: list[dict[str, Any]] = []
        for m in mounts:
            out.append({
                "volume_id": getattr(m, "volume_id", None),
                "mount_path": getattr(m, "mount_path", None),
                "mode": getattr(m, "mode", "rw"),
            })
        return out

    def _mig_profile(self, sess, req) -> str:
        """MIG profile string (e.g. 1g.5gb). Carried on the request/session when mode=mig."""
        return (
            getattr(req, "mig_profile", None)
            or getattr(sess, "mig_profile", None)
            or "1g.5gb"
        )


def _is_not_found(exc: Exception) -> bool:
    """True if a kubernetes_asyncio ApiException is a 404 (so get/delete can be idempotent)."""
    return getattr(exc, "status", None) == 404 or getattr(exc, "reason", None) == "Not Found"
