# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the
project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Container
images and the Helm chart are versioned together with the repository tag.

## [Unreleased]

### Added

- GitHub Actions: CI (lint, test, image build), Docker Hub publishing, release automation, Helm
  chart repository publishing, CodeQL, and Trivy scanning.
- Contributor-facing project files: `CODE_OF_CONDUCT.md`, `SECURITY.md`, `MAINTAINERS.md`, issue
  and pull request templates, `CODEOWNERS`, Dependabot.
- Console internationalisation: `en` (default) and `ko`, selectable from the top bar and
  remembered per browser.
- Console UX standards (`docs/console-ux.md`) and the shared components behind them: `PageHeader`,
  `Table` with sorting and selection, `TableToolbar`, `Pagination`, `Field`, `EmptyState`,
  `TableSkeleton`, `CopyButton`, `Timestamp`, `ConfirmDialog`, `useTableState`, `useUnsavedGuard`,
  `useDocumentTitle`.
- Persona UX audit (`test/e2e/ux`): eight personas, four viewports, two languages, over every
  screen, with no GPU and no cluster. DOM rules (landmarks, labels, contrast, tap targets,
  timestamps, identifiers, table affordances) and interaction probes (empty submit, invalid input,
  destructive actions, dialog focus, filtering to nothing, mobile navigation).
- Bulk session termination from the session list and from session monitoring, backed by
  `POST /sessions/bulk-terminate`.
- Undo for reversible actions: toasts carry an action (`pushToast(kind, message, { label, run })`),
  used for restoring volume access and administrator roles.
- `test/e2e/ux/screenshots.js`, which regenerates `docs/screenshots` from a seeded stack.

### Changed

- Console: per-screen browser titles; `main`/`nav`/`header` landmarks and a skip link; keyboard
  focus rings; list search, sorting and paging in the URL; distinct "nothing yet" and "nothing
  matched" states; loading skeletons; relative timestamps with the exact value on hover; copy
  buttons on identifiers; breadcrumbs and a back control on nested screens; unsaved-changes guards;
  validation on blur; a stated reason on every disabled primary button; and below 768px a
  navigation drawer, 44px touch targets and a 12px type floor.
- Destructive actions use a dialog in place of `window.confirm`: it names the record, lists what
  will be lost, traps and restores focus, and requires the name to be typed for the irreversible —
  a user, group, organization, cluster, offering, preset or snapshot.
- Light theme foregrounds meet WCAG AA against their own backgrounds.
- Numeric fields declare `min`, `max`, `step` and `inputmode`, and clamp to their range on blur.
- A rejected save is reported on the form and stays until the next attempt, not only in a toast.
- Repository layout moved to the conventional open-source shape: `charts/` for the Helm chart,
  `deploy/` for manifests and values overlays, `build/` for image build contexts, `test/e2e/` for
  end-to-end assets, `hack/` for developer and operator scripts.
- Documentation, source comments and the console's default language are English.

### Fixed

- Role gating decided before `/auth/me` answered, redirecting an `org_admin` or `group_admin` who
  opened a bookmarked `/admin/…` link to 403 with the URL replaced. `RequireRole` waits for the
  membership context; `super_admin`, whose role is in the token, was unaffected.
- Administrator forms refused to submit, with no message, when a value did not sit on a field's
  `step`. Capacities accept any value, counts step by one, and the forms carry `noValidate` so the
  console's inline messages are the only validation shown.
- `org.read` was requested unconditionally by the create-user, edit-user and create-group forms,
  giving every `group_admin` a 403 and an unexplained empty organization list.
- 23 audit-log action codes had no label and were shown as raw strings.
- The session wizard swallowed a failed cost estimate, leaving a stale figure on screen, and
  allowed `Next` with no offering selected.
- Operator: `make manifests` scanned only `internal/controller`, dropping the RBAC markers on the
  inventory and health controllers; `make test` resolved `KUBEBUILDER_ASSETS` at parse time and so
  failed on a clean checkout; `setup-envtest` was pinned to a release whose asset index returns 401.

## [0.1.0]

Initial public shape of the platform:

- `gshare-api` — FastAPI control plane: authentication, tenancy (organization → group →
  user), credit accounting, resource policy, session admission, and the internal plane
  (RS256 JWT and JWKS) that operators call back into.
- `gshare-worker` — batch plane: billing consume and settle, queue dequeue, idle
  reaping, credit refill, and token expiry.
- `gshare-operator` — per-cluster Go controller reconciling `GShareSession` custom
  resources into session pods, services, and ingress rules, with GPU inventory sync and
  pause/resume that returns and re-acquires the GPU.
- Console — React single-page application for users and administrators, with live
  session updates over SSE.
- Helm chart covering an all-in-one install, a production install against externally
  managed datastores, and a data-plane-only install attached to an external control plane.

[Unreleased]: https://github.com/boanlab/gShare/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/boanlab/gShare/releases/tag/v0.1.0
