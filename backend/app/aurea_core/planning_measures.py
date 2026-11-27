"""India's planning measures (B-400, L200-2 India sections 1, 5.3, 7 and 11): SIP health against plan, emergency fund
completion, will and nomination completeness, and the construction-first order they are read in.

Every figure is read from what a person recorded, through the module that owns it: SIPs, debit mandates and first debits,
goals' recorded saving, term and health cover, the emergency fund record, wills and nominations. Nothing is estimated, no
target is supplied, and a thing not on file reads "not assessed" or "not on file", which does not mean it does not exist.
Nothing here changes a figure, places an order or tells a client.

**The order** is protection first, the emergency fund second, the documents third and investing fourth: `ORDER`. A step is
done only when what is on file shows it, and `next` is the first one that is not. It orders what to look at and holds
nothing back: no order, SIP or proposal is stopped by it.

**SIP health.** What is registered each month (the next instalment of each SIP that has not ended) against what a person
recorded as each goal's monthly saving. A goal with none recorded is not in the plan figure. Registered is not paid: the
mandate behind each SIP, failed registrations and first debits, how many were put right since, and the instalments missed
in the last year are counted beside it.

**Emergency fund.** The target a person set, what they recorded as set aside and where, from its statement. The months of
essential spending it covers are given where that spending is on file. The record adds no money to anything.

**Wills and nominations** are the estate register's (`estate_register`, B-398), counted. A will is current when one is
on file and its yearly check against the register stands: one never checked, due, overtaken by a change or marked as
needing one is on file and not current. Nominations are counted over every asset on the register that can carry one
(demat accounts and folios, deposits, provident and pension accounts, life and accident policies), covered by a nominee
or a recorded opt-out.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import balance_sheet, estate_register, firm_time, health_cover, markets, protection
from app.core.db import utcnow
from app.models.emergency_fund import EmergencyFund
from app.models.enums import UserRole
from app.models.india_batch_a import DebitMandate
from app.models.graph import Goal, Household
from app.models.india_money import MandateDebit
from app.models.sip import Sip
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
ORDER = ("protection", "emergency_fund", "documents", "investing")
STEP_LABELS = {"protection": "Protection", "emergency_fund": "Emergency fund", "documents": "Will and nominations", "investing": "Investing"}
STATE_LABELS = {"done": "In place", "short": "Short", "building": "Building", "not_started": "Not started", "not_assessed": "Not assessed"}
MISSED_WINDOW_DAYS = 365
NOTE = "Read from what is on file. Not on file does not mean none exists."


class MeasureError(ValueError):
    """What cannot be recorded or read, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def _share(part: float, whole: float) -> float | None:
    return round(part / whole, 3) if whole else None


# ── The emergency fund ────────────────────────────────────────────────────────

