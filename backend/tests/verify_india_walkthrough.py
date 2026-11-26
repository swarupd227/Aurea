"""What the walkthrough of the Indian demonstration found (B-370 to B-375).

Builds the Indian firm from seed/india_demo.py with invented prices and no network, on a throwaway SQLite database, and checks that:

  - a client's "Own and owe" lines add up to the total, with held-away money as one figure and no holding named;
  - each direct-against-regular row and each fund order says whose it is;
  - a reminder task carries its due day and source with no "due today" and one full stop, and the older open ones are rewritten once;
  - a firm-wide question is answered by one call of each India read, with household names, and no household to give;
  - the Licences & SEBI page asks the database far fewer questions and says the same thing;
  - weeks later the backfill's re-dating puts the demonstration's dated scenarios back, leaves history alone and writes nothing twice,
    and the live proof still passes.

    python -m tests.verify_india_walkthrough
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import canvas
from app.aurea_core import (complaints, firm_time, governance, india_reminders, kyc_india, licences, mf_orders, tax_intelligence)
from app.core.db import Base, utcnow
from app.core.read_once import read_once
from app.core.tools import TOOLS, validate_tool_call
from app.llm.executors import ToolExecutors
from app.models.balance_sheet import BalanceItem
from app.models.engagement import Task
from app.models.enums import TaskStatus, UserRole
from app.models.graph import Account, Household, Person
from app.models.held_away import HeldAwayHolding
from app.models.identity import User
from app.models.india_batch_a import LrsRemittance
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Order
from app.tools_live import india_live_proof as proof
from seed import india_batch_c, india_demo, india_redate

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


def shown_total(sheet: dict) -> float:
    """The lines the "Own and owe" panel shows, added up as a reader would."""
    lines = float(sheet["assets"]["managed"]) + sum(u["amount"] for u in sheet.get("unitemised", []))
    return round(lines + sum(-i["amount"] if i["side"] == "liability" else i["amount"] for i in sheet["items"]
                             if i["side"] != "expense" and i["currency"] == sheet["currency"]), 2)


async def user_of(s, firm, role) -> User:
    return (await s.execute(select(User).where(User.firm_id == firm.id, User.role == role).order_by(User.email))).scalars().first()


async def client_lines(s, firm) -> None:
    print("\n=== A client's Own and owe adds up ===")
    client = await user_of(s, firm, UserRole.CLIENT)
    held = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.status == "current"))).scalars().all()
    sheet = await canvas.canvas_net_worth(household_id=None, user=client, firm=firm, db=s)
    check("the demonstration household holds money elsewhere", sheet["assets"]["held_away"] > 0, True)
    check("the lines shown add up to the total", abs(shown_total(sheet) - sheet["net_worth"]) < 0.05, True)
    check("held-away money is one line, named Held elsewhere", [u["label"] for u in sheet.get("unitemised", []) if u["bucket"] == "held_away"], ["Held elsewhere"])
    text = json.dumps(sheet, default=str)
    check("and no holding's institution or document is told", [h.institution for h in held if h.institution in text or (h.source_document or "~") in text], [])
    mine = await ToolExecutors(s, client.id, client.role, firm.id).execute("read_my_net_worth", {})
    check("the client's assistant reads the same lines", abs(shown_total(mine) - mine["net_worth"]) < 0.05, True)
    # A total the lines stand in for, an item in dollars, a recorded pension and a debt.
    line = next(h for h in held if h.person_id is not None and str(h.household_id) == sheet["household_id"])
    for side, kind, name, amount, currency, person in (
            ("asset", "held_away_investment", line.institution, 999_999, "INR", line.person_id),
            ("asset", "pension", "A pension abroad", 5_000, "USD", None),
            ("asset", "pension", "EPF", 450_000, "INR", line.person_id),
            ("liability", "mortgage", "Home loan", 2_000_000, "INR", None)):
        s.add(BalanceItem(firm_id=firm.id, household_id=line.household_id, person_id=person, side=side, kind=kind, name=name, amount=amount,
                          currency=currency, as_of=firm_time.today(firm), source_document="Statement, filed", recorded_by="test"))
    await s.flush()
    sheet = await canvas.canvas_net_worth(household_id=None, user=client, firm=firm, db=s)
    check("with a replaced total, a foreign item, a pension and a debt it still adds up", abs(shown_total(sheet) - sheet["net_worth"]) < 0.05, True)
    check("the replaced total is not listed twice", [i["name"] for i in sheet["items"] if i["amount"] == 999_999], [])
    check("the dollar item is listed apart", ([i["name"] for i in sheet["not_converted"]], "A pension abroad" in [i["name"] for i in sheet["items"]]), (["A pension abroad"], False))


async def whose(s, firm) -> None:
    print("\n=== Each row says whose it is ===")
    compliance = await user_of(s, firm, UserRole.COMPLIANCE)
    ex = ToolExecutors(s, compliance.id, compliance.role, firm.id)
    # Two households hold the same regular plan, as the live demonstration came to: the page showed the scheme twice.
    fund = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "PPFAS-FLEXI-REGULAR"))).scalar_one()
    s.add(Price(firm_id=firm.id, instrument_id=fund.id, as_of=firm_time.today(firm), close=100, currency="INR", source="amfi", is_real=True))
    for name, units in (("Karthik Iyer demat", 200), ("Rajesh Sharma demat", 100)):
        account = (await s.execute(select(Account).where(Account.firm_id == firm.id, Account.name == name))).scalar_one()
        s.add(Holding(firm_id=firm.id, account_id=account.id, instrument_id=fund.id, quantity=units, market_value=units * 100, cost_basis=units * 90))
    await s.flush()
    rows = (await ex.execute("read_distribution_separation", {}))["comparison"]["holdings"]
    check("the scheme shows twice, at two figures", sorted(r["yearly_difference"] for r in rows), [72.0, 144.0])
    check("and each row names its household, holder and account", sorted((r["yearly_difference"], r.get("household"), r.get("holder"), r.get("account")) for r in rows),
          [(72.0, "The Sharma Family", "Rajesh Sharma", "Rajesh Sharma demat"), (144.0, "The Iyer Family", "Karthik Iyer", "Karthik Iyer demat")])


async def firm_wide(s, firm) -> None:
    print("\n=== One call answers a firm-wide question ===")
    adviser = await user_of(s, firm, UserRole.ADVISER)
    ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
    # A filled fund order nobody has recorded a rail for.
    karthik = (await s.execute(select(Account).where(Account.firm_id == firm.id, Account.name == "Karthik Iyer demat"))).scalar_one()
    fund = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == india_batch_c.FUND))).scalar_one()
    await india_batch_c._order(s, firm, karthik, fund, firm_time.today(firm) - timedelta(days=3), 5_000, 70)
    await s.flush()
    mf = await ex.execute("read_mf_orders", {})
    check("fund orders: the whole firm, with its breaks and not-assessed", (mf.get("scope"), mf["breaks"], mf["not_assessed"]), ("firm", 1, 1))
    check("each order names its household", [o for o in mf["orders"] if not o.get("household")], [])
    check("the order with no rail is listed, with whose it is", [(o["household"], o["account"]) for o in mf.get("without_rail", [])], [("The Iyer Family", "Karthik Iyer demat")])
    iyer = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Iyer Family"))).scalar_one()
    sharma = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Sharma Family"))).scalar_one()
    one = await ex.execute("read_mf_orders", {"household_id": str(iyer.id)})
    none = await ex.execute("read_mf_orders", {"household_id": str(sharma.id)})
    check("the per-household form stands", (one["scope"], len(one["orders"]), none["orders"], none["without_rail"]), ("household", 4, [], []))
    check("the description sends a firm-wide question there once", "ONCE with no household_id" in TOOLS["read_mf_orders"].description, True)
    homes = (await s.execute(select(func.count()).select_from(Household).where(Household.firm_id == firm.id))).scalar_one()
    for key in ("read_ticket_gates", "read_tax_habits", "read_agreement_contents"):
        check(f"{key} takes no household", validate_tool_call(UserRole.ADVISER, key, {})[0], True)
    gates = await ex.execute("read_ticket_gates", {})
    check("ticket gates: every household, each named", (len(gates["households"]), all(h["household"] for h in gates["households"])), (homes, True))
    check("a household's row says what its own reading says",
          next(h["open"] for h in gates["households"] if h["household"] == "The Iyer Family"), (await ex.execute("read_ticket_gates", {"household_id": str(iyer.id)}))["open"])
    habits = await ex.execute("read_tax_habits", {})
    own = await ex.execute("read_tax_habits", {"household_id": str(sharma.id)})
    check("tax habits: the firm's, each row named", all(r.get("household") for k in ("gain_harvest", "dividends", "gifts") for r in habits[k]), True)
    check("and a household's rows are the same ones", len([g for g in habits["gifts"] if g["household"] == "The Sharma Family"]), len(own["gifts"]))
    agreements = await ex.execute("read_agreement_contents", {})
    check("agreements: each household on the adviser's track, by name", sorted(h["household"] for h in agreements["households"])[:2], ["The Banerjee Household", "The D'Souza Household"])
    other = Firm(slug="other", name="Other", jurisdiction="IN", base_currency="INR", settings=dict(firm.settings))
    s.add(other)
    await s.flush()
    check("another firm reads none of it", ((await mf_orders.overview(s, other))["orders"], (await mf_orders.overview(s, other))["without_rail"]), ([], []))


async def reminders(s, firm) -> None:
    print("\n=== A reminder task reads true on any day ===")
    item = {"label": "x", "due_on": "2026-11-25", "days": 0, "source": "KRA and CKYC lookups are not connected.", "assumed": "the day."}
    check("due day and source, one full stop, nothing relative", india_reminders._detail(item),
          "Due 2026-11-25. Source: KRA and CKYC lookups are not connected. Assumed: the day.")
    raised = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.source == india_reminders.SOURCE))).scalars().all()
    check("the seeded tasks say no 'due today' or 'overdue by', and end in one full stop",
          [t.detail for t in raised if any(w in t.detail for w in ("due today", "overdue by", "(due in", ".."))], [])
    old = "Due 2026-11-25 (due today). Source: Lookups are not connected.. The household has no adviser on record: assign one."
    late = "Due 2026-11-01 (overdue by 25 day(s)). Source: A circular. Assumed: the day."
    new = "Due 2026-11-25. Source: Lookups are not connected. The household has no adviser on record: assign one."
    a = Task(firm_id=firm.id, title="India: old open", detail=old, status=TaskStatus.OPEN, source=india_reminders.SOURCE, due_date=date(2026, 11, 25))
    b = Task(firm_id=firm.id, title="India: old done", detail=late, status=TaskStatus.DONE, source=india_reminders.SOURCE, due_date=date(2026, 11, 1))
    c = Task(firm_id=firm.id, title="Someone's own", detail=late, status=TaskStatus.OPEN, source="manual", due_date=date(2026, 11, 1))
    s.add_all([a, b, c])
    await s.flush()
    count = (await s.execute(select(func.count()).select_from(Task))).scalar_one()
    changed = await india_reminders.correct_open_details(s, firm.id)
    check("the open one this job raised is rewritten", (changed, a.detail), (1, new))
    check("a closed one and another's task are left", (b.detail, b.status, c.detail), (late, TaskStatus.DONE, late))
    check("a second run changes nothing and no task is added", (await india_reminders.correct_open_details(s, firm.id), (await s.execute(select(func.count()).select_from(Task))).scalar_one()), (0, count))


async def page(Session, db, firm_id) -> None:
    print("\n=== The Licences & SEBI page asks less ===")
    sent = {"n": 0}

    def count(*_):
        sent["n"] += 1

    event.listen(db.sync_engine, "before_cursor_execute", count)
    try:
        async with Session() as s:
            firm = await s.get(Firm, firm_id)
            sent["n"] = 0
            once = await licences.overview(s, firm)
            asked = sent["n"]
        async with Session() as s:
            firm = await s.get(Firm, firm_id)
            plain = await licences._overview(s, firm)
    finally:
        event.remove(db.sync_engine, "before_cursor_execute", count)
    print(f"  ({asked} statements; 1,191 before)")
    check("fewer than half the statements it sent", asked < 600, True)

    def settled(view: dict) -> str:
        view = json.loads(json.dumps(view, default=str))
        view.get("calendar", {}).pop("licences", None)      # named from a set, so their order is any order
        return json.dumps(view, sort_keys=True)

    check("and the same page", settled(once) == settled(plain), True)
    async with Session() as s:
        async with read_once(s):
            q = select(func.count()).select_from(Task)
            first = (await s.execute(q)).scalar_one()
            s.add(Task(firm_id=firm_id, title="written during a read"))
            await s.flush()
            after = (await s.execute(q)).scalar_one()
        check("a write forgets what was remembered", after - first, 1)
        await s.rollback()


async def states(s, firm) -> dict:
    """The dated scenarios as the screens read them today."""
    today = firm_time.today(firm)
    sharma = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == "The Sharma Family"))).scalar_one()
    tax = await tax_intelligence.for_household(s, sharma.id, firm.jurisdiction, firm_id=firm.id)
    huf = next(t for t in tax["taxpayers"] if t["name"] == "Rajesh Sharma HUF")
    register = await complaints.overview(s, firm)
    policies = {p["kind"]: p["state"] for p in (await governance.policies(s, firm))["policies"]}
    kyc = {p["name"]: p["state"] for p in (await kyc_india.overview(s, firm))["parties"]}
    compliance = await user_of(s, firm, UserRole.COMPLIANCE)
    ex = ToolExecutors(s, compliance.id, compliance.role, firm.id)
    certs = {c["label"]: c["state"] for c in (await ex.execute("read_adviser_certifications", {}))["people"][0]["certificates"]}
    accredited = {p["person"]: p["state"] for p in (await ex.execute("read_accredited_investors", {}))["people"]}
    lrs = {p["person"]: p.get("remitted_usd") for p in (await ex.execute("read_lrs", {}))["people"]}
    year = (await s.execute(select(func.count()).select_from(Transaction).where(
        Transaction.firm_id == firm.id, Transaction.txn_type.in_(("sell", "dividend")), Transaction.trade_date <= today,
        Transaction.trade_date >= date(today.year if today.month >= 4 else today.year - 1, 4, 1)))).scalar_one()
    return {"turning long": any(o["kind"] == "turning_long" for o in huf["opportunities"]),
            "complaints": {k: register["counts"][k] for k in ("open", "at_risk", "overdue", "resolved", "withdrawn")},
            "policies": policies, "Karthik": kyc.get("Karthik Iyer"), "Lakshmi": kyc.get("Lakshmi Iyer"), "certificates": certs,
            "accredited": accredited, "remitted": lrs.get("Karthik Iyer"), "this year": year}


async def later(Session, firm_id) -> None:
    real = firm_time.utcnow
    async with Session() as s:
        firm = await s.get(Firm, firm_id)
        first = await states(s, firm)
        history = sorted((str(r.remitted_on) for r in (await s.execute(select(LrsRemittance))).scalars().all()))
        check("on the day it is seeded nothing needs moving", {k: v for k, v in (await india_redate.refresh(s, firm)).items() if v}, {})
        await s.rollback()
    try:
        for gap in (75, 200):                    # 200 days from the autumn is past 31 March: a new tax year
            print(f"\n=== {gap} days later ===")
            firm_time.utcnow = lambda gap=gap: real() + timedelta(days=gap)
            async with Session() as s:
                firm = await s.get(Firm, firm_id)
                drifted = await states(s, firm)
                check("left alone, the scenarios have aged: the lot, the complaint at risk and the certificate due soon",
                      (drifted["turning long"], drifted["complaints"]["at_risk"], drifted["certificates"] == first["certificates"]), (False, 0, False))
                tasks = (await s.execute(select(func.count()).select_from(Task))).scalar_one()
                lots = (await s.execute(select(func.count()).select_from(TaxLot))).scalar_one()
                moved = await india_redate.refresh(s, firm)
                await s.commit()
            async with Session() as s:
                firm = await s.get(Firm, firm_id)
                again = await states(s, firm)
                for key in first:
                    check(f"re-dated, {key} reads as seeded", again[key], first[key])
                check("no task or lot is added or removed", ((await s.execute(select(func.count()).select_from(Task))).scalar_one(),
                                                             (await s.execute(select(func.count()).select_from(TaxLot))).scalar_one()), (tasks, lots))
                check("a second run moves nothing", {k: v for k, v in (await india_redate.refresh(s, firm)).items() if v}, {})
                if gap == 75:
                    check("remittances of the current year stay as recorded", sorted(str(r.remitted_on) for r in (await s.execute(select(LrsRemittance))).scalars().all()), history)
                check("it moved something", sum(moved.values()) > 0, True)
                await s.rollback()
            if gap == 75:
                async with Session() as s:
                    lines: list[str] = []
                    await proof.run(s, out=lines.append, selftest=False)
                    fails = [l for l in lines if l.startswith("FAIL") and ("HUF" in l or "sellable" in l)]
                    check("the live proof reads the lot and the pledge as they stand", fails, [])
    finally:
        firm_time.utcnow = real

    async with Session() as s:
        firm = await s.get(Firm, firm_id)
        twin = Firm(slug="a-client-firm", name="A client firm", jurisdiction="IN", base_currency="INR", settings={"demonstration": False})
        check("a firm that is not the demonstration is never touched", await india_redate.refresh(s, twin), {})
        print("\n=== The proof on a lot that has aged, and a pledge of another size ===")
        huf = (await s.execute(select(Account).where(Account.firm_id == firm.id, Account.name == "Rajesh Sharma HUF demat"))).scalar_one()
        reliance = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "RELIANCE.NS"))).scalar_one()
        holding = (await s.execute(select(Holding).where(Holding.account_id == huf.id, Holding.instrument_id == reliance.id))).scalar_one()
        lot = (await s.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalar_one()
        lot.acquired_on = firm_time.today(firm) - timedelta(days=500)
        nifty = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "NIFTYBEES.NS"))).scalar_one()
        karthik = (await s.execute(select(Account).where(Account.firm_id == firm.id, Account.name == "Karthik Iyer demat"))).scalar_one()
        units = (await s.execute(select(Holding).where(Holding.account_id == karthik.id, Holding.instrument_id == nifty.id))).scalar_one()
        units.quantity = float(units.quantity) - 1_000
        await s.commit()
    async with Session() as s:
        lines = []
        await proof.run(s, out=lines.append, selftest=False)
        mine = [l for l in lines if "HUF's RELIANCE" in l or "sellable is what is held" in l]
        check("both pass: the lot has passed twelve months and is not shown, and sellable is held less lien", [l[:4] for l in mine], ["PASS", "PASS"])


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            firm_id = firm.id
        for part in (client_lines, whose, firm_wide, reminders):
            async with Session() as s:
                try:
                    await part(s, await s.get(Firm, firm_id))
                except Exception as exc:  # noqa: BLE001 - one part's failure is reported, and the others still run
                    check(f"{part.__name__} ran", f"{type(exc).__name__}: {exc}"[:200], "ran")
                await s.rollback()
        await page(Session, db, firm_id)
        await later(Session, firm_id)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
