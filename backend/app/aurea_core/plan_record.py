"""The plan of record (B-181): a household's plan as a produced artifact, with states worked out from the evidence.

The plan's figures move every time the file does; the plan a client was given must not. So a plan is recorded in
three kinds of event, each added and never edited (models/plan_record.py):

    baseline   the figures as they stand, frozen, with the assumption version they ran on. Refused while a fact the
               plan reads is missing (plan_inputs.missing_of): the missing facts are listed, never filled.
    review     a second person's pass or fail on that baseline. Findings are required on a fail, and a fail sends
               the plan back: a new baseline is needed. The person who recorded the baseline cannot review it.
    delivery   the reviewed baseline went to the client, from its evidence (a document or a meeting note). It copies
               the baseline's frozen figures, never figures worked out again at delivery, and each action item becomes
               a task for the household's recorded adviser.

Where a plan stands is worked out from those events, as account_states.py works onboarding, and never set:

    not_started      nothing on file
    data_incomplete  some facts the plan reads are on file, not all (they are listed)
    data_complete    every fact the plan reads is on file
    baseline         a baseline is recorded for the current plan version
    scenarios        scenarios have been saved on the household since that baseline (scenario depth is how many)
    in_review        a review is needed, the baseline and its scenarios are on file, and no review is recorded yet
    reviewed         a second person passed the baseline
    delivered        it was delivered, with no action items to carry out
    implementing     it was delivered and tasks it raised are still open
    complete         every task it raised is closed

A review is needed when the household's complexity is above the firm's line (`firm.settings["plan_review_line"]`);
until the firm sets one, every plan is reviewed and the result says so. Complexity is counted from what is on file,
part by part, never estimated, and the count and the line are frozen with the baseline. A baseline recorded after a
delivery starts the next plan version; one recorded before (after a failed review, say) revises the current one.

A delivered plan is fresh while its age is within the household's review cadence (reviews.py), else twelve months,
named as assumed. Nothing here changes a goal, a mandate, a plan figure or the advice file: it adds events and tasks.
Only an adviser or a paraplanner records a baseline or a delivery; an adviser, a paraplanner or the portfolio team
reviews; compliance and a branch leader read. A client and operations read none of it.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, firm_time, planning, plan_inputs, reviews
from app.models.engagement import Task
from app.models.enums import TaskStatus
from app.models.equity_comp import EquityGrant
from app.models.financial_plan import IncomeStream
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.plan_record import EVIDENCE_KINDS, REVIEW_OUTCOMES, PlanRecordEvent
from app.models.protection import ProtectionPolicy
from app.models.scenarios import PlanScenario
from app.models.tenant import Firm
from app.provenance import ledger

PREPARERS = ("adviser", "paraplanner")
REVIEWERS = ("adviser", "paraplanner", "portfolio_team")
READERS = ("adviser", "paraplanner", "portfolio_team", "compliance", "branch_leader")
STATES = ("not_started", "data_incomplete", "data_complete", "baseline", "scenarios", "in_review", "reviewed",
          "delivered", "implementing", "complete")
# Whose move it is in each state.
OWNERS = {"not_started": "Adviser", "data_incomplete": "Paraplanner", "data_complete": "Paraplanner",
          "baseline": "Paraplanner", "scenarios": "Adviser", "in_review": "Reviewer", "reviewed": "Adviser",
          "delivered": "Done", "implementing": "Adviser", "complete": "Done"}
DELIVERED_STATES = ("delivered", "implementing", "complete")
# The firm's complexity line: a review by a second person is needed when a household's count is above it. A firm
# setting, an integer, set by the firm; unset, every plan is reviewed.
LINE_SETTING = "plan_review_line"
COMPLEXITY_PARTS = ("entities", "accounts", "goals", "income_streams", "debts", "held_away_lines", "equity_grants", "policies")
DEFAULT_FRESH_MONTHS = 12   # a household with no review cadence on file
QC_PERIOD_DAYS = 365
MAX_ACTION_ITEMS = 20
TITLE_MAX = 280
FACT_TEXT = {"date_of_birth": "date of birth", "annual_income": "income", "retirement_goal": "a retirement goal",
             "retirement_age": "retirement age", "plan_to_age": "plan-to age", "funding_share": "funding share",
             "income_need": "the income to plan for", "education_cost": "education cost, start and length"}
REFUSED_PREPARE = "A plan's baseline and its delivery are recorded by an adviser or a paraplanner."
REFUSED_REVIEW = "A plan is reviewed by an adviser, a paraplanner or the portfolio team."
REFUSED_READ = "The plan record is read by advisers, paraplanners, the portfolio team, compliance and branch leaders."


class PlanRecordError(ValueError):
    """A baseline, review or delivery that cannot be recorded, with the reason."""


def _may(role: Any, roles: tuple, words: str) -> None:
    if role is None or str(role) not in roles:
        raise PlanRecordError(words)


def may_read(role: Any) -> None:
    _may(role, READERS, REFUSED_READ)


def _uuid(value: Any, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise PlanRecordError(f"{label} must be an id from a read tool.")


def _day(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise PlanRecordError(f"{label} must be a date, YYYY-MM-DD.")


async def _household(session: AsyncSession, household_id: Any, firm_id: uuid.UUID) -> Household:
    household = await session.get(Household, _uuid(household_id, "household_id")) if household_id else None
    if household is None or household.firm_id != firm_id:
        raise PlanRecordError("That household is not in this firm.")
    return household


# ── What is on file ─────────────────────────────────────────────────────────

def line_of(firm: Firm | None) -> dict:
    """The firm's complexity line, and whether the default (every plan reviewed) stands in for it."""
    raw = ((firm.settings if firm else None) or {}).get(LINE_SETTING)
    try:
        line = int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        line = None
    if line is None or line < 0:
        return {"line": None, "assumed": True}
    return {"line": line, "assumed": False}


