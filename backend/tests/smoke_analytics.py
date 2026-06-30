"""End-to-end smoke for the analytics layer against a running stack.

Run with the stack up:  python backend/tests/smoke_analytics.py"""
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
        return e.code, json.loads(e.read() or b"{}")


def main():
    _, t = call("/api/auth/login-json", {"email": "branch@aurea.demo", "password": "aurea"})
    tok = t["access_token"]
    print("LOGIN (branch leader): ok")

    _, ov = call("/api/analytics/overview", token=tok)
    h = ov["headline"]
    print(f"OVERVIEW: AUM={h['firm_aum']:.0f} clients={h['clients']} wallet_share={h['firm_wallet_share']:.0%} "
          f"return={h['total_return']:.0%} margin={h['firm_margin']:.0%} DQ={h['data_quality_score']:.0%}")
    assert h["firm_aum"] > 0 and h["clients"] >= 3
    assert all(L in ov for L in ["client_intelligence", "portfolio", "advice", "practice", "risk_data"])
    assert len(ov["maturity"]) == 5

    # Each layer endpoint resolves.
    for layer in ["client-intelligence", "portfolio", "advice", "practice", "risk-data"]:
        st, body = call(f"/api/analytics/{layer}", token=tok)
        print(f"  [{'OK ' if st == 200 else 'FAIL'}] /api/analytics/{layer}  keys={list(body)[:4]}")
        assert st == 200

    # Spot-check meaningful numbers.
    ci, pf, pr, rd = ov["client_intelligence"], ov["portfolio"], ov["practice"], ov["risk_data"]
    print(f"  wallet-share households: {[(w['household'], w['wallet_share']) for w in ci['wallet_share']['by_household']]}")
    print(f"  attribution top: {pf['performance']['attribution'][0]}")
    print(f"  fee by segment: { {s: v['effective_rate'] for s, v in pr['fee_margin']['by_segment'].items()} }")
    print(f"  data-quality: {rd['data_quality']['score']} ledger_valid={rd['audit']['chain_valid']}")
    assert ci["wallet_share"]["consolidation_opportunity"] > 0
    assert pr["fee_margin"]["fee_revenue"] > 0
    assert rd["data_quality"]["score"] > 0

    print("\nANALYTICS SMOKE PASSED")


if __name__ == "__main__":
    main()
