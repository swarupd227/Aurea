"""The Roth window, year by year, and the withdrawal order it implies (B-190, L200-2 §5.3 and §3).

US only. From the year the retirement plan retires (or this year, if later) to the year before the first required
minimum distribution of anyone holding a traditional IRA, each year is laid out in today's money:

  - the income on file that year: each recorded stream once its person reaches its start age (Social Security at the
    claim on file, by the law's table), a stream not linked to inflation losing buying power year by year;
  - how much of Social Security is taxed (IRC §86, whose base amounts are not indexed, so they shrink in today's money);
  - AGI, the standard deduction with its additions at 65 and the deduction for seniors while the law has it (2025 to
    2028, reduced above its line), and so taxable income;
  - the bracket the last dollar falls in, and the room to that bracket's top, and to the tops of the 12%, 22% and 24%
    brackets, each the conversion that would reach it, worked with the Social Security it would draw into tax and the
    deduction it would phase out, and the tax that conversion would add;
  - Medicare's IRMAA tier two years on (premiums in year Y+2 read the return for year Y), for whoever is on Medicare
    then, the line above and the conversion that would cross it;
  - the draw the retirement plan needs from the portfolio that year, and how much of it a traditional IRA could supply
    inside the bracket's room, so the withdrawal order is the room each year, not a fixed taxable-deferred-Roth rule.

Each year's brackets, deductions and IRMAA tiers are the law's for that year (aurea_core/law_figures.py); a year past
the last one on file holds the last in today's money, because the law indexes them to prices, and says so.

It lays the window out and recommends nothing: whether and how much to convert, and from which account to draw, is the
client's with their adviser. ACA premium credits and state tax timing are named as not modelled, with the rest.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.aurea_core import law_figures

FILING_KEYS = {"married_filing_jointly": "mfj", "single": "single"}
TARGET_RATES = (0.12, 0.22, 0.24)
MEDICARE_AGE = 65
LOOKBACK = 2                 # IRMAA in year Y+2 reads the return for year Y
MAX_YEARS = 30
NOT_MODELLED = ["aca_premium_tax_credit", "state_income_tax", "state_tax_timing", "itemized_deductions",
                "tax_exempt_interest_in_magi", "capital_gains_on_taxable_draws", "pension_after_tax_basis",
                "earnings_after_retirement_not_recorded_as_a_stream", "survivor_filing_status", "ira_growth",
                "rmd_years", "irmaa_life_changing_event_appeal"]
DECIDES = "nothing: whether and how much to convert, and which account to draw from, is the client's"


def _on(year: int) -> date:
    return date(year, 7, 1)


def _figure_year(key: str, year: int) -> tuple[int, bool]:
    """The year whose figure applies to `year`, and whether it is held from an earlier year (none on file for it)."""
    f = law_figures.get(key, _on(year))
    if f.status(_on(year)) == "in_force":
        return year, False
    return (f.applies_from.year if f.applies_from else year), True


class YearLaw:
    """One tax year's figures in today's money: the law's for that year, else the last on file held in today's money."""

    def __init__(self, year: int, filing: str, today: date, inflation: float):
        k = FILING_KEYS[filing]
        self.year, self.filing = year, filing
        deflate = lambda y: (1 + inflation) ** max(y - today.year, 0)  # noqa: E731
        by, self.brackets_held = _figure_year(f"us_brackets_{k}", year)
        self.bracket_year = by
        self.brackets = [(b / deflate(by), r) for b, r in law_figures.bands(f"us_brackets_{k}", _on(by))]
        dy, _ = _figure_year(f"us_standard_deduction_{k}", year)
        self.standard = law_figures.value(f"us_standard_deduction_{k}", _on(dy)) / deflate(dy)
        aged_key = "us_aged_addition_married" if filing == "married_filing_jointly" else "us_aged_addition_unmarried"
        ay, _ = _figure_year(aged_key, year)
        self.aged = law_figures.value(aged_key, _on(ay)) / deflate(ay)
        # The deduction for seniors exists only in its years; its amount and line are fixed sums, not indexed.
        senior = law_figures.get("us_senior_deduction")
        self.senior_each = senior.value / deflate(year) if senior.status(_on(year)) == "in_force" else 0.0
        self.senior_line = law_figures.value(f"us_senior_deduction_line_{k}") / deflate(year)
        self.senior_taper = law_figures.value("us_senior_deduction_taper")
        # Social Security's base amounts are fixed in the statute: in today's money they fall each year.
        self.ss_base = law_figures.value(f"us_ss_base_{k}") / deflate(year)
        self.ss_adjusted = law_figures.value(f"us_ss_adjusted_base_{k}") / deflate(year)
        # Medicare's tiers for the premium year two on, read with this year's return.
        py = year + LOOKBACK
        iy, self.irmaa_held = _figure_year(f"us_irmaa_{k}", py)
        self.irmaa_year, self.irmaa_table_year = py, iy
        self.irmaa = [(t[0] / deflate(iy), t[1] / deflate(iy), t[2] / deflate(iy)) for t in law_figures.tiers(f"us_irmaa_{k}", _on(iy))]

    def taxed_ss(self, other: float, ss: float) -> float:
        """IRC §86: the part of the benefits taxed, from provisional income (other income plus half the benefits)."""
        if ss <= 0:
            return 0.0
        prov = other + 0.5 * ss
        if prov <= self.ss_base:
            return 0.0
        if prov <= self.ss_adjusted:
            return min(0.5 * (prov - self.ss_base), 0.5 * ss)
        return min(0.85 * ss, 0.85 * (prov - self.ss_adjusted) + min(0.5 * ss, 0.5 * (self.ss_adjusted - self.ss_base)))

    def tax_on(self, taxable: float) -> float:
        tax, low = 0.0, 0.0
        for top, rate in self.brackets:
            if taxable <= low:
                break
            tax += (min(taxable, top) - low) * rate
            low = top
        return tax

    def work(self, other: float, ss: float, aged: int, extra: float = 0.0) -> dict:
        """The year with `extra` ordinary income (a conversion) added: AGI, deductions, taxable income and tax."""
        o = other + extra
        tss = self.taxed_ss(o, ss)
        agi = o + tss
        # Each person's $6,000 is reduced by 6% of modified AGI over the line (P.L. 119-21 §70103).
        senior = aged * max(self.senior_each - self.senior_taper * max(agi - self.senior_line, 0.0), 0.0)
        deduction = self.standard + self.aged * aged + senior
        taxable = max(agi - deduction, 0.0)
        return {"agi": agi, "taxed_ss": tss, "deduction": deduction, "senior": senior, "taxable": taxable, "tax": self.tax_on(taxable)}

    def bracket_of(self, taxable: float) -> tuple[float, float]:
        return next((top, rate) for top, rate in self.brackets if taxable < top)

    def tier_of(self, magi: float) -> int:
        finite = [t[0] for t in self.irmaa if t[0] != float("inf")]
        for i, bound in enumerate(finite):
            # Every bound is "at or below", except the last finite one: the top tier starts at it.
            if magi < bound or (magi == bound and i < len(finite) - 1):
                return i
        return len(finite)


def _largest(f, limit: float, hi: float) -> float:
    """The largest extra income x >= 0 for which f(x) <= limit; f rises with x."""
    if f(0.0) > limit:
        return 0.0
    if f(hi) <= limit:
        return hi
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if f(mid) <= limit else (lo, mid)
    return lo


def _age(dob: date, year: int) -> int:
    """The age reached in the year, which the law reads (65 by 31 December; Medicare from the month of 65)."""
    return year - dob.year


def lay_out(brain: dict, plan: dict | None, streams: list[dict], *, today: date, inflation: float, base: dict) -> dict:
    """The window, year by year, from the brain (people, IRAs, returns), the retirement plan and the streams on file."""
    from app.aurea_core import rmd, tax_returns

    def not_assessed(reason: str) -> dict:
        return {**base, "status": "not_assessed", "reason": reason, "years": []}

    adults = [p for p in brain.get("persons", []) if not p.get("is_next_gen") and not p.get("deceased_on")]
    if not adults:
        return not_assessed("No adult is on file.")
    no_dob = [p.get("preferred_name") or p["full_name"] for p in adults if not p.get("date_of_birth")]
    if no_dob:
        return not_assessed(f"No date of birth is on file for {', '.join(no_dob)}, so no year's ages can be read.")
    dobs = {p["id"]: date.fromisoformat(str(p["date_of_birth"])[:10]) for p in adults}
    name = {p["id"]: p.get("preferred_name") or p["full_name"] for p in adults}
    tax_of = lambda p: (p.get("profile") or {}).get("tax") or {}  # noqa: E731
    owners = [p for p in adults if (tax_of(p).get("ira_value") or 0) > 0]
    if not owners:
        return not_assessed("No traditional IRA is on file, so there is nothing to convert or draw from one.")
    ira = sum(float(tax_of(p).get("ira_value") or 0) for p in adults)
    roth = sum(float(tax_of(p).get("roth_value") or 0) for p in adults)

    # The filing status: the standing return's, else the one recorded, else assumed from the adults on file.
    ids = {p["id"] for p in adults}
    filed = sorted((r for r in (brain.get("tax_returns") or []) if ids & set(r["person_ids"])), key=lambda r: -r["tax_year"])
    recorded = (((brain.get("household") or {}).get("values") or {}).get("filing_status")
                or next((tax_of(p).get("filing_status") for p in adults if tax_of(p).get("filing_status")), None))
    if filed:
        filing, filing_basis = filed[0]["filing_status"], f"the {filed[0]['tax_year']} return"
    elif recorded:
        filing, filing_basis = recorded, "recorded on file"
    else:
        filing = "married_filing_jointly" if len(adults) >= 2 else "single"
        filing_basis = "assumed from the adults on file"
    label = tax_returns.FILING_STATUSES.get(filing, filing)
    if filing not in FILING_KEYS:
        return not_assessed(f"The filing status on file, {label}, is not one whose brackets are held here; a return "
                            "recorded from Form 1040 gives the status the law knows.")
    if filing == "married_filing_jointly" and len(adults) < 2:
        return not_assessed("A joint return is on file but only one adult is: record the return as filed.")

    # The window: from retirement (or this year) to the year before the first RMD of anyone holding an IRA.
    rmd_years = {p["id"]: dobs[p["id"]].year + rmd.start_age(dobs[p["id"]]) for p in owners}
    first_rmd_id = min(rmd_years, key=rmd_years.get)
    end = rmd_years[first_rmd_id] - 1
    assumed: list[str] = []
    if plan is not None:
        start = today.year + int(plan.get("years_to_retirement") or 0)
        need = float(plan.get("income_need") or 0)
        assumed += [a for a in (plan.get("assumed") or []) if a in ("retirement_age", "annual_income")]
    else:
        start, need = today.year, None
        assumed.append("no_retirement_plan")
    start = max(start, today.year)
    window = {"from_year": start, "to_year": end, "starts": "retirement" if plan is not None and start > today.year else "today",
              "ends": f"the year before {name[first_rmd_id]}'s first RMD, at {rmd.start_age(dobs[first_rmd_id])} in {rmd_years[first_rmd_id]}"}
    base = {**base, "filing_status": filing, "filing_label": label, "filing_basis": filing_basis, "inflation": inflation,
            "ira_balance": round(ira, 2), "roth_balance": round(roth, 2), "window": window, "assumed": assumed,
            "rmd_starts": [{"person": name[i], "year": y, "age": rmd.start_age(dobs[i])} for i, y in sorted(rmd_years.items(), key=lambda x: x[1])]}
    if start > end:
        return {**base, "status": "closed", "reason": f"The window has closed: {window['ends'][len('the year before '):]} has come.",
                "years": []}

    years, keys = [], set()
    k = FILING_KEYS[filing]
    for year in range(start, min(end, start + MAX_YEARS - 1) + 1):
        law = YearLaw(year, filing, today, inflation)
        keys |= {f"us_brackets_{k}", f"us_standard_deduction_{k}", f"us_irmaa_{k}", f"us_ss_base_{k}", f"us_ss_adjusted_base_{k}",
                 "us_aged_addition_married" if k == "mfj" else "us_aged_addition_unmarried"}
        if law.senior_each:
            keys |= {"us_senior_deduction", f"us_senior_deduction_line_{k}", "us_senior_deduction_taper"}
        ages = {name[i]: _age(d, year) for i, d in dobs.items()}
        paid, ss, other = [], 0.0, 0.0
        for s in streams:
            dob = dobs.get(s["person_id"])
            if dob is None or _age(dob, year) < int(s["start_age"]):
                continue
            amount = float(s["planned_annual"]) * (1.0 if s.get("inflation_linked") else 1 / (1 + inflation) ** max(year - today.year, 0))
            paid.append({"name": s["name"], "person": name.get(s["person_id"]), "kind": s["kind"], "amount": round(amount, 2)})
            if s["kind"] == "social_security":
                ss += amount
            else:
                other += amount
        aged = sum(1 for a in ages.values() if a >= MEDICARE_AGE)
        work = lambda x: law.work(other, ss, aged, x)  # noqa: E731
        now = work(0.0)
        top, rate = law.bracket_of(now["taxable"])
        hi = max(ira, 1.0) + 2_000_000
        current = None
        if top != float("inf"):
            amt = _largest(lambda x: work(x)["taxable"], top, hi)
            current = {"rate": rate, "top": round(top), "room": round(amt), "tax": round(work(amt)["tax"] - now["tax"]),
                       "effective_rate": round((work(amt)["tax"] - now["tax"]) / amt, 4) if amt else None}
        to_rate = {}
        for r in TARGET_RATES:
            band_top = next(b for b, br in law.brackets if br == r)
            amt = _largest(lambda x: work(x)["taxable"], band_top, hi)
            to_rate[f"{round(r * 100)}"] = {"top": round(band_top), "room": round(amt), "already_above": now["taxable"] >= band_top,
                                            "tax": round(work(amt)["tax"] - now["tax"])}
        # Medicare two years on: who is on it then, the tier this year's income puts them in, and the line above.
        on_medicare = [n for n, a in ages.items() if a + LOOKBACK >= MEDICARE_AGE]
        tier = law.tier_of(now["agi"])
        line = law.irmaa[tier][0] if tier < len(law.irmaa) - 1 else None
        irmaa = {"premium_year": law.irmaa_year, "table_year": law.irmaa_table_year, "held": law.irmaa_held,
                 "on_medicare": on_medicare, "tier": tier if on_medicare else None,
                 "surcharge": round(12 * (law.irmaa[tier][1] + law.irmaa[tier][2]) * len(on_medicare)) if on_medicare else 0,
                 "next_line": round(line) if line is not None and on_medicare else None,
                 "room": round(_largest(lambda x: work(x)["agi"], line, hi)) if line is not None and on_medicare else None}
        draw = max(need - ss - other, 0.0) if need is not None else None
        years.append({
            "year": year, "ages": ages, "streams": paid, "social_security": round(ss, 2), "other_income": round(other, 2),
            "taxed_social_security": round(now["taxed_ss"], 2), "agi": round(now["agi"], 2),
            "deduction": round(now["deduction"], 2), "senior_deduction": round(now["senior"], 2), "taxable_income": round(now["taxable"], 2),
            "tax": round(now["tax"], 2), "bracket": rate, "bracket_year": law.bracket_year, "brackets_held": law.brackets_held,
            "room_in_bracket": current, "room_to": to_rate, "irmaa": irmaa,
            # The conversion to the bracket's top would raise Medicare premiums two years on.
            "bracket_room_crosses_irmaa": bool(current and irmaa["room"] is not None and current["room"] > irmaa["room"]),
            "draw": round(draw, 2) if draw is not None else None,
            # The withdrawal order is the room: what a traditional draw could carry inside this bracket, then the rest.
            "draw_within_room": round(min(draw, current["room"]), 2) if draw is not None and current else None,
            "draw_beyond_room": round(max(draw - current["room"], 0.0), 2) if draw is not None and current else draw,
        })
    held = sorted({y["bracket_year"] for y in years if y["brackets_held"]} | {y["irmaa"]["table_year"] for y in years if y["irmaa"]["held"]})
    if held:
        assumed.append(f"figures after {held[-1]} are {held[-1]}'s, held in today's money as the law indexes them")
    rooms = [y["room_in_bracket"]["room"] for y in years if y["room_in_bracket"]]
    return {**base, "status": "assessed", "years": years,
            "totals": {"room_in_brackets": round(sum(rooms)), "years": len(years), "ira_balance": round(ira),
                       "years_crossing_irmaa": sum(1 for y in years if y["bracket_room_crosses_irmaa"])},
            "law": law_figures.cited(tuple(sorted(keys)), today)}


async def for_household(session, household_id: uuid.UUID, *, firm_id: uuid.UUID, plan: dict | None = None) -> dict:
    """The window for one household, from what is on file. `plan` is the retirement plan, when the caller has it."""
    from app.aurea_core import financial_plan, firm_time, markets, planning, retirement
    from app.aurea_core.graph import household_brain
    from app.models.graph import Household
    from app.models.tenant import Firm

    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ValueError("Household not found")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    base = {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "currency": firm.base_currency,
            "money_basis": "today", "not_modelled": NOT_MODELLED, "decides": DECIDES, "estimate": True}
    market = markets.of_household(firm, household.values)
    if market is None or market.code != "US":
        return {**base, "status": "not_assessed", "reason": "The Roth window is for a household taxed in the US.", "years": []}
    brain = await household_brain(session, household_id, firm_id=firm_id)
    streams = await financial_plan.streams_of(session, household_id, firm, today)
    if plan is None:
        plan = await retirement.for_household(session, household_id, firm_id=firm_id)
    inflation = float((((brain.get("planning") or {}).get("inflation")) or {}).get("cpi", 0.025))
    return lay_out(brain, plan, streams, today=today, inflation=inflation, base={**base, "assumptions_version": planning.version_of(brain)})
