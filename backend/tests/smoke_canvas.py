"""End-to-end smoke for Canvas depth — secure messaging, approved-outreach delivery,
client-ready reports, and the next-gen heir journey.

Run with the stack up:  python backend/tests/smoke_canvas.py"""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method="POST" if data is not None else "GET")
    try:
        with u.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def login(email):
    return call("/api/auth/login-json", {"email": email, "password": "aurea"})[1]["access_token"]


def main():
    adviser = login("sophie.adviser@aurea.demo")
    client = login("client@aurea.demo")     # Wei Chen
    heir = login("heir@aurea.demo")          # Lucas Chen (next-gen)
    print("LOGIN: adviser / client / heir ok")

    # ── Canvas view surfaces messages + next-gen ──
    _, view = call("/api/canvas/me", token=client)
    print(f"CANVAS: wealth={view['total_wealth']:.0f} unread={view['unread_messages']} "
          f"next_gen={[n['name'] for n in view['next_gen']]} reports={len(view['reports'])}")
    assert view["unread_messages"] >= 1          # seeded welcome message
    assert view["next_gen"]                       # Lucas

    # ── Secure messaging round-trip (client → adviser → client) ──
    call("/api/canvas/messages", {"body": "Hi Sophie, can we talk about the gifting plan?"}, token=client)
    chen_id = view["household"]["id"]
    _, inbox = call("/api/engage/messages", token=adviser)
    chen_thread = [t for t in inbox if "Chen" in (t["household_name"] or "")][0]
    print(f"INBOX: Chen unread={chen_thread['unread']}")
    assert chen_thread["unread"] >= 1
    call("/api/canvas/messages", {"body": "Of course — I'll send options tomorrow.", "household_id": chen_id}, token=adviser)
    _, thread = call("/api/canvas/messages", token=client)
    print(f"  thread length={len(thread)} roles={[m['author_role'] for m in thread]}")
    assert any(m["author_role"] == "adviser" and "options" in m["body"] for m in thread)

    # ── Approved client-care outreach is delivered into Canvas ──
    hh = [h for h in call("/api/core/households", token=adviser)[1] if "Chen" in h["name"]][0]
    _, care = call("/api/agents/client_care/run", {"subject_type": "household", "subject_id": hh["id"]}, token=adviser)
    vol = [r for r in care["recommendations"] if r["payload"].get("signal") == "volatility"][0]
    call(f"/api/studio/recommendations/{vol['id']}/decide", {"action": "approve"}, token=adviser)
    _, thread2 = call("/api/canvas/messages", token=client)
    delivered = [m for m in thread2 if m["from_agent"]]
    print(f"OUTREACH: agent-delivered messages={len(delivered)}")
    assert delivered

    # ── Client-ready report surfaces in Canvas ──
    _, run = call("/api/agents/research_reporting/run", {"subject_type": "household", "subject_id": hh["id"]}, token=adviser)
    rec = run["recommendations"][0]
    call(f"/api/studio/recommendations/{rec['id']}/decide", {"action": "approve"}, token=adviser)
    _, view2 = call("/api/canvas/me", token=client)
    print(f"REPORTS in Canvas: {len(view2['reports'])}")
    assert view2["reports"]
    _, rpt = call(f"/api/canvas/reports/{view2['reports'][0]['id']}", token=client)
    assert rpt["sections"]

    # ── Next-gen heir journey progresses ──
    _, j = call("/api/canvas/heir-journey", token=heir)
    print(f"HEIR JOURNEY: status={j['status']} progress={j['progress']} steps={len(j['steps'])}")
    assert j["status"] == "invited" and j["progress"] == 0
    for s in j["steps"]:
        call("/api/canvas/heir-journey/step",
             {"key": s["key"], "captured": {"values_themes": ["climate"]} if s["key"] == "values" else None}, token=heir)
    _, j2 = call("/api/canvas/heir-journey", token=heir)
    print(f"  after completing all: status={j2['status']} progress={j2['progress']}")
    assert j2["status"] == "completed" and j2["progress"] == 1.0

    print("\nCANVAS DEPTH SMOKE PASSED")


if __name__ == "__main__":
    main()
