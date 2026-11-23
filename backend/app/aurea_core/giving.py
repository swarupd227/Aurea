"""Giving, laid out as figures (B-192, L200-2 §5.4). US only; it recommends nothing.

Three ways a US household can give, each from what is on file and the law (aurea_core/law_figures.py):

  - From an IRA (a qualified charitable distribution): who may (an owner past 70 and a half, from the day they reach
    it), this year's limit and next year's where the law has set it, the one-time split-interest amount inside it, and
    how much of this year's required minimum distribution it could satisfy. The IRA value is the one on the profile.
  - Stock: the lots in accounts the household owns directly that have been held more than a year, largest gain first.
    Given to a charity, such a lot is deducted at its value and its gain is never realised; the deduction is capped at
    30% of AGI, read from a recorded return. A lot held a year or less is deducted at cost, so it is counted, not listed.
  - A donor-advised fund bunching year: the yearly giving (as said in the conversation, never worked out) put into one
    year for one to five years, against giving each year: the deductions over those years and the difference, with the
    0.5% AGI floor, the 60% cash limit, the non-itemizer deduction (which a donor-advised fund does not get) and the
    35% cap on an itemized deduction's worth. It needs a recorded return for the AGI and filing status.

What the client chooses, or declines, is recorded on the advice file (B-175), from the conversation.
"""
from __future__ import annotations

import uuid
from datetime import date

from app.aurea_core import law_figures

OWN_ACCOUNTS = frozenset({"individual", "joint_jtwros", "joint_tic", "joint_tbe", "community_property", "trust"})
LONG_HELD_DAYS = 365
TOP_LOTS = 8
BUNCH_YEARS = (1, 2, 3, 4, 5)
NOT_MODELLED = ["state_tax", "agi_changes_across_years", "carryforward_of_gifts_over_the_agi_limits", "private_foundations",
                "gifts_of_property_other_than_listed_stock", "ltcg_tax_on_the_gain_avoided", "charitable_trusts_beyond_the_qcd"]
DECIDES = "nothing: what to give, how and when is the client's"


def _half_year_on(dob: date, age: float) -> date:
    years, half = int(age), (age % 1) >= 0.5
    month = dob.month + (6 if half else 0)
    year = dob.year + years + (month - 1) // 12
    month = (month - 1) % 12 + 1
    day = min(dob.day, 28) if month == 2 else min(dob.day, 30) if month in (4, 6, 9, 11) else dob.day
    return date(year, month, day)


def qcd(brain: dict, *, today: date) -> dict:
    from app.aurea_core import rmd

    age_line = law_figures.value("us_qcd_age")
    limit = law_figures.value("us_qcd_limit", today)
    split = law_figures.value("us_qcd_split_interest", today)
    nxt = date(today.year + 1, 7, 1)
    next_limit = law_figures.value("us_qcd_limit", nxt) if law_figures.get("us_qcd_limit", nxt).status(nxt) == "in_force" else None
    rows, not_assessed = [], []
    for p in brain.get("persons", []):
        if p.get("is_next_gen") or p.get("deceased_on"):
            continue
        ira = float(((p.get("profile") or {}).get("tax") or {}).get("ira_value") or 0)
        if not ira:
            continue
        name = p.get("preferred_name") or p["full_name"]
        if not p.get("date_of_birth"):
            not_assessed.append(f"{name}: no date of birth on file")
            continue
        dob = date.fromisoformat(str(p["date_of_birth"])[:10])
        from_day = _half_year_on(dob, age_line)
        age = rmd.age_in_year(dob, today.year)
        due = ira / rmd.divisor(age) if age >= rmd.start_age(dob) and rmd.divisor(age) else None
        rows.append({"person": name, "ira_balance": round(ira, 2), "balance_source": "profile", "eligible_from": from_day.isoformat(),
                     "eligible_now": today >= from_day, "eligible_this_year": from_day.year <= today.year,
                     "limit": limit, "split_interest_once": split, "next_year_limit": next_limit,
                     "room": round(min(limit, ira), 2), "rmd_this_year": round(due, 2) if due else None,
                     "satisfies_rmd_up_to": round(min(limit, due, ira), 2) if due else None})
    return {"tax_year": today.year, "people": rows, "not_assessed": not_assessed,
            "law": law_figures.cited(("us_qcd_age", "us_qcd_limit", "us_qcd_split_interest"), today)}


