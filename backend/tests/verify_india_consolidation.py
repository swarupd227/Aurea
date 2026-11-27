"""Prove the consolidation workbench, India (B-389).

Covers the fund-house and folio roll-up of the statement lines already on file; facts a person reads off a document and
each refusal; a holding without cost, a tax class or matching lots reading "not assessed"; the redeem-and-reinvest figures
worked on the India tax engine; classification by an adviser or paraplanner only, an exit needing a reason about the
product and a day; the exit schedule against the mandate's gain budget (the firm's own default, never the platform's);
code changes, demat transfers, PMS transitions and exits tracked step by step by operations only, in order, with evidence;
that nothing moves a holding, a line, cash or an order; firm scope, markets and roles; the tools and Confirm cards; the law
figures' marks; and the demonstration rows, added once.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_consolidation
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import consolidation as con
from app.aurea_core import counsel_review, firm_time, held_away, law_figures
from app.core.db import Base
from app.core.tools import CLIENT_TOOLS, ONE_CLIENT_VIEWS, OPEN_VIEWS, TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.consolidation import ConsolidationDecision, ConsolidationFacts, ConsolidationItem, ConsolidationStep
from app.models.enums import AssetClass, MandateType, MarketType, UserRole
from app.models.governance import LedgerEntry
from app.models.graph import Account, Household, Mandate, Person
from app.models.held_away import HeldAwayHolding
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Transaction
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []
R = UserRole


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, contains: str | None = None, exc=(con.ConsolidationError,)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def count(s, model, *where) -> int:
    return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


def by_name(view: dict, name: str) -> dict:
    return next(h for h in view["holdings"] if h["name"] == name)


async def unit() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN", settings={"timezone": "Asia/Kolkata"})
            other = Firm(id=uuid.uuid4(), name="IN2", slug="in2", base_currency="INR", jurisdiction="IN", settings={"timezone": "Asia/Kolkata"})
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other, nz])
            await s.flush()
            today = firm_time.today(firm)
            ago = lambda n: (today - timedelta(days=n)).isoformat()    # noqa: E731
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@in", full_name=r.value, role=r, hashed_password="x")
                     for r in (R.ADVISER, R.PARAPLANNER, R.OPERATIONS, R.COMPLIANCE, R.ADMIN)}
            s.add_all(users.values())
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharmas")
            nz_hh = Household(id=uuid.uuid4(), firm_id=nz.id, name="The Chens")
            s.add_all([hh, nz_hh])
            await s.flush()
            tax = {"tax": {"regime": "new", "marginal_rate": 0.30, "annual_income": 7_200_000}}
            rajesh = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Rajesh Sharma", profile=tax)
            meera = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Meera Sharma", profile={})
            s.add_all([rajesh, meera])
            await s.flush()
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=rajesh.id, name="Rajesh - Balanced", mandate_type=MandateType.ADVISORY,
                              constraints={"cgt_budget": 800000})
            bare = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=meera.id, name="Meera - Balanced", mandate_type=MandateType.ADVISORY, constraints={})
            s.add_all([mandate, bare])
            await s.flush()
            account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Rajesh demat", currency="INR", cash_balance=1000)
            s.add(account)
            fund = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="FLEXI-REG", name="Flexi Cap - Regular", asset_class=AssetClass.EQUITY,
                              market_type=MarketType.PUBLIC, currency="INR", values_tags={"isin": "INF000000011", "tax_class": "equity_fund", "fund_plan": "regular"})
            share = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="SHARE", name="A Share", asset_class=AssetClass.EQUITY,
                               market_type=MarketType.PUBLIC, currency="INR", values_tags={})
            s.add_all([fund, share])
            await s.flush()

            async def line(name, *, person=rajesh, institution="Alpha MF", label="Folio 1", units=1000, value=1_000_000, symbol=None, isin=None, replaces=None):
                out = await held_away.record_holding(
                    s, hh.id, firm_id=firm.id, by="test", institution=institution, instrument_name=name, value=value, value_on=today - timedelta(days=3),
                    source_document="Consolidated statement, test", person_id=person.id if person else None, account_label=label, symbol=symbol,
                    isin=isin, quantity=units, replaces_id=replaces)
                return await s.get(HeldAwayHolding, uuid.UUID(out["holding_id"]))

            flexi = await line("Flexi Cap - Regular", symbol="FLEXI-REG")
            index = await line("Index Fund", label="Folio 1", units=500, value=500_000)
            sector = await line("Sector Fund", institution="Beta MF", label="Folio 9")
            blind = await line("No Cost Fund", institution="Beta MF", label="Folio 10", units=100, value=100_000)
            etf = await line("An ETF", institution="Old Broker", label="Demat OB-1", units=1000, value=200_000)
            gold = await line("Gold ETF", institution="Old Broker", label="Demat OB-1", units=500, value=60_000)
            meeras = await line("Meera Fund", person=meera, institution="Alpha MF", label="Folio 2")
            joint = await line("Joint Fund", person=None, institution="Gamma MF", label="Folio 3", units=None, value=50_000)
            ppf = await line("Public Provident Fund", institution="A bank", label=None, units=None, value=900_000)
            ppf.attributes = {"savings_kind": "ppf"}
            await s.flush()
            before = (await count(s, HeldAwayHolding), await count(s, Holding), await count(s, Order), float(account.cash_balance))

            print("\n=== Discovery: the roll-up of the lines on file ===")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            m = v["summary"]
            check("lines, folios, fund houses and demat accounts", (m["lines"], m["folios"], m["fund_houses"], m["demat_accounts"]), (8, 5, 3, 1))
            check("a fund house's folios and lines", [(f["name"], f["folios"], f["lines"]) for f in v["fund_houses"] if f["name"] == "Beta MF"], [("Beta MF", 2, 2)])
            check("nothing classified yet", m["classified"], {"keep_remap": 0, "transfer": 0, "exit": 0, "unclassified": 8})
            check("no cost on any line, so none is assessed", m["cost_not_on_file"], 8)
            check("what is missing is named", by_name(v, "No Cost Fund")["redeem"]["missing"], ["cost and acquisition date", "tax class"])
            check("a tax class is the instrument's where the firm has it, never the asset class",
                  (by_name(v, "Flexi Cap - Regular")["tax_class"], by_name(v, "Index Fund")["tax_class"]), ("equity_fund", None))
            check("a line without units says so", "units on the statement" in by_name(v, "Joint Fund")["redeem"]["missing"], True)
            check("a savings account is not a folio to consolidate",
                  (m["savings_not_listed"], [h for h in v["holdings"] if h["line_id"] == str(ppf.id)]), (1, []))
            await refused("nor classified", con.record_classification(s, firm, R.ADVISER, by="t", line_ids=[ppf.id], decision="keep_remap",
                                                                      reason="Keep it"), "nothing to consolidate")

            print("\n=== Facts a person reads off a document ===")
            F = lambda role, **t: con.record_facts(s, firm, role, by="test", **t)    # noqa: E731
            doc = "Capital gains statement, filed"
            await refused("compliance records none", F(R.COMPLIANCE, line_id=flexi.id, document=doc, form="folio"), "adviser")
            await refused("an administrator records none", F(R.ADMIN, line_id=flexi.id, document=doc, form="folio"), "adviser")
            await refused("no document, no figure", F(R.ADVISER, line_id=flexi.id, document="", form="folio"), "document")
            await refused("nothing to record", F(R.ADVISER, line_id=flexi.id, document=doc), "at least one")
            await refused("a lot that cannot be read", F(R.ADVISER, line_id=flexi.id, document=doc, lots="600 units at 500"), "units @")
            await refused("more units than the statement", F(R.ADVISER, line_id=flexi.id, document=doc, lots=f"1200 @ 500 on {ago(800)}"), "statement lists")
            await refused("a lot from the future", F(R.ADVISER, line_id=flexi.id, document=doc, lots=f"1 @ 500 on {(today + timedelta(1)).isoformat()}"), "future")
            await refused("an exit load as a percent", F(R.ADVISER, line_id=flexi.id, document=doc, exit_load_rate=1), "fraction")
            await refused("a tax class nobody has", F(R.ADVISER, line_id=flexi.id, document=doc, tax_class="debt"), "tax class")
            await F(R.ADVISER, line_id=flexi.id, document=doc, lots=f"600 @ 500 on {ago(800)}")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("lots for part of the units are not enough", by_name(v, "Flexi Cap - Regular")["redeem"]["missing"], ["lots for 1,000.000 units (recorded: 600.000)"])
            await F(R.OPERATIONS, line_id=flexi.id, document=doc, lots=f"600 @ 500 on {ago(800)}; 400 @ 1,200 on {ago(100)}",
                    exit_load_rate=0.01, exit_load_days=365, distributor_code="ARN-1")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            t = by_name(v, "Flexi Cap - Regular")["redeem"]
            check("long and short gain, oldest first", (t["assessed"], t["long_term"], t["short_term"], t["gain"]), (True, 300_000.0, -80_000.0, 220_000.0))
            # 220,000 net long-term, less 125,000 exempt = 95,000 at 12.5%, surcharge 10%, cess 4%.
            check("the tax on it, on the India engine", t["tax"], 13585.0)
            check("the exit load on the units held under a year", t["exit_load"], 4000.0)
            check("what redeeming would cost", t["cost_of_redeeming"], 17585.0)
            check("the day the short lot turns long", t["long_term_from"] is not None, True)
            check("the earlier record's facts stand where a later one is silent",
                  (by_name(v, "Flexi Cap - Regular")["distributor_code"], by_name(v, "Flexi Cap - Regular")["facts"]["exit_load_rate"]), ("ARN-1", 0.01))
            check("records are added, never edited", await count(s, ConsolidationFacts), 2)
            await F(R.ADVISER, line_id=index.id, document=doc, lots=f"500 @ 800 on {ago(900)}", tax_class="equity_fund")
            await F(R.ADVISER, line_id=sector.id, document=doc, lots=f"600 @ 700 on {ago(900)}; 400 @ 900 on {ago(100)}", tax_class="equity_fund")
            await F(R.ADVISER, line_id=etf.id, document=doc, lots=f"1000 @ 150 on {ago(900)}", tax_class="equity_fund")
            await F(R.ADVISER, line_id=meeras.id, document=doc, lots=f"1000 @ 100 on {ago(100)}", tax_class="specified_fund")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("no exit load recorded reads as none known", by_name(v, "Index Fund")["redeem"]["exit_load"], None)
            check("slab gains with no slab rate on file are not guessed", by_name(v, "Meera Fund")["redeem"]["tax"], 0.0)

            print("\n=== Classification: the adviser's, with its reason ===")
            C = lambda role, **t: con.record_classification(s, firm, role, by="test", **t)    # noqa: E731
            why = "The scheme still fits the plan"
            for role in (R.OPERATIONS, R.COMPLIANCE, R.ADMIN, R.PORTFOLIO_TEAM):
                await refused(f"{role.value} classifies nothing", C(role, line_ids=[flexi.id], decision="keep_remap", reason=why), "adviser")
            await refused("no reason", C(R.ADVISER, line_ids=[flexi.id], decision="keep_remap", reason=""), "reason")
            await refused("a keep takes no planned day", C(R.ADVISER, line_ids=[flexi.id], decision="keep_remap", reason=why, planned_on=today.isoformat()), "exit")
            sell = dict(line_ids=[sector.id], decision="exit", reason="A sector fund adds exposure already held", product_reason="concentration",
                        planned_on=(today + timedelta(10)).isoformat())
            await refused("an exit needs a reason about the product", C(R.ADVISER, **{**sell, "product_reason": None}), "about the product")
            await refused("consolidating is no reason to exit", C(R.ADVISER, **{**sell, "product_reason": "consolidation"}), "consolidating is not one")
            await refused("an exit says why in a sentence", C(R.ADVISER, **{**sell, "reason": "sell it"}), "why")
            await refused("an exit has a planned day", C(R.ADVISER, **{**sell, "planned_on": None}), "planned day")
            await refused("not a past day", C(R.ADVISER, **{**sell, "planned_on": ago(1)}), "past")
            await refused("one holding to an exit", C(R.ADVISER, **{**sell, "line_ids": [sector.id, blind.id]}), "one holding")
            await refused("no more units than held", C(R.ADVISER, **{**sell, "units": 2000}), "no more than")
            await C(R.PARAPLANNER, line_ids=[flexi.id, index.id], decision="transfer", reason=why)
            await C(R.ADVISER, line_ids=f"{flexi.id},{index.id}", decision="keep_remap", reason=why)
            await C(R.ADVISER, line_ids=[etf.id, gold.id], decision="transfer", reason="Moves in kind")
            await C(R.ADVISER, **sell)
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("the latest classification stands", by_name(v, "Index Fund")["decision"]["decision"], "keep_remap")
            check("and the earlier one is kept", await count(s, ConsolidationDecision), 7)
            check("the counts", v["summary"]["classified"], {"keep_remap": 2, "transfer": 2, "exit": 1, "unclassified": 3})
            trap = v["redeem_instead"]
            check("redeem instead covers keep and transfer, never the exit", (trap["holdings"], trap["assessed"], trap["not_assessed"]), (4, 3, 1))
            # Gains 220,000 + 100,000 + 50,000 = 370,000 long net; less 125,000 = 245,000 at 12.5% x 1.1 x 1.04.
            check("its gain, tax set off together, and load", (trap["gain"], trap["tax"], trap["exit_load"]), (370_000.0, 35035.0, 4000.0))

            print("\n=== The schedule against the gain budget ===")
            g = v["schedule"][0]
            row = g["rows"][0]
            check("one taxpayer, one year", (len(v["schedule"]), g["taxpayer"], len(g["rows"])), (1, "Rajesh Sharma", 1))
            check("the mandate's budget, named", (g["budget"]["amount"], "Rajesh - Balanced" in g["budget"]["source"]), (800000.0, True))
            check("the exit's gain on its day", (row["gain"], row["short_term"], row["long_term"]), (220_000.0, 40_000.0, 180_000.0))
            check("used and left", (g["used"], g["left"], g["over"], row["within_budget"]), (220_000.0, 580_000.0, 0.0, True))
            check("its tax is estimated", g["estimated_tax"] > 0, True)
            s.add(Transaction(firm_id=firm.id, account_id=account.id, instrument_id=share.id, txn_type="sell", quantity=100, price=7000, amount=700000,
                              trade_date=today, lineage={"lots": [{"quantity": 100, "cost": 50000, "acquired_on": ago(900)}]}))
            await s.flush()
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            g = v["schedule"][0]
            check("what the book realised this year counts against it", (g["realised_book"], g["used"], g["over"], g["rows"][0]["within_budget"]),
                  (650_000.0, 870_000.0, 70_000.0, False))
            await C(R.ADVISER, **{**sell, "units": 600})
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("part of a holding takes its oldest lots", (v["schedule"][0]["rows"][0]["gain"], v["schedule"][0]["rows"][0]["short_term"]), (180_000.0, 0.0))
            await C(R.ADVISER, **{**sell, "line_ids": [blind.id]})
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            unassessed = next(r for r in v["schedule"][0]["rows"] if r["name"] == "No Cost Fund")
            check("an exit with no cost is on the schedule, not assessed", (unassessed["assessed"], unassessed["gain"], unassessed["tax"], unassessed["within_budget"],
                                                                            v["schedule"][0]["not_assessed"]), (False, None, None, None, 1))
            await C(R.ADVISER, line_ids=[blind.id], decision="keep_remap", reason=why)
            await C(R.ADVISER, line_ids=[meeras.id], decision="exit", reason="Does not fit her risk profile", product_reason="unsuitable",
                    planned_on=(today + timedelta(10)).isoformat())
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            hers = next(g for g in v["schedule"] if g["taxpayer"] == "Meera Sharma")
            check("the platform's default budget is never used", (hers["budget"], hers["left"], hers["rows"][0]["within_budget"]), (None, None, None))
            check("a slab gain without a slab rate is flagged", "slab_rate" in hers["incomplete"], True)
            firm.settings = {**firm.settings, "foundation": {"default_cgt_budget": 100000}}
            await s.flush()
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            hers = next(g for g in v["schedule"] if g["taxpayer"] == "Meera Sharma")
            check("the firm's own default stands in, named", (hers["budget"]["amount"], hers["budget"]["source"]), (100000.0, "The firm's default"))
            await C(R.ADVISER, line_ids=[meeras.id], decision="keep_remap", reason=why)

            print("\n=== The code change tracker ===")
            O = lambda role=R.OPERATIONS, **t: con.open_item(s, firm, role, by="test", **t)    # noqa: E731
            S = lambda item, step, role=R.OPERATIONS, **t: con.record_step(s, firm, role, by="test", item_id=item, step=step,    # noqa: E731
                                                                           **{"evidence": "Filed letter", **t})
            ask = dict(kind="code_change", line_ids=[flexi.id], to_code="INA-1", evidence="Request form", on=ago(10))
            for role in (R.ADVISER, R.PARAPLANNER, R.COMPLIANCE, R.ADMIN):
                await refused(f"{role.value} opens no request", O(role, **ask), "operations")
            await refused("not for a holding classified otherwise", O(**{**ask, "line_ids": [etf.id]}), "classified keep and re-map")
            await refused("not for an unclassified one", O(**{**ask, "line_ids": [joint.id]}), "not classified")
            await refused("a code to re-map to", O(**{**ask, "to_code": ""}), "re-mapped")
            await refused("not the code it has", O(**{**ask, "to_code": "arn-1"}), "already carries")
            await refused("evidence", O(**{**ask, "evidence": ""}), "document")
            await refused("not a future day", O(**{**ask, "on": (today + timedelta(1)).isoformat()}), "future")
            out = await O(**ask)
            change = out["item_id"]
            item = next(i for i in out["items"] if i["item_id"] == change)
            check("it covers the folio's keep holdings", sorted(l["name"] for l in item["lines"]), ["Flexi Cap - Regular", "Index Fund"])
            check("with the code recorded and the new one", (item["details"]["from_code"], item["details"]["to_code"], item["status"]), ("ARN-1", "INA-1", "requested"))
            check("a regular plan is said to stay one", item["regular_plan"], True)
            await refused("no second open request on the folio", O(**ask), "already on an open")
            await refused("submitted only after consent", S(change, "submitted"), "what can follow is consented")
            await refused("an adviser records no step", S(change, "consented", role=R.ADVISER), "operations")
            await refused("consent has evidence", S(change, "consented", evidence=""), "document")
            await refused("not before the last step", S(change, "consented", on=ago(20)), "before the last step")
            await S(change, "consented", on=ago(9))
            await C(R.ADVISER, line_ids=[index.id], decision="transfer", reason="Changed my mind")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("a changed classification is flagged", next(i for i in v["items"] if i["item_id"] == change)["classification_changed"], True)
            await refused("and stops the submission", S(change, "submitted", on=ago(8)), "classified a holding")
            await C(R.ADVISER, line_ids=[index.id], decision="keep_remap", reason=why)
            await S(change, "submitted", on=ago(8))
            await refused("a rejection says why", S(change, "rejected", on=ago(7)), "why")
            await S(change, "rejected", on=ago(7), reason="Signature mismatch")
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("rejected, with the registrar's reason", [(i["status"], i["reject_reason"], i["open"]) for i in v["items"] if i["item_id"] == change],
                  [("rejected", "Signature mismatch", True)])
            await S(change, "submitted", on=ago(6))
            await refused("a confirmation carries the registrar's reference", S(change, "confirmed", on=ago(2)), "reference")
            out = await S(change, "confirmed", on=ago(2), reference="RTA-77")
            item = next(i for i in out["items"] if i["item_id"] == change)
            check("confirmed by the registrar", (item["status"], item["status_label"], item["reference"], item["days"], len(item["steps"])),
                  ("confirmed", "Confirmed by the registrar", "RTA-77", 8, 6))
            await refused("closed is closed", S(change, "withdrawn", reason="No longer needed"), "closed")
            await refused("a confirmed change is not asked twice", O(**ask), "already confirmed")

            print("\n=== An off-market demat transfer ===")
            move = dict(kind="demat_transfer", line_ids=[etf.id, gold.id], to_account="New demat", transfer_kind="own_account", reason_code="OWN-1",
                        evidence="Delivery instruction")
            await refused("a reason code, as on the instruction", O(**{**move, "reason_code": ""}), "never filled in")
            await refused("a receiving demat", O(**{**move, "to_account": ""}), "arrive in")
            await refused("own accounts state no consideration", O(**{**move, "consideration": 500}), "no consideration")
            await refused("a sale states one", O(**{**move, "transfer_kind": "sale"}), "consideration")
            p = await con.prepare_item(s, firm, R.OPERATIONS, **{**move, "transfer_kind": "sale", "consideration": 100000})
            check("stamp duty on a consideration, from the assumed rate", (p["stamp_duty"]["state"], p["stamp_duty"]["estimate"]), ("estimated", 15.0))
            p = await con.prepare_item(s, firm, R.OPERATIONS, **{**move, "transfer_kind": "other"})
            check("with no consideration stated for another reason, not assessed", p["stamp_duty"]["state"], "not_assessed")
            out = await O(**move)
            transfer = out["item_id"]
            item = next(i for i in out["items"] if i["item_id"] == transfer)
            check("own account: no duty, assumed", (item["stamp_duty"]["state"], item["stamp_duty"]["estimate"], item["details"]["reason_code"]),
                  ("none_no_consideration", 0.0, "OWN-1"))
            await S(transfer, "consented")
            await S(transfer, "submitted")
            out = await S(transfer, "confirmed", reference="CDSL-1", stamp_duty_stated=0)
            item = next(i for i in out["items"] if i["item_id"] == transfer)
            check("received: the old line is still listed, because nothing here moves it", (item["old_line_listed"], item["stamp_duty"]["stated"]), (True, 0.0))

            print("\n=== A PMS transition, and an exit's steps ===")
            await C(R.ADVISER, line_ids=[joint.id], decision="transfer", reason="Moves in kind")
            pms = dict(kind="pms_transition", line_ids=[joint.id], manager_from="Old PMS", manager_to="New PMS", to_account="Client demat", evidence="Letter")
            await refused("both managers named", O(**{**pms, "manager_to": ""}), "Portfolio Manager")
            out = await O(**pms)
            check("recorded in kind", [(i["kind"], i["status"]) for i in out["items"] if i["item_id"] == out["item_id"]], [("pms_transition", "requested")])
            await refused("an exit item only for an exit", O(kind="exit", line_ids=[flexi.id], evidence="Form"), "classified exit")
            out = await O(kind="exit", line_ids=[sector.id], evidence="Redemption request")
            exit_item = out["item_id"]
            check("the schedule shows where it stands", [r["status"] for r in out["schedule"][0]["rows"] if r["item_id"] == exit_item], ["requested"])
            await S(exit_item, "consented")
            await S(exit_item, "submitted")
            out = await S(exit_item, "confirmed", reference="STMT-9")
            g = out["schedule"][0]
            check("a redeemed exit still counts against the year", ([r["status"] for r in g["rows"]], g["exit_gain"], g["used"]), (["confirmed"], 180_000.0, 830_000.0))
            line_now = await s.get(HeldAwayHolding, sector.id)
            check("and its line is untouched", (line_now.status, float(line_now.quantity)), ("current", 1000.0))
            check("nothing moved a line, a holding, an order or cash",
                  (await count(s, HeldAwayHolding), await count(s, Holding), await count(s, Order), float((await s.get(Account, account.id)).cash_balance)), before)

            print("\n=== A newer statement keeps what was recorded ===")
            newer = await line("Index Fund", label="Folio 1", units=500, value=520_000, replaces=index.id)
            v = await con.for_household(s, hh.id, firm_id=firm.id)
            check("the classification and cost follow the holding", (by_name(v, "Index Fund")["line_id"] == str(newer.id), by_name(v, "Index Fund")["decision"]["decision"],
                                                                     by_name(v, "Index Fund")["redeem"]["gain"]), (True, "keep_remap", 120_000.0))
            await refused("the ended line takes nothing more", C(R.ADVISER, line_ids=[index.id], decision="keep_remap", reason=why), "no longer a current line")

            print("\n=== Scope: firm, market, roles ===")
            check("another firm reads nothing", await con.for_household(s, hh.id, firm_id=other.id), None)
            check("another firm's list is empty", (await con.for_firm(s, other.id))["households"], [])
            await refused("another firm classifies nothing", con.record_classification(s, other, R.ADVISER, by="x", line_ids=[flexi.id], decision="keep_remap", reason=why), "not on file")
            await refused("or steps nothing", con.record_step(s, other, R.OPERATIONS, by="x", item_id=change, step="withdrawn", evidence="Letter", reason="no"), "not on file")
            check("outside India it does not apply", ((await con.for_household(s, nz_hh.id, firm_id=nz.id))["applies"], (await con.for_firm(s, nz.id))["applies"]), (False, False))
            await refused("nor record", con.record_facts(s, nz, R.ADVISER, by="x", line_id=flexi.id, document=doc, form="folio"), "indian firm")
            firm_list = await con.for_firm(s, firm.id)
            check("the firm's list counts the work", [(r["household"], r["lines"], r["exits_scheduled"]) for r in firm_list["households"]], [("The Sharmas", 8, 1)])

            print("\n=== The tools and their cards ===")
            names = ["read_consolidation", "record_holding_facts", "classify_holding", "open_consolidation_request", "record_consolidation_step"]
            check("one read and four changes behind a Confirm card",
                  [TOOLS[n].change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION for n in names], [False, True, True, True, True])
            check("classifying is the adviser's and the paraplanner's", TOOLS["classify_holding"].roles_required, {R.ADVISER, R.PARAPLANNER})
            check("the tracker is operations'", [TOOLS[n].roles_required for n in names[3:]], [{R.OPERATIONS}, {R.OPERATIONS}])
            check("they match the module's rights", (TOOLS["classify_holding"].roles_required, TOOLS["record_holding_facts"].roles_required, TOOLS["read_consolidation"].roles_required),
                  (set(con.CLASSIFIERS), set(con.FACT_RECORDERS), set(con.READERS)))
            check("no client holds one", [n for n in names if n in CLIENT_TOOLS], [])
            check("the view opens on one client", ("consolidation" in OPEN_VIEWS, "consolidation" in ONE_CLIENT_VIEWS), (True, True))
            ex = lambda role: ToolExecutors(s, users[role].id, role, firm.id)    # noqa: E731
            r = await ex(R.ADVISER).execute("read_consolidation", {"household_id": str(hh.id)})
            check("the read answers with figures and says it is an estimate", ("folio(s)" in r["message"], "estimates" in r["message"], r["applies"]), (True, True, True))
            check("each law figure carries counsel's state", {f["counsel"]["state"] for f in r["law"]}, {"not_reviewed"})
            check("with no household it lists them", len((await ex(R.OPERATIONS).execute("read_consolidation", {}))["households"]), 1)
            rows = lambda card: {x["label"]: x["value"] for x in card["rows"]}    # noqa: E731
            counts = (await count(s, ConsolidationFacts), await count(s, ConsolidationDecision), await count(s, ConsolidationItem), await count(s, ConsolidationStep))
            card = rows(await ex(R.ADVISER).preview_change("record_holding_facts", {"line_id": str(gold.id), "document": doc, "lots": f"500 @ 100 on {ago(500)}", "tax_class": "equity_fund"}))
            check("the facts card names the holding and what redeeming would realise", ("Gold ETF" in card["Holding"], "Gain" in card["If redeemed today"]), (True, True))
            card = rows(await ex(R.ADVISER).preview_change("classify_holding", {"line_ids": str(flexi.id), "decision": "exit", "reason": "Costs more than the direct plan",
                                                                                 "product_reason": "cost", "planned_on": (today + timedelta(5)).isoformat()}))
            check("the exit card shows the tax, the budget and that nothing is sold", ("tax about" in card["On that day"], "Gain budget" in card, "nothing is sold" in card["Note"]), (True, True, True))
            out = await ex(R.ADVISER).execute("classify_holding", {"line_ids": str(gold.id), "decision": "keep_remap", "reason": why})
            check("the tool records and says nothing moved", "No holding moved" in out["message"], True)
            await C(R.ADVISER, line_ids=[gold.id], decision="transfer", reason="Moves in kind after all")
            card = rows(await ex(R.OPERATIONS).preview_change("open_consolidation_request", {"kind": "code_change", "line_ids": str(meeras.id), "from_code": "ARN-1", "to_code": "INA-1", "evidence": "Form"}))
            check("the request card names the codes and that nothing is sent", (card["Distributor code"], "nothing is sent" in card["Note"]), ("ARN-1 to INA-1", True))
            out = await ex(R.OPERATIONS).execute("open_consolidation_request", {"kind": "code_change", "line_ids": str(meeras.id), "from_code": "ARN-1", "to_code": "INA-1", "evidence": "Form"})
            card = rows(await ex(R.OPERATIONS).preview_change("record_consolidation_step", {"item_id": out["item_id"], "step": "consented", "evidence": "Signed consent"}))
            check("the step card says what it is and becomes", (card["Now"], card["Becomes"]), ("Requested", "Consented by the client"))
            done = await ex(R.OPERATIONS).execute("record_consolidation_step", {"item_id": out["item_id"], "step": "consented", "evidence": "Signed consent"})
            check("the step is recorded", next(i["status"] for i in done["items"] if i["item_id"] == out["item_id"]), "consented")
            for label, role, tool, inputs, words in (
                    ("a card is refused where the write would be", R.ADVISER, "classify_holding", {"line_ids": str(flexi.id), "decision": "exit", "reason": why}, "about the product"),
                    ("operations is offered no classification card", R.OPERATIONS, "classify_holding", {"line_ids": str(flexi.id), "decision": "keep_remap", "reason": why}, "adviser"),
                    ("an adviser is offered no step card", R.ADVISER, "record_consolidation_step", {"item_id": out["item_id"], "step": "submitted", "evidence": "Ack"}, "operations")):
                await refused(label, ex(role).preview_change(tool, inputs), words, exc=(ExecutorError,))
            after = (await count(s, ConsolidationFacts), await count(s, ConsolidationDecision), await count(s, ConsolidationItem), await count(s, ConsolidationStep))
            check("a card writes nothing: only the confirmed calls did", tuple(a - b for a, b in zip(after, counts)), (0, 2, 1, 2))
            events = set((await s.execute(select(LedgerEntry.event_type).where(LedgerEntry.firm_id == firm.id))).scalars().all())
            check("each change is on the ledger", {"consolidation_facts_recorded", "consolidation_classified", "consolidation_item_opened", "consolidation_step_recorded"} <= events, True)

        print("\n=== The law figures ===")
        for key in ("in_code_change_rule", "in_same_owner_transfer_rule", "in_stamp_duty_securities_transfer", "in_stamp_duty_no_consideration_rule"):
            f = law_figures.get(key)
            check(f"{key} is marked assumed with a source, and counsel's list has it", (f.source.startswith("ASSUMED"), key in counsel_review.india_keys()), (True, True))
    finally:
        await db.dispose()


async def demonstration() -> None:
    print("\n=== The demonstration firm ===")
    from app.agents import drift_rebalancing as drift
    from app.agents import tax_intelligence as tax_agent
    from seed import india_consolidation, india_demo

    async def _no_research(*a, **k):
        return []

    async def _fallback(ctx, *, fallback, **k):
        return fallback

    drift.knowledge.retrieve = _no_research
    drift.narrate = _fallback
    tax_agent.narrate = _fallback
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = await india_demo.build(s)
            await s.commit()
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            v = await con.for_household(s, homes["The Sharma Family"].id, firm_id=firm.id)
            m = v["summary"]
            check("fourteen folios across five fund houses, and a legacy demat", (m["folios"], m["fund_houses"], m["demat_accounts"]), (14, 5, 1))
            check("ten kept, two moving, one exit, three to classify", m["classified"], {"keep_remap": 10, "transfer": 2, "exit": 1, "unclassified": 3})
            check("ten code changes: five confirmed, one rejected, four at the registrar", m["code_changes"], {"submitted": 4, "confirmed": 5, "rejected": 1})
            check("no direct plan carries a distributor code",
                  [h["name"] for h in v["holdings"] if "Direct Plan" in h["name"] and h["distributor_code"]], [])
            check("two ETF transfers on their way", m["demat_transfers"], {"consented": 1, "submitted": 1})
            g = v["schedule"][0]
            check("the one exit is within Rajesh's gain budget", (len(v["schedule"]), g["taxpayer"], len(g["rows"]), g["budget"]["amount"], g["rows"][0]["within_budget"]),
                  (1, "Rajesh Sharma", 1, 800000.0, True))
            check("its tax is estimated", (g["rows"][0]["assessed"], g["estimated_tax"] > 0, g["rows"][0]["exit_load"] > 0), (True, True, True))
            t = v["redeem_instead"]
            check("redeeming instead has a figure, and says what is not assessed", (t["tax"] > 0, t["assessed"], t["not_assessed"]), (True, 11, 1))
            k = await con.for_household(s, homes["The Kapoor Family Trust"].id, firm_id=firm.id)
            check("the Kapoor trust's shares move in kind", k["summary"]["pms_transitions"], {"consented": 1})
            long = []
            for model in (HeldAwayHolding, ConsolidationFacts, ConsolidationDecision, ConsolidationItem, ConsolidationStep):
                limits = {c.name: c.type.length for c in model.__table__.columns if isinstance(c.type, String) and c.type.length}
                for row in (await s.execute(select(model))).scalars().all():
                    long += [(model.__tablename__, n) for n, size in limits.items() if len(str(getattr(row, n) or "")) > size]
            check("every seeded string fits its column", long, [])
            check("every seeded record says it is invented",
                  [d.reason for d in (await s.execute(select(ConsolidationDecision))).scalars().all() if "invented" not in d.reason], [])
            before = [await count(s, x) for x in (HeldAwayHolding, ConsolidationFacts, ConsolidationDecision, ConsolidationItem, ConsolidationStep)]
            check("a second run adds nothing", await india_consolidation.add(s, firm), 0)
            check("no row", [await count(s, x) for x in (HeldAwayHolding, ConsolidationFacts, ConsolidationDecision, ConsolidationItem, ConsolidationStep)], before)
            # This seed's own exit: the Mehtas (B-400) schedule one too.
            exit_row = (await s.execute(select(ConsolidationDecision).where(ConsolidationDecision.decision == "exit",
                                                                             ConsolidationDecision.decided_by == india_consolidation.BY))).scalars().one()
            exit_row.planned_on = firm_time.today(firm) - timedelta(days=3)
            await s.flush()
            check("a planned exit that has come due is moved ahead", (await india_consolidation.add(s, firm), exit_row.planned_on > firm_time.today(firm)), (1, True))
            # A firm built before the direct folio was put right: this seed's own code, classification and request on it.
            by, ops = india_consolidation.BY, R.OPERATIONS
            direct = (await s.execute(select(HeldAwayHolding).where(HeldAwayHolding.account_label == "Folio 3000303/76",
                                                                    HeldAwayHolding.status == "current"))).scalars().one()
            await con.record_facts(s, firm, ops, by=by, line_id=direct.id, form="folio", distributor_code=india_consolidation.FROM_CODE,
                                   document="Statement (test)")
            await con.record_classification(s, firm, R.ADVISER, by=by, line_ids=[direct.id], decision="keep_remap", reason="As seeded before (test)")
            await con.open_item(s, firm, ops, by=by, kind="code_change", line_ids=[direct.id], to_code=india_consolidation.TO_CODE,
                                on=firm_time.today(firm), evidence="Form (test)")
            check("the old demonstration request is taken off", await india_consolidation.add(s, firm), 1)
            v = await con.for_household(s, homes["The Sharma Family"].id, firm_id=firm.id)
            check("  with its classification and its code",
                  (v["summary"]["classified"]["unclassified"], v["summary"]["code_changes"]["submitted"],
                   [h["distributor_code"] for h in v["holdings"] if h["account_label"] == "Folio 3000303/76"]), (3, 4, [None]))
    finally:
        await db.dispose()


async def main() -> int:
    await unit()
    await demonstration()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
