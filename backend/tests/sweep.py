"""Quick endpoint sweep — hits every API surface the frontend depends on and reports status."""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")

def call(path, data=None, token=None, method=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with u.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def main():
    _, raw = call("/api/auth/login-json", {"email": "admin@aurea.demo", "password": "aurea"})
    tok = json.loads(raw)["access_token"]
    hh = json.loads(call("/api/core/households", token=tok)[1])
    hid = hh[0]["id"]

    checks = [
        ("GET /health", "/health"),
        ("studio/feed", "/api/studio/feed"),
        ("studio/capacity", "/api/studio/capacity"),
        ("agents/catalogue", "/api/agents/catalogue"),
        ("agents/runs", "/api/agents/runs"),
        ("core/households", "/api/core/households"),
        (f"core/household detail", f"/api/core/households/{hid}"),
        (f"core/planning", f"/api/core/households/{hid}/planning"),
        ("provenance/ledger", "/api/provenance/ledger"),
        ("provenance/verify", "/api/provenance/ledger/verify"),
        ("provenance/surveillance", "/api/provenance/surveillance"),
        ("conduit/connectors", "/api/conduit/connectors"),
        ("conduit/registry", "/api/conduit/registry"),
        ("admin/firm", "/api/admin/firm"),
        ("admin/agents", "/api/admin/agents"),
        ("admin/policies", "/api/admin/policies"),
        ("admin/research", "/api/admin/research"),
        (f"canvas/me (staff preview)", f"/api/canvas/me?household_id={hid}"),
        ("onboarding/cases", "/api/onboarding/cases"),
        ("onboarding/document-templates", "/api/onboarding/document-templates"),
        ("onboarding/book-batches", "/api/onboarding/book-batches"),
        ("engage/meetings", "/api/engage/meetings"),
        ("engage/tasks", "/api/engage/tasks"),
        ("engage/reports", "/api/engage/reports"),
        ("provenance/evaluations", "/api/provenance/evaluations"),
        ("provenance/autonomy-changes", "/api/provenance/autonomy-changes"),
        (f"canvas/messages (preview)", f"/api/canvas/messages?household_id={hid}"),
        ("engage/messages (inbox)", "/api/engage/messages"),
        ("canvas/heir-journey", "/api/canvas/heir-journey"),
        ("analytics/overview", "/api/analytics/overview"),
        ("analytics/practice", "/api/analytics/practice"),
        ("analytics/risk-data", "/api/analytics/risk-data"),
        ("atlas/activity", "/api/atlas/activity"),
        ("atlas/workforce", "/api/atlas/workforce"),
    ]
    ok = 0
    for name, path in checks:
        status, _ = call(path, token=tok)
        flag = "OK " if status == 200 else "FAIL"
        if status == 200: ok += 1
        print(f"  [{flag}] {status}  {name}")
    print(f"\n{ok}/{len(checks)} endpoints OK")

if __name__ == "__main__":
    main()
