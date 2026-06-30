"""Layer 2.2 — Portfolio & investment analytics (Analytics Companion §2.2)."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import planning
from app.aurea_core.analytics._common import gather_brains, goal_projections_all, positions_of
from app.models.graph import Account, Mandate
from app.models.portfolio import Holding, Instrument, ModelPortfolio, Price, TargetAllocation


async def _price_window(session: AsyncSession, firm_id, instruments: dict) -> dict:
    """Earliest vs latest close per instrument from stored price history."""
    by_id_symbol = {i.id: sym for sym, i in instruments.items()}
    prices = (
        await session.execute(select(Price).where(Price.firm_id == firm_id).order_by(Price.as_of.asc()))
    ).scalars().all()
    grouped: dict = {}
    for p in prices:
        grouped.setdefault(p.instrument_id, []).append(p)
    out: dict = {}
    all_start, all_end = None, None
    for inst_id, rows in grouped.items():
        sym = by_id_symbol.get(inst_id)
        if not sym or len(rows) < 2:
            continue
        out[sym] = {"start": float(rows[0].close), "end": float(rows[-1].close),
                    "start_date": rows[0].as_of.isoformat(), "end_date": rows[-1].as_of.isoformat()}
        all_start = rows[0].as_of if (all_start is None or rows[0].as_of < all_start) else all_start
        all_end = rows[-1].as_of if (all_end is None or rows[-1].as_of > all_end) else all_end
    if all_start and all_end:
        out["__period__"] = f"{all_start.strftime('%b %Y')} – {all_end.strftime('%b %Y')}"
    return out


async def _drift_summary(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Lightweight drift across every mandate managed to a model (Companion: 'at book scale')."""
    mandates = (
        await session.execute(
            select(Mandate).where(Mandate.firm_id == firm_id, Mandate.model_portfolio_id.isnot(None))
        )
    ).scalars().all()
    instruments = {
        i.id: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()
    }
    rows = []
    breached = 0
    for m in mandates:
        model = await session.get(ModelPortfolio, m.model_portfolio_id)
        targets = (
            await session.execute(select(TargetAllocation).where(TargetAllocation.model_id == model.id))
        ).scalars().all()
        target_w: dict[str, float] = {}
        for t in targets:
            target_w[t.asset_class] = target_w.get(t.asset_class, 0.0) + float(t.target_weight)
        accounts = (await session.execute(select(Account).where(Account.mandate_id == m.id))).scalars().all()
        by_class: dict[str, float] = {}
        total = 0.0
        for acc in accounts:
            total += float(acc.cash_balance or 0)
            by_class["cash"] = by_class.get("cash", 0.0) + float(acc.cash_balance or 0)
            holds = (await session.execute(select(Holding).where(Holding.account_id == acc.id))).scalars().all()
            for h in holds:
                inst = instruments.get(h.instrument_id)
                cls = inst.asset_class if inst else "multi_asset"
                mv = float(h.market_value or 0)
                by_class[cls] = by_class.get(cls, 0.0) + mv
                total += mv
        if total <= 0:
            continue
        max_drift = max((abs(by_class.get(c, 0.0) / total - target_w.get(c, 0.0))
                         for c in set(by_class) | set(target_w) if c != "cash"), default=0.0)
        needs = max_drift > float(model.drift_band)
        if needs:
            breached += 1
        rows.append({"mandate": m.name, "max_drift": round(max_drift, 4),
                     "drift_band": float(model.drift_band), "needs_rebalance": needs})
    return {"mandates_monitored": len(rows), "mandates_breached": breached,
            "by_mandate": sorted(rows, key=lambda r: -r["max_drift"])}


