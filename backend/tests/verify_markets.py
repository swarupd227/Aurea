"""Prove a firm gets its own market's rules, and that a country with none gets a refusal, not New
Zealand's.

An Indian firm used to run NZ law silently, and "Securities and Exchange Board of India" matched
"SEC" and ran American law. The NZ, US and UK answers are pinned here too, because this replaced the
code they all went through.

Runs against a throwaway SQLite database.

    python -m tests.verify_markets
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date
from types import SimpleNamespace as N

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.aurea_core import disclosures, firm_time, markets, tax_intelligence
from app.compliance import engine, ontology
from app.compliance.rules import CheckContext
from app.core.db import Base
from app.models.graph import Household
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {got}, want {want}")


def regime(jurisdiction, regulator="") -> str:
    return ontology.framework_for(N(jurisdiction=jurisdiction, regulator=regulator)).regime


def pure() -> None:
    print("\n=== Which rules a firm gets ===")
    check("India by jurisdiction", regime("IN", "SEBI"), "IN-SEBI")
    check("the Indian regulator in full is not the SEC", regime("IN", "Securities and Exchange Board of India"), "IN-SEBI")
    check("and an unset jurisdiction does not make it the SEC either",
          regime("XX", "Securities and Exchange Board of India"), "IN-SEBI")
    check("New Zealand is unchanged", regime("NZ", "FMA"), "NZ-FMA")
    check("the US is unchanged", regime("US", "SEC/FINRA"), "US-SEC")
    check("the US regulator in full still is", regime("XX", "Securities and Exchange Commission"), "US-SEC")
    check("the UK is unchanged", (regime("UK", "FCA"), regime("GB")), ("UK-FCA", "UK-FCA"))
    check("the EU is unchanged", (regime("EU"), regime("DE", "BaFin")), ("EU-MiFID", "EU-MiFID"))
    check("a firm with no jurisdiction set reads the column's default", regime(None), "NZ-FMA")
    check("Australia has no rules here, and says so", regime("AU", "ASIC"), "NOT-SUPPORTED")
    check("nor Singapore", regime("SG", "MAS"), "NOT-SUPPORTED")
    check("nor a 'per client' firm", regime("MULTI"), "NOT-SUPPORTED")

    print("\n=== A country with no rules cannot be cleared ===")
    ctx = CheckContext(agent_key="drift_rebalancing", rationale="x" * 60, text="", confidence=0.9, payload={},
                       evidence={}, citations=[], mandate_type="advisory", policy={})
    outcome = engine.evaluate(ontology.NO_REGIME, ctx)
    check("the assessment is blocked", outcome["status"], "blocked")
    check("for the right reason", "no rules" in outcome["fails"][0]["finding"], True)

    print("\n=== Which documents a case needs ===")
    check("India has its own set", disclosures.required_for("IN", "individual"),
          ["in_advisory_agreement", "in_mitc", "in_ai_use_disclosure", "privacy_notice"])
    check("the NZ set is unchanged", disclosures.required_for("NZ", "individual"),
          ["disclosure_statement", "soa", "privacy_notice"])
    check("no set for a country means a case cannot activate",
          disclosures.status_for("SG", "individual", [])["blocks_activation"], True)
    check("and it says why", disclosures.status_for("SG", "individual", [])["items"][0]["label"],
          "No disclosure set for this jurisdiction")
    check("the Indian privacy notice names the Indian law",
          disclosures.describe("privacy_notice", "IN")["regime"], "IN DPDP")

    print("\n=== Calendar ===")
    check("the firm's day is India's day",
          firm_time.today(N(settings={}, jurisdiction="IN"), __import__("datetime").datetime(2026, 3, 31, 19, 0)),
          date(2027 - 1, 4, 1))
    year = markets.tax_year(markets.MARKETS["IN"], date(2026, 10, 31))
    check("the Indian tax year is named for both", (year["label"], year["start"], year["end"]),
          ("2026-27", date(2026, 4, 1), date(2027, 3, 31)))
    check("31 March is still last year's", markets.tax_year(markets.MARKETS["IN"], date(2027, 3, 31))["label"], "2026-27")
    check("1 April is the next", markets.tax_year(markets.MARKETS["IN"], date(2027, 4, 1))["label"], "2027-28")
    check("a calendar-year market ends in December",
          markets.tax_year(markets.MARKETS["US"], date(2026, 6, 1))["end"], date(2026, 12, 31))

    print("\n=== Money ===")
    check("a crore is written the Indian way", markets.money(12345678, "INR"), "₹1,23,45,678")
    check("a lakh", markets.money(125000, "INR"), "₹1,25,000")
    check("under a thousand", markets.money(999, "INR"), "₹999")
    check("with paise", markets.money(1234567.5, "INR", dp=2), "₹12,34,567.50")
    check("a loss", markets.money(-250000, "INR"), "-₹2,50,000")
    check("New Zealand dollars are unchanged", markets.money(1234567, "NZD"), "$1,234,567")
    check("pounds", markets.money(1234567, "GBP"), "£1,234,567")
    check("a currency it does not know keeps its code", markets.money(1000, "CHF"), "CHF 1,000")
    check("a firm's formatter uses its books", markets.money_for(N(base_currency="INR"))(150000), "₹1,50,000")

    print("\n=== Resolving a market ===")
    check("aliases", (markets.code("GB"), markets.code("usa"), markets.code("India")), ("UK", "US", "IN"))
    check("no market is None, not a default", markets.get("SG"), None)
    check("a household's own jurisdiction wins",
          markets.of_household(N(jurisdiction="NZ"), {"jurisdiction": "IN"}).code, "IN")
    check("a currency with no market is the firm's", markets.currency("SG", "SGD"), "SGD")
    check("only India fixes the lot method", [m.code for m in markets.MARKETS.values() if m.statutory_fifo], ["IN"])


async def main() -> int:
    pure()
    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(db, expire_on_commit=False)
        async with Session() as s:
            print("\n=== The tax report for a country with none ===")
            firm = Firm(id=uuid.uuid4(), name="S", slug="s", jurisdiction="SG", regulator="MAS", base_currency="SGD")
            s.add(firm)
            await s.flush()
            h = Household(id=uuid.uuid4(), firm_id=firm.id, name="Lim", values={})
            s.add(h)
            await s.flush()
            report = await tax_intelligence.for_household(s, h.id, "SG", firm_id=firm.id)
            check("it says it has none", (report["jurisdiction"], report["supported"]), ("SG", False))
            check("and is not New Zealand's", "pie_optimisation" in report, False)
            check("no flags, so no agent raises anything", report["summary"]["total_flags"], 0)
            book = await tax_intelligence.for_firm(s, firm.id, "SG")
            check("the book counts it apart from New Zealand",
                  (book["jurisdictions"]["NZ"], book["jurisdictions"]["not_supported"]), (0, 1))
    finally:
        await db.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for label in FAIL:
        print(f"  FAILED: {label}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
