"""The Mehta family on the demonstration firm (B-400): the two India modules' worked case, on invented data.

Vivek and Anjali Mehta of Bengaluru, their children Rhea (16) and Dev (14), and Vivek's elder brother Sameer in Dubai,
who is a household of his own. Everything here is invented for the demonstration firm and says so in its evidence; no
registry, bank, fund house, registrar or insurer was asked anything.

Each record goes in through the module a person's entry goes through, so it is refused for the same reasons: a case is
opened (`onboarding_start`), its KYC, identity checks, disclosures and consents are recorded, and activation is the
onboarding agent's own, behind every gate. The consolidation workbench, bank links, mandates, first debits, SIPs,
residency, statements, policies, goals, nominations, the advice file and the plan of record likewise. Four things have no
recording function and are written as the other demonstration seeds write them: the case's agreement, fee and proposal
fields (what the page's buttons set), a custodian's holdings and cash on an account, the joint account itself, and dates
of birth. The days are counted back from the firm's today, Day 0 being `DAY0` days ago.

The couple stand where the planning case leaves them: protection placed, an emergency fund half built, one endowment
surrendered and one kept, no will yet, and a plan of record delivered. Run by the seed when it builds the firm and by
`seed.india_demo_backfill` for a firm already deployed. Each household is written once: where it is on file nothing of it
is written again, so nothing a person has since recorded is touched.

**A refusal skips its own part and no more.** A deployed firm may refuse what a new one accepts, and one transaction holds
the whole backfill, so each onboarding case and each later part is written inside its own savepoint. A case that does not
activate is rolled back and reported, the other cases go on, and what needs the missing person is left out. A household
with no case activated is not written, so a later run tries it again; a household that was written is not added to later.
What was skipped is returned in `report` and logged.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import inspect as inspect_row, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import (advice_file, advice_practice, agreement_contents, aml_review, aml_risk, balance_sheet, consents, consolidation, disclosures, document_matrix, financial_plan, firm_time,
                            goal_record, health_cover, held_away, identity_checks, india_estate, india_money, india_savings, india_tax_year, kyc_india,
                            kyc_registry, markets, money_in, nri_planning, onboarding_start, plan_record, planning_measures, policy_values, protection,
                            residency, retirement_stack, risk_profile, salary_tax, screening_lists, sip, sip_mandates)
from app.core.db import utcnow
from app.core.logging import get_logger
from app.models.advice_file import AdviceRecommendation
from app.models.enums import ClientSegment, UserRole
from app.models.graph import Account, Household, Mandate, Person
from app.models.identity import User
from app.models.onboarding import DisclosureDelivery, FeeSchedule, HeldAwayAsset, OnboardingCase, OnboardingDocument, OnboardingParty
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TaxLot
from app.models.protection import ProtectionPolicy
from app.models.risk_assessment import RiskAssessment
from app.models.tenant import Firm

log = get_logger("aurea.seed.india.mehta")
SOURCE = "Demonstration record, invented for the demonstration firm"
BY = "seed: India Mehta family (demonstration)"
HOME, BROTHER_HOME = "The Mehta Family", "The Sameer Mehta Household"
VIVEK, ANJALI, RHEA, DEV, SAMEER, MOTHER = "Vivek Mehta", "Anjali Mehta", "Rhea Mehta", "Dev Mehta", "Sameer Mehta", "Savitri Mehta"
DAY0 = 45
BANK = "A demonstration bank (invented)"
LEGACY, LEGACY_LABEL = "Legacy distributor demat (invented)", "Demat MEH-LEGACY-01"
JOINT = "Vivek and Anjali Mehta joint demat"
FROM_CODE, TO_CODE = "ARN-000222 (invented)", "INA-DEMO-0001 (invented)"
EXIT_IN = 24
CHILDREN = f"{RHEA}:daughter:50; {DEV}:son:50"
FEE_CODE = "IN-FIXED-MEHTA"

HDFC, UTI, PPFAS = "HDFC Mutual Fund", "UTI Mutual Fund", "PPFAS Mutual Fund"
SAH, COR = "Sahyadri Mutual Fund (invented)", "Coromandel Mutual Fund (invented)"
CORP_R = ("HDFC Corporate Bond Fund - Regular Plan - Growth", "HDFC-CORPBOND-REGULAR", "INF179K01DC2")
CORP_D = ("HDFC Corporate Bond Fund - Direct Plan - Growth", "HDFC-CORPBOND-DIRECT", "INF179K01XD8")
NIFTY_R = ("UTI Nifty 50 Index Fund - Regular Plan - Growth", "UTI-NIFTY50-REGULAR", "INF789F01JN2")
NIFTY_D = ("UTI Nifty 50 Index Fund - Direct Plan - Growth", "UTI-NIFTY50-DIRECT", "INF789F01XA0")
FLEXI_R = ("Parag Parikh Flexi Cap Fund - Regular Plan - Growth", "PPFAS-FLEXI-REGULAR", "INF879O01019")
FLEXI_D = ("Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "PPFAS-FLEXI-DIRECT", "INF879O01027")

# Fourteen folios across five fund houses: eleven whose distributor code changes, one sector fund exited against the gain
# budget, and two direct folios kept as they are. (owner, house, folio, (name, symbol, isin), units, price, tax class, what)
FOLIOS = [
    (VIVEK, HDFC, "Folio MEH-101/11", CORP_R, 12000, 34.10, None, "code"),
    (ANJALI, HDFC, "Folio MEH-102/27", CORP_R, 8000, 34.10, None, "code"),
    (VIVEK, UTI, "Folio MEH-201/08", NIFTY_R, 2000, 162.60, None, "code"),
    (ANJALI, UTI, "Folio MEH-202/54", NIFTY_R, 1000, 162.60, None, "code"),
    (VIVEK, PPFAS, "Folio MEH-301/62", FLEXI_R, 5000, 81.70, None, "code"),
    (ANJALI, PPFAS, "Folio MEH-302/19", FLEXI_R, 3000, 81.70, None, "code"),
    (VIVEK, SAH, "Folio MEH-401/33", ("Sahyadri Large Cap Fund - Regular - Growth (invented)", None, None), 4000, 72.00, "equity_fund", "code"),
    (VIVEK, SAH, "Folio MEH-402/90", ("Sahyadri Short Duration Fund - Regular - Growth (invented)", None, None), 40000, 24.00, "specified_fund", "code"),
    (VIVEK, COR, "Folio MEH-501/04", ("Coromandel Midcap Fund - Regular - Growth (invented)", None, None), 1500, 140.00, "equity_fund", "code"),
    (ANJALI, COR, "Folio MEH-502/81", ("Coromandel Balanced Advantage Fund - Regular - Growth (invented)", None, None), 15000, 30.00, "equity_fund", "code"),
    (ANJALI, SAH, "Folio MEH-403/45", ("Sahyadri Liquid Plus Fund - Regular - Growth (invented)", None, None), 15000, 24.00, "specified_fund", "code"),
    (VIVEK, COR, "Folio MEH-503/58", ("Coromandel Infrastructure Sector Fund - Regular - Growth (invented)", None, None), 8000, 50.00, "equity_fund", "exit"),
    (ANJALI, HDFC, "Folio MEH-103/72", CORP_D, 10000, 35.00, None, "keep"),
    (ANJALI, UTI, "Folio MEH-203/15", NIFTY_D, 800, 165.10, None, "keep"),
]
# Two ETFs in the legacy demat, moved off-market into the joint demat: (symbol, name, units, cost against price, days ago)
ETFS = [("NIFTYBEES.NS", "Nippon India Nifty 50 ETF", 2000, -0.24, 1240), ("GOLDBEES.NS", "Nippon India Gold ETF", 4000, -0.38, 1500)]
# What the custodian holds after consolidation: account -> (cash, [(symbol, rupees at today's price, cost against it, days ago)])
BOOK = {
    VIVEK: (800_000, [("GILT5YBEES.NS", 7_000_000, -0.01, 800), ("NIFTYBEES.NS", 2_200_000, -0.18, 900)]),
    ANJALI: (500_000, [("GILT5YBEES.NS", 4_800_000, -0.01, 700), ("GOLDBEES.NS", 1_200_000, -0.30, 1100), ("EMBASSY.NS", 800_000, -0.10, 650),
                       ("NIFTYBEES.NS", 600_000, -0.12, 500)]),
    SAMEER: (2_500_000, []),
}
LIQUID = ("A demonstration fund house (invented)", "Folio MEH-LIQ-01", "Demonstration Liquid Fund - Direct - Growth (invented)")
EMERGENCY_TARGET, EMERGENCY_HELD = 900_000, 450_000


def _proof(what: str) -> str:
    return f"{what} ({SOURCE})"


async def add(s: AsyncSession, firm: Firm, report: list[str] | None = None) -> int:
    """The households not on file yet. Returns how many it wrote; what it could not write is appended to `report`."""
    said = report if report is not None else []
    if not kyc_india.applies(firm):
        return 0
    have = set((await s.execute(select(Household.name).where(Household.firm_id == firm.id,
                                                            Household.name.in_((HOME, BROTHER_HOME))))).scalars().all())
    if have == {HOME, BROTHER_HOME}:
        return 0
    users = {}
    for u in (await s.execute(select(User).where(User.firm_id == firm.id, User.is_active.is_(True)).order_by(User.email))).scalars().all():
        users.setdefault(UserRole(u.role), u)
    model_rows = {m.name: m for m in (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id))).scalars().all()}
    instruments = {i.symbol: i for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    needed = (UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.COMPLIANCE)
    symbols = (FLEXI_D[1], NIFTY_D[1], CORP_D[1], "NIFTYBEES.NS", "GILT5YBEES.NS", "GOLDBEES.NS", "EMBASSY.NS")
    lacking = [r.value for r in needed if r not in users] + [n for n in ("Core Growth", "Core Balanced") if n not in model_rows] \
        + [sym for sym in symbols if sym not in instruments]
    if lacking:
        _skip(said, "The Mehta households", f"the firm lacks {', '.join(lacking)}")
        return 0
    c = SimpleNamespace(s=s, firm=firm, today=firm_time.today(firm), users=users, models=model_rows, instruments=instruments, report=said)
    c.ago = lambda n: c.today - timedelta(days=n)
    written = 0
    if HOME not in have:
        written += await _family(c)
    if BROTHER_HOME not in have:
        written += await _brother(c)
    await s.flush()
    return written


class _Nobody(Exception):
    """No case of this household activated, so the household is rolled back."""


def _skip(report: list[str], part: str, why) -> None:
    report.append(f"{part}: skipped, {why}")
    log.warning("india_mehta_skipped", part=part, why=str(why)[:500])


async def _reload(c) -> None:
    """A rolled-back savepoint expires what it touched, and an expired row cannot load itself here: read them again."""
    for row in list(c.s.sync_session.identity_map.values()):
        if inspect_row(row).expired_attributes:
            await c.s.refresh(row)


async def _attempt(c, part: str, step):
    """Run one part inside its own savepoint. A refusal or a database error undoes that part alone and is reported."""
    try:
        async with c.s.begin_nested():
            return await step()
    except Exception as exc:  # noqa: BLE001 - reported, and the rest of the backfill goes on
        _skip(c.report, part, exc)
        await _reload(c)
        return None


# ── Onboarding ────────────────────────────────────────────────────────────────

async def _fee_schedule(c, code: str, name: str, amount: int) -> FeeSchedule:
    row = (await c.s.execute(select(FeeSchedule).where(FeeSchedule.firm_id == c.firm.id, FeeSchedule.code == code))).scalars().first()
    if row is None:
        row = FeeSchedule(id=uuid.uuid4(), firm_id=c.firm.id, code=code, name=name, fee_type="flat_fee", flat_fee=amount, tiers=[], currency="INR",
                          notes=SOURCE)
        c.s.add(row)
        await c.s.flush()
    return row


async def _onboard(c, household: Household, name: str, *, registration: str = "individual", born: date, model: str, risk: str,
                   fee: FeeSchedule, registry_first: str, mode: str, band: str, bank_last4: str, residence: str, channel: str, answers: dict,
                   billable: int) -> tuple:
    """One person's case from opening to activation, and the questionnaire after it. Returns (case, person, account, mandate)."""
    from app.agents.onboarding import OnboardingAgent
    from app.atlas.base import AgentContext, Subject

    s, firm, day0 = c.s, c.firm, c.ago(DAY0)
    adviser, ops, compliance = (c.users[r] for r in (UserRole.ADVISER, UserRole.OPERATIONS, UserRole.COMPLIANCE))
    began = utcnow() - timedelta(days=DAY0)
    case = await onboarding_start.start(s, firm, actor=adviser, prospect_name=name, registration_type=registration, household_id=str(household.id),
                                        risk_preference=risk, mandate_preference="advisory", source_of_wealth="Employment income (demonstration)")
    case.created_at = began
    # KYC: the registry's status first (B-386). A registered status goes to video verification before it reads validated.
    held = ["identity_proof", "address_proof", "pan_card", "photograph"]
    await kyc_registry.record_lookup(s, firm, UserRole.OPERATIONS, by=BY, registry="kra", agency="CVL KRA", status=registry_first, answered_on=day0,
                                     document=_proof("KRA status response"), case_id=case.id, **({"holds": held} if registry_first == "validated" else {}))
    party = OnboardingParty(firm_id=firm.id, case_id=case.id, role="owner", legal_name=name, dob=born.isoformat())
    screening_lists.stamp(party, await screening_lists.entries(s, firm.id), "Screened when recorded. Hits await a person's decision.")
    s.add(party)
    await s.flush()
    if registry_first != "validated" or mode == "video_kyc":
        await identity_checks.record_result(s, firm, UserRole.OPERATIONS, by=BY, kind="vipv", case_id=case.id, party_name=name, outcome="passed",
                                            document=_proof("Video verification session record, checked by an officer"))
    if registry_first != "validated":
        await kyc_registry.record_lookup(s, firm, UserRole.OPERATIONS, by=BY, registry="kra", agency="CVL KRA", status="validated", answered_on=day0,
                                         document=_proof("KRA status response after the video re-verification"), case_id=case.id, holds=held)
    await identity_checks.record_result(s, firm, UserRole.OPERATIONS, by=BY, kind="bank", case_id=case.id, party_name=name, outcome="passed",
                                        bank=BANK, last4=bank_last4, name_on_document=name, document=_proof("Cancelled cheque showing the holder's name"))
    await kyc_india.record(s, firm, by=BY, document=_proof("KYC acknowledgement"), case_id=case.id, party_name=name,
                           ckyc_number=f"9000000000{bank_last4}", kra_name="CVL KRA", kra_status="validated", kyc_completed_on=day0,
                           verification_mode=mode, pan_aadhaar_linked=True, risk_band=band)
    s.add(OnboardingDocument(firm_id=firm.id, case_id=case.id, doc_type="passport", filename=f"{name} passport.pdf", raw_text=_proof("Passport")))
    at = began + timedelta(hours=1)
    for doc in disclosures.required_for(firm.jurisdiction, registration, "advisory"):
        s.add(DisclosureDelivery(firm_id=firm.id, case_id=case.id, doc_type=doc, delivered_at=at, method="in_person", delivered_by=adviser.email,
                                 notes=SOURCE))
    for kind, method in (("advisory_agreement", "e_signature"), ("ai_use_disclosure", "email"), ("investor_charter", "email"),
                         ("non_sebi_declaration", "e_signature"), ("e_delivery", "email"), ("fee_debit", "signed_form"), ("risk_profile", "email")):
        await consents.record(s, firm.id, by=BY, case_id=case.id, household_id=None, kind=kind, status="granted", given_by=name, given_on=day0,
                              method=method, evidence=_proof(f"{kind.replace('_', ' ').capitalize()}, filed in the client folder"))
    await aml_risk.record_rating(s, case.id, firm_id=firm.id, by=compliance.email, role=UserRole.COMPLIANCE,
                                 facts={"residence_country": residence, "wealth_source": "employment", "channel": channel})
    if case.edd_status == "edd_pending":
        # A rating above low opens enhanced due diligence: compliance signs the file off, a source for every item.
        await aml_review.record_edd(s, case.id, firm_id=firm.id, by=compliance.email, role=UserRole.COMPLIANCE, wealth_type="employment", conclusion="explicable",
                                    evidence={key: _proof(label) for key, label in aml_review.CHECKLISTS["employment"]}, decision="approve",
                                    rationale="Salary income explained by the documents on file (demonstration).")
    # What the page's buttons set: engagement, the signed agreement, the fee under maker and checker, and the proposal accepted.
    chosen = c.models[model]
    case.engagement_type = "advisory"
    case.agreement_status, case.agreement_sent_at, case.agreement_signed_at = "signed", at, at + timedelta(hours=2)
    case.cip_status = "verified"
    case.fee_schedule_id, case.billing_method, case.billing_frequency = fee.id, "arrears", "quarterly"
    case.billable_aum = billable
    case.fee_set_by, case.fee_set_at, case.fee_confirmed_by, case.fee_confirmed_at = adviser.email, at, compliance.email, at + timedelta(hours=3)
    case.proposal = {"suitability": {"risk_profile": risk, "capacity_for_loss": None},
                     "mandate": {"mandate_type": "advisory", "model_portfolio": chosen.name, "model_portfolio_id": str(chosen.id),
                                 "constraints": {"service_track": "investment_adviser", "cgt_budget": 150000, "values_exclusions": []}}}
    case.ips_accepted_at, case.ips_accepted_by = at + timedelta(hours=4), adviser.email
    # What is held elsewhere was asked: listed here without a figure, and line by line from the statements afterwards.
    s.add(HeldAwayAsset(firm_id=firm.id, case_id=case.id, institution="Held elsewhere, listed from statements", source="client_declared",
                        currency="INR", notes=SOURCE))
    case.held_away_captured_at = at
    await s.flush()
    detail = {"signed_by": [name], "signed_on": day0.isoformat()}
    papers = []
    if registration == "nri_nre":
        detail["country_of_residence"] = "United Arab Emirates"
        papers = ["passport", "overseas_address_proof", "visa_or_oci", "pan_card", "nre_nro_bank_proof", "fatca_crs_self_cert"]
    await document_matrix.record_detail(s, firm, by=BY, case_id=case.id, fields=detail, documents=papers,
                                        evidence=_proof("The signed application and the papers received with it"))
    ctx = AgentContext(session=s, firm=firm, run=None, tier=None, guardrails={}, config={}, subject=Subject("onboarding_case", case.id, name))
    done = await OnboardingAgent().act(ctx, SimpleNamespace(payload={}, modified_payload=None))
    if not done.get("executed"):
        raise RuntimeError(f"The demonstration case for {name} did not activate: {done.get('note')}")
    # Opened within the day: the paperwork clock the module's case reports.
    case.activated_at = began + timedelta(days=1)
    person = await s.get(Person, uuid.UUID(done["person_id"]))
    person.date_of_birth, person.preferred_name = born, name.split()[0]
    mandate = await s.get(Mandate, uuid.UUID(done["mandate_id"]))
    account = await s.get(Account, uuid.UUID(done["account_id"]))
    account.name = f"{name} NRE demat" if registration == "nri_nre" else f"{name} demat"
    account.custodian = "NSDL" if name == ANJALI else "CDSL"
    answered = await risk_profile.submit(s, household.id, answers, firm_id=firm.id, by=BY, role="adviser", subject_id=person.id)
    answered.completed_at = began
    await s.flush()
    await risk_profile.decide(s, firm.id, mandate.id, decision="adopt", reason="Adopted on the day the questionnaire was completed", by=BY)
    return case, person, account, mandate


