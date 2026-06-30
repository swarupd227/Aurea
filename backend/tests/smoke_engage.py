"""End-to-end smoke for the Advise & Engage + Manage & Optimise verticals.

Run with the stack up:  python backend/tests/smoke_engage.py"""
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


def main():
    _, t = call("/api/auth/login-json", {"email": "sophie.adviser@aurea.demo", "password": "aurea"})
    tok = t["access_token"]
    print("LOGIN: ok")

    # ── Meetings: prep → companion → approve → tasks/goals ──
    _, meetings = call("/api/engage/meetings", token=tok)
    mid = meetings[0]["id"]
    call(f"/api/engage/meetings/{mid}/prep", {}, token=tok)
    _, mtg = call(f"/api/engage/meetings/{mid}", token=tok)
    print(f"PREP: status={mtg['status']} agenda={len(mtg['brief'].get('agenda', []))} "
          f"watch_items={len(mtg['brief'].get('watch_items', []))} goals={len(mtg['brief'].get('goals', []))}")
    assert mtg["brief"].get("agenda")

    _, comp = call(f"/api/engage/meetings/{mid}/companion", {}, token=tok)
    _, mtg2 = call(f"/api/engage/meetings/{mid}", token=tok)
    notes = mtg2["notes"]
    print(f"COMPANION: sentiment={notes['sentiment']} actions={len(notes['action_items'])} "
          f"proposed_goals={len(notes['proposed_goals'])}")
    assert notes["action_items"] and notes["proposed_goals"]
    assert any("150" in str(g["target_amount"]) or g["target_amount"] == 150000 for g in notes["proposed_goals"])

    call(f"/api/studio/recommendations/{comp['recommendation_id']}/decide", {"action": "approve"}, token=tok)
    _, mtg3 = call(f"/api/engage/meetings/{mid}", token=tok)
    _, tasks = call("/api/engage/tasks", token=tok)
    print(f"  approved → meeting status={mtg3['status']} open_tasks={len(tasks)}")
    assert mtg3["status"] == "completed" and len(tasks) >= 1

    # ── Research & Reporting → publish client-ready report ──
    _, hh = call("/api/core/households", token=tok)
    chen = [h for h in hh if "Chen" in h["name"]][0]
    _, run = call("/api/agents/research_reporting/run", {"subject_type": "household", "subject_id": chen["id"]}, token=tok)
    rec = run["recommendations"][0]
    print(f"REPORT: drafted with {len(rec['payload']['sections'])} sections")
    assert len(rec["payload"]["sections"]) >= 4
    call(f"/api/studio/recommendations/{rec['id']}/decide", {"action": "approve"}, token=tok)
    _, reports = call("/api/engage/reports", token=tok)
    ready = [r for r in reports if r["status"] == "client_ready"]
    print(f"  published client-ready reports: {len(ready)}")
    assert ready

    # ── Next-Best-Action firm-wide book scan ──
    _, scan = call("/api/studio/scan", {}, token=tok)
    print(f"BOOK SCAN ({scan['agent']}): {scan['items_surfaced']} items surfaced")
    assert scan["items_surfaced"] >= 1

    # ── Client Care multi-signal on a household ──
    _, care = call("/api/agents/client_care/run", {"subject_type": "household", "subject_id": chen["id"]}, token=tok)
    kinds = sorted({r["payload"].get("signal") for r in care["recommendations"]})
    print(f"CLIENT CARE signals: {kinds}")
    assert "volatility" in kinds

    print("\nADVISE & ENGAGE + MANAGE & OPTIMISE SMOKE PASSED")


if __name__ == "__main__":
    main()