def review_need(score: int, line: dict) -> dict:
    """Whether a household of this complexity needs its plan reviewed by a second person, and why."""
    if line["assumed"]:
        return {"needed": True, "basis": "default", "line": None, "line_assumed": True, "score": score}
    return {"needed": score > line["line"], "basis": "above_line" if score > line["line"] else "within_line",
            "line": line["line"], "line_assumed": False, "score": score}


async def complexity_of(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    """The household's complexity, counted part by part from what is on file. Nothing is estimated: a part with
    nothing recorded counts nothing."""
    people = [p for p in (await session.execute(select(Person.id).where(
        Person.firm_id == firm_id, Person.household_id == household_id, Person.deceased_on.is_(None)))).scalars().all()]
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm_id, LegalEntity.household_id == household_id))).scalars().all()
    conds = []
    if people:
        conds.append(Mandate.person_id.in_(people))
    if entities:
        conds.append(Mandate.entity_id.in_([e.id for e in entities]))
    mandates = (await session.execute(select(Mandate.id).where(Mandate.firm_id == firm_id, or_(*conds)))).scalars().all() if conds else []
    accounts = (await session.execute(select(func.count()).select_from(Account).where(
        Account.mandate_id.in_(mandates), Account.status != "closed"))).scalar_one() if mandates else 0
    goals = (await session.execute(select(func.count()).select_from(Goal).where(Goal.firm_id == firm_id, Goal.household_id == household_id))).scalar_one()
    streams = (await session.execute(select(func.count()).select_from(IncomeStream).where(
        IncomeStream.firm_id == firm_id, IncomeStream.household_id == household_id, IncomeStream.ended_on.is_(None)))).scalar_one()
    debts = sum(1 for i in await balance_sheet.current_items(session, household_id, firm_id=firm_id) if i.side == "liability")
    from app.aurea_core import held_away

    lines = len(await held_away.current_lines(session, household_id, firm_id=firm_id))
    grants = (await session.execute(select(func.count()).select_from(EquityGrant).where(
        EquityGrant.firm_id == firm_id, EquityGrant.household_id == household_id, EquityGrant.ended_on.is_(None)))).scalar_one()
    policies = (await session.execute(select(func.count()).select_from(ProtectionPolicy).where(
        ProtectionPolicy.firm_id == firm_id, ProtectionPolicy.household_id == household_id, ProtectionPolicy.status == "in_force"))).scalar_one()
    counts = {"entities": len(entities), "accounts": int(accounts), "goals": int(goals), "income_streams": int(streams),
              "debts": debts, "held_away_lines": lines, "equity_grants": int(grants), "policies": int(policies)}
    trusts = sum(1 for e in entities if "trust" in str(e.entity_type))
    return {"score": sum(counts.values()), "parts": [{"part": k, "count": counts[k]} for k in COMPLEXITY_PARTS],
            "trusts": trusts, "basis": "counted from what is on file"}


