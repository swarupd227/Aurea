"""Money out of a client's account, and money held back from being invested (L100 §5.3, §5.7, §5.8).

A payment is requested by one person and recorded as paid by another, from the custodian's confirmation, and only
then does the cash go down. Custody is mocked, so nothing here moves money. A third-party payee is the fraud vector,
so it needs a callback on the number on file before it can be recorded, the same rule transfers follow. The cash for
an open request is held back, so neither the rebalancer nor a direct order spends it. If there is not enough, the
request stands and the shortfall is shown: raising it is a decision about what to sell, and that is a person's.

A required minimum distribution is worked out by `registration` from the account's value, and what has been taken
this year is read from the ledger, so the gap to 31 December can be seen. Phasing new money in holds the tranches
that have not reached their date out of every purchase.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, registration
from app.aurea_core.billing import _mandate_households
from app.models.cash_movement import CASH_KINDS, FREQUENCIES, CashRequest, PhaseInPlan, StandingInstruction
from app.models.graph import Account, Household, Mandate, Person
from app.models.portfolio import Transaction
from app.models.tenant import Firm
from app.provenance import ledger


class CashMovementError(ValueError):
    """This cannot be recorded, with the reason."""


def _money(value, name: str) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise CashMovementError(f"{name} must be a number.")
    if amount <= 0:
        raise CashMovementError(f"{name} must be above zero.")
    return round(amount, 2)


def _add_months(day: date, months: int) -> date:
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return date(year, month, min(day.day, 28))


def tranches_left(plan: PhaseInPlan, today: date) -> int:
    """Those whose date has not come. The first goes on the start date."""
    done = 0
    while done < plan.tranches and _add_months(plan.start_on, done * plan.every_months) <= today:
        done += 1
    return plan.tranches - done


async def _account(session: AsyncSession, account_id: uuid.UUID, firm_id: uuid.UUID) -> Account:
    account = await session.get(Account, account_id)
    if account is None or account.firm_id != firm_id:
        raise CashMovementError("Account not found.")
    if account.status == "closed":
        raise CashMovementError(f"{account.name} is closed.")
    return account


async def _open_requests(session: AsyncSession, account_ids: list[uuid.UUID]) -> list[CashRequest]:
    if not account_ids:
        return []
    return (await session.execute(select(CashRequest).where(CashRequest.account_id.in_(account_ids), CashRequest.status == "requested"))).scalars().all()


async def held_back(session: AsyncSession, firm: Firm, mandate: Mandate) -> dict:
    """Cash this mandate may not spend on a purchase, and why: open payments out, money still to be phased in, and deposits on hold."""
    accounts = (await session.execute(select(Account.id).where(Account.mandate_id == mandate.id))).scalars().all()
    reserved = sum(float(r.amount) for r in await _open_requests(session, list(accounts)))
    today = firm_time.today(firm)
    plans = (await session.execute(select(PhaseInPlan).where(PhaseInPlan.mandate_id == mandate.id, PhaseInPlan.ended_on.is_(None)))).scalars().all()
    phasing = sum(float(p.amount) * tranches_left(p, today) / p.tranches for p in plans)
    from app.aurea_core import money_in

    deposits = await money_in.holds_for(session, firm, list(accounts))     # large deposits still inside their hold
    return {"requests": round(reserved, 2), "phasing": round(phasing, 2), "deposits": deposits, "total": round(reserved + phasing + deposits, 2)}


async def prepare_request(session: AsyncSession, account_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, amount, payee: str | None, reason: str | None,
                          third_party: bool = False, standing_id: uuid.UUID | None = None) -> dict:
    account = await _account(session, account_id, firm_id)
    if kind not in CASH_KINDS:
        raise CashMovementError(f"The kind must be one of {', '.join(CASH_KINDS)}.")
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    if mandate is not None and mandate.exit_hold == "deceased":
        raise CashMovementError("This client has died and no executor is on file, so nothing may be paid out.")
    from app.aurea_core import account_features
    if await account_features.active_hold(session, await session.get(Firm, firm_id), account.id):
        raise CashMovementError("A temporary hold is in force on this account, so nothing may be paid out until it ends or is released.")
    money = _money(amount, "The amount")
    to, why = (payee or "").strip(), (reason or "").strip()
    if len(to) < 2:
        raise CashMovementError("Say who it is being paid to.")
    if len(why) < 3:
        raise CashMovementError("Say what it is for.")
    if kind == "rmd" and await registration.rmd_for_account(session, account) is None:
        raise CashMovementError(f"{account.name} carries no required distribution.")
    open_now = sum(float(r.amount) for r in await _open_requests(session, [account.id]))
    cash = float(account.cash_balance or 0)
    return {"account": account, "kind": kind, "amount": money, "payee": to, "reason": why, "third_party": bool(third_party), "standing_id": standing_id,
            "cash": cash, "already_open": open_now, "short_by": round(max(money + open_now - cash, 0.0), 2)}


async def request(session: AsyncSession, account_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> CashRequest:
    ready = await prepare_request(session, account_id, firm_id=firm_id, **terms)
    row = CashRequest(firm_id=firm_id, account_id=account_id, kind=ready["kind"], amount=ready["amount"], payee=ready["payee"], third_party=ready["third_party"],
                      reason=ready["reason"], requested_by=by, standing_id=ready["standing_id"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="cash_request_raised", actor=by, content={
        "request_id": str(row.id), "account_id": str(account_id), "amount": ready["amount"], "payee": row.payee, "third_party": row.third_party})
    return row


async def prepare_paid(session: AsyncSession, request_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, paid_on: date | None, source_document: str | None,
                       callback_number: str | None = None) -> dict:
    row = await session.get(CashRequest, request_id)
    if row is None or row.firm_id != firm_id:
        raise CashMovementError("Request not found.")
    if row.status != "requested":
        raise CashMovementError(f"This request is already {row.status}.")
    if row.requested_by == by:
        raise CashMovementError("The person who requested a payment cannot also record it.")
    firm = await session.get(Firm, firm_id)
    if paid_on is None:
        raise CashMovementError("Give the date the custodian paid it.")
    if paid_on > firm_time.today(firm):
        raise CashMovementError("A payment cannot be recorded before it has happened.")
    named = (source_document or "").strip()
    if len(named) < 5:
        raise CashMovementError("Say which custodian confirmation this comes from.")
    number = (callback_number or "").strip() or None
    if row.third_party and not number:
        raise CashMovementError("A third-party payment needs a callback on a recorded line, to the number on file. Give that number.")
    account = await session.get(Account, row.account_id)
    if float(row.amount) > float(account.cash_balance or 0) + 0.005:
        raise CashMovementError(f"{account.name} holds {float(account.cash_balance or 0):,.2f}, so it cannot have paid {float(row.amount):,.2f}. Raise the cash first.")
    return {"request": row, "account": account, "day": paid_on, "document": named, "callback": number}


async def record_paid(session: AsyncSession, request_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, paid_on: date | None, source_document: str | None,
                      callback_number: str | None = None) -> CashRequest:
    ready = await prepare_paid(session, request_id, firm_id=firm_id, by=by, paid_on=paid_on, source_document=source_document, callback_number=callback_number)
    row, account = ready["request"], ready["account"]
    row.status, row.paid_on, row.paid_document, row.paid_by = "paid", ready["day"], ready["document"], by
    row.callback_number, row.callback_by = ready["callback"], by if ready["callback"] else None
    account.cash_balance = float(account.cash_balance or 0) - float(row.amount)
    session.add(Transaction(firm_id=firm_id, account_id=account.id, instrument_id=None, txn_type="withdrawal", quantity=0, price=0, amount=row.amount, trade_date=ready["day"],
                            lineage={"request_id": str(row.id), "kind": row.kind, "payee": row.payee}))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="cash_payment_recorded", actor=by, content={
        "request_id": str(row.id), "amount": float(row.amount), "document": row.paid_document, "paid_on": row.paid_on.isoformat(), "callback": row.callback_number})
    return row


async def prepare_cancel(session: AsyncSession, request_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    row = await session.get(CashRequest, request_id)
    if row is None or row.firm_id != firm_id:
        raise CashMovementError("Request not found.")
    if row.status != "requested":
        raise CashMovementError(f"This request is already {row.status}.")
    why = (reason or "").strip()
    if len(why) < 3:
        raise CashMovementError("Say why it is being cancelled.")
    return {"request": row, "reason": why, "account": await session.get(Account, row.account_id)}


async def cancel(session: AsyncSession, request_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, reason: str | None) -> CashRequest:
    ready = await prepare_cancel(session, request_id, firm_id=firm_id, reason=reason)
    row = ready["request"]
    row.status, row.cancel_reason = "cancelled", ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="cash_request_cancelled", actor=by, content={"request_id": str(row.id), "reason": row.cancel_reason})
    return row


async def prepare_standing(session: AsyncSession, account_id: uuid.UUID, *, firm_id: uuid.UUID, payee: str | None, amount, frequency: str | None, first_due: date | None,
                           source_document: str | None, third_party: bool = False) -> dict:
    account = await _account(session, account_id, firm_id)
    if frequency not in FREQUENCIES:
        raise CashMovementError(f"The frequency must be one of {', '.join(FREQUENCIES)}.")
    to, named = (payee or "").strip(), (source_document or "").strip()
    if len(to) < 2:
        raise CashMovementError("Say who it pays.")
    if len(named) < 5:
        raise CashMovementError("Say which signed form this comes from.")
    if first_due is None:
        raise CashMovementError("Give the first date it is due.")
    return {"account": account, "payee": to, "amount": _money(amount, "The amount"), "frequency": frequency, "first_due": first_due, "document": named, "third_party": bool(third_party)}


async def record_standing(session: AsyncSession, account_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> StandingInstruction:
    ready = await prepare_standing(session, account_id, firm_id=firm_id, **terms)
    row = StandingInstruction(firm_id=firm_id, account_id=account_id, payee=ready["payee"], third_party=ready["third_party"], amount=ready["amount"], frequency=ready["frequency"],
                              first_due=ready["first_due"], source_document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="standing_instruction_recorded", actor=by, content={"account_id": str(account_id), "payee": row.payee, "amount": ready["amount"]})
    return row


async def prepare_end_standing(session: AsyncSession, standing_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    row = await session.get(StandingInstruction, standing_id)
    if row is None or row.firm_id != firm_id:
        raise CashMovementError("Standing instruction not found.")
    if row.ended_on is not None:
        raise CashMovementError("This instruction has already ended.")
    return {"standing": row, "account": await session.get(Account, row.account_id)}


async def end_standing(session: AsyncSession, standing_id: uuid.UUID, *, firm_id: uuid.UUID, by: str) -> StandingInstruction:
    ready = await prepare_end_standing(session, standing_id, firm_id=firm_id)
    row = ready["standing"]
    row.ended_on = firm_time.today(await session.get(Firm, firm_id))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="standing_instruction_ended", actor=by, content={"standing_id": str(row.id)})
    return row


async def prepare_phase_in(session: AsyncSession, mandate_id: uuid.UUID, *, firm_id: uuid.UUID, amount, tranches, every_months, start_on: date | None, reason: str | None) -> dict:
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.firm_id != firm_id or not mandate.is_active:
        raise CashMovementError("Mandate not found.")
    money = _money(amount, "The amount")
    try:
        parts, gap = int(tranches), int(every_months)
    except (TypeError, ValueError):
        raise CashMovementError("Give the number of stages and the months between them.")
    if not 2 <= parts <= 36:
        raise CashMovementError("Phasing takes between 2 and 36 stages.")
    if not 1 <= gap <= 12:
        raise CashMovementError("The gap must be between 1 and 12 months.")
    firm = await session.get(Firm, firm_id)
    day = start_on or firm_time.today(firm)
    why = (reason or "").strip()
    if len(why) < 3:
        raise CashMovementError("Say why it is being phased.")
    accounts = (await session.execute(select(Account).where(Account.mandate_id == mandate_id))).scalars().all()
    cash = sum(float(a.cash_balance or 0) for a in accounts)
    if money > cash + 0.005:
        raise CashMovementError(f"This mandate holds {cash:,.2f} in cash, so it cannot phase in {money:,.2f}.")
    if (await session.execute(select(PhaseInPlan.id).where(PhaseInPlan.mandate_id == mandate_id, PhaseInPlan.ended_on.is_(None)).limit(1))).first():
        raise CashMovementError("This mandate already has a plan running. End it first.")
    return {"mandate": mandate, "amount": money, "tranches": parts, "every": gap, "day": day, "reason": why, "each": round(money / parts, 2),
            "last_on": _add_months(day, (parts - 1) * gap)}


async def start_phase_in(session: AsyncSession, mandate_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> PhaseInPlan:
    ready = await prepare_phase_in(session, mandate_id, firm_id=firm_id, **terms)
    row = PhaseInPlan(firm_id=firm_id, mandate_id=mandate_id, amount=ready["amount"], tranches=ready["tranches"], every_months=ready["every"], start_on=ready["day"], reason=ready["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="phase_in_started", actor=by, content={"mandate_id": str(mandate_id), "amount": ready["amount"], "tranches": row.tranches})
    return row


async def prepare_end_phase_in(session: AsyncSession, plan_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    row = await session.get(PhaseInPlan, plan_id)
    if row is None or row.firm_id != firm_id:
        raise CashMovementError("Plan not found.")
    if row.ended_on is not None:
        raise CashMovementError("This plan has already ended.")
    firm = await session.get(Firm, firm_id)
    left = tranches_left(row, firm_time.today(firm))
    return {"plan": row, "mandate": await session.get(Mandate, row.mandate_id), "released": round(float(row.amount) * left / row.tranches, 2)}


async def end_phase_in(session: AsyncSession, plan_id: uuid.UUID, *, firm_id: uuid.UUID, by: str) -> PhaseInPlan:
    ready = await prepare_end_phase_in(session, plan_id, firm_id=firm_id)
    row = ready["plan"]
    row.ended_on = firm_time.today(await session.get(Firm, firm_id))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="phase_in_ended", actor=by, content={"plan_id": str(row.id)})
    return row


async def _rmd_rows(session: AsyncSession, accounts: list[Account], today: date) -> list[dict]:
    rows = []
    for a in accounts:
        due = await registration.rmd_for_account(session, a)
        if not due or due.get("status") != "required":
            continue
        taken = sum(float(t.amount) for t in (await session.execute(select(Transaction).where(
            Transaction.account_id == a.id, Transaction.txn_type == "withdrawal", Transaction.trade_date >= date(today.year, 1, 1)))).scalars().all()
            if (t.lineage or {}).get("kind") == "rmd")
        rows.append({"account_id": str(a.id), "account": a.name, "required": due["amount"], "taken": round(taken, 2), "left": round(max(due["amount"] - taken, 0), 2),
                     "by": date(today.year, 12, 31).isoformat(), "days_left": (date(today.year, 12, 31) - today).days})
    return rows


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True)))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    accounts = [a for a in (await session.execute(select(Account).where(Account.firm_id == firm_id, Account.status == "active"))).scalars().all()
                if a.mandate_id in owners and (household_id is None or owners[a.mandate_id] == household_id)]
    home = {a.id: names.get(owners.get(a.mandate_id)) for a in accounts}
    by_id = {a.id: a for a in accounts}
    ids = list(by_id)
    requests = (await session.execute(select(CashRequest).where(CashRequest.account_id.in_(ids)).order_by(CashRequest.created_at.desc()).limit(60))).scalars().all() if ids else []
    reserved: dict = defaultdict(float)
    for r in requests:
        if r.status == "requested":
            reserved[r.account_id] += float(r.amount)
    standing = (await session.execute(select(StandingInstruction).where(StandingInstruction.account_id.in_(ids), StandingInstruction.ended_on.is_(None)))).scalars().all() if ids else []
    plans = (await session.execute(select(PhaseInPlan).where(PhaseInPlan.mandate_id.in_(list(owners)), PhaseInPlan.ended_on.is_(None)))).scalars().all()

    def next_due(s: StandingInstruction) -> date:
        day, step = s.first_due, FREQUENCIES[s.frequency]
        while day < today:
            day = _add_months(day, step)
        return day

    rmds = await _rmd_rows(session, accounts, today)
    return {
        "as_of": today.isoformat(), "household_id": str(household_id) if household_id else None,
        "requests": [{"id": str(r.id), "account_id": str(r.account_id), "account": by_id[r.account_id].name, "client": home.get(r.account_id), "kind": r.kind, "amount": float(r.amount), "payee": r.payee,
                      "third_party": r.third_party, "reason": r.reason, "status": r.status, "requested_by": r.requested_by, "paid_on": r.paid_on.isoformat() if r.paid_on else None,
                      "short_by": round(max(reserved[r.account_id] - float(by_id[r.account_id].cash_balance or 0), 0), 2) if r.status == "requested" else None} for r in requests],
        "standing": [{"id": str(s.id), "account": by_id[s.account_id].name, "client": home.get(s.account_id), "payee": s.payee, "third_party": s.third_party, "amount": float(s.amount),
                      "frequency": s.frequency, "next_due": next_due(s).isoformat(), "document": s.source_document} for s in standing],
        "phasing": [{"id": str(p.id), "mandate_id": str(p.mandate_id), "client": names.get(owners.get(p.mandate_id)), "amount": float(p.amount), "tranches": p.tranches, "left": tranches_left(p, today),
                     "held_back": round(float(p.amount) * tranches_left(p, today) / p.tranches, 2), "every_months": p.every_months, "reason": p.reason} for p in plans
                    if household_id is None or owners.get(p.mandate_id) == household_id],
        "rmds": rmds,
        "summary": {"open": sum(1 for r in requests if r.status == "requested"), "value_open": round(sum(reserved.values()), 2), "short": sum(1 for a, v in reserved.items() if v > float(by_id[a].cash_balance or 0)),
                    "standing": len(standing), "rmd_left": round(sum(r["left"] for r in rmds), 2)}}
