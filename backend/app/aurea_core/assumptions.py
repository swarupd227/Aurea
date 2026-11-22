"""The firm's planning assumptions as one versioned record (B-174).

Every figure a projection runs on is here: expected return and volatility by asset class, inflation by index (CPI,
education, healthcare), retirement and plan-to ages, the success each engine must reach to read on track, the cash
reserve, how old a planning input may be, and the line past which a new version asks for a review. Each figure is the
firm's (with the source it was recorded from), its market's suggestion (with that source), or the platform's default,
and says which.

  - A change is a new version, added and never edited; the latest stands. It names each figure it changes with that
    figure's source, and the person's reason, and it is written through to `firm.settings`, which every engine
    already reads (`planning.assumption_set`), so nothing reads a second copy.
  - The set belongs to research and the portfolio team (`decision_rights.ASSUMPTION_OWNERS`), as the house view and
    the model portfolios do. The administrator's capital market table writes a version through the same path.
  - Every projection names the version it ran on (`planning.version_of`).
  - Recording a version runs the refresh: each active household's goal likelihoods (`goal_tradeoff.likelihoods`, the
    one answer) on the old set and the new; a household whose likelihood moved by more than the refresh line gets one
    task, for its recorded adviser. Nothing else changes: no goal, mandate or plan is touched.
"""
from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.decision_rights import ASSUMPTION_OWNERS
from app.models.assumptions import AssumptionSetVersion
from app.models.enums import UserRole
from app.models.graph import Household
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

# How far a household's likelihood may move before a new version asks for its review: the platform's line, named as
# assumed wherever it is used, until a firm records its own.
DEFAULT_REFRESH_LINE = 0.05
DEFAULT_RESERVE_MONTHS = 6
DEFAULT_PLAN_INPUT_DAYS = 90
SOURCE_MAX = 300
TASK_SOURCE = "assumptions"


class AssumptionError(ValueError):
    pass


class NotOwner(AssumptionError):
    pass


@dataclass(frozen=True)
class Figure:
    key: str            # what a person and the model call it
    label: str
    unit: str           # rate, age, months, days
    lo: float
    hi: float
    path: tuple         # where it lives in firm.settings


def _figures() -> dict[str, Figure]:
    from app.aurea_core.planning import CMA, DISCOUNT_SETTING, INFLATION_INDEXES, SUCCESS_CEILING, SUCCESS_TARGETS

    out: list[Figure] = []
    for cls in CMA:
        name = cls.replace("_", " ")
        out.append(Figure(f"return.{cls}", f"Expected return, {name}", "rate", -0.5, 0.5, ("cmas", cls, 0)))
        out.append(Figure(f"volatility.{cls}", f"Volatility, {name}", "rate", 0.0, 1.0, ("cmas", cls, 1)))
    for index, (setting, _default) in INFLATION_INDEXES.items():
        label = "Inflation (CPI)" if index == "cpi" else f"{index.capitalize()} inflation"
        out.append(Figure(f"inflation.{index}", label, "rate", -0.05, 0.25, (setting,)))
    out.append(Figure("retirement_age", "Retirement age", "age", 40, 80, ("retirement_age",)))
    out.append(Figure("plan_to_age", "Plan to age", "age", 70, 120, ("plan_to_age",)))
    for engine, (setting, _default) in SUCCESS_TARGETS.items():
        out.append(Figure(f"success_target.{engine}", f"Success target, {engine}", "rate", 0.5, 0.99, (setting,)))
    out.append(Figure("success_ceiling", "Top of the success band", "rate", 0.5, 1.0, (SUCCESS_CEILING[0],)))
    out.append(Figure("discount_rate", "Discount rate for future needs", "rate", -0.05, 0.15, (DISCOUNT_SETTING,)))
    out.append(Figure("reserve_months", "Cash reserve", "months", 0, 36, ("reserve_months",)))
    out.append(Figure("plan_input_days", "Oldest planning input", "days", 1, 1825, ("plan_input_days",)))
    out.append(Figure("refresh_line", "Refresh line", "rate", 0.01, 0.5, ("assumption_refresh_line",)))
    return {f.key: f for f in out}


