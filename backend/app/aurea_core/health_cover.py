"""Health cover, for an Indian household: who is covered by what, where the cover is only an employer's, and the terms
that limit it (B-397, L200-2 India section 6).

A person records each policy from its schedule: individual, family floater, super top-up (which pays above a
deductible), or an employer's group cover. It covers the people the schedule names, on the household's record or not
(parents often are not). The room-rent cap, sub-limits, co-payment, waiting periods and renewal date are held only as the
document says; a term not recorded is not a term the policy does not have. A waiting period's end is the document's date,
else worked from the recorded start of cover and its months, and says which.

For each person the household view says what is on file: an own policy, an employer's only, a top-up only, or nothing.
Nothing on file does not mean no cover. Employer group cover ends with the job, so it is never in the own figure: it is
shown apart, with what the figures would be with it. A super top-up counts only where the base cover beside it reaches its
deductible; below that it is listed with the amount nobody's policy pays first. A floater's sum is shared by everyone it
names, and is shown as shared. A policy whose renewal date has passed with no later schedule recorded is not counted.

A need is measured only against a figure the firm states with its source (a branch leader or administrator sets it, as
for the cost of care): one for the household's own family and, separately, one for parents. With none stated the need is
"not assessed", and no default is ever supplied. Nothing here estimates a hospital bill, recommends or buys anything.
"""
from __future__ import annotations

import calendar
import re
import uuid
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.aurea_core import firm_time, markets
from app.core.db import utcnow
from app.models.graph import Household, Person
from app.models.health_cover import HEALTH_KINDS, HealthPolicy
from app.models.tenant import Firm
from app.provenance import ledger

READERS = ("adviser", "paraplanner", "portfolio_team", "compliance", "operations", "branch_leader", "admin")
RECORDERS = ("adviser", "paraplanner", "operations")
FIGURE_SETTERS = ("branch_leader", "admin")

KIND_TEXT = {"individual": "Individual", "family_floater": "Family floater", "super_top_up": "Super top-up",
             "employer_group": "Employer group"}
BASE_KINDS = ("individual", "family_floater")
RENEWAL_NOTICE_DAYS = 60
EMPLOYER_NOT_COUNTED = "employer group cover: it ends with the job, so it is shown apart and not counted as the person's own"
RENEWAL_PASSED = "its renewal date has passed and no later schedule is recorded"
NOT_MODELLED = ["what_a_hospitalisation_costs", "room_rent_proportionate_deduction", "co_payment_and_sub_limits_not_netted",
                "waiting_periods_not_netted", "policy_exclusions", "claim_settlement", "portability_and_continuity_credits",
                "premium_affordability", "tax_deduction_for_premiums"]

# What a relationship word means for which figure applies: the household's own family, or a parent.
_PARENTS = ("parent", "mother", "father", "parent-in-law", "mother-in-law", "father-in-law", "parent_in_law")
_FAMILY = ("self", "spouse", "wife", "husband", "child", "son", "daughter", "dependant", "sibling", "brother", "sister", "other")
RELATIONSHIPS = _FAMILY + _PARENTS


class HealthCoverError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None, values: dict | None = None) -> bool:
    market = markets.of_household(firm, values) if firm else None
    return market is not None and market.code == "IN"


def may_record(role: Any) -> None:
    if str(role) not in RECORDERS:
        raise HealthCoverError("A health policy is recorded by an adviser, a paraplanner or operations, from its schedule.")


def cover_figure(firm: Firm | None) -> dict | None:
    """The firm's stated health cover figure, with who set it and from what, or None if nobody has."""
    figure = (((firm.settings or {}).get("protection") or {}).get("health")) if firm else None
    return figure if figure and figure.get("family_cover") else None


# ── reading ───────────────────────────────────────────────────────────────────

def add_months(day: date, months: int) -> date:
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _waiting(rows: list | None, since: date | None, today: date) -> list[dict]:
    out = []
    for w in rows or []:
        ends, basis = w.get("ends_on"), "document"
        if not ends:
            if since and w.get("months") is not None:
                ends, basis = add_months(since, int(w["months"])).isoformat(), "worked from the start of cover"
            else:
                basis = "end not on file"
        out.append({"condition": w["condition"], "months": w.get("months"), "ends_on": ends, "basis": basis,
                    "running": None if not ends else date.fromisoformat(ends) > today})
    return out


