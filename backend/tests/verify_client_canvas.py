"""The client's Canvas (B-95): the client's questions go to the conversation, with tools that read their own
household and change nothing, and staff open what a client sees beside theirs.

    python -m tests.verify_client_canvas

Against a throwaway SQLite database, this checks that:

  - every tool a client holds reads only and takes no household or person id; the client's tools read the
    signed-in client's household whatever the model passes, and a client tied to none reads nothing;
  - the gateway gives a client those tools alone and a prompt of its own that sends advice to the adviser;
  - a client's conversation names no other subject;
  - staff read what a client sees, and recording a questionnaire stops at a Confirm card naming each answer,
    for an adviser or paraplanner only, with every question answered from the form's own options;
  - the routes: the summary PDF prints the real total (it printed 0), a fee no schedule or band sets is not
    on file (it was 75 bps), a staff member does not take a next-gen member's steps, a client never reaches
    another person's journey or another household's report, and only an adviser or paraplanner records a
    questionnaire for a client;
  - the one-shot assistant is gone and the views are registered beside the conversation.
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.api import canvas, threads
from app.aurea_core import firm_time
from app.core.db import Base
from app.core.tools import CLIENT_TOOLS, ONE_CLIENT_VIEWS, OPEN_VIEWS, TOOLS, ToolChangeState, anthropic_tools_for_role, tools_for_role
from app.llm import gateway
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_experience import HeirJourney
from app.models.engagement import ClientReport
from app.models.enums import MarketType, ReportStatus, UserRole
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, Price
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm
from app.models.thread import Thread

PASS, FAIL = [], []
R = UserRole
ROOT = Path(__file__).resolve().parents[2]
CLIENT_READS = ("read_my_wealth", "read_my_net_worth", "read_my_ips", "read_my_retirement", "read_my_goals", "read_my_fees", "read_my_report")
ANSWERS = [{"question": "investment_horizon", "answer": "long"}, {"question": "loss_tolerance", "answer": "medium"},
           {"question": "income_stability", "answer": "stable"}, {"question": "liquidity_needs", "answer": "5+ years"},
           {"question": "esg_priority", "answer": "somewhat"}, {"question": "experience", "answer": "Intermediate"}]


def pdf_text(raw: bytes) -> bytes:
    """The PDF's page streams, inflated: FPDF compresses them."""
    import zlib

    out = b""
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.S):
        try:
            out += zlib.decompress(m.group(1))
        except zlib.error:
            out += m.group(1)
    return out


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, contains: str = "") -> None:
    try:
        await coro
        check(label, "allowed", "refused")
    except (ExecutorError, HTTPException) as exc:
        text = str(getattr(exc, "detail", exc))
        check(label, "refused" if contains.lower() in text.lower() else text, "refused")


