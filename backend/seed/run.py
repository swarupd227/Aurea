"""Idempotent demo seed.

Stands up a synthetic advice-led firm with multi-entity households (a couple, their family
trust, a charitable foundation they govern, and an adult child), portfolios deliberately
drifted and holding both gains and harvestable losses, firm research for grounding, and a
full set of users. Portfolios are valued against REAL market data when the network allows.

Safe to run repeatedly: it no-ops if the demo firm already exists."""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

from sqlalchemy import select

from app.aurea_core import knowledge
from app.aurea_core.valuation import revalue_firm
from app.conduit.service import ensure_default_connectors, sync_market_data
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.core.security import hash_password
from app.models.enums import (
    AgentKey, AssetClass, AutonomyTier, ClientSegment, EntityType, MandateType, MarketType, UserRole,
)
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.knowledge import ResearchDocument
from app.models.portfolio import (
    Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot,
)
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm
from app.models.onboarding import BookIntegrationBatch, OnboardingCase, OnboardingDocument
from app.models.engagement import Meeting
from app.models.client_experience import HeirJourney, Message, default_heir_steps
from app.models.enums import HeirJourneyStatus, MessageAuthor
from datetime import datetime, timezone
from app.aurea_core import sample_docs
from app.aurea_core.sample_book import sample_feed
from app.agents.catalogue import CATALOGUE

log = get_logger("aurea.seed")
PW = hash_password("aurea")
TODAY = date.today()


