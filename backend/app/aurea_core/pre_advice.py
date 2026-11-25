"""No advice before the client's paperwork: India's pre-advice gate for an Investment Adviser (R3).

The IA Master Circular (6 Feb 2026) puts five things before any advice to a client on the adviser's track: the signed
agreement carrying Annexure A and the MITC (para 1(ii)(g): no advice and no fee before it), the disclosure of AI use
(Reg 18(9)), the investor charter, a complete risk profile the client has consented to "either through registered email
or physical document" (para 2.2), and, where the plan covers a product SEBI does not regulate, the client's declaration
that SEBI offers no recourse on it (para 1(xi), Reg 18(8)).

Each is read from what a person recorded: an onboarding case's own agreement and deliveries, or, for a client already
on the books, the consent registry (`consents.py`, the India kinds). Nothing is assumed from a household being a client.
The gate applies to a household with a mandate on the adviser's track; a portfolio manager's discretionary client and a
firm with no licence are outside it. Approving a book-moving proposal is advice reaching the client, so
`runtime.decide` refuses it while the gate is open, and the billing run blocks a fee with no signed agreement.

"Family of client" in SEBI's sense is not the household (a non-dependent adult child is a family of their own). It is what a
person recorded (`sebi_family.py`, India row 13); with none recorded the household stands in for it and says so.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import consents, firm_time, licences
from app.core import decision_rights
from app.models.balance_sheet import BalanceItem
from app.models.consent_registry import ConsentRecord
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.onboarding import DisclosureDelivery, OnboardingCase
from app.models.protection import ProtectionPolicy
from app.models.risk_assessment import RiskAssessment

ADVISER = "investment_adviser"
CITATION = "SEBI Master Circular for Investment Advisers (6 Feb 2026)"
STANDS_IN = "The household stands in for SEBI's family of client, which is not modelled here."
# Assets a person records on the balance sheet that sit outside SEBI's purview: a pension (EPF, PPF, NPS) or property.
NON_SEBI_ASSETS = ("pension", "property")

PASS, FAIL, NA = "pass", "fail", "n/a"


def _check(key: str, label: str, state: str, detail: str, source: str) -> dict:
    return {"key": key, "label": label, "state": state, "detail": detail, "source": source}


async def adviser_mandates(session: AsyncSession, firm, household_id: uuid.UUID) -> list[Mandate]:
    """The household's active mandates on the adviser's track."""
    if not licences.held(firm):
        return []
    people = [p for (p,) in (await session.execute(select(Person.id).where(Person.household_id == household_id))).all()]
    entities = [e for (e,) in (await session.execute(select(LegalEntity.id).where(LegalEntity.household_id == household_id))).all()]
    if not people and not entities:
        return []
    owners = []
    if people:
        owners.append(Mandate.person_id.in_(people))
    if entities:
        owners.append(Mandate.entity_id.in_(entities))
    mandates = (await session.execute(select(Mandate).where(
        Mandate.firm_id == firm.id, Mandate.is_active.is_(True), or_(*owners)).order_by(Mandate.name))).scalars().all()
    return [m for m in mandates if (licences.track_for(firm, m) or None) and licences.track_for(firm, m).key == ADVISER]


async def household_of_mandate(session: AsyncSession, mandate: Mandate) -> uuid.UUID | None:
    owner = await session.get(Person, mandate.person_id) if mandate.person_id else None
    owner = owner or (await session.get(LegalEntity, mandate.entity_id) if mandate.entity_id else None)
    return owner.household_id if owner else None


async def _non_sebi(session: AsyncSession, household_id: uuid.UUID) -> list[str]:
    """What the household holds outside SEBI's purview, as recorded: in-force policies and recorded pensions or property."""
    policies = (await session.execute(select(ProtectionPolicy.kind).join(Person, Person.id == ProtectionPolicy.person_id).where(
        Person.household_id == household_id, ProtectionPolicy.status == "in_force"))).scalars().all()
    items = (await session.execute(select(BalanceItem.kind).where(
        BalanceItem.household_id == household_id, BalanceItem.status == "current", BalanceItem.kind.in_(NON_SEBI_ASSETS)))).scalars().all()
    out = []
    if policies:
        out.append(f"{len(policies)} insurance polic{'y' if len(policies) == 1 else 'ies'}")
    for kind in NON_SEBI_ASSETS:
        n = sum(1 for k in items if k == kind)
        if n:
            out.append(f"{n} {kind}{'' if n == 1 else 's'}")
    return out


