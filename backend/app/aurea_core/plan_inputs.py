"""How old a plan's inputs are, and what the plan reads that is not on file (B-182, L200-2 §2.1, §10).

Every planning fact a person records carries the day it is dated: a balance item the day its figure was true, an
income stream and a policy the day they were recorded, a profile tax field the day beside it in `tax.as_of`. A fact
with no date (a figure carried in before dates were kept) is counted as undated, never given one.

The plan shows its oldest input and how many are older than the firm's line: `firm.settings["plan_input_days"]`,
else 90 days, named as assumed. Completeness is measured against what the retirement projection and the plan read:
each adult's date of birth and income, the retirement goal and its retirement age, plan-to age and funding share, the
income to plan for, and an education goal's cost, start and length. A missing fact is listed, never filled: where the
projection stands one in, its own `assumed` already says so.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, firm_time
from app.models.financial_plan import IncomeStream
from app.models.graph import Goal, Person
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm

DEFAULT_LINE_DAYS = 90
# The profile's tax fields the plan reads: the income (goals' earnings, protection) and the retirement pots
# (net worth, the plan's assets) that a recorded pension does not replace.
TAX_FIELDS = ("annual_income",) + tuple(balance_sheet.RETIREMENT_ACCOUNTS)


def line_of(firm: Firm | None) -> dict:
    """The firm's line for an old input, in days, and whether it is the default standing in."""
    raw = ((firm.settings if firm else None) or {}).get("plan_input_days")
    try:
        days = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    if days is None or days <= 0:
        return {"days": DEFAULT_LINE_DAYS, "assumed": True}
    return {"days": days, "assumed": False}


def _day(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def tax_dates(person: Person) -> list[tuple[str, date | None]]:
    """Each tax field on a person's profile the plan reads, with its date: `tax.as_of[field]`, else, for an income
    recorded with its source, the day that was recorded; else none."""
    tax = (person.profile or {}).get("tax") or {}
    dated = tax.get("as_of") if isinstance(tax.get("as_of"), dict) else {}
    out = []
    for field in TAX_FIELDS:
        if tax.get(field) is None:
            continue
        day = _day(dated.get(field))
        if day is None and field == "annual_income":
            day = _day((tax.get("income_source") or {}).get("recorded_at"))
        out.append((field, day))
    return out


async def facts_of(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[dict]:
    """Every dated planning fact on file for the household that stands today: its kind, what it is, whose, and its day."""
    persons = (await session.execute(select(Person).where(Person.household_id == household_id, Person.firm_id == firm_id,
                                                          Person.deceased_on.is_(None)))).scalars().all()
    names = {p.id: p.full_name for p in persons}
    rows: list[dict] = []
    for i in await balance_sheet.current_items(session, household_id, firm_id=firm_id):
        rows.append({"kind": "balance_item", "name": i.name, "person": names.get(i.person_id), "as_of": i.as_of})
    streams = (await session.execute(select(IncomeStream).where(IncomeStream.household_id == household_id, IncomeStream.firm_id == firm_id,
                                                                IncomeStream.ended_on.is_(None)))).scalars().all()
    for s in streams:
        if s.person_id in names:
            rows.append({"kind": "income_stream", "name": s.name, "person": names[s.person_id], "as_of": s.as_of})
    policies = (await session.execute(select(ProtectionPolicy).where(ProtectionPolicy.household_id == household_id, ProtectionPolicy.firm_id == firm_id,
                                                                     ProtectionPolicy.status == "in_force"))).scalars().all()
    for p in policies:
        if p.person_id in names:
            rows.append({"kind": "policy", "name": f"{p.provider} {p.kind.replace('_', ' ')}", "person": names[p.person_id],
                         "as_of": p.as_of or _day(p.created_at)})
    for person in persons:
        for field, day in tax_dates(person):
            rows.append({"kind": "tax_field", "name": field, "person": person.full_name, "as_of": day})
    return rows


def _adults(persons: list[Person]) -> list[Person]:
    return [p for p in persons if not p.is_next_gen]


async def missing_of(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> tuple[int, list[dict]]:
    """How many facts the plan reads, and those not on file, each named with whose or which goal it is."""
    persons = (await session.execute(select(Person).where(Person.household_id == household_id, Person.firm_id == firm_id,
                                                          Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    goals = (await session.execute(select(Goal).where(Goal.household_id == household_id, Goal.firm_id == firm_id))).scalars().all()
    items = await balance_sheet.current_items(session, household_id, firm_id=firm_id)
    checks: list[tuple[dict, bool]] = []
    for p in _adults(persons):
        checks.append(({"fact": "date_of_birth", "person": p.full_name}, p.date_of_birth is not None))
        checks.append(({"fact": "annual_income", "person": p.full_name}, balance_sheet.income_of(p) is not None))
    rgoal = next((g for g in goals if g.kind == "retirement"), None)
    a = (rgoal.assumptions if rgoal else None) or {}
    checks.append(({"fact": "retirement_goal"}, rgoal is not None))
    checks.append(({"fact": "retirement_age"}, a.get("retirement_age") is not None or (rgoal is not None and (rgoal.target_date is not None or a.get("years") is not None))))
    checks.append(({"fact": "plan_to_age"}, a.get("longevity_age") is not None))
    checks.append(({"fact": "funding_share"}, a.get("funding_share") is not None))
    checks.append(({"fact": "income_need"}, a.get("annual_income") is not None or any(i.side == "expense" for i in items)))
    for g in goals:
        if g.kind == "education":
            ga = g.assumptions or {}
            checks.append(({"fact": "education_cost", "goal": g.name}, bool(ga.get("annual_cost") and ga.get("start_year") and ga.get("years"))))
    return len(checks), [fact for fact, on_file in checks if not on_file]


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    """The plan's inputs: the oldest, how many are past the firm's line, the undated, and completeness."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    line = line_of(firm)
    facts = await facts_of(session, household_id, firm_id=firm_id)
    dated = sorted((f for f in facts if f["as_of"] is not None), key=lambda f: f["as_of"])
    older = [f for f in dated if (today - f["as_of"]).days > line["days"]]
    read, missing = await missing_of(session, household_id, firm_id=firm_id)

    def view(f: dict) -> dict:
        return {"kind": f["kind"], "name": f["name"], "person": f["person"], "as_of": f["as_of"].isoformat() if f["as_of"] else None,
                "days": (today - f["as_of"]).days if f["as_of"] else None}

    oldest = view(dated[0]) if dated else None
    return {
        "as_of": today.isoformat(), "line_days": line["days"], "line_assumed": line["assumed"],
        "inputs": len(facts), "dated": len(dated), "undated": len(facts) - len(dated),
        "oldest": oldest, "older_than_line": len(older),
        "older": [view(f) for f in older], "undated_inputs": [view(f) for f in facts if f["as_of"] is None],
        "completeness": {"read": read, "on_file": read - len(missing), "share": round((read - len(missing)) / read, 3) if read else None},
        "missing": missing,
    }
