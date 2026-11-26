"""A PMS client's unit-value ledger, the returns laid out from it, a verifier's letter and the debit of a confirmed fee line (B-283).

**Unit values are a manager's statement, recorded, never invented.** A Portfolio Manager keeps a client's portfolio in units and states a net
asset value per unit at each month end. A person records one from the statement (`record_unit_value`, added and never edited, the latest row
for a day stands). Nothing is interpolated: a month with no value on file is a gap, named, and the chain across it is still the ratio of the two
values either side, which unit values make exact whatever flowed in or out.

**The high-water mark is exact from it.** The highest value per unit on file up to the day a period opens is the client's own mark. A
performance fee for a period is the rate on what the value per unit at the end is above the higher of that mark and the hurdle, times the units
the statement states. With no value either side of the period, or no units, there is no ledger fee: `pms.apply` falls back to the monthly-linked
estimate on the book, and **labels it an estimate**. The two are never mixed.

**TWRR is laid out from the same values**: each month's change per unit, compounded; it says it is the manager's statement and names its gaps.

**A return is stated to a client only when a verifier's letter backs it** (research report R9): APMI's TWRR or a verification by PaRRVA or
another verifier, recorded from the letter with its date, the period and scope it covers. Without a letter covering the quarter the return reads
"not verified" and the client's report stays not publishable. A letter withdrawn later is a new row. Nothing here verifies anything.

**Debiting a confirmed fee line** (`debit_fee_line`) goes through the gates a fee already has, never automatically and behind a Confirm card:
the client's fee-debit authority and e-delivery consent on the consent registry, a bill for the period that has been issued, a second person
(not the one who confirmed the line), and the cash the mandate holds after what is held back (`cash_movement.held_back`): no overdraft, no
part payment and no sale to raise it. A shortfall is shown, not solved.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import cash_movement, firm_time, pms
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.billing import Invoice
from app.models.consent_registry import ConsentRecord
from app.models.graph import Account, Mandate
from app.models.india_polish import PmsFeeLine
from app.models.india_rem_d import PmsFeeDebit, PmsUnitValue, PmsVerification
from app.models.portfolio import Transaction
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.PORTFOLIO_TEAM, UserRole.OPERATIONS})
VERIFICATION_RECORDERS = frozenset({UserRole.COMPLIANCE, UserRole.PORTFOLIO_TEAM, UserRole.OPERATIONS})
DEBITORS = frozenset({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})
GAP_DAYS = 35            # a month end to the next is at most 31 days; a longer step means a month is missing
BASE_WITHIN_DAYS = 35    # the value that opens a period is at most this long before it
END_WITHIN_DAYS = 5      # the value that closes it is at most this many days before the end (a weekend or a holiday)
BASIS = "Value per unit from the manager's statement, each recorded by a person; nothing is interpolated"
NOT_VERIFIED = "Not verified: no verifier's letter on file covers this period. A return is stated to a client only when one does."


class LedgerError(ValueError):
    """This cannot be recorded or done, with the reason."""


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        raise LedgerError(f"Give {label} as YYYY-MM-DD.")


def _number(value, label: str, *, required: bool = True) -> float | None:
    if value in (None, ""):
        if required:
            raise LedgerError(f"Give {label}.")
        return None
    try:
        out = float(str(value).replace(",", ""))
    except ValueError:
        raise LedgerError(f"{label[0].upper() + label[1:]} is a number.")
    if out <= 0:
        raise LedgerError(f"{label[0].upper() + label[1:]} must be more than zero.")
    return out


async def _client(session: AsyncSession, firm: Firm, mandate_id) -> Mandate:
    if not pms.applies(firm):
        raise LedgerError("A unit-value ledger is kept for a firm holding a Portfolio Manager licence.")
    try:
        mid = mandate_id if isinstance(mandate_id, uuid.UUID) else uuid.UUID(str(mandate_id))
    except (TypeError, ValueError):
        raise LedgerError("Give the client's mandate id, from read_pms.")
    mandate = next((m for m in await pms.pms_mandates(session, firm) if m.id == mid), None)
    if mandate is None:
        raise LedgerError("That is not a PMS client of this firm. read_pms lists them.")
    return mandate


# ── The ledger ────────────────────────────────────────────────────────────────────────────────

async def series(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID) -> list[PmsUnitValue]:
    """The values on file, oldest first, the latest row standing for a day."""
    rows = (await session.execute(select(PmsUnitValue).where(PmsUnitValue.firm_id == firm_id, PmsUnitValue.mandate_id == mandate_id)
                                  .order_by(PmsUnitValue.created_at, PmsUnitValue.id))).scalars().all()
    by_day: dict[date, PmsUnitValue] = {}
    for r in rows:
        by_day[r.as_of] = r
    return [by_day[d] for d in sorted(by_day)]


def twrr(values: list[PmsUnitValue], start: date, end: date) -> dict:
    """The time-weighted return over [start, end] from value per unit alone: the values in the window, each step's change compounded.
    `return` is None with fewer than two values in it. Gaps (a step over 35 days) are named, and the chain across one stays exact."""
    window = [v for v in values if start <= v.as_of <= end]
    if len(window) < 2:
        return {"return": None, "months": 0, "periods": [], "gaps": [],
                "reason": "Fewer than two values per unit are on file in the period."}
    periods, gaps, chain = [], [], 1.0
    for a, b in zip(window, window[1:]):
        r = float(b.nav_per_unit) / float(a.nav_per_unit) - 1.0
        chain *= 1.0 + r
        periods.append({"start": a.as_of.isoformat(), "end": b.as_of.isoformat(), "return": round(r, 6)})
        if (b.as_of - a.as_of).days > GAP_DAYS:
            gaps.append({"from": a.as_of.isoformat(), "to": b.as_of.isoformat(), "days": (b.as_of - a.as_of).days})
    return {"return": round(chain - 1.0, 6), "months": len(periods), "periods": periods, "gaps": gaps,
            "from": window[0].as_of.isoformat(), "to": window[-1].as_of.isoformat(), "basis": BASIS}


def high_water_mark(values: list[PmsUnitValue], through: date) -> dict | None:
    """The highest value per unit on file up to and including `through`, with its day. None with nothing on file by then."""
    upto = [v for v in values if v.as_of <= through]
    if not upto:
        return None
    top = max(upto, key=lambda v: (float(v.nav_per_unit), -v.as_of.toordinal()))
    return {"nav_per_unit": float(top.nav_per_unit), "as_of": top.as_of.isoformat()}


def fee(agreement, values: list[PmsUnitValue], start: date, end: date) -> dict:
    """The performance fee the agreement's terms give for [start, end] on the unit values: exact where there is a value that opens the period, one
    that closes it and the units the statement states. `estimate` is None, with the reason, where one is missing."""
    base = [v for v in values if v.as_of < start]
    closing = [v for v in values if v.as_of <= end and (end - v.as_of).days <= END_WITHIN_DAYS and v.as_of >= start]
    if not base or (start - base[-1].as_of).days > BASE_WITHIN_DAYS:
        return {"estimate": None, "exact": False, "reason": "No value per unit is recorded for the day the period opens."}
    if not closing:
        return {"estimate": None, "exact": False, "reason": "No value per unit is recorded for the day the period closes."}
    opening, closed = base[-1], closing[-1]
    if closed.units is None:
        return {"estimate": None, "exact": False, "reason": "The closing statement states no units, so the fee cannot be worked on the ledger."}
    nav0, nav1 = float(opening.nav_per_unit), float(closed.nav_per_unit)
    days = max((end - start).days, 1)
    hurdle = float(agreement.hurdle_rate or 0) * days / 365.0
    mark = high_water_mark(values, opening.as_of)
    peak = mark["nav_per_unit"] if agreement.high_water_mark and mark else nav0
    threshold = max(peak, nav0 * (1.0 + hurdle))
    excess = max(0.0, nav1 - threshold)
    amount = round(float(agreement.performance_rate or 0) * excess * float(closed.units), 2)
    return {"estimate": amount, "exact": True, "basis": "unit ledger",
            "period_return": round(nav1 / nav0 - 1.0, 4), "hurdle_for_period": round(hurdle, 4), "high_water_mark_nav": round(peak, 6),
            "high_water_mark_on": mark["as_of"] if mark else None, "opening_nav": nav0, "opening_on": opening.as_of.isoformat(),
            "closing_nav": nav1, "closing_on": closed.as_of.isoformat(), "units": float(closed.units),
            "excess_per_unit": round(excess, 6), "months": sum(1 for v in values if start <= v.as_of <= end)}


async def prepare_unit_value(session: AsyncSession, firm: Firm, role, *, mandate_id, as_of, nav_per_unit, document: str | None, units=None) -> dict:
    if role not in RECORDERS:
        raise LedgerError("The portfolio team or operations records a value per unit, from the manager's statement.")
    mandate = await _client(session, firm, mandate_id)
    day = _day(as_of, "the statement's date")
    today = firm_time.today(firm)
    if day > today:
        raise LedgerError("A statement cannot be dated in the future.")
    nav = _number(nav_per_unit, "the value per unit")
    held = _number(units, "the units", required=False)
    if len((document or "").strip()) < 5:
        raise LedgerError("Say where the manager's statement is filed.")
    on_file = {v.as_of: v for v in await series(session, firm.id, mandate.id)}
    before = on_file.get(day)
    if before is not None and abs(float(before.nav_per_unit) - nav) < 1e-9 and (held is None or before.units is not None and abs(float(before.units) - held) < 1e-6):
        raise LedgerError(f"That value is already recorded for {day.isoformat()}.")
    return {"mandate": mandate, "as_of": day, "nav_per_unit": nav, "units": held, "document": document.strip(),
            "replaces": float(before.nav_per_unit) if before is not None else None}


async def record_unit_value(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_unit_value(session, firm, role, **terms)
    row = PmsUnitValue(created_at=utcnow(), firm_id=firm.id, mandate_id=p["mandate"].id, as_of=p["as_of"], nav_per_unit=p["nav_per_unit"], units=p["units"],
                       document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pms_unit_value_recorded", actor=by, content={
        "mandate_id": str(p["mandate"].id), "as_of": p["as_of"].isoformat(), "nav_per_unit": p["nav_per_unit"], "document": p["document"]})
    return await ledger_view(session, firm, mandate_id=p["mandate"].id)


# ── A verifier's letter ──────────────────────────────────────────────────────────────────────

async def verifications(session: AsyncSession, firm_id: uuid.UUID, strategy: str | None = None) -> list[PmsVerification]:
    q = select(PmsVerification).where(PmsVerification.firm_id == firm_id)
    if strategy:
        q = q.where(PmsVerification.strategy == strategy)
    return list((await session.execute(q.order_by(PmsVerification.created_at, PmsVerification.id))).scalars().all())


def verification_for(rows: list[PmsVerification], strategy: str | None, start: date, end: date) -> dict:
    """Whether a letter covers [start, end] for a strategy. The latest row for the same verifier and period stands, so a withdrawal ends it."""
    if not strategy:
        return {"state": "not_verified", "said": NOT_VERIFIED, "reason": "No strategy is recorded on the agreement."}
    latest: dict[tuple, PmsVerification] = {}
    for r in rows:
        if r.strategy == strategy:
            latest[(r.verifier.lower(), r.period_start, r.period_end)] = r
    cover = [r for r in latest.values() if r.state == "verified" and r.period_start <= start and r.period_end >= end]
    if not cover:
        return {"state": "not_verified", "said": NOT_VERIFIED}
    r = max(cover, key=lambda x: x.letter_date)
    return {"state": "verified", "said": f"Verified by {r.verifier}, letter of {r.letter_date.isoformat()}, covering {r.period_start.isoformat()} to {r.period_end.isoformat()}",
            "verifier": r.verifier, "letter_date": r.letter_date.isoformat(), "scope": r.scope, "period_start": r.period_start.isoformat(),
            "period_end": r.period_end.isoformat(), "document": r.document}


async def prepare_verification(session: AsyncSession, firm: Firm, role, *, strategy: str | None, verifier: str | None, period_start, period_end,
                               letter_date, scope: str | None, document: str | None, state: str | None = "verified", reason: str | None = None) -> dict:
    if not pms.applies(firm):
        raise LedgerError("A verification is recorded for a firm holding a Portfolio Manager licence.")
    if role not in VERIFICATION_RECORDERS:
        raise LedgerError("Compliance, the portfolio team or operations records a verifier's letter, from the letter.")
    name = " ".join((strategy or "").split())
    known = {a.strategy for a in await _agreements(session, firm)}
    if name not in known:
        raise LedgerError(f"'{name}' is not a strategy on a recorded PMS agreement. Strategies: {', '.join(sorted(known)) or 'none yet'}.")
    who = " ".join((verifier or "").split())
    if len(who) < 3:
        raise LedgerError("Name the verifier as the letter does (APMI, PaRRVA or another).")
    start, end, sent = _day(period_start, "the period's first day"), _day(period_end, "the period's last day"), _day(letter_date, "the letter's date")
    if end < start:
        raise LedgerError("The period cannot end before it starts.")
    today = firm_time.today(firm)
    if sent > today:
        raise LedgerError("A letter cannot be dated in the future.")
    if end > sent:
        raise LedgerError("A letter cannot verify returns for a period that ended after its own date.")
    kind = (state or "verified").strip().lower()
    if kind not in ("verified", "withdrawn"):
        raise LedgerError("The state is verified, or withdrawn.")
    if len((scope or "").strip()) < 5:
        raise LedgerError("State what the letter says it covers (the strategy's returns, gross or net, since inception or the period).")
    if len((document or "").strip()) < 5:
        raise LedgerError("Say where the verifier's letter is filed.")
    rows = await verifications(session, firm.id, name)
    standing = verification_for(rows, name, start, end)
    if kind == "withdrawn":
        if standing["state"] != "verified":
            raise LedgerError("There is no verification on file for that period to withdraw.")
        if len((reason or "").strip()) < 5:
            raise LedgerError("Say why it is withdrawn, from the letter.")
    elif standing["state"] == "verified" and standing.get("verifier", "").lower() == who.lower() and standing["letter_date"] == sent.isoformat():
        raise LedgerError("That letter is already recorded.")
    return {"strategy": name, "verifier": who, "period_start": start, "period_end": end, "letter_date": sent, "scope": scope.strip(), "state": kind,
            "document": document.strip(), "reason": (reason or "").strip() or None}


async def _agreements(session: AsyncSession, firm: Firm) -> list:
    out = []
    for m in await pms.pms_mandates(session, firm):
        a = await pms.standing(session, firm.id, m.id)
        if a is not None:
            out.append(a)
    return out


async def record_verification(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_verification(session, firm, role, **terms)
    row = PmsVerification(created_at=utcnow(), firm_id=firm.id, strategy=p["strategy"], verifier=p["verifier"], period_start=p["period_start"],
                          period_end=p["period_end"], letter_date=p["letter_date"], scope=p["scope"], state=p["state"], document=p["document"],
                          recorded_by=by, note=p["reason"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pms_verification_recorded", actor=by, content={
        "strategy": p["strategy"], "verifier": p["verifier"], "state": p["state"], "period_start": p["period_start"].isoformat(),
        "period_end": p["period_end"].isoformat(), "letter_date": p["letter_date"].isoformat(), "document": p["document"]})
    return await ledger_view(session, firm)


# ── What the ledger shows ───────────────────────────────────────────────────────────────────────

async def _view_of(session: AsyncSession, firm: Firm, mandate: Mandate, rows: list[PmsVerification], today: date) -> dict:
    agreement = await pms.standing(session, firm.id, mandate.id)
    values = await series(session, firm.id, mandate.id)
    start, end, label = pms.last_quarter(today)
    owner = await pms._owner(session, mandate)
    strategy = agreement.strategy if agreement else None
    quarter = twrr(values, start, end)
    # The quarter's return needs the value that closed the quarter before it: the first value inside it opens the chain only if on the first day.
    opening = [v for v in values if v.as_of < start]
    if opening and (start - opening[-1].as_of).days <= BASE_WITHIN_DAYS:
        window = [opening[-1]] + [v for v in values if start <= v.as_of <= end]
        quarter = twrr(window, window[0].as_of, end) if len(window) >= 2 else quarter
    verified = verification_for(rows, strategy, start, end)
    mark = high_water_mark(values, today)
    return {"mandate_id": str(mandate.id), "client": getattr(owner, "full_name", None) or getattr(owner, "name", None) or mandate.name, "strategy": strategy,
            "values": [{"as_of": v.as_of.isoformat(), "nav_per_unit": float(v.nav_per_unit), "units": float(v.units) if v.units is not None else None}
                       for v in values[-24:]],
            "count": len(values), "latest": {"as_of": values[-1].as_of.isoformat(), "nav_per_unit": float(values[-1].nav_per_unit)} if values else None,
            "high_water_mark": mark, "quarter": {"label": label, "start": start.isoformat(), "end": end.isoformat(), **{k: quarter[k] for k in ("return", "months", "gaps")},
                                                 "reason": quarter.get("reason")},
            "twrr": quarter, "verification": verified, "basis": BASIS,
            "state": "ledger" if values else "not_assessed",
            "message": None if values else "No value per unit is recorded for this client, so the high-water mark and the returns here are not assessed."}


async def ledger_view(session: AsyncSession, firm: Firm, *, mandate_id=None) -> dict:
    today = firm_time.today(firm)
    if not pms.applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "clients": []}
    rows = await verifications(session, firm.id)
    mandates = await pms.pms_mandates(session, firm)
    if mandate_id is not None:
        want = mandate_id if isinstance(mandate_id, uuid.UUID) else uuid.UUID(str(mandate_id))
        mandates = [m for m in mandates if m.id == want]
    clients = [await _view_of(session, firm, m, rows, today) for m in mandates]
    return {"applies": True, "as_of": today.isoformat(), "clients": clients,
            "verifications": [{"strategy": r.strategy, "verifier": r.verifier, "period_start": r.period_start.isoformat(), "period_end": r.period_end.isoformat(),
                               "letter_date": r.letter_date.isoformat(), "scope": r.scope, "state": r.state, "document": r.document} for r in rows[-12:]],
            "counts": {"with_values": sum(1 for c in clients if c["count"]), "verified": sum(1 for c in clients if c["verification"]["state"] == "verified"),
                       "not_verified": sum(1 for c in clients if c["verification"]["state"] != "verified")},
            "note": "Values per unit are the manager's statements as a person recorded them. Not verified means no letter is on file, not that the return is wrong."}


# ── Debiting a confirmed fee line ─────────────────────────────────────────────────────────

async def _line(session: AsyncSession, firm: Firm, fee_line_id) -> PmsFeeLine:
    try:
        lid = fee_line_id if isinstance(fee_line_id, uuid.UUID) else uuid.UUID(str(fee_line_id))
    except (TypeError, ValueError):
        raise LedgerError("Give the fee line's id, from read_pms_ledger.")
    line = await session.get(PmsFeeLine, lid)
    if line is None or line.firm_id != firm.id:
        raise LedgerError("That is not a confirmed fee line of this firm.")
    return line


async def debit_of(session: AsyncSession, line_id: uuid.UUID) -> PmsFeeDebit | None:
    return (await session.execute(select(PmsFeeDebit).where(PmsFeeDebit.fee_line_id == line_id, PmsFeeDebit.status == "posted"))).scalars().first()


async def open_lines(session: AsyncSession, firm: Firm) -> list[dict]:
    """Every confirmed fee line and whether it was debited."""
    out = []
    for line in (await session.execute(select(PmsFeeLine).where(PmsFeeLine.firm_id == firm.id).order_by(PmsFeeLine.period_end.desc()))).scalars().all():
        mandate = await session.get(Mandate, line.mandate_id)
        d = await debit_of(session, line.id)
        out.append({"id": str(line.id), "mandate_id": str(line.mandate_id), "mandate": mandate.name if mandate else None, "period_start": line.period_start.isoformat(),
                    "period_end": line.period_end.isoformat(), "amount": float(line.amount), "currency": line.currency, "confirmed_by": line.confirmed_by,
                    "basis": (line.working or {}).get("basis") or "estimate", "debited": d is not None,
                    "debited_by": d.debited_by if d is not None else None})
    return out


async def prepare_debit(session: AsyncSession, firm: Firm, role, *, by: str, fee_line_id) -> dict:
    """Every gate the debit passes, nothing written. Refused with the one thing to put right."""
    if not pms.applies(firm):
        raise LedgerError("A PMS fee line is debited for a firm holding a Portfolio Manager licence.")
    if role not in DEBITORS:
        raise LedgerError("Operations or the portfolio team takes a confirmed fee line from the client's cash.")
    line = await _line(session, firm, fee_line_id)
    mandate = await session.get(Mandate, line.mandate_id)
    if await debit_of(session, line.id) is not None:
        raise LedgerError("This fee line has already been debited.")
    if (by or "").strip().lower() == (line.confirmed_by or "").strip().lower():
        raise LedgerError("A second person takes the debit: the one who confirmed the line may not also take its money.")
    invoice = (await session.execute(select(Invoice).where(
        Invoice.firm_id == firm.id, Invoice.mandate_id == line.mandate_id, Invoice.period_start == line.period_start, Invoice.kind == "invoice",
        Invoice.status.in_(("issued", "debited"))).order_by(Invoice.created_at.desc()))).scalars().first()
    if invoice is None:
        raise LedgerError("The bill for this period is not issued yet; the line is debited with an issued bill, which tells the client first.")
    from app.aurea_core import pre_advice

    household_id = await pre_advice.household_of_mandate(session, mandate)
    rows = list((await session.execute(select(ConsentRecord).where(ConsentRecord.firm_id == firm.id, ConsentRecord.household_id == household_id))).scalars().all()) if household_id else []
    from app.aurea_core import consents

    have = consents.granted(rows)
    if "fee_debit" not in have:
        raise LedgerError("No authority to debit fees is on file for this client's household (the consent registry's fee debit), so nothing is taken.")
    if "e_delivery" not in have:
        raise LedgerError("No e-delivery consent is on file for this client's household, so the fee statement is not delivered electronically and nothing is taken.")
    accounts = list((await session.execute(select(Account).where(Account.firm_id == firm.id, Account.mandate_id == mandate.id))).scalars().all())
    cash = sum(float(a.cash_balance or 0) for a in accounts)
    held = (await cash_movement.held_back(session, firm, mandate))["total"]
    free = cash - held
    amount = float(line.amount)
    if free < amount:
        raise LedgerError(f"The mandate's cash after what is held back is {free:,.2f} and the line is {amount:,.2f}: it is short by {amount - free:,.2f}. "
                          "Nothing is sold to raise it and no part is taken; what to do is a person's decision.")
    return {"line": line, "mandate": mandate, "invoice": invoice, "amount": amount, "currency": line.currency, "cash": round(cash, 2), "held_back": round(held, 2),
            "free": round(free, 2), "accounts": accounts, "household_id": household_id, "consents": sorted(have & {"fee_debit", "e_delivery"})}


async def debit_fee_line(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_debit(session, firm, role, by=by, **terms)
    today = firm_time.today(firm)
    remaining = p["amount"]
    taken = []
    for acc in sorted(p["accounts"], key=lambda a: -float(a.cash_balance or 0)):
        take = min(float(acc.cash_balance or 0), remaining)
        if take <= 0:
            continue
        acc.cash_balance = round(float(acc.cash_balance) - take, 2)
        session.add(Transaction(firm_id=firm.id, account_id=acc.id, instrument_id=None, txn_type="fee", quantity=0, price=0, amount=take, trade_date=today,
                                lineage={"pms_fee_line_id": str(p["line"].id), "invoice_number": p["invoice"].invoice_number,
                                         "period_start": p["line"].period_start.isoformat(), "period_end": p["line"].period_end.isoformat()}))
        taken.append({"account_id": str(acc.id), "amount": round(take, 2)})
        remaining = round(remaining - take, 2)
    row = PmsFeeDebit(created_at=utcnow(), firm_id=firm.id, fee_line_id=p["line"].id, mandate_id=p["mandate"].id, amount=p["amount"], currency=p["currency"],
                      status="posted", detail={"accounts": taken, "consents": p["consents"], "held_back": p["held_back"]}, debited_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="pms_fee_line_debited", actor=by, content={
        "fee_line_id": str(p["line"].id), "mandate_id": str(p["mandate"].id), "amount": p["amount"], "currency": p["currency"],
        "confirmed_by": p["line"].confirmed_by, "invoice": p["invoice"].invoice_number})
    return {"applies": True, "debited": {"mandate": p["mandate"].name, "amount": p["amount"], "currency": p["currency"], "accounts": taken,
                                         "confirmed_by": p["line"].confirmed_by, "debited_by": by}, "lines": await open_lines(session, firm)}
