"""End-to-end smoke for the Acquire & Onboard vertical against a running stack.

Run with the stack up:  python backend/tests/smoke_onboarding.py"""
import json, os, urllib.request as u, urllib.error

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None, method=None):
    h = {"Content-Type": "application/json"}
    if token: h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with u.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    _, t = call("/api/auth/login-json", {"email": "compliance@aurea.demo", "password": "aurea"})
    tok = t["access_token"]
    print("LOGIN (compliance): ok")

    # 1) New individual prospect → docs → run → approve → materialise.
    _, case = call("/api/onboarding/cases", {
        "prospect_name": "Test Prospect", "is_entity": False, "segment": "private_wealth",
        "intake": {"risk_profile": "growth", "mandate_preference": "advisory",
                   "source_of_wealth": "Salary & equity", "objectives": ["retirement"],
                   "time_horizon_years": 20, "associated_parties": []},
    }, token=tok)
    cid = case["id"]
    for dt in ("passport", "drivers_licence", "proof_of_address"):
        call(f"/api/onboarding/cases/{cid}/documents", {"doc_type": dt}, token=tok)
    _, run = call(f"/api/onboarding/cases/{cid}/run", {}, token=tok)
    _, detail = call(f"/api/onboarding/cases/{cid}", token=tok)
    print(f"INDIVIDUAL: status={detail['status']} aml={detail['screening']['status']} "
          f"exceptions={len(detail['exceptions'])} docs={len(detail['documents'])}")
    assert detail["screening"]["status"] == "clear"
    assert detail["proposal"]["mandate"]["mandate_type"] == "advisory"
    rec_id = detail["recommendation_id"]
    call(f"/api/studio/recommendations/{rec_id}/decide", {"action": "approve"}, token=tok)
    _, after = call(f"/api/onboarding/cases/{cid}", token=tok)
    hh = after["materialized"].get("household_id")
    print(f"  approved → status={after['status']} household={hh is not None}")
    assert after["status"] == "approved" and hh
    _, brain = call(f"/api/core/households/{hh}", token=tok)
    assert brain["household"]["name"] == "Test Prospect"
    print(f"  materialised client has {len(brain['mandates'])} mandate, {len(brain['accounts'])} account")

    # 2) Seeded trust with a PEP trustee → AML review + surveillance flag.
    _, cases = call("/api/onboarding/cases", token=tok)
    sok = [c for c in cases if "Sokolov" in c["prospect_name"]][0]
    call(f"/api/onboarding/cases/{sok['id']}/run", {}, token=tok)
    _, sokd = call(f"/api/onboarding/cases/{sok['id']}", token=tok)
    print(f"TRUST (PEP): aml={sokd['screening']['status']} exceptions={len(sokd['exceptions'])}")
    assert sokd["screening"]["status"] == "review"
    _, flags = call("/api/provenance/surveillance", token=tok)
    aml_flags = [f for f in flags if f["category"] == "aml"]
    print(f"  surveillance AML flags: {len(aml_flags)}")
    assert aml_flags

    # 3) Book integration → reconcile → commit golden records.
    _, batches = call("/api/onboarding/book-batches", token=tok)
    bid = batches[0]["id"]
    call(f"/api/onboarding/book-batches/{bid}/run", {}, token=tok)
    _, batch = call(f"/api/onboarding/book-batches/{bid}", token=tok)
    print(f"BOOK: clients={batch['stats']['clients']} merges={batch['stats']['merges']} "
          f"new={batch['stats']['new_clients']} conflicts={batch['stats']['conflicts']} "
          f"unmapped={batch['stats']['unmapped_securities']}")
    assert batch["stats"]["conflicts"] == 1
    assert batch["stats"]["unmapped_securities"] == 2
    call(f"/api/studio/recommendations/{batch['recommendation_id']}/decide", {"action": "approve"}, token=tok)
    _, committed = call(f"/api/onboarding/book-batches/{bid}", token=tok)
    print(f"  committed: status={committed['status']} {committed['committed']}")
    assert committed["status"] == "committed"
    assert committed["committed"]["holdings_written"] >= 4

    print("\nACQUIRE & ONBOARD SMOKE PASSED")


if __name__ == "__main__":
    main()
