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
    AgentKey, AssetClass, AutonomyTier, ClientSegment, EntityType, MandateType, MarketType,
    PartyRole, UserRole,
)
from app.models.graph import Account, Goal, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.knowledge import ResearchDocument
from app.models.portfolio import (
    Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot,
)
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm
from app.models.onboarding import (
    BeneficialOwner, BookIntegrationBatch, DisclosureDelivery, FeeSchedule, OnboardingCase,
    OnboardingDocument, OnboardingParty, TransferRequest,
)
from app.models.engagement import Meeting
from app.models.client_experience import HeirJourney, Message, default_heir_steps
from app.models.enums import HeirJourneyStatus, MessageAuthor
from datetime import datetime, timezone
from app.aurea_core import disclosures, sample_docs
from app.aurea_core.sample_book import sample_feed
from app.agents.catalogue import CATALOGUE

log = get_logger("aurea.seed")
PW = hash_password("aurea")
TODAY = date.today()
# Timezone-aware "now", used to backdate seeded records so age-based UI (SLA state,
# days-in-stage) has something to read on a freshly seeded database.
_NOW = datetime.now(timezone.utc)


async def seed() -> None:
    async with SessionLocal() as s:
        existing = (await s.execute(select(Firm).where(Firm.slug == "demo"))).scalar_one_or_none()
        if existing:
            log.info("seed_skip_exists")
            return

        firm = Firm(
            slug="demo", name="Astra for Wealth", legal_name="Astra for Wealth",
            jurisdiction="NZ", regulator="FMA", base_currency="NZD",
            branding={
                "primary": "#163a52", "accent": "#c8a35e", "logo_text": "Astra for Wealth",
                "tagline": "Governed AI for Wealth Management",
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
        admin = User(firm_id=firm.id, email="admin@aurea.demo", full_name="Platform Admin",
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
                     profile={
                         "risk_profile": "balanced", "life_stage": "pre-retirement",
                         "held_away": 620000,
                         "tax": {
                             "marginal_rate": 0.33, "pir": 0.28,
                             "kiwisaver_rate": 0.03, "annual_income": 280000,
                         },
                         "properties": [
                             # Approaching 2-year bright-line (safe ~Nov 2026)
                             {"address": "12 Harbour View Tce, Auckland",
                              "acquired_on": "2024-11-15", "value": 1850000},
                             # Approaching 10-year bright-line (safe Jan 2027)
                             {"address": "47 Lakeside Dr, Queenstown",
                              "acquired_on": "2017-01-20", "value": 1200000},
                         ],
                     })
        mei = Person(firm_id=firm.id, household_id=chen.id, full_name="Mei Chen", preferred_name="Mei",
                     date_of_birth=date(1965, 9, 3), segment=ClientSegment.PRIVATE_WEALTH,
                     kyc={"id_verified": True, "aml_screened": True, "status": "verified"},
                     profile={
                         "risk_profile": "balanced",
                         "tax": {
                             "marginal_rate": 0.175, "pir": 0.175,
                             "kiwisaver_rate": 0.03, "annual_income": 120000,
                         },
                     })
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
                                 suitability={"risk_profile": "growth"},
                                 constraints={"cgt_budget": 10000, "account_type": "direct"},
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
    # ── US jurisdiction: estate tax sunset exposure ───────────────────────────
    {"name": "The Morrison Family", "segment": "private_wealth", "model": "balanced", "mandate": "advisory",
     "custodian": "Schwab", "cash": 120000,
     "values": {"jurisdiction": "US", "themes": ["growth", "ESG"]},
     "persons": [
         {"name": "Robert Morrison", "dob": "1959-03-15", "held_away": 1800000, "life_stage": "pre-retirement",
          "tax": {"annual_income": 850000, "marginal_rate": 0.37, "filing_status": "married_joint",
                  "ira_value": 3200000, "roth_value": 680000},
          "properties": [{"address": "742 Nob Hill Blvd, San Francisco CA",
                          "acquired_on": "2008-06-01", "value": 4500000, "type": "primary_residence"}]},
         {"name": "Patricia Morrison", "dob": "1962-07-22",
          "tax": {"annual_income": 180000, "marginal_rate": 0.32}},
     ],
     "holdings": [("AAPL", 2000, 140, 1800), ("MSFT", 1500, 250, 1600), ("AGG", 2000, 100, 1400),
                  ("VNQ", 1000, 80, 1200), ("PPEF1", 2000, 100, 1500)],
     "goals": [{"name": "Retirement", "kind": "retirement", "target": 8000000, "years": 8, "share": 0.8},
               {"name": "Family legacy", "kind": "legacy", "target": 3000000, "years": 20, "share": 0.2}]},
    # ── UK jurisdiction: IHT pension inclusion exposure ───────────────────────
    {"name": "The Hartley Family", "segment": "private_wealth", "model": "balanced", "mandate": "advisory",
     "custodian": "HSBC", "cash": 85000,
     "values": {"jurisdiction": "UK"},
     "persons": [
         {"name": "James Hartley", "dob": "1958-09-12", "life_stage": "pre-retirement",
          "tax": {"annual_income": 320000, "marginal_rate": 0.45,
                  "pension_pot": 2200000, "isa_value": 240000, "lpa_status": "none"}},
         {"name": "Catherine Hartley", "dob": "1961-04-05",
          "tax": {"annual_income": 95000, "marginal_rate": 0.40,
                  "pension_pot": 850000, "isa_value": 110000, "lpa_status": "none"}},
     ],
     "holdings": [("AAPL", 1200, 160, 1600), ("MSFT", 800, 290, 1500),
                  ("AGG", 1500, 100, 1400), ("VNQ", 600, 88, 1300)],
     "goals": [{"name": "Retirement", "kind": "retirement", "target": 5000000, "years": 10, "share": 0.8},
               {"name": "IHT-efficient legacy", "kind": "legacy", "target": 2000000, "years": 25, "share": 0.2}]},
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
        if pd.get("tax"):
            profile["tax"] = pd["tax"]
        if pd.get("properties"):
            profile["properties"] = pd["properties"]
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
    """Seed a spread of onboarding cases that exercises the L200 surface, plus an un-run
    acquired-book batch.

    The cases deliberately cover six registration types, all three AML risk tiers, entities
    with complete and incomplete beneficial ownership, a sanctions hit, a PEP hit, an
    adverse-media hit, and a range of ages so SLA state reads on-track / at-risk / breached.

    Agent *outputs* — readiness_score, nigo_flag, screening_log — are deliberately left
    unset so that running an agent from Studio visibly changes something. What is seeded is
    the input side: registration type, risk tier, parties and documents.
    """
    # Idempotent by prospect name, so this can also top up an existing database that was
    # seeded before these cases were added (see seed/onboarding_refresh.py).
    existing = set((await s.execute(
        select(OnboardingCase.prospect_name).where(OnboardingCase.firm_id == firm.id)
    )).scalars().all())

    async def _aged(case: OnboardingCase, days: int) -> OnboardingCase | None:
        """Add a case backdated by `days`, or return None if it already exists."""
        if case.prospect_name in existing:
            log.info("seed_case_skip", name=case.prospect_name)
            return None
        case.created_at = _NOW - timedelta(days=days)
        s.add(case)
        await s.flush()
        return case

    # ── Case 1 — individual, low risk, complete. The clean path. ──────────────
    daniel = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Daniel Okonkwo", is_entity=False,
        registration_type="individual", segment="private_wealth",
        aml_risk_tier="low", aml_risk_score=18.0, edd_status="cdd", sla_days=5,
        intake={"email": "daniel.okonkwo@example.com", "risk_profile": "growth",
                "objectives": ["retirement", "education"], "time_horizon_years": 18,
                "capacity_for_loss": "medium", "mandate_preference": "advisory",
                "source_of_wealth": "Business sale proceeds", "cgt_budget": 12000,
                "beneficiary_name": "Amara Okonkwo", "associated_parties": []},
    ), days=2)
    if daniel:
        for dt in ("passport", "drivers_licence", "proof_of_address"):
            s.add(OnboardingDocument(firm_id=firm.id, case_id=daniel.id, doc_type=dt,
                                     filename=f"{dt}_daniel.pdf",
                                     raw_text=sample_docs.generate(dt, "Daniel Okonkwo")))

    # ── Case 2 — trust with a PEP trustee. High risk, EDD, BOs complete. ──────
    sokolov = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Sokolov Family Trust", is_entity=True, entity_type="trust",
        registration_type="trust", segment="private_wealth",
        aml_risk_tier="high", aml_risk_score=78.0, edd_status="edd_pending", sla_days=10,
        intake={"risk_profile": "balanced", "objectives": ["wealth preservation"],
                "time_horizon_years": 25, "mandate_preference": "discretionary",
                "source_of_wealth": "Inherited family business — third-generation manufacturing",
                "source_of_funds": "Proceeds of 2019 sale of Sokolov Manufacturing OAO",
                "associated_parties": ["Viktor Sokolov", "Anna Sokolov"], "cgt_budget": 15000},
    ), days=21)
    if sokolov:
        s.add(OnboardingDocument(
            firm_id=firm.id, case_id=sokolov.id, doc_type="trust_deed", filename="trust_deed_sokolov.pdf",
            raw_text=sample_docs.trust_deed("Sokolov Family Trust", settlor="Viktor Sokolov",
                                            trustees=["Viktor Sokolov", "Anna Sokolov", "Aurera Trustees Ltd"],
                                            beneficiaries=["Sokolov children"])))
        s.add(OnboardingDocument(
            firm_id=firm.id, case_id=sokolov.id, doc_type="overseas_pension",
            filename="overseas_pension_sokolov.pdf", raw_text=sample_docs.overseas_pension()))
        for name, pct, control in (("Viktor Sokolov", 55.0, True), ("Anna Sokolov", 45.0, False)):
            s.add(BeneficialOwner(firm_id=firm.id, case_id=sokolov.id, legal_name=name,
                                  ownership_pct=pct, is_control_person=control,
                                  dob="1962-04-11" if control else "1968-09-02",
                                  address="Geneva, Switzerland"))

    # ── Case 3 — employer rollover. Drives the PTE 2020-02 documenter. ────────
    priya = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Priya Raman", is_entity=False,
        registration_type="employer_rollover", segment="private_wealth",
        aml_risk_tier="low", aml_risk_score=22.0, edd_status="cdd", sla_days=5,
        intake={"email": "priya.raman@example.com", "risk_profile": "growth",
                "objectives": ["retirement"], "time_horizon_years": 22,
                "mandate_preference": "advisory", "fee_bps": 80,
                "source_of_wealth": "Employment income — 18 years at Halcyon Health",
                "source_of_funds": "401(k) direct rollover from Halcyon Health plan",
                "beneficiary_name": "Arjun Raman",
                # Consumed by the PTE documenter for the plan-vs-IRA comparison.
                "leaving_plan": {"plan_name": "Halcyon Health 401(k)",
                                 "expense_ratio_bps": 52,
                                 "investment_menu": "21 funds, no managed account",
                                 "recordkeeper": "Empower"},
                "associated_parties": []},
    ), days=9)
    if priya:
        for dt in ("passport", "proof_of_address"):
            s.add(OnboardingDocument(firm_id=firm.id, case_id=priya.id, doc_type=dt,
                                     filename=f"{dt}_raman.pdf",
                                     raw_text=sample_docs.generate(dt, "Priya Raman")))

    # ── Case 4 — LLC, adverse-media party, BOs complete to 100%. ──────────────
    meridian = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Meridian Capital Partners LLC", is_entity=True,
        entity_type="llc", registration_type="entity_llc", segment="institutional",
        aml_risk_tier="medium", aml_risk_score=54.0, edd_status="edd_pending", sla_days=10,
        intake={"risk_profile": "balanced", "objectives": ["capital preservation", "income"],
                "time_horizon_years": 10, "mandate_preference": "discretionary",
                "source_of_wealth": "Operating profits — commercial property advisory",
                "associated_parties": ["Marcus Delacroix", "Helena Ostrowski"],
                "cgt_budget": 40000},
    ), days=12)
    if meridian:
        s.add(OnboardingDocument(
            firm_id=firm.id, case_id=meridian.id, doc_type="trust_deed",
            filename="formation_docs_meridian.pdf",
            raw_text=sample_docs.trust_deed("Meridian Capital Partners LLC",
                                            settlor="Marcus Delacroix",
                                            trustees=["Marcus Delacroix", "Helena Ostrowski"],
                                            beneficiaries=["Members per operating agreement"])))
        for name, pct, control in (("Marcus Delacroix", 40.0, True),
                                   ("Helena Ostrowski", 35.0, False),
                                   ("Jonas Vermeer", 25.0, False)):
            s.add(BeneficialOwner(firm_id=firm.id, case_id=meridian.id, legal_name=name,
                                  ownership_pct=pct, is_control_person=control,
                                  address="Auckland, New Zealand"))

    # ── Case 5 — sanctions hit, incomplete ownership, past SLA. Worst case. ───
    castellanos = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Castellanos Holdings SA", is_entity=True,
        entity_type="corporation", registration_type="entity_corp", segment="institutional",
        aml_risk_tier="high", aml_risk_score=91.0, edd_status="edd_pending", sla_days=10,
        intake={"risk_profile": "conservative", "objectives": ["capital preservation"],
                "time_horizon_years": 8, "mandate_preference": "advisory",
                # Left thin deliberately — the EDD narrator should flag the gap.
                "source_of_wealth": "",
                "associated_parties": ["Imelda Castellanos"], "cgt_budget": 0},
    ), days=34)
    if castellanos:
        # One BO, no control person, ownership well short of 100 — the CDD Rule gap.
        s.add(BeneficialOwner(firm_id=firm.id, case_id=castellanos.id,
                              legal_name="Imelda Castellanos", ownership_pct=30.0,
                              is_control_person=False, address="Caracas, Venezuela"))

    # ── Case 6 — Roth IRA with no beneficiary designated. NIGO path. ──────────
    whitfield = await _aged(OnboardingCase(
        firm_id=firm.id, prospect_name="Emma Whitfield", is_entity=False,
        registration_type="roth_ira", segment="mass_affluent",
        aml_risk_tier="low", aml_risk_score=15.0, edd_status="cdd", sla_days=5,
        intake={"email": "emma.whitfield@example.com", "risk_profile": "conservative",
                "objectives": ["retirement"], "time_horizon_years": 30,
                "mandate_preference": "advisory",
                "source_of_wealth": "Salary — senior nurse practitioner",
                # No beneficiary_name: the NIGO agent should raise missing_beneficiary.
                "associated_parties": []},
    ), days=4)
    if whitfield:
        # Only one of the two required identity documents — should surface as missing_id.
        s.add(OnboardingDocument(firm_id=firm.id, case_id=whitfield.id, doc_type="passport",
                                 filename="passport_whitfield.pdf",
                                 raw_text=sample_docs.generate("passport", "Emma Whitfield")))

    # An acquired book, ready to reconcile — also guarded so a top-up doesn't duplicate it.
    has_batch = (await s.execute(
        select(BookIntegrationBatch.id).where(BookIntegrationBatch.firm_id == firm.id)
    )).first()
    if not has_batch:
        s.add(BookIntegrationBatch(firm_id=firm.id, source_firm="Northbridge Advisory",
                                   feed=sample_feed("Northbridge Advisory")))
    await s.flush()
    await _gate_scenarios(s, firm)


