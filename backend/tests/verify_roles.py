"""Verify the role switcher backend: every persona logs in, the demo-personas endpoint,
security headers, and the branch-leader capacity endpoint."""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method="POST" if data is not None else "GET")
    try:
        with u.urlopen(req) as r:
            return r.status, r.headers, json.load(r)  # r.headers is case-insensitive
    except urllib.error.HTTPError as e:
        return e.code, e.headers, json.loads(e.read() or b"{}")


def main():
    _, _, personas = call("/api/auth/demo-personas")
    print("PERSONAS:", len(personas))
    ok = 0
    for p in personas:
        st, _, body = call("/api/auth/login-json", {"email": p["email"], "password": "aurea"})
        role = body.get("user", {}).get("role")
        if st == 200:
            ok += 1
        print(f"  [{'OK ' if st == 200 else 'FAIL'}] {p['title']:18} {p['email']:28} role={role} -> {p['default_path']}")
    print(f"{ok}/{len(personas)} persona logins OK")
    assert ok == len(personas) and len(personas) == 10

    st, hdrs, _ = call("/health")
    headers = {k: hdrs.get(k) for k in ["X-Content-Type-Options", "X-Frame-Options", "Referrer-Policy"]}
    print("SECURITY HEADERS:", headers)
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"

    _, _, t = call("/api/auth/login-json", {"email": "branch@aurea.demo", "password": "aurea"})
    tok = t["access_token"]
    st, _, cap = call("/api/studio/capacity", token=tok)
    print(f"CAPACITY (branch leader): {st} hours_reclaimed={cap.get('estimated_hours_reclaimed')}")
    assert st == 200

    print("\nROLE SWITCHER & HARDENING VERIFIED")


if __name__ == "__main__":
    main()
