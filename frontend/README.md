# GShare console

The React, TypeScript, and Vite single-page application that users and administrators drive
GShare from. It is entirely data-driven off the backend's REST and SSE endpoints.

## Stack

React 18 · TypeScript · Vite · TanStack Query v5 · Tailwind CSS · React Router v6 · Zustand ·
openapi-fetch with openapi-typescript · react-i18next.

## What it does

- **Authentication is email and password.** An administrator registers a user with an
  initial password, and the user must change it at first login. There is no API key, CLI, or
  SDK.
- **Sessions are interactive only.** There is no batch concept.
- **A two-step session wizard** — workload, then volumes and review, with an advanced
  toggle — that leads with presets. GPUs are offered as per-model full-card fraction tiers:
  `exclusive` (bypassing HAMi) or `fractional` (shared).
- **Live updates over SSE** (`/sessions/{id}/events`), with polling as the fallback. The bearer
  token has no refresh path: a 401 logs the user out. Write requests carry an `Idempotency-Key`.
  Errors map from `error.code` to an inline message on the form, and a toast.
- **One SPA, two consoles.** The user console lives at `/` and the administrator console at
  `/admin`, on separate layouts with role-based navigation and guards, and an admin-mode toggle in
  the top bar. Below 768px the sidebar becomes a drawer.
- **List state lives in the URL.** Search, sort, page and tab are query parameters, so a view can
  be shared and survives Back.

## Getting started

```bash
npm install
npm run dev        # http://localhost:5173/ for users, /admin for administrators
```

`VITE_API_BASE` defaults to `/api/v1` in code. To change it, add
`frontend/.env.production` — Vite reads `.env*` from this directory only, not the repository
root. It is a build-time value and cannot be changed at runtime. Backend and runtime
configuration (the `GSHARE_*` variables) lives in the repository root `.env.example`.

## Scripts

| Command | What it does |
|---|---|
| `npm run dev` | Vite dev server with HMR. Base `/`, and `/api` proxied to the backend on :8080 |
| `npm run build` | Type-check with tsc, then build into `dist/` |
| `npm run gen:api` | Regenerate `src/api/schema.d.ts` from `openapi.json` (normally invoked by `make gen-openapi`) |
| `npm run test` | Vitest unit tests |
| `npm run lint` | ESLint |

## API types

The backend's OpenAPI document is the source of truth for types. Running `make gen-openapi`
from the repository root dumps the backend's `app.openapi()` into `frontend/openapi.json`
and regenerates `src/api/schema.d.ts` with openapi-typescript. Everything happens inside
containers, so no `.venv` or `node_modules` is left on the host.

- `src/api/schema.d.ts` is generated — never edit it. Regenerate whenever a backend route or
  schema changes.
- `src/api/types.ts` layers friendly domain aliases (`Session`, `Volume`, and so on) over
  `schema.d.ts`'s `components.schemas`.

## Internationalisation

The console ships English (`en`, the default and the fallback) and Korean (`ko`). The
language is chosen from the top bar and remembered per browser; `<html lang>` follows it.

```tsx
const { t } = useTranslation();
<h1>{t('sessions.title')}</h1>
```

- Bundles live in `src/i18n/locales/{en,ko}.json` and are imported statically, so switching
  language never waits on a network request.
- Add every new key to **both** bundles. A key missing from `ko.json` falls back to English.
- Never assemble a sentence from translated fragments — use placeholders (`{{count}}`), and
  `<Trans>` when part of the sentence needs markup, so word order stays translatable.
- Code outside a React component (formatters, label helpers) uses the `i18n` singleton
  directly rather than the hook.
- Numbers and dates are formatted through `Intl` with the active locale; see
  `src/lib/format.ts`.

## Layout

- `src/api/` — the openapi-fetch client (`client.ts`), domain hooks (`hooks/`), generated
  types (`schema.d.ts`), and aliases (`types.ts`).
- `src/auth/` — the Zustand auth store, the `RequireAuth` and `RequireRole` guards, and the
  login and password-change calls (`authApi.ts`).
- `src/i18n/` — i18n bootstrap and the locale bundles.
- `src/routes/router.tsx` — route map and guards: users at `/`, administrators at `/admin/*`.
- `src/features/` — one directory per domain: the session wizard, list, detail, and connect
  screens, plus wallet, volume, queue, account, auth, and `admin/*`.
- `src/components/` — shared components: `Layout` (sidebar, top bar, role navigation, admin-mode
  and language toggles), `PageHeader`, `Table`, `Field`, `ConfirmDialog`, `EmptyState`,
  `CopyButton`, `Timestamp`, `Toast`.
- `src/hooks/` — `useTableState` (search, sort, page and tab in the URL), `useUnsavedGuard`,
  `useDocumentTitle`.
- `src/lib/` — sse, format, jwt, rbac, errors.
- `src/store/uiStore.ts` — theme and toasts.

What a screen is expected to do, and which component provides it, is in
[Console UX standards](../docs/console-ux.md).

## Deployment

`Dockerfile` builds the bundle and serves it from nginx using `nginx.conf`. The SPA is
served from the root, and both the user and administrator paths fall back to `index.html`
for React Router. REST is proxied under `/api/v1`, and SSE responses are unbuffered.
