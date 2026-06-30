"""Verify this round: RBAC (client blocked), richer seed, real performance, populated cockpit."""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method="POST" if data is not None else "GET")
    try:
        with u.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


def main():
    admin = call("/api/auth/login-json", {"email": "admin@aurea.demo", "password": "aurea"})[1]["access_token"]
    client = call("/api/auth/login-json", {"email": "client@aurea.demo", "password": "aurea"})[1]["access_token"]

    # ── RBAC: client must be blocked from staff data, allowed on canvas ──
    print("RBAC (Canvas client token):")
    blocked_ok = True
    for path in ["/api/core/households", "/api/analytics/overview", "/api/atlas/activity",
                 "/api/atlas/workforce", "/api/provenance/ledger", "/api/studio/feed"]:
        st, _ = call(path, token=client)
        ok = st == 403
        blocked_ok &= ok
        print(f"   [{'OK ' if ok else 'LEAK'}] {st} {path}")
    st_canvas, _ = call("/api/canvas/me", token=client)
    print(f"   [{'OK ' if st_canvas == 200 else 'FAIL'}] {st_canvas} /api/canvas/me (client allowed)")
    assert blocked_ok and st_canvas == 200

    # ── Richer seed ──
    hh = call("/api/core/households", token=admin)[1]
    print(f"\nHOUSEHOLDS: {len(hh)} (segments: {sorted(set(h['segment'] for h in hh))})")
    assert len(hh) >= 10

    # ── Real performance & attribution ──
    ov = call("/api/analytics/overview", token=admin)[1]
    perf = ov["portfolio"]["performance"]
    print(f"PERFORMANCE: return={perf['total_return']:.1%} period='{perf['period']}' "
          f"start={perf['start_value']:.0f} end={perf['market_value']:.0f}")
    assert perf["period"] != "current holdings"  # real history present
    assert perf["start_value"] > 0

    # ── Cockpit populated by the seed pre-run ──
    feed = call("/api/studio/feed", token=admin)[1]
    acts = call("/api/atlas/activity", token=admin)[1]
    print(f"COCKPIT: {len(feed)} open recommendations · {len(acts)} activity events")
    assert len(feed) >= 1

    print("\nROUND-2 VERIFY PASSED")


if __name__ == "__main__":
    main()