async def _holdings(c, account: Account, lines: list) -> None:
    """A custodian's holdings, at the latest price on file, with the lots behind them."""
    for symbol, rupees, cost_pct, days in lines:
        inst = c.instruments[symbol]
        price = (await c.s.execute(select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()))).scalars().first()
        if price is None:
            continue
        close = float(price.close)
        qty = round(rupees / close)
        cost = round(close * (1 + cost_pct), 2)
        h = Holding(firm_id=c.firm.id, account_id=account.id, instrument_id=inst.id, quantity=qty, market_value=round(qty * close, 2),
                    cost_basis=round(qty * cost, 2), lineage={"source": account.custodian, "as_of": c.today.isoformat()}, confidence=0.95)
        c.s.add(h)
        await c.s.flush()
        c.s.add(TaxLot(firm_id=c.firm.id, holding_id=h.id, quantity=qty, cost_per_unit=cost, acquired_on=c.today - timedelta(days=days)))
    await c.s.flush()


async def _beneficiaries(c, account: Account, nominees: list[tuple[str, str, int]]) -> None:
    """The nominees on a demat account, through the page's own route, which holds SEBI's cap."""
    from app.api.account_registration import BeneficiaryIn, add_beneficiary

    for name, relation, share in nominees:
        await add_beneficiary(account.id, BeneficiaryIn(beneficiary_name=name, relationship_to_owner=relation, percentage=share, notes=SOURCE),
                              user=c.users[UserRole.OPERATIONS], firm=c.firm, db=c.s)