async def seed() -> None:
    async with SessionLocal() as s:
        existing = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if existing:
            log.info("seed_skip_exists")
            return

        firm = Firm(
            slug="demo", name="Aurera", legal_name="Aurera",
            jurisdiction="NZ", regulator="FMA", base_currency="NZD",
            branding={
                "primary": "#163a52", "accent": "#c8a35e", "logo_text": "Aurera",
                "tagline": "Truly personal advice, at scale.",
            },
            settings={
                "ai_usage_policy": "AI outputs are assistive; a named adviser decides. "
                                   "Client data is never used to train external models.",
                "data_residency": "New Zealand",
            },
            model_config_json={},
        )
        s.add(firm)
        await s.flush()

        # ── Users (internal personas + a Canvas client) ──────────────────────
        adviser = User(firm_id=firm.id, email="sophie.adviser@aurea.demo", full_name="Sophie Tran",
                       hashed_password=PW, role=UserRole.ADVISER, title="Senior Adviser")
        admin = User(firm_id=firm.id, email="admin@aurea.demo", full_name="Aurea Administrator",
                     hashed_password=PW, role=UserRole.ADMIN, title="Platform Admin")
        compliance = User(firm_id=firm.id, email="compliance@aurea.demo", full_name="David Okafor",
                          hashed_password=PW, role=UserRole.COMPLIANCE, title="Head of Compliance")
        s.add_all([adviser, admin, compliance])
        # The remaining spec personas (for the role switcher).
        s.add_all([
            User(firm_id=firm.id, email="paraplanner@aurea.demo", full_name="Priya Naidu",
                 hashed_password=PW, role=UserRole.PARAPLANNER, title="Paraplanner"),
            User(firm_id=firm.id, email="portfolio@aurea.demo", full_name="Tom Becker",
                 hashed_password=PW, role=UserRole.PORTFOLIO_TEAM, title="Portfolio Manager"),
            User(firm_id=firm.id, email="research@aurea.demo", full_name="Dr. Elena Cho",
                 hashed_password=PW, role=UserRole.RESEARCH_CIO, title="CIO · Research"),
            User(firm_id=firm.id, email="operations@aurea.demo", full_name="Grace Lim",
                 hashed_password=PW, role=UserRole.OPERATIONS, title="Operations Lead"),
            User(firm_id=firm.id, email="branch@aurea.demo", full_name="Mark Sullivan",
                 hashed_password=PW, role=UserRole.BRANCH_LEADER, title="Branch Leader"),
        ])
        await s.flush()

        # ── Agent configs + autonomy policies ────────────────────────────────
        for key, meta in CATALOGUE.items():
            s.add(AgentConfig(firm_id=firm.id, agent_key=key, enabled=True,
                              default_tier=meta["default_tier"]))
        # Tighter guardrails for the discretionary drift agent.
        s.add(AutonomyPolicy(
            firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=MandateType.DISCRETIONARY,
            tier=AutonomyTier.TIER_2,
            guardrails={"max_trade_value": 250000, "requires_compliance": False},
            rationale="Supervised: adviser approves every order set before it reaches the OMS."))
        s.add(AutonomyPolicy(
            firm_id=firm.id, agent_key=AgentKey.DRIFT_REBALANCING, mandate_type=MandateType.ADVISORY,
            tier=AutonomyTier.TIER_2, guardrails={"requires_client_consent": True},
            rationale="Advisory mandates require client consent per trade."))
        await s.flush()

        # ── Instruments (real, market-priced symbols) + a private fund ───────
        defs = [
            ("AAPL", "Apple Inc.", AssetClass.EQUITY, MarketType.PUBLIC, "USD", "aapl.us", 190.0, {}),
            ("MSFT", "Microsoft Corp.", AssetClass.EQUITY, MarketType.PUBLIC, "USD", "msft.us", 410.0, {}),
            ("AIR.NZ", "Air New Zealand", AssetClass.EQUITY, MarketType.PUBLIC, "NZD", "air.nz", 0.62, {}),
            ("SPK.NZ", "Spark New Zealand", AssetClass.EQUITY, MarketType.PUBLIC, "NZD", "spk.nz", 4.20, {}),
            ("BTI", "British American Tobacco", AssetClass.EQUITY, MarketType.PUBLIC, "USD", "bti.us",
             32.0, {"sin": True, "flags": ["tobacco"], "esg": "low"}),
            ("AGG", "iShares Core US Aggregate Bond ETF", AssetClass.FIXED_INCOME, MarketType.PUBLIC,
             "USD", "agg.us", 98.0, {}),
            ("VNQ", "Vanguard Real Estate ETF", AssetClass.PROPERTY, MarketType.PUBLIC, "USD",
             "vnq.us", 85.0, {}),
            ("PPEF1", "Pacific Private Equity Fund I", AssetClass.ALTERNATIVES, MarketType.PRIVATE,
             "NZD", None, 100.0, {"liquidity": "illiquid", "vintage": 2022}),
        ]
        instruments: dict[str, Instrument] = {}
        for sym, name, ac, mt, ccy, msym, px, attrs in defs:
            inst = Instrument(firm_id=firm.id, symbol=sym, name=name, asset_class=ac, market_type=mt,
                              currency=ccy, market_symbol=msym,
                              values_tags=attrs if mt == MarketType.PUBLIC else {},
                              private_attributes=attrs if mt == MarketType.PRIVATE else {})
            s.add(inst)
            await s.flush()
            instruments[sym] = inst
            s.add(Price(firm_id=firm.id, instrument_id=inst.id, as_of=TODAY, close=px,
                        currency=ccy, source="synthetic", is_real=False))
        await s.flush()

        # ── Model portfolios ─────────────────────────────────────────────────
        balanced = ModelPortfolio(firm_id=firm.id, name="Aurera Balanced",
                                  description="50/30/10/10 balanced model.", drift_band=0.05)
        growth = ModelPortfolio(firm_id=firm.id, name="Aurera Growth",
                                description="Growth-tilted model.", drift_band=0.05)
        s.add_all([balanced, growth])
        await s.flush()
        for ac, w in [(AssetClass.EQUITY, 0.50), (AssetClass.FIXED_INCOME, 0.30),
                      (AssetClass.ALTERNATIVES, 0.10), (AssetClass.PROPERTY, 0.10)]:
            s.add(TargetAllocation(firm_id=firm.id, model_id=balanced.id, asset_class=ac, target_weight=w))
        for ac, w in [(AssetClass.EQUITY, 0.75), (AssetClass.FIXED_INCOME, 0.15),
                      (AssetClass.ALTERNATIVES, 0.10)]:
            s.add(TargetAllocation(firm_id=firm.id, model_id=growth.id, asset_class=ac, target_weight=w))
        await s.flush()

        # ── Household 1: the Chen family (multi-entity) ──────────────────────
        chen = Household(firm_id=firm.id, name="The Chen Family", segment=ClientSegment.PRIVATE_WEALTH,
                         values={"themes": ["clean energy", "education"], "exclusions": ["tobacco"]})
        s.add(chen)
        await s.flush()
        wei = Person(firm_id=firm.id, household_id=chen.id, full_name="Wei Chen", preferred_name="Wei",
                     email="wei.chen@aurea.demo", date_of_birth=date(1962, 4, 12),
                     segment=ClientSegment.PRIVATE_WEALTH,
                     kyc={"id_verified": True, "aml_screened": True, "status": "verified"},
                     profile={"risk_profile": "balanced", "life_stage": "pre-retirement",
                              "held_away": 620000})
        mei = Person(firm_id=firm.id, household_id=chen.id, full_name="Mei Chen", preferred_name="Mei",
                     date_of_birth=date(1965, 9, 3), segment=ClientSegment.PRIVATE_WEALTH,
                     kyc={"id_verified": True, "aml_screened": True, "status": "verified"},
                     profile={"risk_profile": "balanced"})
        lucas = Person(firm_id=firm.id, household_id=chen.id, full_name="Lucas Chen", preferred_name="Lucas",
                       date_of_birth=date(1995, 1, 20), segment=ClientSegment.NEXT_GEN, is_next_gen=True,
                       kyc={"id_verified": False, "aml_screened": False, "status": "pending"},
                       profile={"interests": ["impact investing"]})
        s.add_all([wei, mei, lucas])
        await s.flush()

        trust = LegalEntity(firm_id=firm.id, household_id=chen.id, name="Chen Family Trust",
                            entity_type=EntityType.TRUST,
                            governance={"trustees": ["Wei Chen", "Mei Chen", "Independent Trustee Ltd"]},
                            impact_objectives={})
        foundation = LegalEntity(firm_id=firm.id, household_id=chen.id, name="Chen Education Foundation",
                                 entity_type=EntityType.FOUNDATION,
                                 governance={"trustees": ["Wei Chen", "Lucas Chen"]},
                                 impact_objectives={"mission": "Scholarships for first-in-family students",
                                                    "exclusions": ["tobacco", "weapons"]})
        s.add_all([trust, foundation])
        await s.flush()

        # Client login for Wei.
        s.add(User(firm_id=firm.id, email="client@aurea.demo", full_name="Wei Chen",
                   hashed_password=PW, role=UserRole.CLIENT, title="Client", person_id=wei.id))

        # Discretionary balanced mandate on the trust — the LIGHTHOUSE target (drifted).
        trust_mandate = Mandate(firm_id=firm.id, entity_id=trust.id, name="Chen Family Trust — Balanced",
                                mandate_type=MandateType.DISCRETIONARY,
                                suitability={"risk_profile": "balanced", "values_exclusions": ["BTI"]},
                                constraints={"cgt_budget": 20000}, model_portfolio_id=balanced.id)
        # Advisory growth mandate for the couple.
        couple_mandate = Mandate(firm_id=firm.id, person_id=wei.id, name="Wei & Mei — Growth",
                                 mandate_type=MandateType.ADVISORY,
                                 suitability={"risk_profile": "growth"}, constraints={"cgt_budget": 10000},
                                 model_portfolio_id=growth.id)
        s.add_all([trust_mandate, couple_mandate])
        await s.flush()

        trust_acc = Account(firm_id=firm.id, mandate_id=trust_mandate.id, name="Trust Custody A/C",
                            account_number="CFT-001", custodian="BNY Pershing", currency="NZD",
                            cash_balance=30000)
        couple_acc = Account(firm_id=firm.id, mandate_id=couple_mandate.id, name="Joint Growth A/C",
                             account_number="WMC-002", custodian="FNZ", currency="NZD", cash_balance=15000)
        s.add_all([trust_acc, couple_acc])
        await s.flush()

        # Trust holdings — deliberately equity-overweight, with gain & loss lots.
        await _holding(s, firm.id, trust_acc, instruments["AAPL"], 1500, cost=190 * 1500,
                       lots=[(800, 210.0, 400), (700, 120.0, 900)])   # 210 = loss lot, 120 = gain lot
        await _holding(s, firm.id, trust_acc, instruments["MSFT"], 400, cost=300 * 400,
                       lots=[(400, 300.0, 700)])
        await _holding(s, firm.id, trust_acc, instruments["AGG"], 1000, cost=100 * 1000,
                       lots=[(1000, 100.0, 1100)])
        await _holding(s, firm.id, trust_acc, instruments["VNQ"], 300, cost=90 * 300,
                       lots=[(300, 90.0, 800)])
        await _holding(s, firm.id, trust_acc, instruments["PPEF1"], 1000, cost=100 * 1000,
                       lots=[(1000, 100.0, 1200)])

        # Couple holdings (advisory) — includes an excluded tobacco line to demo values screening.
        await _holding(s, firm.id, couple_acc, instruments["AAPL"], 300, cost=150 * 300,
                       lots=[(300, 150.0, 1000)])
        await _holding(s, firm.id, couple_acc, instruments["BTI"], 500, cost=40 * 500,
                       lots=[(500, 40.0, 1300)])
        await _holding(s, firm.id, couple_acc, instruments["AGG"], 200, cost=100 * 200,
                       lots=[(200, 100.0, 1100)])

        # Goals.
        s.add_all([
            Goal(firm_id=firm.id, household_id=chen.id, person_id=wei.id, name="Comfortable retirement",
                 kind="retirement", target_amount=2500000, target_date=date(TODAY.year + 8, 1, 1),
                 assumptions={"years": 8, "annual_withdrawal": 0, "funding_share": 0.7}),
            Goal(firm_id=firm.id, household_id=chen.id, name="Foundation endowment",
                 kind="legacy", target_amount=500000, target_date=date(TODAY.year + 5, 1, 1),
                 assumptions={"years": 5, "funding_share": 0.3}),
        ])

        # Relationship edges: named adviser + intergenerational.
        s.add_all([
            RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                             to_type="person", to_id=wei.id),
            RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                             to_type="entity", to_id=trust.id),
            RelationshipEdge(firm_id=firm.id, kind="intergenerational", from_type="person", from_id=wei.id,
                             to_type="person", to_id=lucas.id),
            RelationshipEdge(firm_id=firm.id, kind="trustee", from_type="person", from_id=wei.id,
                             to_type="entity", to_id=trust.id),
        ])

        # ── An upcoming meeting for the Chen household (prep + companion) ─────
        s.add(Meeting(
            firm_id=firm.id, household_id=chen.id,
            title="Quarterly review — The Chen Family",
            scheduled_at=datetime(TODAY.year, TODAY.month, min(TODAY.day + 3, 28), 10, 0, tzinfo=timezone.utc),
            transcript=(
                "Adviser: Good to see you both. How are you feeling about things?\n"
                "Wei: A little nervous after the recent market dip, honestly.\n"
                "Mei: And we'd like to help our daughter with a house deposit next year — around $150k.\n"
                "Adviser: Understood. I'll review your cash buffer, model a tax-efficient drawdown, and "
                "set up the gifting goal in your plan. I'll also prepare a note on the foundation's options.\n"
                "Wei: That would be great, thank you."
            ),
        ))
        await s.flush()

        # ── Canvas: heir login, a welcome message, and a next-gen journey ────
        s.add(User(firm_id=firm.id, email="heir@aurea.demo", full_name="Lucas Chen",
                   hashed_password=PW, role=UserRole.CLIENT, title="Next-gen", person_id=lucas.id))
        s.add(Message(
            firm_id=firm.id, household_id=chen.id, author_role=MessageAuthor.ADVISER,
            author_name=adviser.full_name, read_by_adviser=True, read_by_client=False,
            body="Hi Wei and Mei — lovely to see you both at our last review. I've set up your secure "
                 "space here; message me anytime between meetings. — Sophie"))
        s.add(HeirJourney(firm_id=firm.id, person_id=lucas.id, household_id=chen.id,
                          status=HeirJourneyStatus.INVITED, steps=default_heir_steps(), captured={}))
        await s.flush()

        # ── Household 2: Whitestone Charitable Foundation (for-purpose) ───────
        await _for_purpose_household(s, firm, adviser, instruments, balanced)

        # ── Household 3: the Patel household (mass-affluent, for ask-your-book) ─
        await _simple_household(s, firm, adviser, instruments, growth)

        # ── More households for realism (generated, varied) ──────────────────
        for spec in EXTRA_HOUSEHOLDS:
            await _extra_household(s, firm, adviser, instruments, balanced, growth, spec)

        # ── More Advise & Engage rows (meetings + client reports) ────────────
        from seed.engage_extra import seed_engage_extra
        await seed_engage_extra(s, firm)

        # ── Exception / non-happy-path scenarios (governance visibly triggers) ─
        from seed.exceptions import seed_exceptions
        await seed_exceptions(s, firm)

        # ── Example advisor-defined skills ───────────────────────────────────
        from seed.skills_seed import seed_skills
        await seed_skills(s, firm)

        # ── Acquire & onboard: prospect cases + an acquired book ─────────────
        await _acquire_onboard(s, firm)

        # ── Firm research (grounding for agents) ─────────────────────────────
        for doc in _research_docs(firm.id):
            s.add(doc)
            await s.flush()
            await knowledge.ingest_document(s, doc)

        # ── Connectors + valuation ───────────────────────────────────────────
        await ensure_default_connectors(s, firm.id)
        await s.commit()

    # Real market data + price history + revaluation (network-dependent, with fallbacks).
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one()
        try:
            await sync_market_data(s, firm.id)
        except Exception as exc:  # offline — keep synthetic prices
            log.warning("seed_market_sync_failed", error=str(exc))
        try:
            await _seed_price_history(s, firm.id)
        except Exception as exc:
            log.warning("seed_history_failed", error=str(exc))
        await revalue_firm(s, firm.id)
        await s.commit()

    # Pre-run a book-wide scan so the cockpit & activity are alive on first load.
    async with SessionLocal() as s:
        firm = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one()
        try:
            from app.atlas.base import Subject
            from app.atlas.runtime import run_agent
            await run_agent(s, firm=firm, agent_key=AgentKey.NEXT_BEST_ACTION,
                            subject=Subject("firm", firm.id, firm.name), trigger="seed_scan")
            await s.commit()
        except Exception as exc:  # pragma: no cover
            await s.rollback()
            log.warning("seed_prerun_failed", error=str(exc))
    log.info("seed_complete")


