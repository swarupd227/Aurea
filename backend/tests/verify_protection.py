"""Prove protection planning: the gaps are worked from what is on file and nothing else, a missing
income is reported rather than estimated, and cover only counts while it is in force.

Figures are checked by hand: 20 years at 4% is an annuity factor of 13.590326.

Runs against a throwaway SQLite database.

    python -m tests.verify_protection
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import protection as api
from app.aurea_core import protection as pr
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm
from seed import person_tax_backfill

PASS, FAIL = [], []
A20 = 13.590326  # (1 - 1.04^-20) / 0.04


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def near(label: str, got, want, tol=1.0) -> None:
    check(label, abs(got - want) <= tol, True) if abs(got - want) <= tol else check(label, got, want)


async def refused(label: str, coro, contains: str | None = None, exc=pr.ProtectionError) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:90]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def sums() -> None:
    print("\n=== Death ===")
    base = dict(age=45, income=200_000.0, other_income=100_000.0, has_dependants=True, retirement_age=65,
                goals_due=50_000.0, assets=500_000.0)
    r = pr.assess(**base, life=300_000.0)
    d = r["death"]
    check("20 years to retirement", r["years"], 20)
    check("the survivors need 70% of household income, less what they still earn", d["annual_need"], 110_000.0)
    near("to retirement, in today's money", d["income_need"], 110_000 * A20)
    near("plus the goals falling due", d["need"], 110_000 * A20 + 50_000)
    near("less cover and assets", d["gap"], 110_000 * A20 + 50_000 - 800_000)
    check("that is a gap", d["status"], "gap")
    near("and the gap before assets is shown too", d["gap_before_assets"], 110_000 * A20 + 50_000 - 300_000)

    alone = pr.assess(**{**base, "has_dependants": False})["death"]
    check("with nobody depending on the income, only the goals are a need", (alone["income_need"], alone["need"]), (0.0, 50_000.0))
    rich = pr.assess(**{**base, "other_income": 500_000.0})["death"]
    check("a survivor who out-earns the need leaves none", rich["annual_need"], 0.0)
    wealthy = pr.assess(**{**base, "assets": 5_000_000.0})["death"]
    check("assets can close it: self-insured", (wealthy["status"], wealthy["gap"]), ("no_gap", 0.0))
    check("but what the assets did stays visible", wealthy["gap_before_assets"] > 0, True)

    print("\n=== Disability ===")
    d = pr.assess(**base)["disability"]
    check("75% of the earner's own income", d["annual_need"], 150_000.0)
    near("to retirement", d["need"], 150_000 * A20)
    d = pr.assess(**{**base, "assets": 0.0}, income_benefits=[(100_000.0, None)], tpd=200_000.0)["disability"]
    near("an income benefit paid to retirement is worth its annuity", d["income_benefit_value"], 100_000 * A20)
    near("and the gap takes it and the lump sum off", d["gap"], 50_000 * A20 - 200_000)
    short = pr.assess(**{**base, "assets": 0.0}, income_benefits=[(100_000.0, 55)])["disability"]
    near("a benefit that stops at 55 is worth 10 years, not 20", short["income_benefit_value"], 100_000 * 8.110896)
    late = pr.assess(**{**base, "assets": 0.0}, income_benefits=[(100_000.0, 80)])["disability"]
    near("one running past retirement is capped at it", late["income_benefit_value"], 100_000 * A20)

    print("\n=== What is not on file is not guessed ===")
    r = pr.assess(**{**base, "income": None})
    check("no income: not assessed, and it says why", (r["death"]["status"], r["death"]["missing"]), ("not_assessed", ["income"]))
    check("and no figures", "gap" in r["death"], False)
    r = pr.assess(**{**base, "age": None})
    check("no date of birth likewise", r["disability"]["missing"], ["date_of_birth"])
    check("both are named", pr.assess(**{**base, "age": None, "income": None})["death"]["missing"], ["income", "date_of_birth"])
    r = pr.assess(**{**base, "income": 0.0})
    check("a stated income of zero is assessed", r["disability"]["status"], "no_gap")
    check("past retirement there is no earnings to protect", pr.assess(**{**base, "age": 66})["death"]["status"], "past_retirement")
    r = pr.assess(**{**base, "age": 68}, retirement_stated=False)
    check("past the default age with an income has not retired: not assessed, and it says why",
          (r["death"]["status"], r["death"]["missing"]), ("not_assessed", ["retirement_date"]))
    r = pr.assess(**{**base, "age": 68, "income": 0.0}, retirement_stated=False)
    check("past it with no income has nothing to protect", r["death"]["status"], "past_retirement")
    custom = pr.assess(**base, rules={"death_replacement": 0.5})["death"]
    check("a firm's own replacement rate is used", custom["annual_need"], 50_000.0)


async def main() -> int:
    sums()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date(2026, 10, 30)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD")
            s.add_all([firm, other])
            await s.flush()

            def person(h, name, income=None, age=45, f=firm, held=None, next_gen=False, dob=True):
                profile = {}
                if income is not None:
                    profile["tax"] = {"annual_income": income, "marginal_rate": 0.33}
                if held:
                    profile["held_away"] = held
                p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=name, is_next_gen=next_gen,
                           date_of_birth=date(today.year - age, 1, 1) if dob else None, profile=profile)
                s.add(p)
                return p

            def household(name, f=firm):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name)
                s.add(h)
                return h

            async def rec(p, kind="life", **kw):
                kw.setdefault("sum_insured", 300_000 if kind != "income_protection" else None)
                return await pr.record_policy(s, firm.id, person_id=p.id, kind=kind, provider="Insurer",
                                              source_document="Policy schedule 2026", recorded_by="adv@x", today=today, **kw)

            chen = household("Chen")
            await s.flush()
            wei = person(chen, "Wei", 200_000, held=100_000)
            mei = person(chen, "Mei", 100_000)
            lucas = person(chen, "Lucas", next_gen=True, age=20)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=wei.id, name="M", mandate_type="advisory")
            s.add(m)
            await s.flush()
            s.add(Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="A/C", currency="NZD", cash_balance=400_000))
            s.add_all([
                Goal(firm_id=firm.id, household_id=chen.id, name="School", kind="education", target_amount=100_000,
                     target_date=today + timedelta(days=int(365.25 * 5))),
                Goal(firm_id=firm.id, household_id=chen.id, name="Far off", kind="education", target_amount=999_000,
                     target_date=today + timedelta(days=int(365.25 * 30))),
                Goal(firm_id=firm.id, household_id=chen.id, name="Retire", kind="retirement", target_amount=9_000_000,
                     target_date=today + timedelta(days=3650), assumptions={"retirement_age": 65}),
                Goal(firm_id=firm.id, household_id=chen.id, name="Leave", kind="legacy", target_amount=5_000_000,
                     target_date=today + timedelta(days=3650)),
            ])
            await s.flush()

            print("\n=== One household ===")
            v = await pr.for_household(s, chen.id, firm_id=firm.id, today=today)
            names = [p["name"] for p in v["people"]]
            check("a child with no income is not an earner to assess", sorted(names), ["Mei", "Wei"])
            check("assets are managed plus held away", v["assets"], {"managed": 400_000.0, "held_away": 100_000.0, "total": 500_000.0})
            w = next(p for p in v["people"] if p["name"] == "Wei")
            check("each earner's survivor income is the other's", w["death"]["annual_need"], 110_000.0)
            near("only a goal due before retirement, in today's money, is a need",
                 w["death"]["goals_need"], 100_000 / (1.04 ** 5), tol=5.0)
            check("retirement and legacy goals are not", w["death"]["goals_need"] < 100_000, True)
            check("cover not yet on file", w["death"]["cover"], 0.0)
            check("what is left out is stated", "debts" in v["not_modelled"], True)
            check("the assumptions travel with the figures", v["assumptions"]["death_replacement"], 0.7)

            print("\n=== Cover counts while it is in force ===")
            before = w["death"]["gap"]
            await rec(wei, sum_insured=500_000)
            await rec(wei, kind="tpd", sum_insured=100_000)
            await rec(wei, kind="income_protection", annual_benefit=60_000)
            await rec(mei, sum_insured=77_000)
            v = await pr.for_household(s, chen.id, firm_id=firm.id, today=today)
            w = next(p for p in v["people"] if p["name"] == "Wei")
            check("life cover closes the gap by its sum", round(before - w["death"]["gap"], 2), 500_000.0)
            check("another person's policy is not this person's cover", w["death"]["cover"], 500_000.0)
            check("TPD and income both count against disability", w["disability"]["lump_sum"], 100_000.0)
            near("the income benefit at its annuity", w["disability"]["income_benefit_value"], 60_000 * A20)
            check("four policies counted", v["summary"]["policies_counted"], 4)

            term = await rec(wei, sum_insured=1_000_000, expires_on=today + timedelta(days=30))
            v = await pr.for_household(s, chen.id, firm_id=firm.id, today=today + timedelta(days=31))
            row = next(p for p in v["policies"] if p["id"] == term["policy_id"])
            check("a term that has run out stops counting", (row["status"], row["counted"]), ("expired", False))
            ended = await pr.end_policy(s, firm.id, uuid.UUID(term["policy_id"]), reason="Replaced", by="adv@x", today=today)
            check("ending one keeps it, with why", ended["status"], "ended")
            kept = await s.get(ProtectionPolicy, uuid.UUID(term["policy_id"]))
            check("who ended it and why are on the row", (kept.ended_by, kept.ended_reason), ("adv@x", "Replaced"))
            v = await pr.for_household(s, chen.id, firm_id=firm.id, today=today)
            check("and it no longer counts", next(p for p in v["policies"] if p["id"] == term["policy_id"])["counted"], False)
            await refused("ending it twice is refused",
                          pr.end_policy(s, firm.id, uuid.UUID(term["policy_id"]), reason="Again", by="x", today=today),
                          "already ended")
            await refused("ending needs a reason", pr.end_policy(s, firm.id, uuid.uuid4(), reason=" ", by="x"), "why")
            await refused("another firm cannot end it",
                          pr.end_policy(s, other.id, uuid.UUID(term["policy_id"]), reason="Nope", by="x"), "not found")

            print("\n=== A record that cannot be taken at face value ===")
            await refused("no source document", pr.record_policy(
                s, firm.id, person_id=wei.id, kind="life", provider="X Ltd", sum_insured=1, source_document=" ",
                recorded_by="x"), "document")
            await refused("a kind it does not know", rec(wei, kind="trauma"), "unknown policy kind")
            await refused("a life policy with no sum insured", rec(wei, sum_insured=0), "sum insured")
            await refused("income cover with no benefit", rec(wei, kind="income_protection"), "annual benefit")
            await refused("a negative sum", rec(wei, sum_insured=-5), "negative")
            await refused("something that is not a number", rec(wei, sum_insured="plenty"), "number")
            await refused("a policy already expired", rec(wei, expires_on=today - timedelta(days=1)), "expired")
            await refused("a person from another firm", pr.record_policy(
                s, other.id, person_id=wei.id, kind="life", provider="X Ltd", sum_insured=1,
                source_document="Doc 2026", recorded_by="x"), "not in this firm")

            print("\n=== Recording an income ===")
            gap = household("Gap")
            await s.flush()
            noinc = person(gap, "No Income")
            partner = person(gap, "Partner", 50_000)
            await s.flush()
            v = await pr.for_household(s, gap.id, firm_id=firm.id, today=today)
            row = next(p for p in v["people"] if p["name"] == "No Income")
            check("someone with no income is not assessed", (row["death"]["status"], row["income"]), ("not_assessed", None))
            p2 = next(p for p in v["people"] if p["name"] == "Partner")
            check("and their partner's need is flagged as overstated", p2["other_income_missing"], True)
            check("the summary counts them", v["summary"]["not_assessed"], 1)
            out = await pr.record_income(s, firm.id, person_id=noinc.id, annual_income=90_000,
                                         source_document="Payslip Oct 2026", recorded_by="adv@x")
            check("recording says what it replaced", (out["annual_income"], out["previous"]), (90_000.0, None))
            await s.commit()
            await s.refresh(noinc)
            fresh = noinc
            check("it reaches the database, in the field the tax modules read",
                  fresh.profile["tax"]["annual_income"], 90_000.0)
            check("with its source", fresh.profile["tax"]["income_source"]["document"], "Payslip Oct 2026")
            v = await pr.for_household(s, gap.id, firm_id=firm.id, today=today)
            check("now they are assessed", next(p for p in v["people"] if p["name"] == "No Income")["death"]["status"], "gap")
            await pr.record_income(s, firm.id, person_id=wei.id, annual_income=210_000, source_document="Payslip", recorded_by="x")
            check("other tax details are left alone", wei.profile["tax"]["marginal_rate"], 0.33)
            await refused("an income needs a source", pr.record_income(
                s, firm.id, person_id=wei.id, annual_income=1, source_document="", recorded_by="x"), "document")
            await refused("and cannot be negative", pr.record_income(
                s, firm.id, person_id=wei.id, annual_income=-1, source_document="Payslip", recorded_by="x"), "negative")
            await refused("another firm cannot record one", pr.record_income(
                s, other.id, person_id=wei.id, annual_income=1, source_document="Payslip", recorded_by="x"), "not in this firm")

            print("\n=== When someone retires comes from their goal ===")
            late = household("Late")
            await s.flush()
            robert = person(late, "Robert", 800_000, age=67)
            await s.flush()
            s.add(Goal(firm_id=firm.id, household_id=late.id, name="Retire", kind="retirement", target_amount=1,
                       target_date=today + timedelta(days=int(365.25 * 8))))
            await s.flush()
            v = await pr.for_household(s, late.id, firm_id=firm.id, today=today)
            row = v["people"][0]
            check("a goal date puts retirement at 75, not the default 65",
                  (row["retirement_age"], row["retirement_basis"], row["years_to_retirement"]), (75, "goal_date", 8))
            check("so a 67-year-old on an income is assessed", row["disability"]["status"] in ("gap", "no_gap"), True)
            none = household("Working on")
            await s.flush()
            person(none, "James", 320_000, age=68)
            await s.flush()
            v = await pr.for_household(s, none.id, firm_id=firm.id, today=today)
            check("with no goal, a 68-year-old on an income is not called retired",
                  (v["people"][0]["death"]["status"], v["people"][0]["death"]["missing"], v["people"][0]["retirement_basis"]),
                  ("not_assessed", ["retirement_date"], "assumed"))
            stopped = household("Stopped")
            await s.flush()
            person(stopped, "Retired", 0, age=70)
            await s.flush()
            v = await pr.for_household(s, stopped.id, firm_id=firm.id, today=today)
            check("someone retired is measured as nothing, not as covered", v["summary"]["assessed"], 0)

            print("\n=== The book ===")
            trust = household("Trust only")
            await s.flush()
            s.add(LegalEntity(firm_id=firm.id, household_id=trust.id, name="Trust", entity_type="trust"))
            theirs = household("Theirs", f=other)
            unknown = household("Unknown")
            await s.flush()
            person(theirs, "Their Client", 1_000_000, f=other)
            person(unknown, "Blank")
            await s.flush()
            book = await pr.for_firm(s, firm.id, today=today)
            order = [h["household"] for h in book["households"]]
            check("another firm's households are not shown", "Theirs" in order, False)
            check("the largest gap comes first", book["households"][0]["summary"]["largest_gap"] >=
                  book["households"][1]["summary"]["largest_gap"], True)
            check("the counts add up", book["summary"]["households"],
                  sum(book["summary"][k] for k in ("with_gap", "not_assessed", "clear", "no_people")))
            check("a trust and a retired household have no one to assess", book["summary"]["no_people"], 2)
            check("a retired household is not called clear",
                  next(h for h in book["households"] if h["household"] == "Stopped")["summary"]["assessed"], 0)
            check("households with no income or no retirement date are not called clear",
                  book["summary"]["not_assessed"], 2)
            check("no household in another firm can be read",
                  await pr.for_household(s, theirs.id, firm_id=firm.id, today=today), None)

            print("\n=== Restoring the seed's income details ===")
            demo = Firm(id=uuid.uuid4(), name="Demo", slug="demo", base_currency="NZD")
            s.add(demo)
            await s.flush()
            dh = household("The Chen Family", f=demo)
            await s.flush()
            dwei = person(dh, "Wei Chen", f=demo, held=620_000)
            dmei = person(dh, "Mei Chen", 150_000, f=demo)
            await s.flush()
            first = await person_tax_backfill.apply(s)
            check("a person with no tax details gets the seed's", (first, dwei.profile["tax"]["annual_income"]), (1, 280_000))
            check("what else was on the profile is kept", dwei.profile["held_away"], 620_000)
            check("properties come with it", len(dwei.profile["properties"]), 2)
            check("a figure already on file is never overwritten", dmei.profile["tax"]["annual_income"], 150_000)
            check("running it again changes nothing", await person_tax_backfill.apply(s), 0)

            print("\n=== The tools and the routes ===")
            check("reading changes nothing", TOOLS["read_protection"].change_state, ToolChangeState.NO)
            for key in ("record_protection_policy", "end_protection_policy", "record_income"):
                check(f"{key} waits for confirmation", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                check(f"{key} is for advisers, paraplanners and operations", TOOLS[key].roles_required,
                      {UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
            check("a client holds none of them", any(UserRole.CLIENT in TOOLS[k].roles_required for k in
                  ("read_protection", "record_protection_policy", "end_protection_policy", "record_income")), False)
            check("compliance can read but not record",
                  (UserRole.COMPLIANCE in TOOLS["read_protection"].roles_required,
                   UserRole.COMPLIANCE in TOOLS["record_income"].roles_required), (True, False))

            adv = User(id=uuid.uuid4(), firm_id=firm.id, email="adv@x", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adv)
            await s.flush()
            run = ToolExecutors(s, adv.id, UserRole.ADVISER, firm.id)
            out = await run.execute("read_protection", {"household_id": str(chen.id)})
            check("the read tool answers for one household", (out["household"], "gap" in out["message"]), ("Chen", True))
            out = await run.execute("read_protection", {})
            check("and for the book", "households" in out, True)
            try:
                await run.execute("read_protection", {"household_id": str(theirs.id)})
                check("another firm's household is refused", False, True)
            except ExecutorError:
                check("another firm's household is refused", True, True)
            out = await run.execute("record_protection_policy", {
                "person_id": str(mei.id), "kind": "LIFE", "provider": "Insurer", "sum_insured": 250_000,
                "expires_on": "2030-01-01", "source_document": "Schedule 2026"})
            check("the record tool works and says so", "Recorded life cover" in out["message"], True)
            out = await run.execute("end_protection_policy", {"policy_id": out["policy_id"], "reason": "Lapsed"})
            check("so does ending it", "no longer counts" in out["message"], True)
            out = await run.execute("record_income", {"person_id": str(mei.id), "annual_income": 110_000,
                                                      "source_document": "Payslip Oct"})
            check("and recording an income", out["annual_income"], 110_000.0)
            comp = ToolExecutors(s, adv.id, UserRole.COMPLIANCE, firm.id)
            try:
                await comp.execute("record_income", {"person_id": str(mei.id), "annual_income": 1, "source_document": "Pay"})
                check("compliance is refused at the executor too", False, True)
            except ExecutorError:
                check("compliance is refused at the executor too", True, True)
            try:
                await run.execute("record_protection_policy", {"person_id": str(mei.id), "kind": "life", "provider": "X",
                                                               "sum_insured": 1, "source_document": "Doc",
                                                               "expires_on": "soon"})
                check("a bad date is refused", False, True)
            except ExecutorError:
                check("a bad date is refused", True, True)

            view = await api.read(user=SimpleNamespace(role=UserRole.COMPLIANCE), firm=firm, db=s)
            check("compliance sees the page without recording", view["can_record"], False)
            comp_user = SimpleNamespace(role=UserRole.COMPLIANCE, email="c@x")
            adv_user = SimpleNamespace(role=UserRole.ADVISER, email="adv@x")
            body = api.IncomeIn(person_id=mei.id, annual_income=1, source_document="Pay")
            try:
                await api.record_income(body, user=comp_user, firm=firm, db=s)
                check("the route refuses compliance", False, True)
            except HTTPException as e:
                check("the route refuses compliance", e.status_code, 403)
            try:
                await api.record_policy(api.PolicyIn(person_id=mei.id, kind="life", provider="X Ltd", source_document="Doc"),
                                        user=adv_user, firm=firm, db=s)
                check("a policy with no sum is a 422", False, True)
            except HTTPException as e:
                check("a policy with no sum is a 422", e.status_code, 422)
            try:
                await api.read_household(theirs.id, user=adv_user, firm=firm, db=s)
                check("another firm's household is a 404", False, True)
            except HTTPException as e:
                check("another firm's household is a 404", e.status_code, 404)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
