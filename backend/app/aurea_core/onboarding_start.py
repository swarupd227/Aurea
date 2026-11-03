"""Opening an onboarding case: the one way a new client starts, from the conversation or the Onboarding page.

A case is a draft until its gates are met (`agents/onboarding.py` activates it, `aurea_core/account_states.py` reads
where it stands), so opening one screens, signs and invests nothing. What is recorded is what the person gave: the
name, the account type, and, only if they said it, a stated risk preference, a mandate preference and the source of
wealth; nothing is filled in. An account type is offered only in the market it belongs to (IRAs and the other US types
for a US firm, an HUF for an Indian one). The same name with a case still open is refused, so one prospect is not
onboarded twice. The adviser who opens a case is recorded on it, and activation links that adviser.
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ClientSegment, OnboardingStatus, RegistrationType, UserRole
from app.models.identity import AuditEvent, User
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

R = RegistrationType
LABELS = {
    R.INDIVIDUAL: "Individual", R.JOINT_JTWROS: "Joint, with right of survivorship", R.JOINT_TIC: "Joint, tenants in common",
    R.JOINT_TBE: "Joint, tenancy by the entirety", R.COMMUNITY_PROPERTY: "Community property", R.TRADITIONAL_IRA: "Traditional IRA",
    R.ROTH_IRA: "Roth IRA", R.SEP_IRA: "SEP IRA", R.SIMPLE_IRA: "SIMPLE IRA", R.EMPLOYER_ROLLOVER: "Employer plan rollover",
    R.TRUST: "Trust", R.ENTITY_LLC: "Company (LLC)", R.ENTITY_CORP: "Company (corporation)", R.ENTITY_PARTNERSHIP: "Partnership",
    R.CUSTODIAL_UTMA: "Custodial (UTMA)", R.CUSTODIAL_UGMA: "Custodial (UGMA)", R.ESTATE_INHERITED: "Estate", R.INHERITED_IRA: "Inherited IRA",
    R.PLAN_529: "529 plan", R.DAF: "Donor-advised fund", R.HSA: "Health savings account", R.HUF: "Hindu Undivided Family (HUF)",
}
# Account types that exist only in one market's law.
ONLY_IN = {**{t: "US" for t in (R.JOINT_TBE, R.COMMUNITY_PROPERTY, R.TRADITIONAL_IRA, R.ROTH_IRA, R.SEP_IRA, R.SIMPLE_IRA,
                                 R.EMPLOYER_ROLLOVER, R.CUSTODIAL_UTMA, R.CUSTODIAL_UGMA, R.INHERITED_IRA, R.PLAN_529, R.DAF, R.HSA)},
           R.HUF: "IN"}
# The legal entity an entity account becomes at activation (models.enums.EntityType).
ENTITY_OF = {R.TRUST: "trust", R.ENTITY_LLC: "company", R.ENTITY_CORP: "company", R.ENTITY_PARTNERSHIP: "partnership", R.HUF: "huf"}
RISK_PREFERENCES = ("conservative", "balanced", "growth", "aggressive")
MANDATES = ("advisory", "discretionary")
OPENERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.BRANCH_LEADER})


class StartError(Exception):
    """A case that cannot be opened, in words a person can act on."""


def types_for(firm: Firm) -> list[str]:
    """The account types this firm's market offers."""
    market = (firm.jurisdiction or "").upper()
    return [t.value for t in LABELS if ONLY_IN.get(t) in (None, market)]


