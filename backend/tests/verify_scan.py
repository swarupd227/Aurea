"""Consume the book scan SSE and print the diversity of the surfaced items."""
import json, urllib.request as u

B = "http://localhost:8010"
tok = json.load(u.urlopen(u.Request(B + "/api/auth/login-json",
      data=json.dumps({"email": "admin@aurea.demo", "password": "aurea"}).encode(),
      headers={"Content-Type": "application/json"})))["access_token"]

req = u.Request(B + "/api/atlas/scan-stream?agent=next_best_action",
                headers={"Authorization": f"Bearer {tok}"})
progress = 0
done = None
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
            if ev["phase"] == "progress":
                progress += 1
            elif ev["phase"] == "done":
                done = ev

print("progress events:", progress)
print("households_scanned:", done["households_scanned"])
print("signals detected:", done["detected"])
print("items surfaced:", done["items_surfaced"])
print("by_signal:", json.dumps(done["by_signal"]))
print("distinct signal types surfaced:", len(done["by_signal"]))
for it in done["items"][:8]:
    print(f"  - [{it['signal']}] {it['title']} ({it['subject_label']})")
