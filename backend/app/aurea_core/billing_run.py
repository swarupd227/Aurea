"""The billing run lifecycle (L200-5): propose, approve and issue, take the fee, credit.

`billing.preview` works out what is due. This module makes it real, and keeps the controls
that make a bill defensible:

  proposed -> issued     A run is proposed by the billing agent and issued only when a person
                         approves it. Locked and re-read on approval, so two approvals arriving
                         together cannot both bill the client.
  held                   A bill with a block on it is not issued. Only a stale valuation may be
                         knowingly accepted by the approver (recorded on the invoice); a currency
                         we cannot convert or an empty account never can.
  debit                  Taken from the mandate's own cash and no further: no overdraft, no part
                         payment, no sale of assets to raise the cash. A shortfall is recorded.
  credit                 An issued invoice is never edited or deleted. It is offset in full by a
                         credit note, and cash already taken is put back as its own entry - the
                         rule a settled trade already follows: offset, never reversed.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.billing import preview
from app.core.db import utcnow
from app.models.billing import BillingRun, Invoice
from app.models.enums import RecommendationStatus
from app.models.governance import Recommendation
from app.models.graph import Account
from app.models.portfolio import Transaction

# The only block an approver may knowingly accept. A currency we cannot convert, an account
# with nothing in it, or an arrangement not in force are errors, not judgement calls.
OVERRIDABLE_BLOCKS = frozenset({"stale_valuation"})


class BillingError(Exception):
    """A billing action that cannot be taken, with a reason fit to show the user."""


def _d(x) -> Decimal:
    return Decimal(str(x if x is not None else 0))


async def _next_number(session: AsyncSession, firm_id: uuid.UUID, prefix: str, as_of: date) -> str:
    stem = f"{prefix}-{as_of:%Y%m}-"
    existing = (await session.execute(
        select(Invoice.invoice_number).where(Invoice.firm_id == firm_id, Invoice.invoice_number.like(f"{stem}%"))
    )).scalars().all()
    top = max((int(n.rsplit("-", 1)[1]) for n in existing if n), default=0)
    return f"{stem}{top + 1:04d}"


async def open_run(session: AsyncSession, firm_id: uuid.UUID) -> BillingRun | None:
    """The run awaiting a decision, if any. A run whose proposal was dismissed can never be
    approved, so it is cancelled here rather than blocking every run after it."""
    run = (await session.execute(
        select(BillingRun).where(BillingRun.firm_id == firm_id, BillingRun.status == "proposed")
    )).scalars().first()
    if run is None or run.agent_run_id is None:
        return run
    recs = (await session.execute(
        select(Recommendation).where(Recommendation.run_id == run.agent_run_id)
    )).scalars().all()
    if recs and all(r.status == RecommendationStatus.DISMISSED for r in recs):
        run.status = "cancelled"
        await session.execute(
            update(Invoice).where(Invoice.run_id == run.id, Invoice.status == "draft").values(status="void")
        )
        await session.flush()
        return None
    return run


async def propose_run(
    session: AsyncSession, firm_id: uuid.UUID, *, as_of: date | None = None, trigger: str = "manual",
    agent_run_id: uuid.UUID | None = None,
) -> BillingRun | None:
    """Persist what a run would bill as draft invoices awaiting approval.

    Returns None when there is nothing to bill. Raises BillingError if a run is already
    waiting: two open runs would double-bill the same period the moment both were approved."""
    as_of = as_of or utcnow().date()
    waiting = await open_run(session, firm_id)
    if waiting is not None:
        raise BillingError(f"A billing run for {waiting.as_of.isoformat()} is already awaiting approval.")

    pv = await preview(session, firm_id, as_of=as_of)

    # A period already billed is not billed again. A held bill has not been billed, so it is
    # superseded by the new draft rather than left to pile up beside it.
    existing = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm_id, Invoice.kind == "invoice",
                              Invoice.status.in_(("issued", "debited", "held")))
    )).scalars().all()
    billed = {(str(i.arrangement_id), i.period_start.isoformat()) for i in existing if i.status != "held"}
    held = {(str(i.arrangement_id), i.period_start.isoformat()): i for i in existing if i.status == "held"}

    entries, already = [], 0
    for e in pv["arrangements"]:
        if (e["arrangement_id"], e["period_start"]) in billed:
            already += 1
            continue
        entries.append(e)
    if not entries:
        return None

    by_currency: dict[str, float] = defaultdict(float)
    for e in entries:
        if not e["blocked"] and e["currency"]:
            by_currency[e["currency"]] += e["amount"]
    run = BillingRun(
        firm_id=firm_id, as_of=as_of, status="proposed", trigger=trigger, agent_run_id=agent_run_id,
        summary={"invoices": len(entries), "blocked": sum(1 for e in entries if e["blocked"]),
                 "amount_by_currency": {k: round(v, 2) for k, v in by_currency.items()},
                 "already_billed": already, "no_agreement": pv["no_agreement"]},
    )
    session.add(run)
    await session.flush()

    for e in entries:
        old = held.get((e["arrangement_id"], e["period_start"]))
        if old is not None:
            old.status = "void"
        session.add(Invoice(
            firm_id=firm_id, run_id=run.id, kind="invoice", status="draft",
            arrangement_id=uuid.UUID(e["arrangement_id"]), mandate_id=uuid.UUID(e["mandate_id"]),
            household_id=uuid.UUID(e["household_id"]) if e["household_id"] else None,
            mandate_name=e["mandate_name"], household_name=e["household_name"],
            period_start=date.fromisoformat(e["period_start"]), period_end=date.fromisoformat(e["period_end"]),
            amount=e["amount"], currency=e["currency"] or "NZD", calculation=e, checks=e["checks"],
        ))
    await session.flush()
    return run


async def post_debit(session: AsyncSession, invoice: Invoice) -> dict:
    """Take an issued invoice's amount from the mandate's cash - never more than is there.

    No overdraft, no part-payment and no sale of assets to raise cash: if the cash is not
    there the bill stays issued and unpaid and the shortfall is recorded for a person to
    deal with. A fee that quietly liquidated a client's holdings would be a trade nobody
    approved."""
    amount = _d(invoice.amount)
    if amount <= 0:
        invoice.debit_status, invoice.debit_detail = "nothing_due", {}
        return {"status": "nothing_due"}
    accounts = (await session.execute(
        select(Account).where(Account.firm_id == invoice.firm_id, Account.mandate_id == invoice.mandate_id)
    )).scalars().all()
    accounts.sort(key=lambda a: -_d(a.cash_balance))
    available = sum((_d(a.cash_balance) for a in accounts), Decimal(0))
    if available < amount:
        invoice.debit_status = "insufficient_cash"
        invoice.debit_detail = {"needed": float(amount), "available": float(available),
                                "shortfall": float(amount - available)}
        return {"status": "insufficient_cash", **invoice.debit_detail}

    remaining, taken = amount, []
    for acc in accounts:
        take = min(_d(acc.cash_balance), remaining)
        if take <= 0:
            continue
        acc.cash_balance = _d(acc.cash_balance) - take
        session.add(Transaction(
            firm_id=invoice.firm_id, account_id=acc.id, instrument_id=None, txn_type="fee",
            quantity=0, price=0, amount=take, trade_date=utcnow().date(),
            lineage={"invoice_id": str(invoice.id), "invoice_number": invoice.invoice_number,
                     "period_start": invoice.period_start.isoformat(), "period_end": invoice.period_end.isoformat()},
        ))
        taken.append({"account_id": str(acc.id), "amount": float(take)})
        remaining -= take
    invoice.status, invoice.debit_status, invoice.debited_at = "debited", "posted", utcnow()
    invoice.debit_detail = {"accounts": taken}
    return {"status": "posted", "accounts": taken}


async def approve_run(
    session: AsyncSession, run: BillingRun, *, approved_by: str,
    acknowledge: list[str] | None = None, post_debits: bool = True,
) -> dict:
    """Issue the run's invoices and take the fees. Once only: the run is locked and its
    status re-read, so two approvals arriving together cannot both bill the client."""
    run = (await session.execute(
        select(BillingRun).where(BillingRun.id == run.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    if run.status != "proposed":
        raise BillingError(f"This billing run is already {run.status}.")

    acknowledged = set(acknowledge or []) & OVERRIDABLE_BLOCKS
    drafts = (await session.execute(
        select(Invoice).where(Invoice.run_id == run.id, Invoice.status == "draft").order_by(Invoice.mandate_name)
    )).scalars().all()
    now = utcnow()
    counts = {"issued": 0, "held": 0, "debited": 0, "insufficient_cash": 0, "overridden": 0}
    issued_by_currency: dict[str, float] = defaultdict(float)

    for inv in drafts:
        blocks = [c["code"] for c in (inv.checks or []) if c["severity"] == "block"]
        if blocks and not all(b in acknowledged for b in blocks):
            inv.status = "held"
            counts["held"] += 1
            continue
        if blocks:
            inv.overrides = [{"code": b, "accepted_by": approved_by, "at": now.isoformat()} for b in blocks]
            counts["overridden"] += 1
        inv.invoice_number = await _next_number(session, run.firm_id, "INV", run.as_of)
        inv.status, inv.issued_at, inv.issued_by = "issued", now, approved_by
        await session.flush()
        counts["issued"] += 1
        issued_by_currency[inv.currency] += float(inv.amount)
        if post_debits:
            outcome = await post_debit(session, inv)
            if outcome["status"] == "posted":
                counts["debited"] += 1
            elif outcome["status"] == "insufficient_cash":
                counts["insufficient_cash"] += 1

    run.status, run.approved_by, run.approved_at = "issued", approved_by, now
    run.summary = {**(run.summary or {}), "result": {**counts, "issued_by_currency": {
        k: round(v, 2) for k, v in issued_by_currency.items()}}}
    await session.flush()
    return run.summary["result"]


async def credit_invoice(session: AsyncSession, invoice: Invoice, *, reason: str, by: str) -> Invoice:
    """Offset an issued invoice in full. The invoice is kept; a credit note is added, and any
    cash taken for it is put back as its own entry - nothing is edited or deleted."""
    inv = (await session.execute(
        select(Invoice).where(Invoice.id == invoice.id).with_for_update()
        .execution_options(populate_existing=True)
    )).scalar_one()
    if inv.kind != "invoice" or inv.status not in ("issued", "debited"):
        raise BillingError(f"A {inv.status} {inv.kind.replace('_', ' ')} cannot be credited.")

    now = utcnow()
    note = Invoice(
        firm_id=inv.firm_id, run_id=inv.run_id, kind="credit_note", credit_of_id=inv.id, status="issued",
        arrangement_id=inv.arrangement_id, mandate_id=inv.mandate_id, household_id=inv.household_id,
        mandate_name=inv.mandate_name, household_name=inv.household_name,
        period_start=inv.period_start, period_end=inv.period_end, amount=-_d(inv.amount), currency=inv.currency,
        calculation={"credit_of": inv.invoice_number}, checks=[], issued_at=now, issued_by=by,
        credit_reason=reason, invoice_number=await _next_number(session, inv.firm_id, "CN", now.date()),
    )
    session.add(note)
    await session.flush()

    if inv.debit_status == "posted":
        paid = (inv.debit_detail or {}).get("accounts", [])
        for row in paid:
            acc = await session.get(Account, uuid.UUID(row["account_id"]))
            if acc is None:
                continue
            acc.cash_balance = _d(acc.cash_balance) + _d(row["amount"])
            session.add(Transaction(
                firm_id=inv.firm_id, account_id=acc.id, instrument_id=None, txn_type="fee_credit",
                quantity=0, price=0, amount=_d(row["amount"]), trade_date=now.date(),
                lineage={"invoice_id": str(inv.id), "credit_note_id": str(note.id),
                         "credit_note_number": note.invoice_number, "reason": reason},
            ))
        note.debit_status, note.debit_detail, note.debited_at = "posted", {"accounts": paid}, now
    inv.status = "credited"
    await session.flush()
    return note


async def credit_run(session: AsyncSession, run: BillingRun, *, reason: str, by: str) -> dict:
    invoices = (await session.execute(
        select(Invoice).where(Invoice.run_id == run.id, Invoice.kind == "invoice",
                              Invoice.status.in_(("issued", "debited")))
    )).scalars().all()
    notes = [await credit_invoice(session, i, reason=reason, by=by) for i in invoices]
    if run.status == "issued":
        run.status = "credited"
    await session.flush()
    return {"credit_notes": len(notes), "credited": round(sum(-float(n.amount) for n in notes), 2)}
