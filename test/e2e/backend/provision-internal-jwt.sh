#!/usr/bin/env bash
set -euo pipefail
# Provisions the internal plane's RS256 JWT. The api both signs and verifies it, publishing the JWKS;
# the operator merely holds the issued token. This is what authenticates the operator's callbacks,
# such as POST /internal/sessions/{id}/status.
# Requires the backend stack (deploy.sh, including the GSHARE_INTERNAL_JWT_* variables) and a running
# operator.
NS=gshare-system
PEM=/tmp/internal-private.pem
TOKEN=/tmp/operator-internal.jwt

echo "[jwt] 1/4 generating the RSA private key into the gshare-jwt-signing secret"
kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization
k=rsa.generate_private_key(public_exponent=65537,key_size=2048)
print(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode())
" > "${PEM}"
kubectl -n "${NS}" create secret generic gshare-jwt-signing \
  --from-file=private.pem="${PEM}" --from-literal=active_kid=internal \
  --dry-run=client -o yaml | kubectl apply -f -

echo "[jwt] 2/4 restarting the api to load the key, and checking the JWKS is published"
kubectl -n "${NS}" rollout restart deploy/gshare-api
kubectl -n "${NS}" rollout status deploy/gshare-api --timeout=90s
kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
import urllib.request,json; print('jwks kids:', [k['kid'] for k in json.load(urllib.request.urlopen('http://localhost:8000/.well-known/gshare-internal-jwks.json',timeout=5))['keys']])"

echo "[jwt] 3/4 signing the operator token with the api, into the gshare-operator-token secret"
kubectl -n "${NS}" exec deploy/gshare-api -c api -- python -c "
from app.auth.internal_jwt import sign_internal_jwt
print(sign_internal_jwt('operator:clu_fake', ttl=86400), end='')" > "${TOKEN}"
kubectl -n "${NS}" create secret generic gshare-operator-token \
  --from-file=internal-jwt="${TOKEN}" --dry-run=client -o yaml | kubectl apply -f -

echo "[jwt] 4/4 restarting the operator so it picks up the token and SOT_ENDPOINT; operator.yaml carries the volume and env"
kubectl -n "${NS}" rollout restart deploy/gshare-operator
kubectl -n "${NS}" rollout status deploy/gshare-operator --timeout=90s
echo "[jwt] done. The token lasts 24h; re-run steps 3 and 4 when it expires."
