"""Protection planning: what happens to a household if an earner dies or cannot work.

For each earner this works out two gaps, in today's money, by the income-replacement method:

  death       what the survivors would need - a share of the household's income, less what the
              survivors still earn, to the earner's retirement, plus goals other than retirement
              that fall due before then - against life cover and the household's assets.
  disability  a share of the earner's own income to their retirement, against income protection
              paid to that age, total and permanent disablement cover and the household's assets.

It only works from what is on file. Income is never estimated: the planners' fallback of 5% of the
portfolio is a guess about a retirement goal, and a protection figure built on it would look exact.
With no income or date of birth the person is "not assessed", and saying so is the result.

Assets are the managed book, what is held away, cash, and pensions and savings accounts, all at full
value, so a wealthy household reads as self-insured. That is the honest reading of the sum, but it spends
the retirement pot on the claim: the need, the cover and the assets are all reported so an adviser can
see which of them closed the gap. A home or a business is not among them, because neither can pay a claim.

Debts on file add to what survivors would need, the earner's own and the household's joint ones. They do
not add to the disability need, because the income benefit is what keeps paying them. With no debts on file
the need is what it is and the result says none are on file, which does not mean there are none.

Long-term care is a third question, asked of anyone over the firm's care age, retired or not: a year's care
for so many years, against care cover and the same assets. The cost is the firm's figure, set by a person
from a source, because it depends on the place and the kind of care and nothing on file says what it is. With
none set, care is "not assessed". What the state pays towards care is not modelled, and the result says so.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.aurea_core import balance_sheet
from app.aurea_core.balance_sheet import income_of, retirement_accounts_of  # noqa: F401  - read from here elsewhere
from app.aurea_core.goal_tradeoff import human_capital_pv
from app.aurea_core.graph import household_brain, list_households
from app.core.db import utcnow
from app.models.graph import Person
from app.models.protection import POLICY_KINDS, ProtectionPolicy
from app.models.tenant import Firm
from app.provenance import ledger

DEFAULTS = {
    "death_replacement": 0.70,       # of household income, what the survivors need to live on
    "disability_replacement": 0.75,  # of the earner's income, the usual ceiling on an income benefit
    "retirement_age": 65,            # when no retirement goal states one
    "discount_rate": 0.04,           # the risk-free rate the human-capital figure already uses
    "care_from_age": 55,             # younger than this, care is not yet the question
}
# Goals a death does not make more urgent: the earner's own retirement is covered by replacing the
# income, and a legacy is what is left, not a need.
_NOT_A_NEED = ("retirement", "legacy")
# Paid yearly, so they carry an annual benefit and no sum insured.
_YEARLY_KINDS = ("income_protection", "long_term_care")
SCENARIOS = ("death", "disability", "care")
CARE_YEARS_MAX = 40


class ProtectionError(Exception):
    """The record was refused. Carries a reason fit to show the user."""


def assumptions(firm: Firm | None) -> dict:
    cfg = ((firm.settings or {}).get("protection") or {}) if firm else {}
    return {k: type(v)(cfg.get(k, v)) for k, v in DEFAULTS.items()}


def care_basis(firm: Firm | None) -> dict | None:
    """The firm's stated cost of care, with who set it and from what, or None if nobody has."""
    care = (((firm.settings or {}).get("protection") or {}).get("care")) if firm else None
    return care if care and care.get("annual_cost") else None


def not_modelled(basis: dict | None) -> list[str]:
    return [*([] if basis else ["long_term_care"]), "state_care_funding", "funeral_and_final_costs"]


def age_on(dob: date | None, today: date) -> int | None:
    if dob is None:
        return None
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _status(gap: float) -> str:
    return "gap" if gap > 0.005 else "no_gap"