async def _agreement(c, household: Household) -> None:
    """What the signed advisory agreement carried, as a person read it: without it nothing stands before advice."""
    await agreement_contents.record(c.s, c.firm, UserRole.ADVISER, by=BY, household_id=household.id, annexure_a=True, mitc=True,
                                    document=_proof("Signed advisory agreement with Annexure A and the MITC"))


# ── The family ────────────────────────────────────────────────────────────────

def _born(today: date, age: int, month: int, day: int) -> date:
    """The birthday that makes someone `age` years old today."""
    year = today.year - age - (0 if (today.month, today.day) >= (month, day) else 1)
    return date(year, month, day)


GROWTH_ANSWERS = {"investment_horizon": 2, "loss_tolerance": 2, "income_stability": 2, "liquidity_needs": 2, "esg_priority": 0, "experience": 2}
CAUTIOUS_ANSWERS = {"investment_horizon": 2, "loss_tolerance": 0, "income_stability": 1, "liquidity_needs": 2, "esg_priority": 1, "experience": 1}


def _answers(picks: dict) -> dict:
    return {q["key"]: q["options"][picks[q["key"]]] for q in risk_profile.QUESTIONS}


async def _family(c) -> int:
    s, firm, today = c.s, c.firm, c.today
    adviser = c.users[UserRole.ADVISER]
    cases = [
        (VIVEK, (FEE_CODE + "-A", "Fixed advisory fee, family (demonstration)", 100_000),
         dict(born=_born(today, 48, 4, 14), model="Core Growth", risk="growth", registry_first="validated", mode="aadhaar_ekyc", band="medium",
              bank_last4="6120", residence="IN", channel="remote", answers=_answers(GROWTH_ANSWERS), billable=10_000_000)),
        (ANJALI, (FEE_CODE + "-B", "Fixed advisory fee, spouse (demonstration)", 40_000),
         dict(born=_born(today, 46, 9, 3), model="Core Balanced", risk="conservative", registry_first="registered", mode="video_kyc",
              band="medium", bank_last4="7741", residence="IN", channel="remote", answers=_answers(CAUTIOUS_ANSWERS), billable=8_000_000)),
    ]
    people, accounts, mandates = {}, {}, {}

    async def open_household():
        home = Household(firm_id=firm.id, name=HOME, segment=ClientSegment.PRIVATE_WEALTH, values={"jurisdiction": "IN", "demonstration": True})
        s.add(home)
        await s.flush()
        for name, fee, terms in cases:
            async def case(name=name, fee=fee, terms=terms):
                return await _onboard(c, home, name, fee=await _fee_schedule(c, *fee), **terms)

            done = await _attempt(c, f"The onboarding case for {name}", case)
            if done is not None:
                _, people[name], accounts[name], mandates[name] = done
        if not people:
            raise _Nobody("no onboarding case of the household activated; a later run tries again")
        return home

    home = await _attempt(c, HOME, open_household)
    if home is None:
        return 0
    both = VIVEK in people and ANJALI in people
    joint = None

    async def book():
        nonlocal joint
        for name, age, month, day in ((RHEA, 16, 2, 20), (DEV, 14, 7, 8)):
            s.add(Person(firm_id=firm.id, household_id=home.id, full_name=name, preferred_name=name.split()[0],
                         date_of_birth=_born(today, age, month, day), segment=ClientSegment.PRIVATE_WEALTH, is_next_gen=True, kyc={}, profile={}))
        await s.flush()
        # The joint demat, anyone or survivor, chosen and recorded; then what each custodian holds.
        shared = None
        if both:
            shared = Account(firm_id=firm.id, mandate_id=mandates[VIVEK].id, name=JOINT, custodian="CDSL", currency="INR", cash_balance=0,
                             registration_type="joint_anyone", lineage={"source": "demonstration", "as_of": today.isoformat()})
            s.add(shared)
            await s.flush()
        for who, account in accounts.items():
            cash, lines = BOOK[who]
            account.cash_balance = cash
            await _holdings(c, account, lines)
        for account in [*accounts.values(), *([shared] if shared else [])]:
            await _beneficiaries(c, account, [(RHEA, "daughter", 50), (DEV, "son", 50)])
        joint = shared
        return True

    if await _attempt(c, "The family's accounts, holdings and nominees", book) is None:
        joint = None
    await _attempt(c, "The agreement's contents", lambda: _agreement(c, home))
    await _attempt(c, "The statements, consolidation, balance sheet and nominations", lambda: _statements(c, home, people, joint))
    await _attempt(c, "The policies", lambda: _protection(c, home, people))
    await _attempt(c, "The verified banks", lambda: _money(c, people, accounts))
    await _attempt(c, "The goals, SIPs and mandates", lambda: _goals(c, home, accounts))
    await _attempt(c, "The tax reviews", lambda: _reviews(c, people))
    if both:
        await _attempt(c, "The family's risk profile confirmation", lambda: confirm_risk_profiles(s, firm))
        await _attempt(c, "The advice file and the plan of record", lambda: _advice_and_plan(c, home, adviser))
    else:
        _skip(c.report, "The advice file and the plan of record", "they speak for both of the couple and one case did not activate")
    return 1


