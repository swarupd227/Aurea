"""Verify skill ownership, sharing, visibility and clone across two advisers."""
import json, urllib.request as u, urllib.error
B = "http://localhost:8010"


def login(e):
    return json.load(u.urlopen(u.Request(B + "/api/auth/login-json",
        data=json.dumps({"email": e, "password": "aurea"}).encode(),
        headers={"Content-Type": "application/json"})))["access_token"]


def call(p, tok, d=None, m=None):
    r = u.Request(B + p, data=(json.dumps(d).encode() if d is not None else None),
                  headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"},
                  method=m or ("POST" if d is not None else "GET"))
    try:
        with u.urlopen(r, timeout=60) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


sophie = login("sophie.adviser@aurea.demo")
para = login("paraplanner@aurea.demo")

# Firm library (public examples) visible to Sophie
lst = call("/api/skills", sophie)[1]
pub = [s for s in lst if s["visibility"] == "public"]
print("Sophie sees public firm-library skills:", len(pub), "| owners:", {s["owner_name"] for s in pub})

# Sophie creates a PRIVATE skill
mine = call("/api/skills", sophie, {"name": "Sophie private skill", "instruction": "Flag clients with goals off track.", "visibility": "private"})[1]
print("created (mine):", mine["mine"], "| visibility:", mine["visibility"])

# Paraplanner CANNOT see Sophie's private skill, and CANNOT edit it
para_list = call("/api/skills", para)[1]
print("Para sees Sophie's private skill?", any(s["id"] == mine["id"] for s in para_list), "(expect False)")
code, _ = call(f"/api/skills/{mine['id']}", para, {"name": "hijack"}, "PATCH")
print("Para edit Sophie's skill ->", code, "(expect 403)")

# Sophie shares it with the paraplanner
cols = call("/api/skills/colleagues", sophie)[1]
para_id = next(c["id"] for c in cols if "paraplanner" in (c["name"] + c["role"]).lower() or c["role"] == "paraplanner")
call(f"/api/skills/{mine['id']}", sophie, {"visibility": "shared", "shared_with": [para_id]}, "PATCH")
para_list2 = call("/api/skills", para)[1]
shared = next((s for s in para_list2 if s["id"] == mine["id"]), None)
print("After share, Para sees it?", shared is not None, "| can_edit:", shared["can_edit"] if shared else None, "(expect True / False)")

# Paraplanner clones a public example into their own workspace
ex = pub[0]
clone = call(f"/api/skills/{ex['id']}/clone", para, {})[1]
print("Para cloned public example:", clone["name"], "| mine:", clone["mine"], "| visibility:", clone["visibility"])

# cleanup
call(f"/api/skills/{mine['id']}", sophie, m="DELETE")
call(f"/api/skills/{clone['id']}", para, m="DELETE")
print("\nSKILL WORKSPACE VERIFY DONE")
