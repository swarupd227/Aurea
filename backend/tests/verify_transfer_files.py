"""Prove a transfer's file says where it stands and whose move it is, keeps rejects with their codes, shows a partial
transfer and a debit balance, starts the cost-basis clock when the assets arrive, and moves nothing; and that the
rollover analysis works only from recorded facts, says "not assessed" without them, and recommends nothing.

Runs against a throwaway SQLite database.

    python -m tests.verify_transfer_files
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents.rollover_pte_documenter import RolloverPTEDocumenterAgent
from app.aurea_core import firm_time, markets, transfer_file as tf
from app.core.db import Base
from app.core.tools import TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.enums import UserRole
from app.models.portfolio import Holding
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingParty, TransferRequest
from app.models.tenant import Firm
from app.models.transfer_file import TransferFileEntry

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, tf.TransferFileError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def pure() -> None:
    print("\n=== The rollover analysis ===")
    today = date(2026, 11, 2)
    blank = tf.analyse({}, owner_dob=None, today=today)
    check("with nothing on file, every point that needs a fact is not assessed",
          [p["key"] for p in blank["points"] if p["status"] == "not_assessed"], ["cost", "nua", "loan", "rmd_first", "age_55"])
    check("and direct against indirect is still laid out", next(p["status"] for p in blank["points"] if p["key"] == "direct_or_indirect"), "laid_out")
    check("the adviser's reason is theirs to give", "adviser_reason" in blank["missing"], True)
    facts = {"balance": 400000, "plan_cost_bps": 35, "ira_cost_bps": 95, "employer_stock": True, "employer_stock_value": 120000,
             "employer_stock_basis": 20000, "loan_balance": 15000, "separation_year": 2025, "adviser_reason": "Consolidates four plans she cannot manage alone"}
    r = tf.analyse(facts, owner_dob=date(1968, 5, 1), today=today)
    p = {x["key"]: x for x in r["points"]}
    check("the IRA costs 60 bps more, 2,400 a year", (p["cost"]["status"], p["cost"]["figures"]["difference_a_year"]), ("higher_in_ira", 2400.0))
    check("an indirect rollover withholds 80,000", p["direct_or_indirect"]["figures"]["withheld_if_indirect"], 80000.0)
    check("employer stock carries 100,000 of NUA", (p["nua"]["status"], p["nua"]["figures"]["nua"]), ("applies", 100000.0))
    check("the plan loan applies", p["loan"]["status"], "applies")
    check("born 1968, the RMD starts at 75 and is not yet due", (p["rmd_first"]["figures"]["rmd_age"], p["rmd_first"]["status"]), (75, "not_yet"))
    check("she left at 57, so the age-55 exception applies", p["age_55"]["status"], "applies")
    check("with every fact and the reason on file it is complete", (r["complete"], r["missing"]), (True, []))
    old = tf.analyse({**facts, "separation_year": 2019}, owner_dob=date(1952, 1, 1), today=today)
    check("born 1952, this year's RMD comes out first", next(x["status"] for x in old["points"] if x["key"] == "rmd_first"), "applies")


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
            for key, role in (("adv", UserRole.ADVISER), ("ops", UserRole.OPERATIONS)):
                u = User(id=uuid.uuid4(), firm_id=firm.id, email=f"{key}@t", full_name=key, role=role, hashed_password="x")
                s.add(u)
                users[key] = u
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}
            today = firm_time.today(firm)
            c = OnboardingCase(id=uuid.uuid4(), firm_id=firm.id, prospect_name="Maria Lopez", registration_type="employer_rollover", intake={})
            s.add(c)
            await s.flush()
            s.add(OnboardingParty(firm_id=firm.id, case_id=c.id, role="owner", legal_name="Maria Lopez", dob="1968-05-01"))
            t = TransferRequest(id=uuid.uuid4(), firm_id=firm.id, case_id=c.id, transfer_type="acat", direction="in", delivering_firm="Brokerage One",
                                amount=250000, status="initiated")
            s.add(t)
            await s.flush()
            tid = str(t.id)
            ev = lambda kind, days_ago, **kw: {"transfer_id": tid, "kind": kind, "on": (today - timedelta(days=days_ago)).isoformat(),  # noqa: E731
                                               "evidence": "Brokerage One notice", **kw}

            print("\n=== The file ===")
            out = await ex["ops"].read_transfer_files({"case_id": str(c.id)})
            check("a new transfer is requested, operations' move", (out["transfers"][0]["stage"], out["transfers"][0]["owner"]), ("requested", "Operations"))
            await refused("a reject carries its reason code", ex["ops"].preview_change("record_transfer_event", ev("rejected", 30)), "reason")
            await refused("cost basis follows the assets", ex["ops"].preview_change("record_transfer_event", ev("cost_basis", 1)), "received first")
            await refused("nothing is dated ahead", ex["ops"].preview_change("record_transfer_event", ev("submitted", -2)), "ahead")
            check("an adviser does not record on the file", UserRole.ADVISER in TOOLS["record_transfer_event"].roles_required, False)
            await ex["ops"].record_transfer_event(ev("submitted", 40))
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("record_transfer_event", ev("rejected", 35, code="title_mismatch")))["rows"]}
            check("the card names the reject", (card["What happened"], card["Reason"], card["Stands now"]), ("Rejected", "Account title does not match", "submitted"))
            out = await ex["ops"].record_transfer_event(ev("rejected", 35, code="title_mismatch"))
            row = out["transfers"][0]
            check("rejected, back with operations, counted by code", (row["stage"], row["owner"], out["rejects_by_code"][0]["code"]), ("rejected", "Operations", "title_mismatch"))
            await ex["ops"].record_transfer_event(ev("resubmitted", 33))
            out = await ex["ops"].record_transfer_event(ev("asset_memo", 30, lines="AAPL 100 in_kind; Brokerage One Money Fund 5000 stays; XYZ Annuity 1 exchange_1035"))
            row = out["transfers"][0]
            check("the memo makes it partial, one line stays", (row["stage"], row["partial"], [m["description"] for m in row["staying"]]),
                  ("in_transit", True, ["Brokerage One Money Fund"]))
            await refused("a memo line says how it moves", ex["ops"].preview_change("record_transfer_event", ev("asset_memo", 30, lines="AAPL 100")), "how each line")
            out = await ex["ops"].record_transfer_event(ev("debit_balance", 29, amount=1200))
            check("a debit balance is the client's to clear", (out["transfers"][0]["stage"], out["transfers"][0]["owner"]), ("debit_open", "Client"))
            await ex["ops"].record_transfer_event(ev("debit_cleared", 27))
            out = await ex["ops"].record_transfer_event(ev("received", 20))
            row = out["transfers"][0]
            check("received after 13 days from resubmission", row["days_to_receive"], 13)
            check("cost basis was due 15 days after receipt and is 5 days late", (row["cost_basis_due"], row["late"][0]["days_late"]),
                  ((today - timedelta(days=5)).isoformat(), 5))
            await ex["ops"].record_transfer_event(ev("residual", 3, amount=42.17))
            out = await ex["ops"].record_transfer_event(ev("cost_basis", 1))
            row = out["transfers"][0]
            check("with cost basis in it is complete and nothing is late", (row["stage"], row["late"], row["residuals"][0]["amount"]), ("complete", [], 42.17))
            check("the reject reason is kept on the transfer", t.reject_reason, "Account title does not match")
            check("the file moved no holdings", (await s.execute(select(Holding))).scalars().all(), [])
            check("every entry stays on record", len((await s.execute(select(TransferFileEntry))).scalars().all()), 9)

            print("\n=== Rollover facts ===")
            check("operations does not record an adviser's reason", UserRole.OPERATIONS in TOOLS["record_rollover_facts"].roles_required, False)
            await refused("facts need their source", ex["adv"].preview_change("record_rollover_facts", {"case_id": str(c.id), "plan_cost_bps": 35, "source": ""}), "say where")
            agent = RolloverPTEDocumenterAgent()
            from app.atlas.base import AgentContext, Subject
            ctx = AgentContext(session=s, firm=firm, subject=Subject("onboarding_case", c.id, c.prospect_name), run=None, tier=None, guardrails={}, config={})
            check("with no facts the agent drafts nothing", await agent.think(ctx, await agent.sense(ctx)), [])
            ask = {"case_id": str(c.id), "plan_name": "Acme 401(k)", "balance": "400,000", "plan_cost_bps": 35, "ira_cost_bps": 95,
                   "employer_stock": "no", "loan_balance": 0, "separation_year": 2025, "source": "Acme 401(k) statement Q3 2026 and fee disclosure"}
            card = {r["label"]: r["value"] for r in (await ex["adv"].preview_change("record_rollover_facts", ask))["rows"]}
            check("the card shows the facts and what is still missing", (card["Balance"], card["Holds employer stock"], card["Still not assessed"]),
                  (markets.money(400000, "USD"), "No", "adviser_reason"))
            out = await ex["adv"].record_rollover_facts(ask)
            check("recorded; the reason is still the adviser's to give", out["rollover"]["missing"], ["adviser_reason"])
            check("and the agent still drafts nothing", await agent.think(ctx, await agent.sense(ctx)), [])
            out = await ex["adv"].record_rollover_facts({"case_id": str(c.id), "adviser_reason": "She wants one adviser for four plans", "source": "Adviser's file note 2 Nov"})
            drafts = await agent.think(ctx, await agent.sense(ctx))
            check("with the reason on file the agent documents it, quoting the adviser",
                  (len(drafts), drafts[0].payload["adviser_reason"] if drafts else None), (1, "She wants one adviser for four plans"))
            check("its figures are the recorded ones", (drafts[0].payload["plan_expense_ratio_bps"], drafts[0].payload["advisory_fee_bps"]), (35.0, 95.0))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
