"""Whole-portfolio, correlation- and tax-aware rebalancing optimisation (spec §6.5, §7).

This is the engine behind the Drift & Tax-Managed Rebalancing lighthouse agent. It is pure
(no I/O) so it is fully unit-testable and deterministic:

  * detects drift of each asset class vs the target model beyond a tolerance band;
  * for over-weight classes, raises cash by tax-lot selection — harvesting losses first,
    then realising the smallest gains — while respecting a capital-gains-tax budget;
  * for under-weight classes, allocates buys to existing or model instruments, honouring
    values-aligned exclusions;
  * reports estimated realised gain, harvested losses, turnover and any guardrail breaches.

No live execution — the output is a draft, multi-custodian order set for adviser review."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Lot:
    quantity: float
    cost_per_unit: float


@dataclass
class Position:
    holding_id: str
    instrument_id: str
    symbol: str
    name: str
    asset_class: str
    market_value: float
    price: float
    cost_basis: float
    account_id: str
    custodian: str
    lots: list[Lot] = field(default_factory=list)
    # Can this actually be bought or sold on a venue? False for private-market holdings,
    # which the engine must not propose as if they were listed securities.
    tradeable: bool = True
    excluded: bool = False  # values-aligned exclusion (e.g. fails ESG screen) → divest
    protected: bool = False  # adviser asked not to sell this holding → never trim


@dataclass
class Order:
    side: str  # buy | sell
    symbol: str
    name: str
    instrument_id: str
    asset_class: str
    quantity: float
    est_price: float
    est_value: float
    account_id: str
    custodian: str
    est_realised_gain: float = 0.0
    reason: str = ""


@dataclass
class RebalanceResult:
    needs_rebalance: bool
    total_value: float
    drift_band: float
    current_weights: dict[str, float]
    target_weights: dict[str, float]
    drifts: dict[str, float]
    orders: list[Order]
    estimated_realised_gain: float
    harvested_losses: float
    turnover: float
    turnover_pct: float
    guardrail_breaches: list[str]
    limitations: list[str]  # informational — not compliance breaches (e.g. no instrument to buy)
    max_drift: float


def _sell_with_lots(pos: Position, target_value: float, cgt_remaining: float) -> tuple[float, float, float]:
    """Sell up to target_value from a position using tax-optimal lots.

    Returns (value_sold, realised_gain, quantity_sold). Harvests losses first, then takes
    the smallest gains, and stops if the next gain lot would breach the remaining CGT budget
    (unless the lot is a loss, which is always allowed)."""
    if pos.price <= 0 or not pos.lots:
        return 0.0, 0.0, 0.0
    # Order lots by gain-per-unit ascending (most negative / losses first).
    lots = sorted(pos.lots, key=lambda lot: pos.price - lot.cost_per_unit)
    value_sold = realised_gain = qty_sold = 0.0
    for lot in lots:
        if value_sold >= target_value - 1e-6:
            break
        gain_per_unit = pos.price - lot.cost_per_unit
        remaining_value = target_value - value_sold
        max_qty_by_value = remaining_value / pos.price
        qty = min(lot.quantity, max_qty_by_value)
        if qty <= 0:
            continue
        lot_gain = gain_per_unit * qty
        # Respect CGT budget for gain lots (losses always allowed — they free up budget).
        if lot_gain > 0 and (realised_gain + lot_gain) > cgt_remaining:
            allowable_gain = max(0.0, cgt_remaining - realised_gain)
            if gain_per_unit > 0:
                qty = min(qty, allowable_gain / gain_per_unit)
                lot_gain = gain_per_unit * qty
            if qty <= 1e-9:
                continue
        value_sold += qty * pos.price
        realised_gain += lot_gain
        qty_sold += qty
    return value_sold, realised_gain, qty_sold


def optimise(
    *,
    positions: list[Position],
    target_weights: dict[str, float],
    cash: float,
    drift_band: float = 0.05,
    cgt_budget: float | None = None,
    model_instruments: dict[str, Position] | None = None,
    # The venue's fee model, so buys can be sized net of what execution will actually
    # charge. Defaults match the firm default (10bps, $5 minimum); the caller passes the
    # firm's own. Ignoring these is not a rounding error — it over-commits the account by
    # exactly the total fees and the last buy fails to settle.
    fee_rate: float = 0.001,
    fee_minimum: float = 5.0,
) -> RebalanceResult:
    total_value = sum(p.market_value for p in positions) + cash
    if total_value <= 0:
        return RebalanceResult(False, 0, drift_band, {}, target_weights, {}, [], 0, 0, 0, 0, [], 0)

    # Current weights by asset class (cash counted as its own class).
    by_class_value: dict[str, float] = {}
    for p in positions:
        by_class_value[p.asset_class] = by_class_value.get(p.asset_class, 0.0) + p.market_value
    by_class_value["cash"] = by_class_value.get("cash", 0.0) + cash

    classes = set(by_class_value) | set(target_weights)
    current_weights = {c: by_class_value.get(c, 0.0) / total_value for c in classes}
    drifts = {c: current_weights.get(c, 0.0) - target_weights.get(c, 0.0) for c in classes}
    max_drift = max((abs(d) for c, d in drifts.items() if c != "cash"), default=0.0)
    needs = max_drift > drift_band

    cgt_remaining = cgt_budget if cgt_budget is not None else float("inf")
    orders: list[Order] = []
    realised_gain = harvested = 0.0
    breaches: list[str] = []
    limitations: list[str] = []
    # Declared out here, not inside `if needs`, because the shortfall report below runs
    # unconditionally — a within-tolerance portfolio has nothing underfunded, but the name
    # still has to exist.
    underfunded: list[tuple[str, float, float]] = []

    if needs:
        # 1. Sell over-weight classes.
        for cls, drift in sorted(drifts.items(), key=lambda kv: kv[1]):
            if cls == "cash" or drift <= drift_band:
                continue
            sell_value = drift * total_value  # amount over target
            cls_positions = sorted(
                [p for p in positions if p.asset_class == cls and not p.protected],  # never sell protected
                key=lambda p: (not p.excluded, p.market_value - p.cost_basis),  # excluded & losses first
            )
            for pos in cls_positions:
                if sell_value <= 1e-6:
                    break
                want = min(sell_value, pos.market_value)
                value_sold, lot_gain, qty = _sell_with_lots(pos, want, cgt_remaining)
                if qty <= 1e-9:
                    continue
                cgt_remaining -= max(lot_gain, 0.0)
                realised_gain += lot_gain
                if lot_gain < 0:
                    harvested += -lot_gain
                sell_value -= value_sold
                reason = "Trim over-weight"
                if pos.excluded:
                    reason = "Exit values-excluded holding & trim over-weight"
                elif lot_gain < 0:
                    reason = "Harvest loss while trimming over-weight"
                orders.append(Order(
                    side="sell", symbol=pos.symbol, name=pos.name, instrument_id=pos.instrument_id,
                    asset_class=cls, quantity=round(qty, 4), est_price=round(pos.price, 4),
                    est_value=round(value_sold, 2), account_id=pos.account_id, custodian=pos.custodian,
                    est_realised_gain=round(lot_gain, 2), reason=reason,
                ))

        # 2. Buy under-weight classes from proceeds + cash — and only from those.
        #
        # This loop used to size each buy purely from the target weight, which is what the
        # portfolio *wants*, not what it can *fund*. When a CGT budget caps the sells (as it
        # is designed to), the shortfall was ignored and the order set spent money the
        # account did not have. Settlement would then book it and leave the cash balance
        # negative. Buys are now paid for out of a running balance.
        def _fee(value: float) -> float:
            return max(value * fee_rate, fee_minimum) if value > 0 else 0.0

        # Fees are per order, not a blanket percentage of the total, because of the minimum.
        proceeds = sum(o.est_value - _fee(o.est_value) for o in orders if o.side == "sell")
        available = cash + proceeds

        for cls, drift in sorted(drifts.items(), key=lambda kv: kv[1], reverse=True):
            if cls == "cash" or drift >= -drift_band:
                continue
            wanted = (-drift) * total_value
            # Size the buy so the trade *and its fee* fit inside what is left.
            buy_value = min(wanted, available)
            if buy_value + _fee(buy_value) > available:
                buy_value = max(0.0, (available - fee_minimum)
                                if available * fee_rate < fee_minimum
                                else available / (1.0 + fee_rate))
            if buy_value <= 1e-6:
                underfunded.append((cls, wanted, 0.0))
                continue
            # Prefer an existing non-excluded holding in the class; else the instrument the
            # model nominates for it.
            candidates = [p for p in positions if p.asset_class == cls and not p.excluded]
            target_pos = candidates[0] if candidates else (model_instruments or {}).get(cls)
            if target_pos is None:
                limitations.append(
                    f"The model targets {(-drift):.0%} in '{cls}' but names no instrument to "
                    f"buy and no account holds one — adviser to select manually."
                )
                continue
            if not target_pos.tradeable:
                # A private fund is subscribed to, not bought on a venue. Saying so is the
                # difference between an adviser knowing what to do and an order that would
                # be rejected downstream for reasons that look like a system fault.
                limitations.append(
                    f"'{cls}' is {(-drift):.0%} under target. The model's instrument for it "
                    f"({target_pos.symbol} — {target_pos.name}) is a private-market holding, "
                    f"which is subscribed to outside the OMS rather than traded. "
                    f"${buy_value:,.0f} to allocate — adviser to arrange."
                )
                continue
            if target_pos.price <= 0:
                limitations.append(
                    f"No usable price for {target_pos.symbol}, so '{cls}' cannot be bought "
                    f"— adviser to select manually."
                )
                continue
            qty = buy_value / target_pos.price
            orders.append(Order(
                side="buy", symbol=target_pos.symbol, name=target_pos.name,
                instrument_id=target_pos.instrument_id, asset_class=cls, quantity=round(qty, 4),
                est_price=round(target_pos.price, 4), est_value=round(buy_value, 2),
                account_id=target_pos.account_id, custodian=target_pos.custodian,
                reason="Top up under-weight",
            ))
            available -= (buy_value + _fee(buy_value))
            if buy_value < wanted - 1e-6:
                underfunded.append((cls, wanted, buy_value))

    # Say what could not be funded. Silently buying less than the target would look like a
    # completed rebalance that quietly left the portfolio off-model.
    for cls, wanted, funded in underfunded:
        shortfall = wanted - funded
        limitations.append(
            f"'{cls}' needs ${wanted:,.0f} to reach target but only ${funded:,.0f} could be "
            f"funded from cash and sale proceeds — ${shortfall:,.0f} short. Raising more "
            f"would exceed the CGT budget or the available cash."
        )

    if cgt_budget is not None and realised_gain > cgt_budget + 1e-6:
        breaches.append(
            f"Estimated realised gain {realised_gain:,.0f} exceeds CGT budget {cgt_budget:,.0f}."
        )

    turnover = sum(o.est_value for o in orders)
    return RebalanceResult(
        needs_rebalance=needs,
        total_value=round(total_value, 2),
        drift_band=drift_band,
        current_weights={c: round(w, 4) for c, w in current_weights.items()},
        target_weights=target_weights,
        drifts={c: round(d, 4) for c, d in drifts.items()},
        orders=orders,
        estimated_realised_gain=round(realised_gain, 2),
        harvested_losses=round(harvested, 2),
        turnover=round(turnover, 2),
        turnover_pct=round(turnover / total_value, 4) if total_value else 0.0,
        guardrail_breaches=breaches,
        limitations=limitations,
        max_drift=round(max_drift, 4),
    )