async def _statements(c, home, people: dict, joint) -> None:
    """Folios and the legacy demat from their statements, the consolidation, the balance sheet, the retirement accounts
    and the nominations."""
    s, firm, today, ago = c.s, c.firm, c.today, c.ago
    vivek = people.get(VIVEK)
    adviser, ops = UserRole.ADVISER, UserRole.OPERATIONS
    stated = ago(DAY0 + 5)

    async def line(owner, institution, label, name, symbol, isin, units, price, document) -> uuid.UUID:
        out = await held_away.record_holding(s, home.id, firm_id=firm.id, by=BY, institution=institution, instrument_name=name,
                                             value=round(units * price, 2), value_on=stated, source_document=_proof(document), account_label=label,
                                             symbol=symbol, isin=isin, quantity=units, person_id=owner.id)
        return uuid.UUID(out["holding_id"])

    folios = []
    for who, house, folio, (name, symbol, isin), units, price, klass, what in (f for f in FOLIOS if f[0] in people):
        lid = await line(people[who], house, folio, name, symbol, isin, units, price,
                         f"Consolidated account statement, {stated.isoformat()}, from the legacy distributor's file")
        lots = [{"units": units, "cost_per_unit": round(price * 0.78, 2), "acquired_on": ago(900 + len(folios) * 40).isoformat()}]
        await consolidation.record_facts(s, firm, ops, by=BY, line_id=lid, form="folio", tax_class=klass, lots=lots,
                                         distributor_code=None if what == "keep" else FROM_CODE,
                                         exit_load_rate=0.01 if what == "exit" else None, exit_load_days=365 if what == "exit" else None,
                                         document=_proof(f"Capital gains statement for {folio}"))
        folios.append((lid, who, folio, what))
    etfs = []
    # The two ETFs move from Vivek's legacy demat into the joint one, so they need both.
    for symbol, name, units, cost_pct, days in (ETFS if vivek is not None and joint is not None else []):
        price = float((await s.execute(select(Price.close).where(Price.instrument_id == c.instruments[symbol].id)
                                       .order_by(Price.as_of.desc()))).scalars().first())
        lid = await line(vivek, LEGACY, LEGACY_LABEL, name, symbol, None, units, price, f"Depository statement, {stated.isoformat()}")
        cost = round(price * (1 + cost_pct), 2)
        await consolidation.record_facts(s, firm, ops, by=BY, line_id=lid, form="demat",
                                         lots=[{"units": units, "cost_per_unit": cost, "acquired_on": ago(days).isoformat()}],
                                         document=_proof("Contract notes for the legacy demat"))
        etfs.append((lid, symbol, units, cost_pct, days, price))

    await consolidation.record_classification(s, firm, adviser, by=BY, line_ids=[f[0] for f in folios if f[3] != "exit"], decision="keep_remap",
                                              reason=_proof("The scheme still fits; only who services the folio changes"))
    if etfs:
        await consolidation.record_classification(s, firm, adviser, by=BY, line_ids=[e[0] for e in etfs], decision="transfer",
                                                  reason=_proof("Held for the long term; moves in kind to the joint demat"))
    leaving = [f[0] for f in folios if f[3] == "exit"]
    if leaving:
        await consolidation.record_classification(
            s, firm, adviser, by=BY, line_ids=leaving, decision="exit", product_reason="unsuitable",
            reason=_proof("A sector fund that no longer fits the plan; sold against the gain budget"), planned_on=today + timedelta(days=EXIT_IN))

    # Day 1 to 3: the code changes go in. Day 5 to 7: the registrars confirm all eleven, none rejected.
    for n, (lid, who, folio, _) in enumerate(f for f in folios if f[3] == "code"):
        item = (await consolidation.open_item(s, firm, ops, by=BY, kind="code_change", line_ids=[lid], to_code=TO_CODE, on=ago(DAY0 - 1),
                                              evidence=_proof(f"Code change request form for {folio}")))["item_id"]
        await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="consented", on=ago(DAY0 - 1),
                                        evidence=_proof("The holder's signed consent"))
        await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="submitted", on=ago(DAY0 - 2),
                                        evidence=_proof("Registrar's acknowledgement"))
        await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step="confirmed", on=ago(DAY0 - 5 - n % 3),
                                        reference=f"RTA-MEH-{n + 1:04d}", evidence=_proof("Registrar's confirmation letter"))
    for n, (lid, symbol, units, cost_pct, days, price) in enumerate(etfs):
        item = (await consolidation.open_item(
            s, firm, ops, by=BY, kind="demat_transfer", line_ids=[lid], on=ago(DAY0 - 2), to_account=JOINT, transfer_kind="other",
            reason_code="DEMO-CONSOL", evidence=_proof("Delivery instruction slip")))["item_id"]
        for step, day, ref in (("consented", DAY0 - 2, None), ("submitted", DAY0 - 3, None), ("confirmed", DAY0 - 6, f"DP-MEH-{n + 1:04d}")):
            await consolidation.record_step(s, firm, ops, by=BY, item_id=item, step=step, on=ago(day), reference=ref,
                                            evidence=_proof("Depository statement showing the receipt" if step == "confirmed" else "The holder's instruction"))
        # What landed is on the book now, so the old statement line ends and net worth counts it once.
        await held_away.end_holding(s, lid, firm_id=firm.id, by=BY, reason="Received into the joint demat, on the depository's statement")
        await _holdings(c, joint, [(symbol, units * price, cost_pct, days)])

    # The balance sheet.
    for kind, name, amount, person, extra in (
            ("property", "Apartment, Bengaluru (demonstration)", 18_000_000, None, {}),
            ("mortgage", "Home loan, a demonstration bank (invented)", 4_000_000, vivek,
             {"annual_payment": 600_000, "interest_rate": 0.085, "ends_on": today + timedelta(days=9 * 365)}),
            ("cash", "Fixed deposits, a demonstration bank (invented)", 2_500_000, vivek, {}),
            ("essential_spending", "Essential household spending", 1_800_000, None, {})):
        if kind in ("mortgage", "cash") and person is None:
            continue          # his loan and his deposits: not put on anyone else
        await balance_sheet.record_item(s, home.id, firm_id=firm.id, by=BY, kind=kind, name=name, amount=amount, as_of=stated,
                                        source_document=_proof("Statement or schedule on file"), person_id=person.id if person else None, **extra)
    # One tax record a person: basic salary is what the employer's NPS contribution is held against (B-396).
    for who, salary, basic, deductions in ((VIVEK, 4_000_000, 2_000_000, 650_000), (ANJALI, 2_000_000, 1_000_000, 475_000)):
        if who not in people:
            continue
        await protection.record_income(s, firm.id, person_id=people[who].id, annual_income=salary, source_document=_proof("Form 16"), recorded_by=BY)
        await salary_tax.record(s, firm, adviser, by=BY, person_id=people[who].id, document=_proof("Form 16 and the investment declaration"),
                                salary_income=salary, basic_salary=basic, old_regime_deductions=deductions, regime="new", marginal_rate=0.30)

    # EPF passbooks, NPS statements and PPF, and what goes into them each month, the employer's NPS among it (80CCD(2)).
    for who, kind, institution, value, extra in (
            (VIVEK, "epf", "Provident fund office (demonstration)", 1_800_000, {"rate": 8.25}),
            (ANJALI, "epf", "Provident fund office (demonstration)", 900_000, {"rate": 8.25}),
            (VIVEK, "nps", "A demonstration pension fund manager (invented)", 600_000, {"details": {"tier": "Tier I", "equity_share_percent": 50}}),
            (ANJALI, "nps", "A demonstration pension fund manager (invented)", 300_000, {"details": {"tier": "Tier I", "equity_share_percent": 25}})):
        if who not in people:
            continue
        await india_savings.record(s, firm, adviser, by=BY, household_id=home.id, person_id=people[who].id, kind=kind, institution=institution,
                                   value=value, value_on=stated, document=_proof("Passbook or statement"), **extra)
    for who, scheme, payer, monthly in ((VIVEK, "epf", "own", 20_000), (VIVEK, "epf", "employer", 18_000), (ANJALI, "epf", "own", 10_000),
                                        (ANJALI, "epf", "employer", 9_000), (VIVEK, "nps", "employer", 16_000), (ANJALI, "nps", "employer", 8_000)):
        if who not in people:
            continue
        await retirement_stack.record_contribution(s, firm, adviser, by=BY, person_id=people[who].id, scheme=scheme, payer=payer, monthly_amount=monthly,
                                                   statement_date=stated, document=_proof("Payslip"))

    # The emergency fund, a liquid fund half way through its six-month build.
    if vivek is not None:
        await held_away.record_holding(s, home.id, firm_id=firm.id, by=BY, institution=LIQUID[0], instrument_name=LIQUID[2], value=EMERGENCY_HELD,
                                       value_on=ago(5), source_document=_proof("Account statement"), account_label=LIQUID[1], person_id=vivek.id)
        await planning_measures.record_fund(s, firm, UserRole.ADVISER, by=BY, household_id=home.id, target_amount=EMERGENCY_TARGET,
                                            held_amount=EMERGENCY_HELD, held_in=f"{LIQUID[2]}, {LIQUID[1]}", statement_date=ago(5),
                                            document=_proof("Account statement and the signed build plan"), build_months=6,
                                            complete_by=today + timedelta(days=92))

    # Nominations on every folio, the provident fund and the deposits: two children, equally.
    wanted = [(who, "mutual_fund", folio) for _, who, folio, what in folios] + [(VIVEK, "mutual_fund", LIQUID[1]),
              (VIVEK, "epf", "Provident fund (demonstration)"), (ANJALI, "epf", "Provident fund (demonstration)"),
              (VIVEK, "nps", "National Pension System account (demonstration)"), (ANJALI, "nps", "National Pension System account (demonstration)"),
              (VIVEK, "bank_account", "Fixed deposits, a demonstration bank (invented)")]
    for who, kind, label in wanted:
        if who not in people:
            continue
        await india_estate.record_nomination(s, firm, by=BY, person_id=people[who].id, asset_kind=kind, label=label, status="nominated",
                                             nominees=CHILDREN, document=_proof("Nomination form, acknowledged"))


