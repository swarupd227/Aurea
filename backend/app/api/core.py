"""Aurea Core API — the unified client brain, planning & risk."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import behavioural, goal_tradeoff, planning, regulatory_countdown, retirement, tax_intelligence
from app.aurea_core.graph import household_brain, list_households
from app.core.db import get_db
from app.core.security import get_current_user, staff_user
from app.models.graph import Goal, LegalEntity
from app.models.tenant import Firm

router = APIRouter(prefix="/api/core", tags=["core"], dependencies=[Depends(staff_user)])


@router.get("/households")
async def households(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    return await list_households(db, firm.id)


@router.get("/households/{household_id}")
async def household_detail(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    brain = await household_brain(db, household_id)
    if not brain or brain["household"]["id"] != str(household_id):
        raise HTTPException(status_code=404, detail="Household not found")
    return brain


@router.get("/households/{household_id}/planning")
async def household_planning(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Goals-based projections, whole-portfolio risk and stress testing for a household."""
    brain = await household_brain(db, household_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")
    total = brain["totals"]["total_value"]
    by_class = brain["totals"]["by_asset_class"]
    allocation = {k: v for k, v in by_class.items()}

    risk = planning.portfolio_risk(allocation, total)
    stress = planning.stress_test(allocation)

    goal_projections = []
    for g in brain["goals"]:
        assumptions = g.get("assumptions") or {}
        proj = planning.project_goal(
            current_value=total * assumptions.get("funding_share", 1.0 / max(len(brain["goals"]), 1)),
            allocation=allocation,
            annual_contribution=assumptions.get("annual_contribution", 0),
            annual_withdrawal=assumptions.get("annual_withdrawal", 0),
            years=assumptions.get("years", 15),
            target_amount=g["target_amount"],
        )
        goal_projections.append({"goal": g["name"], "kind": g["kind"], **proj.__dict__})

    return {
        "household_id": str(household_id),
        "total_value": total,
        "allocation": allocation,
        "risk": risk,
        "stress_test": stress,
        "goals": goal_projections,
    }


@router.get("/households/{household_id}/retirement")
async def household_retirement(
    household_id: uuid.UUID,
    retirement_age: int | None = None, longevity_age: int | None = None,
    annual_income: float | None = None, annual_contribution: float | None = None,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Retirement income & decumulation projection. Optional query params drive what-if analysis."""
    overrides = {k: v for k, v in {
        "retirement_age": retirement_age, "longevity_age": longevity_age,
        "annual_income": annual_income, "annual_contribution": annual_contribution,
    }.items() if v is not None}
    plan = await retirement.for_household(db, household_id, overrides=overrides)
    if not plan:
        raise HTTPException(status_code=404, detail="Household not found")
    return plan


@router.get("/households/{household_id}/estate")
async def household_estate(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Estate & succession analysis — wealth breakdown, trust governance, heir readiness, succession gaps."""
    from app.agents.estate_succession import _governance_score, _succession_gaps

    brain = await household_brain(db, household_id)
    if not brain:
        raise HTTPException(status_code=404, detail="Household not found")

    # Load full LegalEntity rows (need governance field not in brain snapshot)
    entity_rows = (
        await db.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))
    ).scalars().all()
    entities_gov = [
        {
            "id": str(e.id), "name": e.name, "entity_type": e.entity_type,
            "governance": e.governance or {}, "jurisdiction": e.jurisdiction,
        }
        for e in entity_rows
    ]

    # Resolve jurisdiction once — household value overrides firm default
    hh_values = brain.get("household", {}).get("values") or {}
    hh_jurisdiction = hh_values.get("jurisdiction") or firm.jurisdiction or "NZ"

    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    by_class = totals.get("by_asset_class", {})

    # Wealth breakdown with percentages
    wealth_breakdown = {
        cls: {
            "value": round(v, 2),
            "pct": round(v / total_value, 4) if total_value else 0.0,
        }
        for cls, v in by_class.items()
        if v
    }

    # Trust structure summaries
    trust_structures = []
    for ent in entities_gov:
        gov = ent["governance"]
        score = _governance_score(gov)
        trustees = gov.get("trustees", [])
        beneficiaries = gov.get("beneficiaries", [])
        trust_structures.append({
            "id": ent["id"],
            "name": ent["name"],
            "entity_type": ent["entity_type"],
            "governance_score": score,
            "trustees": trustees if isinstance(trustees, list) else [trustees],
            "settlor": gov.get("settlor"),
            "beneficiaries_count": len(beneficiaries) if isinstance(beneficiaries, list) else 1,
            "established": gov.get("established"),
            "review_date": gov.get("review_date"),
            "jurisdiction": ent["jurisdiction"],
            "compliance_note": {
                "NZ": "NZ Trusts Act 2019 §23 requires trustees to actively administer and review periodically.",
                "UK": "UK Trustee Act 2000 imposes an active investment duty. Trust must be registered with HMRC TRS.",
                "US": "Confirm pour-over will and beneficiary designations. Review step-up in basis and state law compliance.",
            }.get(ent["jurisdiction"] or hh_jurisdiction),
        })

    # Heir readiness
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])
    person_ids_with_mandate = {m.get("person_id") for m in mandates if m.get("person_id")}
    goals = brain.get("goals", [])
    person_ids_with_goal = {g.get("person_id") for g in goals if g.get("person_id")}

    heir_readiness = []
    for p in persons:
        if not p.get("is_next_gen"):
            continue
        has_mandate = p["id"] in person_ids_with_mandate
        has_goals = p["id"] in person_ids_with_goal
        has_profile = bool((p.get("profile") or {}).get("risk_profile"))
        score = sum([has_mandate * 40, has_goals * 30, has_profile * 30])
        level = "ready" if score >= 70 else ("developing" if score >= 40 else "early")
        actions = []
        if not has_mandate:
            actions.append("Establish an advisory mandate")
        if not has_goals:
            actions.append("Define legacy and education goals")
        if not has_profile:
            actions.append("Complete a risk-profile assessment")
        heir_readiness.append({
            "id": p["id"],
            "name": p.get("preferred_name") or p["full_name"],
            "date_of_birth": p.get("date_of_birth"),
            "has_mandate": has_mandate,
            "has_goals": has_goals,
            "has_profile": has_profile,
            "readiness_score": score,
            "readiness_level": level,
            "suggested_actions": actions,
        })

    # Succession gaps — jurisdiction-aware
    gaps = _succession_gaps(brain, entities_gov, jurisdiction=hh_jurisdiction)
    high_count = sum(1 for g in gaps if g.get("severity") == "high")
    risk_score = min(100, high_count * 30 + sum(
        10 if g["severity"] == "medium" else 5 for g in gaps if g["severity"] != "high"
    ))
    risk_level = "high" if risk_score >= 50 else ("moderate" if risk_score >= 20 else "low")

    return {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "total_estate_value": round(total_value, 2),
        "wealth_breakdown": wealth_breakdown,
        "trust_structures": trust_structures,
        "heir_readiness": heir_readiness,
        "succession_gaps": gaps,
        "succession_risk": {
            "score": risk_score,
            "level": risk_level,
            "gaps_count": len(gaps),
            "high_severity_count": high_count,
        },
        "generated_at": date.today().isoformat(),
    }


