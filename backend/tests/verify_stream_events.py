"""Trace the drift run-stream SSE events for the Chen Trust mandate."""
import json, time, urllib.request as u, urllib.error

B = "http://localhost:8010"


def call(p, d=None, t=None):
    h = {"Content-Type": "application/json"}
    if t:
        h["Authorization"] = f"Bearer {t}"
    body = json.dumps(d).encode() if d is not None else None
    r = u.Request(B + p, data=body, headers=h, method="POST" if d is not None else "GET")
    with u.urlopen(r, timeout=60) as x:
        return json.load(x)


tok = call("/api/auth/login-json", {"email": "sophie.adviser@aurea.demo", "password": "aurea"})["access_token"]
hh = call("/api/core/households", t=tok)
chen = next(h for h in hh if "Chen" in h["name"])
brain = call(f"/api/core/households/{chen['id']}", t=tok)
mid = None
for acc in brain["accounts"]:
    if "Trust" in acc["name"] and acc.get("mandate_id"):
        mid = acc["mandate_id"]; break
mid = mid or next(a["mandate_id"] for a in brain["accounts"] if a.get("mandate_id"))
print("mandate:", mid)

qs = f"agent_key=drift_rebalancing&subject_type=mandate&subject_id={mid}"
req = u.Request(f"{B}/api/atlas/run-stream?{qs}", headers={"Authorization": f"Bearer {tok}"})
t0 = time.time()
phases = []
done_payload = None
with u.urlopen(req, timeout=120) as resp:
    buf = ""
    for raw in resp:
        buf += raw.decode("utf-8", "ignore")
        while "\n\n" in buf:
            part, buf = buf.split("\n\n", 1)
            line = next((l for l in part.split("\n") if l.startswith("data:")), None)
            if not line:
                continue
            ev = json.loads(line[5:].strip())
            ph = ev.get("phase")
            if ph != "rationale" or ev.get("status"):
                phases.append(f"{round(time.time()-t0,1)}s {ph}:{ev.get('status','')}")
            if ph == "done":
                done_payload = ev
print("EVENTS:")
for x in phases:
    print("  ", x)
print("DONE payload:", json.dumps(done_payload) if done_payload else "NONE")
