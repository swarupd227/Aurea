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
from app.aurea_core import parties as parties_core
from app.aurea_core import fees as fees_core
from app.aurea_core import gates as gates_core
from app.aurea_core import onboarding_metrics as metrics_core
from app.aurea_core import tracks as tracks_core
from app.aurea_core import transfer_controls
from app.core.db import get_db, utcnow
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.enums import AgentKey, PartyRole
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.onboarding import (
    BeneficialOwner, BookIntegrationBatch, DisclosureDelivery, FeeSchedule, HeldAwayAsset,
    OnboardingCase, OnboardingDocument, OnboardingParty, TransferRequest,
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
    # L200 §5 controls — captured at creation so the checks can run pre-submission.
    delivering_firm: str | None = None
    delivering_account_title: str | None = None
    is_third_party: bool = False


class CallbackIn(BaseModel):
    callback_number: str
    note: str | None = None


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


async def _gates_for(db: AsyncSession, firm: Firm, case: OnboardingCase) -> dict:
    """Activation gates for a case. Shared by the API and the agent's act()."""
    party_rows = await _load_parties(db, case.id)
    disc_rows = (await db.execute(
        select(DisclosureDelivery).where(DisclosureDelivery.case_id == case.id)
    )).scalars().all()
    held_away_n = len((await db.execute(
        select(HeldAwayAsset.id).where(HeldAwayAsset.case_id == case.id)
    )).scalars().all())
    xfer_rows = list((await db.execute(
        select(TransferRequest).where(TransferRequest.case_id == case.id)
    )).scalars().all())
    return gates_core.evaluate(
        case,
        party_status=parties_core.completeness_for(case.registration_type, party_rows),
        disclosure_status=disclosures.status_for(
            firm.jurisdiction, case.registration_type, list(disc_rows)
        ),
        fee_status=await _fee_status(db, case),
        held_away_count=held_away_n,
        transfer_status=transfer_controls.status_for(
            xfer_rows, party_names=[p.legal_name for p in party_rows]
        ),
    )


@router.get("/cases/{case_id}/gates")
async def list_gates(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """What must be true before this account can be activated, and what is not yet."""
    case = await _case_or_404(db, case_id, firm)
    return await _gates_for(db, firm, case)


async def _tracks_for(db: AsyncSession, firm: Firm, case: OnboardingCase) -> dict:
    """The four parallel tracks for a case, derived from its records.

    Derived rather than stored, so the tracks cannot drift from the underlying evidence
    and existing cases are correct without a migration. See aurea_core/tracks.py.
    """
    party_rows = await _load_parties(db, case.id)
    disc_rows = (await db.execute(
        select(DisclosureDelivery).where(DisclosureDelivery.case_id == case.id)
    )).scalars().all()
    doc_rows = (await db.execute(
        select(OnboardingDocument).where(OnboardingDocument.case_id == case.id)
    )).scalars().all()
    xfer_rows = (await db.execute(
        select(TransferRequest).where(TransferRequest.case_id == case.id)
    )).scalars().all()
    return tracks_core.evaluate(
        case,
        party_status=parties_core.completeness_for(case.registration_type, party_rows),
        disclosure_status=disclosures.status_for(
            firm.jurisdiction, case.registration_type, list(disc_rows)
        ),
        documents=list(doc_rows),
        transfers=list(xfer_rows),
    )


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
        # Track A — engagement, agreement, IPS acceptance.
        "engagement_type": getattr(case, "engagement_type", None),
        "discretion_granted": getattr(case, "discretion_granted", None),
        "proxy_voting": getattr(case, "proxy_voting", None),
        "agreement_status": getattr(case, "agreement_status", None),
        "agreement_envelope_id": getattr(case, "agreement_envelope_id", None),
        "agreement_signed_at": case.agreement_signed_at.isoformat() if getattr(case, "agreement_signed_at", None) else None,
        "ips_accepted_at": case.ips_accepted_at.isoformat() if getattr(case, "ips_accepted_at", None) else None,
        "ips_accepted_by": getattr(case, "ips_accepted_by", None),
        "held_away_none_declared": getattr(case, "held_away_none_declared", False),
        "activated_at": case.activated_at.isoformat() if getattr(case, "activated_at", None) else None,
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
    out = []
    for c in rows:
        d = _case_dict(c)
        # The board needs the tracks to show what is actually blocking each case, and
        # who owns it — a single status cannot express four parallel tracks.
        d["tracks"] = await _tracks_for(db, firm, c)
        out.append(d)
    return out


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
    out["tracks"] = await _tracks_for(db, firm, case)
    return out


# ── Beneficial owners ─────────────────────────────────────────────────────────
# Kept for the existing UI, but now backed by OnboardingParty(role='beneficial_owner')
# so there is a single party model rather than two sources of truth.
@router.get("/cases/{case_id}/beneficial-owners")
async def list_beneficial_owners(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await _case_or_404(db, case_id, firm)
    rows = (await db.execute(
        select(OnboardingParty).where(
            OnboardingParty.case_id == case.id,
            OnboardingParty.role == PartyRole.BENEFICIAL_OWNER,
        )
    )).scalars().all()
    return [
        {"id": str(b.id), "legal_name": b.legal_name, "dob": b.dob, "address": b.address,
         "ownership_pct": float(b.ownership_pct) if b.ownership_pct is not None else None,
         "is_control_person": b.is_control_person, "is_stale": b.is_stale, "notes": b.notes,
         "screening_status": b.screening_status or "not_screened"}
        for b in rows
    ]


@router.post("/cases/{case_id}/beneficial-owners")
async def add_beneficial_owner(
    case_id: uuid.UUID, body: BeneficialOwnerIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    case = await _case_or_404(db, case_id, firm)
    party = OnboardingParty(
        firm_id=firm.id, case_id=case.id, role=PartyRole.BENEFICIAL_OWNER,
        legal_name=body.legal_name, dob=body.dob, address=body.address,
        id_number=body.id_number, ownership_pct=body.ownership_pct,
        is_control_person=body.is_control_person, notes=body.notes,
        screening_status="not_screened",
    )
    db.add(party)
    await db.flush()
    await db.commit()
    return {"id": str(party.id), "legal_name": party.legal_name,
            "ownership_pct": body.ownership_pct,
            "is_control_person": party.is_control_person}


@router.delete("/cases/{case_id}/beneficial-owners/{bo_id}", status_code=204)
async def delete_beneficial_owner(
    case_id: uuid.UUID, bo_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    bo = await db.get(OnboardingParty, bo_id)
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


@router.get("/metrics")
async def onboarding_metrics(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Operating metrics for the onboarding book (L200 §4).

    Derived from the timestamps the evidence already carries, so the numbers cannot
    disagree with the records they describe.
    """
    cases = list((await db.execute(
        select(OnboardingCase).where(OnboardingCase.firm_id == firm.id)
    )).scalars().all())
    case_ids = [c.id for c in cases]

    parties_by_case: dict = {}
    disclosures_by_case: dict = {}
    transfers_by_case: dict = {}
    if case_ids:
        for p in (await db.execute(
            select(OnboardingParty).where(OnboardingParty.case_id.in_(case_ids))
        )).scalars().all():
            parties_by_case.setdefault(p.case_id, []).append(p)
        for d in (await db.execute(
            select(DisclosureDelivery).where(DisclosureDelivery.case_id.in_(case_ids))
        )).scalars().all():
            disclosures_by_case.setdefault(d.case_id, []).append(d)
        for t in (await db.execute(
            select(TransferRequest).where(TransferRequest.case_id.in_(case_ids))
        )).scalars().all():
            transfers_by_case.setdefault(t.case_id, []).append(t)

    metrics = metrics_core.compute(
        cases,
        parties_by_case=parties_by_case,
        disclosures_by_case=disclosures_by_case,
        transfers_by_case=transfers_by_case,
    )

    # Which controls block most often across the open book.
    open_cases = [c for c in cases if c.status not in ("approved", "rejected")]
    gate_results = [await _gates_for(db, firm, c) for c in open_cases]
    metrics["top_blockers"] = metrics_core.blocker_frequency(gate_results)
    return metrics


# ── Track A: engagement, agreement, IPS acceptance ────────────────────────────
ENGAGEMENT_TYPES = {
    "discretionary_advisory": "Discretionary advisory",
    "non_discretionary_advisory": "Non-discretionary advisory",
    "brokerage": "Brokerage",
    "financial_planning": "Financial planning only",
    "trust_fiduciary": "Trust / fiduciary",
}


class EngagementIn(BaseModel):
    engagement_type: str
    discretion_granted: bool | None = None
    proxy_voting: str | None = None       # firm | client | not_elected


class AgreementIn(BaseModel):
    action: str                           # send | sign | decline
    envelope_id: str | None = None


@router.get("/engagement-types")
async def engagement_types():
    """The relationship types an account can sit under (L200 Track A step 1)."""
    return [{"value": k, "label": v} for k, v in ENGAGEMENT_TYPES.items()]


@router.put("/cases/{case_id}/engagement")
async def set_engagement(
    case_id: uuid.UUID, body: EngagementIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Define the engagement. Dual registrants must decide which 'hat' each account sits
    under, because it drives every downstream disclosure."""
    case = await _case_or_404(db, case_id, firm)
    if body.engagement_type not in ENGAGEMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown engagement type. Expected one of: {', '.join(ENGAGEMENT_TYPES)}.",
        )
    case.engagement_type = body.engagement_type
    if body.discretion_granted is not None:
        case.discretion_granted = body.discretion_granted
    if body.proxy_voting:
        case.proxy_voting = body.proxy_voting
    await db.commit()
    return {"engagement_type": case.engagement_type,
            "discretion_granted": case.discretion_granted,
            "proxy_voting": case.proxy_voting}


@router.post("/cases/{case_id}/agreement")
async def advisory_agreement(
    case_id: uuid.UUID, body: AgreementIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Advance the advisory agreement (L200 Track A step 3).

    Models the e-signature envelope lifecycle without a provider: the envelope reference is
    recorded so the evidence trail exists, and swapping in DocuSign or OneSpan later means
    populating envelope_id from their API rather than reshaping the model.
    """
    case = await _case_or_404(db, case_id, firm)
    now = utcnow()
    if body.action == "send":
        case.agreement_status = "sent"
        case.agreement_sent_at = now
        case.agreement_envelope_id = body.envelope_id or f"env-{case.id.hex[:12]}"
    elif body.action == "sign":
        if case.agreement_status not in ("sent", "signed"):
            raise HTTPException(
                status_code=409,
                detail="The agreement must be sent before it can be signed.",
            )
        case.agreement_status = "signed"
        case.agreement_signed_at = now
    elif body.action == "decline":
        case.agreement_status = "declined"
    else:
        raise HTTPException(status_code=400, detail="Action must be send, sign or decline.")
    await db.commit()
    return {"agreement_status": case.agreement_status,
            "envelope_id": case.agreement_envelope_id,
            "sent_at": case.agreement_sent_at.isoformat() if case.agreement_sent_at else None,
            "signed_at": case.agreement_signed_at.isoformat() if case.agreement_signed_at else None}


@router.post("/cases/{case_id}/ips/accept")
async def accept_ips(
    case_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Accept the IPS — the suitability anchor for the initial implementation.

    Separate from the agent's proposal on purpose: drafting is automated, accepting is a
    human act with a name attached to it.
    """
    case = await _case_or_404(db, case_id, firm)
    if not (case.proposal or {}).get("mandate"):
        raise HTTPException(
            status_code=409,
            detail="No IPS proposal to accept — run the onboarding agent first.",
        )
    case.ips_accepted_at = utcnow()
    case.ips_accepted_by = user.email
    await db.commit()
    return {"ips_accepted_at": case.ips_accepted_at.isoformat(),
            "ips_accepted_by": case.ips_accepted_by}


# ── Held-away assets ──────────────────────────────────────────────────────────
class HeldAwayIn(BaseModel):
    institution: str
    account_type: str | None = None
    approx_value: float | None = None
    currency: str = "NZD"
    source: str = "client_declared"
    will_transfer: bool = False
    notes: str | None = None


@router.get("/cases/{case_id}/held-away")
async def list_held_away(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await _case_or_404(db, case_id, firm)
    rows = (await db.execute(
        select(HeldAwayAsset).where(HeldAwayAsset.case_id == case.id)
    )).scalars().all()
    return {
        "assets": [
            {"id": str(a.id), "institution": a.institution, "account_type": a.account_type,
             "approx_value": float(a.approx_value) if a.approx_value is not None else None,
             "currency": a.currency, "source": a.source,
             "will_transfer": a.will_transfer, "notes": a.notes}
            for a in rows
        ],
        "total_value": sum(float(a.approx_value or 0) for a in rows),
        "none_declared": case.held_away_none_declared,
        "captured_at": case.held_away_captured_at.isoformat() if case.held_away_captured_at else None,
    }


@router.post("/cases/{case_id}/held-away")
async def add_held_away(
    case_id: uuid.UUID, body: HeldAwayIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    case = await _case_or_404(db, case_id, firm)
    db.add(HeldAwayAsset(
        firm_id=firm.id, case_id=case.id, institution=body.institution,
        account_type=body.account_type, approx_value=body.approx_value,
        currency=body.currency, source=body.source,
        will_transfer=body.will_transfer, notes=body.notes,
    ))
    case.held_away_captured_at = utcnow()
    case.held_away_none_declared = False
    await db.commit()
    return await list_held_away(case_id, firm, db)


@router.post("/cases/{case_id}/held-away/declare-none")
async def declare_no_held_away(
    case_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Record that the client was asked and holds nothing elsewhere.

    Distinct from never having asked — only an explicit declaration satisfies the control
    against advising on a partial balance sheet.
    """
    case = await _case_or_404(db, case_id, firm)
    existing = (await db.execute(
        select(HeldAwayAsset.id).where(HeldAwayAsset.case_id == case.id)
    )).scalars().all()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"{len(existing)} held-away asset(s) already recorded — remove them "
                   "before declaring none.",
        )
    case.held_away_none_declared = True
    case.held_away_captured_at = utcnow()
    await db.commit()
    return await list_held_away(case_id, firm, db)


@router.delete("/cases/{case_id}/held-away/{asset_id}", status_code=204)
async def delete_held_away(
    case_id: uuid.UUID, asset_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    await _case_or_404(db, case_id, firm)
    row = await db.get(HeldAwayAsset, asset_id)
    if not row or row.case_id != case_id or row.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Held-away asset not found")
    await db.delete(row)
    await db.commit()


# ── Fee schedule ──────────────────────────────────────────────────────────────
class FeeAssignIn(BaseModel):
    fee_schedule_id: uuid.UUID
    billing_method: str = "arrears"
    billing_frequency: str = "quarterly"
    householding: bool = False
    billable_aum: float | None = None


async def _fee_status(db: AsyncSession, case: OnboardingCase) -> dict:
    schedule = (
        await db.get(FeeSchedule, case.fee_schedule_id) if case.fee_schedule_id else None
    )
    return fees_core.status_for(case, schedule)


@router.get("/fee-schedules")
async def list_fee_schedules(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """The firm's fee-schedule library — selected from, never typed per case."""
    rows = (await db.execute(
        select(FeeSchedule).where(FeeSchedule.firm_id == firm.id, FeeSchedule.is_active.is_(True))
        .order_by(FeeSchedule.code)
    )).scalars().all()
    return [
        {"id": str(f.id), "code": f.code, "name": f.name, "fee_type": f.fee_type,
         "tiers": f.tiers,
         "flat_bps": float(f.flat_bps) if f.flat_bps is not None else None,
         "flat_fee": float(f.flat_fee) if f.flat_fee is not None else None,
         "minimum_annual_fee": (
             float(f.minimum_annual_fee) if f.minimum_annual_fee is not None else None
         ),
         "currency": f.currency, "notes": f.notes}
        for f in rows
    ]


@router.get("/cases/{case_id}/fee")
async def get_case_fee(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    case = await _case_or_404(db, case_id, firm)
    return await _fee_status(db, case)


@router.put("/cases/{case_id}/fee")
async def assign_case_fee(
    case_id: uuid.UUID, body: FeeAssignIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Assign the fee schedule (the 'maker' half of maker/checker).

    Re-assigning always clears any existing confirmation: a fee changed after sign-off has
    not been checked, and silently keeping the old confirmation is exactly how a mis-set
    fee reaches the first bill.
    """
    case = await _case_or_404(db, case_id, firm)
    schedule = await db.get(FeeSchedule, body.fee_schedule_id)
    if not schedule or schedule.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Fee schedule not found")
    if body.billing_method not in fees_core.BILLING_METHODS:
        raise HTTPException(status_code=400, detail=f"Unknown billing method '{body.billing_method}'.")
    if body.billing_frequency not in fees_core.BILLING_FREQUENCIES:
        raise HTTPException(status_code=400, detail=f"Unknown billing frequency '{body.billing_frequency}'.")

    case.fee_schedule_id = schedule.id
    case.billing_method = body.billing_method
    case.billing_frequency = body.billing_frequency
    case.householding = body.householding
    case.billable_aum = body.billable_aum
    case.fee_set_by = user.email
    case.fee_set_at = utcnow()
    case.fee_confirmed_by = None
    case.fee_confirmed_at = None
    await db.flush()
    result = await _fee_status(db, case)
    await db.commit()
    return result


@router.post("/cases/{case_id}/fee/confirm")
async def confirm_case_fee(
    case_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Confirm the fee schedule (the 'checker' half).

    The confirmer must be a different person from the one who set it — that separation is
    the entire point of maker/checker, and L200 names it as the control for mis-set fees.
    """
    case = await _case_or_404(db, case_id, firm)
    if not case.fee_schedule_id:
        raise HTTPException(status_code=400, detail="No fee schedule assigned to confirm.")
    if case.fee_set_by and case.fee_set_by.lower() == user.email.lower():
        raise HTTPException(
            status_code=409,
            detail="Maker/checker — the fee schedule must be confirmed by someone other "
                   f"than the person who set it ({case.fee_set_by}).",
        )
    status = await _fee_status(db, case)
    if status["problems"]:
        raise HTTPException(
            status_code=409,
            detail="Resolve the fee validation problems before confirming: "
                   + "; ".join(status["problems"]),
        )
    case.fee_confirmed_by = user.email
    case.fee_confirmed_at = utcnow()
    await db.flush()
    result = await _fee_status(db, case)
    await db.commit()
    return result


# ── Parties ───────────────────────────────────────────────────────────────────
class PartyIn(BaseModel):
    role: str
    legal_name: str
    dob: str | None = None
    address: str | None = None
    id_number: str | None = None
    id_type: str | None = None
    ownership_pct: float | None = None
    is_control_person: bool = False
    notes: str | None = None


def _party_dict(p: OnboardingParty) -> dict:
    return {
        "id": str(p.id), "role": p.role,
        "role_label": parties_core.ROLE_LABELS.get(p.role, p.role),
        "legal_name": p.legal_name, "dob": p.dob, "address": p.address,
        "id_type": p.id_type,
        # id_number is deliberately never returned.
        "has_id_number": bool(p.id_number),
        "ownership_pct": float(p.ownership_pct) if p.ownership_pct is not None else None,
        "is_control_person": p.is_control_person,
        "cip_status": p.cip_status,
        "cip_checked_at": p.cip_checked_at.isoformat() if p.cip_checked_at else None,
        "screening_status": p.screening_status or "not_screened",
        "screening_hits": p.screening_hits or [],
        "screened_at": p.screened_at.isoformat() if p.screened_at else None,
        "disposition_note": p.disposition_note,
        "is_stale": p.is_stale,
        "notes": p.notes,
    }


async def _load_parties(db: AsyncSession, case_id: uuid.UUID) -> list[OnboardingParty]:
    return list((await db.execute(
        select(OnboardingParty).where(OnboardingParty.case_id == case_id)
    )).scalars().all())


@router.get("/cases/{case_id}/parties")
async def list_parties(
    case_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Every party on the case, plus the completeness and screening gate."""
    case = await _case_or_404(db, case_id, firm)
    rows = await _load_parties(db, case.id)
    return {
        "parties": [_party_dict(p) for p in rows],
        "completeness": parties_core.completeness_for(case.registration_type, rows),
        "roles": [
            {"value": r, "label": parties_core.ROLE_LABELS[r]}
            for r in parties_core.ROLE_LABELS
        ],
        "required_roles": [
            {"role": r, "label": parties_core.ROLE_LABELS.get(r, r), "min": n, "why": why}
            for r, n, why in parties_core.required_roles(case.registration_type)
        ],
    }


@router.post("/cases/{case_id}/parties")
async def add_party(
    case_id: uuid.UUID, body: PartyIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    case = await _case_or_404(db, case_id, firm)
    if body.role not in parties_core.ROLE_LABELS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown role '{body.role}'. "
                   f"Expected one of: {', '.join(sorted(parties_core.ROLE_LABELS))}.",
        )
    db.add(OnboardingParty(
        firm_id=firm.id, case_id=case.id, role=body.role, legal_name=body.legal_name,
        dob=body.dob, address=body.address, id_number=body.id_number, id_type=body.id_type,
        ownership_pct=body.ownership_pct, is_control_person=body.is_control_person,
        notes=body.notes, screening_status="not_screened",
    ))
    await db.flush()
    rows = await _load_parties(db, case.id)
    await db.commit()
    return {
        "parties": [_party_dict(p) for p in rows],
        "completeness": parties_core.completeness_for(case.registration_type, rows),
    }


@router.delete("/cases/{case_id}/parties/{party_id}", status_code=204)
async def delete_party(
    case_id: uuid.UUID, party_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    await _case_or_404(db, case_id, firm)
    row = await db.get(OnboardingParty, party_id)
    if not row or row.case_id != case_id or row.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Party not found")
    await db.delete(row)
    await db.commit()


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
        delivering_firm=body.delivering_firm,
        delivering_account_title=body.delivering_account_title,
        is_third_party=body.is_third_party,
    )

    # Run the title check immediately on an incoming ACAT — the whole value of the control
    # is that it happens before submission rather than after a reject comes back.
    if body.transfer_type == "acat" and body.direction == "in":
        party_rows = await _load_parties(db, case_id)
        check = transfer_controls.check_title(
            body.delivering_account_title, [p.legal_name for p in party_rows]
        )
        t.title_match_status = check["status"]
        t.title_match_note = check["note"]

    db.add(t)
    await db.flush()
    await db.commit()
    return _transfer_dict(t)


@router.post("/cases/{case_id}/transfers/{transfer_id}/verify-title")
async def verify_transfer_title(
    case_id: uuid.UUID, transfer_id: uuid.UUID,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Re-run the ACAT title check against the current parties of record."""
    await _case_or_404(db, case_id, firm)
    t = await db.get(TransferRequest, transfer_id)
    if not t or t.case_id != case_id or t.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Transfer not found")
    party_rows = await _load_parties(db, case_id)
    check = transfer_controls.check_title(
        t.delivering_account_title, [p.legal_name for p in party_rows]
    )
    t.title_match_status = check["status"]
    t.title_match_note = check["note"]
    await db.commit()
    return _transfer_dict(t)


@router.post("/cases/{case_id}/transfers/{transfer_id}/callback")
async def record_wire_callback(
    case_id: uuid.UUID, transfer_id: uuid.UUID, body: CallbackIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Record callback verification for a third-party transfer.

    L200's control against imposter fraud is a callback on a recorded line to the number
    of record — so what is evidenced is the number called and who called it, not merely
    that someone ticked a box.
    """
    await _case_or_404(db, case_id, firm)
    t = await db.get(TransferRequest, transfer_id)
    if not t or t.case_id != case_id or t.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Transfer not found")
    if not t.is_third_party:
        raise HTTPException(
            status_code=409,
            detail="Callback verification applies to third-party transfers only.",
        )
    t.callback_verified_at = utcnow()
    t.callback_verified_by = user.email
    t.callback_number = body.callback_number
    if body.note:
        t.notes = f"{t.notes + ' · ' if t.notes else ''}Callback: {body.note}"
    await db.commit()
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
        # L200 §5 pre-submission controls.
        "delivering_firm": t.delivering_firm,
        "delivering_account_title": t.delivering_account_title,
        "title_match_status": t.title_match_status or "not_checked",
        "title_match_note": t.title_match_note,
        "is_third_party": t.is_third_party,
        "callback_verified_at": t.callback_verified_at.isoformat() if t.callback_verified_at else None,
        "callback_verified_by": t.callback_verified_by,
        "callback_number": t.callback_number,
        "reject_reason": t.reject_reason,
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
