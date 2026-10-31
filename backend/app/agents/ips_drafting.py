"""IPS Drafter Agent — generates a formal Investment Policy Statement from the client brain.

Synthesises the RRTTLLU framework (Return · Risk · Time horizon · Tax · Liquidity · Legal ·
Unique preferences) from all structured data in the household brain. The output is a
draft IPS for adviser review — the adviser is the named author before client delivery.
"""
from __future__ import annotations

import uuid

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import ips, markets, risk_profile
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier


def _assessed(view: dict | None) -> str:
    """The client's scored answers, which are not the same thing as the profile on the mandate."""
    scored = (view or {}).get("assessment") or {}
    if scored.get("status") != "scored":
        return "No scored questionnaire on file"
    return (f"Questionnaire: tolerance {scored['tolerance']}, capacity {scored['capacity']}, "
            f"indicated {scored['indicated']}" + (" (they differ)" if scored.get("tension") else ""))


def _policy_line(row: dict) -> str:
    """The targets and band this draft may state, so a section is filled from the record or left open."""
    targets = row.get("targets") or {}
    if not targets:
        return "No targets on file"
    weights = ", ".join(f"{c.replace('_', ' ')} {w:.0%}" for c, w in targets.items())
    source = f"IPS version {row['ips']['version']} in force" if row.get("ips") else f"the {row.get('model') or 'mandate'} model"
    return f"{weights}; band ±{row.get('drift_band') or 0:.1%}; from {source}"


def _rrttllu(brain: dict) -> dict:
    """Extract RRTTLLU dimensions from the client brain."""
    household = brain["household"]
    persons = brain.get("persons", [])
    mandates = brain.get("mandates", [])
    goals = brain.get("goals", [])
    entities = brain.get("entities", [])
    totals = brain.get("totals", {})

    # Return objective — from goals
    goal_summaries = []
    for g in goals:
        amt = g.get("target_amount", 0)
        kind = g.get("kind", "goal")
        yr = g.get("target_date", "")[:4] if g.get("target_date") else ""
        goal_summaries.append(f"{kind} target {amt:,.0f} by {yr}" if yr else f"{kind} target {amt:,.0f}")
    return_objective = "; ".join(goal_summaries) if goal_summaries else "Not yet specified"

    # Risk — tolerance (psychological) and capacity (financial)
    primary_mandate = mandates[0] if mandates else {}
    suitability = primary_mandate.get("suitability") or {}
    risk_tolerance = suitability.get("risk_tolerance") or suitability.get("risk_profile") or "Not assessed"
    risk_capacity = suitability.get("risk_capacity") or suitability.get("capacity_for_loss") or "Not assessed"
    horizon_years = suitability.get("time_horizon_years") or "Not specified"

    # Tax — from person profiles
    tax_notes = []
    for p in persons:
        profile = p.get("profile") or {}
        tax = profile.get("tax") or {}
        name = p.get("preferred_name") or p["full_name"].split()[0]
        if tax.get("marginal_rate"):
            tax_notes.append(f"{name}: {int(tax['marginal_rate'] * 100)}% marginal rate")
        if tax.get("pir"):
            tax_notes.append(f"{name}: PIR {int(tax['pir'] * 100)}%")
    tax_situation = "; ".join(tax_notes) if tax_notes else "Review required"

    # Liquidity — from constraints
    constraints = primary_mandate.get("constraints") or {}
    liquidity = constraints.get("liquidity_reserve") or "Not specified"
    cgt_budget = constraints.get("cgt_budget")

    # Legal — entities
    legal_structures = [f"{e['entity_type'].title()} ({e['name']})" for e in entities]
    legal = "; ".join(legal_structures) if legal_structures else "No formal structures recorded"

    # Unique preferences — values exclusions
    values_excl = list(dict.fromkeys([*(suitability.get("values_exclusions") or []),
                                      *(constraints.get("values_exclusions") or [])]))
    unique = "; ".join(values_excl) if values_excl else "No values-based exclusions recorded"

    # Current allocation
    by_class = totals.get("by_asset_class") or {}
    total_val = totals.get("total_value") or 0
    alloc_lines = [
        f"{k.replace('_', ' ')} {v / total_val:.0%}" for k, v in by_class.items() if v and total_val
    ]
    current_alloc = ", ".join(alloc_lines) if alloc_lines else "No holdings recorded"

    return {
        "return_objective": return_objective,
        "risk_tolerance": risk_tolerance,
        "risk_capacity": risk_capacity,
        "time_horizon_years": horizon_years,
        "tax_situation": tax_situation,
        "liquidity": liquidity,
        "cgt_budget": cgt_budget,
        "legal": legal,
        "unique_preferences": unique,
        "current_allocation": current_alloc,
        "total_value": total_val,
        "jurisdiction": (household.get("values") or {}).get("jurisdiction"),
    }