FIGURES = _figures()


def _get(settings: dict, path: tuple):
    node = settings
    for step in path:
        if isinstance(step, int):
            if not isinstance(node, (list, tuple)) or len(node) <= step:
                return None
            node = node[step]
        else:
            if not isinstance(node, dict) or step not in node:
                return None
            node = node[step]
    return node


def _set(settings: dict, path: tuple, value, current: dict) -> None:
    """Write one figure into a copy of the settings. A class's return and volatility are one pair, so the other half
    comes from what stands now."""
    if path[0] == "cmas":
        cls, half = path[1], path[2]
        cmas = settings.setdefault("cmas", {})
        pair = list(cmas.get(cls) or [current[f"return.{cls}"]["value"], current[f"volatility.{cls}"]["value"]])
        pair[half] = float(value)
        cmas[cls] = pair
    else:
        settings[path[0]] = value


def _num(unit: str, value):
    return int(round(float(value))) if unit in ("age", "months", "days") else float(value)


async def latest(session: AsyncSession, firm_id: uuid.UUID) -> AssumptionSetVersion | None:
    return (await session.execute(select(AssumptionSetVersion).where(AssumptionSetVersion.firm_id == firm_id)
                                  .order_by(AssumptionSetVersion.version.desc()).limit(1))).scalars().first()


def figures_now(firm: Firm, recorded: AssumptionSetVersion | None, *, sources: dict | None = None) -> dict[str, dict]:
    """Every figure as a projection would use it today: the value, whose it is (firm, market or platform) and its
    source."""
    from app.aurea_core import market_assumptions, planning, plan_inputs

    s = firm.settings or {}
    aset = planning.assumption_set(s, firm.jurisdiction)
    suggested = market_assumptions.for_market(firm.jurisdiction)
    sources = sources if sources is not None else ((recorded.sources if recorded else {}) or {})
    firm_classes = set((s.get("cmas") or {}).keys())

    def market_source(key: str) -> str | None:
        a = suggested.get(key)
        return f"{a.source} ({a.as_of}; suggested, not a regulatory value)" if a else None

    out: dict[str, dict] = {}
    for key, f in FIGURES.items():
        if key.startswith(("return.", "volatility.")):
            cls = f.path[1]
            value = aset["cma"][cls][f.path[2]]
            mkey = market_assumptions.RETURN_KEYS.get(cls) if key.startswith("return.") else None
            # A class's return and volatility are kept as one pair, so a half is the firm's only when it was recorded
            # with a source, or (set before versions) differs from the platform's.
            own = cls in firm_classes and (key in sources or value != planning.CMA[cls][f.path[2]])
            whose = "firm" if own else "market" if mkey and mkey in suggested and cls not in firm_classes else "platform"
            msrc = market_source(mkey) if whose == "market" else None
        elif key.startswith("inflation."):
            index = key.split(".", 1)[1]
            value, whose = aset["inflation"][index], aset["sources"][f"{index}_inflation"]
            msrc = market_source(f.path[0]) if whose == "market" else None
        elif key in ("retirement_age", "plan_to_age"):
            value, whose = aset[key], aset["sources"][key]
            msrc = market_source(key) if whose == "market" else None
        elif key.startswith("success_target."):
            engine = key.split(".", 1)[1]
            value, whose = aset["success_target"][engine], aset["sources"][f"{engine}_success_target"]
            msrc = None
        elif key == "success_ceiling":
            value, whose, msrc = aset["success_ceiling"], aset["sources"]["success_ceiling"], None
        elif key == "discount_rate":
            value, whose, msrc = aset["discount_rate"], aset["sources"]["discount_rate"], None
            whose = "platform" if whose == "cash_return" else whose
        elif key == "plan_input_days":
            line = plan_inputs.line_of(firm)
            value, whose, msrc = line["days"], "platform" if line["assumed"] else "firm", None
        else:
            raw = s.get(f.path[0])
            default = DEFAULT_RESERVE_MONTHS if key == "reserve_months" else DEFAULT_REFRESH_LINE
            whose = "firm" if raw not in (None, "") else "platform"
            value, msrc = (raw if whose == "firm" else default), None
        source = (sources.get(key) or "Set before the assumption set kept versions") if whose == "firm" \
            else msrc if whose == "market" \
            else "Platform default: the cash return in this set" if key == "discount_rate" else "Platform default"
        out[key] = {"key": key, "label": f.label, "unit": f.unit, "value": _num(f.unit, value), "whose": whose, "source": source}
    return out


