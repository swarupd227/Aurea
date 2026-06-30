"""Find a drifting mandate, run the drift agent, and confirm the deep payload fields exist."""
import json, urllib.request as u, urllib.error

B = "http://localhost:8010"


def call(p, d=None, t=None, m=None):
    h = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = f"Bearer {t}"
    body = json.dumps(d).encode() if d is not None else None
    r = u.Request(B + p, data=body, headers=h, method=m or ("POST" if d is not None else "GET"))
    try:
        with u.urlopen(r, timeout=60) as x:
            return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


tok = call("/api/auth/login-json", {"email": "admin@aurea.demo", "password": "aurea"})[1]["access_token"]
hh = call("/api/core/households", t=tok)[1]
found = None
for h in hh:
    brain = call(f"/api/core/households/{h['id']}", t=tok)[1]
    for acc in brain.get("accounts", []):
        mid = acc.get("mandate_id")
        if not mid:
            continue
        run = call("/api/agents/drift_rebalancing/run", {"subject_type": "mandate", "subject_id": mid}, tok)[1]
        recs = run.get("recommendations") or []
        if recs:
            found = (h["name"], h["id"], recs[0])
            break
    if found:
        break

if not found:
    print("No drifting mandate found across the book")
    raise SystemExit(1)

name, hid, rec = found
pl, ev = rec["payload"], rec["evidence"]
sells_loss = [o for o in pl.get("order_set", []) if o["side"] == "sell" and o["est_realised_gain"] < 0]
print("CLIENT:", name)
print("TITLE:", rec["title"])
print("payload.mandate:", pl.get("mandate"))
print("payload.cgt_budget:", pl.get("cgt_budget"), "| ev.cgt_budget:", ev.get("cgt_budget"))
print("estimated_realised_gain:", pl.get("estimated_realised_gain"))
print("harvested_losses:", pl.get("harvested_losses"))
print("max_drift/band:", pl.get("summary", {}).get("max_drift"), "/", pl.get("summary", {}).get("drift_band"))
print("n_orders:", len(pl.get("order_set", [])), "| loss-harvest sells:", len(sells_loss))
assert pl.get("mandate", {}).get("type"), "mandate.type missing"
assert pl.get("summary", {}).get("max_drift") is not None
print("CLIENT_URL: /studio/clients/" + hid)
print("DEEP DRIFT PAYLOAD VERIFY PASSED")
