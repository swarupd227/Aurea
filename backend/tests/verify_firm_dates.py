"""Prove "today" is the firm's day, not the server's, and that a firm with no market gets no country's rules.

The server runs on UTC, so for most of a New Zealand morning its date is still yesterday, and for a New York
evening it is already tomorrow. An age, an RMD, a three-year review and a tax year each turn over on the firm's
calendar (B-73). The clock is pinned by replacing firm_time's utcnow, so every path that asks firm_time for
today reads the same moment.

The second half pins B-121: a firm in a country with no market gets no disclosure set, no regime and no New
Zealand trust law, and an account is sheltered by its registration, not a word in its name.

Runs against a throwaway SQLite database.

    python -m tests.verify_firm_dates
"""
from __future__ import annotations

import asyncio
import inspect
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from types import SimpleNamespace as N

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import asset_location, client_care, estate_succession, glide_path
from app.aurea_core import exits
from app.aurea_core import disclosures, estate, firm_time, markets, protection, registration, tax_intelligence
from app.compliance import ontology
from app.core.db import Base
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.tenant import Firm

PASS, FAIL = [], []

# 11:30 UTC on 17 Nov is 00:30 on 18 Nov in Auckland (NZDT, +13).
NZ_MORNING = datetime(2026, 11, 17, 11, 30, tzinfo=timezone.utc)
# 02:00 UTC on 18 Nov is 21:00 on 17 Nov in New York (EST, -5).
NY_EVENING = datetime(2026, 11, 18, 2, 0, tzinfo=timezone.utc)
# 11:30 UTC on 31 Dec is 00:30 on 1 Jan in Auckland.
NZ_NEW_YEAR = datetime(2026, 12, 31, 11, 30, tzinfo=timezone.utc)
# 12:00 UTC on 31 Mar is 01:00 on 1 Apr in Auckland: a new New Zealand tax year.
NZ_TAX_YEAR = datetime(2027, 3, 31, 12, 0, tzinfo=timezone.utc)


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


@contextmanager
def clock(moment: datetime):
    """Every firm_time.today / today_of in this block reads `moment` as now."""
    real = firm_time.utcnow
    firm_time.utcnow = lambda: moment
    try:
        yield
    finally:
        firm_time.utcnow = real