async def _fee_schedules(s, firm) -> dict:
    """The firm's fee-schedule library (L200 Track A step 2).

    A library to select from rather than free text per case — L200's control against
    mis-set fees is "fee-schedule library with maker/checker". Returns code -> schedule.
    """
    existing = {
        f.code: f for f in (await s.execute(
            select(FeeSchedule).where(FeeSchedule.firm_id == firm.id)
        )).scalars().all()
    }
    specs = [
        dict(code="PW-TIERED", name="Private Wealth — tiered",
             fee_type="tiered_bps", minimum_annual_fee=3_000,
             tiers=[{"min_aum": 0, "max_aum": 1_000_000, "bps": 100},
                    {"min_aum": 1_000_000, "max_aum": 5_000_000, "bps": 75},
                    {"min_aum": 5_000_000, "max_aum": None, "bps": 50}],
             notes="Standard private wealth schedule. Breakpoints applied marginally."),
        dict(code="MA-FLAT", name="Mass Affluent — flat rate",
             fee_type="flat_bps", flat_bps=95, minimum_annual_fee=1_500,
             notes="Single rate, no breakpoints."),
        dict(code="INST-TIERED", name="Institutional — tiered",
             fee_type="tiered_bps", minimum_annual_fee=25_000,
             tiers=[{"min_aum": 0, "max_aum": 10_000_000, "bps": 45},
                    {"min_aum": 10_000_000, "max_aum": None, "bps": 30}],
             notes="Institutional mandates; householding applied across related entities."),
        dict(code="FP-RETAINER", name="For Purpose — annual retainer",
             fee_type="flat_fee", flat_fee=18_000,
             notes="Fixed retainer for charitable and foundation clients."),
    ]
    out = {}
    for spec in specs:
        if spec["code"] in existing:
            out[spec["code"]] = existing[spec["code"]]
            continue
        f = FeeSchedule(firm_id=firm.id, currency="NZD", is_active=True, **spec)
        s.add(f)
        await s.flush()
        out[spec["code"]] = f
        log.info("seed_fee_schedule", code=spec["code"])
    return out


