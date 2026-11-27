"""India's dates, in the Regulatory Countdown (India row 14, B-258).

The countdown encoded the United States' and the United Kingdom's deadlines. This adds India's, in the same shape, from what is on
file and from no guess. Each item names its source and says "assumed" where the day is this build's reading and not the law's.

**The firm's.** Its licence tracks' filings from `compliance_calendar` (not yet filed: overdue, due soon, next), the registration's
expiry if the firm recorded it, each governance policy's review day (`governance`), and the dated rule changes in `law_figures` that
reach the firm's licences (the Portfolio Managers Regulations 2026, the Common Advertisement Code, the data protection duties).

**A household's.** Advance tax and the return, as a due list from the Income-tax Act's days (whether advance tax is due at all depends
on the year's tax after TDS, which is not assessed here, and the return's day is often moved by notification, so it is marked
assumed); the periodic KYC and risk review of each client rated by the AML model, from its band's cycle (this build's own cycle:
SEBI's KYC periodicity is not modelled, so it is marked assumed); a minor's coming majority; and the nominations still open, which
carry no day because SEBI's dates for them have moved and none is held here.

An item that has passed is overdue or in force, never a countdown at zero. Nothing here files, pays or tells a client anything, and it
does not say the firm or the client is up to date.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import compliance_calendar, firm_time, governance, guardianship, law_figures, licences
from app.models.graph import Person
from app.models.onboarding import OnboardingCase
from app.models.tenant import Firm

DUE_SOON = 30
# A minor's conversion is raised a year before the birthday (B-392, L200-1 India section 10), with what it takes.
MAJORITY_CHECKLIST = ("Before the birthday: a fresh KYC in the young adult's own name and a bank account in their own name; the conversion "
                      "is then recorded from the signed form")


def year_before(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:      # 29 February
        return date(day.year - 1, 3, 1)


ADVANCE_TAX = ((6, 15, "15%"), (9, 15, "45%"), (12, 15, "75%"), (3, 15, "100%"))
STATE_ORDER = {"overdue": 0, "due_soon": 1, "upcoming": 2, "in_force": 3, "not_dated": 4}
RULE_CHANGES = (("in_pm_regs_2026_start", "Portfolio Managers Regulations 2026 apply", ("pms_discretionary", "pms_non_discretionary", "pms_advisory")),
                ("in_ad_code_start", "Common Advertisement Code operative", None),
                ("in_dpdp_duties_start", "Data protection duties start (the day is assumed)", None))


def _item(kind: str, label: str, due: date | None, today: date, *, source: str, assumed: str | None = None, detail: str | None = None,
          state: str | None = None, extra: dict | None = None) -> dict:
    left = (due - today).days if due else None
    if state is None:
        state = "not_dated" if due is None else "overdue" if left < 0 else "due_soon" if left <= DUE_SOON else "upcoming"
    return {"kind": kind, "label": label, "due_on": due.isoformat() if due else None, "days": left, "state": state, "source": source,
            "assumed": assumed, "detail": detail, **(extra or {})}


def _next_date(today: date, month: int, day: int) -> date:
    d = date(today.year, month, day)
    return d if d >= today else date(today.year + 1, month, day)


def _fy_start_year(today: date) -> int:
    return today.year if today.month >= 4 else today.year - 1


async def firm_items(session: AsyncSession, firm: Firm) -> dict:
    """The firm's India dates, soonest first."""
    today = firm_time.today(firm)
    if not governance.applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "items": [], "counts": {}}
    items: list[dict] = []
    cal = await compliance_calendar.overview(session, firm)
    for i in cal.get("items", []):
        if i["state"] == "filed":
            continue
        items.append(_item("filing", f"{i['label']}: {i['period']}", date.fromisoformat(i["due_on"]), today, source=i["citation"],
                           assumed=i["due_basis"].removeprefix("assumed: ") if i["due_basis"].startswith("assumed") else None,
                           state=i["state"]))
    valid_to = ((firm.settings or {}).get("sebi_registration") or {}).get("registration_valid_to")
    try:
        expiry = date.fromisoformat(str(valid_to)) if valid_to else None
    except ValueError:
        expiry = None
    items.append(_item("registration", "SEBI registration valid to", expiry, today, source="The firm's registration certificate, as recorded",
                       detail=None if expiry else "not recorded: the admin records it with a reason on the Licences & SEBI page"))
    for p in (await governance.policies(session, firm))["policies"]:
        due = date.fromisoformat(p["next_review_on"]) if p["next_review_on"] else None
        state = "not_dated" if due is None else None
        items.append(_item("policy", f"{p['label']} policy review", due, today, source=p["citation"],
                           detail="not recorded in the governance register" if due is None else None,       # who owns it stays in the register
                           state=state, assumed="a year after approval" if due and not p["review_set_by_document"] else None))
    # Each adviser's NISM certificate (B-298): expired, due within 90 days, or not on file. A certificate that stands is not an item.
    from app.aurea_core import adviser_certification

    for _who, label, due, cstate, detail in await adviser_certification.deadline_items(session, firm):
        items.append(_item("certification", label, due, today, source=adviser_certification.CITATION, detail=detail,
                           state="overdue" if cstate == "expired" else "due_soon" if cstate == "due_soon" else "not_dated",
                           extra={"remind_within": adviser_certification.DUE_WITHIN}))
    held = {l.key for l in licences.held(firm)}
    for key, label, only in RULE_CHANGES:
        fig = law_figures.get(key, today)
        if only and not (held & set(only)):
            continue
        day = fig.applies_from
        passed = day <= today
        items.append(_item("rule_change", label, day, today, source=fig.source, state="in_force" if passed else None,
                           assumed="ASSUMED" in fig.label.upper() and "the day is assumed" or None))
    reviews = await kyc_reviews(session, firm, today)
    if reviews:
        overdue = [r for r in reviews if r["state"] == "overdue"]
        if overdue:
            items.append(_item("kyc_review", f"{len(overdue)} client risk review(s) overdue", min(date.fromisoformat(r["due_on"]) for r in overdue),
                               today, source=KYC_SOURCE, assumed=KYC_ASSUMED))
    items.sort(key=lambda i: (STATE_ORDER[i["state"]], i["due_on"] or "9999"))
    counts = {s: sum(1 for i in items if i["state"] == s) for s in STATE_ORDER}
    return {"applies": True, "as_of": today.isoformat(), "items": items, "counts": counts,
            "not_compliance": "Dates the firm owes or is waiting for, from what was recorded. It does not say the firm is compliant."}