def pure() -> None:
    nz, us = N(settings={}, jurisdiction="NZ"), N(settings={}, jurisdiction="US")

    print("\n=== The firm's day is not the server's ===")
    check("UTC says the 17th", NZ_MORNING.date(), date(2026, 11, 17))
    check("Auckland says the 18th", firm_time.today(nz, NZ_MORNING), date(2026, 11, 18))
    check("New York is still the 17th when UTC is the 18th", firm_time.today(us, NY_EVENING), date(2026, 11, 17))
    with clock(NZ_MORNING):
        check("today() with no moment reads the pinned clock", firm_time.today(nz), date(2026, 11, 18))

    print("\n=== An age turns over on the firm's day ===")
    nz_today = firm_time.today(nz, NZ_MORNING)
    check("client care: 66 on the firm's day", client_care._age("1960-11-18", nz_today), 66)
    check("client care: 65 on the server's", client_care._age("1960-11-18", NZ_MORNING.date()), 65)
    check("estate & succession reads the same age", estate_succession._age("1960-11-18", nz_today), 66)

    print("\n=== The RMD age is the firm's ===")
    brain = {"persons": [{"full_name": "Ruth", "date_of_birth": "1953-11-18",
                          "profile": {"tax": {"ira_value": 500_000}}}]}
    ny_today = firm_time.today(us, NY_EVENING)
    firm_rmd = tax_intelligence._us_rmd_planning(brain, today=ny_today)["alerts"][0]
    server_rmd = tax_intelligence._us_rmd_planning(brain, today=NY_EVENING.date())["alerts"][0]
    check("72 on the firm's day: no RMD yet", (firm_rmd["age"], firm_rmd["rmd_required"]), (72, False))
    check("the server's date would have demanded one", (server_rmd["age"], server_rmd["rmd_required"]), (73, True))

    print("\n=== A goal's horizon is counted from the firm's day ===")
    goal = {"target_date": "2027-11-18"}
    check("a year to go on the firm's day", round(glide_path._years_to_goal(goal, nz_today), 3), round(365 / 365.25, 3))
    check("a day more on the server's", round(glide_path._years_to_goal(goal, NZ_MORNING.date()), 3), round(366 / 365.25, 3))

    print("\n=== A three-year trust review is due on the firm's New Year ===")
    trust = [{"name": "Family Trust", "governance": {"trustees": ["A"], "settlor": "B", "beneficiaries": ["C"],
                                                     "established": "2024-03-01"}}]
    family = {"totals": {"total_value": 0, "by_asset_class": {}}, "persons": [], "mandates": [], "entities": trust, "goals": []}
    due = lambda day: [g["type"] for g in estate_succession._succession_gaps(family, trust, "NZ", today=day)]
    check("overdue on 1 Jan 2027 in Auckland", "trust_review_overdue" in due(firm_time.today(nz, NZ_NEW_YEAR)), True)
    check("not yet on the server's 31 Dec 2026", "trust_review_overdue" in due(NZ_NEW_YEAR.date()), False)

    print("\n=== The tax year is the firm's ===")
    nz_market = markets.MARKETS["NZ"]
    check("1 Apr in Auckland is the new year", markets.tax_year(nz_market, firm_time.today(nz, NZ_TAX_YEAR))["label"], "2027-28")
    check("the server's 31 Mar is still the old one", markets.tax_year(nz_market, NZ_TAX_YEAR.date())["label"], "2026-27")

    print("\n=== A firm with no market gets no country's rules (B-121) ===")
    check("no disclosure set for Singapore: activation is held",
          disclosures.required_for("SG", "individual"), ["jurisdiction_not_supported"])
    check("nor for Australia, which has no market here", disclosures.required_for("AU", "individual"), ["jurisdiction_not_supported"])
    check("the status names Singapore, not New Zealand", disclosures.status_for("SG", "individual", [])["jurisdiction"], "SG")
    check("the NZ set is unchanged", disclosures.required_for("NZ", "individual"), ["disclosure_statement", "soa", "privacy_notice"])
    check("aliases still resolve", disclosures.required_for("United Kingdom", "individual"), ["terms_of_business", "kid", "privacy_notice"])
    check("no regime for Singapore", ontology.framework_for(N(jurisdiction="SG", regulator="MAS")).regime, "NOT-SUPPORTED")
    check("no NZ trust law for a trust with no market",
          "NZ" in estate_succession._trust_review_recommendation(None) + estate_succession._trust_governance_recommendation(None), False)
    check("New Zealand keeps its own", "NZ Trusts Act" in estate_succession._trust_review_recommendation("NZ"), True)
    from app.agents import onboarding, reverse_churning, wallet_share_scout
    for mod in (asset_location, glide_path, estate_succession, estate, reverse_churning, wallet_share_scout, onboarding,
                disclosures, ontology):
        src = inspect.getsource(mod)
        check(f"{mod.__name__.rsplit('.', 1)[-1]} reads no firm as New Zealand",
              any(f in src for f in ('or "NZ"', 'else "NZ"', 'str = "NZ"', '"jurisdiction", "NZ"')), False)

    print("\n=== Sheltered is the registration, not the name (B-121) ===")
    pos = [{"instrument": "BOND", "asset_class": "fixed_income", "market_value": 50_000}]
    brain = {"accounts": [{"id": "a", "name": "KiwiSaver IRA pension", "positions": pos},
                          {"id": "b", "name": "Brokerage", "positions": pos}]}
    found = asset_location._analyze_location(brain, {"a": "individual", "b": "traditional_ira"})
    check("an individual account named like an IRA is taxable", [f["account"] for f in found], ["KiwiSaver IRA pension"])
    check("an IRA named 'Brokerage' is sheltered, so bonds in it raise nothing",
          any(f["account"] == "Brokerage" for f in found), False)
    brain = {"accounts": [{"id": "s", "name": "SEP", "positions": pos}, {"id": "m", "name": "SIMPLE", "positions": pos}]}
    found = asset_location._analyze_location(brain, {"s": "sep_ira", "m": "simple_ira"})
    check("a SEP IRA and a SIMPLE IRA are sheltered too", [f["account"] for f in found], [])
    check("and neither steps up at death", {"sep_ira", "simple_ira"} <= exits._RETIREMENT, True)