async def data_of(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    """How much of what the plan reads is on file, and what is missing, each named."""
    read, missing = await plan_inputs.missing_of(session, household_id, firm_id=firm_id)
    facts = len(await plan_inputs.facts_of(session, household_id, firm_id=firm_id))
    return {"read": read, "on_file": read - len(missing), "facts": facts,
            "missing": [{**m, "text": FACT_TEXT.get(m["fact"], m["fact"].replace("_", " ")) + (f" ({m['person']})" if m.get("person") else "")
                         + (f" ({m['goal']})" if m.get("goal") else "")} for m in missing]}


def _data_state(data: dict) -> str:
    if not data["missing"]:
        return "data_complete"
    return "not_started" if data["on_file"] == 0 and data["facts"] == 0 else "data_incomplete"


async def figures_of(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    """The plan's headline figures as they stand today: what a baseline freezes. Every figure is the engine's own
    (financial_plan, goal_tradeoff.likelihoods, funded), read and not changed."""
    from app.aurea_core import financial_plan, goal_tradeoff
    from app.aurea_core.graph import household_brain

    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    plan = await financial_plan.for_household(session, household_id, firm_id=firm_id)
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if plan is None or brain is None:
        raise PlanRecordError("That household is not in this firm.")
    likely = goal_tradeoff.likelihoods(brain, today)
    r = plan.get("retirement")
    f = plan.get("funded") or {}
    t = likely.get("tiers") or {}
    i = plan.get("inputs") or {}
    return {
        "as_of": today.isoformat(), "currency": firm.base_currency if firm else None,
        "assumption_version": planning.assumption_version((firm.settings or {}) if firm else None),
        "assumptions_version": planning.version_of(brain), "assumptions_source": planning.source_of(brain),
        "retirement": None if r is None else {k: r.get(k) for k in (
            "success_probability", "on_track", "band", "sustainable_income", "income_need", "income_target",
            "projected_at_retirement_today", "retirement_age", "longevity_age")},
        "goals": [{"id": str(g["id"]), "name": g["name"], "kind": g.get("kind"), "tier": g.get("tier"),
                   "success_probability": g["success_probability"], "on_track": g["on_track"], "band": g.get("band")}
                  for g in likely.get("goals") or []],
        "tiers": {"success": t.get("success"), "success_basis": t.get("success_basis"), "failed_tier": t.get("failed_tier"),
                  "by_tier": {k: v.get("all_met") for k, v in (t.get("by_tier") or {}).items()}},
        "funded": {k: f.get(k) for k in ("ratio", "assets", "pv_needs", "discount_rate", "tiers")},
        "inputs": {"inputs": i.get("inputs"), "dated": i.get("dated"), "undated": i.get("undated"),
                   "older_than_line": i.get("older_than_line"), "line_days": i.get("line_days"),
                   "oldest_days": (i.get("oldest") or {}).get("days"), "completeness": i.get("completeness")},
        "estimate": True,
    }


def differences(frozen: dict, now: dict) -> list[dict]:
    """The headline figures that are not what was frozen, frozen beside today's."""
    def pick(fig: dict) -> dict:
        r = fig.get("retirement") or {}
        out = {"retirement_success": r.get("success_probability"), "sustainable_income": r.get("sustainable_income"),
               "funded_ratio": (fig.get("funded") or {}).get("ratio"), "needs_met": (fig.get("tiers") or {}).get("success"),
               "assumption_version": fig.get("assumption_version")}
        for g in fig.get("goals") or []:
            out[f"goal:{g['id']}"] = g["success_probability"]
        return out

    names = {f"goal:{g['id']}": g["name"] for g in (frozen.get("goals") or []) + (now.get("goals") or [])}
    a, b = pick(frozen), pick(now)
    return [{"figure": k if not k.startswith("goal:") else "goal", "goal": names.get(k), "frozen": a.get(k), "today": b.get(k)}
            for k in list(dict.fromkeys([*a, *b])) if a.get(k) != b.get(k)]


# ── Where a plan stands ─────────────────────────────────────────────────────

def _after(baseline: PlanRecordEvent, scenario_ids: list[str]) -> list[str]:
    before = set((baseline.snapshot or {}).get("scenarios_before") or [])
    return [s for s in scenario_ids if s not in before]


def state_of(events: list[PlanRecordEvent], *, scenario_ids: list[str], task_status: dict[str, str], data: dict) -> dict:
    """The plan's state, from its events, the scenarios on the household, its tasks and what is on file."""
    data_state = _data_state(data)
    out = {"version": 0, "baseline": None, "review": None, "delivery": None, "sent_back": None, "scenarios_since": 0,
           "open_tasks": 0, "tasks": 0}
    if not events:
        return {**out, "state": data_state}
    version = max(e.plan_version for e in events)
    current = [e for e in events if e.plan_version == version]
    baseline = [e for e in current if e.kind == "baseline"][-1]
    delivery = next((e for e in reversed(current) if e.kind == "delivery"), None)
    reviewed = [e for e in current if e.kind == "review" and e.baseline_id == baseline.id]
    last = reviewed[-1] if reviewed else None
    since = _after(baseline, scenario_ids)
    out.update(version=version, baseline=baseline, review=last, delivery=delivery, scenarios_since=len(since))
    need = (baseline.snapshot or {}).get("review") or {"needed": True}
    if delivery is not None:
        ids = (delivery.snapshot or {}).get("task_ids") or []
        open_ = [i for i in ids if task_status.get(i, TaskStatus.OPEN.value) == TaskStatus.OPEN.value]
        out.update(tasks=len(ids), open_tasks=len(open_))
        return {**out, "state": "delivered" if not ids else "implementing" if open_ else "complete"}
    if last is not None and last.outcome == "failed":
        # Sent back: the next step is a new baseline, so the plan stands where its data does.
        return {**out, "state": "data_incomplete" if data_state == "not_started" else data_state, "sent_back": last}
    if last is not None and last.outcome == "passed":
        return {**out, "state": "reviewed"}
    if need.get("needed") and since:
        return {**out, "state": "in_review"}
    return {**out, "state": "scenarios" if since else "baseline"}


def freshness(delivered_on: date, household: Household, today: date) -> dict:
    """A delivered plan is fresh while its age is within the household's review cadence, else twelve months, named."""
    months = household.review_cadence_months
    assumed = months is None
    months = months or DEFAULT_FRESH_MONTHS
    until = reviews.add_months(delivered_on, months)
    return {"delivered_on": delivered_on.isoformat(), "age_days": (today - delivered_on).days, "cadence_months": months,
            "cadence": reviews.CADENCES.get(months, f"Every {months} months"), "cadence_assumed": assumed,
            "fresh_until": until.isoformat(), "fresh": today <= until}


async def _events(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, list[PlanRecordEvent]]:
    q = select(PlanRecordEvent).where(PlanRecordEvent.firm_id == firm_id)
    if household_ids is not None:
        if not household_ids:
            return {}
        q = q.where(PlanRecordEvent.household_id.in_(household_ids))
    out: dict[uuid.UUID, list[PlanRecordEvent]] = {}
    for e in (await session.execute(q.order_by(PlanRecordEvent.household_id, PlanRecordEvent.seq))).scalars().all():
        out.setdefault(e.household_id, []).append(e)
    return out


async def _scenarios(session: AsyncSession, firm_id: uuid.UUID, household_ids: list[uuid.UUID] | None = None) -> dict[uuid.UUID, list[str]]:
    q = select(PlanScenario.id, PlanScenario.household_id).where(PlanScenario.firm_id == firm_id)
    if household_ids is not None:
        if not household_ids:
            return {}
        q = q.where(PlanScenario.household_id.in_(household_ids))
    out: dict[uuid.UUID, list[str]] = {}
    for sid, hid in (await session.execute(q)).all():
        out.setdefault(hid, []).append(str(sid))
    return out


async def _tasks(session: AsyncSession, firm_id: uuid.UUID, events: list[PlanRecordEvent]) -> dict[str, Task]:
    ids = [uuid.UUID(t) for e in events if e.kind == "delivery" for t in (e.snapshot or {}).get("task_ids") or []]
    if not ids:
        return {}
    return {str(t.id): t for t in (await session.execute(select(Task).where(Task.firm_id == firm_id, Task.id.in_(ids)))).scalars().all()}


def _event_view(e: PlanRecordEvent) -> dict:
    snap = e.snapshot or {}
    return {"id": str(e.id), "seq": e.seq, "kind": e.kind, "version": e.plan_version, "happened_on": e.happened_on.isoformat(),
            "baseline_id": str(e.baseline_id) if e.baseline_id else None, "outcome": e.outcome, "findings": e.findings,
            "evidence_kind": e.evidence_kind, "evidence": e.evidence, "recorded_by": e.recorded_by,
            "recorded_by_role": e.recorded_by_role,
            "action_items": len(snap.get("action_items") or []) if e.kind == "delivery" else None}


def _depths(events: list[PlanRecordEvent], scenario_ids: list[str]) -> list[int]:
    """Scenarios saved after each baseline and before the next."""
    baselines = [e for e in events if e.kind == "baseline"]
    out = []
    for i, b in enumerate(baselines):
        after = set(_after(b, scenario_ids))
        if i + 1 < len(baselines):
            after &= set((baselines[i + 1].snapshot or {}).get("scenarios_before") or [])
        out.append(len(after))
    return out


def _task_view(t: Task, names: dict, today: date) -> dict:
    return {"id": str(t.id), "title": t.title, "status": str(t.status), "due_date": t.due_date.isoformat() if t.due_date else None,
            "overdue": str(t.status) == TaskStatus.OPEN.value and t.due_date is not None and t.due_date < today,
            "assigned_to": names.get(t.assigned_to) if t.assigned_to else None}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, with_today: bool = True) -> dict | None:
    """One household's plan record: its state and owner, the current baseline's frozen figures (beside today's where
    they differ), the plan of record as delivered and whether it is fresh, its tasks, and every event."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    events = (await _events(session, firm_id, [household.id])).get(household.id, [])
    scenario_ids = (await _scenarios(session, firm_id, [household.id])).get(household.id, [])
    tasks = await _tasks(session, firm_id, events)
    data = await data_of(session, household.id, firm_id=firm_id)
    st = state_of(events, scenario_ids=scenario_ids, task_status={k: str(t.status) for k, t in tasks.items()}, data=data)
    complexity = await complexity_of(session, household.id, firm_id=firm_id)
    need_now = review_need(complexity["score"], line_of(firm))
    baseline, delivery = st["baseline"], next((e for e in reversed(events) if e.kind == "delivery"), None)
    names = {}
    uids = {t.assigned_to for t in tasks.values() if t.assigned_to}
    if uids:
        names = {u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars().all()}
    record = None
    if delivery is not None:
        snap = delivery.snapshot or {}
        record = {**_event_view(delivery), "figures": snap.get("figures") or {}, "action_items": snap.get("action_items") or [],
                  "owner": snap.get("owner"), "review": snap.get("review"),
                  "tasks": [_task_view(tasks[i], names, today) for i in snap.get("task_ids") or [] if i in tasks],
                  "freshness": freshness(delivery.happened_on, household, today)}
    now = None
    diffs: list[dict] = []
    frozen = (baseline.snapshot or {}).get("figures") if baseline is not None else None
    if with_today and frozen is not None:
        now = await figures_of(session, household.id, firm_id=firm_id)
        diffs = differences(frozen, now)
    review = st["review"]
    return {
        "household_id": str(household.id), "household": household.name, "as_of": today.isoformat(),
        "state": st["state"], "step": STATES.index(st["state"]) + 1, "steps": len(STATES), "owner": OWNERS[st["state"]],
        "version": st["version"], "data": data, "complexity": complexity,
        # The need as it stands now; a recorded baseline carries the need frozen with it, which is the one that binds.
        "review_need": (baseline.snapshot or {}).get("review") if baseline is not None else need_now, "review_need_now": need_now,
        "baseline": None if baseline is None else {**_event_view(baseline), "figures": frozen,
                                                   "complexity": (baseline.snapshot or {}).get("complexity")},
        "review": _event_view(review) if review is not None else None,
        "sent_back": _event_view(st["sent_back"]) if st["sent_back"] is not None else None,
        "scenarios_since_baseline": st["scenarios_since"],
        "plan_of_record": record, "freshness": record["freshness"] if record else None,
        "open_tasks": st["open_tasks"], "tasks": st["tasks"],
        "today": now, "differences": diffs,
        "history": [_event_view(e) for e in reversed(events)],
        "assumption_version": planning.assumption_version(firm.settings or {}),
        "line": line_of(firm), "estimate": True,
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, role: Any = None, user_id: uuid.UUID | None = None,
                   since: Any = None, until: Any = None) -> dict:
    """The firm's plans: coverage, freshness, the QC pass rate and scenario depth, the count in each state, and a row
    per household. An adviser's view counts their own households, as the cockpit does; others the firm's."""
    from app.aurea_core import advisers

    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    end = _day(until, "until") or today
    start = _day(since, "since") or date.fromordinal(end.toordinal() - QC_PERIOD_DAYS)
    if start > end:
        raise PlanRecordError("since is on or before until.")
    homes = list((await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None))
                                        .order_by(Household.name))).scalars().all())
    held = await advisers.of_households(session, firm_id)
    mine = str(role) == "adviser" and user_id is not None
    if mine:
        homes = [h for h in homes if held.get(h.id) == user_id]
    ids = [h.id for h in homes]
    events = await _events(session, firm_id, ids)
    scenarios = await _scenarios(session, firm_id, ids)
    all_events = [e for es in events.values() for e in es]
    tasks = await _tasks(session, firm_id, all_events)
    status = {k: str(t.status) for k, t in tasks.items()}
    uids = set(held.get(h) for h in ids if held.get(h))
    names = {u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars().all()} if uids else {}
    rows, depths, fresh_n, delivered_n, assumed_n = [], [], 0, 0, 0
    passed = failed = 0
    for h in homes:
        es = events.get(h.id, [])
        data = await data_of(session, h.id, firm_id=firm_id)
        st = state_of(es, scenario_ids=scenarios.get(h.id, []), task_status=status, data=data)
        complexity = await complexity_of(session, h.id, firm_id=firm_id)
        need = (st["baseline"].snapshot or {}).get("review") if st["baseline"] is not None else review_need(complexity["score"], line_of(firm))
        delivery = next((e for e in reversed(es) if e.kind == "delivery"), None)
        fresh = freshness(delivery.happened_on, h, today) if delivery is not None else None
        if fresh is not None:
            delivered_n += 1
            fresh_n += fresh["fresh"]
            assumed_n += fresh["cadence_assumed"]
        depths.extend(_depths(es, scenarios.get(h.id, [])))
        for e in es:
            if e.kind == "review" and start <= e.happened_on <= end:
                passed += e.outcome == "passed"
                failed += e.outcome == "failed"
        rows.append({"household_id": str(h.id), "household": h.name, "adviser": names.get(held.get(h.id)) if h.id in held else None,
                     "state": st["state"], "owner": OWNERS[st["state"]], "version": st["version"],
                     "complexity": complexity["score"], "review_needed": bool(need.get("needed")),
                     "missing": len(data["missing"]), "scenarios_since_baseline": st["scenarios_since"],
                     "delivered_on": fresh["delivered_on"] if fresh else None, "fresh": fresh["fresh"] if fresh else None,
                     "open_tasks": st["open_tasks"]})
    reviews_n = passed + failed
    active = len(homes)
    by_state = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    return {
        "household_id": None, "as_of": today.isoformat(), "scope": "own" if mine else "firm",
        "coverage": {"delivered": delivered_n, "active": active, "share": round(delivered_n / active, 4) if active else None},
        "freshness": {"fresh": fresh_n, "stale": delivered_n - fresh_n, "delivered": delivered_n,
                      "share": round(fresh_n / delivered_n, 4) if delivered_n else None, "cadence_assumed": assumed_n,
                      "default_months": DEFAULT_FRESH_MONTHS},
        "qc": {"since": start.isoformat(), "until": end.isoformat(), "reviews": reviews_n, "passed": passed, "failed": failed,
               "pass_rate": round(passed / reviews_n, 4) if reviews_n else None},
        "scenario_depth": {"baselines": len(depths), "scenarios": sum(depths),
                           "median": statistics.median(depths) if depths else None,
                           "mean": round(statistics.mean(depths), 2) if depths else None},
        "by_state": by_state, "states": list(STATES), "households": rows, "line": line_of(firm),
        "assumption_version": planning.assumption_version((firm.settings or {}) if firm else None),
    }


# ── Recording ───────────────────────────────────────────────────────────────

async def _next_seq(session: AsyncSession, household_id: uuid.UUID) -> int:
    got = (await session.execute(select(func.max(PlanRecordEvent.seq)).where(PlanRecordEvent.household_id == household_id))).scalar()
    return int(got or 0) + 1


def _open_baseline(events: list[PlanRecordEvent]) -> tuple[PlanRecordEvent | None, PlanRecordEvent | None]:
    """The current plan version's latest baseline, and its delivery if it has one."""
    if not events:
        return None, None
    version = max(e.plan_version for e in events)
    current = [e for e in events if e.plan_version == version]
    baseline = next((e for e in reversed(current) if e.kind == "baseline"), None)
    delivery = next((e for e in reversed(current) if e.kind == "delivery"), None)
    return baseline, delivery


def _last_review(events: list[PlanRecordEvent], baseline: PlanRecordEvent) -> PlanRecordEvent | None:
    return next((e for e in reversed(events) if e.kind == "review" and e.baseline_id == baseline.id), None)


async def prepare_baseline(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, household_id: Any) -> dict:
    """Everything recording a baseline would refuse, refused without writing, and the figures it would freeze."""
    _may(role, PREPARERS, REFUSED_PREPARE)
    household = await _household(session, household_id, firm_id)
    if household.left_on is not None:
        raise PlanRecordError(f"{household.name} has left the firm; no new plan is recorded for them.")
    data = await data_of(session, household.id, firm_id=firm_id)
    if data["missing"]:
        raise PlanRecordError("A baseline is recorded from a complete file, and these are not on file: "
                              + "; ".join(m["text"] for m in data["missing"]) + ". Record them from their documents first; nothing is filled in.")
    events = (await _events(session, firm_id, [household.id])).get(household.id, [])
    baseline, delivery = _open_baseline(events)
    version = 1 if baseline is None else baseline.plan_version + (1 if delivery is not None else 0)
    firm = await session.get(Firm, firm_id)
    complexity = await complexity_of(session, household.id, firm_id=firm_id)
    need = review_need(complexity["score"], line_of(firm))
    figures = await figures_of(session, household.id, firm_id=firm_id)
    scenario_ids = (await _scenarios(session, firm_id, [household.id])).get(household.id, [])
    return {"household": household, "version": version, "revises": baseline is not None and delivery is None,
            "after_delivery": delivery is not None, "figures": figures, "complexity": complexity, "review": need,
            "scenarios_before": scenario_ids, "data": data, "today": firm_time.today(firm)}


async def record_baseline(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, user_id: uuid.UUID | None,
                          household_id: Any) -> dict:
    """Freeze the plan's figures as they stand. Adds an event; changes nothing else."""
    ready = await prepare_baseline(session, firm_id, role=role, household_id=household_id)
    household = ready["household"]
    row = PlanRecordEvent(firm_id=firm_id, household_id=household.id, seq=await _next_seq(session, household.id), kind="baseline",
                          plan_version=ready["version"], happened_on=ready["today"], recorded_by=recorded_by, recorded_by_id=user_id,
                          recorded_by_role=str(role),
                          snapshot={"figures": ready["figures"], "complexity": ready["complexity"], "review": ready["review"],
                                    "scenarios_before": ready["scenarios_before"],
                                    "data": {"read": ready["data"]["read"], "on_file": ready["data"]["on_file"]}})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="plan_baseline_recorded", actor=recorded_by, content={
        "household_id": str(household.id), "event_id": str(row.id), "plan_version": row.plan_version,
        "assumption_version": ready["figures"]["assumption_version"], "review_needed": ready["review"]["needed"]})
    return await for_household(session, household.id, firm_id=firm_id, with_today=False)


