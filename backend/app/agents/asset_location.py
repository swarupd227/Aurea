"""Asset Location Optimizer — detects tax-inefficient asset placement across account types.

Tax-efficient asset location: put income-generating, tax-inefficient assets in tax-advantaged
accounts (IRA, Roth, ISA, pension) and let growth assets compound in taxable accounts
(where step-up in basis applies at death). Mis-location generates avoidable tax drag.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier

# Tax efficiency score by asset class: lower = more tax-inefficient → prefer tax-advantaged
_TAX_EFFICIENCY: dict[str, float] = {
    "fixed_income": 0.2,    # interest income taxed as ordinary income — bad in taxable
    "alternatives": 0.3,    # often high income (K-1s, distributions) — bad in taxable
    "commodity": 0.4,       # collectibles rate in US — prefer tax-advantaged
    "multi_asset": 0.5,
    "property": 0.6,        # REIT dividends ordinary income — prefer sheltered
    "equity": 0.8,          # qualified dividends + LTCG — reasonably efficient in taxable
    "cash": 0.9,            # interest income taxable but low yield; liquidity trumps optimization
}

# Account types that are tax-advantaged
_TAX_ADVANTAGED_KEYWORDS = {"ira", "roth", "pension", "isa", "kiwisaver", "smsf",
                             "superannuation", "401k", "403b", "rrsp", "tfsa"}


def _is_tax_advantaged(account_name: str) -> bool:
    name_lower = account_name.lower()
    return any(kw in name_lower for kw in _TAX_ADVANTAGED_KEYWORDS)


def _analyze_location(brain: dict) -> list[dict]:
    findings = []
    accounts = brain.get("accounts", [])

    for account in accounts:
        is_sheltered = _is_tax_advantaged(account.get("name", ""))
        positions = account.get("positions", [])

        for pos in positions:
            asset_class = pos.get("asset_class", "equity")
            instrument = pos.get("instrument", "")
            market_value = pos.get("market_value", 0) or 0
            efficiency = _TAX_EFFICIENCY.get(asset_class, 0.6)

            if market_value < 10_000:
                continue

            # Mis-location: tax-inefficient asset in taxable account
            if not is_sheltered and efficiency < 0.5:
                findings.append({
                    "instrument": instrument,
                    "asset_class": asset_class,
                    "account": account.get("name", ""),
                    "account_type": "taxable",
                    "market_value": market_value,
                    "efficiency_score": efficiency,
                    "issue": "tax_inefficient_in_taxable",
                    "recommendation": (
                        f"{instrument} ({asset_class.replace('_', ' ')}) is in a taxable account. "
                        f"Income from this asset is taxed at ordinary rates. "
                        f"Consider moving to a tax-advantaged account to reduce tax drag."
                    ),
                    "severity": "high" if efficiency < 0.35 else "medium",
                })

            # Reverse mis-location: growth equity in tax-advantaged (loses step-up in basis benefit)
            elif is_sheltered and efficiency >= 0.8 and market_value > 50_000:
                findings.append({
                    "instrument": instrument,
                    "asset_class": asset_class,
                    "account": account.get("name", ""),
                    "account_type": "sheltered",
                    "market_value": market_value,
                    "efficiency_score": efficiency,
                    "issue": "growth_equity_in_sheltered",
                    "recommendation": (
                        f"{instrument} (equity) is in {account.get('name', 'a tax-advantaged account')}. "
                        f"For estate-planning households, growth equities in taxable accounts benefit from "
                        f"step-up in basis at death. Consider a swap: move income assets into this account, "
                        f"equity into taxable."
                    ),
                    "severity": "low",
                })

    return findings


class AssetLocationAgent(BaseAgent):
    key = AgentKey.ASSET_LOCATION
    name = "Asset Location Optimizer"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = False

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            ids = [h["id"] for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [str(ctx.subject.id)]
        brains = [b for hid in ids if (b := await household_brain(s, hid))]
        return {"brains": brains}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for brain in sensed["brains"]:
            hh = brain["household"]
            findings = _analyze_location(brain)
            if not findings:
                continue

            high = [f for f in findings if f["severity"] == "high"]
            medium = [f for f in findings if f["severity"] == "medium"]
            total_mislocated = sum(f["market_value"] for f in findings if f["issue"] == "tax_inefficient_in_taxable")
            jurisdiction = (hh.get("values") or {}).get("jurisdiction") or ctx.firm.jurisdiction or "NZ"
            currency = {"US": "USD", "UK": "GBP"}.get(jurisdiction, "NZD")

            finding_text = "\n".join(
                f"- [{f['severity'].upper()}] {f['instrument']} in {f['account']}: {f['recommendation']}"
                for f in findings[:6]
            )
            prompt = (
                f"Write a concise asset location memo for adviser use — household '{hh['name']}', "
                f"jurisdiction {jurisdiction}. "
                f"{len(findings)} mis-location finding(s) identified "
                f"({currency} {total_mislocated:,.0f} potentially mis-located):\n\n{finding_text}\n\n"
                "In 3–4 sentences: summarise the overall picture, name the most impactful swap, "
                "and note the tax rationale. Professional tone, adviser-facing."
            )
            fallback = (
                f"Asset location review for {hh['name']}: {len(findings)} finding(s) "
                f"({len(high)} high, {len(medium)} medium). "
                f"{currency} {total_mislocated:,.0f} potentially mis-located in taxable accounts. "
                "Review the detailed findings and propose account-level swaps."
            )

            memo = await narrate(
                ctx, task="asset_location", system=firm_voice(ctx),
                prompt=prompt, fallback=fallback, max_tokens=400,
            )

            drafts.append(RecommendationDraft(
                title=f"Asset location — {len(high)} high-priority finding(s) — {hh['name']}",
                summary=(
                    f"{len(findings)} placement finding(s). "
                    f"{currency} {total_mislocated:,.0f} in tax-inefficient positions."
                ),
                rationale=memo,
                confidence=0.85,
                priority=1 if high else 2,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={
                    "signal": "asset_location",
                    "findings": findings,
                    "total_mislocated": total_mislocated,
                    "jurisdiction": jurisdiction,
                },
                evidence={"findings_count": len(findings), "high_count": len(high)},
            ))
        return drafts
