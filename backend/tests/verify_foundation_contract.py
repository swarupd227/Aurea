"""Prove the Foundation contract carries the book, and that native changes nothing (B-161, B-162).

A golden household set goes out through the native adapter into contract v1 and back in, through the sync, into a fresh
firm. The household brain, each account's valuation, the rebalancer's proposed orders, the wash-sale guard and the tax
report must match the firm it came from, and the CRM pipeline must read the same. A second import changes nothing and
keeps every Astra id. A record changed here and at the source is a break with its evidence, neither side written; the
source moving on is followed; a record the source no longer has is a break, not a deletion. An owner with no tax lots
makes the tax report, the wash-sale guard and the rebalancer read "not assessed". A firm nobody configured is native
in every domain, and its connectors sync exactly as they did.

Runs against a throwaway SQLite database.

    python -m tests.verify_foundation_contract
"""
from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.agents import drift_rebalancing as drift
from app.atlas.base import AgentContext, Subject
from app.aurea_core import firm_time, pipeline, tax_intelligence, wash_sale
from app.aurea_core.graph import household_brain
from app.aurea_core.valuation import account_valuation
from app.conduit.service import run_sync
from app.core import foundation as policy
from app.core.db import Base, utcnow
from app.foundation import contract as c
from app.foundation import owners
from app.foundation.adapters import ADAPTERS, Adapter, get_adapter, register
from app.foundation.sync import import_records, mark_missing
from app.models.connectors import Connector
from app.models.crm import CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import AutonomyTier, ConnectorDomain, ConnectorStatus, UserRole
from app.models.foundation import ExternalRef, FoundationBreak
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.identity import User
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation, TaxLot, Transaction
from app.models.tenant import Firm

PASS, FAIL = [], []


def check(label: str, got, want) -> None:
    ok = got == want
    (PASS if ok else FAIL).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}: got {str(got)[:160]}, want {str(want)[:160]}")


# ── The golden set ──────────────────────────────────────────────────────────────────────────────

