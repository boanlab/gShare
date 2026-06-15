#!/usr/bin/env bash
# O1 — live control-plane latency from cluster metrics (no workload needed).
# Run against the GShare cluster (kubectl context or via the master). Outputs the
# admission-webhook and operator-reconcile latency histograms → O1 overhead numbers (manuscript §Evaluation).
#   MASTER=ubuntu@10.10.10.162 bash docs/paper/eval/o1/cluster_metrics.sh
set -u
K() { if [ -n "${MASTER:-}" ]; then ssh -o BatchMode=yes "$MASTER" "kubectl $*"; else kubectl "$@"; fi; }

echo "== admission webhook (lend-guard) latency [apiserver metrics] =="
K get --raw /metrics 2>/dev/null \
  | grep -E 'apiserver_admission_webhook_admission_duration_seconds.*lend-guard' \
  | grep -E '_sum|_count|_bucket.*(0.005|0.025|0.1)"'

echo "== operator reconcile latency [controller-runtime metrics] =="
# operator metrics on :8080; query from an in-cluster curl Pod targeting the operator Pod IP.
OPIP=$(K -n gshare-system get pod -l app.kubernetes.io/name=gshare-operator -o jsonpath='{.items[0].status.podIP}' 2>/dev/null)
[ -z "$OPIP" ] && OPIP=$(K -n gshare-system get pod -o jsonpath='{range .items[*]}{.metadata.name}{" "}{.status.podIP}{"\n"}{end}' 2>/dev/null | awk '/operator/{print $2; exit}')
echo "operator podIP=$OPIP"
cat <<EOF | K apply -f - >/dev/null 2>&1
apiVersion: v1
kind: Pod
metadata: {name: o1-curl, namespace: gshare-system}
spec:
  restartPolicy: Never
  containers:
  - name: c
    image: curlimages/curl:latest
    command: ["sh","-c","curl -s http://${OPIP}:8080/metrics | grep -E 'controller_runtime_reconcile_time_seconds.*gsharesession' | grep -E '_sum|_count|le=.(0.005|0.01|0.1).'"]
EOF
for i in $(seq 1 20); do ph=$(K -n gshare-system get pod o1-curl -o jsonpath='{.status.phase}' 2>/dev/null); { [ "$ph" = "Succeeded" ] || [ "$ph" = "Failed" ]; } && break; sleep 3; done
K -n gshare-system logs o1-curl 2>/dev/null
K -n gshare-system delete pod o1-curl --wait=false >/dev/null 2>&1