# Varied households (generated). Holdings are (symbol, qty, cost_per_unit, days_ago).
EXTRA_HOUSEHOLDS = [
    {"name": "The Nguyen Family", "segment": "private_wealth", "model": "growth", "mandate": "advisory",
     "custodian": "FNZ", "cash": 40000,
     "persons": [{"name": "Binh Nguyen", "dob": "1971-05-02", "held_away": 320000, "life_stage": "accumulation"},
                 {"name": "Lan Nguyen", "dob": "1974-08-19"}],
     "holdings": [("AAPL", 600, 150, 800), ("MSFT", 300, 280, 700), ("AGG", 400, 100, 600)],
     "goals": [{"name": "Retirement", "kind": "retirement", "target": 3000000, "years": 16, "share": 0.7},
               {"name": "Children's education", "kind": "education", "target": 250000, "years": 8, "share": 0.3}]},
    {"name": "Aroha Whānau Trust", "segment": "for_purpose", "model": "balanced", "mandate": "discretionary",
     "custodian": "BNY Pershing", "cash": 60000, "entity": {"name": "Aroha Whānau Trust", "type": "iwi"},
     "persons": [], "values": {"themes": ["indigenous enterprise", "education"], "exclusions": ["tobacco"]},
     "holdings": [("SPK.NZ", 6000, 4.5, 900), ("AIR.NZ", 8000, 0.9, 800), ("AGG", 700, 99, 700), ("VNQ", 400, 80, 600)],
     "goals": [{"name": "Intergenerational fund", "kind": "legacy", "target": 2000000, "years": 25, "share": 1.0}]},
    {"name": "The O'Brien Household", "segment": "mass_affluent", "model": "growth", "mandate": "advisory",
     "custodian": "FNZ", "cash": 15000,
     "persons": [{"name": "Sean O'Brien", "dob": "1986-02-11"}],
     "holdings": [("AAPL", 150, 200, 500), ("SPK.NZ", 3000, 4.9, 600), ("AGG", 120, 101, 500)],
     "goals": [{"name": "First home upgrade", "kind": "property", "target": 200000, "years": 5, "share": 1.0}]},
    {"name": "Kahurangi Foundation", "segment": "for_purpose", "model": "balanced", "mandate": "discretionary",
     "custodian": "BNY Pershing", "cash": 90000, "entity": {"name": "Kahurangi Foundation", "type": "foundation"},
     "persons": [], "values": {"mission": "Conservation & climate", "exclusions": ["tobacco", "fossil_fuels"]},
     "holdings": [("MSFT", 250, 300, 800), ("AGG", 900, 100, 700), ("VNQ", 350, 88, 600)],
     "goals": [{"name": "Perpetual endowment", "kind": "legacy", "target": 1500000, "years": 20, "share": 1.0}]},
    {"name": "The Müller Family", "segment": "private_wealth", "model": "balanced", "mandate": "discretionary",
     "custodian": "BNY Pershing", "cash": 50000,
     "persons": [{"name": "Anders Müller", "dob": "1963-11-23", "held_away": 820000, "life_stage": "pre-retirement"},
                 {"name": "Sofia Müller", "dob": "1966-03-30"},
                 {"name": "Erik Müller", "dob": "1996-07-14", "next_gen": True}],
     "holdings": [("AAPL", 1200, 160, 900), ("MSFT", 500, 290, 800), ("AGG", 1000, 100, 700),
                  ("VNQ", 400, 85, 600), ("PPEF1", 800, 100, 1000)],
     "goals": [{"name": "Comfortable retirement", "kind": "retirement", "target": 4500000, "years": 6, "share": 0.7},
               {"name": "Family legacy", "kind": "legacy", "target": 1000000, "years": 15, "share": 0.3}]},
    {"name": "Tanaka Holdings", "segment": "institutional", "model": "growth", "mandate": "discretionary",
     "custodian": "Addepar", "cash": 120000, "entity": {"name": "Tanaka Holdings Ltd", "type": "company"},
     "persons": [], "holdings": [("AAPL", 900, 170, 800), ("MSFT", 700, 300, 700), ("AGG", 600, 100, 600)],
     "goals": [{"name": "Treasury growth", "kind": "other", "target": 5000000, "years": 10, "share": 1.0}]},
    {"name": "The Reddy Household", "segment": "mass_affluent", "model": "balanced", "mandate": "advisory",
     "custodian": "FNZ", "cash": 20000,
     "persons": [{"name": "Arjun Reddy", "dob": "1982-09-05"}, {"name": "Priya Reddy", "dob": "1984-12-01"}],
     "holdings": [("SPK.NZ", 4000, 4.6, 600), ("AGG", 300, 100, 500), ("VNQ", 200, 86, 400)],
     "goals": [{"name": "Children's education", "kind": "education", "target": 320000, "years": 12, "share": 1.0}]},
    {"name": "Whetu Trust", "segment": "private_wealth", "model": "balanced", "mandate": "discretionary",
     "custodian": "BNY Pershing", "cash": 35000, "entity": {"name": "Whetu Trust", "type": "trust"},
     "persons": [], "holdings": [("AAPL", 700, 180, 800), ("AGG", 800, 100, 700), ("VNQ", 300, 84, 600), ("PPEF1", 500, 100, 900)],
     "goals": [{"name": "Beneficiary support", "kind": "legacy", "target": 1800000, "years": 18, "share": 1.0}]},
    {"name": "The Andersen Family", "segment": "private_wealth", "model": "growth", "mandate": "advisory",
     "custodian": "FNZ", "cash": 45000,
     "persons": [{"name": "Markus Andersen", "dob": "1969-04-17", "held_away": 460000, "life_stage": "accumulation"},
                 {"name": "Ingrid Andersen", "dob": "1972-10-08"}],
     "holdings": [("MSFT", 600, 280, 800), ("AAPL", 400, 170, 700), ("AGG", 300, 101, 600)],
     "goals": [{"name": "Retirement", "kind": "retirement", "target": 3500000, "years": 14, "share": 0.8}]},
]


