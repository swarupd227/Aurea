"""Sleeve netting and reconciliation (L200-3 §6.1-6.2).

`net_intents` is pure — no I/O — deliberately, the same discipline `rebalancing.py` follows
for the single-model engine: it takes each sleeve's already-computed trade intent (however
produced — today, by running the existing `rebalancing.rebalance` engine once per sleeve
against that sleeve's own SleeveHolding-scoped positions and model) and crosses conflicting
intents on the same instrument within one account into a single net order, the way §6.2's
worked example turns 700 shares of intent into one 100-share trade.

`reconcile` is the DB-backed nightly check §6.1 requires: every sleeve's attributed
quantity for a holding must sum to that holding's actual flat quantity."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.portfolio import Holding
from app.models.sleeves import Sleeve, SleeveHolding

_EPS = 1e-9


@dataclass
class SleeveIntent:
    sleeve_id: str
    account_id: str
    instrument_id: str
    symbol: str
    side: str          # buy | sell
    quantity: float
    price: float = 0.0


@dataclass
class NetOrder:
    account_id: str
    instrument_id: str
    symbol: str
    side: str | None   # None when fully crossed — nothing needs to trade externally
    quantity: float     # the net external quantity that must actually trade
    gross_buy_quantity: float
    gross_sell_quantity: float
    crossed_quantity: float   # shares matched internally, never hitting the market
    sleeve_allocations: list[dict] = field(default_factory=list)


def net_intents(intents: list[SleeveIntent]) -> list[NetOrder]:
    """Group sleeve intents by (account, instrument) and net them.

    Every sleeve on the smaller-magnitude side gets its intent fully satisfied by internal
    crossing (§6.2's "advisory-only crossing inside one client's account is the easy case").
    Sleeves on the larger side split the net external order pro-rata by their own intent —
    the fairness rule §6.2 requires ("no account may be favoured by fill assignment")."""
    by_key: dict[tuple[str, str], list[SleeveIntent]] = {}
    for i in intents:
        if i.side not in ("buy", "sell"):
            raise ValueError(f"Unknown side '{i.side}' on sleeve {i.sleeve_id}.")
        by_key.setdefault((i.account_id, i.instrument_id), []).append(i)

    orders: list[NetOrder] = []
    for (account_id, instrument_id), group in by_key.items():
        buys = sum(g.quantity for g in group if g.side == "buy")
        sells = sum(g.quantity for g in group if g.side == "sell")
        crossed = min(buys, sells)
        net = buys - sells

        if abs(net) < _EPS:
            side, qty = None, 0.0
        elif net > 0:
            side, qty = "buy", net
        else:
            side, qty = "sell", -net

        allocations = []
        for g in group:
            gross = buys if g.side == "buy" else sells
            crossed_share = (g.quantity * crossed / gross) if gross > _EPS else 0.0
            external_share = g.quantity - crossed_share
            allocations.append({
                "sleeve_id": g.sleeve_id, "side": g.side, "intent_quantity": g.quantity,
                "crossed_quantity": round(crossed_share, 6),
                "external_quantity": round(external_share, 6),
            })

        orders.append(NetOrder(
            account_id=account_id, instrument_id=instrument_id, symbol=group[0].symbol,
            side=side, quantity=round(qty, 6),
            gross_buy_quantity=round(buys, 6), gross_sell_quantity=round(sells, 6),
            crossed_quantity=round(crossed, 6), sleeve_allocations=allocations,
        ))
    return orders


async def reconcile(session: AsyncSession, account_id: uuid.UUID) -> dict:
    """Every sleeve's attributed quantity for a holding must sum to the holding's own flat
    quantity — the check that keeps the logical partition honest against custodian truth."""
    holdings = (await session.execute(
        select(Holding).where(Holding.account_id == account_id)
    )).scalars().all()
    sleeve_holdings = (await session.execute(
        select(SleeveHolding).join(Sleeve, Sleeve.id == SleeveHolding.sleeve_id)
        .where(Sleeve.account_id == account_id)
    )).scalars().all()

    attributed: dict[uuid.UUID, float] = {}
    for sh in sleeve_holdings:
        attributed[sh.holding_id] = attributed.get(sh.holding_id, 0.0) + float(sh.quantity)

    breaks = []
    for h in holdings:
        attributed_qty = attributed.get(h.id, 0.0)
        delta = float(h.quantity) - attributed_qty
        if abs(delta) > 1e-4:
            breaks.append({
                "holding_id": str(h.id), "instrument_id": str(h.instrument_id),
                "flat_quantity": float(h.quantity), "attributed_quantity": round(attributed_qty, 6),
                "unattributed_quantity": round(delta, 6),
            })

    # A sleeve attribution pointing at a holding that no longer exists (or isn't in this
    # account) is a break in its own right — orphaned attribution, not just a shortfall.
    holding_ids = {h.id for h in holdings}
    orphaned = [str(sh.id) for sh in sleeve_holdings if sh.holding_id not in holding_ids]

    return {
        "account_id": str(account_id), "holdings_checked": len(holdings),
        "breaks": breaks, "orphaned_attributions": orphaned,
        "clean": not breaks and not orphaned,
    }
