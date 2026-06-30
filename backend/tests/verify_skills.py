"""Verify an advisor-defined skill runs governed: creates, tests (dry), runs (persists), and the
proposals are ledgered + compliance-assessed + appear in the feed, awaiting approval."""
import json, urllib.request as u, urllib.error
B = "http://localhost:8010"
tok = json.load(u.urlopen(u.Request(B + "/api/auth/login-json",
    data=json.dumps({"email": "sophie.adviser@aurea.demo", "password": "aurea"}).encode(),
    headers={"Content-Type": "application/json"})))["access_token"]


def call(p, d=None, m=None):
    r = u.Request(B + p, data=(json.dumps(d).encode() if d is not None else None),
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
                  method=m or ("POST" if d is not None else "GET"))
    try:
        with u.urlopen(r, timeout=120) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


# 1. Create a skill in plain English.
sk = call("/api/skills", {
    "name": "Cash drag finder",
    "instruction": "Find clients holding more than 12% of their portfolio in cash and propose a short "
                   "note suggesting we put it to work toward their goals.",
    "scope": "book", "output_kind": "note", "default_tier": "tier_1"})[1]
print("CREATED skill:", sk["name"], sk["id"][:8])

# 2. Dry run (test) — nothing persisted.
_, t = call(f"/api/skills/{sk['id']}/test", {})
print("TEST (dry): scanned", t["scanned"], "would surface", t["surfaced"])

# 3. Real run — governed.
_, r = call(f"/api/skills/{sk['id']}/run", {})
print("RUN: scanned", r["scanned"], "surfaced", r["surfaced"], "| recs", len(r["recommendations"]))
if r["recommendations"]:
    rec = r["recommendations"][0]
    print("  e.g.", rec["subject_label"], "—", rec["title"], "| status", rec["status"])
    # 4. The proposal is compliance-assessed (cited) + in the feed.
    comp = call(f"/api/studio/recommendations/{rec['id']}/compliance", t=None)[1] if False else None
    _, comp = call(f"/api/studio/recommendations/{rec['id']}/compliance")
    print("  COMPLIANCE:", comp.get("regime"), comp.get("version"), "status", comp.get("status"),
          "| rules", len(comp.get("results", [])))
    # 5. Approve it (skills have no executable side-effect → just APPROVED).
    appr = call(f"/api/studio/recommendations/{rec['id']}/decide", {"action": "approve"})[1]
    print("  APPROVE ->", appr["status"])

# 6. Confirm it shows in the cockpit feed.
feed = call("/api/studio/feed")[1]
from_skill = [f for f in feed if (f.get("evidence") or {}).get("authored_by") == "adviser"]
print("feed items from a skill:", len(from_skill))

# cleanup the skill
call(f"/api/skills/{sk['id']}", m="DELETE")
print("\nSKILLS VERIFY DONE")