@router.post("/households/{household_id}/goal-tradeoff")
async def household_goal_tradeoff(
    household_id: uuid.UUID,
    body: dict,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Multi-goal trade-off engine: shared-capital MC across all household goals.

    Body (all optional):
      priorities       {goal_id: rank}      — override goal priority ranks
      goal_overrides   {goal_id: {years_delta, target_scale, extra_contribution}}
      annual_income    float                — override for human-capital calculation
    """
    plan = await goal_tradeoff.for_household(
        db, household_id,
        priority_overrides=body.get("priorities"),
        goal_overrides=body.get("goal_overrides"),
        annual_income=body.get("annual_income"),
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Household not found")
    return plan


@router.get("/households/{household_id}/behavioural")
async def household_behavioural(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Behavioural finance profile: bias scores, stress playbook, transcript signals, adviser coaching."""
    result = await behavioural.for_household(db, household_id)
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/tax-intel")
async def household_tax_intel(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Tax intelligence: jurisdiction-dispatched modules (NZ / US / UK)."""
    result = await tax_intelligence.for_household(db, household_id, firm.jurisdiction or "NZ")
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/households/{household_id}/regulatory-countdown")
async def household_regulatory_countdown(
    household_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Jurisdiction-aware regulatory deadline analysis: US estate tax sunset, UK IHT pension inclusion."""
    result = await regulatory_countdown.for_household(db, household_id, firm.jurisdiction or "NZ")
    if not result:
        raise HTTPException(status_code=404, detail="Household not found")
    return result


@router.get("/regulatory-countdown/firm")
async def firm_regulatory_countdown(
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Firm-wide regulatory countdown — all US and UK households with active countdown analyses."""
    results = await regulatory_countdown.for_firm(db, firm.id, firm.jurisdiction or "NZ")
    return {"results": results, "total": len(results)}


@router.post("/portfolio-whatif")
async def portfolio_whatif(
    body: dict, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Risk/return + stress for a hypothetical allocation (drives the client what-if panel)."""
    allocation = {k: float(v) for k, v in (body.get("allocation") or {}).items()}
    total = sum(allocation.values()) or 0.0
    return {
        "total": round(total, 2),
        "risk": planning.portfolio_risk(allocation, total),
        "stress": planning.stress_test(allocation),
    }


@router.post("/projection")
async def ad_hoc_projection(
    body: dict, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """What-if projection from an explicit allocation + assumptions (used by Studio/Canvas)."""
    proj = planning.project_goal(
        current_value=float(body.get("current_value", 0)),
        allocation=body.get("allocation", {"equity": 0.6, "fixed_income": 0.3, "cash": 0.1}),
        annual_contribution=float(body.get("annual_contribution", 0)),
        annual_withdrawal=float(body.get("annual_withdrawal", 0)),
        years=int(body.get("years", 15)),
        target_amount=float(body.get("target_amount", 0)),
    )
    return proj.__dict__
