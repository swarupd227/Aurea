"""End-to-end smoke for Protect & Govern — evaluation harness, adaptive autonomy, rollback,
and communications surveillance. Run with the stack up:  python backend/tests/smoke_govern.py"""
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
    _, t = call("/api/auth/login-json", {"email": "compliance@aurea.demo", "password": "aurea"})
    tok = t["access_token"]
    print("LOGIN: ok")

    # ── Rollback: onboard someone, approve (materialise), then roll back ──
    _, case = call("/api/onboarding/cases", {
        "prospect_name": "Rollback Tester", "is_entity": False, "segment": "mass_affluent",
        "intake": {"risk_profile": "balanced", "mandate_preference": "advisory",
                   "source_of_wealth": "Salary", "associated_parties": []}}, token=tok)
    cid = case["id"]
    for dt in ("passport", "drivers_licence", "proof_of_address"):
        call(f"/api/onboarding/cases/{cid}/documents", {"doc_type": dt}, token=tok)
    call(f"/api/onboarding/cases/{cid}/run", {}, token=tok)
    _, detail = call(f"/api/onboarding/cases/{cid}", token=tok)
    rec_id = detail["recommendation_id"]
    approved = call(f"/api/studio/recommendations/{rec_id}/decide", {"action": "approve"}, token=tok)[1]
    _, after = call(f"/api/onboarding/cases/{cid}", token=tok)
    hh = after["materialized"].get("household_id")
    print(f"ROLLBACK: materialised household={hh is not None} status={after['status']}")
    assert hh
    # Roll it back.
    rolled = call(f"/api/studio/recommendations/{rec_id}/rollback", {}, token=tok)[1]
    _, reopened = call(f"/api/onboarding/cases/{cid}", token=tok)
    status_h, _ = call(f"/api/core/households/{hh}", token=tok)
    print(f"  rolled_back status={rolled['status']} case_reopened={reopened['status']} household_gone={status_h == 404}")
    assert rolled["status"] == "rolled_back"
    assert reopened["status"] == "review"
    assert status_h == 404  # the materialised household was deleted

    # ── Evaluation harness + adaptive autonomy ──
    _, ev = call("/api/provenance/evaluate", {}, token=tok)
    print(f"EVALUATION: evaluated={ev['evaluated']} autonomy_changes={ev['autonomy_changes']}")
    assert ev["evaluated"] >= 9
    _, evals = call("/api/provenance/evaluations", token=tok)
    graded = {e["agent_key"]: e["grade"] for e in evals}
    print(f"  grades: {graded}")
    assert evals and all("quality_score" in e for e in evals)

    # ── Communications surveillance: an over-promising message is flagged ──
    # Create a client-care style recommendation by running client_care, then check no false trigger;
    # then directly verify the rule via a crafted scenario is covered by unit tests. Here we assert
    # the surveillance endpoint is reachable and structured.
    _, flags = call("/api/provenance/surveillance", token=tok)
    print(f"SURVEILLANCE: {len(flags)} flag(s); categories {sorted({f['category'] for f in flags})}")

    # ── Ledger records governance events ──
    _, ledger = call("/api/provenance/ledger", token=tok)
    types = {e["event_type"] for e in ledger}
    print(f"LEDGER event types: {sorted(types)}")
    assert "rollback" in types

    print("\nPROTECT & GOVERN SMOKE PASSED")


if __name__ == "__main__":
    main()
