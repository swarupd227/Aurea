"""A plan scenario: what a change would do to a household's likelihoods and funded ratio, and what it gives up (B-179).

Levers: retire at another age, plan on other spending, claim Social Security at another age (a US household, on its
own statement), move a goal's date, change a goal's target, or save more each year. A scenario is run on the same
engines every page reads (`retirement.for_household`, `goal_tradeoff.likelihoods`, `funded.ratio`), beside the plan as
it stands, under the firm's assumption set, and says which version.

What it gives up is in today's money, each part with its basis: years of retirement income given up by working
longer, spending given up over retirement, Social Security forgone while waiting to claim, the value of having a goal
later, a smaller target, or what is saved. Saved scenarios are ranked by success points per 10,000 given up, where
the points are the change in retirement success plus the change in each goal's likelihood. Running or saving one
changes no record: no goal, income stream, mandate or plan is touched.

Selling down a concentrated holding is not a lever here: the projection is by asset class, so selling one holding into
others of its class changes nothing it measures, and it says so.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AuditEvent, User
from app.models.scenarios import PlanScenario
from app.models.tenant import Firm

NAME_MAX = 120
PER = 10_000          # points are ranked per this much given up, in the firm's money
NOT_A_LEVER = ["concentration_unwind"]


class ScenarioError(ValueError):
    pass


def _int(v, label: str, lo: int, hi: int) -> int | None:
    if v in (None, ""):
        return None
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        raise ScenarioError(f"{label} must be a whole number.") from None
    if not lo <= n <= hi:
        raise ScenarioError(f"{label} must be between {lo} and {hi}.")
    return n


def _money(v, label: str) -> float | None:
    if v in (None, ""):
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ScenarioError(f"{label} must be an amount.") from None
    if n <= 0:
        raise ScenarioError(f"{label} must be more than nothing.")
    return n


def clean(levers: dict | None) -> dict:
    """The levers as numbers, checked. At least one; a goal's levers need the goal."""
    lv = levers or {}
    out = {
        "retire_at": _int(lv.get("retire_at"), "Retire at", 40, 80),
        "spending": _money(lv.get("spending"), "Spending"),
        "claim_age": _int(lv.get("claim_age"), "Claim age", 62, 70),
        "goal_id": str(lv["goal_id"]).strip() if lv.get("goal_id") not in (None, "") else None,
        "goal_years_delta": _int(lv.get("goal_years_delta"), "Years to move the goal", -20, 30),
        "goal_target": _money(lv.get("goal_target"), "The goal's target"),
        "extra_contribution": _money(lv.get("extra_contribution"), "The extra saved each year"),
    }
    if out["goal_years_delta"] == 0:
        out["goal_years_delta"] = None
    if (out["goal_years_delta"] is not None or out["goal_target"] is not None) and not out["goal_id"]:
        raise ScenarioError("Moving a goal or changing its target needs the goal (goal_id, from the household's goals).")
    if all(v is None for k, v in out.items() if k != "goal_id"):
        raise ScenarioError("Give at least one lever: retire_at, spending, claim_age, goal_years_delta or goal_target "
                            "(with goal_id), or extra_contribution.")
    return {k: v for k, v in out.items() if v is not None}


async def _measure(session: AsyncSession, firm: Firm, household_id: uuid.UUID, brain: dict, today, lv: dict) -> dict:
    from app.aurea_core import funded, goal_tradeoff, retirement

    overrides = {k: v for k, v in (("retirement_age", lv.get("retire_at")), ("annual_income", lv.get("spending")),
                                   ("claim_age", lv.get("claim_age")), ("extra_contribution", lv.get("extra_contribution")))
                 if v is not None}
    plan = await retirement.for_household(session, household_id, firm_id=firm.id, overrides=overrides)
    go: dict[str, dict] = {}
    if lv.get("goal_id"):
        g = next((x for x in brain.get("goals", []) if str(x.get("id")) == lv["goal_id"]), None)
        if g is None:
            raise ScenarioError("That goal is not this household's.")
        ov = {}
        if lv.get("goal_years_delta") is not None:
            ov["years_delta"] = lv["goal_years_delta"]
        if lv.get("goal_target") is not None:
            stated = float(g.get("target_amount") or 0)
            if stated <= 0:
                raise ScenarioError("That goal has no target on file to change.")
            ov["target_scale"] = lv["goal_target"] / stated
        go[lv["goal_id"]] = ov
    if lv.get("extra_contribution") and brain.get("goals"):
        first = str(brain["goals"][0].get("id"))
        go.setdefault(first, {})["extra_contribution"] = lv["extra_contribution"]
    lk = goal_tradeoff.likelihoods(brain, today, goal_overrides=go or None)
    f = funded.ratio(brain, lk, plan)
    return {"plan": plan, "lk": lk, "funded": f,
            "view": {"retirement_success": plan["success_probability"] if plan else None,
                     "retirement_on_track": plan["on_track"] if plan else None,
                     "retirement_age": plan["retirement_age"] if plan else None,
                     "goals": [{"id": g["id"], "name": g["name"], "success": g["success_probability"], "on_track": g["on_track"]}
                               for g in lk["goals"]],
                     "funded_ratio": f["ratio"]}}


