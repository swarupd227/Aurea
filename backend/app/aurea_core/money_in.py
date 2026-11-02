"""Money coming in (L200-1 §6.3).

A bank account is verified before money is pulled from it: by micro-deposits the client confirmed, by an instant
check, or from a voided cheque or statement. Its owner is compared with the people in the household; anyone else is a
third party, and is verified only after a callback, as a third-party payment out is. An ACH deposit comes only from a
verified link.

A deposit is recorded from the custodian's credit. One at or over the firm's threshold is held for a number of
business days before it may be spent, and the hold is cash the rebalancer and a direct order leave alone, through
`cash_movement.held_back`. A return is recorded with its NACHA code; an unauthorized one (R05, R07, R10) revokes the
link, and a return after the money became available says the cash may already have been invested. The shortfall is
shown, not solved: what to sell is a person's decision. Nothing here writes cash: the custodian's ledger does.

A standing instruction is revalidated from the client's confirmation each year, with a callback where it pays a
third party, and one past due is listed. The thresholds are the firm's; until it sets its own the platform's are
used, and the view says which.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.aurea_core.billing import _mandate_households
from app.models.cash_movement import StandingInstruction
from app.models.graph import Account, Household, Mandate, Person
from app.models.money_in import BankLink, IncomingDeposit, InstructionCheck
from app.models.tenant import Firm
from app.provenance import ledger

METHODS = ("micro_deposit", "instant", "voided_cheque")
DEPOSIT_METHODS = ("ach", "wire", "check")
RETURN_CODES = {
    "R01": "Insufficient funds", "R02": "Account closed", "R03": "No account, or unable to locate it", "R04": "Invalid account number",
    "R05": "Unauthorized debit to a consumer account", "R07": "Authorization revoked by the customer", "R08": "Payment stopped",
    "R09": "Uncollected funds", "R10": "Customer advises not authorized", "R16": "Account frozen", "R20": "Non-transaction account",
    "R29": "Corporate customer advises not authorized",
}
UNAUTHORIZED = {"R05", "R07", "R10", "R29"}
DEFAULTS = {"deposit_hold_threshold": 25000, "deposit_hold_days": {"ach": 5, "check": 7, "wire": 0}, "instruction_revalidate_months": 12}


class MoneyInError(ValueError):
    """This cannot be recorded, with the reason."""


def policy(firm: Firm) -> dict:
    s = (firm.settings or {}).get("money_in") or {}
    return {k: s.get(k, v) for k, v in DEFAULTS.items()} | {"set_by_firm": bool(s)}


def business_days_after(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _tokens(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) > 1 and w not in ("mr", "mrs", "ms", "dr", "and", "the")}


async def _household_names(session: AsyncSession, firm_id: uuid.UUID, account: Account) -> tuple[uuid.UUID | None, list[str]]:
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    hid = (await _mandate_households(session, firm_id, [mandate])).get(mandate.id) if mandate else None
    people = (await session.execute(select(Person.full_name).where(Person.household_id == hid))).scalars().all() if hid else []
    return hid, list(people)


def first_party(owner: str, names: list[str]) -> bool:
    """Every word of the bank account's owner, bar initials, is in one person's name in the household."""
    want = _tokens(owner)
    return bool(want) and any(want <= _tokens(n) for n in names)


async def _account(session: AsyncSession, firm_id: uuid.UUID, account_id: uuid.UUID) -> Account:
    a = await session.get(Account, account_id)
    if a is None or a.firm_id != firm_id:
        raise MoneyInError("Account not found.")
    return a


def deposit_hold(firm: Firm, d: IncomingDeposit, today: date) -> float:
    if d.returned_on or not d.hold_until or d.hold_until <= today:
        return 0.0
    return float(d.amount)


async def holds_for(session: AsyncSession, firm: Firm, account_ids: list[uuid.UUID]) -> float:
    if not account_ids:
        return 0.0
    today = firm_time.today(firm)
    rows = (await session.execute(select(IncomingDeposit).where(IncomingDeposit.account_id.in_(account_ids)))).scalars().all()
    return round(sum(deposit_hold(firm, d, today) for d in rows), 2)


def _add_months(day: date, months: int) -> date:
    m = day.month - 1 + months
    y, m = day.year + m // 12, m % 12 + 1
    return date(y, m, min(day.day, 28))


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    pol = policy(firm)
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all()
                if household_id is None or owners.get(a.mandate_id) == household_id}
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    who = {aid: names.get(owners.get(a.mandate_id)) for aid, a in accounts.items()}
    ids = list(accounts)
    links = (await session.execute(select(BankLink).where(BankLink.account_id.in_(ids)))).scalars().all() if ids else []
    deposits = (await session.execute(select(IncomingDeposit).where(IncomingDeposit.account_id.in_(ids)))).scalars().all() if ids else []
    standing = (await session.execute(select(StandingInstruction).where(StandingInstruction.account_id.in_(ids), StandingInstruction.ended_on.is_(None)))).scalars().all() if ids else []
    checks: dict = {}
    for c in (await session.execute(select(InstructionCheck).where(InstructionCheck.standing_id.in_([s.id for s in standing])))).scalars().all() if standing else []:
        if c.standing_id not in checks or c.checked_on > checks[c.standing_id].checked_on:
            checks[c.standing_id] = c
    months = int(pol["instruction_revalidate_months"])
    instr = []
    for s in standing:
        last = checks.get(s.id)
        since = last.checked_on if last else s.first_due
        due = _add_months(since, months)
        instr.append({"id": str(s.id), "account": accounts[s.account_id].name, "client": who.get(s.account_id), "payee": s.payee, "third_party": s.third_party,
                      "amount": float(s.amount), "frequency": s.frequency, "last_checked": last.checked_on.isoformat() if last else None,
                      "due": due.isoformat(), "overdue": due < today})
    deps = []
    for d in sorted(deposits, key=lambda d: d.received_on, reverse=True):
        spent_risk = bool(d.returned_on and d.hold_until and d.returned_on > d.hold_until) or bool(d.returned_on and not d.hold_until)
        deps.append({"id": str(d.id), "account": accounts[d.account_id].name, "client": who.get(d.account_id), "method": d.method, "amount": float(d.amount),
                     "received_on": d.received_on.isoformat(), "hold_until": d.hold_until.isoformat() if d.hold_until else None,
                     "on_hold": deposit_hold(firm, d, today) > 0, "returned_on": d.returned_on.isoformat() if d.returned_on else None,
                     "return_code": d.return_code, "return_reason": RETURN_CODES.get(d.return_code) if d.return_code else None,
                     "may_have_been_invested": spent_risk})
    returns: dict = {}
    for d in deposits:
        if d.return_code:
            returns[d.return_code] = returns.get(d.return_code, 0) + 1
    return {"links": [{"id": str(link.id), "account": accounts[link.account_id].name, "client": who.get(link.account_id), "bank": link.bank, "last4": link.last4,
                       "owner": link.owner_name, "third_party": link.third_party, "method": link.method, "status": link.status,
                       "verified_on": link.verified_on.isoformat()} for link in links],
            "deposits": deps, "instructions": sorted(instr, key=lambda r: r["due"]),
            "returns_by_code": [{"code": k, "reason": RETURN_CODES.get(k, k), "count": n} for k, n in sorted(returns.items())],
            "summary": {"on_hold": round(sum(deposit_hold(firm, d, today) for d in deposits), 2), "returned": sum(1 for d in deposits if d.returned_on),
                        "instructions_overdue": sum(1 for r in instr if r["overdue"]), "links_verified": sum(1 for link in links if link.status == "verified")},
            "policy": pol, "as_of": today.isoformat(), "household_id": str(household_id) if household_id else None}


# ── Recording ─────────────────────────────────────────────────────────────────

async def prepare_link(session: AsyncSession, firm_id: uuid.UUID, *, account_id: uuid.UUID, bank: str | None, last4: str | None, owner_name: str | None,
                       method: str | None, evidence: str | None, verified_on: date | None, callback_by: str | None = None, failed: bool = False) -> dict:
    account = await _account(session, firm_id, account_id)
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    method = (method or "").strip().lower()
    if method not in METHODS:
        raise MoneyInError(f"How it was verified: one of {', '.join(METHODS)}.")
    digits = re.sub(r"\D", "", last4 or "")
    if len(digits) != 4:
        raise MoneyInError("Give the last four digits of the bank account, and no more.")
    if len((bank or "").strip()) < 2 or len((owner_name or "").strip()) < 2:
        raise MoneyInError("Name the bank and the account's owner as the bank shows them.")
    if len((evidence or "").strip()) < 5:
        raise MoneyInError("Say where the verification is: the micro-deposit confirmation, the verification report, the voided cheque.")
    day = verified_on or today
    if day > today:
        raise MoneyInError("A verification cannot be dated ahead.")
    hid, people = await _household_names(session, firm_id, account)
    third = not first_party(owner_name, people)
    if third and not failed and not (callback_by or "").strip():
        raise MoneyInError(f"{owner_name.strip()} is not in the household on file, so this is a third-party account: it needs a callback to the client "
                           "on the number of record first. Give who made it.")
    return {"account": account, "bank": bank.strip(), "last4": digits, "owner": owner_name.strip(), "third_party": third, "method": method,
            "evidence": evidence.strip(), "on": day, "callback_by": (callback_by or "").strip() or None, "status": "failed" if failed else "verified"}


async def record_link(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_link(session, firm_id, **terms)
    session.add(BankLink(firm_id=firm_id, account_id=r["account"].id, bank=r["bank"], last4=r["last4"], owner_name=r["owner"], third_party=r["third_party"],
                         method=r["method"], status=r["status"], verified_on=r["on"], evidence=r["evidence"], callback_by=r["callback_by"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="bank_link_recorded", actor=by,
                              content={"account_id": str(r["account"].id), "bank": r["bank"], "last4": r["last4"], "status": r["status"], "third_party": r["third_party"]})
    return await for_firm(session, firm_id)


async def prepare_deposit(session: AsyncSession, firm_id: uuid.UUID, *, account_id: uuid.UUID | None = None, deposit_id: uuid.UUID | None = None,
                          method: str | None = None, amount=None, bank_link_id: uuid.UUID | None = None, on: date | None = None,
                          evidence: str | None = None, return_code: str | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    day = on or today
    if day > today:
        raise MoneyInError("Nothing is recorded ahead of the day it happened.")
    if len((evidence or "").strip()) < 5:
        raise MoneyInError("Say where it comes from: the custodian's credit advice, the return notice.")
    if deposit_id is not None:                                   # a return
        d = await session.get(IncomingDeposit, deposit_id)
        if d is None or d.firm_id != firm_id:
            raise MoneyInError("Deposit not found.")
        if d.returned_on:
            raise MoneyInError("This deposit is already recorded as returned.")
        code = (return_code or "").strip().upper()
        if code not in RETURN_CODES:
            raise MoneyInError(f"A return carries its code: one of {', '.join(RETURN_CODES)}.")
        if day < d.received_on:
            raise MoneyInError("A return comes after the deposit.")
        return {"kind": "return", "deposit": d, "code": code, "on": day, "evidence": evidence.strip(),
                "after_available": bool(d.hold_until is None or day > d.hold_until), "revokes": code in UNAUTHORIZED and d.bank_link_id is not None,
                "account": await session.get(Account, d.account_id)}
    if account_id is None:
        raise MoneyInError("Name the account, or the deposit being returned.")
    account = await _account(session, firm_id, account_id)
    method = (method or "").strip().lower()
    if method not in DEPOSIT_METHODS:
        raise MoneyInError(f"The deposit came by one of {', '.join(DEPOSIT_METHODS)}.")
    try:
        value = round(float(str(amount).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise MoneyInError("Give the amount, as the credit advice shows it.")
    if value <= 0:
        raise MoneyInError("Give the amount, as the credit advice shows it.")
    link = None
    if method == "ach":
        if bank_link_id is None:
            raise MoneyInError("An ACH deposit comes from a verified bank account: name it.")
        link = await session.get(BankLink, bank_link_id)
        if link is None or link.account_id != account.id:
            raise MoneyInError("That bank account is not linked to this account.")
        if link.status != "verified":
            raise MoneyInError(f"That bank account is {link.status}, not verified: no ACH from it.")
    pol = policy(firm)
    days = int((pol["deposit_hold_days"] or {}).get(method, 0))
    hold = business_days_after(day, days) if value >= float(pol["deposit_hold_threshold"]) and days > 0 else None
    return {"kind": "deposit", "account": account, "method": method, "amount": value, "link": link, "on": day, "evidence": evidence.strip(), "hold_until": hold}


async def record_deposit(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_deposit(session, firm_id, **terms)
    if r["kind"] == "return":
        d = r["deposit"]
        d.returned_on, d.return_code, d.return_evidence = r["on"], r["code"], r["evidence"]
        if r["revokes"]:
            link = await session.get(BankLink, d.bank_link_id)
            link.status, link.revoked_reason = "revoked", f"{r['code']} {RETURN_CODES[r['code']]}"
        await session.flush()
        await ledger.append_entry(session, firm_id=firm_id, event_type="deposit_returned", actor=by,
                                  content={"deposit_id": str(d.id), "code": r["code"], "revoked_link": r["revokes"]})
    else:
        session.add(IncomingDeposit(firm_id=firm_id, account_id=r["account"].id, method=r["method"], amount=r["amount"],
                                    bank_link_id=r["link"].id if r["link"] else None, received_on=r["on"], hold_until=r["hold_until"],
                                    evidence=r["evidence"], recorded_by=by))
        await session.flush()
        await ledger.append_entry(session, firm_id=firm_id, event_type="deposit_recorded", actor=by,
                                  content={"account_id": str(r["account"].id), "method": r["method"], "amount": r["amount"],
                                           "hold_until": r["hold_until"].isoformat() if r["hold_until"] else None})
    return await for_firm(session, firm_id)


async def prepare_revalidation(session: AsyncSession, firm_id: uuid.UUID, *, standing_id: uuid.UUID, evidence: str | None, on: date | None,
                               callback_by: str | None = None) -> dict:
    s = await session.get(StandingInstruction, standing_id)
    if s is None or s.firm_id != firm_id:
        raise MoneyInError("Standing instruction not found.")
    if s.ended_on:
        raise MoneyInError("This instruction has ended.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    day = on or today
    if day > today:
        raise MoneyInError("A revalidation cannot be dated ahead.")
    if len((evidence or "").strip()) < 5:
        raise MoneyInError("Say where the client's confirmation is.")
    if s.third_party and not (callback_by or "").strip():
        raise MoneyInError("This pays a third party: revalidating it needs a callback to the client on the number of record. Give who made it.")
    return {"standing": s, "account": await session.get(Account, s.account_id), "on": day, "evidence": evidence.strip(),
            "callback_by": (callback_by or "").strip() or None, "next": _add_months(day, int(policy(firm)["instruction_revalidate_months"]))}


async def record_revalidation(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_revalidation(session, firm_id, **terms)
    session.add(InstructionCheck(firm_id=firm_id, standing_id=r["standing"].id, checked_on=r["on"], evidence=r["evidence"], callback_by=r["callback_by"], recorded_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="standing_instruction_revalidated", actor=by,
                              content={"standing_id": str(r["standing"].id), "on": r["on"].isoformat()})
    return await for_firm(session, firm_id)
