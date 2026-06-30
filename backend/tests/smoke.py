"""End-to-end smoke test against a running stack (default http://localhost:8010).

Exercises the lighthouse flow: login → client brain → trigger drift agent → review the
recommendation → approve at the HITL gate → verify the hash-chained ledger. Run with the
stack up:  python -m tests.smoke   (or  python backend/tests/smoke.py)."""
import json
import os
import urllib.request as u

B = os.environ.get("AUREA_BASE", "http://localhost:8010")


def call(path, data=None, token=None, method=None):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = u.Request(B + path, data=body, headers=h, method=method or ("POST" if data is not None else "GET"))
    with u.urlopen(req) as r:
        return json.load(r)


def main():
    print("HEALTH:", call("/health")["status"])
    tok = call("/api/auth/login-json", {"email": "sophie.adviser@aurea.demo", "password": "aurea"})["access_token"]
    print("LOGIN: ok")

    hh = call("/api/core/households", token=tok)
    print("HOUSEHOLDS:", [(h["name"], f"${h['total_value']:,.0f}") for h in hh])

    chen = [h for h in hh if "Chen" in h["name"]][0]
    detail = call(f"/api/core/households/{chen['id']}", token=tok)
    print("CHEN by_class:", {k: round(v) for k, v in detail["totals"]["by_asset_class"].items()})
    mandate = [m for m in detail["mandates"] if m["mandate_type"] == "discretionary"][0]

    run = call("/api/agents/drift_rebalancing/run",
               {"subject_type": "mandate", "subject_id": mandate["id"]}, token=tok)
    print("\nDRIFT RUN:", run["status"], "| tier", run["tier"], "| recs", len(run["recommendations"]))
    r = run["recommendations"][0]
    print("  TITLE:", r["title"])
    print("  SUMMARY:", r["summary"])
    print("  CONFIDENCE:", r["confidence"], "| CITES:", [c["title"] for c in r["citations"]])
    p = r["payload"]
    print("  realised gain $%.0f | harvested $%.0f | turnover $%.0f"
          % (p["estimated_realised_gain"], p["harvested_losses"], p["summary"]["turnover"]))
    for o in p["order_set"][:8]:
        print(f"    {o['side'].upper():4} {o['quantity']:9.1f} {o['symbol']:7} "
              f"~${o['est_value']:>11,.0f}  gain ${o['est_realised_gain']:>9,.0f}  [{o['reason']}]")
    print("  RATIONALE:", (r["rationale"][:240] + "…") if r["rationale"] else "(none)")

    # Approve at the HITL gate.
    decided = call(f"/api/studio/recommendations/{r['id']}/decide",
                   {"action": "approve", "note": "Reviewed; aligns with house view."}, token=tok)
    print("\nDECISION:", decided["status"], "| exec:", (decided["payload"].get("execution_result") or {}).get("note"))

    # Ledger + chain verification.
    ledger = call("/api/provenance/ledger", token=tok)
    verify = call("/api/provenance/ledger/verify", token=tok)
    print("LEDGER entries:", len(ledger), "| chain valid:", verify["valid"], "| count:", verify["count"])
    print("  recent events:", [e["event_type"] for e in ledger[:5]])

    # Surveillance.
    flags = call("/api/provenance/surveillance", token=tok)
    print("SURVEILLANCE flags:", len(flags), [(f["severity"], f["category"]) for f in flags[:4]])

    # Ask-your-book.
    ans = call("/api/studio/ask", {"question": "which clients are overweight equities with a loss to harvest?"}, token=tok)
    print("\nASK-YOUR-BOOK:", ans["answer"][:200], "| fallback:", ans["is_fallback"])

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()
