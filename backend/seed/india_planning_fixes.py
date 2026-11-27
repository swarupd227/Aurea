"""India, the planning records the demonstration lacked (B-393): a tier on each seeded goal and each household's
essential spending.

Invented for the demonstration firm and said to be: a tier is what a person said, so these stand for what the families
would have said, and the spending carries its demonstration source. Run by the seed when it builds the firm and by
`seed.india_demo_backfill` for a firm already deployed. It adds only what is missing: a tier a person has since recorded
stays, and a household with any spending on file is left alone.
"""
from __future__ import annotations

from sqlalchemy import select

from app.aurea_core import balance_sheet, firm_time
from app.models.balance_sheet import EXPENSE_KINDS, BalanceItem
from app.models.graph import Goal, Household
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India planning (demonstration)"

# (household, goal) -> the tier the family would say: needs, wants or wishes, as the engines read them.
TIERS = {
    ("The Sharma Family", "Retirement at 60"): "needs",
    ("The Sharma Family", "Aarav's postgraduate study"): "wants",
    ("The Iyer Family", "Retirement at 58"): "needs",
    ("The Kapoor Family Trust", "Next generation endowment"): "wishes",
    ("The Banerjee Household", "Income through retirement"): "needs",
    ("The D'Souza Household", "Retirement at 60"): "needs",
    # The goals B-394 adds (seed/india_goals.py).
    ("The Sharma Family", "Aarav's wedding"): "wishes",
    ("The Iyer Family", "A second home"): "wants",
    ("The D'Souza Household", "Home renovation"): "wants",
}
# What this seed first wrote, which no engine reads as a tier: corrected where it is still there (B-394).
SINGULAR = {"need": "needs", "want": "wants", "wish": "wishes"}
# Rupees a year a household cannot do without.
ESSENTIAL_SPENDING = {
    "The Sharma Family": 1_800_000,
    "The Iyer Family": 3_600_000,
    "The Kapoor Family Trust": 2_400_000,
    "The Banerjee Household": 720_000,
    "The D'Souza Household": 1_200_000,
}


async def add(s, firm: Firm) -> int:
    """The tiers and the spending that are not on file yet. Returns how many records it wrote."""
    households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    written = 0
    for (home, name), tier in TIERS.items():
        household = households.get(home)
        if household is None:
            continue
        goal = (await s.execute(select(Goal).where(Goal.firm_id == firm.id, Goal.household_id == household.id,
                                                   Goal.name == name))).scalars().first()
        if goal is None:
            continue
        on_file = (goal.assumptions or {}).get("tier")
        if on_file and on_file not in SINGULAR:
            continue
        goal.assumptions = {**(goal.assumptions or {}), "tier": SINGULAR.get(on_file, tier)}
        written += 1
    for home, amount in ESSENTIAL_SPENDING.items():
        household = households.get(home)
        if household is None:
            continue
        spending = (await s.execute(select(BalanceItem.id).where(
            BalanceItem.firm_id == firm.id, BalanceItem.household_id == household.id,
            BalanceItem.kind.in_(EXPENSE_KINDS)).limit(1))).first()
        if spending:
            continue
        await balance_sheet.record_item(s, household.id, firm_id=firm.id, by=BY, kind="essential_spending",
                                        name="Essential household spending", amount=amount, as_of=firm_time.today(firm),
                                        source_document=SOURCE)
        written += 1
    await s.flush()
    return written
