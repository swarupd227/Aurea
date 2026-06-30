"""Verify the revise loop + exception scenarios."""
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
        with u.urlopen(r, timeout=60) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


tok = login("admin@aurea.demo")

# --- Exceptions ---
cat = call("/api/agents/catalogue", t=tok)[1]
bi = next((a for a in cat if a["agent_key"] == "book_integration"), None)
print("book_integration paused:", bi.get("paused"), "| reason:", (bi.get("paused_reason") or "")[:70])

# surveillance flags via provenance
code, flags = call("/api/provenance/surveillance", t=tok)
if code != 200:
    code, flags = call("/api/provenance/flags", t=tok)
print("surveillance endpoint:", code, "| flags:", len(flags) if isinstance(flags, list) else flags)

# feed should now contain the flagged exception items
feed = call("/api/studio/feed", t=tok)[1]
titles = [r["title"] for r in feed]
print("over-promising draft in feed:", any("commentary draft" in t.lower() for t in titles))
print("low-confidence item in feed:", any("possible concentration" in t.lower() for t in titles))

# --- Revise loop ---
hh = call("/api/core/households", t=tok)[1]
chen = next(h for h in hh if "Chen" in h["name"])
brain = call(f"/api/core/households/{chen['id']}", t=tok)[1]
mid = next(a["mandate_id"] for a in brain["accounts"] if a.get("mandate_id") and "Trust" in a["name"])
run = call("/api/agents/drift_rebalancing/run", {"subject_type": "mandate", "subject_id": mid}, tok)[1]
rid = run["recommendations"][0]["id"]
print("\noriginal rec:", rid[:8], "realised", run["recommendations"][0]["payload"].get("estimated_realised_gain"))
code, res = call(f"/api/studio/recommendations/{rid}/revise",
                 {"note": "Keep realised gains under $8,000 and exclude AAPL.", "cgt_budget": 8000, "exclude": ["AAPL"]}, tok)
print("revise status:", code, "| revised:", res.get("revised"))
nr = res.get("new_recommendation")
if nr:
    print("  new rec realised gain:", nr["payload"].get("estimated_realised_gain"), "(should be <= 8000)")
    print("  revision_note on new rec:", nr["payload"].get("revision_note"))
    print("  AAPL still sold?:", any(o["symbol"] == "AAPL" for o in nr["payload"].get("order_set", [])))
# original should now be dismissed/superseded
orig = call(f"/api/studio/recommendations/{rid}", t=tok)[1]
print("  original status:", orig["status"], "| note:", (orig.get("decision_note") or "")[:50])
print("\nVERIFY DONE")