def status_of(p: HealthPolicy, today: date) -> str:
    if p.status != "in_force":
        return "ended"
    return "renewal_passed" if p.renewal_on is not None and p.renewal_on < today else "in_force"


def limits_of(row: dict, say) -> list[str]:
    """The terms that limit a policy, short enough for a chip, and only those recorded."""
    out = []
    if row["room_rent_cap"]:
        out.append(f"room rent: {row['room_rent_cap']}")
    if row["co_payment_percent"] is not None or row["co_payment_terms"]:
        share = f"{row['co_payment_percent']:g}%" if row["co_payment_percent"] is not None else ""
        out.append(" ".join(x for x in ("co-payment", share, row["co_payment_terms"]) if x))
    out += [f"sub-limit: {s['item']} {s['limit']}" for s in row["sub_limits"]]
    for w in row["waiting_periods"]:
        if w["running"] is False:
            continue
        until = f"to {w['ends_on']}" if w["ends_on"] else (f"{w['months']} months, end not on file" if w["months"] is not None else "end not on file")
        out.append(f"waiting: {w['condition']} {until}")
    if row["deductible"] is not None:
        out.append(f"pays above {say(row['deductible'])}")
    return out


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def _row(p: HealthPolicy, today: date, say) -> dict:
    status = status_of(p, today)
    why = RENEWAL_PASSED if status == "renewal_passed" else (EMPLOYER_NOT_COUNTED if p.kind == "employer_group" else None)
    out = {
        "id": str(p.id), "kind": p.kind, "kind_label": KIND_TEXT.get(p.kind, p.kind), "provider": p.provider,
        "policy_number": p.policy_number, "sum_insured": _num(p.sum_insured), "deductible": _num(p.deductible),
        "members": [{"person_id": m.get("person_id"), "name": m["name"], "relationship": m["relationship"]} for m in p.members or []],
        "room_rent_cap": p.room_rent_cap, "co_payment_percent": _num(p.co_payment_percent), "co_payment_terms": p.co_payment_terms,
        "sub_limits": list(p.sub_limits or []), "waiting_periods": _waiting(p.waiting_periods, p.covered_since, today),
        "covered_since": p.covered_since.isoformat() if p.covered_since else None,
        "renewal_on": p.renewal_on.isoformat() if p.renewal_on else None,
        "days_to_renewal": (p.renewal_on - today).days if p.renewal_on and status == "in_force" else None,
        "annual_premium": _num(p.annual_premium), "currency": p.currency, "status": status, "not_counted": why if status != "ended" else None,
        "source_document": p.source_document, "recorded_by": p.recorded_by, "as_of": p.as_of.isoformat(),
        "ended_reason": p.ended_reason,
    }
    out["limits"] = limits_of(out, say)
    return out


def _key(member: dict) -> str:
    return member.get("person_id") or f"name:{member['name'].strip().lower()}"


