"""Risk profiling: what a client's questionnaire indicates, set beside what is on file (L100 §5.2).

Tolerance and capacity are measured apart because they are different things that are often in tension:

  tolerance  the client's willingness to bear a loss, from how they say they would react. Experience
             only tempers it: a beginner who would buy more has not yet lived through a fall.
  capacity   their financial ability to bear one, from horizon, income stability and liquidity needs.
             Money needed soon cannot ride out a fall, so that alone makes capacity low.

The profile indicated is the lower of the two. ESG priority is a preference, not a measure of risk, and
is not scored. Nothing is scored from part of a form.

It indicates and a person decides. Nothing here changes a mandate until an adviser adopts the result or
keeps what is on file and says why, because the profile is the suitability record and capacity sets the
rebalancer's equity ceiling. The questionnaire cannot see wealth against needs, which is often why an
adviser keeps a different profile, and a decision holds only until the client's next answers.

A mandate is not moved past the equity it is managed to, its IPS where one is in force and otherwise its
model: a mandate over its ceiling breaches on the next rebalance, and that is a high-severity failure
which pauses the agent for the whole firm.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.db import utcnow
from app.models.graph import Household, LegalEntity, Mandate, Person
from app.models.portfolio import InvestmentPolicy, ModelPortfolio, TargetAllocation
from app.models.risk_assessment import RiskAssessment
from app.provenance import ledger

SCORING_VERSION = 1
PROFILES = ("conservative", "balanced", "growth")
CAPACITIES = ("low", "medium", "high")
# The highest equity weight each capacity for loss may hold. The rebalancer enforces this.
CAPACITY_EQUITY_MAX = {"low": 0.40, "medium": 0.70, "high": 1.0}
DECISIONS = ("adopt", "keep")

# Options run from least to most able to bear risk, so an answer's position is its score. They are the
# stored answers, so changing one means a new SCORING_VERSION and a backfill.
QUESTIONS = [
    {"key": "investment_horizon", "question": "What is your investment time horizon?", "measures": "capacity",
     "options": ["short (< 3 years)", "medium (3–7 years)", "long (> 7 years)"]},
    {"key": "loss_tolerance", "question": "How comfortable are you with temporary losses?", "measures": "tolerance",
     "options": ["low — I'd want to sell", "medium — I'd hold on", "high — I'd buy more"]},
    {"key": "income_stability", "question": "How stable is your income?", "measures": "capacity",
     "options": ["variable", "stable", "very stable"]},
    {"key": "liquidity_needs", "question": "How soon might you need access to your funds?", "measures": "capacity",
     "options": ["within 1 year", "1–5 years", "5+ years"]},
    {"key": "esg_priority", "question": "How important is ESG/values alignment?", "measures": None,
     "options": ["not important", "somewhat important", "very important"]},
    {"key": "experience", "question": "How would you describe your investment experience?", "measures": "tolerance",
     "options": ["beginner", "intermediate", "experienced"]},
]
_OPTIONS = {q["key"]: q["options"] for q in QUESTIONS}

# Worst first, so a household reads as its most pressing mandate and the book sorts by what needs attention.
_STATUS_ORDER = ("more_risk", "none_on_file", "less_risk", "not_scored", "not_taken", "kept", "aligned", "no_mandate")
# A mandate edited in admin can hold a profile above the three the questionnaire indicates.
_PROFILE_RANK = {**{p: i for i, p in enumerate(PROFILES)}, "aggressive": len(PROFILES)}
_CAPACITY_RANK = {c: i for i, c in enumerate(CAPACITIES)}


class RiskProfileError(Exception):
    """The request was refused. Carries a reason fit to show the user."""


def questions() -> list[dict]:
    """The form, without what each answer is used for."""
    return [{k: q[k] for k in ("key", "question", "options")} for q in QUESTIONS]


def clean(answers: dict | None) -> dict:
    """Only the questions asked and the options offered, so nothing else reaches the record or a model."""
    return {k: answers[k] for k, options in _OPTIONS.items() if (answers or {}).get(k) in options}


def score(answers: dict | None) -> dict:
    """The tolerance, capacity and indicated profile for one set of answers. Pure."""
    valid = clean(answers)
    missing = [k for k in _OPTIONS if k not in valid]
    if missing:
        return {"status": "not_scored", "missing": missing}
    point = {k: _OPTIONS[k].index(v) for k, v in valid.items()}
    drivers = []

    tolerance = point["loss_tolerance"]
    if tolerance == 2 and point["experience"] == 0:
        tolerance = 1
        drivers.append("inexperience_tempers_tolerance")

    total = point["investment_horizon"] + point["income_stability"] + point["liquidity_needs"]
    capacity = 0 if total <= 2 else 1 if total <= 4 else 2
    for key, driver in (("investment_horizon", "short_horizon"), ("liquidity_needs", "money_needed_soon")):
        if point[key] == 0:
            capacity = 0
            drivers.append(driver)

    return {
        "status": "scored", "tolerance": PROFILES[tolerance], "capacity": CAPACITIES[capacity],
        "indicated": PROFILES[min(tolerance, capacity)], "tension": tolerance != capacity, "drivers": drivers,
    }


def on_file(suitability: dict | None) -> tuple[str | None, str | None]:
    suit = suitability or {}
    profile = str(suit.get("risk_tolerance") or suit.get("risk_profile") or "").strip().lower()
    capacity = str(suit.get("capacity_for_loss") or suit.get("risk_capacity") or "").strip().lower()
    return profile or None, capacity or None


def _mandate_status(row: RiskAssessment | None, profile: str | None, capacity: str | None, decision: dict | None) -> str:
    if row is None:
        return "not_taken"
    if row.indicated is None:
        return "not_scored"
    if decision and decision.get("assessment_id") == str(row.id) and decision.get("decision") == "keep":
        return "kept"
    if profile not in _PROFILE_RANK:
        return "none_on_file"
    gap = _PROFILE_RANK[profile] - _PROFILE_RANK[row.indicated]
    if gap > 0 or _CAPACITY_RANK.get(capacity, -1) > _CAPACITY_RANK[row.capacity]:
        return "more_risk"
    return "less_risk" if gap < 0 else "aligned"


def _assessment_view(row: RiskAssessment | None) -> dict | None:
    if row is None:
        return None
    scored = score(row.answers) if row.scoring_version == SCORING_VERSION else {}
    return {
        "id": str(row.id), "status": "scored" if row.indicated else "not_scored", "missing": scored.get("missing", []),
        "tolerance": row.tolerance, "capacity": row.capacity, "indicated": row.indicated,
        "tension": bool(row.tolerance and PROFILES.index(row.tolerance) != CAPACITIES.index(row.capacity)),
        "drivers": scored.get("drivers", []), "esg_priority": (row.answers or {}).get("esg_priority"),
        "answers": clean(row.answers), "scoring_version": row.scoring_version,
        "completed_at": row.completed_at.isoformat(), "completed_by_role": row.completed_by_role,
    }


async def _views(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None) -> list[dict]:
    query = select(Household).where(Household.firm_id == firm_id)
    if household_id is not None:
        query = query.where(Household.id == household_id)
    households = (await session.execute(query)).scalars().all()
    ids = [h.id for h in households]
    if not ids:
        return []

    owners: dict[uuid.UUID, uuid.UUID] = {}
    for model in (Person, LegalEntity):
        rows = await session.execute(select(model.id, model.household_id).where(model.household_id.in_(ids)))
        owners.update({owner: household for owner, household in rows.all()})
    mandates: dict[uuid.UUID, list[Mandate]] = {}
    for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True)).order_by(Mandate.name)
    )).scalars().all():
        household = owners.get(m.person_id) or owners.get(m.entity_id)
        if household is not None:
            mandates.setdefault(household, []).append(m)

    latest: dict[uuid.UUID, RiskAssessment] = {}
    for row in (await session.execute(
        select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id.in_(ids))
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc())
    )).scalars().all():
        latest.setdefault(row.household_id, row)

    views = []
    for h in households:
        row = latest.get(h.id)
        rows = []
        for m in mandates.get(h.id, []):
            profile, capacity = on_file(m.suitability)
            decision = (m.suitability or {}).get("risk_decision")
            rows.append({
                "mandate_id": str(m.id), "mandate": m.name, "risk_profile": profile, "capacity_for_loss": capacity,
                "status": _mandate_status(row, profile, capacity, decision), "decision": decision,
            })
        statuses = [r["status"] for r in rows]
        status = ("not_taken" if row is None else "not_scored" if row.indicated is None else
                  "no_mandate" if not rows else min(statuses, key=_STATUS_ORDER.index))
        views.append({"household_id": str(h.id), "household": h.name, "status": status,
                      "assessment": _assessment_view(row), "mandates": rows})
    return views


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """One household's latest answers and how each mandate compares, or None if it is not in this firm."""
    views = await _views(session, firm_id, household_id)
    return views[0] if views else None


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household, those whose mandate takes more risk than the client's answers support first."""
    rows = sorted(await _views(session, firm_id), key=lambda v: (_STATUS_ORDER.index(v["status"]), v["household"]))
    count = lambda status: sum(1 for r in rows if r["status"] == status)
    return {
        "households": rows,
        "summary": {
            "households": len(rows), "more_risk": count("more_risk"), "less_risk": count("less_risk"),
            "none_on_file": count("none_on_file"), "aligned": count("aligned"), "kept": count("kept"),
            "not_taken": count("not_taken"), "not_scored": count("not_scored"), "no_mandate": count("no_mandate"),
            "tension": sum(1 for r in rows if (r["assessment"] or {}).get("tension")),
        },
    }


