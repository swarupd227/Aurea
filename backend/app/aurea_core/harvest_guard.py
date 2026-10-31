"""What the rebalancer may not do with a loss in India, which is not what it may not do in the US.

India has no wash-sale rule, no 30-day window and no deferred loss, so aurea_core/wash_sale.py does
not apply and porting it would cost a client valid losses. What India has is narrower, and this is
that:

  - A same-day sale and rebuy of one instrument in one demat. The position nets, it is not settled
    by delivery, and a loss on it risks being speculative. Hard block. The netting mechanics were
    not confirmed from a primary source, so this is the cautious reading.
  - A loss inside a bonus-stripping window (s.175(9)): bought within three months before the record
    date, sold within nine months after, and the bonus units kept. The loss is ignored, so the
    solver does not count on it. Dividend stripping needs exempt income, which residents now rarely
    have, so it is not checked here.
  - A rebuy after a loss, in any account of the household. Nothing bars it and no case tests it, so
    it is a flag an adviser sees and never a statement that it is safe. 30 days is a review period
    this product chose, with no meaning in Indian law.
  - GAAR: a tracker of the year's tax benefit against Rs 3 crore, per taxpayer. Above it, escalate.

Each answer travels with the proposal as evidence, and approval runs it again.
"""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import markets, tax_india, wash_sale
from app.models.corporate_actions import CorporateAction
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, TaxLot, Transaction
from app.models.tenant import Firm

REVIEW_WINDOW_DAYS = 30


def applies(firm: Firm, household_values: dict | None) -> bool:
    market = markets.of_household(firm, household_values)
    return bool(market and market.code == "IN")


def _next_weekday(day: date) -> date:
    following = day + timedelta(days=1)
    while following.weekday() >= 5:
        following += timedelta(days=1)
    return following


async def guard(session: AsyncSession, firm: Firm, mandate: Mandate, *, today: date) -> dict:
    """What the book says about this mandate today, as the solver needs it. JSON-able."""
    out: dict = {"applies": True, "no_sell": {}, "no_buy": {}, "no_loss_sale": {}, "rebuy_watch": {}}
    accounts = (await session.execute(select(Account).where(Account.mandate_id == mandate.id))).scalars().all()
    names = {a.id: a.name for a in accounts}
    if not names:
        return out
    clear_on = _next_weekday(today).isoformat()

    trades = (await session.execute(
        select(Transaction).where(Transaction.account_id.in_(names), Transaction.trade_date == today,
                                  Transaction.txn_type.in_(("buy", "sell")))
    )).scalars().all()
    for t in trades:
        if not t.instrument_id:
            continue
        window = {"on": today.isoformat(), "account": names[t.account_id], "account_id": str(t.account_id),
                  "clear_on": clear_on, "rule": "same_day"}
        if str(t.txn_type) == "sell":
            out["no_buy"][str(t.instrument_id)] = {
                **window, "say": f"Sold today in {names[t.account_id]}, so buying it back today would net "
                                 f"against that sale. It can be bought from {clear_on}."}
        else:
            out["no_sell"][str(t.instrument_id)] = {
                **window, "say": f"Bought today in {names[t.account_id]}, so selling it today would net "
                                 f"against that purchase. It can be sold from {clear_on}."}

    # Bonus stripping: a purchase in the three months before a record date, the nine months after it.
    bonuses = (await session.execute(
        select(CorporateAction).where(CorporateAction.firm_id == firm.id, CorporateAction.action_type == "bonus_issue",
                                      CorporateAction.record_date.is_not(None))
    )).scalars().all()
    for action in bonuses:
        record = action.record_date
        if not record <= today <= tax_india.add_months(record, 9):
            continue
        lots = (await session.execute(
            select(TaxLot, Holding).join(Holding, Holding.id == TaxLot.holding_id).where(
                Holding.account_id.in_(names), Holding.instrument_id == action.instrument_id,
                TaxLot.acquired_on >= tax_india.add_months(record, -3), TaxLot.acquired_on < record)
        )).all()
        if lots:
            clears = tax_india.add_months(record, 9) + timedelta(days=1)
            holding = lots[0][1]
            out["no_loss_sale"][str(action.instrument_id)] = {
                "on": record.isoformat(), "account": names[holding.account_id],
                "account_id": str(holding.account_id), "clear_on": clears.isoformat(), "rule": "bonus_stripping",
                "say": f"Bought within three months before a bonus record date ({record.isoformat()}) in "
                       f"{names[holding.account_id]}, so a loss on a sale before {clears.isoformat()} would be "
                       f"ignored under s.175(9). Held back.",
            }

    # A loss taken anywhere in the household, read as a review period rather than a rule.
    household_id = await wash_sale.household_of(session, mandate)
    family = await wash_sale._household_accounts(session, mandate, household_id)
    family_names = {a.id: a.name for a in family}
    since = today - timedelta(days=REVIEW_WINDOW_DAYS)
    sold = (await session.execute(
        select(Transaction).where(Transaction.account_id.in_(family_names), Transaction.trade_date >= since,
                                  Transaction.trade_date <= today, Transaction.txn_type == "sell")
    )).scalars().all() if family_names else []
    for t in sold:
        gain = float((t.lineage or {}).get("realised_gain") or 0)
        if t.instrument_id and gain < 0:
            out["rebuy_watch"][str(t.instrument_id)] = {
                "on": t.trade_date.isoformat(), "account": family_names[t.account_id], "loss": round(gain, 2)}
    return out


def conflicts(order_set: list[dict], rules: dict) -> list[dict]:
    """Orders the guard now forbids. A proposal can wait for its approval while the book moves."""
    if not rules.get("applies"):
        return []
    out = []
    for o in order_set or []:
        iid = str(o.get("instrument_id"))
        row = None
        if o.get("side") == "sell":
            row = rules["no_sell"].get(iid)
            if row is None and (o.get("est_realised_gain") or 0) < 0:
                row = rules["no_loss_sale"].get(iid)
        elif o.get("side") == "buy":
            row = rules["no_buy"].get(iid)
        if row and row.get("account_id") in (None, str(o.get("account_id"))):
            out.append({"side": o.get("side"), "symbol": o.get("symbol"), **row})
    return out


def flags(order_set: list[dict], rules: dict) -> list[dict]:
    """What an adviser should see and decide on: buying back what the household just sold at a loss."""
    if not rules.get("applies"):
        return []
    return [{"kind": "rebuy_after_loss", "symbol": o.get("symbol"), **rules["rebuy_watch"][str(o.get("instrument_id"))]}
            for o in order_set or []
            if o.get("side") == "buy" and str(o.get("instrument_id")) in rules["rebuy_watch"]]
