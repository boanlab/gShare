# Product screenshots — the Nexus AI Lab walkthrough

> 📚 [Documentation home](../README.md)

Every console screen, captured per role, against a fictional AI company — **Nexus AI Lab** —
running its GPU fleet on GShare. 48 captures at 1440×900 in the English locale.

Regenerate them from a seeded compose stack with
[`test/e2e/ux/screenshots.js`](../../test/e2e/ux/screenshots.js).

## Cast

- **Organization:** Nexus AI Lab (one organization)
- **Groups:** the Vision team and the NLP team
- **People**
  - Platform administrator `admin@example.com` — `super_admin`, running the infrastructure,
    clusters, and offerings
  - Jieun Lee `jieun@nexusai.dev` — `org_admin` for Nexus AI Lab: budget, groups, users
  - Minjun Park `minjun@nexusai.dev` — Vision team lead (`group_admin`)
  - Haneul Jung `haneul@nexusai.dev` — NLP team lead (`group_admin`)
  - Seoyeon Kim `seoyeon@nexusai.dev` — Vision team researcher
  - Dohyun Lee `dohyun@nexusai.dev` — Vision team researcher
  - Woojin Choi `woojin@nexusai.dev` — NLP team researcher
- **Demo password (all accounts):** `Nexus2026!`

## The scenario

- **Credits.** The platform administrator tops the organization wallet up with 50,000. Jieun
  allocates 20,000 to the Vision team and 15,000 to the NLP team; the team leads allocate on to
  individual wallets. One request is awaiting approval.
- **Hardware.** One cluster, `lab-seoul`, with four nodes and eleven GPUs: four RTX PRO 6000 and
  three RTX 4090 in fractional mode, four H100 80GB exclusive. One node is cordoned, one device
  degraded, one card lent out.
- **Sessions.** `vit-base-ft` and `notebook-scratch` (fractional, running), `llama3-eval`
  (exclusive, running), `sweep-lr-0003` (pending), `diffusion-train` (paused), `bert-baseline`
  and `ocr-pipeline` (terminated), `stale-loader` (error).
- **Data.** Four volumes across the dataset, group and scratch types, one with snapshots and an
  expansion request outstanding.

---

## User console — as Seoyeon Kim (researcher)

| # | File | Screen |
|---|---|---|
| 01 | `01-public-login.png` | Login, unauthenticated |
| 02 | `02-user-dashboard.png` | Dashboard — credits, active sessions, GPU VRAM |
| 03 | `03-user-sessions.png` | Session list, own sessions only |
| 04 | `04-user-session-new.png` | New session wizard — compute, GPU, image, estimated cost |
| 05 | `05-user-session-detail.png` | `vit-finetune` detail — pause, resume, connect |
| 06 | `06-user-session-connect.png` | Connect — single-use VS Code, Jupyter, terminal links |
| 07 | `07-user-queue.png` | Queue — priority and waiting time |
| 08 | `08-user-wallet.png` | Wallet — balance and usage history |
| 09 | `09-user-wallet-request.png` | Requesting more credits from the group |
| 10 | `10-user-volumes.png` | Volumes — personal and shared |
| 11 | `11-user-volume-new.png` | New volume — scope, access mode, capacity |
| 12 | `12-user-volume-share.png` | Sharing a volume, read or write, per user |
| 13 | `13-user-volume-quota.png` | Volume capacity — policy limit and expansion request |
| 14 | `14-user-volume-snapshots.png` | Snapshots — create and restore |
| 15 | `15-user-account.png` | Account — organization, group, role, display name |

## Administrator console — as the platform administrator (`super_admin`)

| # | File | Screen |
|---|---|---|
| 16 | `16-superadmin-admin-dashboard.png` | Dashboard — global resource and session summary |
| 17 | `17-superadmin-admin-orgs.png` | Organizations |
| 18 | `18-superadmin-admin-org-new.png` | New organization |
| 19 | `19-superadmin-admin-org-admins.png` | Appointing organization admins |
| 20 | `20-superadmin-admin-users.png` | All users |
| 21 | `21-superadmin-admin-user-new.png` | New user |
| 22 | `22-superadmin-admin-user-edit.png` | Editing a user |
| 23 | `23-superadmin-admin-groups.png` | Groups, by organization |
| 24 | `24-superadmin-admin-group-new.png` | New group |
| 25 | `25-superadmin-admin-group-admins.png` | Appointing group admins |
| 26 | `26-superadmin-admin-resources.png` | Resources — GPU catalogue, presets, rates |
| 27 | `27-superadmin-admin-offering-new.png` | New offering |
| 28 | `28-superadmin-admin-offering-edit.png` | Editing an offering (RTX 4090) |
| 29 | `29-superadmin-admin-policy-new.png` | New resource policy |
| 30 | `30-superadmin-admin-clusters.png` | Clusters — connection state, nodes, GPUs |
| 31 | `31-superadmin-admin-cluster-new.png` | Registering a cluster with a kubeconfig |
| 32 | `32-superadmin-admin-nodes.png` | GPU nodes |
| 33 | `33-superadmin-admin-node-devices.png` | GPU devices on a node — occupancy and mode |
| 34 | `34-superadmin-admin-allocations.png` | Credit allocation and pending requests |
| 35 | `35-superadmin-admin-monitor.png` | Live session and queue monitoring |
| 36 | `36-superadmin-admin-audit.png` | Audit log |
| 37 | `37-superadmin-admin-images.png` | Images and templates |
| 38 | `38-superadmin-admin-image-import.png` | Importing an image |

## Administrator console — as Jieun Lee (`org_admin`, organization scope)

| # | File | Screen |
|---|---|---|
| 39 | `39-orgadmin-dashboard.png` | Dashboard, scoped to Nexus AI Lab |
| 40 | `40-orgadmin-users.png` | Users in the organization only |
| 41 | `41-orgadmin-groups.png` | Groups in the organization only |
| 42 | `42-orgadmin-allocations.png` | Allocating budget per group |
| 43 | `43-orgadmin-monitor.png` | Sessions in the organization |
| 44 | `44-orgadmin-audit.png` | Audit log, organization scope |

## Administrator console — as Minjun Park (Vision team lead, `group_admin`)

| # | File | Screen |
|---|---|---|
| 45 | `45-groupadmin-dashboard.png` | Dashboard, scoped to the Vision team |
| 46 | `46-groupadmin-users.png` | Vision team members |
| 47 | `47-groupadmin-groups.png` | The Vision team |
| 48 | `48-groupadmin-monitor.png` | Vision team sessions |

---

## Reproducing the captures

With the seed data in place:

```bash
docker run --rm --network host \
  -v $PWD/docs/screenshots/_capture.mjs:/work/screenshots.mjs:ro \
  -v $PWD/docs/screenshots:/work/screenshots \
  -e PW=Nexus2026! -e S_VIT=... -e SV=... -e ORG=... -e DEPT_V=... -e OFF=... \
  -e U_SEOYEON=... -e NODE_ID=... \
  -w /work mcr.microsoft.com/playwright:v1.49.0-jammy \
  sh -c 'npm init -y >/dev/null 2>&1 && npm i playwright@1.49.0 >/dev/null 2>&1 && node screenshots.mjs'
```

`_capture.mjs` logs in as each role and walks the routes, capturing as it goes.
