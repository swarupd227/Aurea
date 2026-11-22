"""Social Security for a couple, the law's arithmetic laid out (B-184).

US only, from each person's statement (the benefit at full retirement age, recorded as an income stream). For each
pair of claim ages (62, full retirement age and 70 for each, and the ages on file) it lays out:

  - each person's own benefit at their claim age, by the law's table (`financial_plan.claim_factor`);
  - the spousal benefit: up to half the other's full-age benefit, less their own, from when both have filed, cut for
    each month it starts before the person's full retirement age (25/36 of 1% a month for 36 months, 5/12 of 1% after)
    and never raised for waiting;
  - the survivor's benefit: the larger of their own and what the one who died was paid, at least 82.5% of the one who
    died's full-age benefit;
  - the total paid to both plan-to ages, in today's money (benefits rise with prices), and discounted at the assumption
    set's rate;
  - the bridge: what the portfolio carries from retirement until each claim, and the retirement plan's likelihood with
    those claims (the retirement engine nets each year's need by that year's income, B-169).

It decides nothing: when to claim is the client's. Benefits are in today's money; tax on benefits, the earnings test,
a survivor claiming before their survivor full retirement age, and a divorced spouse's benefit are not modelled, and
it says so. A person with no statement on file is not assessed, which does not mean they have no record.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Household, Person
from app.models.tenant import Firm

SURVIVOR_FLOOR = 0.825          # the survivor's benefit is at least this share of the deceased's full-age benefit
SPOUSAL_SHARE = 0.5
NOT_MODELLED = ["tax_on_benefits", "earnings_test", "survivor_claimed_before_survivor_full_age", "divorced_spouse_benefit",
                "family_maximum"]


def spousal_factor(full_months: int, start_months: int) -> float:
    """What a spousal benefit pays against its full amount: 25/36 of 1% a month for the first 36 months early, 5/12 of
    1% after; nothing more for waiting past full retirement age."""
    early = max(full_months - start_months, 0)
    return 1 - min(early, 36) * 25 / 3600 - max(early - 36, 0) * 5 / 1200


def _person(stream: dict, person: Person, today: date) -> dict:
    from app.aurea_core.financial_plan import full_retirement_months

    dob = person.date_of_birth
    return {"id": str(person.id), "name": person.preferred_name or person.full_name, "birth_year": dob.year,
            "age": today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day)),
            "pia": float(stream["annual"]), "full_months": full_retirement_months(dob.year),
            "claim_on_file": int(stream["start_age"]), "started": bool(stream.get("started")), "document": stream.get("document")}


def benefits(a: dict, b: dict | None, claim_a: int, claim_b: int | None) -> dict:
    """Each person's yearly benefit once both have filed (own plus any spousal excess), and the survivor's if either
    dies first. Today's money."""
    from app.aurea_core.financial_plan import claim_factor

    own_a = a["pia"] * claim_factor(a["full_months"], claim_a * 12)
    if b is None:
        return {"own": {a["id"]: round(own_a, 2)}, "spousal": {a["id"]: 0.0}, "both": round(own_a, 2), "survivor": {}}
    own_b = b["pia"] * claim_factor(b["full_months"], claim_b * 12)

    def spousal(x, y, claim_x, claim_y):
        excess = max(SPOUSAL_SHARE * y["pia"] - x["pia"], 0.0)
        if not excess:
            return 0.0
        # It starts once both have filed: at the later of x's claim and x's age when y files.
        x_age_when_y_files = claim_y + (y["birth_year"] - x["birth_year"])
        starts = max(claim_x, x_age_when_y_files)
        return excess * spousal_factor(x["full_months"], starts * 12)

    sp_a, sp_b = spousal(a, b, claim_a, claim_b), spousal(b, a, claim_b, claim_a)
    paid_a, paid_b = own_a + sp_a, own_b + sp_b
    # The survivor keeps the larger of their own and what the one who died was paid, at least 82.5% of the latter's PIA.
    survivor_if_a_dies = max(paid_b, own_a, SURVIVOR_FLOOR * a["pia"])
    survivor_if_b_dies = max(paid_a, own_b, SURVIVOR_FLOOR * b["pia"])
    return {"own": {a["id"]: round(own_a, 2), b["id"]: round(own_b, 2)},
            "spousal": {a["id"]: round(sp_a, 2), b["id"]: round(sp_b, 2)},
            "both": round(paid_a + paid_b, 2),
            "survivor": {f"if_{a['id']}_dies": round(survivor_if_a_dies, 2), f"if_{b['id']}_dies": round(survivor_if_b_dies, 2)}}


