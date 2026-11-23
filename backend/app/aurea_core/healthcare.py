"""Healthcare in retirement, from what the firm states and what the law sets (B-191, L200-2 §3).

US only. Each adult, each retirement year:

  - before 65, what the firm states healthcare costs a person a year before Medicare (`healthcare.pre65` in the
    assumption set, with its source);
  - from 65, what the firm states it costs on Medicare beyond Part B (`healthcare.medicare`), plus the Part B standard
    premium (the law's, by year) and the IRMAA surcharge for that premium year: the tier the household's income on file
    two years before puts them in (`roth_window`'s arithmetic, B-190), or the AGI on a recorded return for that year.

A firm that states neither has no healthcare here: it is "not assessed", and a retirement plan is exactly what it was.
One stated and not the other leaves the other phase not assessed, named. Nothing is supplied by default, because a cost
nobody stated would read as one somebody did.

Stated costs grow with the firm's healthcare inflation from today; Part B and IRMAA are the law's for the years it has
set and grow with healthcare inflation after, which is assumed and named. IRMAA stops at the premium year that reads an
RMD year's income, because RMDs are not in the income on file, and says so. Amounts are given in the money of their
year (what the plan draws) and in today's money (what a person reads).
"""
from __future__ import annotations

import uuid
from datetime import date

from app.aurea_core import law_figures

MEDICARE_AGE = 65
NOT_MODELLED = ["aca_premium_tax_credit", "hsa", "medigap_underwriting", "part_b_late_enrolment_penalty",
                "irmaa_on_rmd_income", "long_term_care_is_protection", "employer_retiree_cover"]


def _part_b_year(year: int, h: float) -> tuple[float, bool]:
    """Part B's standard premium for a year, a year's worth in that year's money, and whether it is held past the law's
    last year (grown at healthcare inflation)."""
    f = law_figures.get("us_part_b_premium", date(year, 7, 1))
    if f.status(date(year, 7, 1)) == "in_force":
        return 12 * float(f.value), False
    last = f.applies_from.year
    return 12 * float(f.value) * (1 + h) ** max(year - last, 0), True


