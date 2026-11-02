"""Prove the monitoring scenarios find what they should and nothing they should not, that a scenario with no baseline is
not run and says so, that an alert is decided once with a reason and escalation opens or joins an investigation, that
the report clock is the market's, that an exit goes through a forum, and that nothing reaches a client: no client role
holds a tool, and a message that would tip a client off is refused.

Runs against a throwaway SQLite database.

    python -m tests.verify_transaction_monitoring
"""
from __future__ import annotations

import asyncio
import sys
import types
import uuid
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import firm_time, txn_monitoring as tm
from app.core.db import Base
from app.core.tools import OPEN_VIEWS, TOOLS
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.cash_movement import CashRequest
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import OnboardingCase
from app.models.portfolio import Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []
TODAY = date(2026, 11, 3)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


async def refused(label: str, coro, *contains: str) -> None:
    try:
        await coro
    except (ExecutorError, tm.MonitoringError) as e:
        ok = all(c.lower() in str(e).lower() for c in contains)
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:110]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


def mv(i, days_ago, amount, direction, **kw):
    return {"id": f"m{i}", "on": TODAY - timedelta(days=days_ago), "amount": amount, "direction": direction, **kw}


def pure() -> None:
    print("\n=== The scenarios ===")
    run = lambda moves, base=None: tm.scenarios(moves, base, threshold=10000, today=TODAY)  # noqa: E731
    alerts, _ = run([mv(1, 10, 9500, "in"), mv(2, 8, 9200, "in"), mv(3, 6, 9800, "in")])
    check("three deposits just under 10,000 in four days", [(a["scenario"], a["figures"]["count"]) for a in alerts], [("under_threshold", 3)])
    alerts, _ = run([mv(1, 30, 9500, "in"), mv(2, 10, 9200, "in")])
    check("two weeks apart they are not", alerts, [])
    alerts, _ = run([mv(1, 20, 50000, "in"), mv(2, 12, 45000, "out")])
    check("50,000 in and 45,000 out eight days later", [(a["scenario"], a["figures"]["days"]) for a in alerts], [("in_and_out", 8)])
    alerts, _ = run([mv(1, 60, 50000, "in"), mv(2, 20, 45000, "out")])
    check("forty days apart it is not", alerts, [])
    _, skipped = run([mv(1, 5, 100, "out")])
    check("with no baseline the baseline scenarios are not run, and say so", [s.split(":")[0] for s in skipped], ["above_baseline", "undeclared_third_party"])
    outs = [mv(i, i * 7, 2000, "out") for i in range(1, 9)]
    alerts, _ = run(outs, {"wires_per_year": 6, "third_party_payments": False})
    check("eight payments in 90 days against six a year declared", [(a["scenario"], a["figures"]["payments"]) for a in alerts], [("above_baseline", 8)])
    alerts, _ = run([mv(1, 5, 3000, "out", third_party=True, payee="J Smith")], {"wires_per_year": 12, "third_party_payments": False})
    check("a third-party payment when none was declared", [a["scenario"] for a in alerts], ["undeclared_third_party"])
    alerts, _ = run([mv(1, 5, 3000, "out", third_party=True, payee="J Smith")], {"wires_per_year": 12, "third_party_payments": True})
    check("declared, it raises nothing", alerts, [])

    print("\n=== The clock is the market's ===")
    inv = types.SimpleNamespace(detected_on=date(2026, 10, 1), subject_known=True, suspicion_on=None, reported_on=None)
    check("US: 30 days from detection", tm.clock(inv, "US", TODAY)["due"], "2026-10-31")
    inv.subject_known = False
    check("US with no subject: 60", tm.clock(inv, "US", TODAY)["due"], "2026-11-30")
    check("NZ waits for suspicion to be formed", tm.clock(inv, "NZ", TODAY)["due"], None)
    inv.suspicion_on = date(2026, 10, 30)                       # a Friday
    check("NZ: three working days after a Friday is Wednesday", tm.clock(inv, "NZ", TODAY)["due"], "2026-11-04")
    check("India: seven working days", tm.clock(inv, "IN", TODAY)["due"], "2026-11-10")
    inv.reported_on = date(2026, 10, 20)
    c = tm.clock(inv, "US", TODAY)
    check("after a US report, review at 90 and file again by 120", (c["review_due"], c["again_due"]), ("2027-01-18", "2027-02-17"))
    check("a market with no regime gets no clock", tm.clock(inv, "SG", TODAY)["regime"], None)


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
            for key, role in (("comp", UserRole.COMPLIANCE), ("ops", UserRole.OPERATIONS), ("adv", UserRole.ADVISER)):
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
            a = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m.id, name="Rae brokerage", currency="USD", cash_balance=0, registration_type="individual")
            s.add(a)
            s.add(OnboardingCase(firm_id=firm.id, prospect_name="Rae Vance", registration_type="individual", materialized={"household_id": str(h.id)},
                                 intake={"cdd": {"wires_per_year": 4, "third_party_payments": False}}))
            await s.flush()
            today = firm_time.today(firm)
            s.add_all([Transaction(firm_id=firm.id, account_id=a.id, txn_type="deposit", amount=60000, trade_date=today - timedelta(days=12)),
                       CashRequest(firm_id=firm.id, account_id=a.id, kind="withdrawal", amount=55000, payee="Offshore Holdings Ltd", third_party=True,
                                   reason="client instruction", status="paid", requested_by="adv@t", paid_on=today - timedelta(days=5))])
            await s.flush()
            ex = {k: ToolExecutors(s, u.id, u.role, firm.id) for k, u in users.items()}

            print("\n=== The queue ===")
            out = await ex["comp"].read_transaction_monitoring({})
            check("money in and out, and an undeclared third party, are raised", sorted(x["scenario"] for x in out["alerts"]), ["in_and_out", "undeclared_third_party"])
            check("an adviser cannot read it, nor a client", (UserRole.ADVISER in TOOLS["read_transaction_monitoring"].roles_required,
                                                               any(UserRole.CLIENT in TOOLS[k].roles_required for k in ("read_transaction_monitoring", "decide_monitoring_alert", "record_investigation_step"))), (False, False))
            check("and the view is compliance's and operations'", OPEN_VIEWS["monitoring"][2], {UserRole.COMPLIANCE, UserRole.OPERATIONS})
            key = next(x["key"] for x in out["alerts"] if x["scenario"] == "in_and_out")
            third = next(x["key"] for x in out["alerts"] if x["scenario"] == "undeclared_third_party")
            await refused("a decision needs its reason", ex["ops"].preview_change("decide_monitoring_alert", {"alert_key": key, "decision": "closed", "reason": "fine"}), "reason")
            card = {r["label"]: r["value"] for r in (await ex["ops"].preview_change("decide_monitoring_alert",
                    {"alert_key": key, "decision": "escalated", "reason": "Proceeds left within a week to an offshore company nobody declared"}))["rows"]}
            check("the card shows the alert and that a clock starts", (card["Alert"], card["Effect"]), ("Money in and straight back out", "Opens an investigation; the report clock starts"))
            out = await ex["ops"].decide_monitoring_alert({"alert_key": key, "decision": "escalated", "reason": "Proceeds left within a week to an offshore company nobody declared"})
            check("an investigation is open with its US clock", (len(out["investigations"]), out["investigations"][0]["clock"]["regime"]["report"]), (1, "SAR"))
            await refused("an alert is decided once", ex["comp"].preview_change("decide_monitoring_alert", {"alert_key": key, "decision": "closed", "reason": "Second look says it is fine"}), "already decided")
            card = {r["label"]: r["value"] for r in (await ex["comp"].preview_change("decide_monitoring_alert",
                    {"alert_key": third, "decision": "escalated", "reason": "Same payee, same week, part of the same pattern"}))["rows"]}
            check("a second escalation joins the open investigation", card["Effect"].startswith("Joins the open investigation"), True)
            out = await ex["comp"].decide_monitoring_alert({"alert_key": third, "decision": "escalated", "reason": "Same payee, same week, part of the same pattern"})
            check("still one investigation, with both alerts on it", (len(out["investigations"]), [st["step"] for st in out["investigations"][0]["steps"]]),
                  (1, ["alert_added", "alert_added"]))
            inv_id = out["investigations"][0]["id"]

            print("\n=== Tipping off ===")
            await refused("a message that speaks of the investigation is refused", ex["adv"].preview_change("send_client_message",
                          {"household_id": str(h.id), "body": "Our monitoring team flagged your recent wire and is investigating it."}), "compliance first")
            card = await ex["adv"].preview_change("send_client_message", {"household_id": str(h.id), "body": "Could you send the invoice for the October payment?"})
            check("an ordinary message is not", bool(card["rows"]), True)

            print("\n=== Steps ===")
            check("operations does not record the report", UserRole.OPERATIONS in TOOLS["record_investigation_step"].roles_required, False)
            step = lambda **kw: {"investigation_id": inv_id, "note": "Reviewed the payee and the account opening file", **kw}  # noqa: E731
            await refused("a report carries its reference", ex["comp"].preview_change("record_investigation_step", step(step="reported")), "reference")
            await refused("an exit needs a forum of two", ex["comp"].preview_change("record_investigation_step",
                          step(step="exit_decision", attendees="Grace Lim", outcome="exit", minutes="AML forum 3 Nov")), "at least two")
            await refused("a review follows a report", ex["comp"].preview_change("record_investigation_step", step(step="continuing_review")), "follows a filed report")
            out = await ex["comp"].record_investigation_step(step(step="reported", reference="BSA-31000123456789"))
            inv = out["investigations"][0]
            check("reported, and the review dates follow", (inv["status"], inv["report_reference"], inv["clock"]["review_due"] is not None), ("reported", "BSA-31000123456789", True))
            out = await ex["comp"].record_investigation_step(step(step="exit_decision", attendees="Grace Lim, Tom Reid, Ana Cruz", outcome="exit", minutes="AML forum minutes 3 Nov, compliance drive"))
            check("the forum's exit is on record with who took part", (out["investigations"][0]["exit"]["outcome"], len(out["investigations"][0]["exit"]["attendees"])), ("exit", 3))
            await refused("after a report, there is no 'no report'", ex["comp"].preview_change("record_investigation_step", step(step="no_report")), "continuing review")
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
