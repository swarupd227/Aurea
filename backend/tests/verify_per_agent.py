"""Verify per-agent foundation overrides differ from firm policy and are enforced."""
import json, urllib.request as u, urllib.error

B = "http://localhost:8010"


def login(e):
    return json.load(u.urlopen(u.Request(B + "/api/auth/login-json",
        data=json.dumps({"email": e, "password": "aurea"}).encode(),
        headers={"Content-Type": "application/json"})))["access_token"]


def call(p, d=None, t=None, m=None):
    h = {"Content-Type": "application/json"}
    if t: h["Authorization"] = f"Bearer {t}"
    r = u.Request(B + p, data=(json.dumps(d).encode() if d is not None else None), headers=h,
                  method=m or ("POST" if d is not None else "GET"))
    try:
        with u.urlopen(r, timeout=90) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


tok = login("admin@aurea.demo")

# Firm policy: no cap, approval off. Override ONE agent (client_care) with a tiny cost cap +
# require approval; leave drift inheriting firm.
call("/api/admin/foundation", {"policy": {"monthly_cost_cap_usd": 0, "require_approval_everywhere": False}}, tok, "PUT")
call("/api/admin/agents/client_care",
     {"config": {"foundation": {"require_approval_everywhere": True, "min_confidence": 0.95}}}, tok, "PATCH")
call("/api/admin/agents/research_reporting",
     {"config": {"foundation": {"monthly_cost_cap_usd": 0.00001}}}, tok, "PATCH")

agents = call("/api/admin/agents", t=tok)[1]
for a in agents:
    f = (a.get("config") or {}).get("foundation")
    if f:
        print(f"OVERRIDE {a['agent_key']}: {f}")

# Confirm drift still inherits (no foundation override)
drift = next((a for a in agents if a["agent_key"] == "drift_rebalancing"), None)
print("drift overrides:", (drift.get("config") or {}).get("foundation") if drift else "n/a", "(should be none)")

# research_reporting has a near-zero cap → its next narration should be forced to fallback.
hh = call("/api/core/households", t=tok)[1]
chen = next(h for h in hh if "Chen" in h["name"])
call("/api/agents/research_reporting/run", {"subject_type": "household", "subject_id": chen["id"]}, tok)
us = call("/api/admin/usage", t=tok)[1]
print("usage by_agent:", us["by_agent"])

print("\nPER-AGENT VERIFY DONE")
