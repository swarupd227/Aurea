"""A rebalance may only spend money the account actually has.

Found on the live demo book. Chen Family Trust held $30,000 cash. The CGT budget correctly
capped the sells at $83,586, but the buys were sized from target weights alone and totalled
$226,634. Approving would have settled every order and left the custody account at roughly
minus $113,358.

Two independent guards, because either alone is a single point of failure: the engine must
not propose what cannot be funded, and settlement must refuse to overdraw whatever it is
handed.

    python -m tests.verify_funding
"""
from __future__ import annotations

import sys

from app.aurea_core.rebalancing import Lot, Position, optimise

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def pos(sym: str, cls: str, mv: float, px: float, cost: float) -> Position:
    return Position(holding_id=f"h-{sym}", instrument_id=f"i-{sym}", symbol=sym, name=sym,
                    asset_class=cls, market_value=mv, price=px, cost_basis=cost,
                    account_id="a1", custodian="X", lots=[Lot(mv / px, cost / (mv / px))])


def main() -> int:
    print("\n=== the Chen shape: a CGT cap starves the buys ===")
    # Equity heavily over target with a large embedded gain, so a tight CGT budget limits
    # how much can be sold; fixed income and property far under target.
    positions = [
        pos("MSFT", "equity", 628_534, 394.36, 120_000),
        pos("AGG", "fixed_income", 98_325, 98.325, 100_000),
        pos("VNQ", "property", 29_641, 98.805, 27_000),
    ]
    r = optimise(
        positions=positions,
        target_weights={"equity": 0.50, "fixed_income": 0.30, "property": 0.10,
                        "alternatives": 0.10},
        cash=30_000, drift_band=0.05, cgt_budget=20_000,
    )
    sells = sum(o.est_value for o in r.orders if o.side == "sell")
    buys = sum(o.est_value for o in r.orders if o.side == "buy")
    funded = 30_000 + sells
    print(f"  cash 30,000 + sells {sells:,.0f} = {funded:,.0f} available; buys {buys:,.0f}")
    check("buys do not exceed cash plus proceeds", buys <= funded + 1.0, True)
    check("the shortfall is reported, not hidden",
          any("short" in l for l in r.limitations), True)

    print("\n=== nothing to sell and no cash: no buy is invented ===")
    r = optimise(positions=[pos("MSFT", "equity", 100_000, 400.0, 95_000)],
                 target_weights={"equity": 0.5, "fixed_income": 0.5},
                 cash=0.0, drift_band=0.05, cgt_budget=0)
    buys = [o for o in r.orders if o.side == "buy"]
    sells = sum(o.est_value for o in r.orders if o.side == "sell")
    check("buys stay within the zero proceeds available",
          sum(o.est_value for o in buys) <= sells + 1.0, True)

    print("\n=== ample cash: the full target is still bought ===")
    r = optimise(positions=[pos("MSFT", "equity", 50_000, 400.0, 45_000)],
                 target_weights={"equity": 0.25, "fixed_income": 0.75},
                 cash=150_000, drift_band=0.05,
                 model_instruments={"fixed_income": Position(
                     holding_id="", instrument_id="i-AGG", symbol="AGG", name="AGG",
                     asset_class="fixed_income", market_value=0.0, price=98.325,
                     cost_basis=0.0, account_id="a1", custodian="X")},
                 cgt_budget=None)
    buys = sum(o.est_value for o in r.orders if o.side == "buy")
    check("a fully funded buy is not trimmed", buys > 100_000, True)
    check("and no shortfall is reported",
          any("short" in l for l in r.limitations), False)

    print("\n=== a within-tolerance portfolio takes the other path cleanly ===")
    # The shortfall report runs whether or not a rebalance was needed. Declaring its list
    # inside the `if needs:` branch raised UnboundLocalError on exactly this path — every
    # test above took the branch, so only the eval gates caught it.
    r = optimise(positions=[pos("MSFT", "equity", 50_000, 400.0, 45_000),
                            pos("AGG", "fixed_income", 50_000, 98.325, 50_000)],
                 target_weights={"equity": 0.5, "fixed_income": 0.5},
                 cash=0.0, drift_band=0.05, cgt_budget=None)
    check("no rebalance needed", r.needs_rebalance, False)
    check("no orders", len(r.orders), 0)
    check("no shortfall reported", any("short" in l for l in r.limitations), False)

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