def _spouse(who: str, people: dict) -> dict:
    """Each of the couple names the other on their own policies."""
    other = ANJALI if who == VIVEK else VIVEK
    return {"nominee": people[other].full_name, "nominee_relationship": "spouse"} if other in people else {}


async def _protection(c, home, people: dict) -> None:
    """What the family brought and what week 2 placed: term cover, health cover and two old endowments."""
    s, firm, ago = c.s, c.firm, c.ago
    ops = "operations"
    placed = ago(DAY0 - 12)
    for who, number, cover, premium, when in ((VIVEK, "DEMO-MEH-TERM-01", 5_000_000, 14_000, "held"),
                                              (VIVEK, "DEMO-MEH-TERM-02", 30_000_000, 52_000, "placed"),
                                              (ANJALI, "DEMO-MEH-TERM-03", 15_000_000, 22_000, "placed")):
        if who not in people:
            continue
        await protection.record_policy(s, firm.id, recorded_by=BY, person_id=people[who].id, kind="life", provider="Life insurer, term (demonstration)",
                                       sum_insured=cover, annual_premium=premium, policy_number=number, **_spouse(who, people),
                                       expires_on=c.today + timedelta(days=14 * 365),
                                       source_document=_proof("Policy schedule" if when == "held" else f"Policy schedule, issued {placed.isoformat()}"))
    everyone = f"{VIVEK}: self; {ANJALI}: spouse; {RHEA}: daughter; {DEV}: son"
    for number, renews, terms in (
            ("DEMO-MEH-GRP-1", 140, dict(kind="employer_group", provider="Employer group health (demonstration)", sum_insured=500_000, members=everyone)),
            ("DEMO-MEH-FLT-2", 320, dict(kind="family_floater", provider="Health insurer (demonstration)", sum_insured=2_500_000, members=everyone,
                                         annual_premium=41_000, waiting_periods="Pre-existing diseases: 36 months")),
            ("DEMO-MEH-STU-3", 320, dict(kind="super_top_up", provider="Health insurer (demonstration)", sum_insured=7_500_000, deductible=2_500_000,
                                         members=everyone, annual_premium=12_500)),
            ("DEMO-MEH-SNR-4", 250, dict(kind="individual", provider="Senior health insurer (demonstration)", sum_insured=500_000,
                                         members=f"{MOTHER}: mother", annual_premium=46_000, co_payment_percent=20, co_payment_terms="on every claim"))):
        if len(people) < 2:
            break          # the family's schedules name both of them
        await health_cover.record_policy(s, firm.id, recorded_by=BY, role=ops, household_id=home.id, policy_number=number,
                                         source_document=f"{SOURCE} (schedule {number})", renewal_on=c.today + timedelta(days=renews), **terms)
    stated = ago(DAY0 + 5)
    for number, who, sum_assured, paid, premium, left, surrender, paid_up, years_to, maturity in (
            ("DEMO-MEH-END-1", VIVEK, 500_000, 360_000, 30_000, 6, 262_000, 330_000, 6, 590_000),
            ("DEMO-MEH-END-2", ANJALI, 400_000, 220_000, 22_000, 9, 158_000, 205_000, 9, 480_000)):
        if who not in people:
            continue
        made = await protection.record_policy(
            s, firm.id, recorded_by=BY, person_id=people[who].id, kind="life", provider="Life insurer, endowment (demonstration)",
            sum_insured=sum_assured, policy_number=number, source_document=f"{SOURCE} (policy bond {number})", **_spouse(who, people))
        await policy_values.record_statement(
            s, firm.id, recorded_by=BY, role=ops, policy_id=uuid.UUID(made["policy_id"]), statement_date=stated, product="endowment",
            premiums_paid=paid, annual_premium=premium, premium_years_left=left, surrender_value=surrender, paid_up_value=paid_up,
            maturity_on=stated + timedelta(days=years_to * 365 + 2), maturity_value=maturity, maturity_basis="guaranteed",
            commenced_on=stated - timedelta(days=12 * 365), source_document=f"{SOURCE} (statement {number})")
        if number == "DEMO-MEH-END-1":
            await protection.end_policy(s, firm.id, uuid.UUID(made["policy_id"]), by=BY,
                                        reason="Surrendered after the surrender-or-continue layout: the analysis is on file")