async def golden(s, firm: Firm, today: date) -> dict:
    """Two households: a couple and their trust on a growth model, drifted, with lots in and out of the wash-sale
    window and a loss sale in it; a second family in cash. A prospect on the pipeline with two touches."""
    def inst(sym, cls, name):
        i = Instrument(id=uuid.uuid4(), firm_id=firm.id, symbol=sym, name=name, asset_class=cls, market_type="public",
                       currency="USD", values_tags={"esg": "high"} if sym == "CCC" else {})
        s.add(i)
        return i
    aaa, bbb, ccc = inst("AAA", "equity", "Alpha Equity"), inst("BBB", "equity", "Beta Equity"), inst("CCC", "fixed_income", "Core Bond")
    await s.flush()
    for i, closes in ((aaa, (195.0, 200.0)), (bbb, (101.0, 100.0)), (ccc, (99.5, 100.0))):
        for back, px in zip((3, 1), closes):
            s.add(Price(firm_id=firm.id, instrument_id=i.id, as_of=today - timedelta(days=back), close=px, currency="USD",
                        source="stooq", is_real=True))
    model = ModelPortfolio(id=uuid.uuid4(), firm_id=firm.id, name="Growth 70/30", drift_band=0.05)
    s.add(model)
    await s.flush()
    s.add_all([TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="equity", instrument_id=aaa.id, target_weight=0.7),
               TargetAllocation(firm_id=firm.id, model_id=model.id, asset_class="fixed_income", instrument_id=ccc.id, target_weight=0.3)])

    hh = Household(id=uuid.uuid4(), firm_id=firm.id, name="Golden Family", values={"esg": "prefer"}, review_cadence_months=12)
    hh2 = Household(id=uuid.uuid4(), firm_id=firm.id, name="Second Family")
    s.add_all([hh, hh2])
    await s.flush()
    ann = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Ann Golden", preferred_name="Ann",
                 email="ann@example.com", date_of_birth=date(1966, 4, 2), kyc={"status": "verified"},
                 profile={"income": 180000, "held_away": 50000})
    bob = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, full_name="Bob Golden", date_of_birth=date(1964, 9, 9))
    cy = Person(id=uuid.uuid4(), firm_id=firm.id, household_id=hh2.id, full_name="Cy Second")
    trust = LegalEntity(id=uuid.uuid4(), firm_id=firm.id, household_id=hh.id, name="Golden Family Trust", entity_type="trust",
                        governance={"trustees": ["Ann", "Bob"]}, jurisdiction="US")
    s.add_all([ann, bob, cy, trust])
    await s.flush()
    suit = {"risk_tolerance": "growth", "capacity_for_loss": "high", "max_equity": 0.8}
    m1 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=ann.id, name="Ann Growth", mandate_type="discretionary",
                 suitability=suit, constraints={"cgt_budget": 5000}, model_portfolio_id=model.id)
    m2 = Mandate(id=uuid.uuid4(), firm_id=firm.id, entity_id=trust.id, name="Trust Growth", mandate_type="advisory",
                 suitability=suit, constraints={}, model_portfolio_id=model.id)
    m3 = Mandate(id=uuid.uuid4(), firm_id=firm.id, person_id=cy.id, name="Cy Cash", mandate_type="advisory",
                 suitability={}, constraints={})
    s.add_all([m1, m2, m3])
    await s.flush()
    a1 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m1.id, name="Ann Brokerage", account_number="AB-1",
                 custodian="Pershing", currency="USD", cash_balance=10000, registration_type="individual")
    a2 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m2.id, name="Trust Custody", custodian="Pershing",
                 currency="USD", cash_balance=500, registration_type="trust")
    a3 = Account(id=uuid.uuid4(), firm_id=firm.id, mandate_id=m3.id, name="Cy Cash", custodian="FNZ", currency="USD",
                 cash_balance=25000)
    s.add_all([a1, a2, a3])
    await s.flush()

    async def hold(acct, i, qty, mv, cost, lots, conf=1.0):
        h = Holding(id=uuid.uuid4(), firm_id=firm.id, account_id=acct.id, instrument_id=i.id, quantity=qty, market_value=mv,
                    cost_basis=cost, confidence=conf)
        s.add(h)
        await s.flush()
        for q, cpu, days in lots:
            s.add(TaxLot(firm_id=firm.id, holding_id=h.id, quantity=q, cost_per_unit=cpu, acquired_on=today - timedelta(days=days)))
        return h
    await hold(a1, aaa, 150, 30000, 22900, [(100, 140.0, 700), (50, 178.0, 10)])
    await hold(a1, bbb, 60, 6000, 7200, [(60, 120.0, 400)], conf=0.9)
    await hold(a1, ccc, 20, 2000, 2050, [(20, 102.5, 300)])
    await hold(a2, ccc, 100, 10000, 9800, [(100, 98.0, 500)])
    await hold(a2, aaa, 10, 2000, 1500, [(10, 150.0, 900)])
    s.add_all([
        Transaction(firm_id=firm.id, account_id=a1.id, instrument_id=aaa.id, txn_type="buy", quantity=50, price=178,
                    amount=-8900, trade_date=today - timedelta(days=10)),
        Transaction(firm_id=firm.id, account_id=a2.id, instrument_id=bbb.id, txn_type="sell", quantity=5, price=95,
                    amount=475, trade_date=today - timedelta(days=4), lineage={"realised_gain": -60.0}),
        Transaction(firm_id=firm.id, account_id=a3.id, instrument_id=None, txn_type="deposit", amount=25000,
                    trade_date=today - timedelta(days=60)),
    ])

    owner_user = User(id=uuid.uuid4(), firm_id=firm.id, email="sam@golden.test", full_name="Sam", role=UserRole.ADVISER,
                      hashed_password="x")
    s.add(owner_user)
    await s.flush()
    now = utcnow()
    pros = CrmContact(id=uuid.uuid4(), firm_id=firm.id, full_name="Priya Prospect", email="priya@example.com",
                      contact_type="prospect", source="referral", owner_user_id=owner_user.id, is_active=True)
    s.add(pros)
    await s.flush()
    deal = CrmOpportunity(id=uuid.uuid4(), firm_id=firm.id, contact_id=pros.id, title="Priya - rollover", stage="proposal",
                          estimated_aum=750000, probability_pct=60, opened_at=now - timedelta(days=30))
    s.add(deal)
    await s.flush()
    s.add_all([CrmActivity(firm_id=firm.id, contact_id=pros.id, opportunity_id=deal.id, activity_type="meeting",
                           occurred_at=now - timedelta(days=3), detail="Discovery meeting", logged_by_user_id=owner_user.id),
               CrmActivity(firm_id=firm.id, contact_id=pros.id, activity_type="call", occurred_at=now - timedelta(days=20),
                           detail="Intro call")])
    await s.flush()
    return {"model": model, "user": owner_user}


