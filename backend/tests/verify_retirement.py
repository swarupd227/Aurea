"""Verify the retirement endpoints: staff, what-if overrides, and the client Canvas view."""
import json, urllib.request as u

B = "http://localhost:8010"


def login(e):
    return json.load(u.urlopen(u.Request(B + "/api/auth/login-json",
        data=json.dumps({"email": e, "password": "aurea"}).encode(),
        headers={"Content-Type": "application/json"})))["access_token"]


def g(p, t):
    return json.load(u.urlopen(u.Request(B + p, headers={"Authorization": f"Bearer {t}"}), timeout=60))


adm = login("admin@aurea.demo")
hh = g("/api/core/households", adm)
chen = next(h for h in hh if "Chen" in h["name"])
p = g(f"/api/core/households/{chen['id']}/retirement", adm)
print("STAFF retirement:", chen["name"])
print("  age", p["current_age"], "retire", p["retirement_age"], "to", p["longevity_age"], "income", p["income_target"])
print("  success", p["success_probability"], "on_track", p["on_track"], "sustainable", p["sustainable_income"])
print("  depletion_age", p["median_depletion_age"])
print("  seq", p["sequence_risk"])
print("  levers", [(l["label"], l["success"]) for l in p["levers"]])
print("  balance pts", len(p["balance_by_age"]))

p2 = g(f"/api/core/households/{chen['id']}/retirement?retirement_age=70&annual_income=80000", adm)
print("  WHAT-IF retire=70 income=80k -> success", p2["success_probability"], "retire", p2["retirement_age"])
assert p2["retirement_age"] == 70 and p2["income_target"] == 80000

cl = login("client@aurea.demo")
pc = g("/api/canvas/retirement", cl)
print("CLIENT canvas retirement:", pc["household"]["name"], "success", pc["success_probability"])
assert pc.get("balance_by_age")

print("\nRETIREMENT VERIFY PASSED")
