"""Reporting to FIU-IND: the officers who answer for it, the suspicious report's timeliness and the monthly reports
(B-390, L200-1 India §5.3, §8 and §9).

The Principal Officer and the Designated Director are the firm's recorded registration details (the admin's, with a
reason). A suspicious transaction report's steps carry the Principal Officer of the day (txn_monitoring). Its
timeliness is counted from the investigations on file: the working days from the day suspicion was recorded to the
day the report was recorded filed, against the line in law_figures, weekdays only. A report with no recorded day of
suspicion is not measured.

A Cash Transaction Report or the other monthly reports are what compliance says were filed, with the filing's own
reference, once per month and kind, added and never edited. Nothing here files anything, works out which transactions
belong in a report, or says the firm is compliant. The due day and every line are assumed and unreviewed until counsel
has read them. Compliance and operations read it; no client or adviser does, and nobody tells a client.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.aml_monitoring import AmlInvestigation
from app.models.graph import Household
from app.models.pmla import FiuReport
from app.models.tenant import Firm
from app.provenance import ledger

KINDS = {"ctr": "Cash Transaction Report", "monthly": "Other prescribed monthly reports"}
RECORDERS = frozenset({UserRole.COMPLIANCE})
FIGURES = ("in_pml_str_working_days", "in_pml_cash_threshold", "in_pml_ctr_due_day")
NOTE = ("What compliance recorded as filed. Nothing here files a report or says the firm is compliant; the days and amounts are assumed "
        "and not reviewed by counsel; working days are weekdays, holidays not modelled.")


class FiuError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return firm is not None and markets.code(firm.jurisdiction) == "IN"


def officers(firm: Firm) -> dict:
    recorded = (firm.settings or {}).get("sebi_registration") or {}
    return {"principal_officer": recorded.get("principal_officer") or None, "designated_director": recorded.get("designated_director") or None}


def working_days(start: date, end: date) -> int:
    """Weekdays after `start`, up to and including `end`."""
    n, d = 0, start
    while d < end:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def timeliness(investigations: list, names: dict, *, today: date) -> dict:
    """Each suspicion's report against the line. Pure over the investigations given."""
    line = int(law_figures.value("in_pml_str_working_days"))
    rows = []
    for inv in investigations:
        steps = inv.steps or []
        formed = next((s for s in steps if s.get("step") == "suspicion_formed"), None)
        if formed is None and not inv.reported_on:
            continue
        officer = next((s.get("principal_officer") for s in reversed(steps) if s.get("principal_officer")), None)
        days = None
        if formed is None:
            state = "not_measured"
        elif inv.reported_on:
            days = working_days(inv.suspicion_on, inv.reported_on)
            state = "filed_in_time" if days <= line else "filed_late"
        elif inv.status == "closed":
            state = "no_report"
        else:
            days = working_days(inv.suspicion_on, today)
            state = "overdue" if days > line else "open"
        rows.append({"investigation_id": str(inv.id), "household": names.get(inv.household_id), "state": state, "working_days": days,
                     "suspicion_on": inv.suspicion_on.isoformat() if inv.suspicion_on else None,
                     "reported_on": inv.reported_on.isoformat() if inv.reported_on else None, "reference": inv.report_reference,
                     "principal_officer": officer})
    count = lambda s: sum(1 for r in rows if r["state"] == s)  # noqa: E731
    due = count("filed_in_time") + count("filed_late") + count("overdue")
    order = {"overdue": 0, "filed_late": 1, "open": 2, "not_measured": 3, "filed_in_time": 4, "no_report": 5}
    return {"line_working_days": line, "measured": due, "in_time": count("filed_in_time"), "late": count("filed_late"),
            "overdue": count("overdue"), "open": count("open"), "not_measured": count("not_measured"),
            "in_time_pct": round(100 * count("filed_in_time") / due, 1) if due else None,
            "items": sorted(rows, key=lambda r: (order[r["state"]], r["suspicion_on"] or ""))}


def _month_after(period: str) -> date:
    y, m = int(period[:4]), int(period[5:7])
    return date(y + (m == 12), 1 if m == 12 else m + 1, 1)


def due_on(period: str) -> date:
    return _month_after(period).replace(day=int(law_figures.value("in_pml_ctr_due_day")))


def _periods(firm: Firm, today: date) -> list[str]:
    """The ended months whose due day fell, or falls, after the firm came onto the platform; at most the last twelve."""
    start = firm_time.today(firm, firm.created_at) if getattr(firm, "created_at", None) else today
    out, first = [], today.replace(day=1)
    for _ in range(12):
        previous = first - timedelta(days=1)
        period = f"{previous.year}-{previous.month:02d}"
        if due_on(period) >= start:
            out.append(period)
        first = previous.replace(day=1)
    return out


