"""The PMLA programme on the demonstration firm (B-390): screening, ratings, politically exposed persons, enhanced due
diligence, transaction monitoring and reports to FIU-IND.

Every record is invented for the demonstration firm and says so. THE SCREENING LIST ENTRIES ARE INVENTED: they are not
entries of any UNSC, UAPA or politically-exposed-persons list, and name no real person. They are written only to the
demonstration firm's own recorded list changes, marked as demonstration data. The filing references are invented, and
nothing was filed with anyone. Run from `seed.india_demo` when the firm is built and from `seed.india_demo_backfill` on
a firm built before; it adds nothing where a demonstration list change is already on file. `redate` keeps the dated
scenarios at their distance from today and leaves anything a person has since acted on.

  Prakash Aggarwal      an open sanctions hit: a spelling variant of a listed name, father's name and birth date differ
  Lakshman Chowdhury    a hit decided a false positive, sampled for QA
  Girish Nambiar        a PEP match called true by operations, waiting for compliance as the second reviewer
  Harish Rathore        a confirmed PEP, rated high, source-of-wealth file signed off, senior approval outstanding
  Sunita Bhattacharya   marked a PEP on her KYC record: enhanced due diligence open, to sign off
  Deepak Malhotra       a cash business, rated medium, enhanced due diligence open; a later list change not yet screened
  Ritu Saxena           rated low, customer due diligence
  The D'Souza Household deposits under the cash line, investigated and a report recorded filed within the line
  The Banerjee Household money in and straight back out, suspicion formed, the report still due
  The Menons            deposits under the cash line, an alert not yet decided
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import aml_review, aml_risk, fiu_reports, firm_time, kyc_india, screening_lists, txn_monitoring
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.aml_monitoring import AmlInvestigation
from app.models.enums import ClientSegment, OnboardingStatus
from app.models.graph import Account, Household
from app.models.onboarding import OnboardingCase, OnboardingParty
from app.models.pmla import ScreeningListUpdate
from app.models.portfolio import Transaction
from app.models.tenant import Firm
from seed.india_row7 import NAME as MENONS

SOURCE = "Demonstration record, invented for the demonstration firm"
OPS = "Suresh Pillai, operations (seeded, demonstration)"
COMPLIANCE = "Neha Kulkarni, compliance (seeded, demonstration)"
SEED = "india_pmla"
INVENTED = "Invented for the demonstration; names no real person."
FIRST_LIST_DAYS, LATER_LIST_DAYS, SCREENED_DAYS = 40, 4, 10
LATER_VERSION = "DEMO-UAPA-02"

# (list, version, entries). Invented, and marked so wherever they are shown.
FIRST_LISTS = (
    ("unsc", "DEMO-UNSC-01", [("Laxman Choudhary", "Bhola Choudhary", "1969-02-11")]),
    ("uapa", "DEMO-UAPA-01", [("Prakash Agarwal", "Shyam Lal Agarwal", "1966-07-04")]),
    ("pep", "DEMO-PEP-01", [("Harish Rathore", "Bhanwar Singh Rathore", "1964-05-19"), ("Girish Nambiar", "K P Nambiar", "1971-10-02")]),
)
LATER_ENTRIES = [("Dipak Malhotra", "Ram Prasad Malhotra", "1968-03-30")]

# name -> (date of birth, father's name, source of wealth in the intake)
CASES = {
    "Prakash Aggarwal": ("1978-03-22", "Mahendra Aggarwal", "Salary"),
    "Lakshman Chowdhury": ("1985-08-14", "Gopal Chowdhury", "Salary and investments"),
    "Girish Nambiar": ("1971-10-02", "K P Nambiar", "Consulting income"),
    "Harish Rathore": ("1964-05-19", "Bhanwar Singh Rathore", "Public office salary and family agricultural land"),
    "Sunita Bhattacharya": ("1969-12-08", "Ashok Bhattacharya", "Salary from public service"),
    "Deepak Malhotra": ("1975-12-01", "Om Prakash Malhotra", "Jewellery shop, much of it taken in cash"),
    "Ritu Saxena": ("1990-06-17", "Anil Saxena", "Salary"),
}

# household -> [(txn_type, amount, days before today)]
MONEY = {
    "The D'Souza Household": [("deposit", 920_000, 38), ("deposit", 870_000, 35)],
    "The Banerjee Household": [("deposit", 2_400_000, 16), ("withdrawal", 2_300_000, 12)],
    MENONS: [("deposit", 950_000, 9), ("deposit", 900_000, 7)],
}
REPORTED, OPEN = "The D'Souza Household", "The Banerjee Household"
REPORTED_SUSPICION_DAYS, REPORTED_WITHIN, OPEN_WORKING_DAYS_AGO = 24, 5, 3


def _entry(name, father, dob) -> dict:
    return {"name": name, "father_name": father, "dob": dob, "note": INVENTED}


def _weekdays_back(day: date, n: int) -> date:
    """The weekday that is n working days before `day`."""
    while n:
        day -= timedelta(days=1)
        if day.weekday() < 5:
            n -= 1
    return day


def _investigation_days(today: date) -> dict:
    """The day suspicion was formed, and reported, for the two seeded investigations."""
    reported = today - timedelta(days=REPORTED_SUSPICION_DAYS)
    while reported.weekday() >= 5:
        reported -= timedelta(days=1)
    return {REPORTED: (reported, txn_monitoring._add_working_days(reported, REPORTED_WITHIN)),
            OPEN: (_weekdays_back(today, OPEN_WORKING_DAYS_AGO), None)}


def _list_row(firm, key, version, entries, days: int, today: date) -> ScreeningListUpdate:
    return ScreeningListUpdate(created_at=utcnow() - timedelta(days=days), firm_id=firm.id, list_key=key, version=version,
                               published_on=today - timedelta(days=days), entries=[_entry(*e) for e in entries], removed=[],
                               source_document=f"{SOURCE}: no such notification exists", demonstration=True, recorded_by=OPS)


async def _case(s: AsyncSession, firm: Firm, name: str, listed: list) -> tuple[OnboardingCase, OnboardingParty]:
    dob, father, wealth = CASES[name]
    case = OnboardingCase(firm_id=firm.id, prospect_name=name, is_entity=False, registration_type="individual",
                          segment=ClientSegment.PRIVATE_WEALTH, status=OnboardingStatus.REVIEW, sla_days=15, engagement_type="advisory",
                          intake={"source_of_wealth": wealth, "demonstration": SOURCE}, notes=SOURCE)
    s.add(case)
    await s.flush()
    party = OnboardingParty(firm_id=firm.id, case_id=case.id, role="owner", legal_name=name, dob=dob, father_name=father, notes=SOURCE)
    screening_lists.stamp(party, listed, "Screened when recorded. Hits await a person's decision.")
    party.screened_at = utcnow() - timedelta(days=SCREENED_DAYS)
    s.add(party)
    await s.flush()
    return case, party


async def _cases(s: AsyncSession, firm: Firm) -> int:
    listed = await screening_lists.entries(s, firm.id)
    made = {name: await _case(s, firm, name, listed) for name in CASES}

    async def decide(name: str, hit: str, kind: str, decision: str, why: str, by: str, role) -> None:
        await aml_review.record_decision(s, made[name][1].id, firm_id=firm.id, by=by, role=role, hit=hit, kind=kind,
                                         decision=decision, rationale=f"{why} ({SOURCE})")

    async def rate(name: str, by: str, role, **facts) -> None:
        await aml_risk.record_rating(s, made[name][0].id, firm_id=firm.id, by=by, role=role,
                                     facts={"residence_country": "IN", "channel": "face_to_face", **facts})

    await decide("Lakshman Chowdhury", "Laxman Choudhary", "disposition", "false_positive",
                 "Father's name and date of birth both differ from the list entry", OPS, UserRole.OPERATIONS)
    await decide("Girish Nambiar", "Girish Nambiar", "disposition", "true_match",
                 "Name, father's name and date of birth match the list entry", OPS, UserRole.OPERATIONS)
    await decide("Harish Rathore", "Harish Rathore", "disposition", "true_match",
                 "Name, father's name and date of birth match the list entry", OPS, UserRole.OPERATIONS)
    await decide("Harish Rathore", "Harish Rathore", "confirmation", "confirmed",
                 "Checked against the identity document", COMPLIANCE, UserRole.COMPLIANCE)
    await rate("Harish Rathore", COMPLIANCE, UserRole.COMPLIANCE, wealth_source="other", wires_per_year=6)
    await aml_review.record_edd(
        s, made["Harish Rathore"][0].id, firm_id=firm.id, by=COMPLIANCE, role=UserRole.COMPLIANCE, wealth_type="other", decision="approve",
        conclusion="explicable", rationale=f"Salary and land income account for the wealth described ({SOURCE})",
        evidence={"source_account": "Client's signed account of salary and land income",
                  "source_documents": "Salary slips and land revenue records, copies on file",
                  "independent_corroboration": "Three years of income-tax returns"})

    sunita = made["Sunita Bhattacharya"][0]
    today = firm_time.today(firm)
    await kyc_india.record(s, firm, by="seed: india PMLA (demonstration)", case_id=sunita.id, party_name="Sunita Bhattacharya",
                           document=f"KRA acknowledgement and CKYC letter ({SOURCE}; the number is not a real one)",
                           ckyc_number="70000000000020", kra_name="CVL KRA", kra_status="validated", verification_mode="video_kyc",
                           kyc_completed_on=today - timedelta(days=20), pan_aadhaar_linked=True, risk_band="high", pep=True)
    await rate("Sunita Bhattacharya", COMPLIANCE, UserRole.COMPLIANCE, wealth_source="employment", wires_per_year=4)
    await rate("Deepak Malhotra", OPS, UserRole.OPERATIONS, wealth_source="cash_business", wires_per_year=36,
               expected_annual_inflow=6_000_000, third_party_payments=False)
    await rate("Ritu Saxena", OPS, UserRole.OPERATIONS, wealth_source="employment", wires_per_year=6, third_party_payments=False)
    return len(made)


async def _account(s: AsyncSession, firm: Firm, household: str) -> Account | None:
    """The household's first account, by name, reached through whoever holds its mandates."""
    from app.aurea_core.txn_monitoring import _households

    h = (await s.execute(select(Household).where(Household.firm_id == firm.id, Household.name == household))).scalar_one_or_none()
    if h is None:
        return None
    owners, _names = await _households(s, firm.id)
    rows = (await s.execute(select(Account).where(Account.firm_id == firm.id).order_by(Account.name))).scalars().all()
    return next((a for a in rows if owners.get(a.id) == h.id), None)


