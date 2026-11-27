"""India's agreements, disclosures and the NRI checklist on the demonstration firm (B-391).

Every record is invented for the demonstration firm and says so. The fund houses on the acceptance list are invented ones:
no real fund house is said to accept or refuse anyone. Run from `seed.india_demo_backfill`; it adds nothing where its own
records, or a person's, are already on file, and moves an unpaid demonstration drawdown's date ahead on a later run. As the
Mehta seed does, each part is written inside its own savepoint: a part that is refused is undone alone, reported, and tried
again on a later run, and the other parts go on.

  The Pintos      an NRI living in the United States, recorded as meaning to send his money abroad again with only an NRO
                  account (a flag); an invented fund house that does not take US residents (a purchase is refused) and one
                  that takes them on conditions; two regular-plan transactions, one with its EUIN and one without; a power
                  of attorney wider than a Portfolio Manager may take; a family trust case whose beneficial owners are
                  recorded as words and not yet as parties; an NRO account case for a client who means to repatriate
  Karthik Iyer    the Portfolio Manager papers, scheme documents (one given), a demat pack, an e-stamped agreement and an
                  Alternative Investment Fund commitment with one drawdown paid and one unpaid (a task)
  Rajesh Sharma   a demat pack with gaps
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import inspect as inspect_row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import (
    document_matrix, firm_time, fund_acceptance, india_agreements, kyc_india, onboarding_start, pms, residency,
)
from app.core.logging import get_logger
from app.core.security import UserRole
from app.models.enums import AssetClass, ClientSegment, MandateType, MarketType, OrderSide, OrderStatus, PartyRole
from app.models.graph import Account, Household, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.india_agreements import (
    AgreementItem, AifCommitment, AifDrawdown, AifPayment, DistributionDisclosure, EStampRecord, FundAcceptance, SchemeDocument,
)
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.portfolio import Instrument, ModelPortfolio, Price
from app.models.tenant import Firm
from app.models.trading import Order

log = get_logger("aurea.seed.india.agreements")
SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: india agreements (demonstration)"
HOUSEHOLD = "The Pinto NRI Household"
TRUST_CASE = "The Pinto Family Trust"
NRI_CASE = "Neha Pinto"
CERTIFICATE = "IN-DEMO-KA-000391"
AIF_FUND = "Malabar Demonstration Opportunities Fund (invented)"
NORTH = "Narmada Demonstration Mutual Fund (invented)"
LOTUS = "Lotusgate Demonstration Mutual Fund (invented)"
# Invented schemes of the invented fund houses. Their price is a stated placeholder, so nothing ever fills on it.
SCHEMES = {
    "NARMADA-FLEXI-DEMO": ("Narmada Flexi Cap Fund (invented), direct", NORTH, "direct"),
    "LOTUSGATE-FLEXI-DEMO": ("Lotusgate Flexi Cap Fund (invented), regular", LOTUS, "regular"),
}


async def _instruments(s: AsyncSession, firm: Firm, today: date) -> tuple[dict, int]:
    have = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    added = 0
    for sym, (name, house, plan) in SCHEMES.items():
        if sym in have:
            continue
        inst = Instrument(firm_id=firm.id, symbol=sym, name=name, asset_class=AssetClass.EQUITY, market_type=MarketType.PUBLIC,
                          currency="INR", market_symbol=None, values_tags={"amc": house, "fund_plan": plan, "demonstration": True,
                                                                           "tax_class": "equity_oriented"})
        s.add(inst)
        await s.flush()
        s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=today, close=25.0, currency="INR", source="synthetic", is_real=False))
        have[sym] = inst
        added += 1
    return have, added


async def _acceptance(s: AsyncSession, firm: Firm, today: date) -> int:
    if (await s.execute(select(FundAcceptance.id).where(FundAcceptance.firm_id == firm.id, FundAcceptance.recorded_by == BY).limit(1))).first():
        return 0
    rows = [
        (NORTH, "US", "refuses", None),
        (NORTH, "CA", "conditions", "Only from an NRE or NRO account, with a signed FATCA and CRS declaration (invented condition)"),
        (LOTUS, "US", "conditions", "Applications on paper only, with a signed US-person declaration (invented condition)"),
        (LOTUS, "CA", "accepts", None),
    ]
    for house, country, stance, conditions in rows:
        await fund_acceptance.record(s, firm, UserRole.OPERATIONS, by=BY, fund_house=house, country=country, stance=stance,
                                     conditions=conditions, stated_on=today - timedelta(days=45),
                                     document=f"The invented fund house's offer document, filed ({SOURCE})")
    return len(rows)


async def _pintos(s: AsyncSession, firm: Firm, today: date, inst: dict, users: dict) -> int:
    if (await s.execute(select(Household.id).where(Household.firm_id == firm.id, Household.name == HOUSEHOLD))).first():
        return 0
    adviser = users.get(UserRole.ADVISER)
    model = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id,
                                                          ModelPortfolio.name == "Core Balanced"))).scalar_one_or_none()
    household = Household(firm_id=firm.id, name=HOUSEHOLD, segment=ClientSegment.PRIVATE_WEALTH, values={"jurisdiction": "IN"})
    s.add(household)
    await s.flush()
    rohan = Person(firm_id=firm.id, household_id=household.id, full_name="Rohan Pinto", preferred_name="Rohan",
                   date_of_birth=today.replace(year=today.year - 41, month=3, day=12), segment=ClientSegment.PRIVATE_WEALTH,
                   kyc={"demonstration": True}, profile={"risk_profile": "balanced", "tax": {}})
    s.add(rohan)
    await s.flush()
    if adviser is not None:
        s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="person", to_id=rohan.id))
        s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id, to_type="household", to_id=household.id))
    mandate = Mandate(firm_id=firm.id, person_id=rohan.id, name="Rohan Pinto - Growth", mandate_type=MandateType.DISCRETIONARY,
                      suitability={"risk_profile": "growth", "capacity_for_loss": "high", "values_exclusions": []},
                      constraints={"service_track": "pms_discretionary"}, model_portfolio_id=model.id if model else None)
    s.add(mandate)
    await s.flush()
    account = Account(firm_id=firm.id, mandate_id=mandate.id, name="Rohan Pinto NRO demat", custodian="CDSL", currency="INR",
                      cash_balance=1_500_000, registration_type="nri_nro")
    s.add(account)
    await s.flush()
    await residency.record(s, firm, by=BY, person_id=rohan.id, document=f"Passport, US residence card and signed FATCA and CRS form ({SOURCE})",
                           fema_status="nri", country_of_residence="United States", tax_residencies=["United States"], us_person=True,
                           indian_citizen_or_pio=True, days_in_india=22, days_prior_four_years=95, accounts=["NRO"],
                           self_certified_on=today - timedelta(days=50), repatriation_intent="repatriable")
    # Two transactions in a regular plan from before the firm advised him, one carrying its EUIN and one not.
    lotus = inst.get("LOTUSGATE-FLEXI-DEMO")
    orders = []
    for back in (75, 40):
        day = today - timedelta(days=back)
        o = Order(firm_id=firm.id, account_id=account.id, instrument_id=lotus.id, mandate_id=mandate.id, side=OrderSide.BUY,
                  status=OrderStatus.FILLED, quantity=2000, est_price=25, est_value=50_000, filled_quantity=2000, avg_fill_price=25,
                  fees=0, venue="demonstration", reason=f"A demonstration purchase ({SOURCE})",
                  created_at=datetime(day.year, day.month, day.day, 6, 0, tzinfo=timezone.utc))
        s.add(o)
        orders.append(o)
    await s.flush()
    await india_agreements.record_euin(s, firm, UserRole.OPERATIONS, by=BY, order_id=orders[0].id, euin="E000001", arn="ARN-000000",
                                       document=f"Transaction confirmation ({SOURCE})")
    await india_agreements.record_item(s, firm, UserRole.OPERATIONS, by=BY, relationship="pms", mandate_id=mandate.id, item="poa_scope",
                                       powers=["operate_demat", "debit_fees", "Open and close bank accounts"],
                                       dated_on=today - timedelta(days=90), document=f"Power of attorney ({SOURCE})")
    await india_agreements.record_item(s, firm, UserRole.OPERATIONS, by=BY, relationship="pms", mandate_id=mandate.id, item="upfront_fee",
                                       answer="none", dated_on=today - timedelta(days=90), document=f"Fee annexure ({SOURCE})")
    return 8


async def _cases(s: AsyncSession, firm: Firm, today: date, users: dict) -> int:
    adviser = users.get(UserRole.ADVISER)
    if adviser is None:
        return 0
    have = {c.prospect_name for c in (await s.execute(select(OnboardingCase).where(OnboardingCase.firm_id == firm.id))).scalars().all()}
    added = 0
    if TRUST_CASE not in have:
        case = await onboarding_start.start(s, firm, actor=adviser, prospect_name=TRUST_CASE, registration_type="trust")
        s.add(OnboardingParty(firm_id=firm.id, case_id=case.id, role=PartyRole.TRUSTEE, legal_name="Rohan Pinto",
                              screening_status="not_screened", notes=SOURCE))
        await s.flush()
        await kyc_india.record(s, firm, by=BY, case_id=case.id, party_name=TRUST_CASE, structure="trust",
                               document=f"Trust deed ({SOURCE})",
                               beneficial_owners_text="Joseph Pinto, , author; Rohan Pinto, , trustee; Maria Pinto, 40, beneficiary; "
                                                      "Anil Rao, , protector")
        added += 1
    if NRI_CASE not in have:
        case = await onboarding_start.start(s, firm, actor=adviser, prospect_name=NRI_CASE, registration_type="nri_nro")
        await document_matrix.record_detail(s, firm, by=BY, case_id=case.id, documents=None, evidence=f"NRI intake form ({SOURCE})",
                                            fields={"country_of_residence": "Canada", "repatriation_intent": "repatriable"})
        added += 1
    return added


async def _people(s: AsyncSession, firm: Firm, name: str) -> tuple:
    """A demonstration person, their first active mandate and its first account, or Nones."""
    person = (await s.execute(select(Person).where(Person.firm_id == firm.id, Person.full_name == name))).scalar_one_or_none()
    if person is None:
        return None, None, None
    mandate = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id == person.id,
                                                     Mandate.is_active.is_(True)).order_by(Mandate.name))).scalars().first()
    account = (await s.execute(select(Account).where(Account.mandate_id == mandate.id)
                               .order_by(Account.created_at))).scalars().first() if mandate else None
    return person, mandate, account


async def _any_item(s: AsyncSession, firm: Firm, **where) -> bool:
    """Whether anyone has recorded a paper there: where a person has, the seed writes nothing."""
    return (await s.execute(select(AgreementItem.id).where(
        AgreementItem.firm_id == firm.id, *[getattr(AgreementItem, k) == v for k, v in where.items()]).limit(1))).first() is not None


def _doc(what: str) -> str:
    return f"{what} ({SOURCE})"


async def _pms_papers(s: AsyncSession, firm: Firm) -> int:
    karthik, mandate, _ = await _people(s, firm, "Karthik Iyer")
    agreement = await pms.standing(s, firm.id, mandate.id) if mandate else None
    if agreement is None or await _any_item(s, firm, relationship_kind="pms", mandate_id=mandate.id):
        return 0
    signed, ops = agreement.signed_on, UserRole.OPERATIONS
    for item, answer, when in (("disclosure_document", "given", signed - timedelta(days=7)), ("direct_onboarding", "offered", signed - timedelta(days=7)),
                               ("upfront_fee", "none", signed)):
        await india_agreements.record_item(s, firm, ops, by=BY, relationship="pms", mandate_id=mandate.id, item=item, answer=answer,
                                           dated_on=when, document=_doc("PMS onboarding file"))
    await india_agreements.record_item(s, firm, ops, by=BY, relationship="pms", mandate_id=mandate.id, item="poa_scope",
                                       powers=["operate_demat", "debit_fees"], dated_on=signed, document=_doc("Power of attorney"))
    if (await s.execute(select(EStampRecord.id).where(EStampRecord.firm_id == firm.id,
                                                      EStampRecord.certificate_number == CERTIFICATE).limit(1))).first():
        return 4
    await india_agreements.record_estamp(s, firm, ops, by=BY, household_id=karthik.household_id, instrument_kind="pms_agreement",
                                         certificate_number=CERTIFICATE, state="Karnataka", duty=500, stamped_on=signed,
                                         parties="Karthik Iyer and Astra for Wealth India (demonstration)", document=_doc("E-stamp certificate"))
    return 5


async def _demat_pack(s: AsyncSession, firm: Firm, today: date, name: str, items: tuple, days: int) -> int:
    _, _, account = await _people(s, firm, name)
    if account is None or not india_agreements.is_demat(account) or await _any_item(s, firm, relationship_kind="broking", account_id=account.id):
        return 0
    for item in items:
        answer = "signed" if item in ("account_opening_form", "demat_agreement") else "given"
        await india_agreements.record_item(s, firm, UserRole.OPERATIONS, by=BY, relationship="broking", account_id=account.id, item=item,
                                           answer=answer, dated_on=today - timedelta(days=days), document=_doc("Demat opening pack"))
    return len(items)


async def _aif(s: AsyncSession, firm: Firm, today: date) -> int:
    karthik, _, _ = await _people(s, firm, "Karthik Iyer")
    if karthik is None or (await s.execute(select(AifCommitment.id).where(
            AifCommitment.firm_id == firm.id, AifCommitment.fund_name == AIF_FUND).limit(1))).first():
        return 0
    ops = UserRole.OPERATIONS
    held = await india_agreements.record_commitment(s, firm, ops, by=BY, person_id=karthik.id, fund_name=AIF_FUND, category="aif_2",
                                                    commitment=15_000_000, committed_on=today - timedelta(days=200),
                                                    document=_doc("Contribution agreement"))
    cid = next(c["commitment_id"] for c in held["aif"] if c["fund"] == AIF_FUND)
    for item, answer in (("placement_memorandum", "received"), ("contribution_agreement", "signed"), ("investor_consent", "given")):
        await india_agreements.record_item(s, firm, ops, by=BY, relationship="aif", commitment_id=cid, item=item, answer=answer,
                                           dated_on=today - timedelta(days=200), document=_doc("Fund onboarding file"))
    first = await india_agreements.record_drawdown(s, firm, ops, by=BY, commitment_id=cid, amount=3_000_000, notice_on=today - timedelta(days=100),
                                                   due_on=today - timedelta(days=80), document=_doc("Drawdown notice 1"))
    did = next(d["drawdown_id"] for c in first["aif"] if c["commitment_id"] == cid for d in c["drawdowns"] if d["number"] == 1)
    await india_agreements.record_payment(s, firm, ops, by=BY, drawdown_id=did, paid_on=today - timedelta(days=83), document=_doc("Bank advice"))
    await india_agreements.record_drawdown(s, firm, ops, by=BY, commitment_id=cid, amount=2_250_000, notice_on=today - timedelta(days=6),
                                           due_on=today + timedelta(days=12), document=_doc("Drawdown notice 2"))
    return 7


async def _scheme_documents(s: AsyncSession, firm: Firm, today: date) -> int:
    """Scheme documents for the fund Karthik holds: both on file, only the memorandum recorded as given."""
    karthik, _, _ = await _people(s, firm, "Karthik Iyer")
    ppfas = (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == "PPFAS-FLEXI-DIRECT"))).scalar_one_or_none()
    if karthik is None or ppfas is None or (await s.execute(select(SchemeDocument.id).where(SchemeDocument.instrument_id == ppfas.id).limit(1))).first():
        return 0
    ops = UserRole.OPERATIONS
    for kind in ("sid", "kim"):
        await india_agreements.record_scheme_document(s, firm, ops, by=BY, symbol=ppfas.symbol, kind=kind,
                                                      dated_on=today - timedelta(days=150), document=_doc("Scheme document"))
    await india_agreements.record_disclosure(s, firm, ops, by=BY, household_id=karthik.household_id, kind="scheme_documents",
                                             symbol=ppfas.symbol, items=["kim"], method="email", given_on=today - timedelta(days=25),
                                             document=_doc("Email with attachment"))
    return 3


async def _move_ahead(s: AsyncSession, firm: Firm, today: date) -> int:
    """The unpaid demonstration drawdown stays due in the near future on a later run."""
    moved = 0
    for d in (await s.execute(select(AifDrawdown).where(AifDrawdown.firm_id == firm.id, AifDrawdown.recorded_by == BY))).scalars().all():
        if d.due_on >= today + timedelta(days=3):
            continue
        if (await s.execute(select(AifPayment.id).where(AifPayment.drawdown_id == d.id).limit(1))).first():
            continue
        d.notice_on, d.due_on = today - timedelta(days=6), today + timedelta(days=12)
        if d.task_id:
            from app.models.engagement import Task

            task = await s.get(Task, d.task_id)
            if task is not None and str(task.status) == "open":
                task.due_date = d.due_on
        moved += 1
    return moved


async def _reload(s: AsyncSession) -> None:
    """A rolled-back savepoint expires what it touched, and an expired row cannot load itself here: read them again."""
    for row in list(s.sync_session.identity_map.values()):
        if inspect_row(row).expired_attributes:
            await s.refresh(row)


async def _attempt(s: AsyncSession, report: list[str], part: str, step):
    """Run one part inside its own savepoint. A refusal or a database error undoes that part alone and is reported."""
    try:
        async with s.begin_nested():
            return await step()
    except Exception as exc:  # noqa: BLE001 - reported, and the rest of the backfill goes on
        report.append(f"{part}: skipped, {exc}")
        log.warning("india_agreements_skipped", part=part, why=str(exc)[:500])
        await _reload(s)
        return None


async def add(s: AsyncSession, firm: Firm, report: list[str] | None = None) -> int:
    """What is not on file yet. Returns how many records it wrote or re-dated; what it could not write is appended to `report`."""
    said = report if report is not None else []
    if not kyc_india.applies(firm):
        return 0
    today = firm_time.today(firm)
    users: dict = {}
    for u in (await s.execute(select(User).where(User.firm_id == firm.id).order_by(User.email))).scalars().all():
        users.setdefault(UserRole(u.role), u)
    written = 0

    async def part(name: str, step) -> int:
        return await _attempt(s, said, name, step) or 0

    async def schemes_and_list() -> int:
        _, added = await _instruments(s, firm, today)
        return added + await _acceptance(s, firm, today)

    async def pintos() -> int:
        inst = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
        return await _pintos(s, firm, today, inst, users)

    written += await part("The drawdown's date", lambda: _move_ahead(s, firm, today))
    written += await part("The invented schemes and the acceptance list", schemes_and_list)
    written += await part("The Pinto household", pintos)
    written += await part("The two onboarding cases", lambda: _cases(s, firm, today, users))
    written += await part("Karthik Iyer's Portfolio Manager papers", lambda: _pms_papers(s, firm))
    written += await part("Karthik Iyer's demat pack", lambda: _demat_pack(
        s, firm, today, "Karthik Iyer", ("account_opening_form", "rights_obligations", "tariff_sheet", "demat_agreement"), 380))
    written += await part("Rajesh Sharma's demat pack", lambda: _demat_pack(s, firm, today, "Rajesh Sharma", ("account_opening_form",), 500))
    written += await part("Karthik Iyer's fund commitment", lambda: _aif(s, firm, today))
    written += await part("Scheme documents", lambda: _scheme_documents(s, firm, today))
    await s.flush()
    return written