async def _gate_scenarios(s, firm):
    """Cases that exercise the track and gate states the original six do not reach.

    The first six all block on everything at once, which is realistic for fresh intake but
    leaves the cleared path, the isolated-blocker path and the funding states untested.
    These four are seeded with the evidence already in place — parties screened,
    disclosures delivered, CIP run, transfers in flight — so every track state and the
    open gate are visible on a fresh database.

    Idempotent by prospect name, like the cases above.
    """
    existing = set((await s.execute(
        select(OnboardingCase.prospect_name).where(OnboardingCase.firm_id == firm.id)
    )).scalars().all())

    schedules = await _fee_schedules(s, firm)
    required_disclosures = disclosures.required_for(firm.jurisdiction, "individual")

    def set_fee(case, code, aum, *, confirmed=True):
        """Assign a fee schedule, optionally already through maker/checker."""
        case.fee_schedule_id = schedules[code].id
        case.billing_method = "arrears"
        case.billing_frequency = "quarterly"
        case.billable_aum = aum
        case.fee_set_by = "sophie.adviser@aurea.demo"
        case.fee_set_at = _NOW - timedelta(days=2)
        if confirmed:
            # A different person confirms — the whole point of maker/checker.
            case.fee_confirmed_by = "compliance@aurea.demo"
            case.fee_confirmed_at = _NOW - timedelta(days=1)

    def deliver(case, doc_types, *, method="email", days_ago=1):
        for dt in doc_types:
            s.add(DisclosureDelivery(
                firm_id=firm.id, case_id=case.id, doc_type=dt,
                delivered_at=_NOW - timedelta(days=days_ago), method=method,
                evidence_ref=f"msg-{dt[:10]}-{case.prospect_name[:4].lower()}",
                delivered_by="sophie.adviser@aurea.demo",
                acknowledged_at=_NOW - timedelta(days=days_ago) if method == "portal" else None,
            ))

    def party(case, role, name, *, screened="clear", pct=None, control=False, dob=None):
        s.add(OnboardingParty(
            firm_id=firm.id, case_id=case.id, role=role, legal_name=name,
            dob=dob, address="Auckland, New Zealand", ownership_pct=pct,
            is_control_person=control, screening_status=screened, screening_hits=[],
            screened_at=_NOW - timedelta(days=1) if screened != "not_screened" else None,
            disposition_note=("Screened by Adverse Media & PEP Screener agent."
                              if screened != "not_screened" else None),
            cip_status="verified" if screened == "clear" else None,
            cip_checked_at=_NOW - timedelta(days=1) if screened == "clear" else None,
        ))

    # ── A. Every gate satisfied — the cleared path. ───────────────────────────
    if "The Ashworth Family" not in existing:
        ash = OnboardingCase(
            firm_id=firm.id, prospect_name="The Ashworth Family", is_entity=False,
            registration_type="joint_jtwros", segment="private_wealth",
            aml_risk_tier="low", aml_risk_score=12.0, edd_status="cdd", sla_days=5,
            cip_status="verified", cip_score=0.97, cip_reference_id="mock_socure_9f21c",
            status="review",
            intake={"email": "james.ashworth@example.com", "risk_profile": "balanced",
                    "objectives": ["retirement", "property"], "time_horizon_years": 12,
                    "mandate_preference": "advisory",
                    "source_of_wealth": "Professional income and inheritance",
                    "associated_parties": ["Claire Ashworth"]},
        )
        ash.created_at = _NOW - timedelta(days=3)
        s.add(ash)
        await s.flush()
        party(ash, PartyRole.OWNER, "James Ashworth", dob="1974-03-19")
        party(ash, PartyRole.JOINT_OWNER, "Claire Ashworth", dob="1976-11-02")
        set_fee(ash, "PW-TIERED", 1_850_000)
        deliver(ash, required_disclosures, method="portal", days_ago=2)
        for dt in ("passport", "drivers_licence"):
            s.add(OnboardingDocument(firm_id=firm.id, case_id=ash.id, doc_type=dt,
                                     filename=f"{dt}_ashworth.pdf",
                                     raw_text=sample_docs.generate(dt, "James Ashworth")))
        log.info("seed_scenario", name=ash.prospect_name, scenario="all gates pass")

    # ── B. Blocked only on disclosures — one isolated gate. ───────────────────
    if "Nakamura Holdings Trust" not in existing:
        nak = OnboardingCase(
            firm_id=firm.id, prospect_name="Nakamura Holdings Trust", is_entity=True,
            entity_type="trust", registration_type="trust", segment="institutional",
            aml_risk_tier="medium", aml_risk_score=41.0, edd_status="edd_complete",
            sla_days=10, cip_status="verified", cip_score=0.94,
            cip_reference_id="mock_socure_3b77a",
            intake={"risk_profile": "conservative", "objectives": ["capital preservation"],
                    "time_horizon_years": 20, "mandate_preference": "discretionary",
                    "source_of_wealth": "Sale of Nakamura Logistics KK, corroborated by "
                                        "share purchase agreement and bank confirmation",
                    "associated_parties": ["Kenji Nakamura", "Yuki Nakamura"]},
        )
        nak.created_at = _NOW - timedelta(days=6)
        s.add(nak)
        await s.flush()
        party(nak, PartyRole.TRUSTEE, "Kenji Nakamura", dob="1965-07-30")
        party(nak, PartyRole.TRUSTEE, "Yuki Nakamura", dob="1968-01-14")
        party(nak, PartyRole.POA_HOLDER, "Aurera Trustees Ltd")
        # Fee set but deliberately not yet confirmed — shows the maker/checker gate.
        set_fee(nak, "INST-TIERED", 14_200_000, confirmed=False)
        # One of three delivered — Track A in progress, everything else clear.
        deliver(nak, required_disclosures[:1], days_ago=3)
        s.add(OnboardingDocument(
            firm_id=firm.id, case_id=nak.id, doc_type="trust_deed",
            filename="trust_deed_nakamura.pdf",
            raw_text=sample_docs.trust_deed("Nakamura Holdings Trust", settlor="Kenji Nakamura",
                                            trustees=["Kenji Nakamura", "Yuki Nakamura"],
                                            beneficiaries=["Nakamura family"])))
        s.add(OnboardingDocument(firm_id=firm.id, case_id=nak.id, doc_type="passport",
                                 filename="passport_nakamura.pdf",
                                 raw_text=sample_docs.generate("passport", "Kenji Nakamura")))
        log.info("seed_scenario", name=nak.prospect_name, scenario="blocked on disclosures only")

    # ── C. Funding in flight — the waiting-external track state. ──────────────
    if "The Brennan Family" not in existing:
        bre = OnboardingCase(
            firm_id=firm.id, prospect_name="The Brennan Family", is_entity=False,
            registration_type="individual", segment="private_wealth",
            aml_risk_tier="low", aml_risk_score=19.0, edd_status="cdd", sla_days=5,
            cip_status="verified", cip_score=0.96, cip_reference_id="mock_socure_5d12e",
            status="approved",
            custodian_name="schwab", custodian_account_id="SCH-88213004",
            custodian_push_status="active", custodian_push_at=_NOW - timedelta(days=2),
            intake={"email": "orla.brennan@example.com", "risk_profile": "growth",
                    "objectives": ["retirement"], "time_horizon_years": 16,
                    "mandate_preference": "discretionary",
                    "source_of_wealth": "Equity vest — technology sector"},
        )
        bre.created_at = _NOW - timedelta(days=11)
        s.add(bre)
        await s.flush()
        party(bre, PartyRole.OWNER, "Orla Brennan", dob="1981-05-22")
        party(bre, PartyRole.BENEFICIARY, "Sean Brennan", dob="2009-02-08")
        set_fee(bre, "PW-TIERED", 1_240_000)
        deliver(bre, required_disclosures, days_ago=9)
        for dt in ("passport", "drivers_licence"):
            s.add(OnboardingDocument(firm_id=firm.id, case_id=bre.id, doc_type=dt,
                                     filename=f"{dt}_brennan.pdf",
                                     raw_text=sample_docs.generate(dt, "Orla Brennan")))
        s.add(TransferRequest(
            firm_id=firm.id, case_id=bre.id, transfer_type="acat", direction="in",
            amount=1_240_000, asset_description="Full ACAT — mixed equity and fixed income",
            status="in_transit", provider="mock", provider_ref="ACAT-770123",
            custodian="schwab", initiated_at=_NOW - timedelta(days=4)))
        log.info("seed_scenario", name=bre.prospect_name, scenario="funding in transit")

    # ── D. Sanctions match — the hard compliance stop. ────────────────────────
    if "Petrenko Private Office" not in existing:
        pet = OnboardingCase(
            firm_id=firm.id, prospect_name="Petrenko Private Office", is_entity=True,
            entity_type="corporation", registration_type="entity_corp",
            segment="institutional", aml_risk_tier="high", aml_risk_score=88.0,
            edd_status="edd_pending", sla_days=10,
            intake={"risk_profile": "conservative", "objectives": ["capital preservation"],
                    "time_horizon_years": 10, "mandate_preference": "advisory",
                    "source_of_wealth": "Stated as family office capital — not corroborated",
                    "associated_parties": ["Olena Petrenko"]},
        )
        pet.created_at = _NOW - timedelta(days=17)
        s.add(pet)
        await s.flush()
        # Olena Petrenko is on the synthetic watchlist as a PEP — run the screener to see it.
        party(pet, PartyRole.BENEFICIAL_OWNER, "Olena Petrenko",
              screened="not_screened", pct=100.0, control=True)
        deliver(pet, required_disclosures[:2], days_ago=12)
        log.info("seed_scenario", name=pet.prospect_name, scenario="PEP / high risk, unscreened")

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