async def _monitoring(s: AsyncSession, firm: Firm) -> int:
    today = firm_time.today(firm)
    written = 0
    for household, moves in MONEY.items():
        account = await _account(s, firm, household)
        if account is None:
            continue
        for kind, amount, days in moves:
            s.add(Transaction(firm_id=firm.id, account_id=account.id, txn_type=kind, amount=amount, quantity=0, price=0,
                              trade_date=today - timedelta(days=days),
                              lineage={"source": "demonstration", "seed": SEED, "days_ago": days, "note": SOURCE}))
            written += 1
    await s.flush()
    if not fiu_reports.officers(firm)["principal_officer"]:
        return written          # the suspicious report steps need the recorded Principal Officer; none is invented here
    days = _investigation_days(today)
    for household, scenario in ((REPORTED, "under_threshold"), (OPEN, "in_and_out")):
        view = await txn_monitoring.for_firm(s, firm.id)
        alert = next((a for a in view["alerts"] if a["household"] == household and a["scenario"] == scenario and not a["decision"]), None)
        if alert is None:
            continue
        await txn_monitoring.decide_alert(s, firm.id, by=COMPLIANCE, alert_key=alert["key"], decision="escalated",
                                          reason=f"Not explained by anything on file for the household ({SOURCE})")
        inv = (await s.execute(select(AmlInvestigation).where(AmlInvestigation.firm_id == firm.id, AmlInvestigation.status == "open",
                                                              AmlInvestigation.household_id == uuid.UUID(alert["household_id"])))).scalars().first()
        if inv is None or inv.opened_by != COMPLIANCE:
            continue            # a person's investigation is open for this household: nothing is added to it
        suspicion, reported = days[household]
        await txn_monitoring.record_step(s, firm.id, by=COMPLIANCE, investigation_id=inv.id, step="suspicion_formed", on=suspicion,
                                         note=f"The movements have no explanation on file; suspicion concluded ({SOURCE})")
        written += 1
        if reported is not None:
            await txn_monitoring.record_step(s, firm.id, by=COMPLIANCE, investigation_id=inv.id, step="reported", on=reported,
                                             reference="DEMO-STR-0001 (invented acknowledgement)",
                                             note=f"Suspicious transaction report recorded as filed ({SOURCE})")
    return written


