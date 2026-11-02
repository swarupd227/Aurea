"""Estate & succession for one household: what the estate holds, its trusts, the next generation's
readiness, succession gaps, and what a step-up in basis would extinguish.

Moved out of the route so the conversation's read_estate and the Estate view read the same thing.
Basis steps up only where the household's market says it does (`markets.basis_on_death`) and never
in a retirement account, the rule `exits.py` already applies when a client dies.
"""
from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.exits import _RETIREMENT
from app.aurea_core.graph import household_brain
from app.models.graph import Account, Household, LegalEntity, Mandate
from app.models.portfolio import Holding
from app.models.tenant import Firm

TRUST_NOTES = {
    "NZ": "NZ Trusts Act 2019 §23 requires trustees to actively administer and review periodically.",
    "UK": "UK Trustee Act 2000 imposes an active investment duty. Trust must be registered with HMRC TRS.",
    "US": "Confirm pour-over will and beneficiary designations. Review step-up in basis and state law compliance.",
}


async def _step_up(session: AsyncSession, firm: Firm, household: Household, brain: dict) -> dict | None:
    """The unrealised gain a step-up at death would extinguish, outside retirement accounts. None where
    the household's market carries basis over."""
    market = markets.of_household(firm, household.values)
    if market is None or market.basis_on_death != "step_up":
        return None
    person_ids = [uuid.UUID(p["id"]) for p in brain.get("persons", [])]
    entity_ids = [uuid.UUID(e["id"]) for e in brain.get("entities", [])]
    clauses = [c for c in (Mandate.person_id.in_(person_ids) if person_ids else None,
                           Mandate.entity_id.in_(entity_ids) if entity_ids else None) if c is not None]
    gain = left_out = 0.0
    if clauses:
        mandate_ids = (await session.execute(select(Mandate.id).where(or_(*clauses)))).scalars().all()
        accounts = (await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))).scalars().all() if mandate_ids else []
        for acc in accounts:
            holdings = (await session.execute(select(Holding).where(Holding.account_id == acc.id))).scalars().all()
            if acc.registration_type in _RETIREMENT:
                left_out += sum(float(h.market_value or 0) for h in holdings)
                continue
            gain += sum(float(h.market_value or 0) - float(h.cost_basis or 0) for h in holdings)
    return {
        "market": market.code,
        "unrealised_gain": round(gain, 2),
        "step_up_benefit": round(max(gain, 0), 2),
        "retirement_value_excluded": round(left_out, 2),
        "note": ("Heirs would take these holdings at their value on the date of death, so this gain would not be taxed. "
                 "Retirement accounts are left out: their basis does not step up.") if gain > 0 else "No unrealised gains identified.",
    }


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    from app.agents.estate_succession import _governance_score, _succession_gaps

    brain = await household_brain(session, household_id, firm_id=firm_id)
    if not brain:
        return None
    firm = await session.get(Firm, firm_id)
    household = await session.get(Household, household_id)
    entity_rows = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))).scalars().all()
    entities_gov = [{"id": str(e.id), "name": e.name, "entity_type": e.entity_type,
                     "governance": e.governance or {}, "jurisdiction": e.jurisdiction} for e in entity_rows]

    hh_jurisdiction = (brain["household"].get("values") or {}).get("jurisdiction") or firm.jurisdiction or "NZ"
    totals = brain.get("totals", {})
    total_value = totals.get("total_value", 0) or 0
    wealth_breakdown = {cls: {"value": round(v, 2), "pct": round(v / total_value, 4) if total_value else 0.0}
                        for cls, v in totals.get("by_asset_class", {}).items() if v}

    trust_structures = []
    for ent in entities_gov:
        gov = ent["governance"]
        trustees = gov.get("trustees", [])
        beneficiaries = gov.get("beneficiaries", [])
        trust_structures.append({
            "id": ent["id"], "name": ent["name"], "entity_type": ent["entity_type"],
            "governance_score": _governance_score(gov),
            "trustees": trustees if isinstance(trustees, list) else [trustees],
            "settlor": gov.get("settlor"),
            "beneficiaries_count": len(beneficiaries) if isinstance(beneficiaries, list) else 1,
            "established": gov.get("established"), "review_date": gov.get("review_date"),
            "jurisdiction": ent["jurisdiction"],
            "compliance_note": TRUST_NOTES.get(ent["jurisdiction"] or hh_jurisdiction),
        })

    person_ids_with_mandate = {m.get("person_id") for m in brain.get("mandates", []) if m.get("person_id")}
    person_ids_with_goal = {g.get("person_id") for g in brain.get("goals", []) if g.get("person_id")}
    heir_readiness = []
    for p in brain.get("persons", []):
        if not p.get("is_next_gen"):
            continue
        has_mandate = p["id"] in person_ids_with_mandate
        has_goals = p["id"] in person_ids_with_goal
        has_profile = bool((p.get("profile") or {}).get("risk_profile"))
        score = sum([has_mandate * 40, has_goals * 30, has_profile * 30])
        actions = [a for a, missing in (("Establish an advisory mandate", not has_mandate),
                                        ("Define legacy and education goals", not has_goals),
                                        ("Complete a risk-profile assessment", not has_profile)) if missing]
        heir_readiness.append({
            "id": p["id"], "name": p.get("preferred_name") or p["full_name"], "date_of_birth": p.get("date_of_birth"),
            "has_mandate": has_mandate, "has_goals": has_goals, "has_profile": has_profile,
            "readiness_score": score, "readiness_level": "ready" if score >= 70 else ("developing" if score >= 40 else "early"),
            "suggested_actions": actions,
        })

    gaps = _succession_gaps(brain, entities_gov, jurisdiction=hh_jurisdiction)
    high_count = sum(1 for g in gaps if g.get("severity") == "high")
    risk_score = min(100, high_count * 30 + sum(10 if g["severity"] == "medium" else 5 for g in gaps if g["severity"] != "high"))

    return {
        "household_id": str(household_id),
        "household_name": brain["household"]["name"],
        "currency": brain.get("currency"),
        "total_estate_value": round(total_value, 2),
        "wealth_breakdown": wealth_breakdown,
        "trust_structures": trust_structures,
        "heir_readiness": heir_readiness,
        "succession_gaps": gaps,
        "succession_risk": {"score": risk_score,
                            "level": "high" if risk_score >= 50 else ("moderate" if risk_score >= 20 else "low"),
                            "gaps_count": len(gaps), "high_severity_count": high_count},
        "step_up_basis": await _step_up(session, firm, household, brain),
        "generated_at": firm_time.today(firm).isoformat(),
    }