def _view(v: AssumptionSetVersion) -> dict:
    return {"version": v.version, "made": v.created_at.isoformat() if v.created_at else None, "by": v.made_by_name,
            "reason": v.reason, "changed": list(v.changed or []), "refresh": v.refresh or {}}


async def read(session: AsyncSession, firm: Firm, *, history: int = 10) -> dict:
    recorded = latest_v = await latest(session, firm.id)
    rows = (await session.execute(select(AssumptionSetVersion).where(AssumptionSetVersion.firm_id == firm.id)
                                  .order_by(AssumptionSetVersion.version.desc()).limit(history))).scalars().all()
    figures = figures_now(firm, recorded)
    return {"version": latest_v.version if latest_v else None,
            "made": latest_v.created_at.isoformat() if latest_v and latest_v.created_at else None,
            "by": latest_v.made_by_name if latest_v else None, "reason": latest_v.reason if latest_v else None,
            "currency": firm.base_currency, "figures": list(figures.values()),
            "owners": sorted(r.value for r in ASSUMPTION_OWNERS),
            "history": [_view(v) for v in rows]}


def _check_owner(actor: User, *, admin_table: bool = False) -> None:
    if admin_table and actor.role == UserRole.ADMIN:
        return
    if actor.role not in ASSUMPTION_OWNERS:
        raise NotOwner("The planning assumptions are research's and the portfolio team's to change.")


def _clean(changes: list[dict] | None, now: dict[str, dict]) -> list[dict]:
    if not changes:
        raise AssumptionError("Name at least one figure to change, with its value and source.")
    seen: set[str] = set()
    out = []
    for c in changes:
        key = str((c or {}).get("figure") or "").strip()
        f = FIGURES.get(key)
        if f is None:
            raise AssumptionError(f"There is no planning figure called '{key}'. Figures: {', '.join(FIGURES)}.")
        if key in seen:
            raise AssumptionError(f"{f.label} is named twice.")
        seen.add(key)
        try:
            value = _num(f.unit, c.get("value"))
        except (TypeError, ValueError):
            raise AssumptionError(f"{f.label} needs a number.") from None
        if not f.lo <= value <= f.hi:
            hint = " as a yearly fraction (0.07 for 7%)" if f.unit == "rate" else ""
            raise AssumptionError(f"{f.label} must be between {f.lo:g} and {f.hi:g}{hint}.")
        source = str(c.get("source") or "").strip()
        if not source:
            raise AssumptionError(f"{f.label} needs its source: where the figure comes from.")
        if len(source) > SOURCE_MAX:
            raise AssumptionError(f"A source is at most {SOURCE_MAX} characters.")
        before = now[key]
        if before["whose"] == "firm" and before["value"] == value:
            raise AssumptionError(f"{f.label} is already {value:g}.")
        out.append({"key": key, "label": f.label, "unit": f.unit, "before": before["value"], "before_whose": before["whose"],
                    "after": value, "source": source})
    return out


