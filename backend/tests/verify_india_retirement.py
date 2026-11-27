"""Prove the Indian retirement stack inside the projection (B-395, L200-2 India section 4).

  1. Every rule it reads is a law_figures row, assumed and unreviewed, in counsel's tracker.
  2. A person records each monthly contribution (an employer's NPS among them), a quoted annuity rate and a Unified
     Pension Scheme membership from its document, behind a Confirm card; nothing is assumed.
  3. The engine: an account joins the portfolio when it can be drawn, an annuity pays part of the draw, and a plan with no
     stack is worked exactly as before.
  4. EPF, PPF, NPS, SCSS and the EPS pension enter the likelihood and the funded ratio, shown apart, with and without,
     and never inside managed totals. A household with none, or outside India, is as it was.
  5. NPS at exit: the shares and their tax rows; the annuity income only at a stated rate, else not assessed.
  6. The income floor against needs, the liquid defence and the drawing year by year, or not assessed.
  7. A client reads the answers and one total; firm scope and roles hold; the plan of record and its summary say it.
  8. The demonstration's rows, the card, the pane and the page.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_retirement
"""
from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
WRITES = ("record_retirement_contribution", "record_annuity_rate", "record_ups_membership", "end_retirement_record")
ALL_TOOLS = ("read_retirement_stack", *WRITES)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def section(title: str, fn) -> None:
    """One part's checks. A part that raises is one failure, and the rest still run."""
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
        ok = contains.lower() in str(e).lower()
        check(label if ok else f"{label} [{e}]", ok, True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            await run(s)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  failed: {f}")
    return 1 if FAIL else 0


async def run(s) -> None:
    from app.aurea_core import balance_sheet, counsel_review, firm_time, law_figures, retirement, retirement_stack
    from app.aurea_core.graph import household_brain
    from seed import india_demo

    firm = await india_demo.build(s, quotes=None)
    await s.commit()
    today = firm_time.today(firm)
    stated = (today - timedelta(days=10)).isoformat()
    users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    sharma, iyer, banerjee, dsouza = (homes[n] for n in ("The Sharma Family", "The Iyer Family", "The Banerjee Household", "The D'Souza Household"))
    adv = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
    other_firm = Firm(name="Other", slug="other-stack", jurisdiction="IN", base_currency="INR", settings={})
    s.add(other_firm)
    await s.flush()
    outsider = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, other_firm.id)
    rajesh, meera, karthik, lakshmi = (people[n] for n in ("Rajesh Sharma", "Meera Sharma", "Karthik Iyer", "Lakshmi Iyer"))

    async def count(model, *where):
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    async def rows_of(tool, inputs):
        return {r["label"]: r["value"] for r in (await adv.preview_change(tool, inputs))["rows"]}

    async def read(home, **inputs):
        return await adv.read_retirement_stack({"household_id": str(home.id), **inputs})

    async def figures():
        keys = ("in_nps_rule", "in_nps_exit_age", "in_nps_lump_share", "in_nps_annuity_share", "in_nps_lump_tax_rule",
                "in_nps_annuity_tax_rule", "in_ppf_annual_cap", "in_ups_rule")
        check("each rule is a law figure", [k in law_figures.VERSIONS for k in keys], [True] * len(keys))
        new = keys[1:]
        check("each new row is marked assumed", [law_figures.get(k).source.startswith("ASSUMED") for k in new], [True] * len(new))
        check("the exit shares add to the whole", law_figures.value("in_nps_lump_share") + law_figures.value("in_nps_annuity_share"), 1.0)
        check("counsel's tracker lists them", set(keys) <= set(counsel_review.india_keys()), True)
        v = await read(sharma)
        check("and the layout shows each unreviewed", {f["counsel"]["state"] for f in v["figures"]}, {"not_reviewed"})

    await section("1. The rules", figures)

    async def records():
        from app.models.retirement_stack import RetirementRecord

        base = {"person_id": str(lakshmi.id), "scheme": "nps", "payer": "employer", "monthly_amount": 15000, "statement_date": stated,
                "document": "Payslip for the month, filed"}
        before = await count(RetirementRecord)
        card = await rows_of("record_retirement_contribution", base)
        check("the card says who, into what, from whom and how much",
              (card["Person"], card["Into"], card["Paid by"], "15,000" in card["A month"]), ("Lakshmi Iyer", "NPS", "The employer's", True))
        check("and writes nothing", await count(RetirementRecord), before)
        done = await adv.record_retirement_contribution(base)
        check("recorded", await count(RetirementRecord), before + 1)
        check("and on the ledger", await count(LedgerEntry, LedgerEntry.event_type == "retirement_record_added",
                                               LedgerEntry.actor.notlike("seed%")), 1)
        check("the employer's NPS contribution reads back for the tax comparison",
              [(r["person_id"], r["yearly"]) for r in await retirement_stack.employer_nps(s, firm.id, iyer.id) if r["person_id"] == str(lakshmi.id)],
              [(str(lakshmi.id), 180000.0)])
        card = await rows_of("record_retirement_contribution", {**base, "monthly_amount": 16000})
        check("a later statement says what it replaces", "15,000" in card["Replaces"], True)
        done = await adv.record_retirement_contribution({**base, "monthly_amount": 16000})
        check("one stands", await count(RetirementRecord, RetirementRecord.person_id == lakshmi.id, RetirementRecord.status == "current"), 1)
        check("the earlier stays on file", await count(RetirementRecord, RetirementRecord.person_id == lakshmi.id), 2)
        await refused("an older statement never stands over a newer", adv.preview_change(
            "record_retirement_contribution", {**base, "statement_date": (today - timedelta(days=90)).isoformat()}), "newer")
        for label, change, words in (
            ("no amount", {"monthly_amount": None}, "monthly amount"), ("nothing a month", {"monthly_amount": 0}, "above zero"),
            ("no document", {"document": ""}, "which"), ("a day to come", {"statement_date": (today + timedelta(days=3)).isoformat()}, "future"),
            ("no payer said", {"payer": None}, "who pays"), ("an SCSS contribution", {"scheme": "scss"}, "single deposit"),
            ("an employer's PPF", {"scheme": "ppf", "payer": "employer"}, "who pays"),
            ("PPF above its yearly limit", {"scheme": "ppf", "payer": "own", "monthly_amount": 20000}, "yearly limit"),
            ("an Aadhaar number", {"document": "Passbook of 4321 8765 2109"}, "aadhaar"),
        ):
            await refused(label, adv.preview_change("record_retirement_contribution", {**base, **change}), words)
        await refused("another firm's person", outsider.preview_change("record_retirement_contribution", base), "not found")
        ops = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.COMPLIANCE, firm.id)
        await refused("compliance does not record it", ops.preview_change("record_retirement_contribution", base), "adviser")
        held = [t for t in ALL_TOOLS if TOOLS[t].change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION]
        check("every write pauses on a card", held, list(WRITES))
        check("a client holds none of them", set(tools_for_role(UserRole.CLIENT)) & set(ALL_TOOLS), set())
        mine = [r for r in done["records"] if r["person_id"] == str(lakshmi.id)]
        end = {"record_id": mine[0]["id"] if mine else None, "reason": "Left that employer"}
        check("the write answers with the layout", (done["household_id"], len(mine)), (str(iyer.id), 1))
        card = await rows_of("end_retirement_record", end)
        check("the end card names it", "16,000" in card["Ending"], True)
        await refused("no reason", adv.preview_change("end_retirement_record", {**end, "reason": ""}), "say why")
        await adv.end_retirement_record(end)
        check("ended, and it stops counting",
              [r for r in await retirement_stack.employer_nps(s, firm.id, iyer.id) if r["person_id"] == str(lakshmi.id)], [])
        await refused("one already ended", adv.preview_change("end_retirement_record", end), "already ended")

    await section("2. Recording", records)

    async def engine():
        plain = dict(current_value=1_000_000, allocation={"equity": 600_000, "fixed_income": 400_000}, current_age=50, retirement_age=60,
                     longevity_age=80, annual_income=120_000, annual_contribution=50_000, sims=600)
        a, b = retirement.retirement_plan(**plain), retirement.retirement_plan(**plain, sleeves=[])
        check("no stack, the same plan", a, b)
        sleeve = {"value": 500_000, "mu": 0.07, "sigma": 0.06, "crash": 0.0, "add": [0.0] * 90, "unlock_after": 0, "capital": 1.0,
                  "annuity_per_unit": 0.0}
        c = retirement.retirement_plan(**plain, sleeves=[sleeve])
        check("an account that joins adds to the pot", c["projected_at_retirement"] > a["projected_at_retirement"], True)
        check("and to success", c["success_probability"] >= a["success_probability"], True)
        saved = retirement.retirement_plan(**plain, sleeves=[{**sleeve, "add": [60_000.0] * 90}])
        check("what is saved into it counts", saved["projected_at_retirement"] > c["projected_at_retirement"], True)
        broke = dict(current_value=0, allocation={}, current_age=60, retirement_age=60, longevity_age=66, annual_income=100_000, sims=300,
                     inflation=0.0)
        late = retirement.retirement_plan(**broke, sleeves=[{**sleeve, "value": 5_000_000, "unlock_after": 2}])
        check("money locked until later does not pay the years before it", (late["success_probability"], late["median_depletion_age"]), (0.0, 61))
        check("and joins in its year", [p["released"] > 0 for p in late["stack_run"]["path"][:3]], [False, False, True])
        paid = retirement.retirement_plan(**broke, sleeves=[{**sleeve, "value": 2_000_000, "mu": 0.0, "sigma": 0.0001, "capital": 0.0,
                                                             "annuity_per_unit": 0.06}])
        check("an annuity that covers the draw pays every year with an empty pot", paid["success_probability"], 1.0)
        none = retirement.retirement_plan(**broke, sleeves=[{**sleeve, "value": 2_000_000, "capital": 0.0}])
        check("a share with no rate is neither income nor capital", none["success_probability"], 0.0)
        terms = {"upper_band": 0.2, "lower_band": 0.2, "cut": 0.1, "raise": 0.1, "skip_inflation": True, "start_rate": None}
        g = retirement.retirement_plan(**plain, sleeves=[sleeve], guardrails=terms)
        check("the guardrail run counts it too", g["guardrails"]["fixed_success"], g["success_probability"])

    await section("3. The engine", engine)

    async def projection():
        brain = await household_brain(s, sharma.id, firm_id=firm.id)
        managed = brain["totals"]["total_value"]
        plan = await retirement.for_household(s, sharma.id, firm_id=firm.id)
        st = plan["stack"]
        check("the accounts enter as named inputs",
              sorted({(i["scheme"], i["type"]) for i in st["items"]}),
              [("epf", "contribution"), ("epf", "corpus"), ("eps", "pension"), ("nps", "contribution"), ("nps", "corpus"),
               ("ppf", "contribution"), ("ppf", "corpus")])
        check("each with its source", all(i.get("document") for i in st["items"]), True)
        check("the contributions are the recorded ones", st["monthly_contributions"], 25000 + 23750 + 12500 + 20000 + 5000 + 5000)
        check("with beats without", st["with"]["success_probability"] >= st["without"]["success_probability"]
              and st["with"]["projected_at_retirement"] > st["without"]["projected_at_retirement"], True)
        check("the plan's own figure is the one with it", plan["success_probability"], st["with"]["success_probability"])
        check("the EPS pension is only in the one with it",
              st["with"]["guaranteed_at_retirement"] > st["without"]["guaranteed_at_retirement"], True)
        check("the managed total holds none of it", (brain["totals"]["total_value"], plan["current_value"]),
              (managed, round(managed * plan["funding_share"])))
        from app.aurea_core import funded, goal_tradeoff

        f = funded.ratio(brain, goal_tradeoff.likelihoods(brain, today), plan)
        check("the funded ratio adds it as a named input, assets staying the managed book",
              (f["assets"], f["stack_assets"], f["basis"]), (round(managed, 2), st["capital_counted"], "managed_book_and_retirement_stack"))
        check("and shows the ratio without it", (f["ratio"], f["ratio_without_stack"]),
              (round((managed + st["capital_counted"]) / f["pv_needs"], 3), round(managed / f["pv_needs_without_stack"], 3)))
        check("contributions are still not counted there", "contributions" in f["not_counted"], True)
        retirement_goal = next(g for g in f["by_goal"] if g["kind"] == "retirement")
        check("the retirement goal's own ratio counts it", retirement_goal.get("stack_assets"), st["capital_counted"])
        check("no other goal does", [g.get("stack_assets") for g in f["by_goal"] if g["kind"] != "retirement"],
              [None] * (len(f["by_goal"]) - 1))
        said = (await adv.read_financial_plan({"household_id": str(sharma.id)}))
        check("the plan carries it and Astra says both", ("stack" in said["retirement"], "without them it is" in said["message"],
                                                          "held elsewhere" in said["message"]), (True, True, True))

        none = await retirement.for_household(s, dsouza.id, firm_id=firm.id)
        check("a household with none of it has no stack", "stack" in none, False)
        empty = await read(dsouza)
        check("and says nothing is on file, not that there is none", (empty["status"], "does not mean" in empty["message"]), ("nothing_on_file", True))
        brain_d = await household_brain(s, dsouza.id, firm_id=firm.id)
        check("its funded ratio reads as before",
              "stack_assets" in funded.ratio(brain_d, goal_tradeoff.likelihoods(brain_d, today), none), False)

        # The answer without the stack is the plan as it would be with none of it read (Iyer holds no pension stream).
        whole = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        original = retirement_stack.inputs

        async def nothing(*a, **k):
            return None

        retirement_stack.inputs = nothing
        try:
            bare = await retirement.for_household(s, iyer.id, firm_id=firm.id)
        finally:
            retirement_stack.inputs = original
        check("without the stack is exactly the plan with none of it",
              {k: whole["stack"]["without"][k] for k in ("success_probability", "projected_at_retirement", "sustainable_income")},
              {k: bare[k] for k in ("success_probability", "projected_at_retirement", "sustainable_income")})
        us = Firm(name="US", slug="us-stack", jurisdiction="US", base_currency="USD", settings={})
        s.add(us)
        await s.flush()
        check("a firm outside India reads no stack", await retirement_stack.inputs(s, us, brain), None)

    await section("4. Inside the likelihood and the funded ratio", projection)

    async def exit_rules():
        v = await read(sharma)
        rows = {n["person"]: n for n in v["nps_exit"]}
        m, r = rows["Meera Sharma"], rows["Rajesh Sharma"]
        check("today's corpus split by the rule", (m["lump_today"], m["annuity_today"]), (round(m["corpus_today"] * 0.6, 2), round(m["corpus_today"] * 0.4, 2)))
        check("at her quoted rate the annuity is worked",
              (m["annuity_rate"], m["annuity_rate_basis"], m["annuity_a_year"]),
              (6.2, "quote", round(next(a["at_release_median"] for a in v["stack"]["accounts"] if a["label"] == m["label"]) * 0.4 * 0.062)))
        check("his with no rate is not assessed", (r["annuity_rate"], r["annuity_a_year"], r["annuity_not_assessed"]),
              (None, None, retirement_stack.NOT_ASSESSED_ANNUITY))
        check("at exit, when she is 60", m["exit_age"], 60)
        what_if = await read(sharma, annuity_rate_percent=7)
        again = {n["person"]: n for n in what_if["nps_exit"]}
        check("a stated rate is a what-if for each", (again["Rajesh Sharma"]["annuity_rate_basis"], again["Rajesh Sharma"]["annuity_a_year"] > 0,
                                                     what_if["what_if"]), ("what_if", True, {"annuity_rate": 7.0}))
        check("which changed nothing on file", (await read(sharma))["nps_exit"][0]["annuity_rate_basis"], v["nps_exit"][0]["annuity_rate_basis"])
        await refused("a rate that cannot be", read(sharma, annuity_rate_percent=45), "at most")
        stack = (await retirement.for_household(s, sharma.id, firm_id=firm.id))["stack"]
        counted = sum(a["value"] * (a["capital_share"] + (a["annuity_share"] if a["annuity_rate"] else 0)) for a in stack["accounts"])
        check("funded counts what the likelihood counts", stack["capital_counted"], round(counted, 2))
        card = await rows_of("record_annuity_rate", {"person_id": str(rajesh.id), "rate": 6, "quote_date": stated, "document": "Insurer's quote, filed"})
        check("a quote's card", ("6%" in card["Annuity rate quoted"], card["Replaces"]), (True, "Nothing on file"))
        await refused("a quote with no document", adv.preview_change("record_annuity_rate", {"person_id": str(rajesh.id), "rate": 6, "quote_date": stated}), "quote")

        # The Unified Pension Scheme is recorded and not worked out; the person's NPS leaves.
        join = {"person_id": str(karthik.id), "statement_date": stated, "document": "Option form, filed"}
        check("the membership card", (await rows_of("record_ups_membership", join))["Recording"], "A member of the Unified Pension Scheme")
        before = await read(iyer)
        done = await adv.record_ups_membership(join)
        check("his NPS leaves the projection, said",
              (any("Unified Pension Scheme" in (e["why"] or "") for e in done["stack"]["excluded"]) if done["status"] == "on_file" else None,
               [n["person"] for n in done.get("nps_exit", [])]), (True, []))
        check("having been in it", [n["person"] for n in before["nps_exit"]], ["Karthik Iyer"])
        await refused("recorded twice", adv.preview_change("record_ups_membership", join), "already")
        await adv.end_retirement_record({"record_id": done["record_id"], "reason": "Recorded in error"})

        dob = karthik.date_of_birth
        karthik.date_of_birth = None
        await s.flush()
        unknown = await read(iyer)
        check("with no date of birth his NPS is not assessed", any("date of birth" in e["why"] for e in unknown["stack"]["excluded"]), True)
        karthik.date_of_birth = dob
        await s.flush()

    await section("5. NPS at exit", exit_rules)

    async def laid_out():
        v = await read(sharma)
        fl = v["floor"]
        check("guaranteed income against needs", (fl["status"], fl["coverage"], fl["short_by"]),
              ("assessed", round(fl["guaranteed"] / fl["needs"], 3), round(max(fl["needs"] - fl["guaranteed"], 0), 2)))
        check("the sources add to the floor", round(sum(x["a_year"] for x in fl["sources"]), 2), fl["guaranteed"])
        check("needs are essential spending and debts that outlast work",
              fl["needs"], round(fl["essential_spending"] + fl["debt_payments_after_work"], 2))
        b = await read(banerjee)
        scss = next(x for x in b["floor"]["sources"] if x["kind"] == "scss_interest")
        check("SCSS interest is the statement's rate on its value", scss["stated"], round(3_000_000 * 0.082, 2))
        li = v["liquidity"]
        check("years of liquid money", (li["status"], li["years"]), ("assessed", round(li["liquid"] / li["yearly_need"], 2)))
        check("the parts add up", round(sum(p["amount"] for p in li["parts"]), 2), li["liquid"])
        w = v["withdrawals"]
        first = w["years"][0]
        check("the drawing opens on the pot at retirement", first["opening"], w["opening_pot"])
        check("each year closes where the next opens", all(abs(a["closing"] - b_["opening"]) <= 1 for a, b_ in zip(w["years"], w["years"][1:])), True)
        check("it names what is not modelled", len(w["not_modelled"]) >= 4, True)
        tried = await read(sharma, monthly_withdrawal=100000)
        check("a stated monthly amount, grown to retirement", (tried["withdrawals"]["basis"], tried["withdrawals"]["first_monthly_today"]),
              ("monthly_amount_stated", 100000))
        await refused("nothing a month", read(sharma, monthly_withdrawal=-1), "above zero")
        spending = [i for i in (await balance_sheet.current_items(s, sharma.id, firm_id=firm.id)) if i.kind == "essential_spending"]
        for i in spending:
            i.status = "ended"
        await s.flush()
        bare = await read(sharma)
        check("with no essential spending neither is assessed", (bare["floor"]["status"], bare["floor"]["coverage"], bare["liquidity"]["status"],
                                                                 bare["liquidity"]["years"]), ("not_assessed", None, "not_assessed", None))
        for i in spending:
            i.status = "current"
        await s.flush()

    await section("6. Floor, liquid defence and drawing", laid_out)

    async def scope():
        await refused("another firm's household", outsider.read_retirement_stack({"household_id": str(sharma.id)}), "not found")
        client = users[UserRole.CLIENT]
        mine = await ToolExecutors(s, client.id, UserRole.CLIENT, firm.id).read_my_retirement({})
        check("a client reads the answers with and without", {"with", "without"} <= set(mine["stack"]), True)
        check("and no holding, statement or record", sorted(set(mine["stack"]) & {"items", "accounts", "excluded", "assumed", "path"}), [])
        check("nor an institution anywhere", "pension fund manager" in str(mine["stack"]), False)
        from app.aurea_core import plan_record, plan_summary

        figs = await plan_record.figures_of(s, sharma.id, firm_id=firm.id)
        check("a baseline freezes the stack and the ratio without it", {"stack_assets", "ratio_without_stack"} <= set(figs["funded"]), True)
        record = {"version": 1, "baseline": {"happened_on": today.isoformat(), "figures": figs}, "review": None, "plan_of_record": None}
        text = " ".join(x["body"] for x in plan_summary.sections(record, "INR"))
        check("the summary names the accounts held elsewhere", "accounts held elsewhere" in text, True)
        sized = await adv.read_goal_sizing({"household_id": str(sharma.id)})
        check("goal sizing shows the same ratio and the one without", {"stack_assets", "ratio_without_stack"} <= set(sized["household_funded"]), True)

    await section("7. Clients, scope and the plan of record", scope)

    async def demo():
        from app.models.balance_sheet import BalanceItem
        from app.models.held_away import HeldAwayHolding
        from app.models.retirement_stack import RetirementRecord
        from seed import india_retirement

        check("a second run adds nothing", await india_retirement.add(s, firm), 0)
        names = (await s.execute(select(BalanceItem.name, BalanceItem.status).where(BalanceItem.firm_id == firm.id, BalanceItem.kind == "pension"))).all()
        check("the pension totals the lines stand for have ended", sorted({st for _, st in names}), ["ended"])
        check("so each is counted once",
              await count(HeldAwayHolding, HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.status == "current",
                          HeldAwayHolding.person_id == rajesh.id, HeldAwayHolding.value == 1_850_000, HeldAwayHolding.institution == india_retirement.EPFO), 1)
        check("an employer's NPS contribution is on file",
              sorted(r["monthly"] for r in await retirement_stack.employer_nps(s, firm.id, sharma.id)), [20000.0])
        check("every record says it is invented",
              all("invented" in r.source_document for r in (await s.execute(select(RetirementRecord).where(
                  RetirementRecord.firm_id == firm.id, RetirementRecord.recorded_by == india_retirement.BY))).scalars().all()), True)
        text = (BACKEND / "seed" / "india_demo_backfill.py").read_text(encoding="utf-8")
        check("the backfill runs it", "india_retirement.add(s, firm)" in text, True)

    await section("8. The demonstration's rows", demo)

    async def screens():
        pane = (FRONTEND / "lib" / "paneViews.ts").read_text(encoding="utf-8")
        renderer = (FRONTEND / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
        check("each tool has its card and opens the plan beside the conversation",
              [(f'case "{k}":' in pane, f'case "{k}":' in renderer) for k in ALL_TOOLS], [(True, True)] * len(ALL_TOOLS))
        view = (FRONTEND / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
        check("the plan page carries the stack", "/api/plan/retirement-stack" in view and "RetirementStackBody" in view, True)
        from app.api import financial_plan as api

        check("its route", "/api/plan/retirement-stack" in [r.path for r in api.router.routes], True)
        from app.tools_live import india_live_proof

        check("the live proof reads it", "read_retirement_stack" in india_live_proof.INDIA_READ_TOOLS, True)

    await section("9. Card, pane and page", screens)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