def assess_member(policies: list[dict], figure: float | None, missing: str) -> dict:
    """One person's health cover from the in-force policies naming them. Pure.

    `own` is the individual and floater sums plus each super top-up whose deductible they reach; an employer's cover is
    apart, with the figures as they would stand with it. `figure` is the firm's stated cover, or None."""
    live = [p for p in policies if p["status"] == "in_force"]
    base = sum(p["sum_insured"] for p in live if p["kind"] in BASE_KINDS)
    employer = sum(p["sum_insured"] for p in live if p["kind"] == "employer_group")
    tops = [p for p in live if p["kind"] == "super_top_up"]
    counted = sum(p["sum_insured"] for p in tops if base >= p["deductible"])
    short = [{"policy_id": p["id"], "provider": p["provider"], "sum_insured": p["sum_insured"], "deductible": p["deductible"],
              "unpaid_below": round(p["deductible"] - base, 2), "reached_with_employer": base + employer >= p["deductible"]}
             for p in tops if base < p["deductible"]]
    own = base + counted
    with_employer = base + employer + sum(p["sum_insured"] for p in tops if base + employer >= p["deductible"])
    position = "own" if base else ("employer_only" if employer else ("top_up_only" if tops else "none_on_file"))
    if figure is None:
        need = {"status": "not_assessed", "missing": [missing]}
    else:
        gap = max(0.0, figure - own)
        need = {"status": "gap" if gap > 0.005 else "no_gap", "figure": figure, "cover": round(own, 2), "gap": round(gap, 2),
                "gap_with_employer": round(max(0.0, figure - with_employer), 2)}
    return {"position": position, "base_cover": round(base, 2), "top_up_cover": round(counted, 2), "own_cover": round(own, 2),
            "employer_cover": round(employer, 2), "with_employer_cover": round(with_employer, 2), "top_ups_short": short,
            "renewal_passed": sum(1 for p in policies if p["status"] == "renewal_passed"), "need": need}


async def _household(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> Household:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise HealthCoverError("That household is not in this firm.")
    return household


async def _persons(session: AsyncSession, household_id: uuid.UUID, firm_id: uuid.UUID) -> list[Person]:
    return list((await session.execute(select(Person).where(
        Person.household_id == household_id, Person.firm_id == firm_id, Person.deceased_on.is_(None))
        .order_by(Person.date_of_birth, Person.full_name))).scalars().all())


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID,
                        today: date | None = None) -> dict | None:
    """Who in one household is covered by what, or None if it is not in this firm."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    if not applies(firm, household.values):
        return {"applies": False, "household_id": str(household_id), "household": household.name,
                "why": "Health cover is recorded for an Indian household; other markets' health cover is not modelled."}
    today = today or firm_time.today(firm)
    say = markets.money_for(firm)
    stored = (await session.execute(select(HealthPolicy).where(
        HealthPolicy.household_id == household_id, HealthPolicy.firm_id == firm_id)
        .order_by(HealthPolicy.as_of, HealthPolicy.created_at))).scalars().all()
    rows = [_row(p, today, say) for p in stored]
    live = [r for r in rows if r["status"] != "ended"]
    persons = await _persons(session, household_id, firm_id)

    named: dict[str, dict] = {str(p.id): {"person_id": str(p.id), "name": p.full_name, "relationships": [], "on_record": True} for p in persons}
    for r in live:
        for m in r["members"]:
            key = _key(m)
            entry = named.get(key) or named.setdefault(key, {"person_id": m.get("person_id"), "name": m["name"], "relationships": [],
                                                             "on_record": False})
            if m["relationship"] not in entry["relationships"]:
                entry["relationships"].append(m["relationship"])
    stated = cover_figure(firm)
    members = []
    for key, entry in named.items():
        mine = [r for r in live if any(_key(m) == key for m in r["members"])]
        words = entry["relationships"]
        group = "parent" if words and all(w in _PARENTS for w in words) else "family"
        figure = (stated or {}).get("parent_cover" if group == "parent" else "family_cover")
        missing = "no_firm_figure_for_parents" if group == "parent" and stated else "no_firm_figure"
        result = assess_member(mine, float(figure) if figure else None, missing)
        members.append({
            "person_id": entry["person_id"], "name": entry["name"], "relationship": ", ".join(words) or None, "group": group,
            "on_record": entry["on_record"], **result,
            "policies": [{"id": r["id"], "kind": r["kind"], "kind_label": r["kind_label"], "provider": r["provider"],
                          "sum_insured": r["sum_insured"], "deductible": r["deductible"], "status": r["status"],
                          "shared_by": len(r["members"])} for r in mine],
            "limits": [f"{r['provider']}: {t}" for r in mine if r["status"] == "in_force" for t in r["limits"]],
        })
    due = [r for r in live if r["days_to_renewal"] is not None and r["days_to_renewal"] <= RENEWAL_NOTICE_DAYS]
    return {
        "applies": True, "household_id": str(household_id), "household": household.name,
        "currency": firm.base_currency, "as_of": today.isoformat(), "figure": stated, "members": members, "policies": rows,
        "summary": {"members": len(members), "own": sum(1 for m in members if m["position"] == "own"),
                    "employer_only": sum(1 for m in members if m["position"] == "employer_only"),
                    "top_up_only": sum(1 for m in members if m["position"] == "top_up_only"),
                    "none_on_file": sum(1 for m in members if m["position"] == "none_on_file"),
                    "gaps": sum(1 for m in members if m["need"]["status"] == "gap"),
                    "not_assessed": sum(1 for m in members if m["need"]["status"] == "not_assessed"),
                    "policies_in_force": sum(1 for r in rows if r["status"] == "in_force"),
                    "renewals_due": len(due), "renewals_passed": sum(1 for r in rows if r["status"] == "renewal_passed")},
        "not_modelled": NOT_MODELLED,
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, today: date | None = None) -> dict:
    """Every Indian household's health cover, those relying on an employer or with nothing on file first."""
    firm = await session.get(Firm, firm_id)
    today = today or firm_time.today(firm)
    households = (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.left_on.is_(None))
                                        .order_by(Household.name))).scalars().all()
    views = [await for_household(session, h.id, firm_id=firm_id, today=today) for h in households if applies(firm, h.values)]
    views = [v for v in views if v and v["members"]]
    views.sort(key=lambda v: (-v["summary"]["employer_only"], -v["summary"]["none_on_file"], -v["summary"]["gaps"], v["household"]))
    count = lambda key: sum(1 for v in views if v["summary"][key])  # noqa: E731
    return {
        "applies": bool(views) or applies(firm), "households": views, "figure": cover_figure(firm), "currency": firm.base_currency,
        "as_of": today.isoformat(),
        "summary": {"households": len(views), "with_employer_only": count("employer_only"), "with_none_on_file": count("none_on_file"),
                    "with_gap": count("gaps"), "renewals_due": sum(v["summary"]["renewals_due"] for v in views),
                    "renewals_passed": sum(v["summary"]["renewals_passed"] for v in views),
                    "people": sum(v["summary"]["members"] for v in views),
                    "people_employer_only": sum(v["summary"]["employer_only"] for v in views),
                    "people_none_on_file": sum(v["summary"]["none_on_file"] for v in views)},
        "not_modelled": NOT_MODELLED,
    }


