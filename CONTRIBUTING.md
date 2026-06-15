# Contributing to GShare

Thanks for taking the time. This document covers how to get a working development
environment, what the review gates are, and the rules that a pull request has to hold to.

By participating you agree to the [Code of Conduct](./CODE_OF_CONDUCT.md). Security
vulnerabilities follow [SECURITY.md](./SECURITY.md) instead of the public issue tracker.

## Before you start

- **Bugs and features** — open an issue first for anything beyond a small fix. It saves
  you from building something that conflicts with work already in flight.
- **Scope** — GShare is interactive-session only. There is deliberately no API key, CLI,
  SDK, or batch-job surface, and MIG partitioning is out of scope. Proposals that move
  those boundaries are welcome, but argue the case in an issue first.

## Development environment

| Component | Runtime | Directory |
|---|---|---|
| `gshare-api`, `gshare-worker` | Python 3.12 + FastAPI | `backend/` |
| `gshare-operator` | Go 1.22 + controller-runtime | `operator/` |
| Console | Node 20 + Vite + React | `frontend/` |

You need `docker`. For deployment work you also need `kubectl` and `helm` v3.

```bash
make compose-up     # full stack locally: Postgres, Redis, api, worker, console
make test-docker    # backend and frontend tests inside containers
make ci             # lint + test + chart render, the same gates CI runs
make compose-down
```

If you prefer local toolchains over containers:

```bash
pip install -e "backend[dev]"      # Python
make -C operator test              # Go; downloads controller-gen and envtest into operator/bin
npm --prefix frontend ci           # Node
```

Deploying to a real GPU cluster is documented in
[`docs/getting-started.md`](./docs/getting-started.md); the end-to-end assets and their
prerequisites are in [`test/e2e/README.md`](./test/e2e/README.md).

## Branches and commits

- Do not push to `main`. Work on a branch — `feat/…`, `fix/…`, `docs/…` — and open a pull
  request.
- Commit messages and pull request titles follow
  [Conventional Commits](https://www.conventionalcommits.org/): `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `chore:`, with an optional scope such as `feat(scheduler):`.
- Keep a pull request to one concern. A refactor and a behaviour change in the same diff
  are two pull requests.

## Code style

- **Python** — `ruff`, line length 100. `make -C backend lint`, `make -C backend fmt`.
- **Go** — `gofmt` and `go vet`. `make -C operator lint`.
- **TypeScript** — `eslint`. `make -C frontend lint`.
- **Comments** — English, present tense, describing the current state. Explain *why* a
  non-obvious decision was made; do not narrate change history or leave `TODO(name)`
  markers without an issue link.
- **User-facing errors** — the backend raises a typed error; the console maps
  `error.code` to a message. Do not hard-code prose in the API layer.

## Internationalisation

The console ships English (`en`, the default) and Korean (`ko`). Every user-visible string
goes through `react-i18next`:

```tsx
const { t } = useTranslation();
<h1>{t('sessions.title')}</h1>
```

Add the key to **both** `frontend/src/i18n/locales/en.json` and `ko.json`. A key missing
from `ko.json` falls back to English rather than breaking, but an incomplete pull request
is still an incomplete pull request. Never interpolate a translated fragment into another
translated string — use placeholders (`{{count}}`) so word order stays translatable.

## Generated artifacts

Some files are generated and must be regenerated, not hand-edited:

| When you change | Run | Commit |
|---|---|---|
| Backend routes or Pydantic schemas | `make gen-openapi` | `frontend/openapi.json`, `frontend/src/api/schema.d.ts` |
| Operator API types or controller RBAC markers | `make -C operator manifests generate` | `operator/api/**/zz_generated.deepcopy.go`, `operator/config/**` |
| The `GShareSession` CRD | as above, then copy into `charts/gshare/crds/` | both copies |
| Database models | `alembic revision --autogenerate` in `backend/` | the new migration in `backend/alembic/versions/` |

CI fails the build if the operator's generated files are stale.

## Testing

- Unit and functional tests run with no external dependencies: in-memory SQLite for the
  domain layer, `fakeredis` for idempotency and queue paths.
- Anything that needs real compute, real VRAM isolation, or numerical accuracy is marked
  `realgpu` and only runs against a real GPU cluster from `test/e2e/`.
- New behaviour needs a test. Bug fixes need a test that fails before the fix.

```bash
make test                                    # everything that runs without a GPU
make -C backend test ARGS='-k scheduler'     # a subset
make -C backend cov                          # coverage report in backend/htmlcov/
```

## Security gates

A pull request must not break any of these:

- **No secrets in the repository.** No plaintext Secret manifests, kubeconfigs, or private
  keys — check `.gitignore` before adding files. Production secrets come from
  external-secrets only.
- **Signed images.** Session and platform images must be cosign-signed; Kyverno verifies
  them in enforce mode (`deploy/supplychain/`).
- **Least-privilege RBAC.** No wildcard verbs. New permissions are namespace-scoped and
  spelled out.
- **`restricted` Pod Security.** New workloads must pass it: `runAsNonRoot`, drop all
  capabilities, read-only root filesystem.
- **Tenant isolation.** Any new query that touches tenant data must be scoped by the
  caller's organization or group. `backend/tests/test_tenant_isolation.py` is the
  regression net — extend it.

## Review

Two things get a pull request merged: green CI, and a maintainer approval. Expect review
comments on tenant scoping, credit accounting correctness, and anything that changes the
`GShareSession` contract between the API and the operator — those are the places where
mistakes are expensive.
