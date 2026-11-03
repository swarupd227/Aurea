"""Prove a Confirm card names the change: every tool that stops for one shows the record and the figures,
refuses what doing it would refuse, and writes nothing by being shown.

Runs against a throwaway SQLite database.

    python -m tests.verify_confirm_cards
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import consent, protection, reconciliation, risk_profile as rp
from app.core.db import Base, utcnow
from app.core.tools import TOOLS, ToolChangeState
from app.llm import executors
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.billing import BillingRun, Invoice
from app.models.compliance_program import ConflictInventoryItem, WSPRule
from app.models.connectors import Connector
from app.models.consent import TradeConsent
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import ConnectorDomain, MarketType, RecommendationStatus, UserRole
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Account, AccountBeneficiary, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, InvestmentPolicy, ModelPortfolio, Price, TargetAllocation, TaxLot
from app.models.protection import ProtectionPolicy
from app.models.reconciliation import CustodyStatement, ReconBreak
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm
from app.models.trading import Order
from app.models.valuations import ValuationStatement

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ExecutorError as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:90]}")
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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="o@t", full_name="O", role=UserRole.OPERATIONS, hashed_password="x")
            s.add_all([adviser, ops])
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Ann")
            s.add(h)
            await s.flush()
            person = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee",
                            profile={"tax": {"annual_income": 90000}})
            s.add(person)
            model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Growth", drift_band=0.05)
            s.add(model)
            await s.flush()
            s.add(TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", target_weight=0.8))
            mandate = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name="Ann mandate", mandate_type="advisory",
                              suitability={"risk_profile": "growth", "capacity_for_loss": "high"}, constraints={},
                              model_portfolio_id=model.id)
            s.add(mandate)
            await s.flush()
            account = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mandate.id, name="Ann A/C", custodian="Custody Co",
                              currency="NZD", cash_balance=1000)
            s.add(account)
            aaa = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="AAA", asset_class="equity",
                             market_type=MarketType.PUBLIC, currency="NZD")
            s.add(aaa)
            await s.flush()
            s.add(Holding(firm_id=firm.id, account_id=account.id, instrument_id=aaa.id, quantity=100, market_value=10000, cost_basis=9000))
            await s.flush()

            ex = ToolExecutors(s, adviser.id, adviser.role, firm.id)
            run = ToolExecutors(s, ops.id, ops.role, firm.id)
            outsider = ToolExecutors(s, adviser.id, adviser.role, other.id)
            today = utcnow().date()

            async def card(executor, tool, inputs):
                preview = await executor.preview_change(tool, inputs)
                return {r["label"]: r["value"] for r in preview["rows"]}

            async def count(model_):
                return (await s.execute(select(func.count()).select_from(model_))).scalar()

            print("\n=== Recording a policy ===")
            args = {"person_id": str(person.id), "kind": "life", "provider": "Acme Life", "sum_insured": 500000,
                    "source_document": "Policy schedule, 1 Mar 2026"}
            shown = await card(ex, "record_protection_policy", args)
            check("names who, what, whose and how much", (shown["Insured"], shown["Cover"], shown["Provider"], "500,000" in shown["Sum insured"]),
                  ("Ann Lee", "Life", "Acme Life", True))
            check("and where it comes from", shown["Document"], "Policy schedule, 1 Mar 2026")
            check("money reads in the firm's market", shown["Sum insured"].startswith("$") or "NZ" in shown["Sum insured"], True)
            check("showing it writes nothing", await count(ProtectionPolicy), 0)
            shown = await card(ex, "record_protection_policy", {**args, "kind": "income_protection", "sum_insured": None,
                                                                "annual_benefit": 60000, "benefit_to_age": 65,
                                                                "expires_on": (today + timedelta(days=400)).isoformat()})
            check("income cover shows its benefit and age", ("60,000" in shown["Annual benefit"], shown["Benefit to age"], "Sum insured" in shown), (True, "65", False))
            await refused("no sum insured", ex.preview_change("record_protection_policy", {**args, "sum_insured": None}), "needs its sum insured")
            await refused("an unknown kind", ex.preview_change("record_protection_policy", {**args, "kind": "pet"}), "unknown policy kind")
            await refused("no document", ex.preview_change("record_protection_policy", {**args, "source_document": ""}), "which document")
            await refused("expired cover", ex.preview_change("record_protection_policy", {**args, "expires_on": (today - timedelta(days=1)).isoformat()}), "expired")
            await refused("a date it cannot read", ex.preview_change("record_protection_policy", {**args, "expires_on": "soon"}), "YYYY-MM-DD")
            await refused("another firm's person", outsider.preview_change("record_protection_policy", args), "not in this firm")
            done = await ex.record_protection_policy(args)
            check("the write goes through the same checks", (done["provider"], await count(ProtectionPolicy)), ("Acme Life", 1))

            print("\n=== Ending a policy ===")
            ending = {"policy_id": done["policy_id"], "reason": "Lapsed in March"}
            shown = await card(ex, "end_protection_policy", ending)
            check("names the cover being lost", (shown["Insured"], shown["Cover"], "500,000" in shown["Amount"], shown["Reason"]),
                  ("Ann Lee", "Life", True, "Lapsed in March"))
            await refused("no reason", ex.preview_change("end_protection_policy", {**ending, "reason": ""}), "say why")
            await refused("another firm's policy", outsider.preview_change("end_protection_policy", ending), "not found")
            check("showing it ends nothing", (await s.get(ProtectionPolicy, uuid.UUID(done["policy_id"]))).status, "in_force")
            await ex.end_protection_policy(ending)
            await refused("one already ended", ex.preview_change("end_protection_policy", ending), "already ended")

            print("\n=== Recording an income ===")
            income = {"person_id": str(person.id), "annual_income": 120000, "source_document": "Payslip, Oct 2026"}
            shown = await card(ex, "record_income", income)
            check("names the person, the figure and what it replaces", (shown["Person"], "120,000" in shown["Income"], "90,000" in shown["Replaces"]),
                  ("Ann Lee", True, True))
            check("showing it changes nothing", protection.income_of(person), 90000.0)
            await refused("no figure", ex.preview_change("record_income", {**income, "annual_income": None}), "annual income")
            await refused("a negative one", ex.preview_change("record_income", {**income, "annual_income": -1}), "cannot be negative")
            bare = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Bo Lee")
            s.add(bare)
            await s.flush()
            shown = await card(ex, "record_income", {**income, "person_id": str(bare.id)})
            check("a first income says nothing was on file", shown["Replaces"], "Nothing on file")
            await ex.record_income(income)
            check("the write goes through the same checks", protection.income_of(person), 120000.0)

            print("\n=== A risk decision ===")
            decide = {"mandate_id": str(mandate.id), "decision": "keep", "reason": "Client has other assets"}
            await refused("no questionnaire to decide on", ex.preview_change("decide_risk_profile", decide), "no scored questionnaire")
            s.add(RiskAssessment(firm_id=firm.id, household_id=h.id, answers={}, scoring_version=1, tolerance="balanced",
                                 capacity="medium", indicated="balanced", completed_by="c@t", completed_by_role="client",
                                 completed_at=utcnow()))
            await s.flush()
            shown = await card(ex, "decide_risk_profile", decide)
            check("keeping shows what stays and why", (shown["Mandate"], shown["Decision"], shown["On file"], shown["Questionnaire"], shown["Becomes"], shown["Reason"]),
                  ("Ann mandate", "Keep what is on file", "growth · high", "balanced · medium", "growth · high", "Client has other assets"))
            await refused("keeping needs its reason", ex.preview_change("decide_risk_profile", {**decide, "reason": ""}), "needs the reason")
            await refused("adopting past the model's equity", ex.preview_change("decide_risk_profile", {**decide, "decision": "adopt"}), "above the 70% ceiling")
            await refused("an unknown decision", ex.preview_change("decide_risk_profile", {**decide, "decision": "maybe"}), "must be one of")
            await refused("another firm's mandate", outsider.preview_change("decide_risk_profile", decide), "not found")
            (await s.execute(select(TargetAllocation).where(TargetAllocation.model_id == model.id))).scalar_one().target_weight = 0.6
            await s.flush()
            shown = await card(ex, "decide_risk_profile", {**decide, "decision": "adopt", "reason": None})
            check("adopting shows what it becomes", (shown["Decision"], shown["Becomes"]), ("Adopt the questionnaire", "balanced · medium"))
            check("showing it changes nothing", mandate.suitability["risk_profile"], "growth")
            out = await rp.decide(s, firm.id, mandate.id, decision="adopt", reason=None, by="a@t")
            check("the write goes through the same checks", out["risk_profile"], "balanced")

            print("\n=== Importing a statement ===")
            day = (today - timedelta(days=1)).isoformat()
            statement = {"account_id": str(account.id), "as_of": day, "positions": [{"symbol": "AAA", "quantity": 90}],
                         "cash_balance": 1000, "source_document": "Custodian statement, 30 Oct 2026"}
            shown = await card(run, "import_custody_statement", statement)
            check("names the account and every position", (shown["Account"], shown["Custodian"], shown["Positions"]), ("Ann A/C", "Custody Co", "1: AAA 90"))
            check("the cash and the document", ("1,000" in shown["Cash"], shown["Document"]), (True, "Custodian statement, 30 Oct 2026"))
            check("showing it writes nothing", await count(CustodyStatement), 0)
            shown = await card(run, "import_custody_statement", {**statement, "cash_balance": None})
            check("no cash is said", shown["Cash"], "Not on the statement")
            await refused("a date to come", run.preview_change("import_custody_statement", {**statement, "as_of": (today + timedelta(days=2)).isoformat()}), "future")
            await refused("a date it cannot read", run.preview_change("import_custody_statement", {**statement, "as_of": "x"}), "YYYY-MM-DD")
            await refused("no document", run.preview_change("import_custody_statement", {**statement, "source_document": ""}), "which statement")
            await refused("nothing on it", run.preview_change("import_custody_statement", {**statement, "positions": [], "cash_balance": None}), "nothing on this statement")
            await refused("a position twice", run.preview_change("import_custody_statement", {**statement, "positions": [{"symbol": "AAA", "quantity": 1}, {"symbol": "aaa", "quantity": 2}]}), "twice")
            await refused("another firm's account", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("import_custody_statement", statement), "not in this firm")
            await run.import_custody_statement(statement)
            check("the write goes through the same checks", await count(CustodyStatement), 1)
            await refused("a statement older than the one on file", run.preview_change("import_custody_statement", {**statement, "as_of": (today - timedelta(days=5)).isoformat()}), "already on file")

            print("\n=== Deciding a break ===")
            row = (await s.execute(select(ReconBreak))).scalars().first()
            decision = {"break_id": str(row.id), "action": "escalated"}
            shown = await card(run, "resolve_recon_break", decision)
            check("names the break and its figures", (shown["Break"], shown["Account"], shown["Book"], shown["Custodian"], shown["Difference"]),
                  ("AAA", "Ann A/C", "100", "90", "-10"))
            check("and the decision", shown["Decision"], "Escalate to the custodian")
            await refused("setting aside needs a reason", run.preview_change("resolve_recon_break", {**decision, "action": "dismissed"}), "say why")
            await refused("an unknown action", run.preview_change("resolve_recon_break", {**decision, "action": "ignore"}), "unknown action")
            await refused("another firm's break", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("resolve_recon_break", decision), "not found")
            check("showing it changes nothing", str(row.status), "open")
            await run.resolve_recon_break({**decision, "action": "dismissed", "note": "Checked with the custodian"})
            check("the write goes through the same checks", str(row.status), "dismissed")
            await refused("a break already decided", run.preview_change("resolve_recon_break", decision), "already")

            print("\n=== Placing orders ===")
            bbb = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="BBB", name="BBB", asset_class="equity",
                             market_type=MarketType.PUBLIC, currency="NZD")
            ccc = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="CCC", name="CCC", asset_class="equity",
                             market_type=MarketType.PUBLIC, currency="NZD", values_tags={"flags": ["tobacco"]})
            s.add_all([bbb, ccc])
            await s.flush()
            s.add_all([Price(firm_id=firm.id, instrument_id=aaa.id, as_of=today, close=12.5, currency="NZD", source="test", is_real=True),
                       Price(firm_id=firm.id, instrument_id=bbb.id, as_of=today, close=3, currency="NZD", source="seed", is_real=False),
                       Price(firm_id=firm.id, instrument_id=ccc.id, as_of=today, close=5, currency="NZD", source="test", is_real=True)])
            await s.flush()
            held = (await s.execute(select(Holding).where(Holding.instrument_id == aaa.id))).scalar_one()
            s.add(TaxLot(firm_id=firm.id, holding_id=held.id, quantity=100, cost_per_unit=8, acquired_on=today - timedelta(days=200)))
            await s.flush()
            mandate.suitability = {**mandate.suitability, "capacity_for_loss": "high"}
            place = {"mandate_id": str(mandate.id), "orders": [{"symbol": "AAA", "side": "buy", "quantity": 10}], "reason": "Top up"}
            shown = await card(ex, "execute_orders", place)
            check("names the mandate and the account", (shown["Mandate"], shown["Account"]), ("Ann mandate", "Ann A/C · Custody Co"))
            check("each order at its price, to the cent", ("Buy 10 AAA at about" in shown["Order 1"], "12.50" in shown["Order 1"]), (True, True))
            check("what it does to the cash", "875" in shown["Cash, before fees"], True)
            check("a paper venue says so", shown["Venue"].startswith("Paper"), True)
            check("no IPS, nothing said of one", "IPS" in shown, False)
            check("and why", shown["Reason"], "Top up")
            check("showing it places nothing", await count(Order), 0)
            mixed = {**place, "orders": [{"symbol": "AAA", "side": "sell", "quantity": 4}, {"symbol": "BBB", "side": "BUY", "quantity": 1}]}
            shown = await card(ex, "execute_orders", mixed)
            check("two orders get a row each", (shown["Order 1"].startswith("Sell 4 AAA"), "no real price" in shown["Order 2"]), (True, True))
            check("a sale shows its estimated gain", "gain about $18" in shown["Order 1"], True)
            check("one that cannot fill is named", shown["Will not fill"], "BBB")
            check("and leaves the cash alone", "1,050" in shown["Cash, before fees"], True)
            await refused("nothing that could fill", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "BBB", "quantity": 1}]}), "none of these")
            await refused("not a buy or a sell", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "AAA", "side": "hold", "quantity": 1}]}), "a buy or a sell")
            await refused("a negative quantity", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "AAA", "quantity": -1}]}), "above zero")
            await refused("a quantity that is no number", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "AAA", "quantity": "ten"}]}), "must be a number")
            await refused("an unknown symbol", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "ZZZ", "quantity": 1}]}), "unknown instrument")
            await refused("no orders", ex.preview_change("execute_orders", {**place, "orders": []}), "required")
            await refused("another firm's mandate", outsider.preview_change("execute_orders", place), "not found")
            s.add(InvestmentPolicy(firm_id=firm.id, mandate_id=mandate.id, version=1, accepted_on=today, targets={"equity": 1.0}, drift_band=0.05,
                                   restricted=["tobacco"], document="IPS v1", recorded_by="a@t", recorded_by_role="adviser"))
            await s.flush()
            await refused("a purchase the IPS restricts", ex.preview_change("execute_orders", {**place, "orders": [{"symbol": "CCC", "quantity": 1}]}), "restricted by IPS version 1")
            shown = await card(ex, "execute_orders", place)
            check("otherwise it says the order was measured", shown["IPS"], "Version 1: breaches no term and widens no breach")
            done = await ex.execute_orders(place)
            check("the write goes through the same checks", (done["orders_count"], await count(Order)), (1, 1))

            print("\n=== A direct order where the client must consent, and the client's answer ===")
            india = Firm(id=uuid.uuid4(), name="I", slug="i", base_currency="INR", jurisdiction="IN", regulator="SEBI",
                         settings={"licences": ["investment_adviser"]})
            s.add(india)
            await s.flush()
            ih = Household(id=uuid.uuid4(), firm_id=india.id, name="Rao")
            s.add(ih)
            await s.flush()
            ip = Person(id=uuid.uuid4(), firm_id=india.id, household_id=ih.id, full_name="Asha Rao")
            s.add(ip)
            await s.flush()
            im = Mandate(id=uuid.uuid4(), firm_id=india.id, person_id=ip.id, name="Rao mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add(im)
            await s.flush()
            ia = Account(id=uuid.uuid4(), firm_id=india.id, mandate_id=im.id, name="Rao A/C", currency="INR", cash_balance=100000)
            tcs = Instrument(id=uuid.uuid4(), firm_id=india.id, symbol="TCS", name="TCS", asset_class="equity", market_type=MarketType.PUBLIC, currency="INR")
            s.add_all([ia, tcs])
            await s.flush()
            indian = ToolExecutors(s, adviser.id, adviser.role, india.id)
            await refused("a direct order is refused", indian.preview_change(
                "execute_orders", {"mandate_id": str(im.id), "reason": "Test", "orders": [{"symbol": "TCS", "quantity": 1}]}), "through a proposal")
            agent_run = AgentRun(firm_id=india.id, agent_key="drift_rebalancing", status="awaiting", tier="tier_2", trigger="manual", started_at=utcnow())
            s.add(agent_run)
            await s.flush()
            order_set = [{"side": "buy", "symbol": "TCS", "instrument_id": str(tcs.id), "account_id": str(ia.id), "quantity": 5, "est_value": 20000}]
            rec = Recommendation(firm_id=india.id, run_id=agent_run.id, agent_key="drift_rebalancing", tier="tier_2", status=RecommendationStatus.PROPOSED,
                                 title="Rebalance Rao mandate", summary="x", subject_type="mandate", subject_id=im.id, subject_label="Rao mandate",
                                 payload={"order_set": order_set})
            s.add(rec)
            await s.flush()
            answer = {"recommendation_id": str(rec.id), "given": True, "channel": "email", "evidence_ref": "Email from Asha, 1 Nov"}
            await refused("no consent asked for yet", indian.preview_change("record_client_consent", answer), "not waiting")
            held = await consent.hold(s, firm=india, rec=rec, requested_by="a@t")
            check("the proposal waits", held["status"], "awaiting")
            shown = await card(indian, "record_client_consent", answer)
            check("names the proposal and the client's answer", (shown["Proposal"], shown["Mandate"], shown["Answer"]),
                  ("Rebalance Rao mandate", "Rao mandate", "Yes: the orders are placed now"))
            check("how it came, and the orders it covers", (shown["How"], shown["Evidence"], "Buy 5 TCS" in shown["Orders"]), ("email", "Email from Asha, 1 Nov", True))
            shown = await card(indian, "record_client_consent", {**answer, "given": False, "channel": "recorded_call"})
            check("a no places nothing", (shown["Answer"], shown["How"]), ("No: nothing is placed", "recorded call"))
            await refused("no evidence", indian.preview_change("record_client_consent", {**answer, "evidence_ref": ""}), "on file")
            await refused("simulated, at a real firm", indian.preview_change("record_client_consent", {**answer, "channel": "simulated"}), "demonstration")
            await refused("an unknown channel", indian.preview_change("record_client_consent", {**answer, "channel": "smoke"}), "channel must be")
            await refused("another firm's proposal", ex.preview_change("record_client_consent", answer), "not found")
            rec.payload = {"order_set": [{**order_set[0], "quantity": 6}]}
            await refused("orders that have changed", indian.preview_change("record_client_consent", answer), "changed")
            rec.payload = {"order_set": order_set}
            check("showing it records nothing", (await s.execute(select(TradeConsent))).scalar_one().status, "awaiting")

            print("\n=== Billing ===")
            shown = await card(run, "run_billing", {})
            check("nothing to bill says so", (shown["Bills"], shown["To issue"], shown["Held by a block"]), ("0", "Nothing", "0"))
            check("showing it drafts nothing", await count(BillingRun), 0)
            billing = BillingRun(firm_id=firm.id, as_of=today, status="proposed", summary={})
            s.add(billing)
            await s.flush()
            shown = await card(run, "run_billing", {})
            check("a run already waiting is named", (today.isoformat() in shown["Awaiting approval"], "Bills" in shown), (True, False))
            invoice = Invoice(firm_id=firm.id, run_id=billing.id, invoice_number="INV-7", mandate_name="Ann mandate", household_name="Ann",
                              period_start=date(2026, 7, 1), period_end=date(2026, 9, 30), amount=1250, currency="NZD", status="debited",
                              debit_status="posted", debit_detail={"accounts": [{"account_id": str(account.id), "amount": 1250}]})
            s.add(invoice)
            await s.flush()
            credit = {"invoice_id": str(invoice.id), "reason": "Billed on the wrong value"}
            shown = await card(run, "credit_invoice", credit)
            check("names the invoice, client and period", (shown["Invoice"], shown["Client"], shown["Period"]), ("INV-7", "Ann", "2026-07-01 to 2026-09-30"))
            check("what comes back, and why", ("1,250.00" in shown["Credited"], "1,250.00" in shown["Back to cash"], shown["Reason"]), (True, True, "Billed on the wrong value"))
            await refused("no reason", run.preview_change("credit_invoice", {**credit, "reason": ""}), "reason is required")
            await refused("an adviser", ex.preview_change("credit_invoice", credit), "only operations")
            await refused("another firm's invoice", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("credit_invoice", credit), "not found")
            check("showing it credits nothing", (await count(Invoice), invoice.status), (1, "debited"))
            invoice.status = "draft"
            await refused("a draft", run.preview_change("credit_invoice", credit), "cannot be credited")
            invoice.status = "debited"
            await s.delete(invoice)
            await s.delete(billing)
            await s.flush()

            print("\n=== A private fund's valuation ===")
            fund = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="PVT", name="Private Fund", asset_class="alternatives",
                              market_type=MarketType.PRIVATE, currency="NZD")
            s.add(fund)
            await s.flush()
            s.add(Holding(firm_id=firm.id, account_id=account.id, instrument_id=fund.id, quantity=50, market_value=5000, cost_basis=4000))
            s.add(Price(firm_id=firm.id, instrument_id=fund.id, as_of=today - timedelta(days=30), close=100, currency="NZD", source="statement", is_real=True))
            await s.flush()
            value = {"symbol": "PVT", "unit_value": 110, "as_of": (today - timedelta(days=1)).isoformat(), "source_document": "Manager statement, Oct 2026"}
            shown = await card(run, "record_valuation", value)
            check("names the fund and the new unit value", (shown["Fund"], shown["Unit value"]), ("PVT · Private Fund", "110.0000 NZD"))
            check("what it replaces and the move", ("100.0000" in shown["Was"], shown["Change"]), (True, "+10.0%"))
            check("what it does to the value held", ("5,000" in shown["Value held"], "5,500" in shown["Value held"], shown["Holdings revalued"]), (True, True, "1"))
            check("and the document", shown["Document"], "Manager statement, Oct 2026")
            await refused("a listed security", run.preview_change("record_valuation", {**value, "symbol": "AAA"}), "listed security")
            await refused("no document", run.preview_change("record_valuation", {**value, "source_document": ""}), "source document")
            await refused("a value of nothing", run.preview_change("record_valuation", {**value, "unit_value": 0}), "greater than zero")
            await refused("not a number", run.preview_change("record_valuation", {**value, "unit_value": "lots"}), "must be a number")
            await refused("a date to come", run.preview_change("record_valuation", {**value, "as_of": (today + timedelta(days=3)).isoformat()}), "future")
            await refused("another firm's fund", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("record_valuation", value))
            check("showing it writes nothing", await count(ValuationStatement), 0)
            await run.record_valuation(value)
            check("the write goes through the same checks", await count(ValuationStatement), 1)

            print("\n=== Refreshing prices ===")
            await refused("no feed at all", run.preview_change("refresh_prices", {}), "switched off")
            feed = Connector(firm_id=firm.id, domain=ConnectorDomain.MARKET_RESEARCH_DATA, provider_key="marketdata.test", display_name="Feed",
                             use_mock=True)
            s.add(feed)
            await s.flush()
            await refused("a feed switched off", run.preview_change("refresh_prices", {}), "switched off")
            feed.use_mock = False
            feed.last_synced_at = utcnow()
            await refused("fetched moments ago", run.preview_change("refresh_prices", {}), "try again")
            feed.last_synced_at = utcnow() - timedelta(hours=3)
            synced = feed.last_synced_at
            shown = await card(run, "refresh_prices", {})
            check("counts what is held and what is not current", (shown["Held instruments"], "Last fetched" in shown), ("2", True))
            check("and says it moves balances and fees", "balances and fees" in shown["Effect"], True)
            check("showing it fetches nothing", feed.last_synced_at, synced)

            print("\n=== An account's registration and beneficiaries ===")
            reg = {"account_id": str(account.id), "registration_type": "inherited_ira", "original_owner_death_date": "2025-06-30",
                   "rmd_election_method": "10_year_rule"}
            shown = await card(ex, "set_account_registration", reg)
            check("names the change", (shown["Account"], shown["Registration"], "30 June 2025" in shown["Original owner died"], shown["RMD election"]),
                  ("Ann A/C", "Unset becomes inherited ira", True, "10 year rule"))
            await refused("an unknown registration", ex.preview_change("set_account_registration", {**reg, "registration_type": "offshore"}), "unknown registration")
            await refused("a bad election", ex.preview_change("set_account_registration", {**reg, "rmd_election_method": "x"}), "must be")
            await refused("a date it cannot read", ex.preview_change("set_account_registration", {**reg, "original_owner_death_date": "soon"}), "invalid")
            await refused("nothing to change", ex.preview_change("set_account_registration", {"account_id": str(account.id)}), "say what to change")
            await refused("another firm's account", outsider.preview_change("set_account_registration", reg), "not found")
            await refused("a bad election changes nothing else", ex.set_account_registration({**reg, "rmd_election_method": "x"}), "must be")
            check("showing and refusing change nothing", (account.registration_type, account.rmd_election_method), (None, None))
            await ex.set_account_registration(reg)
            check("the write goes through the same checks", (account.registration_type, account.rmd_election_method), ("inherited_ira", "10_year_rule"))
            share = {"account_id": str(account.id), "beneficiary_name": "Bo Lee", "percentage": 60, "relationship_to_owner": "child"}
            shown = await card(ex, "add_account_beneficiary", share)
            check("names who and how much", (shown["Account"], shown["Beneficiary"], shown["Share"], shown["Class"], shown["Relationship"]),
                  ("Ann A/C", "Bo Lee", "60%", "Primary", "child"))
            check("and the total it makes", shown["Primary total"], "0% becomes 60%")
            await refused("no share", ex.preview_change("add_account_beneficiary", {**share, "percentage": 0}), "above 0")
            await refused("over a hundred", ex.preview_change("add_account_beneficiary", {**share, "percentage": 101}), "at most 100")
            await refused("not a number", ex.preview_change("add_account_beneficiary", {**share, "percentage": "half"}), "must be a number")
            await refused("an unknown class", ex.preview_change("add_account_beneficiary", {**share, "designation_class": "other"}), "primary or contingent")
            await refused("another firm's account", outsider.preview_change("add_account_beneficiary", share), "not found")
            check("showing it adds nobody", await count(AccountBeneficiary), 0)
            await ex.add_account_beneficiary(share)
            shown = await card(ex, "add_account_beneficiary", {**share, "beneficiary_name": "Cy Lee", "percentage": 40})
            check("the next one counts the first", shown["Primary total"], "60% becomes 100%")

            print("\n=== A goal ===")
            goal = Goal(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, name="Retirement", target_amount=500000, target_date=date(2040, 12, 31), priority=2)
            s.add(goal)
            await s.flush()
            change = {"goal_id": str(goal.id), "target": 750000, "timeline": "2045", "priority": "high"}
            shown = await card(ex, "update_goal", change)
            check("names the goal and client", (shown["Goal"], shown["Client"]), ("Retirement", "Ann"))
            check("each change from and to", ("500,000" in shown["Target"], "750,000" in shown["Target"], "2040" in shown["Date"], "2045" in shown["Date"], shown["Priority"]),
                  (True, True, True, True, "Medium becomes High"))
            shown = await card(ex, "update_goal", {"goal_id": str(goal.id), "priority": "low"})
            check("only what changes is shown", ("Target" in shown, "Date" in shown, shown["Priority"]), (False, False, "Medium becomes Low"))
            await refused("a timeline that is no year", ex.preview_change("update_goal", {**change, "timeline": "5 years"}), "not a specific year")
            await refused("an unknown priority", ex.preview_change("update_goal", {**change, "priority": "urgent"}), "must be high")
            await refused("a target that is no number", ex.preview_change("update_goal", {**change, "target": "a lot"}), "must be a number")
            await refused("nothing to change", ex.preview_change("update_goal", {"goal_id": str(goal.id)}), "say what to change")
            await refused("another firm's goal", outsider.preview_change("update_goal", change), "not found")
            check("showing it changes nothing", (float(goal.target_amount), goal.priority), (500000.0, 2))
            done = await ex.update_goal(change)
            check("the write goes through the same checks", (float(goal.target_amount), goal.target_date, goal.priority), (750000.0, date(2045, 12, 31), 1))
            check("and answers as it did", done["updates"], {"target_amount": 750000.0, "target_date": "2045-12-31", "priority": "high"})

            print("\n=== Conflicts and supervision ===")
            conflict = ConflictInventoryItem(firm_id=firm.id, conflict_key="revenue_sharing", title="Revenue sharing", mechanism_of_harm="x",
                                             mitigation="x", disclosure="x", owner="CCO")
            rule = WSPRule(firm_id=firm.id, rule_key="wash_sale.loss_harvest", obligation="IRC 1091", designated_role="Compliance",
                           supervisory_activity="x", frequency="per_event", evidence_description="x", change_log=[])
            s.add_all([conflict, rule])
            await s.flush()
            review = ToolExecutors(s, adviser.id, UserRole.COMPLIANCE, firm.id)
            shown = await card(review, "mark_conflict_reviewed", {"conflict_key": "revenue_sharing", "notes": "Annual review"})
            check("names the conflict and owner", (shown["Conflict"], shown["Owner"], shown["Last reviewed"], shown["Notes"]),
                  ("Revenue sharing", "CCO", "Never", "Annual review"))
            await refused("no such conflict", review.preview_change("mark_conflict_reviewed", {"conflict_key": "nope"}), "no conflict-inventory")
            await refused("another firm's conflict", ToolExecutors(s, adviser.id, UserRole.COMPLIANCE, other.id).preview_change("mark_conflict_reviewed", {"conflict_key": "revenue_sharing"}))
            check("showing it reviews nothing", conflict.last_reviewed_at, None)
            shown = await card(review, "log_wsp_evidence", {"rule_key": "wash_sale.loss_harvest"})
            check("names the obligation and how often", (shown["Obligation"], shown["Frequency"], shown["Last evidence"]), ("IRC 1091", "per event", "Never"))
            await refused("no such rule", review.preview_change("log_wsp_evidence", {"rule_key": "nope"}), "no wsp rule")
            check("showing it logs nothing", rule.last_evidence_at, None)
            await review.log_wsp_evidence({"rule_key": "wash_sale.loss_harvest"})
            check("the write goes through the same checks", rule.last_evidence_at is not None, True)

            print("\n=== The pipeline ===")
            lead = {"full_name": "Dee Roy", "contact_type": "prospect", "email": "dee@x", "source": "Referral"}
            shown = await card(ex, "create_crm_contact", lead)
            check("a contact is named", (shown["Name"], shown["Type"], shown["Email"], shown["Source"]), ("Dee Roy", "Prospect", "dee@x", "Referral"))
            await refused("an unknown type", ex.preview_change("create_crm_contact", {**lead, "contact_type": "vip"}), "contact_type")
            await refused("no name", ex.preview_change("create_crm_contact", {**lead, "full_name": ""}), "full_name")
            check("showing it adds nobody", await count(CrmContact), 0)
            dee = (await ex.create_crm_contact(lead))["contact_id"]
            ed = (await ex.create_crm_contact({"full_name": "Ed Roy"}))["contact_id"]
            deal = {"contact_id": dee, "title": "Rollover", "estimated_aum": 900000, "probability_pct": 25}
            shown = await card(ex, "create_crm_opportunity", deal)
            check("an opportunity is named", (shown["Contact"], shown["Opportunity"], "900,000" in shown["Estimated assets"], shown["Probability"], shown["Stage"]),
                  ("Dee Roy", "Rollover", True, "25%", "Lead"))
            await refused("a probability that is no number", ex.preview_change("create_crm_opportunity", {**deal, "probability_pct": "high"}), "whole number")
            await refused("no title", ex.preview_change("create_crm_opportunity", {**deal, "title": ""}), "title")
            await refused("another firm's contact", outsider.preview_change("create_crm_opportunity", deal), "not found")
            check("showing it adds nothing", await count(CrmOpportunity), 0)
            opp = (await ex.create_crm_opportunity(deal))["opportunity_id"]
            note = {"contact_id": dee, "activity_type": "call", "detail": "Spoke about the rollover", "opportunity_id": opp}
            shown = await card(ex, "log_crm_activity", note)
            check("an activity is named", (shown["Contact"], shown["Type"], shown["Detail"], shown["Opportunity"]), ("Dee Roy", "Call", "Spoke about the rollover", "Rollover"))
            await refused("another contact's opportunity", ex.preview_change("log_crm_activity", {**note, "contact_id": ed}), "not this contact's")
            await refused("the write refuses it too", ex.log_crm_activity({**note, "contact_id": ed}), "not this contact's")
            await refused("an unknown activity", ex.preview_change("log_crm_activity", {**note, "activity_type": "fax"}), "activity_type")
            check("showing and refusing log nothing", await count(CrmActivity), 0)
            await ex.log_crm_activity(note)
            check("the write goes through the same checks", await count(CrmActivity), 1)
            stage = {"opportunity_id": opp, "stage": "lost", "lost_reason": "Went elsewhere"}
            shown = await card(ex, "update_crm_opportunity_stage", stage)
            check("a stage shows from and to", (shown["Opportunity"], shown["Contact"], shown["Stage"], shown["Lost reason"]),
                  ("Rollover", "Dee Roy", "Lead becomes Lost", "Went elsewhere"))
            await refused("an unknown stage", ex.preview_change("update_crm_opportunity_stage", {**stage, "stage": "maybe"}), "stage must be")
            await refused("another firm's opportunity", outsider.preview_change("update_crm_opportunity_stage", stage), "not found")
            check("showing it moves nothing", (await s.get(CrmOpportunity, uuid.UUID(opp))).stage, "lead")

            print("\n=== Corporate actions ===")
            holder = (await s.execute(select(Holding).where(Holding.instrument_id == fund.id))).scalar_one()
            dividend = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=fund.id, action_type="cash_dividend", status="announced",
                                       payable_date=date(2026, 12, 1), details={"cash_per_share": 0.75})
            untermed = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=fund.id, action_type="cash_dividend", status="announced", details={})
            merger = CorporateAction(id=uuid.uuid4(), firm_id=firm.id, instrument_id=fund.id, action_type="merger", status="announced", is_voluntary=True, details={})
            s.add_all([dividend, untermed, merger])
            await s.flush()
            compute = {"corporate_action_id": str(dividend.id)}
            shown = await card(run, "compute_corporate_action_entitlements", compute)
            check("names the event and who it reaches", (shown["Event"], shown["Instrument"], shown["Accounts holding"], shown["New entitlements"], shown["Already computed"]),
                  ("Cash dividend", "PVT", "1", "1", "0"))
            await refused("another firm's event", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("compute_corporate_action_entitlements", compute), "not found")
            check("showing it computes nothing", await count(CorporateActionEntitlement), 0)
            await run.compute_corporate_action_entitlements(compute)
            await run.compute_corporate_action_entitlements({"corporate_action_id": str(untermed.id)})
            await run.compute_corporate_action_entitlements({"corporate_action_id": str(merger.id)})
            shown = await card(run, "compute_corporate_action_entitlements", compute)
            check("a second look finds them done", (shown["New entitlements"], shown["Already computed"]), ("0", "1"))
            async def entitlement(action):
                return (await s.execute(select(CorporateActionEntitlement).where(CorporateActionEntitlement.corporate_action_id == action.id))).scalar_one()
            row = await entitlement(dividend)
            post = {"entitlement_id": str(row.id)}
            shown = await card(run, "post_corporate_action_entitlement", post)
            check("names the account, shares and cash", (shown["Account"], shown["Shares entitled"], "37.50" in shown["Cash in"]), ("Ann A/C", "50", True))
            check("and that it cannot be undone", "cannot be undone" in shown["Effect"], True)
            await refused("an event with no terms", run.preview_change("post_corporate_action_entitlement", {"entitlement_id": str((await entitlement(untermed)).id)}), "no cash per share")
            await refused("an event that needs amounts", run.preview_change("post_corporate_action_entitlement", {"entitlement_id": str((await entitlement(merger)).id)}), "corporate actions page")
            await refused("the write refuses it too", run.post_corporate_action_entitlement({"entitlement_id": str((await entitlement(merger)).id)}), "corporate actions page")
            await refused("another firm's entitlement", ToolExecutors(s, ops.id, ops.role, other.id).preview_change("post_corporate_action_entitlement", post), "not found")
            check("showing it posts nothing", (row.status, float(holder.quantity)), ("pending", 50.0))
            await run.post_corporate_action_entitlement(post)
            check("the write goes through the same checks", row.status, "posted")
            await refused("posted once only", run.preview_change("post_corporate_action_entitlement", post), "exactly once")

            print("\n=== Deciding a proposal that trades ===")
            await s.refresh(account)
            cash = float(account.cash_balance)
            rebalance_run = AgentRun(firm_id=firm.id, agent_key="drift_rebalancing", status="awaiting", tier="tier_2", trigger="manual", started_at=utcnow())
            s.add(rebalance_run)
            await s.flush()
            trades = [{"side": "sell", "symbol": "AAA", "instrument_id": str(aaa.id), "account_id": str(account.id), "quantity": 9.6, "est_value": 120.0, "est_realised_gain": 40.0},
                      {"side": "buy", "symbol": "CCC", "instrument_id": str(ccc.id), "account_id": str(account.id), "quantity": 60, "est_value": 300.0, "est_realised_gain": 0}]
            rebalance = Recommendation(firm_id=firm.id, run_id=rebalance_run.id, agent_key="drift_rebalancing", tier="tier_2", status=RecommendationStatus.PROPOSED,
                                       title="Rebalance Ann mandate: max drift 12% exceeds 5% band", summary="1 sell / 1 buy", subject_type="mandate",
                                       subject_id=mandate.id, subject_label="Ann mandate",
                                       payload={"order_set": trades, "estimated_realised_gain": 40.0, "cgt_budget": 500.0})
            s.add(rebalance)
            await s.flush()
            approve = {"recommendation_id": str(rebalance.id), "action": "approve"}
            shown = await card(ex, "decide_recommendation", approve)
            check("names the proposal and the client", (shown["Proposal"], shown["Client"]), (rebalance.title, "Ann mandate"))
            check("says what approving does", shown["Decision"], "Approve: 2 orders are placed now")
            check("each order, by name and size", (shown["Order 1"].startswith("Sell 9.6 AAA"), shown["Order 2"].startswith("Buy 60 CCC")), (True, True))
            check("what it leaves in cash", f"{cash + 120 - 300:,.0f}" in shown["Cash, before fees"], True)
            check("the gain against its budget", ("40" in shown["Realised gain"], "500" in shown["Realised gain"]), (True, True))
            check("a paper venue says so", shown["Venue"].startswith("Paper"), True)
            shown = await card(ex, "decide_recommendation", {**approve, "action": "dismiss"})
            check("dismissing places nothing", shown["Decision"], "Dismiss: nothing is placed and the proposal closes")
            await refused("compliance may not approve a trade", ToolExecutors(s, adviser.id, UserRole.COMPLIANCE, firm.id).preview_change("decide_recommendation", approve))
            await refused("revise is the page's", ex.preview_change("decide_recommendation", {**approve, "action": "revise"}), "Recommendations page")
            await refused("another firm's proposal keeps the plain card and is refused when it runs", outsider.decide_recommendation(approve), "not found")
            await refused("  and gets no card at all", outsider.preview_change("decide_recommendation", approve), "not a proposal on file")
            listed = (await ex.read_recommendations({"subject": "Ann mandate"}))["recommendations"]
            mine = next(r for r in listed if r["id"] == str(rebalance.id))
            check("the model reads the exact orders", [(o["side"], o["symbol"], o["quantity"]) for o in mine["orders"]], [("sell", "AAA", 9.6), ("buy", "CCC", 60)])
            check("  with the gain and the budget", (mine["estimated_realised_gain"], mine["cgt_budget"]), (40.0, 500.0))
            for said in ("Ann rebalance", "the rebalance proposal for Ann", "drift rebalancing Ann"):
                found = (await ex.read_recommendations({"subject": said}))["recommendations"]
                check(f"\"{said}\" finds that one proposal, words in any order", [r["id"] for r in found], [str(rebalance.id)])
            check("a word no proposal carries finds none", (await ex.read_recommendations({"subject": "Ann harvest"}))["recommendations"], [])
            rebalance.status = RecommendationStatus.DISMISSED
            await s.flush()
            await refused("a decided proposal", ex.preview_change("decide_recommendation", approve), "Already")
            s.add(Recommendation(firm_id=firm.id, run_id=rebalance_run.id, agent_key="client_care", tier="tier_1", status=RecommendationStatus.PROPOSED,
                                 title="Call Ann", summary="s", priority=5, created_at=utcnow() - timedelta(days=30)))
            await s.flush()
            everything = (await ex.read_recommendations({"status": "all"}))["recommendations"]
            check("asked for everything, what is still open comes first", [r["status"] for r in everything][:1], ["proposed"])

            print("\n=== Every card names its change ===")
            source = Path(executors.__file__).read_text(encoding="utf-8")
            body = source[source.index("async def preview_change"):]
            body = body[:body.index("\n    async def ", 10)]
            named = set(re.findall(r'"([a-z_]+)"', body))
            gated = {k for k, t in TOOLS.items() if t.change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION}
            check("a tool that stops for a Confirm has its rows; run_agent keeps its own", sorted(gated - named), ["run_agent"])
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
