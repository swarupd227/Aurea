"""SIPs: a client's standing instruction to buy a mutual fund each month (India row 4).

A SIP is recorded by a person from the client's signed registration, or simulated on a demonstration firm and
marked so. That registration is the client's instruction for exactly these purchases: one fund, one account, a
fixed amount, monthly, from a start date to an end date or until it is ended. Nothing beyond them is placed, so a
licence that needs the client's consent to each trade is met by the registration, and a licence that places no
trades takes no SIP.

Each month the worker places one instalment, on or after the day the SIP names: an order for the amount, in units
at the latest real NAV (`execution._units_for_amount`), on the firm's venue. It is tried once a month, as a bank
debit is: before it, the licence and the plan are read again (`shelf.barred`), and the cash the account may spend
(after `cash_movement.held_back`) must cover it. An instalment that cannot go is recorded as missed, with why, and
the shortfall is shown, not solved.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, licences, markets, shelf
from app.models.graph import Account, Mandate
from app.models.portfolio import Instrument
from app.models.sip import Sip
from app.models.tenant import Firm
from app.provenance import ledger


class SipError(Exception):
    """A SIP that cannot be recorded or ended as asked. Carries a reason fit to show the user."""


def due_on(day: int, month_start: date) -> date:
    return month_start.replace(day=day)


def next_instalment(sip_day: int, starts_on: date, today: date, last_month: str | None = None) -> date:
    """The first instalment date on or after the start (and today), skipping a month already tried."""
    on = max(starts_on, today)
    first = on.replace(day=1)
    candidate = due_on(sip_day, first)
    if candidate < on or (last_month and candidate.strftime("%Y-%m") <= last_month):
        candidate = due_on(sip_day, (first + timedelta(days=32)).replace(day=1))
    return candidate


async def _account_mandate(session: AsyncSession, firm: Firm, account_id: uuid.UUID) -> tuple[Account, Mandate]:
    account = await session.get(Account, account_id)
    if account is None or account.firm_id != firm.id:
        raise SipError("No such account at this firm.")
    if (account.status or "active") != "active":
        raise SipError(f"{account.name} is closed.")
    mandate = await session.get(Mandate, account.mandate_id) if account.mandate_id else None
    if mandate is None:
        raise SipError(f"{account.name} sits under no mandate, so no licence says what may be bought in it.")
    return account, mandate


async def _instrument(session: AsyncSession, firm: Firm, instrument: str) -> Instrument:
    ref = (instrument or "").strip()
    row = None
    try:
        row = await session.get(Instrument, uuid.UUID(ref))
    except ValueError:
        row = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == ref.upper()))).scalars().first()
    if row is None or row.firm_id != firm.id:
        raise SipError(f"No fund {ref!r} on file at this firm.")
    if not (row.values_tags or {}).get("amfi_code"):
        raise SipError(f"A SIP buys a mutual fund, and {row.name} is not one.")
    return row


async def _may_buy(session: AsyncSession, firm: Firm, mandate: Mandate, inst: Instrument) -> None:
    licence = licences.track_for(firm, mandate)
    if licence is not None and not licence.may_execute:
        raise SipError(f"Under the {licence.name} licence no order is placed, so it takes no SIP.")
    reason = (await shelf.barred(session, firm, mandate)).get(inst.id)
    if reason:
        raise SipError(f"{inst.name} is {reason}.")


async def prepare(session: AsyncSession, firm: Firm, account_id: uuid.UUID, *, instrument: str, amount, day_of_month,
                  starts_on: date | None, ends_on: date | None, evidence_ref: str | None, simulated: bool = False) -> dict:
    """Everything a SIP would be, checked, with nothing written: the Confirm card and the write read the same."""
    account, mandate = await _account_mandate(session, firm, account_id)
    inst = await _instrument(session, firm, instrument)
    await _may_buy(session, firm, mandate, inst)
    try:
        value = round(float(amount), 2)
    except (TypeError, ValueError):
        raise SipError("Give the amount of each instalment.")
    if value <= 0:
        raise SipError("The amount must be more than nothing.")
    try:
        day = int(day_of_month)
    except (TypeError, ValueError):
        raise SipError("Give the day of the month, 1 to 28.")
    if not 1 <= day <= 28:
        raise SipError("The day of the month must be 1 to 28, so every month has it.")
    if starts_on is None:
        raise SipError("Give the date the SIP starts.")
    if ends_on is not None and ends_on <= starts_on:
        raise SipError("The SIP must end after it starts.")
    named = (evidence_ref or "").strip()
    if simulated:
        if not (firm.settings or {}).get("demonstration"):
            raise SipError("A simulated registration is for demonstration firms only. Say where the client's signed registration is on file.")
    elif len(named) < 5:
        raise SipError("Say where the client's signed SIP registration is on file.")
    today = firm_time.today(firm)
    return {"account": account, "mandate": mandate, "instrument": inst, "amount": value, "day": day,
            "starts_on": starts_on, "ends_on": ends_on, "evidence": None if simulated else named, "simulated": bool(simulated),
            "licence": licences.track_for(firm, mandate), "first": next_instalment(day, starts_on, today)}


async def record(session: AsyncSession, firm: Firm, account_id: uuid.UUID, *, by: str, **terms) -> Sip:
    ready = await prepare(session, firm, account_id, **terms)
    row = Sip(firm_id=firm.id, account_id=account_id, instrument_id=ready["instrument"].id, amount=ready["amount"],
              day_of_month=ready["day"], starts_on=ready["starts_on"], ends_on=ready["ends_on"], evidence_ref=ready["evidence"],
              simulated=ready["simulated"], recorded_by=by, history=[])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="sip_recorded", actor=by, content={
        "sip_id": str(row.id), "account_id": str(account_id), "fund": ready["instrument"].name, "amount": ready["amount"],
        "day_of_month": ready["day"], "starts_on": ready["starts_on"].isoformat(),
        "ends_on": ready["ends_on"].isoformat() if ready["ends_on"] else None,
        "evidence_ref": ready["evidence"], "simulated": ready["simulated"]})
    return row


async def prepare_end(session: AsyncSession, firm: Firm, sip_id: uuid.UUID, *, reason: str | None) -> dict:
    row = await session.get(Sip, sip_id)
    if row is None or row.firm_id != firm.id:
        raise SipError("No such SIP at this firm.")
    if row.ended_on:
        raise SipError(f"This SIP already ended on {row.ended_on.isoformat()}.")
    why = (reason or "").strip()
    if len(why) < 3:
        raise SipError("Say why it is ending: the client's instruction, the registration lapsing, and so on.")
    return {"sip": row, "account": await session.get(Account, row.account_id),
            "instrument": await session.get(Instrument, row.instrument_id), "reason": why}


async def end(session: AsyncSession, firm: Firm, sip_id: uuid.UUID, *, by: str, reason: str | None) -> Sip:
    ready = await prepare_end(session, firm, sip_id, reason=reason)
    row = ready["sip"]
    row.ended_on, row.ended_by, row.end_reason = firm_time.today(firm), by, ready["reason"]
    await ledger.append_entry(session, firm_id=firm.id, event_type="sip_ended", actor=by,
                              content={"sip_id": str(row.id), "reason": ready["reason"]})
    return row


def _active(row: Sip, today: date) -> bool:
    return row.ended_on is None and row.starts_on <= today and (row.ends_on is None or today <= row.ends_on)


async def run_due(session: AsyncSession, firm: Firm, today: date | None = None) -> dict:
    """Place this month's instalment of every SIP that has reached its day and not yet been tried this month."""
    from app.aurea_core import cash_movement
    from app.conduit import execution

    today = today or firm_time.today(firm)
    month = today.strftime("%Y-%m")
    rows = (await session.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.ended_on.is_(None)))).scalars().all()
    out = {"bought": 0, "missed": 0}
    for row in rows:
        if not _active(row, today) or today.day < row.day_of_month or row.last_month == month:
            continue
        row.last_month = month
        entry = {"month": month, "on": today.isoformat(), "amount": float(row.amount)}
        account = await session.get(Account, row.account_id)
        inst = await session.get(Instrument, row.instrument_id)
        try:
            _, mandate = await _account_mandate(session, firm, row.account_id)
            await _may_buy(session, firm, mandate, inst)
            spendable = float(account.cash_balance or 0) - (await cash_movement.held_back(session, firm, mandate))["total"]
            if spendable < float(row.amount):
                raise SipError(f"short of cash: {markets.money_for(firm)(max(spendable, 0))} free for a "
                               f"{markets.money_for(firm)(float(row.amount))} instalment")
            result = await execution.execute_order_set(session, firm=firm, mandate_id=mandate.id, actor="sip", order_set=[{
                "account_id": str(account.id), "instrument_id": str(inst.id), "side": "buy", "amount": float(row.amount),
                "symbol": inst.symbol, "asset_class": inst.asset_class, "custodian": account.custodian,
                "reason": f"SIP instalment, {month}"}])
            settled = result["settled"][0] if result["settled"] else None
            if settled is None:
                why = (result["failed"][0]["reason"] if result["failed"] else
                       result["breaks"][0]["reason"] if result["breaks"] else "the order did not settle")
                raise SipError(why)
            entry.update(status="bought", units=settled["quantity"], nav=settled["price"], order_id=settled["order_id"],
                         venue=result["venue"], reaches_market=result["reaches_market"])
            out["bought"] += 1
        except SipError as exc:
            entry.update(status="missed", reason=str(exc))
            out["missed"] += 1
        row.history = [*(row.history or []), entry]
        await ledger.append_entry(session, firm_id=firm.id, event_type="sip_instalment", actor="sip",
                                  content={"sip_id": str(row.id), **entry})
    await session.flush()
    return out


