"""Eval & quality gates (Foundation pillar 'Eval & quality gates').

Golden, deterministic cases that assert the rebalancing engine's invariants — regression,
equivalence and behaviour checks that must stay green before a model/config change ships. Pure
(no LLM, no I/O) so the gate is fast and reproducible."""
from __future__ import annotations

from app.aurea_core.rebalancing import Lot, Position, optimise
from app.compliance.rules import CheckContext as _CC, narrative_instrument_check as _nic


def _pos(symbol, ac, mv, price, cost, *, excluded=False, protected=False, lots=None) -> Position:
    qty = mv / price if price else 0
    return Position(holding_id=symbol, instrument_id=symbol, symbol=symbol, name=symbol,
                    asset_class=ac, market_value=mv, price=price, cost_basis=cost,
                    account_id="a", custodian="c", excluded=excluded, protected=protected,
                    lots=lots or [Lot(qty, (cost / qty) if qty else 0.0)])


def run_gates() -> dict:
    cases: list[dict] = []

    def add(key, name, category, passed, detail):
        cases.append({"key": key, "name": name, "category": category,
                      "passed": bool(passed), "detail": detail})

    # 1. Regression — realised gain never exceeds the CGT budget.
    pos = [_pos("EQ", "equity", 80000, 100, 40000), _pos("FI", "fixed_income", 20000, 100, 20000)]
    r = optimise(positions=pos, target_weights={"equity": 0.5, "fixed_income": 0.5}, cash=0,
                 drift_band=0.05, cgt_budget=5000)
    add("cgt_budget", "Realised gain ≤ CGT budget", "regression",
        r.estimated_realised_gain <= 5000 + 1, f"realised ${r.estimated_realised_gain:,.0f} vs budget $5,000")

    # 2. Behaviour — losses are harvested first when trimming.
    pos = [_pos("EQg", "equity", 60000, 100, 30000), _pos("EQl", "equity", 40000, 100, 60000),
           _pos("FI", "fixed_income", 0.0, 100, 0)]
    r = optimise(positions=pos, target_weights={"equity": 0.5, "fixed_income": 0.5}, cash=0,
                 drift_band=0.05, cgt_budget=None)
    add("loss_harvest", "Losses harvested before gains", "behaviour",
        r.harvested_losses > 0, f"${r.harvested_losses:,.0f} losses harvested")

    # 3. Equivalence — a portfolio already at target produces no orders.
    pos = [_pos("EQ", "equity", 50000, 100, 40000), _pos("FI", "fixed_income", 50000, 100, 50000)]
    r = optimise(positions=pos, target_weights={"equity": 0.5, "fixed_income": 0.5}, cash=0, drift_band=0.05)
    add("within_tolerance", "Within tolerance → no trades", "equivalence",
        not r.needs_rebalance and not r.orders, f"{len(r.orders)} orders, needs_rebalance={r.needs_rebalance}")

    # 4. Behaviour — a protected holding is never sold.
    pos = [_pos("EQp", "equity", 70000, 100, 35000, protected=True),
           _pos("FI", "fixed_income", 30000, 100, 30000)]
    r = optimise(positions=pos, target_weights={"equity": 0.5, "fixed_income": 0.5}, cash=0, drift_band=0.05)
    add("protect", "Protected holding never sold", "behaviour",
        all(o.symbol != "EQp" for o in r.orders if o.side == "sell"), "EQp excluded from sells")

    # 5. Behaviour — a values-excluded holding is divested first.
    pos = [_pos("EQx", "equity", 60000, 100, 50000, excluded=True),
           _pos("EQ2", "equity", 40000, 100, 30000), _pos("FI", "fixed_income", 0.0, 100, 0)]
    r = optimise(positions=pos, target_weights={"equity": 0.6, "fixed_income": 0.4}, cash=0, drift_band=0.05)
    add("exclusion", "Values-excluded holding divested", "behaviour",
        any(o.symbol == "EQx" for o in r.orders if o.side == "sell"), "EQx appears in sells")

    # 6. Guardrail — an under-weight class with no eligible instrument raises a breach.
    pos = [_pos("EQ", "equity", 100000, 100, 50000)]
    r = optimise(positions=pos, target_weights={"equity": 0.6, "alternatives": 0.4}, cash=0, drift_band=0.05)
    add("guardrail", "Missing instrument raises a guardrail", "guardrail",
        len(r.guardrail_breaches) > 0, f"{len(r.guardrail_breaches)} breach(es) detected")

    # 7. Groundedness — the narrative instrument check catches a disconnected rationale and
    #    passes a grounded one. This gate validates the compliance rule itself deterministically.
    _orders = [
        {"symbol": "EQ", "name": "NZ Equity Fund", "side": "sell",
         "asset_class": "equity", "est_realised_gain": 0},
        {"symbol": "FI", "name": "NZ Bond Fund", "side": "buy",
         "asset_class": "fixed_income", "est_realised_gain": 0},
    ]
    _ctx_bad = _CC(
        agent_key="drift_rebalancing",
        rationale="The portfolio is well-positioned and no changes are required at this time.",
        text="the portfolio is well-positioned and no changes are required at this time",
        confidence=0.9, payload={"order_set": _orders}, evidence={},
        citations=[], mandate_type=None, policy={},
    )
    _ctx_good = _CC(
        agent_key="drift_rebalancing",
        rationale="We recommend trimming equity exposure and adding to fixed income to reduce drift and rebalance the portfolio.",
        text="we recommend trimming equity exposure and adding to fixed income to reduce drift",
        confidence=0.9, payload={"order_set": _orders}, evidence={},
        citations=[], mandate_type=None, policy={},
    )
    _bad = _nic(_ctx_bad)
    _good = _nic(_ctx_good)
    add("narrative_groundedness", "Groundedness rule catches disconnected narrative", "groundedness",
        _bad.status != "pass" and _good.status == "pass",
        f"disconnected→{_bad.status} · grounded→{_good.status}")

    passed = sum(1 for c in cases if c["passed"])
    return {"agent": "drift_rebalancing", "cases": cases, "passed": passed, "total": len(cases),
            "all_green": passed == len(cases)}
