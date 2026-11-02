"""The trade blotter: what was traded, for whom, on whose say, and when it settles (L100 §6.1).

The book takes a trade on its trade date, as advisers' systems do, and the settlement date is worked out from the
market: T+1 or T+2 on weekdays, with public holidays not modelled. A confirmation is a reference to an order and what
it filled at. On the paper venue that fill is a record, no market was reached, and the row says so.

Orders in one instrument on one side on one day, across accounts, are shown as a block, with the average price each
account was allocated at, so a fair allocation can be seen. Every order here says whether a proposal or a person's
direct order raised it, because the two are supervised differently.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.billing import _mandate_households
from app.conduit.execution import venue_for_firm
from app.core.db import utcnow
from app.models.graph import Account, Household, Mandate
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.models.trading import Order

DEFAULT_DAYS = 30
SHOWN = 200


def settlement_date(trade_day: date, market) -> date:
    """Trade date plus the market's settlement days, counting weekdays."""
    left, day = market.settle_days, trade_day
    while left:
        day += timedelta(days=1)
        if day.weekday() < 5:
            left -= 1
    return day


def confirmation(order: Order, trade_day: date) -> str:
    return f"TC-{trade_day:%Y%m%d}-{str(order.id)[:6].upper()}"


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None, days: int = DEFAULT_DAYS) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    market = markets.for_firm(firm)
    since = utcnow() - timedelta(days=days)
    rows = (await session.execute(select(Order).where(Order.firm_id == firm_id, Order.created_at >= since).order_by(Order.created_at.desc()).limit(SHOWN))).scalars().all()
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all()}
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()}
    paper = not venue_for_firm(firm).reaches_market
    trades, groups = [], defaultdict(list)
    for o in rows:
        account = accounts.get(o.account_id)
        home = owners.get(account.mandate_id) if account else None
        if household_id is not None and home != household_id:
            continue
        inst = instruments.get(o.instrument_id)
        day = firm_time.today(firm, o.created_at)
        filled = float(o.filled_quantity or 0)
        price = float(o.avg_fill_price or 0)
        gross, fees = round(filled * price, 2), float(o.fees or 0)
        side = str(o.side)
        # With no market there is no rule to work a date from, and the row says nothing rather than a guess.
        settles = settlement_date(day, market) if filled and market else None
        row = {"id": str(o.id), "confirmation": confirmation(o, day) if filled else None, "trade_date": day.isoformat(), "settles_on": settles.isoformat() if settles else None,
               "settled": bool(settles and settles <= today), "side": side, "symbol": inst.symbol if inst else "?", "quantity": filled or float(o.quantity), "price": price, "gross": gross, "fees": fees,
               "net": round(gross + fees if side == "buy" else gross - fees, 2), "realised_gain": float(o.realised_gain or 0) if side == "sell" and filled else None, "status": str(o.status), "rejected_reason": o.rejected_reason,
               "account": account.name if account else None, "client": names.get(home), "household_id": str(home) if home else None, "source": "proposal" if o.recommendation_id else "direct", "reason": o.reason, "venue": o.venue}
        trades.append(row)
        if filled:
            groups[(day, row["symbol"], side)].append(row)
    blocks = []
    for (day, symbol, side), members in groups.items():
        if len({m["account"] for m in members}) < 2:
            continue
        units = sum(m["quantity"] for m in members)
        blocks.append({"trade_date": day.isoformat(), "symbol": symbol, "side": side, "accounts": len({m["account"] for m in members}), "units": units,
                       "average_price": round(sum(m["quantity"] * m["price"] for m in members) / units, 6) if units else 0.0, "same_price": len({m["price"] for m in members}) == 1})
    return {"trades": trades, "blocks": sorted(blocks, key=lambda b: b["trade_date"], reverse=True), "paper_venue": paper, "as_of": today.isoformat(), "days": days,
            "settle_days": market.settle_days if market else None, "household_id": str(household_id) if household_id else None,
            "summary": {"trades": len(trades), "filled": sum(1 for t in trades if t["confirmation"]), "unsettled": sum(1 for t in trades if t["confirmation"] and not t["settled"]),
                        "direct": sum(1 for t in trades if t["source"] == "direct"), "rejected": sum(1 for t in trades if t["status"] == "rejected"), "blocks": len(blocks)}}
