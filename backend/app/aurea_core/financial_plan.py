"""The financial plan: will the money last, is there enough cash, what do the debts cost, and can the school fees
be paid (L100 §5.4, L200-2 §3).

It works from what is on file. An income stream is recorded from a statement, and a cost of education from a named
source. With no spending on file the retirement figures are the engine's own, and with nothing recorded they do not
change. Spending on file becomes the income to plan for, the debts that outlast retirement add to it, and income
that has started by then reduces what the portfolio must draw.

Social Security's claiming arithmetic is the law's, so a claiming table is worked out for a US household and for
nobody else. A pension's choices are compared on what they imply, and the decision is the client's, because both
are one-time and largely irreversible. Nothing here advises: it lays the numbers side by side, and the assumptions it
needs are named with them. A reserve target and education inflation are firm settings with stated defaults.

Everything here is an estimate. Tax on income, a surviving spouse's cut in income, long-term care and a guardrail
spending rule are not modelled.
"""
from __future__ import annotations

import math
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, firm_time, markets, retirement
from app.aurea_core.graph import household_brain
from app.aurea_core.planning import _blended_assumptions
from app.models.financial_plan import STREAM_KINDS, IncomeStream
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.portfolio import Holding
from app.models.tenant import Firm
from app.provenance import ledger

DEFAULT_RESERVE_MONTHS = 6
DEFAULT_EDUCATION_INFLATION = 0.05
RULE_OF_THUMB = 0.04
PROJECTION_YEARS = 10
LIFE_TO = 95
NOT_MODELLED = ["tax_on_income", "survivor_income_drop", "long_term_care", "guardrail_spending"]


class PlanError(ValueError):
    """This cannot be recorded, with the reason."""


def full_retirement_months(birth_year: int) -> int:
    """US full retirement age, in months."""
    if birth_year <= 1954:
        return 66 * 12
    if birth_year >= 1960:
        return 67 * 12
    return 66 * 12 + (birth_year - 1954) * 2


def claim_factor(full_months: int, claim_months: int) -> float:
    """What claiming at this age pays against full retirement age: 5/9% a month for the first 36 months early,
    5/12% after that, and 2/3% a month delayed, to 70."""
    claim_months = min(claim_months, 70 * 12)
    early = full_months - claim_months
    if early > 0:
        return 1 - min(early, 36) * 5 / 900 - max(early - 36, 0) * 5 / 1200
    return 1 + (claim_months - full_months) * 2 / 300


def claiming_table(benefit_at_full: float, birth_year: int) -> dict:
    """Claiming at 62, at full retirement age and at 70, and the age at which waiting has paid for itself."""
    full = full_retirement_months(birth_year)
    rows = []
    for months, label in ((62 * 12, "62"), (full, f"{full // 12}" + (f" and {full % 12} months" if full % 12 else "")), (70 * 12, "70")):
        factor = claim_factor(full, months)
        rows.append({"age": label, "months": months, "factor": round(factor, 4), "annual": round(benefit_at_full * factor, 2)})
    for later, earlier in ((rows[1], rows[0]), (rows[2], rows[0]), (rows[2], rows[1])):
        gain = later["annual"] - earlier["annual"]
        # Total paid catches up where the extra each year has covered what was given up by waiting.
        later.setdefault("breaks_even", {})[earlier["age"]] = round(later["months"] / 12 + (later["months"] - earlier["months"]) / 12 * earlier["annual"] / gain, 1) if gain > 0 else None
    return {"full_retirement_age": rows[1]["age"], "claims": rows}


def implied_return(lump: float, annual: float, years: int) -> float | None:
    """The rate at which a level income for `years` is worth the lump sum."""
    if lump <= 0 or annual <= 0 or years <= 0:
        return None
    worth = lambda r: sum(annual / (1 + r) ** t for t in range(years))  # noqa: E731
    low, high = -0.2, 1.0
    if not worth(high) <= lump <= worth(low):
        return None
    for _ in range(60):
        mid = (low + high) / 2
        low, high = (mid, high) if worth(mid) > lump else (low, mid)
    return round((low + high) / 2, 4)