async def _money(c, people: dict, accounts: dict) -> None:
    """Verified banks: what the SIPs and their mandates are paid from."""
    for who, last4 in ((VIVEK, "6120"), (ANJALI, "7741")):
        if who not in people:
            continue
        await money_in.record_link(c.s, c.firm.id, by=BY, account_id=accounts[who].id, bank=BANK, last4=last4, owner_name=people[who].full_name,
                                   method="voided_cheque", evidence=_proof("Cancelled cheque, filed"), verified_on=c.ago(DAY0 - 1))


async def _goals(c, home, accounts: dict) -> None:
    """Goals with their tiers, then the SIPs that save towards them, their mandates and first debits."""
    from app.llm.executors import ToolExecutors

    s, firm, today, ago = c.s, c.firm, c.today, c.ago
    adviser = c.users[UserRole.ADVISER]
    tools = ToolExecutors(s, adviser.id, UserRole.ADVISER, firm.id)
    education = {}
    # Four years each. The two together come to about Rs 1.4 crore once grown at education inflation to the years they fall in.
    for student, start, cost in ((RHEA, today.year + 2, 1_150_000), (DEV, today.year + 4, 1_150_000)):
        row = await financial_plan.record_education(s, home.id, firm_id=firm.id, by=BY, student=student, start_year=start, years=4, annual_cost=cost,
                                                    cost_source=_proof("College fee schedule, invented"))
        education[student] = row
    retire = await goal_record.add(s, firm, home.id, {
        "name": "Retirement at 58", "kind": "retirement", "target": 15_000_000, "target_basis": "today", "retirement_age": 58,
        "retirement_spending": 1_200_000, "tier": "needs", "priority": "high", "monthly_contribution": 40_000, "funding_share_percent": 33}, by=BY)
    await goal_record.add(s, firm, home.id, {
        "name": "Rhea's wedding", "kind": "wedding", "target": 3_000_000, "target_basis": "today", "target_date": str(today.year + 10),
        "tier": "wishes", "priority": "low"}, by=BY)
    await tools.update_goal({"goal_id": str(retire.id), "plan_to_age": 90})
    for student, share in ((RHEA, "34"), (DEV, "33")):
        await tools.update_goal({"goal_id": str(education[student].id), "tier": "needs", "monthly_contribution": 10_000, "funding_share_percent": share})

    day = min(ago(DAY0 - 12).day, 28)
    starts = ago(DAY0 - 8)
    plan = [(VIVEK, "6120", NIFTY_D[1], 40_000, retire, "nach", "UMRN-DEMO-MEHTA-0001", {"step_up_percent": 10, "step_up_every_months": 12, "step_up_cap": 80_000}),
            (VIVEK, "6120", FLEXI_D[1], 10_000, education[RHEA], "upi_autopay", "UPI-DEMO-MEHTA-0002", {}),
            (ANJALI, "7741", CORP_D[1], 10_000, education[DEV], "upi_autopay", "UPI-DEMO-MEHTA-0003", {})]
    for who, last4, symbol, amount, goal, rail, reference, step in plan:
        account = accounts.get(who)
        if account is None:
            continue
        limit = step.get("step_up_cap", amount)
        await sip_mandates.record(s, firm, UserRole.OPERATIONS, by=BY, account_id=account.id, reference=reference, mandate_type=rail,
                                  per_debit_limit=limit, starts_on=starts, last4=last4, document=_proof("Mandate registration, filed"))
        await sip.record(s, firm, account.id, by=BY, instrument=symbol, amount=amount, day_of_month=day, starts_on=starts, ends_on=goal.target_date,
                         evidence_ref=None, simulated=True, goal_id=goal.id, **step)
        debited = sip.next_instalment(day, starts, starts)
        if debited <= today:
            await india_money.record_first_debit(s, firm, UserRole.OPERATIONS, by=BY, account_id=account.id, reference=reference, outcome="confirmed",
                                                 debited_on=debited, amount=amount, document=_proof("Bank's debit confirmation"))


