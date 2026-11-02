"""Transaction monitoring, investigations and suspicious activity reports (L200-1 §5.5).

Scenarios run over each household's money movements, against what the household said to expect when it was onboarded
(the facts the risk rating recorded). An alert is worked out each time it is read, keyed by its scenario and the
movements behind it, so the same movements raise it once; a person decides it, closed with a reason or escalated.
Escalation opens an investigation, or joins the household's open one.

The report's clock is the market's: in the US a SAR is due 30 days from detection, 60 when no subject is identified,
and continuing activity is reviewed at 90 days and reported again within 120; in New Zealand a SAR is due 3 working
days after suspicion is formed; in India an STR 7 working days after. A market with none of these gets no clock, and
says so. Whether to report is compliance's decision, and the reference is the filing's own. Deciding to exit a
client goes through a forum of at least two people, recorded with its minutes.

None of it reaches a client: no client route or tool reads it, and a message to a client under investigation is
refused if it speaks of monitoring, an investigation or a report. A baseline nobody recorded is not a baseline: the
scenarios that need one are not run for that household, and the reason is shown.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets
from app.aurea_core.billing import _mandate_households
from app.models.aml_monitoring import AlertDecision, AmlInvestigation
from app.models.cash_movement import CashRequest
from app.models.graph import Account, Household, Mandate
from app.models.onboarding import OnboardingCase
from app.models.portfolio import Transaction
from app.models.tenant import Firm
from app.provenance import ledger

IN_TYPES = {"deposit", "transfer_in", "contribution"}
OUT_TYPES = {"withdrawal", "transfer_out"}
SCENARIOS = {
    "under_threshold": "Deposits kept under the reporting threshold",
    "in_and_out": "Money in and straight back out",
    "above_baseline": "Payments out above what was declared",
    "undeclared_third_party": "A third-party payment nobody declared",
}
THRESHOLD = {"US": 10000, "NZ": 10000, "IN": 1000000}       # the cash reporting line, in the firm's currency
LOOKBACK_DAYS = 90
REGIMES = {
    "US": {"report": "SAR", "to": "FinCEN", "from": "detection", "days": 30, "no_subject_days": 60, "review_days": 90, "again_days": 120},
    "NZ": {"report": "SAR", "to": "the NZ Police FIU", "from": "suspicion", "working_days": 3},
    "IN": {"report": "STR", "to": "FIU-IND", "from": "suspicion", "working_days": 7},
}
_TIPPING = re.compile(r"suspicious|\bSARs?\b|\bSTRs?\b|investigat|monitoring|FinCEN|\bFIU\b|money laundering|reported (you|your)", re.I)


class MonitoringError(ValueError):
    """This cannot be recorded, with the reason."""


def _key(scenario: str, household_id, ids: list[str]) -> str:
    return f"{scenario}:{str(household_id)[:8]}:" + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:16]


def scenarios(moves: list[dict], baseline: dict | None, *, threshold: float, today: date) -> tuple[list[dict], list[str]]:
    """Run every scenario over one household's movements. Returns the alerts and the scenarios not run, with why."""
    alerts, skipped = [], []
    ins = sorted([m for m in moves if m["direction"] == "in"], key=lambda m: m["on"])
    outs = sorted([m for m in moves if m["direction"] == "out"], key=lambda m: m["on"])

    # Two or more deposits between 80% and 100% of the line within seven days, together over it.
    near = [m for m in ins if threshold * 0.8 <= m["amount"] < threshold]
    used: set[str] = set()
    for i, m in enumerate(near):
        if m["id"] in used:
            continue
        group = [x for x in near[i:] if (x["on"] - m["on"]).days <= 7 and x["id"] not in used]
        if len(group) >= 2 and sum(x["amount"] for x in group) >= threshold:
            used |= {x["id"] for x in group}
            alerts.append({"scenario": "under_threshold", "moves": group, "figures": {"total": round(sum(x["amount"] for x in group), 2),
                                                                                       "count": len(group), "threshold": threshold}})

    # 80% or more of a deposit paid out within fourteen days of it.
    for m in ins:
        after = [o for o in outs if 0 <= (o["on"] - m["on"]).days <= 14]
        if after and sum(o["amount"] for o in after) >= 0.8 * m["amount"]:
            alerts.append({"scenario": "in_and_out", "moves": [m, *after],
                           "figures": {"in": m["amount"], "out": round(sum(o["amount"] for o in after), 2), "days": max((o["on"] - m["on"]).days for o in after)}})

    if baseline is None:
        skipped.append("above_baseline: no expected activity on file")
        skipped.append("undeclared_third_party: no expected activity on file")
        return alerts, skipped

    wires = baseline.get("wires_per_year")
    recent = [o for o in outs if (today - o["on"]).days <= LOOKBACK_DAYS]
    if wires in (None, ""):
        skipped.append("above_baseline: no expected payments per year on file")
    else:
        expected = float(wires) * LOOKBACK_DAYS / 365
        if len(recent) > max(2 * expected, expected + 2):
            alerts.append({"scenario": "above_baseline", "moves": recent,
                           "figures": {"payments": len(recent), "expected": round(expected, 1), "days": LOOKBACK_DAYS}})
    if baseline.get("third_party_payments") is True:
        pass
    elif baseline.get("third_party_payments") is None:
        skipped.append("undeclared_third_party: third-party payments not declared either way")
    else:
        third = [o for o in outs if o.get("third_party")]
        for o in third:
            alerts.append({"scenario": "undeclared_third_party", "moves": [o], "figures": {"amount": o["amount"], "payee": o.get("payee")}})
    return alerts, skipped


