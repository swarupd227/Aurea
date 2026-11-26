"""Prove the planning defects the India planning module found are fixed (B-393).

  1. A person records salary, old-regime deductions, the regime and the slab rate from a document, behind a Confirm card, so
     the regime comparison works for a household the seed did not write.
  2. Protection reads a goal's target on its own price basis, takes the retirement age and the discount rate from the
     assumption set, and shows employer group cover apart without counting it.
  3. India has a suggested cash return, with its source and marked assumed, so the funded ratio is not discounted at the
     platform's 2.5%.
  4. The US 4% rule and the 529 column are shown to a US household and nobody else.
  5. Surplus is income before tax less spending, and says so.
  6. A meeting follow-up creates a goal with nothing invented.
  7. The glide path holds money for a goal under three years away outside equity.
  8. A visiting citizen or person of Indian origin over the income line with 120 to 181 days is not ordinarily resident, and
     the 182-day relaxation needs the recorded fact that they left for work or are visiting.
  9. The demonstration has goal tiers and essential spending.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_planning_fixes
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import glide_path, meeting_companion
from app.aurea_core import (
    assumptions as assumption_record, balance_sheet, firm_time, financial_plan, goal_tradeoff, law_figures, planning, protection,
    residency,
)
from app.aurea_core.graph import household_brain
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.balance_sheet import BalanceItem
from app.models.enums import MandateType, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
FRONTEND = Path(__file__).resolve().parents[2] / "frontend"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def section(title: str, fn) -> None:
    """One defect's checks. A section that raises (the fix is not there yet) is one failure, and the rest still run."""
    print(f"\n=== {title} ===")
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:200]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except ExecutorError as e:
        check(label, contains.lower() in str(e).lower(), True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def person(s, firm, household, name, *, born=date(1980, 5, 1), tax=None):
    p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=household.id, full_name=name, date_of_birth=born,
               profile={"tax": tax} if tax else {})
    s.add(p)
    return p


