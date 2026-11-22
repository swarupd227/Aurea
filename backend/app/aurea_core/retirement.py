"""Retirement income & decumulation engine (spec §6.5 — the 'will my money last?' question).

Pure-numpy, deterministic. Two-phase Monte-Carlo: accumulation (contributions until retirement)
then decumulation (each year's need, grown with inflation, less the guaranteed income paid that year,
drawn until the plan-to age). On top of the base projection it solves for the sustainable income at a
target success rate, stresses the plan with a sequence-of-returns shock in the first year of retirement
(the risk that dominates early decumulation), and evaluates the levers an adviser actually pulls —
retire later, trim spending, take more growth risk.

Returns come from the firm's capital market assumptions when it has set them (B-167). A guaranteed stream
that is not linked to inflation is paid at its stated amount, so it buys less each year; one that starts
after retirement pays nothing until it starts, and the portfolio bridges the gap (B-169). A couple's plan
runs until the younger adult reaches the plan-to age, and says whose ages it counts in (B-173).

Beside the first-year crash, one deterministic sequence of returns and its reverse run on the household's own pot
and draw, labelled an illustration (B-195). The plan carries how old its inputs are and what it reads that is not
on file (`plan_inputs`, B-182).

No I/O here; `for_household` (below) assembles the inputs from the client brain."""
from __future__ import annotations

from statistics import NormalDist

import numpy as np

from app.aurea_core.planning import STRESS_SCENARIOS, _blended_assumptions

# What the projection stands in for when nothing is on file; each is named in the plan's `assumed`.
ASSUMED_AGE = 54
ASSUMED_LONGEVITY = 95    # the plan-to age, for the last survivor, when none is on file
ASSUMED_FUNDING_SHARE = 0.7


def _draws(need_today: float, streams: list[dict], *, acc: int, dec: int, inflation: float) -> list[float]:
    """Each retirement year's draw on the portfolio, in that year's money: the need grown with inflation, less
    the guaranteed income paid that year. A stream linked to inflation keeps its buying power; a level one is
    paid at its stated amount and so falls in real terms; one not started yet pays nothing, and the portfolio
    bridges until it does. `starts_in` is the years from today until a stream is paid."""
    out = []
    for y in range(dec):
        t = acc + y
        grow = (1 + inflation) ** t
        paid = sum(s["annual"] * (grow if s.get("inflation_linked") else 1.0) for s in streams if t >= s["starts_in"])
        out.append(max(need_today * grow - paid, 0.0))
    return out


def _simulate(
    current_value: float, mu: float, sigma: float, *, acc_years: int, draws: list[float],
    annual_contribution: float, sims: int, seed: int, first_year_shock: float | None = None,
) -> dict:
    rng = np.random.default_rng(seed)
    paths = np.full(sims, float(current_value))
    for _ in range(acc_years):
        shocks = rng.normal(mu, sigma, sims)
        paths = np.maximum(paths * (1 + shocks) + annual_contribution, 0.0)
    at_retirement = paths.copy()

    depletion = np.full(sims, -1)  # year index of depletion in retirement; -1 = survived
    balance: list[tuple[int, float, float, float]] = []
    for y, withdrawal in enumerate(draws):
        if first_year_shock is not None and y == 0:
            shocks = np.full(sims, first_year_shock)
        else:
            shocks = rng.normal(mu, sigma, sims)
        paths = paths * (1 + shocks) - withdrawal
        newly = (paths <= 0) & (depletion < 0)
        depletion = np.where(newly, y, depletion)
        paths = np.maximum(paths, 0.0)
        p10, med, p90 = np.percentile(paths, [10, 50, 90])
        balance.append((y, float(p10), float(med), float(p90)))

    return {
        "success": float(np.mean(paths > 0)),
        "end": paths, "at_retirement": at_retirement, "depletion": depletion, "balance": balance,
    }


