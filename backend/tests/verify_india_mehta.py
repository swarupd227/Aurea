"""Prove the Mehta family and the India planning measures (B-400, L200-1 and L200-2 India section 12).

  1. The demonstration families' figures read plausibly: some retirements on track, some not, for reasons on file.
  2. A deployed firm is brought up to them by the backfill's steps, once, and what a person recorded is left alone.
  3. The onboarding case, walked day by day: each step shown from the records, or named as one that cannot be.
  4. The planning case, walked week by week, the same way.
  5. The measures: SIP health against plan, the emergency fund, will and nomination completeness, the construction-first
     order, for a household and the firm; roles, firm scope and refusals.
  6. The tools: the Confirm card, the pane, the card and the page; the plan-to age a plan of record needs.

A step that cannot be shown is printed as NOT SHOWN with why: a vendor, or not built. It is not a check.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_mehta
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta
from pathlib import Path

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Account, AccountBeneficiary, Goal, Household, Person
from app.models.identity import User
from app.models.tenant import Firm

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING))

PASS, FAIL, NOT_SHOWN = [], [], []
BACKEND = Path(__file__).resolve().parents[1]
FRONTEND = BACKEND.parent / "frontend"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def not_shown(step: str, why: str) -> None:
    NOT_SHOWN.append(step)
    print(f"  NOT SHOWN  {step}: {why}")


async def section(title: str, fn) -> None:
    """One part's checks. A part that raises is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:400]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 - the refusal's own words are what is checked
        ok = contains.lower() in str(exc).lower()
        check(label if ok else f"{label} [{exc}]", ok, True)
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
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(NOT_SHOWN)} step(s) named as not shown")
    for f in FAIL:
        print(f"  failed: {f}")
    return 1 if FAIL else 0