async def compute(session: AsyncSession, firm_id: uuid.UUID, brains: list[dict] | None = None) -> dict:
    brains = brains if brains is not None else await gather_brains(session, firm_id)
    instruments = {
        i.symbol: i for i in (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()
    }

    # Real price history → start-of-period vs latest close per instrument (trailing window).
    price_window = await _price_window(session, firm_id, instruments)
    period = price_window.get("__period__")

    total_mv = total_cost = 0.0
    start_mv = 0.0  # value of CURRENT holdings at the start of the window (real history)
    class_start: dict[str, float] = {}
    class_end: dict[str, float] = {}
    class_gain: dict[str, float] = {}
    class_cost: dict[str, float] = {}
    by_class_mv: dict[str, float] = {}
    harvestable = 0.0
    unrealised_gains = 0.0
    alt_value = alt_illiquid = 0.0
    esg_equity = esg_excluded = 0.0

    for b in brains:
        excl = set((b["household"].get("values") or {}).get("exclusions", []))
        for p in positions_of(b):
            mv, cost = p["market_value"], p["cost_basis"]
            cls = p["asset_class"]
            total_mv += mv
            total_cost += cost
            by_class_mv[cls] = by_class_mv.get(cls, 0.0) + mv
            class_gain[cls] = class_gain.get(cls, 0.0) + (mv - cost)
            class_cost[cls] = class_cost.get(cls, 0.0) + cost
            # Period start value of this position from real history (fallback: cost basis).
            win = price_window.get(p["instrument"])
            start_px = win["start"] if win else None
            sv = (p["quantity"] * start_px) if (start_px and p.get("quantity")) else cost
            start_mv += sv
            class_start[cls] = class_start.get(cls, 0.0) + sv
            class_end[cls] = class_end.get(cls, 0.0) + mv
            gain = mv - cost
            if gain < 0:
                harvestable += -gain
            else:
                unrealised_gains += gain
            inst = instruments.get(p["instrument"])
            tags = (inst.values_tags or {}) if inst else {}
            if cls == "alternatives" or (inst and inst.market_type == "private"):
                alt_value += mv
                if (inst.private_attributes or {}).get("liquidity") == "illiquid" if inst else False:
                    alt_illiquid += mv
            if cls == "equity":
                esg_equity += mv
                excluded = bool(tags.get("sin")) or any(t in excl for t in tags.get("flags", [])) or (inst.symbol in excl if inst else False)
                if excluded:
                    esg_excluded += mv

    allocation = {k: round(v, 2) for k, v in by_class_mv.items()}
    risk = planning.portfolio_risk(allocation, total_mv)
    stress = planning.stress_test(allocation)

    # Performance & attribution — time-weighted over the price window on current holdings.
    total_return = round((total_mv - start_mv) / start_mv, 4) if start_mv else 0.0
    attribution = [
        {"asset_class": c,
         "contribution": round((class_end.get(c, 0) - class_start.get(c, 0)) / start_mv, 4) if start_mv else 0.0,
         "class_return": round((class_end.get(c, 0) - class_start.get(c, 0)) / class_start[c], 4) if class_start.get(c) else 0.0,
         "weight": round(by_class_mv[c] / total_mv, 4) if total_mv else 0.0}
        for c in sorted(by_class_mv, key=lambda c: -(class_end.get(c, 0) - class_start.get(c, 0)))
    ]

    goals = await goal_projections_all(brains)
    on_track = sum(1 for g in goals if g["on_track"])

    return {
        "performance": {"total_return": total_return, "period": period or "current holdings",
                        "market_value": round(total_mv, 2), "start_value": round(start_mv, 2),
                        "cost_basis": round(total_cost, 2), "unrealised_gain": round(total_mv - total_cost, 2),
                        "attribution": attribution},
        "risk": {**risk, "stress_test": stress, "allocation": allocation},
        "drift": await _drift_summary(session, firm_id),
        "tax": {"harvestable_losses": round(harvestable, 2), "unrealised_gains": round(unrealised_gains, 2),
                "estimated_tax_alpha": round(harvestable * 0.28, 2)},  # at a 28% rate
        "goals": {"total": len(goals), "on_track": on_track,
                  "avg_probability": round(sum(g["probability"] for g in goals) / len(goals), 3) if goals else None,
                  "by_goal": goals},
        "esg": {"equity_value": round(esg_equity, 2), "excluded_value": round(esg_excluded, 2),
                "alignment_score": round(1 - (esg_excluded / esg_equity), 3) if esg_equity else 1.0},
        "alternatives": {"value": round(alt_value, 2),
                         "pct_of_book": round(alt_value / total_mv, 4) if total_mv else 0.0,
                         "illiquid_value": round(alt_illiquid, 2),
                         "illiquid_pct": round(alt_illiquid / alt_value, 4) if alt_value else 0.0},
    }
