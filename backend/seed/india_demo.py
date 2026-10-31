"""The Indian demonstration firm: a second firm beside the demo one, for showing the India work.

    python -m seed.india_demo

The clients are invented. The prices are real where the network allows, and each lot's cost is set
from the live price, so the gains and losses on screen are real arithmetic on invented holdings. The
firm is marked as a demonstration, which is what lets a client's consent be simulated, and the screens
say so. Market data here is Yahoo's, which is good enough to demonstrate with and is not licensed for
client use.

The firm holds two licences, so one book shows both: the Sharma, Banerjee and D'Souza households are
on the Investment Adviser's track and the Iyer and Kapoor households on the discretionary Portfolio
Manager's.

Safe to run again: it does nothing if the firm exists. Separate from seed/run.py, which is keyed to
the demo firm and which an existing database never reads.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.agents import schedules as agent_schedules
from app.agents.catalogue import CATALOGUE
from app.aurea_core import firm_time, knowledge, markets
from app.aurea_core.valuation import revalue_firm
from app.conduit.service import ensure_default_connectors, sync_market_data
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.personas import INDIA_PERSONAS
from app.core.security import hash_password
from app.models.enums import (
    AgentKey, AssetClass, AutonomyTier, ClientSegment, EntityType, MandateType, MarketType, UserRole,
)
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.knowledge import ResearchDocument
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot, Transaction
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm

log = get_logger("aurea.seed.india")
SLUG = "india-demo"

ADVISER, MANAGER = "investment_adviser", "pms_discretionary"

# Agents that have no business on an Indian book.
NOT_FOR_INDIA = (AgentKey.ROLLOVER_PTE_DOCUMENTER,)

# symbol: name, asset class, tax class, approximate close if the feed cannot be reached, extra tags.
INSTRUMENTS = {
    "RELIANCE.NS": ("Reliance Industries", AssetClass.EQUITY, "listed_equity", 1165.0, {}),
    "TCS.NS": ("Tata Consultancy Services", AssetClass.EQUITY, "listed_equity", 2500.0, {}),
    "INFY.NS": ("Infosys", AssetClass.EQUITY, "listed_equity", 1185.0, {}),
    "HDFCBANK.NS": ("HDFC Bank", AssetClass.EQUITY, "listed_equity", 690.0, {}),
    "ICICIBANK.NS": ("ICICI Bank", AssetClass.EQUITY, "listed_equity", 1400.0, {}),
    "BHARTIARTL.NS": ("Bharti Airtel", AssetClass.EQUITY, "listed_equity", 1975.0, {}),
    "ITC.NS": ("ITC", AssetClass.EQUITY, "listed_equity", 265.0, {"flags": ["tobacco"], "esg": "low"}),
    "NIFTYBEES.NS": ("Nippon India Nifty 50 ETF", AssetClass.EQUITY, "equity_fund", 262.0, {}),
    "GILT5YBEES.NS": ("Nippon India 5 Year Gilt ETF", AssetClass.FIXED_INCOME, "specified_fund", 65.0, {}),
    "GOLDBEES.NS": ("Nippon India Gold ETF", AssetClass.COMMODITY, "other_listed", 116.0, {}),
    "EMBASSY.NS": ("Embassy Office Parks REIT", AssetClass.PROPERTY, "reit_invit", 415.0, {}),
}

MODELS = {
    "balanced": ("Core Balanced", "50/30/10/10 balanced model.",
                 [(AssetClass.EQUITY, 0.50, "NIFTYBEES.NS"), (AssetClass.FIXED_INCOME, 0.30, "GILT5YBEES.NS"),
                  (AssetClass.COMMODITY, 0.10, "GOLDBEES.NS"), (AssetClass.PROPERTY, 0.10, "EMBASSY.NS")]),
    "growth": ("Core Growth", "Growth-tilted model.",
               [(AssetClass.EQUITY, 0.70, "NIFTYBEES.NS"), (AssetClass.FIXED_INCOME, 0.15, "GILT5YBEES.NS"),
                (AssetClass.COMMODITY, 0.10, "GOLDBEES.NS"), (AssetClass.PROPERTY, 0.05, "EMBASSY.NS")]),
}
CAPACITY = {"balanced": "medium", "growth": "high"}

# A holding is (symbol, lots); a lot is (quantity, cost against today's price, days ago). -0.30 cost 30%
# under the price, so it stands at a gain. A taxpayer is a person or an entity with one demat.
HOUSEHOLDS = [
    {"name": "The Sharma Family", "values": {"themes": ["education"]}, "segment": ClientSegment.PRIVATE_WEALTH,
     "people": [
         {"name": "Rajesh Sharma", "dob": date(1974, 3, 12), "client_login": True,
          "tax": {"regime": "new", "marginal_rate": 0.30, "annual_income": 7200000,
                  "losses_brought_forward": {"long_term": 40000}},
          "taxpayer": {"track": ADVISER, "model": "balanced", "custodian": "CDSL", "cash": 300000,
                       "exclude": ["tobacco"], "budget": 800000,
                       "holdings": [("RELIANCE.NS", [(500, -0.35, 900), (400, -0.10, 200)]),
                                    ("INFY.NS", [(700, 0.18, 140)]),
                                    ("HDFCBANK.NS", [(1000, -0.30, 1100), (500, 0.06, 60)]),
                                    ("ITC.NS", [(1000, -0.20, 700)]),
                                    ("GILT5YBEES.NS", [(3000, 0.01, 300)])],
                       "sold": [("TCS.NS", 300, -0.30, 900, 120)]}},
         {"name": "Meera Sharma", "dob": date(1977, 8, 21),
          "tax": {"regime": "new", "marginal_rate": 0.30, "annual_income": 2400000},
          "taxpayer": {"track": ADVISER, "model": "balanced", "custodian": "NSDL", "cash": 150000, "budget": 400000,
                       "holdings": [("NIFTYBEES.NS", [(4000, -0.28, 1000)]),
                                    ("HDFCBANK.NS", [(600, -0.15, 500)]),
                                    ("GOLDBEES.NS", [(2000, -0.30, 900)])]}},
         {"name": "Aarav Sharma", "dob": date(2004, 1, 9), "next_gen": True, "tax": {}},
     ],
     "entity": {"name": "Rajesh Sharma HUF", "type": EntityType.HUF,
                "governance": {"karta": "Rajesh Sharma", "coparceners": ["Rajesh Sharma", "Aarav Sharma"],
                               "members": ["Meera Sharma"],
                               "tax": {"regime": "new", "marginal_rate": 0.20, "annual_income": 900000}},
                "taxpayer": {"track": ADVISER, "model": "balanced", "custodian": "CDSL", "cash": 100000, "budget": 300000,
                             "holdings": [("BHARTIARTL.NS", [(500, -0.40, 1400)]),
                                          ("EMBASSY.NS", [(1500, -0.12, 900)]),
                                          ("RELIANCE.NS", [(300, 0.09, 340)])]}},
     "goals": [("Retirement at 60", "retirement", 20000000, 12, {"retirement_age": 60, "funding_share": 0.85}),
               ("Aarav's postgraduate study", "education", 2500000, 3, {"funding_share": 0.15})]},

    {"name": "The Iyer Family", "values": {}, "segment": ClientSegment.PRIVATE_WEALTH,
     "people": [
         {"name": "Karthik Iyer", "dob": date(1979, 11, 2),
          "tax": {"regime": "new", "marginal_rate": 0.30, "annual_income": 24000000,
                  "losses_brought_forward": {"short_term": 150000}},
          "taxpayer": {"track": MANAGER, "model": "growth", "custodian": "CDSL", "cash": 500000, "budget": 1500000,
                       "holdings": [("TCS.NS", [(2000, -0.30, 1000)]),
                                    ("INFY.NS", [(3000, -0.25, 800), (1000, 0.12, 100)]),
                                    ("NIFTYBEES.NS", [(20000, -0.20, 700)]),
                                    ("GOLDBEES.NS", [(1000, -0.15, 500)])]}},
         {"name": "Lakshmi Iyer", "dob": date(1982, 5, 17), "tax": {"regime": "new", "annual_income": 3600000}},
     ],
     "goals": [("Retirement at 58", "retirement", 30000000, 11, {"retirement_age": 58, "funding_share": 0.9})]},

    {"name": "The Kapoor Family Trust", "values": {}, "segment": ClientSegment.INSTITUTIONAL,
     "people": [{"name": "Nandini Kapoor", "dob": date(1963, 2, 25), "tax": {"regime": "old", "annual_income": 9000000}}],
     "entity": {"name": "Kapoor Family Trust", "type": EntityType.TRUST,
                "governance": {"trustees": ["Nandini Kapoor", "Independent Trustee Pvt Ltd"],
                               "tax": {"regime": "old", "marginal_rate": 0.30}},
                "taxpayer": {"track": MANAGER, "model": "balanced", "custodian": "NSDL", "cash": 1000000, "budget": 2500000,
                             "holdings": [("RELIANCE.NS", [(5000, -0.30, 1300)]),
                                          ("ICICIBANK.NS", [(4000, -0.22, 1000)]),
                                          ("GILT5YBEES.NS", [(40000, 0.02, 600)]),
                                          ("EMBASSY.NS", [(5000, -0.08, 800)])]}},
     "goals": [("Next generation endowment", "legacy", 40000000, 20, {"funding_share": 0.8})]},

    {"name": "The Banerjee Household", "values": {}, "segment": ClientSegment.MASS_AFFLUENT,
     "people": [
         {"name": "Subir Banerjee", "dob": date(1958, 6, 4),
          "tax": {"regime": "old", "marginal_rate": 0.20, "annual_income": 1400000},
          "taxpayer": {"track": ADVISER, "model": "balanced", "custodian": "CDSL", "cash": 200000, "budget": 150000,
                       "holdings": [("GILT5YBEES.NS", [(30000, 0.02, 500)]),
                                    ("NIFTYBEES.NS", [(6000, -0.18, 650)]),
                                    ("HDFCBANK.NS", [(800, -0.10, 450)])]}},
     ],
     "goals": [("Income through retirement", "retirement", 6000000, 15, {"retirement_age": 60, "funding_share": 0.7})]},

    {"name": "The D'Souza Household", "values": {}, "segment": ClientSegment.MASS_AFFLUENT,
     "people": [
         {"name": "Elaine D'Souza", "dob": date(1980, 9, 30),
          "tax": {"regime": "new", "marginal_rate": 0.30, "annual_income": 3800000},
          "taxpayer": {"track": ADVISER, "model": "growth", "custodian": "NSDL", "cash": 200000, "budget": 500000,
                       "holdings": [("ICICIBANK.NS", [(900, 0.07, 120)]),
                                    ("TCS.NS", [(300, -0.22, 500)]),
                                    ("NIFTYBEES.NS", [(3000, -0.15, 380)]),
                                    ("GILT5YBEES.NS", [(5000, 0.01, 400)])]}},
     ],
     "goals": [("Retirement at 60", "retirement", 10000000, 14, {"retirement_age": 60, "funding_share": 0.8})]},
]

RESEARCH = [
    ("House View: India, Quality Over Momentum", "house_view",
     "Neutral on Indian equities with a quality tilt; a short gilt position for ballast.",
     "Our house view holds a neutral allocation to Indian equities with a tilt toward quality and cash-generative "
     "businesses, reached through broad index exposure where it is cheap to do so. We are wary of single-name "
     "concentration and recommend trimming any position above twenty percent of a portfolio. In fixed income we favour "
     "short gilts for ballast. Gold and listed REITs are held as diversifiers, not as return drivers. Rebalance when an "
     "asset class drifts beyond its five percent band. This is a demonstration document.",
     ["house_view", "rebalancing"]),
    ("Adviser Note: Tax-Aware Rebalancing in India", "playbook",
     "First-in first-out means the lot is not a choice, and each family member is their own taxpayer.",
     "Lots in a demat account are relieved first-in first-out, so the question in India is not which lot to sell but "
     "whether to sell now. Harvest value is net of any gain on older lots in front of the loss. Every person, HUF and "
     "trust is a separate taxpayer, and a loss never crosses between them. A short-term loss shelters more than a "
     "long-term one, so a loss about to pass twelve months is worth acting on. India has no wash-sale rule, but an "
     "immediate rebuy of the same instrument is untested and is not to be described as safe. Review before 31 March "
     "each year. This is a demonstration document.",
     ["tax", "rebalancing", "harvesting"]),
    ("Adviser Note: Who Consents to What", "playbook",
     "Under an Investment Adviser's licence the client consents to every trade; under a discretionary mandate the firm decides.",
     "On an Investment Adviser's track, an adviser's approval places nothing: the client's specific and positive consent "
     "to each trade is needed, and it is recorded from the email, call recording or signed document. On a discretionary "
     "Portfolio Manager's track the firm acts inside the mandate, individually for each client, and a named person "
     "approves every order set. This is a demonstration document.",
     ["licence", "consent"]),
]


def _closes_or_fallback(quotes: dict | None) -> dict[str, tuple[float, bool, date]]:
    """(close, is real, as of) per symbol. A price the feed did not give is marked as invented."""
    today = date.today()
    return {sym: (quotes[sym][1], True, quotes[sym][0]) if quotes and quotes.get(sym) else (spec[3], False, today)
            for sym, spec in INSTRUMENTS.items()}


async def _live_closes() -> dict | None:
    from app.conduit.marketdata import fetch_series
    try:
        feed = await fetch_series({sym: "5d" for sym in INSTRUMENTS}, interval="1d")
    except Exception as exc:
        log.warning("india_quotes_failed", error=str(exc))
        return None
    return {sym: rows[-1] for sym, rows in feed.series.items() if rows}


async def build(s, *, quotes: dict | None = None) -> Firm:
    """Write the firm. Takes the prices, so a test can run it with none and no network."""
    pw = hash_password("aurea")
    firm = Firm(
        slug=SLUG, name="Astra for Wealth India", legal_name="Astra for Wealth India (demonstration)",
        jurisdiction="IN", regulator="SEBI", base_currency="INR",
        branding={"primary": "#163a52", "accent": "#c8a35e", "logo_text": "Astra for Wealth",
                  "tagline": "Governed AI for Wealth Management"},
        settings={
            "timezone": "Asia/Kolkata", "demonstration": True, "licences": [ADVISER, MANAGER],
            "execution": {"fee_bps": 10, "fee_minimum": 20},
            "protection": {"retirement_age": 60},
            "ai_usage_policy": "AI outputs are assistive; a named person at the firm decides and answers for them. "
                               "Client data is never used to train external models.",
        },
        model_config_json={},
    )
    s.add(firm)
    await s.flush()

    users: dict[UserRole, User] = {}
    for p in INDIA_PERSONAS:
        if p["role"] == UserRole.CLIENT:
            continue
        u = User(firm_id=firm.id, email=p["email"], full_name=p["full_name"], hashed_password=pw,
                 role=p["role"], title=p["title"])
        s.add(u)
        users[p["role"]] = u
    await s.flush()

    for key, meta in CATALOGUE.items():
        cron, _why = agent_schedules.DEFAULT_SCHEDULES.get(key, (None, None))
        s.add(AgentConfig(firm_id=firm.id, agent_key=key, enabled=key not in NOT_FOR_INDIA,
                          default_tier=meta["default_tier"], schedule_cron=cron, schedule_enabled=bool(cron)))
    s.add(AutonomyPolicy(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=MandateType.DISCRETIONARY,
                         tier=AutonomyTier.TIER_2, guardrails={"requires_compliance": False},
                         rationale="Discretionary: a named person at the firm approves every order set."))
    s.add(AutonomyPolicy(firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=MandateType.ADVISORY,
                         tier=AutonomyTier.TIER_2, guardrails={"requires_client_consent": True},
                         rationale="Advisory: the client consents to every trade."))

    closes = _closes_or_fallback(quotes)
    instruments: dict[str, Instrument] = {}
    for sym, (name, cls, tax_class, _fallback, tags) in INSTRUMENTS.items():
        inst = Instrument(firm_id=firm.id, symbol=sym, name=name, asset_class=cls, market_type=MarketType.PUBLIC,
                          currency="INR", market_symbol=sym, values_tags={"tax_class": tax_class, **tags})
        s.add(inst)
        await s.flush()
        instruments[sym] = inst
        close, real, as_of = closes[sym]
        s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=as_of, close=close, currency="INR",
                    source="yahoo" if real else "synthetic", is_real=real))

    models: dict[str, ModelPortfolio] = {}
    for key, (name, description, targets) in MODELS.items():
        model = ModelPortfolio(firm_id=firm.id, name=name, description=description, drift_band=0.05)
        s.add(model)
        await s.flush()
        models[key] = model
        for cls, weight, sym in targets:
            s.add(TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class=cls, target_weight=weight,
                                   instrument_id=instruments[sym].id))
    await s.flush()

    today = firm_time.today(firm)
    tax_year = markets.tax_year(markets.MARKETS["IN"], today)
    adviser, manager = users[UserRole.ADVISER], users[UserRole.PORTFOLIO_TEAM]

    async def taxpayer(owner_person, owner_entity, name, spec, household_name):
        model = models[spec["model"]]
        label = model.name.split()[-1]
        mandate = Mandate(
            firm_id=firm.id, person_id=owner_person.id if owner_person else None,
            entity_id=owner_entity.id if owner_entity else None, name=f"{name} - {label}",
            mandate_type=MandateType.DISCRETIONARY if spec["track"] == MANAGER else MandateType.ADVISORY,
            suitability={"risk_profile": spec["model"], "capacity_for_loss": CAPACITY[spec["model"]],
                         "values_exclusions": spec.get("exclude", [])},
            constraints={"service_track": spec["track"], "cgt_budget": spec["budget"]},
            model_portfolio_id=model.id)
        s.add(mandate)
        await s.flush()
        account = Account(firm_id=firm.id, mandate_id=mandate.id, name=f"{name} demat", custodian=spec["custodian"],
                          currency="INR", cash_balance=spec["cash"])
        s.add(account)
        await s.flush()
        for sym, lots in spec["holdings"]:
            close = closes[sym][0]
            qty = sum(q for q, _, _ in lots)
            priced = [(q, round(close * (1 + c), 2), d) for q, c, d in lots]
            h = Holding(firm_id=firm.id, account_id=account.id, instrument_id=instruments[sym].id, quantity=qty,
                        market_value=round(qty * close, 2), cost_basis=round(sum(q * c for q, c, _ in priced), 2),
                        lineage={"source": spec["custodian"], "as_of": today.isoformat()}, confidence=0.95)
            s.add(h)
            await s.flush()
            for q, cost, days in priced:
                s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=q, cost_per_unit=cost,
                             acquired_on=today - timedelta(days=days)))
        for sym, qty, cost_pct, held_days, days_ago in spec.get("sold", []):
            # Earlier in this tax year, so the year has something to set off against.
            on = max(today - timedelta(days=days_ago), tax_year["start"])
            close = closes[sym][0]
            cost = round(close * (1 + cost_pct), 2)
            s.add(Transaction(firm_id=firm.id, account_id=account.id, instrument_id=instruments[sym].id, txn_type="sell",
                              quantity=qty, price=close, amount=round(qty * close, 2), trade_date=on,
                              lineage={"source": "demonstration", "fees": 0, "lot_method": "fifo",
                                       "realised_gain": round(qty * (close - cost), 2),
                                       "lots": [{"acquired_on": (on - timedelta(days=held_days)).isoformat(),
                                                 "quantity": qty, "cost": round(qty * cost, 2)}]}))
        await s.flush()
        return mandate

    for spec in HOUSEHOLDS:
        household = Household(firm_id=firm.id, name=spec["name"], segment=spec["segment"], values=spec["values"])
        s.add(household)
        await s.flush()
        first_person = None
        for pd in spec["people"]:
            person = Person(firm_id=firm.id, household_id=household.id, full_name=pd["name"],
                            preferred_name=pd["name"].split()[0], date_of_birth=pd["dob"], segment=spec["segment"],
                            is_next_gen=pd.get("next_gen", False),
                            kyc={"status": "verified", "id_verified": True, "aml_screened": True, "demonstration": True},
                            profile={"risk_profile": (pd.get("taxpayer") or {}).get("model", "balanced"), "tax": pd["tax"]})
            s.add(person)
            await s.flush()
            first_person = first_person or person
            track = (pd.get("taxpayer") or {}).get("track")
            s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user",
                                   from_id=(manager if track == MANAGER else adviser).id,
                                   to_type="person", to_id=person.id))
            if pd.get("taxpayer"):
                await taxpayer(person, None, pd["name"], pd["taxpayer"], spec["name"])
            if pd.get("client_login"):
                client = next(p for p in INDIA_PERSONAS if p["role"] == UserRole.CLIENT)
                s.add(User(firm_id=firm.id, email=client["email"], full_name=pd["name"], hashed_password=pw,
                           role=UserRole.CLIENT, title="Client", person_id=person.id))
        if spec.get("entity"):
            e = spec["entity"]
            entity = LegalEntity(firm_id=firm.id, household_id=household.id, name=e["name"], entity_type=e["type"],
                                 governance=e["governance"], impact_objectives={}, jurisdiction="IN")
            s.add(entity)
            await s.flush()
            await taxpayer(None, entity, e["name"], e["taxpayer"], spec["name"])
        for name, kind, target, years, assumptions in spec["goals"]:
            s.add(Goal(firm_id=firm.id, household_id=household.id, person_id=first_person.id, name=name, kind=kind,
                       target_amount=target, target_date=date(today.year + years, 3, 31),
                       assumptions={"years": years, **assumptions}))
    await s.flush()
    return firm


async def seed() -> None:
    async with SessionLocal() as s:
        if (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one_or_none():
            print("\nThe Indian demonstration firm already exists. Nothing to do.\n")
            return
        firm = await build(s, quotes=await _live_closes())
        for title, doc_type, summary, body, tags in RESEARCH:
            doc = ResearchDocument(firm_id=firm.id, title=title, doc_type=doc_type, author="Investment Committee",
                                   summary=summary, body=body, tags=tags)
            s.add(doc)
            await s.flush()
            await knowledge.ingest_document(s, doc)
        await ensure_default_connectors(s, firm.id)
        await s.commit()

    from seed.run import _seed_price_history

    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one()
        try:
            await sync_market_data(s, firm.id)
            await _seed_price_history(s, firm.id)
        except Exception as exc:  # offline: the seeded prices stand, marked as invented
            log.warning("india_market_sync_failed", error=str(exc))
        await revalue_firm(s, firm.id)
        await s.commit()

    # Proposals, so the firm is not empty on first sign-in.
    from app.atlas.base import Subject
    from app.atlas.runtime import run_agent

    raised = 0
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == SLUG))).scalar_one()
        mandates = (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()
        for m in mandates:
            try:
                await run_agent(s, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING, trigger="seed_scan",
                                subject=Subject("mandate", m.id, m.name), require_human=True)
                await s.commit()
                raised += 1
            except Exception as exc:
                await s.rollback()
                log.warning("india_drift_failed", mandate=m.name, error=str(exc))
        try:
            await run_agent(s, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, trigger="seed_scan",
                            subject=Subject("firm", firm.id, firm.name), require_human=True)
            await s.commit()
        except Exception as exc:
            await s.rollback()
            log.warning("india_tax_agent_failed", error=str(exc))
    print(f"\nCreated the Indian demonstration firm: {len(HOUSEHOLDS)} households, {raised} mandate(s) scanned.\n")


if __name__ == "__main__":
    configure_logging()
    asyncio.run(seed())
