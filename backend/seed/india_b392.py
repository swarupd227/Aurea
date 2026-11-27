"""What the onboarding measures need on the demonstration firm (B-392): accounts opened through onboarding in each cohort, one still
waiting for its holder's signature, the exceptions a person worked on them, and a minor whose eighteenth birthday is within the year.

Screening hits, rescreen backlog and reports to FIU-IND are the PMLA programme's (B-390, `seed/india_pmla.py`): this module writes no
screened party, decision, investigation or report, so no household has two.

Every record is invented for the demonstration firm and says so. Dates are days before the firm's today. Run from `seed.india_demo`
when the firm is built and from `seed.india_demo_backfill` on a firm built before; each part is added once. A later run moves this
module's own rows forward by the days since they were last dated, so the open case stays within days of its signature request and the
board does not read every opened account as stuck. A case a person has since moved on is left as it stands.

  Rajesh Sharma          resident, opened the same day, no exception
  Rajesh Sharma HUF      entity, opened in 4 days, no exception
  Elaine D'Souza         resident, a signature missing and a rejected demat instruction before the second one settled
  Karthik Iyer           resident, no exception
  Vikram Menon           non-resident (NRO), video verification done by hand
  Kapoor Family Trust    entity, opened in 19 days, no exception
  Meera Sharma           a joint account waiting for her signature (on the client's page)
  Diya Iyer              seventeen: the conversion task a year ahead
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, guardianship, identity_checks, india_deadlines, india_reminders
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.enums import OrderSide, OrderStatus
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.onboarding import OnboardingCase, TransferRequest
from app.models.portfolio import Instrument, Price
from app.models.tenant import Firm
from app.models.trading import Order

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India onboarding measures (demonstration)"
MARK = "b392_dated"                      # on a case's intake: the day its dates were last set
FUND = "GILT5YBEES.NS"
MINOR = "Diya Iyer"
# holder, account type, days since the case was opened, days to the account being opened, what a person had to work
OPENED = (
    ("Rajesh Sharma", "individual", 40, 0.2, ()),
    ("Rajesh Sharma HUF", "huf", 38, 4.0, ()),
    ("Elaine D'Souza", "individual", 33, 6.0, ("nigo", "rejected_instruction")),
    ("Karthik Iyer", "individual", 31, 2.0, ()),
    ("Vikram Menon", "nri_nro", 45, 12.0, ("video_by_hand",)),
    ("Kapoor Family Trust", "trust", 52, 19.0, ()),
)
WAITING = ("Meera Sharma", "joint_anyone", 4)


async def _cases(s: AsyncSession, firm: Firm, today: date) -> int:
    if (await s.execute(select(OnboardingCase.id).where(OnboardingCase.firm_id == firm.id, OnboardingCase.notes == BY).limit(1))).first():
        return 0
    now = utcnow()
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    entities = {e.name: e for e in (await s.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id))).scalars().all()}
    fund = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == FUND))).scalars().first()
    price = (await s.execute(select(Price.close).where(Price.firm_id == firm.id, Price.instrument_id == fund.id)
                             .order_by(Price.as_of.desc()))).scalars().first() if fund is not None else None
    n = 0
    for name, registration, started, took, worked in OPENED:
        person, entity = people.get(name), entities.get(name)
        holder = person or entity
        if holder is None:
            continue
        mine = Mandate.person_id == person.id if person is not None else Mandate.entity_id == entity.id
        account = (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                                   .where(Mandate.firm_id == firm.id, mine).order_by(Account.created_at, Account.name))).scalars().first()
        if account is None:
            continue
        start = now - timedelta(days=started)
        opened = start + timedelta(days=took)
        made = {"person_id": str(person.id)} if person is not None else {"entity_id": str(entity.id)}
        case = OnboardingCase(
            firm_id=firm.id, prospect_name=name, is_entity=entity is not None, registration_type=registration, segment=(
                (await s.get(Household, holder.household_id)).segment), status="approved", sla_days=30, notes=BY, created_at=start,
            intake={"household_id": str(holder.household_id), MARK: today.isoformat()},
            materialized={**made, "household_id": str(holder.household_id), "account_id": str(account.id)},
            agreement_status="signed", agreement_sent_at=start + timedelta(days=took * 0.3), agreement_signed_at=start + timedelta(days=took * 0.5),
            activated_at=start + timedelta(days=took * 0.8), custodian_name=account.custodian, custodian_push_status="active",
            custodian_push_at=opened, cip_status="verified")
        if "nigo" in worked:
            case.nigo_root_cause = "missing_signature"      # put right before the account opened, so no longer flagged
        s.add(case)
        await s.flush()
        n += 1
        settled = opened + timedelta(days=1)
        if "rejected_instruction" in worked:
            s.add(TransferRequest(firm_id=firm.id, case_id=case.id, transfer_type="demat", direction="in", status="failed",
                                  reject_reason="Signature on the delivery instruction does not match (demonstration)",
                                  initiated_at=opened + timedelta(hours=2), notes=SOURCE, created_at=opened))
            settled = opened + timedelta(days=3)
        s.add(TransferRequest(firm_id=firm.id, case_id=case.id, transfer_type="demat", direction="in", status="settled",
                              initiated_at=opened + timedelta(hours=3), settled_at=settled, notes=SOURCE, created_at=opened))
        if price:
            qty = round(10_000 / float(price), 3)
            s.add(Order(firm_id=firm.id, account_id=account.id, instrument_id=fund.id, mandate_id=account.mandate_id, side=OrderSide.BUY,
                        status=OrderStatus.FILLED, quantity=qty, est_price=float(price), est_value=10_000, filled_quantity=qty,
                        avg_fill_price=float(price), fees=0, venue="demonstration", reason=f"First purchase after onboarding. {BY}",
                        created_at=settled + timedelta(days=2)))
        if "video_by_hand" in worked and person is not None:
            try:
                await identity_checks.record_result(s, firm, UserRole.OPERATIONS, by=BY, kind="vipv", person_id=person.id, case_id=case.id,
                                                    outcome="passed", document=f"In-person verification record signed by the officer ({SOURCE})")
            except identity_checks.CheckError:
                pass          # what a person has since recorded on the deployed firm stands

    name, registration, started = WAITING
    holder = people.get(name)
    if holder is not None:
        start = now - timedelta(days=started)
        s.add(OnboardingCase(
            firm_id=firm.id, prospect_name=name, registration_type=registration, segment=(await s.get(Household, holder.household_id)).segment,
            status="intake", sla_days=10, notes=BY, created_at=start, intake={"household_id": str(holder.household_id), MARK: today.isoformat()},
            agreement_status="sent", agreement_sent_at=now - timedelta(days=2)))
        n += 1
    return n


async def _minor(s: AsyncSession, firm: Firm, today: date) -> int:
    if (await s.execute(select(Person.id).where(Person.firm_id == firm.id, Person.full_name == MINOR))).first():
        return 0
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    father = people.get("Karthik Iyer")
    if father is None:
        return 0
    born = today - timedelta(days=17 * 365 + 4 + 150)          # about seven months short of eighteen
    diya = Person(firm_id=firm.id, household_id=father.household_id, full_name=MINOR, preferred_name="Diya", date_of_birth=born,
                  segment=father.segment, kyc={"demonstration": True}, profile={"tax": {}},
                  is_next_gen=True)      # a child: without it the retirement plan counts her as an adult and runs to her plan-to age
    s.add(diya)
    await s.flush()
    await guardianship.record(s, firm, by=BY, minor_person_id=diya.id, guardian_person_id=father.id, guardian_name=father.full_name,
                              relationship="father", document=f"Birth certificate naming the father ({SOURCE})")
    items = [i for i in (await india_deadlines.household_items(s, firm, father.household_id, nominations=False))["items"]
             if i["kind"] == "majority" and MINOR in i["label"]]
    return 2 + len(await india_reminders.raise_items(s, firm, father.household_id, items))


async def _redate(s: AsyncSession, firm: Firm, today: date) -> int:
    """Move this module's own rows forward by the days since they were last dated."""
    moved = 0
    for case in (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id, OnboardingCase.notes == BY))).scalars().all():
        dated = (case.intake or {}).get(MARK)
        if not dated:
            continue
        by = today - date.fromisoformat(dated)
        if by <= timedelta(0):
            continue
        waiting = case.materialized in (None, {})
        if waiting and (case.agreement_signed_at is not None or str(case.status) in ("approved", "rejected")):
            continue          # a person has moved it on: it is theirs
        for field in ("created_at", "agreement_sent_at", "agreement_signed_at", "activated_at", "custodian_push_at"):
            value = getattr(case, field)
            if value is not None:
                setattr(case, field, value + by)
        case.intake = {**case.intake, MARK: today.isoformat()}
        for t in (await s.execute(select(TransferRequest).where(TransferRequest.case_id == case.id, TransferRequest.notes == SOURCE))).scalars().all():
            for field in ("created_at", "initiated_at", "settled_at"):
                if getattr(t, field) is not None:
                    setattr(t, field, getattr(t, field) + by)
        account = (case.materialized or {}).get("account_id")
        if account:
            import uuid

            for o in (await s.execute(select(Order).where(Order.account_id == uuid.UUID(account), Order.reason.like(f"%{BY}")))).scalars().all():
                o.created_at = o.created_at + by
        moved += 1
    return moved


async def add(s: AsyncSession, firm: Firm) -> int:
    today = firm_time.today(firm)
    n = await _redate(s, firm, today)
    n += await _cases(s, firm, today)
    n += await _minor(s, firm, today)
    await s.flush()
    return n
