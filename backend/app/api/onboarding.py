"""Acquire & onboard API — onboarding cases (KYC/AML) and book-integration batches."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.aurea_core import disclosures, sample_docs
from app.core.db import get_db, utcnow
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.enums import AgentKey
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.onboarding import (
    BeneficialOwner, BookIntegrationBatch, DisclosureDelivery, OnboardingCase,
    OnboardingDocument, TransferRequest,
)
from app.models.tenant import Firm

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"], dependencies=[Depends(staff_user)])


# ── Onboarding cases ──────────────────────────────────────────────────────────
class CaseCreate(BaseModel):
    prospect_name: str
    is_entity: bool = False
    entity_type: str | None = None
    registration_type: str | None = None
    segment: str = "private_wealth"
    intake: dict = {}


class BeneficialOwnerIn(BaseModel):
    legal_name: str
    dob: str | None = None
    address: str | None = None
    id_number: str | None = None
    ownership_pct: float | None = None
    is_control_person: bool = False
    notes: str | None = None


class AMLRatingIn(BaseModel):
    aml_risk_tier: str  # low | medium | high
    aml_risk_score: float | None = None
    notes: str | None = None


class TransferCreate(BaseModel):
    transfer_type: str           # acat | wire | ach
    direction: str               # in | out
    amount: float | None = None
    asset_description: str | None = None
    custodian: str | None = None
    notes: str | None = None


class DocumentCreate(BaseModel):
    doc_type: str
    raw_text: str | None = None  # if omitted, a sample of this type is generated


def _sla_status(case: OnboardingCase) -> str:
    if not case.created_at or case.status in ("approved", "rejected"):
        return "n/a"
    from app.core.db import utcnow
    from datetime import timedelta
    elapsed = (utcnow() - case.created_at).days
    sla = getattr(case, "sla_days", 30) or 30
    if elapsed >= sla:
        return "breached"
    if elapsed >= sla * 0.8:
        return "at_risk"
    return "on_track"


def _case_dict(
    case: OnboardingCase,
    docs: list[OnboardingDocument] | None = None,
    bos: list[BeneficialOwner] | None = None,
) -> dict:
    sla_days = getattr(case, "sla_days", 30) or 30
    d = {
        "id": str(case.id), "prospect_name": case.prospect_name, "is_entity": case.is_entity,
        "entity_type": case.entity_type,
        "registration_type": getattr(case, "registration_type", None),
        "segment": case.segment, "status": case.status,
        "intake": case.intake, "screening": case.screening,
        "screening_log": getattr(case, "screening_log", None) or [],
        "proposal": case.proposal,
        "exceptions": case.exceptions, "materialized": case.materialized,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "sla_days": sla_days,
        "sla_status": _sla_status(case),
        "nigo_flag": getattr(case, "nigo_flag", False) or False,
        "nigo_reason": getattr(case, "nigo_reason", None),
        "nigo_root_cause": getattr(case, "nigo_root_cause", None),
        "readiness_score": getattr(case, "readiness_score", None),
        "aml_risk_tier": getattr(case, "aml_risk_tier", None),
        "aml_risk_score": float(case.aml_risk_score) if getattr(case, "aml_risk_score", None) else None,
        "edd_status": getattr(case, "edd_status", None),
        "pte_status": getattr(case, "pte_status", None),
        "cip_status": getattr(case, "cip_status", None),
        "cip_score": float(case.cip_score) if getattr(case, "cip_score", None) else None,
        "cip_flags": getattr(case, "cip_flags", None) or [],
        "cip_reference_id": getattr(case, "cip_reference_id", None),
        "custodian_name": getattr(case, "custodian_name", None),
        "custodian_account_id": getattr(case, "custodian_account_id", None),
        "custodian_push_status": getattr(case, "custodian_push_status", None),
        "custodian_push_at": case.custodian_push_at.isoformat() if getattr(case, "custodian_push_at", None) else None,
    }
    if docs is not None:
        d["documents"] = [
            {"id": str(x.id), "doc_type": x.doc_type, "filename": x.filename,
             "extracted": x.extracted, "field_confidence": x.field_confidence,
             "confidence": float(x.confidence or 0), "verified": x.verified, "raw_text": x.raw_text}
            for x in docs
        ]
    if bos is not None:
        d["beneficial_owners"] = [
            {
                "id": str(b.id), "legal_name": b.legal_name, "dob": b.dob,
                "address": b.address, "ownership_pct": float(b.ownership_pct) if b.ownership_pct else None,
                "is_control_person": b.is_control_person, "is_stale": b.is_stale, "notes": b.notes,
            }
            for b in bos
        ]
    return d


@router.get("/cases")
async def list_cases(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
                         .order_by(OnboardingCase.created_at.desc()))
    ).scalars().all()
    return [_case_dict(c) for c in rows]


@router.post("/cases")
async def create_case(
    body: CaseCreate, user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    # Infer is_entity from registration_type when provided.
    is_entity = body.is_entity
    entity_type = body.entity_type
    if body.registration_type in ("trust", "entity_llc", "entity_corp", "entity_partnership"):
        is_entity = True
        entity_type = entity_type or body.registration_type.replace("entity_", "")
    case = OnboardingCase(
        firm_id=firm.id, prospect_name=body.prospect_name, is_entity=is_entity,
        entity_type=entity_type, registration_type=body.registration_type,
        segment=body.segment, intake=body.intake,
    )
    db.add(case)
    await db.flush()
    return _case_dict(case, [], [])


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    docs = (
        await db.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
    ).scalars().all()
    bos = (
        await db.execute(select(BeneficialOwner).where(BeneficialOwner.case_id == case.id))
    ).scalars().all()
    # Attach the latest agent recommendation for this case, if any.
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.subject_id == case.id,
                                         Recommendation.agent_key == AgentKey.ONBOARDING_KYC_AML)
            .order_by(Recommendation.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    out = _case_dict(case, docs, bos)
    out["recommendation_id"] = str(rec.id) if rec else None
    return out


# ── Beneficial owners ─────────────────────────────────────────────────────────
@router.get("/cases/{case_id}/beneficial-owners")
async def list_beneficial_owners(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    rows = (
        await db.execute(select(BeneficialOwner).where(BeneficialOwner.case_id == case_id))
    ).scalars().all()
    return [
        {"id": str(b.id), "legal_name": b.legal_name, "dob": b.dob, "address": b.address,
         "ownership_pct": float(b.ownership_pct) if b.ownership_pct else None,
         "is_control_person": b.is_control_person, "is_stale": b.is_stale, "notes": b.notes}
        for b in rows
    ]


@router.post("/cases/{case_id}/beneficial-owners")
async def add_beneficial_owner(
    case_id: uuid.UUID, body: BeneficialOwnerIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    bo = BeneficialOwner(
        firm_id=firm.id, case_id=case_id, legal_name=body.legal_name,
        dob=body.dob, address=body.address, id_number=body.id_number,
        ownership_pct=body.ownership_pct, is_control_person=body.is_control_person,
        notes=body.notes,
    )
    db.add(bo)
    await db.flush()
    return {"id": str(bo.id), "legal_name": bo.legal_name, "ownership_pct": body.ownership_pct,
            "is_control_person": bo.is_control_person}


@router.delete("/cases/{case_id}/beneficial-owners/{bo_id}", status_code=204)
async def delete_beneficial_owner(
    case_id: uuid.UUID, bo_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    bo = await db.get(BeneficialOwner, bo_id)
    if not bo or bo.case_id != case_id or bo.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Beneficial owner not found")
    await db.delete(bo)


# ── AML risk rating ───────────────────────────────────────────────────────────
@router.put("/cases/{case_id}/aml-risk-rating")
async def set_aml_risk_rating(
    case_id: uuid.UUID, body: AMLRatingIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Manually set or override the AML risk tier on a case (compliance officer action)."""
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    if body.aml_risk_tier not in ("low", "medium", "high"):
        raise HTTPException(status_code=422, detail="aml_risk_tier must be low, medium, or high")
    case.aml_risk_tier = body.aml_risk_tier
    if body.aml_risk_score is not None:
        case.aml_risk_score = body.aml_risk_score
    # Set EDD status based on tier.
    if body.aml_risk_tier in ("medium", "high") and not getattr(case, "edd_status", None):
        case.edd_status = "edd_pending"
    await db.flush()
    return {"aml_risk_tier": case.aml_risk_tier, "edd_status": getattr(case, "edd_status", None)}