def lay_out(brain: dict, streams: list[dict], *, today: date, from_year: int, to_year: int) -> dict:
    """Each year's healthcare from `from_year` to `to_year`, per adult, from the brain and the streams on file."""
    from app.aurea_core import rmd, roth_window

    planning = brain.get("planning") or {}
    stated = planning.get("healthcare") or {}
    pre, med = stated.get("pre65"), stated.get("medicare")
    inflation = planning.get("inflation") or {}
    cpi, h = float(inflation.get("cpi", 0.025)), float(inflation.get("healthcare", 0.05))
    base = {"money_basis": "today and the year's own", "not_modelled": NOT_MODELLED,
            "costs": {"pre65": pre, "medicare_beyond_part_b": med}, "inflation": {"cpi": cpi, "healthcare": h}}
    if pre is None and med is None:
        return {**base, "status": "not_assessed", "reason": "The firm has stated no healthcare cost in its planning assumptions.",
                "years": [], "by_t": {}}
    adults = [p for p in brain.get("persons", []) if not p.get("is_next_gen") and not p.get("deceased_on")]
    names = {p["id"]: p.get("preferred_name") or p["full_name"] for p in adults}
    dobs = {p["id"]: date.fromisoformat(str(p["date_of_birth"])[:10]) for p in adults if p.get("date_of_birth")}
    assumed, not_assessed = [], []
    if "healthcare_inflation" in (planning.get("inflation_assumed") or []):
        assumed.append("healthcare_inflation")
    missing = [names[p["id"]] for p in adults if p["id"] not in dobs]
    if missing:
        not_assessed.append(f"no date of birth on file for {', '.join(missing)}")
    if pre is None:
        not_assessed.append("before Medicare: the firm has stated no cost")
    if med is None:
        not_assessed.append("on Medicare: the firm has stated no cost beyond Part B")

    # IRMAA: the filing status the law knows, and the income on file each year; RMD years' income is not on file.
    filing, filing_basis, label = roth_window.filing_of(brain, adults)
    irmaa_ok = filing in roth_window.FILING_KEYS and not (filing == "married_filing_jointly" and len(adults) < 2)
    if not irmaa_ok:
        not_assessed.append(f"IRMAA: the filing status on file, {label}, is not one the law's tables are held for")
    tax_of = lambda p: (p.get("profile") or {}).get("tax") or {}  # noqa: E731
    rmd_years = [dobs[p["id"]].year + rmd.start_age(dobs[p["id"]]) for p in adults
                 if p["id"] in dobs and (tax_of(p).get("ira_value") or 0) > 0]
    first_rmd = min(rmd_years) if rmd_years else None
    returns = {r["tax_year"]: r for r in (brain.get("tax_returns") or []) if r.get("agi") is not None}
    irmaa_stops, no_return = None, set()

    def irmaa_month(premium_year: int, aged: int) -> tuple[float | None, str]:
        """The surcharge a month per person on Medicare in `premium_year`, in that year's money, and what it read."""
        nonlocal irmaa_stops
        income_year = premium_year - roth_window.LOOKBACK
        if first_rmd is not None and income_year >= first_rmd:
            irmaa_stops = irmaa_stops or premium_year
            return None, "rmd_income_not_on_file"
        if income_year < today.year:
            # A filed year: its AGI, in that year's dollars, against the premium year's table as the law publishes it.
            ret = returns.get(income_year)
            if ret is None:
                no_return.add(premium_year)
                return None, "no_return_for_the_year"
            k = roth_window.FILING_KEYS[filing]
            tiers = law_figures.tiers(f"us_irmaa_{k}", date(premium_year, 7, 1))
            tier = roth_window.tier_in(tiers, float(ret["agi"]))
            return tiers[tier][1] + tiers[tier][2], f"tier {tier}, from the {income_year} return"
        law = roth_window.YearLaw(income_year, filing, today, cpi)
        _, ss, other = roth_window.income_in(streams, dobs, names, income_year, today=today, inflation=cpi)
        tier = law.tier_of(law.work(other, ss, aged)["agi"])
        # The tier's amounts are in today's money; back to the premium year's money.
        monthly_today = law.irmaa[tier][1] + law.irmaa[tier][2]
        return monthly_today * (1 + cpi) ** max(premium_year - today.year, 0), f"tier {tier}, from income on file"

    years, by_t, part_b_held = [], {}, False
    for year in range(max(from_year, today.year), to_year + 1):
        t = year - today.year
        deflate = (1 + cpi) ** t
        people, total = {}, 0.0
        aged_in_income_year = sum(1 for d in dobs.values() if (year - roth_window.LOOKBACK) - d.year >= MEDICARE_AGE)
        for pid, dob in dobs.items():
            age = year - dob.year
            if age < MEDICARE_AGE:
                if pre is None:
                    people[names[pid]] = {"phase": "before_medicare", "status": "not_assessed", "age": age}
                    continue
                amount = pre * (1 + h) ** t
                people[names[pid]] = {"phase": "before_medicare", "age": age, "stated": round(amount / deflate, 2),
                                      "amount_today": round(amount / deflate, 2)}
            else:
                if med is None:
                    people[names[pid]] = {"phase": "medicare", "status": "not_assessed", "age": age}
                    continue
                part_b, held = _part_b_year(year, h)
                part_b_held = part_b_held or held
                stated_now = med * (1 + h) ** t
                surcharge, read = irmaa_month(year, aged_in_income_year) if irmaa_ok else (None, "not_assessed")
                amount = stated_now + part_b + 12 * (surcharge or 0.0)
                people[names[pid]] = {"phase": "medicare", "age": age, "stated": round(stated_now / deflate, 2),
                                      "part_b": round(part_b / deflate, 2), "part_b_held": held,
                                      "irmaa": round(12 * surcharge / deflate, 2) if surcharge is not None else None, "irmaa_read": read,
                                      "amount_today": round(amount / deflate, 2)}
            total += amount
        by_t[t] = round(total, 2)
        years.append({"year": year, "people": people, "total": round(total, 2), "total_today": round(total / deflate, 2)})
    if part_b_held:
        assumed.append("Part B after the law's last year grows with healthcare inflation")
    if no_return:
        not_assessed.append(f"IRMAA for {', '.join(map(str, sorted(no_return)))}: no return on file for the year it reads")
    if irmaa_stops:
        not_assessed.append(f"IRMAA from {irmaa_stops}: it reads an RMD year's income, which is not on file")
    status = "assessed" if not not_assessed else "partly_assessed"
    return {**base, "status": status, "years": years, "by_t": by_t, "assumed": assumed, "not_assessed": not_assessed,
            "filing_status": filing, "filing_basis": filing_basis,
            "first_year_today": years[0]["total_today"] if years else None,
            "total_today": round(sum(y["total_today"] for y in years)),
            "law": law_figures.cited(("us_part_b_premium",) + ((f"us_irmaa_{roth_window.FILING_KEYS[filing]}",) if irmaa_ok else ()), today)}


async def for_household(session, household_id: uuid.UUID, *, firm_id: uuid.UUID, from_year: int | None = None,
                        to_year: int | None = None, brain: dict | None = None) -> dict:
    """A household's healthcare, year by year. Without years given it runs from the retirement plan's first year to its
    end; the plan itself calls this with its own years."""
    from app.aurea_core import financial_plan, firm_time, markets
    from app.aurea_core.graph import household_brain
    from app.models.graph import Household
    from app.models.tenant import Firm

    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ValueError("Household not found")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    head = {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "currency": firm.base_currency,
            "decides": "nothing: what cover to buy is the client's", "estimate": True}
    market = markets.of_household(firm, household.values)
    if market is None or market.code != "US":
        return {**head, "status": "not_assessed", "reason": "Healthcare costs are laid out for a household taxed in the US.",
                "years": [], "by_t": {}, "not_modelled": NOT_MODELLED}
    brain = brain or await household_brain(session, household_id, firm_id=firm_id)
    if from_year is None or to_year is None:
        from app.aurea_core import retirement

        plan = await retirement.for_household(session, household_id, firm_id=firm_id)
        if plan is None:
            return {**head, "status": "not_assessed", "reason": "No retirement plan can be run.", "years": [], "by_t": {}}
        return {**head, **(plan.get("healthcare") or {}), "plan_years": [today.year + plan["years_to_retirement"],
                                                                         today.year + plan["years_to_retirement"] + plan["years_in_retirement"] - 1]}
    streams = await financial_plan.streams_of(session, household_id, firm, today)
    return {**head, **lay_out(brain, streams, today=today, from_year=from_year, to_year=to_year)}
