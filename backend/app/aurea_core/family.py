"""A family across households: everyone linked to a household's members by spouse, parent, child or
intergenerational edges, the households they sit in, what their own mandates hold, and their goals.

Moved out of the route so the conversation's read_family and the Family view read the same thing. The
route used to look people and households up by id alone, so a person id from another firm returned
that firm's household names; every read here is held to the caller's firm.
"""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Account, Goal, Household, Mandate, Person, RelationshipEdge
from app.models.portfolio import Holding
from app.models.tenant import Firm

FAMILY_KINDS = {"intergenerational", "spouse", "parent", "child"}
AUM_BASIS = "Securities held in family members' own mandates; cash and trusts' mandates are not included."


class FamilyError(ValueError):
    """A family that cannot be read as asked, with the reason."""


async def for_anchor(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None,
                     person_id: uuid.UUID | None = None) -> dict:
    """The family around a household's members, or around one person."""
    if household_id:
        anchor = await session.get(Household, household_id)
        if anchor is None or anchor.firm_id != firm_id:
            raise FamilyError("Household not found.")
        start = set((await session.execute(select(Person.id).where(
            Person.household_id == household_id, Person.firm_id == firm_id))).scalars().all())
    elif person_id:
        person = await session.get(Person, person_id)
        if person is None or person.firm_id != firm_id:
            raise FamilyError("Person not found.")
        anchor = await session.get(Household, person.household_id) if person.household_id else None
        start = {person.id}
    else:
        raise FamilyError("Give a household or a person.")

    seen, frontier = set(start), list(start)
    while frontier:
        edges = (await session.execute(select(RelationshipEdge).where(
            RelationshipEdge.firm_id == firm_id, RelationshipEdge.kind.in_(FAMILY_KINDS),
            RelationshipEdge.from_id.in_(frontier)))).scalars().all()
        frontier = []
        for e in edges:
            if e.to_type == "person" and e.to_id not in seen:
                seen.add(e.to_id)
                frontier.append(e.to_id)

    people = (await session.execute(select(Person).where(Person.id.in_(seen), Person.firm_id == firm_id))).scalars().all() if seen else []
    household_ids = {p.household_id for p in people if p.household_id}
    households = (await session.execute(select(Household).where(
        Household.id.in_(household_ids), Household.firm_id == firm_id))).scalars().all() if household_ids else []
    names = {h.id: h.name for h in households}
    person_ids = [p.id for p in people]

    aum = float((await session.execute(
        select(func.coalesce(func.sum(Holding.market_value), 0))
        .join(Account, Account.id == Holding.account_id)
        .join(Mandate, Mandate.id == Account.mandate_id)
        .where(Mandate.firm_id == firm_id, Mandate.person_id.in_(person_ids))
    )).scalar_one()) if person_ids else 0.0

    goals = (await session.execute(select(Goal).where(
        Goal.firm_id == firm_id, Goal.household_id.in_(household_ids)))).scalars().all() if household_ids else []
    firm = await session.get(Firm, firm_id)
    return {
        "anchor": {"household_id": str(anchor.id) if anchor else None, "household": anchor.name if anchor else None},
        "person_ids": [str(p) for p in person_ids],
        "persons": [{"id": str(p.id), "full_name": p.full_name, "household_id": str(p.household_id) if p.household_id else None,
                     "household": names.get(p.household_id)} for p in people],
        "household_count": len(households),
        "households": [{"id": str(h.id), "name": h.name, "segment": h.segment} for h in households],
        "total_family_aum": round(aum, 2),
        "aum_basis": AUM_BASIS,
        "currency": firm.base_currency if firm else None,
        "goal_count": len(goals),
        "goals": [{"name": g.name, "kind": g.kind, "target_amount": float(g.target_amount or 0),
                   "household_id": str(g.household_id)} for g in goals],
    }
