#!/usr/bin/env bash
set -euo pipefail
# The idle reaper scenario: with a short idle_timeout, the operator's IdleReaper pauses an inactive
# GPU session automatically. An idle GPU session is reclaimed by pausing, not terminating — the GPU
# comes back and the session is preserved. Termination is reserved for the max-runtime cap and idle
# CPU sessions.
#
# Without a DCGM collector, utilisation reads as 0 (always idle), so the session is reaped once the
# custom resource's idle-timeout-sec elapses.
#
# This script shortens GSHARE_IDLE_TIMEOUT_SEC to 20 seconds, creates a session, confirms the
# automatic pause, and restores the original value.
# Requires the backend, the operator, the internal JWT, and seed-gpu.sql; inventory populates itself.
NS=gshare-system
tokpost() { kubectl -n "$NS" exec deploy/gshare-api -c api -- python -c "
import time, httpx
from jose import jwt
tok=jwt.encode({'sub':'usr_admin','email':'admin@example.com','global_role':'super_admin','exp':int(time.time())+86400},'gshare-dev-secret',algorithm='HS256')
body={'offering_id':'off_gpu_excl','image_id':'nginxinc/nginx-unprivileged:alpine','resource_class':'gpu','mode':'exclusive','cluster_id':'clu_fake','cluster_mode':'single','billing_wallet_id':'wal_demo'}
r=httpx.post('http://localhost:8000/api/v1/sessions',json=body,headers={'Authorization':'Bearer '+tok,'Idempotency-Key':'idle-test','Content-Type':'application/json'},timeout=20)
print(r.json().get('id',''))"; }

echo "[idle] shortening idle-timeout to 20s and restarting the api"
kubectl -n "$NS" set env deploy/gshare-api GSHARE_IDLE_TIMEOUT_SEC=20
kubectl -n "$NS" rollout status deploy/gshare-api --timeout=90s >/dev/null

echo "[idle] creating a session"; SID=$(tokpost | tail -1); echo "  session=$SID"
echo "[idle] waiting for the reaper to pause it (a 60s operator tick plus 20s idle, up to ~3 minutes)"
until [ "$(kubectl -n "$NS" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "select status from session where id='$SID'" 2>/dev/null|tr -d ' ')" = "paused" ]; do sleep 5; done
echo "[idle] paused automatically; the GPU is back:"
kubectl -n "$NS" exec deploy/gshare-pg -- psql -U gshare -d gshare -tAc "
select '  session='||status from session where id='$SID';
select '  txns: '||coalesce(string_agg(type,',' order by created_at),'none') from credit_transaction where wallet_id='wal_demo';"

echo "[idle] restoring idle-timeout to 1800"
kubectl -n "$NS" set env deploy/gshare-api GSHARE_IDLE_TIMEOUT_SEC=1800 >/dev/null
kubectl -n "$NS" rollout status deploy/gshare-api --timeout=90s >/dev/null
echo "[idle] done."
