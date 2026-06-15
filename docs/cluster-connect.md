# Connecting a cluster

> 📚 [Documentation home](./README.md)

Where [`cluster-setup.md`](./cluster-setup.md) **builds** a GPU cluster, this page
**attaches** it to the GShare **control plane**. The control plane is cluster-agnostic: it
applies `GShareSession` custom resources to external GPU clusters using their registered
kubeconfig, and each cluster's operator reconciles them and calls back with status and
inventory.

## Concepts

| Actor | Runs on | Responsibility |
|---|---|---|
| Control plane (`api`, `worker`) | Compose or Kubernetes | Registers clusters, applies custom resources through the kubeconfig, verifies operator callbacks. It both signs and verifies the internal JWT, and publishes the JWKS. |
| Operator | Each GPU cluster | Reconciles `GShareSession`, reports inventory and status. Holds the internal JWT. |

- **`cluster_id`** — a `clu_…` ULID minted by the control plane at registration. The
  operator's `--cluster-id` **must match it**, or inventory and session foreign keys will
  not line up. There is no negotiation: take the id returned by registration and put it in
  the operator deployment.
- **Internal JWT** — authenticates operator-to-control-plane callbacks (RS256,
  `aud=gshare-internal`). The control plane signs and verifies it, and publishes the public
  verification key at `GET /.well-known/gshare-internal-jwks.json`.
- **kubeconfig** — never stored in plaintext in the database. The control plane only ever
  reads a projected file: external-secrets on Kubernetes, a direct mount on Compose or bare
  metal. The path is `<cluster_id>/kubeconfig` under `GSHARE_CLUSTER_KUBECONFIG_DIR`.

## A. In-cluster all-in-one — registered automatically

`make deploy-incluster` sets `bootstrapLocalCluster: true`, which ensures a `Cluster` row
with the fixed id `clu_local` at startup, and the chart's `operator.clusterId: clu_local`
points at it. The operator reaches Kubernetes through its own ServiceAccount in the same
cluster, so there is no kubeconfig to register. **Nothing to do.**

## B. Registering an external cluster

The registration probe checks that the target cluster is reachable, has RuntimeClass
`nvidia`, and advertises HAMi's `nvidia.com/gpumem`. To build such a cluster, see
[`cluster-setup.md`](./cluster-setup.md).

Register from the console under **Admin → Clusters** by pasting a name and a kubeconfig, or
through the API:

```bash
curl -sX POST https://<console>/api/v1/clusters \
  -H "Authorization: Bearer $ADMIN_JWT" -H "Idempotency-Key: $(uuidgen)" \
  -d "{\"name\":\"gpu-a\",\"role\":\"primary\",\"kubeconfig_b64\":\"$(base64 -w0 <kubeconfig)\"}"
```

On a successful probe it returns the `clu_…` id. Only a secret reference is stored; the
kubeconfig is used once for validation, and all later I/O goes through the projected file
or the operator's ServiceAccount.

## C. Compose control plane with an external GPU cluster

Compose serves plain HTTP and expects a TLS-terminating reverse proxy in front (see the
comments in [`docker-compose.yml`](../docker-compose.yml)). The control plane's
`/internal/*` endpoints and its JWKS must be **reachable from the external cluster** —
`/internal` is gated on the internal JWT, and the JWKS is public and verification-only.