async def prepare_review(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, user_id: uuid.UUID | None,
                         household_id: Any, outcome: Any, findings: Any = None) -> dict:
    """What recording a review would refuse, refused without writing."""
    _may(role, REVIEWERS, REFUSED_REVIEW)
    household = await _household(session, household_id, firm_id)
    events = (await _events(session, firm_id, [household.id])).get(household.id, [])
    baseline, delivery = _open_baseline(events)
    if baseline is None:
        raise PlanRecordError(f"{household.name} has no baseline to review: one is recorded first.")
    if delivery is not None:
        raise PlanRecordError(f"Plan v{baseline.plan_version} was delivered; a new baseline starts the next plan, and that is what is reviewed.")
    last = _last_review(events, baseline)
    if last is not None and last.outcome == "passed":
        raise PlanRecordError(f"This baseline passed review on {last.happened_on.isoformat()}, by {last.recorded_by}.")
    if last is not None and last.outcome == "failed":
        raise PlanRecordError("This baseline failed review and was sent back: a new baseline is recorded, and that is reviewed.")
    same = (baseline.recorded_by_id == user_id) if (baseline.recorded_by_id and user_id) else (baseline.recorded_by == recorded_by)
    if same:
        raise PlanRecordError("A plan is reviewed by a second person: whoever recorded the baseline does not review it.")
    result = str(outcome or "").strip().lower()
    if result not in REVIEW_OUTCOMES:
        raise PlanRecordError("A review passed or failed.")
    words = str(findings or "").strip()
    if result == "failed" and len(words) < 10:
        raise PlanRecordError("A failed review names its findings, so the plan can be put right.")
    firm = await session.get(Firm, firm_id)
    return {"household": household, "baseline": baseline, "outcome": result, "findings": words[:4000] or None,
            "today": firm_time.today(firm)}


