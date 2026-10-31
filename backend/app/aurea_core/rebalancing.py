"""Whole-portfolio, correlation- and tax-aware rebalancing optimisation (spec §6.5, §7).

This is the engine behind the Drift & Tax-Managed Rebalancing lighthouse agent. It is pure
(no I/O) so it is fully unit-testable and deterministic:

  * detects drift of each asset class vs its target beyond a tolerance band;
  * for over-weight classes, raises cash by tax-lot selection — harvesting losses first,
    then realising the smallest gains — while respecting a capital-gains-tax budget;
  * for under-weight classes, allocates buys to existing or model instruments, honouring
    values-aligned exclusions;
  * reports estimated realised gain, harvested losses, turnover and any guardrail breaches.

No live execution — the output is a draft, multi-custodian order set for adviser review."""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date


# Quantities are carried to four decimal places. Orders are *sized* in value but *carried*
# as a quantity, and settlement recomputes the value back from that quantity — so rounding
# the quantity up makes the settled cost exceed the value the order was funded against.
# That overdrew a real account by 1.5 cents and failed an otherwise correct settlement.
# Flooring guarantees qty * price <= the value reserved, and therefore the recomputed fee
# cannot exceed the reserved fee either. Trading a fractionally smaller amount is always
# safe; a fractionally larger one is not.
_QTY_DP = 4
_SHORTFALL_WORTH_SAYING = 1.0  # the limitation is written in whole currency units


def _floor_qty(qty: float) -> float:
    return math.floor(qty * (10 ** _QTY_DP)) / (10 ** _QTY_DP)


def _dollars(value: float) -> str:
    return f"${value:,.0f}"


@dataclass
class Lot:
    quantity: float
    cost_per_unit: float
    # Where the law cares when a lot was bought: the date decides the order under FIFO, and the
    # bucket is the tax treatment of a gain on this lot if it is sold now.
    acquired_on: date | None = None
    bucket: str | None = None


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
class WashSaleGuard:
    """What the household did in the wash-sale window (aurea_core/wash_sale.py), by instrument id.
    Each value says when, where and when it clears: {"on", "account", "clear_on"}. India's rules take
    the same shape (aurea_core/harvest_guard.py) and add "say", the limitation in their own words."""

    no_loss_sale: dict[str, dict] = field(default_factory=dict)  # bought recently: a loss here is disallowed
    no_buy: dict[str, dict] = field(default_factory=dict)        # sold at a loss recently: buying back disallows it
    no_sell: dict[str, dict] = field(default_factory=dict)       # bought today: selling it would net


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
    gain_buckets: dict[str, float] = field(default_factory=dict)


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
    # What the wash-sale guard kept out of the order set, as figures for the proposal's card.
    wash_sale_held: list[dict] = field(default_factory=list)
    gains_by_bucket: dict[str, float] = field(default_factory=dict)


def _sell_with_lots(
    pos: Position, target_value: float, cgt_remaining: float, *, no_loss: bool = False,
    fifo: bool = False, whole_units: bool = False, gains: dict[str, float] | None = None,
    bucket_left: dict[str, float] | None = None,
) -> tuple[float, float, float]:
    """Sell up to target_value from a position using tax-optimal lots.

    Returns (value_sold, realised_gain, quantity_sold). Harvests losses first, then takes
    the smallest gains, and stops if the next gain lot would breach the remaining CGT budget
    (unless the lot is a loss, which is always allowed). With `no_loss` the loss lots are left
    alone: inside a wash-sale window the loss would be disallowed.

    With `fifo` the law has already chosen: the oldest lot goes first, and a lot that cannot be sold
    cannot be stepped over, so reaching one ends the sale. Where lots carry a bucket, `gains` collects
    the gain by bucket and `bucket_left` is a budget per bucket."""
    if pos.price <= 0 or not pos.lots:
        return 0.0, 0.0, 0.0
    # Order lots by gain-per-unit ascending (most negative / losses first).
    lots = (sorted(pos.lots, key=lambda lot: lot.acquired_on or date.min) if fifo
            else sorted(pos.lots, key=lambda lot: pos.price - lot.cost_per_unit))
    value_sold = realised_gain = qty_sold = 0.0
    for lot in lots:
        if value_sold >= target_value - 1e-6:
            break
        gain_per_unit = pos.price - lot.cost_per_unit
        if no_loss and gain_per_unit < 0:
            if fifo:
                break
            continue
        remaining_value = target_value - value_sold
        max_qty_by_value = remaining_value / pos.price
        qty = min(lot.quantity, max_qty_by_value)
        if qty <= 0:
            continue
        lot_gain = gain_per_unit * qty
        # Respect CGT budget for gain lots (losses always allowed — they free up budget).
        room = cgt_remaining - realised_gain
        if bucket_left is not None and lot.bucket in bucket_left:
            room = min(room, bucket_left[lot.bucket])
        capped = lot_gain > 0 and lot_gain > room
        if capped:
            allowable_gain = max(0.0, room)
            if gain_per_unit > 0:
                qty = min(qty, allowable_gain / gain_per_unit)
                if whole_units:
                    qty = math.floor(qty + 1e-9)
                lot_gain = gain_per_unit * qty
            if qty <= 1e-9:
                if fifo:
                    break
                continue
        value_sold += qty * pos.price
        realised_gain += lot_gain
        qty_sold += qty
        if lot.bucket:
            if gains is not None:
                gains[lot.bucket] = gains.get(lot.bucket, 0.0) + lot_gain
            if bucket_left is not None and lot.bucket in bucket_left and lot_gain > 0:
                bucket_left[lot.bucket] -= lot_gain
        if capped and fifo:
            break
    return value_sold, realised_gain, qty_sold


