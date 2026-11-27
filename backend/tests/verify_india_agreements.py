"""Prove India's agreements, disclosures and the NRI checklist (B-391).

  1. The tools: reads change nothing, every record waits behind a Confirm card, no client holds any, each has a card and
     opens beside the conversation.
  2. The demonstration carries it, once, with invented fund houses and every string within its column.
  3. The fund-house acceptance list: nothing on file bars nothing; a recorded refusal is read by shelf.barred, so a direct
     order is refused (and the rebalancer reads the same); conditions and "not assessed" are named; sales are not stopped;
     a US indicium alone is not residence.
  4. NRI intake: repatriation intent on the residence record and the case, read against the account.
  5. Mutual funds: EUIN or the declaration per order, scheme documents, the commission disclosure.
  6. PMS checks, the AIF commitment with drawdowns as tasks, the broking and demat pack and the e-stamp record.
  7. A trust's recorded beneficial owners hold activation until they are screened parties.
  8. Outside India nothing is read or barred.

Runs against a throwaway SQLite database, with the demonstration firm built from invented prices.

    python -m tests.verify_india_agreements
"""
from __future__ import annotations

import asyncio
import re
import sys
import uuid
from datetime import timedelta
from pathlib import Path

from sqlalchemy import String, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.core.db import Base
from app.core.tools import TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, Goal, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.portfolio import Holding, Instrument, Price
from app.models.tenant import Firm
from app.models.trading import Order
from seed import india_demo

PASS, FAIL = [], []
R = UserRole
ROOT = Path(__file__).resolve().parents[2]
READS = ("read_fund_acceptance", "read_agreement_file")
WRITES = ("record_fund_acceptance", "record_order_euin", "record_scheme_document", "record_distribution_disclosure",
          "record_agreement_item", "record_aif_commitment", "record_aif_drawdown", "record_aif_payment", "record_estamp",
          "record_trust_owners")


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}", flush=True)


async def section(title: str, fn) -> None:
    """One part's checks. A section that raises (the feature is not there) is one failure, and the rest still run."""
    print(f"\n=== {title} ===", flush=True)
    try:
        await fn()
    except Exception as exc:  # noqa: BLE001 - reported as a failure with what raised
        FAIL.append(title)
        print(f"  FAIL  {title}: {type(exc).__name__}: {str(exc)[:400]}")


