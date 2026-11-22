"""Prove the advice file (B-175): an adviser's own recommendation to a client, and what became of it.

  - "Recommended umbrella cover; client declined" is a row, with its facts, alternatives, evidence and owner;
  - added and never edited: the client's decision changing, or the implementation, is a new outcome and the history stays;
  - only an adviser or paraplanner records, in the core module as well as the catalogue; compliance, a branch leader and
    the portfolio team read; a client reads none of it, because advice reaches a client through their adviser;
  - a decision or an implementation needs its evidence, a document or a note;
  - the firm's assumption version is captured when it is recorded, and stays when the assumptions move;
  - the implementation rate is implemented over made, by type and overall, for a period, with declined and open apart;
  - one firm never reads or records on another's;
  - each Confirm card names the change, refuses what recording would refuse, and writes nothing;
  - the view is in the Advice checks section and a client's tabs, and opens beside the conversation.

Runs against a throwaway SQLite database.

    python -m tests.verify_advice_file
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import advice_file, firm_time, planning
from app.core.db import Base
from app.core.tools import CLIENT_TOOLS, ONE_CLIENT_VIEWS, OPEN_VIEWS, TOOLS, ToolChangeState, tools_for_role
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.advice_file import AdviceOutcome, AdviceRecommendation
from app.models.enums import UserRole as R
from app.models.governance import LedgerEntry
from app.models.graph import Household, Person, RelationshipEdge
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
ROOT = Path(__file__).resolve().parents[2]
TOOL_KEYS = ("read_advice_file", "record_recommendation", "record_advice_outcome")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, advice_file.AdviceError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:120]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def catalogue() -> None:
    print("\n=== The tools, and who holds them ===")
    check("read_advice_file changes nothing", TOOLS["read_advice_file"].change_state, ToolChangeState.NO)
    check("  read by advisers, paraplanners, the portfolio team, compliance and branch leaders",
          TOOLS["read_advice_file"].roles_required, {R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM, R.COMPLIANCE, R.BRANCH_LEADER})
    for key in ("record_recommendation", "record_advice_outcome"):
        check(f"{key} stops for a Confirm", TOOLS[key].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
        check(f"  and only an adviser or paraplanner holds it", TOOLS[key].roles_required, {R.ADVISER, R.PARAPLANNER})
        check("  compliance does not", key in tools_for_role(R.COMPLIANCE), False)
    check("a client holds none of the advice file's tools", [k for k in TOOL_KEYS if k in CLIENT_TOOLS or k in tools_for_role(R.CLIENT)], [])
    view = (ROOT / "backend" / "app" / "aurea_core" / "client_view.py").read_text(encoding="utf-8")
    check("the client's own view reads no advice file", "advice" in view.lower(), False)
    route = (ROOT / "backend" / "app" / "api" / "advice_file.py").read_text(encoding="utf-8")
    check("the route admits no client", "CLIENT" in route.split("ReaderDep", 1)[1].split("\n\n", 1)[0], False)
    module = (ROOT / "backend" / "app" / "aurea_core" / "advice_file.py").read_text(encoding="utf-8")
    check("nothing in the module edits or deletes a record: it reads with select and adds",
          ("from sqlalchemy import select\n" in module, any(w in module for w in ("session.delete", "setattr(", "flag_modified"))), (True, False))

    print("\n=== The view: Advice checks, a client's tabs, and the work pane ===")
    check("open_view opens it, gated like its page", OPEN_VIEWS.get("advice_file"),
          ("/studio/advice-file", "Advice file", {R.ADVISER, R.PARAPLANNER, R.PORTFOLIO_TEAM, R.COMPLIANCE, R.BRANCH_LEADER}))
    check("  on one client when given household_id", "advice_file" in ONE_CLIENT_VIEWS, True)
    nav = (ROOT / "frontend" / "lib" / "nav.ts").read_text(encoding="utf-8")
    advice = nav.split('{ key: "advice"', 1)[1].split("] },", 1)[0]
    check("it is a page of the Advice checks section", '"/studio/advice-file"' in advice, True)
    check("  a tab of a client, named Advice file", '["/studio/advice-file", "Advice file"]' in nav, True)
    check("  and carries ?household= between a client's tabs", '"/studio/advice-file"' in nav.split("TAKES_HOUSEHOLD", 1)[1].split("]);", 1)[0], True)
    check("  not a new top-level entry: no SECTION of its own", nav.count('key: "advice_file"'), 0)
    panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    work = (ROOT / "frontend" / "app" / "workspace" / "components" / "WorkPane.tsx").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
    check("it is a pane view", 'key: "advice_file"' in panes and 'case "advice_file":' in work, True)
    check("each tool's result draws a card and opens the view", [k for k in TOOL_KEYS if f'case "{k}":' not in renderer or f'case "{k}":' not in panes], [])
    page = ROOT / "frontend" / "app" / "(app)" / "studio" / "advice-file" / "page.tsx"
    check("the page is the view, not a copy", page.exists() and "AdviceFileView" in page.read_text(encoding="utf-8"), True)


async def db_checks() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Oak", slug="oak", base_currency="USD", jurisdiction="US", settings={})
            other = Firm(id=uuid.uuid4(), name="Kauri", slug="kauri", base_currency="NZD", jurisdiction="NZ", settings={})
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)

            def user(f, name, role):
                u = User(id=uuid.uuid4(), firm_id=f.id, email=f"{uuid.uuid4().hex}@x.test", full_name=name, role=role, hashed_password="x", is_active=True)
                s.add(u)
                return u
            ann, pat, cora, bea, pete, opal, cleo = (
                user(firm, "Ann Adviser", R.ADVISER), user(firm, "Pat Para", R.PARAPLANNER), user(firm, "Cora Compliance", R.COMPLIANCE),
                user(firm, "Bea Branch", R.BRANCH_LEADER), user(firm, "Pete Portfolio", R.PORTFOLIO_TEAM), user(firm, "Opal Ops", R.OPERATIONS),
                user(firm, "Cleo Client", R.CLIENT))
            kiri = user(other, "Kiri Adviser", R.ADVISER)
            park, lone, gone = (Household(id=uuid.uuid4(), firm_id=firm.id, name="Park"), Household(id=uuid.uuid4(), firm_id=firm.id, name="Lone"),
                                Household(id=uuid.uuid4(), firm_id=firm.id, name="Gone", left_on=today - timedelta(days=5)))
            theirs = Household(id=uuid.uuid4(), firm_id=other.id, name="Kauri Family")
            s.add_all([park, lone, gone, theirs])
            await s.flush()
            jin = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=park.id, full_name="Jin Park")
            leo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lone.id, full_name="Leo Lone")
            s.add_all([jin, leo])
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=ann.id, to_type="household", to_id=park.id))
            await s.commit()

            def ex(u):
                return ToolExecutors(s, u.id, u.role, u.firm_id)

            async def count(model) -> int:
                return (await s.execute(select(func.count()).select_from(model))).scalar_one()

            umbrella = {"household_id": str(park.id), "person_id": str(jin.id), "category": "protection",
                        "recommendation": "Umbrella liability cover of $2m over the home and auto policies",
                        "facts": "Net worth on file $3.1m; a rental property; teenage drivers",
                        "alternatives": [{"option": "Raise the home policy's liability limit", "why_rejected": "does not reach the rental property"},
                                         {"option": "Do nothing", "why_rejected": "leaves the rental's liability uncovered"}],
                        "decision": "declined", "evidence_kind": "note", "evidence": "Meeting note: client declined on cost",
                        "revisit_on": (today + timedelta(days=180)).isoformat()}

            print("\n=== The Confirm card names the change and writes nothing ===")
            card = (await ex(ann).preview_change("record_recommendation", umbrella))["rows"]
            rows = {r["label"]: r["value"] for r in card}
            check("it names the client and the member", rows.get("Client"), "Park · Jin Park")
            check("  the type", rows.get("Type"), "Protection")
            check("  what was recommended", rows.get("Recommended"), umbrella["recommendation"])
            check("  the assumption version it is recorded against", rows.get("Assumption version"), planning.assumption_version(firm.settings))
            check("  the alternatives and why each was rejected", "Do nothing: rejected, leaves the rental's liability uncovered" in rows.get("Alternatives", ""), True)
            check("  the client's decision with its evidence", rows.get("Client's decision", "").startswith("Declined") and "client declined on cost" in rows.get("Client's decision", ""), True)
            check("  the owner, the recorded adviser by default", rows.get("Owner"), "Ann Adviser")
            check("  and that it sends nothing to the client", "sends nothing to the client" in rows.get("Changes", ""), True)
            check("showing the card wrote nothing", (await count(AdviceRecommendation), await count(AdviceOutcome)), (0, 0))
            await refused("compliance is offered no card", ex(cora).preview_change("record_recommendation", umbrella), "adviser or paraplanner")

            print("\n=== 'Recommended umbrella cover; client declined' is a row ===")
            row = await ex(ann).record_recommendation(umbrella)
            check("it is on file, declined", (row["category"], row["status"]), ("protection", "declined"))
            check("  with what was recommended and the facts it rested on", (row["recommendation"], row["facts"]), (umbrella["recommendation"], umbrella["facts"]))
            check("  the alternatives considered", [a["option"] for a in row["alternatives"]], ["Raise the home policy's liability limit", "Do nothing"])
            check("  the evidence of the decline", (row["evidence_kind"], row["evidence"]), ("note", "Meeting note: client declined on cost"))
            check("  the owner, due and revisit dates", (row["owner"], row["due_on"], row["revisit_on"]), ("Ann Adviser", None, umbrella["revisit_on"]))
            check("  for Jin, made today on the firm's calendar", (row["person"], row["recommended_on"], row["data_as_of"]), ("Jin Park", today.isoformat(), today.isoformat()))
            check("  who recorded it", (row["recorded_by"], row["recorded_by_role"]), (ann.email, "adviser"))
            check("  and the assumption version it was recorded against", row["assumption_version"], planning.assumption_version(firm.settings))
            check("the ledger has it", (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == "advice_recommended"))).scalar_one(), 1)

            print("\n=== The assumption version is captured, and stays ===")
            was = row["assumption_version"]
            firm.settings = {"cmas": {"equity": [0.02, 0.15]}}
            await s.flush()
            moved = planning.assumption_version(firm.settings)
            check("the firm's assumptions moved", moved != was, True)
            check("  the recommendation keeps the version it was recorded against", (await advice_file.one(s, uuid.UUID(row["id"]), firm_id=firm.id))["assumption_version"], was)
            later = await ex(pat).record_recommendation({"household_id": str(lone.id), "category": "investment",
                                                         "recommendation": "Move the cash surplus into the balanced model",
                                                         "facts": "Cash of $240k against six months' spending of $40k",
                                                         "alternatives": [{"option": "A term deposit ladder", "why_rejected": "below the goal's return"}]})
            check("  and a new one carries the new version", later["assumption_version"], moved)
            check("a paraplanner records; open needs no evidence", (later["status"], later["evidence"]), ("open", None))
            check("with no adviser on record, the owner is whoever recorded it", later["owner"], "Pat Para")

            print("\n=== A decision needs its evidence ===")
            await refused("declined with no evidence", ex(ann).preview_change("record_recommendation", {**umbrella, "evidence_kind": None, "evidence": None}), "document or a note")
            await refused("accepted with a kind but no words", ex(ann).record_recommendation({**umbrella, "decision": "accepted", "evidence": ""}), "name the note")
            await refused("evidence that is neither a document nor a note", ex(ann).record_recommendation({**umbrella, "evidence_kind": "email"}), "document or a note")
            await refused("implemented at once is not a decision", ex(ann).record_recommendation({**umbrella, "decision": "implemented"}), "open, accepted or declined")
            await refused("an outcome with no evidence", ex(ann).record_advice_outcome({"recommendation_id": later["id"], "status": "accepted"}), "document or a note")
            await refused("no alternative", ex(ann).record_recommendation({**umbrella, "alternatives": []}), "at least one alternative")
            await refused("an alternative with no reason", ex(ann).record_recommendation({**umbrella, "alternatives": [{"option": "x"}]}), "why it was rejected")
            await refused("an unknown type", ex(ann).record_recommendation({**umbrella, "category": "crypto"}), "type is one of")
            await refused("made tomorrow", ex(ann).record_recommendation({**umbrella, "recommended_on": (today + timedelta(days=1)).isoformat()}), "once it is made")
            await refused("due before it was made", ex(ann).record_recommendation({**umbrella, "due_on": (today - timedelta(days=1)).isoformat()}), "on or after")
            await refused("a person from another household", ex(ann).record_recommendation({**umbrella, "person_id": str(leo.id)}), "not in this household")
            await refused("a client who has left", ex(ann).record_recommendation({**umbrella, "household_id": str(gone.id), "person_id": None}), "has left")
            await refused("an owner nobody matches", ex(ann).record_recommendation({**umbrella, "owner": "Zed"}), "no one at this firm")
            await refused("a client cannot own it", ex(ann).record_recommendation({**umbrella, "owner": "Cleo Client"}), "no one at this firm")
            named = await ex(ann).record_recommendation({**umbrella, "owner": "Pat Para", "decision": "open", "evidence_kind": None, "evidence": None,
                                                         "category": "estate", "recommendation": "Update the wills after the move to Texas"})
            check("an owner may be named", named["owner"], "Pat Para")

            print("\n=== Added and never edited: an update is a new record, and the history stays ===")
            before = await s.get(AdviceRecommendation, uuid.UUID(later["id"]))
            words = before.recommendation
            outcome_card = {r["label"]: r["value"] for r in (await ex(ann).preview_change("record_advice_outcome", {
                "recommendation_id": later["id"], "status": "accepted", "evidence_kind": "document", "evidence": "Signed authority to proceed"}))["rows"]}
            check("the outcome card names where it stood and where it goes", (outcome_card.get("Was", "").split(",")[0], outcome_card.get("Now", "").split(",")[0]), ("Open", "Accepted"))
            check("  and the evidence", outcome_card.get("Evidence"), "Document: Signed authority to proceed")
            check("  writing nothing", (await count(AdviceOutcome)), 3)
            accepted = await ex(ann).record_advice_outcome({"recommendation_id": later["id"], "status": "accepted", "evidence_kind": "document",
                                                            "evidence": "Signed authority to proceed"})
            check("accepted, superseding open", [h["status"] for h in accepted["history"]], ["open", "accepted"])
            done = await ex(pat).record_advice_outcome({"recommendation_id": later["id"], "status": "implemented", "evidence_kind": "document",
                                                        "evidence": "Contract note 4471: $200k into the balanced model"})
            check("implemented, with every earlier step kept", [h["status"] for h in done["history"]], ["open", "accepted", "implemented"])
            check("  three outcome rows for it, none overwritten", (await s.execute(select(func.count()).select_from(AdviceOutcome)
                                                                     .where(AdviceOutcome.recommendation_id == uuid.UUID(later["id"])))).scalar_one(), 3)
            await s.refresh(before)
            check("  the recommendation as made is unchanged", before.recommendation, words)
            check("  the ledger has each outcome", (await s.execute(select(func.count()).select_from(LedgerEntry).where(LedgerEntry.event_type == "advice_outcome_recorded"))).scalar_one(), 2)
            await refused("nothing follows implemented", ex(ann).record_advice_outcome({"recommendation_id": later["id"], "status": "declined", "evidence_kind": "note", "evidence": "changed mind"}), "new recommendation")
            await refused("the same decision twice", ex(ann).record_advice_outcome({"recommendation_id": row["id"], "status": "declined", "evidence_kind": "note", "evidence": "again"}), "already declined")
            await refused("declined, then implemented without accepting", ex(ann).record_advice_outcome({"recommendation_id": row["id"], "status": "implemented", "evidence_kind": "document", "evidence": "policy schedule"}), "accepted first")
            await refused("open is where it starts", ex(ann).record_advice_outcome({"recommendation_id": row["id"], "status": "open", "evidence_kind": "note", "evidence": "reopened"}), "accepted or declined")
            await refused("an outcome dated tomorrow", ex(ann).record_advice_outcome({"recommendation_id": named["id"], "status": "accepted", "evidence_kind": "note", "evidence": "Call note",
                                                                                     "happened_on": (today + timedelta(days=1)).isoformat()}), "has happened")
            changed = await ex(ann).record_advice_outcome({"recommendation_id": row["id"], "status": "accepted", "evidence_kind": "document",
                                                           "evidence": "Signed umbrella application"})
            check("a client who declined may change their mind, on evidence", [h["status"] for h in changed["history"]], ["declined", "accepted"])
            check("  the revisit date stays as set", changed["revisit_on"], umbrella["revisit_on"])

            print("\n=== Rights: an adviser or paraplanner records; others read; a client gets nothing ===")
            await refused("compliance records no recommendation", ex(cora).record_recommendation(umbrella), "adviser or paraplanner")
            await refused("  nor an outcome", ex(cora).record_advice_outcome({"recommendation_id": named["id"], "status": "accepted", "evidence_kind": "note", "evidence": "x note"}), "adviser or paraplanner")
            await refused("  and the core module refuses it too, not only the tool",
                          advice_file.record_recommendation(s, firm.id, recorded_by=cora.email, role="compliance", **{k: v for k, v in umbrella.items()}), "adviser or paraplanner")
            await refused("an operations user records nothing", ex(opal).record_recommendation(umbrella), "adviser or paraplanner")
            for who in (cora, bea, pete, pat):
                got = await ex(who).read_advice_file({"household_id": str(park.id)})
                check(f"{who.role} reads it", len(got["recommendations"]), 2)
            await refused("a client reads none of it", ex(cleo).read_advice_file({"household_id": str(park.id)}), "read by advisers")
            await refused("operations reads none of it", ex(opal).read_advice_file({}), "read by advisers")

            print("\n=== The implementation rate: implemented over made, by type, with declined and open apart ===")
            # Two more, made a while ago: an open retirement one past its due date, and a declined tax one due a revisit.
            overdue = await ex(ann).record_recommendation({"household_id": str(park.id), "category": "retirement", "recommendation": "Raise the 401(k) deferral to the maximum",
                                                           "facts": "Salary on file $180k; deferral 6%", "alternatives": [{"option": "An IRA", "why_rejected": "income is over the deduction limit"}],
                                                           "recommended_on": (today - timedelta(days=40)).isoformat(), "due_on": (today - timedelta(days=10)).isoformat()})
            revisit = await ex(ann).record_recommendation({"household_id": str(park.id), "category": "tax", "recommendation": "Harvest the loss in the bond fund",
                                                           "facts": "Unrealised loss on file of $12k", "alternatives": [{"option": "Hold", "why_rejected": "wastes the loss"}],
                                                           "decision": "declined", "evidence_kind": "note", "evidence": "Call note: client prefers to hold",
                                                           "recommended_on": (today - timedelta(days=30)).isoformat(), "decided_on": (today - timedelta(days=29)).isoformat(),
                                                           "revisit_on": (today - timedelta(days=1)).isoformat()})
            old = await ex(ann).record_recommendation({"household_id": str(park.id), "category": "investment", "recommendation": "Rebalance into the growth model",
                                                       "facts": "Equity 40% against a growth profile", "alternatives": [{"option": "Stay", "why_rejected": "under-risked"}],
                                                       "recommended_on": (today - timedelta(days=400)).isoformat()})
            await ex(ann).record_advice_outcome({"recommendation_id": old["id"], "status": "implemented", "evidence_kind": "document", "evidence": "Trade confirmations",
                                                 "happened_on": (today - timedelta(days=390)).isoformat()})
            check("past its due date and not done is overdue", (overdue["status"], overdue["overdue"]), ("open", True))
            check("declined with its revisit date reached is due a revisit", (revisit["status"], revisit["revisit_due"]), ("declined", True))
            firm_view = await ex(cora).read_advice_file({})
            o = firm_view["implementation"]["overall"]
            check("over the last year: made, implemented, accepted, declined, open", (o["made"], o["implemented"], o["accepted"], o["declined"], o["open"]), (5, 1, 1, 1, 2))
            check("  the rate is implemented over made", o["rate"], 0.2)
            by = {c["category"]: c for c in firm_view["implementation"]["by_category"]}
            check("  by type", {k: (v["made"], v["implemented"], v["rate"]) for k, v in by.items()},
                  {"protection": (1, 0, 0.0), "investment": (1, 1, 1.0), "estate": (1, 0, 0.0), "retirement": (1, 0, 0.0), "tax": (1, 0, 0.0)})
            check("  declined and open are counted apart, never in the rate", (by["tax"]["declined"], by["retirement"]["open"], by["protection"]["accepted"]), (1, 1, 1))
            check("  one made more than a year ago is outside the period", firm_view["implementation"]["since"], (today - timedelta(days=365)).isoformat())
            all_time = await ex(cora).read_advice_file({"household_id": str(park.id)})
            check("a household's rate is over all time", (all_time["implementation"]["overall"]["made"], all_time["implementation"]["overall"]["implemented"]), (5, 1))
            check("overdue and revisit-due are counted", (firm_view["summary"]["overdue"], firm_view["summary"]["revisit_due"]), (1, 1))
            check("  and listed first", [r["id"] for r in firm_view["recommendations"][:2]], [overdue["id"], revisit["id"]])
            span = await ex(cora).read_advice_file({"since": (today - timedelta(days=500)).isoformat(), "until": (today - timedelta(days=380)).isoformat()})
            check("a period of its own: the old one, implemented", (span["implementation"]["overall"]["made"], span["implementation"]["overall"]["rate"]), (1, 1.0))
            none = await ex(cora).read_advice_file({"since": "2001-01-01", "until": "2001-12-31"})
            check("nothing made in a period is no rate, never zero", (none["implementation"]["overall"]["made"], none["implementation"]["overall"]["rate"]), (0, None))
            check("  and the rate is from the records alone", "only what is recorded here is counted" in none["not_on_file"].lower(), True)
            only = await ex(cora).read_advice_file({"category": "investment"})
            check("one type only", {r["category"] for r in only["recommendations"]}, {"investment"})
            await refused("an unknown type to filter on", ex(cora).read_advice_file({"category": "crypto"}), "type is one of")
            await refused("a period backwards", ex(cora).read_advice_file({"since": today.isoformat(), "until": (today - timedelta(days=1)).isoformat()}), "on or before")

            print("\n=== Firm scope ===")
            await refused("another firm reads no household of ours", ex(kiri).read_advice_file({"household_id": str(park.id)}), "not found")
            check("  and its firm-wide file holds none of ours", (await ex(kiri).read_advice_file({}))["recommendations"], [])
            await refused("  records no outcome on ours", ex(kiri).record_advice_outcome({"recommendation_id": named["id"], "status": "accepted", "evidence_kind": "note", "evidence": "Call note"}), "not on this firm")
            await refused("  and puts nothing on our household", ex(kiri).record_recommendation(umbrella), "not in this firm")
            await refused("nor do we on theirs", ex(ann).record_recommendation({**umbrella, "household_id": str(theirs.id), "person_id": None}), "not in this firm")
    finally:
        await db.dispose()


def main() -> int:
    catalogue()
    asyncio.run(db_checks())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
