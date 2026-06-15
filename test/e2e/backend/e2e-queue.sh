#!/usr/bin/env bash
set -euo pipefail
# Verifies the queue on a *real* GPU by oversubscribing the VRAM of one physical card in the
# fractional pool:
#   session A reserves, session B finds no capacity and is queued, A terminates, queue_ticker dequeues
#   B, and B runs.
#
# Requires real HAMi, the backend (deploy.sh), the operator (../operator/deploy.sh), the internal JWT,
# seed-gpu.sql, a fractional node (gshare.io/gpu-mode=fractional), and the off_gpu_frac offering.
#
# Note that the worker needs write RBAC on gsharesessions (service account gshare-api), because the
# dequeue handoff happens in the worker.
NS=gshare-system
FRAC_NODE="${FRAC_NODE:-gpu2-2}"            # the fractional node, a real RTX 4090
REQ_MEM="${REQ_MEM:-16000}"                 # VRAM per session in MB; over half the card, so a second one cannot fit
REQ_CORES="${REQ_CORES:-40}"

tok() { kubectl -n "$NS" exec deploy/gshare-api -c api -- python -c "
import time;from jose import jwt;print(jwt.encode({'sub':'usr_admin','email':'admin@example.com','global_role':'super_admin','exp':int(time.time())+86400},'gshare-dev-secret',algorithm='HS256'))"; }
post() { kubectl -n "$NS" exec deploy/gshare-api -c api -- python -c "
import httpx;r=httpx.post('http://localhost:8000/api/v1/sessions',json=$1,headers={'Authorization':'Bearer $2','Idempotency-Key':'$3','Content-Type':'application/json'},timeout=20);print(r.status_code,r.json().get('id',''),r.json().get('status',''))"; }
delete() { kubectl -n "$NS" exec deploy/gshare-api -c api -- python -c "
import httpx;httpx.delete('http://localhost:8000/api/v1/sessions/$2',headers={'Authorization':'Bearer $1'},timeout=20)"; }
q() { kubectl -n "$NS" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "$1" 2>/dev/null | tr -d ' \r'; }

T=$(tok)
REQ="{\"offering_id\":\"off_gpu_frac\",\"image_id\":\"nginxinc/nginx-unprivileged:alpine\",\"resource_class\":\"gpu\",\"mode\":\"fractional\",\"gpu_mem_mb\":${REQ_MEM},\"gpu_cores\":${REQ_CORES},\"cluster_id\":\"clu_fake\",\"cluster_mode\":\"single\",\"billing_wallet_id\":\"wal_demo\"}"

echo "[q] 1) session A (${REQ_MEM}MB) reserves on ${FRAC_NODE}"
A=$(post "$REQ" "$T" qA-real | awk '{print $2}'); echo "    A=$A"
sleep 6
echo "[q] 2) session B (${REQ_MEM}MB) finds no capacity and is queued"
post "$REQ" "$T" qB-real
B=$(q "select id from session where status='pending' order by created_at desc limit 1")
sleep 2
echo "[q]   queued=$(q "select count(*) from queue_entry")  B=$B  used_mem(${FRAC_NODE})=$(q "select used_mem_mb from gpu_device where node_id='${FRAC_NODE}'")"

echo "[q] 3) A terminates and queue_ticker dequeues B (needs the worker service account RBAC)"
delete "$T" "$A"
for _ in $(seq 1 24); do [ "$(q "select count(*) from queue_entry")" = "0" ] && break; sleep 5; done
for _ in $(seq 1 24); do [ "$(q "select status from session where id='$B'")" = "running" ] && break; sleep 5; done

echo "[q] 4) result:"
echo "    queued after termination=$(q "select count(*) from queue_entry")"
echo "    B status=$(q "select status from session where id='$B'")"
echo "    B allocation=$(q "select status||' '||gpu_uuid from allocation a join session s on a.session_id=s.id where s.id='$B'")"
kubectl -n gshare-sessions get pod -o wide 2>&1 | grep -vE 'No resources' || true

echo "[q] cleaning up: terminating B"
delete "$T" "$B"
echo "[q] done."
