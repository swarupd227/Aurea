"""India money in and bank rules on the demonstration firm (B-388): mandates in every state a SIP can die in, first debits, credits by
Indian rails with the payer checked, a changed bank cooling off, an employer's payroll exception and an NRO inward remittance.

Every record is invented for the demonstration firm and says so in its evidence. Dates are days before the firm's today. Run from
`seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before; it adds nothing where its first
debit record is already on file.

  The firm         sets its own 10-day cooling-off on a changed bank (its choice for the demonstration, not a legal period)
  Karthik Iyer     the first debit under his NACH mandate confirmed; a NEFT credit from his registered bank; an employer's payroll
                   recorded as an exception and a SIP instalment received from it; a second bank registered days ago, still cooling off
  Rajesh Sharma    a SIP whose UPI Autopay registration failed (a task for his adviser)
  Meera Sharma     a SIP whose NACH e-mandate was requested twelve days ago and is still not registered (a task)
  Subir Banerjee   a SIP whose mandate registered but whose first debit failed (a task)
  The Kapoor trust the SIP with no mandate on file (already seeded) joins the queue, with its task
  Vikram Menon     the NRO bank account registered, and an inward remittance with its certificate reference
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, india_money, india_reminders, money_in, platform_admin, sip, sip_mandates
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Mandate, Person
from app.models.identity import User
from app.models.india_money import MandateDebit
from app.models.money_in import BankLink
from app.models.sip import Sip
from app.models.tenant import Firm

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India money in (demonstration)"
BANK, OTHER_BANK = "A demonstration bank (invented)", "A second demonstration bank (invented)"
EMPLOYER = "Demonstration Systems Private Limited"
COOLING_DAYS = 10
CHANGED_BANK_LAST4, CHANGED_BANK_AGE = "7310", 3
FUND = "PPFAS-FLEXI-DIRECT"
SIP_RUNS_FOR = 300
# Mandate reference -> how many days ago the request was made or the registration failed.
REQUESTED = {"NACH-REQ-DEMO-SHARMA-2": 12}
FAILED = {"UPI-REQ-DEMO-SHARMA-1": (8, 5)}


async def _account_of(s: AsyncSession, firm: Firm, person: Person | None) -> Account | None:
    if person is None:
        return None
    return (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                            .where(Mandate.firm_id == firm.id, Mandate.person_id == person.id).order_by(Account.created_at))).scalars().first()


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(MandateDebit.id).where(MandateDebit.firm_id == firm.id, MandateDebit.recorded_by == BY).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    n = 0

    admin = (await s.execute(select(User).where(User.firm_id == firm.id, User.role == UserRole.ADMIN))).scalars().first()
    if india_money.cooling_days(firm) is None:
        firm.settings = {**(firm.settings or {}), india_money.SETTINGS_KEY: {india_money.COOLING_KEY: COOLING_DAYS}}
        if admin is not None:
            platform_admin.audit(s, firm.id, admin, "india_money.cooling_set", subject="Cooling-off on a changed bank",
                                 detail={"before": None, "after": COOLING_DAYS, "reason": f"The demonstration firm's own control ({SOURCE})"})
        n += 1

    async def where_it_stands(record) -> int:
        """One demonstration record, left out where what a person has since recorded on the deployed firm no longer admits it."""
        try:
            await record
        except india_money.IndiaMoneyError:
            return 0
        return 1

    async def bank(account: Account, owner: str, last4: str, days_ago: int, name: str = BANK, callback: str | None = None) -> BankLink:
        nonlocal n
        have = (await s.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.account_id == account.id, BankLink.last4 == last4))).scalars().first()
        if have is not None:
            return have
        await money_in.record_link(s, firm.id, by=BY, account_id=account.id, bank=name, last4=last4, owner_name=owner, method="voided_cheque",
                                   evidence=f"Cancelled cheque, filed ({SOURCE})", verified_on=today - timedelta(days=days_ago), callback_by=callback)
        n += 1
        return (await s.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.account_id == account.id, BankLink.last4 == last4))).scalars().one()

    async def sip_on(account: Account, amount: int, day: int, since: int) -> Sip | None:
        nonlocal n
        try:
            row = await sip.record(s, firm, account.id, by=BY, instrument=FUND, amount=amount, day_of_month=day, starts_on=today - timedelta(days=since),
                                   ends_on=today + timedelta(days=SIP_RUNS_FOR), evidence_ref=None, simulated=True)
        except sip.SipError:
            return None
        n += 1
        return row

    karthik = people.get("Karthik Iyer")
    k_account = await _account_of(s, firm, karthik)
    if k_account is not None:
        main = await bank(k_account, karthik.full_name, "4821", 210)
        k_sip = (await s.execute(select(Sip).where(Sip.firm_id == firm.id, Sip.account_id == k_account.id).order_by(Sip.starts_on))).scalars().first()
        mandate = next((m for m in await sip_mandates.standing(s, firm.id, [k_account.id]) if m.reference == "UMRN-DEMO-IYER-0001"), None)
        if mandate is not None and k_sip is not None:
            debit_day = sip.next_instalment(k_sip.day_of_month, k_sip.starts_on, k_sip.starts_on)
            if debit_day <= today:
                n += await where_it_stands(india_money.record_first_debit(
                    s, firm, UserRole.OPERATIONS, by=BY, account_id=k_account.id, reference=mandate.reference, outcome="confirmed", debited_on=debit_day,
                    amount=float(k_sip.amount), document=f"Bank's debit confirmation, filed ({SOURCE})"))
        n += await where_it_stands(india_money.record_credit(
            s, firm, UserRole.OPERATIONS, by=BY, account_id=k_account.id, rail="neft", amount=250_000, bank_link_id=main.id, payer_name="KARTHIK IYER",
            reference="NEFT-DEMO-000101", on=today - timedelta(days=9), evidence=f"Bank's credit advice, filed ({SOURCE})"))
        if k_sip is not None:
            n += await where_it_stands(india_money.record_exception(
                s, firm, UserRole.OPERATIONS, by=BY, account_id=k_account.id, kind="employer_payroll", payer_name=EMPLOYER, bank=BANK, last4="9001",
                evidence=f"Employer's payroll deduction letter, filed ({SOURCE})"))
            n += await where_it_stands(india_money.record_credit(
                s, firm, UserRole.OPERATIONS, by=BY, account_id=k_account.id, rail="neft", amount=float(k_sip.amount), payer_name=EMPLOYER, payer_last4="9001",
                reference="NEFT-DEMO-000102", on=today - timedelta(days=4), evidence=f"Bank's credit advice, filed ({SOURCE})"))
        await bank(k_account, karthik.full_name, CHANGED_BANK_LAST4, CHANGED_BANK_AGE, name=OTHER_BANK)

    rajesh, meera, subir = people.get("Rajesh Sharma"), people.get("Meera Sharma"), people.get("Subir Banerjee")
    r_account = await _account_of(s, firm, rajesh)
    if r_account is not None and await sip_on(r_account, 15_000, 7, 12) is not None:
        await bank(r_account, rajesh.full_name, "2204", 40)
        asked, failed = FAILED["UPI-REQ-DEMO-SHARMA-1"]
        terms = dict(account_id=r_account.id, reference="UPI-REQ-DEMO-SHARMA-1", document=f"Mandate request, filed ({SOURCE})")
        await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, stage="requested", mandate_type="upi_autopay", per_debit_limit=15_000,
                                  starts_on=today - timedelta(days=asked), last4="2204", on=today - timedelta(days=asked), **terms)
        await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, stage="failed", on=today - timedelta(days=failed),
                                  reason="The bank returned the request: the account could not be set up for UPI Autopay (demonstration)",
                                  **{**terms, "document": f"Bank's return notice, filed ({SOURCE})"})
        n += 2

    m_account = await _account_of(s, firm, meera)
    if m_account is not None and await sip_on(m_account, 10_000, 10, 15) is not None:
        await bank(m_account, meera.full_name, "5532", 40)
        asked = REQUESTED["NACH-REQ-DEMO-SHARMA-2"]
        await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, account_id=m_account.id, reference="NACH-REQ-DEMO-SHARMA-2", stage="requested",
                                  mandate_type="nach", per_debit_limit=10_000, starts_on=today - timedelta(days=asked), last4="5532",
                                  on=today - timedelta(days=asked), document=f"Mandate request, filed ({SOURCE})")
        n += 1

    s_account = await _account_of(s, firm, subir)
    if s_account is not None:
        row = await sip_on(s_account, 5_000, (today - timedelta(days=4)).day if (today - timedelta(days=4)).day <= 28 else 28, 40)
        if row is not None:
            await bank(s_account, subir.full_name, "8846", 60)
            await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, account_id=s_account.id, reference="UMRN-DEMO-BANERJEE-0001", mandate_type="nach",
                                      per_debit_limit=10_000, starts_on=today - timedelta(days=6), last4="8846", document=f"NACH registration, filed ({SOURCE})")
            first = sip.next_instalment(row.day_of_month, today - timedelta(days=6), today - timedelta(days=6))
            if first <= today:
                await india_money.record_first_debit(s, firm, UserRole.OPERATIONS, by=BY, account_id=s_account.id, reference="UMRN-DEMO-BANERJEE-0001",
                                                     outcome="failed", debited_on=first, amount=5_000, reason="Insufficient funds (demonstration)",
                                                     document=f"Bank's debit return, filed ({SOURCE})")
                n += 1
            n += 1

    vikram = people.get("Vikram Menon")
    v_account = await _account_of(s, firm, vikram)
    if v_account is not None:
        nro = await bank(v_account, vikram.full_name, "6650", 90)
        n += await where_it_stands(india_money.record_remittance(
            s, firm, UserRole.OPERATIONS, by=BY, account_id=v_account.id, bank_link_id=nro.id, account_kind="nro", received_on=today - timedelta(days=18),
            amount_inr=600_000, certificate_reference="FIRC-DEMO-000045", remitter_name=vikram.full_name, foreign_amount=26_000, foreign_currency="AED",
            document=f"Bank's inward remittance certificate, filed ({SOURCE})"))

    # What is already past its chase line is a task, once.
    await s.flush()
    for entry in await india_money.queue(s, firm):
        if entry["overdue"] and entry["household_id"]:
            await india_reminders.raise_items(s, firm, uuid.UUID(entry["household_id"]), [india_money.chase_item(entry, today)])
    await s.flush()
    return n


async def redate(s: AsyncSession, firm: Firm, today) -> int:
    """Keep the demonstration's request, failure and cooling-off the same distance from today (called by `india_redate`)."""
    moved = 0
    rows = {r.reference: r for r in await sip_mandates.standing(s, firm.id) if r.recorded_by == BY}
    wanted = {**{ref: ("requested", days) for ref, days in REQUESTED.items()}, **{ref: ("failed", days[1]) for ref, days in FAILED.items()}}
    for ref, (kind, days) in wanted.items():
        row = rows.get(ref)
        target = today - timedelta(days=days)
        if row is not None and row.kind == kind and row.event_on is not None and row.event_on != target:
            row.event_on = target
            moved += 1
    changed = (await s.execute(select(BankLink).where(BankLink.firm_id == firm.id, BankLink.recorded_by == BY, BankLink.last4 == CHANGED_BANK_LAST4,
                                                      BankLink.status == "verified"))).scalars().first()
    if changed is not None and changed.verified_on != today - timedelta(days=CHANGED_BANK_AGE):
        # Still recorded after the account's first bank, which is what makes it a change.
        first = (await s.execute(select(BankLink.created_at).where(BankLink.account_id == changed.account_id, BankLink.id != changed.id)
                                 .order_by(BankLink.created_at))).scalars().first()
        when = utcnow() - timedelta(days=CHANGED_BANK_AGE)
        if first is not None:
            when = max(when, (first if first.tzinfo else first.replace(tzinfo=timezone.utc)) + timedelta(seconds=1))
        changed.verified_on, changed.created_at = today - timedelta(days=CHANGED_BANK_AGE), when
        moved += 1
    return moved
