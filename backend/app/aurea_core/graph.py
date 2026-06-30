"""Client State Graph read services — assemble the unified per-client view from Core."""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.valuation import account_valuation
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.portfolio import Holding


async def household_brain(session: AsyncSession, household_id: uuid.UUID) -> dict | None:
    """The total-portfolio, multi-entity view of a household — the 'client brain' snapshot."""
    household = await session.get(Household, household_id)
    if household is None:
        return None
    firm_id = household.firm_id

    persons = (
        await session.execute(select(Person).where(Person.household_id == household_id))
    ).scalars().all()
    entities = (
        await session.execute(select(LegalEntity).where(LegalEntity.household_id == household_id))
    ).scalars().all()

    person_ids = [p.id for p in persons]
    entity_ids = [e.id for e in entities]
    conds = []
    if person_ids:
        conds.append(Mandate.person_id.in_(person_ids))
    if entity_ids:
        conds.append(Mandate.entity_id.in_(entity_ids))
    mandates = (
        await session.execute(select(Mandate).where(or_(*conds)))
    ).scalars().all() if conds else []

    mandate_ids = [m.id for m in mandates]
    accounts = (
        await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
    ).scalars().all() if mandate_ids else []

    goals = (
        await session.execute(select(Goal).where(Goal.household_id == household_id))
    ).scalars().all()

    edges = (
        await session.execute(
            select(RelationshipEdge).where(RelationshipEdge.firm_id == firm_id)
        )
    ).scalars().all()

    # Value each account and roll up.
    total = 0.0
    by_class: dict[str, float] = defaultdict(float)
    account_views = []
    confidences = []
    for acc in accounts:
        val = await account_valuation(session, acc)
        total += val["total_value"]
        for cls, v in val["by_asset_class"].items():
            by_class[cls] += v
        confidences.append(val["data_confidence"])
        account_views.append({"id": str(acc.id), "name": acc.name, "custodian": acc.custodian,
                              "mandate_id": str(acc.mandate_id) if acc.mandate_id else None, **val})

    member_node_ids = set(map(str, person_ids + entity_ids))
    related_edges = [
        {
            "kind": e.kind,
            "from": {"type": e.from_type, "id": str(e.from_id)},
            "to": {"type": e.to_type, "id": str(e.to_id)},
        }
        for e in edges
        if str(e.from_id) in member_node_ids or str(e.to_id) in member_node_ids
    ]

    return {
        "household": {
            "id": str(household.id),
            "name": household.name,
            "segment": household.segment,
            "values": household.values,
        },
        "persons": [
            {
                "id": str(p.id), "full_name": p.full_name, "preferred_name": p.preferred_name,
                "segment": p.segment, "is_next_gen": p.is_next_gen, "kyc": p.kyc,
                "profile": p.profile,
                "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
            }
            for p in persons
        ],
        "entities": [
            {"id": str(e.id), "name": e.name, "entity_type": e.entity_type,
             "impact_objectives": e.impact_objectives}
            for e in entities
        ],
        "mandates": [
            {"id": str(m.id), "name": m.name, "mandate_type": m.mandate_type,
             "suitability": m.suitability, "constraints": m.constraints,
             "model_portfolio_id": str(m.model_portfolio_id) if m.model_portfolio_id else None}
            for m in mandates
        ],
        "accounts": account_views,
        "goals": [
            {"id": str(g.id), "name": g.name, "kind": g.kind,
             "target_amount": float(g.target_amount), "target_date": g.target_date.isoformat() if g.target_date else None,
             "assumptions": g.assumptions}
            for g in goals
        ],
        "relationships": related_edges,
        "totals": {
            "total_value": round(total, 2),
            "by_asset_class": {k: round(v, 2) for k, v in by_class.items()},
            "data_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 1.0,
        },
    }


async def list_households(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    households = (
        await session.execute(select(Household).where(Household.firm_id == firm_id))
    ).scalars().all()
    out = []
    for h in households:
        # Lightweight total via holdings sum.
        mandates = (
            await session.execute(
                select(Mandate).where(
                    Mandate.person_id.in_(
                        select(Person.id).where(Person.household_id == h.id)
                    )
                    | Mandate.entity_id.in_(
                        select(LegalEntity.id).where(LegalEntity.household_id == h.id)
                    )
                )
            )
        ).scalars().all()
        mandate_ids = [m.id for m in mandates]
        total = 0.0
        if mandate_ids:
            accounts = (
                await session.execute(select(Account).where(Account.mandate_id.in_(mandate_ids)))
            ).scalars().all()
            acc_ids = [a.id for a in accounts]
            total += sum(float(a.cash_balance or 0) for a in accounts)
            if acc_ids:
                holdings = (
                    await session.execute(select(Holding).where(Holding.account_id.in_(acc_ids)))
                ).scalars().all()
                total += sum(float(hh.market_value or 0) for hh in holdings)
        out.append({"id": str(h.id), "name": h.name, "segment": h.segment,
                    "total_value": round(total, 2)})
    out.sort(key=lambda x: x["total_value"], reverse=True)
    return out