# ── recording ─────────────────────────────────────────────────────────────────

def _amount(value: Any, label: str, *, required: bool = False) -> float | None:
    if value in (None, ""):
        if required:
            raise HealthCoverError(f"{label} is needed, as the schedule states it.")
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise HealthCoverError(f"{label} must be a number.")
    if amount <= 0:
        raise HealthCoverError(f"{label} must be above zero.")
    return amount


def _date(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise HealthCoverError(f"{label} must be a date, YYYY-MM-DD.")


def _text(value: Any, limit: int) -> str | None:
    return str(value or "").strip()[:limit] or None


def _entries(value: Any) -> list:
    """A list as the page sends it, or "a; b; c" as the conversation does."""
    if value in (None, ""):
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [part.strip() for part in str(value).split(";") if part.strip()]


def parse_members(value: Any, persons: list[Person]) -> list[dict]:
    """Who the schedule insures: "Name: relationship; ..." or a list. A name on the household's record is tied to it."""
    by_name = {p.full_name.strip().lower(): p for p in persons}
    by_id = {str(p.id): p for p in persons}
    out: list[dict] = []
    for entry in _entries(value):
        if isinstance(entry, dict):
            pid, name, relationship = entry.get("person_id"), entry.get("name"), entry.get("relationship")
        else:
            name, _, relationship = str(entry).partition(":")
            pid = None
        name, relationship = str(name or "").strip(), str(relationship or "").strip().lower()
        if not relationship:
            raise HealthCoverError("Say each insured person's relationship, as 'Name: relationship' (self, spouse, son, mother and so on).")
        if relationship not in RELATIONSHIPS:
            raise HealthCoverError(f"'{relationship}' is not a relationship this records. Use one of: {', '.join(RELATIONSHIPS)}.")
        person = by_id.get(str(pid)) if pid else by_id.get(name) or by_name.get(name.lower())
        if pid and person is None:
            raise HealthCoverError("An insured person is not on this household's record.")
        if person is None and len(name) < 2:
            raise HealthCoverError("Name each insured person.")
        row = {"person_id": str(person.id) if person else None, "name": person.full_name if person else name[:120],
               "relationship": relationship}
        if any(_key(m) == _key(row) for m in out):
            raise HealthCoverError(f"{row['name']} is named twice.")
        out.append(row)
    if not out:
        raise HealthCoverError("Say who the policy insures.")
    return out


def parse_sub_limits(value: Any) -> list[dict]:
    out = []
    for entry in _entries(value):
        if isinstance(entry, dict):
            item, limit = entry.get("item"), entry.get("limit")
        else:
            item, _, limit = str(entry).partition(":")
        item, limit = str(item or "").strip()[:80], str(limit or "").strip()[:80]
        if not item or not limit:
            raise HealthCoverError("Each sub-limit is 'what: the limit the schedule states', for example 'Cataract: 40,000 an eye'.")
        out.append({"item": item, "limit": limit})
    return out


_PERIOD = re.compile(r"^(?:(\d{1,3})\s*(month|months|year|years))?\s*(?:(?:until|to|ends)\s+(\d{4}-\d{2}-\d{2}))?$")


def parse_waiting(value: Any) -> list[dict]:
    """"Pre-existing diseases: 36 months", "Maternity: until 2027-03-31", or both."""
    out = []
    shape = "Each waiting period is 'what: N months', 'what: until YYYY-MM-DD' or both, as the schedule states it."
    for entry in _entries(value):
        if isinstance(entry, dict):
            condition, months, ends = entry.get("condition"), entry.get("months"), _date(entry.get("ends_on"), "A waiting period's end")
            if months not in (None, ""):
                try:
                    months = int(months)
                except (TypeError, ValueError):
                    raise HealthCoverError(shape)
            else:
                months = None
        else:
            condition, _, period = str(entry).partition(":")
            found = _PERIOD.match(period.strip().lower())
            if not period.strip() or not found:
                raise HealthCoverError(shape)
            number, unit, until = found.groups()
            months = int(number) * (12 if unit.startswith("year") else 1) if number else None
            ends = _date(until, "A waiting period's end")
        condition = str(condition or "").strip()[:80]
        if not condition or (months is None and ends is None) or (months is not None and not 0 < months <= 120):
            raise HealthCoverError(shape)
        out.append({"condition": condition, "months": months, "ends_on": ends.isoformat() if ends else None})
    return out


async def prepare_policy(
    session: AsyncSession, firm_id: uuid.UUID, *, role: Any, household_id: Any, kind: Any, provider: Any, sum_insured: Any,
    members: Any, source_document: Any, deductible: Any = None, policy_number: Any = None, room_rent_cap: Any = None,
    co_payment_percent: Any = None, co_payment_terms: Any = None, sub_limits: Any = None, waiting_periods: Any = None,
    covered_since: Any = None, renewal_on: Any = None, annual_premium: Any = None, replaces_id: Any = None,
    today: date | None = None,
) -> dict:
    """Everything recording a policy would refuse, refused without writing, and what would be written."""
    may_record(role)
    try:
        hid = uuid.UUID(str(household_id))
    except (TypeError, ValueError):
        raise HealthCoverError("household_id must be an id from a read tool.")
    household = await _household(session, firm_id, hid)
    firm = await session.get(Firm, firm_id)
    if not applies(firm, household.values):
        raise HealthCoverError("Health cover is recorded for an Indian household; other markets' health cover is not modelled.")
    today = today or firm_time.today(firm)
    kind = str(kind or "").strip().lower()
    if kind not in HEALTH_KINDS:
        raise HealthCoverError(f"The kind must be one of {', '.join(HEALTH_KINDS)}.")
    who = str(provider or "").strip()
    if len(who) < 2:
        raise HealthCoverError("Name the insurer.")
    document = str(source_document or "").strip()
    if len(document) < 5:
        raise HealthCoverError("Say which document this comes from: the policy schedule, its date and insurer.")
    lump = _amount(sum_insured, "The sum insured", required=True)
    excess = _amount(deductible, "The deductible", required=kind == "super_top_up")
    if excess is not None and kind != "super_top_up":
        raise HealthCoverError("Only a super top-up has a deductible here. Record a base policy's share as its co-payment.")
    insured = parse_members(members, await _persons(session, hid, firm_id))
    if kind == "individual" and len(insured) != 1:
        raise HealthCoverError("An individual policy insures one person; one shared by several is a family floater.")
    if kind == "family_floater" and len(insured) < 2:
        raise HealthCoverError("A family floater is shared by at least two people; one person's is an individual policy.")
    share = _amount(co_payment_percent, "The co-payment percent")
    if share is not None and share > 100:
        raise HealthCoverError("The co-payment is a percent, at most 100.")
    since, renewal = _date(covered_since, "The start of cover"), _date(renewal_on, "The renewal date")
    if since and since > today:
        raise HealthCoverError("The start of cover is in the future.")
    if renewal and renewal < today:
        raise HealthCoverError(f"This schedule's renewal date, {renewal.isoformat()}, has passed. Record the renewed schedule.")
    replaced = None
    if replaces_id not in (None, ""):
        try:
            replaced = await session.get(HealthPolicy, uuid.UUID(str(replaces_id)))
        except (TypeError, ValueError):
            replaced = None
        if replaced is None or replaced.firm_id != firm_id or replaced.household_id != hid or replaced.status != "in_force":
            raise HealthCoverError("The policy replaced must be a health policy in force for this household.")
    else:
        same = (await session.execute(select(HealthPolicy).where(
            HealthPolicy.firm_id == firm_id, HealthPolicy.household_id == hid, HealthPolicy.status == "in_force",
            HealthPolicy.kind == kind))).scalars().all()
        number = _text(policy_number, 64)
        keys = {_key(m) for m in insured}
        for other in same:
            if other.provider.strip().lower() == who.lower() and ((number and other.policy_number == number)
                                                                  or {_key(m) for m in other.members} == keys):
                raise HealthCoverError(f"This {KIND_TEXT[kind].lower()} with {other.provider} for the same people is already on file. "
                                       "For a renewal or a change give its id as replaces_id, so it is not counted twice.")
    return {
        "household": household, "kind": kind, "provider": who[:120], "document": document[:255], "sum_insured": lump,
        "deductible": excess, "policy_number": _text(policy_number, 64), "members": insured,
        "room_rent_cap": _text(room_rent_cap, 200), "co_payment_percent": share, "co_payment_terms": _text(co_payment_terms, 200),
        "sub_limits": parse_sub_limits(sub_limits), "waiting_periods": parse_waiting(waiting_periods), "covered_since": since,
        "renewal_on": renewal, "annual_premium": _amount(annual_premium, "The annual premium"), "replaces": replaced,
    }


async def record_policy(session: AsyncSession, firm_id: uuid.UUID, *, recorded_by: str, **terms) -> dict:
    ready = await prepare_policy(session, firm_id, **terms)
    firm = await session.get(Firm, firm_id)
    today = terms.get("today") or firm_time.today(firm)
    replaced = ready.pop("replaces")
    household, document = ready.pop("household"), ready.pop("document")
    row = HealthPolicy(firm_id=firm_id, household_id=household.id, source_document=document, currency=firm.base_currency,
                       status="in_force", recorded_by=recorded_by, as_of=today, replaces_id=replaced.id if replaced else None, **ready)
    session.add(row)
    if replaced is not None:
        replaced.status, replaced.ended_on, replaced.ended_by = "ended", today, recorded_by
        replaced.ended_reason = f"Replaced by a later schedule: {document}"[:255]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="health_policy_recorded", actor=recorded_by, content={
        "policy_id": str(row.id), "household_id": str(household.id), "kind": row.kind, "provider": row.provider,
        "document": document, "replaces": str(replaced.id) if replaced else None})
    return {"policy_id": str(row.id), "household_id": str(household.id), "household": household.name, "kind": row.kind,
            "kind_label": KIND_TEXT[row.kind], "provider": row.provider, "sum_insured": ready["sum_insured"],
            "deductible": ready["deductible"], "members": [m["name"] for m in row.members], "status": "in_force",
            "replaced": str(replaced.id) if replaced else None}


