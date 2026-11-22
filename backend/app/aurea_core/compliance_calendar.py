"""What an Indian licence must file, and when, and who at the firm answers for it (R7).

Each obligation is the licence's, from its master circular, with the paragraph it rests on: an adviser's monthly
complaint data, half-yearly report to IAASB, deposit revision, annual compliance audit and segregation certificate, and a
portfolio manager's monthly report to SEBI and APMI. The due day is worked from the firm's calendar; where the circular
gives a cadence and no day, the day used is named as assumed. Working days are weekdays: exchange holidays are not
modelled.

A filing is what a person in compliance says was made, with its reference, once per obligation and period, added and
never edited. Nothing here files anything, and an unfiled period past its day reads overdue until someone records it.
The calendar starts from the day the firm came onto the platform: what fell due before that is not asked of it.

The registered details and the people a SEBI intermediary must name (principal officer, compliance officer, designated
director, CISO, grievance contact) are the firm's settings, the admin's to record with a reason on the audit log. One
not recorded reads "not recorded", never a guess. This is not a statement that the firm is compliant.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, licences, markets
from app.models.compliance_calendar import ComplianceFiling
from app.models.tenant import Firm
from app.provenance import ledger

IA_MC = "SEBI Master Circular for Investment Advisers (6 Feb 2026)"
PM_MC = "SEBI Master Circular for Portfolio Managers (Jul 2025)"
DUE_SOON_DAYS = 14
PMS = ("pms_discretionary", "pms_non_discretionary", "pms_advisory")

OFFICERS = (("registration_number", "SEBI registration number"), ("registration_valid_to", "Registration valid to"),
            ("registered_address", "Registered address"), ("sebi_regional_office", "SEBI regional office"),
            ("principal_officer", "Principal Officer"), ("compliance_officer", "Compliance Officer"),
            ("designated_director", "Designated Director"), ("ciso", "Chief Information Security Officer"),
            ("grievance_contact", "Grievance contact"))
OFFICER_KEYS = {k for k, _ in OFFICERS}


class CalendarError(ValueError):
    """This cannot be recorded, with the reason."""


def _month_end(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


def _add_working_days(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _fy(y: int) -> str:
    return f"{y}-{(y + 1) % 100:02d}"


# Each yields (period label, due day) for periods whose due day falls in [lo, hi].
def _complaints(lo: date, hi: date):
    for y, m in _months(lo - timedelta(days=40), hi):
        due = date(y + (m == 12), 1 if m == 12 else m + 1, 7)
        yield f"{y}-{m:02d}", due


def _half_yearly(lo: date, hi: date):
    for y in range(lo.year - 1, hi.year + 1):
        yield f"H1 {_fy(y)}", date(y, 10, 30)          # April to September, within 30 days of 30 Sep
        yield f"H2 {_fy(y)}", date(y + 1, 4, 30)       # October to March, within 30 days of 31 Mar


def _yearly(month: int, day: int):
    def gen(lo: date, hi: date):
        for y in range(lo.year - 1, hi.year + 1):
            # Every one of these falls after 31 March, so it covers the financial year that ended then.
            yield _fy(y - 1), date(y, month, day)
    return gen


def _pms_monthly(lo: date, hi: date):
    for y, m in _months(lo - timedelta(days=40), hi):
        yield f"{y}-{m:02d}", _add_working_days(_month_end(y, m), 7)


@dataclass(frozen=True)
class Obligation:
    key: str
    label: str
    licences: tuple[str, ...]
    citation: str
    periods: Callable
    due_basis: str = "the circular"


OBLIGATIONS: tuple[Obligation, ...] = (
    Obligation("complaints_monthly", "Complaint data on the website and app (Annexure C)", ("investment_adviser",),
               f"{IA_MC} paras 2.4, 7.5: by the 7th of the following month", _complaints),
    Obligation("iaasb_half_yearly", "Half-yearly report to IAASB", ("investment_adviser",),
               f"{IA_MC} para 30.4: within 30 days of 30 Sep and 31 Mar", _half_yearly),
    Obligation("saas_undertaking", "Half-yearly SaaS undertaking", ("investment_adviser",),
               f"{IA_MC} para 31.1: half-yearly", _half_yearly,
               due_basis="assumed: with the half-yearly report, since the circular gives no day"),
    Obligation("deposit_revision", "Deposit revised to the previous year's peak clients", ("investment_adviser",),
               f"{IA_MC} para 1(iv)(c): by 30 Apr", _yearly(4, 30)),
    Obligation("compliance_audit", "Annual compliance audit completed", ("investment_adviser",),
               f"{IA_MC} para 1(xiv): by 30 Sep", _yearly(9, 30)),
    Obligation("audit_findings_iaasb", "Audit report and adverse findings to IAASB", ("investment_adviser",),
               f"{IA_MC} para 1(xiv): adverse findings by 31 Oct", _yearly(10, 31)),
    Obligation("segregation_certificate", "Client-level segregation certificate", ("investment_adviser",),
               f"{IA_MC}: within six months of the financial year's end", _yearly(9, 30)),
    Obligation("pms_monthly_report", "Monthly report to SEBI and APMI", PMS,
               f"{PM_MC}: within 7 working days of the month's end", _pms_monthly,
               due_basis="the circular; working days are weekdays, holidays not modelled"),
)
BY_KEY = {o.key: o for o in OBLIGATIONS}


def applies(firm: Firm) -> bool:
    return markets.code(firm.jurisdiction) == "IN" and bool(licences.held(firm))


def obligations_for(firm: Firm) -> list[Obligation]:
    held = {l.key for l in licences.held(firm)}
    return [o for o in OBLIGATIONS if held & set(o.licences)]


def _start(firm: Firm) -> date:
    return firm_time.today(firm, firm.created_at) if getattr(firm, "created_at", None) else firm_time.today(firm)


def officers(firm: Firm) -> list[dict]:
    recorded = ((firm.settings or {}).get("sebi_registration")) or {}
    return [{"key": k, "label": label, "value": recorded.get(k) or None} for k, label in OFFICERS]


async def overview(session: AsyncSession, firm: Firm) -> dict:
    """Every obligation's periods from the firm's start to its next due day, each filed, overdue, due soon or upcoming."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "items": [], "officers": [], "counts": {}}
    start = _start(firm)
    filings = {(f.obligation, f.period): f for f in (await session.execute(
        select(ComplianceFiling).where(ComplianceFiling.firm_id == firm.id))).scalars().all()}
    items = []
    for o in obligations_for(firm):
        periods = sorted({(due, label) for label, due in o.periods(start, today + timedelta(days=400)) if due >= start})
        upcoming_shown = False
        for due, label in periods:
            filed = filings.get((o.key, label))
            if due > today and not filed:
                if upcoming_shown:
                    continue
                upcoming_shown = True
            state = ("filed" if filed else "overdue" if due < today
                     else "due_soon" if (due - today).days <= DUE_SOON_DAYS else "upcoming")
            items.append({"obligation": o.key, "label": o.label, "period": label, "due_on": due.isoformat(),
                          "days": (due - today).days, "state": state, "citation": o.citation, "due_basis": o.due_basis,
                          "licences": [licences.LICENCES[k].name for k in o.licences if k in licences.LICENCES],
                          "filed_on": filed.filed_on.isoformat() if filed else None,
                          "reference": filed.reference if filed else None,
                          "recorded_by": filed.recorded_by if filed else None})
    order = {"overdue": 0, "due_soon": 1, "upcoming": 2, "filed": 3}
    items.sort(key=lambda i: (order[i["state"]], i["due_on"] if i["state"] != "filed" else "", i["label"]))
    counts = {s: sum(1 for i in items if i["state"] == s) for s in order}
    people = officers(firm)
    return {"applies": True, "as_of": today.isoformat(), "since": start.isoformat(), "items": items, "counts": counts,
            "officers": people, "officers_missing": [p["label"] for p in people if not p["value"]],
            "licences": [licences.LICENCES[k].name for k in {k for o in obligations_for(firm) for k in o.licences} if k in licences.LICENCES
                         and k in {l.key for l in licences.held(firm)}],
            "not_compliance": "A calendar of what is owed, from what was recorded; it does not make the firm compliant."}