async def _extra_household(s, firm, adviser, instruments, balanced, growth, spec):
    model = growth if spec["model"] == "growth" else balanced
    hh = Household(firm_id=firm.id, name=spec["name"], segment=ClientSegment(spec["segment"]),
                   values=spec.get("values", {}))
    s.add(hh)
    await s.flush()

    owner_person_id = owner_entity_id = None
    edge_to_type, edge_to_id = None, None
    for i, pd in enumerate(spec.get("persons", [])):
        profile = {"risk_profile": spec["model"]}
        if pd.get("held_away"):
            profile["held_away"] = pd["held_away"]
        if pd.get("life_stage"):
            profile["life_stage"] = pd["life_stage"]
        p = Person(firm_id=firm.id, household_id=hh.id, full_name=pd["name"],
                   preferred_name=pd["name"].split()[0],
                   date_of_birth=date.fromisoformat(pd["dob"]) if pd.get("dob") else None,
                   segment=ClientSegment(spec["segment"]), is_next_gen=pd.get("next_gen", False),
                   kyc={"status": "verified", "id_verified": True, "aml_screened": True}, profile=profile)
        s.add(p)
        await s.flush()
        if i == 0:
            owner_person_id, edge_to_type, edge_to_id = p.id, "person", p.id

    if spec.get("entity"):
        ent = LegalEntity(firm_id=firm.id, household_id=hh.id, name=spec["entity"]["name"],
                          entity_type=EntityType(spec["entity"]["type"]),
                          governance={}, impact_objectives=spec.get("values", {}))
        s.add(ent)
        await s.flush()
        owner_entity_id, edge_to_type, edge_to_id = ent.id, "entity", ent.id

    mandate = Mandate(firm_id=firm.id, person_id=owner_person_id, entity_id=owner_entity_id,
                      name=f"{spec['name']} — {model.name.split()[-1]}",
                      mandate_type=MandateType(spec["mandate"]),
                      suitability={"risk_profile": spec["model"]},
                      constraints={"cgt_budget": 12000}, model_portfolio_id=model.id)
    s.add(mandate)
    await s.flush()
    acc = Account(firm_id=firm.id, mandate_id=mandate.id, name=f"{spec['name'].split()[-1]} A/C",
                  custodian=spec["custodian"], currency="NZD", cash_balance=spec["cash"])
    s.add(acc)
    await s.flush()
    for sym, qty, cpu, days in spec["holdings"]:
        await _holding(s, firm.id, acc, instruments[sym], qty, cost=cpu * qty, lots=[(qty, float(cpu), days)])
    for g in spec.get("goals", []):
        s.add(Goal(firm_id=firm.id, household_id=hh.id, person_id=owner_person_id, name=g["name"],
                   kind=g["kind"], target_amount=g["target"],
                   target_date=date(TODAY.year + g["years"], 1, 1),
                   assumptions={"years": g["years"], "funding_share": g["share"]}))
    if edge_to_id:
        s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                               to_type=edge_to_type, to_id=edge_to_id))
    await s.flush()


