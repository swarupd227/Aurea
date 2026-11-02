"""The book, one row per household, for questions asked of all of it: who is over their equity target, who
holds a loss, whose values are not on their mandates, who has a next generation on file.

It replaces Ask-your-book, which put a snapshot of the book into a prompt and judged "overweight" against a
fixed 60%. Here equity is measured mandate by mandate against what that mandate is managed to - its IPS where
one is in force, else its model, inside the same band - so a mandate with neither is not measured. A loss is
what the book shows, not what may be harvested: the wash-sale window and India's rules are their own tools.
It reads and never writes.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import ips
from app.aurea_core.graph import household_brain, list_households
from app.aurea_core.risk_profile import model_equity
from app.models.graph import Mandate
from app.models.portfolio import Instrument, ModelPortfolio

FILTERS = ("over_equity_target", "unrealised_loss", "values_gap", "next_gen")
NOT_CHECKED = ("A loss is not checked against what may be harvested: the household's own tax rules "
               "(read_tax_position) and, for a household taxed in the US, the wash-sale window (check_household_wash_sale).")


class ScreenError(ValueError):
    """A screen that cannot be run as asked, with the reason."""


async def _equity(session: AsyncSession, brain: dict) -> list[dict]:
    """Each mandate's equity weight against what it is managed to. A mandate with no model and no IPS, or
    with nothing in it, is listed as not measured."""
    by_mandate: dict[str, dict[str, float]] = {}
    for acc in brain["accounts"]:
        mix = by_mandate.setdefault(acc.get("mandate_id") or "", {})
        for cls, v in (acc.get("by_asset_class") or {}).items():
            mix[cls] = mix.get(cls, 0.0) + float(v)
    out = []
    for m in brain["mandates"]:
        mix = by_mandate.get(m["id"], {})
        total = sum(mix.values())
        mandate = await session.get(Mandate, uuid.UUID(m["id"]))
        policy = await ips.in_force(session, mandate.id) if mandate else None
        target = band = None
        against = None
        if policy:
            target = float((policy.targets or {}).get("equity", 0.0))
            band, against = float(policy.drift_band), "ips"
        elif mandate and mandate.model_portfolio_id:
            model = await session.get(ModelPortfolio, mandate.model_portfolio_id)
            target = await model_equity(session, mandate.model_portfolio_id)
            band, against = (float(model.drift_band) if model else None), "model"
        weight = mix.get("equity", 0.0) / total if total > 0 else None
        measured = weight is not None and target is not None
        out.append({
            "mandate_id": m["id"], "mandate": m["name"],
            "equity_weight": round(weight, 4) if weight is not None else None,
            "equity_target": round(target, 4) if target is not None else None,
            "drift_band": band, "measured_against": against if measured else None,
            "over_by": round(weight - target, 4) if measured else None,
            "over_target": bool(measured and band is not None and weight - target > band),
        })
    return out


def _values(brain: dict, mandates: dict[str, Mandate], instruments: dict[str, Instrument]) -> dict:
    """The household's values exclusions that a mandate does not carry, and what is held against them."""
    wanted = [str(x).strip() for x in ((brain["household"].get("values") or {}).get("exclusions") or []) if str(x).strip()]
    missing = []
    for m in brain["mandates"]:
        mandate = mandates.get(m["id"])
        carried = set(ips.exclusions_of(mandate)) if mandate else set()
        gaps = [x for x in wanted if x not in carried]
        if gaps:
            missing.append({"mandate_id": m["id"], "mandate": m["name"], "not_carried": gaps})
    held = [{"instrument": p["instrument"], "market_value": p["market_value"]}
            for acc in brain["accounts"] for p in acc.get("positions", [])
            if wanted and ips.restricted_by(instruments.get(p["instrument"]), set(wanted))]
    return {"exclusions": wanted, "not_on_mandates": missing,
            "held_against": held, "held_against_value": round(sum(h["market_value"] for h in held), 2)}


async def screen(session: AsyncSession, firm_id: uuid.UUID, *, only: list[str] | None = None,
                 limit: int | None = None) -> dict:
    """Every active household, or those matching every filter in `only`, largest first."""
    only = [f.strip() for f in (only or []) if f and f.strip()]
    unknown = [f for f in only if f not in FILTERS]
    if unknown:
        raise ScreenError(f"Unknown filter {', '.join(unknown)}. Use: {', '.join(FILTERS)}.")
    instruments = {i.symbol: i for i in (await session.execute(
        select(Instrument).where(Instrument.firm_id == firm_id))).scalars().all()}
    mandates = {str(m.id): m for m in (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm_id))).scalars().all()}

    rows, currency, screened = [], None, 0
    for h in await list_households(session, firm_id):
        brain = await household_brain(session, uuid.UUID(h["id"]), firm_id=firm_id)
        if not brain:
            continue
        screened += 1
        currency = currency or brain.get("currency")
        losers = [p for acc in brain["accounts"] for p in acc.get("positions", []) if p["unrealised_gain"] < 0]
        equity = await _equity(session, brain)
        values = _values(brain, mandates, instruments)
        row = {
            "household_id": brain["household"]["id"], "household": brain["household"]["name"],
            "total_value": brain["totals"]["total_value"],
            "equity": equity,
            "over_equity_target": any(e["over_target"] for e in equity),
            "unrealised_loss": round(-sum(p["unrealised_gain"] for p in losers), 2),
            "positions_at_loss": len(losers),
            "values": values,
            "values_gap": bool(values["not_on_mandates"] or values["held_against"]),
            "next_gen": [p["full_name"] for p in brain["persons"] if p.get("is_next_gen")],
        }
        matches = {"over_equity_target": row["over_equity_target"], "unrealised_loss": row["positions_at_loss"] > 0,
                   "values_gap": row["values_gap"], "next_gen": bool(row["next_gen"])}
        if all(matches[f] for f in only):
            rows.append(row)

    rows.sort(key=lambda r: -r["total_value"])
    shown = rows[:limit] if limit else rows
    return {
        "currency": currency, "filters": only, "households_screened": screened, "matched": len(rows),
        "households": shown,
        "counts": {
            "over_equity_target": sum(1 for r in rows if r["over_equity_target"]),
            "unrealised_loss": sum(1 for r in rows if r["positions_at_loss"]),
            "values_gap": sum(1 for r in rows if r["values_gap"]),
            "next_gen": sum(1 for r in rows if r["next_gen"]),
        },
        "not_checked": NOT_CHECKED,
    }