> **Session routing matters here.** Session URLs are
> `{GSHARE_SESSION_DOMAIN}/proxy/{cr}/{code|lab|terminal}`, but in this topology the console
> (the Compose frontend) and the session apps (the external cluster's ingress-nginx) are
> *different backends*. Your reverse proxy therefore has to route **only `/proxy/`** to the
> cluster ingress and everything else to the console. Without that split, `/proxy/…` lands
> on the console SPA and the user gets a client-side 404. With nginx:
>
> ```nginx
> location /proxy/ {                          # session apps → cluster ingress (NodePort 30080)
>     proxy_pass http://<cluster-node>:30080;
>     proxy_set_header Host $host;            # must equal GSHARE_SESSION_DOMAIN, matched by the ingress
>     proxy_http_version 1.1;                 # code-server and the terminal use WebSockets
>     proxy_set_header Upgrade $http_upgrade;
>     proxy_set_header Connection "upgrade";
>     proxy_read_timeout 3600s;
> }
> location / { proxy_pass http://<compose-host>:8000; }   # console
> ```
>
> A simpler alternative is a dedicated session domain — say `sessions.example.com` —
> pointed straight at the cluster ingress, with `GSHARE_SESSION_DOMAIN` and the operator's
> `SESSION_DOMAIN` set to it. Then the split is by host and needs no path rules.
>
> **The easiest option** is to let the console relay. If your proxy cannot split by path,
> set `GSHARE_SESSION_INGRESS=<cluster-node>:30080` in `.env`. The Compose frontend (nginx)
> then relays `/proxy/` to that cluster ingress, WebSockets included, and the external proxy
> only has to forward everything to the frontend. This handles a single external cluster,
> and is left unset for an all-in-one Kubernetes install where the cluster ingress routes
> `/proxy/` itself.

1. **Enable internal callbacks** — generate the RS256 key, then start:

   ```bash
   ./hack/gen-secrets.sh   # writes GSHARE_INTERNAL_JWT_PRIVATE_KEY and KID into .env
   make compose-up         # without the key the stack is explore-only: no operator can attach
   ```

2. **Register the cluster** (section B) and note the `clu_id`.

3. **Provide the kubeconfig** so the control plane can apply custom resources:

   ```bash
   mkdir -p deploy/clusters/<clu_id> && cp <kubeconfig> deploy/clusters/<clu_id>/kubeconfig
   ```

   Compose mounts that directory read-only at `/run/gshare/clusters` — see
   [`deploy/clusters/README.md`](../deploy/clusters/README.md). The files are git-ignored.

4. **Deploy the data plane** on the GPU cluster. This assumes the cluster is already set up
   per [`cluster-setup.md`](./cluster-setup.md) (`cluster-bootstrap.sh up`, so GPU, HAMi,
   and CRIU nodes are ready).

   **Recommended — the full-featured Helm data plane.** The same chart as the all-in-one
   install, with the control plane switched off (`controlPlane.enabled=false`). You get the
   operator, CRDs, namespaces with Pod Security, RBAC, HAMi-monitor idle reclamation, the
   lossless agent, and the lend-guard webhook — exactly as in the all-in-one install. The
   command also mints the token with the Compose API and injects the secret:

   ```bash
   KUBECONFIG=<target cluster kubeconfig> \
     make deploy-dataplane CLUSTER_ID=<clu_id> CONTROL_PLANE_URL=http://<compose-host>:8080
   ```

   `CONTROL_PLANE_URL` must be reachable **from both the operator pod and ingress-nginx** —
   the operator uses it for status callbacks, and the ingress uses it for session
   forward-auth. Set the session domain with `-f deploy/values/domain.yaml` or
   `--set global.domains.console=`. The token TTL defaults to 7 days (`JWT_TTL`); refresh
   just the secret before it expires with
   `make dataplane-token CLUSTER_ID=<clu_id>`.

   **Alternative — the minimal single script.** For a quick core-session-plus-cold-pause
   setup without idle reclamation, lossless pause, or the webhook.
   [`hack/deploy-operator.sh`](../hack/deploy-operator.sh) applies the CRD, namespace,
   ServiceAccount, RBAC, secret, and deployment in one `kubectl` pass:

   ```bash
   TOKEN=$(make -s compose-operator-token CLUSTER_ID=<clu_id>)   # internal JWT, 24h TTL
   CLUSTER_ID=<clu_id> SOT_ENDPOINT=https://<public control-plane URL> OPERATOR_TOKEN="$TOKEN" \
     KUBECONFIG=<target cluster kubeconfig> SESSION_DOMAIN=<same as the control plane's GSHARE_SESSION_DOMAIN> \
     ./hack/deploy-operator.sh
   ```

   `connect-verify-url` and `internal-jwks-url` are derived from `SOT_ENDPOINT`; override
   the operator image with `IMAGE`.

   > ⚠️ **`SESSION_DOMAIN` must equal the control plane's `GSHARE_SESSION_DOMAIN`.** Session
   > ingress hosts are generated from it, and the control plane builds connect URLs from the
   > same value. A mismatch means the ingress host never matches and every connect attempt
   > 404s. When `SESSION_DOMAIN` is unset the script falls back to `GSHARE_SESSION_DOMAIN`
   > from the control plane's `.env` — leaving it empty is the safest choice. Existing
   > sessions keep the ingress they were created with, so after changing the domain you have
   > to recreate them.

5. **Verify** — callbacks return 200 in the operator log, the console shows the cluster as
   `connected`, node and GPU inventory appears, and creating a session applies a custom
   resource on that cluster.

### Token rotation

The internal JWT has to be renewed before it expires. A Compose control plane does not
rotate it for you.

- Helm data plane: `make dataplane-token CLUSTER_ID=<clu_id>` — re-injects the secret,
  7-day TTL.
- Minimal script: re-run `./hack/deploy-operator.sh` with a fresh
  `compose-operator-token` (24-hour TTL); it updates the secret and restarts the rollout.

```bash
# Example: rotate daily at 02:00 from the Compose host's crontab (Helm data plane).
0 2 * * * cd /path/to/gShare && KUBECONFIG=<target cluster kubeconfig> make -s dataplane-token CLUSTER_ID=<clu_id>
```

## D. Kubernetes control plane with additional external clusters

When the control plane itself runs on Kubernetes, external-secrets projects each
kubeconfig under `GSHARE_CLUSTER_KUBECONFIG_DIR`. Deploy each additional cluster's operator
with the chart, setting `operator.clusterId` to the registered id, plus
`operator.internalJwksUrl` and `operator.internalJwtSecret`.

## Security and limitations

- **kubeconfigs are never stored in plaintext in the database** — they are read from a
  mounted file (`deploy/clusters/` on Compose, git-ignored).
- `/internal/*` and all callbacks require the RS256 internal JWT with
  `aud=gshare-internal`. The JWKS is public, verification-only, and never exposes the
  private key.
- With Compose plus an external cluster, the control plane's `/internal` endpoints and JWKS
  must be reachable from that cluster — a public URL behind a TLS proxy. Corporate
  networks, firewalls, and split-horizon DNS need their own handling (VPN, internal DNS
  views).
- Automatic internal-JWT rotation exists only on the Kubernetes path, via the chart's Job
  and CronJob. On Compose, re-issue it manually before expiry.
