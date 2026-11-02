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
adviser keeps a different profile, so one household's read shows that beside the result, as figures, and a
decision holds only until the client's next answers. A person or a trust can be asked for itself, and those answers
speak for its own mandates; a household's stand for the rest.

A mandate is not moved past the equity it is managed to, its IPS where one is in force and otherwise its
model: a mandate over its ceiling breaches on the next rebalance, and that is a high-severity failure
which pauses the agent for the whole firm.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
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


def model_profile(name: str | None) -> str | None:
    """The profile a model is built for. Its name is the only thing that says so."""
    words = (name or "").lower()
    return next((p for p in _PROFILE_RANK if p in words), None)


def model_fit(profile: str | None, model: str | None) -> str | None:
    """How a mandate's model sits against the profile on file. None with no model. Adopting a profile changes the
    profile and not the model, so this is what says the two have parted. Only a person moves a mandate to a new model."""
    if model is None:
        return None
    stated = model_profile(model)
    if stated is None or profile not in _PROFILE_RANK:
        return "not_stated"
    gap = _PROFILE_RANK[stated] - _PROFILE_RANK[profile]
    return "above_profile" if gap > 0 else "below_profile" if gap < 0 else "matches"


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
    names: dict[uuid.UUID, str] = {}
    for model, label in ((Person, Person.full_name), (LegalEntity, LegalEntity.name)):
        rows = await session.execute(select(model.id, model.household_id, label).where(model.household_id.in_(ids)))
        for owner, household, name in rows.all():
            owners[owner] = household
            names[owner] = name
    mandates: dict[uuid.UUID, list[Mandate]] = {}
    for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id, Mandate.is_active.is_(True)).order_by(Mandate.name)
    )).scalars().all():
        household = owners.get(m.person_id) or owners.get(m.entity_id)
        if household is not None:
            mandates.setdefault(household, []).append(m)

    # A household's answers stand for every mandate in it, until a person or an entity has been asked for itself.
    latest: dict[uuid.UUID, RiskAssessment] = {}
    own: dict[uuid.UUID, RiskAssessment] = {}
    for row in (await session.execute(
        select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id.in_(ids))
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc())
    )).scalars().all():
        subject = row.person_id or row.entity_id
        if subject:
            own.setdefault(subject, row)
        else:
            latest.setdefault(row.household_id, row)
    model_ids = {m.model_portfolio_id for rows in mandates.values() for m in rows if m.model_portfolio_id}
    models = {i: n for i, n in (await session.execute(select(ModelPortfolio.id, ModelPortfolio.name).where(ModelPortfolio.id.in_(model_ids)))).all()} if model_ids else {}
    mandate_ids = [m.id for rows in mandates.values() for m in rows]
    with_ips = set((await session.execute(select(InvestmentPolicy.mandate_id).where(InvestmentPolicy.mandate_id.in_(mandate_ids)))).scalars().all()) if mandate_ids else set()

    views = []
    for h in households:
        row = latest.get(h.id)
        rows = []
        for m in mandates.get(h.id, []):
            profile, capacity = on_file(m.suitability)
            decision = (m.suitability or {}).get("risk_decision")
            owner = m.person_id or m.entity_id
            mine = own.get(owner)
            model = models.get(m.model_portfolio_id)
            rows.append({
                "mandate_id": str(m.id), "mandate": m.name, "risk_profile": profile, "capacity_for_loss": capacity,
                "status": _mandate_status(mine or row, profile, capacity, decision), "decision": decision,
                "owner_id": str(owner) if owner else None, "owner": names.get(owner), "assessment": _assessment_view(mine),
                "model": model, "model_fit": "ips" if m.id in with_ips else model_fit(profile, model),
            })
        statuses = [r["status"] for r in rows]
        asked_alone = any(r["assessment"] for r in rows)
        status = ("not_taken" if row is None and not asked_alone else "not_scored" if row is not None and row.indicated is None else
                  "no_mandate" if not rows else min(statuses, key=_STATUS_ORDER.index))
        views.append({"household_id": str(h.id), "household": h.name, "status": status,
                      "assessment": _assessment_view(row), "mandates": rows})
    return views


async def wealth_against_needs(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """What the questionnaire cannot see: what could be drawn on, less what is owed, against what the household
    spends. It sits beside the result and never changes it, and with no spending on file it is not assessed."""
    from app.aurea_core import balance_sheet

    sheet = await balance_sheet.for_household(session, household_id, firm_id=firm_id)
    if sheet is None:
        return None
    flow = sheet["cash_flow"]
    owed = sheet["liabilities"]["total"]
    free = round(sheet["liquid"] - owed, 2)
    spending = flow["expenses"]
    return {"status": "assessed" if spending else "not_assessed", "liquid": sheet["liquid"], "owed": owed, "free": free, "annual_spending": spending,
            "debt_payments": flow["debt_payments"], "years_covered": round(free / (spending + flow["debt_payments"]), 1) if spending else None, "basis": sheet["basis"]}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """One household's latest answers and how each mandate compares, or None if it is not in this firm."""
    views = await _views(session, firm_id, household_id)
    if not views:
        return None
    return {**views[0], "wealth": await wealth_against_needs(session, household_id, firm_id=firm_id)}


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
            "model_above": sum(1 for r in rows for m in r["mandates"] if m["model_fit"] == "above_profile"),
        },
    }


