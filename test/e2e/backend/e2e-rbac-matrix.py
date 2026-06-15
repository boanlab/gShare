"""Role-based permission matrix, end to end: super_admin, org_admin, group_admin, and member.

Runs inside the api container, using python-jose and httpx against the api on localhost:8080.
Each role's HS256 token (signed with USER_JWT_SECRET) calls the endpoints, and only authorization is
checked:
- ALLOW passes when the response is neither 401 nor 403. A 2xx, or a 4xx that is a legitimate
  business-rule rejection, both count as authorized.
- DENY passes only on exactly 403.
"""
import os, time, sys
import httpx
from jose import jwt

BASE = os.environ.get("BASE", "http://localhost:8080/api/v1")
SECRET = os.environ.get("GSHARE_USER_JWT_SECRET", "gshare-dev-secret")
GRP = "grp_e2e"

ROLES = {
    "super": ("usr_super", "super@e2e.test", "super_admin"),
    "org":   ("usr_org",   "org@e2e.test",   None),
    "grp":   ("usr_grp",   "grp@e2e.test",   None),
    "mem":   ("usr_mem",   "member@e2e.test", None),
}

def tok(uid, email, role):
    c = {"sub": uid, "email": email, "exp": int(time.time()) + 3600}
    if role:
        c["global_role"] = role
    return jwt.encode(c, SECRET, algorithm="HS256")

TOKENS = {k: tok(*v) for k, v in ROLES.items()}

def hdr(role, extra=None):
    h = {"Authorization": f"Bearer {TOKENS[role]}", "X-Project-Id": GRP}
    if extra:
        h.update(extra)
    return h

# (label, method, path, the set of allowed roles, body, extra headers)
IDEM = {"Idempotency-Key": "e2e-" + str(int(time.time()))}
CASES = [
    ("auth.me",            "GET",  "/auth/me",                         {"super","org","grp","mem"}, None, None),
    ("offerings.list",     "GET",  "/offerings",                       {"super","org","grp","mem"}, None, None),
    ("presets.list",       "GET",  "/resource-presets",                {"super","org","grp","mem"}, None, None),
    ("sessions.list",      "GET",  "/sessions",                        {"super","org","grp","mem"}, None, None),
    ("dashboard",          "GET",  "/dashboard/summary",               {"super","org","grp","mem"}, None, None),
    ("clusters.list",      "GET",  "/clusters",                        {"super"},                   None, None),
    ("users.list",         "GET",  "/users",                           {"super","org"},             None, None),
    ("orgs.list",          "GET",  "/organizations",                   {"super","org"},             None, None),
    ("webhooks.list",      "GET",  "/webhooks",                        {"super","org"},             None, None),
    ("memberships.read",   "GET",  f"/projects/{GRP}/memberships",     {"super","org","grp"},       None, None),
    ("queue.read",         "GET",  "/queue",                           {"super","org","grp"},       None, None),
    ("budgets.read",       "GET",  "/budgets",                         {"super","org","grp"},       None, None),
    ("audit.read",         "GET",  "/audit-logs",                      {"super","org","grp"},       None, None),
    # POST and PUT with valid bodies, so the only possible reason for failure is authorization.
    ("org.create",         "POST", "/organizations",                   {"super"},
        {"name": "probe-org-"+str(int(time.time()))}, None),
    ("offering.create",    "POST", "/offerings",                       {"super"},
        {"name":"probe-off","resource_class":"cpu","credit_per_hour":"0"}, None),
    ("policy.create",      "POST", "/resource-policies",               {"super","org","grp"},
        {"scope":"group","scope_id":GRP,"max_concurrent":1,"max_queued":1,
         "max_runtime_min":60,"idle_timeout_sec":1800}, None),
    ("topup",              "POST", "/credits/wallets/wal_mem/topup",   {"super"},
        {"amount":10}, IDEM),
    ("set_global_role",    "PUT",  "/users/usr_mem/global-role",       {"super"},
        {"global_role": None}, None),
    ("preview_cost",       "POST", "/sessions/preview-cost",           {"super","org","grp","mem"},
        {"offering_id":"off_cpu_free","resource_class":"cpu","cluster_id":"clu_fake"}, None),
]

def main():
    results = []
    fails = 0
    with httpx.Client(base_url=BASE, timeout=15) as cli:
        # 0. Login round trip, which exercises password hashing, for all four roles.
        print("== password login ==")
        for role,(uid,email,_) in ROLES.items():
            r = cli.post("/auth/login", json={"email":email,"password":"Passw0rd!"})
            ok = r.status_code==200 and r.json().get("access_token")
            print(f"  {role:5} {email:18} -> {r.status_code} {'OK' if ok else 'FAIL'}")
            if not ok: fails+=1

        print("\n== permission matrix (✓ as expected, ✗ mismatch) ==")
        header = f"{'action':22} {'method':5} " + " ".join(f"{r:>10}" for r in ROLES)
        print(header); print("-"*len(header))
        for label, method, path, allowed, body, extra in CASES:
            row = [f"{label:22} {method:5}"]
            for role in ROLES:
                try:
                    r = cli.request(method, path, headers=hdr(role, extra), json=body)
                    sc = r.status_code
                except Exception as e:
                    sc = -1
                expect_allow = role in allowed
                if expect_allow:
                    passed = sc not in (401,403,-1)
                else:
                    passed = sc == 403
                if not passed: fails += 1
                mark = "✓" if passed else "✗"
                row.append(f"{mark}{sc:>4}({'A' if expect_allow else 'D'})")
            print(" ".join(f"{c:>10}" if i else c for i,c in enumerate(row)))
            results.append((label, allowed))

    print(f"\n== summary: {'ALL PASS' if fails==0 else str(fails)+' FAIL'} ==")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