def lifetime(a: dict, b: dict | None, claim_a: int, claim_b: int | None, plan_to: int, rate: float) -> dict:
    """Year by year from today until both reach the plan-to age: what is paid, in today's money and discounted."""
    from app.aurea_core.financial_plan import claim_factor

    total = pv = 0.0
    paid = benefits(a, b, claim_a, claim_b)
    for t in range(0, 120):
        age_a = a["age"] + t
        age_b = b["age"] + t if b else None
        alive_a, alive_b = age_a < plan_to, (b is not None and age_b < plan_to)
        if not alive_a and not alive_b:
            break
        year = 0.0
        if b is None:
            year = paid["own"][a["id"]] if age_a >= claim_a else 0.0
        elif alive_a and alive_b:
            year += (paid["own"][a["id"]] if age_a >= claim_a else 0.0)
            year += (paid["own"][b["id"]] if age_b >= claim_b else 0.0)
            if age_a >= claim_a and age_b >= claim_b:
                year += paid["spousal"][a["id"]] + paid["spousal"][b["id"]]
        else:
            survivor, died = (b, a) if alive_b else (a, b)
            s_age = age_b if alive_b else age_a
            s_claim = claim_b if alive_b else claim_a
            own = survivor["pia"] * claim_factor(survivor["full_months"], s_claim * 12) if s_age >= s_claim else 0.0
            # A survivor's benefit can start at 60; before then the survivor's own, if they have claimed.
            year = paid["survivor"][f"if_{died['id']}_dies"] if s_age >= 60 else own
        total += year
        pv += year / (1 + rate) ** (t + 1)
    return {"total_today": round(total), "pv": round(pv)}


async def couple(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, run_plans: bool = True) -> dict:
    from app.aurea_core import firm_time, markets, planning, retirement
    from app.aurea_core.financial_plan import streams_of
    from app.aurea_core.graph import household_brain

    firm = await session.get(Firm, firm_id)
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ValueError("That household is not one of this firm's.")
    base = {"household_id": str(household_id), "household": household.name, "estimate": True, "not_modelled": NOT_MODELLED,
            "money_basis": "today", "decides": "nothing: when to claim is the client's"}
    market = markets.of_household(firm, household.values)
    if not market or market.code != "US":
        return {**base, "status": "not_assessed", "reason": "Social Security is for a household taxed in the US.", "pairs": []}
    today = firm_time.today(firm)
    people = {str(p.id): p for p in (await session.execute(select(Person).where(
        Person.household_id == household_id, Person.deceased_on.is_(None), Person.is_next_gen.is_(False)))).scalars().all()}
    ss = [s for s in await streams_of(session, household_id, firm, today) if s["kind"] == "social_security"]
    on_file = [(_person(s, people[s["person_id"]], today)) for s in ss
               if s["person_id"] in people and people[s["person_id"]].date_of_birth]
    seen, persons = set(), []
    for p in on_file:
        if p["id"] not in seen:
            persons.append(p)
            seen.add(p["id"])
    without = [p.preferred_name or p.full_name for pid, p in people.items() if pid not in seen]
    if not persons:
        return {**base, "status": "not_assessed", "reason": "No Social Security statement with a date of birth is on file.",
                "without_statement": without, "pairs": []}
    persons = sorted(persons, key=lambda p: -p["age"])[:2]
    a, b = persons[0], (persons[1] if len(persons) > 1 else None)
    brain = await household_brain(session, household_id, firm_id=firm_id)
    plan_to = int(planning.default_of(brain, "plan_to_age", planning.PLATFORM_PLAN_TO_AGE))
    rate, whose = planning.discount_of(brain)

    def ages(p):
        """62, full retirement age and 70, and the age on file; from today on, and only the one on file once paid."""
        if p["started"] or p["age"] >= 70:
            return [p["claim_on_file"]]
        full = p["full_months"] // 12
        return sorted(o for o in {max(62, p["age"]), max(full, p["age"]), 70, max(p["claim_on_file"], p["age"])} if 62 <= o <= 70)

    combos = [(x, y) for x in ages(a) for y in (ages(b) if b else [None])]
    pairs = []
    for x, y in combos:
        paid = benefits(a, b, x, y)
        life = lifetime(a, b, x, y, plan_to, rate)
        row = {"claim": {a["id"]: x, **({b["id"]: y} if b else {})}, "own": paid["own"], "spousal": paid["spousal"],
               "both": paid["both"], "survivor": paid["survivor"], "lifetime_today": life["total_today"], "lifetime_pv": life["pv"],
               "on_file": x == a["claim_on_file"] and (b is None or y == b["claim_on_file"])}
        if run_plans:
            plan = await retirement.for_household(session, household_id, firm_id=firm_id,
                                                  overrides={"claim_ages": row["claim"]})
            if plan:
                retire_age = int(plan["retirement_age"])     # in the eldest's ages
                bridge = 0.0
                for p in (a, b) if b else (a,):
                    at_retirement = retire_age - (a["age"] - p["age"])
                    years = max(row["claim"][p["id"]] - max(at_retirement, p["age"]), 0)
                    bridge += years * (paid["own"][p["id"]] + paid["spousal"][p["id"]])
                row["bridge_today"] = round(bridge)
                row["retirement_success"] = plan["success_probability"]
        pairs.append(row)
    return {**base, "status": "assessed" if b else "one_statement",
            "people": [{k: p[k] for k in ("id", "name", "age", "pia", "claim_on_file", "document")}
                       | {"full_retirement_age": f"{p['full_months'] // 12}" + (f" and {p['full_months'] % 12} months" if p["full_months"] % 12 else "")}
                       for p in (a, b) if p],
            "without_statement": without, "plan_to_age": plan_to, "discount_rate": rate,
            "discount_whose": "firm" if whose == "firm" else "cash_return",
            "assumed": ["both live to the plan-to age"] + ([] if b else ["spousal and survivor not assessed: one statement on file"]),
            "pairs": pairs}
