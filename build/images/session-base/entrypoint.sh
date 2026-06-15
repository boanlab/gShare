#!/usr/bin/env bash
# Entrypoint for a GShare session container. Access is path-based under one domain, with symmetric
# subpaths:
#   External URL: {console domain}/proxy/{cr}/{lab|terminal|code}. The operator creates an Ingress
#   with the /proxy/{cr} prefix and injects GSHARE_URL_PREFIX=/proxy/{cr} into the environment. With
#   it unset the prefix is empty and everything serves from the root.
#   8888  jupyterlab     — base_url=${PREFIX}/lab
#   7681  ttyd web terminal, at base path ${PREFIX}/terminal
#   8080  code-server, served from the root. The ingress strips ${PREFIX}/code by rewrite; its
#         assets are relative, so no base path is needed.
# Authentication happens at the ingress, through the connect-token auth-url
# (/internal/connect/verify). The container's services sit behind it.
set -u
PREFIX="${GSHARE_URL_PREFIX:-}"
# Working directory: use /workspace, the volume mount point, when it is writable; otherwise fall back
# to the user's home. Running as uid 1000 with no volume mounted, the base image's root-owned
# /workspace is not writable.
WORKDIR="/workspace"
[ -w "$WORKDIR" ] || WORKDIR="${HOME:-/home/coder}"
cd "$WORKDIR" 2>/dev/null || true
echo "[gshare-session] uid=$(id -u) workdir='${WORKDIR}' prefix='${PREFIX}' — vscode:8080(${PREFIX}/code) jupyter:8888(${PREFIX}/lab) terminal:7681(${PREFIX}/terminal)"

# 1. JupyterLab on ${PREFIX}/lab, with no token or password. Authentication is the ingress connect
#    gate, the same contract as code-server's --auth none and ttyd.
jupyter lab --no-browser --ip=0.0.0.0 --port=8888 \
  --ServerApp.base_url="${PREFIX}/lab" --ServerApp.root_dir="$WORKDIR" --ServerApp.token="" \
  --ServerApp.password="" --ServerApp.allow_origin='*' \
  --ServerApp.disable_check_xsrf=True --ServerApp.trust_xheaders=True \
  >/tmp/jupyter.log 2>&1 &

# 2. The web terminal on ${PREFIX}/terminal.
ttyd --port 7681 --base-path "${PREFIX}/terminal" --writable bash >/tmp/ttyd.log 2>&1 &

# 3. code-server in the foreground, served from the root; the ingress strips the prefix.
#    Authentication is again the ingress connect gate.
exec code-server --bind-addr 0.0.0.0:8080 --disable-telemetry --auth none "$WORKDIR"
