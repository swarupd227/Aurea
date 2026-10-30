"""The rebalancer can buy into a class no account holds — and says so when it cannot.

Wei & Mei — Growth sat 10% under its alternatives target and produced zero orders, because
the only way the engine could find something to buy was an existing holding in that class.
The model's own instrument nomination existed on TargetAllocation and was never used.

    python -m tests.verify_model_instruments
"""
from __future__ import annotations

import sys

from app.aurea_core.rebalancing import Lot, Position, optimise

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(f"{label}: got {got}, want {want}")
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def equity(mv: float) -> Position:
    return Position(holding_id="h1", instrument_id="i1", symbol="MSFT", name="Microsoft",
                    asset_class="equity", market_value=mv, price=410.0, cost_basis=mv * 0.8,
                    account_id="a1", custodian="X",
                    lots=[Lot(mv / 410.0, 328.0)])


TARGETS = {"equity": 0.75, "alternatives": 0.25}


def main() -> int:
    print("\n=== without a model nomination: nothing to buy, and it says so ===")
    r = optimise(positions=[equity(100_000)], target_weights=TARGETS, cash=0.0,
                 drift_band=0.05, cgt_budget=None, model_instruments=None)
    check("rebalance needed", r.needs_rebalance, True)
    # Equity is over target, so a sell is correct. It is the *buy* side that has nowhere
    # to go — which is exactly the hole: proceeds raised with nothing to reinvest them in.
    check("the over-weight equity is still sold",
          len([o for o in r.orders if o.side == "sell"]), 1)
    check("but no alternatives buy is possible",
          len([o for o in r.orders if o.asset_class == "alternatives"]), 0)
    check("limitation explains why", any("names no instrument" in l for l in r.limitations), True)

    print("\n=== with a tradeable nomination: a real buy order appears ===")
    listed = Position(holding_id="", instrument_id="i2", symbol="ALTX", name="Alt Index ETF",
                      asset_class="alternatives", market_value=0.0, price=50.0,
                      cost_basis=0.0, account_id="", custodian="", tradeable=True)
    r = optimise(positions=[equity(100_000)], target_weights=TARGETS, cash=0.0,
                 drift_band=0.05, cgt_budget=None,
                 model_instruments={"alternatives": listed})
    buys = [o for o in r.orders if o.side == "buy" and o.asset_class == "alternatives"]
    check("an alternatives buy is proposed", len(buys), 1)
    check("it buys the nominated instrument", buys[0].symbol if buys else None, "ALTX")
    check("no 'nothing to buy' limitation", any("names no instrument" in l for l in r.limitations), False)

    print("\n=== a private nomination is named, not silently dropped ===")
    private = Position(holding_id="", instrument_id="i3", symbol="PPEF1",
                       name="Pacific Private Equity Fund I", asset_class="alternatives",
                       market_value=0.0, price=100.0, cost_basis=0.0, account_id="",
                       custodian="", tradeable=False)
    r = optimise(positions=[equity(100_000)], target_weights=TARGETS, cash=0.0,
                 drift_band=0.05, cgt_budget=None,
                 model_instruments={"alternatives": private})
    check("no order for a private fund", len([o for o in r.orders if o.asset_class == "alternatives"]), 0)
    lim = " ".join(r.limitations)
    check("the instrument is named", "PPEF1" in lim, True)
    check("subscription, not trading, is explained", "subscribed to" in lim, True)
    check("and the amount to allocate is given", "$" in lim, True)

    print("\n=== an existing holding still wins over the nomination ===")
    held = Position(holding_id="h2", instrument_id="i4", symbol="HELD", name="Held Alt",
                    asset_class="alternatives", market_value=1_000.0, price=25.0,
                    cost_basis=800.0, account_id="a1", custodian="X", lots=[Lot(40, 20.0)])
    r = optimise(positions=[equity(100_000), held], target_weights=TARGETS, cash=0.0,
                 drift_band=0.05, cgt_budget=None,
                 model_instruments={"alternatives": listed})
    buys = [o for o in r.orders if o.side == "buy" and o.asset_class == "alternatives"]
    check("buys into the position already held", buys[0].symbol if buys else None, "HELD")

    print("\n=== a buy rounded down is not a shortfall; a real one still is ===")
    odd = Position(holding_id="", instrument_id="i5", symbol="ODD", name="Odd Price ETF",
                   asset_class="alternatives", market_value=0.0, price=33.337, cost_basis=0.0,
                   account_id="", custodian="", tradeable=True)
    # Cash covers the fees, so the only gap left between wanted and bought is the rounding.
    funded = {"equity": 0.5, "alternatives": 0.45, "cash": 0.05}
    r = optimise(positions=[equity(95_000)], target_weights=funded, cash=5_000.0, drift_band=0.05,
                 cgt_budget=None, model_instruments={"alternatives": odd})
    buys = [o for o in r.orders if o.side == "buy"]
    check("the buy lands a fraction under what was wanted",
          0 < 45_000 - buys[0].quantity * odd.price < 1 if buys else None, True)
    check("and no '$0 short' is reported", any("short" in l for l in r.limitations), False)
    r = optimise(positions=[equity(100_000)], target_weights=TARGETS, cash=0.0, drift_band=0.05,
                 cgt_budget=0.0, model_instruments={"alternatives": odd})
    check("a CGT budget that stops the sale is still reported as a shortfall",
          any("short" in l and "$0 short" not in l for l in r.limitations), True)

    print(f"\n{'=' * 64}\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