def _given(stands: dict, kind: str) -> ConsentRecord | None:
    row = stands.get(kind)
    return row if row is not None and row.status == "granted" else None


def _evidence(row: ConsentRecord) -> str:
    return f"{row.method.replace('_', ' ')}, {row.given_on.isoformat()}: {row.evidence}"


async def status(session: AsyncSession, firm, household_id: uuid.UUID) -> dict:
    """The gate for one household: each item, its state and where it is on file."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise ValueError("Household not found.")
    mandates = await adviser_mandates(session, firm, household_id)
    base = {"household_id": str(household_id), "household": household.name, "citation": CITATION}
    if not mandates:
        return {**base, "applies": False, "met": True, "checks": [], "missing": [], "mandates": []}

    cases = [c for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id))).scalars().all()
             if (c.materialized or {}).get("household_id") == str(household_id)]
    delivered: dict[str, DisclosureDelivery] = {}
    if cases:
        for d in (await session.execute(select(DisclosureDelivery).where(
                DisclosureDelivery.case_id.in_([c.id for c in cases])))).scalars().all():
            if d.doc_type not in delivered or d.delivered_at > delivered[d.doc_type].delivered_at:
                delivered[d.doc_type] = d
    clauses = [ConsentRecord.household_id == household_id]
    if cases:
        clauses.append(ConsentRecord.case_id.in_([c.id for c in cases]))
    stands = consents.standing(list((await session.execute(select(ConsentRecord).where(
        ConsentRecord.firm_id == firm.id, or_(*clauses)))).scalars().all()))

    def delivery(doc: str) -> str | None:
        d = delivered.get(doc)
        return f"delivered {firm_time.today(firm, d.delivered_at).isoformat()} by {d.method}" if d else None

    checks = []
    # 1. The agreement, with Annexure A and the MITC.
    signed_case = next((c for c in cases if c.agreement_status == "signed" and c.agreement_signed_at), None)
    agreement = _given(stands, "advisory_agreement")
    if agreement:
        checks.append(_check("agreement", "Agreement signed, with the MITC", PASS, _evidence(agreement), "para 1(ii)(g)"))
    elif signed_case and delivered.get("in_mitc"):
        checks.append(_check("agreement", "Agreement signed, with the MITC", PASS,
                             f"signed {firm_time.today(firm, signed_case.agreement_signed_at).isoformat()}; MITC {delivery('in_mitc')}", "para 1(ii)(g)"))
    elif signed_case:
        checks.append(_check("agreement", "Agreement signed, with the MITC", FAIL, "signed, but no MITC delivery on file", "para 1(ii)(g)"))
    else:
        checks.append(_check("agreement", "Agreement signed, with the MITC", FAIL, "no signed agreement on file", "para 1(ii)(g)"))
    # 2 and 3. Disclosures the client receives.
    for key, kind, doc, label, source in (("ai_disclosure", "ai_use_disclosure", "in_ai_use_disclosure", "AI use disclosed", "Reg 18(9)"),
                                          ("investor_charter", "investor_charter", "in_investor_charter", "Investor charter given", "investor charter")):
        row = _given(stands, kind)
        if row:
            checks.append(_check(key, label, PASS, _evidence(row), source))
        elif delivered.get(doc):
            checks.append(_check(key, label, PASS, delivery(doc), source))
        else:
            checks.append(_check(key, label, FAIL, "not on file", source))
    # 4. A complete risk profile, and the client's consent to it, given after the answers it consents to.
    assessment = (await session.execute(select(RiskAssessment).where(
        RiskAssessment.household_id == household_id, RiskAssessment.indicated.is_not(None)
    ).order_by(RiskAssessment.completed_at.desc()))).scalars().first()
    consent = _given(stands, "risk_profile")
    if assessment is None:
        checks.append(_check("risk_profile", "Risk profile complete and consented", FAIL,
                             "no complete questionnaire on file", "para 2.2, Reg 16"))
    else:
        answered: date = firm_time.today(firm, assessment.completed_at)
        if consent is None:
            detail, state = f"answered {answered.isoformat()} ({assessment.indicated}); the client's consent is not on file", FAIL
        elif consent.given_on < answered:
            detail, state = f"consent of {consent.given_on.isoformat()} predates the answers of {answered.isoformat()}", FAIL
        else:
            detail, state = f"{assessment.indicated}, answered {answered.isoformat()}; consented by {_evidence(consent)}", PASS
        checks.append(_check("risk_profile", "Risk profile complete and consented", state, detail, "para 2.2, Reg 16"))
    # 5. The declaration on products outside SEBI's purview, where the plan covers one.
    outside = await _non_sebi(session, household_id)
    declared = _given(stands, "non_sebi_declaration")
    if not outside:
        checks.append(_check("non_sebi", "Non-SEBI products declared", NA, "nothing outside SEBI's purview on file", "para 1(xi)"))
    elif declared:
        checks.append(_check("non_sebi", "Non-SEBI products declared", PASS, _evidence(declared), "para 1(xi)"))
    elif delivered.get("in_non_sebi_declaration"):
        checks.append(_check("non_sebi", "Non-SEBI products declared", PASS, delivery("in_non_sebi_declaration"), "para 1(xi)"))
    else:
        checks.append(_check("non_sebi", "Non-SEBI products declared", FAIL,
                             f"holds {', '.join(outside)}; no declaration on file", "para 1(xi)"))

    # 6. A non-resident or overseas citizen needs their own FATCA and CRS self-certification (India row 7).
    from app.aurea_core import residency

    nri = await residency.gate_check(session, firm, household_id)
    if nri is not None:
        checks.append(nri)

    # 7. SEBI's family of client, as a person recorded it; the household stands in where none is (India row 13, B-245).
    from app.aurea_core import sebi_family

    family = await sebi_family.household_view(session, firm, household_id)
    recorded = family.get("basis") == "recorded"
    if recorded:
        apart = [m["name"] for f in family["families"] for m in f["members"] if m["household"] != household.name]
        checks.append(_check("family", "Family of client", NA, "recorded: " + "; ".join(
            ", ".join(m["name"] for m in f["members"]) for f in family["families"])
            + (f" (outside this household: {', '.join(apart)})" if apart else ""), "para 1(iii)"))

    missing = [c["label"] for c in checks if c["state"] == FAIL]
    return {**base, "applies": True, "met": not missing, "checks": checks, "missing": missing,
            "mandates": [{"id": str(m.id), "name": m.name} for m in mandates], "stands_in": None if recorded else STANDS_IN,
            "family": {"basis": family.get("basis"), "families": family.get("families", [])}}


async def for_firm(session: AsyncSession, firm) -> list[dict]:
    """Every household on the adviser's track, with its gate."""
    if not licences.held(firm):
        return []
    out = []
    for h in (await session.execute(select(Household).where(Household.firm_id == firm.id).order_by(Household.name))).scalars().all():
        s = await status(session, firm, h.id)
        if s["applies"]:
            out.append(s)
    return out