async def prepare(session: AsyncSession, firm: Firm, *, prospect_name: str | None, registration_type: str | None,
                  segment: str | None = None, risk_preference: str | None = None, mandate_preference: str | None = None,
                  source_of_wealth: str | None = None, email: str | None = None) -> dict:
    """What opening the case would record. Writes nothing."""
    name = " ".join((prospect_name or "").split())
    if not name:
        raise StartError("Name the prospect: the person, couple, trust or company being onboarded.")
    try:
        rtype = RegistrationType((registration_type or "").strip().lower())
    except ValueError:
        rtype = None
    if rtype not in LABELS:
        raise StartError(f"No account type {registration_type!r}. For this firm: {', '.join(types_for(firm))}.")
    if rtype.value not in types_for(firm):
        raise StartError(f"A {LABELS[rtype]} account exists only in {ONLY_IN[rtype]} law; this firm is in "
                         f"{firm.jurisdiction or 'no market'}. For this firm: {', '.join(types_for(firm))}.")
    seg = (segment or ClientSegment.PRIVATE_WEALTH.value).strip().lower()
    if seg not in {s.value for s in ClientSegment}:
        raise StartError(f"No segment {segment!r}; the segments are {', '.join(s.value for s in ClientSegment)}.")
    risk = (risk_preference or "").strip().lower() or None
    if risk and risk not in RISK_PREFERENCES:
        raise StartError(f"A stated risk preference is one of {', '.join(RISK_PREFERENCES)}, or leave it out for the questionnaire.")
    mandate = (mandate_preference or "").strip().lower() or None
    if mandate and mandate not in MANDATES:
        raise StartError(f"A mandate preference is {' or '.join(MANDATES)}, or leave it out.")
    open_case = (await session.execute(select(OnboardingCase).where(
        OnboardingCase.firm_id == firm.id, func.lower(OnboardingCase.prospect_name) == name.lower(),
        OnboardingCase.status.notin_([OnboardingStatus.APPROVED, OnboardingStatus.REJECTED])))).scalars().first()
    if open_case:
        raise StartError(f"{open_case.prospect_name} already has an onboarding case open ({open_case.status}); "
                         "read_onboarding_cases finds it.")
    return {"name": name, "type": rtype, "label": LABELS[rtype], "segment": seg, "risk": risk, "mandate": mandate,
            "source_of_wealth": (source_of_wealth or "").strip() or None, "email": (email or "").strip() or None}


async def start(session: AsyncSession, firm: Firm, *, actor: User, extra_intake: dict | None = None, **terms) -> OnboardingCase:
    p = await prepare(session, firm, **terms)
    intake = {k: v for k, v in (extra_intake or {}).items() if v not in (None, "", [])}
    for key, value in (("risk_profile", p["risk"]), ("mandate_preference", p["mandate"]),
                       ("source_of_wealth", p["source_of_wealth"]), ("email", p["email"])):
        if value:
            intake[key] = value
    if UserRole(actor.role) == UserRole.ADVISER:
        intake["adviser_id"] = str(actor.id)
    entity = ENTITY_OF.get(p["type"])
    case = OnboardingCase(firm_id=firm.id, prospect_name=p["name"], is_entity=entity is not None, entity_type=entity,
                          registration_type=p["type"].value, segment=p["segment"], intake=intake)
    session.add(case)
    await session.flush()
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="onboarding.case_started",
                           subject=p["name"], detail={"case_id": str(case.id), "registration_type": p["type"].value}))
    await session.flush()
    return case


async def adviser_for(session: AsyncSession, firm: Firm, case: OnboardingCase) -> User | None:
    """Who the new client's adviser is at activation: the adviser who opened the case, else the firm's only adviser.
    A firm with several advisers and none on the case is left unassigned for a person to decide (assign_adviser)."""
    import uuid

    wanted = (case.intake or {}).get("adviser_id")
    if wanted:
        try:
            u = await session.get(User, uuid.UUID(str(wanted)))
        except ValueError:
            u = None
        if u and u.firm_id == firm.id and UserRole(u.role) == UserRole.ADVISER and u.is_active:
            return u
    advisers = (await session.execute(select(User).where(
        User.firm_id == firm.id, User.role == UserRole.ADVISER, User.is_active.is_(True)))).scalars().all()
    return advisers[0] if len(advisers) == 1 else None
