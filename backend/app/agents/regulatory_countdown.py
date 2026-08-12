"""Regulatory Countdown Agent — proactive jurisdiction-aware deadline intelligence.

Monitors time-critical regulatory deadlines and surfaces adviser alerts before they become crises:
  • US: Federal Estate Tax Exemption Sunset (Jan 1, 2026)
  • UK: IHT Pension Pot Inclusion (April 6, 2027)
"""
from __future__ import annotations

import uuid

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import regulatory_countdown
from app.aurea_core.graph import list_households
from app.models.enums import AgentKey, AutonomyTier


class RegulatoryCountdownAgent(BaseAgent):
    key = AgentKey.REGULATORY_COUNTDOWN
    name = "Regulatory Countdown"
    lifecycle_stage = "protect_govern"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        firm_jurisdiction = ctx.firm.jurisdiction or "NZ"

        if ctx.subject.type in (None, "firm"):
            ids = [h["id"] for h in await list_households(s, ctx.firm.id)]
        else:
            ids = [str(ctx.subject.id)]

        analyses = []
        for hid in ids:
            result = await regulatory_countdown.for_household(s, uuid.UUID(hid), firm_jurisdiction)
            if result and result.get("analyses"):
                analyses.append(result)

        return {"analyses": analyses, "firm_jurisdiction": firm_jurisdiction}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts: list[RecommendationDraft] = []

        for hh_result in sensed["analyses"]:
            for analysis in hh_result.get("analyses", []):
                figures = analysis.get("figures", {})
                if not figures.get("affected"):
                    continue

                urgency = analysis.get("urgency", "medium")
                days = analysis.get("days_remaining", 999)
                currency = analysis.get("currency", "USD")
                actions = analysis.get("actions", [])
                action_text = "; ".join(a["action"] for a in actions[:3])

                if analysis["type"] == "us_estate_tax_sunset":
                    additional_tax = figures.get("additional_tax_at_risk", 0)
                    gross_estate = figures.get("gross_estate", 0)
                    fallback = (
                        f"Estate tax sunset alert for {hh_result['household_name']}: "
                        f"${gross_estate/1e6:.1f}M gross estate faces ${additional_tax/1e6:.2f}M additional "
                        f"federal estate tax if the TCJA exemption sunsets on Jan 1 2026 ({days} days). "
                        f"Immediate actions: {action_text}."
                    )
                    prompt = (
                        f"Write a concise adviser alert for '{hh_result['household_name']}'. "
                        f"Gross estate: ${gross_estate/1e6:.1f}M. "
                        f"Current combined exemption: ${figures.get('current_exemption', 0)/1e6:.1f}M. "
                        f"Post-sunset exemption: ${figures.get('post_sunset_exemption', 0)/1e6:.1f}M. "
                        f"Additional estate tax at risk: ${additional_tax/1e6:.2f}M. "
                        f"Days until TCJA sunset: {days}. "
                        f"Top planning actions: {action_text}. "
                        "Write 2-3 sentences for the adviser — professional, urgent but not alarmist, "
                        "cite the specific dollar figure and deadline. No hype."
                    )

                else:  # uk_iht_pension_inclusion
                    additional_iht = figures.get("additional_iht", 0)
                    pension_total = figures.get("pension_total", 0)
                    fallback = (
                        f"IHT pension alert for {hh_result['household_name']}: "
                        f"£{pension_total:,.0f} pension assets will enter the IHT estate from 6 April 2027 ({days} days). "
                        f"Additional IHT exposure: £{additional_iht:,.0f}. Actions: {action_text}."
                    )
                    prompt = (
                        f"Write a concise adviser alert for '{hh_result['household_name']}'. "
                        f"From 6 April 2027, unused pension pots enter the IHT estate under Finance Bill 2024. "
                        f"Pension assets: £{pension_total:,.0f}. "
                        f"Additional IHT exposure: £{additional_iht:,.0f}. "
                        f"Days until change takes effect: {days}. "
                        f"Top planning actions: {action_text}. "
                        "Write 2-3 sentences for the adviser — professional, factual, UK regulatory context. No hype."
                    )

                memo = await narrate(
                    ctx, task="regulatory_countdown_alert", system=firm_voice(ctx),
                    prompt=prompt, fallback=fallback, max_tokens=300,
                )

                priority = 1 if urgency == "critical" else 2
                drafts.append(RecommendationDraft(
                    title=f"{analysis['title']} — {hh_result['household_name']}",
                    summary=analysis["summary"],
                    rationale=memo,
                    confidence=0.92,
                    priority=priority,
                    subject=Subject("household", hh_result["household_id"], hh_result["household_name"]),
                    payload={
                        "signal": "regulatory_countdown",
                        "type": analysis["type"],
                        "jurisdiction": hh_result["jurisdiction"],
                        "deadline": analysis["deadline"],
                        "days_remaining": days,
                        "urgency": urgency,
                        "figures": figures,
                        "actions": actions,
                        "currency": currency,
                    },
                    evidence={
                        "days_remaining": days,
                        "urgency": urgency,
                        "actions_count": len(actions),
                    },
                ))

        return drafts
