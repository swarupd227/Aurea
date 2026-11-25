"""Licences and client consent.

Reading is for staff. Recording a client's answer is for advisers and paraplanners, who hold the
client relationship; it is not a client action, and it is not an approval (runtime.decide is that).
The agent-facing tool is the same action behind a confirmation card.
"""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.atlas import runtime
from app.aurea_core import compliance_calendar, consent, licences, pre_advice
from app.core.db import get_db
from app.core.security import UserRole, require_roles
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/licences", tags=["licences"])
ReaderDep = Depends(require_roles(UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                                  UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.ADMIN))
RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})


class AnswerIn(BaseModel):
    given: bool
    channel: str
    evidence_ref: str | None = None
    note: str | None = None


@router.get("")
async def read(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import governance

    result = await licences.overview(db, firm)
    if user.role not in governance.READERS:
        result.pop("rate_card", None)
        result.pop("governance", None)      # the register and the licence map are compliance's, a branch leader's, operations' and the admin's
    return {**result, "can_record": user.role in RECORDERS}


@router.post("/consents/{recommendation_id}")
async def answer(recommendation_id: uuid.UUID, body: AnswerIn, user: User = ReaderDep,
                 firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    if user.role not in RECORDERS:
        raise HTTPException(status_code=403, detail="Recording a client's answer is for advisers and paraplanners.")
    rec = (await db.execute(select(Recommendation).where(
        Recommendation.id == recommendation_id, Recommendation.firm_id == firm.id))).scalar_one_or_none()
    if rec is None:
        raise HTTPException(status_code=404, detail="Proposal not found.")
    try:
        rec = await runtime.client_response(
            db, firm=firm, recommendation=rec, given=body.given, channel=body.channel,
            evidence_ref=body.evidence_ref, note=body.note, actor_label=user.email)
    except consent.ConsentError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except runtime.AlreadyDecidedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return {"id": str(rec.id), "status": rec.status, "client_consent": (rec.payload or {}).get("client_consent"),
            "execution_result": (rec.payload or {}).get("execution_result")}


class FilingIn(BaseModel):
    obligation: str
    period: str
    reference: str
    filed_on: date | None = None
    note: str | None = None


class RegistrationIn(BaseModel):
    details: dict
    reason: str


@router.get("/pre-advice/{household_id}")
async def before_advice(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    try:
        return await pre_advice.status(db, firm, household_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/filings")
async def record_filing(body: FilingIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                        db: AsyncSession = Depends(get_db)):
    """The tool record_compliance_filing behind its Confirm card is the same action."""
    if user.role != UserRole.COMPLIANCE:
        raise HTTPException(status_code=403, detail="A filing is recorded by compliance.")
    try:
        result = await compliance_calendar.record_filing(db, firm, by=user.email, note=body.note, obligation=body.obligation,
                                                         period=body.period, filed_on=body.filed_on, reference=body.reference)
    except compliance_calendar.CalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.put("/registration")
async def record_registration(body: RegistrationIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    """The tool record_sebi_registration behind its Confirm card is the same action."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="The firm's registration and officers are its administrator's to record.")
    try:
        result = await compliance_calendar.record_officers(db, firm, user, details=body.details, reason=body.reason)
    except compliance_calendar.CalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


# ── India row 7: residence and a minor's guardian ───────────────────────────────────────────────────────────

class ResidencyIn(BaseModel):
    person_id: uuid.UUID
    document: str
    tax_year: str | None = None
    days_in_india: int | None = None
    days_prior_four_years: int | None = None
    days_prior_seven_years: int | None = None
    nonresident_years_of_prior_ten: int | None = None
    indian_citizen_or_pio: bool | None = None
    left_for_employment_abroad: bool | None = None
    india_income: float | None = None
    taxed_elsewhere: bool | None = None
    fema_status: str | None = None
    country_of_residence: str | None = None
    accounts: list[str] | None = None
    pis_permission: str | None = None
    tax_residencies: list[str] | None = None
    us_person: bool | None = None
    us_indicia: list[str] | None = None
    self_certified_on: date | None = None
    note: str | None = None


class GuardianIn(BaseModel):
    minor_person_id: uuid.UUID
    guardian_name: str | None = None
    relationship: str
    document: str
    guardian_person_id: uuid.UUID | None = None
    note: str | None = None


@router.get("/residency")
async def residence(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import guardianship, residency

    return {**await residency.overview(db, firm), "guardians": await guardianship.overview(db, firm),
            "can_record": user.role in residency.RECORDERS}


@router.get("/residency/{household_id}")
async def household_residence(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                              db: AsyncSession = Depends(get_db)):
    from app.aurea_core import residency

    try:
        return await residency.household_view(db, firm, household_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/residency")
async def record_residence(body: ResidencyIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_residency behind its Confirm card is the same action."""
    from app.aurea_core import residency

    if user.role not in residency.RECORDERS:
        raise HTTPException(status_code=403, detail="A residence is recorded by an adviser, a paraplanner or operations.")
    data = body.model_dump(exclude={"note"})
    try:
        result = await residency.record(db, firm, by=user.email, note=body.note, **data)
    except residency.ResidencyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/guardians")
async def record_guardian(body: GuardianIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_guardian behind its Confirm card is the same action."""
    from app.aurea_core import guardianship, residency

    if user.role not in residency.RECORDERS:
        raise HTTPException(status_code=403, detail="A guardian is recorded by an adviser, a paraplanner or operations.")
    try:
        result = await guardianship.record(db, firm, by=user.email, **body.model_dump())
    except guardianship.GuardianError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


# ── India row 8: Portfolio Manager clients ──────────────────────────────────────────────────────────────────

class PmsIn(BaseModel):
    mandate_id: uuid.UUID
    strategy: str
    kind: str
    signed_on: date
    document: str
    initial_corpus: float | None = None
    segregated: bool | None = None
    benchmarks: list[str] | None = None
    fee_type: str | None = None
    fixed_rate: float | None = None
    performance_rate: float | None = None
    hurdle_rate: float | None = None
    high_water_mark: bool | None = None
    operating_expense_rate: float | None = None
    exit_load: list[float] | None = None
    note: str | None = None


@router.get("/pms")
async def portfolio_manager_clients(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import pms

    return {**await pms.overview(db, firm), "can_record": user.role in pms.RECORDERS}


@router.get("/pms/{mandate_id}/report")
async def portfolio_manager_report(mandate_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                   db: AsyncSession = Depends(get_db)):
    from app.aurea_core import pms

    try:
        return await pms.client_report(db, firm, mandate_id)
    except pms.PmsError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/pms")
async def record_portfolio_manager_agreement(body: PmsIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                             db: AsyncSession = Depends(get_db)):
    """The tool record_pms_agreement behind its Confirm card is the same action."""
    from app.aurea_core import pms

    if user.role not in pms.RECORDERS:
        raise HTTPException(status_code=403, detail="A PMS agreement is recorded by the portfolio team, operations or an adviser.")
    try:
        result = await pms.record(db, firm, by=user.email, **body.model_dump())
    except pms.PmsError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


# ── India row 9: the Account Aggregator and consolidated statements ────────────────────────────────────────

class AaConsentIn(BaseModel):
    household_id: uuid.UUID
    artefact_id: str
    aggregator: str
    purpose_code: str
    fi_types: list[str]
    data_from: date
    data_to: date
    expires_on: date
    document: str
    person_id: uuid.UUID | None = None
    fetch_type: str | None = "onetime"
    consent_start: date | None = None
    note: str | None = None


class AaRevokeIn(BaseModel):
    reason: str
    document: str | None = None


class EcasIn(BaseModel):
    household_id: uuid.UUID
    kind: str
    statement_date: date
    document: str
    lines: str
    person_id: uuid.UUID | None = None
    period_from: date | None = None


@router.get("/aggregator")
async def aggregator_overview(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import account_aggregator

    return {**await account_aggregator.overview(db, firm), "can_record": user.role in account_aggregator.RECORDERS}


@router.post("/aggregator/consents")
async def record_aggregator_consent(body: AaConsentIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                    db: AsyncSession = Depends(get_db)):
    """The tool record_aggregator_consent behind its Confirm card is the same action."""
    from app.aurea_core import account_aggregator

    if user.role not in account_aggregator.RECORDERS:
        raise HTTPException(status_code=403, detail="A consent is recorded by an adviser, a paraplanner or operations.")
    try:
        result = await account_aggregator.record_consent(db, firm, by=user.email, **body.model_dump())
    except account_aggregator.AggregatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/aggregator/consents/{consent_id}/revoke")
async def revoke_aggregator_consent(consent_id: uuid.UUID, body: AaRevokeIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                    db: AsyncSession = Depends(get_db)):
    from app.aurea_core import account_aggregator

    if user.role not in account_aggregator.RECORDERS:
        raise HTTPException(status_code=403, detail="A withdrawal is recorded by an adviser, a paraplanner or operations.")
    try:
        result = await account_aggregator.revoke_consent(db, firm, by=user.email, consent_id=consent_id, reason=body.reason, document=body.document)
    except account_aggregator.AggregatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/aggregator/pull")
async def pull_aggregator(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import account_aggregator

    if user.role not in account_aggregator.RECORDERS:
        raise HTTPException(status_code=403, detail="Fetching is for an adviser, a paraplanner or operations.")
    try:
        result = await account_aggregator.pull(db, firm, by=user.email)
    except account_aggregator.AggregatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/aggregator/statements")
async def record_ecas(body: EcasIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_ecas behind its Confirm card is the same action."""
    from app.aurea_core import account_aggregator

    if user.role not in account_aggregator.RECORDERS:
        raise HTTPException(status_code=403, detail="A statement is recorded by an adviser, a paraplanner or operations.")
    data = body.model_dump()
    try:
        result = await account_aggregator.record_ecas(db, firm, by=user.email, lines_text=data.pop("lines"), **data)
    except account_aggregator.AggregatorError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result.pop("held_away", None)
    await db.commit()
    return result


# ── India row 10: the complaints register and the advertising check ────────────────────────────────────────

class ComplaintIn(BaseModel):
    complainant: str
    source: str
    category: str
    summary: str
    received_on: date | None = None
    household_id: uuid.UUID | None = None
    scores_reference: str | None = None
    reference: str | None = None
    document: str | None = None
    about_advice: bool | None = None


class ComplaintStepIn(BaseModel):
    kind: str
    happened_on: date | None = None
    outcome: str | None = None
    reference: str | None = None
    note: str | None = None


class AdvertisingIn(BaseModel):
    text: str
    kind: str = "message"


@router.get("/complaints")
async def complaints_register(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import complaints

    return await complaints.staff_view(db, firm, user.role)


@router.get("/complaints/annexure-c/{period}")
async def complaints_annexure(period: str, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import complaints

    if user.role not in complaints.READERS:
        raise HTTPException(status_code=403, detail="The register is read by compliance, a branch leader and operations.")
    try:
        return {"annexure_c": await complaints.annexure_c(db, firm, period)}
    except complaints.ComplaintError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.post("/complaints")
async def record_complaint(body: ComplaintIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_complaint behind its Confirm card is the same action."""
    from app.aurea_core import complaints

    try:
        result = await complaints.record_complaint(db, firm, user.role, by=user.email, **body.model_dump())
    except complaints.ComplaintError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    if user.role not in complaints.READERS:
        return {"recorded": result["recorded"]}
    return result


@router.post("/complaints/{reference}/steps")
async def record_complaint_step(reference: str, body: ComplaintStepIn, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                db: AsyncSession = Depends(get_db)):
    """The tool record_complaint_step behind its Confirm card is the same action."""
    from app.aurea_core import complaints

    try:
        result = await complaints.record_event(db, firm, user.role, by=user.email, complaint=reference, **body.model_dump())
    except complaints.ComplaintError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    if user.role not in complaints.READERS:
        return {"recorded": result["recorded"]}
    return result


@router.post("/advertising/check")
async def check_advertising(body: AdvertisingIn, user: User = ReaderDep, firm: Firm = Depends(current_firm)):
    """Findings only: the check approves nothing, and its patterns are not exhaustive."""
    from app.aurea_core import advertising

    return advertising.for_firm(firm, body.text, body.kind)
# ── India row 11: the tax engine's records. Row 12: KYC ─────────────────────────────────────────────────────────

class TaxLossIn(BaseModel):
    person_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    tax_year: str
    kind: str
    amount: float
    used: float | None = None
    return_filed_on: date | None = None
    document: str
    note: str | None = None


class GrandfatherIn(BaseModel):
    instrument_id: uuid.UUID | None = None
    symbol: str | None = None
    fmv_per_unit: float
    document: str
    note: str | None = None


class KycIn(BaseModel):
    person_id: uuid.UUID | None = None
    entity_id: uuid.UUID | None = None
    case_id: uuid.UUID | None = None
    party_name: str | None = None
    document: str
    ckyc_number: str | None = None
    kra_name: str | None = None
    kra_status: str | None = None
    kyc_completed_on: date | None = None
    verification_mode: str | None = None
    ipv_on: date | None = None
    aadhaar_last4: str | None = None
    aadhaar_xml_dated: date | None = None
    pan_aadhaar_linked: bool | None = None
    id_proof_kind: str | None = None
    id_proof_valid_to: date | None = None
    address_proof_kind: str | None = None
    address_proof_dated: date | None = None
    address_proof_valid_to: date | None = None
    risk_band: str | None = None
    pep: bool | None = None
    structure: str | None = None
    beneficial_owners: list[dict] | None = None
    trigger: str | None = None
    trigger_on: date | None = None
    note: str | None = None


@router.get("/tax-records")
async def tax_records(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import tax_india_full

    return {**await tax_india_full.records(db, firm), "can_record": user.role in tax_india_full.RECORDERS}


@router.post("/tax-losses")
async def record_tax_loss(body: TaxLossIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_tax_loss behind its Confirm card is the same action."""
    from app.aurea_core import tax_india_full

    if user.role not in tax_india_full.RECORDERS:
        raise HTTPException(status_code=403, detail="A capital loss is recorded by an adviser, a paraplanner, operations or the portfolio team.")
    try:
        result = await tax_india_full.record_loss(db, firm, by=user.email, **body.model_dump())
    except tax_india_full.TaxFactError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/grandfather-values")
async def record_grandfather_value(body: GrandfatherIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_grandfather_value behind its Confirm card is the same action."""
    from app.aurea_core import tax_india_full

    if user.role not in tax_india_full.RECORDERS:
        raise HTTPException(status_code=403, detail="A 2018 value is recorded by an adviser, a paraplanner, operations or the portfolio team.")
    try:
        result = await tax_india_full.record_fmv(db, firm, by=user.email, **body.model_dump())
    except tax_india_full.TaxFactError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.get("/kyc")
async def kyc(household_id: uuid.UUID | None = None, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import kyc_india

    return {**await kyc_india.overview(db, firm, household_id=household_id), "can_record": user.role in kyc_india.RECORDERS}


@router.post("/kyc")
async def record_kyc(body: KycIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_kyc behind its Confirm card is the same action."""
    from app.aurea_core import kyc_india

    if user.role not in kyc_india.RECORDERS:
        raise HTTPException(status_code=403, detail="KYC is recorded by an adviser, a paraplanner, operations or compliance.")
    data = body.model_dump(exclude={"note", "beneficial_owners"})
    try:
        result = await kyc_india.record(db, firm, by=user.email, note=body.note, beneficial_owners_text=body.beneficial_owners, **data)
    except kyc_india.KycError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


# ── India row 13: SEBI's family of client, and the family's estate records ─────────────────────────────────────

class FamilyIn(BaseModel):
    person_id: uuid.UUID
    of_person_id: uuid.UUID
    relation: str
    dependent: bool | None = None
    document: str


class NominationIn(BaseModel):
    person_id: uuid.UUID
    asset_kind: str
    label: str
    status: str
    document: str
    nominees: list[dict] | str | None = None
    declaration_on: date | None = None


class WillStatusIn(BaseModel):
    person_id: uuid.UUID
    document: str
    personal_law: str | None = None
    registered: bool | None = None
    registration_ref: str | None = None
    probate: str | None = None
    probate_ref: str | None = None
    executor: str | None = None
    as_of: date | None = None


class HufIn(BaseModel):
    entity_id: uuid.UUID
    karta_name: str
    coparceners: list[dict] | str
    document: str
    karta_person_id: uuid.UUID | None = None
    as_of: date | None = None


class GratuityIn(BaseModel):
    person_id: uuid.UUID
    employer: str
    amount: float
    document: str
    as_of: date | None = None
    payable_on: date | None = None
    years_of_service: float | None = None


def _estate_recorder(user: User) -> None:
    from app.aurea_core import india_estate

    if user.role not in india_estate.RECORDERS:
        raise HTTPException(status_code=403, detail="A family or estate record is made by an adviser, a paraplanner or operations.")


@router.get("/family")
async def sebi_family_overview(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import india_estate, sebi_family

    from app.aurea_core import counsel_review

    return await counsel_review.annotate(db, firm, {**await sebi_family.overview(db, firm), "can_record": user.role in india_estate.RECORDERS})


@router.get("/family/{household_id}")
async def sebi_family_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                db: AsyncSession = Depends(get_db)):
    from app.aurea_core import sebi_family

    from app.aurea_core import counsel_review

    try:
        return await counsel_review.annotate(db, firm, await sebi_family.household_view(db, firm, household_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/family")
async def record_sebi_family(body: FamilyIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_sebi_family behind its Confirm card is the same action."""
    from app.aurea_core import sebi_family

    _estate_recorder(user)
    try:
        result = await sebi_family.record(db, firm, by=user.email, **body.model_dump())
    except sebi_family.FamilyError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.get("/estate")
async def india_estate_overview(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import india_estate

    from app.aurea_core import counsel_review

    return await counsel_review.annotate(db, firm, {**await india_estate.overview(db, firm), "can_record": user.role in india_estate.RECORDERS})


@router.get("/estate/{household_id}")
async def india_estate_household(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm),
                                 db: AsyncSession = Depends(get_db)):
    from app.aurea_core import india_estate

    from app.aurea_core import counsel_review

    try:
        return await counsel_review.annotate(db, firm, await india_estate.household_view(db, firm, household_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


async def _record_estate(db, firm, user, name: str, body: BaseModel):
    from app.aurea_core import india_estate

    _estate_recorder(user)
    try:
        result = await getattr(india_estate, name)(db, firm, by=user.email, **body.model_dump())
    except india_estate.EstateError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/estate/nominations")
async def record_asset_nomination(body: NominationIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_asset_nomination behind its Confirm card is the same action."""
    return await _record_estate(db, firm, user, "record_nomination", body)


@router.post("/estate/will")
async def record_will_status(body: WillStatusIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_will_status behind its Confirm card is the same action."""
    return await _record_estate(db, firm, user, "record_will_status", body)


@router.post("/estate/huf")
async def record_huf(body: HufIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_huf behind its Confirm card is the same action."""
    return await _record_estate(db, firm, user, "record_huf", body)


@router.post("/estate/gratuity")
async def record_gratuity(body: GratuityIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_gratuity behind its Confirm card is the same action."""
    return await _record_estate(db, firm, user, "record_gratuity", body)


# ── India row 14: the governance register, the licence map and counsel's review of each law figure ─────────────

class PolicyIn(BaseModel):
    kind: str
    version: str
    owner: str
    approved_on: date
    document: str
    approved_by: str | None = None
    next_review_on: date | None = None
    note: str | None = None


class CounselIn(BaseModel):
    figure_key: str
    reviewer: str
    reviewed_on: date
    outcome: str
    document: str
    note: str | None = None


@router.get("/governance")
async def governance_view(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The licence map, the policy register and counsel's review of each figure. Read by compliance, a branch leader, operations and an administrator."""
    from app.aurea_core import counsel_review, governance

    if user.role not in governance.READERS:
        raise HTTPException(status_code=403, detail="The governance register is read by compliance, a branch leader, operations and an administrator.")
    result = {"applies": governance.applies(firm), "map": await governance.licence_map(db, firm), "policies": await governance.policies(db, firm),
              "counsel": await counsel_review.overview(db, firm), "can_record": user.role in governance.RECORDERS}
    return await counsel_review.annotate(db, firm, result)


@router.get("/dates")
async def india_dates(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The Indian firm's own dates for the Regulatory Countdown (its households' are on the countdown route)."""
    from app.aurea_core import regulatory_countdown

    return await regulatory_countdown.india_firm(db, firm.id) or {"analysis": None}


@router.post("/governance/policies")
async def record_governance_policy(body: PolicyIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_governance_policy behind its Confirm card is the same action."""
    from app.aurea_core import governance

    try:
        result = await governance.record_policy(db, firm, user.role, by=user.email, **body.model_dump())
    except governance.GovernanceError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.post("/governance/counsel")
async def record_counsel_review(body: CounselIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_counsel_review behind its Confirm card is the same action."""
    from app.aurea_core import counsel_review

    try:
        result = await counsel_review.record(db, firm, user.role, by=user.email, **body.model_dump())
    except counsel_review.CounselError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


# ── India, the L100 items: ticket-size gates (B-290), accredited investors (B-291), fee terms (B-292), the rate card (B-300) ──

class AccreditedIn(BaseModel):
    person_id: uuid.UUID
    document: str
    state: str = "granted"
    certificate_ref: str | None = None
    agency: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None
    criteria: list[str] = []
    reason: str | None = None


class FeeTermIn(BaseModel):
    household_id: uuid.UUID
    kind: str
    document: str
    effective_on: date | None = None
    mode: str | None = None
    advance_months: int | None = None
    assets: float | None = None
    rate: float | None = None
    fee: float | None = None
    reason: str | None = None


class WatchIn(BaseModel):
    figure_key: str
    reason: str
    state: str = "awaiting_budget"


@router.get("/ticket-gates/{household_id}")
async def ticket_gates(household_id: uuid.UUID, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import counsel_review, product_gates

    return await counsel_review.annotate(db, firm, await product_gates.open_to(db, firm, household_id))


@router.get("/accredited")
async def accredited_view(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import accredited, counsel_review

    return await counsel_review.annotate(db, firm, {**await accredited.overview(db, firm), "can_record": user.role in accredited.RECORDERS})


@router.post("/accredited")
async def record_accredited(body: AccreditedIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_accredited_investor behind its Confirm card is the same action."""
    from app.aurea_core import accredited

    try:
        result = await accredited.record(db, firm, user.role, by=user.email, **body.model_dump())
    except accredited.AccreditedError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.get("/fee-terms")
async def fee_terms(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import adviser_fees, counsel_review

    return await counsel_review.annotate(db, firm, {**await adviser_fees.overview(db, firm), "can_record": user.role in adviser_fees.RECORDERS})


@router.post("/fee-terms")
async def record_fee_term(body: FeeTermIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_adviser_fee_term behind its Confirm card is the same action."""
    from app.aurea_core import adviser_fees

    try:
        result = await adviser_fees.record(db, firm, user.role, by=user.email, **body.model_dump())
    except adviser_fees.FeeTermError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return result


@router.get("/rate-card")
async def rate_card_view(user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    from app.aurea_core import counsel_review, governance, rate_card

    if user.role not in governance.READERS:
        raise HTTPException(status_code=403, detail="The rate card is read by compliance, a branch leader, operations and an administrator.")
    return await counsel_review.annotate(db, firm, {**await rate_card.overview(db, firm), "can_record": user.role in counsel_review.RECORDERS})


@router.post("/rate-card/watch")
async def record_figure_watch(body: WatchIn, user: User = ReaderDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """The tool record_figure_watch behind its Confirm card is the same action."""
    from app.aurea_core import counsel_review, rate_card

    try:
        await counsel_review.record_watch(db, firm, user.role, by=user.email, **body.model_dump())
    except counsel_review.CounselError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    return await rate_card.overview(db, firm)
