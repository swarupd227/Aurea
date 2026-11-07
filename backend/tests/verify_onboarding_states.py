"""Prove each onboarding account has a state worked out from its evidence, with an owner and an SLA; that exceptions
sit in queues by kind; that a household reads its least advanced account; that the three clocks are kept apart; and
that nothing is transferred into an account the custodian has not opened.

Runs against a throwaway SQLite database.

    python -m tests.verify_onboarding_states
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import onboarding as api
from app.aurea_core import account_states as st
from app.core.db import Base
from app.llm.executors import ToolExecutors
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty, TransferRequest
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.models.trading import Order

PASS, FAIL = [], []
NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def case(**kw):
    base = dict(id=uuid.uuid4(), prospect_name="A", registration_type="individual", status="intake", created_at=NOW - timedelta(days=20),
                updated_at=NOW - timedelta(days=20), agreement_sent_at=None, agreement_signed_at=None, activated_at=None,
                custodian_push_status=None, custodian_push_at=None, edd_status=None, nigo_flag=False, nigo_reason=None, nigo_root_cause=None,
                materialized={}, intake={})
    base.update(kw)
    return SimpleNamespace(**base)


def transfer(**kw):
    base = dict(direction="in", transfer_type="acat", status="initiated", initiated_at=NOW - timedelta(days=4), settled_at=None,
                reject_reason=None, created_at=NOW - timedelta(days=4))
    base.update(kw)
    return SimpleNamespace(**base)


def pure() -> None:
    print("\n=== A state from the evidence ===")
    at = lambda c, **kw: st.state_of(c, data_complete=kw.get("data", False), gates_blocking=kw.get("blocking", False),  # noqa: E731
                                     transfers=kw.get("t", []), first_fill=kw.get("fill"))["state"]
    check("a new case is a draft", at(case()), "draft")
    check("parties and documents in, data complete", at(case(), data=True), "data_complete")
    check("the agreement out to sign", at(case(agreement_sent_at=NOW - timedelta(days=6))), "documents_generated")
    check("signed, with nothing blocking", at(case(agreement_signed_at=NOW - timedelta(days=5))), "signed")
    check("signed, with compliance's gates open", at(case(agreement_signed_at=NOW - timedelta(days=5)), blocking=True), "in_review")
    check("activated, waiting for the custodian", at(case(agreement_signed_at=NOW, activated_at=NOW)), "submitted")
    opened = dict(agreement_signed_at=NOW - timedelta(days=9), activated_at=NOW - timedelta(days=8), custodian_push_status="active", custodian_push_at=NOW - timedelta(days=7))
    check("the custodian opened it", at(case(**opened)), "open")
    check("money on its way", at(case(**opened), t=[transfer()]), "funding_in_progress")
    arrived = transfer(status="settled", settled_at=NOW - timedelta(days=2))
    check("money arrived", at(case(**opened), t=[arrived]), "funded")
    check("one still moving is not funded", at(case(**opened), t=[arrived, transfer(transfer_type="wire")]), "funding_in_progress")
    check("the first purchase after funding: invested", at(case(**opened), t=[arrived], fill=NOW - timedelta(days=1)), "invested")
    check("a purchase before the money arrived is not it", at(case(**opened), t=[arrived], fill=NOW - timedelta(days=3)), "funded")

    print("\n=== Owner, SLA and age ===")
    row = st.account_row(case(agreement_signed_at=NOW - timedelta(days=7), edd_status="edd_pending"), data_complete=True, gates_blocking=True,
                         kyc=["EDD: awaiting sign-off"], transfers=[], first_fill=None, now=NOW)
    check("in review is compliance's, with EDD's longer SLA", (row["owner"], row["sla_days"], row["age_days"], row["overdue"]), ("Compliance", 10, 7.0, False))
    row = st.account_row(case(created_at=NOW - timedelta(days=5)), data_complete=False, gates_blocking=False, kyc=[], transfers=[], first_fill=None, now=NOW)
    check("a draft past its SLA is overdue", (row["owner"], row["sla_days"], row["overdue"]), ("Adviser", 3, True))

    print("\n=== Exceptions in their own queues ===")
    ex = st.exceptions_of(case(nigo_flag=True, nigo_root_cause="missing_signature", custodian_push_status="failed"),
                          kyc=["Every party screened: 1 not cleared"],
                          transfers=[transfer(status="failed", reject_reason="Title mismatch"), transfer(transfer_type="ach", status="failed", reject_reason="R01 insufficient funds")], now=NOW)
    check("each kind in its queue", sorted(e["queue"] for e in ex), ["custodian_reject", "funding_fail", "kyc", "nigo", "transfer_reject"])
    check("with the reason it failed", next(e["detail"] for e in ex if e["queue"] == "funding_fail"), "ACH in: R01 insufficient funds")
    check("and who works it", next(e["owner"] for e in ex if e["queue"] == "transfer_reject"), "Transfers team")

    print("\n=== A household is its least advanced account ===")
    a, b = case(intake={"household": "Hartman"}), case(intake={"household": "Hartman"})
    rows = [{"case_id": "1", "account": "Joint", "step": 9, "state": "funded", "owner": "Portfolio team", "exceptions": [], "overdue": False, "rejected": False},
            {"case_id": "2", "account": "IRA", "step": 7, "state": "open", "owner": "Operations", "exceptions": [{}], "overdue": True, "rejected": False}]
    h = st.households(rows, {"1": a, "2": b})
    check("one household of two accounts, waiting on its slowest", (len(h), h[0]["accounts"], h[0]["state"], h[0]["waiting_on"], h[0]["exceptions"]),
          (1, 2, "open", "Operations", 1))

    print("\n=== The three clocks apart ===")
    c1 = case(agreement_signed_at=NOW - timedelta(days=20), custodian_push_status="active", custodian_push_at=NOW - timedelta(days=18))
    clocks = st.clocks([c1], transfers_by_case={c1.id: [transfer(status="settled", initiated_at=NOW - timedelta(days=17), settled_at=NOW - timedelta(days=11))]},
                       fills={c1.id: NOW - timedelta(days=10)})
    check("paperwork, money and invested", (clocks["paperwork"]["p50"], clocks["money"]["p50"], clocks["invested"]["p50"]), (2.0, 7.0, 1.0))
    check("nothing out of order", (clocks["out_of_order"], clocks["paperwork"]["out_of_order"]), ([], 0))

    print("\n=== A clock never runs backwards (B-157) ===")
    early = case(prospect_name="Early Opener", agreement_signed_at=NOW - timedelta(days=10), custodian_push_status="active",
                 custodian_push_at=NOW - timedelta(days=12))
    clocks = st.clocks([c1, early], transfers_by_case={c1.id: [transfer(status="settled", settled_at=NOW - timedelta(days=11))],
                                                       early.id: [transfer(status="settled", settled_at=NOW - timedelta(days=13))]},
                       fills={c1.id: NOW - timedelta(days=10)})
    check("opened before it was signed: left out of paperwork", (clocks["paperwork"]["n"], clocks["paperwork"]["p50"]), (1, 2.0))
    check("and funded before it was opened: left out of money", (clocks["money"]["n"], clocks["money"]["p50"]), (1, 7.0))
    check("each counted against its clock", (clocks["paperwork"]["out_of_order"], clocks["money"]["out_of_order"]), (1, 1))
    check("and named, with its dates, for a person to correct",
          [(w["client"], w["clock"], w["days"]) for w in clocks["out_of_order"]], [("Early Opener", "paperwork", -2.0), ("Early Opener", "money", -1.0)])
    check("no clock reads below zero", min(v for k in ("paperwork", "money", "invested") for v in (clocks[k]["p50"], clocks[k]["p90"], clocks[k]["mean"]) if v is not None) >= 0, True)


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            s.add(firm)
            await s.flush()
            ops = User(id=uuid.uuid4(), firm_id=firm.id, email="ops@t", full_name="Ops", role=UserRole.OPERATIONS, hashed_password="x")
            s.add(ops)
            c = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Hartman Joint", registration_type="individual", intake={"household": "Hartman"})
            s.add(c)
            await s.flush()
            s.add(OnboardingParty(firm_id=firm.id, case_id=c.id, role="owner", legal_name="Ann Hartman"))
            await s.flush()

            print("\n=== Nothing moves into an account that is not open ===")
            body = api.TransferCreate(transfer_type="acat", direction="in", amount=100000, delivering_account_title="Ann Hartman")
            try:
                await api.create_transfer(c.id, body, user=ops, firm=firm, db=s)
                check("a transfer before the account is refused", "not refused", "refused")
            except HTTPException as exc:
                check("a transfer before the account is refused", (exc.status_code, "before starting a transfer" in exc.detail), (409, True))

            print("\n=== The read ===")
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Hartman")
            s.add(h)
            await s.flush()
            p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Hartman")
            s.add(p)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name="Hartman", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            acc = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Hartman A/C", currency="USD", cash_balance=0)
            s.add(acc)
            now = datetime.now(timezone.utc)
            c.agreement_signed_at, c.activated_at, c.status = now - timedelta(days=9), now - timedelta(days=8), "approved"
            c.custodian_push_status, c.custodian_push_at = "active", now - timedelta(days=7)
            c.materialized = {"household_id": str(h.id), "account_id": str(acc.id)}
            s.add(TransferRequest(firm_id=firm.id, case_id=c.id, transfer_type="acat", direction="in", status="settled", initiated_at=now - timedelta(days=6), settled_at=now - timedelta(days=2)))
            s.add(TransferRequest(firm_id=firm.id, case_id=c.id, transfer_type="ach", direction="in", status="failed", initiated_at=now - timedelta(days=3), reject_reason="R01 insufficient funds"))
            await s.flush()
            read = await ToolExecutors(s, ops.id, ops.role, firm.id).read_onboarding_queues({})
            row = read["accounts"][0]
            check("the account is funded, the failed ACH aside", (row["state"], row["owner"]), ("funded", "Portfolio team"))
            check("the failed ACH is in the funding queue", [e["detail"] for e in read["queues"]["funding_fail"]["items"]], ["ACH in: R01 insufficient funds"])
            check("the message says what is stuck", "funding failed 1" in read["message"], True)
            check("the money clock reads open to funded", read["clocks"]["money"]["p50"], 5.0)
            inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol="AAA", name="AAA", asset_class="equity", currency="USD")
            s.add(inst)
            await s.flush()
            s.add(Order(firm_id=firm.id, account_id=acc.id, instrument_id=inst.id, side="buy", status="filled", quantity=10, filled_quantity=10))
            await s.flush()
            read = await ToolExecutors(s, ops.id, ops.role, firm.id).read_onboarding_queues({})
            check("a purchase after funding: invested, and the household is done", (read["accounts"][0]["state"], read["households"][0]["state"]), ("invested", "done"))
            body = api.TransferCreate(transfer_type="wire", direction="in", amount=5000)
            out = await api.create_transfer(c.id, body, user=ops, firm=firm, db=s)
            check("once open, money can come in", out["transfer_type"], "wire")
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
