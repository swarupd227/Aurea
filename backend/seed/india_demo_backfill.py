"""Bring the deployed Indian demonstration firm up to what `seed/india_demo.py` now writes.

The firm's goals were sized without contributions, so a family on a high income showed none of its
goals on track, and its tax memos were written before their prompt named the household properly.
This resets the goals from the seed and reruns the tax agent, which refreshes its open proposals
rather than raising more. It touches no other firm. Safe to run twice.

Row 7 (a non-resident household and a minor), row 8 (a Portfolio Manager client's agreement and fee), row 9 (a consolidated
statement) row 10 (a complaints register), row 13 (a family and its estate records) and row 14 (a governance register and counsel's review) and the L100 items (accredited investors, an adviser's fee terms, the deposit, a wait for the Budget) and batch A (remittances, a SIP mandate, the shelf's other classes, a pledge, gifts and dividends) and India money in (B-388: mandate states, first debits, credits by Indian rails, registered banks, a cooling-off, an inward remittance) and batch C (mutual fund order rails, the firm's group and its trail, expense ratios, an adviser's certificates, advertisement approvals) are added once. The simulated Account Aggregator, WhatsApp channel and DigiLocker and eSign provider are not written here: they are
opt-in (`python -m app.simulations seed aa_feed whatsapp_channel india_stack kyc_registry identity_checks`, with AUREA_SIMULATIONS on). Languages, WhatsApp opt-ins and templates (B-301) are added once.
The polish row (B-247 and others: Tara Menon as a minor now an adult, Kabir's case, a PMS fee line, segment minimums in rupees, the reminders as tasks) is added once.
Row 11 (capital losses carried forward, a 2018 value, a regime comparison's inputs) and row 12 (KYC in every state) likewise.
A tier on each seeded goal and each household's essential spending (B-393) are added where missing; a tier on file stays.
The consolidation workbench's folios, classifications and requests (B-389, `seed/india_consolidation.py`) are added once.
Health policies, an endowment and a unit-linked statement, a waiver rider and a stated health cover figure (B-397,
`seed/india_protection.py`) are added once; a renewal date of theirs that is near or past is moved ahead.
Three more goals and a step-up SIP (B-394, seed/india_goals.py) are added where missing. A seeded goal a person has changed
from the conversation is not reset.
The KYC registry's answers, the on-hold client's steps and contact attempts, and a new prospect nobody has looked up (B-386,
`seed/india_b386.py`) are added once.
The identity and bank checks a person did by hand, one passed and one failed in the queue (B-387, `seed/india_b387.py`), are added once.
The PMLA programme's cases, invented screening list entries, monitoring scenarios and recorded FIU-IND reports (B-390,
`seed/india_pmla.py`) are added once.
The retirement stack (B-395, seed/india_retirement.py): statement lines, monthly contributions and a quoted annuity rate, added
where missing; a pension total those lines stand for is ended, so net worth counts it once.
Advice files and one sample audit with a finding (B-399, `seed/india_advice.py`) are added once.
Will witnesses and a will check, a wish, a tax residency certificate and a planned return with its tasks (B-398,
`seed/india_estate_nri.py`) are added once. Run after `seed.india_demo_backfill`'s earlier rows, which record the wills.
The accounts opened through onboarding and a minor near eighteen that the onboarding measures read (B-392, `seed/india_b392.py`) are
added once, and their own dates are moved forward on a later run. Screening and reports are B-390's.

Every run also moves the dated demonstration scenarios back to their distance from today (`seed/india_redate.py`) and rewrites the
description of an open reminder task still in the older wording.

Run: python -m seed.india_demo_backfill
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

import app.models  # noqa: F401
from app.atlas.base import Subject
from app.atlas.runtime import run_agent
from app.core.db import SessionLocal
from app.core.logging import configure_logging
from app.models.enums import AgentKey
from app.models.graph import Goal, Household
from app.models.tenant import Firm
from seed.india_demo import HOUSEHOLDS, INSTRUMENTS, SLUG, _closes_or_fallback, add_instrument


async def reset_goals(s, firm: Firm) -> int:
    """Set each seeded goal's target and assumptions, by household and goal name. A goal a person has changed from the
    conversation (B-394) is theirs now and stays as they left it."""
    from app.models.governance import LedgerEntry

    changed = 0
    changed_by_a_person = {str((e.content or {}).get("goal_id")) for e in (await s.execute(select(LedgerEntry).where(
        LedgerEntry.firm_id == firm.id, LedgerEntry.event_type.in_(("goal_updated", "goal_recorded"))))).scalars().all()
        if not (e.actor or "").startswith("seed:")}
    for spec in HOUSEHOLDS:
        household = (await s.execute(select(Household).where(
            Household.firm_id == firm.id, Household.name == spec["name"]))).scalar_one_or_none()
        if household is None:
            continue
        for name, _kind, target, years, assumptions in spec["goals"]:
            goal = (await s.execute(select(Goal).where(
                Goal.firm_id == firm.id, Goal.household_id == household.id, Goal.name == name))).scalar_one_or_none()
            if goal is None or str(goal.id) in changed_by_a_person:
                continue
            goal.target_amount = target
            # A tier is what a person said (B-393), so one on file is not reset with the rest.
            said = {k: v for k, v in (goal.assumptions or {}).items() if k == "tier"}
            goal.assumptions = {"years": years, **assumptions, **said}
            changed += 1
    return changed


async def add_funds(s, firm: Firm) -> int:
    """Row 4: the mutual funds the seed now carries, added where the deployed firm lacks them. They have no
    price until the sync below fetches the real NAV from AMFI."""
    from app.models.portfolio import Instrument

    have = set((await s.execute(select(Instrument.symbol).where(Instrument.firm_id == firm.id))).scalars().all())
    closes = _closes_or_fallback(None)
    added = 0
    for sym, spec in INSTRUMENTS.items():
        if spec[4].get("amfi_code") and sym not in have:
            await add_instrument(s, firm, sym, closes[sym])
            added += 1
    return added


async def main() -> None:
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none()
        if firm is None:
            print("\nNo Indian demonstration firm here. Run python -m seed.india_demo first.\n")
            return
        changed = await reset_goals(s, firm)
        funds = await add_funds(s, firm)
        from seed import india_row5

        row5 = await india_row5.add(s, firm)
        from seed import india_row6

        row6 = await india_row6.add(s, firm)
        row6 += await india_row6.correct_names(s, firm)
        from seed import india_row7, india_row8

        row7 = await india_row7.add(s, firm)
        row8 = await india_row8.add(s, firm)
        from seed import india_row9, india_row10

        row9 = await india_row9.add(s, firm)
        row10 = await india_row10.add(s, firm)
        from seed import india_row11, india_row12

        row11 = await india_row11.add(s, firm)
        row12 = await india_row12.add(s, firm)
        from seed import india_row13, india_row14

        row13 = await india_row13.add(s, firm)
        row14 = await india_row14.add(s, firm)
        from seed import india_polish

        polish = await india_polish.add(s, firm)
        from seed import india_l100a

        l100a = await india_l100a.add(s, firm)
        from seed import india_batch_b

        batch_b = await india_batch_b.add(s, firm)
        from seed import india_batch_a

        batch_a = await india_batch_a.add(s, firm)
        from seed import india_batch_c

        batch_c = await india_batch_c.add(s, firm)
        from seed import india_rem_e

        rem_e = await india_rem_e.add(s, firm)

        from seed import india_rem_d

        rem_d = await india_rem_d.add(s, firm)
        from seed import india_planning_fixes

        planning_records = await india_planning_fixes.add(s, firm)
        from seed import india_consolidation

        consolidation_rows = await india_consolidation.add(s, firm)
        from seed import india_b388

        money = await india_b388.add(s, firm)
        from seed import india_protection

        protection_rows = await india_protection.add(s, firm)
        from seed import india_goals

        goal_rows = await india_goals.add(s, firm)
        from seed import india_b386

        registry_rows = await india_b386.add(s, firm)
        from seed import india_b387

        check_rows = await india_b387.add(s, firm)
        from seed import india_pmla

        pmla_rows = await india_pmla.add(s, firm)
        from seed import india_retirement

        stack_rows = await india_retirement.add(s, firm)
        from seed import india_advice

        advice_rows = await india_advice.add(s, firm)
        from seed import india_estate_nri

        estate_rows = await india_estate_nri.add(s, firm)
        from seed import india_b392

        measure_rows = await india_b392.add(s, firm)
        # Last, so what the steps above added is read too: the dated scenarios back to their distance from today (B-374), and the
        # open reminder tasks raised in the older wording (B-372).
        from app.aurea_core import india_reminders
        from seed import india_redate

        redated = await india_redate.refresh(s, firm)
        reworded = await india_reminders.correct_open_details(s, firm.id)
        await s.commit()
        from app.conduit.service import sync_market_data
        priced = await sync_market_data(s, firm.id)
        await s.commit()
        await run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                        subject=Subject("firm", firm.id, firm.name), require_human=True)
        await s.commit()
    print(f"\nReset {changed} goal(s), added {funds} fund(s), {row5} row 5, {row6} row 6, {row7} row 7, {row8} row 8, {row9} row 9, {row10} row 10, {row11} row 11, {row12} row 12, {row13} row 13, {row14} row 14, {polish} polish, {l100a} L100, {batch_a} batch A, {batch_c} batch C, {rem_e} batch E, {rem_d} remainder(s), {planning_records} goal tier(s) and spending record(s), {money} money-in record(s), {goal_rows} goal(s) and step-up SIP and {batch_b} language and WhatsApp record(s), priced {priced['resolved']} instrument(s) "
          f"({len(priced['failed'])} failed), and refreshed the tax positions.\n"
          f"Re-dated for today: {', '.join(f'{n} {k}' for k, n in redated.items() if n) or 'nothing'}. "
          f"Rewrote {reworded} open reminder task description(s). Consolidation workbench: {consolidation_rows} holding(s) added or re-dated. Health cover and policy values: {protection_rows} added or re-dated. "
          f"KYC registry: {registry_rows} answer(s), step(s) and contact attempt(s) added. Identity and bank checks done by hand: {check_rows} added. "
          f"PMLA programme: {pmla_rows} row(s) added. Retirement stack: {stack_rows} record(s) added. "
          f"Advice files and audit: {advice_rows} added. Estate and NRI records: {estate_rows} added. "
          f"Onboarding measures: {measure_rows} record(s) added or re-dated.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
