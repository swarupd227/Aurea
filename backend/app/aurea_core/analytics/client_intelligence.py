"""Layer 2.1 — Client & household intelligence (Analytics Companion §2.1)."""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.analytics import assumptions as A
from app.aurea_core.analytics._common import gather_brains, positions_of


def _annuity_pv(annual: float, years: int, rate: float) -> float:
    if rate <= 0:
        return annual * years
    return annual * (1 - (1 + rate) ** -years) / rate


async def compute(session: AsyncSession, firm_id: uuid.UUID, brains: list[dict] | None = None) -> dict:
    brains = brains if brains is not None else await gather_brains(session, firm_id)

    firm_aum = 0.0
    by_class: dict[str, float] = {}
    public = private = 0.0
    households = []
    wallet = []
    clv_rows = []
    tiers: dict[str, int] = {}
    nextgen_total = nextgen_engaged = 0
    multi_entity = 0
    n_persons = n_entities = n_relationships = 0

    for b in brains:
        hh = b["household"]
        seg = hh["segment"]
        aum = b["totals"]["total_value"]
        firm_aum += aum
        for k, v in b["totals"]["by_asset_class"].items():
            by_class[k] = by_class.get(k, 0.0) + v
        for p in positions_of(b):
            if p.get("market_type") == "private":
                private += p["market_value"]
            else:
                public += p["market_value"]

        n_persons += len(b["persons"])
        n_entities += len(b["entities"])
        n_relationships += len(b.get("relationships", []))
        if len(b["entities"]) > 0 and len(b["persons"]) > 0:
            multi_entity += 1
        ng = [p for p in b["persons"] if p.get("is_next_gen")]
        nextgen_total += len(ng)

        # Wallet-share & held-away.
        held_away = sum((p.get("profile") or {}).get("held_away", 0) for p in b["persons"])
        if held_away > 0:
            total_wealth = aum + held_away
        else:
            share = A.wallet_share_default(seg)
            total_wealth = aum / share if share else aum
            held_away = max(total_wealth - aum, 0.0)
        share = round(aum / total_wealth, 4) if total_wealth else 1.0
        wallet.append({"household": hh["name"], "aum": round(aum, 2),
                       "held_away": round(held_away, 2), "total_wealth": round(total_wealth, 2),
                       "wallet_share": share})

        # CLV & segmentation.
        tier = A.value_tier(aum)
        tiers[tier] = tiers.get(tier, 0) + 1
        annual_fee = aum * A.fee_rate(seg)
        clv = _annuity_pv(annual_fee, A.tenure(seg), A.CLV_DISCOUNT_RATE)
        clv_rows.append({"household": hh["name"], "segment": seg, "tier": tier,
                         "aum": round(aum, 2), "annual_fee": round(annual_fee, 2),
                         "lifetime_value": round(clv, 2)})

        households.append({"id": hh["id"], "name": hh["name"], "segment": seg,
                           "aum": round(aum, 2), "tier": tier})

    total_held_away = sum(w["held_away"] for w in wallet)
    firm_wallet_share = round(firm_aum / (firm_aum + total_held_away), 4) if (firm_aum + total_held_away) else 1.0

    return {
        "total_portfolio": {
            "firm_aum": round(firm_aum, 2), "n_households": len(brains),
            "n_accounts": sum(len(b["accounts"]) for b in brains),
            "by_asset_class": {k: round(v, 2) for k, v in by_class.items()},
            "public_value": round(public, 2), "private_value": round(private, 2),
        },
        "householding": {
            "households": len(brains), "persons": n_persons, "entities": n_entities,
            "relationships": n_relationships, "multi_entity_households": multi_entity,
            "next_gen_members": nextgen_total,
        },
        "wallet_share": {
            "firm_wallet_share": firm_wallet_share, "held_away_total": round(total_held_away, 2),
            "consolidation_opportunity": round(total_held_away, 2), "by_household": wallet,
        },
        "clv_segmentation": {
            "total_lifetime_value": round(sum(r["lifetime_value"] for r in clv_rows), 2),
            "by_tier": tiers, "clients": sorted(clv_rows, key=lambda r: -r["lifetime_value"]),
        },
    }
