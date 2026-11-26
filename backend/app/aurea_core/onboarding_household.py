"""A case opened into a household already on the book, so a couple is one household (B-385).

The person opening the case says which household it joins; none said means a new one, as before. Activation then adds the new
person or entity to that household instead of making a second one, and where a member of exactly that name is already there the
account is theirs, so nobody is entered twice. Reversing the activation removes only what that activation made: the household,
and a member who was there before it, stay. A household of another firm, or one that has left, is refused.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Household, LegalEntity, Person
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm


class HouseholdError(ValueError):
    """This case cannot join that household, with the reason."""


async def resolve(session: AsyncSession, firm: Firm, household_id) -> Household | None:
    if household_id in (None, ""):
        return None
    try:
        household = await session.get(Household, uuid.UUID(str(household_id)))
    except ValueError:
        household = None
    if household is None or household.firm_id != firm.id:
        raise HouseholdError("No such household at this firm. Give its id from the client search, or leave it out to open a new one.")
    if household.left_on is not None:
        raise HouseholdError(f"{household.name} has left the firm, so no case is opened into it.")
    return household


async def member(session: AsyncSession, household: Household, name: str, *, entity: bool):
    """The living member, or the entity, of exactly this name already in the household."""
    model = LegalEntity if entity else Person
    wanted = " ".join((name or "").split()).casefold()
    for row in (await session.execute(select(model).where(model.household_id == household.id))).scalars().all():
        if " ".join((row.name if entity else row.full_name).split()).casefold() == wanted and not getattr(row, "deceased_on", None):
            return row
    return None


def keep_on_reversal(materialized: dict) -> set[str]:
    """The references a reversal leaves in place, because the activation did not make them."""
    kept: set[str] = set()
    if materialized.get("joined_household"):
        kept.add("household_id")
    if materialized.get("existing_holder"):
        kept |= {"person_id", "entity_id"}
    return kept


async def undo_carried(session: AsyncSession, case: OnboardingCase) -> None:
    """What this case carried onto a household that stays is taken off again; a new household takes its own with it."""
    from app.models.balance_sheet import BalanceItem

    made = case.materialized or {}
    if not made.get("joined_household"):
        return
    rows = (await session.execute(select(BalanceItem).where(
        BalanceItem.household_id == uuid.UUID(str(made["household_id"])), BalanceItem.recorded_by == "onboarding"))).scalars().all()
    for r in rows:
        if (r.source_document or "").startswith("Declared at onboarding") and (r.source_document or "").endswith(f": {case.prospect_name}"):
            await session.delete(r)
