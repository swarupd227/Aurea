"""Prove a bank account is verified before money is pulled from it and a third party only after a callback, that a large
deposit is held and the rebalancer's held_back counts it, that a return carries its NACHA code and an unauthorized one
revokes the link, that a return after the money was free says it may have been invested, that nothing writes cash,
and that standing instructions fall due for revalidation.

Runs against a throwaway SQLite database.

    python -m tests.verify_money_in
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import cash_movement, firm_time, money_in as mi
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.cash_movement import StandingInstruction
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, mi.MoneyInError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== Who owns the bank account ===")
    names = ["Rae Anne Vance", "Tom Vance"]
    check("the owner's name in the household is first party", mi.first_party("Rae Vance", names), True)
    check("an honorific does not matter", mi.first_party("Mr Tom Vance", names), True)
    check("someone else is a third party", mi.first_party("Harbour Trading Ltd", names), False)
    check("three business days after a Friday is Wednesday", mi.business_days_after(date(2026, 10, 30), 3), date(2026, 11, 4))


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            s.add(firm)
            await s.flush()
            users = {}
            for key, role in (("ops", UserRole.OPERATIONS), ("adv", UserRole.ADVISER)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Vance")
            s.add(h)
            await s.flush()
            p = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Rae Vance", date_of_birth=date(1970, 1, 1))
            s.add(p)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=p.id, name="Rae", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Rae brokerage", currency="USD", cash_balance=100000, registration_type="individual")
            s.add(a)
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            today = firm_time.today(firm)
            link = {"account_id": str(a.id), "bank": "First Bank", "last4": "4821", "owner_name": "Rae Vance", "method": "micro_deposit",
                    "evidence": "Micro-deposit amounts confirmed by Rae in the portal"}

            print("\n=== Bank links ===")
            check("only operations records a link or a deposit", (TOOLS["record_bank_link"].roles_required, TOOLS["record_deposit"].roles_required),
                  ({UserRole.OPERATIONS}, {UserRole.OPERATIONS}))
            await refused("only the last four digits", ex["ops"].preview_change("record_bank_link", {**link, "last4": "123456784821"}), "last four")
            await refused("a third party needs a callback", ex["ops"].preview_change("record_bank_link", {**link, "owner_name": "Harbour Trading Ltd"}), "callback")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_bank_link", link))["rows"]}
            check("the card shows the bank and the party", (card["Bank"], card["Party"]), ("First Bank ending 4821", "In the household"))
            out = await ex["ops"].record_bank_link(link)
            link_id = next(x["id"] for x in out["links"] if x["last4"] == "4821")
            other = await ex["ops"].record_bank_link({**link, "bank": "Harbour Bank", "last4": "0001", "owner_name": "Harbour Trading Ltd", "callback_by": "Grace Lim"})
            check("a third party with a callback is recorded as one", next(x["third_party"] for x in other["links"] if x["last4"] == "0001"), True)

            print("\n=== Deposits and holds ===")
            dep = {"account_id": str(a.id), "method": "ach", "amount": 40000, "evidence": "Custodian credit advice"}
            await refused("an ACH names its bank account", ex["ops"].preview_change("record_deposit", dep), "verified bank account")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_deposit", {**dep, "bank_link_id": link_id}))["rows"]}
            hold = mi.business_days_after(today, 5)
            check("40,000 by ACH is held five business days", card["Hold"], f"Until {firm_time.spoken(hold)}")
            out = await ex["ops"].record_deposit({**dep, "bank_link_id": link_id})
            check("the hold is shown", (out["summary"]["on_hold"], out["deposits"][0]["on_hold"]), (40000.0, True))
            check("and purchases leave it alone", (await cash_movement.held_back(s, firm, m))["deposits"], 40000.0)
            check("the book's cash is untouched", float(a.cash_balance), 100000.0)
            out = await ex["ops"].record_deposit({"account_id": str(a.id), "method": "wire", "amount": 80000, "evidence": "Fed wire credit advice"})
            check("a wire is not held", next(d["on_hold"] for d in out["deposits"] if d["method"] == "wire"), False)
            small = await ex["ops"].record_deposit({"account_id": str(a.id), "method": "ach", "amount": 5000, "bank_link_id": link_id,
                                                    "on": (today - timedelta(days=20)).isoformat(), "evidence": "Custodian credit advice"})
            small_id = next(d["id"] for d in small["deposits"] if d["amount"] == 5000)

            print("\n=== Returns ===")
            await refused("a return carries its code", ex["ops"].preview_change("record_deposit", {"deposit_id": small_id, "evidence": "Return notice"}), "code")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_deposit",
                    {"deposit_id": small_id, "return_code": "R10", "evidence": "ACH return notice"}))["rows"]}
            check("an unauthorized return after the money was free says so, and revokes the link",
                  (card["Already available"], card["Bank link"]), ("Yes: it may have been invested", "Revoked"))
            out = await ex["ops"].record_deposit({"deposit_id": small_id, "return_code": "R10", "evidence": "ACH return notice"})
            check("returns are counted by code", out["returns_by_code"], [{"code": "R10", "reason": "Customer advises not authorized", "count": 1}])
            check("the link is revoked", next(x["status"] for x in out["links"] if x["last4"] == "4821"), "revoked")
            await refused("no ACH from a revoked link", ex["ops"].preview_change("record_deposit", {**dep, "bank_link_id": link_id}), "revoked")

            print("\n=== Standing instructions ===")
            si = StandingInstruction(firm_id=firm.id, account_id=a.id, payee="Harbour Trading Ltd", third_party=True, amount=2000, frequency="monthly",
                                     first_due=today - timedelta(days=400), source_document="SLOA 2025", recorded_by="ops@t")
            s.add(si)
            await s.flush()
            out = await ex["adv"].read_money_in({})
            check("an instruction a year old is overdue", (out["instructions"][0]["overdue"], out["summary"]["instructions_overdue"]), (True, 1))
            await refused("a third-party instruction needs a callback", ex["adv"].preview_change("revalidate_standing_instruction",
                          {"standing_id": str(si.id), "evidence": "Client email 2 Nov"}), "callback")
            out = await ex["adv"].revalidate_standing_instruction({"standing_id": str(si.id), "evidence": "Client email 2 Nov", "callback_by": "Sophie Tran"})
            check("revalidated, it is due again in a year", (out["instructions"][0]["overdue"], out["instructions"][0]["last_checked"]), (False, today.isoformat()))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
