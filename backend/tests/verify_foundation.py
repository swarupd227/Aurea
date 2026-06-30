"""Verify the Common Foundation: usage telemetry, PII redaction, eval gates, foundation aggregate."""
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

# Generate a real LLM call (narrate → records usage + redacts PII).
hh = call("/api/core/households", t=tok)[1]
chen = next(h for h in hh if "Chen" in h["name"])
brain = call(f"/api/core/households/{chen['id']}", t=tok)[1]
mid = next(a["mandate_id"] for a in brain["accounts"] if a.get("mandate_id") and "Trust" in a["name"])
call("/api/agents/drift_rebalancing/run", {"subject_type": "mandate", "subject_id": mid}, tok)

usage = call("/api/admin/usage", t=tok)[1]
print("USAGE: calls", usage["calls"], "tokens", usage["total_tokens"], "cost $%.4f" % usage["est_cost"],
      "redacted", usage["redacted_entities"], "fallback_rate", usage["fallback_rate"])
print("  by_model:", usage["by_model"])

gates = call("/api/admin/eval-gates", t=tok)[1]
print("EVAL GATES:", gates["passed"], "/", gates["total"], "all_green", gates["all_green"])

f = call("/api/admin/foundation", t=tok)[1]
print("FOUNDATION pillars:", len(f["pillars"]))
for p in f["pillars"]:
    print(f"  [{p['status']:9}] {p['title']}")

# PII toggle round-trip
off = call("/api/admin/security/pii", {"enabled": False}, tok, "PUT")[1]
on = call("/api/admin/security/pii", {"enabled": True}, tok, "PUT")[1]
print("PII toggle:", off, "->", on)

# RBAC: adviser cannot read foundation (AdminDep = admin/compliance)
adv = login("sophie.adviser@aurea.demo")
code, _ = call("/api/admin/foundation", t=adv)
print("adviser /foundation ->", code, "(expect 403)")
print("\nFOUNDATION VERIFY DONE")
