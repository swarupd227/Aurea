"""A simulated aggregation feed for money held elsewhere (B-223), standing in for a vendor parked under L200-2.

The real feature is B-198: holdings held elsewhere, line by line, measured on real prices (`aurea_core/held_away.py`),
and the feed registry a real vendor will use (`aurea_core/held_away_feeds.py`). This package is only the source: a
feed registered at that seam which delivers statements for the demonstration firm's invented households, in real
listed instruments the market-data path prices (Spark, Air New Zealand, Apple, Microsoft, BAT, the bond and property
ETFs), plus a fund with no symbol so the "no performance" path shows. Apple and Microsoft are also in those households'
managed books, so the overlap shows. Whether a line trails its reference is worked out from real prices, never set
here: Spark and Air New Zealand are the lines most likely to.

Seeding opts the firm in and pulls the feed; every row carries `source_system = simulated:held_away_feed` and
`simulated: true`, and no ledger or audit entry is written. Discarding deletes those rows and the opt-in.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select

from app.aurea_core import firm_time, held_away_feeds
from app.aurea_core.held_away_feeds import FeedLine
from app.models.graph import Household, Person
from app.models.held_away import HeldAwayHolding
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "held_away_feed"
SOURCE = tag(KEY)

# (household, owner's full name or None for joint, institution, account, symbol, name, asset class, quantity, value stated)
# Values are in the firm's currency, as an NZ broker's statement states them; they are invented, and say so.
STATEMENTS = [
    ("The Chen Family", "Wei Chen", "Jarden Direct", "Wei Chen - Direct", "SPK.NZ", "Spark New Zealand Ltd", "equity", 40000, 104000),
    ("The Chen Family", "Wei Chen", "Jarden Direct", "Wei Chen - Direct", "AIR.NZ", "Air New Zealand Ltd", "equity", 120000, 66000),
    ("The Chen Family", "Wei Chen", "Hatch", "Wei Chen - US shares", "AAPL", "Apple Inc", "equity", 300, 118000),
    ("The Chen Family", "Wei Chen", "Milford Asset Management", "Wei Chen - KiwiSaver", None, "Milford Active Growth Fund", "multi_asset", None, 210000),
    ("The Chen Family", "Mei Chen", "Hatch", "Mei Chen - US shares", "VNQ", "Vanguard Real Estate ETF", "property", 500, 72000),
    ("The Nguyen Family", "Binh Nguyen", "Sharesies", "Binh Nguyen", "MSFT", "Microsoft Corp", "equity", 150, 112000),
    ("The Nguyen Family", "Binh Nguyen", "Sharesies", "Binh Nguyen", "AIR.NZ", "Air New Zealand Ltd", "equity", 60000, 33000),
    ("The Nguyen Family", "Lan Nguyen", "Kernel", "Lan Nguyen", None, "Kernel NZ 20 Fund", "equity", None, 45000),
    ("The Nguyen Family", "Lan Nguyen", "ASB Securities", "Lan Nguyen", "AGG", "iShares Core US Aggregate Bond ETF", "fixed_income", 300, 50000),
    ("The Müller Family", "Anders Müller", "Craigs Investment Partners", "Anders Müller - Portfolio", "SPK.NZ", "Spark New Zealand Ltd", "equity", 50000, 130000),
    ("The Müller Family", "Anders Müller", "Craigs Investment Partners", "Anders Müller - Portfolio", "BTI", "British American Tobacco plc ADR", "equity", 2000, 140000),
    ("The Müller Family", "Anders Müller", "Craigs Investment Partners", "Anders Müller - Portfolio", "AAPL", "Apple Inc", "equity", 400, 158000),
    ("The Müller Family", "Sofia Müller", "Craigs Investment Partners", "Sofia Müller - Portfolio", "AGG", "iShares Core US Aggregate Bond ETF", "fixed_income", 1500, 250000),
]


# India (B-256): broker statements for the Indian demonstration firm's invented households, in NSE-listed instruments the firm
# prices (they overlap its managed books, so the overlap shows), plus a fund with no symbol so the "no performance" path shows.
# Mutual funds, NPS and REITs held away arrive through the Account Aggregator stand-in (`aa_feed`) or a consolidated statement,
# so none is repeated here. Values are in rupees, as a broker's statement states them; they are invented, and say so.
STATEMENTS_IN = [
    ("The Sharma Family", "Rajesh Sharma", "Zerodha", "Rajesh Sharma - Demat", "ITC.NS", "ITC Ltd", "equity", 1500, 397500),
    ("The Sharma Family", "Rajesh Sharma", "Zerodha", "Rajesh Sharma - Demat", "INFY.NS", "Infosys Ltd", "equity", 400, 474000),
    ("The Sharma Family", "Meera Sharma", "ICICI Direct", "Meera Sharma - Demat", "TCS.NS", "Tata Consultancy Services Ltd", "equity", 200, 500000),
    ("The Banerjee Household", "Subir Banerjee", "HDFC Securities", "Subir Banerjee - Demat", "RELIANCE.NS", "Reliance Industries Ltd", "equity", 250, 291250),
    ("The Banerjee Household", "Subir Banerjee", "Kotak Mutual Fund", "Subir Banerjee - Folio", None, "Kotak Emerging Equity Fund", "equity", None, 180000),
    ("The D'Souza Household", "Elaine D'Souza", "Groww", "Elaine D'Souza - Demat", "HDFCBANK.NS", "HDFC Bank Ltd", "equity", 700, 483000),
    ("The D'Souza Household", "Elaine D'Souza", "Groww", "Elaine D'Souza - Demat", "ICICIBANK.NS", "ICICI Bank Ltd", "equity", 300, 420000),
]


def _india(firm) -> bool:
    from app.aurea_core import markets

    return markets.code(getattr(firm, "jurisdiction", None)) == "IN"


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")[:40]


class SimulatedHeldAwayFeed:
    key = SOURCE
    name = "Simulated aggregation feed"
    simulated = True

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY)

    async def lines(self, session, firm) -> list[FeedLine]:
        """Statements dated the last day of the month before the firm's today. A household or person the firm does not
        have is left out, as a vendor delivers only the accounts a client linked."""
        today = firm_time.today(firm)
        stated = today.replace(day=1) - timedelta(days=1)
        homes = {h.name: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
        out = []
        for household, owner, institution, account, symbol, name, cls, qty, value in (STATEMENTS_IN if _india(firm) else STATEMENTS):
            h = homes.get(household)
            if h is None:
                continue
            person_id = None
            if owner:
                p = (await session.execute(select(Person).where(Person.household_id == h.id, Person.full_name == owner))).scalars().first()
                if p is None:
                    continue
                person_id = p.id
            out.append(FeedLine(
                external_id=f"{_slug(household)}:{_slug(institution)}:{_slug(symbol or name)}:{_slug(owner or 'joint')}",
                household_id=h.id, person_id=person_id, institution=institution, account_label=account, symbol=symbol,
                instrument_name=name, asset_class=cls, quantity=qty, value=value, currency=(firm.base_currency or ("INR" if _india(firm) else "NZD")),
                value_on=stated, document=f"{institution} statement, {stated.isoformat()} (simulated)"))
        return out


FEED = SimulatedHeldAwayFeed()


def install() -> None:
    held_away_feeds.register(FEED)


async def demo_firms(session) -> list:
    return (await session.execute(select(Firm).where(Firm.slug.in_(("demo", "india-demo"))))).scalars().all()


async def seed(session, firm) -> dict:
    if held_away_feeds.FEEDS.get(SOURCE) is not FEED:
        install()
    await opt_in(session, firm, KEY)
    pulled = await held_away_feeds.pull(session, firm, key=SOURCE)
    return {"added": pulled["added"], "superseded": pulled["superseded"], "unchanged": pulled["unchanged"],
            "skipped": len(pulled["refused"])}


async def discard(session, firm) -> dict:
    n = (await session.execute(select(func.count()).select_from(HeldAwayHolding).where(
        HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.source_system == SOURCE))).scalar_one()
    if n:
        await session.execute(delete(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.source_system == SOURCE))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {"holdings": n, "opted_out": was}


register(Simulation(
    key=KEY, name="Held-away aggregation feed", stands_for="An aggregation feed or statements for money held elsewhere",
    seam="aurea_core.held_away_feeds.register (B-198)", install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Values are invented; performance is worked out from real prices, never set here."],
))