def stock(lots: list[dict], registrations: dict[str, str], agi: float | None, *, today: date) -> dict:
    own = [l for l in lots if registrations.get(l.get("account_id")) in OWN_ACCOUNTS]
    gains = [l for l in own if l["current_price"] > l["cost_per_unit"]]
    long_held = [l for l in gains if l["holding_days"] > LONG_HELD_DAYS]
    rows = sorted(({"symbol": l["symbol"], "instrument": l["instrument_name"], "account": l["account_name"],
                    "quantity": l["quantity"], "value": round(l["quantity"] * l["current_price"], 2),
                    "gain": round(l["quantity"] * (l["current_price"] - l["cost_per_unit"]), 2),
                    "gain_share": round(1 - l["cost_per_unit"] / l["current_price"], 4) if l["current_price"] else None,
                    "held_days": l["holding_days"]} for l in long_held), key=lambda r: -r["gain"])
    cap = law_figures.value("us_charity_stock_limit")
    return {"lots": rows[:TOP_LOTS], "long_held_with_gain": len(long_held), "short_held_with_gain": len(gains) - len(long_held),
            "not_own_accounts": len(lots) - len(own),
            "deduction_cap": round(cap * agi, 2) if agi is not None else None, "cap_basis": "the return's AGI" if agi is not None else None,
            "law": law_figures.cited(("us_charity_stock_limit",), today)}


def bunching(brain: dict, *, today: date, annual_giving: float | None, other_itemized: float | None) -> dict:
    """One to five years of giving put into a donor-advised fund in one year, against giving each year, this year's law
    and the AGI held flat."""
    from app.aurea_core import roth_window

    if not annual_giving or annual_giving <= 0:
        return {"status": "not_assessed", "reason": "No yearly giving was given: say how much the household gives a year.", "rows": []}
    adults = [p for p in brain.get("persons", []) if not p.get("is_next_gen") and not p.get("deceased_on")]
    ids = {p["id"] for p in adults}
    filed = sorted((r for r in (brain.get("tax_returns") or []) if ids & set(r["person_ids"]) and r.get("agi") is not None),
                   key=lambda r: -r["tax_year"])
    if not filed:
        return {"status": "not_assessed", "reason": "No return with an AGI is on file: the floor and the limits read it.", "rows": []}
    ret = filed[0]
    filing = ret["filing_status"]
    if filing not in roth_window.FILING_KEYS:
        return {"status": "not_assessed", "reason": f"The return's filing status, {filing}, is not one whose figures are held here.", "rows": []}
    k = roth_window.FILING_KEYS[filing]
    agi = float(ret["agi"])
    law = roth_window.YearLaw(today.year, filing, today, 0.0)
    aged = sum(1 for p in adults if p.get("date_of_birth") and today.year - int(str(p["date_of_birth"])[:4]) >= 65)
    standard = law.standard + law.aged * aged
    floor = law_figures.value("us_charity_floor") * agi
    cash_cap = law_figures.value("us_charity_cash_limit") * agi
    nonitem = law_figures.value(f"us_nonitemizer_charity_{k}")
    other = float(other_itemized or 0.0)
    rate = float(ret["marginal_rate"]) if ret.get("marginal_rate") is not None else \
        law.bracket_of(float(ret["taxable_income"]))[1] if ret.get("taxable_income") is not None else None
    worth = min(rate, law_figures.value("us_itemized_value_cap")) if rate is not None else None

    itemized_each = other + max(min(annual_giving, cash_cap) - floor, 0.0)
    standard_each = standard + min(annual_giving, nonitem)        # cash to a public charity, not a fund
    each = max(itemized_each, standard_each)
    rows = []
    for n in BUNCH_YEARS:
        put_in = annual_giving * n
        itemized_once = other + max(min(put_in, cash_cap) - floor, 0.0)
        first = max(itemized_once, standard)                        # a fund's gift gets no non-itemizer deduction
        total = first + (n - 1) * standard                          # the fund grants in the years after; nothing new to deduct
        extra = total - n * each
        rows.append({"years": n, "put_in": round(put_in, 2), "itemizes_in_first_year": itemized_once > standard,
                     "first_year_deduction": round(first, 2), "deductions_bunched": round(total, 2),
                     "deductions_each_year": round(n * each, 2), "extra_deduction": round(extra, 2),
                     "over_cash_limit": put_in > cash_cap,
                     "worth": round(extra * worth, 2) if worth is not None else None})
    assumed = ["the yearly giving as given in the conversation, in cash, to public charities",
               "the AGI held at the return's for every year", f"other itemized deductions of {other:,.0f}" if other_itemized is not None
               else "no other itemized deductions, none given"]
    return {"status": "assessed", "rows": rows, "annual_giving": annual_giving, "other_itemized": other,
            "agi": agi, "return_year": ret["tax_year"], "filing_status": filing, "standard_with_aged": round(standard, 2),
            "floor": round(floor, 2), "cash_limit": round(cash_cap, 2), "nonitemizer_deduction": nonitem,
            "giving_each_year_itemizes": itemized_each > standard_each, "rate": rate, "deduction_worth_rate": worth,
            "assumed": assumed,
            "law": law_figures.cited((f"us_standard_deduction_{k}", "us_charity_floor", "us_charity_cash_limit",
                                      f"us_nonitemizer_charity_{k}", "us_itemized_value_cap"), today)}