def _add_working_days(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def clock(inv: AmlInvestigation, code: str, today: date) -> dict:
    """When the report is due, from the market's regime."""
    regime = REGIMES.get(code)
    if regime is None:
        return {"regime": None, "due": None, "says": f"No reporting clock is set for {code or 'this market'}."}
    out = {"regime": {k: v for k, v in regime.items()}, "due": None, "review_due": None, "again_due": None}
    if inv.reported_on and regime.get("review_days"):
        out["review_due"] = (inv.reported_on + timedelta(days=regime["review_days"])).isoformat()
        out["again_due"] = (inv.reported_on + timedelta(days=regime["again_days"])).isoformat()
    if inv.reported_on:
        return out
    if regime["from"] == "detection":
        due = inv.detected_on + timedelta(days=regime["days"] if inv.subject_known else regime["no_subject_days"])
    elif inv.suspicion_on:
        due = _add_working_days(inv.suspicion_on, regime["working_days"])
    else:
        return {**out, "says": "The clock starts when suspicion is formed; that is not recorded."}
    out["due"] = due.isoformat()
    out["days_left"] = (due - today).days
    return out


async def _households(session: AsyncSession, firm_id: uuid.UUID) -> tuple[dict, dict]:
    """account id -> household id, and household id -> name."""
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()
    owners = await _mandate_households(session, firm_id, list(mandates))
    accounts = (await session.execute(select(Account).where(Account.firm_id == firm_id))).scalars().all()
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    return {a.id: owners.get(a.mandate_id) for a in accounts}, names


async def _baselines(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """household id -> the expected activity its onboarding recorded."""
    out = {}
    for c in (await session.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm_id))).scalars().all():
        hid = (c.materialized or {}).get("household_id")
        cdd = (c.intake or {}).get("cdd")
        if hid and cdd:
            out[uuid.UUID(str(hid))] = cdd
    return out


async def _moves(session: AsyncSession, firm_id: uuid.UUID, account_household: dict, since: date) -> dict:
    by: dict = {}
    for t in (await session.execute(select(Transaction).where(Transaction.firm_id == firm_id, Transaction.trade_date >= since,
                                                              Transaction.txn_type.in_(IN_TYPES | OUT_TYPES)))).scalars().all():
        hid = account_household.get(t.account_id)
        if hid:
            by.setdefault(hid, []).append({"id": f"t:{t.id}", "on": t.trade_date, "amount": abs(float(t.amount)),
                                           "direction": "in" if t.txn_type in IN_TYPES else "out", "kind": t.txn_type})
    for r in (await session.execute(select(CashRequest).where(CashRequest.firm_id == firm_id, CashRequest.status == "paid",
                                                              CashRequest.paid_on >= since))).scalars().all():
        hid = account_household.get(r.account_id)
        if hid:
            by.setdefault(hid, []).append({"id": f"c:{r.id}", "on": r.paid_on, "amount": float(r.amount), "direction": "out", "kind": r.kind,
                                           "third_party": r.third_party, "payee": r.payee})
    return by


