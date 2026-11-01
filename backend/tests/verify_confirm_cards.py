"""Prove a Confirm card names the change: the risk decision, protection and reconciliation tools show the
record and the figures, refuse what doing it would refuse, and write nothing by being shown.

Runs against a throwaway SQLite database.

    python -m tests.verify_confirm_cards
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import protection, reconciliation, risk_profile as rp
from app.core.db import Base, utcnow
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import MarketType, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, TargetAllocation
from app.models.protection import ProtectionPolicy
from app.models.reconciliation import CustodyStatement, ReconBreak
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm

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
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
