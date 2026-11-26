"""Prove India money in and bank rules (B-388, onboarding module sections 4.2, 6.1 and 10).

Covers: a mandate requested, registered, failed and cancelled on its rail, its first debit confirmed or failed, and a queue with each item's age,
owner and task so no SIP dies silently, with nothing retried, sent or paid; money in by UPI, netbanking, RTGS, NEFT and cheque in rupees with
the payer checked against the registered bank and the investor, and the US ACH rules and 25,000 hold threshold not applied; the recognised payer
exceptions (a relative for a minor, an employer's payroll) and a name in initials; payments out only to a registered bank of the investor, with
the firm's own cooling-off on a changed bank and the words said when none is set; inward remittances to NRE or NRO accounts; the tools, their
Confirm cards and roles; and a firm outside India left as it was.

Builds the Indian demonstration firm (invented prices, no network) on a throwaway SQLite database.

    python -m tests.verify_india_money
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import (cash_movement, counsel_review, firm_time, guardianship, india_money, india_reminders, law_figures, money_in, platform_admin,
                            sip as sip_core, sip_mandates)
from app.core.db import Base, utcnow
from app.core.tools import CLIENT_TOOLS, TOOLS, ToolChangeState
from app.llm.executors import ExecutorError, ToolExecutors
from app.models.client_experience import Message
from app.models.engagement import Task
from app.models.enums import UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import AuditEvent, User
from app.models.india_money import IndiaCredit, MandateDebit, PayerException
from app.models.money_in import BankLink, IncomingDeposit
from app.models.portfolio import Price
from app.models.sip import Sip
from app.models.tenant import Firm
from app.models.trading import Order
from seed import india_b388, india_demo

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PASS, FAIL = [], []
ERRORS = (india_money.IndiaMoneyError, sip_mandates.MandateError, money_in.MoneyInError, cash_movement.CashMovementError, platform_admin.AdminError, ExecutorError)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got!r}, want {want!r}")


async def refused(label: str, coro, contains: str | None = None) -> None:
    try:
        await coro
    except ERRORS as e:
        ok = contains is None or contains.lower() in str(e).lower()
        (PASS if ok else FAIL).append(label)
        print(f"  {'PASS' if ok else 'FAIL'}  {label}: refused - {str(e)[:150]}")
    else:
        FAIL.append(label)
        print(f"  FAIL  {label}: was not refused")


async def count(s, model, *where) -> int:
    return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def main() -> int:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            firm = await india_demo.build(s)
            await s.commit()
            today = firm_time.today(firm)
            users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
            people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
            homes = {h.name: h for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}

            async def account_of(name: str) -> Account:
                return (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                                        .where(Mandate.person_id == people[name].id).order_by(Account.created_at))).scalars().first()

            karthik, rajesh, meera, subir = [await account_of(n) for n in ("Karthik Iyer", "Rajesh Sharma", "Meera Sharma", "Subir Banerjee")]
            vikram = await account_of("Vikram Menon")

            async def link_of(account, last4) -> BankLink:
                return (await s.execute(select(BankLink).where(BankLink.account_id == account.id, BankLink.last4 == last4))).scalars().one()

            k_bank, k_new = await link_of(karthik, "4821"), await link_of(karthik, "7310")
            r_bank, m_bank = await link_of(rajesh, "2204"), await link_of(meera, "5532")
            cash_before = {a.id: float(a.cash_balance) for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
            orders_before = await count(s, Order, Order.firm_id == firm.id)
            messages_before = await count(s, Message)

            print("\n=== The one new figure is assumed and unread by counsel ===")
            fig = law_figures.get("in_third_party_minor_limit", today)
            states = {r["key"]: r["counsel"]["state"] for r in (await counsel_review.overview(s, firm))["figures"]}
            check("the minor line says ASSUMED and reads not reviewed", ("ASSUMED" in fig.source, states.get("in_third_party_minor_limit"), fig.value), (True, "not_reviewed", 50_000))

            print("\n=== Names: initials are not a person ===")
            m = india_money.match
            check("the whole name, any order and case", (m("KARTHIK IYER", "Karthik Iyer"), m("Iyer Karthik", "Karthik Iyer")), ("full", "full"))
            check("a middle name or initial added by the bank", (m("Rajesh Kumar Sharma", "Rajesh Sharma"), m("Rajesh K Sharma", "Rajesh Sharma")), ("full", "full"))
            check("R Sharma reads by initials for Rajesh, and not at all for Meera", (m("R Sharma", "Rajesh Sharma"), m("R Sharma", "Meera Sharma")), ("initials", "none"))
            check("a wife's name carrying her husband's is not his", (m("Meera Rajesh Sharma", "Rajesh Sharma"), m("M Rajesh Sharma", "Rajesh Sharma") != "full"), ("none", True))
            check("a surname alone is nobody", m("Sharma", "Rajesh Sharma"), "none")
            check("a joint bank account passes where the investor holds it", (m("Karthik Iyer & Lakshmi Iyer", "Karthik Iyer"), m("Lakshmi Iyer", "Karthik Iyer")), ("full", "none"))
            check("an apostrophe is part of the name", m("ELAINE DSOUZA", "Elaine D'Souza"), "full")

            print("\n=== The demonstration queue: nothing dies silently ===")
            q = await india_money.queue(s, firm)
            by_state = {e["state"]: e for e in q}
            check("four things stand between a SIP and its debit", sorted(by_state), ["failed", "first_debit_failed", "none", "requested"])
            check("each has its age", {k: v["age_days"] for k, v in by_state.items()}, {"failed": 5, "requested": 12, "first_debit_failed": 4, "none": 100})
            check("each names its owner, its SIP and its task", ([e["owner"] is not None for e in q], [len(e["sips"]) for e in q], {e["task"] for e in q}), ([True] * 4, [1] * 4, {"open"}))
            check("all are past their chase line", [e["overdue"] for e in q], [True] * 4)
            check("a confirmed first debit leaves Karthik's mandate out", any(e["account_id"] == str(karthik.id) for e in q), False)
            tasks = (await s.execute(select(Task).where(Task.firm_id == firm.id, Task.title.like("India: %mandate%"), Task.source == india_reminders.SOURCE))).scalars().all()
            adviser_id = users[UserRole.ADVISER].id
            check("the tasks are the household adviser's", {t.assigned_to for t in tasks}, {adviser_id})
            n_tasks = await count(s, Task, Task.firm_id == firm.id)
            await india_reminders.raise_due(s, firm)
            check("the daily job raises none of them twice", await count(s, Task, Task.firm_id == firm.id, Task.title.like("India: %mandate%")),
                  len([t for t in tasks]))
            check("the seed adds nothing a second time", await india_b388.add(s, firm), 0)
            check("one household's queue is its own", {e["household"] for e in await india_money.queue(s, firm, household_id=homes["The Sharma Family"].id)}, {"The Sharma Family"})
            ov = await sip_mandates.overview(s, firm)
            check("the mandates page carries the queue and the states", (len(ov["queue"]), ov["counts"]["requested"], ov["counts"]["failed"]), (4, 1, 1))
            check("a mandate shows its rail and first debit", [(x["type_label"], x["first_debit"]["outcome"]) for x in ov["mandates"] if x["reference"] == "UMRN-DEMO-IYER-0001"],
                  [("NACH e-mandate", "confirmed")])
            for ref, kind in (("NACH-REQ-DEMO-SHARMA-2", "requested"), ("UPI-REQ-DEMO-SHARMA-1", "failed")):
                row = next(r for r in await sip_mandates.standing(s, firm.id) if r.reference == ref)
                check(f"a {kind} mandate is the reason an instalment is missed", sip_mandates.reason_unfit(row, 1_000, today) is not None, True)

            print("\n=== A request, a failure and a registration, each from its paper ===")
            base = dict(account_id=karthik.id, document="Mandate paper, filed")
            req = dict(base, reference="NACH-PAPER-77", stage="requested", mandate_type="nach_paper", per_debit_limit=20_000, starts_on=today, last4="4821")
            await refused("a request on a bank not verified is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **{**req, "last4": "1111"}), "not verified")
            await refused("a request on another person's account is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **{**req, "account_id": rajesh.id}), "not verified")
            await refused("a stage that is not one is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **{**req, "stage": "pending"}), "requested, registered or failed")
            await refused("a request dated ahead is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **{**req, "on": today + timedelta(days=1)}), "ahead")
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", **req)
            row = next(r for r in await sip_mandates.standing(s, firm.id, [karthik.id]) if r.reference == "NACH-PAPER-77")
            check("it stands as requested, on paper", (sip_mandates.state_of(row, today), row.mandate_type, row.event_on), ("requested", "nach_paper", today))
            await refused("the same request twice is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **req), "already recorded")
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", **{**req, "reference": "UPI-REQ-OLD-1", "mandate_type": "upi_autopay",
                                      "starts_on": today - timedelta(days=5), "on": today - timedelta(days=5)})
            counted = await count(s, Task, Task.firm_id == firm.id, Task.title.like("%UPI-REQ-OLD-1%"))
            await india_reminders.raise_due(s, firm)
            check("a request is no task when recorded, and the daily job raises one once it is past its chase line",
                  (counted, await count(s, Task, Task.firm_id == firm.id, Task.title.like("%UPI-REQ-OLD-1%"))), (0, 1))
            fail_terms = dict(base, reference="NACH-PAPER-77", stage="failed")
            await refused("a failure needs the bank's reason", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **fail_terms), "why the registration failed")
            before = await count(s, Task, Task.firm_id == firm.id)
            got = await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", reason="Signature differs from the bank's record", **fail_terms)
            row = next(r for r in await sip_mandates.standing(s, firm.id, [karthik.id]) if r.reference == "NACH-PAPER-77")
            check("a failure carries the request's terms", (sip_mandates.state_of(row, today), float(row.per_debit_limit), row.mandate_type), ("failed", 20000.0, "nach_paper"))
            check("it is queued and tasked even with an earlier mandate in force, which may not be the one the client means", (any(e["reference"] == "NACH-PAPER-77" for e in got["queue"]),
                  await count(s, Task, Task.firm_id == firm.id) - before), (True, 1))
            await refused("a registered mandate is not recorded as a failed registration", sip_mandates.prepare(s, firm, UserRole.OPERATIONS,
                          **{**fail_terms, "reference": "UMRN-DEMO-IYER-0001", "reason": "Returned by the bank"}), "is registered")
            await refused("a registration that follows nothing is refused", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **base, reference="UMRN-N-1", request_reference="NOPE-1",
                          mandate_type="nach", per_debit_limit=1, starts_on=today, last4="4821"), "no requested or failed mandate")
            meera_sip = (await s.execute(select(Sip).where(Sip.account_id == meera.id))).scalars().one()
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=meera.id, reference="UMRN-DEMO-SHARMA-9", request_reference="NACH-REQ-DEMO-SHARMA-2",
                                      starts_on=today, document="NACH registration, filed")
            behind = await sip_mandates.for_sip(s, meera_sip, today)
            check("a registration replaces its request, under its new number", (behind["state"], behind["row"].reference, behind["row"].mandate_type,
                  [r.reference for r in await sip_mandates.standing(s, firm.id, [meera.id])]), ("active", "UMRN-DEMO-SHARMA-9", "nach", ["UMRN-DEMO-SHARMA-9"]))
            check("and Meera's request leaves the queue", any(e["account_id"] == str(meera.id) and e["state"] == "requested" for e in await india_money.queue(s, firm)), False)

            print("\n=== The first debit ===")
            debit = dict(account_id=meera.id, reference="UMRN-DEMO-SHARMA-9", debited_on=today, amount=10_000, document="Bank's notice, filed")
            await refused("no first debit under a mandate that failed", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, account_id=rajesh.id,
                          reference="UPI-REQ-DEMO-SHARMA-1", outcome="confirmed", debited_on=today, amount=100, document="Notice, filed"), "not registered")
            await refused("a debit before the mandate starts is refused", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, outcome="confirmed",
                          **{**debit, "debited_on": today - timedelta(days=1)}), "starts on")
            await refused("a confirmed debit above the limit is refused", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, outcome="confirmed", **{**debit, "amount": 99_000}), "above the mandate's limit")
            await refused("a failure needs its reason", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, outcome="failed", **debit), "reason")
            await refused("compliance does not record a debit", india_money.prepare_first_debit(s, firm, UserRole.COMPLIANCE, outcome="confirmed", **debit), "records a first debit")
            await refused("neither confirmed nor failed is refused", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, outcome="maybe", **debit), "confirmed or failed")
            before = await count(s, Task, Task.firm_id == firm.id)
            got = await india_money.record_first_debit(s, firm, UserRole.ADVISER, by="adviser", outcome="failed", reason="Account closed", **debit)
            mine = [e for e in got["queue"] if e["account_id"] == str(meera.id)]
            check("a failed first debit is queued and tasked at once", ([(e["state"], e["task"], e["reason"]) for e in mine], await count(s, Task, Task.firm_id == firm.id) - before, len(got["tasks_raised"])),
                  ([("first_debit_failed", "open", "Account closed")], 1, 1))
            await refused("the same failure twice is refused", india_money.prepare_first_debit(s, firm, UserRole.ADVISER, outcome="failed", reason="Account closed", **debit), "already recorded")
            got = await india_money.record_first_debit(s, firm, UserRole.OPERATIONS, by="ops", outcome="confirmed", **debit)
            check("confirmed later, it leaves the queue and both records stay", (any(e["account_id"] == str(meera.id) for e in got["queue"]),
                  await count(s, MandateDebit, MandateDebit.account_id == meera.id)), (False, 2))
            await refused("a confirmed first debit is not recorded again", india_money.prepare_first_debit(s, firm, UserRole.OPERATIONS, outcome="failed", reason="Again", **debit), "already recorded as confirmed")
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=rajesh.id, reference="UPI-DEMO-SHARMA-7", request_reference="UPI-REQ-DEMO-SHARMA-1",
                                      starts_on=today - timedelta(days=5), document="UPI Autopay registration, filed")
            r_sip = (await s.execute(select(Sip).where(Sip.account_id == rajesh.id))).scalars().one()
            r_sip.starts_on, r_sip.day_of_month = today - timedelta(days=40), min((today - timedelta(days=4)).day, 28)
            await s.flush()
            unconfirmed = [e for e in await india_money.queue(s, firm) if e["account_id"] == str(rajesh.id)]
            check("a first debit nobody recorded joins the queue once its day has passed", [(e["state"], e["overdue"]) for e in unconfirmed], [("unconfirmed", True)])
            await s.execute(Price.__table__.update().values(is_real=True))
            k_row = next(r for r in await sip_mandates.standing(s, firm.id, [meera.id]) if r.reference == "UMRN-DEMO-SHARMA-9")
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by="ops", account_id=meera.id, reference=k_row.reference, cancel=True, document="Cancellation notice",
                                      reason="The client cancelled it at the bank")
            check("a cancelled mandate behind a running SIP is queued", [e["state"] for e in await india_money.queue(s, firm) if e["account_id"] == str(meera.id)], ["cancelled"])
            asked = await count(s, Order, Order.firm_id == firm.id)
            await sip_core.run_due(s, firm, today=today.replace(day=28))
            by_sip_run = await count(s, Order, Order.firm_id == firm.id) - asked
            await s.refresh(meera_sip)
            check("and the instalment is missed for it", (meera_sip.history[-1]["status"], "cancelled" in meera_sip.history[-1]["reason"]), ("missed", True))

            print("\n=== Money in, in rupees, by Indian rails ===")
            pol = money_in.policy(firm)
            check("an Indian firm has no hold threshold until it sets one", (pol["deposit_hold_threshold"], pol["deposit_hold_days"]), (None, {}))
            check("a US firm keeps its own", money_in.policy(Firm(jurisdiction="US", settings={}))["deposit_hold_threshold"], 25000)
            await refused("the US path is refused for India", money_in.prepare_deposit(s, firm.id, account_id=karthik.id, method="wire", amount=10, evidence="Advice, filed"), "record_india_credit")
            terms = dict(account_id=karthik.id, rail="rtgs", amount="2,500,000", bank_link_id=k_bank.id, payer_name="Karthik Iyer", reference="UTR-1001", evidence="Credit advice, filed")
            p = await india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **terms)
            check("a large credit carries no hold", (p["amount"], p["hold_until"], p["check"]["basis"]), (2_500_000.0, None, "investor"))
            for rail in ("upi", "netbanking", "neft", "cheque"):
                got = await india_money.record_credit(s, firm, UserRole.OPERATIONS, by="ops", **{**terms, "rail": rail, "amount": 1000, "reference": f"REF-{rail}"})
            stored = (await s.execute(select(IncomingDeposit.method).join(IndiaCredit, IndiaCredit.deposit_id == IncomingDeposit.id).where(IndiaCredit.bank_reference.like("REF-%")))).scalars().all()
            check("each rail is stored within the column's eight characters", (sorted(stored), all(len(x) <= 8 for x in stored)), (["cheque", "neft", "netbank", "upi"], True))
            check("the view names the rail, the payer and the basis", [(d["rail_label"], d["payer"], d["basis_label"]) for d in got["deposits"] if d.get("reference") == "REF-netbanking"],
                  [("Netbanking", "Karthik Iyer", "The investor's own account")])
            await refused("ACH is not an Indian rail", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "rail": "ach"}), "US rules")
            await refused("the same reference twice is refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "rail": "neft", "reference": "REF-neft"}), "already recorded")
            await refused("a credit dated ahead is refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "on": today + timedelta(days=1)}), "ahead")
            await refused("an adviser does not record money in", india_money.prepare_credit(s, firm, UserRole.ADVISER, **terms), "operations records")
            await refused("a paying name that is not the registered owner's is refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "payer_name": "Lakshmi Iyer"}), "does not match")
            await money_in.record_link(s, firm.id, by="ops", account_id=karthik.id, bank="B2", last4="3333", owner_name="Lakshmi Iyer", method="voided_cheque",
                                       evidence="Cheque, filed", verified_on=today)
            spouse = await link_of(karthik, "3333")
            check("a spouse's account is first party to the US reading", spouse.third_party, False)
            await refused("but a household member is not the investor", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "bank_link_id": spouse.id, "payer_name": "Lakshmi Iyer"}), "third-party")
            await refused("nor may her account carry his mandate", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, account_id=karthik.id, reference="UMRN-SPOUSE-1", mandate_type="nach",
                          per_debit_limit=1000, starts_on=today, last4="3333", document="Registration, filed"), "third-party")
            await refused("the investor's name on an account nobody registered is refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS,
                          **{**terms, "bank_link_id": None, "payer_last4": "0000"}), "not named as a registered bank")
            await refused("another account's bank is not this one's", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "bank_link_id": r_bank.id}), "not registered on")

            print("\n=== A name in initials, confirmed once from the bank's document ===")
            await money_in.record_link(s, firm.id, by="ops", account_id=rajesh.id, bank="B3", last4="4444", owner_name="R Sharma", method="instant", evidence="Report, filed", verified_on=today)
            short = await link_of(rajesh, "4444")
            initials = dict(account_id=rajesh.id, rail="neft", amount=5000, bank_link_id=short.id, payer_name="R SHARMA", reference="UTR-2001", evidence="Advice, filed")
            await refused("initials alone are refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **initials), "initials only")
            conf = dict(account_id=rajesh.id, kind="name_confirmed", payer_name="R Sharma", bank="B3", last4="4444", evidence="Bank's letter naming Rajesh Sharma")
            await refused("an adviser does not record an exception", india_money.prepare_exception(s, firm, UserRole.ADVISER, **conf), "Operations or compliance")
            await refused("a different person's name is not an abbreviation", india_money.prepare_exception(s, firm, UserRole.OPERATIONS, **{**conf, "payer_name": "Kiran Rao"}), "somebody else")
            await india_money.record_exception(s, firm, UserRole.COMPLIANCE, by="compliance", **conf)
            got = await india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **initials)
            check("once confirmed it is accepted, and says on what basis", (got["check"]["ok"], got["check"]["basis"]), (True, "name_confirmed"))

            print("\n=== A parent paying for a minor ===")
            ira = people["Ira Menon"]
            mandate = Mandate(firm_id=firm.id, person_id=ira.id, name="Ira Menon - Savings", mandate_type="advisory", suitability={}, constraints={})
            s.add(mandate)
            await s.flush()
            minor = Account(firm_id=firm.id, mandate_id=mandate.id, name="Ira Menon demat", custodian="CDSL", currency="INR", cash_balance=100_000)
            s.add(minor)
            await s.flush()
            cash_before[minor.id] = 100_000.0
            await money_in.record_link(s, firm.id, by="ops", account_id=minor.id, bank="B4", last4="7001", owner_name="Vikram Menon", method="voided_cheque", evidence="Cheque, filed", verified_on=today)
            dad = await link_of(minor, "7001")
            m_terms = dict(account_id=minor.id, reference="UMRN-MINOR-1", mandate_type="nach", per_debit_limit=10_000, starts_on=today, last4="7001", document="Registration, filed")
            await refused("a parent's bank is refused until the exception is on file", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **m_terms), "third-party")
            exc = dict(account_id=minor.id, kind="minor_relative", payer_name="Vikram Menon", bank="B4", last4="7001", relationship="father", evidence="Birth certificate and declaration, filed")
            await refused("an exception needs the relationship", india_money.prepare_exception(s, firm, UserRole.OPERATIONS, **{**exc, "relationship": None}), "who the payer is")
            await refused("an adult has no minor exception", india_money.prepare_exception(s, firm, UserRole.OPERATIONS, **{**exc, "account_id": karthik.id}), "not a minor")
            card = await india_money.prepare_exception(s, firm, UserRole.OPERATIONS, **exc)
            check("the exception carries the line, as assumed", card["line"], 50_000)
            await india_money.record_exception(s, firm, UserRole.OPERATIONS, by="ops", **exc)
            ok = await sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **m_terms)
            check("with it the mandate is accepted, naming the basis", ok["payer"]["basis"], "minor_relative")
            await refused("a debit limit above the line is not carried", sip_mandates.prepare(s, firm, UserRole.OPERATIONS, **{**m_terms, "per_debit_limit": 60_000}), "above the")
            gift = dict(account_id=minor.id, rail="upi", amount=40_000, bank_link_id=dad.id, payer_name="Vikram Menon", reference="UPI-MINOR-1", evidence="Advice, filed")
            check("a payment within the line is accepted", (await india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **gift))["check"]["basis"], "minor_relative")
            await refused("one above it is refused", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**gift, "amount": 60_000}), "above the")
            await refused("the parent is never paid out to", cash_movement.prepare_request(s, minor.id, firm_id=firm.id, kind="withdrawal", amount=100, payee=None,
                          reason="School fees", bank_link_id=dad.id), "registered bank account of the investor")
            await money_in.record_link(s, firm.id, by="ops", account_id=minor.id, bank="B4", last4="7002", owner_name="Ira Menon & Vikram Menon", method="voided_cheque", evidence="Cheque, filed", verified_on=today)
            joint = await link_of(minor, "7002")
            check("the minor's joint account with her guardian is hers", (await india_money.check_payer(s, firm, minor, payer_name="Ira Menon", bank_link=joint, use="payout"))["ok"], True)
            born = ira.date_of_birth
            ira.date_of_birth = born.replace(year=born.year - 10)
            await s.flush()
            await refused("at majority the exception stops carrying", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **gift), "not a minor")
            ira.date_of_birth = born
            await s.flush()
            await refused("a withdrawal of an exception needs its reason", india_money.prepare_exception(s, firm, UserRole.OPERATIONS, **{**exc, "withdraw": True}), "why it is withdrawn")
            await india_money.record_exception(s, firm, UserRole.OPERATIONS, by="ops", **{**exc, "withdraw": True, "reason": "The father asked for it to end"})
            await refused("withdrawn, it carries nothing", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **gift), "third-party")
            check("both rows stay", await count(s, PayerException, PayerException.account_id == minor.id), 2)

            print("\n=== An employer's payroll ===")
            payroll = dict(account_id=karthik.id, rail="neft", amount=10_000, payer_name="Demonstration Systems Private Limited", payer_last4="9001", reference="UTR-3001", evidence="Advice, filed")
            check("the demonstration's payroll exception carries a SIP credit", (await india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **payroll))["check"]["basis"], "employer_payroll")
            await refused("another account number is not the employer's", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**payroll, "payer_last4": "9002"}), "third-party")
            await refused("an account with no SIP has no payroll exception", india_money.prepare_exception(s, firm, UserRole.OPERATIONS, account_id=vikram.id, kind="employer_payroll",
                          payer_name="An Employer Limited", bank="B1", last4="9100", evidence="Letter, filed"), "no active SIP")
            m_check = await india_money.verdict(s, firm, account_id=karthik.id, payer_name="Demonstration Systems Private Limited", last4="9001", use="mandate")
            check("a payroll never carries a mandate", (m_check["ok"], "payroll" in m_check["said"]), (False, True))
            unregistered = await india_money.verdict(s, firm, account_id=karthik.id, payer_name="Karthik Iyer", last4="0000", use="payout")
            check("nothing is paid to an account that is not registered", (unregistered["ok"], "registered bank" in unregistered["said"]), (False, True))

            print("\n=== A return, as the memo states it ===")
            dep = (await s.execute(select(IndiaCredit).where(IndiaCredit.bank_reference == "REF-cheque"))).scalars().one()
            await refused("a return needs the memo's reason", india_money.prepare_credit(s, firm, UserRole.OPERATIONS, deposit_id=dep.deposit_id, evidence="Memo, filed"), "reason")
            got = await india_money.record_credit(s, firm, UserRole.OPERATIONS, by="ops", deposit_id=dep.deposit_id, return_reason="Cheque returned: funds insufficient", evidence="Return memo, filed")
            back = next(d for d in got["deposits"] if d["id"] == str(dep.deposit_id))
            check("it is returned with its reason and no NACHA code", (back["return_code"], back["return_reason"], got["returns_by_code"]), (None, "Cheque returned: funds insufficient", []))
            firm.settings = {**firm.settings, "money_in": {"deposit_hold_threshold": 100_000, "deposit_hold_days": {"cheque": 3}}}
            held = await india_money.prepare_credit(s, firm, UserRole.OPERATIONS, **{**terms, "rail": "cheque", "reference": "CHQ-9"})
            check("a hold the firm sets itself is applied", held["hold_until"], money_in.business_days_after(today, 3))
            firm.settings = {k: v for k, v in firm.settings.items() if k != "money_in"}

            print("\n=== Money out: only to the investor's registered bank, and not to a changed one yet ===")
            pay = dict(firm_id=firm.id, kind="withdrawal", amount=1000, payee="Somebody Else", reason="School fees")
            await refused("a payee in free text is refused", cash_movement.prepare_request(s, karthik.id, **pay), "registered bank account")
            await refused("a third party is refused", cash_movement.prepare_request(s, karthik.id, **pay, third_party=True, bank_link_id=k_bank.id), "never to a third party")
            await refused("a spouse's bank is refused", cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=spouse.id), "investor")
            await refused("another account's bank is refused", cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=r_bank.id), "not registered on")
            await refused("the changed bank waits for the cooling-off, with the date", cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_new.id), "cooling-off runs to")
            r = await cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_bank.id)
            check("the first registered bank is paid, and names the payee whatever was typed", (r["payee"], r["bank"]["change"]), (f"{k_bank.owner_name}, {k_bank.bank} ending 4821", False))
            later = k_new.created_at, k_new.verified_on
            k_new.created_at, k_new.verified_on = utcnow() - timedelta(days=30), today - timedelta(days=30)
            k_bank.created_at = utcnow() - timedelta(days=60)
            await s.flush()
            r = await cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_new.id)
            check("once it has passed, the changed bank is paid", (r["bank"]["change"], r["bank"]["payable"]), (True, True))
            k_new.created_at, k_new.verified_on = utcnow(), today - timedelta(days=300)
            await s.flush()
            await refused("an old verification date does not shorten the wait", cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_new.id), "cooling-off")
            keep = firm.settings
            firm.settings = {k: v for k, v in firm.settings.items() if k != "india_money"}
            r = await cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_new.id)
            check("with none set a changed bank is payable at once, and it says so", (r["bank"]["payable"], r["bank"]["cooling_days"], "no cooling-off" in r["bank"]["said"].lower()), (True, None, True))
            check("and the view says none is set", (await india_money.overview(s, firm))["cooling"]["label"], "No cooling-off set: a changed bank is payable at once")
            firm.settings = keep
            k_new.status = "revoked"
            await s.flush()
            await refused("a revoked bank is not registered", cash_movement.prepare_request(s, karthik.id, **pay, bank_link_id=k_new.id), "revoked")
            k_new.status = "verified"
            await s.flush()
            await refused("a standing payment needs a registered bank too", cash_movement.prepare_standing(s, karthik.id, firm_id=firm.id, payee="Somebody", amount=100, frequency="monthly",
                          first_due=today, source_document="Signed form"), "registered bank account")
            order = await cash_movement.record_standing(s, karthik.id, firm_id=firm.id, by="ops", payee=None, amount=100, frequency="monthly", first_due=today,
                                                        source_document="Signed form", bank_link_id=k_bank.id)
            via = await cash_movement.prepare_request(s, karthik.id, firm_id=firm.id, kind="standing", amount=100, payee=None, reason="This month", standing_id=order.id)
            check("a request under it goes to its bank", (order.bank_link_id, via["bank"]["link"].id), (k_bank.id, k_bank.id))
            made = await cash_movement.request(s, karthik.id, firm_id=firm.id, by="adviser", kind="withdrawal", amount=500, payee=None, reason="Fees", bank_link_id=k_bank.id)
            paid = await cash_movement.prepare_paid(s, made.id, firm_id=firm.id, by="ops", paid_on=today, source_document="Custodian confirmation")
            check("recording it paid shows the registered bank", (made.bank_link_id, paid["bank"]), (k_bank.id, f"{k_bank.bank} ending 4821"))

            print("\n=== The cooling-off is the administrator's, on the audit log ===")
            admin = users[UserRole.ADMIN]
            await refused("operations cannot set it", india_money.set_cooling(s, firm, users[UserRole.OPERATIONS], days=3, reason="Fraud control"), "administrator")
            await refused("it needs a reason", india_money.set_cooling(s, firm, admin, days=3, reason=""), "reason")
            await refused("it is 1 to 365 days or none", india_money.set_cooling(s, firm, admin, days=900, reason="Fraud control"), "1 to 365")
            await refused("the same value is refused", india_money.set_cooling(s, firm, admin, days=10, reason="Fraud control"), "already")
            got = await india_money.set_cooling(s, firm, admin, days="none", reason="Trying the control without it")
            event = (await s.execute(select(AuditEvent).where(AuditEvent.firm_id == firm.id, AuditEvent.event_type == "india_money.cooling_set")
                                     .order_by(AuditEvent.created_at.desc(), AuditEvent.id))).scalars().all()
            check("cleared, said so, and recorded", (got["cooling"]["set_by_firm"], any(e.detail["reason"] == "Trying the control without it" for e in event)), (False, True))
            await india_money.set_cooling(s, firm, admin, days=10, reason="Back to ten days")
            check("the generic settings route cannot change it", "india_money" in platform_admin.RESERVED_SETTINGS, True)

            print("\n=== Inward remittances ===")
            nro = await link_of(vikram, "6650")
            rem = dict(account_id=vikram.id, bank_link_id=nro.id, account_kind="nro", received_on=today, amount_inr=100_000, certificate_reference="FIRC-2",
                       remitter_name="Vikram Menon", document="Certificate, filed")
            await refused("it needs the certificate's reference", india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **{**rem, "certificate_reference": ""}), "certificate")
            await refused("the same certificate twice is refused", india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **{**rem, "certificate_reference": "FIRC-DEMO-000045"}), "already recorded")
            await refused("NRE or NRO is said, never assumed", india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **{**rem, "account_kind": ""}), "NRE or an NRO")
            await refused("a resident's account takes none", india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **{**rem, "account_id": karthik.id, "bank_link_id": k_bank.id}), "non-resident")
            vikram.registration_type = "nri_nre"
            await s.flush()
            await refused("NRO money does not go on an NRE account", india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **rem), "repatriable")
            vikram.registration_type = "nri_nro"
            await s.flush()
            warned = await india_money.prepare_remittance(s, firm, UserRole.OPERATIONS, **{**rem, "account_kind": "nre", "remitter_name": "A Relative Abroad"})
            check("NRE money into an NRO account is warned, and the sender read", (bool(warned["warning"]), warned["remitter_match"]), (True, "none"))
            got = await india_money.record_remittance(s, firm, UserRole.ADVISER, by="adviser", **rem)
            check("recorded with its certificate", sorted(r["certificate"] for r in got["remittances"]), ["FIRC-2", "FIRC-DEMO-000045"])

            print("\n=== The tools, their cards and who holds them ===")
            writes = ("record_payer_exception", "record_india_credit", "record_inward_remittance", "record_first_debit", "set_bank_cooling_off")
            check("every change stops for a Confirm; the check does not", ([TOOLS[k].change_state for k in writes], TOOLS["check_payer"].change_state),
                  ([ToolChangeState.PAUSE_FOR_CONFIRMATION] * 5, ToolChangeState.NO))
            check("no client holds any of them", [k for k in (*writes, "check_payer") if k in CLIENT_TOOLS], [])
            check("the cooling-off is the administrator's tool alone", TOOLS["set_bank_cooling_off"].roles_required, {UserRole.ADMIN})

            def ex(role) -> ToolExecutors:
                return ToolExecutors(s, users[role].id, role, firm.id)

            ops = ex(UserRole.OPERATIONS)
            deposits_before = await count(s, IncomingDeposit)
            credit = {"account_id": str(karthik.id), "rail": "upi", "amount": 2000, "bank_link_id": str(k_bank.id), "payer_name": "Karthik Iyer", "reference": "UPI-TOOL-1", "evidence": "Advice, filed"}
            card = {r["label"]: r["value"] for r in (await ops.preview_change("record_india_credit", credit))["rows"]}
            check("the credit card names the money, the rail, the payer and the check, and writes nothing",
                  (card["Amount"], card["By"], card["Payer check"], "no hold" in card["Hold"], await count(s, IncomingDeposit) - deposits_before),
                  ("₹2,000", "UPI", "The investor's own account", True, 0))
            await refused("a card refuses what the write would", ops.preview_change("record_india_credit", {**credit, "payer_name": "Lakshmi Iyer"}), "does not match")
            done = await ops.execute("record_india_credit", credit)
            check("confirmed, it is recorded and says nothing moved", (await count(s, IncomingDeposit) - deposits_before, "no cash" in done["message"]), (1, True))
            verdict = await ops.execute("check_payer", {"account_id": str(karthik.id), "bank_link_id": str(k_new.id), "use": "payout"})
            check("the check answers before money moves", (verdict["check"]["ok"], verdict["check"]["label"]), (False, "Cooling off"))
            read = await ops.execute("read_money_in", {})
            check("money in reads India's own", (read["india"]["applies"], read["policy"]["deposit_hold_threshold"], bool(read["india"]["queue"])), (True, None, True))
            reqcard = {r["label"]: r["value"] for r in (await ex(UserRole.ADVISER).preview_change("request_cash_movement",
                       {"account_id": str(karthik.id), "kind": "withdrawal", "amount": 100, "reason": "Fees", "bank_link_id": str(k_bank.id)}))["rows"]}
            check("the payment card names the registered bank and the cooling-off", ("ending 4821" in reqcard["To"], "10 days" in reqcard["Bank"]), (True, True))
            mcard = {r["label"]: r["value"] for r in (await ops.preview_change("record_sip_mandate", {"account_id": str(subir.id), "reference": "UMRN-DEMO-BANERJEE-0002",
                     "stage": "failed", "mandate_type": "nach", "per_debit_limit": 5000, "starts_on": today.isoformat(), "reason": "Returned by the bank", "document": "Return memo"}))["rows"]}
            check("a failure's card says who gets the task", (mcard["Recording"], mcard["Then"].startswith("A task for ")), ("A failed registration", True))
            bcard = {r["label"]: r["value"] for r in (await ops.preview_change("record_bank_link", {"account_id": str(rajesh.id), "bank": "B9", "last4": "9876", "owner_name": "Rajesh Sharma",
                     "method": "instant", "evidence": "Report, filed"}))["rows"]}
            check("a new bank's card says when it may be paid", "10-day cooling-off" in bcard["If this changes the bank"], True)
            for key, args in (("record_first_debit", {"account_id": str(rajesh.id), "reference": "UPI-DEMO-SHARMA-7", "outcome": "confirmed", "debited_on": today.isoformat(), "amount": 100, "document": "Notice, filed"}),
                              ("record_payer_exception", {**conf, "account_id": str(rajesh.id), "payer_name": "Rajesh Sharma"}),
                              ("record_inward_remittance", {**rem, "account_id": str(vikram.id), "bank_link_id": str(nro.id), "received_on": today.isoformat(), "certificate_reference": "FIRC-3"}),
                              ("set_bank_cooling_off", {"days": "7", "reason": "A shorter wait"})):
                actor = ex(UserRole.ADMIN) if key == "set_bank_cooling_off" else ops
                if key == "record_payer_exception":
                    await refused("an exception for the investor's own full name is not needed", actor.preview_change(key, {**args, "kind": "name_confirmed"}), "nothing to confirm")
                    continue
                rows = (await actor.preview_change(key, args))["rows"]
                check(f"{key} has its card", len(rows) >= 4, True)

            print("\n=== Nothing here moves money, places an order or crosses firms ===")
            now = {a.id: float(a.cash_balance) for a in (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}
            check("no cash changed on any account, and nothing but the SIP run asked for an order",
                  (all(abs(now[a] - cash_before[a]) < 1e-6 for a in cash_before), await count(s, Order, Order.firm_id == firm.id) - orders_before - by_sip_run), (True, 0))
            check("and nothing was sent to any client", await count(s, Message) - messages_before, 0)
            other = Firm(slug="other-in", name="Other", jurisdiction="IN", base_currency="INR", settings={})
            s.add(other)
            await s.flush()
            await refused("another firm cannot read this account's payer", india_money.verdict(s, other, account_id=karthik.id, payer_name=None, bank_link_id=k_bank.id), "Account not found")
            await refused("nor record a credit on it", india_money.prepare_credit(s, other, UserRole.OPERATIONS, **{**terms, "reference": "X-OTHER"}), "Account not found")
            check("another firm's queue is empty", await india_money.queue(s, other), [])
            nz = Firm(slug="nz-x", name="NZ", jurisdiction="NZ", base_currency="NZD", settings={})
            s.add(nz)
            await s.flush()
            await refused("a firm outside India records none of this", india_money.prepare_credit(s, nz, UserRole.OPERATIONS, **terms), "Indian firm")
            check("and has no India view", ((await money_in.for_firm(s, nz.id))["india"], (await india_money.overview(s, nz))["applies"]), (None, False))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


async def redating() -> None:
    """A fresh build, read a week later: the request, the failure and the cooling-off move with the day."""
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            firm = await india_demo.build(s)
            await s.commit()
            later = firm_time.today(firm) + timedelta(days=30)
            check("a month on, the request, the failure and the changed bank are moved", await india_b388.redate(s, firm, later), 3)
            check("and a second run moves nothing", await india_b388.redate(s, firm, later), 0)
            karthik = (await s.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.last4 == "7310"))).scalars().one()
            account = await s.get(Account, karthik.account_id)
            check("the moved bank is still the changed one", (await india_money.bank_state(s, firm, account, karthik))["change"], True)
    finally:
        await db.dispose()


async def run() -> int:
    await redating()
    return await main()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
