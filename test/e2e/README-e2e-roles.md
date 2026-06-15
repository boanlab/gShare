# Role-based end-to-end checks

Verifies **API authorization** and **UI visibility and route guards** from the perspective
of all four roles — `super_admin`, `org_admin`, `group_admin`, and `member` — against the
local Docker Compose stack. No GPU required; the real-GPU path uses the Kubernetes assets
in [`README.md`](./README.md).

## 0. Start the stack and seed the roles

```bash
sudo docker compose up -d --build    # api on :8080, console on :8000

# Seed four roles plus an organization, a group, and wallets. Every password is 'Passw0rd!'.
sudo docker compose exec -T postgres psql -U gshare -d gshare < test/e2e/backend/seed-roles.sql
```

Seeded users:

| Role | Email | `global_role` | Membership in `grp_e2e` |
|---|---|---|---|
| super_admin | super@e2e.test | `super_admin` | — |
| org_admin | org@e2e.test | — | `org_admin` |
| group_admin | grp@e2e.test | — | `group_admin` |
| member | member@e2e.test | — | `member` |

## 1. API permission matrix

Runs inside the API container (using python-jose and httpx), checking allow and deny for
each role's token.

```bash
sudo docker compose cp test/e2e/backend/e2e-rbac-matrix.py gshare-api:/tmp/m.py
sudo docker compose exec -T gshare-api python /tmp/m.py
# => "== summary: ALL PASS =="
```

It covers a login round trip plus 19 actions × 4 roles. `ALLOW` expects 2xx (or a 4xx that
is a legitimate business-rule rejection); `DENY` expects exactly 403.

## 2. UI checks with Playwright

Logs into the console on `:8000` as each role and asserts navigation visibility, the admin
mode toggle, and route guards (403), capturing screenshots along the way.

```bash
sudo docker run --rm --network host --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v "$PWD":/work -w /work/test/e2e/frontend \
  -e PLAYWRIGHT_BROWSERS_PATH=/ms-playwright -e PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1 \
  -e ORIGIN=http://localhost:8000 \
  mcr.microsoft.com/playwright:v1.40.0-jammy \
  sh -c "npm i playwright@1.40.0 --no-audit --no-fund --silent && node e2e-roles.js"
# => "== UI summary: ALL PASS ==", screenshots in test/e2e/frontend/out/role-*.png
```

Note: the dashboard and monitoring screens hold an SSE stream open, so `networkidle` is
never reached — navigation waits use `domcontentloaded` instead.

### Further UI checks on the same harness

Run these in the same Playwright container (`ORIGIN=http://localhost:8000`, with
`seed-roles.sql` already applied) in place of `node e2e-roles.js`. Screenshots land in
`test/e2e/frontend/out/`.

| Script | What it verifies |
|---|---|
| `e2e-billing-report.js` | `super_admin` → admin billing → the settlement report tab renders |
| `e2e-topup-reject.js` | `super_admin` → billing → top-up requests → rejecting one (a reason is mandatory) removes it from the list. Requires a pending `topup_request` for 777 to be seeded first |

## Cleaning up

```bash
sudo docker compose down -v
```
