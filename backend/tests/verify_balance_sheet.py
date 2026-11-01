"""Prove net worth: it works only from what is on file, says when it is assets only, counts nothing twice,
adds no currency it cannot convert, feeds debts into protection, and a firm sees only its own.

Runs against a throwaway SQLite database.

    python -m tests.verify_balance_sheet
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import balance_sheet as api
from app.aurea_core import balance_sheet as bs
from app.aurea_core import firm_time, protection
from app.aurea_core.graph import household_brain
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, anthropic_tool_schema, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.balance_sheet import BalanceItem, side_of
from app.models.enums import MarketType, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import HeldAwayAsset, OnboardingCase
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm
from seed import balance_sheet_backfill

PASS, FAIL = [], []
DOC = "Bank statement, 30 Oct 2026"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except (bs.BalanceSheetError, ExecutorError) as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:90]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def forbidden(label: str, coro, code: int = 403) -> None:
    try:
        await coro
    except HTTPException as e:
        check(label, e.status_code, code)
    else:
        check(label, "allowed", code)


def pure() -> None:
    print("\n=== Reading what is on file ===")
    def item(kind, amount, person=None, payment=None):
        return SimpleNamespace(kind=kind, side=side_of(kind), amount=amount, person_id=person, annual_payment=payment)

    def who(pid, held=0, pots=0, income=None, kid=False):
        return SimpleNamespace(id=pid, profile={"held_away": held, "tax": {"pension_pot": pots, **({"annual_income": income} if income is not None else {})}},
                               is_next_gen=kid)

    a, b = uuid.uuid4(), uuid.uuid4()
    people = [who(a, held=100, pots=50), who(b, held=200, pots=70)]
    got = bs.assets_on_file(people, [])
    check("with nothing recorded, the profiles stand", (got["held_away"], got["retirement_accounts"]), (300.0, 120.0))
    got = bs.assets_on_file(people, [item("held_away_investment", 150, a)])
    check("a recorded figure replaces that person's profile, not the other's", got["held_away"], 350.0)
    got = bs.assets_on_file(people, [item("held_away_investment", 150)])
    check("a joint figure replaces everyone's", got["held_away"], 150.0)
    got = bs.assets_on_file(people, [item("pension", 500, b)])
    check("a recorded pension replaces that person's pots", got["retirement_accounts"], 550.0)
    got = bs.assets_on_file(people, [item("property", 900), item("business", 40), item("other_asset", 5), item("cash", 10)])
    check("each kind lands where it should", (got["property"], got["business"], got["other"], got["cash"]), (900.0, 40.0, 5.0, 10.0))
    check("a home and a business cannot pay a claim", bs.liquid_of(got), 300.0 + 120.0 + 10.0)
    debts = [item("mortgage", 400), item("personal_loan", 30, a), item("credit_card", 5, b)]
    check("all that is owed", bs.debts_of(debts), 435.0)
    check("one person's: their own and the joint ones", (bs.debts_of(debts, a), bs.debts_of(debts, b)), (430.0, 405.0))
    check("no debts is nothing", bs.debts_of([]), 0)

    print("\n=== Cash flow ===")
    flow = bs._cash_flow([who(a, income=100000), who(b, income=50000)],
                         [item("essential_spending", 60000), item("discretionary_spending", 20000), item("mortgage", 400, payment=30000)])
    check("what comes in less what goes out", (flow["status"], flow["income"], flow["expenses"], flow["debt_payments"], flow["surplus"]),
          ("assessed", 150000.0, 80000.0, 30000.0, 40000.0))
    flow = bs._cash_flow([who(a, income=100000)], [item("mortgage", 400)])
    check("no expenses on file is not assessed, with no figure", (flow["status"], flow["missing"], flow["surplus"]), ("not_assessed", ["expenses"], None))
    check("a debt with no payment is counted as unknown", flow["payments_missing"], 1)
    flow = bs._cash_flow([who(a)], [item("essential_spending", 1)])
    check("no income on file is not assessed", (flow["status"], flow["missing"]), ("not_assessed", ["income"]))
    flow = bs._cash_flow([who(a, income=100000), who(b)], [item("essential_spending", 1)])
    check("one income unknown is said", flow["income_partial"], True)
    flow = bs._cash_flow([who(a, income=100000), who(b, kid=True)], [item("essential_spending", 1)])
    check("a child is not a missing income", flow["income_partial"], False)
    flow = bs._cash_flow([who(a, income=0)], [item("essential_spending", 5000)])
    check("zero is a stated income", (flow["status"], flow["surplus"]), ("assessed", -5000.0))

    print("\n=== Protection counts a debt ===")
    plain = protection.assess(age=40, income=100000, other_income=0, has_dependants=True, retirement_age=65, goals_due=0, assets=0)
    owed = protection.assess(age=40, income=100000, other_income=0, has_dependants=True, retirement_age=65, goals_due=0, assets=0, debts=400000)
    check("a debt adds to what survivors would need", round(owed["death"]["need"] - plain["death"]["need"], 2), 400000.0)
    check("and is reported on its own", (owed["death"]["debts"], plain["death"]["debts"]), (400000.0, 0.0))
    check("it does not change the disability need", owed["disability"]["need"], plain["disability"]["need"])
    alone = protection.assess(age=40, income=100000, other_income=0, has_dependants=False, retirement_age=65, goals_due=0, assets=0, debts=400000)
    check("with nobody left behind, it is not a survivor's need", alone["death"]["need"], 0.0)

    print("\n=== The tools ===")
    record, end = TOOLS["record_balance_item"], TOOLS["end_balance_item"]
    check("recording stops at a Confirm card", record.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("ending too", end.change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    writers = {UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS}
    check("for the people who record protection details", (record.roles_required, end.roles_required), (writers, writers))
    check("reading changes nothing", TOOLS["read_balance_sheet"].change_state, ToolChangeState.NO)
    check("a client holds none of them", validate_tool_call(UserRole.CLIENT, "read_balance_sheet", {})[0], False)
    check("the view opens beside the conversation", OPEN_VIEWS["net_worth"][0], "/studio/net-worth")
    props = anthropic_tool_schema(record)["input_schema"]["properties"]
    check("a figure reaches the model as a number", props["amount"]["type"], "number")


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()

            def user(role, name, f=firm):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name}@t", full_name=name, role=role, hashed_password="x")
                s.add(u)
                return u

            sophie, priya, olive, david, rae, wei = (user(UserRole.ADVISER, "sophie"), user(UserRole.PARAPLANNER, "priya"),
                                                     user(UserRole.OPERATIONS, "olive"), user(UserRole.COMPLIANCE, "david"),
                                                     user(UserRole.RESEARCH_CIO, "rae"), user(UserRole.CLIENT, "wei"))
            outsider = user(UserRole.ADVISER, "ola", other)
            eq = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="EQ", name="EQ", asset_class="equity",
                            market_type=MarketType.PUBLIC, currency="NZD")
            s.add(eq)
            await s.flush()
            today = firm_time.today(firm)

            async def home(name, *people, managed=0.0, f=firm):
                h = Household(id=uuid.uuid4(), firm_id=f.id, name=name)
                s.add(h)
                await s.flush()
                out = []
                for label, dob, profile in people:
                    p = Person(id=uuid.uuid4(), firm_id=f.id, household_id=h.id, full_name=label, date_of_birth=dob, profile=profile)
                    s.add(p)
                    out.append(p)
                await s.flush()
                if managed and out:
                    m = Mandate(id=uuid.uuid4(), firm_id=f.id, person_id=out[0].id, name=f"{name} mandate", mandate_type="advisory",
                                suitability={}, constraints={})
                    s.add(m)
                    await s.flush()
                    a = Account(id=uuid.uuid4(), firm_id=f.id, mandate_id=m.id, name=f"{name} A/C", currency="NZD", cash_balance=0)
                    s.add(a)
                    await s.flush()
                    s.add(Holding(firm_id=f.id, account_id=a.id, instrument_id=eq.id, quantity=1, market_value=managed, cost_basis=managed))
                    await s.flush()
                return (h, *out)

            def write(h, kind="mortgage", name="ANZ", amount=400000.0, f=firm, **kw):
                args = {"as_of": today - timedelta(days=2), "source_document": DOC, "by": "sophie@t", **kw}
                return bs.record_item(s, h.id, firm_id=f.id, kind=kind, name=name, amount=amount, **args)

            def prepare(h, kind="mortgage", name="ANZ", amount=400000.0, **kw):
                args = {"as_of": today - timedelta(days=2), "source_document": DOC, **kw}
                return bs.prepare_item(s, h.id, firm_id=firm.id, kind=kind, name=name, amount=amount, **args)

            async def items():
                return (await s.execute(select(func.count()).select_from(BalanceItem))).scalar()

            print("\n=== A household with nothing recorded ===")
            ann, a1, a2 = await home("Ann", ("Ann Lee", date(1980, 5, 1), {"held_away": 100000, "tax": {"annual_income": 120000, "pension_pot": 50000}}),
                                     ("Bo Lee", date(1982, 5, 1), {"tax": {"annual_income": 60000}}), managed=500000.0)
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("the book and what profiles say", (v["assets"]["managed"], v["assets"]["held_away"], v["assets"]["retirement_accounts"], v["assets"]["total"]),
                  (500000.0, 100000.0, 50000.0, 650000.0))
            check("it reads assets only, not debt free", (v["basis"], v["liabilities"]["total"], v["net_worth"]), ("assets_only", 0, 650000.0))
            check("cash flow is not assessed", (v["cash_flow"]["status"], v["cash_flow"]["missing"], v["cash_flow"]["surplus"]), ("not_assessed", ["expenses"], None))
            brain = await household_brain(s, ann.id, firm_id=firm.id)
            check("held-away reaches the household's totals", brain["totals"]["held_away"], 100000.0)
            check("the managed total is untouched, so nothing billed on it moves", brain["totals"]["total_value"], 500000.0)
            before = await protection.for_household(s, ann.id, firm_id=firm.id)
            check("protection reads the same assets", (before["assets"]["held_away"], before["assets"]["retirement_accounts"], before["assets"]["cash"], before["assets"]["total"]),
                  (100000.0, 50000.0, 0.0, 650000.0))
            check("and says debts are not missing from its method", "debts" in before["not_modelled"], False)
            check("but that none are on file", before["debts_on_file"], 0)

            print("\n=== Refused before anything is written ===")
            await refused("an unknown kind", prepare(ann, kind="yacht"), "unknown kind")
            await refused("no name", prepare(ann, name=" "), "name the institution")
            await refused("no amount", prepare(ann, amount=None), "give the amount")
            await refused("zero", prepare(ann, amount=0), "give the amount")
            await refused("a negative amount", prepare(ann, amount=-5), "cannot be negative")
            await refused("an amount that is not a number", prepare(ann, amount="lots"), "must be a number")
            await refused("no date", prepare(ann, as_of=None), "date the figure was true")
            await refused("a date to come", prepare(ann, as_of=today + timedelta(days=1)), "in the future")
            await refused("no document", prepare(ann, source_document=" "), "which document")
            await refused("a payment on an asset", prepare(ann, kind="property", name="Home", annual_payment=10), "belongs to a debt")
            await refused("a rate over 100%", prepare(ann, interest_rate=1.5), "between 0% and 100%")
            await refused("a debt already over", prepare(ann, ends_on=today - timedelta(days=1)), "is not owed")
            await refused("a bad currency", prepare(ann, currency="DOLLARS"), "three-letter")
            theirs, t1 = await home("Theirs", ("Tom", None, {}), f=other)
            await refused("someone from another household", prepare(ann, person_id=t1.id), "not in this household")
            await refused("another firm's household", bs.prepare_item(s, ann.id, firm_id=other.id, kind="mortgage", name="ANZ", amount=1, as_of=today, source_document=DOC), "not found")
            check("nothing was written", await items(), 0)

            print("\n=== Recording ===")
            out = await write(ann, annual_payment=30000, interest_rate=0.065, ends_on=today + timedelta(days=3000))
            check("a debt lowers net worth", (out["net_worth"], out["liabilities"]["total"], out["liabilities"]["by_kind"]), (250000.0, 400000.0, {"mortgage": 400000.0}))
            check("and the figure now reads as both sides", out["basis"], "assets_and_liabilities")
            first = out["item_id"]
            check("its terms are kept", (out["items"][0]["annual_payment"], out["items"][0]["interest_rate"], out["items"][0]["recorded_by"]), (30000.0, 0.065, "sophie@t"))
            check("the ledger has it", (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == "balance_item_recorded"))).scalar(), 1)
            await refused("the same debt twice", prepare(ann), "already on file")
            await write(ann, kind="personal_loan", name="ANZ", amount=10000)
            ok = await prepare(ann, person_id=a1.id)
            check("one in someone's own name is a different item", ok["person"].full_name, "Ann Lee")
            out = await write(ann, kind="property", name="Family home", amount=900000)
            check("a home adds to net worth", (out["assets"]["property"], out["net_worth"]), (900000.0, 1140000.0))
            check("but not to what could be drawn on", out["liquid"], 650000.0)
            out = await write(ann, kind="essential_spending", name="Living costs", amount=70000)
            check("expenses complete the cash flow", (out["cash_flow"]["status"], out["cash_flow"]["surplus"]), ("assessed", 80000.0))
            check("an expense does not touch net worth", out["net_worth"], 1140000.0)
            check("a payment left off a loan is said", out["cash_flow"]["payments_missing"], 1)

            print("\n=== A new figure ===")
            out = await write(ann, name="ANZ", amount=380000, replaces_id=uuid.UUID(first))
            check("the new figure counts", out["liabilities"]["by_kind"]["mortgage"], 380000.0)
            kept = [i for i in out["items"] if i["kind"] == "mortgage"][0]
            check("a new balance is not a new loan: its terms carry over", (kept["annual_payment"], kept["interest_rate"], kept["ends_on"] is not None), (30000.0, 0.065, True))
            check("so the cash flow does not move", out["cash_flow"]["debt_payments"], 30000.0)
            restated = await prepare(ann, name="ANZ", amount=370000, replaces_id=uuid.UUID(kept["id"]), annual_payment=28000)
            check("a term that is restated wins", (restated["annual_payment"], restated["interest_rate"]), (28000.0, 0.065))
            old = await s.get(BalanceItem, uuid.UUID(first))
            check("the old one stays on file, ended", (old.status, "Replaced" in old.ended_reason), ("ended", True))
            check("and is shown as ended", [e["id"] for e in out["ended"]], [first])
            await refused("an item already replaced", prepare(ann, replaces_id=uuid.UUID(first)), "not on file")
            await refused("a debt cannot replace an asset", prepare(ann, kind="mortgage", name="Other lender", replaces_id=(await s.execute(select(BalanceItem).where(BalanceItem.kind == "property"))).scalar_one().id), "same type")

            print("\n=== Counting nothing twice ===")
            out = await write(ann, kind="held_away_investment", name="Other broker", amount=120000, person_id=a1.id)
            check("a recorded holding replaces the profile's figure", out["assets"]["held_away"], 120000.0)
            check("the brain agrees", (await household_brain(s, ann.id, firm_id=firm.id))["totals"]["held_away"], 120000.0)
            out = await write(ann, kind="pension", name="KiwiSaver", amount=80000, person_id=a1.id)
            check("a recorded pension replaces the pots", out["assets"]["retirement_accounts"], 80000.0)
            check("the wallet-share profile is left as it was", a1.profile["held_away"], 100000)

            print("\n=== Another currency ===")
            out = await write(ann, kind="held_away_investment", name="US broker", amount=99999, currency="usd", person_id=a2.id)
            check("it is listed, not added", (out["assets"]["held_away"], [i["name"] for i in out["not_converted"]]), (120000.0, ["US broker"]))
            check("and marked in the code it came in", out["not_converted"][0]["currency"], "USD")
            ready = await prepare(ann, kind="cash", name="Euro account", amount=5, currency="EUR")
            check("the card can say so", ready["not_converted"], True)

            print("\n=== Protection reads the debts ===")
            view = await protection.for_household(s, ann.id, firm_id=firm.id)
            ann_row = next(p for p in view["people"] if p["name"] == "Ann Lee")
            bo_row = next(p for p in view["people"] if p["name"] == "Bo Lee")
            check("the joint mortgage and joint loan count for each", (ann_row["death"]["debts"], bo_row["death"]["debts"]), (390000.0, 390000.0))
            check("they are on file", view["debts_on_file"], 2)
            check("a home does not close a gap", view["assets"]["total"], 500000.0 + 120000.0 + 80000.0)
            await write(ann, kind="credit_card", name="Visa", amount=5000, person_id=a2.id)
            view = await protection.for_household(s, ann.id, firm_id=firm.id)
            ann_row = next(p for p in view["people"] if p["name"] == "Ann Lee")
            bo_row = next(p for p in view["people"] if p["name"] == "Bo Lee")
            check("a debt in one name counts only for them", (ann_row["death"]["debts"], bo_row["death"]["debts"]), (390000.0, 395000.0))

            print("\n=== Ending ===")
            visa = (await s.execute(select(BalanceItem).where(BalanceItem.name == "Visa"))).scalar_one()
            await refused("no reason", bs.prepare_end(s, visa.id, firm_id=firm.id, reason=" "), "say why")
            await refused("another firm's item", bs.prepare_end(s, visa.id, firm_id=other.id, reason="Repaid"), "not found")
            out = await bs.end_item(s, visa.id, firm_id=firm.id, reason="Repaid in full", by="sophie@t")
            check("it stops counting", out["liabilities"]["by_kind"].get("credit_card"), None)
            check("and is kept with why", (visa.status, visa.ended_reason, visa.ended_by), ("ended", "Repaid in full", "sophie@t"))
            await refused("once is enough", bs.end_item(s, visa.id, firm_id=firm.id, reason="Again", by="x"), "already ended")

            print("\n=== The book, and firm scope ===")
            cal, c1 = await home("Cal", ("Cal Ng", None, {}), managed=2000000.0)
            book = await bs.for_firm(s, firm.id)
            check("the largest net worth first", [r["household"] for r in book["households"]], ["Cal", "Ann"])
            check("the summary adds up", (book["summary"]["with_liabilities"], book["summary"]["assets_only"], book["summary"]["cash_flow_assessed"], book["summary"]["not_converted"]), (1, 1, 1, 1))
            check("another firm sees none of it", await bs.for_household(s, ann.id, firm_id=other.id), None)
            check("nor in its book", [r["household"] for r in (await bs.for_firm(s, other.id))["households"]], ["Theirs"])

            print("\n=== Carried from onboarding ===")
            jay, j1 = await home("Jay", ("Jay Roe", None, {}))
            case = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Jay", materialized={"household_id": str(jay.id)})
            gone = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Gone", materialized={"household_id": str(uuid.uuid4())})
            s.add_all([case, gone])
            await s.flush()
            s.add_all([HeldAwayAsset(firm_id=firm.id, case_id=case.id, institution="Other Bank", account_type="brokerage", approx_value=250000, currency="NZD"),
                       HeldAwayAsset(firm_id=firm.id, case_id=case.id, institution="Super Co", account_type="pension", approx_value=90000, currency="NZD"),
                       HeldAwayAsset(firm_id=firm.id, case_id=case.id, institution="Beach house", account_type="property", approx_value=700000, currency="NZD"),
                       HeldAwayAsset(firm_id=firm.id, case_id=case.id, institution="Unknown", account_type="brokerage", approx_value=None, currency="NZD")])
            await s.flush()
            check("what was declared with a value is carried", await balance_sheet_backfill.apply(s), 3)
            v = await bs.for_household(s, jay.id, firm_id=firm.id)
            check("onto the right lines", (v["assets"]["held_away"], v["assets"]["retirement_accounts"], v["assets"]["property"]), (250000.0, 90000.0, 700000.0))
            check("marked as declared", all("Declared at onboarding" in i["source_document"] for i in v["items"]), True)
            check("running it again changes nothing", await balance_sheet_backfill.apply(s), 0)
            check("no debt is invented", v["basis"], "assets_only")

            print("\n=== Who may do what ===")
            await api.ReaderDep.dependency(david)
            await forbidden("a client is refused", api.ReaderDep.dependency(wei))
            await forbidden("research is refused", api.ReaderDep.dependency(rae))
            body = api.ItemIn(kind="cash", name="Savings", amount=1000, as_of=today, source_document=DOC)
            await forbidden("compliance reads and does not record", api.record(cal.id, body, david, firm, s))
            await forbidden("another firm's household is not found", api.record(cal.id, body, outsider, other, s), 404)
            await forbidden("nor read", api.read_household(cal.id, outsider, other, s), 404)
            await forbidden("a refusal is a conflict", api.record(cal.id, api.ItemIn(kind="cash", name="Savings", amount=1000, as_of=today), priya, firm, s), 409)
            check("the rights go to the page", ((await api.read(david, firm, s))["can_record"], (await api.read(olive, firm, s))["can_record"]), (False, True))
            out = await api.record(cal.id, body, olive, firm, s)
            check("operations records it", out["recorded"], "Savings")
            await forbidden("compliance does not end one", api.end(uuid.UUID(out["item_id"]), api.EndIn(reason="Closed"), david, firm, s))

            print("\n=== In the conversation ===")
            ex = ToolExecutors(s, sophie.id, sophie.role, firm.id)
            args = {"household_id": str(cal.id), "kind": "mortgage", "name": "BNZ", "amount": 650000, "as_of": today.isoformat(),
                    "source_document": "Loan statement, Oct 2026", "person_id": str(c1.id), "annual_payment": 42000, "interest_rate": 6.25,
                    "ends_on": (today + timedelta(days=4000)).isoformat()}
            before_count = await items()
            preview = {r["label"]: r["value"] for r in (await ex.preview_change("record_balance_item", args))["rows"]}
            check("the card names the client and the debt", (preview["Client"], preview["Type"], preview["Name"], preview["Whose"]),
                  ("Cal", "Mortgage", "BNZ", "Cal Ng"))
            check("and every figure", ("650,000" in preview["Amount"], "42,000" in preview["Paid a year"], preview["Interest rate"]), (True, True, "6.25%"))
            check("and where it came from", preview["Document"], "Loan statement, Oct 2026")
            check("and what it does to net worth", "becomes" in preview["Net worth"], True)
            check("a preview writes nothing", await items(), before_count)
            shown = {r["label"]: r["value"] for r in (await ex.preview_change("record_balance_item", {**args, "name": "Euro loan", "currency": "EUR"}))["rows"]}
            check("another currency is said on the card", shown["Counted"], "Not added: not converted")
            await refused("the card refuses what recording would", ex.preview_change("record_balance_item", {**args, "source_document": ""}), "which document")
            await refused("a date it cannot read", ex.preview_change("record_balance_item", {**args, "as_of": "yesterday"}), "YYYY-MM-DD")
            await refused("a role that slips past is refused", ToolExecutors(s, david.id, david.role, firm.id).record_balance_item(args), "cannot change")
            done = await ex.record_balance_item(args)
            check("confirming records it at the rate given", (done["items"][-1]["interest_rate"] if done["items"][-1]["name"] == "BNZ" else [i for i in done["items"] if i["name"] == "BNZ"][0]["interest_rate"]), 0.0625)
            check("and says where it leaves the client", "Cal" in done["message"], True)
            read = await ex.read_balance_sheet({})
            check("reading the book", "assets only" in read["message"], True)
            one = await ex.read_balance_sheet({"household_id": str(cal.id)})
            check("reading a household", one["basis"], "assets_and_liabilities")
            await refused("another firm cannot read it", ToolExecutors(s, outsider.id, outsider.role, other.id).read_balance_sheet({"household_id": str(cal.id)}), "not found")
            bnz = [i for i in one["items"] if i["name"] == "BNZ"][0]
            ending = {"item_id": bnz["id"], "reason": "Repaid on sale"}
            shown = {r["label"]: r["value"] for r in (await ex.preview_change("end_balance_item", ending))["rows"]}
            check("ending names what goes", (shown["Client"], shown["Item"], "650,000" in shown["Amount"], shown["Reason"]), ("Cal", "BNZ", True, "Repaid on sale"))
            await refused("with no reason, no card", ex.preview_change("end_balance_item", {**ending, "reason": ""}), "say why")
            out = await ex.end_balance_item(ending)
            check("confirming ends it", [i for i in out["items"] if i["name"] == "BNZ"], [])
            check("Astra opens it beside the conversation", (await ex.open_view({"view": "net_worth"}))["view"], "/studio/net-worth")
            await refused("not for a role that does not read it", ToolExecutors(s, rae.id, rae.role, firm.id).open_view({"view": "net_worth"}), "not one this role opens")
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
