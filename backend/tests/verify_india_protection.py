"""Prove health cover and the protection questions India asks (B-397).

  1. The tools: two reads that change nothing, four changes behind a Confirm card, none held by a client.
  2. A health policy is recorded from its schedule, each limiting term only as stated, and refused otherwise.
  3. The household view: own cover, employer-only, a floater shared, a top-up counted only where base cover reaches its
     deductible, parents, waiting periods, renewals - and nothing assessed until the firm states a figure.
  4. The firm's figure is a branch leader's or an administrator's, with its source; no default is ever supplied.
  5. Health cover moves no death or disability gap, and counts as insurance for the non-SEBI declaration.
  6. An endowment or unit-linked statement lays out surrender, paid-up and continue as figures, recommends nothing, and
     its life cover is counted once.
  7. A waiver-of-premium rider and critical illness are listed as recorded, never counted.
  8. Another firm reads and changes nothing, and a household taxed elsewhere is refused.
  9. The demonstration carries each answer, once.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_protection
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, protection
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.protection import ProtectionPolicy
from app.models.tenant import Firm

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def section(title: str, fn) -> None:
    """One part's checks. A section that raises (the feature is not there) is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:300]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        check(label, contains.lower() in str(e).lower() or str(e), True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def labels(card: dict) -> dict:
    return {r["label"]: r["value"] for r in card["rows"]}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN",
                        settings={"timezone": "Asia/Kolkata",
                                  "protection": {"care": {"annual_cost": 600000, "years": 3, "source": "x", "set_by": "y"}}})
            other = Firm(id=uuid.uuid4(), name="IN2", slug="in2", base_currency="INR", jurisdiction="IN", settings={})
            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([firm, other, us])
            await s.flush()

            def user(f, role):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{role.value}@{f.slug}", full_name=role.value, role=role,
                         hashed_password="x")
                s.add(u)
                return u

            users = {r: user(firm, r) for r in (R.ADVISER, R.PARAPLANNER, R.OPERATIONS, R.BRANCH_LEADER, R.COMPLIANCE, R.CLIENT)}
            outsider_user, us_user = user(other, R.ADVISER), user(us, R.ADVISER)
            outsider_leader, us_leader = user(other, R.BRANCH_LEADER), user(us, R.BRANCH_LEADER)
            await s.flush()
            ex = {r: ToolExecutors(s, u.id, r, firm.id) for r, u in users.items()}
            adviser, leader = ex[R.ADVISER], ex[R.BRANCH_LEADER]
            outsider = ToolExecutors(s, outsider_user.id, R.ADVISER, other.id)
            american = ToolExecutors(s, us_user.id, R.ADVISER, us.id)
            today = firm_time.today(firm)

            async def home(f, name, people):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name)
                s.add(h)
                await s.flush()
                out = []
                for full, born, income in people:
                    p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=full, date_of_birth=born,
                               profile={"tax": {"annual_income": income}} if income else {}, is_next_gen=income is None)
                    s.add(p)
                    out.append(p)
                await s.flush()
                return h, out

            mehta, (arjun, priya, kid) = await home(firm, "The Mehta Family", [
                ("Arjun Mehta", date(1978, 4, 2), 4_500_000), ("Priya Mehta", date(1980, 9, 12), 1_500_000),
                ("Isha Mehta", today - timedelta(days=15 * 365), None)])
            us_home, (smith,) = await home(us, "The Smiths", [("John Smith", date(1970, 1, 1), 100_000)])
            term = ProtectionPolicy(firm_id=firm.id, household_id=mehta.id, person_id=arjun.id, kind="life", provider="Term insurer",
                                    sum_insured=5_000_000, currency="INR", status="in_force", source_document="Schedule 2024",
                                    recorded_by="a")
            s.add(term)
            await s.flush()

            schedule = lambda **kw: {"household_id": str(mehta.id), "kind": "family_floater", "provider": "Care Insurer",  # noqa: E731
                                     "sum_insured": 1_000_000, "members": "Arjun Mehta: self; Priya Mehta: spouse; Isha Mehta: daughter",
                                     "source_document": "Policy schedule, 1 Nov 2026, Care Insurer", **kw}

            async def tools():
                for key in ("read_health_cover", "read_policy_values"):
                    check(f"{key} changes nothing", TOOLS[key].change_state, ToolChangeState.NO)
                for key in ("record_health_policy", "end_health_policy", "set_health_cover_figure", "record_policy_values"):
                    check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                    check(f"a client holds no {key}", R.CLIENT in TOOLS[key].roles_required, False)
                check("nor a read", any(R.CLIENT in TOOLS[k].roles_required for k in ("read_health_cover", "read_policy_values")), False)
                check("the figure is a branch leader's or an administrator's", TOOLS["set_health_cover_figure"].roles_required,
                      {R.BRANCH_LEADER, R.ADMIN})

            async def recording():
                before = (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one()
                card = labels(await adviser.preview_change("record_health_policy", schedule(
                    room_rent_cap="1% of the sum insured a day", co_payment_percent=10,
                    sub_limits="Cataract: Rs 40,000 an eye", waiting_periods="Pre-existing diseases: 36 months",
                    covered_since=(today - timedelta(days=400)).isoformat(), renewal_on=(today + timedelta(days=30)).isoformat())))
                check("the card names the client, cover, insurer, people, sum and document",
                      all(k in card for k in ("Client", "Cover", "Insurer", "Insured", "Sum insured", "Document")), True)
                check("and each limit stated", all(k in card for k in ("Room-rent cap", "Co-payment", "Sub-limits", "Waiting periods", "Renews on")), True)
                check("a floater's sum reads shared", "shared" in str(card["Sum insured"]), True)
                check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one(), before)
                p = lambda **kw: adviser.preview_change("record_health_policy", schedule(**kw))  # noqa: E731
                await refused("it needs its document", p(source_document=""), "document")
                await refused("and a sum insured", p(sum_insured=None), "sum insured")
                await refused("a super top-up needs its deductible", p(kind="super_top_up"), "deductible")
                await refused("a floater has no deductible here", p(deductible=500000), "deductible")
                await refused("an individual policy insures one", p(kind="individual"), "one person")
                await refused("a floater is shared", p(members="Arjun Mehta: self"), "at least two")
                await refused("a relationship is said", p(members="Arjun Mehta; Priya Mehta: spouse"), "relationship")
                await refused("and is one it knows", p(members="Arjun Mehta: boss; Priya Mehta: spouse"), "boss")
                await refused("a person is named once", p(members="Arjun Mehta: self; arjun mehta: self"), "twice")
                await refused("a passed renewal date is not cover", p(renewal_on=(today - timedelta(days=1)).isoformat()), "passed")
                await refused("a waiting period reads as stated", p(waiting_periods="Pre-existing diseases: a while"), "waiting period")
                await refused("a sub-limit has its limit", p(sub_limits="Cataract"), "sub-limit")
                await refused("a co-payment is a percent", p(co_payment_percent=120), "percent")
                await refused("compliance does not record it", ex[R.COMPLIANCE].preview_change("record_health_policy", schedule()), "adviser")
                await refused("another firm's household is not found", outsider.preview_change("record_health_policy", schedule()), "not in this firm")
                await refused("a US household is not recorded", american.preview_change("record_health_policy", schedule(
                    household_id=str(us_home.id), members="John Smith: self", kind="individual")), "Indian")

                await adviser.execute("record_health_policy", schedule(
                    kind="employer_group", provider="Employer Insurer", sum_insured=500_000,
                    source_document="Employer benefit certificate, 2026"))
                await refused("the same policy is not counted twice", adviser.preview_change("record_health_policy", schedule(
                    kind="employer_group", provider="employer insurer", sum_insured=500_000)), "already on file")
                await ex[R.OPERATIONS].execute("record_health_policy", schedule(
                    kind="individual", provider="Senior Insurer", sum_insured=500_000, members="Kamla Mehta: mother",
                    co_payment_percent=20, co_payment_terms="on every claim", room_rent_cap="Single private room",
                    sub_limits="Cataract: Rs 40,000 an eye; Knee replacement: Rs 2,00,000",
                    waiting_periods="Pre-existing diseases: 36 months; Maternity: until 2020-01-01",
                    covered_since=(today - timedelta(days=200)).isoformat(), renewal_on=(today + timedelta(days=20)).isoformat()))
                check("two recorded", (await s.execute(select(func.count()).select_from(LedgerEntry).where(
                    LedgerEntry.event_type == "health_policy_recorded"))).scalar_one(), 2)

            def member(view, name):
                return next(m for m in view["members"] if m["name"] == name)

            async def view_before_figure():
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                check("everyone on record and the named parent", sorted(m["name"] for m in v["members"]),
                      ["Arjun Mehta", "Isha Mehta", "Kamla Mehta", "Priya Mehta"])
                a, k = member(v, "Arjun Mehta"), member(v, "Kamla Mehta")
                check("only the employer's cover", (a["position"], a["own_cover"], a["employer_cover"]), ("employer_only", 0.0, 500000.0))
                check("a parent not on record is covered personally", (k["position"], k["group"], k["on_record"], k["own_cover"]),
                      ("own", "parent", False, 500000.0))
                check("with no figure the need is not assessed", {m["need"]["status"] for m in v["members"]}, {"not_assessed"})
                check("and says why", a["need"]["missing"], ["no_firm_figure"])
                senior = next(p for p in v["policies"] if p["provider"] == "Senior Insurer")
                waiting = {w["condition"]: w for w in senior["waiting_periods"]}
                since = today - timedelta(days=200)
                from app.aurea_core.health_cover import add_months
                check("a waiting period's end is worked from the start of cover, and says so",
                      (waiting["Pre-existing diseases"]["ends_on"], waiting["Pre-existing diseases"]["basis"]),
                      (add_months(since, 36).isoformat(), "worked from the start of cover"))
                check("a document's end date stands", (waiting["Maternity"]["ends_on"], waiting["Maternity"]["running"]), ("2020-01-01", False))
                limits = " | ".join(k["limits"])
                check("the limits are listed", all(w in limits for w in ("room rent: Single private room", "co-payment 20% on every claim",
                                                                         "sub-limit: Cataract", "waiting: Pre-existing diseases")), True)
                check("a waiting period that has run out limits nothing", "Maternity" in limits, False)
                check("a renewal within 60 days is counted", v["summary"]["renewals_due"], 1)
                check("the answer names employer-only cover and the missing figure",
                      ("only the employer's cover" in v["message"], "stated no health cover figure" in v["message"]), (True, True))
                check("what is not modelled travels with it", "co_payment_and_sub_limits_not_netted" in v["not_modelled"], True)

            async def figure():
                good = {"family_cover": 2_000_000, "source_document": "Advice committee minute, 3 Nov 2026"}
                await refused("an adviser does not set it", adviser.preview_change("set_health_cover_figure", good), "branch leader")
                await refused("it needs its source", leader.preview_change("set_health_cover_figure", {**good, "source_document": ""}), "source")
                await refused("and a figure", leader.preview_change("set_health_cover_figure", {**good, "family_cover": 0}), "above zero")
                await refused("a US firm has none", ToolExecutors(s, us_leader.id, R.BRANCH_LEADER, us.id).preview_change(
                    "set_health_cover_figure", good), "Indian")
                card = labels(await leader.preview_change("set_health_cover_figure", good))
                check("the card says parents read not assessed", "not assessed" in card["Cover for a parent"], True)
                check("showing it set nothing", (firm.settings.get("protection") or {}).get("health"), None)
                await leader.execute("set_health_cover_figure", good)
                check("the care cost beside it is kept", firm.settings["protection"]["care"]["annual_cost"], 600000)
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                a, k = member(v, "Arjun Mehta"), member(v, "Kamla Mehta")
                check("the family gap is measured on own cover, the employer's beside it",
                      (a["need"]["status"], a["need"]["gap"], a["need"]["gap_with_employer"]), ("gap", 2000000.0, 1500000.0))
                check("a parent with no parent figure is not assessed", (k["need"]["status"], k["need"]["missing"]),
                      ("not_assessed", ["no_firm_figure_for_parents"]))
                await leader.execute("set_health_cover_figure", {**good, "parent_cover": 1_000_000})
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                check("and measured once stated", member(v, "Kamla Mehta")["need"]["gap"], 500000.0)

            async def top_ups():
                await adviser.execute("record_health_policy", schedule(
                    kind="super_top_up", provider="Top Insurer", sum_insured=7_500_000, deductible=2_500_000,
                    members="Arjun Mehta: self; Priya Mehta: spouse"))
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                a = member(v, "Arjun Mehta")
                check("a top-up with no base under it is not counted",
                      (a["own_cover"], a["top_ups_short"][0]["unpaid_below"], a["position"]), (0.0, 2500000.0, "employer_only"))
                await adviser.execute("record_health_policy", schedule(sum_insured=2_500_000, members="Arjun Mehta: self; Priya Mehta: spouse"))
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                a, isha = member(v, "Arjun Mehta"), member(v, "Isha Mehta")
                check("counted once base cover reaches its deductible",
                      (a["own_cover"], a["base_cover"], a["top_up_cover"], a["need"]["status"], a["position"]),
                      (10000000.0, 2500000.0, 7500000.0, "no_gap", "own"))
                check("a person the floater does not name is not covered by it", (isha["position"], isha["need"]["gap"]),
                      ("employer_only", 2000000.0))
                check("the floater reads shared by two", [p["shared_by"] for p in a["policies"] if p["kind"] == "family_floater"], [2])

            async def renewal_and_end():
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                senior = next(p for p in v["policies"] if p["provider"] == "Senior Insurer")
                from app.aurea_core import health_cover
                later = await health_cover.for_household(s, mehta.id, firm_id=firm.id, today=today + timedelta(days=21))
                k = member(later, "Kamla Mehta")
                check("past its renewal date with no later schedule it is not counted",
                      (k["own_cover"], k["position"], k["renewal_passed"], later["summary"]["renewals_passed"]), (0.0, "none_on_file", 1, 1))
                renew = schedule(kind="individual", provider="Senior Insurer", sum_insured=700_000, members="Kamla Mehta: mother",
                                 renewal_on=(today + timedelta(days=385)).isoformat(), replaces_id=senior["id"],
                                 source_document="Renewal schedule, 2027")
                check("the card names what it replaces", "which ends" in labels(await adviser.preview_change("record_health_policy", renew))["Replaces"], True)
                await adviser.execute("record_health_policy", renew)
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                check("the renewal counts and the old schedule has ended",
                      (member(v, "Kamla Mehta")["own_cover"], next(p for p in v["policies"] if p["id"] == senior["id"])["status"]),
                      (700000.0, "ended"))
                top = next(p for p in v["policies"] if p["kind"] == "super_top_up")
                await refused("ending needs a reason", adviser.preview_change("end_health_policy", {"policy_id": top["id"], "reason": ""}), "why")
                await refused("another firm cannot end it", outsider.preview_change(
                    "end_health_policy", {"policy_id": top["id"], "reason": "Lapsed"}), "not found")
                check("the card names it", labels(await adviser.preview_change(
                    "end_health_policy", {"policy_id": top["id"], "reason": "Lapsed"}))["Insurer"], "Top Insurer")
                await adviser.execute("end_health_policy", {"policy_id": top["id"], "reason": "Lapsed"})
                await refused("it ends once", adviser.preview_change("end_health_policy", {"policy_id": top["id"], "reason": "Lapsed"}), "already")
                v = await adviser.execute("read_health_cover", {"household_id": str(mehta.id)})
                check("it no longer counts", member(v, "Arjun Mehta")["own_cover"], 2500000.0)

            async def apart_from_the_gaps():
                from app.aurea_core import pre_advice
                cover = await protection.for_household(s, mehta.id, firm_id=firm.id)
                a = next(p for p in cover["people"] if p["name"] == "Arjun Mehta")
                check("health cover is in no death or disability figure",
                      (a["death"]["cover"], a["death"]["group_cover"], a["disability"]["cover"], len(cover["policies"])), (5000000.0, 0, 0.0, 1))
                check("it counts as insurance outside SEBI's purview", await pre_advice._non_sebi(s, mehta.id), ["4 insurance policies"])

            meera = {}

            def statement(**kw):
                return {"product": "endowment", "statement_date": (today - timedelta(days=10)).isoformat(), "premiums_paid": 288_000,
                        "surrender_value": 205_000, "paid_up_value": 300_000, "annual_premium": 24_000, "premium_years_left": 8,
                        "maturity_on": (today - timedelta(days=10) + timedelta(days=2922)).isoformat(), "maturity_value": 530_000,
                        "maturity_basis": "guaranteed", "person_id": str(priya.id), "provider": "Life Insurer", "sum_assured": 500_000,
                        "source_document": "Insurer's statement of values, Nov 2026", **kw}

            async def values():
                p = lambda **kw: adviser.preview_change("record_policy_values", statement(**kw))  # noqa: E731
                policies_before = (await s.execute(select(func.count()).select_from(ProtectionPolicy))).scalar_one()
                card = labels(await p())
                check("the card shows each figure and that life cover is recorded too",
                      all(k in card for k in ("Premiums paid", "Surrender value", "Paid-up value", "Maturity benefit", "Also records", "Document")), True)
                check("and says nothing is surrendered or recommended", "nothing is surrendered" in card["Note"], True)
                check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(ProtectionPolicy))).scalar_one(), policies_before)
                await refused("at least one value", p(surrender_value=None, paid_up_value=None), "at least one")
                await refused("premiums paid", p(premiums_paid=None), "premiums paid")
                await refused("a maturity benefit says guaranteed or illustrated", p(maturity_basis=None), "guaranteed or illustrated")
                await refused("an endowment has no fund value", p(fund_value=1), "fund value")
                await refused("a future statement", p(statement_date=(today + timedelta(days=1)).isoformat()), "future")
                await refused("a document", p(source_document=""), "statement")
                await refused("a product it knows", p(product="pension plan"), "product")
                await refused("compliance does not record it", ex[R.COMPLIANCE].preview_change("record_policy_values", statement()), "adviser")
                await refused("another firm's person", outsider.preview_change("record_policy_values", statement()), "not in this firm")
                await refused("a US household", american.preview_change("record_policy_values", statement(person_id=str(smith.id))), "Indian")

                done = await adviser.execute("record_policy_values", statement())
                meera.update(done)
                cover = await protection.for_household(s, mehta.id, firm_id=firm.id)
                pm = next(x for x in cover["people"] if x["name"] == "Priya Mehta")
                check("its life cover is on file and counted in the death gap", pm["death"]["cover"], 500000.0)
                await refused("the same policy is not recorded again", p(), "already on file")
                v = await adviser.execute("read_policy_values", {"household_id": str(mehta.id)})
                row = v["policies"][0]
                opt = {o["key"]: o for o in row["options"]}
                check("surrender: the cash now, nothing more to pay, no cover, and against premiums paid",
                      [opt["surrender"][k] for k in ("cash_now", "premiums_still_to_pay", "life_cover", "against_premiums_paid")],
                      [205000.0, 0.0, 0.0, -83000.0])
                check("paid-up: no cash, nothing more, the stated value, and cover not on the statement",
                      [opt["paid_up"][k] for k in ("cash_now", "premiums_still_to_pay", "stated_value", "life_cover")], [0.0, 0.0, 300000.0, None])
                check("continue: what is still to pay, the benefit and the cover",
                      [opt["continue"][k] for k in ("premiums_still_to_pay", "stated_value", "life_cover")], [192000.0, 530000.0, 500000.0])
                rate = row["continuing_return"]["rate"]
                years = 2922 / 365.25
                grown = 205000 * (1 + rate) ** years + sum(24000 * (1 + rate) ** (years - n) for n in range(8))
                check("the implied return grows the figures to the benefit", abs(grown - 530000) < 600, True)
                check("under five per cent", 0.04 < rate < 0.05, True)
                check("no recommendation, and tax named as not modelled",
                      (v["recommends"], "tax_on_surrender_or_maturity" in v["not_modelled"]), ("nothing", True))
                check("the answer recommends none", "not a recommendation" in v["message"], True)
                check("an endowment has no lock-in", row["lock_in"], None)

                older = statement(policy_id=done["policy_id"], statement_date=(today - timedelta(days=400)).isoformat(),
                                  surrender_value=150_000, maturity_value=None)
                check("an older statement's card says it will not stand", "later statement" in labels(
                    await adviser.preview_change("record_policy_values", older))["Stands"], True)
                await adviser.execute("record_policy_values", older)
                v = await adviser.execute("read_policy_values", {"household_id": str(mehta.id)})
                check("the latest statement stands, the older kept",
                      (v["policies"][0]["statement"]["surrender_value"], v["policies"][0]["earlier_statements"]), (205000.0, 1))

                ulip = await adviser.execute("record_policy_values", statement(
                    product="unit_linked", person_id=str(arjun.id), provider="Unit Insurer", sum_assured=1_000_000,
                    surrender_value=None, paid_up_value=None, fund_value=415_000, maturity_value=None, maturity_basis=None,
                    commenced_on=(today - timedelta(days=3 * 365)).isoformat()))
                opt = {o["key"]: o for o in ulip["options"]}
                check("a missing figure reads not on file",
                      (opt["surrender"]["missing"], opt["paid_up"]["missing"], opt["continue"]["missing"], ulip["continuing_return"]["rate"]),
                      (["surrender_value"], ["paid_up_value"], ["maturity_value"], None))
                check("a unit-linked policy shows its lock-in, an assumed rule with its source",
                      (ulip["lock_in"]["status"], ulip["lock_in"]["assumed"], ulip["law"][0]["key"]), ("locked", True, "in_ulip_lock_in_years"))
                await refused("a statement cannot sit on a rider or another firm's policy", outsider.preview_change(
                    "record_policy_values", statement(policy_id=done["policy_id"], person_id=None)), "not found")
                await adviser.execute("end_protection_policy", {"policy_id": ulip["policy_id"], "reason": "Surrendered"})
                v = await adviser.execute("read_policy_values", {"household_id": str(mehta.id)})
                gone = next(p for p in v["policies"] if p["policy_id"] == ulip["policy_id"])
                check("an ended policy is listed with no options", (gone["status"], gone["options"], v["summary"]["policies"]), ("ended", [], 1))

            async def pure_return():
                from app.aurea_core.policy_values import implied_return
                check("a sum that doubles in ten years", implied_return(surrender=100.0, premium=0.0, years_left=0, maturity=200.0, years=10), 0.0718)
                check("premiums past maturity give no rate", implied_return(surrender=100, premium=10, years_left=12, maturity=300, years=10), None)
                check("nothing paid in gives no rate", implied_return(surrender=0, premium=0, years_left=0, maturity=100, years=5), None)

            async def riders():
                base = {"person_id": str(arjun.id), "kind": "waiver_of_premium", "provider": "Term insurer",
                        "source_document": "Rider schedule 2024", "terms_stated": "Premiums waived on total and permanent disability"}
                await refused("a waiver is a rider", adviser.preview_change("record_protection_policy", base), "rider")
                await refused("with no sum of its own", adviser.preview_change(
                    "record_protection_policy", {**base, "rider_of": str(term.id), "sum_insured": 100}), "no sum insured")
                card = labels(await adviser.preview_change("record_protection_policy", {**base, "rider_of": str(term.id)}))
                check("the card names the cover, its base and its terms, and that it is not counted",
                      (card["Cover"], "Term insurer" in card["Rider of"], "waived" in card["Terms stated"], card["Counted"].startswith("No")),
                      ("Waiver of premium", True, True, True))
                await adviser.execute("record_protection_policy", {**base, "rider_of": str(term.id)})
                await adviser.execute("record_protection_policy", {
                    "person_id": str(arjun.id), "kind": "critical_illness", "provider": "Term insurer", "sum_insured": 2_000_000,
                    "rider_of": str(term.id), "source_document": "Rider schedule 2024", "terms_stated": "36 listed illnesses; 30-day survival"})
                cover = await protection.for_household(s, mehta.id, firm_id=firm.id)
                parent = next(r for r in cover["policies"] if r["id"] == str(term.id))
                check("both sit under the base policy with their terms, not counted",
                      sorted((r["kind"], r["counted"], bool(r["terms_stated"])) for r in parent["riders"]),
                      [("critical_illness", False, True), ("waiver_of_premium", False, True)])

            async def firm_and_tenant():
                book = await adviser.execute("read_health_cover", {})
                check("the book lists the household", [h["household"] for h in book["households"]], ["The Mehta Family"])
                empty = await outsider.execute("read_health_cover", {})
                check("another firm sees none of it", (empty["households"], (await outsider.execute("read_policy_values", {}))["policies"]), ([], []))
                await refused("nor one household by id", outsider.execute("read_health_cover", {"household_id": str(mehta.id)}), "not found")
                await refused("nor its policy values", outsider.execute("read_policy_values", {"household_id": str(mehta.id)}), "not found")
                abroad = await american.execute("read_health_cover", {"household_id": str(us_home.id)})
                check("a US household reads not applicable", abroad["applies"], False)
                check("and a US firm's book is not offered", (await american.execute("read_health_cover", {}))["applies"], False)

            for title, fn in (("The tools", tools), ("Recording a health policy", recording), ("Who is covered by what", view_before_figure),
                              ("The firm's figure", figure), ("Super top-ups and floaters", top_ups), ("Renewal and ending", renewal_and_end),
                              ("Apart from the death and disability gaps", apart_from_the_gaps), ("Surrender, paid-up, continue", values),
                              ("The implied return", pure_return), ("Riders as recorded", riders), ("Firm scope and market", firm_and_tenant)):
                await section(title, fn)
    finally:
        await db.dispose()

    async def demonstration():
        from seed import india_demo, india_protection

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as s:
                from app.aurea_core import health_cover, policy_values
                from app.models.health_cover import HealthPolicy, PolicyValueStatement

                firm = await india_demo.build(s)
                await s.flush()
                homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
                views = {n: await health_cover.for_household(s, h.id, firm_id=firm.id) for n, h in homes.items()}
                positions = {m["name"]: m["position"] for v in views.values() for m in v["members"]}
                check("the Sharmas' own cover is only the employer's, a parent's is hers",
                      [positions[n] for n in ("Rajesh Sharma", "Meera Sharma", "Aarav Sharma", "Kamla Sharma")],
                      ["employer_only", "employer_only", "employer_only", "own"])
                check("the Iyers are covered, one holds a top-up only and one nothing",
                      [positions[n] for n in ("Karthik Iyer", "Nandini Kapoor", "Elaine D'Souza")], ["own", "top_up_only", "none_on_file"])
                iyer = next(m for m in views["The Iyer Family"]["members"] if m["name"] == "Karthik Iyer")
                check("floater plus top-up reach the stated figure", (iyer["own_cover"], iyer["need"]["status"]), (10000000.0, "no_gap"))
                check("the households that stand for a missing paper are given nothing",
                      [positions[n] for n in ("Subir Banerjee", "Elaine D'Souza")], ["none_on_file", "none_on_file"])
                check("the figure carries its source, said to be invented",
                      "invented" in health_cover.cover_figure(firm)["source"], True)
                worth = await policy_values.for_firm(s, firm.id)
                check("an endowment and a unit-linked policy, the second locked in",
                      sorted((p["product"], (p["lock_in"] or {}).get("status")) for p in worth["policies"]),
                      [("endowment", None), ("unit_linked", "locked")])
                endowment = next(p for p in worth["policies"] if p["product"] == "endowment")
                check("the endowment implies under five per cent", 0.03 < endowment["continuing_return"]["rate"] < 0.05, True)
                karthik = await protection.for_household(s, homes["The Iyer Family"].id, firm_id=firm.id)
                check("a waiver-of-premium rider is on file", any(p["kind"] == "waiver_of_premium" and p["rider_of"] for p in karthik["policies"]), True)
                count = lambda m: s.execute(select(func.count()).select_from(m))  # noqa: E731
                before = [(await count(m)).scalar_one() for m in (HealthPolicy, PolicyValueStatement, ProtectionPolicy)]
                check("a second run adds nothing", await india_protection.add(s, firm), 0)
                check("and writes no row", [(await count(m)).scalar_one() for m in (HealthPolicy, PolicyValueStatement, ProtectionPolicy)], before)
                soon = (await s.execute(select(HealthPolicy).where(HealthPolicy.policy_number == "DEMO-SNR-0192"))).scalar_one()
                soon.renewal_on = firm_time.today(firm) - timedelta(days=3)
                await s.flush()
                moved = await india_protection.add(s, firm)
                check("a passed renewal date of the demonstration's is moved ahead", (moved, soon.renewal_on > firm_time.today(firm)), (1, True))
                limits = {"provider": 120, "policy_number": 64, "source_document": 255, "recorded_by": 200, "room_rent_cap": 200}
                long = [(p.policy_number, k) for p in (await s.execute(select(HealthPolicy))).scalars().all()
                        for k, n in limits.items() if len(getattr(p, k) or "") > n]
                policies = (await s.execute(select(ProtectionPolicy))).scalars().all()
                long += [(p.policy_number, "protection") for p in policies
                         if len(p.recorded_by) > 120 or len(p.provider) > 120 or len(p.policy_number or "") > 64]
                check("every seeded string fits its column", long, [])
        finally:
            await engine.dispose()

    await section("The demonstration", demonstration)

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