async def _reports(s: AsyncSession, firm: Firm) -> int:
    """Two months of FIU-IND reports as recorded; the latest month's Cash Transaction Report is left to record."""
    if not fiu_reports.officers(firm)["principal_officer"]:
        return 0
    today = firm_time.today(firm)
    last = today.replace(day=1) - timedelta(days=1)
    before = last.replace(day=1) - timedelta(days=1)
    wanted = [("ctr", before, 3), ("monthly", before, None), ("monthly", last, None)]
    for kind, month, count in wanted:
        period = f"{month.year}-{month.month:02d}"
        filed = min(today, fiu_reports.due_on(period) - timedelta(days=2))
        await fiu_reports.record(s, firm, UserRole.COMPLIANCE, by=COMPLIANCE, kind=kind, period=period, filed_on=filed,
                                 transactions=count, reference=f"DEMO-FIU-{kind.upper()}-{period} (invented)", note=SOURCE)
    return len(wanted)


async def add(s: AsyncSession, firm: Firm) -> int:
    if firm.slug != "india-demo" or (firm.settings or {}).get("demonstration") is not True:
        return 0
    if (await s.execute(select(ScreeningListUpdate.id).where(ScreeningListUpdate.firm_id == firm.id,
                                                             ScreeningListUpdate.demonstration.is_(True)).limit(1))).first():
        return 0
    today = firm_time.today(firm)
    for key, version, entries in FIRST_LISTS:
        s.add(_list_row(firm, key, version, entries, FIRST_LIST_DAYS, today))
    await s.flush()
    written = len(FIRST_LISTS)
    if not (await s.execute(select(OnboardingCase.id).where(OnboardingCase.firm_id == firm.id,
                                                            OnboardingCase.prospect_name.in_(list(CASES))).limit(1))).first():
        written += await _cases(s, firm)
    # A change received and not yet screened: the delta rescreen and its backlog age.
    s.add(_list_row(firm, "uapa", LATER_VERSION, LATER_ENTRIES, LATER_LIST_DAYS, today))
    await s.flush()
    written += 1 + await _monitoring(s, firm) + await _reports(s, firm)
    return written


