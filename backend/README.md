# GShare backend — the Python control plane

The FastAPI control plane, and the authority on **money and session state**: REST,
authentication and RBAC, the credit engine (hold, consume, settle, refund), budget and
FinOps gates, CRUD for the catalogue, presets, policies, users, and organizations, the
audit trail, connect-token issuing, and **session admission**.

It pairs with the Go cluster operator in [`../operator`](../operator), which owns
everything that actually touches Kubernetes: reconciling pods, services, and ingresses,
node health and cordoning, the idle reaper, and node inventory.

## Boundaries

- The control plane **never calls the workload Kubernetes API directly**. It expresses
  desired state by applying a `GShareSession` custom resource to the target cluster —
  CRD-primary, implemented in `app/cluster/`.
- Status comes back through a signed internal callback,
  `POST /internal/sessions/{id}/status`.
- Authentication: end users log in locally with **email and password** and carry a bearer
  JWT. There are no API keys and no batch sessions. Plane-to-plane calls use a short-lived
  **RS256 internal JWT** (`aud=gshare-internal`), verified against the JWKS this service
  publishes at `GET /.well-known/gshare-internal-jwks.json`.

## Layout

```
app/
  main.py            create_app(): mounts api_router and internal_router, /healthz, bootstrap admin
  core/              config (pydantic-settings), errors (envelope), passwords, redis, ids, logging
  auth/              RBAC and principal, internal_jwt (RS256), bootstrap (seeds the first super_admin)
  api/               public REST routers, with schemas/
  internal/          internal-only routers: status, connect-verify, audit, jwks
  domain/            scheduler, credit_engine, session/volume/budget services, connection_token
  cluster/           handoff, crd (GShareSession apply), status_sync — apply only, no workload calls
  db/                base (engine and session), models
  workers/           runner plus billing, budget_rollup, credit_refill, token_expiry, queue_ticker
alembic/             env.py and versions/
tests/               pytest against in-memory SQLite; real-GPU e2e lives in /test/e2e
```

## Running locally

The quickest path is the whole stack from the repository root: `make compose-up`. To run
just the API by hand:

```bash
pip install -e ".[dev]"
cp ../.env.example .env                    # set GSHARE_BOOTSTRAP_ADMIN_* and INTERNAL_JWT_PRIVATE_KEY
docker compose up -d postgres redis        # datastores only
alembic upgrade head                       # apply the schema
uvicorn app.main:app --reload --port 8080  # docs at http://localhost:8080/api/v1/docs
python -m app.workers.runner               # billing, budget rollup, credit refill, token expiry, queue ticker
```

Port 8080 matches what Compose and the frontend proxy expect.

## Testing

```bash
make test                  # pytest -ra --cov, from backend/
make test-backend-docker   # from the repository root, if you have no local Python toolchain
```

Domain tests run against an in-memory SQLite session, so no external Postgres or Redis is
needed. Tests that require a real GPU cluster carry the `realgpu` marker and are excluded
by default.