def catalogue() -> None:
    print("\n=== A client's tools read their own household and change nothing ===")
    check("the client holds the seven reads", sorted(CLIENT_TOOLS), sorted(CLIENT_READS))
    check("and nothing else", sorted(tools_for_role(R.CLIENT)), sorted(CLIENT_READS))
    for key in CLIENT_TOOLS:
        tool = TOOLS[key]
        check(f"{key} changes nothing", tool.change_state, ToolChangeState.NO)
        check(f"{key} takes no household or person id", [i.name for i in tool.inputs if i.name in ("household_id", "person_id", "subject_id")], [])
        check(f"{key} is a client's alone", {r.value for r in tool.roles_required}, {"client"})
    check("the model is given those tools alone", sorted(t["name"] for t in anthropic_tools_for_role(R.CLIENT)), sorted(CLIENT_READS))
    check("no staff tool is a client's", [k for k, t in TOOLS.items() if R.CLIENT in t.roles_required and k not in CLIENT_READS], [])

    print("\n=== Staff see what a client sees, and record a questionnaire behind a Confirm ===")
    check("read_client_canvas changes nothing", TOOLS["read_client_canvas"].change_state, ToolChangeState.NO)
    check("held by the Client portal's roles and admin", {r.value for r in TOOLS["read_client_canvas"].roles_required},
          {"adviser", "paraplanner", "branch_leader", "admin"})
    check("record_questionnaire stops for a Confirm", TOOLS["record_questionnaire"].change_state, ToolChangeState.PAUSE_FOR_CONFIRMATION)
    check("an adviser or paraplanner records one", {r.value for r in TOOLS["record_questionnaire"].roles_required}, {"adviser", "paraplanner"})

    print("\n=== The client's prompt is its own ===")
    prompt = gateway.CLIENT_PROMPT_TEMPLATE
    check("it names their household only", "their household only" in prompt, True)
    check("it gives no advice and sends it to the adviser", "You give no advice" in prompt and "their adviser" in prompt, True)
    check("it changes nothing", "You cannot change anything" in prompt, True)
    check("it carries no client facts, only the firm and the day", sorted(re.findall(r"\{(\w+)\}", prompt)), ["firm", "today"])

    print("\n=== The one-shot assistant is gone, and the views open beside the conversation ===")
    check("no /api/canvas/assistant route", any(getattr(r, "path", "") == "/api/canvas/assistant" for r in canvas.router.routes), False)
    panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
    pane_body = (ROOT / "frontend" / "app" / "workspace" / "components" / "WorkPane.tsx").read_text(encoding="utf-8")
    renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
    for view in ("canvas", "canvas_retirement", "canvas_goals", "canvas_next_gen", "canvas_questionnaire"):
        check(f"{view} is a view open_view knows, on one client", view in OPEN_VIEWS and view in ONE_CLIENT_VIEWS, True)
        check(f"{view} is a pane view", f'key: "{view}"' in panes and f'case "{view}":' in pane_body, True)
    for key in ("read_client_canvas", "record_questionnaire", *CLIENT_READS):
        check(f"{key} draws a card", f'case "{key}":' in renderer, True)
    old = (ROOT / "frontend" / "app" / "(app)" / "canvas" / "page.tsx").read_text(encoding="utf-8")
    check("the Canvas page is the view, not a copy of it", len(old.splitlines()) < 30 and "CanvasView" in old, True)
    check("and has no assistant box of its own", "/api/canvas/assistant" in old, False)
    for page in ("canvas", "canvas/goals", "canvas/retirement", "canvas/next-gen", "canvas/questionnaire"):
        src = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "frontend" / "app" / "(app)" / page).glob("*.tsx"))
        check(f"/{page} uses no retired class", re.findall(r"\b(?:navy-\d+|gold(?:-soft|-dark)?|btn-gold)\b", src), [])
    shell = (ROOT / "frontend" / "components" / "Shell.tsx").read_text(encoding="utf-8")
    check("a client's Canvas carries the docked conversation",
          bool(re.search(r'showDock = .*isClient && pathname\.startsWith\("/canvas"\)', shell)), True)