async def record_review(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, user_id: uuid.UUID | None, **terms) -> dict:
    """A second person's pass or fail on the current baseline. A fail sends the plan back for a new baseline."""
    ready = await prepare_review(session, firm_id, role=role, recorded_by=recorded_by, user_id=user_id, **terms)
    household, baseline = ready["household"], ready["baseline"]
    row = PlanRecordEvent(firm_id=firm_id, household_id=household.id, seq=await _next_seq(session, household.id), kind="review",
                          plan_version=baseline.plan_version, baseline_id=baseline.id, outcome=ready["outcome"], findings=ready["findings"],
                          happened_on=ready["today"], recorded_by=recorded_by, recorded_by_id=user_id, recorded_by_role=str(role), snapshot={})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="plan_review_recorded", actor=recorded_by, content={
        "household_id": str(household.id), "event_id": str(row.id), "baseline_id": str(baseline.id),
        "plan_version": row.plan_version, "outcome": row.outcome})
    return await for_household(session, household.id, firm_id=firm_id, with_today=False)


def _items(raw: Any, today: date, default_due: date | None) -> list[dict]:
    if raw in (None, "", []):
        return []
    if not isinstance(raw, list):
        raise PlanRecordError("Give the action items as a list, each a title with an optional due date.")
    if len(raw) > MAX_ACTION_ITEMS:
        raise PlanRecordError(f"At most {MAX_ACTION_ITEMS} action items at one delivery.")
    out = []
    for item in raw:
        title, due = (item, None) if isinstance(item, str) else ((item or {}).get("title") or (item or {}).get("item"), (item or {}).get("due_on"))
        title = str(title or "").strip()
        if len(title) < 3:
            raise PlanRecordError("Each action item needs its words.")
        if len(title) > TITLE_MAX:
            raise PlanRecordError(f"An action item is at most {TITLE_MAX} characters.")
        when = _day(due, "An action item's due date") or default_due
        if when is not None and when < today:
            raise PlanRecordError("An action item is due today or later, on the firm's calendar.")
        out.append({"title": title, "due_on": when.isoformat() if when else None})
    return out


