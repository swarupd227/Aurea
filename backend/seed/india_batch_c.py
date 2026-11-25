"""India batch C on the demonstration firm: mutual fund orders with their rails and NAV days, the firm's group and what it earns from an advised
household, two schemes' expense ratios, the adviser's NISM certificates and advertisement pre-approvals (B-294, B-297, B-298).

Every record is invented for the demonstration firm and says so in its evidence. Dates are days before the firm's today, so nothing ages. Run from
`seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on a firm built before it; it adds nothing where a group entity is on file.

  Karthik Iyer      four orders in the Parag Parikh Flexi Cap direct plan: one on its cut-off day, one the registrar allotted a day later than the rule
                    gives (a break, reported and not repaired), a large purchase whose NAV follows the day its funds were realised, and a large
                    purchase with no realisation day on file ("not assessed")
  The group         a subsidiary that holds an ARN (invented) and a parent that holds none; the subsidiary earns trail from the first household the
                    Investment Adviser advises, on a statement compliance recorded: a flag for compliance, not a finding
  Expense ratios    two schemes' direct and regular plans, and a third scheme with only its direct plan (the comparison reads "not assessed")
  The adviser       X-A valid, X-B due within 90 days
  Marketing copy    one piece approved, one rejected for promising a return, one waiting for compliance
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import ad_approval, adviser_certification, distribution_separation as ds, firm_time, mf_orders
from app.core.security import UserRole
from app.models.enums import OrderSide, OrderStatus
from app.models.graph import Account, Mandate, Person
from app.models.identity import User
from app.models.india_batch_c import GroupEntity
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.models.trading import Order

SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India batch C (demonstration)"
FUND = "PPFAS-FLEXI-DIRECT"


def _weekday(day: date) -> date:
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _next_weekday(day: date) -> date:
    day += timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


async def _account_of(s: AsyncSession, firm: Firm, person: Person | None) -> Account | None:
    if person is None:
        return None
    return (await s.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                            .where(Mandate.firm_id == firm.id, Mandate.person_id == person.id).order_by(Account.created_at))).scalars().first()


async def _order(s: AsyncSession, firm: Firm, account: Account, inst: Instrument, day: date, amount: float, price: float) -> Order:
    qty = round(amount / price, 3)
    o = Order(firm_id=firm.id, account_id=account.id, instrument_id=inst.id, mandate_id=account.mandate_id, side=OrderSide.BUY, status=OrderStatus.FILLED, quantity=qty,
              est_price=price, est_value=amount, filled_quantity=qty, avg_fill_price=price, fees=0, venue="demonstration",
              reason=f"A demonstration purchase ({SOURCE})", created_at=datetime(day.year, day.month, day.day, 6, 0, tzinfo=timezone.utc))
    s.add(o)
    await s.flush()
    return o


async def add(s: AsyncSession, firm: Firm) -> int:
    if (await s.execute(select(GroupEntity.id).where(GroupEntity.firm_id == firm.id).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    users = {u.role: u for u in (await s.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    people = {p.full_name: p for p in (await s.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()}
    inst = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    n = 0

    # B-294: four mutual fund orders and how each travelled.
    account = await _account_of(s, firm, people.get("Karthik Iyer"))
    fund = inst.get(FUND)
    if account is not None and fund is not None:
        d1, d2, d3, d4 = (_weekday(today - timedelta(days=k)) for k in (24, 18, 12, 6))
        evidence = f"Rail and registrar confirmation, filed ({SOURCE})"
        o1 = await _order(s, firm, account, fund, d1, 10_000, 70)
        await mf_orders.record(s, firm, UserRole.OPERATIONS, by=BY, order_id=o1.id, rail="bse_star_mf", rta="cams", cutoff_rule="standard", order_on=d1, order_time="10:30",
                               nav_date_reported=d1, folio="DEMO-1234567/01", amount_inr=10_000, document=evidence)
        o2 = await _order(s, firm, account, fund, d2, 25_000, 70)
        await mf_orders.record(s, firm, UserRole.OPERATIONS, by=BY, order_id=o2.id, rail="nse_nmf", rta="cams", cutoff_rule="standard", order_on=d2, order_time="14:50",
                               nav_date_reported=_next_weekday(d2), folio="DEMO-1234567/01", amount_inr=25_000, document=evidence)
        o3 = await _order(s, firm, account, fund, d3, 500_000, 70)
        await mf_orders.record(s, firm, UserRole.OPERATIONS, by=BY, order_id=o3.id, rail="mfcentral", rta="cams", cutoff_rule="standard", order_on=d3, order_time="11:00",
                               funds_realised_on=_next_weekday(d3), nav_date_reported=_next_weekday(d3), folio="DEMO-1234567/01", amount_inr=500_000, document=evidence)
        o4 = await _order(s, firm, account, fund, d4, 300_000, 70)
        await mf_orders.record(s, firm, UserRole.OPERATIONS, by=BY, order_id=o4.id, rail="amc_direct", rta="cams", cutoff_rule="standard", order_on=d4, order_time="11:00",
                               nav_date_reported=d4, folio="DEMO-1234567/01", amount_inr=300_000, document=evidence)
        n += 4

    # B-297: the group, trail from an advised household, and two schemes' expense ratios.
    doc = f"The group structure chart, filed ({SOURCE})"
    await ds.record_entity(s, firm, UserRole.COMPLIANCE, by=BY, name="Demonstration Distribution Private Limited (invented)", relationship="subsidiary", holds_arn=True,
                           arn="ARN-000000 (invented for the demonstration)", document=doc)
    await ds.record_entity(s, firm, UserRole.COMPLIANCE, by=BY, name="Demonstration Holdings Limited (invented)", relationship="parent", holds_arn=False, document=doc)
    n += 2
    advised = await ds._advised_households(s, firm)
    if advised:
        home_id = sorted(advised, key=lambda h: advised[h])[0]
        await ds.record_trail(s, firm, UserRole.COMPLIANCE, by=BY, household_id=home_id, entity="Demonstration Distribution Private Limited (invented)",
                              statement_on=today - timedelta(days=40), amount_inr=18_500,
                              document=f"The group company's trail statement, filed ({SOURCE})")
        n += 1
    ter_doc = f"Scheme information document, filed ({SOURCE}; the ratios are invented)"
    for scheme, plan, ter in (("Parag Parikh Flexi Cap Fund", "direct", 0.63), ("Parag Parikh Flexi Cap Fund", "regular", 1.35),
                              ("UTI Nifty 50 Index Fund", "direct", 0.20), ("UTI Nifty 50 Index Fund", "regular", 0.45),
                              ("HDFC Corporate Bond Fund", "direct", 0.38)):
        await ds.record_ter(s, firm, UserRole.RESEARCH_CIO, by=BY, scheme=scheme, plan=plan, ter_pct=ter, as_of=today - timedelta(days=60), document=ter_doc)
        n += 1

    # B-298: the adviser's certificates, and three pieces of marketing copy.
    adviser, compliance = users.get(UserRole.ADVISER), users.get(UserRole.COMPLIANCE)
    if adviser is not None:
        cdoc = f"NISM certificate, filed ({SOURCE})"
        await adviser_certification.record(s, firm, UserRole.COMPLIANCE, by=BY, user_id=adviser.id, kind="nism_xa", certificate_number="DEMO-XA-0001",
                                           issued_on=today - timedelta(days=600), expires_on=today + timedelta(days=495), document=cdoc)
        await adviser_certification.record(s, firm, UserRole.COMPLIANCE, by=BY, user_id=adviser.id, kind="nism_xb", certificate_number="DEMO-XB-0001",
                                           issued_on=today - timedelta(days=1035), expires_on=today + timedelta(days=60), document=cdoc)
        n += 2
    if adviser is not None and compliance is not None:
        ok = await ad_approval.submit(s, firm, adviser, title="Retirement planning evening (demonstration)", channel="email",
                                      text="Join our evening on planning for retirement. Investments in the securities market are subject to market risks. "
                                           "Past performance may not be sustained. Registration granted by SEBI is no guarantee of performance. Registration number INA000000000.")
        await ad_approval.decide(s, firm, compliance, submission_id=ok["submissions"][0]["id"], decision="approved",
                                 reason="Wording read against the advertisement check and the firm's template: approved as submitted (demonstration).")
        bad = await ad_approval.submit(s, firm, adviser, title="Returns campaign (demonstration)", channel="social",
                                       text="Our portfolios give guaranteed returns of 18% a year. Act now, this is the best offer.")
        await ad_approval.decide(s, firm, compliance, submission_id=bad["submissions"][0]["id"], decision="rejected",
                                 reason="Promises a return and states one with no verified basis (demonstration).")
        await ad_approval.submit(s, firm, adviser, title="A guide to goals-based planning (demonstration)", channel="website",
                                 text="A short guide to setting goals, and how a plan may change as your life does. Investments in the securities market are subject to market risks.")
        n += 3
    # A certificate due within 90 days is a task for compliance, as the polish seed does for the dates it holds (once; a second run raises nothing).
    from app.aurea_core import india_reminders

    await india_reminders.raise_due(s, firm)
    await s.flush()
    return n