def assess(
    *, age: int | None, income: float | None, other_income: float, has_dependants: bool, retirement_age: int,
    goals_due: float, assets: float, life: float = 0.0, tpd: float = 0.0, income_benefits: list[tuple[float, int | None]] = (),
    rules: dict | None = None, retirement_stated: bool = True, debts: float = 0.0,
) -> dict:
    """The two gaps for one earner. Pure. `income_benefits` is [(annual benefit, paid to age)].
    `retirement_stated` says the retirement age came from the client's own goal, not the default.
    `debts` are what survivors would have to repay; with nobody left behind they are not a need."""
    rules = {**DEFAULTS, **(rules or {})}
    missing = [name for name, v in (("income", income), ("date_of_birth", age)) if v is None]
    if missing:
        blank = {"status": "not_assessed", "missing": missing}
        return {"years": None, "death": blank, "disability": blank}
    years = max(0, retirement_age - age)
    if years == 0:
        # Past the default age with an income is someone who has not retired, not someone with
        # nothing to protect. How long the earnings run is unknown, so it is not assessed.
        if income > 0 and not retirement_stated:
            blank = {"status": "not_assessed", "missing": ["retirement_date"]}
            return {"years": None, "death": blank, "disability": blank}
        done = {"status": "past_retirement"}
        return {"years": 0, "death": done, "disability": done}

    pv = lambda annual, to_age=retirement_age: human_capital_pv(
        age, min(to_age, retirement_age), annual, discount_rate=rules["discount_rate"])

    survivors_need = (max(0.0, rules["death_replacement"] * (income + other_income) - other_income)
                      if has_dependants else 0.0)
    income_need = pv(survivors_need)
    owed = debts if has_dependants else 0.0
    need = income_need + goals_due + owed
    death_gap = max(0.0, need - life - assets)

    own_need = pv(rules["disability_replacement"] * income)
    benefits = sum(pv(b, to if to is not None else retirement_age) for b, to in income_benefits)
    disability_gap = max(0.0, own_need - benefits - tpd - assets)

    return {
        "years": years,
        "death": {
            "status": _status(death_gap), "need": round(need, 2), "income_need": round(income_need, 2),
            "goals_need": round(goals_due, 2), "debts": round(owed, 2), "annual_need": round(survivors_need, 2),
            "cover": round(life, 2),
            "assets": round(assets, 2), "gap": round(death_gap, 2),
            "gap_before_assets": round(max(0.0, need - life), 2),
        },
        "disability": {
            "status": _status(disability_gap), "need": round(own_need, 2),
            "annual_need": round(rules["disability_replacement"] * income, 2),
            "cover": round(benefits + tpd, 2), "income_benefit_value": round(benefits, 2), "lump_sum": round(tpd, 2),
            "assets": round(assets, 2), "gap": round(disability_gap, 2),
            "gap_before_assets": round(max(0.0, own_need - benefits - tpd), 2),
        },
    }


def assess_care(*, age: int | None, basis: dict | None, assets: float, benefits: list[tuple[float, int | None]] = (),
                from_age: int = DEFAULTS["care_from_age"]) -> dict:
    """One person's care, in today's money. Pure. `benefits` is [(annual benefit, years paid)], None
    meaning for as long as care lasts. Each person is read alone against the same assets, so two
    people's gaps are not a household's: the assets can only be spent once."""
    if age is None:
        return {"status": "not_assessed", "missing": ["date_of_birth"]}
    if age < from_age:
        return {"status": "not_yet"}
    if basis is None:
        return {"status": "not_assessed", "missing": ["care_cost"]}
    annual, years = float(basis["annual_cost"]), int(basis["years"])
    need = annual * years
    cover = sum(b * min(years, y if y is not None else years) for b, y in benefits)
    gap = max(0.0, need - cover - assets)
    return {"status": _status(gap), "need": round(need, 2), "annual_need": round(annual, 2), "years": years,
            "cover": round(cover, 2), "assets": round(assets, 2), "gap": round(gap, 2),
            "gap_before_assets": round(max(0.0, need - cover), 2)}


def _retirement_for(age: int | None, goals: list[dict], *, today: date, default: int) -> tuple[int, str]:
    """(retirement age, where it came from): the age the retirement goal states, else the age this
    person reaches on the goal's date, else the firm's default."""
    for g in goals:
        if g.get("kind") != "retirement":
            continue
        stated = (g.get("assumptions") or {}).get("retirement_age")
        if stated:
            return int(stated), "goal_age"
        if age is not None and g.get("target_date"):
            years = (date.fromisoformat(g["target_date"]) - today).days / 365.25
            if years > 0:
                return age + max(1, round(years)), "goal_date"
    return default, "assumed"


