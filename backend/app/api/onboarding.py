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
from app.aurea_core import sample_docs
from app.core.db import get_db
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.enums import AgentKey
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.onboarding import BookIntegrationBatch, OnboardingCase, OnboardingDocument
from app.models.tenant import Firm

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"], dependencies=[Depends(staff_user)])


# ── Onboarding cases ──────────────────────────────────────────────────────────
class CaseCreate(BaseModel):
    prospect_name: str
    is_entity: bool = False
    entity_type: str | None = None
    segment: str = "private_wealth"
    intake: dict = {}


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


def _case_dict(case: OnboardingCase, docs: list[OnboardingDocument] | None = None) -> dict:
    sla_days = getattr(case, "sla_days", 30) or 30
    d = {
        "id": str(case.id), "prospect_name": case.prospect_name, "is_entity": case.is_entity,
        "entity_type": case.entity_type, "segment": case.segment, "status": case.status,
        "intake": case.intake, "screening": case.screening, "proposal": case.proposal,
        "exceptions": case.exceptions, "materialized": case.materialized,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "sla_days": sla_days,
        "sla_status": _sla_status(case),
    }
    if docs is not None:
        d["documents"] = [
            {"id": str(x.id), "doc_type": x.doc_type, "filename": x.filename,
             "extracted": x.extracted, "field_confidence": x.field_confidence,
             "confidence": float(x.confidence or 0), "verified": x.verified, "raw_text": x.raw_text}
            for x in docs
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
    case = OnboardingCase(
        firm_id=firm.id, prospect_name=body.prospect_name, is_entity=body.is_entity,
        entity_type=body.entity_type, segment=body.segment, intake=body.intake,
    )
    db.add(case)
    await db.flush()
    return _case_dict(case, [])


@router.get("/cases/{case_id}")
async def get_case(case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    case = await db.get(OnboardingCase, case_id)
    if not case or case.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Case not found")
    docs = (
        await db.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
    ).scalars().all()
    # Attach the latest agent recommendation for this case, if any.
    rec = (
        await db.execute(
            select(Recommendation).where(Recommendation.subject_id == case.id,
                                         Recommendation.agent_key == AgentKey.ONBOARDING_KYC_AML)
            .order_by(Recommendation.created_at.desc()).limit(1)
        )
    ).scalar_one_or_none()
    out = _case_dict(case, docs)
    out["recommendation_id"] = str(rec.id) if rec else None
    return out


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
