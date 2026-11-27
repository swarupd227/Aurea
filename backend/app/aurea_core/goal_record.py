"""A goal as a person said it, recorded and changed from the conversation (B-394).

An adviser or a paraplanner records a goal (retirement with an age, education, a wedding, a home, a legacy or another)
with its target, whether that is in today's money or a fixed sum, its date, its tier, what is saved for it each month
and what share of the managed book is set aside for it. Each is as said: a goal needs a target, a price basis and a
date (or, for retirement, an age that gives one from a date of birth on file), and everything else left unsaid stays
unrecorded. A tier left unsaid is "not tiered", never a need.

Written where every engine already reads it: the goal's own columns and its `assumptions`. A monthly contribution is
kept as said, with the yearly figure the engines read. Shares of one household's book cannot be recorded past the
whole. Nothing here changes a mandate, an investment or a SIP.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets, planning
from app.models.graph import Goal, Household, Person
from app.models.tenant import Firm
from app.provenance import ledger

# As said -> as stored. "home" is the "property" kind other screens already name.
KINDS = {"retirement": "retirement", "education": "education", "wedding": "wedding", "marriage": "wedding", "home": "property",
         "house": "property", "property": "property", "legacy": "legacy", "other": "other"}
KIND_LABELS = {"retirement": "Retirement", "education": "Education", "wedding": "Wedding", "property": "Home", "legacy": "Legacy",
               "other": "Other"}
TIER_WORDS = {"needs": "Needs", "wants": "Wants", "wishes": "Wishes", None: "Not tiered"}
PRIORITY = {"high": 1, "medium": 2, "low": 3}
RANK_WORDS = {1: "High", 2: "Medium", 3: "Low"}
INDEX_WORDS = {"cpi": "CPI", "education": "Education inflation", "healthcare": "Healthcare inflation"}
NAME_MAX = 200
AGES = (30, 85)
# The terms a change may touch beyond what `update_goal` first changed (target, year, priority, basis, index, tier).
MORE_TERMS = ("name", "target_date", "retirement_age", "retirement_spending", "monthly_contribution", "funding_share_percent",
              "plan_to_age")
PLAN_TO = (60, 115)             # the ages a plan may be said to run to


class GoalError(ValueError):
    """A goal that cannot be recorded as asked. Carries a reason fit to show the user."""


def _said(v) -> bool:
    return v not in (None, "")


def _amount(v, label: str, *, zero_ok: bool = False) -> float:
    try:
        n = round(float(str(v).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise GoalError(f"{label} must be an amount, in figures.") from None
    if n < 0 or (n == 0 and not zero_ok):
        raise GoalError(f"{label} must be {'nothing or more' if zero_ok else 'more than nothing'}.")
    return n


def _day(v, today: date) -> date:
    text = str(v).strip()
    try:
        on = date(int(text), 12, 31) if text.isdigit() and len(text) == 4 else date.fromisoformat(text[:10])
    except ValueError:
        raise GoalError(f"'{text}' is not a date: give YYYY-MM-DD, or the year.") from None
    if on <= today:
        raise GoalError("A goal's date must be after today.")
    return on


def _age(v) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise GoalError("The retirement age must be a whole number.") from None
    if not AGES[0] <= n <= AGES[1]:
        raise GoalError(f"The retirement age must be between {AGES[0]} and {AGES[1]}.")
    return n


def _plan_to(v, retirement_age) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        raise GoalError("The plan-to age must be a whole number.") from None
    if not PLAN_TO[0] <= n <= PLAN_TO[1]:
        raise GoalError(f"The plan-to age must be between {PLAN_TO[0]} and {PLAN_TO[1]}.")
    if retirement_age is not None and n <= int(retirement_age):
        raise GoalError(f"The plan-to age must be after the retirement age ({int(retirement_age)}).")
    return n


def _tier(v) -> str | None:
    said = str(v).strip().lower()
    said = {"need": "needs", "want": "wants", "wish": "wishes"}.get(said, said)
    if said in ("none", "not tiered", "clear"):
        return None
    if said not in ("needs", "wants", "wishes"):
        raise GoalError("tier must be needs, wants or wishes (or none to clear it).")
    return said


def _share(v) -> float | None:
    if str(v).strip().lower() in ("none", "clear"):
        return None
    try:
        pct = float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        raise GoalError("The funding share is a percentage of the managed book, 0 to 100.") from None
    if not 0 <= pct <= 100:
        raise GoalError("The funding share is a percentage of the managed book, 0 to 100.")
    return round(pct / 100.0, 4)


async def _goals_of(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> list[Goal]:
    return list((await session.execute(select(Goal).where(Goal.firm_id == firm.id, Goal.household_id == household_id))).scalars().all())


async def _date_at_age(session: AsyncSession, firm: Firm, household_id: uuid.UUID, age: int) -> tuple[date, str]:
    """The day the eldest adult on file reaches `age`: the ages the retirement plan counts in."""
    people = (await session.execute(select(Person).where(
        Person.firm_id == firm.id, Person.household_id == household_id, Person.deceased_on.is_(None),
        Person.is_next_gen.is_(False), Person.date_of_birth.isnot(None)).order_by(Person.date_of_birth))).scalars().all()
    if not people:
        raise GoalError("No adult of this household has a date of birth on file, so the age gives no date: give the date too.")
    born, who = people[0].date_of_birth, people[0].full_name
    try:
        on = born.replace(year=born.year + age)
    except ValueError:                     # born 29 February
        on = date(born.year + age, 3, 1)
    if on <= firm_time.today(firm):
        raise GoalError(f"{who} has already reached {age}: give the date the goal is for.")
    return on, who


def _share_check(others: list[Goal], share: float | None, money_word: str = "managed book") -> float:
    claimed = sum(float((g.assumptions or {}).get("funding_share") or 0) for g in others)
    total = claimed + (share or 0.0)
    if share and total > 1.0 + 1e-9:
        raise GoalError(f"The household's other goals already hold {claimed:.0%} of the {money_word}, so {share:.0%} would make "
                        f"{total:.0%}. Change the other shares first, or give at most {max(1 - claimed, 0):.0%}.")
    return total


# ── A new goal ────────────────────────────────────────────────────────────────

async def prepare_add(session: AsyncSession, firm: Firm, household_id: uuid.UUID, inputs: dict) -> dict:
    """Everything a new goal would be, checked, with nothing written: the Confirm card and the write read the same."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise GoalError("No such household at this firm.")
    today = firm_time.today(firm)
    say = markets.money_for(firm)

    kind = KINDS.get(str(inputs.get("kind") or "").strip().lower())
    if kind is None:
        raise GoalError("Say what kind of goal it is: retirement, education, wedding, home, legacy or other.")
    if not _said(inputs.get("target")):
        raise GoalError("Give the goal's target amount. None is worked out or supplied.")
    target = _amount(inputs["target"], "The target")
    basis = str(inputs.get("target_basis") or "").strip().lower()
    if basis not in ("today", "nominal"):
        raise GoalError("Say whether the target is in today's money (target_basis today, grown to the goal's date) or a fixed sum "
                        "(nominal).")
    index = str(inputs.get("index") or "").strip().lower() or None
    if index and index not in planning.INFLATION_INDEXES:
        raise GoalError(f"index must be one of {', '.join(planning.INFLATION_INDEXES)}.")
    if index and basis != "today":
        raise GoalError("An index applies only to a target in today's money.")
    if basis == "today" and index is None:
        index = "education" if kind == "education" else "cpi"

    existing = await _goals_of(session, firm, household_id)
    age = _age(inputs["retirement_age"]) if _said(inputs.get("retirement_age")) else None
    spending = _amount(inputs["retirement_spending"], "Retirement spending a year") if _said(inputs.get("retirement_spending")) else None
    if kind != "retirement" and (age is not None or spending is not None):
        raise GoalError("A retirement age and retirement spending belong to a retirement goal.")
    if kind == "retirement":
        taken = next((g for g in existing if g.kind == "retirement"), None)
        if taken is not None:
            raise GoalError(f"A retirement goal is already on file ({taken.name}). Change it with update_goal.")
    when = inputs.get("target_date")
    worked_from = None
    if _said(when):
        on = _day(when, today)
    elif age is not None:
        on, worked_from = await _date_at_age(session, firm, household_id, age)
    else:
        raise GoalError("Give the goal's date (or its year)" + (", or the retirement age." if kind == "retirement" else "."))

    name = str(inputs.get("name") or "").strip()
    if not name:
        if kind == "other":
            raise GoalError("Give the goal a name.")
        name = f"Retirement at {age}" if kind == "retirement" and age else KIND_LABELS[kind]
    if len(name) > NAME_MAX:
        raise GoalError(f"A goal's name is at most {NAME_MAX} characters.")
    if any(g.name.strip().lower() == name.lower() for g in existing):
        raise GoalError(f"A goal named {name} is already on file for this household. Change it with update_goal, or name this one apart.")

    tier = _tier(inputs["tier"]) if _said(inputs.get("tier")) else None
    priority = None
    if _said(inputs.get("priority")):
        priority = PRIORITY.get(str(inputs["priority"]).strip().lower())
        if priority is None:
            raise GoalError("priority must be high, medium or low.")
    monthly = _amount(inputs["monthly_contribution"], "The monthly contribution", zero_ok=True) \
        if _said(inputs.get("monthly_contribution")) else None
    share = _share(inputs["funding_share_percent"]) if _said(inputs.get("funding_share_percent")) else None
    shares_total = _share_check(existing, share)

    terms: dict = {"target_basis": basis}
    for key, value in (("index", index), ("tier", tier), ("funding_share", share), ("retirement_age", age), ("annual_income", spending)):
        if value is not None:
            terms[key] = value
    if monthly is not None:
        terms["monthly_contribution"], terms["annual_contribution"] = monthly, round(monthly * 12, 2)

    rows = [
        ("Client", household.name), ("Goal", name), ("Kind", KIND_LABELS[kind]),
        ("Target", f"{say(target)} " + ("in today's money" if basis == "today" else "as a fixed sum")),
        ("Grows with", INDEX_WORDS[index] if index else "Nothing"),
        ("Date", firm_time.spoken(on) + (f" (when {worked_from} turns {age})" if worked_from else "")),
        ("Retirement age", age),
        ("Retirement spending", f"{say(spending)} a year, today's money" if spending is not None else None),
        ("Tier", TIER_WORDS[tier]),
        ("Priority", RANK_WORDS[priority] if priority else "Medium (none stated)"),
        ("Saving each month", say(monthly) if monthly is not None else "Not stated"),
        ("Funding share", f"{share:.0%} of the managed book ({shares_total:.0%} across the household's goals)" if share is not None
         else "None recorded: nothing already held is counted for it"),
    ]
    return {"household": household, "name": name, "kind": kind, "target": target, "date": on, "terms": terms,
            "priority": priority or PRIORITY["medium"], "rows": rows}