async def _seed_price_history(s, firm_id):
    """Store ~12 months of real monthly closes per public instrument (powers attribution)."""
    from app.conduit.marketdata import fetch_history_yahoo

    instruments = (
        await s.execute(select(Instrument).where(Instrument.firm_id == firm_id,
                                                 Instrument.market_type == MarketType.PUBLIC))
    ).scalars().all()
    added = 0
    for inst in instruments:
        hist = await fetch_history_yahoo(inst.symbol)
        if len(hist) < 2:
            # Synthetic deterministic monthly path back from the current synthetic price.
            latest = (await s.execute(select(Price).where(Price.instrument_id == inst.id)
                                      .order_by(Price.as_of.desc()))).scalars().first()
            base = float(latest.close) if latest else 100.0
            for m in range(12, 0, -1):
                d = date(TODAY.year, TODAY.month, 1) - timedelta(days=30 * m)
                px = base * (1 - 0.012 * m)  # gentle upward drift to today
                hist.append((d.isoformat(), round(px, 4)))
        for d_iso, close in hist:
            d = date.fromisoformat(d_iso)
            if d >= TODAY:
                continue
            exists = (await s.execute(select(Price).where(Price.instrument_id == inst.id,
                                                          Price.as_of == d))).scalar_one_or_none()
            if exists:
                continue
            s.add(Price(firm_id=firm_id, instrument_id=inst.id, as_of=d, close=close,
                        currency=inst.currency, source="yahoo_history", is_real=True))
            added += 1
    await s.flush()
    log.info("price_history_seeded", points=added)