def _hold_loss_lots(pos: Position, want: float, window: dict, held: list[dict], limitations: list[str],
                    say_money: Callable[[float], str] = _dollars) -> None:
    """Record what the guard kept out of a sale: the loss lots a sale of `want` would have taken."""
    if pos.price <= 0:
        return
    left, qty, loss = want / pos.price, 0.0, 0.0
    for lot in sorted(pos.lots, key=lambda l: pos.price - l.cost_per_unit):
        per_unit = lot.cost_per_unit - pos.price
        if per_unit <= 0 or left <= 1e-9:
            break
        take = min(lot.quantity, left)
        qty, loss, left = qty + take, loss + take * per_unit, left - take
    if qty <= 1e-9:
        return
    qty = _floor_qty(qty)
    held.append({"side": "sell", "symbol": pos.symbol, "instrument_id": pos.instrument_id,
                 "asset_class": pos.asset_class, "quantity": qty, "loss": round(loss, 2), **window})
    limitations.append(window.get("say") or (
        f"{qty:g} {pos.symbol} at a loss held back: it was bought on {window['on']} in "
        f"{window['account']}, so selling now would disallow about {say_money(loss)} of loss. "
        f"Sellable at a loss from {window['clear_on']}."
    ))


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
    wash: WashSaleGuard | None = None,
    # What the market fixes. `fifo`: the law picks the lot, so the estimate has to pick the same one.
    # `whole_units`: a demat holds whole shares. `bucket_budgets`: a gain budget per tax bucket.
    fifo: bool = False,
    whole_units: bool = False,
    bucket_budgets: dict[str, float] | None = None,
    say_money: Callable[[float], str] = _dollars,
    # What an IPS holds the mandate to, as fractions of its value: cash no buy may spend, and the most
    # any one instrument may be after a buy. A breach already on the book is reported, not sold down.
    min_cash: float | None = None,
    max_position: float | None = None,
) -> RebalanceResult:
    total_value = sum(p.market_value for p in positions) + cash
    if total_value <= 0:
        return RebalanceResult(False, 0, drift_band, {}, target_weights, {}, [], 0, 0, 0, 0, [], [], 0)
    wash = wash or WashSaleGuard()
    held: list[dict] = []
    bucket_left = dict(bucket_budgets) if bucket_budgets else None
    gains_by_bucket: dict[str, float] = {}

    def _carried(qty: float) -> float:
        return math.floor(qty + 1e-6) if whole_units else _floor_qty(qty)

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
                barred = wash.no_sell.get(pos.instrument_id)
                if barred and barred.get("account_id") in (None, pos.account_id):
                    held.append({"side": "sell", "symbol": pos.symbol, "instrument_id": pos.instrument_id,
                                 "asset_class": pos.asset_class, "value": round(want, 2), **barred})
                    limitations.append(barred.get("say") or f"{pos.symbol} cannot be sold today.")
                    continue
                if whole_units and pos.price > 0:
                    want = math.floor(want / pos.price + 1e-9) * pos.price
                window = wash.no_loss_sale.get(pos.instrument_id)
                sale_gains: dict[str, float] = {}
                value_sold, lot_gain, qty = _sell_with_lots(
                    pos, want, cgt_remaining, no_loss=bool(window), fifo=fifo, whole_units=whole_units,
                    gains=sale_gains, bucket_left=bucket_left)
                if window:
                    _hold_loss_lots(pos, want, window, held, limitations, say_money)
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
                # Value the order the way settlement will — from the quantity actually
                # carried, not the pre-rounding figure. Otherwise the card shows one number
                # and the book moves by another.
                sell_qty = _carried(qty)
                if sell_qty <= 0:
                    continue
                for bucket, gain in sale_gains.items():
                    gains_by_bucket[bucket] = gains_by_bucket.get(bucket, 0.0) + gain
                orders.append(Order(
                    side="sell", symbol=pos.symbol, name=pos.name, instrument_id=pos.instrument_id,
                    asset_class=cls, quantity=sell_qty, est_price=round(pos.price, 4),
                    est_value=round(sell_qty * round(pos.price, 4), 2),
                    account_id=pos.account_id, custodian=pos.custodian,
                    est_realised_gain=round(lot_gain, 2), reason=reason,
                    gain_buckets={b: round(g, 2) for b, g in sale_gains.items()},
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

        # Settlement values an order as quantity * price, so the funding maths must use the
        # same figure. est_value is a display estimate and can differ from it by the
        # quantity rounding — reasoning in one while settling in the other is what left an
        # order 1.5 cents short of settling.
        def _gross(o: "Order") -> float:
            return o.quantity * o.est_price

        # Fees are per order, not a blanket percentage of the total, because of the minimum.
        proceeds = sum(_gross(o) - _fee(_gross(o)) for o in orders if o.side == "sell")
        cash_floor = (min_cash or 0.0) * total_value
        available = max(0.0, cash + proceeds - cash_floor)

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
            # model nominates for it. Either way, not one the household sold at a loss inside
            # the window: buying it back would disallow that loss.
            candidates = [p for p in positions if p.asset_class == cls and not p.excluded]
            pick = (model_instruments or {}).get(cls)
            target_pos = next((p for p in candidates if p.instrument_id not in wash.no_buy), None)
            if target_pos is None and pick is not None and pick.instrument_id not in wash.no_buy:
                target_pos = pick
            if target_pos is None and (candidates or pick is not None):
                blocked = candidates[0] if candidates else pick
                window = wash.no_buy[blocked.instrument_id]
                held.append({"side": "buy", "symbol": blocked.symbol, "instrument_id": blocked.instrument_id,
                             "asset_class": cls, "value": round(buy_value, 2), **window})
                limitations.append(window.get("say") or (
                    f"'{cls}' is {(-drift):.0%} under target, but {blocked.symbol} was sold at a loss on "
                    f"{window['on']} in {window['account']} and buying it back now would disallow that "
                    f"loss. {say_money(buy_value)} to allocate from {window['clear_on']}, or into another "
                    f"instrument - adviser to select."
                ))
                continue
            if target_pos is None:
                limitations.append(
                    f"'{cls}' is {(-drift):.0%} under target, but the model names no instrument to "
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
                    f"{say_money(buy_value)} to allocate — adviser to arrange."
                )
                continue
            if target_pos.price <= 0:
                limitations.append(
                    f"No usable price for {target_pos.symbol}, so '{cls}' cannot be bought "
                    f"— adviser to select manually."
                )
                continue
            # A model pick nobody holds yet has no account of its own. It goes where the mandate's
            # other money is: an order with no account cannot be placed at all.
            if not target_pos.account_id and positions:
                target_pos = replace(target_pos, account_id=positions[0].account_id,
                                     custodian=positions[0].custodian)
            capped = False
            if max_position is not None:
                already = sum(p.market_value for p in positions if p.instrument_id == target_pos.instrument_id)
                room = max(0.0, max_position * total_value - already)
                if buy_value > room:
                    capped = True
                    buy_value = room
                    limitations.append(
                        f"'{cls}' is {(-drift):.0%} under target, but {target_pos.symbol} is limited to "
                        f"{max_position:.0%} of this mandate. {say_money(wanted - room)} to allocate to "
                        f"another instrument - adviser to select."
                    )
            # Size in quantity and then price it the way settlement will, stepping down a
            # tick if the rounded quantity does not fit. Checking the value alone is not
            # enough: the order carries the quantity, and that is what gets valued.
            price = target_pos.price
            qty = _carried(buy_value / price)
            tick = 1 if whole_units else 1 / (10 ** _QTY_DP)
            while qty > 0 and (qty * price) + _fee(qty * price) > available + 1e-9:
                qty = _carried(qty - tick)
            if qty <= 0:
                if not capped:
                    underfunded.append((cls, wanted, 0.0))
                continue
            buy_value = qty * price
            orders.append(Order(
                side="buy", symbol=target_pos.symbol, name=target_pos.name,
                instrument_id=target_pos.instrument_id, asset_class=cls, quantity=qty,
                est_price=round(target_pos.price, 4), est_value=round(buy_value, 2),
                account_id=target_pos.account_id, custodian=target_pos.custodian,
                reason="Top up under-weight",
            ))
            available -= (buy_value + _fee(buy_value))
            # Flooring the quantity always leaves a buy a few cents under what was wanted. That is
            # rounding, not a shortfall, and reporting it told an adviser they were "$0 short".
            if not capped and wanted - buy_value >= _SHORTFALL_WORTH_SAYING:
                underfunded.append((cls, wanted, buy_value))

    # Say what could not be funded. Silently buying less than the target would look like a
    # completed rebalance that quietly left the portfolio off-model.
    for cls, wanted, funded in underfunded:
        shortfall = wanted - funded
        limitations.append(
            f"'{cls}' needs {say_money(wanted)} to reach target but only {say_money(funded)} could be "
            f"funded from cash and sale proceeds — {say_money(shortfall)} short. Raising more "
            f"would exceed the CGT budget or the available cash."
        )
    if min_cash and underfunded:
        limitations.append(f"Buys stop at {say_money(min_cash * total_value)} of cash, the floor in the IPS.")

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
        wash_sale_held=held,
        gains_by_bucket={b: round(g, 2) for b, g in gains_by_bucket.items()},
    )
