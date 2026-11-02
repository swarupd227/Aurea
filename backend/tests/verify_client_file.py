"""Prove the client file holds what a person recorded, finds what no longer agrees with a family's life, and that a life
event raises a task and changes no figure.

Runs against a throwaway SQLite database.

    python -m tests.verify_client_file
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_file, firm_time
from app.core.db import Base
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_file import EstateDocument, LifeEvent, TrustedContact
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, AccountBeneficiary, Household, Mandate, Person
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
    except (ExecutorError, client_file.ClientFileError) as e:
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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            s.add(adviser)
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            s.add(h)
            await s.flush()
            ann, bo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee"), Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Bo Lee")
            s.add_all([ann, bo])
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann mandate", mandate_type="advisory", suitability={}, constraints={})
            s.add(m)
            await s.flush()
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Ann IRA", currency="NZD", cash_balance=0, registration_type="traditional_ira")
            s.add(a)
            await s.flush()
            s.add_all([AccountBeneficiary(firm_id=firm.id, account_id=a.id, beneficiary_name="Sam Lee", percentage=50, designation_class="primary"),
                       AccountBeneficiary(firm_id=firm.id, account_id=a.id, beneficiary_name="Gran Lee", percentage=50, designation_class="primary")])
            await s.flush()
            ex, outsider = ToolExecutors(s, adviser.id, adviser.role, firm.id), ToolExecutors(s, adviser.id, adviser.role, other.id)

            async def card(tool, inputs):
                return {r["label"]: r["value"] for r in (await ex.preview_change(tool, inputs))["rows"]}

            async def count(model):
                return (await s.execute(select(func.count()).select_from(model))).scalar()

            print("\n=== Nothing on file ===")
            read = await ex.read_client_file({"household_id": str(h.id)})
            check("no contact, and what is not on file says so", (read["people"][0]["trusted_contact"], read["people"][0]["not_on_file"]), (None, ["will", "power_of_attorney", "health_directive"]))
            check("the firm's list counts it", (await ex.read_client_file({}))["summary"]["with_no_trusted_contact"], 1)
            check("and says it does not mean none", "does not mean none exists" in read["message"], True)
            check("a balanced designation is no issue", read["designation_issues"], [])

            print("\n=== A trusted contact ===")
            contact = {"person_id": str(ann.id), "name": "Tui Lee", "relationship": "Sister", "phone": "021 555 0100", "source_document": "Client form, Oct 2026"}
            shown = await card("record_trusted_contact", contact)
            check("the card names them, and that they cannot act", (shown["Contact"], shown["Relationship"], "None" in shown["Authority"]), ("Tui Lee", "Sister", True))
            await refused("the client themself", ex.preview_change("record_trusted_contact", {**contact, "name": "Ann Lee"}), "someone other than the client")
            await refused("no way to reach them", ex.preview_change("record_trusted_contact", {**contact, "phone": ""}), "phone number or email")
            await refused("no document", ex.preview_change("record_trusted_contact", {**contact, "source_document": ""}), "which document")
            await refused("another firm's person", outsider.preview_change("record_trusted_contact", contact), "not found")
            check("showing it writes nothing", await count(TrustedContact), 0)
            read = await ex.record_trusted_contact(contact)
            check("it is on file", read["people"][0]["trusted_contact"]["name"], "Tui Lee")
            shown = await card("record_trusted_contact", {**contact, "name": "Mere Lee"})
            check("a new one names who it replaces", shown["Replaces"], "Tui Lee")

            print("\n=== Estate documents ===")
            will = {"person_id": str(ann.id), "kind": "will", "signed_on": (today - timedelta(days=800)).isoformat(), "source_document": "Attorney's letter, 2024", "held_by": "Smith & Co"}
            shown = await card("record_estate_document", will)
            check("the card names it", (shown["Document"], shown["Held by"]), ("Will", "Smith & Co"))
            await refused("an unknown kind", ex.preview_change("record_estate_document", {**will, "kind": "diary"}), "kind must be")
            await refused("signed in the future", ex.preview_change("record_estate_document", {**will, "signed_on": (today + timedelta(days=1)).isoformat()}), "future")
            await refused("no date", ex.preview_change("record_estate_document", {**will, "signed_on": None}), "date it was signed")
            read = await ex.record_estate_document(will)
            d = read["people"][0]["documents"][0]
            check("current, it is not out of date", (d["out_of_date"], read["people"][0]["not_on_file"]), (False, ["power_of_attorney", "health_directive"]))
            await ex.record_estate_document({**will, "kind": "power_of_attorney", "signed_on": (today - timedelta(days=365 * 6)).isoformat(), "held_by": None})
            read = await ex.read_client_file({"household_id": str(h.id)})
            old = next(x for x in read["people"][0]["documents"] if x["kind"] == "power_of_attorney")
            check("past the review age it is", (old["out_of_date"], old["why"]), (True, "age"))

            print("\n=== A life event ===")
            event = {"household_id": str(h.id), "kind": "divorce", "happened_on": (today - timedelta(days=30)).isoformat(), "person_id": str(ann.id), "other_party": "Gran Lee", "source_document": "Meeting note, 12 Oct 2026"}
            shown = await card("record_life_event", event)
            check("the card names what it touches", (shown["Event"], "beneficiaries" in shown["Touches"], shown["Then"]), ("Divorce", True, "A review task opens"))
            await refused("an unknown event", ex.preview_change("record_life_event", {**event, "kind": "party"}), "event must be")
            await refused("one to come", ex.preview_change("record_life_event", {**event, "happened_on": (today + timedelta(days=1)).isoformat()}), "once it has happened")
            await refused("someone outside the household", ex.preview_change("record_life_event", {**event, "person_id": str(uuid.uuid4())}), "not in this household")
            await refused("another firm's household", outsider.preview_change("record_life_event", event), "not found")
            check("showing it writes nothing", (await count(LifeEvent), await count(Task)), (0, 0))
            read = await ex.record_life_event(event)
            task = (await s.execute(select(Task))).scalar_one()
            check("it raises a task, with what to look at", ("divorce" in task.title, "beneficiaries" in task.detail, task.due_date), (True, True, today + timedelta(days=30)))
            check("a former spouse still named is found", [i["issue"] for i in read["designation_issues"]], ["beneficiary is a former spouse"])
            d = next(x for x in read["people"][0]["documents"] if x["kind"] == "will")
            check("a will signed before it is out of date", (d["out_of_date"], d["why"]), (True, "divorce"))
            check("the other person's papers are not touched", read["people"][1]["out_of_date"], 0)
            read = await ex.record_life_event({**event, "kind": "death_in_family", "other_party": "Sam Lee", "person_id": None})
            check("a beneficiary who has died is found too", sorted(i["issue"] for i in read["designation_issues"]), ["beneficiary has died", "beneficiary is a former spouse"])
            bo.deceased_on = today
            await s.flush()
            check("someone who has died leaves the people", [p["name"] for p in (await ex.read_client_file({"household_id": str(h.id)}))["people"]], ["Ann Lee"])

            print("\n=== Scope ===")
            check("another firm sees none of it", (await outsider.read_client_file({}))["households"], [])
            await refused("nor one household", outsider.read_client_file({"household_id": str(h.id)}), "not found")
            check("a date is a date", isinstance(date.fromisoformat(read["as_of"]), date), True)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