async def for_household(session, household_id: uuid.UUID, *, firm_id: uuid.UUID, annual_giving: float | None = None,
                        other_itemized: float | None = None) -> dict:
    from sqlalchemy import select

    from app.aurea_core import firm_time, markets, tax_intelligence
    from app.aurea_core.graph import household_brain
    from app.models.graph import Account, Household
    from app.models.tenant import Firm

    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ValueError("Household not found")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    base = {"household_id": str(household.id), "household": household.name, "as_of": today.isoformat(), "currency": firm.base_currency,
            "decides": DECIDES, "not_modelled": NOT_MODELLED, "estimate": True,
            "advice_file": "what the client chooses or declines is recorded with record_recommendation (category tax)"}
    market = markets.of_household(firm, household.values)
    if market is None or market.code != "US":
        return {**base, "status": "not_assessed", "reason": "Giving is laid out for a household taxed in the US."}
    brain = await household_brain(session, household_id, firm_id=firm_id)
    lots = await tax_intelligence._load_lots(session, brain, today=today)
    ids = [uuid.UUID(a["id"]) for a in brain.get("accounts", [])]
    regs = {str(a.id): a.registration_type for a in (await session.execute(select(Account).where(Account.id.in_(ids)))).scalars().all()} if ids else {}
    funds = [a for a in brain.get("accounts", []) if regs.get(a["id"]) == "daf"]
    adults = {p["id"] for p in brain.get("persons", []) if not p.get("is_next_gen")}
    filed = sorted((r for r in (brain.get("tax_returns") or []) if adults & set(r["person_ids"]) and r.get("agi") is not None),
                   key=lambda r: -r["tax_year"])
    agi = float(filed[0]["agi"]) if filed else None
    return {**base, "status": "assessed", "qcd": qcd(brain, today=today), "stock": stock(lots, regs, agi, today=today),
            "bunching": bunching(brain, today=today, annual_giving=annual_giving, other_itemized=other_itemized),
            "donor_advised_funds_on_file": [a["name"] for a in funds]}