def illustration_returns(mu: float, sigma: float, years: int) -> list[float]:
    """One deterministic return for each retirement year, weakest first: the plan's own expected return and
    volatility, spread at evenly spaced points of the normal curve, so their average is the expected return."""
    n = max(int(years), 1)
    curve = NormalDist()
    return [mu + sigma * curve.inv_cdf((k + 0.5) / n) for k in range(n)]


def sequence_illustration(pot: float, draws: list[float], *, mu: float, sigma: float, retirement_age: int,
                          deflator: float = 1.0) -> dict:
    """The same returns run on the household's pot and draw twice, weakest years first and then reversed (B-195).
    An illustration of order alone, beside the first-year crash: no randomness, and not a forecast. `pot` and
    `draws` are in the money of their year; the ending balance is also given in today's money by `deflator`."""
    returns = illustration_returns(mu, sigma, len(draws))
    orders = []
    for order, path in (("weak_first", returns), ("strong_first", list(reversed(returns)))):
        balance, depletes_at = float(pot), None
        for y, (r, draw) in enumerate(zip(path, draws)):
            balance = balance * (1 + r) - draw
            if balance <= 0:
                balance, depletes_at = 0.0, retirement_age + y + 1
                break
        orders.append({"order": order, "first_year_return": round(path[0], 4), "depletes_at": depletes_at,
                       "ending_balance": round(balance), "ending_balance_today": round(balance / (deflator or 1.0))})
    return {"illustration": True, "basis": "plan_return_and_volatility", "pot": round(pot), "years": len(draws),
            "average_return": round(sum(returns) / len(returns), 4), "orders": orders}


def _shift_to_growth(allocation: dict[str, float], frac: float = 0.10) -> dict[str, float]:
    """Move `frac` of the portfolio from defensive (cash/fixed income) into equity."""
    total = sum(allocation.values()) or 1.0
    out = dict(allocation)
    move = total * frac
    for defensive in ("cash", "fixed_income"):
        take = min(out.get(defensive, 0.0), move)
        out[defensive] = out.get(defensive, 0.0) - take
        out["equity"] = out.get("equity", 0.0) + take
        move -= take
        if move <= 0:
            break
    return out