async def _reviews(c, people: dict) -> None:
    """The regime comparison run in week 3, recorded as a review held for each (B-396). No harvest review is recorded: the
    year-end task is raised from January."""
    began = markets.tax_year(markets.MARKETS["IN"], c.today)["start"]
    for who, person in people.items():
        await india_tax_year.record_review(
            c.s, c.firm, UserRole.ADVISER, by=BY, person_id=person.id, kind="regime",
            covered="Both regimes read with the employer's NPS contribution; the choice stays theirs",
            reviewed_on=max(began, c.ago(DAY0 - 18)), document=_proof("Meeting note"))


ADVICE = [
    ("protection", "Place term cover of Rs 3 crore on Vivek and Rs 1.5 crore on Anjali", "Term cover on file was Rs 50 lakh against the computed need.",
     [("Keep the existing term policy only", "Leaves most of the computed need uncovered"), ("Buy a unit-linked plan for the cover", "Cover and investment bought together cost more for less cover")], "implemented"),
    ("protection", "Take a family floater of Rs 25 lakh with a Rs 75 lakh super top-up", "The only health cover was the employer's, which ends with the job.",
     [("Rely on the employer's group cover", "It ends with the job")], "implemented"),
    ("cash_debt", "Build an emergency fund of Rs 9 lakh in a liquid fund over six months", "No emergency fund was on file against essential spending of Rs 18 lakh a year.",
     [("Use the fixed deposits", "They are set aside and break with a penalty"), ("Start the long-term SIPs first", "A job loss would stop them")], "accepted"),
    ("tax", "Stay on the new tax regime and direct the employer's NPS contribution under section 80CCD(2)", "The deductions on file do not reach the old regime's breakeven.",
     [("Move to the old regime", "Costs more tax on the deductions on file")], "accepted"),
    ("protection", "Surrender one old endowment policy and keep the other under review", "Both statements imply a low yearly return on continuing.",
     [("Continue both", "The return the statements imply is low"), ("Surrender both", "The second is closer to maturity and was kept for now")], "implemented"),
    ("retirement", "Save Rs 60,000 a month by SIP, stepping the retirement SIP up 10 percent a year in the mandate", "Rs 60,000 a month of fresh saving fits the cash flow on file.",
     [("A flat SIP raised by an annual resolution", "The raise is often not made")], "implemented"),
    ("estate", "Brief a lawyer to draft both wills from the asset register", "No will is on file for either of them.",
     [("Rely on nominations alone", "A nominee does not take over succession law")], "accepted"),
    ("investment", "Decline the unit-linked plan the family's bank proposed", "The bank pitched a unit-linked plan during the consolidation.",
     [("Take the unit-linked plan", "Charges and lock-in for cover already bought as term insurance")], "accepted"),
    ("investment", "Hold gold at no more than 10 percent of investable assets", "The family asked to add to gold.",
     [("Add to gold as asked", "Takes the holding past the cap")], "accepted"),
]


async def confirm_risk_profiles(s: AsyncSession, firm: Firm) -> int:
    """The family's confirmation of its risk profile by registered email, given after the answers and recorded for the
    household, which is where a recommendation looks for it. Recorded once. The demonstration's own recommendations, made
    after it, then keep the basis they rest on."""
    home = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == HOME))).scalars().first()
    if home is None:
        return 0
    done = 0
    basis = await advice_practice.risk_basis(s, firm, home.id, None, firm_time.today(firm))
    if basis["status"] == "on_file" and not basis["consent"]:
        await consents.record(s, firm.id, by=BY, case_id=None, household_id=home.id, kind="risk_profile", status="granted", given_by=VIVEK,
                              given_on=date.fromisoformat(basis["answered_on"]), method="email",
                              evidence=_proof("Risk profile confirmed by registered email, after the answers"))
        done += 1
    for rec in (await s.execute(select(AdviceRecommendation).where(
            AdviceRecommendation.firm_id == firm.id, AdviceRecommendation.household_id == home.id,
            AdviceRecommendation.recommendation.in_([a[1] for a in ADVICE])))).scalars().all():
        kept = rec.risk_basis
        if kept and kept.get("status") == "on_file" and not kept.get("consent"):
            fresh = await advice_practice.risk_basis(s, firm, home.id, rec.person_id, rec.recommended_on)
            if fresh.get("consent"):
                rec.risk_basis = fresh
                done += 1
    await s.flush()
    return done


