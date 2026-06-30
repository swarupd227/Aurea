"""Book integration agent (spec Table 8) — deep implementation. Tier 2.

sense:  load the inbound acquired-book feed; read any capital-call notices via document
        intelligence; reconcile clients, securities and holdings against the client brain.
think:  present the mapping proposal with conflicts flagged for operations to validate.
act:    on operations approval, commit the accepted mappings into the brain as golden records
        (new households/instruments/accounts/holdings; conflicts resolved to the inbound value).
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.book_recon import _norm, reconcile
from app.aurea_core.document_intel import DOC_LABELS, extract
from app.aurea_core.graph import list_households
from app.core.db import utcnow
from app.models.enums import AgentKey, AssetClass, AutonomyTier, BookBatchStatus, MandateType
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.onboarding import BookIntegrationBatch
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TaxLot


class BookIntegrationAgent(BaseAgent):
    key = AgentKey.BOOK_INTEGRATION
    name = "Book Integration"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        batch = await s.get(BookIntegrationBatch, ctx.subject.id) if ctx.subject.id else None
        if batch is None:
            return {"applicable": False}
        feed = batch.feed or {}

        households = await list_households(s, ctx.firm.id)
        instruments = (
            await s.execute(select(Instrument).where(Instrument.firm_id == ctx.firm.id))
        ).scalars().all()
        master = {i.symbol: i.name for i in instruments}

        # Existing holdings keyed by normalised client name + symbol (for conflict detection).
        existing_by_cs: dict[str, float] = {}
        hh_objs = (
            await s.execute(select(Household).where(Household.firm_id == ctx.firm.id))
        ).scalars().all()
        inst_by_id = {i.id: i for i in instruments}
        for hh in hh_objs:
            brain_accounts = (
                await s.execute(
                    select(Account).join(Mandate, Account.mandate_id == Mandate.id).where(
                        (Mandate.person_id.in_(select(Person.id).where(Person.household_id == hh.id)))
                        | (Mandate.entity_id.in_(select(LegalEntity.id).where(LegalEntity.household_id == hh.id)))
                    )
                )
            ).scalars().all()
            for acc in brain_accounts:
                hs = (await s.execute(select(Holding).where(Holding.account_id == acc.id))).scalars().all()
                for h in hs:
                    inst = inst_by_id.get(h.instrument_id)
                    if inst:
                        key = f"{_norm(hh.name)}|{inst.symbol.lower()}"
                        existing_by_cs[key] = existing_by_cs.get(key, 0.0) + float(h.quantity)

        # Document intelligence over capital-call notices.
        capital_calls = []
        for doc in feed.get("capital_calls", []):
            res = extract("capital_call_notice", doc.get("raw_text", ""))
            capital_calls.append({"filename": doc.get("filename"), "label": DOC_LABELS["capital_call_notice"],
                                  "fields": res.fields, "confidence": res.confidence, "missing": res.missing})

        recon = reconcile(
            inbound_clients=feed.get("clients", []),
            existing_households=[{"id": h["id"], "name": h["name"]} for h in households],
            inbound_securities=feed.get("securities", []),
            master_symbols=master,
            inbound_holdings=feed.get("holdings", []),
            existing_by_client_symbol=existing_by_cs,
        )

        batch.mappings = {
            "client_mappings": recon.client_mappings,
            "security_mappings": recon.security_mappings,
            "holding_conflicts": recon.holding_conflicts,
            "capital_calls": capital_calls,
        }
        batch.stats = recon.stats
        batch.status = BookBatchStatus.RECONCILED
        await s.flush()

        return {"applicable": True, "source_firm": batch.source_firm, "stats": recon.stats,
                "mappings": batch.mappings}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        stats = sensed["stats"]
        m = sensed["mappings"]
        conflicts = m["holding_conflicts"]
        unmapped = [x for x in m["security_mappings"] if x["action"] == "create"]

        exceptions = []
        if conflicts:
            exceptions.append(f"{len(conflicts)} holding conflict(s) — quantities differ from the brain.")
        if unmapped:
            exceptions.append(f"{len(unmapped)} security(ies) not in the instrument master — will be created.")
        merges = [x for x in m["client_mappings"] if x["action"] == "merge"]
        if merges:
            exceptions.append(f"{len(merges)} client(s) matched to existing households — confirm the merge.")

        fallback = (
            f"Reconciled {stats['clients']} clients, {stats['securities']} securities and "
            f"{stats['holdings']} holdings from {sensed['source_firm']}. {stats['merges']} client(s) "
            f"matched existing households, {stats['new_clients']} are new; {stats['unmapped_securities']} "
            f"security(ies) need creating; {stats['conflicts']} holding conflict(s) flagged. Operations "
            "should validate these mappings before they become golden records."
        )
        prompt = (
            f"Summarise a book-integration reconciliation from '{sensed['source_firm']}'. Stats: {stats}. "
            f"Conflicts: {conflicts}. List what operations must validate before mappings become golden "
            "records. Be concise and precise."
        )
        rationale = await narrate(ctx, task="advice", system=firm_voice(ctx), prompt=prompt, fallback=fallback)
        confidence = round(0.9 - 0.1 * (len(conflicts) > 0) - 0.05 * (len(unmapped) > 0), 3)

        return [RecommendationDraft(
            title=f"Integrate book — {sensed['source_firm']}",
            summary=(f"{stats['clients']} clients · {stats['merges']} merges · {stats['new_clients']} new · "
                     f"{stats['conflicts']} conflict(s)."),
            rationale=rationale, confidence=confidence, priority=2,
            subject=Subject("book_batch", ctx.subject.id, sensed["source_firm"]),
            payload={**m, "stats": stats},
            evidence={"data_confidence": confidence, "conflicts": [str(c) for c in conflicts],
                      "n_unmapped": len(unmapped)},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Commit the accepted mappings as golden records."""
        s = ctx.session
        batch = await s.get(BookIntegrationBatch, ctx.subject.id)
        if batch is None:
            return {"executed": False, "note": "Batch not found."}
        if batch.status == BookBatchStatus.COMMITTED:
            return {"executed": True, "note": "Already committed.", **(batch.committed or {})}

        feed = batch.feed or {}
        mappings = recommendation.modified_payload or recommendation.payload or batch.mappings
        today = date.today()
        model = (
            await s.execute(select(ModelPortfolio).where(ModelPortfolio.firm_id == ctx.firm.id).limit(1))
        ).scalars().first()

        # 1. Ensure instruments exist for every inbound security.
        inst_by_symbol: dict[str, Instrument] = {
            i.symbol: i
            for i in (await s.execute(select(Instrument).where(Instrument.firm_id == ctx.firm.id))).scalars().all()
        }
        created_instruments = 0
        created_instrument_ids, created_household_ids, created_account_ids = [], [], []
        for sec in feed.get("securities", []):
            sym = sec["symbol"]
            if sym in inst_by_symbol:
                continue
            inst = Instrument(firm_id=ctx.firm.id, symbol=sym, name=sec.get("name", sym),
                              asset_class=AssetClass(sec.get("asset_class", "equity")),
                              currency=sec.get("currency", "USD"), market_symbol=sym)
            s.add(inst)
            await s.flush()
            s.add(Price(firm_id=ctx.firm.id, instrument_id=inst.id, as_of=today,
                        close=sec.get("last_price", 1.0), currency=sec.get("currency", "USD"),
                        source="acquired_book", is_real=False))
            inst_by_symbol[sym] = inst
            created_instruments += 1
            created_instrument_ids.append(str(inst.id))

        # 2. Resolve each inbound client to a household + account (merge or create).
        client_to_account: dict[str, Account] = {}
        new_clients = 0
        for cm in mappings.get("client_mappings", []):
            name = cm["inbound"]
            if cm["action"] == "merge" and cm.get("target_id"):
                household = await s.get(Household, cm["target_id"])
            else:
                household = Household(firm_id=ctx.firm.id, name=name, segment="mass_affluent", values={})
                s.add(household)
                await s.flush()
                created_household_ids.append(str(household.id))
                person = Person(firm_id=ctx.firm.id, household_id=household.id, full_name=name,
                                preferred_name=name.split()[0],
                                kyc={"status": "migrated", "aml_screened": False}, profile={})
                s.add(person)
                await s.flush()
                mandate = Mandate(firm_id=ctx.firm.id, person_id=person.id, name=f"{name} — Migrated",
                                  mandate_type=MandateType.ADVISORY, suitability={}, constraints={},
                                  model_portfolio_id=model.id if model else None)
                s.add(mandate)
                await s.flush()
                new_clients += 1
            # One migrated account per client.
            acc = Account(firm_id=ctx.firm.id,
                          mandate_id=(await _mandate_for_household(s, household.id)),
                          name=f"{name.split()[0]} Migrated A/C", custodian=batch.source_firm,
                          currency=ctx.firm.base_currency, cash_balance=0,
                          lineage={"source": f"book:{batch.source_firm}", "as_of": utcnow().isoformat()})
            s.add(acc)
            await s.flush()
            created_account_ids.append(str(acc.id))
            client_to_account[_norm(name)] = acc

        # 3. Create holdings (conflicts resolved to the inbound/validated quantity).
        holdings_written = 0
        for h in feed.get("holdings", []):
            acc = client_to_account.get(_norm(h["client"]))
            inst = inst_by_symbol.get(h["symbol"])
            if not acc or not inst:
                continue
            price = (await s.execute(select(Price).where(Price.instrument_id == inst.id))).scalars().first()
            px = float(price.close) if price else 1.0
            holding = Holding(firm_id=ctx.firm.id, account_id=acc.id, instrument_id=inst.id,
                              quantity=h["quantity"], market_value=h["quantity"] * px,
                              cost_basis=h.get("cost_basis", h["quantity"] * px),
                              lineage={"source": f"book:{batch.source_firm}", "as_of": today.isoformat()},
                              confidence=0.8)
            s.add(holding)
            await s.flush()
            s.add(TaxLot(firm_id=ctx.firm.id, holding_id=holding.id, quantity=h["quantity"],
                         cost_per_unit=(h.get("cost_basis", h["quantity"] * px) / h["quantity"]) if h["quantity"] else px,
                         acquired_on=today))
            holdings_written += 1

        committed = {"new_clients": new_clients, "created_instruments": created_instruments,
                     "holdings_written": holdings_written,
                     "created_household_ids": created_household_ids,
                     "created_account_ids": created_account_ids,
                     "created_instrument_ids": created_instrument_ids}
        batch.status = BookBatchStatus.COMMITTED
        batch.committed = committed
        await s.flush()
        return {"executed": True,
                "note": f"Committed {holdings_written} holdings for {len(client_to_account)} clients as golden records.",
                "new_clients": new_clients, "created_instruments": created_instruments,
                "holdings_written": holdings_written}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        """Undo a commit — delete accounts (cascade holdings), new households, and new instruments."""
        s = ctx.session
        batch = await s.get(BookIntegrationBatch, ctx.subject.id)
        if batch is None or batch.status != BookBatchStatus.COMMITTED:
            return {"reversed": False, "note": "Nothing to reverse."}
        c = batch.committed or {}
        for acc_id in c.get("created_account_ids", []):
            obj = await s.get(Account, acc_id)
            if obj:
                await s.delete(obj)
        for hh_id in c.get("created_household_ids", []):
            obj = await s.get(Household, hh_id)
            if obj:
                await s.delete(obj)
        for inst_id in c.get("created_instrument_ids", []):
            obj = await s.get(Instrument, inst_id)
            if obj:
                await s.delete(obj)
        batch.status = BookBatchStatus.RECONCILED
        batch.committed = {}
        await s.flush()
        return {"reversed": True, "note": f"Reversed integration of {batch.source_firm}; "
                "committed records removed and the batch reopened."}


async def _mandate_for_household(session, household_id):
    from app.models.graph import LegalEntity as LE, Mandate as M, Person as P
    m = (
        await session.execute(
            select(M).where(
                (M.person_id.in_(select(P.id).where(P.household_id == household_id)))
                | (M.entity_id.in_(select(LE.id).where(LE.household_id == household_id)))
            ).limit(1)
        )
    ).scalars().first()
    return m.id if m else None
