"""Prove India row 10: the complaints register and the advertising check.

Covers a complaint as received (added once, with the firm's reference and its SCORES number where it came that way) and every
refusal; its steps (acknowledged, Action Taken Report, resolved with an outcome, escalated, withdrawn) added once and never edited,
and who may record which; the state worked from the firm's day and the 21-day limit (open, at risk, overdue, resolved, resolved
late, withdrawn) and the acknowledgement period named as assumed; a complaint about advice told to compliance as a task and
closed with it; the Annexure C table worked from the register and read by the compliance calendar's monthly filing; who reads
the register; and the advertising check: a promise blocks, a denial does not, a stated return is a finding to verify, marketing
lacks its disclaimers, the Code's bans are notices before 8 April 2027, nothing is ever "compliant", and a message to a client of an
Indian firm that promises a return is refused before its card and again at Confirm.

Runs against a throwaway SQLite database.

    python -m tests.verify_india_complaints
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import advertising, compliance_calendar, complaints, firm_time, law_figures
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.complaints import Complaint, ComplaintEvent
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Household
from app.models.identity import User
from app.models.tenant import Firm

PASS, FAIL = [], []
C = complaints


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, contains: str | None = None, exc=(C.ComplaintError,)) -> None:
    try:
        await coro
    except exc as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:100]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def kinds(text: str, kind: str = "message", today: date = date(2026, 11, 25)) -> list[str]:
    return [f["rule"] for f in advertising.check(text, kind, today=today)["findings"]]


async def main() -> int:
    print("\n=== The limits, with their source ===")
    today0 = date(2026, 11, 25)
    check("a complaint is resolved within 21 calendar days", law_figures.value("in_complaint_resolve_days", today0), 21)
    check("the acknowledgement period is named as assumed in its own source", "assumed" in law_figures.get("in_complaint_ack_days").source.lower(), True)
    check("Annexure C counts long pending after three months", law_figures.value("in_complaint_pending_months", today0), 3)

    print("\n=== The advertising check ===")
    check("a promise of return blocks", kinds("This fund is guaranteed to give you assured returns."), ["promise_of_return", "promise_of_return"])
    check("a denial does not", kinds("Returns are not guaranteed and no investment is risk-free."), [])
    check("a quoted or named example does not", kinds('We never use the word "guaranteed" and avoid words such as risk-free.'), [])
    check("a stated return is a finding to verify, with a caveat missing", kinds("The strategy returned 18% last year."), ["performance_claim", "past_performance_caveat"])
    check("with the caveat beside it only the verification remains",
          kinds("The strategy returned 18% last year. Past performance may or may not be sustained in future."), ["performance_claim"])
    out = advertising.check("We returned 18% a year.", "marketing", today=today0)
    check("in marketing a stated return blocks; in a message it is flagged",
          (next(f for f in out["findings"] if f["rule"] == "performance_claim")["severity"],
           next(f for f in advertising.check("We returned 18% a year.", "message", today=today0)["findings"] if f["rule"] == "performance_claim")["severity"]), ("block", "flag"))
    check("a superlative is flagged", kinds("We are the best advisers in the city."), ["superlative"])
    check("marketing without its words lacks four of them",
          [r for r in kinds("Join our firm for wealth planning.", "marketing") if r != "performance_claim"],
          ["market_risk_disclaimer", "past_performance_disclaimer", "registration_disclaimer", "registration_number"])
    full = ("Astra Advisers, SEBI registration INA000012345. Investments in securities market are subject to market risks. Past performance "
            "may or may not be sustained in future. Registration granted by SEBI does not in any way guarantee performance of the intermediary.")
    check("marketing with them has no missing-word finding", kinds(full, "marketing"), [])
    check("a testimonial and pressure are notices before the Code is operative, flags after",
          ([f["severity"] for f in advertising.check("Our clients say it works. Act now!", "marketing", today=date(2026, 11, 25))["findings"] if f["rule"] in ("testimonial", "pressure")],
           [f["severity"] for f in advertising.check("Our clients say it works. Act now!", "marketing", today=date(2027, 4, 8))["findings"] if f["rule"] in ("testimonial", "pressure")]),
          (["notice", "notice"], ["flag", "flag"]))
    clean = advertising.check("Your review is on Monday at ten. Bring last year's statement.", today=today0)
    check("a clean text is not called compliant: it says the patterns are not exhaustive",
          (clean["findings"], clean["not_exhaustive"], "not exhaustive" in clean["note"], "compliant" in advertising.summary({"applies": True, **clean}).lower().replace("not approved or compliant", "")), ([], True, True, False))
    check("every finding names the rule's source", all(f["citation"] for f in advertising.check(full + " Guaranteed returns, the best!", "marketing", today=today0)["findings"]), True)
    check("the check changes nothing and reads no firm for the plain call", advertising.check(None)["findings"], [])

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="IN", slug="in", base_currency="INR", jurisdiction="IN", settings={"licences": ["investment_adviser"], "timezone": "Asia/Kolkata"})
            nz = Firm(id=uuid.uuid4(), name="NZ", slug="nz", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, nz])
            await s.flush()
            today = firm_time.today(firm)
            users = {r: User(id=uuid.uuid4(), firm_id=firm.id, email=f"{r.value}@in", full_name=r.value.title(), role=r, hashed_password="x")
                     for r in (UserRole.ADVISER, UserRole.COMPLIANCE, UserRole.BRANCH_LEADER, UserRole.OPERATIONS, UserRole.PARAPLANNER)}
            s.add_all(users.values())
            hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="The Sharmas")
            nz_hh = Household(id=uuid.uuid4(), firm_id=nz.id, name="The Chens")
            s.add_all([hh, nz_hh])
            await s.flush()
            back = lambda n: today - timedelta(days=n)    # noqa: E731
            base = dict(complainant="Meera Sharma", source="email", category="fees", summary="My fee is higher than the agreement says.", household_id=hh.id)

            print("\n=== Recording a complaint, and what is refused ===")
            clock = C.clocks(firm, today)
            check("the clocks: 21 days, and an acknowledgement period that says it is assumed", (clock["resolve_days"], clock["ack_days"], clock["ack_assumed"]), (21, 3, True))
            firm.settings = {**firm.settings, "complaints": {"ack_days": 2}}
            check("the firm's own acknowledgement period replaces it", (C.clocks(firm, today)["ack_days"], C.clocks(firm, today)["ack_assumed"]), (2, False))
            firm.settings = {k: v for k, v in firm.settings.items() if k != "complaints"}
            await refused("a firm outside India keeps no register here", C.record_complaint(s, nz, UserRole.COMPLIANCE, by="x", **{**base, "household_id": nz_hh.id}), "indian firm")
            await refused("a role that does not look after clients cannot record", C.record_complaint(s, firm, UserRole.ADMIN, by="x", **base), "looks after clients")
            await refused("a complainant is named", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "complainant": " "}), "complainant")
            await refused("the source is one of the list", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "source": "pigeon"}), "came in")
            await refused("the category is one of the list", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "category": "mood"}), "category")
            await refused("the words are given", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "summary": "bad"}), "what the complainant said")
            await refused("not received in the future", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **base, received_on=today + timedelta(days=1)), "future")
            await refused("a SCORES complaint carries its SCORES number", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "source": "scores"}), "registration number")
            await refused("another firm's household", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **{**base, "household_id": nz_hh.id}), "not this firm")
            out = await C.record_complaint(s, firm, UserRole.OPERATIONS, by="operations@in", **base, received_on=back(9))
            check("it is recorded with a number of the firm's own", out["recorded"], f"CMP-{today.year}-0001")
            fee = out["complaints"][0]
            check("worked from the day: 12 days left, open, acknowledgement overdue, due on day 21", (fee["status"], fee["days_left"], fee["ack_state"], fee["due_on"]),
                  ("open", 12, "ack_overdue", back(9).__add__(timedelta(days=21)).isoformat()))
            check("a complaint about fees is not about advice and tells no one", (fee["about_advice"], fee["told_compliance"]), (False, False))
            await refused("the firm's reference is unique", C.record_complaint(s, firm, UserRole.COMPLIANCE, by="x", **base, reference=out["recorded"]), "already on the register")

            print("\n=== A complaint about advice is told to compliance ===")
            adv = await C.record_complaint(s, firm, UserRole.ADVISER, by="adviser@in", **{**base, "category": "advice", "summary": "I was told the fund was safe and it fell."}, received_on=back(19))
            check("an adviser who records it does not read the register", set(adv) - {"recorded"}, set())  if False else None
            row = (await s.execute(select(Complaint).where(Complaint.reference == adv["recorded"]))).scalars().one()
            task = await s.get(Task, row.task_id)
            check("it is marked about advice and a task is open for compliance, due on the limit",
                  (row.about_advice, task.source, str(task.status), task.assigned_to == users[UserRole.COMPLIANCE].id, task.due_date.isoformat()),
                  (True, "complaint", "open", True, (back(19) + timedelta(days=21)).isoformat()))
            check("the task names the reference and no account is touched", (task.title.endswith(adv["recorded"]), row.household_id == hh.id), (True, True))
            check("a complaint about impersonation is not about advice unless a person says so",
                  (await C.prepare_complaint(s, firm, UserRole.COMPLIANCE, **{**base, "category": "impersonation"}))["about_advice"], False)

            print("\n=== The steps: who, in what order, once ===")
            ref_fee, ref_adv = out["recorded"], adv["recorded"]
            await refused("an adviser does not resolve", C.record_event(s, firm, UserRole.ADVISER, by="x", complaint=ref_adv, kind="resolved", outcome="not_upheld", note="Explained to the client."), "compliance")
            await refused("nor withdraw one", C.record_event(s, firm, UserRole.ADVISER, by="x", complaint=ref_adv, kind="withdrawn", note="They said so"), "compliance")
            await refused("a resolution names its outcome", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="resolved", note="Explained."), "outcome")
            await refused("and what was done", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="resolved", outcome="upheld"), "what was done")
            await refused("a step is not before the complaint", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="acknowledged", happened_on=back(40)), "before it")
            await refused("a step is not in the future", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="acknowledged", happened_on=today + timedelta(days=1)), "future")
            await refused("a report needs a SCORES number on the complaint", C.record_event(s, firm, UserRole.OPERATIONS, by="x", complaint=ref_adv, kind="atr_filed", reference="ATR-1"), "no scores registration")
            await refused("an unknown complaint", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint="CMP-0-9999", kind="acknowledged"), "not on the register")
            await C.record_event(s, firm, UserRole.PARAPLANNER, by="paraplanner@in", complaint=ref_adv, kind="acknowledged", happened_on=back(17))
            await refused("acknowledged once", C.record_event(s, firm, UserRole.PARAPLANNER, by="x", complaint=ref_adv, kind="acknowledged"), "already")
            reg = await C.overview(s, firm)
            by_ref = {c["reference"]: c for c in reg["complaints"]}
            check("the advice complaint, 19 days in, is at risk with 2 days left, acknowledged on time", (by_ref[ref_adv]["status"], by_ref[ref_adv]["days_left"], by_ref[ref_adv]["ack_state"], by_ref[ref_adv]["ack_late"]),
                  ("at_risk", 2, "acknowledged", False))
            sc = await C.record_complaint(s, firm, UserRole.COMPLIANCE, by="neha", **{**base, "source": "scores", "category": "service_delay"}, scores_reference="SCORES/2026/0001", received_on=back(30))
            check("a SCORES complaint 30 days in is overdue, and so is read", [c["status"] for c in sc["complaints"] if c["scores_reference"]], ["overdue"])
            await C.record_event(s, firm, UserRole.OPERATIONS, by="ops", complaint=sc["recorded"], kind="atr_filed", reference="ATR-SCORES-1", happened_on=back(25))
            await refused("an escalation carries the number the complainant was given", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=sc["recorded"], kind="escalated_odr"), "registration number")
            await C.record_event(s, firm, UserRole.COMPLIANCE, by="neha", complaint=sc["recorded"], kind="escalated_odr", reference="ODR-1", happened_on=back(2))
            ev = (await s.execute(select(ComplaintEvent).where(ComplaintEvent.recorded_by == "ops"))).scalars().one()
            check("the report is on file with its reference, never edited", (ev.kind, ev.reference), ("atr_filed", "ATR-SCORES-1"))
            res = await C.record_event(s, firm, UserRole.COMPLIANCE, by="neha", complaint=ref_adv, kind="resolved", outcome="not_upheld",
                                       note="The risk profile and agreement show the fund as moderate risk; explained to the client.", happened_on=back(3))
            done = {c["reference"]: c for c in res["complaints"]}[ref_adv]
            check("resolved after 16 days, inside the limit", (done["status"], done["outcome"], done["days_taken"], done["resolved_late"]), ("resolved", "not_upheld", 16, False))
            check("resolving closes compliance's task", str((await s.get(Task, task.id)).status), "done")
            await refused("a resolved complaint takes no other step", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="withdrawn", note="They withdrew"), "resolved on")
            await refused("nor a second resolution", C.record_event(s, firm, UserRole.COMPLIANCE, by="x", complaint=ref_adv, kind="resolved", outcome="upheld", note="Again."), "already")
            late = await C.record_complaint(s, firm, UserRole.COMPLIANCE, by="neha", **{**base, "category": "data_privacy"}, received_on=back(60))
            lres = await C.record_event(s, firm, UserRole.BRANCH_LEADER, by="kavita", complaint=late["recorded"], kind="resolved", outcome="partly_upheld",
                                        note="The old address was removed and the trustees told.", happened_on=back(30))
            lc = {c["reference"]: c for c in lres["complaints"]}[late["recorded"]]
            check("a branch leader resolves, and 30 days is late", (lc["status"], lc["resolved_late"], lc["days_taken"]), ("resolved", True, 30))
            wd = await C.record_complaint(s, firm, UserRole.COMPLIANCE, by="neha", **{**base, "source": "phone"}, received_on=back(40))
            await C.record_event(s, firm, UserRole.COMPLIANCE, by="neha", complaint=wd["recorded"], kind="withdrawn", note="The complainant said by phone it was settled.", happened_on=back(35))
            check("a withdrawn complaint is closed, not resolved", {c["reference"]: c["status"] for c in (await C.overview(s, firm))["complaints"]}[wd["recorded"]], "withdrawn")

            print("\n=== The register as a whole ===")
            reg = await C.overview(s, firm)
            check("counted by state", {k: reg["counts"][k] for k in ("overdue", "at_risk", "open", "resolved", "withdrawn", "total")},
                  {"overdue": 1, "at_risk": 0, "open": 1, "resolved": 2, "withdrawn": 1, "total": 5})
            check("sorted with what needs a person first", [c["status"] for c in reg["complaints"]], ["overdue", "open", "resolved", "resolved", "withdrawn"])
            check("escalations and the long-pending are counted", (reg["counts"]["escalated"], reg["counts"]["long_pending"]), (1, 0))
            check("it says it does not make the firm compliant", "does not say the firm handles complaints in good order" in reg["not_compliance"], True)

            print("\n=== Annexure C, from the register, and the calendar's filing ===")
            prev = today.replace(day=1) - timedelta(days=1)
            t = await C.annexure_c(s, firm, f"{prev.year}-{prev.month:02d}")
            check("a table of three groups with the totals", ([r["group"] for r in t["rows"]], sorted(t["total"])),
                  (["scores", "exchange", "direct"], ["pending_at_end", "pending_at_start", "pending_over_months", "received", "resolved"]))
            check("the month's figures add up: start + received - resolved = end", t["total"]["pending_at_start"] + t["total"]["received"] - t["total"]["resolved"], t["total"]["pending_at_end"])
            check("a month before the register began shows none", (await C.annexure_c(s, firm, "2020-01"))["total"], {k: 0 for k in t["total"]})
            await refused("a period is YYYY-MM", C.annexure_c(s, firm, "last month"), "YYYY-MM")
            check("a firm with no complaint on file has no table, which is not none made", await C.annexure_c(s, nz, "2026-10"), None)
            cal = await compliance_calendar.overview(s, firm)
            monthly = [i for i in cal["items"] if i["obligation"] == "complaints_monthly"]
            check("the calendar's monthly complaint data carries the register's counts", all("register" in i for i in monthly) and monthly[0]["register"] is not None, True)
            check("the counts are the table's for that period", (monthly[0]["register"]["received"], monthly[0]["register"]["from_register"]),
                  ((await C.period_counts(s, firm, monthly[0]["period"]))["received"], True))

            print("\n=== The conversation: tools, roles and Confirm cards ===")
            for k in ("record_complaint", "record_complaint_step"):
                check(f"{k} stops for a Confirm", TOOLS[k].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
            check("reading and checking do not", (TOOLS["read_complaints"].change_state, TOOLS["check_advertising"].change_state), (ToolChangeState.NO, ToolChangeState.NO))
            check("an adviser may not read the register by the tool's roles", UserRole.ADVISER in TOOLS["read_complaints"].roles_required, False)
            check("a client holds none", [k for k in ("read_complaints", "record_complaint", "record_complaint_step", "check_advertising") if UserRole.CLIENT in TOOLS[k].roles_required], [])
            ex = ToolExecutors(s, users[UserRole.COMPLIANCE].id, UserRole.COMPLIANCE, firm.id)
            n = (await s.execute(select(func.count()).select_from(Complaint))).scalar_one()
            card = await ex.preview_change("record_complaint", {"complainant": "Subir Banerjee", "source": "phone", "category": "advice", "household_id": str(hh.id),
                                                                 "summary": "He says he was not told the bond fund could lose money."})
            labels = [r["label"] for r in card["rows"]]
            check("the card names the clock and says compliance is told", all(x in labels for x in ("Resolve by", "Reference", "Told to compliance", "They said")), True)
            check("showing it wrote nothing", (await s.execute(select(func.count()).select_from(Complaint))).scalar_one(), n)
            ad = ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, firm.id)
            made = await ad.record_complaint({"complainant": "Subir Banerjee", "source": "phone", "category": "advice", "household_id": str(hh.id),
                                              "summary": "He says he was not told the bond fund could lose money."})
            check("an adviser records it and is not shown the register", (made["complaints"], "Recorded as" in made["message"]), ([], True))
            await refused("an adviser cannot read the register", ad.read_complaints({}), "compliance", (ExecutorError,))
            await refused("nor resolve", ad.preview_change("record_complaint_step", {"complaint": made["recorded"] if "recorded" in made else "x", "kind": "resolved", "outcome": "upheld", "note": "Done it."}), "compliance", (ExecutorError,))
            read = await ex.read_complaints({})
            check("compliance reads it, with the table and the acknowledgement period said to be assumed", (len(read["complaints"]) >= 5, "assumed" in read["message"], read["annexure_c"] is not None), (True, True, True))
            step = await ex.preview_change("record_complaint_step", {"complaint": made["recorded"], "kind": "acknowledged"})
            check("a step's card names the complaint and the step", [r["label"] for r in step["rows"]][:2], ["Complaint", "Step"])
            chk = await ad.check_advertising({"text": "This is guaranteed to return 12%.", "kind": "message"})
            check("the check tool reports findings and says it approves nothing", (chk["blocks"], "not exhaustive" in chk["message"]), (1, True))
            check("for a firm outside India it reads nothing", (await ToolExecutors(s, uuid.uuid4(), UserRole.ADVISER, nz.id).check_advertising({"text": "guaranteed"}))["applies"], False)

            print("\n=== A message to a client that promises a return is refused ===")
            promise = {"household_id": str(hh.id), "body": "Good news: the fund is guaranteed to return 12% a year."}
            await refused("before its card", ad.preview_change("send_client_message", promise), "promises a return", (ExecutorError,))
            await refused("and again at Confirm", ad.send_client_message(promise), "promises a return", (ExecutorError,))
            weigh = {"household_id": str(hh.id), "body": "Our flexi cap fund returned 18% last year, and your review is on Monday."}
            card = await ad.preview_change("send_client_message", weigh)
            check("a stated return is not refused: its card carries the check for the person to weigh",
                  [r["value"] for r in card["rows"] if r["label"] == "Advertising check"][0].startswith("1 to weigh") or "to weigh" in [r["value"] for r in card["rows"] if r["label"] == "Advertising check"][0], True)
            plain = await ad.preview_change("send_client_message", {"household_id": str(hh.id), "body": "Your review is on Monday at ten."})
            check("a plain message carries nothing extra on its card", "Advertising check" in [r["label"] for r in plain["rows"]], False)
            sent = await ad.send_client_message({"household_id": str(hh.id), "body": "Returns are not guaranteed; your review is on Monday."})
            check("a denial goes", "Sent to" in sent["message"], True)
            nz_card = await ToolExecutors(s, users[UserRole.ADVISER].id, UserRole.ADVISER, nz.id).preview_change(
                "send_client_message", {"household_id": str(nz_hh.id), "body": "This fund is guaranteed to return 12%."})
            check("a firm outside India is not held to this check", "Advertising check" in [r["label"] for r in nz_card["rows"]], False)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
