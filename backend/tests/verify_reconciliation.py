"""Prove custodian reconciliation: a statement is compared with the book as it stood on its date,
differences become breaks with a likely cause, and nothing here ever changes the book.

Runs against a throwaway SQLite database; no custodian is contacted, which is the point - the
statements are supplied, not invented.

    python -m tests.verify_reconciliation
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import reconciliation as recon_api
from app.aurea_core import reconciliation as rc
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.enums import MarketType, UserRole
from app.models.graph import Account
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Transaction
from app.models.reconciliation import CustodyStatement, ReconBreak
from app.models.tenant import Firm

PASS, FAIL = [], []
UTC = timezone.utc
D = Decimal


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, exc=rc.ReconciliationError, contains: str | None = None) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:90]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== Comparing positions ===")
    a, b, c, d = (uuid.uuid4() for _ in range(4))
    book = {a: D(100), b: D(50), c: D("0.00001"), d: D(10)}
    cust = {a: D(100), b: D(40), c: D(0), d: D(0)}
    out = {x["instrument_id"]: x["kind"] for x in rc.compare(book, cust)}
    check("equal positions are not a break", a in out, False)
    check("a different quantity is a quantity break", out.get(b), "quantity")
    check("a dust position the custodian dropped is a match", c in out, False)
    check("a line the custodian no longer holds is missing at the custodian", out.get(d), "missing_at_custodian")
    check("a line only the custodian holds is missing in the book",
          rc.compare({}, {a: D(5)})[0]["kind"], "missing_in_book")
    check("an instrument left out of the comparison is not compared", rc.compare(book, cust, skip={b, d}), [])

    print("\n=== What a booked transaction did to cash ===")
    t = lambda kind, amount, fees=0: SimpleNamespace(txn_type=kind, amount=amount, lineage={"fees": fees})
    check("a buy takes the price and the fee", rc.cash_effect(t("buy", 1000, 5)), D("-1005"))
    check("a sell gives the price less the fee", rc.cash_effect(t("sell", 1000, 5)), D("995"))
    check("a fee debit takes", rc.cash_effect(t("fee", 40)), D("-40"))
    check("a fee credit and a dividend give", (rc.cash_effect(t("fee_credit", 40)), rc.cash_effect(t("dividend", 9))),
          (D("40"), D("9")))
    check("a kind it does not know has no known effect", rc.cash_effect(t("transfer", 500)), None)


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        today = date(2026, 10, 27)

        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD")
            s.add_all([firm, other])
            await s.flush()

            def inst(sym, f=firm):
                i = Instrument(id=uuid.uuid4(), firm_id=f.id, symbol=sym, name=f"{sym} Ltd", asset_class="equity",
                               market_type=MarketType.PUBLIC, currency="NZD")
                s.add(i)
                return i

            aaa, bbb, theirs = inst("AAA"), inst("BBB"), inst("THEIRS", other)
            await s.flush()

            def acct(name, cash=10000, f=firm, hold=((100, "aaa"), (50, "bbb"))):
                a = Account(id=uuid.uuid4(), firm_id=f.id, name=name, custodian="Custody Co", currency="NZD",
                            cash_balance=cash)
                s.add(a)
                return a

            async def holdings(a, **qty):
                for sym, q in qty.items():
                    i = {"aaa": aaa, "bbb": bbb}[sym]
                    s.add(Holding(firm_id=a.firm_id, account_id=a.id, instrument_id=i.id, quantity=q,
                                  market_value=0, cost_basis=0))
                await s.flush()

            def txn(a, kind, qty, amount, days_ago, i=None, fees=0):
                t = Transaction(firm_id=a.firm_id, account_id=a.id, instrument_id=i.id if i else None,
                                txn_type=kind, quantity=qty, price=0, amount=amount,
                                trade_date=today - timedelta(days=days_ago), lineage={"fees": fees})
                s.add(t)
                return t

            async def stmt(a, positions, cash=None, as_of=today, source="Custody Co statement 27 Oct", **kw):
                return await rc.import_statement(
                    s, firm.id, account_id=a.id, as_of=as_of, positions=positions, cash_balance=cash,
                    source_document=source, imported_by="ops@x", today=today, **kw)

            async def breaks_of(a, *statuses):
                rows = (await s.execute(select(ReconBreak).where(ReconBreak.account_id == a.id))).scalars().all()
                return [b for b in rows if not statuses or str(b.status) in statuses]

            print("\n=== A statement that agrees ===")
            a1 = acct("Clean")
            await s.flush()
            await holdings(a1, aaa=100, bbb=50)
            r = await stmt(a1, [{"symbol": "AAA", "quantity": 100}, {"symbol": "bbb", "quantity": 50}], cash=10000)
            check("nothing to open", r["breaks"], {"opened": 0, "refreshed": 0, "cleared": 0, "open": 0})
            check("both positions matched and cash compared", (r["positions_matched"], r["cash"]), (2, "compared"))
            check("symbols are matched without regard to case", r["positions_compared"], 2)

            print("\n=== A difference nothing explains ===")
            a2 = acct("Unexplained")
            await s.flush()
            await holdings(a2, aaa=100, bbb=50)
            r = await stmt(a2, [{"symbol": "AAA", "quantity": 90}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            (b,) = await breaks_of(a2)
            check("one quantity break", (b.kind, b.symbol, str(b.status)), ("quantity", "AAA", "open"))
            check("figures are the book's, the custodian's and custodian minus book",
                  (float(b.book_value), float(b.custodian_value), float(b.difference)), (100.0, 90.0, -10.0))
            check("left unexplained, to be investigated", (b.cause, b.next_step, float(b.confidence)),
                  ("unexplained", "investigate", 0.0))
            holding = (await s.execute(select(Holding).where(Holding.account_id == a2.id,
                                                              Holding.instrument_id == aaa.id))).scalar_one()
            check("the book was not touched", float(holding.quantity), 100.0)

            print("\n=== A trade the custodian has not settled yet ===")
            a3 = acct("Timing", cash=8995)
            await s.flush()
            await holdings(a3, aaa=110, bbb=50)
            t3 = txn(a3, "buy", 10, 1000, 2, aaa, fees=5)
            await s.flush()
            r = await stmt(a3, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            rows = {b.kind: b for b in await breaks_of(a3)}
            check("the position and the cash both differ", sorted(rows), ["cash", "quantity"])
            check("the position gap is the buy", (rows["quantity"].cause, rows["quantity"].next_step,
                                                  float(rows["quantity"].confidence)),
                  ("settlement_timing", "wait_for_next_statement", 0.9))
            check("the cash gap is the buy and its fee", (rows["cash"].cause, rows["cash"].evidence.get("net_cash")),
                  ("settlement_timing", -1005.0))
            check("the evidence names the transaction", rows["quantity"].evidence.get("transactions"), [str(t3.id)])
            r = await stmt(a3, [{"symbol": "AAA", "quantity": 110}, {"symbol": "BBB", "quantity": 50}], cash=8995,
                           as_of=today)
            check("both cleared, none left", (r["breaks"]["cleared"], r["breaks"]["open"]), (2, 0))
            check("and they are kept as cleared, not deleted",
                  sorted(str(b.status) for b in await breaks_of(a3)), ["cleared", "cleared"])

            print("\n=== The book as it stood on the statement's date ===")
            a4 = acct("AsOf", cash=8995)
            await s.flush()
            await holdings(a4, aaa=110, bbb=50)
            txn(a4, "buy", 10, 1000, 2, aaa, fees=5)
            await s.flush()
            r = await stmt(a4, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=10000,
                           as_of=today - timedelta(days=5))
            check("a buy booked after the statement date is not held against it", r["breaks"]["open"], 0)
            check("and cash is rolled back too", r["cash"], "compared")

            print("\n=== A corporate action the book has not posted ===")
            a5 = acct("Pending", cash=10000)
            await s.flush()
            await holdings(a5, aaa=100, bbb=50)
            split = CorporateAction(firm_id=firm.id, instrument_id=aaa.id, action_type="split", status="announced",
                                    ex_date=today - timedelta(days=1), details={"ratio": 2})
            div = CorporateAction(firm_id=firm.id, instrument_id=bbb.id, action_type="cash_dividend",
                                  status="announced", payable_date=today - timedelta(days=1),
                                  details={"cash_per_share": 1.0})
            s.add_all([split, div])
            await s.flush()
            s.add_all([CorporateActionEntitlement(firm_id=firm.id, corporate_action_id=split.id, account_id=a5.id,
                                                  status="pending", shares_entitled=100),
                       CorporateActionEntitlement(firm_id=firm.id, corporate_action_id=div.id, account_id=a5.id,
                                                  status="pending", shares_entitled=50)])
            await s.flush()
            await stmt(a5, [{"symbol": "AAA", "quantity": 200}, {"symbol": "BBB", "quantity": 50}], cash=10050)
            rows = {b.kind: b for b in await breaks_of(a5)}
            check("a split not yet posted explains the extra shares",
                  (rows["quantity"].cause, rows["quantity"].next_step, rows["quantity"].evidence["expected_quantity"]),
                  ("corporate_action_pending", "post_entitlement", 100.0))
            check("a dividend not yet posted explains the extra cash",
                  (rows["cash"].cause, rows["cash"].evidence["expected_cash"]), ("corporate_action_pending", 50.0))

            print("\n=== What cannot be rolled back is not compared ===")
            a6 = acct("Split since")
            await s.flush()
            await holdings(a6, aaa=200, bbb=50)
            posted = CorporateAction(firm_id=firm.id, instrument_id=aaa.id, action_type="split", status="posted",
                                     ex_date=today - timedelta(days=1), details={"ratio": 2})
            s.add(posted)
            await s.flush()
            s.add(CorporateActionEntitlement(firm_id=firm.id, corporate_action_id=posted.id, account_id=a6.id,
                                             status="posted", shares_entitled=100,
                                             posted_at=datetime.combine(today, datetime.min.time(), tzinfo=UTC)))
            await s.flush()
            r = await stmt(a6, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=10000,
                           as_of=today - timedelta(days=3))
            check("a split posted since is reported, not guessed at", (r["not_comparable"], r["breaks"]["open"]),
                  (["AAA"], 0))
            a7 = acct("Unknown movement")
            await s.flush()
            await holdings(a7, aaa=100, bbb=50)
            txn(a7, "transfer", 0, 500, 1)
            await s.flush()
            r = await stmt(a7, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=9000,
                           as_of=today - timedelta(days=3))
            check("cash is not compared across a movement of a kind it cannot read", r["cash"], "not_comparable")
            check("and no cash break is raised", [b.kind for b in await breaks_of(a7)], [])
            a8 = acct("No cash figure")
            await s.flush()
            await holdings(a8, aaa=100, bbb=50)
            r = await stmt(a8, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}])
            check("a statement with no cash is not taken to mean zero cash", (r["cash"], r["breaks"]["open"]),
                  ("no_cash_on_statement", 0))

            print("\n=== A fee and a security the firm does not hold ===")
            a9 = acct("Fee", cash=9975)
            await s.flush()
            await holdings(a9, aaa=100, bbb=50)
            txn(a9, "fee", 0, 25, 1)
            await s.flush()
            await stmt(a9, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            (b,) = await breaks_of(a9)
            check("a fee the custodian has not debited is fee timing", (b.kind, b.cause), ("cash", "fee_timing"))
            a10 = acct("Unmapped")
            await s.flush()
            await holdings(a10, aaa=100, bbb=50)
            await stmt(a10, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50},
                             {"symbol": "ZZZ", "quantity": 7}], cash=10000)
            (b,) = await breaks_of(a10)
            check("a symbol the firm has no instrument for is unmapped",
                  (b.kind, b.symbol, b.cause, b.next_step), ("unmapped_security", "ZZZ", "unmapped_security",
                                                             "map_instrument"))
            check("and it is not matched to another firm's instrument", b.instrument_id, None)

            print("\n=== A break seen again, explained, dismissed ===")
            before = (await breaks_of(a2))[0]
            r = await stmt(a2, [{"symbol": "AAA", "quantity": 90}, {"symbol": "BBB", "quantity": 50}], cash=10000,
                           as_of=today)
            rows = await breaks_of(a2)
            check("the same disagreement is one break, seen again", (len(rows), rows[0].id == before.id,
                                                                    rows[0].times_seen), (1, True, 2))
            out = await rc.resolve_break(s, firm.id, before.id, action="explained", note=None, by="ops@x")
            check("it can be accepted as explained", (out["status"], out["was"]), ("explained", "open"))
            await stmt(a2, [{"symbol": "AAA", "quantity": 90}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            check("an explained break stays explained when nothing changed", str((await breaks_of(a2))[0].status),
                  "explained")
            await stmt(a2, [{"symbol": "AAA", "quantity": 85}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            row = (await breaks_of(a2))[0]
            check("and reopens when the figures move", (str(row.status), float(row.difference)), ("open", -15.0))
            check("with the reopening on its history", [h["event"] for h in row.history][-1], "reopened")
            await rc.resolve_break(s, firm.id, row.id, action="dismissed", note="Custodian confirmed a rounding lot",
                                   by="ops@x")
            await stmt(a2, [{"symbol": "AAA", "quantity": 85}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            check("a dismissed break stays quiet while it is unchanged", str((await breaks_of(a2))[0].status),
                  "dismissed")
            r = await stmt(a2, [{"symbol": "AAA", "quantity": 100}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            check("and clears when the statement agrees again", (str((await breaks_of(a2))[0].status),
                                                                 r["breaks"]["cleared"]), ("cleared", 1))

            print("\n=== Acting on a break ===")
            a11 = acct("Resolve")
            await s.flush()
            await holdings(a11, aaa=100, bbb=50)
            await stmt(a11, [{"symbol": "AAA", "quantity": 95}, {"symbol": "BBB", "quantity": 50}], cash=10000)
            (b,) = await breaks_of(a11)
            await refused("setting a break aside needs a reason",
                          rc.resolve_break(s, firm.id, b.id, action="dismissed", note=" ", by="x"), contains="reason")
            await refused("an action it does not know is refused",
                          rc.resolve_break(s, firm.id, b.id, action="delete", note="x", by="x"), contains="unknown")
            await refused("another firm cannot touch it",
                          rc.resolve_break(s, other.id, b.id, action="escalated", note=None, by="x"),
                          contains="not found")
            out = await rc.resolve_break(s, firm.id, b.id, action="escalated", note="Sent to custody ops", by="a@x")
            check("escalating records who and when", (out["status"], out["resolved_by"]), ("escalated", "a@x"))
            out = await rc.resolve_break(s, firm.id, b.id, action="resolved", note="Custodian corrected", by="b@x")
            check("it can then be marked fixed", out["status"], "resolved")
            await refused("a closed break stays closed",
                          rc.resolve_break(s, firm.id, b.id, action="escalated", note=None, by="c@x"),
                          contains="already resolved")
            await refused("and the second of two people acting is told where it stands",
                          rc.resolve_break(s, firm.id, b.id, action="resolved", note="again", by="c@x"),
                          contains="already resolved")
            check("every step is on the history", [h["event"] for h in (await breaks_of(a11))[0].history],
                  ["opened", "escalated", "resolved"])

            print("\n=== A statement that cannot be taken at face value ===")
            a12 = acct("Import rules")
            await s.flush()
            await holdings(a12, aaa=100, bbb=50)
            ok = [{"symbol": "AAA", "quantity": 100}]
            await refused("no source document", stmt(a12, ok, source=" "), contains="source")
            await refused("a date in the future", stmt(a12, ok, as_of=today + timedelta(days=1)), contains="future")
            await refused("a currency the account is not held in", stmt(a12, ok, currency="USD"), contains="USD")
            await refused("nothing on it", stmt(a12, []), contains="nothing")
            await refused("the same symbol twice", stmt(a12, ok + ok), contains="twice")
            await refused("a negative quantity", stmt(a12, [{"symbol": "AAA", "quantity": -1}]), contains="negative")
            await refused("a quantity that is not a number", stmt(a12, [{"symbol": "AAA", "quantity": "lots"}]),
                          contains="not a number")
            await refused("an account from another firm", rc.import_statement(
                s, firm.id, account_id=(await s.execute(select(Account.id).where(Account.firm_id == other.id))).scalar()
                or uuid.uuid4(), as_of=today, positions=ok, source_document="x doc", imported_by="x", today=today),
                contains="not in this firm")
            await stmt(a12, ok, as_of=today)
            await refused("a statement older than the one on file", stmt(a12, ok, as_of=today - timedelta(days=2)),
                          contains="newer")
            await stmt(a12, ok, as_of=today, source="Corrected statement")
            rows = (await s.execute(select(CustodyStatement).where(CustodyStatement.account_id == a12.id))).scalars().all()
            check("the same date replaces the first, which is kept as superseded",
                  sorted(str(r.status) for r in rows), ["current", "superseded"])

            print("\n=== The overview ===")
            ov = await rc.overview(s, firm.id, today=today)
            states = {a["account"]: a["state"] for a in ov["accounts"]}
            check("an account that agrees is reconciled", states["Clean"], "reconciled")
            check("a break that was fixed no longer counts against an account", states["Resolve"], "reconciled")
            check("a statement with no cash still reconciles on its positions", states["No cash figure"], "reconciled")
            old = acct("Old statement")
            never = acct("Never")
            acct("Other", f=other)
            await s.flush()
            await holdings(old, aaa=100, bbb=50)
            await stmt(old, [{"symbol": "AAA", "quantity": 100}], as_of=today - timedelta(days=9))
            ov = await rc.overview(s, firm.id, today=today)
            states = {a["account"]: a["state"] for a in ov["accounts"]}
            check("a statement older than a week no longer counts", states["Old statement"], "stale_statement")
            check("an account with none is not called reconciled", states["Never"], "no_statement")
            check("an account with a break open has breaks", states["Fee"], "breaks")
            check("another firm's accounts are not shown", any(a["account"] == "Other" for a in ov["accounts"]), False)
            sm = ov["summary"]
            check("the summary adds up", sm["reconciled"] + sm["breaks"] + sm["stale_statement"] + sm["no_statement"],
                  sm["accounts"])
            check("open breaks are counted by cause", set(sm["by_cause"]) <= set(
                ("settlement_timing", "fee_timing", "corporate_action_pending", "unmapped_security", "unexplained")), True)
            first = ov["breaks"][0]
            check("a break row carries figures and codes, not sentences",
                  (set(first) >= {"book", "custodian_value", "difference", "cause", "next_step", "evidence"},
                   "message" in first or "summary" in first), (True, False))
            check("the worst are listed first", [b["status"] for b in ov["breaks"]] ==
                  sorted((b["status"] for b in ov["breaks"]), key=lambda x: {"escalated": 0, "open": 1, "explained": 2}[x]),
                  True)

            print("\n=== The tools and the routes ===")
            check("reading changes nothing", TOOLS["read_reconciliation"].change_state, ToolChangeState.NO)
            check("importing a statement waits for confirmation",
                  TOOLS["import_custody_statement"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("so does acting on a break", TOOLS["resolve_recon_break"].change_state,
                  ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("only operations and the portfolio team import or act",
                  (TOOLS["import_custody_statement"].roles_required, TOOLS["resolve_recon_break"].roles_required),
                  ({UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM},) * 2)
            check("an adviser and a client cannot read it",
                  (UserRole.ADVISER in TOOLS["read_reconciliation"].roles_required,
                   UserRole.CLIENT in TOOLS["read_reconciliation"].roles_required), (False, False))
            check("compliance can read but not act",
                  (UserRole.COMPLIANCE in TOOLS["read_reconciliation"].roles_required,
                   UserRole.COMPLIANCE in TOOLS["resolve_recon_break"].roles_required), (True, False))

            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@x", full_name="Ops", role=UserRole.OPERATIONS,
                       hashed_password="x")
            adv = User(id=uuid.uuid4(), firm_id=firm.id, email="adv@x", full_name="Adv", role=UserRole.ADVISER,
                       hashed_password="x")
            s.add_all([ops, adv])
            await s.flush()
            a13 = acct("Tool")
            await s.flush()
            await holdings(a13, aaa=100, bbb=50)
            args = {"account_id": str(a13.id), "as_of": today.isoformat(),
                    "positions": [{"symbol": "AAA", "quantity": 99}, {"symbol": "BBB", "quantity": 50}],
                    "cash_balance": 10000, "source_document": "Custody Co statement 27 Oct"}
            try:
                await ToolExecutors(s, adv.id, UserRole.ADVISER, firm.id).execute("import_custody_statement", args)
                check("an adviser is refused at the executor too", False, True)
            except ExecutorError:
                check("an adviser is refused at the executor too", True, True)
            res = await ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id).execute("import_custody_statement", args)
            check("operations import it and the tool says what happened",
                  (res["breaks"]["opened"], "1 break(s) opened" in res["message"]), (1, True))
            try:
                await ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id).execute(
                    "import_custody_statement", {**args, "as_of": "next week"})
                check("a bad date is refused", False, True)
            except ExecutorError:
                check("a bad date is refused", True, True)
            read = await ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id).execute("read_reconciliation", {})
            tool_break = next(b for b in read["breaks"] if b["account"] == "Tool")
            check("reading shows the break it opened", (tool_break["symbol"], tool_break["difference"]), ("AAA", -1.0))
            out = await ToolExecutors(s, ops.id, UserRole.OPERATIONS, firm.id).execute(
                "resolve_recon_break", {"break_id": tool_break["id"], "action": "escalated"})
            check("operations act on it and it is recorded with their name",
                  (out["status"], out["resolved_by"]), ("escalated", "ops@x"))

            ops_user = SimpleNamespace(role=UserRole.OPERATIONS, email="ops@x")
            adv_user = SimpleNamespace(role=UserRole.ADVISER, email="adv@x")
            view = await recon_api.read(user=adv_user, firm=firm, db=s)
            check("an adviser can read the page but not act", view["can_act"], False)
            body = recon_api.StatementIn(account_id=a13.id, as_of=today, positions=[{"symbol": "AAA", "quantity": 99}],
                                         source_document="x statement")
            for label, call in (
                ("importing", recon_api.import_statement(body, user=adv_user, firm=firm, db=s)),
                ("acting", recon_api.resolve(uuid.uuid4(), recon_api.ResolveIn(action="explained"), user=adv_user,
                                             firm=firm, db=s)),
            ):
                try:
                    await call
                    check(f"{label} is refused to an adviser", False, True)
                except HTTPException as e:
                    check(f"{label} is refused to an adviser", e.status_code, 403)
            try:
                await recon_api.import_statement(
                    recon_api.StatementIn(account_id=a13.id, as_of=today + timedelta(days=3),
                                          positions=[{"symbol": "AAA", "quantity": 1}], source_document="x statement"),
                    user=ops_user, firm=firm, db=s)
                check("a statement dated ahead is a 422", False, True)
            except HTTPException as e:
                check("a statement dated ahead is a 422", e.status_code, 422)
            try:
                await recon_api.resolve(uuid.UUID(tool_break["id"]), recon_api.ResolveIn(action="explained"),
                                        user=ops_user, firm=firm, db=s)
                check("acting on a break that has moved on is a 409", False, True)
            except HTTPException as e:
                check("acting on a break that has moved on is a 409", e.status_code, 409)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
