"""Meeting notes on the client file: what a person heard, added and never edited, and no figure moves (B-194).

    python -m tests.verify_discovery_notes

Runs against a throwaway SQLite database:
  - a note is goals, money history, influence or engagement, from a named meeting, in the words of whoever wrote it;
  - an engagement note names one adult and says engaged or not; the latest note on a person stands, and an adult with none
    is "not recorded", never engaged; the unengaged adult is named;
  - the ledger names the note and its subject, never its words; nothing is edited and no figure, task or goal changes;
  - only an adviser or paraplanner records, a client reads none, and the card says what the conversation did.
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import client_file, firm_time
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_file import DiscoveryNote
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Goal, Household, Person
from app.models.identity import User
from app.models.tenant import Firm
from app.models.governance import LedgerEntry

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
            firm = Firm(id=uuid.uuid4(), name="T", slug="t", base_currency="USD", jurisdiction="US")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="USD", jurisdiction="US")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            adviser = User(id=uuid.uuid4(), firm_id=firm.id, email="a@t", full_name="A", role=UserRole.ADVISER, hashed_password="x")
            para = User(id=uuid.uuid4(), firm_id=firm.id, email="p@t", full_name="P", role=UserRole.PARAPLANNER, hashed_password="x")
            client = User(id=uuid.uuid4(), firm_id=firm.id, email="c@t", full_name="C", role=UserRole.CLIENT, hashed_password="x")
            s.add_all([adviser, para, client])
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            oh = Household(id=uuid.uuid4(), firm_id=other.id, name="Elsewhere")
            s.add_all([h, oh])
            await s.flush()
            ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Ann Lee")
            bo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Bo Lee")
            kid = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, full_name="Kit Lee", is_next_gen=True)
            stray = Person(id=uuid.uuid4(), firm_id=other.id, household_id=oh.id, full_name="Stray")
            s.add_all([ann, bo, kid, stray])
            s.add(Goal(id=uuid.uuid4(), firm_id=firm.id, household_id=h.id, name="Retire", kind="retirement", target_amount=1_000_000))
            await s.flush()
            met = today - timedelta(days=3)

            def ex(user=adviser):
                return ToolExecutors(s, user.id, user.role, firm.id)

            print("\n=== Nothing on file ===")
            v = await client_file.for_household(s, h.id, firm_id=firm.id)
            check("no notes, and every adult's engagement is not recorded", (v["discovery"]["notes"], v["discovery"]["engagement_not_recorded"]),
                  ([], ["Ann Lee", "Bo Lee"]))
            check("a child has no engagement row", [e["person"] for e in v["discovery"]["engagement"]], ["Ann Lee", "Bo Lee"])
            check("not recorded is not engaged", v["discovery"]["not_engaged"], [])
            tasks_before = (await s.execute(select(func.count(Task.id)))).scalar()
            goals_before = [(g.name, float(g.target_amount)) for g in (await s.execute(select(Goal))).scalars().all()]

            print("\n=== Recording ===")
            r = await ex().execute("record_discovery_note", {"household_id": str(h.id), "kind": "goals", "met_on": met.isoformat(),
                                   "source_document": "Discovery meeting, both present", "body": "Wants to stop work at 62 and fund two grandchildren's schooling."})
            check("a goals note is on file with who wrote it", (r["discovery"]["counts"]["goals"], r["discovery"]["notes"][0]["recorded_by"]), (1, "a@t"))
            check("and says it changes no figure", "changes no figure" in r["message"], True)
            await ex().execute("record_discovery_note", {"household_id": str(h.id), "kind": "money_history", "met_on": met.isoformat(),
                               "source_document": "Discovery meeting, both present", "body": "Sold a family firm in 2014; the rest was inherited."})
            await ex().execute("record_discovery_note", {"household_id": str(h.id), "kind": "influence", "met_on": met.isoformat(),
                               "source_document": "Discovery meeting, both present", "body": "Their accountant son reviews every large decision."})
            check("three kinds on file", sorted(k for k, n in (await client_file.for_household(s, h.id, firm_id=firm.id))["discovery"]["counts"].items() if n), ["goals", "influence", "money_history"])

            print("\n=== Engagement ===")
            r = await ex().execute("record_discovery_note", {"household_id": str(h.id), "kind": "engagement", "met_on": met.isoformat(), "person_id": str(bo.id),
                                   "engaged": "false", "source_document": "Discovery meeting: Bo left the room twice and deferred to Ann"})
            check("the unengaged adult is named", r["discovery"]["not_engaged"], ["Bo Lee"])
            check("the message names them", "Bo Lee" in r["message"], True)
            check("Ann's engagement is still not recorded", r["discovery"]["engagement_not_recorded"], ["Ann Lee"])
            await ex().execute("record_discovery_note", {"household_id": str(h.id), "kind": "engagement", "met_on": today.isoformat(), "person_id": str(bo.id),
                               "engaged": "true", "source_document": "Review meeting: Bo led the questions on the plan"})
            now = (await client_file.for_household(s, h.id, firm_id=firm.id))["discovery"]
            check("a later note stands over an earlier one", (now["not_engaged"], [e["engaged"] for e in now["engagement"]]), ([], [None, True]))
            check("both notes are still on file, added and never edited", (await s.execute(select(func.count(DiscoveryNote.id)).where(DiscoveryNote.kind == "engagement"))).scalar(), 2)

            print("\n=== Refused ===")
            base = {"household_id": str(h.id), "kind": "goals", "met_on": met.isoformat(), "source_document": "Discovery meeting, both present", "body": "Wants a boat."}
            await refused("a kind outside the four", ex().execute("record_discovery_note", {**base, "kind": "gossip"}), "must be one of")
            await refused("no meeting named", ex().execute("record_discovery_note", {**base, "source_document": "x"}), "meeting note")
            await refused("a meeting yet to be held", ex().execute("record_discovery_note", {**base, "met_on": (today + timedelta(days=1)).isoformat()}), "once it has been held")
            await refused("no words", ex().execute("record_discovery_note", {**base, "body": "  "}), "what was said")
            await refused("too long", ex().execute("record_discovery_note", {**base, "body": "x" * 2001}), "2,000")
            await refused("an engagement note with no person", ex().execute("record_discovery_note", {**base, "kind": "engagement", "engaged": "true"}), "whose")
            await refused("a child's engagement", ex().execute("record_discovery_note", {**base, "kind": "engagement", "person_id": str(kit_id := kid.id), "engaged": "true"}), "adult")
            await refused("engagement with no answer", ex().execute("record_discovery_note", {**base, "kind": "engagement", "person_id": str(ann.id)}), "true or false")
            await refused("an answer on a goals note", ex().execute("record_discovery_note", {**base, "engaged": "true"}), "only an engagement")
            await refused("engaged must be true or false", ex().execute("record_discovery_note", {**base, "kind": "engagement", "person_id": str(ann.id), "engaged": "maybe"}), "true or false")
            await refused("a person from another household", ex().execute("record_discovery_note", {**base, "person_id": str(stray.id)}), "not in this household")
            await refused("another firm's household", ex().execute("record_discovery_note", {**base, "household_id": str(oh.id)}), "not found")

            print("\n=== Nothing else moves, and the ledger keeps no words ===")
            check("no task was raised", (await s.execute(select(func.count(Task.id)))).scalar(), tasks_before)
            check("no goal changed", [(g.name, float(g.target_amount)) for g in (await s.execute(select(Goal))).scalars().all()], goals_before)
            ledger = (await s.execute(select(LedgerEntry).where(LedgerEntry.event_type == "discovery_note_recorded"))).scalars().all()
            check("each note is on the ledger", len(ledger), 5)
            check("by kind and subject, never its words", any("grandchildren" in str(e.content) or "accountant" in str(e.content) for e in ledger), False)

            print("\n=== Who may ===")
            check("recording is a Confirm card", TOOLS["record_discovery_note"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("an adviser and a paraplanner hold it", {UserRole.ADVISER, UserRole.PARAPLANNER} <= TOOLS["record_discovery_note"].roles_required, True)
            check("a client does not", UserRole.CLIENT in TOOLS["record_discovery_note"].roles_required, False)
            check("nor operations, who file documents but do not meet the client", UserRole.OPERATIONS in TOOLS["record_discovery_note"].roles_required, False)
            card = await ex(para).preview_change("record_discovery_note", {**base, "household_id": str(h.id)})
            labels = {r["label"]: r["value"] for r in card["rows"]}
            check("the card shows the words and where they come from", ({"Said", "From", "Then"} <= set(labels), labels.get("Said")), (True, "Wants a boat."))
            eng = await ex().preview_change("record_discovery_note", {"household_id": str(h.id), "kind": "engagement", "met_on": today.isoformat(), "person_id": str(bo.id),
                                                                      "engaged": "false", "source_document": "Review meeting, Bo quiet"})
            replaced = {r["label"]: r["value"] for r in eng["rows"]}
            check("an engagement card says what it replaces and what it says", (replaced.get("Engagement"), str(replaced.get("Replaces")).startswith("Engaged on")),
                  ("Not engaged", True))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