async def copy_model(s, src: Firm, dst: Firm) -> None:
    """The model portfolio is the portfolio team's record here, not the Foundation's: the fresh firm has its own,
    by the same name, naming its own instruments."""
    model = (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == src.id))).scalars().one()
    mine = ModelPortfolio(id=uuid.uuid4(), firm_id=dst.id, name=model.name, drift_band=model.drift_band)
    s.add(mine)
    await s.flush()
    for t in (await s.execute(select(TargetAllocation).where(TargetAllocation.model_id == model.id))).scalars().all():
        sym = (await s.get(Instrument, t.instrument_id)).symbol if t.instrument_id else None
        local = (await s.execute(select(Instrument.id).where(Instrument.firm_id == dst.id, Instrument.symbol == sym))).scalar_one_or_none()
        s.add(TargetAllocation(firm_id=dst.id, model_id=mine.id, asset_class=t.asset_class, instrument_id=local,
                               target_weight=t.target_weight))
    await s.flush()


# Ids that are each firm's own, and the order a list happens to come back in, are not the book.
LOCAL_IDS = {"lot_id", "household_id", "holding_id", "account_id", "instrument_id", "mandate_id", "id"}


def canon(v):
    if isinstance(v, dict):
        return {k: canon(x) for k, x in v.items() if k not in LOCAL_IDS}
    if isinstance(v, (list, tuple)):
        items = [canon(x) for x in v]
        return sorted(items, key=repr) if all(isinstance(x, dict) for x in items) else items
    return v


# ── What is compared ────────────────────────────────────────────────────────────────────────────