async def prepare(session: AsyncSession, firm: Firm, actor: User, *, changes: list[dict] | None, reason: str | None,
                  admin_table: bool = False) -> dict:
    """What recording would change, checked, and nothing written."""
    _check_owner(actor, admin_table=admin_table)
    reason = (reason or "").strip()
    if not reason:
        raise AssumptionError("A new version of the assumptions needs a reason.")
    if len(reason) > 400:
        raise AssumptionError("A reason is at most 400 characters.")
    recorded = await latest(session, firm.id)
    now = figures_now(firm, recorded)
    clean = _clean(changes, now)
    after = {k: v["value"] for k, v in now.items()} | {c["key"]: c["after"] for c in clean}
    if not after["success_ceiling"] > max(after["success_target.goals"], after["success_target.retirement"]):
        raise AssumptionError("The top of the success band must be above both success targets.")
    line = next((c["after"] for c in clean if c["key"] == "refresh_line"), now["refresh_line"]["value"])
    return {"version": (recorded.version if recorded else 0) + 1, "changes": clean, "reason": reason,
            "refresh_line": line, "refresh_line_assumed": now["refresh_line"]["whose"] == "platform"
            and not any(c["key"] == "refresh_line" for c in clean)}


async def record(session: AsyncSession, firm: Firm, actor: User, *, changes: list[dict] | None, reason: str | None,
                 admin_table: bool = False, refresh: bool = True) -> dict:
    """Add the next version, write it through to the firm's settings, and run the refresh."""
    plan = await prepare(session, firm, actor, changes=changes, reason=reason, admin_table=admin_table)
    recorded = await latest(session, firm.id)
    now = figures_now(firm, recorded)
    before_settings = copy.deepcopy(firm.settings or {})
    settings = copy.deepcopy(firm.settings or {})
    for c in plan["changes"]:
        _set(settings, FIGURES[c["key"]].path, c["after"], now)
    settings["assumption_version"] = plan["version"]
    firm.settings = settings
    sources = {**((recorded.sources if recorded else {}) or {}), **{c["key"]: c["source"] for c in plan["changes"]}}
    after_now = figures_now(firm, None, sources=sources)
    row = AssumptionSetVersion(
        firm_id=firm.id, version=plan["version"], figures={k: v["value"] for k, v in after_now.items()},
        sources={k: v for k, v in sources.items() if after_now.get(k, {}).get("whose") == "firm"},
        changed=[c["key"] for c in plan["changes"]], reason=plan["reason"], made_by=actor.id,
        made_by_name=actor.full_name or actor.email, refresh={})
    session.add(row)
    await session.flush()
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="assumptions.recorded",
                           subject=f"Assumptions v{plan['version']}",
                           detail={"version": plan["version"], "changed": row.changed, "reason": plan["reason"]}))
    row.refresh = await run_refresh(session, firm, actor, before_settings=before_settings, version=plan["version"],
                                    line=float(plan["refresh_line"]), line_assumed=plan["refresh_line_assumed"]) \
        if refresh else {"run": False}
    await session.flush()
    return {**(await read(session, firm)), "recorded": _view(row), "changes": plan["changes"]}


