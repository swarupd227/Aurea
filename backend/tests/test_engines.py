"""Unit tests for the deterministic engines — no database required.

Covers the governance- and advice-critical maths: tax-aware rebalancing, the planning/risk
engine, and the decision-ledger hash chain."""
from app.aurea_core.rebalancing import Lot, Position, optimise
from app.aurea_core import planning
from app.provenance.ledger import GENESIS, compute_hash


def _pos(symbol, ac, qty, price, cost_per_unit, lots=None):
    mv = qty * price
    return Position(
        holding_id=symbol, instrument_id=symbol, symbol=symbol, name=symbol, asset_class=ac,
        market_value=mv, price=price, cost_basis=qty * cost_per_unit, account_id="a", custodian="c",
        lots=lots or [Lot(qty, cost_per_unit)],
    )


def test_no_rebalance_within_band():
    positions = [
        _pos("EQ", "equity", 50, 10, 8),       # 500
        _pos("FI", "fixed_income", 50, 10, 10), # 500
    ]
    res = optimise(positions=positions, target_weights={"equity": 0.5, "fixed_income": 0.5},
                   cash=0, drift_band=0.05)
    assert res.needs_rebalance is False
    assert res.orders == []


def test_detects_drift_and_generates_orders():
    positions = [
        _pos("EQ", "equity", 80, 10, 5),        # 800 (overweight)
        _pos("FI", "fixed_income", 20, 10, 10), # 200 (underweight)
    ]
    res = optimise(positions=positions, target_weights={"equity": 0.5, "fixed_income": 0.5},
                   cash=0, drift_band=0.05)
    assert res.needs_rebalance is True
    assert res.max_drift > 0.05
    sells = [o for o in res.orders if o.side == "sell"]
    buys = [o for o in res.orders if o.side == "buy"]
    assert sells and buys
    assert sells[0].asset_class == "equity"


def test_cgt_budget_caps_realised_gain():
    # Single overweight equity with a large embedded gain; CGT budget should cap the sale.
    positions = [
        _pos("EQ", "equity", 100, 10, 1, lots=[Lot(100, 1.0)]),  # huge gain (9/unit)
        _pos("FI", "fixed_income", 10, 10, 10),
    ]
    res = optimise(positions=positions, target_weights={"equity": 0.5, "fixed_income": 0.5},
                   cash=0, drift_band=0.05, cgt_budget=90)
    # Realised gain must not exceed the budget (within rounding).
    assert res.estimated_realised_gain <= 90 + 1e-6


def test_loss_harvesting_prefers_loss_lots():
    # Two lots: one at a loss, one at a gain. Selling should harvest the loss first.
    positions = [
        _pos("EQ", "equity", 100, 10, 0, lots=[Lot(50, 14.0), Lot(50, 2.0)]),  # 50 loss lot, 50 gain lot
        _pos("FI", "fixed_income", 10, 10, 10),
    ]
    res = optimise(positions=positions, target_weights={"equity": 0.4, "fixed_income": 0.6},
                   cash=0, drift_band=0.05)
    sell = [o for o in res.orders if o.side == "sell"][0]
    # First units sold are the loss lot (cost 14 vs price 10 → negative gain).
    assert sell.est_realised_gain < 0
    assert res.harvested_losses > 0


def test_values_excluded_position_sold_first():
    positions = [
        _pos("OK", "equity", 50, 10, 8),
        _pos("BAD", "equity", 50, 10, 8),
        _pos("FI", "fixed_income", 10, 10, 10),
    ]
    positions[1].excluded = True
    res = optimise(positions=positions, target_weights={"equity": 0.4, "fixed_income": 0.6},
                   cash=0, drift_band=0.05)
    sells = [o for o in res.orders if o.side == "sell"]
    assert sells[0].symbol == "BAD"  # excluded holding trimmed first


def test_planning_projection_and_stress():
    proj = planning.project_goal(
        current_value=500000, allocation={"equity": 0.6, "fixed_income": 0.4},
        annual_contribution=20000, annual_withdrawal=0, years=10, target_amount=800000, sims=500,
    )
    assert 0.0 <= proj.probability_of_success <= 1.0
    assert proj.projected_p10 <= proj.projected_median <= proj.projected_p90

    risk = planning.portfolio_risk({"equity": 0.6, "fixed_income": 0.4}, 1_000_000)
    assert risk["volatility"] > 0
    assert risk["var_95_1y"] >= 0

    stress = planning.stress_test({"equity": 600000, "fixed_income": 400000}, ["gfc_2008"])
    assert stress["gfc_2008"]["impact_value"] < 0  # a loss


def test_planning_is_deterministic():
    a = planning.project_goal(current_value=100000, allocation={"equity": 1.0},
                              annual_contribution=0, annual_withdrawal=0, years=5,
                              target_amount=120000, sims=300, seed=7)
    b = planning.project_goal(current_value=100000, allocation={"equity": 1.0},
                              annual_contribution=0, annual_withdrawal=0, years=5,
                              target_amount=120000, sims=300, seed=7)
    assert a.probability_of_success == b.probability_of_success


def test_ledger_hash_chain_is_tamper_evident():
    c1 = {"event": "recommendation", "value": 100}
    h1 = compute_hash(GENESIS, 1, "recommendation", c1)
    h2 = compute_hash(h1, 2, "decision", {"action": "approve"})
    # Deterministic.
    assert compute_hash(GENESIS, 1, "recommendation", c1) == h1
    # Any change to content changes the hash (tamper-evidence).
    assert compute_hash(GENESIS, 1, "recommendation", {"event": "recommendation", "value": 101}) != h1
    # Chain links via prev hash.
    assert compute_hash("deadbeef", 2, "decision", {"action": "approve"}) != h2