async def latest_answers(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    row = (await session.execute(
        select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id == household_id)
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc()).limit(1)
    )).scalars().first()
    return clean(row.answers) if row else {}


async def submit(session: AsyncSession, household_id: uuid.UUID, answers: dict | None, *, firm_id: uuid.UUID,
                 by: str, role: str) -> RiskAssessment:
    """Record a completed questionnaire. A mandate does not change."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise RiskProfileError("Household not found.")
    scored = score(answers)
    if scored["status"] != "scored":
        raise RiskProfileError(f"Every question needs one of its answers. Missing: {', '.join(scored['missing'])}.")
    row = RiskAssessment(
        firm_id=firm_id, household_id=household_id, answers=clean(answers), scoring_version=SCORING_VERSION,
        tolerance=scored["tolerance"], capacity=scored["capacity"], indicated=scored["indicated"],
        completed_by=by, completed_by_role=str(role), completed_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def _household_of(session: AsyncSession, mandate: Mandate) -> uuid.UUID | None:
    owner = (await session.get(Person, mandate.person_id) if mandate.person_id
             else await session.get(LegalEntity, mandate.entity_id) if mandate.entity_id else None)
    return owner.household_id if owner else None


async def model_equity(session: AsyncSession, model_id: uuid.UUID | None) -> float | None:
    """The equity weight a model targets, as the rebalancer sums it. None with no model."""
    if model_id is None:
        return None
    weights = (await session.execute(
        select(TargetAllocation.target_weight).where(TargetAllocation.model_id == model_id,
                                                     TargetAllocation.asset_class == "equity")
    )).scalars().all()
    return sum(float(w) for w in weights)


async def decide(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID, *, decision: str,
                 reason: str | None, by: str) -> dict:
    """An adviser's decision on the household's latest answers, for one mandate."""
    if decision not in DECISIONS:
        raise RiskProfileError(f"The decision must be one of: {', '.join(DECISIONS)}.")
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.firm_id != firm_id or not mandate.is_active:
        raise RiskProfileError("Mandate not found.")
    household_id = await _household_of(session, mandate)
    row = (await session.execute(
        select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id == household_id)
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc()).limit(1)
    )).scalars().first()
    if row is None or row.indicated is None:
        raise RiskProfileError("This household has no scored questionnaire to decide on.")
    reason = (reason or "").strip()
    if decision == "keep" and len(reason) < 3:
        raise RiskProfileError("Keeping what is on file needs the reason.")

    suit = dict(mandate.suitability or {})
    profile, capacity = on_file(suit)
    if decision == "adopt":
        ceiling = CAPACITY_EQUITY_MAX[row.capacity]
        # The rebalancer works to the IPS where one is in force, so that is the equity that would breach.
        policy = (await session.execute(
            select(InvestmentPolicy).where(InvestmentPolicy.mandate_id == mandate.id)
            .order_by(InvestmentPolicy.version.desc()).limit(1))).scalars().first()
        if policy is not None:
            equity = float((policy.targets or {}).get("equity", 0.0))
            if equity > ceiling:
                raise RiskProfileError(
                    f"IPS version {policy.version} holds {equity:.0%} equity, above the {ceiling:.0%} ceiling for "
                    f"{row.capacity} capacity. Record a new IPS within the ceiling first.")
        else:
            equity = await model_equity(session, mandate.model_portfolio_id)
            if equity is not None and equity > ceiling:
                model = await session.get(ModelPortfolio, mandate.model_portfolio_id)
                raise RiskProfileError(
                    f"The {model.name} model holds {equity:.0%} equity, above the {ceiling:.0%} ceiling for {row.capacity} "
                    f"capacity. Move this mandate to a model within the ceiling first.")
        suit["risk_profile"] = row.indicated
        suit["capacity_for_loss"] = row.capacity
        for key, value in (("risk_tolerance", row.indicated), ("risk_capacity", row.capacity)):
            if key in suit:   # what the admin page and the IPS read first, so it cannot be left behind
                suit[key] = value
    suit["risk_decision"] = {
        "assessment_id": str(row.id), "decision": decision, "reason": reason, "decided_by": by,
        "decided_at": utcnow().isoformat(), "was": {"risk_profile": profile, "capacity_for_loss": capacity},
    }
    mandate.suitability = suit
    flag_modified(mandate, "suitability")
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="risk_profile_decision", actor=by, content={
        "mandate_id": str(mandate.id), "assessment_id": str(row.id), "decision": decision, "reason": reason,
        "was": {"risk_profile": profile, "capacity_for_loss": capacity},
        "indicated": row.indicated, "capacity": row.capacity,
    })
    now_profile, now_capacity = on_file(suit)
    return {
        "mandate_id": str(mandate.id), "mandate": mandate.name, "decision": decision, "reason": reason,
        "risk_profile": now_profile, "capacity_for_loss": now_capacity, "indicated": row.indicated,
        "was": {"risk_profile": profile, "capacity_for_loss": capacity},
    }
