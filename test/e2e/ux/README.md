# Persona UX audit

An automated review of the console, driven as eight different people on eight different screens.
It needs the control plane and nothing else — **no GPU, no Kubernetes cluster** — so it runs on a
laptop in about ten minutes and is the fastest way to see what the console is like to use before
any hardware is involved.

## What it does

For every persona, it signs in over the API, walks every screen that persona is entitled to reach
at the viewport and language they actually work in, and then:

- **reads** the rendered DOM (`audits.js`) — landmarks, headings, labels, contrast, tap targets,
  timestamps, identifiers, empty states, table affordances;
- **drives** it (`flows.js`) — submits an empty form, types an invalid value, presses delete,
  filters a list to nothing, opens a dialog and presses Escape, switches language, navigates on a
  phone.

Every observation names a specific element on a specific screen for a specific persona, so the
output is a work list rather than a score.

## Running it

```bash
docker compose up -d                                   # control plane on :8080, console on :8000
node test/e2e/ux/seed.js                               # volumes, requests, webhooks
docker compose exec -T postgres psql -U gshare -d gshare < test/e2e/ux/fixture.sql
node test/e2e/ux/audit.js                              # writes out/findings.{json,md}
```

Playwright is the only dependency (`npm install` in this directory). To run it without installing
anything on the host:

```bash
docker run --rm --network host -v "$PWD":/w -w /w -u "$(id -u):$(id -g)" -e HOME=/tmp \
  -e ORIGIN=http://localhost:8000 -e API_ORIGIN=http://localhost:8080 \
  mcr.microsoft.com/playwright:v1.48.0-jammy node audit.js
```

| Variable | Default | What it does |
|---|---|---|
| `ORIGIN` | `http://localhost:8000` | Console origin |
| `API_ORIGIN` | `ORIGIN` | API origin, when it is served separately |
| `UX_PERSONA` | all | Audit one persona, by id |
| `UX_SKIP_FLOWS` | unset | Static audits only — faster, and makes no writes |
| `UX_OUT` | `./out` | Where the report is written |
| `UX_*_EMAIL` / `UX_*_PASSWORD` | seeded demo accounts | Credentials per persona |

## The data it needs

`seed.js` and `fixture.sql` exist because most of what this audit judges only appears when a screen
has something on it: an empty table cannot be checked for sortable columns, and a list with two
rows never reveals that it has no pagination.

- `seed.js` goes through the API — volumes, snapshots, credit and top-up requests, webhooks.
The audit drives the real console against real records, so a run can change state: it types into
forms, presses buttons and takes the undo it is testing. Point it at a throwaway deployment, never
at anything whose data matters. `fixture.sql` restores the demo accounts on the way in, so a run is
repeatable whatever the previous one left behind.

- `fixture.sql` writes a **synthetic data plane** straight to the database: a connected cluster,
  four nodes and eleven GPUs spanning exclusive and fractional modes, a cordoned node, a degraded
  device, a lent-out card, and sessions in every lifecycle state. Creating those through the API
  needs a reachable Kubernetes API server; the control plane cannot tell the difference, so every
  screen renders as it would against real hardware. Nothing here can run CUDA.

## Personas

| Persona | Role | Viewport | Language | What they are trying to do |
|---|---|---|---|---|
| `platform-admin` | super_admin | 1920 | en | Onboard a cluster, publish an offering, trace an action |
| `org-admin` | org_admin | 1280 | ko | Split a budget, approve a top-up, explain a month of spend |
| `team-lead` | group_admin | 1280 | ko | Add a member, cap a runaway session |
| `researcher` | member | 1280 | en | Launch a session, reattach, not run out of credit |
| `newcomer` | member | 1280 | en | Understand what this is and find the one button |
| `mobile-user` | member | 390 | ko | Is it still running, what has it cost, stop it |
| `keyboard-user` | member | 1280 | en | Reach every control by Tab, escape every dialog |
| `tablet-lead` | group_admin | 820 | en | Skim the numbers, approve without a keyboard |

## Reading the output

`out/findings.json` is the machine-readable form; `out/findings.md` is the same thing grouped by
severity and by rule. Observations are deduplicated into **jobs** — one rule on one screen at one
selector is one thing to fix, however many personas hit it.

| Severity | Meaning |
|---|---|
| `blocker` | The persona cannot complete the task, or the control does not exist for them |
| `major` | They can finish, but are misled, delayed, or at risk of losing work |
| `minor` | Friction: extra clicks, missing context, a convention broken |
| `polish` | Correct but unpolished |

## Known gaps

Row selection on the user list and the offering catalogue is reported and not implemented: acting
on a selection needs bulk endpoints the API does not offer, and inventing one to satisfy a rule
would be worse than carrying the item.

## Adding a rule

Rules live in `audits.js` (DOM) and `flows.js` (interaction) and are plain functions returning
`{ rule, severity, message, selector, evidence }`. Two things keep the output worth reading:

- **Be specific.** "This input has no accessible name" is a job; "forms need work" is not.
- **Say why it matters, in the message.** A rule whose message only restates its own name gets
  ignored; one that names the consequence ("the user loses the field's name mid-entry") gets fixed.

The audit is a work list, not a gate. False positives are worse than missing rules — a list nobody
trusts is a list nobody reads — so when a rule fires on something the console does deliberately,
narrow the rule and say why beside it.
