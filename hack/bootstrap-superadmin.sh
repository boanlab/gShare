#!/usr/bin/env bash
set -euo pipefail
# Bootstraps the first super_admin.
#
# GSHARE_BOOTSTRAP_ADMIN_EMAIL and GSHARE_BOOTSTRAP_ADMIN_PASSWORD make seed_bootstrap_admin ensure
# the account at startup; this script then logs in with that email and password
# (AUTH_ALLOW_LOCAL_PASSWORD=true) to obtain a token.
#
# Prints the super_admin JWT to stdout; capture it as ADMIN_JWT for administrative API calls.
#   ADMIN_JWT=$(./hack/bootstrap-superadmin.sh)

SYS_NS="${SYS_NS:-gshare-system}"
API_BASE="${API_BASE:-http://api.${SYS_NS}.svc/api/v1}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-devpassword}"

echo "[bootstrap] injecting the administrator env (GSHARE_BOOTSTRAP_ADMIN_EMAIL=${ADMIN_EMAIL})" >&2
# Inject the bootstrap administrator into the api Deployment; seed_bootstrap_admin ensures the
# super_admin account on the next start.
kubectl -n "${SYS_NS}" set env deploy/gshare-api \
  "GSHARE_BOOTSTRAP_ADMIN_EMAIL=${ADMIN_EMAIL}" \
  "GSHARE_BOOTSTRAP_ADMIN_PASSWORD=${ADMIN_PASSWORD}" \
  "AUTH_ALLOW_LOCAL_PASSWORD=true" >/dev/null
kubectl -n "${SYS_NS}" rollout status deploy/gshare-api --timeout=120s >&2

echo "[bootstrap] issuing a super_admin token through local login" >&2
RESP="$(curl -sS -X POST "${API_BASE}/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"${ADMIN_EMAIL}\",\"password\":\"${ADMIN_PASSWORD}\"}")"

# Extract access_token, preferring jq and falling back to grep.
if command -v jq >/dev/null 2>&1; then
  TOKEN="$(printf '%s' "${RESP}" | jq -r '.access_token // empty')"
else
  TOKEN="$(printf '%s' "${RESP}" | grep -o '"access_token":"[^"]*"' | cut -d'"' -f4)"
fi

if [[ -z "${TOKEN}" ]]; then
  echo "[bootstrap] FAILED to obtain a token. Response:" >&2
  printf '%s\n' "${RESP}" >&2
  exit 1
fi

echo "[bootstrap] super_admin token issued" >&2
printf '%s\n' "${TOKEN}"     # on stdout, to capture as ADMIN_JWT