def _annuity(rate: float, start: int, years: int) -> float:
    return sum(1 / (1 + rate) ** (start + k + 1) for k in range(max(years, 0)))


async def _given_up(session: AsyncSession, firm: Firm, household_id: uuid.UUID, brain: dict, today, lv: dict, base: dict) -> list[dict]:
    """What each lever gives up, in today's money, with its basis. Negative where the lever gives something back."""
    from app.aurea_core import financial_plan, planning

    rate, _ = planning.discount_of(brain)
    plan = base["plan"] or {}
    need = float(plan.get("income_need") or 0)
    acc, dec = int(plan.get("years_to_retirement") or 0), int(plan.get("years_in_retirement") or 0)
    parts = []
    if "retire_at" in lv and plan:
        n = lv["retire_at"] - int(plan["retirement_age"])
        parts.append({"lever": "retire_at", "amount": round(n * need),
                      "basis": f"{abs(n)} year(s) of retirement income {'given up' if n > 0 else 'gained'}"})
    if "spending" in lv and plan:
        cut = need - lv["spending"]
        parts.append({"lever": "spending", "amount": round(cut * _annuity(rate, acc, dec)),
                      "basis": f"spending {'given up' if cut > 0 else 'added'} over {dec} years of retirement, at {rate:.2%}"})
    if "claim_age" in lv:
        streams = [s for s in await financial_plan.streams_of(session, household_id, firm, today)
                   if s["kind"] == "social_security" and s.get("claiming")]
        if not streams:
            raise ScenarioError("Claim age is Social Security's: a US household with a statement on file.")
        for s in streams:
            n = lv["claim_age"] - int(s["start_age"])
            parts.append({"lever": "claim_age", "amount": round(n * float(s["planned_annual"])),
                          "basis": f"{abs(n)} year(s) of {s['person'] or 'the'} benefit {'forgone while waiting' if n > 0 else 'gained by claiming earlier'}"})
    if lv.get("goal_id"):
        g = next(x for x in base["lk"]["goals"] if x["id"] == lv["goal_id"])
        today_value = float(g.get("target_today") or g["target_amount"])
        if "goal_years_delta" in lv:
            n = lv["goal_years_delta"]
            parts.append({"lever": "goal_years_delta", "amount": round(today_value * (1 - 1 / (1 + rate) ** n)),
                          "basis": f"{g['name']} {abs(n)} year(s) {'later' if n > 0 else 'sooner'}, valued at {rate:.2%}"})
        if "goal_target" in lv:
            stated = next(float(x.get("target_amount") or 0) for x in brain["goals"] if str(x.get("id")) == lv["goal_id"])
            parts.append({"lever": "goal_target", "amount": round(stated - lv["goal_target"]),
                          "basis": f"{g['name']}'s target {'reduced' if lv['goal_target'] < stated else 'raised'}"})
    if "extra_contribution" in lv:
        years = acc or max((int(x["years"]) for x in base["lk"]["goals"]), default=0)
        parts.append({"lever": "extra_contribution", "amount": round(lv["extra_contribution"] * _annuity(rate, 0, years)),
                      "basis": f"saved for {years} years, at {rate:.2%}"})
    return parts


