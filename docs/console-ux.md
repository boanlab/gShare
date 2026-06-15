# Console UX standards

What every screen in the console is expected to do, and the components that make it cheap to do.
Most of it is checked automatically by [`test/e2e/ux`](../test/e2e/ux) — eight personas, four
viewports, two languages, against a control plane with no GPU attached.

## Every screen

| Guarantee | How | Checked by |
|---|---|---|
| The browser tab is named after the screen | `<PageHeader title>` calls `useDocumentTitle` | `page.title.notPerScreen` |
| One `<h1>`, in document order | `<PageHeader>` | `page.heading.noH1` |
| `<main>`, `<nav>` and `<header>` landmarks, and a skip link | `Layout` | `page.landmark.*`, `page.skipLink.missing` |
| `<html lang>` follows the chosen language | `frontend/src/i18n/index.ts` | `i18n.htmlLangMismatch` |
| A visible focus ring on every control | `:focus-visible` in `frontend/src/styles/index.css` | `keyboard.noFocusRing` |
| Text meets WCAG AA against its own background | theme tokens | `contrast.belowWcagAA` |
| No horizontal scroll at 390px | `.gs-shell`, `hideOnMobile` columns | `responsive.horizontalScroll` |
| Touch targets ≥ 44px, text ≥ 12px on a phone | phone overrides at the end of `frontend/src/styles/index.css` | `responsive.tapTargetTooSmall` |
| Animation respects `prefers-reduced-motion` | `frontend/src/styles/index.css` | — |

## Screens below the top level

- **A breadcrumb trail and a way back.** `<PageHeader crumbs={[…]}>` plus `<BackLink>`. Without
  them the only way up is the browser Back button, which on a half-filled form loses the work.
- **The record is named in the heading**, not just its type: "Edit group — NLP팀".

## Lists

Everything here lives in `frontend/src/components/Table.tsx` and `frontend/src/hooks/useTableState.ts`, so a screen gets it
by passing `sort`, `dir` and `onSort`:

- **Sortable columns.** A column sorts on its underlying value, not its rendered node; set
  `sortable: false` where ordering is meaningless.
- **Search, a live match count, and a one-click clear** (`<TableToolbar>`).
- **State in the URL.** Search, sort, page and tab are query parameters, so a view can be
  bookmarked, shared, and recovered with Back after clicking into a row.
- **Two different empty states.** `<EmptyState>` when there is nothing yet — with the action that
  creates the first one — and `<NoResults>` when a filter matched nothing, with the way to clear it.
  A blank table reads as a broken screen.
- **A skeleton while loading** (`<TableSkeleton>`), not a blank panel and not a bare spinner.
- **A sticky header** past fifteen rows, **pagination** past twenty-five.
- **Row selection** wherever an action is worth doing to five things at once.
- **A caption** so the table is announced as something other than "table".

## Forms

- **Real labels.** `<Field>` wires `<label for>`, the required marker, the hint and the error
  message to the control, and sets `aria-describedby` / `aria-invalid` / `aria-required`. A
  placeholder is not a label: it disappears the moment the user types.
- **A `<form>` element**, so Enter submits. This is the fastest path for a keyboard user and it is
  free.
- **Validation on blur, not on submit.** An email or a quantity that is obviously wrong is flagged
  where it was typed, before the user commits to the action.
- **`autocomplete`, `inputmode`, `min`, `max` and `step`** on the fields that take them, so
  password managers work and phones show the right keyboard.
- **A named reason when the primary button is disabled** (`<DisabledReason>`). A dead button with
  no explanation leaves the user guessing which field is at fault.
- **An unsaved-changes guard** (`useUnsavedGuard`), covering both in-app navigation and a closed tab.

## Numbers

Every numeric field declares `min`, `max`, `step` and `inputmode`, and clamps to its range when it
is left. The attributes bind the spinner and the browser's own validation, but nothing stops a
typed or pasted value outside them — so without the clamp a negative quantity or a stray extra
digit reaches the API and comes back as a 422 the user has to decode.

## Destructive actions

`useConfirm()` replaces `window.confirm`, which cannot show what is about to be lost, cannot be
translated with the rest of the console, and blocks the whole tab.

- **Name the thing** — "Terminate vit-base-ft?", not "Are you sure?".
- **List the consequences**: the credits about to be settled, the data that goes with the volume,
  the sessions that will be orphaned.
- **Require the name to be typed** for the irreversible: deleting an organization, a cluster, a
  shared dataset, or terminating five sessions at once.
- Escape and the overlay decline; focus is trapped while it is open and returned to the control
  that opened it.
- **Offer undo where the action can be taken back.** Revoking access, for instance, is applied
  immediately and the toast holds the way back (`pushToast(kind, message, { label, run })`) — which
  is better than a confirmation, because the common case costs nothing. Reserve typed confirmation
  for what cannot be undone.

## Errors from the server

A rejected save is reported **on the form**, not only in a toast: a message that disappears after
five seconds and never says which field caused it is not an error message. Keep it until the next
attempt, next to the action that failed.

## Live data

- **Say when it was last fetched, and offer a refresh** (`<PageHeader updatedAt onRefresh>`).
  Without it a user cannot tell stale data from a stalled job.
- **Relative time with the exact value on hover** (`<Timestamp>`): "6 hours ago" answers the
  question people have; the `datetime` attribute and the tooltip let them correlate with a log.
  The relative text re-renders on a timer, so a tab left open overnight does not claim a job
  started "2 minutes ago".

## Identifiers and copying

Session, cluster, device and wallet identifiers are quoted into tickets, `kubectl` commands and
chat constantly. Every one of them gets a `<CopyButton>` with visible confirmation — silence makes
people press twice and doubt both presses.

## Language

- English is the source and the fallback; Korean is a full translation. Both bundles are checked
  for key parity.
- Never assemble a sentence from fragments — use placeholders, and `<Trans>` when part of the
  sentence needs markup, so word order stays translatable.
- The language control is reachable from every screen, including the signed-out and error screens.
- Names an administrator typed stay as they were typed, in either language.

## Adding a screen

Start from `PageHeader` + `Table`/`Field` and most of this comes for free. Then run the audit for
the persona who will use it:

```bash
UX_PERSONA=researcher node test/e2e/ux/audit.js
```