async def db_checks() -> None:
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            nz = Firm(id=uuid.uuid4(), name="Kauri", slug="kauri", jurisdiction="NZ", base_currency="NZD")
            us = Firm(id=uuid.uuid4(), name="Hudson", slug="hudson", jurisdiction="US", base_currency="USD")
            sg = Firm(id=uuid.uuid4(), name="Merlion", slug="merlion", jurisdiction="SG", regulator="MAS", base_currency="SGD")
            s.add_all([nz, us, sg])
            await s.flush()

            print("\n=== Protection: an earner's age on the firm's day ===")
            hh = Household(id=uuid.uuid4(), firm_id=nz.id, name="Ngata", values={})
            s.add(hh)
            await s.flush()
            s.add(Person(id=uuid.uuid4(), firm_id=nz.id, household_id=hh.id, full_name="Aroha Ngata", is_next_gen=False,
                         date_of_birth=date(1980, 11, 18), profile={"tax": {"annual_income": 120_000, "marginal_rate": 0.33}}))
            await s.flush()
            with clock(NZ_MORNING):
                view = await protection.for_household(s, hh.id, firm_id=nz.id)
            check("46 on Auckland's 18 Nov, not 45 on the server's 17th", view["people"][0]["age"], 46)

            print("\n=== An IRA's RMD starts on the firm's day ===")
            us_hh = Household(id=uuid.uuid4(), firm_id=us.id, name="Okafor", values={})
            s.add(us_hh)
            await s.flush()
            ruth = Person(id=uuid.uuid4(), firm_id=us.id, household_id=us_hh.id, full_name="Ruth Okafor", is_next_gen=False,
                          date_of_birth=date(1953, 11, 18), profile={"tax": {"ira_value": 500_000}})
            s.add(ruth)
            await s.flush()
            m = Mandate(id=uuid.uuid4(), firm_id=us.id, person_id=ruth.id, name="M", mandate_type="advisory")
            s.add(m)
            await s.flush()
            ira = Account(id=uuid.uuid4(), firm_id=us.id, mandate_id=m.id, name="IRA", currency="USD", cash_balance=0,
                          registration_type="traditional_ira")
            s.add(ira)
            await s.flush()
            with clock(NY_EVENING):
                rmd = await registration.rmd_for_account(s, ira)
                report = await tax_intelligence.for_household(s, us_hh.id, "US", firm_id=us.id)
            check("the account: 72 in New York, so not yet", (rmd["status"], rmd["owner_age"]), ("not_yet", 72))
            alert = report["rmd_planning"]["alerts"][0]
            check("the tax report agrees: no RMD demanded", (alert["age"], alert["rmd_required"]), (72, False))
            check("and it is dated on the firm's day", report["generated_at"], "2026-11-17")

            print("\n=== Estate: a trust review falls due on the firm's New Year ===")
            whanau = Household(id=uuid.uuid4(), firm_id=nz.id, name="Whanau", values={})
            s.add(whanau)
            await s.flush()
            s.add(LegalEntity(firm_id=nz.id, household_id=whanau.id, name="Whanau Trust", entity_type="trust", jurisdiction="NZ",
                              governance={"trustees": ["A"], "settlor": "B", "beneficiaries": ["C"], "established": "2024-03-01"}))
            await s.flush()
            with clock(NZ_NEW_YEAR):
                view = await estate.for_household(s, whanau.id, firm_id=nz.id)
            check("overdue on 1 Jan 2027 in Auckland", "trust_review_overdue" in [g["type"] for g in view["succession_gaps"]], True)
            check("the estate view is dated on the firm's day", view["generated_at"], "2027-01-01")

            print("\n=== Estate for a firm with no market (B-121) ===")
            lim = Household(id=uuid.uuid4(), firm_id=sg.id, name="Lim", values={})
            s.add(lim)
            await s.flush()
            s.add(LegalEntity(firm_id=sg.id, household_id=lim.id, name="Lim Trust", entity_type="trust", jurisdiction="SG",
                              governance={"trustees": ["A"]}))
            await s.flush()
            view = await estate.for_household(s, lim.id, firm_id=sg.id)
            words = " ".join(g["recommendation"] for g in view["succession_gaps"])
            check("the trust carries no country's note", view["trust_structures"][0]["compliance_note"], None)
            check("and no gap cites New Zealand law", ("NZ" in words, "Trusts Act 2019" in words), (False, False))
            check("and there is no step-up reading", view["step_up_basis"], None)
    finally:
        await db.dispose()


async def main() -> int:
    pure()
    await db_checks()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
