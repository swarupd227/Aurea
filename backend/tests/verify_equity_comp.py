"""Prove a grant is read from its statement and its schedule, values only on a real price, and sits beside net
worth; that a concentrated position is measured with what is granted, and that a plan and its paths place nothing.

Runs against a throwaway SQLite database.

    python -m tests.verify_equity_comp
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import balance_sheet, equity_comp, firm_time
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.equity_comp import ConcentrationPlan, EquityGrant
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price, TaxLot
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, equity_comp.EquityCompError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:130]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="USD", jurisdiction="US")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            s.add(h)
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee")
            s.add(ann)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann A/C", currency="USD", cash_balance=100000)
            nvd = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="NVD", name="Nvidia", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            soft = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="SOFT", name="Soft", asset_class="equity", market_type=MarketType.PUBLIC, currency="USD")
            bnd = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BND", name="Bonds", asset_class="fixed_income", market_type=MarketType.PUBLIC, currency="USD")
            s.add_all([a, nvd, soft, bnd])
            await s.flush()
            s.add_all([Price(firm_id=firm.id, instrument_id=nvd.id, as_of=today, close=100, currency="USD", source="test", is_real=True),
                       Price(firm_id=firm.id, instrument_id=soft.id, as_of=today, close=9, currency="USD", source="seed", is_real=False),
                       Price(firm_id=firm.id, instrument_id=bnd.id, as_of=today, close=10, currency="USD", source="test", is_real=True)])
            hold = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=nvd.id, quantity=5000, market_value=500000, cost_basis=100000)
            bonds = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=a.id, instrument_id=bnd.id, quantity=40000, market_value=400000, cost_basis=400000)
            s.add_all([hold, bonds])
            await s.flush()
            s.add(TaxLot(firm_id=firm.id, holding_id=hold.id, quantity=5000, cost_per_unit=20, acquired_on=today - timedelta(days=900)))
            await s.flush()
            ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
            outsider = ToolExecutors(s, adviser.id, adviser.role, other.id)

            async def card(tool, inputs, executor=ex):
                return {r["label"]: r["value"] for r in (await executor.preview_change(tool, inputs))["rows"]}

            async def count(model):
                return (await s.execute(select(func.count()).select_from(model))).scalar()

            print("\n=== Concentration before any grant ===")
            read = await ex.read_equity_comp({"household_id": str(h.id)})
            e = read["exposures"][0]
            check("a position past a fifth of the book is found", (e["symbol"], e["weight"], e["concentrated"], e["plan"]), ("NVD", 0.5, True, None))
            check("one under the line is not", [x["symbol"] for x in read["exposures"]], ["NVD"])
            paths = {p["years"]: p for p in e["scenarios"]}
            check("selling to a fifth over three years", (paths[3]["sold_a_year"], paths[3]["units_a_year"]), (100000.0, 1000.0))
            check("over seven", paths[7]["sold_a_year"], 42857.14)
            check("the gain comes from the lots", paths[3]["gain_a_year"], 80000.0)
            check("the firm's list has it, with no plan", ((await ex.read_equity_comp({}))["summary"]["no_plan"]), 1)

            print("\n=== A grant ===")
            vest = [{"on": (today - timedelta(days=30)).isoformat(), "units": 400}, {"on": (today + timedelta(days=60)).isoformat(), "units": 300}, {"on": (today + timedelta(days=500)).isoformat(), "units": 300}]
            rsu = {"person_id": str(ann.id), "kind": "rsu", "employer": "Nvidia Corp", "units": 1000, "grant_date": (today - timedelta(days=400)).isoformat(),
                   "source_document": "Plan statement, Q3 2026", "symbol": "nvd", "vesting": vest}
            shown = await card("record_equity_grant", rsu)
            check("the card names it", (shown["Person"], shown["Grant"], shown["Symbol"], shown["Units"]), ("Ann Lee", "RSU · Nvidia Corp", "NVD", "1,000"))
            check("and its schedule", "3 dates" in shown["Vesting"], True)
            await refused("an unknown kind", ex.preview_change("record_equity_grant", {**rsu, "kind": "bonus"}), "kind must be")
            await refused("no statement", ex.preview_change("record_equity_grant", {**rsu, "source_document": "x"}), "plan statement")
            await refused("no units", ex.preview_change("record_equity_grant", {**rsu, "units": 0}), "above zero")
            await refused("an option with no strike", ex.preview_change("record_equity_grant", {**rsu, "kind": "iso"}), "strike")
            await refused("more vesting than granted", ex.preview_change("record_equity_grant", {**rsu, "units": 500}), "more than the grant")
            await refused("vesting before the grant", ex.preview_change("record_equity_grant", {**rsu, "grant_date": today.isoformat()}), "before the grant")
            await refused("a schedule it cannot read", ex.preview_change("record_equity_grant", {**rsu, "vesting": [{"units": 5}]}), "needs a date")
            await refused("granted in the future", ex.preview_change("record_equity_grant", {**rsu, "grant_date": (today + timedelta(days=1)).isoformat()}), "future")
            await refused("another firm's person", ex.preview_change("record_equity_grant", {**rsu, "person_id": str(uuid.uuid4())}), "not found")
            check("showing it writes nothing", await count(EquityGrant), 0)
            read = await ex.record_equity_grant(rsu)
            g = read["grants"][0]
            check("what has vested comes from the schedule", (g["vested"], g["unvested"]), (400.0, 600.0))
            check("at the real price", (g["value_vested"], g["value_unvested"]), (40000.0, 60000.0))
            check("the next twelve months", (g["vesting_12m"], g["value_vesting_12m"], read["next_vest_on"]), (300.0, 30000.0, vest[1]["on"]))
            e = read["exposures"][0]
            check("what is granted counts toward the position", (e["held"], e["granted"], e["weight"]), (500000.0, 100000.0, 0.5455))
            check("and it stays out of net worth", (await balance_sheet.for_household(s, h.id, firm_id=firm.id))["assets"]["managed"], 1000000.0)

            print("\n=== An option, and no real price ===")
            option = {**rsu, "kind": "iso", "strike": 70, "units": 100, "vesting": None, "expires_on": (today + timedelta(days=2000)).isoformat(), "source_document": "Option statement 2026"}
            read = await ex.record_equity_grant(option)
            o = next(x for x in read["grants"] if x["kind"] == "iso")
            check("an option is worth what it is in the money", (o["strike"], o["vested"], o["value_vested"]), (70.0, 100.0, 3000.0))
            under = await ex.record_equity_grant({**option, "strike": 150, "kind": "nso", "source_document": "Option statement 2027"})
            check("under water it is worth nothing", next(x for x in under["grants"] if x["kind"] == "nso")["value_vested"], 0.0)
            priced = await ex.record_equity_grant({**rsu, "symbol": "SOFT", "vesting": None, "units": 10, "source_document": "Soft statement 2026"})
            soft_grant = next(x for x in priced["grants"] if x["symbol"] == "SOFT")
            check("a seeded price gives no value", (soft_grant["price"], soft_grant["value_vested"]), (None, None))
            private = await ex.record_equity_grant({**rsu, "symbol": None, "vesting": None, "units": 10, "employer": "Startup Ltd", "source_document": "Startup letter 2026"})
            check("nor does no symbol", next(x for x in private["grants"] if x["employer"] == "Startup Ltd")["value_vested"], None)

            print("\n=== Ending one ===")
            ending = {"grant_id": g["id"], "reason": "Forfeited on leaving"}
            shown = await card("end_equity_grant", ending)
            check("the card names it", (shown["Grant"], shown["Reason"]), ("RSU · Nvidia Corp", "Forfeited on leaving"))
            await refused("no reason", ex.preview_change("end_equity_grant", {**ending, "reason": ""}), "say why")
            await refused("another firm's", outsider.preview_change("end_equity_grant", ending), "not found")
            after = await ex.end_equity_grant(ending)
            check("it leaves the list, and stays on record", (len(after["grants"]), await count(EquityGrant)), (4, 5))
            await refused("not twice", ex.preview_change("end_equity_grant", ending), "already ended")

            print("\n=== A plan ===")
            plan = {"household_id": str(h.id), "symbol": "NVD", "method": "10b5_1", "target_weight_pct": 15, "target_date": (today + timedelta(days=1000)).isoformat(),
                    "rationale": "Reduce single-stock risk before retirement", "source_document": "Meeting note, 28 Oct 2026"}
            shown = await card("record_concentration_plan", plan)
            check("the card names the position, method and target", (shown["Position"], shown["Method"], shown["Down to"], shown["Orders"]), ("NVD", "10b5 1", "15.0%", "None placed"))
            check("and where it stands", "of the book" in shown["Now"], True)
            await refused("an unknown method", ex.preview_change("record_concentration_plan", {**plan, "method": "hope"}), "method must be")
            await refused("a target past now", ex.preview_change("record_concentration_plan", {**plan, "target_date": today.isoformat()}), "in the future")
            await refused("no reason", ex.preview_change("record_concentration_plan", {**plan, "rationale": ""}), "why this plan")
            await refused("no source", ex.preview_change("record_concentration_plan", {**plan, "source_document": ""}), "where this was agreed")
            await refused("a target that is no number", ex.preview_change("record_concentration_plan", {**plan, "target_weight_pct": "low"}), "must be a number")
            await refused("another firm's household", outsider.preview_change("record_concentration_plan", plan), "not found")
            check("showing it writes nothing", await count(ConcentrationPlan), 0)
            read = await ex.record_concentration_plan(plan)
            e = next(x for x in read["exposures"] if x["symbol"] == "NVD")
            check("the plan is read beside the position", (e["plan"]["method"], e["plan"]["reached"], e["plan"]["overdue"]), ("10b5_1", False, False))
            check("the paths go to its target", e["scenarios"][0]["sold_a_year"], 116666.67)
            check("no order is placed", await count(Order), 0)
            await ex.record_concentration_plan({**plan, "target_weight_pct": 25})
            read = await ex.read_equity_comp({"household_id": str(h.id)})
            check("the latest plan is the one in force", next(x for x in read["exposures"] if x["symbol"] == "NVD")["plan"]["target_weight"], 0.25)
            for row in (await s.execute(select(ConcentrationPlan))).scalars().all():
                row.target_date = today - timedelta(days=1)
            await s.flush()
            check("past its date and short of it is overdue", (await ex.read_equity_comp({}))["summary"]["plan_overdue"], 1)

            print("\n=== Scope ===")
            check("another firm sees none of it", (await outsider.read_equity_comp({}))["households"], [])
            await refused("nor one household of it", outsider.read_equity_comp({"household_id": str(h.id)}), "not found")
            check("what is not modelled is said", "amt_on_isos" in read["not_modelled"], True)
            check("a date is a date", isinstance(date.fromisoformat(read["as_of"]), date), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
