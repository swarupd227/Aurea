"""Wash sales (IRC §1091) as a constraint on what the rebalancer proposes, not a report afterwards.

A loss is disallowed if the same security is bought within 30 days either side of the sale, in any
account of the household - a spouse's account or an IRA counts. The tax engine could report this and
the compliance rule could spot it inside a single order set, but the solver knew nothing of either,
so it could propose the very sale it would then flag. This reads what the household actually did in
the window and tells the solver two things: which instruments it should not sell at a loss, and
which it should not buy back.

It is a US rule, so it binds a household taxed in the US and nobody else: enforcing it on an NZ
client would cost them a legitimate harvest for no benefit. The jurisdiction is the household's own
if it states one, otherwise the firm's - the same reading tax_intelligence.for_household makes.

"Substantially identical" is read as the same instrument. Two funds tracking one index are not
matched; that takes a judgement this module does not pretend to make.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, TaxLot, Transaction
from app.models.tenant import Firm

WINDOW_DAYS = 30
_APPLIES_IN = ("US", "USA", "UNITED STATES")


def applies(jurisdiction: str | None) -> bool:
    return (jurisdiction or "").strip().upper() in _APPLIES_IN


async def household_of(session: AsyncSession, mandate: Mandate) -> uuid.UUID | None:
    owner = None
    if mandate.person_id:
        owner = await session.get(Person, mandate.person_id)
    elif mandate.entity_id:
        owner = await session.get(LegalEntity, mandate.entity_id)
    return owner.household_id if owner else None


async def _household_accounts(session: AsyncSession, mandate: Mandate, household_id: uuid.UUID | None) -> list[Account]:
    if household_id is None:
        return (await session.execute(select(Account).where(Account.mandate_id == mandate.id))).scalars().all()
    return (await session.execute(
        select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
            Mandate.person_id.in_(select(Person.id).where(Person.household_id == household_id))
            | Mandate.entity_id.in_(select(LegalEntity.id).where(LegalEntity.household_id == household_id)))
    )).scalars().all()


def _note(found: dict, instrument_id, *, on: date, account: str) -> None:
    """Keep the latest event per instrument: it is the one that decides when the window closes."""
    key, row = str(instrument_id), found.get(str(instrument_id))
    if row is None or on.isoformat() > row["on"]:
        found[key] = {"on": on.isoformat(), "account": account,
                      "clear_on": (on + timedelta(days=WINDOW_DAYS + 1)).isoformat()}


async def guard(session: AsyncSession, firm: Firm, mandate: Mandate, *, today: date | None = None) -> dict:
    """What the household did in the window, as the solver needs it. JSON-able: it travels through
    the agent's sensed state and onto the proposal."""
    today = today or utcnow().date()
    household_id = await household_of(session, mandate)
    household = await session.get(Household, household_id) if household_id else None
    jurisdiction = ((household.values or {}).get("jurisdiction") if household else None) or firm.jurisdiction
    out = {"applies": applies(jurisdiction), "jurisdiction": jurisdiction, "no_loss_sale": {}, "no_buy": {}}
    if not out["applies"]:
        return out

    accounts = await _household_accounts(session, mandate, household_id)
    names = {a.id: a.name for a in accounts}
    if not names:
        return out
    since = today - timedelta(days=WINDOW_DAYS)

    # Bought in the window: settled trades, and lots that came from anywhere else (a transfer in, a
    # seeded book). A lot sold since still counts as a purchase, which is why trades are read too.
    lots = (await session.execute(
        select(TaxLot, Holding).join(Holding, Holding.id == TaxLot.holding_id)
        .where(Holding.account_id.in_(names), TaxLot.acquired_on >= since, TaxLot.acquired_on <= today)
    )).all()
    for lot, holding in lots:
        _note(out["no_loss_sale"], holding.instrument_id, on=lot.acquired_on, account=names[holding.account_id])

    txns = (await session.execute(
        select(Transaction).where(Transaction.account_id.in_(names), Transaction.trade_date >= since,
                                  Transaction.trade_date <= today, Transaction.txn_type.in_(("buy", "sell")))
    )).scalars().all()
    for t in txns:
        if not t.instrument_id:
            continue
        if str(t.txn_type) == "buy":
            _note(out["no_loss_sale"], t.instrument_id, on=t.trade_date, account=names[t.account_id])
        elif float((t.lineage or {}).get("realised_gain") or 0) < 0:
            _note(out["no_buy"], t.instrument_id, on=t.trade_date, account=names[t.account_id])
    return out


def conflicts(order_set: list[dict], rules: dict) -> list[dict]:
    """Orders in a set that the window now forbids. A proposal can sit for days before it is
    approved, and a purchase in another account may have happened in between."""
    if not rules.get("applies"):
        return []
    out = []
    for o in order_set or []:
        iid = str(o.get("instrument_id"))
        if o.get("side") == "sell" and (o.get("est_realised_gain") or 0) < 0 and iid in rules["no_loss_sale"]:
            out.append({"side": "sell", "symbol": o.get("symbol"), **rules["no_loss_sale"][iid]})
        elif o.get("side") == "buy" and iid in rules["no_buy"]:
            out.append({"side": "buy", "symbol": o.get("symbol"), **rules["no_buy"][iid]})
    return out