async def reports(session: AsyncSession, firm: Firm) -> list[dict]:
    today = firm_time.today(firm)
    filed = {(r.kind, r.period): r for r in (await session.execute(select(FiuReport).where(FiuReport.firm_id == firm.id))).scalars().all()}
    wanted = {(k, p) for p in _periods(firm, today) for k in KINDS} | set(filed)
    out = []
    for kind, period in sorted(wanted, key=lambda x: (x[1], x[0]), reverse=True):
        row = filed.get((kind, period))
        due = row.due_on if row else due_on(period)
        state = ("filed_late" if row.filed_on > due else "filed") if row else ("overdue" if due < today else "due")
        out.append({"kind": kind, "label": KINDS[kind], "period": period, "due_on": due.isoformat(), "state": state,
                    "days": (due - today).days, "filed_on": row.filed_on.isoformat() if row else None,
                    "reference": row.reference if row else None, "transactions": row.transactions if row else None,
                    "principal_officer": row.principal_officer if row else None, "recorded_by": row.recorded_by if row else None})
    return out


async def overview(session: AsyncSession, firm: Firm) -> dict | None:
    """The FIU-IND side of the programme, or None outside India."""
    if not applies(firm):
        return None
    today = firm_time.today(firm)
    investigations = list((await session.execute(select(AmlInvestigation).where(AmlInvestigation.firm_id == firm.id))).scalars().all())
    names = {h.id: h.name for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    rows = await reports(session, firm)
    who = officers(firm)
    return {"officers": who, "officers_missing": [k.replace("_", " ") for k, v in who.items() if not v],
            "str": timeliness(investigations, names, today=today), "reports": rows,
            "counts": {s: sum(1 for r in rows if r["state"] == s) for s in ("overdue", "due", "filed", "filed_late")},
            "figures": law_figures.cited(list(FIGURES), today), "note": NOTE}


async def prepare(session: AsyncSession, firm: Firm, role, *, kind: str | None, period: str | None, reference: str | None,
                  filed_on: date | None = None, transactions=None, note: str | None = None) -> dict:
    if not applies(firm):
        raise FiuError("FIU-IND reports are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise FiuError("Compliance records a report to FIU-IND.")
    key = (kind or "").strip().lower()
    if key not in KINDS:
        raise FiuError("The kind is ctr (Cash Transaction Report) or monthly (the other prescribed monthly reports).")
    month = (period or "").strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise FiuError("The period is a month, YYYY-MM.")
    today = firm_time.today(firm)
    if _month_after(month) > today:
        raise FiuError(f"{month} has not ended.")
    day = filed_on or today
    if day > today:
        raise FiuError("A filing cannot be dated in the future.")
    if day < _month_after(month):
        raise FiuError(f"A report for {month} cannot have been filed before the month ended.")
    ref = (reference or "").strip()
    if not 4 <= len(ref) <= 120:
        raise FiuError("Give the filing's own reference, as FIU-IND acknowledged it (up to 120 characters).")
    count = None
    if transactions not in (None, ""):
        try:
            count = int(float(transactions))
        except (TypeError, ValueError):
            raise FiuError("The number of transactions reported is a whole number; 0 for a nil report.")
        if count < 0:
            raise FiuError("The number of transactions reported is a whole number; 0 for a nil report.")
    elif key == "ctr":
        raise FiuError("Say how many transactions the report carried; 0 for a nil report.")
    officer = officers(firm)["principal_officer"]
    if not officer:
        raise FiuError("The firm has no Principal Officer recorded: the admin records it with the firm's registration details "
                       "(record_sebi_registration).")
    existing = (await session.execute(select(FiuReport).where(FiuReport.firm_id == firm.id, FiuReport.kind == key,
                                                              FiuReport.period == month))).scalar_one_or_none()
    if existing is not None:
        raise FiuError(f"The {KINDS[key]} for {month} was recorded filed on {existing.filed_on.isoformat()} by {existing.recorded_by}.")
    return {"kind": key, "label": KINDS[key], "period": month, "due_on": due_on(month), "filed_on": day, "reference": ref,
            "transactions": count, "principal_officer": officer, "note": (note or "").strip() or None}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = FiuReport(created_at=utcnow(), firm_id=firm.id, kind=p["kind"], period=p["period"], due_on=p["due_on"], filed_on=p["filed_on"],
                    reference=p["reference"], transactions=p["transactions"], principal_officer=p["principal_officer"], note=p["note"],
                    recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="fiu_report_recorded", actor=by, content={
        "kind": row.kind, "period": row.period, "filed_on": row.filed_on.isoformat(), "reference": row.reference,
        "principal_officer": row.principal_officer})
    return p