async def _advice_and_plan(c, home, adviser) -> None:
    s, firm, today, ago = c.s, c.firm, c.today, c.ago
    for n, (category, text, facts, alternatives, outcome) in enumerate(ADVICE):
        made = ago(DAY0 - 14 - n)
        out = await advice_file.record_recommendation(
            s, firm.id, recorded_by=adviser.email, role="adviser", by_user=adviser.id, household_id=home.id, category=category, recommendation=text,
            facts=facts, alternatives=[{"option": o, "why_rejected": w} for o, w in alternatives], recommended_on=made, data_as_of=made,
            decision="accepted", evidence_kind="note", evidence=_proof("Meeting note: the family agreed"), decided_on=made)
        if outcome == "implemented":
            rec_id = out.get("id") or out.get("recommendation_id") or str((await s.execute(select(AdviceRecommendation.id).where(
                AdviceRecommendation.household_id == home.id, AdviceRecommendation.recommendation == text))).scalar_one())
            await advice_file.record_outcome(s, firm.id, recorded_by=adviser.email, role="adviser", recommendation_id=rec_id, status="implemented",
                                             evidence_kind="document", evidence=_proof("Policy schedule, statement or mandate on file"),
                                             happened_on=ago(max(DAY0 - 30, 0)))
    planner = c.users[UserRole.PARAPLANNER]
    await plan_record.record_baseline(s, firm.id, role="adviser", recorded_by=adviser.email, user_id=adviser.id, household_id=home.id)
    await plan_record.record_review(s, firm.id, role="paraplanner", recorded_by=planner.email, user_id=planner.id, household_id=home.id,
                                    outcome="passed", findings="Checked against the statements on file (demonstration).")
    await plan_record.record_delivery(
        s, firm.id, role="adviser", recorded_by=adviser.email, user_id=adviser.id, household_id=home.id, evidence_kind="note",
        evidence=_proof("Plan meeting note, signed by both"), delivered_on=today,
        action_items=[{"title": "Mehtas: brief a lawyer on both wills with the asset register", "due_on": (today + timedelta(days=30)).isoformat()},
                      {"title": "Mehtas: complete the emergency fund build", "due_on": (today + timedelta(days=92)).isoformat()}])


# ── The brother in Dubai ──────────────────────────────────────────────────────

async def _brother(c) -> int:
    """Sameer, non-resident, in a household of his own: the NRI facts first, then the account, the remittance and his goal."""
    s, firm, today, ago = c.s, c.firm, c.today, c.ago

    async def open_household():
        home = Household(firm_id=firm.id, name=BROTHER_HOME, segment=ClientSegment.PRIVATE_WEALTH,
                         values={"jurisdiction": "IN", "demonstration": True})
        s.add(home)
        await s.flush()
        fee = await _fee_schedule(c, FEE_CODE + "-N", "Fixed advisory fee, non-resident (demonstration)", 60_000)
        _, sameer, account, _ = await _onboard(
            c, home, SAMEER, registration="nri_nre", born=_born(today, 52, 1, 19), model="Core Balanced", risk="balanced", fee=fee,
            registry_first="registered", mode="video_kyc", band="medium", bank_last4="5588", residence="AE", channel="remote",
            answers=_answers({"investment_horizon": 2, "loss_tolerance": 1, "income_stability": 1, "liquidity_needs": 2, "esg_priority": 0,
                              "experience": 1}), billable=2_500_000)
        return home, sameer, account

    opened = await _attempt(c, f"The onboarding case for {SAMEER} (a later run tries again)", open_household)
    if opened is None:
        return 0
    home, sameer, account = opened

    async def non_resident():
        await _agreement(c, home)
        year = markets.tax_year(markets.MARKETS["IN"], today)["label"]
        await residency.record(
            s, firm, by=BY, person_id=sameer.id, tax_year=year, document=_proof("Passport and residence visa scans, travel record"),
            indian_citizen_or_pio=True, fema_status="nri", country_of_residence="United Arab Emirates", tax_residencies=["United Arab Emirates"],
            days_in_india=34, days_prior_four_years=150, days_prior_seven_years=280, nonresident_years_of_prior_ten=10, left_for_employment_abroad=True,
            india_income=210_000, taxed_elsewhere=False, accounts=["NRE"], us_person=False, self_certified_on=ago(DAY0 - 2))
        account.cash_balance = BOOK[SAMEER][0]
        await _beneficiaries(c, account, [("Meher Mehta", "wife", 100)])
        # The paper a treaty claim needs (B-398): his tax residency certificate for this tax year.
        from_year = date(today.year if today.month >= 4 else today.year - 1, 4, 1)
        await nri_planning.record_certificate(s, firm, by=BY, person_id=sameer.id, kind="trc", country="United Arab Emirates",
                                              reference="DEMO-TRC-MEH-0001", valid_from=from_year, valid_to=date(from_year.year + 1, 3, 31),
                                              document=_proof("Certificate from the foreign tax authority"))
        return True

    async def money():
        await money_in.record_link(s, firm.id, by=BY, account_id=account.id, bank=BANK, last4="5588", owner_name=SAMEER, method="voided_cheque",
                                   evidence=_proof("NRE cheque leaf, filed"), verified_on=ago(DAY0 - 2))
        link = await money_in_link(s, firm, account)
        await india_money.record_remittance(
            s, firm, UserRole.OPERATIONS, by=BY, account_id=account.id, bank_link_id=link, account_kind="nre", received_on=ago(DAY0 - 7),
            amount_inr=2_500_000, certificate_reference="FIRC-DEMO-000071", remitter_name=SAMEER, foreign_amount=108_000, foreign_currency="AED",
            document=_proof("Bank's inward remittance certificate"))
        return True

    async def goal():
        await goal_record.add(s, firm, home.id, {"name": "Return to India", "kind": "other", "target": 3_000_000, "target_basis": "today",
                                                 "target_date": str(today.year + 8), "tier": "wants", "priority": "medium",
                                                 "monthly_contribution": 25_000, "funding_share_percent": 100}, by=BY)
        return True

    await _attempt(c, "The brother's agreement, residence, nominee and certificate", non_resident)
    await _attempt(c, "The brother's bank and remittance", money)
    await _attempt(c, "The brother's goal", goal)
    return 1


async def money_in_link(s, firm, account) -> uuid.UUID:
    from app.models.money_in import BankLink

    return (await s.execute(select(BankLink.id).where(BankLink.firm_id == firm.id, BankLink.account_id == account.id))).scalars().first()
