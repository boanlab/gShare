# Administrator manual

> 📚 [Documentation home](./README.md)

The **administrator console** is where you manage organizations, groups, and users; the
resource catalogue, offerings, and policy; credit allocation; clusters and nodes; live
session monitoring; and the audit log. The end-user screens are covered in
[`user-manual.md`](./user-manual.md).

> The screenshots are of the fictional company *Nexus AI Lab* (see
> [`screenshots/README.md`](screenshots/README.md)), captured in English. The console also ships
> in Korean — switch from the top bar.

## Switching consoles, and what each role sees

Switch to the administrator console from the top right. Menus and data are scoped to your
role:

- **`group_admin`** — their own group.
- **`org_admin`** — their own organization: its groups, users, and budget allocation.
- **`super_admin`** — everything, including organizations, offerings, policy, clusters,
  nodes, and images.

Anything that cannot be undone — deleting a user, group, organization, cluster, offering, preset
or snapshot — asks for the record's name to be typed. Anything that can be, such as removing an
administrator, applies at once and offers **Undo** for a few seconds.

---

## 1. Administrator dashboard

A summary of resources and sessions within your scope.

| Super admin (everything) | Organization admin | Group admin |
|---|---|---|
| ![Global dashboard](screenshots/16-superadmin-admin-dashboard.png) | ![Organization dashboard](screenshots/39-orgadmin-dashboard.png) | ![Group dashboard](screenshots/45-groupadmin-dashboard.png) |

---

## 2. Live session monitoring

Watch sessions and the queue in real time: owner, organization, group, resources, and state
(running, paused, terminated). Sort and search the list, and **force-terminate** one session or
several at once — a bulk termination asks for the count to be typed, since the sessions belong to
other people.

![Session monitoring, all scopes](screenshots/35-superadmin-admin-monitor.png)

An organization admin sees only their organization's sessions; a group admin sees only
their group's.

| Organization admin | Group admin |
|---|---|
| ![Organization monitoring](screenshots/43-orgadmin-monitor.png) | ![Group monitoring](screenshots/48-groupadmin-monitor.png) |

---

## 3. Organizations (super admin)

Create organizations and appoint their administrators.

![Organization list](screenshots/17-superadmin-admin-orgs.png)
![New organization](screenshots/18-superadmin-admin-org-new.png)
![Organization admins](screenshots/19-superadmin-admin-org-admins.png)

---

## 4. Groups

Create groups under an organization and appoint their administrators. An organization admin
sees only their own organization's groups.

![Group list](screenshots/23-superadmin-admin-groups.png)
![New group](screenshots/24-superadmin-admin-group-new.png)
![Group admins](screenshots/25-superadmin-admin-group-admins.png)

| Organization admin scope | Group admin scope |
|---|---|
| ![Groups in the organization](screenshots/41-orgadmin-groups.png) | ![My group](screenshots/47-groupadmin-groups.png) |

---

## 5. Users

Add and edit users. Only users within your scope are listed.

![User list](screenshots/20-superadmin-admin-users.png)
![New user](screenshots/21-superadmin-admin-user-new.png)
![Edit user](screenshots/22-superadmin-admin-user-edit.png)

| Organization admin scope | Group admin scope |
|---|---|
| ![Organization users](screenshots/40-orgadmin-users.png) | ![Group members](screenshots/46-groupadmin-users.png) |

---

## 6. Resources, offerings, presets, and policy (super admin)

Manage GPU offerings (per-model full card, hourly rate, minimum CUDA version), presets
(compute plus a GPU fraction tier — XL ½, L ¼, M ⅛, S 1/16, SS 1/32 — or exclusive), and
resource policies (concurrency, resource ceilings, idle timeout). Policies resolve
most-specific first: **user → group → organization → global**.

The storage rate (`STORAGE_CREDIT_PER_GB_HOUR`) is deployment configuration, set through
the environment. It has no admin UI by design.

![Resources, offerings, presets](screenshots/26-superadmin-admin-resources.png)
![New offering](screenshots/27-superadmin-admin-offering-new.png)
![Edit offering](screenshots/28-superadmin-admin-offering-edit.png)
![New resource policy](screenshots/29-superadmin-admin-policy-new.png)

---

## 7. Credit allocation and requests

Allocate credits down the hierarchy — system → organization → group → individual — and
approve or reject users' allocation requests. An organization admin allocates from the
organization to its groups; a group admin from the group to individuals.

Note that personal and group wallets are also charged continuously for **provisioned volume
capacity**, on top of session compute. A balance can therefore fall even with no session
running.

![Credit allocation, global](screenshots/34-superadmin-admin-allocations.png)
![Credit allocation, organization admin](screenshots/42-orgadmin-allocations.png)

---

## 8. Clusters and nodes (super admin)

Register clusters with a kubeconfig, and manage connection state, nodes, and GPU devices
including their occupancy and mode.

![Clusters](screenshots/30-superadmin-admin-clusters.png)
![Register a cluster](screenshots/31-superadmin-admin-cluster-new.png)
![Nodes](screenshots/32-superadmin-admin-nodes.png)
![GPU devices on a node](screenshots/33-superadmin-admin-node-devices.png)

---

## 9. Images and templates (super admin)

Manage base images — CUDA version, public or private — and import new ones.

![Images and templates](screenshots/37-superadmin-admin-images.png)
![Import an image](screenshots/38-superadmin-admin-image-import.png)

---

## 10. Audit log

Trace permission, billing, and resource changes within your scope. Filter by actor, action, target
and period; the filter is in the address bar, so a query can be pasted into a ticket and reproduce
the same rows. Open an entry for the full before-and-after and the identifiers to quote.

![Audit log, global](screenshots/36-superadmin-admin-audit.png)
![Audit log, organization](screenshots/44-orgadmin-audit.png)

---

## Appendix — capabilities by role

| Capability | User | Group admin | Organization admin | Super admin |
|---|---|---|---|---|
| Own sessions, volumes, wallet | ✅ | ✅ | ✅ | ✅ |
| Session monitoring and audit | — | Group | Organization | Everything |
| User and group management | — | Group | Organization | Everything |
| Organizations, offerings, policy, clusters, nodes, images | — | — | Partial (own organization) | ✅ |
| Credit allocation | Request only | Group → individual | Organization → group | Top-up and everything |
