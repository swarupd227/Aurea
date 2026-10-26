"""Billing API (L100 §9, L200-5).

Reads are open to staff. Proposing a run is not an approval, so it is open to the people
who run or oversee billing; *approving* one goes through the same exactly-once decision path
as every other proposal (`/api/studio/recommendations/{id}/decide`, rights from
`decision_rights`). Crediting an issued invoice changes money that has already moved, so it
holds the approvers' rights - and require_roles' blanket administrator pass is deliberately
not honoured for it.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import billing, billing_run, billing_views
from app.aurea_core.invoice_pdf import render_invoice_pdf
from app.core import decision_rights
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.billing import BillingRun, Invoice
from app.models.enums import AgentKey
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/billing", tags=["billing"])
StaffDep = Depends(require_roles(*decision_rights.STAFF))
ProposeDep = Depends(require_roles(
    UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.COMPLIANCE, UserRole.ADMIN))


@router.get("/overview")
async def get_overview(user: User = StaffDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await billing_views.overview(db, firm.id)


@router.get("/preview")
async def get_preview(as_of: date | None = None, user: User = StaffDep, firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    """A dry run: what a run on this date would charge, and what is wrong with it. Writes nothing."""
    return await billing.preview(db, firm.id, as_of=as_of)


@router.get("/runs/{run_id}")
async def get_run(run_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                  db: AsyncSession = Depends(get_db)):
    run = await db.get(BillingRun, run_id)
    if run is None or run.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Billing run not found.")
    return await billing_views.run_view(db, run, with_invoices=True)


@router.get("/households/{household_id}/invoices")
async def household_invoices(household_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                             db: AsyncSession = Depends(get_db)):
    return await billing_views.household_invoices(db, firm.id, household_id)


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                      db: AsyncSession = Depends(get_db)):
    view = await billing_views.invoice_detail(db, firm.id, invoice_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return view


@router.get("/invoices/{invoice_id}/pdf")
async def get_invoice_pdf(invoice_id: uuid.UUID, user: User = StaffDep, firm: Firm = Depends(current_firm),
                          db: AsyncSession = Depends(get_db)):
    view = await billing_views.invoice_detail(db, firm.id, invoice_id)
    if view is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    pdf = render_invoice_pdf(view, firm_name=firm.name, accent=(firm.branding or {}).get("accent", "#c8a35e"),
                             base_currency=firm.base_currency or "NZD")
    name = (view["number"] or "invoice-draft").replace(" ", "_")
    return Response(content=pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.post("/runs/propose")
async def propose_run(user: User = ProposeDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Have the billing agent prepare a run. Nothing is issued or taken until it is approved."""
    from app.atlas.base import Subject
    from app.atlas.runtime import AgentPausedError, run_agent

    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.BILLING,
                              subject=Subject("firm", firm.id, firm.name), trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    proposed = (await db.execute(select(BillingRun).where(BillingRun.agent_run_id == run.id))).scalars().first()
    if proposed is not None:
        return {"proposed": True, "run": await billing_views.run_view(db, proposed, with_invoices=True)}
    waiting = await billing_run.open_run(db, firm.id)
    return {"proposed": False, "run": await billing_views.run_view(db, waiting) if waiting else None,
            "reason": ("A run is already awaiting approval." if waiting
                       else "Nothing is due to be billed.")}


class CreditIn(BaseModel):
    reason: str


@router.post("/invoices/{invoice_id}/credit")
async def credit_invoice(invoice_id: uuid.UUID, body: CreditIn, user: User = StaffDep,
                         firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Offset an issued invoice in full with a credit note; cash already taken is returned."""
    try:
        decision_rights.check(user.role, AgentKey.BILLING, "approve")
    except decision_rights.DecisionForbidden as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    inv = await db.get(Invoice, invoice_id)
    if inv is None or inv.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    if not body.reason.strip():
        raise HTTPException(status_code=422, detail="A reason is required to credit an invoice.")
    try:
        note = await billing_run.credit_invoice(db, inv, reason=body.reason.strip(), by=user.email)
    except billing_run.BillingError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return billing_views.invoice_view(note, detail=True, credit_of_number=inv.invoice_number)