async def prepare_delivery(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, household_id: Any, evidence_kind: Any,
                           evidence: Any, action_items: Any = None, due_on: Any = None, delivered_on: Any = None) -> dict:
    """What recording a delivery would refuse, refused without writing, and what it would freeze and raise."""
    from app.aurea_core import advisers

    _may(role, PREPARERS, REFUSED_PREPARE)
    household = await _household(session, household_id, firm_id)
    events = (await _events(session, firm_id, [household.id])).get(household.id, [])
    baseline, delivery = _open_baseline(events)
    if baseline is None:
        raise PlanRecordError(f"{household.name} has no baseline to deliver: a plan is delivered from a recorded baseline.")
    if delivery is not None:
        raise PlanRecordError(f"Plan v{baseline.plan_version} was delivered on {delivery.happened_on.isoformat()}; a new baseline starts the next.")
    need = (baseline.snapshot or {}).get("review") or {"needed": True}
    last = _last_review(events, baseline)
    if last is not None and last.outcome == "failed":
        raise PlanRecordError("This baseline failed review and was sent back: a new baseline is recorded and reviewed first.")
    if need.get("needed") and (last is None or last.outcome != "passed"):
        why = ("every plan is reviewed until the firm sets its complexity line" if need.get("line_assumed")
               else f"its complexity of {need.get('score')} is above the firm's line of {need.get('line')}")
        raise PlanRecordError(f"This plan needs a review by a second person before it is delivered: {why}.")
    kind = str(evidence_kind or "").strip().lower()
    if kind not in EVIDENCE_KINDS:
        raise PlanRecordError("A delivery is recorded from its evidence: say whether it is a document or a meeting note.")
    words = str(evidence or "").strip()
    if len(words) < 5:
        raise PlanRecordError(f"Name the {kind} that shows the plan was delivered.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    on = _day(delivered_on, "delivered_on") or today
    if on > today:
        raise PlanRecordError("A delivery is recorded once it has happened, not before.")
    if on < baseline.happened_on:
        raise PlanRecordError(f"The baseline it delivers was recorded on {baseline.happened_on.isoformat()}; the delivery follows it.")
    items = _items(action_items, today, _day(due_on, "due_on"))
    return {"household": household, "baseline": baseline, "review": last, "need": need, "evidence_kind": kind,
            "evidence": words[:2000], "delivered_on": on, "items": items,
            "owner": await advisers.adviser_of(session, firm_id, household.id), "today": today}


async def record_delivery(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, user_id: uuid.UUID | None, **terms) -> dict:
    """The reviewed baseline went to the client. Copies its frozen figures and raises each action item as a task for
    the household's recorded adviser. Changes no goal, mandate or plan figure."""
    ready = await prepare_delivery(session, firm_id, role=role, **terms)
    household, baseline, owner = ready["household"], ready["baseline"], ready["owner"]
    raised = []
    for item in ready["items"]:
        t = Task(firm_id=firm_id, household_id=household.id, title=item["title"], status=TaskStatus.OPEN,
                 detail=f"From plan v{baseline.plan_version}, delivered {ready['delivered_on'].isoformat()}.",
                 due_date=date.fromisoformat(item["due_on"]) if item["due_on"] else None, source="plan_delivery",
                 subject_label=household.name, assigned_to=owner.id if owner else None, assigned_by=user_id if owner else None)
        session.add(t)
        raised.append(t)
    await session.flush()
    last = ready["review"]
    row = PlanRecordEvent(
        firm_id=firm_id, household_id=household.id, seq=await _next_seq(session, household.id), kind="delivery",
        plan_version=baseline.plan_version, baseline_id=baseline.id, evidence_kind=ready["evidence_kind"], evidence=ready["evidence"],
        happened_on=ready["delivered_on"], recorded_by=recorded_by, recorded_by_id=user_id, recorded_by_role=str(role),
        snapshot={"figures": dict((baseline.snapshot or {}).get("figures") or {}), "baseline_seq": baseline.seq,
                  "baseline_on": baseline.happened_on.isoformat(), "complexity": (baseline.snapshot or {}).get("complexity"),
                  "review": {"needed": bool(ready["need"].get("needed")), "basis": ready["need"].get("basis"),
                             "by": last.recorded_by if last else None, "on": last.happened_on.isoformat() if last else None},
                  "action_items": ready["items"], "task_ids": [str(t.id) for t in raised],
                  "owner": owner.full_name if owner else None})
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="plan_delivered", actor=recorded_by, content={
        "household_id": str(household.id), "event_id": str(row.id), "baseline_id": str(baseline.id), "plan_version": row.plan_version,
        "evidence_kind": row.evidence_kind, "tasks": len(raised),
        "assumption_version": ((baseline.snapshot or {}).get("figures") or {}).get("assumption_version")})
    return await for_household(session, household.id, firm_id=firm_id, with_today=False)
