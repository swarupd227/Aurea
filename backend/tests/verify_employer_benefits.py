"""Employer benefits, income stream end ages, riders, and umbrella and property cover (B-193).

    python -m tests.verify_employer_benefits

Runs against a throwaway SQLite database:
  - a retirement match and a deferred compensation election are recorded from the plan document; none is estimated, nothing is
    assumed to be received, and neither moves a plan figure; one that ended is kept with why;
  - an income stream may end at an age: it stops being paid in the retirement engine, in the guaranteed income at retirement,
    and in the Roth window; one over before retirement counts for nothing; Social Security has no end;
  - a rider is listed under its base policy and counted once; umbrella and property cover are listed and never counted against
    a gap;
  - only an adviser or paraplanner records, staff read, and no client tool reads any of it.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import employer_benefits, financial_plan, firm_time, protection, retirement, roth_window
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.employer_benefit import EmployerBenefit
from app.models.enums import UserRole
from app.models.financial_plan import IncomeStream
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, employer_benefits.EmployerBenefitError, financial_plan.PlanError, protection.ProtectionError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def streams(*rows) -> list[dict]:
    return [{"annual": a, "inflation_linked": True, "name": "x", "person": "p", "starts_in": s, "ends_in": e} for a, s, e in rows]


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            s.add(firm)
            await s.flush()
            today = firm_time.today(firm)
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@t", full_name=r.value, role=r, hashed_password="x")
                     for r in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.CLIENT, UserRole.OPERATIONS, UserRole.COMPLIANCE)}
            s.add_all(users.values())
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            s.add(h)
            await s.flush()
            dob = date(today.year - 55, 1, 15)
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee", date_of_birth=dob,
                         profile={"tax": {"annual_income": 150_000}})
            bo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Bo Lee", date_of_birth=dob)
            s.add_all([ann, bo])
            await s.flush()

            def ex(role=UserRole.ADVISER):
                u = users[role]
                return ToolExecutors(s, u.id, u.role, firm.id)

            print("\n=== A retirement match ===")
            match = {"person_id": str(ann.id), "kind": "retirement_match", "employer": "Acme", "source_document": "Acme 401(k) summary plan description, 2026",
                     "match_rate": 0.5, "match_up_to": 0.06, "vesting": "Fully vested after 3 years"}
            r = await ex().execute("record_employer_benefit", match)
            b = r["benefits"][0]
            check("recorded, with the most it could add from the income on file", (b["figures"], b["put_in_to_reach_it"], b["most_it_adds_a_year"]), ("assessed", 9000.0, 4500.0))
            check("it is never counted, and says it moves no figure", (b["counted"], "moves no plan figure" in r["message"]), (False, True))
            await ex().execute("record_employer_benefit", {**match, "person_id": str(bo.id)})
            r = await ex().execute("read_employer_benefits", {"household_id": str(h.id)})
            by = {x["person"]: x for x in r["benefits"]}
            check("a person with no income on file is not assessed, never guessed", (by["Bo Lee"]["figures"], "most_it_adds_a_year" in by["Bo Lee"]), ("not_assessed", False))
            check("the household's total is only what is assessed", r["summary"]["most_matches_add_a_year"], 4500.0)
            check("and the unassessed match is counted", r["summary"]["matches_not_assessed"], 1)

            print("\n=== A deferred compensation election ===")
            dc = {"person_id": str(ann.id), "kind": "deferred_compensation", "employer": "Acme", "source_document": "Acme NQDC election form, 2026",
                  "annual_deferral": 40_000, "payout_start_age": 62, "payout_form": "installments", "payout_years": 10, "election_year": 2026}
            r = await ex().execute("record_employer_benefit", dc)
            d = next(x for x in r["benefits"] if x["kind"] == "deferred_compensation")
            check("the election is on file with its payout", (d["annual_deferral"], d["payout_start_age"], d["payout_form"], d["payout_years"]), (40_000.0, 62, "installments", 10))
            check("its payout counts as income only if recorded as a stream", "income stream" in d["payout_counted_as"], True)
            check("not modelled is named", "deferred_payout_counts_only_as_a_recorded_income_stream" in r["not_modelled"], True)
            check("a person with nothing on file is named, never read as none", employer_benefits.NOT_MODELLED != [] and "Bo Lee" not in r["adults_with_none"], True)

            print("\n=== Ending, and refusals ===")
            r = await ex().execute("end_employer_benefit", {"benefit_id": d["id"], "reason": "Left Acme in March"})
            ended = next(x for x in r["benefits"] if x["id"] == d["id"])
            check("an ended benefit is kept, with why", (ended["active"], ended["ended_reason"]), (False, "Left Acme in March"))
            await refused("ended twice", ex().execute("end_employer_benefit", {"benefit_id": d["id"], "reason": "again"}), "already ended")
            other = next(x for x in r["benefits"] if x["person"] == "Bo Lee")
            await refused("no reason to end", ex().execute("end_employer_benefit", {"benefit_id": other["id"], "reason": " "}), "why")
            await refused("a match with no rate", ex().execute("record_employer_benefit", {**match, "match_rate": None}), "rate")
            await refused("a match rate above 200%", ex().execute("record_employer_benefit", {**match, "match_rate": 3}), "at most")
            await refused("pay share above 100%", ex().execute("record_employer_benefit", {**match, "match_up_to": 1.5}), "at most")
            await refused("a match with a deferral on it", ex().execute("record_employer_benefit", {**match, "annual_deferral": 5}), "deferred compensation")
            await refused("a deferral with a match on it", ex().execute("record_employer_benefit", {**dc, "match_rate": 0.5}), "retirement match")
            await refused("installments with no years", ex().execute("record_employer_benefit", {**dc, "payout_years": None}), "number of years")
            await refused("a lump sum with years", ex().execute("record_employer_benefit", {**dc, "payout_form": "lump_sum"}), "no years")
            await refused("no payout form", ex().execute("record_employer_benefit", {**dc, "payout_form": "someday"}), "payout form")
            await refused("no document named", ex().execute("record_employer_benefit", {**match, "source_document": "x"}), "plan document")
            await refused("a kind outside the two", ex().execute("record_employer_benefit", {**match, "kind": "bonus"}), "must be one of")
            await refused("someone not in the firm", ex().execute("record_employer_benefit", {**match, "person_id": str(uuid.uuid4())}), "not found")
            check("three benefits, one ended, none edited", (await s.execute(select(func.count(EmployerBenefit.id)))).scalar(), 3)

            print("\n=== Who may ===")
            check("recording and ending are Confirm cards; reading is a read",
                  (TOOLS["record_employer_benefit"].change_state, TOOLS["end_employer_benefit"].change_state, TOOLS["read_employer_benefits"].change_state),
                  (ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.NO))
            check("no client holds any of them", any(UserRole.CLIENT in TOOLS[k].roles_required for k in ("read_employer_benefits", "record_employer_benefit", "end_employer_benefit")), False)
            await refused("compliance does not record one", ex(UserRole.COMPLIANCE).execute("record_employer_benefit", match), "adviser or a paraplanner")
            check("compliance reads", len((await ex(UserRole.COMPLIANCE).execute("read_employer_benefits", {"household_id": str(h.id)}))["benefits"]), 3)
            card = await ex().preview_change("record_employer_benefit", match)
            rows = {x["label"]: x["value"] for x in card["rows"]}
            check("the card says what the employer adds and the document", (rows["Employer adds"], rows["Up to"], rows["Plan document"]),
                  ("50% of what they put in", "6.0% of pay", "Acme 401(k) summary plan description, 2026"))

            print("\n=== An income stream may end at an age ===")
            base = {"person_id": str(ann.id), "kind": "employer_pension", "annual_amount": 20_000, "start_age": 62, "source_document": "Acme pension statement 2026", "name": "Term pension"}
            await refused("an ending age before the start", ex().execute("record_income_stream", {**base, "end_age": 60}), "after the starting age")
            await refused("an ending age past 110", ex().execute("record_income_stream", {**base, "end_age": 120}), "at most 110")
            await refused("Social Security has no end", ex().execute("record_income_stream", {**base, "kind": "social_security", "end_age": 80}), "for life")
            await refused("an ending age that is not a number", ex().execute("record_income_stream", {**base, "end_age": "later"}), "whole number")
            card = await ex().preview_change("record_income_stream", {**base, "end_age": 72})
            check("the card says until when", {x["label"]: x["value"] for x in card["rows"]}.get("Until age"), "72")
            row = await financial_plan.record_stream(s, ann.id, firm_id=firm.id, by="a@t", kind="employer_pension", name="Term pension", annual_amount=20_000,
                                                     start_age=62, end_age=72, source_document="Acme pension statement 2026")
            check("it is on the stream", row.end_age, 72)
            view = (await financial_plan.streams_of(s, h.id, firm, today))[0]
            check("the view says when it stops, in years from today", (view["end_age"], view["ends_in"]), (72, 72 - 55))
            lifelong = await financial_plan.record_stream(s, bo.id, firm_id=firm.id, by="a@t", kind="employer_pension", name="For life", annual_amount=10_000,
                                                          start_age=62, source_document="Acme pension statement 2026")
            check("a stream with no end is for life", lifelong.end_age, None)

            print("\n=== The engines stop paying it ===")
            from app.aurea_core.retirement import _draws

            term = streams((20_000, 5, 15))
            draws = _draws(100_000, term, acc=5, dec=20, inflation=0.0)
            check("paid from the start year, and not after it ends (no inflation)", (draws[0], draws[9], draws[10]), (80_000.0, 80_000.0, 100_000.0))
            check("with no end it is paid throughout", _draws(100_000, streams((20_000, 5, None)), acc=5, dec=20, inflation=0.0)[19], 80_000.0)
            plan = retirement.retirement_plan(current_value=2_000_000, allocation={"equity": 1.0}, current_age=60, retirement_age=65, longevity_age=95,
                                              annual_income=100_000, streams=streams((20_000, 5, 15)), sims=200)
            over = retirement.retirement_plan(current_value=2_000_000, allocation={"equity": 1.0}, current_age=60, retirement_age=65, longevity_age=95,
                                              annual_income=100_000, streams=streams((20_000, 0, 5)), sims=200)
            none = retirement.retirement_plan(current_value=2_000_000, allocation={"equity": 1.0}, current_age=60, retirement_age=65, longevity_age=95,
                                              annual_income=100_000, streams=[], sims=200)
            check("a stream paid at retirement is guaranteed income", plan["guaranteed_at_retirement"], 20_000.0)
            check("one that ended by retirement counts for nothing, exactly as if there were none", (over["guaranteed_at_retirement"], over["income_target"]),
                  (0.0, none["income_target"]))
            ret = await financial_plan.retirement_inputs(s, h.id, firm_id=firm.id, retirement_age=65, current_age=55)
            check("on file: a pension ending at 72 pays at 65 and is guaranteed", [(x["name"], x["starts_in"], x["ends_in"]) for x in ret["streams"] if x["name"] == "Term pension"],
                  [("Term pension", 7, 17)])
            ended_early = await financial_plan.retirement_inputs(s, h.id, firm_id=firm.id, retirement_age=75, current_age=55)
            check("retiring after it ends: it counts for nothing, and is not 'starts later'", (ended_early["guaranteed"], ended_early["starts_later"]), (10_000.0 * 0 + 10_000.0, 0.0))

            print("\n=== The Roth window stops it too ===")
            dobs, names = {str(ann.id): dob}, {str(ann.id): "Ann Lee"}
            sv = [{"person_id": str(ann.id), "kind": "employer_pension", "name": "T", "planned_annual": 20_000, "inflation_linked": True, "start_age": 62, "end_age": 72}]
            years = {y: roth_window.income_in(sv, dobs, names, y, today=today, inflation=0.0)[2] for y in (dob.year + 61, dob.year + 62, dob.year + 71, dob.year + 72)}
            check("before it starts, while paid, last year paid, and after it ends", list(years.values()), [0.0, 20_000.0, 20_000.0, 0.0])

            print("\n=== Riders, umbrella and property cover ===")
            life = await protection.record_policy(s, firm.id, person_id=ann.id, kind="life", provider="Allied", source_document="Allied life schedule 2026", recorded_by="a@t",
                                                  sum_insured=500_000)
            await protection.record_policy(s, firm.id, person_id=ann.id, kind="critical_illness", provider="Allied", source_document="Allied rider schedule 2026", recorded_by="a@t",
                                           sum_insured=100_000, rider_of=uuid.UUID(life["policy_id"]))
            before = await protection.for_household(s, h.id, firm_id=firm.id)
            await protection.record_policy(s, firm.id, person_id=ann.id, kind="umbrella", provider="Chubb", source_document="Chubb umbrella declarations 2026", recorded_by="a@t",
                                           sum_insured=2_000_000)
            await protection.record_policy(s, firm.id, person_id=bo.id, kind="property", provider="Travelers", source_document="Travelers home policy 2026", recorded_by="a@t",
                                           sum_insured=900_000)
            view = await protection.for_household(s, h.id, firm_id=firm.id)
            rows = {r["kind"] + ("/rider" if r["rider_of"] else ""): r for r in view["policies"]}
            check("the base policy lists its rider", [(x["kind"], x["sum_insured"]) for x in rows["life"]["riders"]], [("critical_illness", 100_000.0)])
            check("the rider is not also a policy to count: critical illness is not counted", rows["critical_illness/rider"]["counted"], False)
            check("umbrella is listed, not counted, and says why", (rows["umbrella"]["counted"], "not counted against a gap" in rows["umbrella"]["not_counted"]), (False, True))
            check("property likewise", (rows["property"]["counted"], rows["property"]["not_counted"] is not None), (False, True))
            check("neither moves anyone's death, disability or care figures",
                  [(p["death"], p["disability"], p["care"]) for p in view["people"]], [(p["death"], p["disability"], p["care"]) for p in before["people"]])
            await refused("a rider on someone else's policy", protection.record_policy(s, firm.id, person_id=bo.id, kind="critical_illness", provider="Other Co", source_document="Rider schedule 2026",
                                                                                     recorded_by="a@t", sum_insured=1, rider_of=uuid.UUID(life["policy_id"])), "same person")
            await refused("umbrella with no limit", protection.record_policy(s, firm.id, person_id=ann.id, kind="umbrella", provider="Chubb", source_document="Chubb declarations 2026",
                                                                           recorded_by="a@t"), "sum insured")
            check("the new kinds are known", {"umbrella", "property"} <= set(__import__("app.models.protection", fromlist=["POLICY_KINDS"]).POLICY_KINDS), True)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