def _age_on(dob: date | None, today: date) -> int | None:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)) if dob else None


def _stream_view(s: IncomeStream, person: Person | None, today: date, us: bool) -> dict:
    annual = float(s.annual_amount)
    age = _age_on(person.date_of_birth, today) if person else None
    view = {"id": str(s.id), "person_id": str(s.person_id), "person": person.full_name if person else None, "kind": s.kind, "name": s.name, "annual": annual,
            "start_age": s.start_age, "inflation_linked": s.inflation_linked, "document": s.source_document, "as_of": s.as_of.isoformat(), "started": age is not None and age >= s.start_age,
            "planned_annual": annual, "claiming": None, "pension_options": None}
    if s.kind == "social_security" and us and person and person.date_of_birth:
        full = full_retirement_months(person.date_of_birth.year)
        view["planned_annual"] = round(annual * claim_factor(full, s.start_age * 12), 2)
        view["claiming"] = claiming_table(annual, person.date_of_birth.year)
    if s.lump_sum is not None or s.joint_annual is not None:
        years = max(LIFE_TO - max(s.start_age, age or 0), 1)
        lump = float(s.lump_sum) if s.lump_sum is not None else None
        joint = float(s.joint_annual) if s.joint_annual is not None else None
        view["pension_options"] = {
            "lump_sum": lump, "years_to_match_lump": round(lump / annual, 1) if lump else None, "implied_return": implied_return(lump, annual, years) if lump else None,
            "to_age": LIFE_TO, "joint_annual": joint, "joint_survivor_share": float(s.joint_survivor_share) if s.joint_survivor_share is not None else None,
            "cost_of_joint": round(1 - joint / annual, 4) if joint else None}
    return view


