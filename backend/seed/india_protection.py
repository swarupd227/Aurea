"""India, health cover and the policies a household brings (B-397): health policies for the demonstration families, an
old endowment and a unit-linked policy with their insurers' statements, a waiver-of-premium rider, and a health cover
figure the demonstration firm states.

Every record is invented for the demonstration firm and says so in its source. Each goes in through the module a person's
entry goes through, so it is refused for the same reasons. The families stand for different answers: the Sharmas' own
cover is only an employer's (a parent has a policy of her own, with caps, a co-payment and a waiting period) and Meera
Sharma brings an old endowment and a unit-linked policy; the Iyers hold a floater and a super top-up; Nandini Kapoor a
top-up with nothing under it; the Banerjees and D'Souzas have no health cover on file. The D'Souzas are given nothing,
because they stand for a household holding nothing outside SEBI's purview, and the Banerjees nothing, because their one
missing paper before advice is their consent to the risk profile. Run by the seed when it builds the firm and by `seed.india_demo_backfill` for a firm already deployed: it
adds once, and moves a renewal date of its own that is near or past ahead again.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, health_cover, policy_values, protection
from app.models.graph import Household, Person
from app.models.health_cover import HealthPolicy
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India protection (demonstration)"
RENEWAL_MOVE_WITHIN = 7
FIGURE = {"family_cover": 2_500_000, "parent_cover": 1_000_000,
          "source_document": "Demonstration planning note on health cover, invented for the demonstration firm"}

# (household, key, days from today to renewal, terms). The key is the policy number, which says it is invented.
POLICIES = [
    ("The Sharma Family", "DEMO-GRP-0417", 120, dict(
        kind="employer_group", provider="Employer group health (demonstration)", sum_insured=500_000,
        members="Rajesh Sharma: self; Meera Sharma: spouse; Aarav Sharma: son", room_rent_cap="1% of the sum insured a day")),
    ("The Sharma Family", "DEMO-SNR-0192", 38, dict(
        kind="individual", provider="Senior health insurer (demonstration)", sum_insured=500_000, members="Kamla Sharma: mother",
        room_rent_cap="Single private room", co_payment_percent=20, co_payment_terms="on every claim",
        sub_limits="Cataract: Rs 40,000 an eye; Knee replacement: Rs 2,00,000 a knee",
        waiting_periods="Pre-existing diseases: 36 months", annual_premium=42_000, covered_days_ago=420)),
    ("The Iyer Family", "DEMO-FLT-2210", 200, dict(
        kind="family_floater", provider="Health insurer (demonstration)", sum_insured=2_500_000,
        members="Karthik Iyer: self; Lakshmi Iyer: spouse", annual_premium=38_000, covered_days_ago=2000,
        waiting_periods="Pre-existing diseases: 36 months")),
    ("The Iyer Family", "DEMO-STU-2211", 200, dict(
        kind="super_top_up", provider="Health insurer (demonstration)", sum_insured=7_500_000, deductible=2_500_000,
        members="Karthik Iyer: self; Lakshmi Iyer: spouse", annual_premium=11_000, covered_days_ago=700)),
    ("The Kapoor Family Trust", "DEMO-STU-5530", 150, dict(
        kind="super_top_up", provider="Health insurer (demonstration)", sum_insured=5_000_000, deductible=1_000_000,
        members="Nandini Kapoor: self", annual_premium=14_000)),
]


async def _health(s: AsyncSession, firm: Firm, households: dict, today) -> int:
    added = 0
    have = {p.policy_number: p for p in (await s.execute(select(HealthPolicy).where(
        HealthPolicy.firm_id == firm.id, HealthPolicy.recorded_by == BY))).scalars().all()}
    for home, number, renews_in, terms in POLICIES:
        row = have.get(number)
        if row is not None:
            if row.status == "in_force" and row.renewal_on and row.renewal_on < today + timedelta(days=RENEWAL_MOVE_WITHIN):
                row.renewal_on = today + timedelta(days=renews_in)
                added += 1
            continue
        household = households.get(home)
        if household is None:
            continue
        terms = dict(terms)
        since = terms.pop("covered_days_ago", None)
        names = {part.split(":")[0].strip() for part in terms["members"].split(";") if part.split(":")[1].strip() not in ("mother", "father")}
        on_record = set((await s.execute(select(Person.full_name).where(
            Person.firm_id == firm.id, Person.household_id == household.id, Person.deceased_on.is_(None)))).scalars().all())
        if not names <= on_record:
            continue
        await health_cover.record_policy(
            s, firm.id, recorded_by=BY, role="operations", household_id=household.id, policy_number=number,
            source_document=f"{SOURCE} (schedule {number})", renewal_on=today + timedelta(days=renews_in),
            covered_since=today - timedelta(days=since) if since else None, **terms)
        added += 1
    return added


async def _statements(s: AsyncSession, firm: Firm, today) -> int:
    """An old endowment and a unit-linked policy for Meera Sharma, once each."""
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    stated = today.replace(day=1) - timedelta(days=1)
    rows = [
        ("Meera Sharma", "DEMO-END-7731", dict(
            product="endowment", provider="Life insurer, endowment (demonstration)", sum_assured=500_000, premiums_paid=288_000,
            annual_premium=24_000, premium_years_left=8, surrender_value=205_000, paid_up_value=300_000,
            maturity_on=stated + timedelta(days=8 * 365 + 2), maturity_value=530_000, maturity_basis="guaranteed",
            commenced_on=stated - timedelta(days=12 * 365 + 3))),
        ("Meera Sharma", "DEMO-ULIP-2290", dict(
            product="unit_linked", provider="Life insurer, unit-linked (demonstration)", sum_assured=1_000_000, premiums_paid=400_000,
            annual_premium=100_000, premium_years_left=6, surrender_value=409_000, fund_value=415_000,
            maturity_on=stated + timedelta(days=6 * 365 + 1), commenced_on=stated - timedelta(days=3 * 365 + 200),
            charges_stated="Fund management 1.35% a year; mortality and policy administration charges monthly")),
    ]
    added = 0
    for name, number, terms in rows:
        person = people.get(name)
        if person is None:
            continue
        there = (await s.execute(select(ProtectionPolicy.id).where(
            ProtectionPolicy.firm_id == firm.id, ProtectionPolicy.person_id == person.id,
            ProtectionPolicy.policy_number == number))).first()
        if there:
            continue
        await policy_values.record_statement(
            s, firm.id, recorded_by=BY, role="operations", person_id=person.id, policy_number=number, statement_date=stated,
            source_document=f"{SOURCE} (statement {number})", **terms)
        added += 1
    return added


async def _riders(s: AsyncSession, firm: Firm) -> int:
    """A waiver-of-premium rider on Karthik Iyer's term policy, as its schedule would state it."""
    karthik = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == "Karthik Iyer"))).scalar_one_or_none()
    if karthik is None:
        return 0
    policies = (await s.execute(select(ProtectionPolicy).where(
        ProtectionPolicy.firm_id == firm.id, ProtectionPolicy.person_id == karthik.id))).scalars().all()
    if any(p.kind == "waiver_of_premium" for p in policies):
        return 0
    base = next((p for p in policies if p.kind == "life" and p.status == "in_force" and p.rider_of is None), None)
    if base is None:
        return 0
    await protection.record_policy(
        s, firm.id, recorded_by=BY, person_id=karthik.id, kind="waiver_of_premium", provider=base.provider, rider_of=base.id,
        source_document=f"{SOURCE} (rider schedule)", expires_on=base.expires_on,
        terms_stated="Later premiums waived on total and permanent disability or a listed critical illness, after a 90-day waiting period")
    return 1


async def add(s: AsyncSession, firm: Firm) -> int:
    """What is not on file yet, and renewal dates moved ahead. Returns how many records it wrote or moved."""
    if not health_cover.applies(firm):
        return 0
    households = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    today = firm_time.today(firm)
    written = await _health(s, firm, households, today)
    written += await _statements(s, firm, today)
    written += await _riders(s, firm)
    if households.get("The Sharma Family") is not None and health_cover.cover_figure(firm) is None:
        await health_cover.set_figure(s, firm.id, role="branch_leader", set_by=BY, **FIGURE)
        written += 1
    await s.flush()
    return written
