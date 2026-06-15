#!/usr/bin/env bash
set -euo pipefail
# GPU health leading to an automatic cordon.
# Injecting a fatal Xid annotation onto a node makes the operator's HealthReconciler cordon it, emit a
# NodeHealthEvent, set GpuNode.status to cordoned, and write an audit entry.
# Requires the operator, the internal JWT (provision-internal-jwt.sh), and populated inventory.
NS=gshare-system
NODE="${1:-kz-node-2}"          # the node to cordon; it has to exist as a GpuNode in the inventory
XID="${XID:-79}"               # fatal Xid: 48|74|79|94 (operator fatalXidCodes)

echo "[cordon] injecting a fatal Xid($XID) onto $NODE"
kubectl annotate node "$NODE" gshare.io/dcgm-xid="$XID" --overwrite
echo "[cordon] waiting 12s for the operator to reconcile"; sleep 12

echo "[cordon] result:"
kubectl get node "$NODE" --no-headers | awk '{print "  node:",$1,$2}'
kubectl -n "$NS" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "
select '  health_event: '||kind||'/'||severity||'/'||coalesce(action,'-') from node_health_event where node_id='$NODE' order by created_at desc limit 1;
select '  gpu_node.status='||status from gpu_node where id='$NODE';"

echo "[cordon] cleaning up and restoring:"
kubectl annotate node "$NODE" gshare.io/dcgm-xid- 2>/dev/null || true
kubectl uncordon "$NODE"
kubectl -n "$NS" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "update gpu_node set status='ready' where id='$NODE'; delete from node_health_event where node_id='$NODE';" >/dev/null
echo "[cordon] done."
