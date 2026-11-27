"""Prove India's tax planning in the plan (B-396, L200-2 India sections 3 and 11).

  1. Every new rule is a law_figures row, assumed and unreviewed, in counsel's tracker.
  2. The regime comparison deducts the employer's recorded NPS contribution, held to each regime's share of basic salary
     where that is on file and shown with and without it where it is not; it states the switching limit only where business
     income is on file. Basic salary and business income are recorded behind a Confirm card.
  3. After tax: deposits, debt, arbitrage and equity funds, and growth against income plans, at each taxpayer's recorded
     slab, on returns a person recorded from a document; none on file is not assessed, and nothing is recommended.
  4. One year-end gain-harvest task per taxpayer and tax year, for the recorded adviser, from the engine's own figures.
  5. A person records that a regime or harvest review happened, added and never edited, with the firm's completion shares.
  6. Roles, firm scope, the demonstration rows, the card, the pane and the page.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_tax_planning
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.engagement import Task
from app.models.governance import LedgerEntry
from app.models.graph import Household, LegalEntity, Person
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"
READS = ("read_after_tax_comparison", "read_tax_reviews")
WRITES = ("record_stated_return", "record_tax_review", "raise_harvest_tasks")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def close(label: str, got, want, within: float = 0.011) -> None:
    ok = got is not None and abs(got - want) <= within
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {round(want, 2)!r}")


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
    from app.aurea_core import counsel_review, firm_time, law_figures, markets, rate_card
    from app.aurea_core import tax_india_full as full
    from seed import india_demo

    firm = await india_demo.build(s, quotes=None)
    await s.commit()
    today = firm_time.today(firm)
    year = markets.tax_year(markets.MARKETS["IN"], today)
    users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    sharma, iyer = homes["The Sharma Family"], homes["The Iyer Family"]
    rajesh, meera, karthik, lakshmi = (people[n] for n in ("Rajesh Sharma", "Meera Sharma", "Karthik Iyer", "Lakshmi Iyer"))
    huf = (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.name == "Rajesh Sharma HUF"))).scalar_one()

    def ex(role, f=firm):
        return ToolExecutors(s, users[role].id, role, f.id)

    adv, ops, comp = ex(UserRole.ADVISER), ex(UserRole.OPERATIONS), ex(UserRole.COMPLIANCE)
    other_firm = Firm(name="Other", slug="other-taxplan", jurisdiction="IN", base_currency="INR", settings={})
    s.add(other_firm)
    await s.flush()
    outsider = ex(UserRole.ADVISER, other_firm)

    async def count(model, *where):
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    async def rows_of(executor, tool, inputs):
        return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

    async def taxpayer(home, name):
        result = await adv.read_tax_position({"household_id": str(home.id)})
        return next(t for t in result["taxpayers"] if t["name"] == name)

    async def rules():
        new = ("in_80ccd2_new_cap", "in_80ccd2_old_cap", "in_employer_contribution_cap", "in_regime_business_switch_rule",
               "in_deposit_interest_rule", "in_specified_fund_rule", "in_idcw_rule", "in_arbitrage_equity_rule")
        check("each rule is a law figure", [k in law_figures.VERSIONS for k in new], [True] * len(new))
        check("each is marked assumed", [law_figures.get(k).source.startswith("ASSUMED") for k in new], [True] * len(new))
        check("counsel's tracker lists them", set(new) <= set(counsel_review.india_keys()), True)
        reviews = await counsel_review.standing(s, firm.id)
        check("and none is reviewed", [k for k in new if k in reviews], [])
        check("the Budget's 80CCD(2) share is on the February rate card", dict(rate_card.listed_keys(today)).get("in_80ccd2_new_cap"), "budget")

    await section("1. The rules", rules)

    async def regime_arithmetic():
        from app.aurea_core import india_regime as r

        none = r.nps_facts(yearly=None, basic=1_000_000)
        check("no contribution on file counts none", (none["state"], none["new"], none["old"]), ("none_on_file", 0.0, 0.0))
        unchecked = r.nps_facts(yearly=240_000, basic=None)
        check("without basic salary it is counted as recorded, limit not checked", (unchecked["state"], unchecked["new"], unchecked["old"]),
              ("cap_not_checked", 240_000, 240_000))
        capped = r.nps_facts(yearly=240_000, basic=2_000_000)
        check("with it each regime holds its share: 14% new, 10% old", (capped["state"], capped["new"], capped["old"]), ("capped", 240_000, 200_000))
        within = r.nps_facts(yearly=100_000, basic=2_000_000)
        check("a contribution inside both shares is whole", (within["state"], within["new"], within["old"]), ("within_limit", 100_000, 100_000))
        check("employer money above the yearly line is shown", r.nps_facts(yearly=600_000, epf_yearly=250_000, basic=None)["above_line"], 100_000.0)
        check("business income not on file: the switching limit is not assessed", r.switching(None)["state"], "not_assessed")
        check("business income on file: switching is limited", r.switching(500_000)["state"], "limited")
        check("recorded as none: chosen year by year", r.switching(0)["state"], "no_business_income")
        base = dict(income=3_000_000, salary=3_000_000, deductions=500_000, age=45, gains=0.0, is_individual=True)
        plain = full.regime_comparison(**base)
        with_nps = full.regime_comparison(**base, facts={"employer_nps": capped, "switching": r.switching(None)})
        check("the taxable income falls by what each regime allows",
              (plain["new"]["taxable_income"] - with_nps["new"]["taxable_income"], plain["old"]["taxable_income"] - with_nps["old"]["taxable_income"]),
              (240_000, 200_000))
        # 24 to 30 lakh slab in the new regime: 30% above 24 lakh, 25% from 20 to 24, with 4% cess.
        close("and the new regime's tax by the slab it came out of", plain["new"]["tax"] - with_nps["new"]["tax"], 240_000 * 0.30 * 1.04)
        check("a checked limit asks for nothing more about it", [n for n in with_nps["needs"] if "basic salary" in n], [])
        open_ = full.regime_comparison(**base, facts={"employer_nps": unchecked, "switching": r.switching(None)})
        check("unchecked, the answer without the contribution is beside it",
              (open_["without_employer_nps"]["new_tax"], open_["without_employer_nps"]["old_tax"]), (plain["new"]["tax"], plain["old"]["tax"]))
        check("and basic salary is asked for", any("basic salary" in n for n in open_["needs"]), True)
        flips = None
        for deductions in range(0, 1_600_000, 5_000):
            c = full.regime_comparison(income=3_000_000, salary=3_000_000, deductions=deductions, age=45, gains=0.0, is_individual=True,
                                       facts={"employer_nps": r.nps_facts(yearly=700_000, basic=None), "switching": r.switching(None)})
            if c["without_employer_nps"]["cheaper"] != c["cheaper"]:
                flips = c
                break
        check("where the limit would change the cheaper regime it does not pick",
              None if flips is None else (flips["conclusive"], flips["turns_on_nps_limit"]), (False, True))
        open_ded = full.regime_comparison(**{**base, "deductions": None}, facts={"employer_nps": capped, "switching": r.switching(None)})
        edge = full.regime_comparison(**{**base, "deductions": open_ded["break_even_deductions"] + 100},
                                      facts={"employer_nps": capped, "switching": r.switching(None)})
        check("the break-even deductions are worked with the contribution in", edge["cheaper"], "old")

    await section("2a. The comparison's arithmetic", regime_arithmetic)

    async def regime_on_file():
        regime = (await taxpayer(sharma, "Rajesh Sharma"))["engine"]["regime"]
        nps = regime["employer_nps"]
        check("Rajesh's recorded employer contribution is read, held to his basic salary",
              (nps["recorded_yearly"], nps["state"], regime["new"]["employer_nps_deduction"], regime["old"]["employer_nps_deduction"]),
              (240_000.0, "capped", 240_000.0, 200_000.0))
        check("with its statement named", bool(nps["document"]), True)
        check("his switching limit is not assessed: no business income figure", regime["switching"]["state"], "not_assessed")
        k = (await taxpayer(iyer, "Karthik Iyer"))["engine"]["regime"]
        check("Karthik's is counted with the limit not checked", (k["employer_nps"]["state"], "without_employer_nps" in k), ("cap_not_checked", True))
        check("Meera's papers show no business income", (await taxpayer(sharma, "Meera Sharma"))["engine"]["regime"]["switching"]["state"],
              "no_business_income")
        huf_block = await taxpayer(sharma, "Rajesh Sharma HUF")
        check("an HUF has no employer contribution or switching reading", "employer_nps" in huf_block["engine"]["regime"], False)
        inputs = {"person_id": str(lakshmi.id), "business_income": 3_000_000, "basic_salary": 400_000, "salary_income": 600_000,
                  "document": "Filed return for last year, on file"}
        before = dict((lakshmi.profile or {}).get("tax") or {})
        card = await rows_of(adv, "record_tax_inputs", inputs)
        check("the card names both new figures", {"Basic salary and dearness allowance a year", "Business or professional income a year"} <= set(card), True)
        check("and writes nothing", dict((lakshmi.profile or {}).get("tax") or {}), before)
        await refused("basic salary above the salary is refused", adv.preview_change("record_tax_inputs", {**inputs, "basic_salary": 700_000}), "part of the salary")
        await refused("business income above the income is refused", adv.preview_change("record_tax_inputs", {**inputs, "business_income": 9_000_000}), "above the annual income")
        await refused("operations records none of it", ops.record_tax_inputs(inputs), "adviser or a paraplanner")
        await adv.record_tax_inputs(inputs)
        after = (await taxpayer(iyer, "Karthik Iyer"))["engine"]
        check("recorded, it stands on file", (lakshmi.profile["tax"]["basic_salary"], lakshmi.profile["tax"]["business_income"]), (400_000.0, 3_000_000.0))
        check("and Karthik's reading is his own", after["on_file"]["business_income"], None)

    await section("2b. The comparison on file", regime_on_file)

    async def after_tax():
        from app.aurea_core import india_after_tax as a
        from app.models.india_tax_planning import StatedReturn

        r = await adv.read_after_tax_comparison({"household_id": str(sharma.id)})
        check("the demonstration's four stated returns, each with its document",
              [(x["state"], bool(x["document"])) for x in r["rates"]], [("on_file", True)] * 4)
        check("the amount and the period are named as assumed", (r["amount"], r["months"], r["amount_assumed"], r["months_assumed"]),
              (1_000_000, 36, True, True))
        mine = next(t for t in r["taxpayers"] if t["name"] == "Rajesh Sharma")
        check("Rajesh is read at his recorded slab and the surcharge of his income", (mine["state"], mine["slab_rate"], mine["surcharge_rate"]), ("assessed", 0.3, 0.1))
        by = {h["vehicle"]: h for h in mine["held"]}
        slab = 0.30 * 1.10 * 1.04
        close("deposit: interest taxed each year at slab", by["deposit"]["after_tax_value"], 1e6 * (1 + 0.07 * (1 - slab)) ** 3)
        gross = 1e6 * 1.074 ** 3
        close("debt fund: the whole gain at slab when sold", by["debt_fund"]["after_tax_value"], gross - (gross - 1e6) * slab)
        gross = 1e6 * 1.071 ** 3
        close("arbitrage: the long-term equity rate", by["arbitrage_fund"]["after_tax_value"], gross - (gross - 1e6) * 0.125 * 1.10 * 1.04)
        close("and beside it with the whole exemption unused", by["arbitrage_fund"]["if_exemption_unused"]["after_tax_value"],
              gross - max(gross - 1e6 - 125_000, 0) * 0.125 * 1.10 * 1.04)
        plans = {p["vehicle"]: p for p in mine["plans"]}
        check("growth against income for each fund with a return", sorted(plans), ["arbitrage_fund", "debt_fund", "equity_fund"])
        close("income plan: each year's payout at slab, put back", plans["equity_fund"]["income"]["after_tax_value"], 1e6 * (1 + 0.11 * (1 - slab)) ** 3)
        close("a debt fund's growth plan is the debt fund held", plans["debt_fund"]["growth"]["after_tax_value"], by["debt_fund"]["after_tax_value"])
        short = await adv.read_after_tax_comparison({"household_id": str(sharma.id), "months": 12, "amount": 500000})
        arb = next(h for t in short["taxpayers"] if t["name"] == "Rajesh Sharma" for h in t["held"] if h["vehicle"] == "arbitrage_fund")
        close("twelve months is short-term", arb["after_tax_value"], 500000 * 1.071 - 500000 * 0.071 * 0.20 * 1.10 * 1.04)
        k = next(t for t in (await adv.read_after_tax_comparison({"household_id": str(iyer.id)}))["taxpayers"] if t["name"] == "Karthik Iyer")
        kb = {h["vehicle"]: h for h in k["held"]}
        check("a higher surcharge is capped for equity gains and not for slab income",
              (k["surcharge_rate"], kb["arbitrage_fund"]["rate_borne"], kb["debt_fund"]["rate_borne"]),
              (0.25, round(0.125 * 1.15 * 1.04, 6), round(0.30 * 1.25 * 1.04, 6)))
        lk = next(t for t in (await adv.read_after_tax_comparison({"household_id": str(iyer.id)}))["taxpayers"] if t["name"] == "Lakshmi Iyer")
        check("no slab rate on file: not assessed, and it says what is needed", (lk["state"], "slab" in lk["needs"].lower()), ("not_assessed", True))
        words = str(r).lower()
        check("nothing ranks or picks", [w for w in ("'best'", "'cheaper'", "'recommended'", "'winner'") if w in words], [])
        check("what is left out and the rules travel with it", (bool(r["not_modelled"]), all(f.get("counsel") for f in r["figures"])), (True, True))
        none = await outsider.read_after_tax_comparison({})
        check("another firm has no stated return, and none is supplied", {x["state"] for x in none["rates"]}, {"not_on_file"})
        await refused("another firm reads no household here", outsider.read_after_tax_comparison({"household_id": str(sharma.id)}), "not found")
        await refused("a period that is not whole months is refused", adv.read_after_tax_comparison({"months": 0}), "months")

        new = {"vehicle": "deposit", "name": "Bank term deposit", "rate_percent": 6.5, "stated_on": today.isoformat(), "document": "Rate sheet on file"}
        before = await count(StatedReturn, StatedReturn.firm_id == firm.id)
        card = await rows_of(adv, "record_stated_return", new)
        check("the card names the return, its day and what it replaces",
              (card["Vehicle"], card["Return a year, before tax"], card["Replaces"].startswith("7%")), ("Deposit", "6.5%", True))
        check("and writes nothing", await count(StatedReturn, StatedReturn.firm_id == firm.id), before)
        await refused("no document, no return", adv.record_stated_return({**new, "document": ""}), "document")
        await refused("a return out of range is refused", adv.record_stated_return({**new, "rate_percent": 70}), "percentage")
        await refused("operations does not record one", ops.record_stated_return(new), "adviser")
        await refused("a document dated ahead is refused", adv.record_stated_return({**new, "stated_on": (today + timedelta(days=1)).isoformat()}), "ahead")
        done = await adv.record_stated_return(new)
        check("recorded, the later one stands and the earlier stays on file",
              (next(x["rate"] for x in done["rates"] if x["vehicle"] == "deposit"), await count(StatedReturn, StatedReturn.firm_id == firm.id)),
              (6.5, before + 1))
        await refused("the same return twice is refused", adv.record_stated_return(new), "already on file")
        await refused("an older one does not replace it", adv.record_stated_return({**new, "stated_on": (today - timedelta(days=60)).isoformat()}), "newer")
        check("it is on the ledger", await count(LedgerEntry, LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == "stated_return_recorded"), before + 1)
        check("the constants name what they assume", (a.DEFAULT_AMOUNT, a.DEFAULT_MONTHS), (1_000_000, 36))

    await section("3. After tax", after_tax)

    async def reviews():
        from app.models.india_tax_planning import TaxReview

        v = await adv.read_tax_reviews({})
        c = v["completion"]
        check("the demonstration's two reviews are counted", (c["regime"]["done"], c["harvest"]["done"], v["tax_year"]), (1, 1, year["label"]))
        regimes = [t for t in v["taxpayers"] if t["applies"]["regime"]]
        check("each share is done over those it applies to",
              (c["regime"]["of"], c["regime"]["share"]), (len(regimes), round(1 / len(regimes), 4)))
        huf_row = next(t for t in v["taxpayers"] if t["taxpayer_id"] == str(huf.id))
        check("an HUF takes a harvest review and no regime review", (huf_row["regime"]["state"], huf_row["harvest"]["state"]), ("not_applicable", "not_recorded"))
        check("a client with no account and no tax figure is no taxpayer", "Aarav Sharma" in {t["name"] for t in v["taxpayers"]}, False)
        base = {"person_id": str(karthik.id), "kind": "regime", "covered": "Both regimes read with him; he stays in the new one",
                "document": "Meeting note on file"}
        before = await count(TaxReview, TaxReview.firm_id == firm.id)
        card = await rows_of(adv, "record_tax_review", base)
        check("the card says whose, which, when and the figures kept",
              (card["Taxpayer"], card["Review"], card["Tax year"], card["Figures kept beside it"].startswith("New ")),
              ("Karthik Iyer", "Regime review", year["label"], True))
        check("and writes nothing", await count(TaxReview, TaxReview.firm_id == firm.id), before)
        await refused("only an adviser or paraplanner records", comp.record_tax_review(base), "adviser or a paraplanner")
        await refused("another firm's taxpayer is refused", outsider.record_tax_review(base), "not a client")
        await refused("a day ahead is refused", adv.record_tax_review({**base, "reviewed_on": (today + timedelta(days=1)).isoformat()}), "future")
        await refused("two years back is refused", adv.record_tax_review({**base, "tax_year": "2023-24"}), "this tax year")
        await refused("a day before the year began is refused",
                      adv.record_tax_review({**base, "reviewed_on": (year["start"] - timedelta(days=1)).isoformat()}), "first day")
        await refused("what was covered is asked for", adv.record_tax_review({**base, "covered": ""}), "looked at")
        await refused("evidence is asked for", adv.record_tax_review({**base, "document": ""}), "evidence")
        await refused("an HUF has no regime review", adv.record_tax_review({**{k: v for k, v in base.items() if k != "person_id"}, "entity_id": str(huf.id)}), "for a person")
        done = await adv.record_tax_review(base)
        check("recorded with the figures as they stood", done["recorded"]["figures"]["assessed"], True)
        check("and the household's share moves", done["completion"]["regime"]["done"], 1)
        await refused("the same review twice is refused", adv.record_tax_review(base), "already recorded")
        v = await adv.read_tax_reviews({})
        check("the firm's regime reviews are now two", v["completion"]["regime"]["done"], 2)
        last = await adv.record_tax_review({**base, "person_id": str(lakshmi.id), "tax_year": f"{year['start'].year - 1}-{year['start'].year % 100:02d}"})
        check("last year's review keeps no figures and does not count this year",
              (last["recorded"]["figures"], (await adv.read_tax_reviews({}))["completion"]["regime"]["done"]), (None, 2))
        both = await adv.record_tax_review({**base, "kind": "harvest"})
        check("a taxpayer with both counts as complete",
              next(t for t in both["taxpayers"] if t["name"] == "Karthik Iyer")["harvest"]["state"], "done")
        check("each is on the ledger", await count(LedgerEntry, LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == "tax_review_recorded"),
              await count(TaxReview, TaxReview.firm_id == firm.id))
        check("another firm counts none", (await outsider.read_tax_reviews({}))["completion"]["regime"], {"done": 0, "of": 0, "share": None})

    async def harvest():
        from app.aurea_core import advisers, india_tax_year as t

        q = (await adv.read_tax_reviews({}))["harvest"]
        queue = q["queue"]
        check("the queue is the engine's own exemption lots", all(x["lots"] and x["gain"] > 0 for x in queue), True)
        position = {p["name"]: p for h in (sharma, iyer) for p in (await adv.read_tax_position({"household_id": str(h.id)}))["taxpayers"]}
        for row in [x for x in queue if x["name"] in position]:
            want = round(sum(o["gain"] for o in position[row["name"]]["opportunities"] if o["kind"] == "exemption"), 2)
            close(f"{row['name']}: the gain is the tax position's", row["gain"], want)
        states = {x["name"]: x["state"] for x in queue}
        check("a taxpayer whose harvest review is recorded gets no task", states.get("Meera Sharma", "reviewed"), "reviewed")
        check("outside January to March the worker raises nothing",
              (await t.raise_due(s, firm, today=date(year["end"].year - 1, 11, 20)))["raised"], 0)
        check("and nothing has been raised by the seed", await count(Task, Task.firm_id == firm.id, Task.source == t.SOURCE), 0)

        mine = [x for x in queue if x["household"] == "The Sharma Family" and x["state"] == "to_raise"]
        card = await rows_of(adv, "raise_harvest_tasks", {"household_id": str(sharma.id)})
        check("the card lists each task and its owner", (card["Tasks"], all(any(k.startswith(x["name"]) for k in card) for x in mine)), (str(len(mine)), True))
        check("and writes nothing", await count(Task, Task.firm_id == firm.id, Task.source == t.SOURCE), 0)
        await refused("compliance does not raise them", comp.raise_harvest_tasks({}), "branch leader")
        await refused("another firm's household is refused", outsider.raise_harvest_tasks({"household_id": str(sharma.id)}), "not a client")
        done = await adv.raise_harvest_tasks({"household_id": str(sharma.id)})
        check("one task each", len(done["raised"]), len(mine))
        tasks = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == t.SOURCE))).scalars().all()
        owner = await advisers.adviser_of(s, firm.id, sharma.id)
        check("to the household's recorded adviser, due at the year's end",
              {(x.assigned_to, x.due_date, x.household_id) for x in tasks}, {(owner.id if owner else None, year["end"], sharma.id)})
        check("the task advises no sale or rebuy", all("advises no sale and no rebuy" in x.detail for x in tasks), True)
        await refused("a second time there is nothing to raise", adv.raise_harvest_tasks({"household_id": str(sharma.id)}), "nothing to raise")
        tasks[0].status = "done"
        await s.flush()
        january = date(year["end"].year, 1, 5)
        rest = sum(1 for x in (await t.harvest_queue(s, firm, today=january)) if x["state"] == "to_raise")
        first = await t.raise_due(s, firm, today=january)
        check("from January the worker raises the rest", (first["raised"], rest > 0), (rest, True))
        check("and a closed one is not raised again; a second pass raises nothing", (await t.raise_due(s, firm, today=january))["raised"], 0)
        every = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == t.SOURCE))).scalars().all()
        check("once for each taxpayer", len({x.title for x in every}), len(every))
        unassigned = [x for x in every if x.assigned_to is None]
        check("an unassigned task says so", all("unassigned" in x.detail for x in unassigned), True)
        entries = (await s.execute(select(LedgerEntry).where(LedgerEntry.firm_id == firm.id, LedgerEntry.event_type == t.RAISED_EVENT))).scalars().all()
        check("each is on the ledger", len(entries), len(every))
        nxt = await t.harvest_queue(s, firm, today=date(year["end"].year + 1, 1, 5))
        check("the next tax year starts afresh", {x["state"] for x in nxt}, {"to_raise"})
        banerjee = homes["The Banerjee Household"]
        banerjee.left_on = today
        await s.flush()
        gone = await adv.read_tax_reviews({})
        check("a household that has left is in no count and no queue",
              ("The Banerjee Household" in {x["household"] for x in gone["taxpayers"]}, "The Banerjee Household" in {x["household"] for x in gone["harvest"]["queue"]}),
              (False, False))
        banerjee.left_on = None
        await s.flush()

    await section("5. Reviews and completion", reviews)
    await section("4. The year-end gain harvest", harvest)

    async def surface():
        from app.api import india_tax_planning as api
        from seed import india_tax_planning as seeded

        check("the tools are registered", [k in TOOLS for k in (*READS, *WRITES)], [True] * 5)
        check("reads change nothing and writes stop for a Confirm card",
              ([TOOLS[k].change_state == ToolChangeState.NO for k in READS],
               [TOOLS[k].change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION for k in WRITES]), ([True] * 2, [True] * 3))
        check("a client holds none of them", [k for k in (*READS, *WRITES) if k in tools_for_role(UserRole.CLIENT)], [])
        check("each is run by an executor", [hasattr(adv, k) for k in (*READS, *WRITES)], [True] * 5)
        check("the stated return's writers", sorted(str(r.value) for r in TOOLS["record_stated_return"].roles_required),
              ["adviser", "paraplanner", "portfolio_team"])
        check("seeding again adds nothing", await seeded.add(s, firm), 0)
        check("the page reads the same", (await api.reviews(None, firm, s))["completion"],
              (await adv.read_tax_reviews({}))["completion"])
        check("and the comparison", (await api.after_tax(sharma.id, None, None, firm, s))["household"], "The Sharma Family")
        panes = (FRONTEND / "lib" / "paneViews.ts").read_text(encoding="utf-8")
        renderer = (FRONTEND / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
        check("each opens beside the conversation", [f'case "{k}":' in panes for k in (*READS, *WRITES)], [True] * 5)
        check("each has a card", [f'case "{k}":' in renderer for k in (*READS, *WRITES)], [True] * 5)
        view = (FRONTEND / "components" / "views" / "TaxView.tsx").read_text(encoding="utf-8")
        check("the Tax page carries India's sections", ("IndiaAfterTaxSection" in view, "IndiaReviewsSection" in view), (True, True))
        worker = (BACKEND / "app" / "atlas" / "worker.py").read_text(encoding="utf-8")
        check("the worker raises them", 'id="gain_harvest_tasks"' in worker, True)

    await section("6. The surface", surface)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
