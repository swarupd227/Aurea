"""What an employer's plan gives a person: a retirement match and a deferred compensation election (B-193, L200-2 §2.1).

A person records each from the plan document, naming it; nothing here is worked out, and none of it moves a plan figure.

  - A match is the share of what the person puts in that the employer adds, up to a share of pay, and how it vests as the
    document says. With the person's income on file the most it could add in a year is shown as a figure (income x the pay
    limit x the rate), and what the person would put in to reach it; with none on file that reads "not assessed". Whether
    the person puts that in is not on file, so no match is ever assumed to be received.
  - A deferred compensation election is a year's deferral and when and how it is paid out. Its payout is income only when
    someone records it from the statement as an income stream (financial_plan), and this says so rather than counting it.

Added and never edited. One that has ended is kept, with why. Only an adviser or paraplanner records; staff read; no client
tool reads it. A person with no benefit recorded has none on file, which does not mean they have none.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, protection
from app.models.employer_benefit import BENEFIT_KINDS, PAYOUT_FORMS, EmployerBenefit
from app.models.graph import Household, Person
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = ("adviser", "paraplanner")
READERS = ("adviser", "paraplanner", "portfolio_team", "compliance", "operations", "branch_leader")
NOT_MODELLED = ["match_vesting_schedule", "whether_the_person_contributes_enough_to_receive_the_match", "deferred_compensation_tax_timing",
                "employer_credit_risk_on_deferred_compensation", "deferred_payout_counts_only_as_a_recorded_income_stream"]
DECIDES = "nothing: these are what a plan document says, and they move no figure"


class EmployerBenefitError(ValueError):
    """This cannot be recorded, with the reason."""


def _may(role: Any, roles: tuple, words: str) -> None:
    if role is None or str(role) not in roles:
        raise EmployerBenefitError(words)


def may_read(role: Any) -> None:
    _may(role, READERS, "Employer benefits are read by staff, not by clients.")


def _number(value: Any, label: str, low: float, high: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        raise EmployerBenefitError(f"{label} must be a number.")
    if not low < n <= high:
        raise EmployerBenefitError(f"{label} must be above {low:g} and at most {high:g}.")
    return n


async def _person(session: AsyncSession, person_id: Any, firm_id: uuid.UUID) -> Person:
    try:
        pid = uuid.UUID(str(person_id))
    except (TypeError, ValueError):
        raise EmployerBenefitError("person_id must be an id from a read tool.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm_id or person.household_id is None:
        raise EmployerBenefitError("Person not found.")
    return person


async def prepare_benefit(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, person_id: Any, kind: Any, employer: Any, source_document: Any,
                          match_rate: Any = None, match_up_to: Any = None, vesting: Any = None, annual_deferral: Any = None,
                          election_year: Any = None, payout_start_age: Any = None, payout_form: Any = None, payout_years: Any = None) -> dict:
    """What recording a benefit would refuse, refused without writing."""
    _may(role, RECORDERS, "An employer benefit is recorded by an adviser or a paraplanner, from the plan document.")
    person = await _person(session, person_id, firm_id)
    if kind not in BENEFIT_KINDS:
        raise EmployerBenefitError(f"The kind must be one of {', '.join(BENEFIT_KINDS)}.")
    who = str(employer or "").strip()
    if len(who) < 2:
        raise EmployerBenefitError("Give the employer.")
    document = str(source_document or "").strip()
    if len(document) < 5:
        raise EmployerBenefitError("Say which plan document this comes from.")
    terms: dict[str, Any] = {"match_rate": None, "match_up_to": None, "vesting": None, "annual_deferral": None, "election_year": None,
                             "payout_start_age": None, "payout_form": None, "payout_years": None}
    if kind == "retirement_match":
        terms["match_rate"] = _number(match_rate, "The match rate", 0, 2)
        terms["match_up_to"] = _number(match_up_to, "The share of pay it matches up to", 0, 1)
        if terms["match_rate"] is None or terms["match_up_to"] is None:
            raise EmployerBenefitError("A match needs its rate (0.5 is fifty cents on the dollar) and the share of pay it matches up to (0.06 is six percent).")
        terms["vesting"] = (str(vesting).strip()[:200] or None) if vesting not in (None, "") else None
        if any(v not in (None, "") for v in (annual_deferral, election_year, payout_start_age, payout_form, payout_years)):
            raise EmployerBenefitError("A match has no deferral or payout: those are a deferred compensation election's.")
    else:
        terms["annual_deferral"] = _number(annual_deferral, "The yearly deferral", 0, 100_000_000)
        if terms["annual_deferral"] is None:
            raise EmployerBenefitError("A deferred compensation election needs the yearly amount deferred.")
        age = _number(payout_start_age, "The age it is paid from", 39, 100)
        if age is None:
            raise EmployerBenefitError("Say the age the deferral is paid out from.")
        terms["payout_start_age"] = int(age)
        if payout_form not in PAYOUT_FORMS:
            raise EmployerBenefitError(f"The payout form must be one of {', '.join(PAYOUT_FORMS)}.")
        terms["payout_form"] = payout_form
        if payout_form == "installments":
            years = _number(payout_years, "The years it is paid over", 0, 40)
            if years is None:
                raise EmployerBenefitError("Installments need the number of years they are paid over.")
            terms["payout_years"] = int(years)
        elif payout_years not in (None, ""):
            raise EmployerBenefitError("A lump sum has no years to be paid over.")
        year = _number(election_year, "The election year", 1990, 2100)
        terms["election_year"] = int(year) if year is not None else None
        if any(v not in (None, "") for v in (match_rate, match_up_to, vesting)):
            raise EmployerBenefitError("A deferral has no match: those are a retirement match's.")
    return {"person": person, "kind": kind, "employer": who[:200], "document": document[:300], **terms}


async def record_benefit(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, **terms) -> EmployerBenefit:
    ready = await prepare_benefit(session, firm_id, role=role, **terms)
    firm = await session.get(Firm, firm_id)
    row = EmployerBenefit(firm_id=firm_id, household_id=ready["person"].household_id, person_id=ready["person"].id, kind=ready["kind"], employer=ready["employer"],
                          match_rate=ready["match_rate"], match_up_to=ready["match_up_to"], vesting=ready["vesting"], annual_deferral=ready["annual_deferral"],
                          election_year=ready["election_year"], payout_start_age=ready["payout_start_age"], payout_form=ready["payout_form"],
                          payout_years=ready["payout_years"], source_document=ready["document"], as_of=firm_time.today(firm), recorded_by=recorded_by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="employer_benefit_recorded", actor=recorded_by, content={
        "benefit_id": str(row.id), "person_id": str(row.person_id), "kind": row.kind, "employer": row.employer, "document": row.source_document})
    return row


async def prepare_end(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, benefit_id: Any, reason: Any) -> dict:
    _may(role, RECORDERS, "An employer benefit is ended by an adviser or a paraplanner, with why.")
    try:
        bid = uuid.UUID(str(benefit_id))
    except (TypeError, ValueError):
        raise EmployerBenefitError("benefit_id must be an id from a read tool.")
    row = await session.get(EmployerBenefit, bid)
    if row is None or row.firm_id != firm_id:
        raise EmployerBenefitError("Benefit not found.")
    if row.ended_on is not None:
        raise EmployerBenefitError("This benefit has already ended.")
    why = str(reason or "").strip()
    if len(why) < 3:
        raise EmployerBenefitError("Say why it has ended: left the employer, the plan was closed, or the election changed.")
    return {"benefit": row, "reason": why[:300], "person": await session.get(Person, row.person_id)}


async def end_benefit(session: AsyncSession, firm_id: uuid.UUID, *, role: Any, recorded_by: str, **terms) -> EmployerBenefit:
    ready = await prepare_end(session, firm_id, role=role, **terms)
    row = ready["benefit"]
    row.ended_on, row.ended_reason = firm_time.today(await session.get(Firm, firm_id)), ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="employer_benefit_ended", actor=recorded_by,
                              content={"benefit_id": str(row.id), "reason": row.ended_reason})
    return row


def _view(b: EmployerBenefit, name: str | None, income: float | None) -> dict:
    out = {"id": str(b.id), "person_id": str(b.person_id), "person": name, "kind": b.kind, "employer": b.employer, "document": b.source_document,
           "as_of": b.as_of.isoformat(), "ended_on": b.ended_on.isoformat() if b.ended_on else None, "ended_reason": b.ended_reason,
           "active": b.ended_on is None, "recorded_by": b.recorded_by}
    if b.kind == "retirement_match":
        rate, upto = float(b.match_rate), float(b.match_up_to)
        out |= {"match_rate": rate, "match_up_to": upto, "vesting": b.vesting, "counted": False}
        if income:
            out |= {"income": income, "put_in_to_reach_it": round(income * upto, 2), "most_it_adds_a_year": round(income * upto * rate, 2), "figures": "assessed"}
        else:
            out |= {"figures": "not_assessed", "figures_reason": "No income is on file for this person, so what a match could add is not worked out."}
    else:
        out |= {"annual_deferral": float(b.annual_deferral), "election_year": b.election_year, "payout_start_age": b.payout_start_age,
                "payout_form": b.payout_form, "payout_years": b.payout_years, "counted": False,
                "payout_counted_as": "an income stream, only when someone records it from the statement"}
    return out


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    firm = await session.get(Firm, firm_id)
    people = {p.id: p for p in (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()}
    rows = (await session.execute(select(EmployerBenefit).where(EmployerBenefit.household_id == household_id, EmployerBenefit.firm_id == firm_id)
                                  .order_by(EmployerBenefit.created_at))).scalars().all()
    views = [_view(b, people[b.person_id].full_name if b.person_id in people else None,
                   protection.income_of(people[b.person_id]) if b.person_id in people else None) for b in rows]
    live = [v for v in views if v["active"]]
    return {"household_id": str(household.id), "household": household.name, "as_of": firm_time.today(firm).isoformat(), "benefits": views,
            "summary": {"active": len(live), "matches": sum(1 for v in live if v["kind"] == "retirement_match"),
                        "deferrals": sum(1 for v in live if v["kind"] == "deferred_compensation"),
                        "most_matches_add_a_year": round(sum(v.get("most_it_adds_a_year") or 0 for v in live), 2),
                        "matches_not_assessed": sum(1 for v in live if v["kind"] == "retirement_match" and v["figures"] == "not_assessed")},
            "adults_with_none": [p.full_name for p in people.values() if not p.is_next_gen and not any(b.person_id == p.id and b.ended_on is None for b in rows)],
            "not_on_file": "None on file does not mean none exists: the employer's plan may hold one.", "not_modelled": NOT_MODELLED, "decides": DECIDES}
