"""Prove a feature is asked for on an agreement and decided by a principal who did not ask, that a retirement account
takes no margin and a limited options level, and that a senior hold is for a specified adult, stops new payments out,
counts business days, needs its notice before an extension and a report before the second, and is a US rule.

Runs against a throwaway SQLite database.

    python -m tests.verify_account_features
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import account_features as af, cash_movement, firm_time
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_file import TrustedContact
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
    except (ExecutorError, af.FeatureError, cash_movement.CashMovementError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US", settings={})
            nz = Firm(id=uuid.uuid4(), name="N", slug="n", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, nz])
            await s.flush()
            users = {}
            for key, role in (("adv", UserRole.ADVISER), ("comp", UserRole.COMPLIANCE), ("branch", UserRole.BRANCH_LEADER)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Hale")
            s.add(h)
            await s.flush()
            old = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Edna Hale", date_of_birth=date(1948, 3, 1))
            young = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Joe Hale", date_of_birth=date(1985, 3, 1))
            s.add_all([old, young])
            await s.flush()
            s.add(TrustedContact(firm_id=firm.id, person_id=old.id, name="Mary Hale", relationship="daughter", as_of=date(2025, 1, 1),
                                 source_document="Trusted contact form", recorded_by="adv@t"))
            mo = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=old.id, name="Edna", mandate_type="advisory", suitability={}, constraints={})
            my = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=young.id, name="Joe", mandate_type="advisory", suitability={}, constraints={})
            s.add_all([mo, my])
            await s.flush()
            ira = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mo.id, name="Edna IRA", currency="USD", cash_balance=50000, registration_type="traditional_ira")
            taxable = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=mo.id, name="Edna brokerage", currency="USD", cash_balance=50000, registration_type="individual")
            joe = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=my.id, name="Joe brokerage", currency="USD", cash_balance=5000, registration_type="individual")
            s.add_all([ira, taxable, joe])
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            today = firm_time.today(firm)

            print("\n=== Features ===")
            check("an adviser asks, a principal decides", (UserRole.ADVISER in TOOLS["request_account_feature"].roles_required,
                                                          TOOLS["decide_account_feature"].roles_required), (True, {UserRole.COMPLIANCE, UserRole.BRANCH_LEADER}))
            await refused("an IRA takes no margin", ex["adv"].preview_change("request_account_feature",
                          {"account_id": str(ira.id), "feature": "margin", "agreement": "Margin agreement 2026"}), "no margin")
            await refused("nor options above the firm's ceiling", ex["adv"].preview_change("request_account_feature",
                          {"account_id": str(ira.id), "feature": "options", "level": 3, "agreement": "Options agreement", "experience_years": 5, "objective": "growth"}), "level 2")
            await refused("the client's objective is recorded, not assumed", ex["adv"].preview_change("request_account_feature",
                          {"account_id": str(taxable.id), "feature": "options", "level": 2, "agreement": "Options agreement"}), "objective")
            ask = {"account_id": str(taxable.id), "feature": "options", "level": 2, "agreement": "Options agreement signed 1 Nov", "experience_years": 4, "objective": "income"}
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("request_account_feature", ask))["rows"]}
            check("the card names the level and the next step", (card["Level"], card["Next"]), ("2: Buying calls and puts, cash-secured puts", "A principal decides"))
            out = await ex["adv"].request_account_feature(ask)
            fid = out["features"][0]["id"]
            await refused("asking twice is refused", ex["adv"].preview_change("request_account_feature", ask), "already requested")
            adv_as_principal = ToolExecutors(s, users["adv"].id, UserRole.COMPLIANCE, firm.id)
            await refused("the person who asked cannot approve", adv_as_principal.preview_change("decide_account_feature",
                          {"feature_id": fid, "decision": "approve", "reason": "Reviewed the agreement and experience"}), "cannot also approve")
            out = await ex["comp"].decide_account_feature({"feature_id": fid, "decision": "approve", "reason": "Reviewed the agreement and four years' experience"})
            check("approved by compliance", out["features"][0]["status"], "approved")
            out = await ex["adv"].request_account_feature({**ask, "level": 3})
            new = next(f["id"] for f in out["features"] if f["level"] == 3)
            out = await ex["branch"].decide_account_feature({"feature_id": new, "decision": "approve", "reason": "Spreads reviewed against the income objective"})
            check("a new level replaces the old one", sorted((f["level"], f["status"]) for f in out["features"]), [(2, "removed"), (3, "approved")])

            print("\n=== Senior holds ===")
            hold = {"action": "place", "account_id": str(taxable.id), "reason": "A new 'friend' has called three times asking us to wire 40,000 to him"}
            await refused("Joe is 41 with no impairment recorded, so no hold", ex["comp"].preview_change("record_senior_hold",
                          {**hold, "account_id": str(joe.id)}), "specified adult", "41")
            check("an adviser does not place a hold", UserRole.ADVISER in TOOLS["record_senior_hold"].roles_required, False)
            card = {r["label"]: r["value"] for r in (await ex["comp"].preview_change("record_senior_hold", hold))["rows"]}
            check("Edna is 78, and it ends fifteen business days on", (card["Specified adult"], card["Ends"]),
                  ("78 years old", firm_time.spoken(af.business_days_after(today, 15))))
            out = await ex["comp"].record_senior_hold(hold)
            hid = out["holds"][0]["id"]
            check("in force, with her trusted contact shown", (out["holds"][0]["state"], out["holds"][0]["trusted_contacts"]), ("active", ["Mary Hale (daughter)"]))
            await refused("no new payment out while it stands", cash_movement.prepare_request(s, taxable.id, firm_id=firm.id, kind="withdrawal",
                                                                                                amount=40000, payee="J Smith", reason="client asked"), "temporary hold")
            ok = await cash_movement.prepare_request(s, ira.id, firm_id=firm.id, kind="withdrawal", amount=1000, payee="Edna Hale", reason="monthly")
            check("her other account is not held", ok["amount"], 1000.0)
            await refused("no extension before anyone is told", ex["comp"].preview_change("record_senior_hold",
                          {"action": "extend", "hold_id": hid, "reason": "Internal review found more calls from the same man"}), "who was told")
            await ex["comp"].record_senior_hold({"action": "notify", "hold_id": hid, "notified": "Mary Hale", "reason": "Called Mary on the number of record"})
            out = await ex["comp"].record_senior_hold({"action": "extend", "hold_id": hid, "reason": "Internal review found more calls from the same man"})
            check("the first extension adds ten business days", out["holds"][0]["expires_on"], af.business_days_after(af.business_days_after(today, 15), 10).isoformat())
            await refused("the second needs a report to a state agency or court", ex["comp"].preview_change("record_senior_hold",
                          {"action": "extend", "hold_id": hid, "reason": "Still unresolved after the review"}), "state agency")
            await ex["comp"].record_senior_hold({"action": "extend", "hold_id": hid, "reason": "Reported to Adult Protective Services", "reference": "APS case 2026-11873"})
            await refused("there is no third", ex["comp"].preview_change("record_senior_hold", {"action": "extend", "hold_id": hid, "reason": "Still waiting on APS",
                                                                                               "reference": "APS case 2026-11873"}), "both extensions")
            out = await ex["comp"].record_senior_hold({"action": "release", "hold_id": hid, "reason": "APS closed the case; the caller was a scammer and is gone"})
            check("released, payments may be asked for again", (out["holds"][0]["state"], (await cash_movement.prepare_request(
                s, taxable.id, firm_id=firm.id, kind="withdrawal", amount=100, payee="Edna Hale", reason="test"))["amount"]), ("released", 100.0))

            print("\n=== A US rule ===")
            nz_ex = ToolExecutors(s, users["comp"].id, UserRole.COMPLIANCE, nz.id)
            await refused("a New Zealand firm is told it does not apply", nz_ex.preview_change("record_senior_hold", hold), "US rule")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
