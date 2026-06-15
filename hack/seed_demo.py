#!/usr/bin/env python3
"""Demo seed: fills a freshly deployed GShare with the fictional *Nexus AI Lab* scenario.

Creates one organization, two groups (the Vision and NLP teams), six users, and the credit
allocations down the hierarchy: system to organization to group to individual. Offerings and images
are already seeded at bootstrap and are not touched here; the GPU catalogue lives in
hack/seed_resources.py.

The scenario matches docs/screenshots/README.md. Organizations, groups, and users are idempotent —
an existing one is looked up and reused. Step 5, the credit allocation, assumes a **fresh database
and a single run**; running it again allocates again, so a clean demo means redeploying first.

The seeded names are Korean, matching the published screenshots.

Usage:
  GSHARE_ADMIN_TOKEN=<super_admin access token> \
  GSHARE_API=https://<console>/api/v1 \
  python3 hack/seed_demo.py

For the token: sign in to the console as a super_admin, open the browser's network inspector, and
copy the Bearer value from the Authorization header of any /api request.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.request

API = os.environ.get("GSHARE_API", "http://localhost:8080/api/v1").rstrip("/")
TOKEN = os.environ.get("GSHARE_ADMIN_TOKEN", "").strip()
PW = os.environ.get("GSHARE_DEMO_PASSWORD", "Nexus2026!")
if not TOKEN:
    sys.exit("GSHARE_ADMIN_TOKEN is required: a super_admin access token.")

ORG = "Nexus AI Lab"
GROUPS = ["비전팀", "NLP팀"]
# (email, name, group, initial_role). Jieun is promoted to org_admin separately, below.
USERS = [
    ("jieun@nexusai.dev",   "이지은", "비전팀", "member"),
    ("minjun@nexusai.dev",  "박민준", "비전팀", "group_admin"),
    ("haneul@nexusai.dev",  "정하늘", "NLP팀",  "group_admin"),
    ("seoyeon@nexusai.dev", "김서연", "비전팀", "member"),
    ("dohyun@nexusai.dev",  "이도현", "비전팀", "member"),
    ("woojin@nexusai.dev",  "최우진", "NLP팀",  "member"),
]
ORG_ADMIN_EMAIL = "jieun@nexusai.dev"
ORG_TOPUP = 50000                                   # system to organization
GROUP_ALLOC = {"비전팀": 20000, "NLP팀": 15000}        # organization to group
USER_ALLOC = {"seoyeon@nexusai.dev": 3000, "dohyun@nexusai.dev": 2000, "woojin@nexusai.dev": 2500}  # group to individual


def req(method: str, path: str, body: dict | None = None) -> tuple[int, dict | list]:
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(API + path, data=data, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Content-Type", "application/json")
    if method in ("POST", "PUT", "PATCH"):
        # The key hashes method, path, and body together. Calling one path several times with
        # different bodies — /credits/allocate, for instance — would otherwise collide on the key and
        # be deduplicated into a no-op. Retrying the identical call keeps the same key, so it stays
        # idempotent.
        digest = hashlib.sha256(f"{method}{path}{data!r}".encode()).hexdigest()[:32]
        r.add_header("Idempotency-Key", f"seed-demo-{digest}")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "null")
        except Exception:
            return e.code, {}


def find(items: list, **kv):
    for it in items:
        if all(it.get(k) == v for k, v in kv.items()):
            return it
    return None


def get_or_create(list_path: str, create_path: str, match: dict, body: dict, label: str) -> dict:
    """Look it up if it exists, create it otherwise. Idempotent."""
    _, items = req("GET", list_path)
    items = items if isinstance(items, list) else items.get("data", []) if isinstance(items, dict) else []
    existing = find(items, **match)
    if existing:
        print(f"  = {label}: already exists ({existing.get('id')})")
        return existing
    st, res = req("POST", create_path, body)
    if st in (200, 201) and isinstance(res, dict):
        print(f"  + {label}: created ({res.get('id')})")
        return res
    print(f"  ! {label}: creation failed, {st} {res}")
    return {}


def wallet_of(owner_type: str, owner_id: str) -> dict | None:
    _, ws = req("GET", "/credits/wallets")
    return find(ws if isinstance(ws, list) else [], owner_type=owner_type, owner_id=owner_id)


def topup(wallet_id: str, amount: float, reason: str) -> None:
    st, _ = req("POST", f"/credits/wallets/{wallet_id}/adjust", {"amount": amount, "reason": reason})
    print(f"  $ topup {wallet_id} += {amount} -> {st}")


def allocate(from_w: str, to_w: str, amount: float, reason: str) -> None:
    st, res = req("POST", "/credits/allocate",
                  {"from_wallet_id": from_w, "to_wallet_id": to_w, "amount": amount, "reason": reason})
    print(f"  → allocate {amount}: {from_w} -> {to_w} ({st})")
    if st not in (200, 201):
        print(f"      {res}")


def main() -> None:
    print(f"API={API}")
    print("1. Organization")
    org = get_or_create("/organizations", "/organizations", {"name": ORG}, {"name": ORG}, ORG)
    org_id = org.get("id")

    print("2. Groups")
    gids: dict[str, str] = {}
    for g in GROUPS:
        grp = get_or_create("/projects", "/projects", {"name": g, "org_id": org_id},
                            {"org_id": org_id, "name": g, "create_project_wallet": True}, g)
        gids[g] = grp.get("id")

    print("3. Users")
    uids: dict[str, str] = {}
    for email, name, group, role in USERS:
        u = get_or_create("/users", "/users", {"email": email},
                          {"email": email, "name": name, "group_id": gids[group],
                           "status": "active", "initial_role": role, "password": PW}, f"{name}({email})")
        uids[email] = u.get("id")

    print("4. Appointing the organization admin")
    if org_id and uids.get(ORG_ADMIN_EMAIL):
        st, _ = req("POST", f"/organizations/{org_id}/admins", {"user_id": uids[ORG_ADMIN_EMAIL]})
        print(f"  org_admin = {ORG_ADMIN_EMAIL} -> {st}")

    print("5. Credit allocation: system to organization to group to individual")
    ow = wallet_of("organization", org_id) or wallet_of("org", org_id)
    if not ow:
        print("  ! organization wallet not found; skipping the allocation"); return
    topup(ow["id"], ORG_TOPUP, "seed: system to organization")
    for g, amt in GROUP_ALLOC.items():
        gw = wallet_of("group", gids[g]) or wallet_of("project", gids[g])
        if gw:
            allocate(ow["id"], gw["id"], amt, f"seed: organization to {g}")
    for email, amt in USER_ALLOC.items():
        uw = wallet_of("user", uids[email])
        grp = next(g for e, n, g, r in USERS if e == email)
        gw = wallet_of("group", gids[grp]) or wallet_of("project", gids[grp])
        if uw and gw:
            allocate(gw["id"], uw["id"], amt, f"seed: {grp}→{email}")

    print("Done. Offerings and images come from the bootstrap seed; the GPU catalogue is in hack/seed_resources.py.")


if __name__ == "__main__":
    main()
