"""Read side of billing, shared by the API and the conversation tools so there is one copy of
what an invoice, a run and the exception queue look like (a tool never keeps its own copy of
domain logic).

Everything here is scoped to the caller's firm: an id from a request or a language model is
never trusted on its own.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import billing_run
from app.models.billing import BillingRun, Invoice
from app.models.enums import RecommendationStatus
from app.models.governance import Recommendation


def invoice_view(inv: Invoice, *, detail: bool = False, credit_of_number: str | None = None) -> dict:
    out = {
        "id": str(inv.id), "number": inv.invoice_number, "kind": inv.kind, "status": inv.status,
        "run_id": str(inv.run_id), "mandate_id": str(inv.mandate_id) if inv.mandate_id else None,
        "mandate_name": inv.mandate_name,
        "household_id": str(inv.household_id) if inv.household_id else None,
        "household_name": inv.household_name,
        "period_start": inv.period_start.isoformat(), "period_end": inv.period_end.isoformat(),
        "amount": float(inv.amount), "currency": inv.currency,
        "checks": inv.checks or [], "overrides": inv.overrides or [],
        "debit_status": inv.debit_status,
        "shortfall": (inv.debit_detail or {}).get("shortfall"),
        "issued_at": inv.issued_at.isoformat() if inv.issued_at else None, "issued_by": inv.issued_by,
        "credit_of": credit_of_number, "credit_reason": inv.credit_reason,
    }
    if detail:
        calc = inv.calculation or {}
        out["calculation"] = {k: calc.get(k) for k in (
            "schedule", "billing_method", "billing_frequency", "householded", "period_fraction",
            "periods_per_year", "billable_value", "group_value", "annual_fee", "effective_bps",
            "breakdown", "accounts", "valuation")}
    return out


async def _number_of(session: AsyncSession, invoice_id: uuid.UUID | None) -> str | None:
    if invoice_id is None:
        return None
    orig = await session.get(Invoice, invoice_id)
    return orig.invoice_number if orig else None


async def run_view(session: AsyncSession, run: BillingRun, *, with_invoices: bool = False) -> dict:
    rec = None
    if run.agent_run_id:
        rec = (await session.execute(
            select(Recommendation).where(Recommendation.run_id == run.agent_run_id,
                                         Recommendation.agent_key == "billing")
            .order_by(Recommendation.created_at.desc()))).scalars().first()
    out = {
        "id": str(run.id), "as_of": run.as_of.isoformat(), "status": run.status, "trigger": run.trigger,
        "summary": run.summary or {},
        "recommendation_id": str(rec.id) if rec else None,
        "awaiting_decision": bool(rec and rec.status == RecommendationStatus.PROPOSED),
        "approved_by": run.approved_by, "approved_at": run.approved_at.isoformat() if run.approved_at else None,
    }
    if with_invoices:
        rows = (await session.execute(
            select(Invoice).where(Invoice.run_id == run.id, Invoice.status != "void")
            .order_by(Invoice.household_name, Invoice.mandate_name))).scalars().all()
        out["invoices"] = [invoice_view(i, credit_of_number=await _number_of(session, i.credit_of_id)) for i in rows]
    return out


async def overview(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """What a billing officer needs first: the run waiting on them and everything that is wrong."""
    open_run = await billing_run.open_run(session, firm_id)
    held = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm_id, Invoice.status == "held")
        .order_by(Invoice.household_name))).scalars().all()
    unpaid = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm_id, Invoice.kind == "invoice", Invoice.status == "issued",
                              Invoice.debit_status == "insufficient_cash")
        .order_by(Invoice.household_name))).scalars().all()
    recent = (await session.execute(
        select(BillingRun).where(BillingRun.firm_id == firm_id, BillingRun.status != "cancelled")
        .order_by(BillingRun.created_at.desc()).limit(8))).scalars().all()
    from app.aurea_core.billing import preview
    pv = await preview(session, firm_id)
    billed: dict[str, float] = {}
    issued = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm_id, Invoice.status.in_(("issued", "debited", "credited"))
                              | (Invoice.kind == "credit_note")))).scalars().all()
    year = date.today().year
    for i in issued:
        if i.issued_at and i.issued_at.year == year:
            billed[i.currency] = round(billed.get(i.currency, 0.0) + float(i.amount), 2)
    return {
        "open_run": await run_view(session, open_run, with_invoices=True) if open_run else None,
        "exceptions": {
            "no_agreement": pv["no_agreement"],
            "held": [invoice_view(i) for i in held],
            "unpaid": [invoice_view(i) for i in unpaid],
        },
        "recent_runs": [await run_view(session, r) for r in recent],
        "billed_this_year": billed,
    }


async def household_invoices(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> dict:
    rows = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm_id, Invoice.household_id == household_id,
                              Invoice.status.in_(("issued", "debited", "credited", "held")))
        .order_by(Invoice.period_end.desc(), Invoice.invoice_number.desc()))).scalars().all()
    return {"household_id": str(household_id),
            "invoices": [invoice_view(i, credit_of_number=await _number_of(session, i.credit_of_id)) for i in rows]}


async def invoice_detail(session: AsyncSession, firm_id: uuid.UUID, invoice_id: uuid.UUID) -> dict | None:
    inv = await session.get(Invoice, invoice_id)
    if inv is None or inv.firm_id != firm_id:
        return None
    return invoice_view(inv, detail=True, credit_of_number=await _number_of(session, inv.credit_of_id))
