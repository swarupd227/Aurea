"""The simulated wealth platform's book: invented clients holding real listed instruments.

Households, people and a trust; the mandates and accounts they are managed under; holdings; a year of transactions.
No tax lots, because the platform this stands for may not keep them, so Astra's tax and rebalancing read "not
assessed" for this firm. Fixed instants, so a second read gives exactly the first. Quantities and costs are invented;
prices are not here at all: the platform values a holding at Astra's own real feed, as the firm's valuation does.

The people are invented. Any likeness to a real person is accidental.
"""
from __future__ import annotations

PLATFORM = {"name": "WealthSmart (simulated)", "version": "sim-1"}
CUSTODIAN = "WealthSmart (simulated)"
STAMP = "2026-11-14T22:00:00+00:00"   # when the platform last changed the fixture's records

# The instruments it holds: symbol, name, asset class. Real listed instruments, priced by the firm's real feed.
INSTRUMENTS = {
    "VTI": ("Vanguard Total Stock Market ETF", "equity"),
    "VXUS": ("Vanguard Total International Stock ETF", "equity"),
    "BND": ("Vanguard Total Bond Market ETF", "fixed_income"),
    "AAPL": ("Apple Inc.", "equity"),
    "MSFT": ("Microsoft Corporation", "equity"),
    "JNJ": ("Johnson & Johnson", "equity"),
    "GLD": ("SPDR Gold Shares", "commodity"),
}

# The models the mandates name. A model portfolio is the portfolio team's record in Astra, so the firm keeps these.
MODELS = {
    "Core Balanced": ("Balanced: 55/35/10.", [("equity", 0.40, "VTI"), ("equity", 0.15, "VXUS"),
                                                ("fixed_income", 0.35, "BND"), ("commodity", 0.10, "GLD")]),
    "Core Growth": ("Growth: 75/20/5.", [("equity", 0.55, "VTI"), ("equity", 0.20, "VXUS"),
                                          ("fixed_income", 0.20, "BND"), ("commodity", 0.05, "GLD")]),
}