@router.post("/cases/{case_id}/run-cip")
async def run_cip(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """A2 — Trigger CIP identity verification via the configured IdentityAdapter."""
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.CIP_IDENTITY_VERIFIER,
                              subject=Subject("onboarding_case", case.id, case.prospect_name),
                              trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {"run_id": str(run.id), "status": run.status,
            "recommendations": [{"id": str(r.id)} for r in recs]}


@router.post("/cases/{case_id}/push-to-custodian")
async def push_to_custodian(
    case_id: uuid.UUID, user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """F6 — Push approved case to custodian account-opening API (single-keying)."""
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.CUSTODIAN_ACCOUNT_OPENER,
                              subject=Subject("onboarding_case", case.id, case.prospect_name),
                              trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {"run_id": str(run.id), "status": run.status,
            "recommendations": [{"id": str(r.id)} for r in recs]}


# ── Disclosure delivery ───────────────────────────────────────────────────────
class DisclosureIn(BaseModel):
    doc_type: str
    method: str = "email"                 # email | portal | in_person | post
    evidence_ref: str | None = None
    notes: str | None = None
    acknowledged: bool = False


_DELIVERY_METHODS = {"email", "portal", "in_person", "post"}


async def _case_or_404(db: AsyncSession, case_id: uuid.UUID, firm: Firm) -> OnboardingCase:
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    return case


@router.get("/cases/{case_id}/disclosures")
async def list_disclosures(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Required disclosures for this case, reconciled against what has been delivered."""
    case = await _case_or_404(db, case_id, firm)
    rows = (await db.execute(
        select(DisclosureDelivery).where(DisclosureDelivery.case_id == case.id)
    )).scalars().all()
    return disclosures.status_for(firm.jurisdiction, case.registration_type, rows)


@router.post("/cases/{case_id}/disclosures")
async def record_disclosure(
    case_id: uuid.UUID, body: DisclosureIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Record that a disclosure was delivered — the evidence a regulator asks for."""
    case = await _case_or_404(db, case_id, firm)
    if body.method not in _DELIVERY_METHODS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown delivery method '{body.method}'. "
                   f"Expected one of: {', '.join(sorted(_DELIVERY_METHODS))}.",
        )
    if body.doc_type not in disclosures.CATALOGUE:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown disclosure '{body.doc_type}'.",
        )
    now = utcnow()
    db.add(DisclosureDelivery(
        firm_id=firm.id, case_id=case.id, doc_type=body.doc_type,
        delivered_at=now, method=body.method, evidence_ref=body.evidence_ref,
        delivered_by=user.email, notes=body.notes,
        acknowledged_at=now if body.acknowledged else None,
    ))
    await db.flush()
    rows = (await db.execute(
        select(DisclosureDelivery).where(DisclosureDelivery.case_id == case.id)
    )).scalars().all()
    await db.commit()
    return disclosures.status_for(firm.jurisdiction, case.registration_type, rows)


@router.delete("/cases/{case_id}/disclosures/{delivery_id}", status_code=204)
async def delete_disclosure(
    case_id: uuid.UUID, delivery_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Remove a delivery record entered in error.

    Deliberately a hard delete rather than a status flag: a delivery log that contains
    retracted entries is worse evidence than one that does not, and the decision ledger
    already records who changed what.
    """
    await _case_or_404(db, case_id, firm)
    row = await db.get(DisclosureDelivery, delivery_id)
    if not row or row.case_id != case_id or row.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Delivery record not found")
    await db.delete(row)
    await db.commit()


# Onboarding-stage agents that can be run against a single case from the case workspace.
# These were previously reachable only via the generic agent-run API, which no UI called —
# so the fields they populate (readiness_score, screening_log, aml_risk_tier, edd_status,
# pte_status) were null on every case and the panels that render them never appeared.
CASE_AGENTS: dict[str, AgentKey] = {
    "nigo_prevention": AgentKey.NIGO_PREVENTION,
    "adverse_media_pep": AgentKey.ADVERSE_MEDIA_PEP,
    "edd_sow_narrator": AgentKey.EDD_SOW_NARRATOR,
    "rollover_pte_documenter": AgentKey.ROLLOVER_PTE_DOCUMENTER,
    "cip_identity_verifier": AgentKey.CIP_IDENTITY_VERIFIER,
    "custodian_account_opener": AgentKey.CUSTODIAN_ACCOUNT_OPENER,
}


@router.post("/cases/{case_id}/run-agent/{agent_slug}")
async def run_case_agent(
    case_id: uuid.UUID,
    agent_slug: str,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Run one onboarding-stage agent against a single case.

    Allowlisted rather than accepting an arbitrary AgentKey, so this cannot be used to
    invoke unrelated agents against an onboarding subject.

    These agents are Tier 1/2, so `act()` does not run here — the agent produces a
    recommendation that a human approves. The response returns the recommendation ids so
    the caller can surface the pending decision instead of appearing to do nothing.
    """
    agent_key = CASE_AGENTS.get(agent_slug)
    if agent_key is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown onboarding agent '{agent_slug}'. "
                   f"Available: {', '.join(sorted(CASE_AGENTS))}.",
        )
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        run = await run_agent(
            db, firm=firm, agent_key=agent_key,
            subject=Subject("onboarding_case", case.id, case.prospect_name),
            trigger="manual",
        )
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {
        "run_id": str(run.id),
        "status": run.status,
        "agent_key": str(agent_key),
        # Tier 1/2 agents stop at a checkpoint; the caller must surface these for approval.
        "awaiting_approval": [{"id": str(r.id), "title": r.title} for r in recs],
    }


# ── Transfers ─────────────────────────────────────────────────────────────────
@router.get("/cases/{case_id}/transfers")
async def list_transfers(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    rows = (await db.execute(
        select(TransferRequest).where(TransferRequest.case_id == case_id)
        .order_by(TransferRequest.created_at.desc())
    )).scalars().all()
    return [_transfer_dict(t) for t in rows]


@router.post("/cases/{case_id}/transfers")
async def create_transfer(
    case_id: uuid.UUID, body: TransferCreate,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """F4 — Submit a new transfer request (ACAT / wire / ACH) via the TransferAdapter."""
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    if body.transfer_type not in ("acat", "wire", "ach"):
        raise HTTPException(status_code=422, detail="transfer_type must be acat, wire, or ach")
    if body.direction not in ("in", "out"):
        raise HTTPException(status_code=422, detail="direction must be in or out")

    from app.aurea_core.integrations import transfer as transfer_integration
    from app.core.db import utcnow
    adapter = transfer_integration.get_adapter()
    result = adapter.submit(
        transfer_type=body.transfer_type,
        direction=body.direction,
        amount=body.amount,
        asset_description=body.asset_description or "",
        custodian=body.custodian,
        case_reference=str(case_id),
    )
    t = TransferRequest(
        firm_id=firm.id, case_id=case_id,
        transfer_type=body.transfer_type, direction=body.direction,
        amount=body.amount, asset_description=body.asset_description,
        status=result.status, provider=adapter.provider_name,
        provider_ref=result.reference_id,
        custodian=result.custodian or body.custodian,
        notes=body.notes, initiated_at=utcnow(),
    )
    db.add(t)
    await db.flush()
    return _transfer_dict(t)


@router.put("/cases/{case_id}/transfers/{transfer_id}/advance")
async def advance_transfer(
    case_id: uuid.UUID, transfer_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Advance a transfer to the next status (mock only — real: comes via webhook)."""
    t = await db.get(TransferRequest, transfer_id)
    if not t or t.case_id != case_id or t.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Transfer not found")
    from app.aurea_core.integrations import transfer as transfer_integration
    from app.aurea_core.integrations.transfer.base import TRANSFER_STATUSES, TERMINAL_STATUSES
    from app.core.db import utcnow
    adapter = transfer_integration.get_adapter(t.provider)
    try:
        result = adapter.advance(t.provider_ref or "")
        t.status = result.status
    except NotImplementedError:
        # Real adapter — just cycle status locally for demo
        if t.status not in TERMINAL_STATUSES:
            idx = TRANSFER_STATUSES.index(t.status) if t.status in TRANSFER_STATUSES else 0
            t.status = TRANSFER_STATUSES[min(idx + 1, len(TRANSFER_STATUSES) - 1)]
    if t.status == "settled":
        t.settled_at = utcnow()
    await db.flush()
    return _transfer_dict(t)


def _transfer_dict(t: TransferRequest) -> dict:
    return {
        "id": str(t.id), "transfer_type": t.transfer_type, "direction": t.direction,
        "amount": float(t.amount) if t.amount else None,
        "asset_description": t.asset_description,
        "status": t.status, "provider": t.provider, "provider_ref": t.provider_ref,
        "custodian": t.custodian, "notes": t.notes,
        "initiated_at": t.initiated_at.isoformat() if t.initiated_at else None,
        "settled_at": t.settled_at.isoformat() if t.settled_at else None,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


@router.post("/cases/{case_id}/documents")
async def add_document(
    case_id: uuid.UUID, body: DocumentCreate, user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    raw = body.raw_text or sample_docs.generate(body.doc_type, case.prospect_name)
    doc = OnboardingDocument(
        firm_id=firm.id, case_id=case.id, doc_type=body.doc_type,
        filename=f"{body.doc_type}_{case.prospect_name.split()[0].lower()}.pdf", raw_text=raw,
    )
    db.add(doc)
    await db.flush()
    return {"id": str(doc.id), "doc_type": doc.doc_type, "filename": doc.filename}


@router.get("/document-templates")
async def document_templates():
    return sample_docs.TEMPLATES


@router.post("/cases/{case_id}/run")
async def run_case(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.ONBOARDING_KYC_AML,
                              subject=Subject("onboarding_case", case.id, case.prospect_name),
                              trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {"run_id": str(run.id), "status": run.status,
            "recommendations": [{"id": str(r.id)} for r in recs]}


# ── Book-integration batches ──────────────────────────────────────────────────
class BatchCreate(BaseModel):
    source_firm: str
    feed: dict | None = None


def _batch_dict(b: BookIntegrationBatch) -> dict:
    return {
        "id": str(b.id), "source_firm": b.source_firm, "status": b.status,
        "feed": b.feed, "mappings": b.mappings, "stats": b.stats, "committed": b.committed,
        "created_at": b.created_at.isoformat() if b.created_at else None,
    }


@router.get("/book-batches")
async def list_batches(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(BookIntegrationBatch).where(BookIntegrationBatch.firm_id == firm.id)
                         .order_by(BookIntegrationBatch.created_at.desc()))
    ).scalars().all()
    return [_batch_dict(b) for b in rows]


@router.post("/book-batches")
async def create_batch(
    body: BatchCreate, user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    from app.aurea_core.sample_book import sample_feed

    feed = body.feed or sample_feed(body.source_firm)
    b = BookIntegrationBatch(firm_id=firm.id, source_firm=body.source_firm, feed=feed)
    db.add(b)
    await db.flush()
    return _batch_dict(b)


@router.get("/book-batches/{batch_id}")
async def get_batch(batch_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    b = await db.get(BookIntegrationBatch, batch_id)
    if not b or b.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.subject_id == b.id,
                                         Recommendation.agent_key == AgentKey.BOOK_INTEGRATION)
            .order_by(Recommendation.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    out = _batch_dict(b)
    out["recommendation_id"] = str(rec.id) if rec else None
    return out


@router.post("/book-batches/{batch_id}/run")
async def run_batch(batch_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    b = await db.get(BookIntegrationBatch, batch_id)
    if not b or b.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Batch not found")
    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.BOOK_INTEGRATION,
                              subject=Subject("book_batch", b.id, b.source_firm), trigger="manual")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    recs = (await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))).scalars().all()
    return {"run_id": str(run.id), "status": run.status,
            "recommendations": [{"id": str(r.id)} for r in recs]}