def _of_subject(query, subject_id: uuid.UUID | None):
    """A query narrowed to one person's or entity's answers, or with none, the household's own."""
    if subject_id is None:
        return query.where(RiskAssessment.person_id.is_(None), RiskAssessment.entity_id.is_(None))
    return query.where(or_(RiskAssessment.person_id == subject_id, RiskAssessment.entity_id == subject_id))


async def latest_answers(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, subject_id: uuid.UUID | None = None) -> dict:
    row = (await session.execute(_of_subject(
        select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id == household_id), subject_id)
        .order_by(RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc()).limit(1)
    )).scalars().first()
    return clean(row.answers) if row else {}


async def subjects(session: AsyncSession, household_id: uuid.UUID) -> list[dict]:
    """Who in a household can be asked for themselves. Next-gen members hold no mandate of their own."""
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.is_next_gen.is_(False)).order_by(Person.full_name))).scalars().all()
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id).order_by(LegalEntity.name))).scalars().all()
    return [*({"id": str(p.id), "name": p.full_name, "kind": "person"} for p in people),
            *({"id": str(e.id), "name": e.name, "kind": "entity"} for e in entities)]


async def submit(session: AsyncSession, household_id: uuid.UUID, answers: dict | None, *, firm_id: uuid.UUID,
                 by: str, role: str, subject_id: uuid.UUID | None = None) -> RiskAssessment:
    """Record a completed questionnaire, for the household or for one person or entity in it. A trust is its own
    investor: its answers are the trustees', for its beneficiaries and horizon. A mandate does not change."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise RiskProfileError("Household not found.")
    person = await session.get(Person, subject_id) if subject_id else None
    entity = await session.get(LegalEntity, subject_id) if subject_id and person is None else None
    if subject_id and (person or entity) is None or (person or entity) is not None and (person or entity).household_id != household_id:
        raise RiskProfileError("That person or entity is not in this household.")
    scored = score(answers)
    if scored["status"] != "scored":
        raise RiskProfileError(f"Every question needs one of its answers. Missing: {', '.join(scored['missing'])}.")
    row = RiskAssessment(
        firm_id=firm_id, household_id=household_id, person_id=person.id if person else None, entity_id=entity.id if entity else None,
        answers=clean(answers), scoring_version=SCORING_VERSION,
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


async def prepare_decision(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID, *, decision: str,
                           reason: str | None) -> dict:
    """Everything a decision would refuse, refused without writing, so a Confirm card that would only
    fail is not shown. Returns the mandate, the answers it rests on and what is on file now."""
    if decision not in DECISIONS:
        raise RiskProfileError(f"The decision must be one of: {', '.join(DECISIONS)}.")
    mandate = await session.get(Mandate, mandate_id)
    if mandate is None or mandate.firm_id != firm_id or not mandate.is_active:
        raise RiskProfileError("Mandate not found.")
    household_id = await _household_of(session, mandate)
    latest = select(RiskAssessment).where(RiskAssessment.firm_id == firm_id, RiskAssessment.household_id == household_id)
    newest = (RiskAssessment.completed_at.desc(), RiskAssessment.created_at.desc())
    row = (await session.execute(_of_subject(latest, mandate.person_id or mandate.entity_id).order_by(*newest).limit(1))).scalars().first()
    if row is None:
        row = (await session.execute(_of_subject(latest, None).order_by(*newest).limit(1))).scalars().first()
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
    model = await session.get(ModelPortfolio, mandate.model_portfolio_id) if mandate.model_portfolio_id else None
    policy = (await session.execute(select(InvestmentPolicy.id).where(InvestmentPolicy.mandate_id == mandate.id).limit(1))).first()
    after = row.indicated if decision == "adopt" else profile
    return {"mandate": mandate, "assessment": row, "reason": reason, "profile": profile, "capacity": capacity,
            "model": model.name if model else None, "model_fit": None if policy else model_fit(after, model.name if model else None)}


async def decide(session: AsyncSession, firm_id: uuid.UUID, mandate_id: uuid.UUID, *, decision: str,
                 reason: str | None, by: str) -> dict:
    """An adviser's decision on the household's latest answers, for one mandate."""
    ready = await prepare_decision(session, firm_id, mandate_id, decision=decision, reason=reason)
    mandate, row, reason = ready["mandate"], ready["assessment"], ready["reason"]
    profile, capacity = ready["profile"], ready["capacity"]
    suit = dict(mandate.suitability or {})
    if decision == "adopt":
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