def _view_inv(inv: AmlInvestigation, name: str | None, code: str, today: date) -> dict:
    return {"id": str(inv.id), "household_id": str(inv.household_id), "household": name, "status": inv.status, "detected_on": inv.detected_on.isoformat(),
            "subject_known": inv.subject_known, "suspicion_on": inv.suspicion_on.isoformat() if inv.suspicion_on else None,
            "reported_on": inv.reported_on.isoformat() if inv.reported_on else None, "report_reference": inv.report_reference,
            "steps": inv.steps or [], "clock": clock(inv, code, today),
            "exit": next((s for s in reversed(inv.steps or []) if s.get("step") == "exit_decision"), None)}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, household_id: uuid.UUID | None = None) -> dict:
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    code = markets.code(firm.jurisdiction)
    threshold = float((firm.settings or {}).get("cash_reporting_threshold") or THRESHOLD.get(code, 10000))
    account_household, names = await _households(session, firm_id)
    baselines = await _baselines(session, firm_id)
    moves = await _moves(session, firm_id, account_household, today - timedelta(days=LOOKBACK_DAYS + 30))
    decided = {d.alert_key: d for d in (await session.execute(select(AlertDecision).where(AlertDecision.firm_id == firm_id))).scalars().all()}
    alerts, not_run = [], {}
    for hid, rows in moves.items():
        if household_id is not None and hid != household_id:
            continue
        found, skipped = scenarios(rows, baselines.get(hid), threshold=threshold, today=today)
        if skipped:
            not_run[str(hid)] = skipped
        for a in found:
            key = _key(a["scenario"], hid, [m["id"] for m in a["moves"]])
            d = decided.get(key)
            alerts.append({"key": key, "household_id": str(hid), "household": names.get(hid), "scenario": a["scenario"], "label": SCENARIOS[a["scenario"]],
                           "figures": a["figures"], "first_on": min(m["on"] for m in a["moves"]).isoformat(), "last_on": max(m["on"] for m in a["moves"]).isoformat(),
                           "moves": [{**m, "on": m["on"].isoformat()} for m in a["moves"]],
                           "decision": {"decision": d.decision, "reason": d.reason, "by": d.decided_by} if d else None})
    q = select(AmlInvestigation).where(AmlInvestigation.firm_id == firm_id)
    if household_id is not None:
        q = q.where(AmlInvestigation.household_id == household_id)
    investigations = [_view_inv(i, names.get(i.household_id), code, today) for i in (await session.execute(q)).scalars().all()]
    open_alerts = [a for a in alerts if a["decision"] is None]
    scanned = len([h for h in moves if household_id is None or h == household_id])
    return {"alerts": sorted(alerts, key=lambda a: (a["decision"] is not None, a["last_on"]), reverse=False),
            "investigations": sorted(investigations, key=lambda i: (i["status"] != "open", i["clock"].get("due") or "9999")),
            "summary": {"open_alerts": len(open_alerts), "households_scanned": scanned,
                        "alert_rate_pct": round(100 * len({a["household_id"] for a in alerts}) / scanned, 1) if scanned else None,
                        "open_investigations": sum(1 for i in investigations if i["status"] == "open"),
                        "reports_due_7_days": sum(1 for i in investigations if i["clock"].get("days_left") is not None and i["clock"]["days_left"] <= 7)},
            "not_run": not_run, "threshold": threshold, "regime": REGIMES.get(code), "market": code, "as_of": today.isoformat(),
            "household_id": str(household_id) if household_id else None}


# ── Decisions ────────────────────────────────────────────────────────────────

async def prepare_alert(session: AsyncSession, firm_id: uuid.UUID, *, alert_key: str | None, decision: str | None, reason: str | None,
                        subject_known: bool | None = None) -> dict:
    view = await for_firm(session, firm_id)
    alert = next((a for a in view["alerts"] if a["key"] == (alert_key or "").strip()), None)
    if alert is None:
        raise MonitoringError("No such alert: read the monitoring queue for its key.")
    if alert["decision"]:
        raise MonitoringError(f"Already decided: {alert['decision']['decision']} by {alert['decision']['by']}.")
    decision = (decision or "").strip().lower()
    if decision not in ("closed", "escalated"):
        raise MonitoringError("An alert is closed, with the reason it is explained, or escalated to an investigation.")
    why = (reason or "").strip()
    if len(why) < 15:
        raise MonitoringError("Give the reason, as you would to an examiner: what explains it, or what does not.")
    hid = uuid.UUID(alert["household_id"])
    existing = (await session.execute(select(AmlInvestigation).where(AmlInvestigation.household_id == hid,
                                                                      AmlInvestigation.status == "open"))).scalars().first()
    return {"alert": alert, "decision": decision, "reason": why, "household_id": hid, "joins": existing,
            "subject_known": True if subject_known is None else bool(subject_known)}