async def prepare_filing(session: AsyncSession, firm: Firm, *, obligation: str | None, period: str | None,
                         filed_on: date | None, reference: str | None) -> dict:
    if not applies(firm):
        raise CalendarError("This firm holds no Indian licence, so it has no SEBI calendar.")
    o = BY_KEY.get((obligation or "").strip())
    if o is None or o not in obligations_for(firm):
        raise CalendarError(f"The obligation is one of {', '.join(x.key for x in obligations_for(firm))}.")
    today = firm_time.today(firm)
    known = {label: due for label, due in o.periods(_start(firm), today + timedelta(days=400))}
    label = (period or "").strip()
    if label not in known:
        raise CalendarError(f"No {o.label.lower()} period called {label!r}; the calendar names them like "
                            f"{', '.join(list(known)[-3:])}.")
    day = filed_on or today
    if day > today:
        raise CalendarError("A filing cannot be dated in the future.")
    ref = (reference or "").strip()
    if len(ref) < 4:
        raise CalendarError("Say where it was filed: the portal acknowledgement, the reference or the email.")
    existing = (await session.execute(select(ComplianceFiling).where(
        ComplianceFiling.firm_id == firm.id, ComplianceFiling.obligation == o.key, ComplianceFiling.period == label))).scalar_one_or_none()
    if existing is not None:
        raise CalendarError(f"{o.label} for {label} was recorded filed on {existing.filed_on.isoformat()} by {existing.recorded_by}.")
    return {"obligation": o, "period": label, "due_on": known[label], "filed_on": day, "reference": ref}


