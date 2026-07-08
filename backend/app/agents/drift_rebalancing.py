"""Drift & Tax-Managed Rebalancing — the lighthouse agent (spec §7.2, Table 10, §14).

Continuously monitors holdings vs the target model, runs whole-portfolio, tax-aware
optimisation, drafts a multi-custodian order set with a plain-language rationale grounded in
the firm's house views, and routes it to the adviser for approve / modify / dismiss at
Tier 2. On approval it routes the draft order set to the (mock) OMS — no live execution."""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import knowledge
from app.aurea_core.rebalancing import Lot, Position, optimise
from app.aurea_core.valuation import latest_prices
from app.models.enums import AgentKey, AutonomyTier
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, Instrument, ModelPortfolio, TargetAllocation, TaxLot


class DriftRebalancingAgent(BaseAgent):
    key = AgentKey.DRIFT_REBALANCING
    name = "Drift & Tax-Managed Rebalancing"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        mandate = await s.get(Mandate, ctx.subject.id) if ctx.subject.id else None
        if mandate is None or mandate.model_portfolio_id is None:
            return {"applicable": False, "reason": "No mandate or target model."}

        model = await s.get(ModelPortfolio, mandate.model_portfolio_id)
        targets = (
            await s.execute(
                select(TargetAllocation).where(TargetAllocation.model_id == model.id)
            )
        ).scalars().all()
        target_weights: dict[str, float] = {}
        for t in targets:
            target_weights[t.asset_class] = target_weights.get(t.asset_class, 0.0) + float(t.target_weight)

        accounts = (
            await s.execute(select(Account).where(Account.mandate_id == mandate.id))
        ).scalars().all()
        prices = await latest_prices(s, ctx.firm.id)

        # Adviser revision overrides (from a "Revise with comments" request) — applied for this
        # run only, never mutating the mandate. `protect` = do-not-sell holdings.
        ov = (ctx.config or {}).get("overrides") or {}
        exclusions = set((mandate.suitability or {}).get("values_exclusions", [])) | set(ov.get("exclude") or [])
        protect = {s.upper() for s in (ov.get("protect") or [])}
        positions: list[dict] = []
        cash = 0.0
        for acc in accounts:
            cash += float(acc.cash_balance or 0)
            holdings = (
                await s.execute(select(Holding).where(Holding.account_id == acc.id))
            ).scalars().all()
            # Eager-load tax lots for all these holdings (async — no lazy loading).
            holding_ids = [h.id for h in holdings]
            lots_by_holding: dict = {}
            if holding_ids:
                for lt in (
                    await s.execute(select(TaxLot).where(TaxLot.holding_id.in_(holding_ids)))
                ).scalars().all():
                    lots_by_holding.setdefault(lt.holding_id, []).append(lt)
            for h in holdings:
                inst = await s.get(Instrument, h.instrument_id)
                price = prices.get(h.instrument_id)
                px = float(price.close) if price else (float(h.market_value) / float(h.quantity) if h.quantity else 0)
                tags = (inst.values_tags or {}) if inst else {}
                excluded = bool(tags.get("sin")) or any(t in exclusions for t in tags.get("flags", [])) \
                    or (inst.symbol in exclusions if inst else False)
                positions.append({
                    "holding_id": str(h.id), "instrument_id": str(h.instrument_id),
                    "symbol": inst.symbol if inst else "?", "name": inst.name if inst else "",
                    "asset_class": inst.asset_class if inst else "multi_asset",
                    "market_value": float(h.market_value or 0), "price": px,
                    "cost_basis": float(h.cost_basis or 0),
                    "account_id": str(acc.id), "custodian": acc.custodian or "—",
                    "excluded": excluded, "protected": bool(inst and inst.symbol in protect),
                    "confidence": float(h.confidence or 1.0),
                    "lots": [{"quantity": float(lt.quantity), "cost_per_unit": float(lt.cost_per_unit)}
                             for lt in lots_by_holding.get(h.id, [])],
                })

        from app.core import foundation
        pol = await foundation.for_agent(ctx.session, ctx.firm, self.key)
        drift_band = float(ov["drift_band"]) if ov.get("drift_band") is not None else float(model.drift_band)
        cgt_budget = ov["cgt_budget"] if ov.get("cgt_budget") is not None else (mandate.constraints or {}).get("cgt_budget")
        if cgt_budget is None:  # firm-wide default guardrail when the mandate sets none
            cgt_budget = pol.get("default_cgt_budget")
        return {
            "applicable": True,
            "mandate": {"id": str(mandate.id), "name": mandate.name, "type": mandate.mandate_type},
            "model": {"id": str(model.id), "name": model.name, "drift_band": drift_band},
            "target_weights": target_weights,
            "cgt_budget": cgt_budget,
            "positions": positions,
            "cash": cash,
            "revision_note": ov.get("note"),
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        positions = [
            Position(
                holding_id=p["holding_id"], instrument_id=p["instrument_id"], symbol=p["symbol"],
                name=p["name"], asset_class=p["asset_class"], market_value=p["market_value"],
                price=p["price"], cost_basis=p["cost_basis"], account_id=p["account_id"],
                custodian=p["custodian"], excluded=p["excluded"], protected=p.get("protected", False),
                lots=[Lot(l["quantity"], l["cost_per_unit"]) for l in p["lots"]],
            )
            for p in sensed["positions"]
        ]
        # One representative model instrument per class (first non-excluded position seen).
        model_instruments = {}
        for p in positions:
            if not p.excluded:
                model_instruments.setdefault(p.asset_class, p)

        result = optimise(
            positions=positions,
            target_weights=sensed["target_weights"],
            cash=sensed["cash"],
            drift_band=sensed["model"]["drift_band"],
            cgt_budget=sensed.get("cgt_budget"),
            model_instruments=model_instruments,
        )
        if not result.needs_rebalance:
            return []

        # Data confidence across positions.
        confs = [p["confidence"] for p in sensed["positions"]] or [1.0]
        data_confidence = sum(confs) / len(confs)

        # Ground the rationale in the firm's house views (retrieval depth is policy-configurable).
        from app.core import foundation
        rag_k = int((await foundation.for_agent(ctx.session, ctx.firm, self.key)).get("rag_top_k", 3))
        citations = await knowledge.retrieve(
            ctx.session, ctx.firm.id,
            query=f"rebalancing drift asset allocation {' '.join(sensed['target_weights'])}",
            k=rag_k,
        )

        orders = [_order_dict(o) for o in result.orders]
        sells = [o for o in orders if o["side"] == "sell"]
        buys = [o for o in orders if o["side"] == "buy"]

        payload = {
            "order_set": orders,
            "mandate": {"name": sensed["mandate"]["name"], "type": sensed["mandate"]["type"]},
            "summary": {
                "total_value": result.total_value,
                "max_drift": result.max_drift,
                "drift_band": result.drift_band,
                "turnover": result.turnover,
                "turnover_pct": result.turnover_pct,
                "n_sells": len(sells),
                "n_buys": len(buys),
            },
            "current_weights": result.current_weights,
            "target_weights": result.target_weights,
            "drifts": result.drifts,
            "estimated_realised_gain": result.estimated_realised_gain,
            "harvested_losses": result.harvested_losses,
            "cgt_budget": sensed.get("cgt_budget"),
        }
        if sensed.get("revision_note"):
            payload["revision_note"] = sensed["revision_note"]
        evidence = {
            "data_confidence": round(data_confidence, 3),
            "cgt_budget": sensed.get("cgt_budget"),
            "guardrail_breaches": result.guardrail_breaches,
            "limitations": result.limitations,
            "price_source": "Conduit market-data feed (latest close)",
            "n_positions": len(positions),
        }

        rationale = await self._rationale(ctx, sensed, result, citations)
        confidence = round(min(data_confidence, 0.95) * (0.6 if result.guardrail_breaches else 1.0), 3)

        title = (
            f"Rebalance {sensed['mandate']['name']}: max drift "
            f"{result.max_drift:.1%} exceeds {result.drift_band:.0%} band"
        )
        summary = (
            f"{len(sells)} sell / {len(buys)} buy orders, turnover "
            f"${result.turnover:,.0f} ({result.turnover_pct:.1%}). Estimated realised gain "
            f"${result.estimated_realised_gain:,.0f}"
            + (f", harvesting ${result.harvested_losses:,.0f} of losses" if result.harvested_losses else "")
            + "."
        )

        return [RecommendationDraft(
            title=title, summary=summary, rationale=rationale, confidence=confidence, priority=2,
            subject=Subject("mandate", ctx.subject.id, sensed["mandate"]["name"]),
            payload=payload, evidence=evidence, citations=citations,
        )]

    async def _rationale(self, ctx, sensed, result, citations) -> str:
        drift_lines = "\n".join(
            f"  - {c}: now {result.current_weights.get(c,0):.1%} vs target {result.target_weights.get(c,0):.1%} "
            f"(drift {result.drifts.get(c,0):+.1%})"
            for c in result.target_weights
        )
        order_lines = "\n".join(
            f"  - {o.side.upper()} {o.quantity:g} {o.symbol} (~${o.est_value:,.0f}; "
            f"gain ${o.est_realised_gain:,.0f}; {o.reason})"
            for o in result.orders
        )
        research = "\n".join(f"  - [{c['title']}] {c['excerpt'][:200]}" for c in citations) or "  (none)"
        fallback = (
            f"The {sensed['mandate']['name']} mandate has drifted beyond its "
            f"{result.drift_band:.0%} tolerance (max drift {result.max_drift:.1%}). The proposed "
            f"order set realigns the portfolio to target while managing tax: estimated realised "
            f"gain ${result.estimated_realised_gain:,.0f}"
            + (f", with ${result.harvested_losses:,.0f} of losses harvested" if result.harvested_losses else "")
            + f", on ${result.turnover:,.0f} turnover. Tax lots were selected losses-first to "
            "minimise the tax cost. The adviser should review and approve, modify or dismiss."
        )
        revision = ""
        if sensed.get("revision_note"):
            revision = (f"\n\nThis is a REVISED proposal. The adviser asked: \"{sensed['revision_note']}\". "
                        "Open the rationale by acknowledging how this revision reflects that request.")
        prompt = (
            "Draft a concise, plain-language rationale (2 short paragraphs) for the following "
            "rebalancing recommendation, for an adviser to review. Explain WHY (drift vs target) "
            "and HOW tax was managed (lot selection, loss harvesting, CGT budget). Ground it in "
            "the firm research excerpts where relevant and reference them by title."
            + revision + "\n\n"
            f"Mandate: {sensed['mandate']['name']} ({sensed['mandate']['type']})\n"
            f"Allocation drift:\n{drift_lines}\n\n"
            f"Proposed orders:\n{order_lines}\n\n"
            f"Estimated realised gain: ${result.estimated_realised_gain:,.0f}; "
            f"harvested losses: ${result.harvested_losses:,.0f}; "
            f"CGT budget: {sensed.get('cgt_budget')}\n\n"
            f"Firm research excerpts:\n{research}\n"
        )
        return await narrate(ctx, task="advice", system=firm_voice(ctx), prompt=prompt, fallback=fallback)

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Route the (possibly modified) order set to the mock OMS connector."""
        payload = recommendation.modified_payload or recommendation.payload or {}
        orders = payload.get("order_set", [])
        return {
            "executed": True,
            "venue": "mock-oms",
            "orders_routed": len(orders),
            "note": "Draft order set routed to the mock OMS for staging. No live market execution.",
        }

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        payload = recommendation.modified_payload or recommendation.payload or {}
        n = len(payload.get("order_set", []))
        return {"reversed": True, "orders_recalled": n,
                "note": f"{n} staged order(s) recalled from the mock OMS before execution."}


def _order_dict(o) -> dict:
    return {
        "side": o.side, "symbol": o.symbol, "name": o.name, "instrument_id": o.instrument_id,
        "asset_class": o.asset_class, "quantity": o.quantity, "est_price": o.est_price,
        "est_value": o.est_value, "account_id": o.account_id, "custodian": o.custodian,
        "est_realised_gain": o.est_realised_gain, "reason": o.reason,
    }
