"""Client State Graph read services — assemble the unified per-client view from Core."""
from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.valuation import account_valuation
from app.models.balance_sheet import BalanceItem
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.portfolio import Holding
from app.models.tenant import Firm


def replaced_by(items, kind: str, person) -> bool:
    """Whether a recorded item stands in for what a person's profile says. A joint one does for everyone."""
    return any(i.kind == kind and i.person_id in (None, person.id) for i in items)


def _institution(name) -> str:
    return " ".join(str(name or "").lower().split())


def itemised_by(lines, owner_id, institution) -> bool:
    """Whether holdings listed line by line (B-198) stand in for a total recorded for the same owner at the same
    institution. A trust's lines stand in for nothing a person or the household recorded."""
    return any(l.entity_id is None and l.person_id == owner_id and _institution(l.institution) == _institution(institution)
               for l in lines)


def held_away_of(persons, items, lines=()) -> float:
    """What a household holds elsewhere: holdings listed line by line, recorded totals, and for anyone with
    neither, the figure on their profile. Never an estimate, and never two of them for the same money: the most
    detailed figure replaces the less detailed, owner by owner - lines replace a total recorded for the same
    institution, and either replaces a profile. `lines` are current and in the firm's currency."""
    recorded = sum(float(i.amount) for i in items
                   if i.kind == "held_away_investment" and not (lines and itemised_by(lines, i.person_id, i.name)))
    listed = sum(float(l.value) for l in lines)

    def covered(p) -> bool:
        return replaced_by(items, "held_away_investment", p) or any(
            l.entity_id is None and l.person_id in (None, p.id) for l in lines)
    return recorded + listed + sum(float((p.profile or {}).get("held_away") or 0) for p in persons if not covered(p))


async def household_brain(
    session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID
) -> dict | None:
    """The total-portfolio, multi-entity view of a household — the 'client brain' snapshot.

    `firm_id` is required and keyword-only, and a household belonging to any other firm is
    reported as not found. This used to look the household up by id alone, so anyone who
    could name another firm's household id could read its whole client brain — through the
    household routes, and through running any agent against it as a subject. An optional
    argument would leave that one forgotten keyword away; the conversation gateway will
    pass ids that came from a language model, so the omission has to be impossible.

    An id given as text (agents read them from `list_households`) is made a UUID here, because SQLite
    refuses text where Postgres casts it (B-226); text that is no id is not found.
    """
    if not isinstance(household_id, uuid.UUID):
        try:
            household_id = uuid.UUID(str(household_id))
        except ValueError:
            return None
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None

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

    from app.aurea_core import sip_step_up
    from app.models.sip import Sip

    sips_by_goal: dict = defaultdict(list)
    if goals:
        for row in (await session.execute(select(Sip).where(Sip.firm_id == firm_id, Sip.ended_on.is_(None),
                                                            Sip.goal_id.in_([g.id for g in goals])).order_by(Sip.starts_on))).scalars().all():
            sips_by_goal[row.goal_id].append({k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sip_step_up.terms(row).items()})

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

    firm = await session.get(Firm, firm_id)
    base = ((firm.base_currency if firm else None) or "INR").upper()
    held_items = (await session.execute(select(BalanceItem).where(
        BalanceItem.household_id == household_id, BalanceItem.kind == "held_away_investment", BalanceItem.status == "current",
    ))).scalars().all()
    from app.models.held_away import HeldAwayHolding

    held_lines = [l for l in (await session.execute(select(HeldAwayHolding).where(
        HeldAwayHolding.household_id == household_id, HeldAwayHolding.status == "current",
    ))).scalars().all() if (l.currency or base).upper() == base]
    from app.aurea_core.advisers import adviser_of

    adviser = await adviser_of(session, firm_id, household_id)
    from app.aurea_core.planning import assumption_set
    from app.aurea_core import tax_returns

    # The return that stands for each set of people, as a person recorded it from the document (B-183).
    returns_on_file = tax_returns.standing(await tax_returns.for_household(session, household.id, firm_id=firm_id))

    return {
        "tax_returns": returns_on_file,
        "currency": firm.base_currency if firm else None,
        # The firm's returns and inflation indexes, so every engine reading the brain projects on them (B-167).
        "planning": assumption_set(firm.settings if firm else None, firm.jurisdiction if firm else None),
        "household": {
            "id": str(household.id),
            "name": household.name,
            "segment": household.segment,
            "values": household.values,
            # Whose client this is, as a person recorded it; None is unassigned (aurea_core/advisers.py).
            "adviser": adviser.full_name if adviser else None,
        },
        "persons": [
            {
                "id": str(p.id), "full_name": p.full_name, "preferred_name": p.preferred_name,
                "segment": p.segment, "is_next_gen": p.is_next_gen, "kyc": p.kyc,
                "profile": p.profile,
                "date_of_birth": p.date_of_birth.isoformat() if p.date_of_birth else None,
                "deceased_on": p.deceased_on.isoformat() if p.deceased_on else None,
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
             "model_portfolio_id": str(m.model_portfolio_id) if m.model_portfolio_id else None,
             "person_id": str(m.person_id) if m.person_id else None,
             "entity_id": str(m.entity_id) if m.entity_id else None}
            for m in mandates
        ],
        "accounts": account_views,
        "goals": [
            {"id": str(g.id), "name": g.name, "kind": g.kind,
             "target_amount": float(g.target_amount), "target_date": g.target_date.isoformat() if g.target_date else None,
             "assumptions": g.assumptions, "priority": g.priority,
             # The SIP registrations recorded against it (B-394), only where there are any.
             **({"sips": sips_by_goal[g.id]} if g.id in sips_by_goal else {})}
            for g in goals
        ],
        "relationships": related_edges,
        "totals": {
            "total_value": round(total, 2),
            # Not part of total_value, which is what is managed and billed on.
            "held_away": round(held_away_of(persons, [i for i in held_items if (i.currency or base).upper() == base], held_lines), 2),
            "by_asset_class": {k: round(v, 2) for k, v in by_class.items()},
            "data_confidence": round(sum(confidences) / len(confidences), 3) if confidences else 1.0,
        },
    }


async def list_households(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    households = (
        await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None)))
    ).scalars().all()
    from app.aurea_core.advisers import of_households

    from app.aurea_core import firm_time, shelf
    from app.models.tenant import Firm

    held = await of_households(session, firm_id)
    names = {u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(set(held.values()))))).scalars().all()} if held else {}
    # An investor class is a certification a person recorded from a document (aurea_core/shelf.py), never AUM (B-125).
    classes = await shelf.household_classes(session, firm_id, today=firm_time.today(await session.get(Firm, firm_id)))
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
                    "adviser_id": str(held[h.id]) if h.id in held else None, "adviser": names.get(held.get(h.id)),
                    "total_value": round(total, 2), "investor_class": classes.get(h.id)})
    out.sort(key=lambda x: x["total_value"], reverse=True)
    return out
