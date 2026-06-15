#!/bin/sh
# Optional, Compose only: relay /proxy/ session traffic to an external GPU cluster's ingress.
#
# Topologies:
#   - Compose control plane with an external cluster: the console (this frontend) and the session
#     apps (the cluster's ingress) are different backends, so /proxy/ on the single domain has to be
#     handed to the cluster. Setting GSHARE_SESSION_INGRESS=host:port — the cluster ingress NodePort,
#     for example 10.0.0.10:30080 — generates this location block.
#   - All-in-one Kubernetes: GSHARE_SESSION_INGRESS is unset, nothing is generated, and the cluster
#     ingress routes /proxy/ itself.
#
# A single relay cannot serve several external clusters; for that, split by path in the front-facing
# reverse proxy, or give sessions their own domain.
set -e
DIR=/etc/nginx/session-proxy
mkdir -p "$DIR"
[ -n "${GSHARE_SESSION_INGRESS:-}" ] || exit 0

# The Host header the cluster ingress matches on is the session domain; unset, the original Host is
# preserved.
HOST_HDR="${GSHARE_SESSION_DOMAIN:-\$host}"

cat > "$DIR/proxy.conf" <<'EOF'
location /proxy/ {
    proxy_pass http://__UPSTREAM__;
    proxy_set_header Host __HOSTHDR__;
    proxy_set_header X-Forwarded-Proto $http_x_forwarded_proto;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;          # vscode/terminal = WebSocket
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
    proxy_buffering off;
}
EOF
sed -i "s|__UPSTREAM__|${GSHARE_SESSION_INGRESS}|; s|__HOSTHDR__|${HOST_HDR}|" "$DIR/proxy.conf"
echo "[session-proxy] /proxy/ -> http://${GSHARE_SESSION_INGRESS} (Host ${HOST_HDR})"
