#!/usr/bin/env bash
set -euo pipefail
# The whole path for a CPU session: API to custom resource to operator to pod.
# Requires the backend stack (deploy.sh), the operator (../operator/deploy.sh), seed.sql, and
# AUTH_ALLOW_LOCAL_PASSWORD=true.
# Authenticates as super_admin with a development HS256 token signed with USER_JWT_SECRET, posts a
# CPU session, and waits for the operator to create the pod.
NS=gshare-system
IDEM="${IDEM:-e2e-$(date +%s 2>/dev/null || echo manual)}"

echo "[e2e] POST /sessions (CPU) via dev token"
kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
import time, httpx
from jose import jwt
tok = jwt.encode({'sub':'usr_admin','email':'admin@example.com','global_role':'super_admin','exp':int(time.time())+86400}, 'gshare-dev-secret', algorithm='HS256')
body = {'offering_id':'off_cpu_free','image_id':'nginxinc/nginx-unprivileged:alpine','resource_class':'cpu','cluster_id':'clu_fake','cluster_mode':'single'}
h = {'Authorization':'Bearer '+tok,'Idempotency-Key':'${IDEM}','Content-Type':'application/json'}
r = httpx.post('http://localhost:8000/api/v1/sessions', json=body, headers=h, timeout=20)
print('  STATUS', r.status_code, r.text[:300])
"
echo "[e2e] waiting for the operator to reconcile"
sleep 8
echo "[e2e] GShareSession CR + Pod:"
kubectl -n gshare-sessions get gsharesession,pod -o wide 2>&1 | grep -vE 'No resources'
