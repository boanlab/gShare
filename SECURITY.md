# Security Policy

## Supported versions

GShare has not reached 1.0. Security fixes land on `main` and in the next tagged
release; older tags are not patched.

| Version | Supported |
|---|---|
| `main` and the latest `v0.x` tag | ✅ |
| Any earlier tag | ❌ |

## Reporting a vulnerability

Do not open a public issue.

Report privately through
[GitHub Security Advisories](https://github.com/boanlab/gShare/security/advisories/new),
or by email to <namjh@dankook.ac.kr>.

Please include the affected component (`gshare-api`, `gshare-worker`,
`gshare-operator`, the console, or the Helm chart), the version or image digest, and
enough detail to reproduce. A proof of concept helps but is not required.

What to expect:

- Acknowledgement within 5 working days.
- An assessment, and a fix or mitigation plan, within 30 days for confirmed issues.
- Credit in the release notes unless you ask otherwise.

## Scope

GShare hands tenant workloads a shared GPU, so the security boundary matters. In scope:

- Cross-tenant access to sessions, volumes, credits, or audit records.
- Privilege escalation across the role planes (`member` → `group_admin` → `org_admin` → `super_admin`).
- Forging or replaying the internal RS256 JWT that the operator uses to call back into the API.
- Escaping a session pod, or reaching another tenant's VRAM through the fractional GPU path.
- Leaking kubeconfigs, wallet balances, or any other tenant data through the API or the console.

Out of scope:

- Vulnerabilities in upstream dependencies with no GShare-specific exploit path — report
  those upstream (notably [HAMi](https://github.com/Project-HAMi/HAMi), vendored under
  `third_party/`).
- Findings that require cluster-admin on the hosting cluster, which is already a full compromise.
- Denial of service caused by a tenant exhausting the credits or quota assigned to them —
  that is the intended accounting behaviour.

## Hardening expectations for operators

A GShare install is only as isolated as the cluster underneath it. The deployment
assets in `deploy/security/` are the baseline: `restricted` Pod Security Admission on
`gshare-sessions`, default-deny NetworkPolicies, per-namespace LimitRange and
ResourceQuota, and namespace-scoped RBAC without wildcard verbs. `deploy/supplychain/`
adds Kyverno cosign image verification in enforce mode. Do not disable these in
production.
