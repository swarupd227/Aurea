"""Reverse Churning Detector — flags fee-paying clients with no advisory activity.

'Reverse churning: parking inactive clients in fee-based accounts — a supervisory
focus area' — L100 §8.4. Regulators expect ongoing advisory value for ongoing fees.
Extends Conduct Surveillance with a specific reverse-churning surveillance signal.
Creates a SurveillanceFlag + suggests a check-in agenda for each flagged household.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier, SurveillanceSeverity
from app.models.governance import AgentRun, Recommendation, SurveillanceFlag
from app.models.engagement import Meeting, Task

_INACTIVITY_MONTHS = 12  # flag when no advisory activity for this long
_FEE_MANDATE_KEYWORDS = ("discretionary", "advisory")  # fee-based mandate types


def _last_activity_date(meetings: list, recommendations: list, tasks: list) -> date | None:
    dates = []
    for m in meetings:
        if m.updated_at:
            dates.append(m.updated_at.date() if hasattr(m.updated_at, "date") else m.updated_at)
    for r in recommendations:
        if r.created_at:
            dates.append(r.created_at.date() if hasattr(r.created_at, "date") else r.created_at)
    for t in tasks:
        if t.created_at:
            dates.append(t.created_at.date() if hasattr(t.created_at, "date") else t.created_at)
    return max(dates) if dates else None


class ReverseChurningAgent(BaseAgent):
    key = AgentKey.REVERSE_CHURNING
    name = "Reverse Churning Detector"
    lifecycle_stage = "protect_govern"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        households = await list_households(s, ctx.firm.id)
        results = []
        cutoff = date.today() - timedelta(days=_INACTIVITY_MONTHS * 30)

        for hh_summary in households:
            hid = hh_summary["id"]
            brain = await household_brain(s, hid)
            if not brain:
                continue

            # Only flag fee-based mandates
            mandates = brain.get("mandates", [])
            fee_mandates = [
                m for m in mandates
                if any(kw in (m.get("mandate_type") or "").lower() for kw in _FEE_MANDATE_KEYWORDS)
            ]
            if not fee_mandates:
                continue

            hid_uuid = uuid.UUID(hid)

            # Check for advisory activity: meetings, recommendations, tasks
            meetings = (
                await s.execute(select(Meeting).where(Meeting.household_id == hid_uuid))
            ).scalars().all()
            tasks = (
                await s.execute(select(Task).where(Task.household_id == hid_uuid))
            ).scalars().all()
            recs = (
                await s.execute(select(Recommendation).where(Recommendation.household_id == hid_uuid))
            ).scalars().all()

            last_active = _last_activity_date(meetings, recs, tasks)

            if last_active is None or last_active < cutoff:
                months_inactive = (
                    (date.today() - last_active).days // 30
                    if last_active else _INACTIVITY_MONTHS + 99
                )
                aum = brain["totals"].get("total_value", 0) or 0
                results.append({
                    "brain": brain,
                    "months_inactive": months_inactive,
                    "last_active": last_active.isoformat() if last_active else None,
                    "aum": aum,
                    "mandate_types": [m.get("mandate_type") for m in fee_mandates],
                })

        results.sort(key=lambda r: -r["months_inactive"])
        return {"flagged": results}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed["flagged"]:
            return []

        drafts = []
        for item in sensed["flagged"]:
            brain = item["brain"]
            hh = brain["household"]
            jurisdiction = (hh.get("values") or {}).get("jurisdiction") or ctx.firm.jurisdiction or "NZ"
            currency = {"US": "USD", "UK": "GBP"}.get(jurisdiction, "NZD")

            goals = brain.get("goals", [])
            goal_summary = ", ".join(g.get("kind", "") for g in goals[:3]) or "no goals recorded"

            prompt = (
                f"Draft a re-engagement check-in agenda for the adviser. "
                f"Household: {hh['name']} | Jurisdiction: {jurisdiction}\n"
                f"AUM: {currency} {item['aum']:,.0f} | "
                f"Inactive for: {item['months_inactive']} months\n"
                f"Last advisory activity: {item['last_active'] or 'never recorded'}\n"
                f"Goals on file: {goal_summary}\n\n"
                "Generate 4–5 agenda items for a re-engagement meeting that are specific to "
                "this client's situation. Cover: portfolio review, goal progress, life events "
                "that may have occurred, regulatory changes that affect them, and next steps. "
                "Tone: warm, professional — the adviser is re-opening the relationship."
            )
            fallback = (
                f"Re-engagement agenda for {hh['name']}: "
                f"1. Portfolio performance review ({currency} {item['aum']:,.0f} AUM). "
                f"2. Goal progress update ({goal_summary}). "
                f"3. Life events since last review (tax, estate, family changes). "
                f"4. Regulatory updates affecting the client. "
                f"5. Confirm ongoing service needs and fee appropriateness."
            )

            agenda = await narrate(
                ctx, task="reverse_churning_agenda", system=firm_voice(ctx),
                prompt=prompt, fallback=fallback, max_tokens=450,
            )

            # Write a SurveillanceFlag for compliance records
            try:
                flag = SurveillanceFlag(
                    firm_id=ctx.firm.id,
                    household_id=uuid.UUID(hh["id"]),
                    severity=SurveillanceSeverity.MEDIUM,
                    category="conduct",
                    kind="reverse_churning",
                    finding=(
                        f"{hh['name']} has been on fee-based advisory mandate(s) "
                        f"with no recorded advisory activity for {item['months_inactive']} months "
                        f"(last active: {item['last_active'] or 'never'}). "
                        "Potential reverse-churning conduct risk."
                    ),
                    resolved=False,
                    auto_paused_agent=False,
                    attributes={
                        "months_inactive": item["months_inactive"],
                        "last_active": item["last_active"],
                        "aum": item["aum"],
                        "mandate_types": item["mandate_types"],
                    },
                )
                ctx.session.add(flag)
                await ctx.session.flush()
            except Exception:
                pass  # flag write must not break the agent run

            drafts.append(RecommendationDraft(
                title=(
                    f"Reverse churning risk — {item['months_inactive']}m inactive — {hh['name']}"
                ),
                summary=(
                    f"Fee-based mandate, no advisory activity for {item['months_inactive']} months. "
                    f"AUM {currency} {item['aum']:,.0f}. Re-engagement agenda drafted."
                ),
                rationale=agenda,
                confidence=0.92,
                priority=2,
                subject=Subject("household", hh["id"], hh["name"]),
                payload={
                    "signal": "reverse_churning",
                    "months_inactive": item["months_inactive"],
                    "last_active": item["last_active"],
                    "aum": item["aum"],
                    "jurisdiction": jurisdiction,
                },
                evidence={
                    "months_inactive": item["months_inactive"],
                    "last_active": item["last_active"],
                },
            ))

        return drafts