def _goals_due(goals: list[dict], *, today: date, years: int, rate: float) -> float:
    """Goals other than retirement that fall due before the earner would have retired, in today's money."""
    total = 0.0
    for g in goals:
        if g.get("kind") in _NOT_A_NEED or not g.get("target_date"):
            continue
        due = (date.fromisoformat(g["target_date"]) - today).days / 365.25
        if 0 < due <= years:
            total += float(g.get("target_amount") or 0) / ((1 + rate) ** due)
    return total


def _policy_row(p: ProtectionPolicy, names: dict, today: date) -> dict:
    lapsed = p.expires_on is not None and p.expires_on < today
    return {
        "id": str(p.id), "person_id": str(p.person_id), "person": names.get(p.person_id), "kind": p.kind,
        "provider": p.provider, "policy_number": p.policy_number,
        "sum_insured": float(p.sum_insured) if p.sum_insured is not None else None,
        "annual_benefit": float(p.annual_benefit) if p.annual_benefit is not None else None,
        "benefit_to_age": p.benefit_to_age, "benefit_years": p.benefit_years, "currency": p.currency,
        "annual_premium": float(p.annual_premium) if p.annual_premium is not None else None,
        "expires_on": p.expires_on.isoformat() if p.expires_on else None,
        "status": "expired" if p.status == "in_force" and lapsed else str(p.status),
        "counted": p.status == "in_force" and not lapsed, "source_document": p.source_document,
        "recorded_by": p.recorded_by,
    }


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID,
                        today: date | None = None) -> dict | None:
    """Every earner's gaps for one household, or None if it is not in this firm."""
    today = today or utcnow().date()
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    firm = await session.get(Firm, firm_id)
    rules, care = assumptions(firm), care_basis(firm)
    persons = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    policies = (await session.execute(
        select(ProtectionPolicy).where(ProtectionPolicy.household_id == household_id, ProtectionPolicy.firm_id == firm_id)
    )).scalars().all()
    names = {p.id: p.full_name for p in persons}
    rows = [_policy_row(p, names, today) for p in policies]
    in_force = [p for p, r in zip(policies, rows) if r["counted"]]

    managed = float(brain["totals"]["total_value"])
    usable, _ = balance_sheet.in_currency(await balance_sheet.current_items(session, household_id, firm_id=firm_id), firm)
    outside = balance_sheet.assets_on_file(persons, usable)
    assets = managed + balance_sheet.liquid_of(outside)
    has_people = len(persons) > 1

    people = []
    for p in persons:
        income = income_of(p)
        if p.is_next_gen and income is None:
            continue   # a child on the household's record is someone protected, not an earner to assess
        others = [o for o in persons if o.id != p.id]
        other_income = sum(income_of(o) or 0.0 for o in others)
        age = age_on(p.date_of_birth, today)
        mine = [x for x in in_force if x.person_id == p.id]
        retirement_age, basis = _retirement_for(age, brain["goals"], today=today, default=rules["retirement_age"])
        years = max(0, retirement_age - age) if age is not None else 0
        result = assess(
            age=age, income=income, other_income=other_income, has_dependants=has_people,
            retirement_age=retirement_age, retirement_stated=basis != "assumed", assets=assets, rules=rules,
            goals_due=_goals_due(brain["goals"], today=today, years=years, rate=rules["discount_rate"]),
            debts=balance_sheet.debts_of(usable, p.id),
            life=sum(float(x.sum_insured or 0) for x in mine if x.kind == "life"),
            tpd=sum(float(x.sum_insured or 0) for x in mine if x.kind == "tpd"),
            income_benefits=[(float(x.annual_benefit or 0), x.benefit_to_age) for x in mine
                             if x.kind == "income_protection"],
        )
        people.append({
            "person_id": str(p.id), "name": p.full_name, "age": age, "income": income,
            "retirement_age": retirement_age, "retirement_basis": basis, "years_to_retirement": result["years"],
            # A survivor whose income is unknown is counted as earning nothing, which overstates the need.
            "other_income_missing": any(income_of(o) is None and not o.is_next_gen for o in others),
            "death": result["death"], "disability": result["disability"],
            "care": assess_care(age=age, basis=care, assets=assets, from_age=rules["care_from_age"],
                                benefits=[(float(x.annual_benefit or 0), x.benefit_years) for x in mine
                                          if x.kind == "long_term_care"]),
        })

    statuses = [s for r in people for s in (r["death"]["status"], r["disability"]["status"], r["care"]["status"])]
    gaps = [r[k]["gap"] for r in people for k in SCENARIOS if r[k].get("gap")]
    return {
        "household_id": str(household_id), "household": brain["household"]["name"],
        "currency": firm.base_currency if firm else None, "people": people, "policies": rows,
        "assets": {"managed": round(managed, 2), "held_away": round(outside["held_away"], 2),
                   "retirement_accounts": round(outside["retirement_accounts"], 2), "cash": round(outside["cash"], 2),
                   "total": round(assets, 2)},
        # No debt on file is not no debt, so the count travels with the figures.
        "debts_on_file": sum(1 for i in usable if i.side == "liability"),
        "assumptions": rules, "care_basis": care, "not_modelled": not_modelled(care),
        "summary": {"people": len(people), "gaps": statuses.count("gap"), "largest_gap": max(gaps, default=0.0),
                    "not_assessed": sum(1 for r in people if r["death"]["status"] == "not_assessed"),
                    "assessed": sum(1 for r in people if r["death"]["status"] in ("gap", "no_gap")),
                    "policies_counted": len(in_force)},
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    """Every household, those with a gap first, then the ones nobody can assess yet."""
    today = today or utcnow().date()
    firm = await session.get(Firm, firm_id)
    rows = []
    for h in await list_households(session, firm_id):
        view = await for_household(session, uuid.UUID(h["id"]), firm_id=firm_id, today=today)
        if view:
            rows.append(view)
    rows.sort(key=lambda r: (-r["summary"]["largest_gap"], -r["summary"]["not_assessed"], r["household"]))
    # Clear means someone was measured and came up covered. A household nobody could be measured in
    # is not clear: it has no one to assess, however it got there.
    gap = [r for r in rows if r["summary"]["gaps"]]
    unknown = [r for r in rows if not r["summary"]["gaps"] and r["summary"]["not_assessed"]]
    clear = [r for r in rows if not r["summary"]["gaps"] and not r["summary"]["not_assessed"] and r["summary"]["assessed"]]
    care = care_basis(firm)
    return {
        "households": rows, "assumptions": assumptions(firm), "care_basis": care, "not_modelled": not_modelled(care),
        "currency": firm.base_currency if firm else None,
        "summary": {
            "households": len(rows),
            "with_gap": len(gap), "not_assessed": len(unknown), "clear": len(clear),
            "no_people": len(rows) - len(gap) - len(unknown) - len(clear),
            "people_not_assessed": sum(r["summary"]["not_assessed"] for r in rows),
        },
    }


def gaps_of(view: dict) -> list[dict]:
    """Each measured gap in a household's result, largest first."""
    found = [{"person_id": p["person_id"], "person": p["name"], "scenario": scenario, "gap": p[scenario]["gap"],
              "need": p[scenario]["need"], "cover": p[scenario]["cover"], "assets": p[scenario]["assets"],
              "partner_income_unknown": scenario == "death" and p["other_income_missing"],
              "retirement_assumed": scenario != "care" and p["retirement_basis"] == "assumed"}
             for p in view["people"] for scenario in SCENARIOS if p[scenario]["status"] == "gap"]
    return sorted(found, key=lambda g: -g["gap"])


def _money(value, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise ProtectionError(f"{label} must be a number.")
    if amount < 0:
        raise ProtectionError(f"{label} cannot be negative.")
    return amount


def _years(value, label: str) -> int:
    try:
        years = int(value)
    except (TypeError, ValueError):
        raise ProtectionError(f"{label} must be a whole number of years.")
    if not 1 <= years <= CARE_YEARS_MAX:
        raise ProtectionError(f"{label} must be between 1 and {CARE_YEARS_MAX} years.")
    return years


def _source(document: str | None) -> str:
    if len((document or "").strip()) < 3:
        raise ProtectionError("Say which document this comes from - its name, date and provider. "
                              "A figure with no source cannot be taken to a client.")
    return document.strip()[:255]


async def _person(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID) -> Person:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id:
        raise ProtectionError("That person is not in this firm.")
    return person


async def prepare_policy(
    session: AsyncSession, firm_id: uuid.UUID, *, person_id: uuid.UUID, kind: str, provider: str,
    source_document: str, sum_insured=None, annual_benefit=None, benefit_to_age=None, benefit_years=None,
    annual_premium=None, expires_on: date | None = None, today: date | None = None,
) -> dict:
    """Everything recording a policy would refuse, refused without writing, so a Confirm card that
    would only fail is not shown. Returns what would be written."""
    today = today or utcnow().date()
    person = await _person(session, firm_id, person_id)
    if person.household_id is None:
        raise ProtectionError("That person is not in a household, so the cover has nowhere to count.")
    if kind not in POLICY_KINDS:
        raise ProtectionError(f"Unknown policy kind '{kind}'. Choose one of: {', '.join(POLICY_KINDS)}.")
    if len((provider or "").strip()) < 2:
        raise ProtectionError("Name the provider.")
    source = _source(source_document)
    lump, benefit = _money(sum_insured, "The sum insured"), _money(annual_benefit, "The annual benefit")
    premium = _money(annual_premium, "The annual premium")
    if kind in _YEARLY_KINDS:
        if not benefit:
            raise ProtectionError("Income protection needs its annual benefit." if kind == "income_protection"
                                  else "Long-term care cover needs its annual benefit.")
        lump = None
    else:
        if not lump:
            raise ProtectionError("This cover needs its sum insured.")
        benefit, benefit_to_age = None, None
    if kind != "income_protection":
        benefit_to_age = None
    paid_for = _years(benefit_years, "The benefit period") if kind == "long_term_care" and benefit_years not in (None, "") else None
    if expires_on is not None and expires_on < today:
        raise ProtectionError(f"This policy expired on {expires_on.isoformat()}, so it is not cover.")
    return {"person": person, "kind": kind, "provider": provider.strip()[:120], "source": source, "sum_insured": lump,
            "annual_benefit": benefit, "benefit_to_age": int(benefit_to_age) if benefit_to_age else None,
            "benefit_years": paid_for, "annual_premium": premium, "expires_on": expires_on}


async def record_policy(
    session: AsyncSession, firm_id: uuid.UUID, *, person_id: uuid.UUID, kind: str, provider: str,
    source_document: str, recorded_by: str, sum_insured=None, annual_benefit=None, benefit_to_age=None,
    benefit_years=None, annual_premium=None, policy_number: str | None = None, expires_on: date | None = None,
    currency: str | None = None, today: date | None = None,
) -> dict:
    ready = await prepare_policy(session, firm_id, person_id=person_id, kind=kind, provider=provider,
                                 source_document=source_document, sum_insured=sum_insured, annual_benefit=annual_benefit,
                                 benefit_to_age=benefit_to_age, benefit_years=benefit_years, annual_premium=annual_premium,
                                 expires_on=expires_on, today=today)
    person = ready["person"]
    firm = await session.get(Firm, firm_id)
    row = ProtectionPolicy(
        firm_id=firm_id, household_id=person.household_id, person_id=person.id, kind=kind,
        provider=ready["provider"], policy_number=(policy_number or "").strip()[:64] or None,
        sum_insured=ready["sum_insured"], annual_benefit=ready["annual_benefit"], benefit_to_age=ready["benefit_to_age"],
        benefit_years=ready["benefit_years"],
        annual_premium=ready["annual_premium"], currency=(currency or firm.base_currency or "NZD").upper()[:3],
        expires_on=expires_on, status="in_force", source_document=ready["source"], recorded_by=recorded_by,
    )
    session.add(row)
    await session.flush()
    return {"policy_id": str(row.id), "person": person.full_name, "household_id": str(person.household_id),
            "kind": kind, "provider": row.provider, "sum_insured": ready["sum_insured"],
            "annual_benefit": ready["annual_benefit"]}


async def prepare_end(session: AsyncSession, firm_id: uuid.UUID, policy_id: uuid.UUID, *, reason: str) -> dict:
    """What ending would refuse, refused without writing, and the policy it would end."""
    if len((reason or "").strip()) < 3:
        raise ProtectionError("Say why this cover ended - lapsed, cancelled, claimed, replaced.")
    row = await session.get(ProtectionPolicy, policy_id)
    if row is None or row.firm_id != firm_id:
        raise ProtectionError("That policy was not found.")
    if row.status != "in_force":
        raise ProtectionError("This policy has already ended.")
    return {"policy": row, "person": await session.get(Person, row.person_id), "reason": reason.strip()[:255]}


async def end_policy(session: AsyncSession, firm_id: uuid.UUID, policy_id: uuid.UUID, *, reason: str, by: str,
                     today: date | None = None) -> dict:
    """Mark a policy as no longer in force. Kept, with why, rather than deleted. The row is locked and
    its status re-read, so two people ending it cannot both record a reason."""
    if len((reason or "").strip()) < 3:
        raise ProtectionError("Say why this cover ended - lapsed, cancelled, claimed, replaced.")
    row = (await session.execute(
        select(ProtectionPolicy).where(ProtectionPolicy.id == policy_id, ProtectionPolicy.firm_id == firm_id)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if row is None:
        raise ProtectionError("That policy was not found.")
    if row.status != "in_force":
        raise ProtectionError("This policy has already ended.")
    row.status, row.ended_on, row.ended_by, row.ended_reason = "ended", today or utcnow().date(), by, reason.strip()[:255]
    await session.flush()
    return {"policy_id": str(row.id), "status": "ended", "provider": row.provider, "kind": row.kind}


async def prepare_care(session: AsyncSession, firm_id: uuid.UUID, *, annual_cost, years, source_document: str) -> dict:
    """What setting the firm's care cost would refuse, refused without writing, and what it replaces."""
    amount = _money(annual_cost, "The yearly cost of care")
    if not amount:
        raise ProtectionError("Give the yearly cost of care.")
    return {"annual_cost": amount, "years": _years(years, "The length of care"), "source": _source(source_document),
            "previous": care_basis(await session.get(Firm, firm_id))}


async def set_care(session: AsyncSession, firm_id: uuid.UUID, *, annual_cost, years, source_document: str, set_by: str) -> dict:
    """State the firm's cost of care. It moves every client's care figure at once, so it names its
    source, and what it replaced goes on the ledger with who changed it."""
    ready = await prepare_care(session, firm_id, annual_cost=annual_cost, years=years, source_document=source_document)
    firm = await session.get(Firm, firm_id)
    basis = {"annual_cost": ready["annual_cost"], "years": ready["years"], "source": ready["source"],
             "set_by": set_by, "set_at": utcnow().isoformat()}
    settings = dict(firm.settings or {})
    settings["protection"] = {**(settings.get("protection") or {}), "care": basis}
    firm.settings = settings
    flag_modified(firm, "settings")
    await ledger.append_entry(session, firm_id=firm_id, event_type="protection_care_basis", actor=set_by,
                              content={"care": basis, "replaced": ready["previous"]})
    await session.flush()
    return {**basis, "previous": ready["previous"]}


async def prepare_income(session: AsyncSession, firm_id: uuid.UUID, *, person_id: uuid.UUID, annual_income,
                         source_document: str) -> dict:
    """What recording an income would refuse, refused without writing, and what it would replace."""
    person = await _person(session, firm_id, person_id)
    amount = _money(annual_income, "The annual income")
    if amount is None:
        raise ProtectionError("Give the annual income.")
    return {"person": person, "amount": amount, "source": _source(source_document), "previous": income_of(person)}


async def record_income(session: AsyncSession, firm_id: uuid.UUID, *, person_id: uuid.UUID, annual_income,
                        source_document: str, recorded_by: str) -> dict:
    """Record a person's income where the tax modules already read it, with where it came from."""
    ready = await prepare_income(session, firm_id, person_id=person_id, annual_income=annual_income,
                                 source_document=source_document)
    person, amount, source = ready["person"], ready["amount"], ready["source"]
    profile = dict(person.profile or {})
    tax = dict(profile.get("tax") or {})
    previous = tax.get("annual_income")
    tax["annual_income"] = amount
    tax["income_source"] = {"document": source, "recorded_by": recorded_by, "recorded_at": utcnow().isoformat()}
    profile["tax"] = tax
    person.profile = profile
    flag_modified(person, "profile")
    await session.flush()
    return {"person_id": str(person.id), "person": person.full_name, "annual_income": amount, "previous": previous}