async def record_filing(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare_filing(session, firm, **terms)
    row = ComplianceFiling(firm_id=firm.id, obligation=p["obligation"].key, period=p["period"], due_on=p["due_on"],
                           filed_on=p["filed_on"], reference=p["reference"], recorded_by=by, note=(note or "").strip() or None)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="compliance_filing_recorded", actor=by, content={
        "obligation": row.obligation, "period": row.period, "due_on": row.due_on.isoformat(),
        "filed_on": row.filed_on.isoformat(), "reference": row.reference})
    return await overview(session, firm)


def prepare_officers(firm: Firm, details: dict | None, reason: str | None) -> dict:
    if not applies(firm):
        raise CalendarError("This firm holds no Indian licence, so it has no SEBI registration to record.")
    given = {k: str(v).strip() for k, v in (details or {}).items() if v is not None and str(v).strip()}
    unknown = sorted(set(given) - OFFICER_KEYS)
    if unknown:
        raise CalendarError(f"Not a registration detail: {', '.join(unknown)}. One of {', '.join(k for k, _ in OFFICERS)}.")
    if not given:
        raise CalendarError("Give at least one detail to record.")
    if len((reason or "").strip()) < 4:
        raise CalendarError("Say why it changes, for the audit log.")
    before = ((firm.settings or {}).get("sebi_registration")) or {}
    changes = [{"field": k, "before": before.get(k), "after": v} for k, v in given.items() if before.get(k) != v]
    if not changes:
        raise CalendarError("Nothing changes: those details are already recorded.")
    return {"details": given, "changes": changes, "reason": reason.strip()}


async def record_officers(session: AsyncSession, firm: Firm, actor, *, details: dict | None, reason: str | None) -> dict:
    from app.aurea_core import platform_admin

    p = prepare_officers(firm, details, reason)
    settings = dict(firm.settings or {})
    settings["sebi_registration"] = {**(settings.get("sebi_registration") or {}), **p["details"]}
    firm.settings = settings
    platform_admin.audit(session, firm.id, actor, "sebi_registration.updated", subject=firm.name,
                         detail={"changes": p["changes"], "reason": p["reason"]})
    await session.flush()
    return await overview(session, firm)