async def prepare_end(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, policy_id: Any, reason: Any) -> dict:
    may_record(role)
    try:
        row = await session.get(HealthPolicy, uuid.UUID(str(policy_id)))
    except (TypeError, ValueError):
        raise HealthCoverError("policy_id must be an id from read_health_cover.")
    if row is None or row.firm_id != firm_id:
        raise HealthCoverError("That health policy was not found.")
    if row.status != "in_force":
        raise HealthCoverError("This policy has already ended.")
    why = str(reason or "").strip()
    if len(why) < 3:
        raise HealthCoverError("Say why this cover ended: lapsed, cancelled, ported to another insurer, or the job ended.")
    return {"policy": row, "household": await session.get(Household, row.household_id), "reason": why[:255]}


async def end_policy(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, policy_id: Any, reason: Any, by: str,
                     today: date | None = None) -> dict:
    ready = await prepare_end(session, firm_id, role=role, policy_id=policy_id, reason=reason)
    row = (await session.execute(select(HealthPolicy).where(HealthPolicy.id == ready["policy"].id, HealthPolicy.firm_id == firm_id)
                                 .with_for_update().execution_options(populate_existing=True))).scalar_one()
    if row.status != "in_force":
        raise HealthCoverError("This policy has already ended.")
    row.status, row.ended_on, row.ended_by = "ended", today or await firm_time.today_of(session, firm_id), by
    row.ended_reason = ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="health_policy_ended", actor=by,
                              content={"policy_id": str(row.id), "reason": row.ended_reason})
    return {"policy_id": str(row.id), "household_id": str(row.household_id), "household": ready["household"].name,
            "kind": row.kind, "kind_label": KIND_TEXT.get(row.kind, row.kind), "provider": row.provider, "status": "ended"}


