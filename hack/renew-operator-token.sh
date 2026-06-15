#!/usr/bin/env bash
set -euo pipefail
# Re-issues the operator's internal JWT and refreshes the cluster secret.
#
# In the Compose-control-plane-plus-external-cluster topology, this is the Compose host doing what
# the all-in-one chart's CronJob (operator-token.yaml) does on Kubernetes. Without it the token
# expires, the operator's callbacks start returning 401, sessions get stuck, and inventory stops.
#
# The token is signed by the Compose gshare-api, which holds the private key. The secret is injected
# either over SSH using that host's kubectl (when MASTER is set, for a Compose host with no kubectl
# of its own), or with the local kubectl against the current KUBECONFIG.
#
# Usage:
#   CLUSTER_ID=clu_... [MASTER=ubuntu@10.0.0.10] [SYS_NS=gshare-system] [TTL=604800] \
#     hack/renew-operator-token.sh
# Daily cron: make dataplane-token-cron CLUSTER_ID=clu_... MASTER=ubuntu@10.0.0.10

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

: "${CLUSTER_ID:?CLUSTER_ID is required (clu_...)}"
MASTER="${MASTER:-}"
SYS_NS="${SYS_NS:-gshare-system}"
TTL="${TTL:-604800}"                  # 7 days, matching the chart default
SECRET="${OP_JWT_SECRET:-gshare-operator-internal-jwt}"
COMPOSE="${COMPOSE:-docker compose}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

# 1. Re-sign the internal JWT with the Compose api.
TOKEN="$($COMPOSE exec -T gshare-api python -c \
  "from app.auth.internal_jwt import sign_internal_jwt; print(sign_internal_jwt('operator:${CLUSTER_ID}', ttl=${TTL}))" 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "$(ts) FAILED to sign the token — is the Compose stack (gshare-api) running?" >&2
  exit 1
fi

# 2. Inject the secret. The token travels on stdin, so it never appears in argv or the process list.
remote_cmd="kubectl create secret generic ${SECRET} -n ${SYS_NS} --from-literal=internal-jwt=\"\$(cat)\" --dry-run=client -o yaml | kubectl apply -f -"
if [ -n "$MASTER" ]; then
  printf '%s' "$TOKEN" | ssh -o BatchMode=yes "$MASTER" "$remote_cmd" >/dev/null
else
  printf '%s' "$TOKEN" | bash -c "$remote_cmd" >/dev/null
fi

echo "$(ts) OK: refreshed ${SECRET} (cluster=${CLUSTER_ID}, ttl=${TTL}s, via=${MASTER:-local kubectl})"