async def _holding(s, firm_id, account, instrument, qty, cost, lots):
    h = Holding(firm_id=firm_id, account_id=account.id, instrument_id=instrument.id,
                quantity=qty, market_value=0, cost_basis=cost,
                lineage={"source": account.custodian, "as_of": TODAY.isoformat()}, confidence=0.9)
    s.add(h)
    await s.flush()
    for q, cpu, days_ago in lots:
        s.add(TaxLot(firm_id=firm_id, holding_id=h.id, quantity=q, cost_per_unit=cpu,
                     acquired_on=TODAY - timedelta(days=days_ago)))
    await s.flush()
    return h


async def _acquire_onboard(s, firm):
    """Seed two onboarding cases (one clean individual, one trust with a PEP trustee) and an
    un-run acquired-book batch. The agents are run on demand from Studio."""
    # Case 1 — individual, clean AML, full documents.
    daniel = OnboardingCase(
        firm_id=firm.id, prospect_name="Daniel Okonkwo", is_entity=False,
        segment="private_wealth",
        intake={"email": "daniel.okonkwo@example.com", "risk_profile": "growth",
                "objectives": ["retirement", "education"], "time_horizon_years": 18,
                "capacity_for_loss": "medium", "mandate_preference": "advisory",
                "source_of_wealth": "Business sale proceeds", "cgt_budget": 12000,
                "associated_parties": []},
    )
    s.add(daniel)
    await s.flush()
    for dt in ("passport", "drivers_licence", "proof_of_address"):
        s.add(OnboardingDocument(firm_id=firm.id, case_id=daniel.id, doc_type=dt,
                                 filename=f"{dt}_daniel.pdf",
                                 raw_text=sample_docs.generate(dt, "Daniel Okonkwo")))

    # Case 2 — family trust whose trustee is a PEP (triggers an AML review exception).
    sokolov = OnboardingCase(
        firm_id=firm.id, prospect_name="Sokolov Family Trust", is_entity=True, entity_type="trust",
        segment="private_wealth",
        intake={"risk_profile": "balanced", "objectives": ["wealth preservation"],
                "time_horizon_years": 25, "mandate_preference": "discretionary",
                "source_of_wealth": "Inherited family business",
                "associated_parties": ["Viktor Sokolov", "Anna Sokolov"], "cgt_budget": 15000},
    )
    s.add(sokolov)
    await s.flush()
    s.add(OnboardingDocument(
        firm_id=firm.id, case_id=sokolov.id, doc_type="trust_deed", filename="trust_deed_sokolov.pdf",
        raw_text=sample_docs.trust_deed("Sokolov Family Trust", settlor="Viktor Sokolov",
                                        trustees=["Viktor Sokolov", "Anna Sokolov", "Aurera Trustees Ltd"],
                                        beneficiaries=["Sokolov children"])))
    s.add(OnboardingDocument(
        firm_id=firm.id, case_id=sokolov.id, doc_type="overseas_pension",
        filename="overseas_pension_sokolov.pdf", raw_text=sample_docs.overseas_pension()))

    # An acquired book, ready to reconcile.
    s.add(BookIntegrationBatch(firm_id=firm.id, source_firm="Northbridge Advisory",
                               feed=sample_feed("Northbridge Advisory")))
    await s.flush()