async def _latest_fund(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> EmergencyFund | None:
    return (await session.execute(select(EmergencyFund).where(
        EmergencyFund.firm_id == firm_id, EmergencyFund.household_id == household_id)
        .order_by(EmergencyFund.created_at.desc(), EmergencyFund.id.desc()))).scalars().first()


async def emergency_fund(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    row = await _latest_fund(session, firm.id, household_id)
    items, _ = balance_sheet.in_currency(await balance_sheet.current_items(session, household_id, firm_id=firm.id), firm)
    essential = sum(float(i.amount) for i in items if i.kind == "essential_spending")
    base = {"essential_spending_a_year": round(essential, 2) if essential else None}
    if row is None:
        return {**base, "state": "not_assessed", "why": "none_on_file", "target": None, "held": None, "share": None, "gap": None,
                "months_covered": None, "target_months": None}
    target, held = float(row.target_amount), float(row.held_amount)
    monthly = essential / 12 if essential else None
    return {**base, "state": "done" if held >= target else "building", "why": None, "target": round(target, 2), "held": round(held, 2),
            "share": _share(min(held, target), target), "gap": round(max(target - held, 0.0), 2),
            "months_covered": round(held / monthly, 1) if monthly else None,
            "target_months": round(target / monthly, 1) if monthly else None,
            "held_in": row.held_in, "statement_date": row.statement_date.isoformat(),
            "complete_by": row.complete_by.isoformat() if row.complete_by else None, "build_months": row.build_months,
            "document": row.source_document, "recorded_by": row.recorded_by}


def _amount(value, label: str, *, zero_ok: bool = False) -> float:
    try:
        n = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        raise MeasureError(f"{label} is an amount in rupees.") from None
    if n != n or n in (float("inf"), float("-inf")) or n < 0 or (n == 0 and not zero_ok):
        raise MeasureError(f"{label} must be {'nothing or more' if zero_ok else 'more than nothing'}.")
    if n >= 1e15:
        raise MeasureError(f"{label} is too large to record.")
    return round(n, 2)


def _day(value, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise MeasureError(f"Give {label} as YYYY-MM-DD.") from None


async def prepare_fund(session: AsyncSession, firm: Firm, role, *, household_id, target_amount=None, held_amount=None, held_in=None,
                       statement_date=None, document=None, complete_by=None, build_months=None) -> dict:
    """Everything recording would refuse, refused without writing, and what would be recorded."""
    if role not in RECORDERS:
        raise MeasureError("An adviser or a paraplanner records a household's emergency fund.")
    if not applies(firm):
        raise MeasureError("The emergency fund is recorded for an Indian firm's households.")
    try:
        hid = household_id if isinstance(household_id, uuid.UUID) else uuid.UUID(str(household_id))
    except (TypeError, ValueError):
        raise MeasureError("Give the household's id from the client search.") from None
    household = await session.get(Household, hid)
    if household is None or household.firm_id != firm.id:
        raise MeasureError("Household not found.")
    if household.left_on is not None:
        raise MeasureError(f"{household.name} has left the firm.")
    if target_amount in (None, ""):
        raise MeasureError("Give the target the household set. None is worked out or supplied.")
    if held_amount in (None, ""):
        raise MeasureError("Give what is set aside now, from its statement (nothing set aside is 0).")
    target, held = _amount(target_amount, "The target"), _amount(held_amount, "What is set aside", zero_ok=True)
    where = " ".join(str(held_in or "").split())
    if held > 0 and len(where) < 3:
        raise MeasureError("Say where it is held: the liquid fund, sweep deposit or account the statement is for.")
    if len(where) > 200:
        raise MeasureError("Where it is held is at most 200 characters.")
    proof = " ".join(str(document or "").split())
    if len(proof) < 5:
        raise MeasureError("Say which statement or signed note this comes from.")
    if len(proof) > 300:
        raise MeasureError("The document's name is at most 300 characters.")
    today = firm_time.today(firm)
    stated = _day(statement_date, "the statement's date")
    if stated is None:
        raise MeasureError("Give the statement's date.")
    if stated > today:
        raise MeasureError("A statement cannot be dated in the future.")
    by_day = _day(complete_by, "the day it is to be complete")
    months = None
    if build_months not in (None, ""):
        try:
            months = int(build_months)
        except (TypeError, ValueError):
            raise MeasureError("The months of the build are a whole number.") from None
        if not 1 <= months <= 60:
            raise MeasureError("A build is planned over 1 to 60 months.")
    before = await _latest_fund(session, firm.id, hid)
    if before is not None and (float(before.target_amount), float(before.held_amount), before.held_in or "", before.statement_date, before.complete_by,
                               before.build_months) == (target, held, where, stated, by_day, months):
        raise MeasureError("That is already what is recorded for this household's emergency fund.")
    return {"household": household, "target": target, "held": held, "held_in": where or None, "statement_date": stated, "document": proof,
            "complete_by": by_day, "build_months": months, "before": before}


async def record_fund(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> EmergencyFund:
    p = await prepare_fund(session, firm, role, **terms)
    row = EmergencyFund(firm_id=firm.id, household_id=p["household"].id, target_amount=p["target"], held_amount=p["held"], held_in=p["held_in"],
                        statement_date=p["statement_date"], complete_by=p["complete_by"], build_months=p["build_months"],
                        source_document=p["document"], recorded_by=by, created_at=utcnow())
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="emergency_fund_recorded", actor=by, content={
        "household_id": str(row.household_id), "target": p["target"], "held": p["held"], "held_in": p["held_in"],
        "statement_date": p["statement_date"].isoformat(), "document": p["document"]})
    return row


# ── SIP health against plan ───────────────────────────────────────────────────

async def sip_health(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    from app.aurea_core import india_money, sip as sip_engine, sip_mandates
    from app.aurea_core.performance import household_account_ids

    today = firm_time.today(firm)
    account_ids = await household_account_ids(session, household_id, firm_id=firm.id) or []
    views = (await sip_engine.for_firm(session, firm, household_id=household_id))["sips"]
    active = [v for v in views if v["status"] == "active"]
    goals = (await session.execute(select(Goal).where(Goal.firm_id == firm.id, Goal.household_id == household_id)
                                   .order_by(Goal.priority, Goal.name))).scalars().all()
    goal_rows, planned_total = [], 0.0
    for g in goals:
        yearly = (g.assumptions or {}).get("annual_contribution")
        planned = round(float(yearly) / 12, 2) if yearly else None
        linked = [v for v in active if v["goal_id"] == str(g.id)]
        if planned is None and not linked:
            continue
        registered = round(sum(v["amount"] for v in linked), 2)
        planned_total += planned or 0.0
        goal_rows.append({"goal_id": str(g.id), "goal": g.name, "planned_monthly": planned, "registered_monthly": registered,
                          "share": _share(registered, planned) if planned else None, "sips": len(linked),
                          "step_up": sum(1 for v in linked if v["step_up"])})
    registered_total = round(sum(v["amount"] for v in active), 2)
    in_force = sum(1 for v in active if (v.get("mandate") or {}).get("state") == "active")
    no_mandate = sum(1 for v in active if (v.get("mandate") or {}).get("state") in (None, "none"))

    # Every registration or first debit that failed on these accounts, and how many have since been put right: a mandate
    # recorded at or after the failure on the same account is in force, and its own first debit has not failed. A later
    # request that is not yet registered puts nothing right.
    ids = account_ids or [uuid.uuid4()]
    rows = await sip_mandates.standing(session, firm.id, account_ids) if account_ids else []
    debits = await india_money.debits(session, firm.id, ids)
    failed: dict[tuple, object] = {}
    for r in (await session.execute(select(DebitMandate).where(DebitMandate.firm_id == firm.id, DebitMandate.account_id.in_(ids))
                                    .order_by(DebitMandate.created_at))).scalars().all():
        if r.kind == "failed" or r.status == "rejected":
            failed.setdefault((r.account_id, r.reference), r.created_at)
    for d in (await session.execute(select(MandateDebit).where(MandateDebit.firm_id == firm.id, MandateDebit.account_id.in_(ids),
                                                               MandateDebit.outcome == "failed").order_by(MandateDebit.created_at))).scalars().all():
        failed.setdefault((d.account_id, d.reference), d.created_at)

    def working(r) -> bool:
        debit = debits.get((r.account_id, r.reference))
        return sip_mandates.state_of(r, today) == "active" and (debit is None or debit.outcome != "failed")

    def put_right(account_id, reference, since) -> bool:
        return any(r.account_id == account_id and working(r) and (r.reference == reference
                   or r.created_at.replace(tzinfo=None) >= since.replace(tzinfo=None)) for r in rows)

    recovered = sum(1 for (account_id, reference), since in failed.items() if put_right(account_id, reference, since))
    open_failures = len(failed) - recovered
    since = (today - timedelta(days=MISSED_WINDOW_DAYS)).isoformat()
    sip_rows = (await session.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.account_id.in_(account_ids or [uuid.uuid4()])))).scalars().all()
    missed = sum(1 for s in sip_rows for h in (s.history or []) if h.get("status") == "missed" and str(h.get("on") or "") >= since)
    stepped = sum(1 for v in active if v["step_up"])

    if not views and not planned_total:
        state, why = "not_assessed", "no_sip_and_no_planned_saving"
    elif not active:
        state, why = "not_started", "no_sip_registered"
    elif not planned_total:
        state, why = "not_assessed", "no_planned_saving_recorded"
    elif registered_total + 0.005 < planned_total or open_failures or no_mandate:
        state, why = "short", "below_plan" if registered_total + 0.005 < planned_total else "debit_not_working"
    else:
        state, why = "done", None
    return {"state": state, "why": why, "active": len(active), "registered_monthly": registered_total,
            "planned_monthly": round(planned_total, 2) or None, "share": _share(registered_total, planned_total) if planned_total else None,
            "mandate_in_force": in_force, "without_mandate": no_mandate,
            "step_up_registered": stepped, "step_up_share": _share(stepped, len(active)),
            "failures": {"ever": len(failed), "open": open_failures, "recovered": recovered, "recovery_share": _share(recovered, len(failed))},
            "missed_last_year": missed, "goals": goal_rows,
            "sips": [{"id": v["id"], "fund": v["fund"], "account": v["account"], "amount": v["amount"], "goal": v["goal"],
                      "step_up": (v["step_up"] or {}).get("label"), "mandate": (v.get("mandate") or {}).get("label")} for v in active],
            "mandates_on_file": len(rows)}


# ── Wills and nominations ─────────────────────────────────────────────────────

WILL_STATES = {"checked": "current", "never_checked": "not_checked", "due": "to_check", "changed_since": "to_check", "needs_change": "to_check"}


async def completeness(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """Wills and nominations as the estate register reads them (`estate_register`, B-398): this adds the shares and
    works nothing out again, so the measure and the register cannot disagree."""
    reg = await estate_register.household_register(session, firm, household_id)
    wills = [{"person_id": p["person_id"], "person": p["name"], "signed_on": (p["will"] or {}).get("signed_on"),
              "state": "not_on_file" if p["will"] is None else WILL_STATES[p["check"]["state"]]} for p in reg["people"]]
    current = sum(1 for w in wills if w["state"] == "current")
    items = [{"key": a["key"], "category": a["category"], "label": a["label"], "person": a["owner"], "how": a["nomination"]["state"],
              "covered": a["nomination"]["state"] != "not_on_file"} for a in reg["assets"] if a["nomination"]["state"] != "not_applicable"]
    covered = sum(1 for i in items if i["covered"])
    wills_complete = bool(wills) and current == len(wills)
    noms_complete = bool(items) and covered == len(items)
    state = "not_assessed" if not wills else "done" if wills_complete and noms_complete else "short"
    return {"state": state,
            "wills": {"adults": len(wills), "current": current, "on_file": sum(1 for w in wills if w["state"] != "not_on_file"),
                      "not_on_file": sum(1 for w in wills if w["state"] == "not_on_file"), "share": _share(current, len(wills)),
                      "complete": wills_complete if wills else None, "people": wills},
            "nominations": {"assets": len(items), "covered": covered, "share": _share(covered, len(items)),
                            "complete": noms_complete if items else None, "items": items}}


# ── Protection ────────────────────────────────────────────────────────────────

async def protection_step(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    life = await protection.for_household(session, household_id, firm_id=firm.id)
    health = await health_cover.for_household(session, household_id, firm_id=firm.id)
    people = (life or {}).get("people") or []
    earners = [p for p in people if p["death"]["status"] != "not_assessed"]
    gaps = [p for p in earners if p["death"].get("gap")]
    unassessed = len(people) - len(earners)
    members = [m for m in ((health or {}).get("members") or []) if m["group"] != "parent"]
    own = sum(1 for m in members if m["position"] == "own")
    term = {"people": len(people), "assessed": len(earners), "with_gap": len(gaps), "not_assessed": unassessed,
            "met": len(earners) - len(gaps), "gap": round(sum(p["death"]["gap"] for p in gaps), 2),
            "rows": [{"person": p["name"], "need": p["death"].get("need"), "cover": p["death"].get("cover"), "gap": p["death"].get("gap"),
                      "status": p["death"]["status"]} for p in people]}
    cover = {"members": len(members), "own": own, "employer_only": sum(1 for m in members if m["position"] == "employer_only"),
             "none_on_file": sum(1 for m in members if m["position"] == "none_on_file"),
             "rows": [{"person": m["name"], "position": m["position"]} for m in members]}
    if gaps or (members and own < len(members) and any(m["position"] != "none_on_file" for m in members)):
        state = "short"
    elif not earners or unassessed or not members or own < len(members):
        state = "not_assessed"
    else:
        state = "done"
    return {"state": state, "term": term, "health": cover}


# ── The household and the firm ────────────────────────────────────────────────

def _chip(state: str) -> str:
    return STATE_LABELS[state]


async def for_household(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        return None
    if not applies(firm) or not _indian(firm, household):
        return {"applies": False, "scope": "household", "household_id": str(household_id), "household": household.name,
                "why": "These measures are India's: they are read for an Indian household."}
    cover = await protection_step(session, firm, household_id)
    fund = await emergency_fund(session, firm, household_id)
    docs = await completeness(session, firm, household_id)
    sips = await sip_health(session, firm, household_id)
    states = {"protection": cover["state"], "emergency_fund": fund["state"], "documents": docs["state"], "investing": sips["state"]}
    order = [{"step": i + 1, "key": k, "label": STEP_LABELS[k], "state": states[k], "chip": _chip(states[k])} for i, k in enumerate(ORDER)]
    return {"applies": True, "scope": "household", "household_id": str(household_id), "household": household.name,
            "currency": firm.base_currency, "as_of": firm_time.today(firm).isoformat(), "order": order,
            "next": next((s["key"] for s in order if s["state"] != "done"), None),
            "in_place": sum(1 for s in order if s["state"] == "done"),
            "protection": cover, "emergency_fund": fund, "documents": docs, "sip_health": sips, "note": NOTE}


def _indian(firm: Firm, household: Household) -> bool:
    market = markets.of_household(firm, household.values)
    return market is not None and market.code == "IN"


async def for_firm(session: AsyncSession, firm: Firm) -> dict:
    """Every current household's four steps, and each measure as a share of the households or items it covers. A share
    counts every household: one with nothing on file is in the denominator and is not counted as met."""
    if not applies(firm):
        return {"applies": False, "scope": "firm", "why": "These measures are India's: they are read for an Indian firm."}
    homes = (await session.execute(select(Household).where(Household.firm_id == firm.id, Household.left_on.is_(None))
                                   .order_by(Household.name))).scalars().all()
    views = [await for_household(session, firm, h.id) for h in homes]
    views = [v for v in views if v and v["applies"]]
    n = len(views)

    def total(get) -> float:
        return sum(get(v) or 0 for v in views)

    covered, items = total(lambda v: v["documents"]["nominations"]["covered"]), total(lambda v: v["documents"]["nominations"]["assets"])
    planned = total(lambda v: v["sip_health"]["planned_monthly"])
    planned_homes = [v for v in views if v["sip_health"]["planned_monthly"]]
    registered = sum(v["sip_health"]["registered_monthly"] for v in planned_homes)
    active, stepped = total(lambda v: v["sip_health"]["active"]), total(lambda v: v["sip_health"]["step_up_registered"])
    ever, recovered = total(lambda v: v["sip_health"]["failures"]["ever"]), total(lambda v: v["sip_health"]["failures"]["recovered"])
    with_adults = [v for v in views if v["documents"]["wills"]["adults"]]
    wills_done = sum(1 for v in with_adults if v["documents"]["wills"]["complete"])
    both_done = sum(1 for v in with_adults if v["documents"]["state"] == "done")
    funded = sum(1 for v in views if v["emergency_fund"]["state"] == "done")
    protected = sum(1 for v in views if v["protection"]["state"] == "done")
    shares = {
        "protection": {"met": protected, "of": n, "share": _share(protected, n),
                       "term_gap": round(total(lambda v: v["protection"]["term"]["gap"]), 2)},
        "emergency_fund": {"met": funded, "of": n, "share": _share(funded, n),
                           "building": sum(1 for v in views if v["emergency_fund"]["state"] == "building"),
                           "not_on_file": sum(1 for v in views if v["emergency_fund"]["state"] == "not_assessed")},
        "wills": {"met": wills_done, "of": len(with_adults), "share": _share(wills_done, len(with_adults))},
        "nominations": {"met": int(covered), "of": int(items), "share": _share(covered, items)},
        "will_and_nominations": {"met": both_done, "of": len(with_adults), "share": _share(both_done, len(with_adults))},
        "sip_against_plan": {"registered_monthly": round(registered, 2), "planned_monthly": round(planned, 2) or None,
                             "share": _share(registered, planned), "households_with_a_plan": len(planned_homes), "of": n},
        "step_up": {"met": int(stepped), "of": int(active), "share": _share(stepped, active)},
        "mandate_recovery": {"met": int(recovered), "of": int(ever), "share": _share(recovered, ever)},
    }
    rows = [{"household_id": v["household_id"], "household": v["household"], "next": v["next"], "in_place": v["in_place"],
             "order": v["order"], "emergency_share": v["emergency_fund"]["share"], "will_share": v["documents"]["wills"]["share"],
             "nomination_share": v["documents"]["nominations"]["share"], "sip_share": v["sip_health"]["share"],
             "term_gap": v["protection"]["term"]["gap"]} for v in views]
    rows.sort(key=lambda r: (r["in_place"], r["household"]))
    return {"applies": True, "scope": "firm", "currency": firm.base_currency, "as_of": firm_time.today(firm).isoformat(),
            "households": rows, "measures": shares, "steps": [{"key": k, "label": STEP_LABELS[k]} for k in ORDER], "note": NOTE}
