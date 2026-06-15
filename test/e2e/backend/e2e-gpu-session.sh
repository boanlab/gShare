#!/usr/bin/env bash
set -euo pipefail
# End-to-end lifecycle of an exclusive GPU session: creation (credit hold, VRAM precheck, Allocation,
# custom resource, operator pod) through termination (credit settle and refund, allocation release,
# custom resource deletion, the operator cleaning the pod up and returning the GPU).
# Requires the backend (deploy.sh), the operator (../operator/deploy.sh), seed.sql, seed-gpu.sql, and
# AUTH_ALLOW_LOCAL_PASSWORD=true.
NS=gshare-system
IDEM="${IDEM:-e2e-gpu-$(date +%s 2>/dev/null || echo manual)}"

echo "[gpu] POST /sessions (gpu/exclusive)"
SID=$(kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
import time, httpx
from jose import jwt
tok = jwt.encode({'sub':'usr_admin','email':'admin@example.com','global_role':'super_admin','exp':int(time.time())+86400}, 'gshare-dev-secret', algorithm='HS256')
body={'offering_id':'off_gpu_excl','image_id':'nginxinc/nginx-unprivileged:alpine','resource_class':'gpu','mode':'exclusive','cluster_id':'clu_fake','cluster_mode':'single','billing_wallet_id':'wal_demo'}
r=httpx.post('http://localhost:8000/api/v1/sessions', json=body, headers={'Authorization':'Bearer '+tok,'Idempotency-Key':'${IDEM}','Content-Type':'application/json'}, timeout=20)
print(r.json().get('id',''))" | tail -1)
echo "  session=$SID"
sleep 8
echo "[gpu] operator pod and GPU consumption:"
kubectl -n gshare-sessions get pod -o wide 2>&1 | grep -vE 'No resources'
kubectl describe node kz-node-2 | grep -A12 'Allocated' | grep 'nvidia.com/gpu' | sed 's/^/  GPU: /'

echo "[gpu] DELETE /sessions/$SID (terminate+settle)"
kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
import time, httpx
from jose import jwt
tok = jwt.encode({'sub':'usr_admin','email':'admin@example.com','global_role':'super_admin','exp':int(time.time())+86400}, 'gshare-dev-secret', algorithm='HS256')
r=httpx.delete('http://localhost:8000/api/v1/sessions/$SID', headers={'Authorization':'Bearer '+tok}, timeout=20)
print('  DELETE', r.status_code, r.text[:120])"
sleep 10
echo "[gpu] after termination (ledger hold, refund, settle; GPU returned):"
kubectl -n gshare-sessions get pod 2>&1 | grep -vE 'No resources' || echo "  pod cleaned up ✓"
kubectl -n "${NS}" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "
select '  session='||status from session where id='$SID';
select '  wallet reserved='||reserved from credit_wallet where id='wal_demo';
select '  txns: '||string_agg(type,',' order by created_at) from credit_transaction where wallet_id='wal_demo';"