async def _for_purpose_household(s, firm, adviser, instruments, model):
    hh = Household(firm_id=firm.id, name="Whitestone Charitable Foundation",
                   segment=ClientSegment.FOR_PURPOSE,
                   values={"mission": "Community health & education", "exclusions": ["tobacco"]})
    s.add(hh)
    await s.flush()
    entity = LegalEntity(firm_id=firm.id, household_id=hh.id, name="Whitestone Charitable Foundation",
                         entity_type=EntityType.CHARITY,
                         governance={"trustees": ["Board of 5"]},
                         impact_objectives={"mission": "Community health & education",
                                            "exclusions": ["tobacco", "fossil_fuels"]})
    s.add(entity)
    await s.flush()
    mandate = Mandate(firm_id=firm.id, entity_id=entity.id, name="Whitestone — Balanced (ESG)",
                      mandate_type=MandateType.DISCRETIONARY,
                      suitability={"risk_profile": "balanced", "values_exclusions": ["BTI", "tobacco"]},
                      constraints={"cgt_budget": 0}, model_portfolio_id=model.id)
    s.add(mandate)
    await s.flush()
    acc = Account(firm_id=firm.id, mandate_id=mandate.id, name="Foundation A/C", account_number="WCF-001",
                  custodian="BNY Pershing", currency="NZD", cash_balance=80000)
    s.add(acc)
    await s.flush()
    # Holds an excluded tobacco line — drift agent should propose exiting it.
    await _holding(s, firm.id, acc, instruments["BTI"], 2000, cost=35 * 2000, lots=[(2000, 35.0, 600)])
    await _holding(s, firm.id, acc, instruments["MSFT"], 200, cost=320 * 200, lots=[(200, 320.0, 500)])
    await _holding(s, firm.id, acc, instruments["AGG"], 500, cost=101 * 500, lots=[(500, 101.0, 900)])
    s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                           to_type="entity", to_id=entity.id))
    s.add(Goal(firm_id=firm.id, household_id=hh.id, name="Perpetual endowment", kind="legacy",
               target_amount=1000000, assumptions={"years": 20, "funding_share": 1.0}))