async def home(s, firm, name, *, cash=1_000_000):
    h = Household(id=uuid.uuid4(), firm_id=firm.id, name=name)
    s.add(h)
    await s.flush()
    p = person(s, firm, h, f"{name} Earner", tax={"annual_income": 3_000_000})
    await s.flush()
    m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name=f"{name} mandate", mandate_type=MandateType.ADVISORY, constraints={})
    s.add(m)
    await s.flush()
    s.add(Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name=f"{name} account", currency=firm.base_currency, cash_balance=cash))
    await s.flush()
    return h, p


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN",
                        settings={"timezone": "Asia/Kolkata"})
            us = Firm(id=uuid.uuid4(), name="US", slug="us", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([firm, us])
            await s.flush()
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@in", full_name=r.value, role=r, hashed_password="x")
                     for r in (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS)}
            us_adviser = User(id=uuid.uuid4(), firm_id=us.id, email="a@us", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add_all([*users.values(), us_adviser])
            await s.flush()
            today = firm_time.today(firm)
            ex = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            hh, earner = await home(s, firm, "Rao")
            us_hh, us_earner = await home(s, us, "Smith")

            async def tax_inputs():
                key = "record_tax_inputs"
                check("a state change behind a Confirm card", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
                check("an adviser or a paraplanner", TOOLS[key].roles_required, {UserRole.ADVISER, UserRole.PARAPLANNER})
                before = (await ex.read_tax_position({"household_id": str(hh.id)}))["taxpayers"][0]["engine"]["regime"]
                check("before, the comparison cannot pick a regime", (before["conclusive"], before["salary_recorded"]), (False, False))
                good = {"person_id": str(earner.id), "document": "Form 16 for 2025-26 and the investment proofs", "salary_income": 3_000_000,
                        "old_regime_deductions": 350_000, "regime": "new", "marginal_rate": 30}
                ledger_before = (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one()
                card = await ex.preview_change(key, good)
                labels = [r["label"] for r in card["rows"]]
                check("the card names the person, each figure and the document",
                      all(x in labels for x in ("Taxpayer", "Salary a year", "Old-regime deductions", "Tax regime", "Slab rate", "Document")), True)
                await s.refresh(earner)
                check("showing it wrote nothing", ("salary_income" in earner.profile["tax"],
                      (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one()), (False, ledger_before))
                await refused("operations does not record it",
                              ToolExecutors(s, users[UserRole.OPERATIONS].id, UserRole.OPERATIONS, firm.id).preview_change(key, good), "adviser")
                await refused("it needs its document", ex.preview_change(key, {**good, "document": " "}), "document")
                await refused("at least one figure", ex.preview_change(key, {"person_id": str(earner.id), "document": "Form 16"}), "at least one")
                await refused("a salary is not above the income on file", ex.preview_change(key, {**good, "salary_income": 4_000_000}), "income")
                await refused("a regime is new or old", ex.preview_change(key, {**good, "regime": "both"}), "new or old")
                await refused("a slab rate is one the regime has", ex.preview_change(key, {**good, "marginal_rate": 42}), "slab")
                await refused("nothing negative", ex.preview_change(key, {**good, "old_regime_deductions": -1}), "negative")
                await refused("another firm's person is not found", ex.preview_change(key, {**good, "person_id": str(us_earner.id)}), "not")
                await refused("a household taxed elsewhere has no regime",
                              ToolExecutors(s, us_adviser.id, UserRole.ADVISER, us.id).preview_change(key, {**good, "person_id": str(us_earner.id)}), "india")
                done = await ex.execute(key, good)
                await s.refresh(earner)
                tax = earner.profile["tax"]
                check("confirmed, it is on the person's profile, the income kept",
                      (tax["salary_income"], tax["old_regime_deductions"], tax["regime"], tax["marginal_rate"], tax["annual_income"]),
                      (3_000_000.0, 350_000.0, "new", 0.3, 3_000_000))
                check("with its document and day", ("Form 16" in tax["tax_inputs_source"]["document"], tax["as_of"]["salary_income"]),
                      (True, today.isoformat()))
                check("on the ledger", (await s.execute(select(func.count()).select_from(LedgerEntry))).scalar_one(), ledger_before + 1)
                t = done["taxpayers"][0]
                check("the result is the tax position, and the comparison now picks",
                      (t["engine"]["regime"]["conclusive"], t["engine"]["regime"]["salary_recorded"], t["engine"]["regime"]["cheaper"]), (True, True, "new"))
                check("what is on file is shown beside it", (t["engine"]["on_file"]["salary_income"], t["engine"]["on_file"]["old_regime_deductions"]),
                      (3_000_000.0, 350_000.0))

            await section("1. Salary, deductions, regime and slab rate are recorded by a person", tax_inputs)

            async def protect():
                h, p = await home(s, firm, "Nair", cash=0)
                p.date_of_birth = date(today.year - 45, 1, 1)
                due = today + timedelta(days=int(365.25 * 8))
                s.add(Goal(firm_id=firm.id, household_id=h.id, name="College", kind="education", target_amount=1_000_000, target_date=due,
                           assumptions={"target_basis": "today", "index": "education"}))
                s.add(Goal(firm_id=firm.id, household_id=h.id, name="Retire", kind="retirement", target_amount=1,
                           target_date=today + timedelta(days=int(365.25 * 15))))
                q = person(s, firm, h, "Nair Partner", tax={"annual_income": 0})
                await s.flush()
                view = await protection.for_household(s, h.id, firm_id=firm.id, today=today)
                rules = view["assumptions"]
                check("retirement age and discount rate are the assumption set's",
                      (rules["retirement_age"], rules["discount_rate"], rules["sources"]["retirement_age"], rules["sources"]["discount_rate"]),
                      (60, 0.055, "market", "cash_return"))
                row = next(r for r in view["people"] if r["name"] == "Nair Earner")
                years = (due - today).days / 365.25
                want = 1_000_000 * 1.09 ** years / 1.055 ** years
                check("a goal in today's money grows at education inflation before it is discounted",
                      abs(row["death"]["goals_need"] - want) < 5, True)
                firm.settings = {**firm.settings, "protection": {"retirement_age": 62, "discount_rate": 0.04}}
                await s.flush()
                rules = protection.assumptions(firm)
                check("a firm's protection setting stands over a market suggestion",
                      (rules["retirement_age"], rules["discount_rate"], rules["sources"]["discount_rate"]), (62, 0.04, "protection_setting"))
                firm.settings = {**firm.settings, "retirement_age": 59, "planning_discount_rate": 0.065}
                await s.flush()
                rules = protection.assumptions(firm)
                check("the firm's assumption set stands over both",
                      (rules["retirement_age"], rules["discount_rate"], rules["sources"]["retirement_age"]), (59, 0.065, "firm"))
                firm.settings = {"timezone": "Asia/Kolkata"}
                await s.flush()

                base = await protection.for_household(s, h.id, firm_id=firm.id, today=today)
                b = next(r for r in base["people"] if r["name"] == "Nair Earner")
                common = dict(person_id=p.id, provider="Employer scheme", source_document="Benefit statement 2026", recorded_by="a@in", today=today)
                await protection.record_policy(s, firm.id, kind="life", sum_insured=5_000_000, group_cover=True, **common)
                await protection.record_policy(s, firm.id, kind="life", sum_insured=2_000_000, **{**common, "provider": "Term insurer"})
                await protection.record_policy(s, firm.id, kind="tpd", sum_insured=1_000_000, group_cover=True, **common)
                view = await protection.for_household(s, h.id, firm_id=firm.id, today=today)
                r = next(x for x in view["people"] if x["name"] == "Nair Earner")
                check("only personal cover counts", (r["death"]["cover"], r["death"]["group_cover"]), (2_000_000.0, 5_000_000.0))
                check("the gap is the gap without the employer's cover", round(b["death"]["gap"] - r["death"]["gap"], 2), 2_000_000.0)
                check("and what it would be with it is shown", round(r["death"]["gap"] - r["death"]["gap_with_group_cover"], 2), 5_000_000.0)
                check("disability likewise", (r["disability"]["cover"], r["disability"]["group_cover"]), (0.0, 1_000_000.0))
                group = [x for x in view["policies"] if x["group_cover"]]
                check("each group policy says why it is not counted",
                      [(x["counted"], "employer" in (x["not_counted"] or "").lower()) for x in group], [(False, True), (False, True)])
                check("two figures apart, one policy counted", view["summary"]["policies_counted"], 1)
                gap = next(g for g in protection.gaps_of(view) if g["person"] == "Nair Earner" and g["scenario"] == "death")
                check("a gap carries the employer cover beside it", (gap["group_cover"], gap["gap_with_group_cover"] < gap["gap"]), (5_000_000.0, True))
                card = await ex.preview_change("record_protection_policy", {"person_id": str(p.id), "kind": "life", "provider": "Employer scheme",
                                               "source_document": "Benefit statement 2026", "sum_insured": 100, "group_cover": True})
                counted = next((x["value"] for x in card["rows"] if x["label"] == "Counted"), "")
                check("the Confirm card says it will not be counted", counted.startswith("No"), True)
                said = (await ex.read_protection({"household_id": str(h.id)}))["message"]
                check("the reading says the employer cover is apart", "employer" in said.lower(), True)
                assert q is not None

            await section("2. Protection: the goal's price basis, the assumption set and employer cover", protect)

            async def cash_return():
                india = planning.assumption_set({}, "IN")
                basis = next(b for b in india["market_basis"] if b["key"] == "cash_return")
                check("a suggested Indian cash return, marked assumed with its source",
                      (india["cma"]["cash"][0], basis["assumed"], "ASSUMED" in basis["source"], "assumed" in basis["basis"]),
                      (0.055, True, True, True))
                check("the funded ratio is discounted at it", (india["discount_rate"], india["sources"]["discount_rate"]), (0.055, "cash_return"))
                fig = assumption_record.figures_now(firm, None)["return.cash"]
                check("the assumption record says whose", (fig["value"], fig["whose"], "ASSUMED" in fig["source"]), (0.055, "market", True))
                check("a firm's own cash return replaces it", planning.assumption_set({"cmas": {"cash": [0.06, 0.01]}}, "IN")["discount_rate"], 0.06)
                check("a market with none keeps the platform's", planning.assumption_set({}, "NZ")["discount_rate"], 0.025)

            await section("3. India's cash return", cash_return)

            async def plan_by_market():
                for h, f in ((hh, firm), (us_hh, us)):
                    s.add(Goal(firm_id=f.id, household_id=h.id, name="School", kind="education", target_amount=500_000,
                               target_date=today + timedelta(days=3650), assumptions={}))
                await s.flush()
                ind = await financial_plan.for_household(s, hh.id, firm_id=firm.id)
                usa = await financial_plan.for_household(s, us_hh.id, firm_id=us.id)
                check("no 4% rule for an Indian household", (ind["retirement"]["rule_of_thumb_rate"], ind["retirement"]["rule_of_thumb_income"]), (None, None))
                check("nor a 529 figure", [e["in_529"] for e in ind["education"]], [None])
                check("a US household keeps both", (usa["retirement"]["rule_of_thumb_rate"], [e["in_529"] for e in usa["education"]]), (0.04, [0]))
                said = (await ex.read_financial_plan({"household_id": str(hh.id)}))["message"]
                check("and the reading does not name the 4% rule to India", "4%" in said, False)
                us_said = (await ToolExecutors(s, us_adviser.id, UserRole.ADVISER, us.id).read_financial_plan({"household_id": str(us_hh.id)}))["message"]
                check("while the US reading still does", "4% rule" in us_said, True)
                view = (FRONTEND / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
                check("the page shows each only where the plan carries it",
                      ("rule_of_thumb_income != null" in view, "in_529 != null" in view), (True, True))

            await section("4. US rules of thumb stay with US households", plan_by_market)

            async def before_tax():
                await balance_sheet.record_item(s, hh.id, firm_id=firm.id, by="a@in", kind="essential_spending", name="Household spending",
                                                amount=1_200_000, as_of=today, source_document="Bank statements 2026")
                sheet = await balance_sheet.for_household(s, hh.id, firm_id=firm.id)
                check("the cash flow says its surplus is before tax", sheet["cash_flow"]["surplus_basis"], "before_tax")
                said = (await ex.read_balance_sheet({"household_id": str(hh.id)}))["message"]
                check("the reading says so", "before tax" in said, True)
                check("the tool says so", "before tax" in TOOLS["read_balance_sheet"].description, True)
                card = (FRONTEND / "app" / "workspace" / "components" / "artifacts" / "BalanceSheetCard.tsx").read_text(encoding="utf-8")
                view = (FRONTEND / "components" / "views" / "FinancialPlanView.tsx").read_text(encoding="utf-8")
                check("the chip and the plan page label it", ("before tax" in card, "before tax" in view), (True, True))

            await section("5. Surplus is before tax", before_tax)

            async def meeting():
                h, _p = await home(s, firm, "Pillai")
                agent = meeting_companion.MeetingCompanionAgent()

                async def fake_narrate(ctx, **kw):
                    return kw["fallback"]

                async def no_llm(ctx, transcript):
                    return None

                meeting_companion.narrate, agent._llm_extract = fake_narrate, no_llm
                ctx = SimpleNamespace(session=s, firm=firm, subject=SimpleNamespace(type="household", id=h.id), config={})
                drafts = await agent.think(ctx, {"applicable": True, "meeting_id": None, "household_id": str(h.id),
                                                  "transcript": "Client: We want to put by 1500000 for education."})
                goals = drafts[0].payload["proposed_goals"]
                check("no date is invented for a goal the transcript did not date", [g["target_date"] for g in goals], [None])
                rec = SimpleNamespace(payload={"action_items": [], "proposed_goals": goals + [{"name": "Travel goal", "kind": "other", "target_amount": 0, "target_date": None}]},
                                      modified_payload=None, subject_id=h.id, subject_label=h.name)
                out = await agent.act(ctx, rec)
                made = (await s.execute(select(Goal).where(Goal.household_id == h.id))).scalars().all()
                check("the goal carries no invented horizon or share", [(g.assumptions, g.target_date) for g in made], [({}, None)])
                check("a goal with no amount is not created, and the note says so", (out["goals_created"], "no target amount" in out["note"]), (1, True))
                brain = await household_brain(s, h.id, firm_id=firm.id)
                row = goal_tradeoff.likelihoods(brain, today)["goals"][0]
                check("its horizon reads as assumed, and its share as not recorded", (row["horizon"], "years" in row["assumed"]), ("assumed", True))

            await section("6. A meeting follow-up invents nothing", meeting)

            async def glide():
                agent = glide_path.GlidePathAgent()

                async def fake_narrate(ctx, **kw):
                    return kw["fallback"]

                glide_path.narrate = fake_narrate
                ctx = SimpleNamespace(session=s, firm=firm)

                def brain(*goals, equity=900_000, total=1_000_000):
                    return {"household": {"id": str(uuid.uuid4()), "name": "G", "values": {}}, "goals": list(goals),
                            "totals": {"total_value": total, "by_asset_class": {"equity": equity}}, "planning": planning.assumption_set({}, "IN")}

                def goal(kind, years, amount):
                    return {"id": "g", "name": kind, "kind": kind, "target_amount": amount, "assumptions": {},
                            "target_date": (today + timedelta(days=int(365.25 * years))).isoformat()}

                got = await agent.think(ctx, {"brains": [brain(goal("education", 2, 600_000))]})
                p = got[0].payload
                check("money for a goal two years off is held outside equity", (p["target_equity_pct"], p["equity_gap"]), (0.4, 0.5))
                check("and the proposal says which money", p["short_horizon_money"], 600_000)
                got = await agent.think(ctx, {"brains": [brain(goal("property", 1, 5_000_000))]})
                check("any dated spend counts, and all the book when it needs all", got[0].payload["target_equity_pct"], 0.0)
                got = await agent.think(ctx, {"brains": [brain(goal("education", 2, 50_000), equity=900_000)]})
                check("money that is already outside equity raises nothing", got, [])
                got = await agent.think(ctx, {"brains": [brain(goal("retirement", 2, 50_000_000))]})
                check("retirement is spent over years, so it keeps the step-down", got[0].payload["target_equity_pct"], 0.26)
                got = await agent.think(ctx, {"brains": [brain(goal("education", 5, 600_000))]})
                check("three years and more keeps the step-down", got[0].payload["target_equity_pct"], 0.35)

            await section("7. No equity for money needed within three years", glide)

            async def residence():
                def st(**f):
                    return residency.tax_status({"tax_year": "2026-27", **f}, date(2026, 11, 26))

                visit = dict(days_in_india=130, indian_citizen_or_pio=True, india_income=2_000_000, days_prior_four_years=400, taxed_elsewhere=True,
                             left_for_employment_abroad=False, visiting_india=True)
                got = st(**visit)
                check("a visitor over the income line with 120 to 181 days is not ordinarily resident",
                      (got["status"], got["ordinarily_resident"]), ("resident_rnor", False))
                check("with 181 days too", st(**{**visit, "days_in_india": 181})["status"], "resident_rnor")
                check("at 182 days the ordinary tests apply",
                      st(**{**visit, "days_in_india": 182, "nonresident_years_of_prior_ten": 0, "days_prior_seven_years": 2000})["status"], "resident_ordinary")
                low = {**visit, "india_income": 800_000, "days_in_india": 150}
                check("a visitor under the line needs 182", st(**low)["status"], "non_resident")
                neither = {**low, "visiting_india": False, "nonresident_years_of_prior_ten": 0, "days_prior_seven_years": 2000}
                check("neither visiting nor leaving for work: the 60-day test applies", st(**neither)["status"], "resident_ordinary")
                unknown = {k: v for k, v in low.items() if k != "visiting_india"}
                got = st(**unknown)
                check("visiting not recorded is not assessed, and asked for",
                      (got["status"], any("visit" in n.lower() for n in got["needs"])), ("not_assessed", True))
                check("not asked where the answer does not turn on it: under 365 days in four years",
                      st(**{**unknown, "days_prior_four_years": 100})["status"], "non_resident")
                check("nor where they left for work", st(**{**unknown, "left_for_employment_abroad": True})["status"], "non_resident")
                for key in ("in_res_days_citizen_away", "in_res_days_high_income", "in_res_income_line"):
                    check(f"{key} is marked assumed", "ASSUMED" in law_figures.get(key, date(2026, 11, 26)).source, True)
                check("the tool takes the fact", "visiting_india" in [i.name for i in TOOLS["record_residency"].inputs], True)
                await ex.execute("record_residency", {"person_id": str(earner.id), "document": "Passport and travel record", "visiting_india": True,
                                                      "days_in_india": 130, "indian_citizen_or_pio": True, "india_income": 2_000_000,
                                                      "days_prior_four_years": 400, "taxed_elsewhere": True, "left_for_employment_abroad": False})
                view = await residency.person_view(s, firm, earner)
                check("recorded and read back", (view["tax"]["status"], view["facts"].get(residency.FIELD_LABELS["visiting_india"])),
                      ("resident_rnor", True))
                boot = (Path(__file__).resolve().parents[1] / "app" / "db_bootstrap.py").read_text(encoding="utf-8")
                check("a deployed database gets the column", "ADD COLUMN IF NOT EXISTS visiting_india" in boot, True)

            await section("8. Residency: the visiting rule", residence)

        db2 = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            async with db2.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(db2, expire_on_commit=False)() as s:
                async def seed():
                    from seed import india_demo, india_demo_backfill, india_planning_fixes

                    firm = await india_demo.build(s, quotes=None)
                    seeded = [h["name"] for h in india_demo.HOUSEHOLDS]
                    homes = {h.id: h.name for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()
                             if h.name in seeded}
                    goals = (await s.execute(select(Goal).where(Goal.household_id.in_(list(homes))))).scalars().all()
                    check("every seeded goal has a tier a person would have said",
                          sorted({(g.assumptions or {}).get("tier") for g in goals}, key=str), ["needs", "wants", "wishes"])
                    spending = (await s.execute(select(BalanceItem).where(BalanceItem.firm_id == firm.id,
                                                                          BalanceItem.kind == "essential_spending"))).scalars().all()
                    check("each seeded household has essential spending, said to be a demonstration record",
                          (len({i.household_id for i in spending} & set(homes)), all("demonstration" in i.source_document.lower() for i in spending)),
                          (len(seeded), True))
                    check("a second run adds nothing", await india_planning_fixes.add(s, firm), 0)
                    goal = goals[0]
                    goal.assumptions = {**goal.assumptions, "tier": "wishes"}
                    await s.flush()
                    await india_demo_backfill.reset_goals(s, firm)
                    await india_planning_fixes.add(s, firm)
                    check("a tier a person changed stays through the backfill", goal.assumptions["tier"], "wishes")
                    for g in goals:
                        g.assumptions = {k: v for k, v in g.assumptions.items() if k != "tier"}
                    for i in spending:
                        await s.delete(i)
                    await s.flush()
                    wrote = await india_planning_fixes.add(s, firm)
                    check("a firm built before this gets both", wrote, len(goals) + len(seeded))
                    source = Path(india_demo_backfill.__file__).read_text(encoding="utf-8")
                    check("the backfill runs it", "india_planning_fixes.add" in source, True)

                await section("9. The demonstration has goal tiers and essential spending", seed)
        finally:
            await db2.dispose()
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