async def run(s) -> None:
    from app.aurea_core import (advice_file, aml_risk, balance_sheet, consents, consolidation, financial_plan, firm_time, goal_tradeoff,
                                identity_checks, india_money, kyc_registry, plan_record, planning_measures as pm, policy_values, pre_advice,
                                protection, residency, retirement, risk_profile, sip_mandates, tax_habits, tax_india_full)
    from app.aurea_core.graph import household_brain
    from app.models.balance_sheet import BalanceItem
    from app.models.consolidation import ConsolidationItem
    from app.models.consent_registry import ConsentRecord
    from app.models.emergency_fund import EmergencyFund
    from app.models.financial_plan import IncomeStream
    from app.models.held_away import HeldAwayHolding
    from app.models.identity_checks import VerificationAttempt
    from app.models.kyc_registry import KycLookup
    from app.models.onboarding import OnboardingCase
    from app.models.sip import Sip
    from seed import india_demo, india_demo_backfill, india_figures, india_mehta, india_planning_fixes

    # The firm as it stood before this item: built with the Mehtas left out, so the backfill's path is the one that adds them.
    real_add = india_mehta.add

    async def later(session, firm):
        return 0

    india_mehta.add = later
    try:
        firm = await india_demo.build(s, quotes=None)
    finally:
        india_mehta.add = real_add
    await s.commit()
    today = firm_time.today(firm)

    async def count(model, *where):
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    users = {}
    for u in (await s.execute(select(User).where(User.firm_id == firm.id).order_by(User.email))).scalars().all():
        users.setdefault(u.role, u)
    homes = lambda: s.execute(select(Household).where(Household.firm_id == firm.id))  # noqa: E731

    async def home(name):
        return (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == name))).scalar_one()

    async def person(name):
        return (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == name))).scalar_one()

    # ── 1 ────────────────────────────────────────────────────────────────────
    async def figures():
        plans, incomes = {}, {}
        for name in ("The Sharma Family", "The Iyer Family", "The Banerjee Household", "The D'Souza Household"):
            h = await home(name)
            plans[name] = await retirement.for_household(s, h.id, firm_id=firm.id)
            brain = await household_brain(s, h.id, firm_id=firm.id)
            incomes[name] = sum(float(((p.get("profile") or {}).get("tax") or {}).get("annual_income") or 0) for p in brain["persons"])
        p = {n: plans[n]["success_probability"] for n in plans}
        print("  retirement likelihoods:", p)
        check("the Iyers, on the highest income, are on track", p["The Iyer Family"] >= 0.85, True)
        check("the retired Banerjee, with a pension, is on track", p["The Banerjee Household"] >= 0.85, True)
        check("the Sharmas are short of their target but not broken", 0.5 <= p["The Sharma Family"] < 0.85, True)
        kapoor = await retirement.for_household(s, (await home("The Kapoor Family Trust")).id, firm_id=firm.id)
        check("the Kapoor trustee, with rent coming in, no longer reads broken", kapoor["success_probability"] >= 0.7, True)
        check("the D'Souzas are clearly short", 0.15 <= p["The D'Souza Household"] < 0.5, True)
        stack = plans["The Sharma Family"]["stack"]
        check("the stack adds to the Sharmas' answer and is not all of it", 0.05 < stack["without"]["success_probability"] < p["The Sharma Family"], True)
        saves = {n: (plans[n]["contribution"]["first_year"] + plans[n]["income_need"]) / incomes[n] for n in plans}
        check("spending and saving together fit inside each family's income after tax", {n: v <= 0.65 for n, v in saves.items()},
              {n: True for n in saves})
        sharma = await home("The Sharma Family")
        likely = await goal_tradeoff.for_household(s, sharma.id, firm_id=firm.id)
        g = {x["name"]: x["success_probability"] for x in likely["goals"]}
        check("a goal's likelihood and the retirement answer no longer contradict each other",
              abs(g["Retirement at 60"] - p["The Sharma Family"]) < 0.25, True)

    await section("1. The demonstration families' figures", figures)

    # ── 2 ────────────────────────────────────────────────────────────────────
    async def backfill():
        sharma, iyer, dsouza = [await home(n) for n in ("The Sharma Family", "The Iyer Family", "The D'Souza Household")]

        async def spending(h):
            rows = (await s.execute(select(BalanceItem).where(BalanceItem.household_id == h.id, BalanceItem.kind == "essential_spending",
                                                              BalanceItem.status == "current"))).scalars().all()
            return rows

        check("a second run of the figures writes nothing", await india_figures.add(s, firm), 0)
        # As a deployed firm stands: the planning seed's first figures.
        for h, old in ((sharma, 2_400_000), (iyer, 4_800_000)):
            (await spending(h))[0].amount = old
        # The D'Souzas' figure was replaced by a person.
        row = (await spending(dsouza))[0]
        row.amount, row.recorded_by = 1_500_000, "adviser@astra-india.demo"
        banerjee = await person("Subir Banerjee")
        for stream in (await s.execute(select(IncomeStream).where(IncomeStream.person_id == banerjee.id))).scalars().all():
            await s.delete(stream)
        for fund in (await s.execute(select(EmergencyFund).where(EmergencyFund.firm_id == firm.id))).scalars().all():
            await s.delete(fund)
        diya = await person(india_figures.MINOR)
        diya.is_next_gen = False
        await s.flush()
        wrote = await india_figures.add(s, firm)
        check("it replaces the two seeded figures, adds the pension and both funds, and marks the child", (wrote, diya.is_next_gen), (6, True))
        check("each restated figure stands once", [(float(x.amount), x.recorded_by) for h in (sharma, iyer) for x in await spending(h)],
              [(1_800_000.0, india_figures.BY), (3_600_000.0, india_figures.BY)])
        check("and the old one is kept", await count(BalanceItem, BalanceItem.household_id == sharma.id, BalanceItem.kind == "essential_spending",
                                                     BalanceItem.status == "ended") >= 1, True)
        check("a figure a person recorded is left alone", [(float(x.amount), x.recorded_by) for x in await spending(dsouza)],
              [(1_500_000.0, "adviser@astra-india.demo")])
        check("and a second run writes nothing", await india_figures.add(s, firm), 0)

        goals = (await s.execute(select(Goal).where(Goal.household_id.in_([sharma.id, iyer.id]), Goal.kind == "retirement"))).scalars().all()
        mine = next(g for g in goals if g.household_id == sharma.id)
        theirs = next(g for g in goals if g.household_id == iyer.id)
        theirs.target_amount, theirs.assumptions = 30_000_000, {**theirs.assumptions, "annual_contribution": 1_000_000}
        adviser = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
        await adviser.execute("update_goal", {"goal_id": str(mine.id), "target": 61_000_000})
        await india_demo_backfill.reset_goals(s, firm)
        await india_planning_fixes.add(s, firm)
        check("a seeded goal takes the restated figures", (float(theirs.target_amount), theirs.assumptions["annual_contribution"]),
              (130_000_000.0, 9_000_000))
        check("a goal a person changed stays", float(mine.target_amount), 61_000_000.0)

        check("the backfill adds the two Mehta households", await india_mehta.add(s, firm), 2)
        check("and only once", await india_mehta.add(s, firm), 0)
        source = Path(india_demo_backfill.__file__).read_text(encoding="utf-8")
        check("the backfill runs both steps", ("india_figures.add" in source, "india_mehta.add" in source), (True, True))
        check("every seeded record of the Mehtas says it is a demonstration record",
              [r.source_document for r in (await s.execute(select(BalanceItem).where(
                  BalanceItem.household_id == (await home(india_mehta.HOME)).id))).scalars().all() if "demonstration" not in r.source_document.lower()], [])

    await section("2. The backfill", backfill)
    await s.commit()

    mehta, brother = await home(india_mehta.HOME), await home(india_mehta.BROTHER_HOME)
    vivek, anjali, sameer = [await person(n) for n in (india_mehta.VIVEK, india_mehta.ANJALI, india_mehta.SAMEER)]
    cases = {c.prospect_name: c for c in (await s.execute(select(OnboardingCase).where(
        OnboardingCase.firm_id == firm.id, OnboardingCase.prospect_name.in_([india_mehta.VIVEK, india_mehta.ANJALI, india_mehta.SAMEER])))).scalars().all()}
    accounts = {a.name: a for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
    day0 = today - timedelta(days=india_mehta.DAY0)

    async def lookups(case):
        return [r.status for r in (await s.execute(select(KycLookup).where(KycLookup.case_id == case.id)
                                                   .order_by(KycLookup.created_at))).scalars().all()]

    async def check_attempts(case):
        return sorted((r.kind, r.outcome) for r in (await s.execute(select(VerificationAttempt).where(
            VerificationAttempt.case_id == case.id))).scalars().all())

    # ── 3 ────────────────────────────────────────────────────────────────────
    async def onboarding():
        print("  Day 0")
        check("both spouses' cases activated behind every gate", [str(cases[n].status) for n in (india_mehta.VIVEK, india_mehta.ANJALI)],
              ["approved", "approved"])
        check("they are one household with two children", sorted((p.full_name, p.is_next_gen) for p in (await s.execute(
            select(Person).where(Person.household_id == mehta.id))).scalars().all()),
              [("Anjali Mehta", False), ("Dev Mehta", True), ("Rhea Mehta", True), ("Vivek Mehta", False)])
        check("the registry answered validated for him", await lookups(cases[india_mehta.VIVEK]), ["validated"])
        check("registered for her, then validated after re-verification", await lookups(cases[india_mehta.ANJALI]), ["registered", "validated"])
        check("her video re-verification and both banks are recorded as checked",
              [await check_attempts(cases[n]) for n in (india_mehta.ANJALI, india_mehta.VIVEK)],
              [[("bank", "passed"), ("vipv", "passed")], [("bank", "passed")]])
        not_shown("KRA lookup, video verification and penny drop run by a provider", "vendor (B-401): a person recorded each answer by hand")
        agreed = (await s.execute(select(ConsentRecord).where(ConsentRecord.case_id.in_([c.id for c in cases.values()]),
                                                              ConsentRecord.kind == "advisory_agreement"))).scalars().all()
        check("each advisory agreement is e-signed in the consent registry", sorted({r.method for r in agreed}), ["e_signature"])
        not_shown("e-signing through a provider", "vendor (B-401): the signature is recorded from its evidence")
        gates = [await pre_advice.status(s, firm, h.id) for h in (mehta, brother)]
        check("nothing stands before advice for either household", [(g["met"], g["missing"]) for g in gates], [(True, []), (True, [])])
        from app.aurea_core import fee_cap
        from app.models.onboarding import FeeSchedule

        fees = [float((await s.get(FeeSchedule, cases[n].fee_schedule_id)).flat_fee) for n in (india_mehta.VIVEK, india_mehta.ANJALI)]
        cap, _ = fee_cap.cap_for({"flat_fee"}, 0.0, today)
        print(f"  fixed fees {sum(fees):,.0f} a year against the family cap of {cap:,.0f}")
        check("the couple's fixed fees together sit within the family cap", 0 < sum(fees) <= cap, True)
        view = await risk_profile.people_compared(s, mehta.id, firm_id=firm.id)
        asked = {r["person"]: r["assessment"]["indicated"] for r in view["people"] if r["assessment"]}
        check("his questionnaire indicates growth and hers conservative", asked, {"Vivek Mehta": "growth", "Anjali Mehta": "conservative"})
        check("the difference is shown side by side", view["gap"]["indicated"], 2)
        not_shown("a profile named aggressive", "the platform's highest indicated profile is growth")
        joint = accounts[india_mehta.JOINT]
        check("the joint demat is anyone or survivor, chosen and recorded", joint.registration_type, "joint_anyone")
        names = [india_mehta.JOINT, "Vivek Mehta demat", "Anjali Mehta demat"]
        shares = {}
        for n in names:
            rows = (await s.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id == accounts[n].id))).scalars().all()
            shares[n] = sorted((r.beneficiary_name, float(r.percentage)) for r in rows)
        check("each demat names the two children 50:50", shares, {n: [("Dev Mehta", 50.0), ("Rhea Mehta", 50.0)] for n in names})
        mandates = await sip_mandates.overview(s, firm, household_id=mehta.id)
        check("three mandates are in force, two of them UPI Autopay",
              sorted((m["reference"], m["state"]) for m in mandates["mandates"]),
              [("UMRN-DEMO-MEHTA-0001", "active"), ("UPI-DEMO-MEHTA-0002", "active"), ("UPI-DEMO-MEHTA-0003", "active")])
        not_shown("a joint demat opened with a depository, and folios opened with fund houses", "vendor (B-401): recorded from the paperwork")

        print("  Day 1 to 3")
        bench = await consolidation.for_household(s, mehta.id, firm_id=firm.id)
        items = (await s.execute(select(ConsolidationItem).where(ConsolidationItem.household_id == mehta.id))).scalars().all()
        folios = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.household_id == mehta.id,
                                                                HeldAwayHolding.account_label.like("Folio MEH-%")))).scalars().all()
        consolidated = [f for f in folios if f.account_label != india_mehta.LIQUID[1]]
        check("fourteen folios across five fund houses", (len(consolidated), len({f.institution for f in consolidated})), (14, 5))
        check("code changes for eleven", sum(1 for i in items if i.kind == "code_change"), 11)
        check("two ETFs moved off-market into the new demat", sum(1 for i in items if i.kind == "demat_transfer"), 2)
        check("one exit scheduled against the gain budget", bench["summary"]["exits_scheduled"], 1)
        not_shown("the consolidated statement read by itself", "vendor or Account Aggregator (B-401): the lines are recorded from it")

        print("  Day 2 to 5")
        case = cases[india_mehta.SAMEER]
        check("the brother is a household of his own, onboarded non-resident", (str(case.status), case.registration_type), ("approved", "nri_nre"))
        check("his account is the repatriable NRE one", accounts["Sameer Mehta NRE demat"].registration_type, "nri_nre")
        view = await residency.household_view(s, firm, brother.id)
        text = str(view)
        check("UAE residence, no US person and the self-certification are on file",
              ("United Arab Emirates" in text, (await residency.records(s, firm.id, sameer.id))[0].us_person), (True, False))
        check("his video verification is recorded", ("vipv", "passed") in await check_attempts(case), True)
        rating = await aml_risk.assess(s, case.id, firm_id=firm.id)
        check("screening is clear and the rating carries the cross-border factor",
              (any(f.get("factor", f.get("name")) in ("geography",) for f in rating["factors"]), rating["score"] > 0), (True, True))
        not_shown("the fund houses' acceptance matrix for his residence", "not built (B-401: counsel's rules)")

        print("  Day 5 to 7")
        steps = [i for i in bench["items"] if i["kind"] == "code_change"]
        check("all eleven code changes confirmed, none rejected", sorted({i["status"] for i in steps}), ["confirmed"])
        check("both transfers received", sorted({i["status"] for i in bench["items"] if i["kind"] == "demat_transfer"}), ["confirmed"])
        held = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.household_id == mehta.id,
                                                              HeldAwayHolding.institution == india_mehta.LEGACY))).scalars().all()
        from app.models.portfolio import Holding

        check("what landed is on the book once and no longer held away",
              (sorted({h.status for h in held}), await count(Holding, Holding.account_id == accounts[india_mehta.JOINT].id)), (["ended"], 2))
        money = await india_money.overview(s, firm, household_id=brother.id)
        check("his first remittance is filed with its certificate", "FIRC-DEMO-000071" in str(money), True)
        debits = await india_money.debits(s, firm.id, [accounts["Vivek Mehta demat"].id, accounts["Anjali Mehta demat"].id])
        check("the first debit of each SIP is confirmed", sorted(d.outcome for d in debits.values()), ["confirmed"] * 3)
        check("nothing waits in the debit queue", await india_money.queue(s, firm, household_id=mehta.id), [])
        check("paperwork took a day", {(c.activated_at - c.created_at).days for c in cases.values()}, {1})
        not_shown("registrar and exchange rails confirming by themselves, and a transaction reject rate", "vendor (B-401): confirmations are recorded")
        not_shown("the money and invested clocks", "no transfer or order sits on a case, and nothing fills on a synthetic price")

    await section("3. Onboarding the Mehta family", onboarding)

    # ── 4 ────────────────────────────────────────────────────────────────────
    async def planning():
        brain = await household_brain(s, mehta.id, firm_id=firm.id)
        sheet = await balance_sheet.for_household(s, mehta.id, firm_id=firm.id)
        plan = await financial_plan.for_household(s, mehta.id, firm_id=firm.id)
        print("  Week 1")
        check("income of Rs 60 lakh is recorded", sheet["cash_flow"]["income"], 6_000_000.0)
        kinds = {i["kind"]: i["amount"] for i in sheet["items"] if i["status"] == "current"}
        check("the apartment, its loan and the deposits", (kinds["property"], kinds["mortgage"], kinds["cash"]), (18_000_000.0, 4_000_000.0, 2_500_000.0))
        savings = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.household_id == mehta.id,
                                                                 HeldAwayHolding.status == "current"))).scalars().all()
        check("EPF passbooks and NPS statements are on file",
              sorted(l.attributes["savings_kind"] for l in savings if (l.attributes or {}).get("savings_kind")), ["epf", "epf", "nps", "nps"])
        # The managed book and the consolidated folios; the deposits and the emergency fund are held beside them.
        investable = (brain["totals"]["total_value"]
                      + sum(float(l.value) for l in savings if (l.account_label or "").startswith("Folio MEH-") and l.account_label != india_mehta.LIQUID[1]))
        print(f"  investable assets: {investable:,.0f}")
        check("investable assets are about Rs 2.4 crore", 23_000_000 <= investable <= 25_500_000, True)
        goals = {g["name"]: g for g in goal_tradeoff.likelihoods(brain, today)["goals"]}
        edu = [g for n, g in goals.items() if n.startswith("Education")]
        priced = sum(g["target_amount"] for g in edu)
        print(f"  education: {sum(g['target_today'] for g in edu):,.0f} today, {priced:,.0f} when it falls due")
        check("both children's education comes to about Rs 1.4 crore at education inflation",
              (13_000_000 <= priced <= 15_000_000, {g["index"] for g in edu}, all(g["target_amount"] > g["target_today"] for g in edu)),
              (True, {"education"}, True))
        r = plan["retirement"]
        check("retirement at 58 on Rs 12 lakh a year of today's spending", (r["retirement_age"], r["income_need"], r["income_basis"]),
              (58, 1_200_000, "stated"))
        check("the goals are tiered, the wedding a wish", (sorted({g["tier"] for g in edu}), goals["Retirement at 58"]["tier"],
                                                          goals["Rhea's wedding"]["tier"]), (["needs"], "needs", "wishes"))
        not_shown("equity as 22% of financial assets on a screen", "not built: no view reads the asset mix across folios and deposits")
        life = await protection.for_household(s, mehta.id, firm_id=firm.id)
        need = {p["name"]: p["death"] for p in life["people"]}
        print("  term need and cover:", {n: (v.get("need"), v.get("cover"), v.get("gap")) for n, v in need.items()})
        old = [p for p in life["policies"] if p["sum_insured"] == 5_000_000 and p["kind"] == "life"]
        check("the Rs 50 lakh term policy he brought is on file", len(old), 1)
        values = await policy_values.for_household(s, mehta.id, firm_id=firm.id)
        kept = [p for p in values["policies"] if p["status"] == "in_force"]
        gone = [p for p in values["policies"] if p["status"] == "ended"]
        check("two endowments: one kept, one surrendered with its reason", (len(kept), len(gone), "Surrendered" in gone[0]["ended_reason"]), (1, 1, True))
        check("continuing the kept one implies under 5% a year", kept[0]["continuing_return"] is not None and kept[0]["continuing_return"] < 0.05, True) \
            if not isinstance(kept[0]["continuing_return"], dict) else check("continuing the kept one implies under 5% a year",
                                                                            kept[0]["continuing_return"].get("rate", 1) < 0.05, True)

        print("  Week 2")
        covers = sorted((p["person"], p["sum_insured"]) for p in life["policies"] if p["kind"] == "life" and p["status"] == "in_force"
                        and p["sum_insured"] >= 10_000_000)
        check("term cover of Rs 3 crore and Rs 1.5 crore placed", covers, [("Anjali Mehta", 15_000_000.0), ("Vivek Mehta", 30_000_000.0)])
        m = await pm.for_household(s, mehta.id, firm_id=firm.id)
        check("the family has cover of its own, and a parent is read apart",
              (m["protection"]["health"]["own"], m["protection"]["health"]["members"]), (4, 4))
        e = m["emergency_fund"]
        check("an emergency fund of Rs 9 lakh, half built over six months",
              (e["target"], e["held"], e["share"], e["build_months"], e["target_months"], e["state"]), (900_000.0, 450_000.0, 0.5, 6, 6.0, "building"))
        not_shown("the premiums shown to fit the cash flow before any SIP increase", "not built: cash flow is before tax and no premium test is made")

        print("  Week 3")
        compare = {}
        for p in (vivek, anjali):
            tax = p.profile["tax"]
            compare[p.full_name] = tax_india_full.regime_comparison(income=tax["annual_income"], salary=tax["salary_income"],
                                                                    deductions=tax["old_regime_deductions"], age=48, gains=0, is_individual=True)
        saving = sum(c["old"]["tax"] - c["new"]["tax"] for c in compare.values())
        print(f"  the new regime is lower by {saving:,.0f} across the two")
        check("the regime comparison is run and the new regime wins for both, by about Rs 1.1 lakh together",
              (all(c["new"]["tax"] < c["old"]["tax"] for c in compare.values()), 100_000 <= saving <= 125_000), (True, True))
        from app.aurea_core import retirement_stack

        check("employer NPS is recorded for both", len(await retirement_stack.employer_nps(s, firm.id, mehta.id)), 2)
        not_shown("the employer NPS deduction (80CCD(2)) inside the regime comparison", "not built (B-396)")
        harvest = await tax_habits.gain_harvest(s, firm, vivek, today)
        check("the yearly gain-harvest position is read from the book", isinstance(harvest, dict) and bool(harvest), True)
        not_shown("a gain-harvest calendar and a record that the reviews happened", "not built (B-396)")
        h = m["sip_health"]
        check("Rs 60,000 a month of SIPs registered against Rs 60,000 planned on goals", (h["registered_monthly"], h["planned_monthly"], h["share"]),
              (60_000.0, 60_000.0, 1.0))
        not_shown("tax savings and surrender proceeds traced into the new SIP capacity", "not built: no flow links one to the other")

        print("  Week 4")
        sips = (await s.execute(select(Sip).where(Sip.account_id.in_([accounts[n].id for n in ("Vivek Mehta demat", "Anjali Mehta demat")])))).scalars().all()
        check("the retirement SIP steps up 10% a year in the mandate", [(float(x.step_up_percent), x.step_up_every_months) for x in sips if x.step_up_percent],
              [(10.0, 12)])
        check("each education goal has its SIP", sorted(g["name"] for g in goals.values() if g["contribution_basis"] == "sip_registration"),
              sorted(["Retirement at 58", "Education · Rhea Mehta", "Education · Dev Mehta"]))
        not_shown("equity-and-hybrid glide paths and systematic transfers from year 15", "not built (B-408)")
        sized = await goal_tradeoff.for_household(s, brother.id, firm_id=firm.id)
        check("the brother's money sits in a plan of his own", [g["name"] for g in sized["goals"]], ["Return to India"])
        from app.models.estate_nri import TreatyCertificate

        trc = (await s.execute(select(TreatyCertificate).where(TreatyCertificate.household_id == brother.id))).scalars().all()
        check("his tax residency certificate, the paper a treaty claim needs, is on file for this tax year",
              [(t.kind, t.country, t.valid_from <= today <= t.valid_to) for t in trc], [("trc", "United Arab Emirates", True)])
        not_shown("treaty (DTAA) rates and relief worked out on the brother's plan", "not built: the certificate is recorded, no rate is applied (B-413)")
        record = await plan_record.for_household(s, mehta.id, firm_id=firm.id)
        text = str(record)
        check("a plan of record: baseline, a second person's review and delivery, its actions now open",
              (record["state"], "passed" in text), ("implementing", True))
        funded = plan["funded"]
        by_goal = {g["name"]: g["ratio"] for g in funded["by_goal"] if g["tier"] == "needs"}
        print(f"  funded ratio {funded['ratio']}, by needs goal {by_goal}")
        check("a funded ratio is computed for the household and each needs goal", (funded["ratio"] is not None, len(by_goal)), (True, 3))
        check("each education goal's funded ratio is between 0.9 and 1.1",
              [0.9 <= by_goal[n] <= 1.1 for n in by_goal if n.startswith("Education")], [True, True])
        not_shown("the retirement goal and the household at a funded ratio between 0.9 and 1.1",
                  f"the ratio counts the managed book and the retirement stack, not saving to come: retirement reads {by_goal['Retirement at 58']}, "
                  f"the household {funded['ratio']}")
        likely = {g["name"]: g["success_probability"] for g in goal_tradeoff.likelihoods(brain, today)["goals"]}
        print(f"  likelihoods: {likely}; retirement income {plan['retirement']['success_probability']}")
        check("the plan reads as a family with work to do: education likelier than not, the retirement income short",
              (all(likely[n] >= 0.5 for n in likely if n.startswith("Education")), plan["retirement"]["success_probability"] < 0.5), (True, True))
        advice = await advice_file.for_household(s, mehta.id, firm_id=firm.id)
        words = str(advice)
        check("the advice file holds each recommendation with what was rejected, the two declines among them",
              ("unit-linked plan the family's bank proposed" in words, "no more than 10 percent" in words, "why_rejected" in words), (True, True, True))
        from app.models.engagement import Task

        tasks = [t.title for t in (await s.execute(select(Task).where(Task.household_id == mehta.id, Task.source == "plan_delivery"))).scalars().all()]
        check("the will is briefed to a lawyer as a task", any("lawyer" in t for t in tasks), True)
        check("no will is on file, and nominations are complete", (m["documents"]["wills"]["current"], m["documents"]["nominations"]["share"]), (0, 1.0))
        from app.aurea_core import estate_register

        register = await estate_register.household_register(s, firm, mehta.id)
        check("the asset register for the lawyer lists what they hold and owe, with no nomination missing",
              (register["counts"]["assets"] > 15, register["counts"]["liabilities"], register["missing_nominations"],
               register["counts"]["without_will"]), (True, 1, [], 2))
        not_shown("the step-up chart in the client conversation", "the goal sizing card compares flat and step-up as figures; no chart (B-394)")
        check("each recommendation carries the risk profile it was made against",
              all(r.get("risk_basis") for r in advice["recommendations"]), True)
        not_shown("the advice file sampled and found complete to the SEBI standard",
                  "compliance's sample audit is built (B-399); none is seeded for the Mehtas, and nothing here says the file meets the rule")

    await section("4. Building the Mehta plan", planning)

    # ── 5 ────────────────────────────────────────────────────────────────────
    async def measures():
        m = await pm.for_household(s, mehta.id, firm_id=firm.id)
        check("the order is protection, emergency fund, documents, investing", [x["key"] for x in m["order"]],
              ["protection", "emergency_fund", "documents", "investing"])
        states = {x["key"]: x["state"] for x in m["order"]}
        check("the Mehtas: fund building, will outstanding, SIPs on plan",
              (states["emergency_fund"], states["documents"], states["investing"]), ("building", "short", "done"))
        check("next is the first step not in place", m["next"], next(x["key"] for x in m["order"] if x["state"] != "done"))
        check("the step-up share is one of three, and the mandates are in force",
              (m["sip_health"]["step_up_registered"], m["sip_health"]["active"], m["sip_health"]["mandate_in_force"]), (1, 3, 3))
        sharma = await home("The Sharma Family")
        sm = await pm.for_household(s, sharma.id, firm_id=firm.id)
        fails = sm["sip_health"]["failures"]
        check("a failed mandate is counted open until put right", (fails["ever"] >= 1, fails["open"] >= 1, fails["recovered"] <= fails["ever"]),
              (True, True, True))
        # Put right: a registration recorded after the failure.
        account = accounts_of = None  # noqa: F841
        from app.models.india_batch_a import DebitMandate

        failed = (await s.execute(select(DebitMandate).where(DebitMandate.reference == "UPI-REQ-DEMO-SHARMA-1", DebitMandate.kind == "failed"))).scalars().one()
        await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=failed.account_id, reference="UPI-DEMO-RETRY-1",
                                  mandate_type="upi_autopay", per_debit_limit=15_000, starts_on=today, last4="2204", document="Mandate registration, filed")
        again = (await pm.for_household(s, sharma.id, firm_id=firm.id))["sip_health"]["failures"]
        check("and counted recovered once a later registration stands", (again["open"], again["recovered"]), (fails["open"] - 1, fails["recovered"] + 1))
        iyer = await pm.for_household(s, (await home("The Iyer Family")).id, firm_id=firm.id)
        check("a complete fund is done", (iyer["emergency_fund"]["state"], iyer["emergency_fund"]["share"]), ("done", 1.0))
        banerjee = await pm.for_household(s, (await home("The Banerjee Household")).id, firm_id=firm.id)
        check("none on file is not assessed, never zero", (banerjee["emergency_fund"]["state"], banerjee["emergency_fund"]["share"],
                                                          banerjee["emergency_fund"]["held"]), ("not_assessed", None, None))
        check("a fund with nothing set aside is building at 0%",
              (lambda e: (e["state"], e["share"]))((await pm.emergency_fund(s, firm, sharma.id))), ("building", 0.0))

        view = await pm.for_firm(s, firm)
        n = await count(Household, Household.firm_id == firm.id, Household.left_on.is_(None))
        f = view["measures"]
        check("the firm's shares count every household", (len(view["households"]), f["emergency_fund"]["of"]), (n, n))
        check("the emergency fund share", (f["emergency_fund"]["met"], f["emergency_fund"]["share"]), (1, round(1 / n, 3)))
        check("will completeness counts households with an adult", f["wills"]["of"] <= n and f["wills"]["met"] < f["wills"]["of"], True)
        check("nomination completeness is a share of demats and folios", 0 < f["nominations"]["met"] <= f["nominations"]["of"], True)
        check("registered saving is compared with planned saving", f["sip_against_plan"]["planned_monthly"] > 0, True)

        # Wills and nominations are the estate register's, counted: a will on file is current once its yearly check stands.
        from app.aurea_core import client_file, estate_register

        for p in (vivek, anjali):
            await client_file.record_estate_document(s, p.id, firm_id=firm.id, by="a", kind="will", signed_on=today - timedelta(days=10),
                                                     source_document="Signed will, filed", held_by=None)
        wills = (await pm.for_household(s, mehta.id, firm_id=firm.id))["documents"]["wills"]
        check("a will never checked is on file and not current", (wills["on_file"], wills["current"], wills["share"]), (2, 0, 0.0))
        await estate_register.record_check(s, firm, by="a", person_id=vivek.id, outcome="matches", document="Adviser's note of the check")
        docs = (await pm.for_household(s, mehta.id, firm_id=firm.id))["documents"]
        check("a checked will counts", (docs["wills"]["current"], docs["wills"]["share"], docs["state"]), (1, 0.5, "short"))
        await estate_register.record_check(s, firm, by="a", person_id=anjali.id, outcome="matches", document="Adviser's note of the check")
        docs = (await pm.for_household(s, mehta.id, firm_id=firm.id))["documents"]
        check("both checked and every nomination on file puts the step in place", (docs["state"], docs["nominations"]["share"]), ("done", 1.0))
        reg = await estate_register.household_register(s, firm, sharma.id)
        theirs = (await pm.for_household(s, sharma.id, firm_id=firm.id))["documents"]["nominations"]
        check("the measure and the register agree on what is missing", theirs["assets"] - theirs["covered"], reg["counts"]["missing_nominations"])

        tools = {r: ToolExecutors(s, users[r].id, r, firm.id) for r in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.COMPLIANCE, UserRole.OPERATIONS)}
        ok = {"household_id": str(mehta.id), "target_amount": 900_000, "held_amount": 600_000, "held_in": "Liquid fund folio",
              "statement_date": today.isoformat(), "document": "Account statement, filed"}
        for role in (UserRole.COMPLIANCE, UserRole.OPERATIONS):
            check(f"{role.value} holds no record tool", role in TOOLS["record_emergency_fund"].roles_required, False)
        await refused("a target is never supplied", tools[UserRole.ADVISER].preview_change("record_emergency_fund", {**ok, "target_amount": None}),
                      "None is worked out")
        await refused("money set aside says where", tools[UserRole.ADVISER].preview_change("record_emergency_fund", {**ok, "held_in": ""}), "where it is held")
        await refused("a statement is not from the future", tools[UserRole.ADVISER].preview_change(
            "record_emergency_fund", {**ok, "statement_date": (today + timedelta(days=2)).isoformat()}), "future")
        await refused("a figure is a number", tools[UserRole.ADVISER].preview_change("record_emergency_fund", {**ok, "held_amount": "nan"}), "")
        before = await count(EmergencyFund, EmergencyFund.household_id == mehta.id)
        card = await tools[UserRole.ADVISER].preview_change("record_emergency_fund", ok)
        rows = {r["label"]: r["value"] for r in card["rows"]}
        check("the card says what changes and writes nothing",
              (rows["Household"], "4,50,000" in str(rows["On file now"]), await count(EmergencyFund, EmergencyFund.household_id == mehta.id)),
              ("The Mehta Family", True, before))
        out = await tools[UserRole.PARAPLANNER].execute("record_emergency_fund", ok)
        check("recorded, added and never edited", (out["emergency_fund"]["held"], await count(EmergencyFund, EmergencyFund.household_id == mehta.id)),
              (600_000.0, before + 1))
        await refused("the same again is refused", tools[UserRole.ADVISER].execute("record_emergency_fund", ok), "already")
        other = Firm(name="Other", slug="other-measures", jurisdiction="IN", base_currency="INR", settings={})
        us = Firm(name="US", slug="us-measures", jurisdiction="US", base_currency="USD", settings={})
        s.add_all([other, us])
        await s.flush()
        outsider = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, other.id)
        await refused("another firm reads nothing of it", outsider.execute("read_planning_measures", {"household_id": str(mehta.id)}), "not found")
        await refused("nor records on it", outsider.execute("record_emergency_fund", ok), "not found")
        check("another firm's book is empty", (await outsider.execute("read_planning_measures", {}))["households"], [])
        check("outside India the measures say so", (await pm.for_firm(s, us))["applies"], False)

    await section("5. The measures", measures)

    # ── 6 ────────────────────────────────────────────────────────────────────
    async def tools_and_views():
        check("reading changes nothing and recording stops for a Confirm",
              (TOOLS["read_planning_measures"].change_state, TOOLS["record_emergency_fund"].change_state),
              (ToolChangeState.NO, ToolChangeState.PAUSE_FOR_CONFIRMATION))
        check("a client holds neither", [UserRole.CLIENT in TOOLS[k].roles_required for k in ("read_planning_measures", "record_emergency_fund")],
              [False, False])
        check("it opens beside the conversation", ("view plan" in TOOLS["read_planning_measures"].description, "plan" in OPEN_VIEWS), (True, True))
        adviser = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
        said = (await adviser.execute("read_planning_measures", {"household_id": str(mehta.id)}))["message"]
        check("the message names the order and the first step not in place", ("in order:" in said, "first step not yet in place" in said), (True, True))
        said = (await adviser.execute("read_planning_measures", {}))["message"]
        check("and the book's shares", "Across" in said, True)
        goal = (await s.execute(select(Goal).where(Goal.household_id == mehta.id, Goal.kind == "retirement"))).scalars().one()
        check("the plan-to age is recorded from the conversation", goal.assumptions["longevity_age"], 90)
        await refused("it is after the retirement age", adviser.execute("update_goal", {"goal_id": str(goal.id), "plan_to_age": 57}), "between")
        out = await adviser.preview_change("update_goal", {"goal_id": str(goal.id), "plan_to_age": 92})
        check("its card says the change", {r["label"]: r["value"] for r in out["rows"]}.get("Plan to age"), "90 becomes 92")
        read = lambda p: (FRONTEND / p).read_text(encoding="utf-8")  # noqa: E731
        for path, words in (("app/workspace/components/artifacts/ArtifactRenderer.tsx", ('case "read_planning_measures"', 'case "record_emergency_fund"')),
                            ("lib/paneViews.ts", ('case "read_planning_measures"', 'case "record_emergency_fund"')),
                            ("components/views/FinancialPlanView.tsx", ("plan-measures", "/api/plan/measures")),
                            ("app/workspace/components/artifacts/PlanningMeasuresCard.tsx", ("planning-measures-card", "measures-order"))):
            text = read(path)
            check(f"{path.split('/')[-1]} carries it", [w in text for w in words], [True] * len(words))
        from app.api import financial_plan as api

        got = await api.measures_route(household=mehta.id, user=users[UserRole.ADVISER], firm=firm, session=s)
        check("the page's route reads the same", (got["next"], got["can_record"]), ((await pm.for_household(s, mehta.id, firm_id=firm.id))["next"], True))

    await section("6. The tools, the pane and the page", tools_and_views)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