def book() -> dict:
    """The platform's records, in its own ids, written to the Foundation contract v1 (the profile's record shape)."""
    households = [
        {"entity": "household", "external_id": "WS-HH-1001", "as_of": STAMP, "name": "The Harrington Family",
         "segment": "private_wealth", "values": {"jurisdiction": "US"}, "review_cadence_months": 12},
        {"entity": "household", "external_id": "WS-HH-1002", "as_of": STAMP, "name": "The Okonkwo Household",
         "segment": "mass_affluent", "values": {"jurisdiction": "US"}, "review_cadence_months": 12},
        {"entity": "household", "external_id": "WS-HH-1003", "as_of": STAMP, "name": "The Whitlock Family",
         "segment": "private_wealth", "values": {"jurisdiction": "US"}, "review_cadence_months": 6},
    ]
    people = [
        {"entity": "person", "external_id": "WS-P-2001", "as_of": STAMP, "household_ref": "WS-HH-1001",
         "full_name": "Claire Harrington", "preferred_name": "Claire", "email": "claire.harrington@example.com",
         "date_of_birth": "1968-03-14", "kyc": {"status": "verified", "simulated": True}},
        {"entity": "person", "external_id": "WS-P-2002", "as_of": STAMP, "household_ref": "WS-HH-1001",
         "full_name": "Michael Harrington", "preferred_name": "Michael", "email": "michael.harrington@example.com",
         "date_of_birth": "1966-08-02", "kyc": {"status": "verified", "simulated": True}},
        {"entity": "person", "external_id": "WS-P-2003", "as_of": STAMP, "household_ref": "WS-HH-1002",
         "full_name": "Adaeze Okonkwo", "preferred_name": "Adaeze", "email": "adaeze.okonkwo@example.com",
         "date_of_birth": "1979-11-21", "kyc": {"status": "verified", "simulated": True}},
        {"entity": "person", "external_id": "WS-P-2004", "as_of": STAMP, "household_ref": "WS-HH-1003",
         "full_name": "Eleanor Whitlock", "preferred_name": "Eleanor", "email": "eleanor.whitlock@example.com",
         "date_of_birth": "1951-05-30", "kyc": {"status": "verified", "simulated": True}},
    ]
    entities = [
        {"entity": "legal_entity", "external_id": "WS-E-3001", "as_of": STAMP, "household_ref": "WS-HH-1003",
         "name": "Whitlock Family Trust", "entity_type": "trust", "jurisdiction": "US",
         "governance": {"trustees": ["Eleanor Whitlock"], "simulated": True}},
    ]
    mandates = [
        {"entity": "mandate", "external_id": "WS-M-4001", "as_of": STAMP, "person_ref": "WS-P-2001",
         "name": "Harrington Joint - Balanced", "mandate_type": "advisory", "model_portfolio": "Core Balanced",
         "suitability": {"risk_profile": "balanced", "capacity_for_loss": "medium"}, "constraints": {"cgt_budget": 10000}},
        {"entity": "mandate", "external_id": "WS-M-4002", "as_of": STAMP, "person_ref": "WS-P-2001",
         "name": "Claire Harrington IRA - Growth", "mandate_type": "advisory", "model_portfolio": "Core Growth",
         "suitability": {"risk_profile": "growth", "capacity_for_loss": "high"}, "constraints": {}},
        {"entity": "mandate", "external_id": "WS-M-4003", "as_of": STAMP, "person_ref": "WS-P-2003",
         "name": "Adaeze Okonkwo - Growth", "mandate_type": "discretionary", "model_portfolio": "Core Growth",
         "suitability": {"risk_profile": "growth", "capacity_for_loss": "high"}, "constraints": {"cgt_budget": 5000}},
        {"entity": "mandate", "external_id": "WS-M-4004", "as_of": STAMP, "entity_ref": "WS-E-3001",
         "name": "Whitlock Family Trust - Balanced", "mandate_type": "discretionary", "model_portfolio": "Core Balanced",
         "suitability": {"risk_profile": "balanced", "capacity_for_loss": "medium"}, "constraints": {"cgt_budget": 25000}},
    ]
    accounts = [
        {"entity": "account", "external_id": "WS-A-5001", "as_of": STAMP, "mandate_ref": "WS-M-4001",
         "name": "Harrington joint brokerage", "account_number": "WS-****-5001", "custodian": CUSTODIAN,
         "currency": "USD", "cash_balance": 42500.0, "registration_type": "joint"},
        {"entity": "account", "external_id": "WS-A-5002", "as_of": STAMP, "mandate_ref": "WS-M-4002",
         "name": "Claire Harrington IRA", "account_number": "WS-****-5002", "custodian": CUSTODIAN,
         "currency": "USD", "cash_balance": 8200.0, "registration_type": "ira"},
        {"entity": "account", "external_id": "WS-A-5003", "as_of": STAMP, "mandate_ref": "WS-M-4003",
         "name": "Adaeze Okonkwo individual", "account_number": "WS-****-5003", "custodian": CUSTODIAN,
         "currency": "USD", "cash_balance": 15750.0, "registration_type": "individual"},
        {"entity": "account", "external_id": "WS-A-5004", "as_of": STAMP, "mandate_ref": "WS-M-4004",
         "name": "Whitlock Family Trust", "account_number": "WS-****-5004", "custodian": CUSTODIAN,
         "currency": "USD", "cash_balance": 96000.0, "registration_type": "trust"},
    ]
    # (account, symbol, quantity, cost basis). No lots: the platform keeps a position's total cost only.
    positions = [
        ("WS-A-5001", "VTI", 620, 148800.0), ("WS-A-5001", "BND", 1450, 105300.0), ("WS-A-5001", "AAPL", 180, 31500.0),
        ("WS-A-5001", "GLD", 90, 19800.0),
        ("WS-A-5002", "VTI", 310, 71300.0), ("WS-A-5002", "VXUS", 520, 31200.0), ("WS-A-5002", "MSFT", 60, 21900.0),
        ("WS-A-5003", "VTI", 240, 61200.0), ("WS-A-5003", "VXUS", 400, 25600.0), ("WS-A-5003", "BND", 300, 21900.0),
        ("WS-A-5004", "VTI", 1100, 231000.0), ("WS-A-5004", "BND", 2600, 189800.0), ("WS-A-5004", "JNJ", 400, 61200.0),
        ("WS-A-5004", "VXUS", 900, 54000.0), ("WS-A-5004", "GLD", 150, 33000.0),
    ]
    holdings = [{"entity": "holding", "external_id": f"WS-H-{acct}-{sym}", "as_of": STAMP, "account_ref": acct,
                 "symbol": sym, "quantity": float(qty), "cost_basis": cost} for acct, sym, qty, cost in positions]
    # The last year's activity on the platform: deposits, purchases and dividends.
    txns = [
        ("WS-T-0001", "WS-A-5001", None, "deposit", 0, 0, 50000.0, "2026-01-12"),
        ("WS-T-0002", "WS-A-5001", "AAPL", "buy", 40, 212.5, 8500.0, "2026-02-03"),
        ("WS-T-0003", "WS-A-5001", "VTI", "dividend", 0, 0, 1980.4, "2026-03-27"),
        ("WS-T-0004", "WS-A-5002", "VXUS", "buy", 120, 61.0, 7320.0, "2026-04-15"),
        ("WS-T-0005", "WS-A-5003", None, "deposit", 0, 0, 20000.0, "2026-05-02"),
        ("WS-T-0006", "WS-A-5003", "VTI", "buy", 40, 281.0, 11240.0, "2026-05-06"),
        ("WS-T-0007", "WS-A-5004", "BND", "dividend", 0, 0, 6230.75, "2026-06-30"),
        ("WS-T-0008", "WS-A-5004", "JNJ", "buy", 100, 152.0, 15200.0, "2026-08-19"),
        ("WS-T-0009", "WS-A-5004", "VTI", "dividend", 0, 0, 3510.2, "2026-09-26"),
        ("WS-T-0010", "WS-A-5001", "BND", "dividend", 0, 0, 3920.1, "2026-10-31"),
    ]
    activity = [{"entity": "txn", "external_id": ext, "as_of": STAMP, "account_ref": acct, "symbol": sym,
                 "txn_type": kind, "quantity": float(q), "price": float(px), "amount": amt, "trade_date": day,
                 "lineage": {"platform": PLATFORM["name"], "simulated": True}}
                for ext, acct, sym, kind, q, px, amt, day in txns]
    return {"parties": households + people + entities, "accounts": mandates + accounts, "positions": holdings,
            "activity": activity}