async def run_refresh(session: AsyncSession, firm: Firm, actor: User, *, before_settings: dict, version: int,
                      line: float, line_assumed: bool) -> dict:
    """Each active household's goal likelihoods on the old set and the new. One task per household that moved by
    more than the line, for its recorded adviser; nothing else is changed."""
    from app.aurea_core import advisers, firm_time, goal_tradeoff, planning, tasks
    from app.aurea_core.graph import household_brain

    today = firm_time.today(firm)
    old_planning = planning.assumption_set(before_settings, firm.jurisdiction)
    households = (await session.execute(select(Household).where(
        Household.firm_id == firm.id, Household.left_on.is_(None)).order_by(Household.name))).scalars().all()
    moved, measured, unmeasured = [], 0, 0
    for hh in households:
        brain = await household_brain(session, hh.id, firm_id=firm.id)
        if not brain or not brain.get("goals"):
            unmeasured += 1
            continue
        after = goal_tradeoff.likelihoods(brain, today)
        before = goal_tradeoff.likelihoods({**brain, "planning": old_planning}, today)
        was = {g["id"]: g for g in before["goals"]}
        goals = []
        for g in after["goals"]:
            b = was.get(g["id"])
            if b is None:
                continue
            delta = round(g["success_probability"] - b["success_probability"], 3)
            goals.append({"goal": g["name"], "before": b["success_probability"], "after": g["success_probability"],
                          "change": delta, "on_track_before": b["on_track"], "on_track_after": g["on_track"]})
        measured += 1
        biggest = max((abs(x["change"]) for x in goals), default=0.0)
        flipped = any(x["on_track_before"] != x["on_track_after"] for x in goals)
        if biggest > line or flipped:
            adviser = await advisers.adviser_of(session, firm.id, hh.id)
            title = f"Assumptions v{version} moved {hh.name}'s goal likelihoods: review the plan"
            t = tasks.new(firm.id, title[:tasks.TITLE_MAX], household=hh, due_date=None, assignee=adviser, by=actor.id)
            t.source = TASK_SOURCE
            t.detail = "; ".join(f"{x['goal']} {x['before']:.0%} to {x['after']:.0%}" for x in goals)[:2000]
            session.add(t)
            moved.append({"household": hh.name, "household_id": str(hh.id), "biggest_change": biggest,
                          "on_track_changed": flipped, "adviser": adviser.full_name if adviser else None, "goals": goals})
    moved.sort(key=lambda m: -m["biggest_change"])
    return {"run": True, "line": line, "line_assumed": line_assumed, "measured": measured,
            "not_measured": unmeasured, "moved": moved, "tasks": len(moved)}


# ── The administrator's capital market table writes through the same record ─────────────────────────────────

async def record_cma_table(session: AsyncSession, firm: Firm, actor: User, cmas: dict, reason: str | None) -> dict:
    """`platform_admin.set_cmas`: the administrator's table of returns and volatility, as one version. A class
    left out of the table goes back to the platform's figures."""
    from app.aurea_core.planning import CMA

    current = (firm.settings or {}).get("cmas") or {}
    source = "The administrator's capital market table"
    changes = []
    for cls, pair in cmas.items():
        for half, kind in ((0, "return"), (1, "volatility")):
            if (current.get(cls) or [None, None])[half] != pair[half]:
                changes.append({"figure": f"{kind}.{cls}", "value": pair[half], "source": source})
    removed = [cls for cls in current if cls not in cmas and cls in CMA]
    if not changes and not removed:
        return cmas
    settings = copy.deepcopy(firm.settings or {})
    if removed:
        settings["cmas"] = {k: v for k, v in (settings.get("cmas") or {}).items() if k not in removed}
        firm.settings = settings
    if changes:
        await record(session, firm, actor, changes=changes, reason=reason or "The administrator's capital market table",
                     admin_table=True)
    else:
        recorded = await latest(session, firm.id)
        version = (recorded.version if recorded else 0) + 1
        firm.settings = {**firm.settings, "assumption_version": version}
        after_now = figures_now(firm, recorded)
        session.add(AssumptionSetVersion(
            firm_id=firm.id, version=version, figures={k: v["value"] for k, v in after_now.items()},
            sources={k: v for k, v in ((recorded.sources if recorded else {}) or {}).items()
                     if after_now.get(k, {}).get("whose") == "firm"},
            changed=[f"return.{c}" for c in removed] + [f"volatility.{c}" for c in removed],
            reason=reason or "The administrator's capital market table", made_by=actor.id,
            made_by_name=actor.full_name or actor.email, refresh={"run": False}))
        await session.flush()
    return (firm.settings or {}).get("cmas") or {}