KYC_SOURCE = "aml_risk.py: this build's review cycle by risk band (low 4 years, medium 2, high 1); SEBI's KYC periodicity is not modelled"
KYC_ASSUMED = "the cycle is this build's own, not SEBI's"


async def kyc_reviews(session: AsyncSession, firm: Firm, today: date, household_id: uuid.UUID | None = None) -> list[dict]:
    """Each rated client's next periodic risk review from its latest rating, soonest first."""
    from app.models.aml_risk import AmlRiskRating

    rows = (await session.execute(select(AmlRiskRating).where(AmlRiskRating.firm_id == firm.id).order_by(AmlRiskRating.created_at))).scalars().all()
    latest = {}
    for r in rows:
        latest[r.case_id] = r
    out = []
    for case_id, r in latest.items():
        case = await session.get(OnboardingCase, case_id)
        home = (case.materialized or {}).get("household_id") if case else None
        if household_id is not None and home != str(household_id):
            continue
        due = r.next_review_on
        out.append({"case": case.prospect_name if case else None, "household_id": home, "band": r.band, "due_on": due.isoformat(), "months": r.review_months,
                    "days": (due - today).days, "state": "overdue" if due < today else "due_soon" if (due - today).days <= DUE_SOON else "upcoming"})
    return sorted(out, key=lambda x: x["due_on"])


