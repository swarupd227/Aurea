"""India, goals sized in conversation and a step-up SIP (B-394): the demonstration's rows.

Invented for the demonstration firm and said to be: a wedding, a second home and a renovation, each recorded through the
same checks a person's goal passes (`goal_record`), with the tiers the families would have said
(`india_planning_fixes.TIERS`), and one simulated SIP registration with a yearly step-up saving towards the wedding. Run
by the seed when it builds the firm and by `seed.india_demo_backfill` for a firm already deployed. The SIP runs in the
family's HUF account, with a bank account verified from an invented cheque and a debit mandate whose limit is the
step-up's cap, so it adds nothing to the debit queue the other demonstration SIPs fill (B-388). It adds only what is
missing: a goal of the same name stays as it is, a household whose shares a person has changed is left alone, and the
SIP is not recorded where any SIP already saves towards the goal.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.aurea_core import firm_time, goal_record, money_in, sip, sip_mandates
from app.models.enums import UserRole
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate
from app.models.portfolio import Instrument
from app.models.sip import Sip
from app.models.tenant import Firm

BY = "seed: India goals (demonstration)"
SOURCE = "Demonstration record, invented for the demonstration firm"

# (household, name, kind, target in today's rupees, years, priority, monthly saving, funding share %). None = not said.
GOALS = [
    ("The Sharma Family", "Aarav's wedding", "wedding", 3_000_000, 8, "low", None, None),
    ("The Iyer Family", "A second home", "home", 10_000_000, 6, "medium", 100_000, 10),
    ("The D'Souza Household", "Home renovation", "other", 900_000, 2, "medium", 20_000, 10),
]
# The registration the Sharmas' wedding saving is simulated from.
SIP_HOUSEHOLD, SIP_GOAL = "The Sharma Family", "Aarav's wedding"
SIP_TERMS = {"amount": 15_000, "day_of_month": 25, "step_up_percent": 10, "step_up_every_months": 12, "step_up_cap": 30_000}
SIP_FUND = "PPFAS-FLEXI-DIRECT"
BANK, LAST4, REFERENCE = "A demonstration bank (invented)", "3947", "UMRN-DEMO-SHARMA-HUF-0001"


async def add(s, firm: Firm) -> int:
    """The goals and the step-up SIP that are not on file yet. Returns how many records it wrote."""
    from seed.india_planning_fixes import TIERS

    today = firm_time.today(firm)
    households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    written = 0
    for home, name, kind, target, years, priority, monthly, share in GOALS:
        household = households.get(home)
        if household is None:
            continue
        if (await s.execute(select(Goal.id).where(Goal.firm_id == firm.id, Goal.household_id == household.id, Goal.name == name))).first():
            continue
        said = {"name": name, "kind": kind, "target": target, "target_basis": "today", "target_date": (today + timedelta(days=round(years * 365.25))).isoformat(),
                "tier": TIERS.get((home, name)), "priority": priority, "monthly_contribution": monthly, "funding_share_percent": share}
        try:
            await goal_record.add(s, firm, household.id, said, by=BY)
        except goal_record.GoalError:
            continue
        written += 1

    household = households.get(SIP_HOUSEHOLD)
    goal = None if household is None else (await s.execute(select(Goal).where(
        Goal.firm_id == firm.id, Goal.household_id == household.id, Goal.name == SIP_GOAL))).scalars().first()
    fund = (await s.execute(select(Instrument.id).where(Instrument.firm_id == firm.id, Instrument.symbol == SIP_FUND))).first()
    if goal is not None and fund and not (await s.execute(select(Sip.id).where(Sip.firm_id == firm.id, Sip.goal_id == goal.id))).first():
        found = (await s.execute(select(Account, LegalEntity).join(Mandate, Mandate.id == Account.mandate_id)
                                 .join(LegalEntity, LegalEntity.id == Mandate.entity_id)
                                 .where(Mandate.firm_id == firm.id, LegalEntity.household_id == household.id)
                                 .order_by(Account.created_at))).first()
        if found is not None:
            account, entity = found
            # A year and more ago, so the first step-up has applied; the instalment still falls on its registered day.
            try:
                await sip.record(s, firm, account.id, by=BY, instrument=SIP_FUND, starts_on=today - timedelta(days=400),
                                 ends_on=goal.target_date, evidence_ref=None, simulated=True, goal_id=goal.id, **SIP_TERMS)
                written += 1
            except sip.SipError:
                return written
            if not await sip_mandates.standing(s, firm.id, [account.id]):
                await money_in.record_link(s, firm.id, by=BY, account_id=account.id, bank=BANK, last4=LAST4, owner_name=entity.name,
                                           method="voided_cheque", evidence=f"Cancelled cheque, filed ({SOURCE})",
                                           verified_on=today - timedelta(days=60))
                await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, account_id=account.id, reference=REFERENCE, mandate_type="nach",
                                          per_debit_limit=SIP_TERMS["step_up_cap"], starts_on=today, last4=LAST4,
                                          document=f"NACH registration, filed ({SOURCE})")
                written += 2
    await s.flush()
    return written