class IPSDraftingAgent(BaseAgent):
    key = AgentKey.IPS_DRAFTING
    name = "IPS Drafter"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_2
    scheduled = False

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            ids = [uuid.UUID(str(h["id"])) for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [ctx.subject.id]
        brains = [b for hid in ids if (b := await household_brain(s, hid, firm_id=ctx.firm.id))]
        assessed, policies = {}, {}
        for brain in brains:
            hid = brain["household"]["id"]
            household_id = uuid.UUID(str(hid))
            assessed[hid] = _assessed(await risk_profile.for_household(s, household_id, firm_id=ctx.firm.id))
            rows = (await ips.for_household(s, household_id, firm_id=ctx.firm.id) or {}).get("mandates") or []
            policies[hid] = _policy_line(rows[0]) if rows else "No targets on file"
        return {"brains": brains, "assessed": assessed, "policies": policies}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for brain in sensed["brains"]:
            hh = brain["household"]
            dims = _rrttllu(brain)
            dims["assessment"] = sensed["assessed"].get(hh["id"], "No scored questionnaire on file")
            dims["policy"] = sensed["policies"].get(hh["id"], "No targets on file")
            dims["jurisdiction"] = dims["jurisdiction"] or ctx.firm.jurisdiction
            currency = markets.currency(dims["jurisdiction"], ctx.firm.base_currency)

            prompt = f"""Draft a formal Investment Policy Statement (IPS) for the adviser's review.

Client: {hh['name']} | Jurisdiction: {dims['jurisdiction']} | Total portfolio: {currency} {dims['total_value']:,.0f}

RRTTLLU framework — populate each section from the data below:

R — Return objective: {dims['return_objective']}
R — Risk tolerance (psychological): {dims['risk_tolerance']}
    Risk capacity (financial): {dims['risk_capacity']}
    {dims['assessment']}
T — Time horizon: {dims['time_horizon_years']} years
T — Tax situation: {dims['tax_situation']}
    CGT budget: {dims['cgt_budget'] or 'Not set'}
L — Liquidity needs: {dims['liquidity']}
L — Legal & ownership structures: {dims['legal']}
U — Unique preferences / values exclusions: {dims['unique_preferences']}

Current allocation: {dims['current_allocation']}
Targets and rebalancing band on file: {dims['policy']}

State the targets and band exactly as given. Do not propose others, and if none are on file mark those
sections for the adviser. Where the questionnaire and the mandate's profile differ, say so; do not resolve it.

Write the IPS draft in professional advisory language. Include:
1. Client objective statement (2 sentences)
2. Return requirement (specific, linked to goals)
3. Risk profile (tolerance AND capacity — distinguish them)
4. Time horizon
5. Tax considerations
6. Liquidity requirements
7. Legal / regulatory constraints
8. Unique preferences
9. Target allocation statement
10. Rebalancing policy (drift bands)

Mark any section where data is insufficient with [ADVISER TO COMPLETE].
This is a draft for adviser review — not final or client-facing."""

            fallback = (
                f"IPS draft for {hh['name']}: "
                f"Return objective: {dims['return_objective']}. "
                f"Risk tolerance: {dims['risk_tolerance']}, capacity: {dims['risk_capacity']}. "
                f"{dims['assessment']}. Targets: {dims['policy']}. "
                f"Time horizon: {dims['time_horizon_years']} years. "
                f"Tax: {dims['tax_situation']}. Legal: {dims['legal']}. "
                "Adviser to review and complete missing sections before client delivery."
            )

            ips_text = await narrate(
                ctx, task="ips_draft", system=firm_voice(ctx), prompt=prompt,
                fallback=fallback, max_tokens=1200,
            )

            drafts.append(RecommendationDraft(
                title=f"IPS draft ready — {hh['name']}",
                summary=f"RRTTLLU-structured IPS draft for adviser review. "
                        f"Portfolio {currency} {dims['total_value']:,.0f}. "
                        f"Return objective: {dims['return_objective'][:80]}.",
                rationale=ips_text,
                confidence=0.82,
                priority=2,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={"signal": "ips_draft", "rrttllu": dims, "ips_text": ips_text},
                evidence={"framework": "RRTTLLU", "jurisdiction": dims["jurisdiction"]},
            ))
        return drafts