async def household_items(session: AsyncSession, firm: Firm, household_id: uuid.UUID, *, nominations: bool = True) -> dict:
    """One household's India dates: tax, KYC review, majority, open nominations. A caller with no use for the nominations passes
    `nominations=False`, because finding them reads the whole client file."""
    today = firm_time.today(firm)
    items: list[dict] = []
    adv = law_figures.get("in_advance_tax_dates", today)
    year = _fy_start_year(today)
    for month, day, pct in ADVANCE_TAX:
        due = date(year + (1 if month < 4 else 0), month, day)
        if due < today:
            continue
        items.append(_item("advance_tax", f"Advance tax instalment, {pct} of the year's tax", due, today, source=adv.source,
                           detail="Due where the tax after TDS is Rs 10,000 or more; whether it is, is not assessed here"))
    itr = law_figures.get("in_itr_due_date", today)
    items.append(_item("itr", "Income-tax return for the year just ended (31 July; 31 October where an audit is needed)", _next_date(today, 7, 31), today,
                       source=itr.source, assumed="the day in force is set by notification each year and is often moved; the statute's day is shown",
                       detail="Each individual and each HUF files its own"))
    for r in await kyc_reviews(session, firm, today, household_id):
        items.append(_item("kyc_review", f"Periodic KYC and risk review ({r['case']}, {r['band']} risk)", date.fromisoformat(r["due_on"]), today,
                           source=KYC_SOURCE, assumed=KYC_ASSUMED))
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.deceased_on.is_(None)))).scalars().all()
    for p in people:
        g = await guardianship.standing(session, firm.id, p.id)
        s = guardianship.status_of(p, g, today, await guardianship.conversion_of(session, firm.id, p.id))
        if s.get("minor") and s.get("majority_on"):
            turns = date.fromisoformat(s["majority_on"])
            items.append(_item("majority", f"{p.full_name} reaches majority: the account is converted", turns, today,
                               source=guardianship.CITATION, detail="Nothing converts the account here; the guardian's authority ends",
                               extra={"remind_within": (turns - year_before(turns)).days, "checklist": MAJORITY_CHECKLIST}))
        elif s.get("state") == "attained_majority":
            items.append(_item("majority", f"{p.full_name} is now an adult: convert the account", date.fromisoformat(s["majority_on"]), today,
                               source=guardianship.CITATION, state="overdue",
                               detail="The guardian's authority has ended and orders on the account are refused until a person records the conversion"))
    from app.aurea_core import client_file

    file = await client_file.for_household(session, household_id, firm_id=firm.id) if nominations else None
    open_noms = [i for i in (file or {}).get("designation_issues", []) if i["issue"].startswith("no nomination")]
    if open_noms:
        fig = law_figures.get("in_nomination_opt_out", today)
        items.append(_item("nomination", f"{len(open_noms)} account(s) with no nomination or signed opt-out on file", None, today, source=fig.source,
                           detail="SEBI's dates for nomination have moved and none is held here, so none is invented",
                           extra={"accounts": [i["account"] for i in open_noms]}))
    items.sort(key=lambda i: (STATE_ORDER[i["state"]], i["due_on"] or "9999"))
    return {"items": items, "law": law_figures.cited(["in_advance_tax_dates", "in_itr_due_date", "in_nomination_opt_out"], today)}


# ── As the countdown's analyses ──────────────────────────────────────────────────────────────────────────────────────

def _urgency(items: list[dict]) -> tuple[str, int, str | None]:
    dated = [i for i in items if i["due_on"] and i["state"] != "in_force"]
    if not dated:
        return "medium", 0, None
    first = min(dated, key=lambda i: i["due_on"])
    d = first["days"]
    return ("critical" if d < 0 or d <= 14 else "high" if d <= 60 else "medium"), max(d, 0), first["due_on"]


def _summary(items: list[dict]) -> str:
    c = {s: sum(1 for i in items if i["state"] == s) for s in STATE_ORDER}
    return f"{c['overdue']} overdue, {c['due_soon']} due within {DUE_SOON} days, {c['upcoming']} upcoming, {c['not_dated']} with no day to count to."


async def household_analysis(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    today = firm_time.today(firm)
    data = await household_items(session, firm, household_id)
    urgency, days, deadline = _urgency(data["items"])
    return {"type": "in_client_dates", "title": "India: tax, KYC and nomination dates", "subtitle": "Advance tax, the return, the periodic KYC review",
            "deadline": deadline or today.isoformat(), "status": "upcoming" if deadline else "in_force", "days_remaining": days, "urgency": urgency,
            "summary": _summary(data["items"]), "figures": {"items": data["items"], "affected": None}, "actions": [], "currency": firm.base_currency,
            "regulation": "Income-tax Act 1961 (carried into the Income-tax Act 2025, renumbered sections not read) and SEBI KYC and nomination rules",
            "law": data["law"], "assumed": sorted({i["assumed"] for i in data["items"] if i["assumed"]})}


async def firm_analysis(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    data = await firm_items(session, firm)
    urgency, days, deadline = _urgency(data["items"])
    cited = [k for k in ("in_pm_regs_2026_start", "in_ad_code_start", "in_dpdp_duties_start")]
    return {"type": "in_firm_deadlines", "title": "India: the firm's filings, policies and rule changes", "subtitle": "From the licences the firm holds",
            "deadline": deadline or today.isoformat(), "status": "upcoming" if deadline else "in_force", "days_remaining": days, "urgency": urgency,
            "summary": _summary(data["items"]), "figures": {"items": data["items"], "affected": None, "counts": data["counts"]}, "actions": [],
            "currency": firm.base_currency, "regulation": "The licence's master circulars, as the compliance calendar and the governance register hold them",
            "law": law_figures.cited(cited, today), "assumed": sorted({i["assumed"] for i in data["items"] if i["assumed"]})}
