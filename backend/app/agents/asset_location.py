"""Asset Location Optimizer — detects tax-inefficient asset placement across account types.

Tax-efficient asset location: income-generating, tax-inefficient assets in a tax-deferred account,
growth in a Roth, where it is never taxed, and otherwise in a taxable account (where basis steps up
at death, in a market that does that). An account's treatment is its registration. A move is named
only where an account of the right kind can receive it; with none, the agent says nothing about it.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.agents._follow_up import FollowUpOnApproval
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import markets
from app.aurea_core.exits import _RETIREMENT
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier
from app.models.graph import Account

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

# How an account is taxed, by its registration (B-187). A Roth grows tax-free and is still an IRA, so it is sheltered
# like the rest; traditional, SEP, SIMPLE and rollover IRAs defer tax; anything else registered as a retirement wrapper
# (inherited IRA, 529, HSA) is sheltered but takes nothing in a swap. An account with no such registration is taxable.
TAX_FREE = frozenset({"roth_ira"})
TAX_DEFERRED = frozenset({"traditional_ira", "sep_ira", "simple_ira", "employer_rollover"})
RECEIVES = TAX_FREE | TAX_DEFERRED


def treatment(registration: str | None) -> str:
    if registration in TAX_FREE:
        return "tax_free"
    if registration in TAX_DEFERRED:
        return "tax_deferred"
    return "sheltered" if registration in _RETIREMENT else "taxable"


def _value(account: dict) -> float:
    return sum(float(p.get("market_value") or 0) for p in account.get("positions", []))


def _analyze_location(brain: dict, registrations: dict[str, str | None], *, step_up: bool = False) -> list[dict]:
    """`registrations` is each account's registration type by id. An account is taxed by how it is registered
    (exits._RETIREMENT, the set the step-up rule reads), never by a word in its name. Income belongs in a tax-deferred
    account, growth in a Roth; a move is named only where an account of the right kind, holding something to swap
    out, can receive it. Where none can, nothing is said. `step_up` is whether the household's market steps basis up
    at death (markets.basis_on_death), and only then is it given as a reason."""
    findings = []
    accounts = brain.get("accounts", [])
    kind = {a.get("id"): treatment(registrations.get(a.get("id"))) for a in accounts}
    able = [a for a in accounts if registrations.get(a.get("id")) in RECEIVES and _value(a) > 0]
    deferred = [a.get("name", "") for a in able if kind[a.get("id")] == "tax_deferred"]
    roth = [a.get("name", "") for a in able if kind[a.get("id")] == "tax_free"]
    taxable = [a.get("name", "") for a in accounts if kind[a.get("id")] == "taxable" and _value(a) > 0]

    for account in accounts:
        where = kind[account.get("id")]
        for pos in account.get("positions", []):
            asset_class = pos.get("asset_class", "equity")
            instrument = pos.get("instrument", "")
            market_value = pos.get("market_value", 0) or 0
            efficiency = _TAX_EFFICIENCY.get(asset_class, 0.6)
            if market_value < 10_000:
                continue
            row = {"instrument": instrument, "asset_class": asset_class, "account": account.get("name", ""), "account_type": where,
                   "market_value": market_value, "efficiency_score": efficiency}

            # Income in a taxable account, and a sheltered account that can take it: tax-deferred first, else a Roth.
            if where == "taxable" and efficiency < 0.5 and (deferred or roth):
                to = deferred or roth
                findings.append({**row, "issue": "tax_inefficient_in_taxable", "receiving_accounts": to,
                                 "recommendation": (f"{instrument} ({asset_class.replace('_', ' ')}) is in a taxable account, where its income is taxed "
                                                    f"at ordinary rates. {', '.join(to)} could hold it instead, in a swap."),
                                 "severity": "high" if efficiency < 0.35 else "medium"})

            # Income in a Roth spends tax-free growth on an asset that grows little, where a tax-deferred account could hold it.
            elif where == "tax_free" and efficiency < 0.5 and deferred:
                findings.append({**row, "issue": "income_in_tax_free", "receiving_accounts": deferred,
                                 "recommendation": (f"{instrument} ({asset_class.replace('_', ' ')}) is in {account.get('name', 'a Roth')}, whose growth "
                                                    f"is never taxed. {', '.join(deferred)} defers tax and could hold it, with growth here instead."),
                                 "severity": "low"})

            # Growth in a tax-deferred account is taxed as income when drawn: a Roth takes it first, else a taxable account.
            elif where == "tax_deferred" and asset_class == "equity" and market_value > 50_000 and (roth or taxable):
                to = roth or taxable
                why = ("its growth would never be taxed there" if roth else
                       "it would be taxed at capital-gains rates there" + (", and its basis steps up at death" if step_up else ""))
                findings.append({**row, "issue": "growth_in_tax_deferred", "receiving_accounts": to,
                                 "recommendation": (f"{instrument} (equity) is in {account.get('name', 'a tax-deferred account')}, and is taxed as income "
                                                    f"when drawn. {', '.join(to)} could hold it, in a swap: {why}."),
                                 "severity": "low"})

    return findings


class AssetLocationAgent(FollowUpOnApproval, BaseAgent):
    follow_up_verb = "Review with the client"
    follow_up_what = "review where assets are held with the client"
    follow_up_days = 14
    follow_up_caveat = "no order is placed"
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
        brains = [b for hid in ids if (b := await household_brain(s, uuid.UUID(hid), firm_id=ctx.firm.id))]
        account_ids = [uuid.UUID(a["id"]) for b in brains for a in b.get("accounts", [])]
        registrations = {str(i): r for i, r in (await s.execute(
            select(Account.id, Account.registration_type).where(
                Account.firm_id == ctx.firm.id, Account.id.in_(account_ids)))).all()} if account_ids else {}
        return {"brains": brains, "registrations": registrations}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for brain in sensed["brains"]:
            hh = brain["household"]
            # The household's own market, else its firm's; one with no market is named as none, not New Zealand.
            market = markets.of_household(ctx.firm, hh.get("values"))
            jurisdiction = market.code if market else None
            findings = _analyze_location(brain, sensed.get("registrations") or {},
                                         step_up=bool(market and market.basis_on_death == "step_up"))
            if not findings:
                continue

            high = [f for f in findings if f["severity"] == "high"]
            medium = [f for f in findings if f["severity"] == "medium"]
            total_mislocated = sum(f["market_value"] for f in findings if f["issue"] == "tax_inefficient_in_taxable")
            currency = markets.currency(jurisdiction, ctx.firm.base_currency)

            finding_text = "\n".join(
                f"- [{f['severity'].upper()}] {f['instrument']} in {f['account']}: {f['recommendation']}"
                for f in findings[:6]
            )
            prompt = (
                f"Write a concise asset location memo for adviser use — household '{hh['name']}', "
                f"jurisdiction {jurisdiction or 'not a supported market'}. "
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
                subject=Subject("household", uuid.UUID(str(hh["id"])), hh["name"]),
                payload={
                    "signal": "asset_location",
                    "findings": findings,
                    "total_mislocated": total_mislocated,
                    "jurisdiction": jurisdiction,
                },
                evidence={"findings_count": len(findings), "high_count": len(high)},
            ))
        return drafts