async def prepare_figure(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, family_cover: Any, source_document: Any,
                         parent_cover: Any = None) -> dict:
    """What stating the firm's health cover figure would refuse, refused without writing, and what it replaces."""
    if str(role) not in FIGURE_SETTERS:
        raise HealthCoverError("The firm's health cover figure is set by a branch leader or an administrator.")
    firm = await session.get(Firm, firm_id)
    if not applies(firm):
        raise HealthCoverError("The health cover figure is for an Indian firm.")
    family = _amount(family_cover, "The cover for the family", required=True)
    parents = _amount(parent_cover, "The cover for a parent")
    source = str(source_document or "").strip()
    if len(source) < 5:
        raise HealthCoverError("Say where the figure comes from: the document or decision, and its date. A figure with no source is not used.")
    return {"family_cover": family, "parent_cover": parents, "source": source[:255], "previous": cover_figure(firm)}


async def set_figure(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, family_cover: Any, source_document: Any,
                     set_by: str, parent_cover: Any = None) -> dict:
    ready = await prepare_figure(session, firm_id, role=role, family_cover=family_cover, source_document=source_document,
                                 parent_cover=parent_cover)
    firm = await session.get(Firm, firm_id)
    figure = {"family_cover": ready["family_cover"], "parent_cover": ready["parent_cover"], "source": ready["source"],
              "set_by": set_by, "set_at": utcnow().isoformat()}
    settings = dict(firm.settings or {})
    settings["protection"] = {**(settings.get("protection") or {}), "health": figure}
    firm.settings = settings
    flag_modified(firm, "settings")
    await ledger.append_entry(session, firm_id=firm_id, event_type="health_cover_figure", actor=set_by,
                              content={"health": figure, "replaced": ready["previous"]})
    await session.flush()
    return {**figure, "previous": ready["previous"]}