async def decide_alert(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_alert(session, firm_id, **terms)
    a = r["alert"]
    inv = None
    if r["decision"] == "escalated":
        inv = r["joins"]
        if inv is None:
            inv = AmlInvestigation(firm_id=firm_id, household_id=r["household_id"], detected_on=date.fromisoformat(a["last_on"]),
                                   subject_known=r["subject_known"], opened_by=by, steps=[])
            session.add(inv)
            await session.flush()
        inv.steps = [*(inv.steps or []), {"step": "alert_added", "alert": a["key"], "scenario": a["scenario"], "by": by,
                                          "on": firm_time.today(await session.get(Firm, firm_id)).isoformat(), "note": r["reason"]}]
    session.add(AlertDecision(firm_id=firm_id, household_id=r["household_id"], alert_key=a["key"], scenario=a["scenario"], decision=r["decision"],
                              reason=r["reason"], facts={k: a[k] for k in ("figures", "moves", "first_on", "last_on")}, investigation_id=inv.id if inv else None,
                              decided_by=by))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="aml_alert_decided", actor=by,
                              content={"alert": a["key"], "decision": r["decision"], "investigation": str(inv.id) if inv else None})
    return await for_firm(session, firm_id)


STEPS = ("suspicion_formed", "reported", "no_report", "continuing_review", "exit_decision", "closed")


async def prepare_step(session: AsyncSession, firm_id: uuid.UUID, *, investigation_id: uuid.UUID, step: str | None, on: date | None,
                       note: str | None, reference: str | None = None, attendees: list[str] | None = None, outcome: str | None = None,
                       minutes: str | None = None) -> dict:
    inv = await session.get(AmlInvestigation, investigation_id)
    if inv is None or inv.firm_id != firm_id:
        raise MonitoringError("Investigation not found.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    step = (step or "").strip().lower()
    if step not in STEPS:
        raise MonitoringError(f"The step is one of {', '.join(STEPS)}.")
    day = on or today
    if day > today:
        raise MonitoringError("Nothing is recorded ahead of the day it happened.")
    text = (note or "").strip()
    if len(text) < 15:
        raise MonitoringError("Say what was weighed, as you would to an examiner.")
    if inv.status == "closed":
        raise MonitoringError("This investigation is closed.")
    entry = {"step": step, "on": day.isoformat(), "note": text}
    if step == "reported":
        if not (reference or "").strip():
            raise MonitoringError("A report is recorded with the filing's own reference.")
        entry["reference"] = reference.strip()
    if step == "no_report" and inv.reported_on:
        raise MonitoringError("A report has been filed; record a continuing review instead.")
    if step == "continuing_review" and not inv.reported_on:
        raise MonitoringError("A continuing review follows a filed report.")
    if step == "exit_decision":
        people = [p.strip() for p in (attendees or []) if p.strip()]
        if len(set(p.lower() for p in people)) < 2:
            raise MonitoringError("An exit is decided by a forum: name at least two people who took part.")
        if (outcome or "").strip().lower() not in ("exit", "retain"):
            raise MonitoringError("The forum's outcome is exit or retain.")
        if len((minutes or "").strip()) < 5:
            raise MonitoringError("Say where the forum's minutes are filed.")
        entry.update({"attendees": people, "outcome": outcome.strip().lower(), "minutes": minutes.strip()})
    return {"investigation": inv, "entry": entry, "day": day, "firm": firm}


async def record_step(session: AsyncSession, firm_id: uuid.UUID, *, by: str, **terms) -> dict:
    r = await prepare_step(session, firm_id, **terms)
    inv, entry, day = r["investigation"], {**r["entry"], "by": by}, r["day"]
    if entry["step"] == "suspicion_formed" and inv.suspicion_on is None:
        inv.suspicion_on = day
    if entry["step"] == "reported":
        inv.reported_on, inv.report_reference, inv.status = day, entry["reference"], "reported"
        inv.suspicion_on = inv.suspicion_on or day
    if entry["step"] in ("no_report", "closed"):
        inv.status = "closed"
    inv.steps = [*(inv.steps or []), entry]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="aml_investigation_step", actor=by,
                              content={"investigation": str(inv.id), "step": entry["step"], "on": entry["on"]})
    return await for_firm(session, firm_id)


async def tipping_off(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID, body: str) -> None:
    """Refuse words to a client that would tell them they are being looked at."""
    open_inv = (await session.execute(select(AmlInvestigation.id).where(AmlInvestigation.firm_id == firm_id, AmlInvestigation.household_id == household_id,
                                                                         AmlInvestigation.status.in_(("open", "reported"))))).first()
    if open_inv and _TIPPING.search(body or ""):
        raise MonitoringError("This message cannot be sent as written: a message to a client that mentions monitoring, an investigation or a "
                              "report goes to compliance first. Rewrite it without that.")
