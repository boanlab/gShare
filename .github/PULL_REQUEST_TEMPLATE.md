<!--
Keep the title in Conventional Commits form, e.g.
  feat(scheduler): admit CPU-only sessions without a credit hold
  fix(operator): re-acquire the GPU when resuming a paused session
-->

## What this changes

<!-- The behaviour before and after. Link the issue with "Fixes #123" when there is one. -->

## Why

<!-- The problem this solves. Skip if the "what" already makes it obvious. -->

## How it was verified

<!-- Delete the lines that do not apply. -->

- [ ] `make lint`
- [ ] `make test`
- [ ] `make helm-lint`
- [ ] Real GPU cluster e2e (`test/e2e/`) — say which scripts
- [ ] Manual check in the console — say which screens

## Notes for reviewers

- [ ] Database schema changed — an Alembic migration is included
- [ ] The `GShareSession` CRD changed — `make -C operator manifests` was re-run and the chart CRD updated
- [ ] The backend API surface changed — `make gen-openapi` was re-run
- [ ] User-visible strings changed — both `en` and `ko` bundles are updated
- [ ] Documentation under `docs/` reflects the new behaviour
