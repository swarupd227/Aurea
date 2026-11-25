"""A simulated Account Aggregator, standing in for a real one (India row 9).

The real feature is `aurea_core/account_aggregator.py`: a consent artefact a person records (its purpose, the kinds of
information, the data range, when it lapses, whether it was withdrawn), the gate `permitted` that any source must read, and
the held-away feed registry (`aurea_core/held_away_feeds.py`) a real Financial Information User client will register at. This
package is only the source: a feed registered at that seam which, for each active consent, delivers the statements of the
Indian demonstration firm's invented households, and nothing a consent does not cover (its kinds, its person, its dates).

A consent that is lapsed, withdrawn or not yet started delivers nothing, and what was delivered under it before ends at the
next pull, kept on file. Seeding opts the firm in, writes the consents a demonstration needs (every row tagged
`simulated:aa_feed`: an active one for the Sharmas and the Banerjees, a lapsed one for the D'Souzas, a withdrawn one for the
Kapoor trust) and pulls. The holdings are invented and so is the aggregator: lines carry `simulated: true` and the card shows
a chip. Funds are real AMFI schemes held in invented quantities, so their performance is worked from real NAVs; the NPS and any
line with no priced instrument here has none. Discarding deletes every consent and line it wrote and the opt-in; no ledger or
audit entry is written.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, func, select

from app.aurea_core import account_aggregator, firm_time, held_away_feeds, markets
from app.aurea_core.held_away_feeds import FeedLine
from app.models.aggregator import AaConsent
from app.models.graph import Household, Person
from app.models.held_away import HeldAwayHolding
from app.models.tenant import Firm
from app.simulations import Simulation, enabled_for, opt_in, opt_out, register, tag

KEY = "aa_feed"
SOURCE = tag(KEY)
AGGREGATOR = "Simulated Account Aggregator (demonstration)"

# (household, who it covers: a person or None for the household, artefact id, kinds, state)
CONSENTS = [
    ("The Sharma Family", None, "SIM-AA-SHARMA-001", ["mutual_funds", "etfs", "reits_invits", "nps"], "active"),
    ("The Banerjee Household", None, "SIM-AA-BANERJEE-001", ["mutual_funds"], "active"),
    ("The D'Souza Household", None, "SIM-AA-DSOUZA-001", ["mutual_funds", "etfs"], "lapsed"),
    ("The Kapoor Family Trust", None, "SIM-AA-KAPOOR-001", ["mutual_funds"], "withdrawn"),
]

# (household, owner or None, kind of information, institution, account, symbol or None, isin or None, name, asset class, units, value)
# Symbols are the firm's own funds and ETFs where it prices one; the value is the statement's, invented. The Banerjee equities
# line is outside the Banerjees' consent (mutual funds only), so the feed leaves it out: the gate working.
STATEMENTS = [
    ("The Sharma Family", "Rajesh Sharma", "mutual_funds", "PPFAS Mutual Fund", "Folio 1234567/89", "PPFAS-FLEXI-DIRECT", "INF879O01027",
     "Parag Parikh Flexi Cap Fund - Direct Plan - Growth", "equity", 1200, 107700),
    ("The Sharma Family", "Rajesh Sharma", "reits_invits", "Aggregated holdings (REIT)", "Rajesh Sharma - Demat", "EMBASSY.NS", None,
     "Embassy Office Parks REIT", "property", 2000, 830000),
    ("The Sharma Family", "Meera Sharma", "mutual_funds", "UTI Mutual Fund", "Folio 7654321/10", "UTI-NIFTY50-DIRECT", "INF789F01XA0",
     "UTI Nifty 50 Index Fund - Direct Plan - Growth", "equity", 800, 132100),
    ("The Sharma Family", "Meera Sharma", "nps", "National Pension System", "Meera Sharma - Tier I", None, None,
     "NPS Tier I, balanced allocation", "multi_asset", None, 640000),
    ("The Banerjee Household", "Subir Banerjee", "mutual_funds", "HDFC Mutual Fund", "Folio 4455667/01", "HDFC-CORPBOND-DIRECT", "INF179K01XD8",
     "HDFC Corporate Bond Fund - Direct Plan - Growth", "fixed_income", 15000, 525150),
    ("The Banerjee Household", "Subir Banerjee", "equities", "Aggregated holdings (demat)", "Subir Banerjee - Demat", "ITC.NS", None,
     "ITC Ltd", "equity", 900, 238500),
    ("The D'Souza Household", "Elaine D'Souza", "mutual_funds", "PPFAS Mutual Fund", "Folio 9988776/54", "PPFAS-FLEXI-REGULAR", "INF879O01019",
     "Parag Parikh Flexi Cap Fund - Regular Plan - Growth", "equity", 2500, 204200),
    ("The D'Souza Household", "Elaine D'Souza", "etfs", "Aggregated holdings (ETF)", "Elaine D'Souza - Demat", "GOLDBEES.NS", None,
     "Nippon India Gold ETF", "commodity", 5000, 580000),
    ("The Kapoor Family Trust", "Nandini Kapoor", "mutual_funds", "UTI Mutual Fund", "Folio 1010101/20", "UTI-NIFTY50-DIRECT", "INF789F01XA0",
     "UTI Nifty 50 Index Fund - Direct Plan - Growth", "equity", 3000, 495300),
]


def _slug(text: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in str(text).lower()).strip("-")[:40]


class SimulatedAggregator:
    key = SOURCE
    name = "Simulated Account Aggregator"
    simulated = True
    account_aggregator = True        # `account_aggregator.pull` asks the feeds that say so

    def enabled_for(self, firm) -> bool:
        return enabled_for(firm, KEY) and account_aggregator.applies(firm)

    async def lines(self, session, firm) -> list[FeedLine]:
        """Statements dated the last day of the month before the firm's today, or the consent's own last day if that is earlier,
        for each active consent and only inside it. A household the firm does not have is left out."""
        today = firm_time.today(firm)
        stated = today.replace(day=1) - timedelta(days=1)
        homes = {h.id: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
        active = await account_aggregator.permitted(session, firm, today)
        out, seen = [], set()
        for c in active:
            h = homes.get(c.household_id)
            if h is None:
                continue
            on = min(stated, c.data_to)
            for household, owner, fi_type, institution, account, symbol, isin, name, cls, units, value in STATEMENTS:
                if household != h.name:
                    continue
                p = (await session.execute(select(Person).where(Person.household_id == h.id, Person.full_name == owner))).scalars().first() if owner else None
                if owner and p is None:
                    continue
                if not account_aggregator.permits(c, fi_type=fi_type, value_on=on, person_id=p.id if p else None, household_id=h.id):
                    continue
                key = (household, institution, name, owner)
                if key in seen:                  # two consents never deliver one holding twice
                    continue
                seen.add(key)
                out.append(FeedLine(
                    external_id=f"{account_aggregator.external_prefix(c)}{_slug(isin or name)}:{_slug(owner or 'joint')}",
                    household_id=h.id, person_id=p.id if p else None, institution=institution, account_label=account, symbol=symbol,
                    isin=isin, instrument_name=name, asset_class=cls, quantity=units, value=value, currency=firm.base_currency or "INR",
                    value_on=on, document=f"{c.aggregator}, consent {c.artefact_id}, {on.isoformat()} (simulated)"))
        await account_aggregator.mark_pulled(session, active, today)
        return out


FEED = SimulatedAggregator()


def install() -> None:
    held_away_feeds.register(FEED)


async def demo_firms(session) -> list:
    return list((await session.execute(select(Firm).where(Firm.slug == "india-demo"))).scalars().all())


async def _write_consents(session, firm) -> int:
    today = firm_time.today(firm)
    stated = today.replace(day=1) - timedelta(days=1)
    homes = {h.name: h for h in (await session.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all()}
    have = set((await session.execute(select(AaConsent.artefact_id).where(AaConsent.firm_id == firm.id))).scalars().all())
    added = 0
    for household, _who, artefact, kinds, state in CONSENTS:
        h = homes.get(household)
        if h is None or artefact in have:
            continue
        row = AaConsent(firm_id=firm.id, household_id=h.id, person_id=None, artefact_id=artefact, aggregator=AGGREGATOR, purpose_code="101",
                        fi_types=kinds, fetch_type="periodic", data_from=today - timedelta(days=365), data_to=stated,
                        consent_start=today - timedelta(days=60), expires_on=today - timedelta(days=10) if state == "lapsed" else today + timedelta(days=300),
                        document=f"Consent artefact {artefact}, as the simulated aggregator shows it", recorded_by="simulation: aa_feed",
                        source_system=SOURCE)
        if state == "withdrawn":
            row.revoked_on, row.revoked_by = today - timedelta(days=20), "simulation: aa_feed"
            row.revoked_reason = "The client withdrew it in the aggregator's app (simulated)"
        session.add(row)
        added += 1
    await session.flush()
    return added


async def seed(session, firm) -> dict:
    if not account_aggregator.applies(firm):
        return {"consents": 0, "added": 0, "skipped": "not an Indian firm"}
    if held_away_feeds.FEEDS.get(SOURCE) is not FEED:
        install()
    await opt_in(session, firm, KEY)
    consents = await _write_consents(session, firm)
    pulled = await held_away_feeds.pull(session, firm, key=SOURCE)
    return {"consents": consents, "added": pulled["added"], "superseded": pulled["superseded"], "ended": pulled["ended"],
            "unchanged": pulled["unchanged"], "skipped": len(pulled["refused"])}


async def discard(session, firm) -> dict:
    lines = (await session.execute(select(func.count()).select_from(HeldAwayHolding).where(
        HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.source_system == SOURCE))).scalar_one()
    if lines:
        await session.execute(delete(HeldAwayHolding).where(HeldAwayHolding.firm_id == firm.id, HeldAwayHolding.source_system == SOURCE))
    consents = (await session.execute(select(func.count()).select_from(AaConsent).where(
        AaConsent.firm_id == firm.id, AaConsent.source_system == SOURCE))).scalar_one()
    if consents:
        await session.execute(delete(AaConsent).where(AaConsent.firm_id == firm.id, AaConsent.source_system == SOURCE))
    was = enabled_for(firm, KEY)
    if was:
        await opt_out(session, firm, KEY)
    await session.flush()
    return {"holdings": lines, "consents": consents, "opted_out": was}


register(Simulation(
    key=KEY, name="Simulated Account Aggregator", stands_for="An RBI-licensed Account Aggregator, as a financial information user reads it",
    seam="aurea_core.held_away_feeds.register, gated by aurea_core.account_aggregator.permitted (India row 9)",
    install=install, seed=seed, discard=discard, firms=demo_firms,
    notes=["Consents and statements are invented for the Indian demonstration firm and tagged simulated:aa_feed; no ledger or audit entry.",
           "A lapsed or withdrawn consent delivers nothing; funds are real AMFI schemes in invented quantities, priced from real NAVs."],
))