async def refused(label: str, coro, contains: str) -> None:
    try:
        await coro
    except Exception as e:  # noqa: BLE001 - the words are what is checked
        ok = contains.lower() in str(e).lower()
        check(label, True if ok else str(e), True)
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def labels(card: dict) -> dict:
    return {r["label"]: r["value"] for r in card["rows"]}


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()

            async def person_of(email) -> User:
                return (await s.execute(select(User).where(User.email == email))).scalar_one()

            users = {role: await person_of(f"{who}@astra-india.demo") for role, who in (
                (R.ADVISER, "adviser"), (R.COMPLIANCE, "compliance"), (R.OPERATIONS, "operations"), (R.ADMIN, "admin"))}

            def ex(role) -> ToolExecutors:
                return ToolExecutors(s, users[role].id, role, firm.id)

            async def one(model, **where):
                q = select(model).where(*[getattr(model, k) == v for k, v in where.items()])
                return (await s.execute(q)).scalar_one()

            async def tools():
                check("both reads change nothing", [TOOLS[k].change_state for k in READS], [ToolChangeState.NO] * 2)
                check("every record waits behind Confirm", [k for k in WRITES if TOOLS[k].change_state != ToolChangeState.PAUSE_FOR_CONFIRMATION
                                                            or not TOOLS[k].confirmation], [])
                check("no client holds any of them", [k for k in (*READS, *WRITES) if R.CLIENT in TOOLS[k].roles_required], [])
                check("the acceptance list is research's, compliance's and operations'",
                      TOOLS["record_fund_acceptance"].roles_required, {R.RESEARCH_CIO, R.COMPLIANCE, R.OPERATIONS})
                renderer = (ROOT / "frontend" / "app" / "workspace" / "components" / "artifacts" / "ArtifactRenderer.tsx").read_text(encoding="utf-8")
                panes = (ROOT / "frontend" / "lib" / "paneViews.ts").read_text(encoding="utf-8")
                check("each has a card and opens beside the conversation",
                      [k for k in (*READS, *WRITES) if f'case "{k}":' not in renderer or f'case "{k}":' not in panes], [])
                check("the registration tool takes the repatriation intent", all(
                    any(i.name == "repatriation_intent" for i in TOOLS[k].inputs) for k in ("record_residency", "record_registration_detail")), True)

            await section("The tools", tools)

            async def seeded():
                from seed import india_agreements as seed

                before = (await s.execute(select(func.count()).select_from(Instrument))).scalar_one()
                real = seed._aif

                async def refuses(*a, **k):
                    a[0].add(Goal(firm_id=firm.id, household_id=uuid.uuid4(), name="half written", target_amount=1))
                    raise ValueError("refused (test)")

                seed._aif, said = refuses, []
                try:
                    added = await seed.add(s, firm, said)
                finally:
                    seed._aif = real
                await s.commit()
                check("a refused part is reported and the rest is written", said, ["Karthik Iyer's fund commitment: skipped, refused (test)"])
                check("and leaves nothing half written",
                      (await s.execute(select(func.count()).select_from(Goal).where(Goal.name == "half written"))).scalar_one(), 0)
                said = []
                check("a later run writes the part that was refused", (await seed.add(s, firm, said) > 0, said), (True, []))
                await s.commit()
                check("the seed adds its records", added > 20, True)
                check("with two invented schemes", (await s.execute(select(func.count()).select_from(Instrument))).scalar_one() - before, 2)
                skipped: list[str] = []
                check("a second run adds nothing and skips nothing", (await seed.add(s, firm, skipped), skipped), (0, []))
                await s.commit()
                long = []
                for table in Base.metadata.sorted_tables:
                    for col in table.columns:
                        if isinstance(col.type, String) and col.type.length:
                            worst = (await s.execute(select(func.max(func.length(col))))).scalar_one()
                            if worst and worst > col.type.length:
                                long.append(f"{table.name}.{col.name} {worst}>{col.type.length}")
                check("every seeded string fits its column", long, [])
                from app.aurea_core import fund_acceptance

                lines = (await fund_acceptance.standing(s, firm.id)).values()
                check("every acceptance line is about an invented fund house and says so",
                      [r.fund_house for r in lines if "(invented)" not in r.fund_house or "invented" not in r.document], [])

            await section("The demonstration", seeded)

            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
            async def acceptance():
                from app.aurea_core import fund_acceptance, order_guard, shelf

                rohan = await one(Mandate, firm_id=firm.id, name="Rohan Pinto - Growth")
                account = await one(Account, mandate_id=rohan.id)
                insts = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
                north, lotus, ppfas = insts["NARMADA-FLEXI-DEMO"], insts["LOTUSGATE-FLEXI-DEMO"], insts["PPFAS-FLEXI-DIRECT"]
                barred = await shelf.barred(s, firm, rohan)
                check("the shelf bars the refusing house for a US resident", north.id in barred, True)
                check("  naming the house, the document and why", all(w in barred.get(north.id, "") for w in
                      ("not accepted from a resident of the United States", "Narmada", "invented", "Rohan Pinto is recorded as living")), True)
                check("conditions bar nothing (the regular plan is barred by the licence), and neither does nothing on file",
                      ("resident" in barred.get(lotus.id, ""), "regular plan" in barred.get(lotus.id, ""), ppfas.id in barred), (False, True, False))
                vikram = await one(Mandate, firm_id=firm.id, name="Vikram Menon - Balanced")
                check("a UAE resident is not reached", await fund_acceptance.bars(s, firm, vikram), {})
                refuse = ex(R.ADVISER).preview_change("execute_orders", {"mandate_id": str(rohan.id), "reason": "Client asked",
                                                                         "orders": [{"symbol": north.symbol, "side": "buy", "quantity": 10}]})
                await refused("a direct order for the refused scheme is refused, price or no price", refuse,
                              "not accepted from a resident of the United States")
                # A real price, as the NAV sync would write it, so the other cards can be read.
                s.add(Price(firm_id=firm.id, instrument_id=ppfas.id, as_of=__import__("datetime").date.today(), close=80, currency="INR",
                            source="amfi", is_real=True))
                await s.flush()
                card = await ex(R.ADVISER).preview_change("execute_orders", {"mandate_id": str(rohan.id), "reason": "Client asked",
                                                                             "orders": [{"symbol": ppfas.symbol, "side": "buy", "quantity": 10}]})
                notes = " ".join(r["value"] for r in card["rows"] if r["label"] == "Note")
                check("a buy with nothing on file goes ahead and says not assessed", "not assessed, and not stopped" in notes, True)
                order = [{"symbol": ppfas.symbol, "side": "buy", "quantity": 10, "instrument_id": str(ppfas.id), "est_value": 800.0, "est_price": 80.0}]
                await fund_acceptance.record(s, firm, R.OPERATIONS, by="ops", symbol=ppfas.symbol, country="USA", stance="conditions",
                                             conditions="Paper application only (test)", document="Test fixture notice")
                notes = await fund_acceptance.order_notes(s, firm, rohan, order, {ppfas.symbol: ppfas})
                check("conditions are named on the purchase", ["Paper application only (test)" in n for n in notes], [True])
                await fund_acceptance.record(s, firm, R.OPERATIONS, by="ops", symbol=ppfas.symbol, country="US", stance="refuses",
                                             document="Test fixture notice, later")
                found = await order_guard.review(s, firm=firm, mandate=rohan, account=account, orders=[dict(order[0])],
                                                 instruments={ppfas.symbol: ppfas}, unpriced=[])
                check("a priced buy the list refuses is refused once", sum("not accepted from a resident" in r for r in found["refused"]), 1)
                check("a sale is never stopped", await fund_acceptance.order_refusals(s, firm, rohan, [{**order[0], "side": "sell"}]), [])
                await fund_acceptance.record(s, firm, R.OPERATIONS, by="ops", symbol=ppfas.symbol, country="US", stance="accepts",
                                             document="Test fixture notice, latest")
                check("a later line stands over an earlier one", ppfas.id in await shelf.barred(s, firm, rohan), False)
                await refused("conditions need their words", fund_acceptance.prepare(s, firm, R.OPERATIONS, fund_house="Some House", country="US",
                              stance="conditions", document="A notice"), "conditions as the document")
                await refused("an adviser does not record the list", fund_acceptance.prepare(s, firm, R.ADVISER, fund_house="Some House",
                              country="US", stance="accepts", document="A notice"), "research, compliance or operations")
                await refused("only the US and Canada", fund_acceptance.prepare(s, firm, R.OPERATIONS, fund_house="Some House", country="UK",
                              stance="accepts", document="A notice"), "US or Canada")
                check("a US address alone is not residence", fund_acceptance.exposure({"country_of_residence": "United Arab Emirates",
                      "us_indicia": ["us_address"]}), {})
                check("a declared US person is reached", list(fund_acceptance.exposure({"us_person": True, "country_of_residence": "India"})), ["US"])
                view = await ex(R.COMPLIANCE).read_fund_acceptance({})
                pinto = next(c for c in view["clients"] if c["mandate"] == "Rohan Pinto - Growth")
                check("the list names who it reaches and what is barred", (pinto["reached"][0]["name"], pinto["barred"] >= 1, pinto["not_assessed"] > 0),
                      ("Rohan Pinto", True, True))
                check("and says so in a sentence", "Does not accept" in view["message"], True)
                card = labels(await ex(R.COMPLIANCE).preview_change("record_fund_acceptance", {
                    "symbol": lotus.symbol, "country": "US", "stance": "refuses", "document": "A later notice (test)"}))
                check("the Confirm card names who it reaches and the effect", ("Rohan Pinto - Growth" in card["Clients reached"], card["Effect"]),
                      (True, "Purchases for these residents are refused"))

            await section("The fund-house acceptance list", acceptance)

            async def nri():
                from app.aurea_core import document_matrix, residency

                rohan = people.get("Rohan Pinto") or await one(Person, firm_id=firm.id, full_name="Rohan Pinto")
                view = await residency.person_view(s, firm, rohan)
                check("an intent to repatriate against an NRO account is named", view["repatriation"]["state"], "breach")
                check("nothing recorded reads not on file", residency.repatriation({"accounts": ["NRO"]})["state"], "not_on_file")
                check("with an NRE account it reads on file", residency.repatriation({"repatriation_intent": "repatriable", "accounts": ["NRE"]})["state"], "on_file")
                await refused("the intent is one of three", residency.prepare(s, firm, person_id=rohan.id, document="A form",
                              repatriation_intent="maybe"), "repatriation intent is one of")
                neha = await one(OnboardingCase, firm_id=firm.id, prospect_name="Neha Pinto")
                got = await document_matrix.check_case(s, neha, firm)
                mine = [i for i in got["issues"] if i["field"] == "repatriation_intent"]
                check("an NRO case for a client who means to repatriate is held", [i["severity"] for i in mine], ["critical"])
                said = neha.intake
                neha.intake = {**said, "registration": {**said["registration"], "repatriation_intent": None}}
                got = await document_matrix.check_case(s, neha, firm)
                check("not recorded is named and holds nothing", [i["severity"] for i in got["issues"] if i["field"] == "repatriation_intent"], ["medium"])
                neha.intake = said

            await section("NRI intake", nri)

            async def funds():
                from app.aurea_core import india_agreements as ag

                home = await one(Household, firm_id=firm.id, name="The Pinto NRI Household")
                view = await ex(R.ADVISER).read_agreement_file({"household_id": str(home.id)})
                euins = sorted(o["euin_state"] for o in view["funds"]["orders"])
                check("one order carries an EUIN and one neither", euins, ["missing", "recorded"])
                check("a regular plan makes the commission disclosure asked, and none is on file",
                      (view["funds"]["commission"]["asked"], view["funds"]["commission"]["state"]), (True, "not_on_file"))
                check("the NRI checklist carries the mismatch and the refusals",
                      (next(c for c in view["nri"][0]["checks"] if c["key"] == "repatriation")["state"], view["nri"][0]["refused"] >= 1), ("breach", True))
                check("the sentence names the missing EUIN", "1 mutual fund order(s) without an EUIN" in view["message"], True)
                missing = next(o for o in view["funds"]["orders"] if o["euin_state"] == "missing")
                await refused("an EUIN is E and six digits", ex(R.OPERATIONS).preview_change("record_order_euin", {
                    "order_id": missing["order_id"], "euin": "123", "document": "Confirmation"}), "E and six digits")
                await refused("not both a code and a declaration", ex(R.OPERATIONS).preview_change("record_order_euin", {
                    "order_id": missing["order_id"], "euin": "E123456", "execution_only": True, "document": "Confirmation"}), "not both")
                direct = (await s.execute(select(Order).join(Instrument, Instrument.id == Order.instrument_id).where(
                    Order.firm_id == firm.id, Instrument.symbol == "PPFAS-FLEXI-DIRECT"))).scalars().first()
                await refused("a direct plan carries neither", ex(R.OPERATIONS).preview_change("record_order_euin", {
                    "order_id": str(direct.id), "euin": "E123456", "document": "Confirmation"}), "direct plan")
                inputs = {"order_id": missing["order_id"], "execution_only": True, "document": "Signed declaration (test)"}
                card = labels(await ex(R.OPERATIONS).preview_change("record_order_euin", inputs))
                check("the card shows the declaration", card["Declaration"], "Execution-only, declared by the investor")
                done = await ex(R.OPERATIONS).record_order_euin(inputs)
                check("confirmed, nothing is missing", done["funds"]["missing_euin"], 0)
                done = await ex(R.ADVISER).record_distribution_disclosure({
                    "household_id": str(home.id), "kind": "commission", "symbol": "LOTUSGATE-FLEXI-DEMO", "terms": "Trail 0.8% a year (invented)",
                    "method": "email", "document": "Email sent (test)"})
                check("the commission disclosure reads on file with its terms", (done["funds"]["commission"]["state"],
                      done["funds"]["commission"]["given"][-1]["terms"]), ("on_file", "Trail 0.8% a year (invented)"))
                await refused("a commission needs its terms", ex(R.ADVISER).preview_change("record_distribution_disclosure", {
                    "household_id": str(home.id), "kind": "commission", "symbol": "LOTUSGATE-FLEXI-DEMO", "method": "email",
                    "document": "Email sent"}), "commission as the disclosure states")
                iyer = await one(Household, firm_id=firm.id, name="The Iyer Family")
                view = await ag.household_view(s, firm, iyer.id)
                ppfas = next(x for x in view["funds"]["schemes"] if x["symbol"] == "PPFAS-FLEXI-DIRECT")
                check("a scheme shows what the firm holds and what the client was given",
                      (all(ppfas["on_file"].values()), ppfas["given"]), (True, ["kim"]))
                await refused("a record is not a client's", ex(R.ADVISER).preview_change("record_scheme_document", {
                    "symbol": "RELIANCE.NS", "kind": "sid", "dated_on": "2026-01-01", "document": "A document"}), "not a mutual fund")

            await section("Mutual fund distribution", funds)

            async def pms_aif():
                from app.aurea_core import firm_time, india_agreements as ag, law_figures

                today = firm_time.today(firm)
                iyer = await one(Household, firm_id=firm.id, name="The Iyer Family")
                pintos = await one(Household, firm_id=firm.id, name="The Pinto NRI Household")
                view = await ag.household_view(s, firm, iyer.id)
                mine = next(m for m in view["pms"] if m["mandate"].startswith("Karthik Iyer"))
                check("Karthik's PMS papers all pass", [i["state"] for i in mine["items"]], ["on_file"] * 4)
                theirs = (await ag.household_view(s, firm, pintos.id))["pms"][0]
                states = {i["item"]: i["state"] for i in theirs["items"]}
                check("a power of attorney wider than operate and debit fails",
                      (states["poa_scope"], states["upfront_fee"], states["disclosure_document"]), ("breach", "on_file", "not_on_file"))
                mid = mine["mandate_id"]
                card = labels(await ex(R.OPERATIONS).preview_change("record_agreement_item", {
                    "relationship": "pms", "mandate_id": mid, "item": "disclosure_document", "answer": "given",
                    "dated_on": today.isoformat(), "document": "A later file (test)"}))
                check("a disclosure document given after signing fails, and the card says so", "after the agreement was signed" in card["Reads as"], True)
                card = labels(await ex(R.OPERATIONS).preview_change("record_agreement_item", {
                    "relationship": "pms", "mandate_id": mid, "item": "poa_scope", "powers": "operate_demat, Sell without instruction",
                    "answer": "limited", "document": "A power of attorney (test)"}))
                check("the scope is read from the powers, never taken as said", card["Answer"], "wider")
                await refused("an upfront fee answer is none or charged", ex(R.OPERATIONS).preview_change("record_agreement_item", {
                    "relationship": "pms", "mandate_id": mid, "item": "upfront_fee", "answer": "yes", "document": "Fee annexure"}), "none, charged")
                sharma = await one(Mandate, firm_id=firm.id, name="Rajesh Sharma - Balanced")
                await refused("PMS papers are for a PMS mandate", ex(R.OPERATIONS).preview_change("record_agreement_item", {
                    "relationship": "pms", "mandate_id": str(sharma.id), "item": "upfront_fee", "answer": "none", "document": "Fee annexure"}),
                    "not on a Portfolio Manager")

                fund = view["aif"][0]
                check("the commitment with drawdowns paid and unpaid", ([d["state"] for d in fund["drawdowns"]], fund["unpaid"], fund["below_minimum"]),
                      (["paid", "unpaid"], 2_250_000.0, False))
                check("the obligation not yet discussed reads not on file",
                      next(i["state"] for i in fund["items"] if i["item"] == "drawdown_discussed"), "not_on_file")
                unpaid = fund["drawdowns"][1]
                task = await s.get(Task, uuid.UUID(unpaid["task_id"]))
                check("the unpaid drawdown is an open task for the adviser, due on its date",
                      (str(task.status), task.due_date.isoformat(), task.assigned_to is not None, task.source),
                      ("open", unpaid["due_on"], True, "aif_drawdown"))
                paid_task = await s.get(Task, uuid.UUID(fund["drawdowns"][0]["task_id"]))
                check("a paid one's task is closed", str(paid_task.status), "done")
                cid = fund["commitment_id"]
                await refused("a call beyond the commitment is refused", ex(R.OPERATIONS).preview_change("record_aif_drawdown", {
                    "commitment_id": cid, "amount": 12_000_000, "notice_on": today.isoformat(), "due_on": (today + timedelta(days=10)).isoformat(),
                    "document": "Notice"}), "against a commitment")
                await refused("more than is unpaid is refused", ex(R.OPERATIONS).preview_change("record_aif_payment", {
                    "drawdown_id": unpaid["drawdown_id"], "amount": 3_000_000, "paid_on": today.isoformat(), "document": "Advice"}), "is unpaid")
                part = await ex(R.OPERATIONS).record_aif_payment({"drawdown_id": unpaid["drawdown_id"], "amount": 1_000_000,
                                                                  "paid_on": today.isoformat(), "document": "Advice (test)"})
                await s.refresh(task)
                check("a part payment leaves it unpaid and the task open", (part["aif"][0]["unpaid"], str(task.status)), (1_250_000.0, "open"))
                card = labels(await ex(R.OPERATIONS).preview_change("record_aif_payment", {
                    "drawdown_id": unpaid["drawdown_id"], "paid_on": today.isoformat(), "document": "Advice (test)"}))
                check("the card says the task closes", card["Task"], "Closes")
                done = await ex(R.OPERATIONS).record_aif_payment({"drawdown_id": unpaid["drawdown_id"], "paid_on": today.isoformat(),
                                                                  "document": "Advice (test)"})
                await s.refresh(task)
                check("paid in full, nothing unpaid and the task is closed", (done["aif"][0]["unpaid"], str(task.status)), (0.0, "done"))
                over = await ex(R.ADVISER).record_aif_drawdown({"commitment_id": cid, "amount": 100_000,
                                                                "notice_on": (today - timedelta(days=30)).isoformat(),
                                                                "due_on": (today - timedelta(days=1)).isoformat(), "document": "Notice (test)"})
                check("past its date it reads overdue", (over["aif"][0]["overdue"], "overdue" in over["message"] or "task" in over["message"]), (1, True))
                card = labels(await ex(R.ADVISER).preview_change("record_aif_commitment", {
                    "person_id": str(people["Karthik Iyer"].id), "fund_name": "Small Fund (invented)", "category": "Category III",
                    "commitment": 5_000_000, "committed_on": today.isoformat(), "document": "Agreement (test)"}))
                check("a commitment below the minimum is shown", "Below the" in card.get("Minimum", ""), True)

                demat = next(a for a in view["broking"] if a["account"] == "Karthik Iyer demat")
                gaps = [i["item"] for i in demat["items"] if i["state"] != "on_file"]
                check("the demat pack names what is not on file", gaps, ["running_account_authorisation", "margin_terms"])
                check("optional papers are not counted as missing", view["counts"]["broking_missing"], 0)
                other = await ag.household_view(s, firm, (await one(Household, firm_id=firm.id, name="The Sharma Family")).id)
                check("a pack with gaps counts them", other["counts"]["broking_missing"] > 0, True)
                check("the e-stamp is on file as stated", [(e["certificate_number"], e["duty"]) for e in view["estamps"]], [("IN-DEMO-KA-000391", 500.0)])
                await refused("a certificate is recorded once", ex(R.OPERATIONS).preview_change("record_estamp", {
                    "household_id": str(iyer.id), "instrument_kind": "pms_agreement", "certificate_number": "in-demo-ka-000391",
                    "state": "Karnataka", "duty": 500, "stamped_on": today.isoformat(), "parties": "The parties", "document": "Certificate"}),
                    "already on file")
                book = await ex(R.COMPLIANCE).read_agreement_file({})
                check("the firm view totals the gaps", (book["totals"]["pms_breaches"] >= 1, book["totals"]["missing_euin"]), (True, 0))
                keys = [k for k in ag.LAW]
                check("every rule is a law figure marked assumed, but the thresholds taken from circulars",
                      [k for k in keys if k not in ("in_aif_min_ticket", "in_kyc_bo_trust") and not law_figures.get(k).source.startswith("ASSUMED")], [])
                from app.aurea_core import counsel_review
                check("counsel's tracker lists them, not reviewed", "in_pms_poa_scope" in counsel_review.india_keys(), True)

            await section("PMS, AIF, broking and e-stamp", pms_aif)

            async def trust():
                from app.api.onboarding import _gates_for
                from app.aurea_core import document_matrix, parties as parties_core, trust_owners

                case = await one(OnboardingCase, firm_id=firm.id, prospect_name="The Pinto Family Trust")
                got = await document_matrix.check_case(s, case, firm)
                named = sorted(i["description"].split(" (")[0] for i in got["issues"] if i["field"] == "beneficial_owners")
                check("each recorded owner who is not a party keeps the case out of good order",
                      (named, got["in_good_order"]), (["Anil Rao", "Joseph Pinto", "Maria Pinto"], False))
                gate = next(g for g in (await _gates_for(s, firm, case))["gates"] if g["key"] == "registration_in_good_order")
                check("the activation gate fails naming them", (gate["state"], "Joseph Pinto" in gate["detail"]), ("fail", True))
                await refused("an administrator does not record owners", ex(R.ADMIN).preview_change("record_trust_owners", {"case_id": str(case.id)}),
                              "records a trust")
                card = labels(await ex(R.ADVISER).preview_change("record_trust_owners", {"case_id": str(case.id)}))
                check("the card lists each party and role", ("Protector" in " ".join(v for k, v in card.items() if k.startswith("Party")),
                      "Settlor" in " ".join(v for k, v in card.items() if k.startswith("Party"))), (True, True))
                done = await ex(R.ADVISER).record_trust_owners({"case_id": str(case.id)})
                check("three are added, each screened", (len(done["added"]), [a for a in done["added"] if a["screening"] == "not_screened"], done["missing"]),
                      (3, [], []))
                got = await document_matrix.check_case(s, case, firm)
                check("the ownership issues are gone", [i for i in got["issues"] if i["field"] == "beneficial_owners"], [])
                rows = list((await s.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all())
                state = parties_core.completeness_for("trust", rows, {}, market="IN")
                check("a protector is not held to a share, and the beneficiary's share is read", state["ownership_issues"], [])
                from app.agents.cip_identity_verifier import CIP_ROLES
                check("a protector's identity is verified", "protector" in {str(r) for r in CIP_ROLES}, True)
                await refused("nothing left to add", trust_owners.prepare(s, firm, R.ADVISER, case_id=case.id), "already a party")

            await section("A trust's beneficial owners", trust)

            async def elsewhere():
                from app.aurea_core import document_matrix, fund_acceptance, india_agreements as ag, shelf, trust_owners

                us = Firm(id=uuid.uuid4(), name="US", slug="us-agree", base_currency="USD", jurisdiction="US", settings={})
                s.add(us)
                await s.flush()
                person = Person(firm_id=us.id, full_name="Pat Lee", segment="private_wealth", profile={}, kyc={})
                s.add(person)
                await s.flush()
                mandate = Mandate(firm_id=us.id, person_id=person.id, name="Pat", mandate_type="discretionary", suitability={}, constraints={})
                s.add(mandate)
                await s.flush()
                check("nothing is barred", await shelf.barred(s, us, mandate), {})
                check("the reads say it does not apply", ((await fund_acceptance.overview(s, us))["applies"], (await ag.overview(s, us))["applies"]),
                      (False, False))
                await refused("nothing is recorded", ag.prepare_estamp(s, us, R.OPERATIONS), "Indian firm")
                case = OnboardingCase(firm_id=us.id, prospect_name="A Trust", registration_type="trust", intake={})
                s.add(case)
                await s.flush()
                check("a US trust case is not read", (await trust_owners.matrix_issues(s, us, case, [])), [])
                got = await document_matrix.check_case(s, case, us)
                check("and gets no repatriation or owner issue", [i for i in got["issues"] if i["field"] in ("repatriation_intent", "beneficial_owners")], [])

            await section("Outside India", elsewhere)
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