async def _simple_household(s, firm, adviser, instruments, model):
    hh = Household(firm_id=firm.id, name="The Patel Household", segment=ClientSegment.MASS_AFFLUENT,
                   values={})
    s.add(hh)
    await s.flush()
    p = Person(firm_id=firm.id, household_id=hh.id, full_name="Anika Patel", preferred_name="Anika",
               date_of_birth=date(1980, 7, 7), segment=ClientSegment.MASS_AFFLUENT,
               kyc={"id_verified": True, "aml_screened": True, "status": "verified"},
               profile={"risk_profile": "growth", "held_away": 90000})
    s.add(p)
    await s.flush()
    mandate = Mandate(firm_id=firm.id, person_id=p.id, name="Patel — Growth", mandate_type=MandateType.ADVISORY,
                      suitability={"risk_profile": "growth"}, constraints={"cgt_budget": 8000},
                      model_portfolio_id=model.id)
    s.add(mandate)
    await s.flush()
    acc = Account(firm_id=firm.id, mandate_id=mandate.id, name="Patel A/C", account_number="AP-001",
                  custodian="FNZ", currency="NZD", cash_balance=12000)
    s.add(acc)
    await s.flush()
    await _holding(s, firm.id, acc, instruments["AAPL"], 200, cost=160 * 200, lots=[(200, 160.0, 800)])
    await _holding(s, firm.id, acc, instruments["SPK.NZ"], 5000, cost=4.8 * 5000, lots=[(5000, 4.8, 700)])
    await _holding(s, firm.id, acc, instruments["AGG"], 150, cost=100 * 150, lots=[(150, 100.0, 600)])
    s.add(RelationshipEdge(firm_id=firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                           to_type="person", to_id=p.id))
    s.add(Goal(firm_id=firm.id, household_id=hh.id, person_id=p.id, name="Children's education",
               kind="education", target_amount=300000, assumptions={"years": 10, "funding_share": 1.0}))


def _research_docs(firm_id) -> list[ResearchDocument]:
    return [
        ResearchDocument(
            firm_id=firm_id, title="House View — Q2: Measured Risk, Quality Tilt", doc_type="house_view",
            author="Aurera Investment Committee",
            summary="Neutral equities with a quality tilt; favour duration in fixed income.",
            body="Our house view maintains a neutral allocation to equities with a deliberate tilt "
                 "toward quality and cash-generative businesses. We are wary of single-name "
                 "concentration and recommend trimming positions exceeding 20% of a portfolio. In "
                 "fixed income we favour adding duration via aggregate bond exposure. For tax-managed "
                 "portfolios, harvest losses opportunistically and stay within each client's stated "
                 "capital-gains budget. Rebalance back to target when any asset class drifts beyond "
                 "its five-percent tolerance band.",
            tags=["house_view", "rebalancing", "tax"]),
        ResearchDocument(
            firm_id=firm_id, title="Adviser Playbook — Values-Aligned Portfolios", doc_type="playbook",
            author="Aurera Advice Standards",
            summary="How to implement exclusions and impact themes without sacrificing diversification.",
            body="When a mandate carries values exclusions, exit excluded holdings on the next "
                 "rebalance and redeploy into diversified, screened alternatives. For for-purpose "
                 "entities, frame all reporting against the entity's stated mission and impact "
                 "objectives. Tobacco, controversial weapons and, where specified, fossil fuels are "
                 "common exclusions for charitable foundations.",
            tags=["values", "esg", "for_purpose"]),
        ResearchDocument(
            firm_id=firm_id, title="Decumulation & Longevity Note", doc_type="research",
            author="Aurera Research",
            summary="Sequencing-of-returns risk dominates the early retirement window.",
            body="In the decumulation phase, sequencing-of-returns risk dominates. Maintain a cash "
                 "buffer of 1–2 years of spending and avoid forced selling of growth assets during "
                 "drawdowns. Stress-test the plan against historical shocks such as the 2008 GFC and "
                 "the 2020 COVID drawdown before reassuring clients.",
            tags=["retirement", "decumulation", "stress"]),
    ]


def main() -> None:
    configure_logging()
    asyncio.run(seed())


if __name__ == "__main__":
    main()