async def redate(s: AsyncSession, firm: Firm, today: date) -> int:
    """Put the open scenarios back at their distance from today, where only the seed has touched them."""
    moved = 0
    households = {h.id: h.name for h in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    held: set = set()          # households a person has since acted on: their movements and investigation stay
    days = _investigation_days(today)
    for inv in (await s.execute(select(AmlInvestigation).where(AmlInvestigation.firm_id == firm.id))).scalars().all():
        name = households.get(inv.household_id)
        if name not in days:
            continue
        wanted_status = "reported" if name == REPORTED else "open"
        if inv.opened_by != COMPLIANCE or inv.status != wanted_status or any(step.get("by") != COMPLIANCE for step in inv.steps or []):
            held.add(name)
            continue
        suspicion, reported = days[name]
        if inv.suspicion_on == suspicion and inv.reported_on == reported:
            continue
        shift = suspicion - inv.suspicion_on
        inv.suspicion_on, inv.reported_on = suspicion, reported
        inv.detected_on = inv.detected_on + shift
        on = {"suspicion_formed": suspicion, "reported": reported}
        inv.steps = [{**step, "on": (on.get(step["step"]) or date.fromisoformat(step["on"]) + shift).isoformat()} for step in inv.steps]
        moved += 1
    owners, _names = await txn_monitoring._households(s, firm.id)
    for t in (await s.execute(select(Transaction).where(Transaction.firm_id == firm.id,
                                                        Transaction.txn_type.in_(("deposit", "withdrawal"))))).scalars().all():
        lineage = t.lineage or {}
        if lineage.get("seed") != SEED or households.get(owners.get(t.account_id)) in held:
            continue
        target = today - timedelta(days=lineage["days_ago"])
        if t.trade_date != target:
            t.trade_date = target
            moved += 1
    # The change still waiting to be screened keeps its age; once anyone has screened since, it stays where it is.
    later = (await s.execute(select(ScreeningListUpdate).where(ScreeningListUpdate.firm_id == firm.id,
                                                               ScreeningListUpdate.version == LATER_VERSION,
                                                               ScreeningListUpdate.demonstration.is_(True)))).scalars().first()
    if later is not None and later.published_on != today - timedelta(days=LATER_LIST_DAYS):
        waiting = await screening_lists.backlog(s, firm)
        if waiting["parties_in_book"] and waiting["count"] == waiting["parties_in_book"]:
            later.published_on = today - timedelta(days=LATER_LIST_DAYS)
            later.created_at = utcnow() - timedelta(days=LATER_LIST_DAYS)
            moved += 1
    await s.flush()
    return moved

