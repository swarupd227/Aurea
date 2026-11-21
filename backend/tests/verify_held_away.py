"""How money held elsewhere is doing (B-198).

    python -m tests.verify_held_away

Against a throwaway SQLite database. Checks that:

  - net worth counts held-away money once: lines replace a total recorded for the same owner at the same institution
    and the profile figure, a trust's lines replace nothing, a line in another currency is listed and not added, and
    the managed total is untouched; protection reads the same figure;
  - a holding is measured only on real prices: its return over 365 days beside its class reference over the same
    dates, underperforming at five points behind, with the best shelf product that beat it - approved before not
    reviewed, never one closed, under watch, excluded by the household's values, or not reviewed while approved-only
    is on; a synthetic price or no symbol means no performance; the same instrument in the managed book is an overlap;
  - a feed registered at the seam is pulled by its own ids: a second pull adds nothing, a changed line supersedes
    the old one, a line no longer delivered ends, another firm's household is refused, and a simulated feed says so;
  - the tools: reading changes nothing, recording and ending stop at a Confirm card that names the figures and
    refuses what recording would, a role that may not record is refused, and each person's change is on the ledger;
  - the meeting brief carries a held-away section and puts the underperformer on the agenda; the wallet-share agent
    raises it as a proposal whose approval is a task for the adviser;
  - nothing reaches a client: no client tool reads it and the client's net worth carries none of it;
  - another firm sees none of it.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api import held_away as api
from app.aurea_core import balance_sheet as bs
from app.aurea_core import firm_time, held_away, held_away_feeds, protection, shelf
from app.aurea_core.graph import held_away_of, household_brain
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS, ToolChangeState, validate_tool_call
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import AssetClass, MarketType, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.held_away import HeldAwayHolding
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm

PASS, FAIL = [], []
DOC = "Jarden statement, 31 Oct 2026"


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except (held_away.HeldAwayError, ExecutorError) as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== Counting once ===")
    a, b = uuid.uuid4(), uuid.uuid4()
    people = [SimpleNamespace(id=a, profile={"held_away": 300}), SimpleNamespace(id=b, profile={"held_away": 100})]
    item = lambda name, amount, pid=None: SimpleNamespace(kind="held_away_investment", name=name, amount=amount, person_id=pid)  # noqa: E731
    line = lambda inst, value, pid=None, eid=None: SimpleNamespace(institution=inst, value=value, person_id=pid, entity_id=eid)  # noqa: E731
    check("with nothing listed, the profiles stand", held_away_of(people, []), 400.0)
    check("a line replaces its owner's profile figure only", held_away_of(people, [], [line("Jarden", 50, a)]), 150.0)
    check("a line replaces the total recorded for the same owner at the same institution",
          held_away_of(people, [item("Jarden", 90, a), item("Other", 150, a)], [line(" jarden ", 50, a)]), 300.0)
    check("but not one recorded for someone else there", held_away_of(people, [item("Jarden", 90, b)], [line("Jarden", 50, a)]), 140.0)
    check("a trust's line replaces nothing", held_away_of(people, [], [line("Jarden", 20, None, uuid.uuid4())]), 420.0)
    check("a joint line replaces every profile", held_away_of(people, [], [line("Kernel", 5)]), 5.0)

    print("\n=== Measuring ===")
    today = firm_time.today(None)
    series = [(today - timedelta(days=d), 100 * (1 - 0.1 * (365 - d) / 365)) for d in (400, 365, 200, 30, 0)]
    series.sort()
    m = held_away.measure(series, today)
    check("a year's return on its closes", (m["return"], m["start"], m["end"]), (-0.1, (today - timedelta(days=365)).isoformat(), today.isoformat()))
    check("too little history measures nothing", held_away.measure(series[-2:], today), None)
    check("another instrument over the same dates", held_away.over([(today - timedelta(days=366), 50.0), (today, 60.0)], m["start"], m["end"]), 0.2)
    check("and none where it has no close near them", held_away.over([(today - timedelta(days=100), 50.0), (today, 60.0)], m["start"], m["end"]), None)

    print("\n=== The tools ===")
    read, record, end = TOOLS["read_held_away"], TOOLS["record_held_away_holding"], TOOLS["end_held_away_holding"]
    check("reading changes nothing", read.change_state, ToolChangeState.NO)
    check("recording and ending stop at a Confirm card", (record.change_state, end.change_state),
          (ToolChangeState.PAUSE_FOR_CONFIRMATION, ToolChangeState.PAUSE_FOR_CONFIRMATION))
    writers = {UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS}
    check("recorded by those who record net worth", (record.roles_required, end.roles_required), (writers, writers))
    check("no client reads it", validate_tool_call(UserRole.CLIENT, "read_held_away", {})[0], False)
    check("its page is net worth, beside the conversation", OPEN_VIEWS["net_worth"][0], "/studio/net-worth")


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ", settings={})
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)

            def user(role, name, f=firm, person_id=None):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{name}@t", full_name=name.title(), role=role, hashed_password="x",
                         person_id=person_id)
                s.add(u)
                return u
            sophie, david = user(UserRole.ADVISER, "sophie"), user(UserRole.COMPLIANCE, "david")
            outsider = user(UserRole.ADVISER, "ola", other)

            insts = {}
            for sym, cls, ret, tags, real in (
                    ("SPY", "equity", 0.20, {}, True), ("AGG", "fixed_income", 0.03, {}, True), ("SLOW", "equity", -0.10, {}, True),
                    ("FAST", "equity", 0.25, {}, True), ("BEST", "equity", 0.40, {}, True), ("WATCH", "equity", 0.35, {}, True),
                    ("SIN", "equity", 0.50, {"flags": ["tobacco"]}, True), ("UNREV", "equity", 0.30, {}, True),
                    ("OKAY", "equity", 0.18, {}, True), ("SYN", "equity", 0.10, {}, False), ("MGD", "equity", 0.20, {}, True),
                    ("BND2", "fixed_income", -0.05, {}, True)):
                i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=f"{sym} Inc", asset_class=cls,
                               market_type=MarketType.PUBLIC, currency="USD", values_tags=tags)
                s.add(i)
                await s.flush()
                for d in (390, 365, 300, 200, 100, 30, 0):
                    s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today - timedelta(days=d), close=100 * (1 + ret * (365 - d) / 365),
                                currency="USD", source="yahoo" if real else "synthetic", is_real=real))
                insts[sym] = i
            await s.flush()
            for sym, status in (("FAST", "approved"), ("BEST", "closed"), ("WATCH", "watch"), ("SIN", "approved")):
                await shelf.record_decision(s, firm.id, by="rae@t", role="research_cio", symbol=sym, status=status,
                                            rationale="Due diligence done", document="DD paper 2026")

            ann = Household(id=uuid.uuid4(), firm_id=firm.id, name="Ann", values={"exclusions": ["tobacco"]})
            s.add(ann)
            await s.flush()
            a1 = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=ann.id, full_name="Ann Lee", profile={"held_away": 300000})
            a2 = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=ann.id, full_name="Bo Lee", profile={"held_away": 100000})
            trust = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=ann.id, name="Lee Trust", entity_type="trust")
            s.add_all([a1, a2, trust])
            await s.flush()
            from app.models.graph import RelationshipEdge
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=sophie.id, to_type="household", to_id=ann.id, attributes={}))
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=a1.id, name="Ann growth", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            acct = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann A/C", currency="NZD", cash_balance=0)
            s.add(acct)
            await s.flush()
            s.add(Holding(firm_id=firm.id, account_id=acct.id, instrument_id=insts["MGD"].id, quantity=100, market_value=500000, cost_basis=400000))
            client = user(UserRole.CLIENT, "annclient", person_id=a1.id)
            await s.flush()

            ex = ToolExecutors(s, sophie.id, sophie.role, firm.id)

            def args(**kw):
                return {"household_id": str(ann.id), "institution": "Jarden", "instrument_name": "Slow Co", "value": 50000,
                        "value_on": (today - timedelta(days=10)).isoformat(), "source_document": DOC, "symbol": "SLOW",
                        "quantity": 400, "person_id": str(a1.id), **kw}

            async def ledger_count(kind):
                return (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == kind))).scalar()

            print("\n=== Net worth before anything is listed ===")
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("the profiles stand", v["assets"]["held_away"], 400000.0)
            await bs.record_item(s, ann.id, firm_id=firm.id, by="sophie@t", kind="held_away_investment", name="Other Broker", amount=150000,
                                 as_of=today, source_document="Other statement", person_id=a1.id)
            await bs.record_item(s, ann.id, firm_id=firm.id, by="sophie@t", kind="held_away_investment", name="Jarden", amount=90000,
                                 as_of=today, source_document="Jarden summary", person_id=a1.id)
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("recorded totals replace Ann's profile", v["assets"]["held_away"], 150000.0 + 90000.0 + 100000.0)

            print("\n=== Refused before anything is written ===")
            for label, bad, why in (("no institution", {"institution": " "}, "institution"), ("no name", {"instrument_name": ""}, "name the holding"),
                                    ("no value", {"value": None}, "give the value"), ("a negative value", {"value": -1}, "negative"),
                                    ("a value that is not a number", {"value": "lots"}, "number"), ("no date", {"value_on": ""}, "statement's date"),
                                    ("a date to come", {"value_on": (today + timedelta(days=2)).isoformat()}, "future"),
                                    ("no statement", {"source_document": ""}, "which statement"), ("a bad class", {"asset_class": "gold"}, "asset class"),
                                    ("a bad ISIN", {"isin": "US123"}, "twelve"), ("a person and an entity", {"entity_id": str(trust.id)}, "not two"),
                                    ("a bad currency", {"currency": "DOLLARS"}, "three-letter")):
                await refused(f"the card refuses {label}", ex.preview_change("record_held_away_holding", args(**bad)), why)
            theirs = Household(id=uuid.uuid4(), firm_id=other.id, name="Theirs")
            s.add(theirs)
            await s.flush()
            await refused("another firm's household", ex.preview_change("record_held_away_holding", args(household_id=str(theirs.id))), "not found")
            check("nothing was written", (await s.execute(select(func.count()).select_from(HeldAwayHolding))).scalar(), 0)

            print("\n=== Recording, behind its Confirm card ===")
            shown = {r["label"]: r["value"] for r in (await ex.preview_change("record_held_away_holding", args()))["rows"]}
            check("the card names the client, the holding and the figures",
                  (shown["Client"], shown["Institution"], shown["Holding"], shown["Symbol"], "50,000" in shown["Value"], shown["Whose"], shown["Priced here"]),
                  ("Ann", "Jarden", "Slow Co", "SLOW", True, "Ann Lee", "SLOW"))
            check("and its source", shown["Document"], DOC)
            check("a preview writes nothing", (await s.execute(select(func.count()).select_from(HeldAwayHolding))).scalar(), 0)
            await refused("a role that slips past is refused", ToolExecutors(s, david.id, david.role, firm.id).record_held_away_holding(args()), "cannot change")
            out = await ex.record_held_away_holding(args())
            check("recorded, and on the ledger", (out["recorded"], await ledger_count("held_away_holding_recorded")), ("Slow Co at Jarden", 1))
            await refused("the same holding twice", ex.preview_change("record_held_away_holding", args()), "already on file")
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("its line replaces the Jarden total recorded for Ann", v["assets"]["held_away"], 150000.0 + 50000.0 + 100000.0)
            check("and says which total it stands in for", (v["held_away_lines"]["count"], v["held_away_lines"]["replaces"]), (1, ["Jarden"]))

            await ex.record_held_away_holding(args(institution="Schwab", instrument_name="Okay Corp", symbol="OKAY", value=10000, currency="USD",
                                                   person_id=str(a2.id)))
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("a line in another currency is listed, not added, and replaces nothing", (v["assets"]["held_away"], v["held_away_lines"]["not_converted"]),
                  (300000.0, 1))
            await ex.record_held_away_holding(args(institution="Craigs", instrument_name="Bond fund", symbol="BND2", value=20000, person_id=None,
                                                   entity_id=str(trust.id)))
            v = await bs.for_household(s, ann.id, firm_id=firm.id)
            check("a trust's line adds and replaces nothing", v["assets"]["held_away"], 320000.0)
            brain = await household_brain(s, ann.id, firm_id=firm.id)
            check("the brain agrees, and the managed total is untouched", (brain["totals"]["held_away"], brain["totals"]["total_value"]), (320000.0, 500000.0))
            prot = await protection.for_household(s, ann.id, firm_id=firm.id)
            check("protection reads the same figure", prot["assets"]["held_away"], 320000.0)
            for sym, name, inst in (("SYN", "Syn Ltd", "Craigs"), ("MGD", "Managed Co", "Hatch"), (None, "Milford Active Growth", "Milford"),
                                    ("FAST", "Fast Inc", "Hatch")):
                await ex.record_held_away_holding(args(institution=inst, instrument_name=name, symbol=sym, value=10000, person_id=str(a1.id)))

            print("\n=== How it is doing ===")
            view = await ex.read_held_away({"household_id": str(ann.id)})
            rows = {r["name"]: r for r in view["holdings"]}
            slow = rows["Slow Co"]
            check("measured on real prices over a year", (slow["performance"]["return"], slow["reference"]["symbol"], slow["reference"]["return"], slow["gap"]),
                  (-0.1, "SPY", 0.2, -0.3))
            check("five points or more behind its reference is underperforming", slow["underperforming"], True)
            check("the alternative is the approved product that beat it, not a closed, watched, excluded or better unreviewed one",
                  (slow["alternative"]["symbol"], slow["alternative"]["shelf"], slow["alternative"]["return"]), ("FAST", "approved", 0.25))
            check("SPY, FAST, UNREV, OKAY and MGD were considered", slow["alternatives_considered"], 5)
            bond = rows["Bond fund"]
            check("a bond is held to the bond reference", (bond["reference"]["symbol"], bond["gap"], bond["underperforming"]), ("AGG", -0.08, True))
            check("and its alternative, not reviewed, says so", (bond["alternative"]["symbol"], bond["alternative"]["shelf"]), ("AGG", "not_reviewed"))
            check("a trust's line names the trust", bond["owner"], "Lee Trust")
            okay = rows["Okay Corp"]
            check("two points behind is not underperforming", (okay["gap"], okay["underperforming"], okay["counted"]), (-0.02, False, False))
            check("a synthetic price measures nothing", (rows["Syn Ltd"]["performance"], rows["Syn Ltd"]["not_measured"]),
                  (None, "no real price history over 365 days"))
            check("no symbol, no performance, listed at its value", (rows["Milford Active Growth"]["not_measured"], rows["Milford Active Growth"]["value"]),
                  ("no symbol on the statement", 10000.0))
            check("the same instrument in the managed book is an overlap", rows["Managed Co"]["overlap"], {"managed_value": 500000.0})
            fast = rows["Fast Inc"]
            check("a holding doing well is not raised, and what beat it is still shown, unreviewed and said so", (fast["underperforming"], fast["alternative"]["symbol"], fast["alternative"]["shelf"]), (False, "UNREV", "not_reviewed"))
            check("the findings for the RM", sorted((f["kind"], f["symbol"]) for f in view["findings"]),
                  [("overlap", "MGD"), ("underperforming", "BND2"), ("underperforming", "SLOW")])
            check("it says what it is", (view["estimate"], "dividends" in view["not_modelled"][0], "5%" in view["rule"]["text"]), (True, True, True))
            await shelf.set_enforcement(s, firm.id, on=True, by="rae@t")
            view = await held_away.for_household(s, ann.id, firm_id=firm.id)
            rows = {r["name"]: r for r in view["holdings"]}
            check("with approved products only on, nothing unreviewed is offered", (rows["Bond fund"]["alternative"], rows["Slow Co"]["alternatives_considered"]),
                  (None, 1))
            await shelf.set_enforcement(s, firm.id, on=False, by="rae@t")
            book = await ex.read_held_away({})
            check("the book view leads with the household that has findings", (book["households"][0]["household"], book["summary"]["underperforming"]), ("Ann", 2))

            print("\n=== A feed, at the seam ===")
            class Feed:
                key, name, simulated = "vendor", "Test vendor", False

                def __init__(self):
                    self.out = []

                def enabled_for(self, f):
                    return f.id == firm.id

                async def lines(self, session, f):
                    return list(self.out)

            feed = Feed()
            held_away_feeds.register(feed)
            line = lambda ext, value, hid=ann.id: held_away_feeds.FeedLine(  # noqa: E731
                external_id=ext, household_id=hid, institution="Vendor Bank", instrument_name=f"Line {ext}", value=value, currency="NZD",
                value_on=today - timedelta(days=3), document="Vendor feed", person_id=a2.id, symbol="OKAY")
            feed.out = [line("1", 1000), line("2", 2000), line("3", 5, theirs.id)]
            first = await held_away_feeds.pull(s, firm)
            check("delivered lines are added, another firm's household is refused",
                  (first["added"], [r["reason"] for r in first["refused"]]), (2, ["the household is not this firm's"]))
            again = await held_away_feeds.pull(s, firm)
            check("a second pull adds nothing", (again["added"], again["unchanged"]), (0, 2))
            feed.out = [line("1", 1500)]
            moved = await held_away_feeds.pull(s, firm)
            check("a changed line supersedes, one no longer delivered ends", (moved["added"], moved["superseded"], moved["ended"]), (1, 1, 1))
            kept = (await s.execute(select(func.count()).select_from(HeldAwayHolding).where(HeldAwayHolding.source_system == "vendor"))).scalar()
            check("and both stay on file", kept, 3)
            check("a pull writes no ledger entry", await ledger_count("held_away_holding_recorded"), 7)
            check("the feed's line replaces Bo's profile", (await bs.for_household(s, ann.id, firm_id=firm.id))["assets"]["held_away"],
                  150000.0 + 50000.0 + 40000.0 + 20000.0 + 1500.0)
            check("no other firm is pulled", (await held_away_feeds.pull(s, other))["feeds"], [])
            feed.key, feed.simulated = "simulated:test", True
            held_away_feeds.unregister("vendor")
            held_away_feeds.register(feed)
            feed.out = [line("9", 700)]
            await held_away_feeds.pull(s, firm)
            view = await held_away.for_household(s, ann.id, firm_id=firm.id)
            sim = [r for r in view["holdings"] if r["source"] == "feed" and r["simulated"]]
            check("a simulated feed's line says so", ([r["name"] for r in sim], view["summary"]["simulated"]), (["Line 9"], 1))
            check("a feed no longer installed still names itself", (held_away_feeds.describe("vendor")["feed"], held_away_feeds.describe("simulated:gone")["simulated"]),
                  ("vendor", True))
            held_away_feeds.unregister("simulated:test")

            print("\n=== Ending ===")
            slow_id = next(r["id"] for r in view["holdings"] if r["name"] == "Slow Co")
            shown = {r["label"]: r["value"] for r in (await ex.preview_change("end_held_away_holding", {"holding_id": slow_id, "reason": "Sold"}))["rows"]}
            check("the card names what ends", (shown["Client"], shown["Holding"], shown["Institution"], shown["Reason"]), ("Ann", "Slow Co", "Jarden", "Sold"))
            await refused("with no reason, no card", ex.preview_change("end_held_away_holding", {"holding_id": slow_id, "reason": ""}), "say why")
            out = await ex.end_held_away_holding({"holding_id": slow_id, "reason": "Sold"})
            check("it stops counting, and is kept with why", ([r["name"] for r in out["holdings"] if r["name"] == "Slow Co"], out["ended"][0]["reason"]), ([], "Sold"))
            check("on the ledger", await ledger_count("held_away_holding_ended"), 1)
            await refused("once is enough", held_away.end_holding(s, uuid.UUID(slow_id), firm_id=firm.id, reason="Again", by="x"), "already ended")
            check("with Ann's Jarden line gone, her Jarden total counts again", (await bs.for_household(s, ann.id, firm_id=firm.id))["held_away_lines"]["replaces"], [])

            print("\n=== Raised to the RM ===")
            await ex.record_held_away_holding(args(institution="Jarden", instrument_name="Slow Co again", symbol="SLOW", value=40000))
            from app.agents.meeting_prep import MeetingPrepAgent
            from app.agents.wallet_share_scout import WalletShareScoutAgent
            from app.atlas.base import AgentContext, Subject
            from app.aurea_core import brief_profile

            check("the meeting brief has a held-away section", "held_away" in [k for k, _ in brief_profile.SECTIONS["meeting"]], True)
            agent = MeetingPrepAgent()
            ctx = AgentContext(session=s, firm=firm, run=None, tier=agent.default_tier, guardrails={}, config={}, subject=Subject("household", ann.id, "Ann"))
            brief = (await agent.think(ctx, await agent.sense(ctx)))[0].payload["brief"]
            held = brief["held_away"]
            check("the brief names the underperformers with the shelf's alternative",
                  sorted((f["symbol"], (f["alternative"] or {}).get("symbol")) for f in held["underperforming"]), [("BND2", "AGG"), ("SLOW", "FAST")])
            check("and the overlap", [f["symbol"] for f in held["overlaps"]], ["MGD"])
            check("and puts them on the agenda", sum(1 for a in brief["agenda"] if a.startswith("Held away:")), 3)
            check("laid out as a section", "held_away" in brief["shaped_for"]["order"], True)
            scout = WalletShareScoutAgent()
            wctx = AgentContext(session=s, firm=firm, run=None, tier=scout.default_tier, guardrails={}, config={}, subject=Subject("household", ann.id, "Ann"))
            # The agent's sense reads brains by string ids, which SQLite cannot look up; its finding is read from the brain directly.
            drafts = await scout._underperforming(wctx, await household_brain(s, ann.id, firm_id=firm.id))
            check("the wallet-share agent raises each underperformer", sorted(d.payload["symbol"] for d in drafts), ["BND2", "SLOW"])
            check("with its evidence and the rule", all(d.payload["rule"] == held_away.RULE and d.evidence["gap"] < 0 for d in drafts), True)
            check("and approving is a task for the adviser that buys nothing", "task for the household's adviser" in scout.on_approval(None), True)

            print("\n=== Nothing reaches a client ===")
            mine = await ToolExecutors(s, client.id, client.role, firm.id).execute("read_my_net_worth", {})
            check("the client's net worth carries the figure on file and none of the findings",
                  (mine["assets"]["held_away"] > 0, any(k in str(mine) for k in ("underperforming", "alternative", "findings", "SLOW"))), (True, False))
            try:
                await api.ReaderDep.dependency(client)
                check("the route refuses a client", "allowed", 403)
            except HTTPException as e:
                check("the route refuses a client", e.status_code, 403)

            print("\n=== Firm scope ===")
            check("another firm reads none of it", await held_away.for_household(s, ann.id, firm_id=other.id), None)
            await refused("nor through the tool", ToolExecutors(s, outsider.id, outsider.role, other.id).read_held_away({"household_id": str(ann.id)}), "not found")
            check("nor in its book", (await held_away.for_firm(s, other.id))["households"], [])
            await refused("nor ends one", held_away.prepare_end(s, uuid.UUID(out["holdings"][0]["id"]), firm_id=other.id, reason="Sold"), "not found")
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