def retirement_plan(
    *,
    current_value: float,
    allocation: dict[str, float],
    current_age: int,
    retirement_age: int,
    longevity_age: int,
    annual_income: float,
    annual_contribution: float = 0.0,
    inflation: float = 0.025,
    target_success: float = 0.85,
    sims: int = 2500,
    seed: int = 7,
    streams: list[dict] | None = None,
    cma: dict | None = None,
) -> dict:
    """Full retirement projection + sustainable income + sequence risk + levers.

    `annual_income` is the income needed in retirement, in today's money, before guaranteed income; `streams`
    are that income ({annual, starts_in, inflation_linked}), netted year by year. `cma` is the firm's capital
    market assumptions (None for the platform's). Figures named `_today`, `income_target`, `sustainable_income`,
    `income_gap` and `guaranteed_at_retirement` are in today's money; the rest are in the money of their year."""
    streams = streams or []
    mu, sigma = _blended_assumptions(allocation, cma=cma)
    acc = max(0, int(retirement_age - current_age))
    dec = max(1, int(longevity_age - retirement_age))
    infl_to_ret = (1 + inflation) ** acc

    def draws_for(need: float, acc_years: int = acc, dec_years: int = dec) -> list[float]:
        return _draws(need, streams, acc=acc_years, dec=dec_years, inflation=inflation)

    draws = draws_for(annual_income)
    # Guaranteed income paid in the first year of retirement, in today's money: a level stream has lost the
    # inflation between now and then.
    guaranteed_at_ret = sum(s["annual"] * (1.0 if s.get("inflation_linked") else 1 / infl_to_ret)
                            for s in streams if s["starts_in"] <= acc)
    income_target = draws[0] / infl_to_ret
    income_at_ret = draws[0]

    base = _simulate(current_value, mu, sigma, acc_years=acc, draws=draws,
                     annual_contribution=annual_contribution, sims=sims, seed=seed)

    failed = base["depletion"][base["depletion"] >= 0]
    median_depletion_age = int(retirement_age + np.median(failed) + 1) if failed.size else None
    proj_at_ret = float(np.median(base["at_retirement"]))

    # Sustainable income (today's money) achieving target_success — binary search over the need, reported as
    # the first year's draw on the portfolio so it reads beside `income_target`.
    def success_for_income(need_today: float, alloc_mu=mu, alloc_sigma=sigma) -> float:
        return _simulate(current_value, alloc_mu, alloc_sigma, acc_years=acc, draws=draws_for(need_today),
                         annual_contribution=annual_contribution, sims=1200, seed=seed)["success"]

    lo, hi = 0.0, max(annual_income * 2.5, proj_at_ret * 0.09, 1.0) + sum(s["annual"] for s in streams)
    for _ in range(16):
        mid = (lo + hi) / 2
        if success_for_income(mid) >= target_success:
            lo = mid
        else:
            hi = mid
    sustainable = max(lo - guaranteed_at_ret, 0.0)

    # Sequence-of-returns stress: a GFC-style crash in the first year of retirement.
    gfc = STRESS_SCENARIOS["gfc_2008"]
    tot = sum(allocation.values()) or 1.0
    crash_return = sum((v / tot) * gfc.get(c, 0.0) for c, v in allocation.items())
    seq = _simulate(current_value, mu, sigma, acc_years=acc, draws=draws,
                    annual_contribution=annual_contribution, sims=sims, seed=seed,
                    first_year_shock=crash_return)
    illustration = sequence_illustration(proj_at_ret, draws, mu=mu, sigma=sigma, retirement_age=retirement_age,
                                         deflator=(1 + inflation) ** (acc + dec))

    # Levers the adviser can pull.
    levers = []
    late = min(retirement_age + 3, longevity_age - 1)
    if late > retirement_age:
        late_acc = max(0, late - current_age)
        s_late = _simulate(current_value, mu, sigma, acc_years=late_acc,
                           draws=draws_for(annual_income, late_acc, max(1, longevity_age - late)),
                           annual_contribution=annual_contribution, sims=1500, seed=seed)["success"]
        levers.append({"key": "retire_later", "label": f"Retire at {late} (+{late - retirement_age}y)",
                       "success": round(s_late, 3)})
    levers.append({"key": "trim_income", "label": "Reduce income 10%",
                   "success": round(success_for_income(annual_income * 0.9), 3)})
    growth = _shift_to_growth(allocation, 0.10)
    gmu, gsigma = _blended_assumptions(growth, cma=cma)
    levers.append({"key": "more_growth", "label": "Shift +10% to growth assets",
                   "success": round(success_for_income(annual_income, gmu, gsigma), 3)})

    balance_by_age = [{"age": retirement_age + y + 1, "p10": round(p10), "median": round(med),
                       "p90": round(p90)} for (y, p10, med, p90) in base["balance"]]

    return {
        "current_age": current_age, "retirement_age": retirement_age, "longevity_age": longevity_age,
        "years_to_retirement": acc, "years_in_retirement": dec,
        "income_target": round(income_target), "income_at_retirement": round(income_at_ret),
        "guaranteed_at_retirement": round(guaranteed_at_ret, 2),
        "current_value": round(current_value), "projected_at_retirement": round(proj_at_ret),
        "projected_at_retirement_today": round(proj_at_ret / infl_to_ret),
        "success_probability": round(base["success"], 3),
        "on_track": base["success"] >= target_success,
        "target_success": target_success,
        "median_depletion_age": median_depletion_age,
        "sustainable_income": round(sustainable),
        "income_gap": round(income_target - sustainable),
        "sequence_risk": {
            "baseline": round(base["success"], 3),
            "early_crash": round(seq["success"], 3),
            "delta": round(base["success"] - seq["success"], 3),
            "crash_return": round(crash_return, 3),
            "illustration": illustration,
        },
        "levers": levers,
        "balance_by_age": balance_by_age,
        "assumptions": {
            "expected_return": round(mu, 4), "volatility": round(sigma, 4),
            "inflation": inflation, "target_success": target_success,
        },
    }