async def behaviour() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = Firm(id=uuid.uuid4(), name="Tui Wealth", slug="t", base_currency="NZD", jurisdiction="NZ")
            other = Firm(id=uuid.uuid4(), name="O", slug="o", base_currency="NZD", jurisdiction="NZ")
            s.add_all([firm, other])
            await s.flush()
            today = firm_time.today(firm)
            lee = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lee")
            kim = Household(id=uuid.uuid4(), firm_id=firm.id, name="Kim")
            zed = Household(id=uuid.uuid4(), firm_id=other.id, name="Zed")
            s.add_all([lee, kim, zed])
            await s.flush()
            mia = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lee.id, full_name="Mia Lee")
            leo = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=lee.id, full_name="Leo Lee", is_next_gen=True)
            sam = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=kim.id, full_name="Sam Kim", is_next_gen=True)
            s.add_all([mia, leo, sam])
            await s.flush()
            users = {}
            for key, role, person in (("ann", R.ADVISER, None), ("pat", R.PARAPLANNER, None), ("ola", R.OPERATIONS, None),
                                      ("bea", R.BRANCH_LEADER, None), ("mia", R.CLIENT, mia), ("leo", R.CLIENT, leo),
                                      ("lone", R.CLIENT, None)):
                users[key] = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t.test", full_name=key.title(),
                                  hashed_password="x", role=role, person_id=person.id if person else None)
                s.add(users[key])
            await s.flush()

            for household, person, value in ((lee, mia, 400000), (kim, sam, 900000)):
                m = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=person.id, name=f"{household.name} mandate",
                            mandate_type="advisory", suitability={}, constraints={})
                s.add(m)
                await s.flush()
                acc = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Main", custodian="C", currency="NZD", cash_balance=0)
                inst = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=f"F{household.name}", name="Fund", asset_class="equity",
                                  market_type=MarketType.PUBLIC, currency="NZD")
                s.add_all([acc, inst])
                await s.flush()
                s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=10, currency="NZD", source="test", is_real=True))
                s.add(Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acc.id, instrument_id=inst.id, quantity=value / 10,
                              market_value=value, cost_basis=value))
                s.add(Goal(firm_id=firm.id, household_id=household.id, name=f"{household.name} retirement", kind="retirement",
                           target_amount=value * 2))
            kim_report = ClientReport(id=uuid.uuid4(), firm_id=firm.id, household_id=kim.id, title="Kim quarterly", period="Q3",
                                      status=ReportStatus.CLIENT_READY, sections=[{"heading": "H", "body": "B"}], data={})
            s.add(kim_report)
            await s.flush()

            def ex(key):
                u = users[key]
                return ToolExecutors(s, u.id, UserRole(u.role), firm.id)

            print("\n=== The client reads their own household, whatever is passed ===")
            mine = await ex("mia").execute("read_my_wealth", {"household_id": str(kim.id)})
            check("Mia's wealth is the Lee household's", (mine["household_id"], mine["total_wealth"]), (str(lee.id), 400000.0))
            check("every goal's likelihood is marked an estimate", all(g["estimate"] for g in mine["goals"]), True)
            check("and says the horizon was assumed", mine["goals"][0]["assumed"], ["years", "funding_share"])
            check("another family's members are not in it", "members" in mine, False)
            worth = await ex("mia").execute("read_my_net_worth", {"household_id": str(kim.id)})
            check("her net worth is hers", worth["household_id"], str(lee.id))
            check("with no debt on file it says assets only", worth["basis"], "assets_only")
            fees = await ex("mia").execute("read_my_fees", {})
            check("a fee no schedule or band sets is not on file", (fees["source"], fees["fee_bps"], fees["annual_fee"]), ("not_on_file", None, None))
            await refused("Kim's report is not Mia's to read", ex("mia").execute("read_my_report", {"report_id": str(kim_report.id)}), "not one published to you")
            await refused("a client tied to no household reads nothing", ex("lone").execute("read_my_wealth", {}), "No household")
            await refused("a staff member cannot borrow a client's tool", ToolExecutors(s, users["ann"].id, R.ADVISER, firm.id).read_my_wealth({}),
                          "client's own")
            ips = await ex("mia").execute("read_my_ips", {})
            check("with no IPS on file it says so, and not that there is none", (ips["policies"], "does not mean" in ips["message"]), ([], True))

            print("\n=== Staff see what a client sees ===")
            seen = await ex("bea").execute("read_client_canvas", {"household_id": str(kim.id)})
            check("a branch leader sees the Kims' page", (seen["household_id"], seen["total_wealth"], [n["name"] for n in seen["next_gen"]]),
                  (str(kim.id), 900000.0, ["Sam Kim"]))
            await refused("another firm's household is not found", ex("ann").execute("read_client_canvas", {"household_id": str(zed.id)}), "not found")
            turn = [{"tool_key": "read_client_canvas", "inputs": {}, "result": seen}]
            opened = await ex("bea").open_view({"view": "canvas", "household_id": str(kim.id)}, turn=turn)
            check("open_view opens the Kims' page beside the conversation", opened["view"], f"/canvas?household={kim.id}")
            check("and their retirement page", (await ex("ann").open_view({"view": "canvas_retirement", "household_id": str(kim.id)}, turn=turn))["view"],
                  f"/canvas/retirement?household={kim.id}")
            await refused("operations has no client portal", ex("ola").open_view({"view": "canvas"}), "not one this role opens")
            await refused("a branch leader does not record a questionnaire there", ex("bea").open_view({"view": "canvas_questionnaire"}),
                          "not one this role opens")

            print("\n=== Recording a questionnaire stops at a Confirm naming each answer ===")
            preview = await ex("pat").preview_change("record_questionnaire", {"household_id": str(lee.id), "answers": ANSWERS})
            rows = {r["label"]: r["value"] for r in preview["rows"]}
            check("it names the client", rows.get("Client"), "Lee")
            check("each answer as the form words it", [v for k, v in rows.items() if k.endswith("?")],
                  ["long (> 7 years)", "medium — I'd hold on", "stable", "5+ years", "somewhat important", "intermediate"])
            check("and that no mandate changes", "Nothing on a mandate" in rows.get("Changes", ""), True)
            await refused("a question left out is refused before the card", ex("pat").preview_change(
                "record_questionnaire", {"household_id": str(lee.id), "answers": ANSWERS[:2]}), "Missing")
            await refused("an answer the form does not offer is refused", ex("pat").preview_change(
                "record_questionnaire", {"household_id": str(lee.id), "answers": [*ANSWERS[:2], {"question": "income_stability", "answer": "huge"}, *ANSWERS[3:]]}),
                "not an answer")
            await refused("a subject from another household is refused", ex("pat").preview_change(
                "record_questionnaire", {"household_id": str(lee.id), "answers": ANSWERS, "subject_id": str(sam.id)}), "not in this household")
            await refused("operations does not record one", ex("ola").record_questionnaire({"household_id": str(lee.id), "answers": ANSWERS}),
                          "adviser or a paraplanner")
            done = await ex("pat").execute("record_questionnaire", {"household_id": str(lee.id), "answers": ANSWERS})
            row = (await s.execute(select(RiskAssessment).where(RiskAssessment.household_id == lee.id))).scalar_one()
            check("it is on record, by the paraplanner", (row.completed_by, row.completed_by_role, done["indicated"] == row.indicated),
                  ("pat@t.test", "paraplanner", True))

            print("\n=== A client's conversation names no other subject ===")
            await refused("a household thread", threads.create_thread({"kind": "household", "subject_id": str(kim.id)}, user=users["mia"], session=s),
                          "no subject")
            await refused("an Ask Astra thread about someone", threads.create_thread({"kind": "ask_astra", "subject_id": str(kim.id)},
                                                                                     user=users["mia"], session=s), "no subject")
            made = await threads.create_thread({"kind": "ask_astra", "title": "Am I on track?"}, user=users["mia"], session=s)
            t = await s.get(Thread, uuid.UUID(made["id"]))
            check("an Ask Astra thread is hers alone", (t.created_by_user_id, t.subject_id), (users["mia"].id, None))

            print("\n=== The Canvas routes ===")
            pdf = await canvas.download_summary_pdf(household_id=None, user=users["mia"], firm=firm, db=s)
            check("the summary PDF prints her real total", b"$400,000" in pdf_text(pdf.body), True)
            from app.aurea_core import client_view
            home = await client_view.home(s, lee.id, firm=firm)
            check("the PDF reads the same page (the total it printed was 0)", home["total_wealth"], 400000.0)
            got = await canvas.get_fees(household_id=None, user=users["mia"], firm=firm, db=s)
            check("the fee panel has no rate it was not given", (got["source"], got["fee_bps"]), ("not_on_file", None))
            await refused("staff do not take a next-gen member's steps", canvas.heir_step(
                canvas.StepIn(key="welcome", person_id=leo.id), user=users["ann"], firm=firm, db=s), "own journey")
            j = await canvas.heir_journey(person_id=sam.id, user=users["leo"], firm=firm, db=s)
            check("a client asking for another person's journey gets their own", j["person"]["id"], str(leo.id))
            await refused("a client tied to no person gets no journey", canvas.heir_journey(person_id=sam.id, user=users["lone"], firm=firm, db=s),
                          "No next-gen journey")
            await canvas.heir_step(canvas.StepIn(key="welcome"), user=users["leo"], firm=firm, db=s)
            journeys = (await s.execute(select(HeirJourney).where(HeirJourney.person_id == sam.id))).scalars().all()
            check("and his step never touched Sam's", journeys, [])
            await refused("another household's report reads as not there", canvas.canvas_report(kim_report.id, user=users["mia"], firm=firm, db=s),
                          "not available")
            check("staff read a published report", (await canvas.canvas_report(kim_report.id, user=users["bea"], firm=firm, db=s))["title"],
                  "Kim quarterly")
            body = canvas.QuestionnaireIn(answers={a["question"]: a["answer"] for a in ANSWERS}, household_id=lee.id)
            await refused("operations does not record a client's questionnaire", canvas.submit_questionnaire(body, user=users["ola"], firm=firm, db=s),
                          "Only the client")
    finally:
        await db.dispose()


def main() -> None:
    catalogue()
    asyncio.run(behaviour())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:\n  " + "\n  ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
