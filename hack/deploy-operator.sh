#!/usr/bin/env bash
set -euo pipefail
# Deploys the GShare operator to an external GPU cluster, and rotates its token.
# Register the cluster with the control plane first (under Compose or Kubernetes), then run this to
# bring the operator up on that cluster.
#
# Usage:
#   CLUSTER_ID=clu_... SOT_ENDPOINT=https://<control-plane> OPERATOR_TOKEN=<internal-jwt> \
#     [KUBECONFIG=/path/to/cluster.kubeconfig] [SESSION_DOMAIN=<the control plane's GSHARE_SESSION_DOMAIN>] \
#     ./hack/deploy-operator.sh
#   With SESSION_DOMAIN unset, the control plane's GSHARE_SESSION_DOMAIN from .env is used. The two
#   have to match, or session connections will not work.
#   To rotate an expiring token, re-run with a fresh OPERATOR_TOKEN: the secret is updated and the
#   deployment restarted.
#
# Mint a token from a Compose control plane with:
#   make compose-operator-token CLUSTER_ID=clu_...
# On a Kubernetes control plane the chart's hook Job issues and rotates it automatically, so this
# script's token path is for the Compose and external case.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRD="${CRD:-${ROOT}/operator/config/crd/bases/gshare.io_gsharesessions.yaml}"

: "${CLUSTER_ID:?CLUSTER_ID is required (the clu_... returned by registration)}"
: "${SOT_ENDPOINT:?SOT_ENDPOINT is required (the control plane URL, e.g. https://gshare.example.com)}"
: "${OPERATOR_TOKEN:?OPERATOR_TOKEN is required (the internal JWT; mint one with make compose-operator-token)}"
IMAGE="${IMAGE:-docker.io/boanlab/gshare-operator:latest}"
# SESSION_DOMAIN is the host of the session Ingress, and it *must* equal the control plane's
# GSHARE_SESSION_DOMAIN, because the control plane builds the connect URLs from that domain. A
# mismatch means the ingress host never matches and code-server, JupyterLab, and the terminal all
# 404. Unset, it defaults to GSHARE_SESSION_DOMAIN from the control plane's .env.
_env_session_domain() { [ -f "${ROOT}/.env" ] && sed -n 's/^GSHARE_SESSION_DOMAIN=//p' "${ROOT}/.env" | tail -1; }
SESSION_DOMAIN="${SESSION_DOMAIN:-$(_env_session_domain)}"
SESSION_DOMAIN="${SESSION_DOMAIN:-gshare.example.com}"
SESSION_NS="${SESSION_NS:-gshare-sessions}"
SYS_NS="${SYS_NS:-gshare-system}"
SOT="${SOT_ENDPOINT%/}"

kc(){ kubectl "$@"; }   # KUBECONFIG points at the target cluster; unset, the current context is used

if [ "${SESSION_DOMAIN}" = "gshare.example.com" ]; then
  echo "[deploy-operator][warning] SESSION_DOMAIN is still the placeholder gshare.example.com." >&2
  echo "    Set it to the same real domain as the control plane's GSHARE_SESSION_DOMAIN, or sessions will not be reachable." >&2
fi

echo "[deploy-operator] cluster=${CLUSTER_ID} sot=${SOT} image=${IMAGE} session-domain=${SESSION_DOMAIN}"
echo "[deploy-operator] 1/3 applying the CRD"
kc apply -f "${CRD}" >/dev/null

echo "[deploy-operator] 2/3 applying namespace, service account, RBAC, secret, and deployment"
kc apply -f - <<YAML >/dev/null
apiVersion: v1
kind: Namespace
metadata: { name: ${SYS_NS} }
---
apiVersion: v1
kind: Namespace
metadata:
  name: ${SESSION_NS}
  labels:
    # Session pods run non-root, drop all capabilities, and set a seccomp profile, which satisfies
    # restricted Pod Security; see the operator's podbuilder.
    pod-security.kubernetes.io/enforce: restricted
---
apiVersion: v1
kind: ServiceAccount
metadata: { name: gshare-operator, namespace: ${SYS_NS} }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata: { name: gshare-operator }
rules:
  - { apiGroups: ["gshare.io"], resources: ["gsharesessions","gsharesessions/status","gsharesessions/finalizers"], verbs: ["get","list","watch","update","patch"] }
  - { apiGroups: [""], resources: ["pods","services","secrets"], verbs: ["get","list","watch","create","update","patch","delete"] }
  - { apiGroups: ["networking.k8s.io"], resources: ["ingresses"], verbs: ["get","list","watch","create","update","patch","delete"] }
  - { apiGroups: [""], resources: ["nodes"], verbs: ["get","list","watch","patch"] }
  - { apiGroups: ["node.k8s.io"], resources: ["runtimeclasses"], verbs: ["get","list"] }
  - { apiGroups: ["coordination.k8s.io"], resources: ["leases"], verbs: ["get","list","watch","create","update","patch","delete"] }
  - { apiGroups: [""], resources: ["events"], verbs: ["create","patch"] }
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata: { name: gshare-operator }
roleRef: { apiGroup: rbac.authorization.k8s.io, kind: ClusterRole, name: gshare-operator }
subjects: [ { kind: ServiceAccount, name: gshare-operator, namespace: ${SYS_NS} } ]
---
apiVersion: v1
kind: Secret
metadata: { name: gshare-operator-internal-jwt, namespace: ${SYS_NS} }
type: Opaque
data: { internal-jwt: $(printf '%s' "${OPERATOR_TOKEN}" | base64 -w0) }
---
apiVersion: apps/v1
kind: Deployment
metadata: { name: gshare-operator, namespace: ${SYS_NS}, labels: { app.kubernetes.io/name: gshare-operator } }
spec:
  replicas: 1
  selector: { matchLabels: { app.kubernetes.io/name: gshare-operator } }
  template:
    metadata: { labels: { app.kubernetes.io/name: gshare-operator } }
    spec:
      serviceAccountName: gshare-operator
      automountServiceAccountToken: true
      containers:
        - name: operator
          image: ${IMAGE}
          imagePullPolicy: Always
          command: ["/manager"]
          args:
            - "--leader-elect"
            - "--session-namespace=${SESSION_NS}"
            - "--session-domain=${SESSION_DOMAIN}"
            - "--connect-verify-url=${SOT}/internal/connect/verify"
            - "--sot-endpoint=${SOT}"
            - "--internal-jwks-url=${SOT}/.well-known/gshare-internal-jwks.json"
            - "--cluster-id=${CLUSTER_ID}"
          env:
            - { name: INTERNAL_JWT_TOKEN_FILE, value: /var/run/gshare/internal-jwt }
          ports:
            - { name: metrics, containerPort: 8080 }
            - { name: healthz, containerPort: 8081 }
          readinessProbe: { httpGet: { path: /readyz, port: healthz }, initialDelaySeconds: 5 }
          livenessProbe: { httpGet: { path: /healthz, port: healthz }, initialDelaySeconds: 15 }
          volumeMounts: [ { name: internal-jwt, mountPath: /var/run/gshare, readOnly: true } ]
      volumes:
        - { name: internal-jwt, secret: { secretName: gshare-operator-internal-jwt, items: [ { key: internal-jwt, path: internal-jwt } ] } }
YAML

echo "[deploy-operator] 3/3 restarting the rollout to pick up the new token and image, then waiting"
kc -n "${SYS_NS}" rollout restart deploy/gshare-operator >/dev/null
kc -n "${SYS_NS}" rollout status deploy/gshare-operator --timeout=120s
echo "[deploy-operator] done. The operator now reports status and inventory to ${SOT}."