async def for_household(
    session, household_id, *, firm_id, overrides: dict | None = None
) -> dict | None:
    """Assemble retirement inputs from the client brain (with sensible defaults) and project."""
    from app.aurea_core.graph import household_brain  # local import avoids a cycle

    brain = await household_brain(session, household_id, firm_id=firm_id)
    if not brain:
        return None
    overrides = overrides or {}

    total = brain["totals"]["total_value"] or 0.0
    allocation = {k: v for k, v in brain["totals"]["by_asset_class"].items() if v}

    # Find the retirement goal (if any) to seed the inputs.
    goals = brain.get("goals", [])
    rgoal = next((g for g in goals if g.get("kind") == "retirement"), None)
    a = (rgoal or {}).get("assumptions") or {}

    # Every input is what is on file, else a stated default, and each default is named in `assumed`, so a
    # projection never reads as more than it is. The goal's own horizon is goal_tradeoff.horizon's, the one
    # the goals page reads.
    from app.aurea_core import firm_time, goal_tradeoff, planning
    from app.models.tenant import Firm

    today = firm_time.today(await session.get(Firm, firm_id))
    assumed: list[str] = []
    # The adults with a date of birth on file, eldest first. The plan counts in the eldest's ages and runs until
    # the youngest reaches the plan-to age, so a younger spouse's last years are funded too.
    adults = sorted(((age, p.get("full_name")) for p in brain.get("persons", []) if not p.get("deceased_on") and not p.get("is_next_gen")
                     if (age := goal_tradeoff.age_of(p.get("date_of_birth"), today)) is not None), key=lambda x: -x[0])
    if adults:
        derived_age = adults[0][0]
    elif a.get("current_age"):
        derived_age = int(a["current_age"])
    else:
        derived_age = ASSUMED_AGE
        assumed.append("current_age")
    current_age = int(overrides.get("current_age") or derived_age)

    # Retirement age: stated, else the age reached on the goal's date or recorded horizon, else a default.
    default_ret_age = a.get("retirement_age")
    if default_ret_age is None and rgoal and (rgoal.get("target_date") or a.get("years")):
        default_ret_age = current_age + goal_tradeoff.horizon(rgoal, today)[0]
    if default_ret_age is None:
        default_ret_age = max(planning.default_of(brain, "retirement_age", 65), current_age + 5)
        assumed.append("retirement_age")
    retirement_age = int(overrides.get("retirement_age") or default_ret_age)
    if not overrides.get("longevity_age") and not a.get("longevity_age"):
        assumed.append("longevity_age")
    # The plan-to age is the last survivor's: the youngest adult's. On the eldest's ages it falls the gap between them later.
    plan_to_age = int(overrides.get("longevity_age") or a.get("longevity_age", planning.default_of(brain, "plan_to_age", ASSUMED_LONGEVITY)))
    gap = adults[0][0] - adults[-1][0] if adults else 0
    end_age = plan_to_age + gap
    if a.get("funding_share") is None:
        assumed.append("funding_share")
    funding_share = float(a.get("funding_share", ASSUMED_FUNDING_SHARE))
    current_value = float(overrides.get("current_value") or total * funding_share)

    # Income target (today's $): explicit, else a ~5% draw on the funded retirement pot. Anchoring to
    # the funded value (not the aspirational goal lump sum) keeps the projection realistic.
    # Spending on file, and the debts that outlast the working years, is the income to plan for; guaranteed income
    # is taken off it year by year, from when each stream starts. With nothing recorded this changes nothing.
    from app.aurea_core import financial_plan  # local import avoids a cycle

    on_file = await financial_plan.retirement_inputs(session, household_id, firm_id=firm_id, retirement_age=retirement_age, current_age=current_age)
    recorded = on_file["spending"] + on_file["debt_payments"] if on_file["spending"] else None
    default_income = a.get("annual_income") or recorded or current_value * 0.05
    income_need = float(overrides.get("annual_income") or default_income)
    if on_file["stream_age_assumed"]:
        assumed.append("stream_start")
    annual_contribution = float(overrides.get("annual_contribution") if overrides.get("annual_contribution") is not None
                                else a.get("annual_contribution", 0.0))

    retire_at = max(retirement_age, current_age + 1)
    plan = retirement_plan(
        current_value=current_value, allocation=allocation, current_age=current_age,
        retirement_age=retire_at, longevity_age=max(end_age, retire_at + 1),
        annual_income=income_need, annual_contribution=annual_contribution,
        streams=on_file["streams"], cma=planning.cma_of(brain),
        # The firm's inflation, or its market's: this used the 2.5% default whatever the firm had set.
        inflation=float((((brain.get("planning") or {}).get("inflation")) or {}).get("cpi", 0.025)),
        # The firm's retirement success target, from the assumption set (B-174); the platform's 85% without one.
        target_success=planning.target_of(brain, "retirement"),
    )
    acc = plan["years_to_retirement"]
    plan["household"] = {"id": str(household_id), "name": brain["household"]["name"]}
    plan["currency"] = brain.get("currency")   # without it a reader guesses, and a model guessed pounds
    plan["income_need"] = round(income_need)
    plan["income_basis"] = "stated" if overrides.get("annual_income") or a.get("annual_income") else "recorded_spending" if recorded else "assumed_draw"
    # Paid by the first year of retirement, in today's money; what starts later is listed at its stated amount.
    plan["guaranteed_income"] = plan["guaranteed_at_retirement"]
    plan["starts_later"] = round(sum(s["annual"] for s in on_file["streams"] if s["starts_in"] > acc), 2)
    plan["money_basis"] = "today"
    # Whose ages these are, and whose plan-to age ends the plan (B-173).
    plan["ages_of"] = adults[0][1] if adults else None
    plan["longevity_age"] = plan_to_age
    plan["end_age"] = plan["current_age"] + acc + plan["years_in_retirement"]
    plan["plan_to"] = {"age": plan_to_age, "person": adults[-1][1] if adults else None,
                       "whose": "younger_adult" if len(adults) > 1 else "only_adult" if adults else "not_on_file",
                       "assumed": "longevity_age" in assumed}
    # Whose returns these are: the firm's capital market assumptions, or the platform's (B-167).
    plan["assumptions_source"] = planning.source_of(brain)
    plan["assumptions"]["source"] = plan["assumptions_source"]
    plan["assumptions_version"] = plan["assumptions"]["version"] = planning.version_of(brain)
    plan["goal"] = rgoal["name"] if rgoal else "Retirement"
    plan["funding_share"] = funding_share
    if plan["income_basis"] == "assumed_draw":
        assumed.append("annual_income")
    plan["assumed"] = assumed
    # What this answers, which is not what the goals page answers: whether the savings set aside for
    # retirement pay the income wanted until the longevity age, not whether the goal's target sum is reached.
    plan["measures"] = "retirement_income"
    plan["estimate"] = True
    # How old what it works from is, and what it reads that is not on file (B-182).
    from app.aurea_core import plan_inputs  # local import avoids a cycle

    plan["inputs"] = await plan_inputs.for_household(session, household_id, firm_id=firm_id)
    plan["inputs_editable"] = ["retirement_age", "longevity_age", "annual_income", "annual_contribution"]
    return plan