async def streams_of(session: AsyncSession, household_id: uuid.UUID, firm: Firm, today: date) -> list[dict]:
    rows = (await session.execute(select(IncomeStream).where(IncomeStream.household_id == household_id, IncomeStream.firm_id == firm.id,
                                                             IncomeStream.ended_on.is_(None)).order_by(IncomeStream.start_age))).scalars().all()
    if not rows:
        return []
    household = await session.get(Household, household_id)
    market = markets.of_household(firm, household.values if household else None)
    people = {p.id: p for p in (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()}
    return [_stream_view(r, people.get(r.person_id), today, bool(market and market.code == "US")) for r in rows
            if not (people.get(r.person_id) and people[r.person_id].deceased_on)]


def _years_to_clear(balance: float, rate: float, payment: float) -> float | None:
    if payment <= 0:
        return None
    if rate <= 0:
        return round(balance / payment, 1)
    if payment <= rate * balance:
        return None
    return round(-math.log(1 - rate * balance / payment) / math.log(1 + rate), 1)


def _debts(items: list[dict], mu: float) -> list[dict]:
    rows = []
    for i in items:
        if i["side"] != "liability":
            continue
        rate, payment = i.get("interest_rate"), i.get("annual_payment")
        years = _years_to_clear(i["amount"], rate, payment) if rate is not None and payment else None
        rows.append({"id": i["id"], "name": i["name"], "kind": i["kind"], "balance": i["amount"], "rate": rate, "annual_payment": payment, "years_to_clear": years,
                     "interest_left": round(payment * years - i["amount"], 2) if years is not None else None,
                     "dearer_than_the_portfolio": rate is not None and rate > mu, "status": "assessed" if years is not None else "not_assessed"})
    # Dearest first, which is the order extra money saves the most interest.
    return sorted(rows, key=lambda r: -(r["rate"] or 0))


def _projection(sheet: dict, debts: list[dict], mu: float, today: date) -> list[dict]:
    a = sheet["assets"]
    growing = a["managed"] + a["held_away"] + a["retirement_accounts"] + a["cash"]
    fixed = a["property"] + a["business"] + a["other"]
    surplus = sheet["cash_flow"]["surplus"] or 0.0
    balances = [dict(d) for d in debts]
    rows = []
    for n in range(1, PROJECTION_YEARS + 1):
        growing = growing * (1 + mu) + surplus
        for d in balances:
            if d["status"] == "assessed":
                d["balance"] = max(d["balance"] * (1 + (d["rate"] or 0)) - d["annual_payment"], 0.0)
        owed = sum(d["balance"] for d in balances)
        rows.append({"year": today.year + n, "invested": round(growing), "fixed": round(fixed), "owed": round(owed), "net_worth": round(growing + fixed - owed)})
    return rows


def _cash_flow(sheet: dict, firm: Firm, mu: float, today: date, book_cash: float) -> dict:
    target = int((firm.settings or {}).get("reserve_months") or DEFAULT_RESERVE_MONTHS)
    spending = [i for i in sheet["items"] if i["side"] == "expense"]
    essential = sum(i["amount"] for i in spending if i["kind"] == "essential_spending") or sum(i["amount"] for i in spending)
    cash = sheet["assets"]["cash"] + book_cash
    debts = _debts(sheet["items"], mu)
    return {"reserve": {"status": "assessed" if essential else "not_assessed", "cash": round(cash, 2), "months": round(cash / (essential / 12), 1) if essential else None,
                        "target_months": target, "short_by": round(max(essential / 12 * target - cash, 0), 2) if essential else None},
            "cash_flow": sheet["cash_flow"], "basis": sheet["basis"], "debts": debts, "projection": _projection(sheet, debts, mu, today), "expected_return": round(mu, 4)}


async def _book_cash(session: AsyncSession, household_id: uuid.UUID) -> tuple[float, dict]:
    """Cash in the household's accounts, and what its 529 accounts hold."""
    accounts = (await session.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id).where(
        Mandate.person_id.in_(select(Person.id).where(Person.household_id == household_id)), Account.status == "active"))).scalars().all()
    plans = [a.id for a in accounts if a.registration_type == "plan_529"]
    held = (await session.execute(select(Holding).where(Holding.account_id.in_(plans)))).scalars().all() if plans else []
    return sum(float(a.cash_balance or 0) for a in accounts), {"accounts": len(plans), "value": sum(float(h.market_value or 0) for h in held) + sum(float(a.cash_balance or 0) for a in accounts if a.id in plans)}


def _education(goals: list[dict], saved: dict, firm: Firm, mu: float, today: date) -> list[dict]:
    rate = float((firm.settings or {}).get("education_inflation") or DEFAULT_EDUCATION_INFLATION)
    rows = []
    share = saved["value"] / max(len([g for g in goals if g.get("kind") == "education"]), 1)
    for g in goals:
        if g.get("kind") != "education":
            continue
        a = g.get("assumptions") or {}
        cost, start, years = a.get("annual_cost"), a.get("start_year"), a.get("years")
        if not cost or not start or not years:
            rows.append({"goal": g["name"], "status": "not_assessed", "student": a.get("student")})
            continue
        wait = max(int(start) - today.year, 0)
        total = sum(float(cost) * (1 + rate) ** (wait + k) for k in range(int(years)))
        grown = share * (1 + mu) ** wait
        gap = max(total - grown, 0.0)
        saving = gap * mu / ((1 + mu) ** wait - 1) if wait and mu else gap
        rows.append({"goal": g["name"], "status": "assessed", "student": a.get("student"), "start_year": int(start), "years": int(years), "annual_cost_today": float(cost),
                     "cost_source": a.get("cost_source"), "total_cost": round(total), "saved": round(share), "funded": round(min(grown / total, 1), 3) if total else None,
                     "saving_a_year": round(saving), "inflation": rate})
    return rows