async def run(session: AsyncSession, firm: Firm, household_id: uuid.UUID, levers: dict | None) -> dict:
    """The plan as it stands and with the levers, and what the change gives up. Writes nothing."""
    from app.aurea_core import firm_time, planning
    from app.aurea_core.graph import household_brain

    lv = clean(levers)
    brain = await household_brain(session, household_id, firm_id=firm.id)
    if not brain:
        raise ScenarioError("That household is not one of this firm's.")
    today = firm_time.today(firm)
    base = await _measure(session, firm, household_id, brain, today, {})
    after = await _measure(session, firm, household_id, brain, today, lv)
    b, a = base["view"], after["view"]
    ret_points = round((a["retirement_success"] - b["retirement_success"]) * 100, 1) \
        if a["retirement_success"] is not None and b["retirement_success"] is not None else 0.0
    was = {g["id"]: g for g in b["goals"]}
    goal_points = [{"goal": g["name"], "before": was[g["id"]]["success"], "after": g["success"],
                    "points": round((g["success"] - was[g["id"]]["success"]) * 100, 1)} for g in a["goals"] if g["id"] in was]
    points = round(ret_points + sum(g["points"] for g in goal_points), 1)
    parts = await _given_up(session, firm, household_id, brain, today, lv, base)
    given_up = sum(p["amount"] for p in parts)
    return {
        "household_id": str(household_id), "household": brain["household"]["name"], "currency": brain.get("currency"),
        "as_of": today.isoformat(), "levers": lv, "not_levers": NOT_A_LEVER,
        "assumptions_version": planning.version_of(brain), "estimate": True,
        "before": b, "after": a,
        "change": {"retirement_points": ret_points, "goals": goal_points, "points": points,
                   "funded_ratio": round(a["funded_ratio"] - b["funded_ratio"], 3)
                   if a["funded_ratio"] is not None and b["funded_ratio"] is not None else None},
        "given_up": {"today": round(given_up), "parts": parts},
        "points_per_10k": round(points / (given_up / PER), 2) if given_up > 0 else None,
    }


async def _owned(session: AsyncSession, firm: Firm, household_id: uuid.UUID):
    from app.models.graph import Household

    hh = await session.get(Household, household_id)
    if hh is None or hh.firm_id != firm.id:
        raise ScenarioError("That household is not one of this firm's.")
    return hh


async def prepare_save(session: AsyncSession, firm: Firm, household_id: uuid.UUID, *, name: str | None, levers: dict | None) -> dict:
    await _owned(session, firm, household_id)
    name = (name or "").strip()
    if not name:
        raise ScenarioError("A saved scenario needs a name.")
    if len(name) > NAME_MAX:
        raise ScenarioError(f"A scenario's name is at most {NAME_MAX} characters.")
    return {"name": name, "result": await run(session, firm, household_id, levers)}


async def save(session: AsyncSession, firm: Firm, actor: User, household_id: uuid.UUID, *, name: str | None, levers: dict | None) -> dict:
    """Keep a scenario on the household. It changes no record."""
    plan = await prepare_save(session, firm, household_id, name=name, levers=levers)
    row = PlanScenario(firm_id=firm.id, household_id=household_id, name=plan["name"], levers=plan["result"]["levers"],
                       result=plan["result"], made_by=actor.id, made_by_name=actor.full_name or actor.email)
    session.add(row)
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="plan_scenario.saved",
                           subject=plan["result"]["household"], detail={"name": plan["name"], "levers": plan["result"]["levers"]}))
    await session.flush()
    return {**_view(row), "result": plan["result"]}


def _view(r: PlanScenario) -> dict:
    res = r.result or {}
    return {"id": str(r.id), "name": r.name, "made": r.created_at.isoformat() if r.created_at else None, "by": r.made_by_name,
            "levers": r.levers, "points": (res.get("change") or {}).get("points"),
            "funded_change": (res.get("change") or {}).get("funded_ratio"),
            "given_up": (res.get("given_up") or {}).get("today"), "points_per_10k": res.get("points_per_10k"),
            "retirement_after": (res.get("after") or {}).get("retirement_success"),
            "assumptions_version": res.get("assumptions_version")}


def rank(rows: list[dict]) -> list[dict]:
    """Best first: success points per 10,000 given up; a scenario that gives nothing up is listed after, by points."""
    priced = sorted((r for r in rows if r["points_per_10k"] is not None), key=lambda r: -r["points_per_10k"])
    free = sorted((r for r in rows if r["points_per_10k"] is None), key=lambda r: -(r["points"] or 0))
    return [{**r, "rank": i + 1} for i, r in enumerate(priced + free)]


async def saved(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    from app.aurea_core import planning
    from app.aurea_core.graph import household_brain

    hh = await _owned(session, firm, household_id)
    rows = (await session.execute(select(PlanScenario).where(PlanScenario.firm_id == firm.id, PlanScenario.household_id == household_id)
                                  .order_by(PlanScenario.created_at.desc()))).scalars().all()
    now = planning.version_of(await household_brain(session, household_id, firm_id=firm.id))
    ranked = rank([_view(r) for r in rows])
    for r in ranked:
        r["earlier_assumptions"] = r["assumptions_version"] != now
    return {"household_id": str(household_id), "household": hh.name, "currency": firm.base_currency, "per": PER,
            "assumptions_version": now, "scenarios": ranked}