def _row(row: Sip, inst: Instrument | None, account: Account | None, firm: Firm, today: date) -> dict:
    tags = (inst.values_tags or {}) if inst else {}
    last = (row.history or [])[-1] if row.history else None
    active = _active(row, today) or (row.ended_on is None and row.starts_on > today)
    return {
        "id": str(row.id), "fund": inst.name if inst else None, "symbol": inst.symbol if inst else None,
        "plan": tags.get("fund_plan"), "scheme": tags.get("scheme"), "account": account.name if account else None,
        "amount": float(row.amount), "currency": firm.base_currency, "day_of_month": row.day_of_month,
        "starts_on": row.starts_on.isoformat(), "ends_on": row.ends_on.isoformat() if row.ends_on else None,
        "status": "active" if active else "ended", "ended_on": row.ended_on.isoformat() if row.ended_on else None,
        "end_reason": row.end_reason, "simulated": row.simulated, "evidence_ref": row.evidence_ref,
        "next_instalment": next_instalment(row.day_of_month, row.starts_on, today, row.last_month).isoformat() if active else None,
        "instalments": len(row.history or []),
        "bought": sum(1 for h in (row.history or []) if h.get("status") == "bought"),
        "units": round(sum(float(h.get("units") or 0) for h in (row.history or []) if h.get("status") == "bought"), 3),
        "last": last,
    }


async def for_firm(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """Every SIP at the firm, or one household's, with what each has bought and what is next."""
    from app.aurea_core.performance import household_account_ids

    stmt = select(Sip).where(Sip.firm_id == firm.id)
    if household_id is not None:
        ids = await household_account_ids(session, household_id, firm_id=firm.id) or []
        stmt = stmt.where(Sip.account_id.in_(ids)) if ids else stmt.where(Sip.id.is_(None))
    rows = (await session.execute(stmt.order_by(Sip.starts_on))).scalars().all()
    today = firm_time.today(firm)
    out = [_row(r, await session.get(Instrument, r.instrument_id), await session.get(Account, r.account_id), firm, today)
           for r in rows]
    return {"sips": out, "currency": firm.base_currency, "today": today.isoformat(),
            "active": sum(1 for r in out if r["status"] == "active"),
            "monthly": round(sum(r["amount"] for r in out if r["status"] == "active"), 2)}