async def retirement_inputs(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, retirement_age: int, current_age: int) -> dict:
    """What is on file that bears on the income to plan for: recorded spending, debts that outlast the working
    years, and income that has started by retirement. Empty when nothing is recorded, so nothing changes."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    items = await balance_sheet.current_items(session, household_id, firm_id=firm_id)
    usable, _ = balance_sheet.in_currency(items, firm)
    spending = sum(float(i.amount) for i in usable if i.side == "expense")
    retire_on = today.year + max(retirement_age - current_age, 0)
    debts = sum(float(i.annual_payment or 0) for i in usable if i.side == "liability" and i.annual_payment and (i.ends_on is None or i.ends_on.year > retire_on))
    streams = await streams_of(session, household_id, firm, today)
    counted = [s for s in streams if s["start_age"] <= retirement_age]
    return {"spending": spending, "debt_payments": debts, "guaranteed": round(sum(s["planned_annual"] for s in counted), 2),
            "starts_later": round(sum(s["planned_annual"] for s in streams if s["start_age"] > retirement_age), 2)}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    plan = await retirement.for_household(session, household_id, firm_id=firm_id)
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}
    mu = _blended_assumptions(allocation)[0] if allocation else 0.0
    sheet = await balance_sheet.for_household(session, household_id, firm_id=firm_id)
    book_cash, saved = await _book_cash(session, household_id)
    streams = await streams_of(session, household_id, firm, today)
    projected = plan["projected_at_retirement"] if plan else 0
    rate = (plan["income_target"] / projected) if plan and projected else None
    essential = sum(i["amount"] for i in sheet["items"] if i["kind"] == "essential_spending")
    guaranteed = plan.get("guaranteed_income", 0.0) if plan else 0.0
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    return {
        "household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "not_modelled": NOT_MODELLED, "currency": firm.base_currency,
        "people": [{"person_id": str(p.id), "name": p.full_name} for p in people], "streams": streams,
        "retirement": None if plan is None else {
            **{k: plan[k] for k in ("current_age", "retirement_age", "longevity_age", "income_target", "projected_at_retirement", "success_probability", "on_track",
                                    "target_success", "median_depletion_age", "sustainable_income", "income_gap", "sequence_risk", "levers")},
            "income_need": plan.get("income_need"), "income_basis": plan.get("income_basis"), "guaranteed_income": guaranteed, "starts_later": plan.get("starts_later", 0.0),
            "withdrawal_rate": round(rate, 4) if rate is not None else None, "sustainable_rate": round(plan["sustainable_income"] / projected, 4) if projected else None,
            "rule_of_thumb_rate": RULE_OF_THUMB, "rule_of_thumb_income": round(projected * RULE_OF_THUMB),
            "essential_spending": essential or None, "essentials_covered": round(guaranteed / essential, 3) if essential and guaranteed else None},
        "cash": _cash_flow(sheet, firm, mu, today, book_cash), "education": _education(brain.get("goals", []), saved, firm, mu, today),
    }


async def prepare_stream(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str | None, name: str | None, annual_amount, start_age, source_document: str | None,
                         inflation_linked: bool = False, lump_sum=None, joint_annual=None, joint_survivor_share=None) -> dict:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id or person.household_id is None:
        raise PlanError("Person not found.")
    if kind not in STREAM_KINDS:
        raise PlanError(f"The kind must be one of {', '.join(STREAM_KINDS)}.")
    try:
        amount, age = float(annual_amount), int(start_age)
    except (TypeError, ValueError):
        raise PlanError("Give the yearly amount and the age it starts.")
    if amount <= 0:
        raise PlanError("The yearly amount must be above zero.")
    if not 18 <= age <= 100:
        raise PlanError("The starting age must be between 18 and 100.")
    if kind == "social_security" and not 62 <= age <= 70:
        raise PlanError("Social Security is claimed between 62 and 70.")
    named = (source_document or "").strip()
    if len(named) < 5:
        raise PlanError("Say which statement this comes from.")
    numbers = {}
    for label, value in (("lump sum", lump_sum), ("joint-life amount", joint_annual), ("survivor share", joint_survivor_share)):
        try:
            numbers[label] = float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            raise PlanError(f"The {label} must be a number.")
    if numbers["survivor share"] is not None and not 0 < numbers["survivor share"] <= 1:
        raise PlanError("The survivor share must be above 0% and at most 100%.")
    return {"person": person, "kind": kind, "name": (name or "").strip() or kind.replace("_", " ").capitalize(), "amount": amount, "age": age, "document": named,
            "inflation_linked": bool(inflation_linked), "lump_sum": numbers["lump sum"], "joint_annual": numbers["joint-life amount"], "survivor_share": numbers["survivor share"]}


async def record_stream(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> IncomeStream:
    ready = await prepare_stream(session, person_id, firm_id=firm_id, **terms)
    firm = await session.get(Firm, firm_id)
    row = IncomeStream(firm_id=firm_id, household_id=ready["person"].household_id, person_id=person_id, kind=ready["kind"], name=ready["name"], annual_amount=ready["amount"],
                       start_age=ready["age"], inflation_linked=ready["inflation_linked"], lump_sum=ready["lump_sum"], joint_annual=ready["joint_annual"],
                       joint_survivor_share=ready["survivor_share"], source_document=ready["document"], as_of=firm_time.today(firm), recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="income_stream_recorded", actor=by, content={
        "stream_id": str(row.id), "person_id": str(person_id), "kind": row.kind, "annual": ready["amount"], "start_age": row.start_age, "document": row.source_document})
    return row


async def prepare_end(session: AsyncSession, stream_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    row = await session.get(IncomeStream, stream_id)
    if row is None or row.firm_id != firm_id:
        raise PlanError("Income stream not found.")
    if row.ended_on is not None:
        raise PlanError("This income stream has already ended.")
    why = (reason or "").strip()
    if len(why) < 3:
        raise PlanError("Say why it has ended.")
    return {"stream": row, "reason": why, "person": await session.get(Person, row.person_id)}


async def end_stream(session: AsyncSession, stream_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, reason: str | None) -> IncomeStream:
    ready = await prepare_end(session, stream_id, firm_id=firm_id, reason=reason)
    row = ready["stream"]
    row.ended_on, row.ended_reason = firm_time.today(await session.get(Firm, firm_id)), ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="income_stream_ended", actor=by, content={"stream_id": str(row.id), "reason": row.ended_reason})
    return row


async def prepare_education(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, student: str | None, start_year, years, annual_cost, cost_source: str | None) -> dict:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise PlanError("Household not found.")
    who = (student or "").strip()
    if len(who) < 2:
        raise PlanError("Say who this is for.")
    try:
        first, span, cost = int(start_year), int(years), float(annual_cost)
    except (TypeError, ValueError):
        raise PlanError("Give the starting year, the number of years and the yearly cost.")
    firm = await session.get(Firm, firm_id)
    if first < firm_time.today(firm).year:
        raise PlanError("The starting year has passed.")
    if not 1 <= span <= 10:
        raise PlanError("The number of years must be between 1 and 10.")
    if cost <= 0:
        raise PlanError("The yearly cost must be above zero.")
    source = (cost_source or "").strip()
    if len(source) < 5:
        raise PlanError("Say where the cost comes from: the school's published fees, say.")
    taken = (await session.execute(select(Goal).where(Goal.household_id == household_id, Goal.kind == "education", Goal.name == f"Education · {who}"))).scalars().first()
    if taken is not None:
        raise PlanError(f"An education goal for {who} is already on file.")
    return {"household": household, "student": who, "start_year": first, "years": span, "annual_cost": cost, "source": source}


async def record_education(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **terms) -> Goal:
    ready = await prepare_education(session, household_id, firm_id=firm_id, **terms)
    row = Goal(firm_id=firm_id, household_id=household_id, name=f"Education · {ready['student']}", kind="education", target_amount=ready["annual_cost"] * ready["years"],
               target_date=date(ready["start_year"], 1, 1), priority=2,
               assumptions={"student": ready["student"], "start_year": ready["start_year"], "years": ready["years"], "annual_cost": ready["annual_cost"], "cost_source": ready["source"]})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="education_goal_recorded", actor=by, content={
        "goal_id": str(row.id), "student": ready["student"], "annual_cost": ready["annual_cost"], "source": ready["source"]})
    return row