async def add(session: AsyncSession, firm: Firm, household_id: uuid.UUID, inputs: dict, *, by: str) -> Goal:
    ready = await prepare_add(session, firm, household_id, inputs)
    row = Goal(firm_id=firm.id, household_id=household_id, name=ready["name"], kind=ready["kind"], target_amount=ready["target"],
               target_date=ready["date"], priority=ready["priority"], assumptions=ready["terms"])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="goal_recorded", actor=by, content={
        "goal_id": str(row.id), "household_id": str(household_id), "name": row.name, "kind": row.kind, "target": ready["target"],
        "target_date": ready["date"].isoformat(), "priority": ready["priority"], "terms": ready["terms"]})
    return row


# ── More of what a change to a goal may say ───────────────────────────────────

async def more_changes(session: AsyncSession, firm: Firm, goal: Goal, inputs: dict) -> dict:
    """The changes to a goal beyond target, year, priority, basis, index and tier: its name, full date, retirement age and
    spending, monthly contribution and funding share. Returns the column changes, the `assumptions` changes (None
    removes the key) and the card's rows. Empty where nothing is said or what is said is already on file."""
    columns: dict = {}
    terms: dict = {}
    rows: list[tuple[str, object]] = []
    if not any(_said(inputs.get(k)) for k in MORE_TERMS):
        return {"columns": columns, "terms": terms, "rows": rows}
    a = goal.assumptions or {}
    today = firm_time.today(firm)
    say = markets.money_for(firm)
    others = [g for g in await _goals_of(session, firm, goal.household_id) if g.id != goal.id]

    if _said(inputs.get("name")):
        name = str(inputs["name"]).strip()
        if not name or len(name) > NAME_MAX:
            raise GoalError(f"A goal's name is 1 to {NAME_MAX} characters.")
        if any(g.name.strip().lower() == name.lower() for g in others):
            raise GoalError(f"Another goal of this household is already named {name}.")
        if name != goal.name:
            columns["name"] = name
            rows.append(("Name", f"{goal.name} becomes {name}"))

    on = None
    if _said(inputs.get("target_date")):
        if _said(inputs.get("timeline")):
            raise GoalError("Give the goal's date once: target_date or timeline.")
        on = _day(inputs["target_date"], today)
    age = None
    if _said(inputs.get("retirement_age")):
        if goal.kind != "retirement":
            raise GoalError("A retirement age belongs to a retirement goal.")
        age = _age(inputs["retirement_age"])
        if age == a.get("retirement_age"):
            age = None
        else:
            terms["retirement_age"] = age
            rows.append(("Retirement age", f"{a.get('retirement_age') or 'Not on file'} becomes {age}"))
            if on is None and not _said(inputs.get("timeline")):
                on, who = await _date_at_age(session, firm, goal.household_id, age)
    if on is not None and on != goal.target_date:
        columns["target_date"] = on
        rows.append(("Date", f"{firm_time.spoken(goal.target_date) if goal.target_date else 'None'} becomes {firm_time.spoken(on)}"
                     + (f" (from the age and {who}'s date of birth)" if age is not None and not _said(inputs.get("target_date")) else "")))

    if _said(inputs.get("retirement_spending")):
        if goal.kind != "retirement":
            raise GoalError("Retirement spending belongs to a retirement goal.")
        spend = _amount(inputs["retirement_spending"], "Retirement spending a year")
        if spend != a.get("annual_income"):
            terms["annual_income"] = spend
            before = say(float(a["annual_income"])) if a.get("annual_income") is not None else "Not on file"
            rows.append(("Retirement spending", f"{before} becomes {say(spend)} a year, today's money"))

    # The age the plan runs to (B-400): a plan of record is not taken until one is on file, and nothing else recorded it.
    if _said(inputs.get("plan_to_age")):
        if goal.kind != "retirement":
            raise GoalError("A plan-to age belongs to a retirement goal.")
        until = _plan_to(inputs["plan_to_age"], terms.get("retirement_age") or a.get("retirement_age"))
        if until != a.get("longevity_age"):
            terms["longevity_age"] = until
            rows.append(("Plan to age", f"{a.get('longevity_age') or 'Not on file'} becomes {until}"))

    if _said(inputs.get("monthly_contribution")):
        monthly = _amount(inputs["monthly_contribution"], "The monthly contribution", zero_ok=True)
        yearly = a.get("annual_contribution")
        now = a.get("monthly_contribution", round(float(yearly) / 12, 2) if yearly is not None else None)
        if now is None or monthly != float(now) or yearly is None:
            terms["monthly_contribution"], terms["annual_contribution"] = monthly, round(monthly * 12, 2)
            rows.append(("Saving each month", f"{say(float(now)) if now is not None else 'Not on file'} becomes {say(monthly)}"))
            from app.models.sip import Sip

            linked = (await session.execute(select(Sip.id).where(Sip.firm_id == firm.id, Sip.goal_id == goal.id,
                                                                 Sip.ended_on.is_(None)))).scalars().all()
            if linked:
                rows.append(("Counted", "The SIP registrations recorded against this goal are counted in its projections, "
                                        "in place of this figure"))

    if _said(inputs.get("funding_share_percent")):
        share = _share(inputs["funding_share_percent"])
        before = a.get("funding_share")
        if share != before:
            total = _share_check(others, share)
            terms["funding_share"] = share
            rows.append(("Funding share", f"{'None' if before is None else f'{float(before):.0%}'} becomes "
                                          f"{'none' if share is None else f'{share:.0%}'} of the managed book"
                                          + (f" ({total:.0%} across the household's goals)" if share is not None else "")))
    return {"columns": columns, "terms": terms, "rows": rows}
