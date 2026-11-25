"""The mandate behind a SIP's debit: NACH and UPI Autopay (L100 India edition section 5.2, B-295).

A SIP is the client's instruction to buy; the debit that pays for it needs a second registration, a mandate on the client's bank account.
`sip.py` records the first and the worker buys each instalment; this records the second, from its registration: the account it debits (a bank
account `money_in` verified from its evidence, never one this module accepts on trust), the registration's own reference (the UMRN for a
NACH mandate), the most one debit may be, the day it starts, the day it expires, and the status the registration reports. Added and never
edited: a cancellation is a second row for the same reference, and the latest row for a reference stands.

What it changes in a SIP is only what the SIP says when an instalment is missed. A mandate that has lapsed, expired, been paused, cancelled or
rejected, or one whose per-debit limit is below the instalment, is named as the reason the instalment was missed (`sip.run_due`), and the
instalment is not tried. A SIP with no mandate on file reads "mandate not on file" and carries a warning, and its instalments are still
tried: the platform's earlier SIPs were recorded before this, and a fact not on file is not a no. Nothing here moves money, registers a
mandate with a bank, or presents a debit.

A UPI Autopay mandate above the line for mutual funds is named, not refused: a bank's own limit may be lower or higher, and the line is a law
figure counsel has not yet read.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account
from app.models.india_batch_a import DebitMandate
from app.models.money_in import BankLink
from app.models.sip import Sip
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
TYPES = {"nach": "NACH", "upi_autopay": "UPI Autopay"}
STATUSES = {"active": "Active", "paused": "Paused", "rejected": "Rejected by the bank"}
STATES = {"active": "In force", "not_yet": "Not yet in force", "expired": "Expired", "paused": "Paused", "rejected": "Rejected", "cancelled": "Cancelled",
          "none": "Mandate not on file"}
LINE_KEY = "in_upi_autopay_limit_mf"


class MandateError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _day(value, label: str, *, required: bool = True) -> date | None:
    if value in (None, ""):
        if required:
            raise MandateError(f"Give {label} as YYYY-MM-DD.")
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise MandateError(f"Give {label} as YYYY-MM-DD.")


def state_of(row: DebitMandate | None, today: date) -> str:
    if row is None:
        return "none"
    if row.kind == "cancelled":
        return "cancelled"
    if row.status == "rejected":
        return "rejected"
    if row.status == "paused":
        return "paused"
    if row.starts_on > today:
        return "not_yet"
    if row.expires_on is not None and row.expires_on < today:
        return "expired"
    return "active"


async def standing(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None) -> list[DebitMandate]:
    """The latest row for each registration reference."""
    stmt = select(DebitMandate).where(DebitMandate.firm_id == firm_id)
    if account_ids is not None:
        stmt = stmt.where(DebitMandate.account_id.in_(account_ids or [uuid.uuid4()]))
    latest: dict[tuple, DebitMandate] = {}
    for r in (await session.execute(stmt.order_by(DebitMandate.created_at, DebitMandate.id))).scalars().all():
        latest[(r.account_id, r.reference)] = r
    return list(latest.values())


def reason_unfit(row: DebitMandate | None, amount: float, today: date) -> str | None:
    """Why this mandate cannot pay a debit of `amount` today, or None. No row is not a reason: it is the absence of a fact."""
    if row is None:
        return None
    state = state_of(row, today)
    if state == "expired":
        return f"the {TYPES[row.mandate_type]} mandate {row.reference} expired on {row.expires_on.isoformat()}"
    if state in ("paused", "rejected", "cancelled"):
        return f"the {TYPES[row.mandate_type]} mandate {row.reference} is {STATES[state].lower()}"
    if state == "not_yet":
        return f"the {TYPES[row.mandate_type]} mandate {row.reference} starts on {row.starts_on.isoformat()}"
    if amount > float(row.per_debit_limit) + 1e-9:
        return f"the instalment is above the {TYPES[row.mandate_type]} mandate {row.reference}'s limit of {float(row.per_debit_limit):,.0f} a debit"
    return None


async def for_sip(session: AsyncSession, sip: Sip, today: date) -> dict:
    """The mandate that stands behind a SIP: the best of those on its account, an in-force one first."""
    rows = await standing(session, sip.firm_id, [sip.account_id])
    if not rows:
        return {"state": "none", "label": STATES["none"], "row": None, "warning": "Mandate not on file: record the NACH or UPI Autopay registration behind this SIP's debit."}
    # An in-force one first; failing that a registered one over a cancelled one, and the one that ran longest.
    rows.sort(key=lambda r: (state_of(r, today) != "active", r.kind == "cancelled", -(r.expires_on.toordinal() if r.expires_on else 10 ** 7)))
    best = rows[0]
    state = state_of(best, today)
    unfit = reason_unfit(best, float(sip.amount), today)
    return {"state": state, "label": STATES[state], "row": best, "fits": unfit is None,
            "warning": unfit and f"This SIP's debit would fail: {unfit}."}


def view_row(r: DebitMandate, today: date, account: Account | None = None, link: BankLink | None = None) -> dict:
    state = state_of(r, today)
    line = law_figures.value(LINE_KEY)
    return {"id": str(r.id), "reference": r.reference, "type": r.mandate_type, "type_label": TYPES.get(r.mandate_type, r.mandate_type), "state": state, "label": STATES[state],
            "per_debit_limit": float(r.per_debit_limit), "starts_on": r.starts_on.isoformat(), "expires_on": r.expires_on.isoformat() if r.expires_on else None,
            "account": account.name if account else None, "account_id": str(r.account_id), "bank": link.bank if link else None, "last4": link.last4 if link else None,
            "document": r.source_document, "reason": r.reason,
            "over_upi_line": r.mandate_type == "upi_autopay" and float(r.per_debit_limit) > line}


async def prepare(session: AsyncSession, firm: Firm, role, *, account_id, reference: str | None = None, mandate_type: str | None = None, per_debit_limit=None,
                  starts_on=None, expires_on=None, last4: str | None = None, status: str | None = "active", document: str | None = None, cancel: bool = False,
                  reason: str | None = None) -> dict:
    if not applies(firm):
        raise MandateError("A debit mandate is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise MandateError("An adviser, paraplanner or operations records a mandate, from its registration.")
    try:
        aid = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    except ValueError:
        raise MandateError("Give the account from the client's page.")
    account = await session.get(Account, aid)
    if account is None or account.firm_id != firm.id:
        raise MandateError("Account not found.")
    today = firm_time.today(firm)
    ref = (reference or "").strip()
    if len(ref) < 4:
        raise MandateError("Give the registration's own reference: the UMRN for a NACH mandate, the mandate id for UPI Autopay.")
    if len((document or "").strip()) < 5:
        raise MandateError("Say where the mandate's registration is filed.")
    before = next((r for r in await standing(session, firm.id, [aid]) if r.reference == ref), None)
    if cancel:
        if before is None or before.kind == "cancelled":
            raise MandateError("There is no mandate on file under that reference to cancel.")
        if len((reason or "").strip()) < 5:
            raise MandateError("Say why it is cancelled, from the bank's or the client's notice.")
        return {"account": account, "reference": ref, "type": before.mandate_type, "kind": "cancelled", "limit": float(before.per_debit_limit), "starts_on": before.starts_on,
                "expires_on": before.expires_on, "status": before.status, "link": None, "document": document.strip(), "reason": reason.strip(), "before": before, "today": today}
    kind = (mandate_type or "").strip().lower().replace(" ", "_")
    if kind not in TYPES:
        raise MandateError("The mandate is NACH or UPI Autopay.")
    try:
        cap = round(float(str(per_debit_limit).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise MandateError("Give the most one debit may be, as the registration states it.")
    if cap <= 0:
        raise MandateError("The limit must be more than nothing.")
    start = _day(starts_on, "the day the mandate starts")
    end = _day(expires_on, "the day the mandate expires", required=False)
    if end is not None and end <= start:
        raise MandateError("A mandate cannot expire before it starts.")
    state = (status or "active").strip().lower()
    if state not in STATUSES:
        raise MandateError("The registration's status is active, paused or rejected.")
    digits = "".join(c for c in (last4 or "") if c.isdigit())
    if len(digits) != 4:
        raise MandateError("Give the last four digits of the bank account the mandate debits, and no more.")
    links = (await session.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.account_id == aid, BankLink.last4 == digits,
                                                         BankLink.status == "verified"))).scalars().all()
    if not links:
        raise MandateError(f"The bank account ending {digits} is not verified for {account.name}. Verify it from its evidence first (a micro-deposit, an instant "
                           "check or a voided cheque), because a mandate debits only an account that is.")
    link = sorted(links, key=lambda l: l.verified_on)[-1]
    if link.third_party:
        raise MandateError(f"The account ending {digits} is in a name outside the household. A SIP is paid from the client's own account.")
    if before is not None and before.kind == "registered" and (float(before.per_debit_limit), before.starts_on, before.expires_on, before.status, before.mandate_type) == (cap, start, end, state, kind):
        raise MandateError("That registration is already recorded.")
    return {"account": account, "reference": ref, "type": kind, "kind": "registered", "limit": cap, "starts_on": start, "expires_on": end, "status": state, "link": link,
            "document": document.strip(), "reason": (reason or "").strip() or None, "before": before, "today": today,
            "over_upi_line": kind == "upi_autopay" and cap > law_figures.value(LINE_KEY)}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = DebitMandate(created_at=utcnow(), firm_id=firm.id, account_id=p["account"].id, bank_link_id=p["link"].id if p["link"] else (p["before"].bank_link_id if p["before"] else None),
                       reference=p["reference"], mandate_type=p["type"], kind=p["kind"], per_debit_limit=p["limit"], starts_on=p["starts_on"], expires_on=p["expires_on"],
                       status=p["status"], source_document=p["document"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="debit_mandate_recorded", actor=by, content={
        "account_id": str(p["account"].id), "reference": row.reference, "type": row.mandate_type, "kind": row.kind, "per_debit_limit": float(row.per_debit_limit),
        "starts_on": row.starts_on.isoformat(), "expires_on": row.expires_on.isoformat() if row.expires_on else None, "document": row.source_document})
    return await overview(session, firm)


async def overview(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None) -> dict:
    """Every mandate, and every SIP against the one that stands behind it."""
    from app.aurea_core import sip as sip_engine

    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "mandates": [], "sips": []}
    sips = (await sip_engine.for_firm(session, firm, household_id=household_id))["sips"]
    rows_sip = {str(s.id): s for s in (await session.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.id.in_([uuid.UUID(s["id"]) for s in sips] or [uuid.uuid4()])))).scalars().all()}
    sip_view = []
    for s in sips:
        if s["status"] != "active":
            continue
        m = await for_sip(session, rows_sip[s["id"]], today)
        sip_view.append({"sip_id": s["id"], "fund": s["fund"], "account": s["account"], "amount": s["amount"], "mandate_state": m["state"], "mandate_label": m["label"],
                         "reference": m["row"].reference if m["row"] else None, "limit": float(m["row"].per_debit_limit) if m["row"] else None, "warning": m["warning"]})
    accounts_ids = [rows_sip[s["id"]].account_id for s in sips] if household_id is not None else None
    rows = await standing(session, firm.id, list(set(accounts_ids)) if accounts_ids is not None else None)
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.id.in_([r.account_id for r in rows] or [uuid.uuid4()])))).scalars().all()}
    links = {l.id: l for l in (await session.execute(select(BankLink).where(BankLink.id.in_([r.bank_link_id for r in rows if r.bank_link_id] or [uuid.uuid4()])))).scalars().all()}
    mandates = [view_row(r, today, accounts.get(r.account_id), links.get(r.bank_link_id)) for r in rows]
    mandates.sort(key=lambda m: (m["state"] != "active", m["reference"]))
    counts = {s: sum(1 for m in mandates if m["state"] == s) for s in STATES if s != "none"}
    return {"applies": True, "as_of": today.isoformat(), "mandates": mandates, "sips": sip_view, "counts": counts,
            "without_mandate": sum(1 for s in sip_view if s["mandate_state"] == "none"),
            "failing": sum(1 for s in sip_view if s["warning"] and s["mandate_state"] != "none"),
            "line": law_figures.cited([LINE_KEY], today)[0],
            "note": "Nothing here registers a mandate or presents a debit. A SIP with no mandate on file is warned, never stopped: not on file is not a no."}