async def signed(session: AsyncSession, firm, household_id: uuid.UUID) -> bool | None:
    """Whether the household's agreement is on file. None where the gate does not apply."""
    s = await status(session, firm, household_id)
    if not s["applies"]:
        return None
    return next(c for c in s["checks"] if c["key"] == "agreement")["state"] == PASS


def refusal_text(s: dict) -> str:
    return (f"No advice to {s['household']} before the client's paperwork: {'; '.join(s['missing'])}. "
            f"Record it in the consent registry from its evidence ({CITATION}).")


async def refusal(session: AsyncSession, firm, rec) -> str | None:
    """Why this proposal may not be approved yet, or None. Only a book-moving proposal on the adviser's track is held."""
    if not licences.held(firm) or not decision_rights.moves_the_book(rec.agent_key):
        return None
    if rec.subject_type != "mandate" or not rec.subject_id:
        return None
    mandate = await session.get(Mandate, rec.subject_id)
    track = licences.track_for(firm, mandate) if mandate else None
    if track is None or track.key != ADVISER:
        return None
    household_id = await household_of_mandate(session, mandate)
    if household_id is None:
        return None
    s = await status(session, firm, household_id)
    return None if s["met"] else refusal_text(s)


class PreAdviceError(Exception):
    """The adviser's paperwork for this client is not complete, so nothing is advised."""