async def brain_view(s, firm: Firm) -> list:
    models = {m.id: m.name for m in (await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == firm.id))).scalars().all()}
    out = []
    for hh in sorted((await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all(), key=lambda h: h.name):
        b = await household_brain(s, hh.id, firm_id=firm.id)
        out.append({
            "household": (b["household"]["name"], str(b["household"]["segment"]), b["household"]["values"], b["household"]["adviser"]),
            "persons": sorted((p["full_name"], p["preferred_name"], str(p["segment"]), p["is_next_gen"], p["kyc"], p["profile"],
                               p["date_of_birth"], p["deceased_on"]) for p in b["persons"]),
            "entities": sorted((e["name"], str(e["entity_type"]), e["impact_objectives"]) for e in b["entities"]),
            "mandates": sorted((m["name"], str(m["mandate_type"]), m["suitability"], m["constraints"],
                                models.get(uuid.UUID(m["model_portfolio_id"])) if m["model_portfolio_id"] else None,
                                bool(m["person_id"]), bool(m["entity_id"])) for m in b["mandates"]),
            "accounts": sorted(({k: v for k, v in a.items() if k not in ("id", "mandate_id")} for a in b["accounts"]),
                               key=lambda a: a["name"]),
            "goals": b["goals"], "relationships": b["relationships"], "totals": b["totals"], "currency": b["currency"],
        })
    return out


async def valuations(s, firm: Firm) -> dict:
    return {a.name: await account_valuation(s, a) for a in
            (await s.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()}


async def proposals(s, firm: Firm) -> dict:
    out = {}
    for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all():
        accounts = {str(a.id): a.name for a in (await s.execute(select(Account).where(Account.mandate_id == m.id))).scalars().all()}
        ctx = AgentContext(session=s, firm=firm, run=None, tier=AutonomyTier.TIER_2, guardrails={}, config={},
                           subject=Subject("mandate", m.id, m.name))
        agent = drift.DriftRebalancingAgent()
        sensed = await agent.sense(ctx)
        drafts = await agent.think(ctx, sensed)
        out[m.name] = {
            "applicable": sensed.get("applicable"), "reason": sensed.get("reason"),
            "orders": sorted((o["side"], o["symbol"], round(o["quantity"], 4), round(o["est_value"], 2),
                              round(o.get("est_realised_gain") or 0, 2), accounts.get(o["account_id"])) for d in drafts
                             for o in d.payload["order_set"]),
            "gain": [d.payload["estimated_realised_gain"] for d in drafts],
            "held": [[(h.get("symbol"), h.get("side")) for h in d.payload["wash_sale"]["held"]] for d in drafts],
        }
    return out


async def guards(s, firm: Firm) -> dict:
    symbols = {str(i.id): i.symbol for i in (await s.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all()}
    out = {}
    for m in (await s.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all():
        g = await wash_sale.guard(s, firm, m)
        out[m.name] = {"applies": g["applies"], "not_assessed": g.get("not_assessed"),
                       "no_loss_sale": {symbols[k]: v for k, v in g["no_loss_sale"].items()},
                       "no_buy": {symbols[k]: v for k, v in g["no_buy"].items()}}
    return out


async def tax_reports(s, firm: Firm) -> dict:
    out = {}
    for hh in (await s.execute(select(Household).where(Household.firm_id == firm.id))).scalars().all():
        r = await tax_intelligence.for_household(s, hh.id, firm.jurisdiction, firm_id=firm.id)
        out[hh.name] = {k: v for k, v in r.items() if k not in ("household_id",)}
    return out


async def funnel(s, firm: Firm) -> dict:
    o = await pipeline.overview(s, firm, None)
    return {"stages": o["stages"], "open": {k: o["open"][k] for k in ("count", "estimated_aum")},
            "rows": sorted((r["contact"], r["stage"], r.get("estimated_aum")) for r in o["opportunities"])}


async def counts(s, firm: Firm) -> dict:
    out = {}
    for model in (Household, Person, LegalEntity, Mandate, Account, Instrument, Price, Holding, TaxLot, Transaction,
                  CrmContact, CrmOpportunity, CrmActivity):
        out[model.__tablename__] = (await s.execute(select(func.count()).select_from(model).where(model.firm_id == firm.id))).scalar_one()
    return out


class NoLots(Adapter):
    """A wealth platform that keeps no tax lots, as the client's might (the design's open question)."""
    key = "wealth.nolots"
    name = "Wealth platform without lots"
    domains = frozenset({"parties", "accounts", "positions", "activity"})
    capabilities = {"tax_lots": False, "transactions_days": 14, "events": False}


async def main() -> int:
    async def _no_research(*a, **k):
        return []

    async def _fallback(ctx, *, fallback, **k):
        return fallback

    drift.knowledge.retrieve = _no_research
    drift.narrate = _fallback

    db = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with db.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(db, expire_on_commit=False)() as s:
            src = Firm(id=uuid.uuid4(), name="Golden", slug="golden", base_currency="USD", jurisdiction="US", settings={})
            dst = Firm(id=uuid.uuid4(), name="Copy", slug="copy", base_currency="USD", jurisdiction="US", settings={})
            s.add_all([src, dst])
            await s.flush()
            today = firm_time.today(src)
            await golden(s, src, today)

            print("\n=== A firm nobody configured is native in every domain ===")
            check("every domain is native", owners.owners(src), {d: "native" for d in owners.DOMAINS})
            check("nothing is lacking", [owners.lacks(src, k) for k in owners.CAPABILITY_DOMAIN], [None, None])
            check("Astra writes its own CRM", owners.write_refusal(src, "crm", "add the contact"), None)
            check("the agent policy reads exactly as before", policy.policy(src), policy.DEFAULTS)
            sourced = {"source_system", "external_id", "as_of"}
            check("each Foundation-owned table carries where a row came from",
                  [t for t in ("household", "person", "legal_entity", "mandate", "account", "holding", "tax_lot", "txn",
                               "crm_contact", "crm_opportunity", "crm_activity")
                   if not sourced <= set(Base.metadata.tables[t].columns.keys())], [])
            check("and a row Astra made itself says nothing about it",
                  (await s.execute(select(func.count()).select_from(Account).where(Account.source_system.is_not(None)))).scalar_one(), 0)
            from app.db_bootstrap import FOUNDATION_SOURCED_TABLES
            check("the bootstrap adds the three columns to the same tables", len(FOUNDATION_SOURCED_TABLES), 11)
            crm_conn = Connector(firm_id=src.id, domain=ConnectorDomain.CRM, provider_key="crm.dynamics",
                                 display_name="Dynamics 365", status=ConnectorStatus.MOCK, use_mock=True, config={})
            s.add(crm_conn)
            await s.flush()
            before = await counts(s, src)
            sync = await run_sync(s, crm_conn)
            check("its CRM connector's sync is the no-op it was", (sync.status, sync.records_ingested, sync.detail.get("mode"),
                                                                   "foundation" in sync.detail), ("success", 0, "mock", False))
            check("and brings in no record", await counts(s, src), before)

            print("\n=== Native out, into contract v1 ===")
            native = get_adapter("native")
            records: list[c.Record] = []
            for d in c.DOMAIN_ORDER:
                records += await native.export(s, src, d)
            by = {}
            for r in records:
                by[r.entity] = by.get(r.entity, 0) + 1
            check("every entity of the golden set is exported", by,
                  {"household": 2, "person": 3, "legal_entity": 1, "mandate": 3, "account": 3, "instrument": 3, "price": 6,
                   "holding": 5, "tax_lot": 6, "txn": 3, "crm_contact": 1, "crm_opportunity": 1, "crm_activity": 2})
            check("each record says its source, id, version and when it was read",
                  all(r.source == "native" and r.external_id and r.contract_version == c.CONTRACT_VERSION for r in records)
                  and all(r.as_of is not None for r in records if r.entity not in ("instrument", "price")), True)
            ann = next(r for r in records if r.entity == "person" and r.full_name == "Ann Golden")
            check("a reference is the other record's id at the source", ann.household_ref,
                  next(r.external_id for r in records if r.entity == "household" and r.name == "Golden Family"))
            check("a record round-trips through JSON", c.Person.model_validate_json(ann.model_dump_json()) == ann, True)

            print("\n=== Back in, into a fresh firm ===")
            first = await import_records(s, dst, [r for r in records if r.domain == "prices"])
            await copy_model(s, src, dst)
            report = await import_records(s, dst, records)
            check("everything is created once, nothing breaks, nothing is skipped",
                  (report["created"], report["breaks"], report["skipped"]), (len(records) - first["created"], 0, []))
            check("the copy holds what the source holds", await counts(s, dst), await counts(s, src))
            check("a synced row says where it came from",
                  {(a.source_system, a.external_id is not None, a.as_of is not None)
                   for a in (await s.execute(select(Account).where(Account.firm_id == dst.id))).scalars().all()},
                  {("native", True, True)})

            print("\n=== The same brain, valuation, rebalance and rules ===")
            check("the household brain", canon(await brain_view(s, dst)), canon(await brain_view(s, src)))
            check("each account's valuation", canon(await valuations(s, dst)), canon(await valuations(s, src)))
            want = await proposals(s, src)
            check("the rebalancer proposes on the golden set (or the test proves nothing)",
                  sum(len(p["orders"]) for p in want.values()) > 0, True)
            check("the rebalancer's proposed orders", await proposals(s, dst), want)
            check("the wash-sale guard (a rule module)", await guards(s, dst), await guards(s, src))
            check("  which binds, with a lot and a loss sale in the window",
                  sorted(k for g in (await guards(s, src)).values() for k in (*g["no_loss_sale"], *g["no_buy"])), ["AAA", "AAA", "BBB", "BBB"])
            ts, td = await tax_reports(s, src), await tax_reports(s, dst)
            check("the US tax report", canon(td), canon(ts))
            check("the pipeline reads the same", await funnel(s, dst), await funnel(s, src))

            print("\n=== Idempotent, and the ids stay ===")
            ids = {(r.entity_type, r.external_id): r.local_id for r in
                   (await s.execute(select(ExternalRef).where(ExternalRef.firm_id == dst.id))).scalars().all()}
            again = await import_records(s, dst, records)
            check("a second import changes nothing", (again["created"], again["updated"], again["breaks"]), (0, 0, 0))
            check("  and holds the same rows", await counts(s, dst), await counts(s, src))
            check("  under the same Astra ids", {(r.entity_type, r.external_id): r.local_id for r in
                                                 (await s.execute(select(ExternalRef).where(ExternalRef.firm_id == dst.id))).scalars().all()}, ids)

            print("\n=== Breaks are reported, never overwritten ===")
            acct_rec = next(r for r in records if r.entity == "account" and r.name == "Ann Brokerage")
            local = await s.get(Account, ids[("account", acct_rec.external_id)])
            local.cash_balance = 12345
            await s.flush()
            moved = acct_rec.model_copy(update={"cash_balance": 9000.0})
            rep = await import_records(s, dst, [moved])
            await s.refresh(local)
            brk = (await s.execute(select(FoundationBreak).where(FoundationBreak.firm_id == dst.id,
                                                                 FoundationBreak.status == "open"))).scalars().all()
            check("changed here and at the source: a break", (rep["breaks"], len(brk)), (1, 1))
            check("  with each side's value and what was synced",
                  [(f["field"], f["here"], f["source"], f["synced"]) for f in brk[0].fields],
                  [("cash_balance", 12345.0, 9000.0, 10000.0)])
            check("  and the copy keeps what it had", float(local.cash_balance), 12345.0)
            local.cash_balance = 9000
            await s.flush()
            rep = await import_records(s, dst, [moved])
            await s.refresh(brk[0])
            check("once they agree the break clears", (rep["cleared"], brk[0].status), (1, "cleared"))
            moved2 = moved.model_copy(update={"cash_balance": 7000.0, "custodian": "BNY Pershing"})
            rep = await import_records(s, dst, [moved2])
            await s.refresh(local)
            check("untouched here, the copy follows its source", (rep["updated"], float(local.cash_balance), local.custodian),
                  (1, 7000.0, "BNY Pershing"))
            gone = [r for r in records if r.domain == "activity" and r.lineage.get("realised_gain") is None]
            n = await mark_missing(s, dst, "native", "activity", gone)
            txn_left = (await s.execute(select(func.count()).select_from(Transaction).where(Transaction.firm_id == dst.id))).scalar_one()
            check("a record the source no longer has is a break, and is kept", (n, txn_left), (1, 3))

            print("\n=== An owner that cannot supply tax lots ===")
            register(NoLots())
            check("an adapter declares its capabilities", get_adapter("wealth.nolots").capabilities["tax_lots"], False)
            dst.settings = owners.with_owner(owners.with_owner(dst.settings, "positions", "wealth.nolots"), "activity", "wealth.nolots")
            await s.flush()
            gap = owners.lacks(dst, "tax_lots")
            check("the firm lacks them, and says whose", bool(gap and "not assessed" in gap.lower() and "Wealth platform" in gap), True)
            hh = (await s.execute(select(Household).where(Household.firm_id == dst.id, Household.name == "Golden Family"))).scalar_one()
            tr = await tax_intelligence.for_household(s, hh.id, dst.jurisdiction, firm_id=dst.id)
            check("the tax report reads not assessed, with no figure", (bool(tr.get("not_assessed")), tr["summary"]["harvestable_loss"]),
                  (True, None))
            book = await tax_intelligence.for_firm(s, dst.id, dst.jurisdiction)
            check("the firm's tax book counts it as not assessed", book["jurisdictions"]["not_assessed"], 2)
            m = (await s.execute(select(Mandate).where(Mandate.firm_id == dst.id, Mandate.name == "Ann Growth"))).scalar_one()
            g = await wash_sale.guard(s, dst, m)
            check("the wash-sale guard names both gaps (no lots, 14 days of trades)",
                  [("lots" in x, "14 days" in x) for x in g.get("not_assessed") or []], [(True, False), (False, True)])
            p = (await proposals(s, dst))["Ann Growth"]
            check("the rebalancer proposes nothing", (p["applicable"], p["orders"]), (False, []))
            check("  and says why", "not assessed" in (p["reason"] or "").lower(), True)
            check("the native firm is unaffected", (await proposals(s, src))["Ann Growth"], want["Ann Growth"])
            check("  nor its tax report", await tax_reports(s, src), ts)
            dst.settings = owners.with_owner(owners.with_owner(dst.settings, "positions", "native"), "activity", "native")
            check("set back to native, every domain reads native again", owners.owners(dst), {d: "native" for d in owners.DOMAINS})
            check("  and the agent policy is untouched", policy.policy(dst), policy.DEFAULTS)
            ADAPTERS.pop("wealth.nolots", None)
    finally:
        await db.dispose()

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
