"""Reverse Churning Detector — flags fee-paying clients with no advisory activity.

'Reverse churning: parking inactive clients in fee-based accounts — a supervisory
focus area' — L100 §8.4. Regulators expect ongoing advisory value for ongoing fees.
Extends Conduct Surveillance with a specific reverse-churning surveillance signal.
Creates a SurveillanceFlag + suggests a check-in agenda for each flagged household.

What counts is a review a person recorded or a meeting that was held. Tasks and proposals do not: most are the
platform's own, and this agent's proposal must not reset its own clock. A household with neither on record is
not measured, because no record does not mean no review. Client reviews lists those with what they were billed.
A household keeps one open flag: a run that finds it again adds nothing.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.agents._follow_up import FollowUpOnApproval
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time, markets, reviews
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier, MeetingStatus, SurveillanceSeverity
from app.models.governance import SurveillanceFlag
from app.models.engagement import ClientReview, Meeting

_INACTIVITY_MONTHS = 12  # flag when no advisory activity for this long
_FEE_MANDATE_KEYWORDS = ("discretionary", "advisory")  # fee-based mandate types


def _last_activity_date(held_reviews: list[date], held_meetings: list[date]) -> date | None:
    return max([*held_reviews, *held_meetings], default=None)


class ReverseChurningAgent(FollowUpOnApproval, BaseAgent):
    follow_up_verb = "Book a review"
    follow_up_what = "book a review with the client"
    follow_up_days = 14
    follow_up_caveat = "the flag stays until a person resolves it, and a task is not a review"
    key = AgentKey.REVERSE_CHURNING
    name = "Reverse Churning Detector"
    lifecycle_stage = "protect_govern"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        households = await list_households(s, ctx.firm.id)
        results = []
        not_measured = 0
        today = firm_time.today(ctx.firm)
        cutoff = reviews.add_months(today, -_INACTIVITY_MONTHS)

        for hh_summary in households:
            hid = hh_summary["id"]
            brain = await household_brain(s, uuid.UUID(str(hid)), firm_id=ctx.firm.id)
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

            held = (await s.execute(select(ClientReview.held_on).where(ClientReview.household_id == hid_uuid))).scalars().all()
            met = (await s.execute(select(Meeting.scheduled_at).where(
                Meeting.household_id == hid_uuid, Meeting.status == MeetingStatus.COMPLETED.value))).scalars().all()
            last_active = _last_activity_date(list(held), [firm_time.today(ctx.firm, m) for m in met if m])

            if last_active is None:
                not_measured += 1
                continue
            if last_active < cutoff:
                months_inactive = (today - last_active).days // 30
                aum = brain["totals"].get("total_value", 0) or 0
                results.append({
                    "brain": brain,
                    "months_inactive": months_inactive,
                    "last_active": last_active.isoformat(),
                    "aum": aum,
                    "mandate_types": [m.get("mandate_type") for m in fee_mandates],
                })

        results.sort(key=lambda r: -r["months_inactive"])
        return {"flagged": results, "not_measured": not_measured}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed["flagged"]:
            return []

        drafts = []
        for item in sensed["flagged"]:
            brain = item["brain"]
            hh = brain["household"]
            jurisdiction = (hh.get("values") or {}).get("jurisdiction") or ctx.firm.jurisdiction or "NZ"
            currency = markets.currency(jurisdiction, ctx.firm.base_currency)

            goals = brain.get("goals", [])
            goal_summary = ", ".join(g.get("kind", "") for g in goals[:3]) or "no goals recorded"

            prompt = (
                f"Draft a re-engagement check-in agenda for the adviser. "
                f"Household: {hh['name']} | Jurisdiction: {jurisdiction}\n"
                f"AUM: {currency} {item['aum']:,.0f} | "
                f"Inactive for: {item['months_inactive']} months\n"
                f"Last review or meeting on record: {item['last_active']}\n"
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

            # Write a SurveillanceFlag for compliance records, once: it stands until a person resolves it. A flag
            # has no household column, so the household rides in its attributes. It used to be passed as a column,
            # which raised, and the except below hid that no flag was ever written.
            open_flags = (await ctx.session.execute(select(SurveillanceFlag.attributes).where(
                SurveillanceFlag.firm_id == ctx.firm.id, SurveillanceFlag.kind == "reverse_churning",
                SurveillanceFlag.resolved.is_(False)))).scalars().all()
            standing = any((a or {}).get("household_id") == str(hh["id"]) for a in open_flags)
            try:
                flag = None if standing else SurveillanceFlag(
                    firm_id=ctx.firm.id,
                    target_agent_key=str(self.key),
                    severity=SurveillanceSeverity.MEDIUM,
                    category="conduct",
                    kind="reverse_churning",
                    finding=(
                        f"{hh['name']} has been on fee-based advisory mandate(s) "
                        f"with no review or meeting on record for {item['months_inactive']} months "
                        f"(last: {item['last_active']}). "
                        "Potential reverse-churning conduct risk."
                    ),
                    resolved=False,
                    auto_paused_agent=False,
                    attributes={
                        "household_id": str(hh["id"]), "household": hh["name"],
                        "months_inactive": item["months_inactive"],
                        "last_active": item["last_active"],
                        "aum": item["aum"],
                        "mandate_types": item["mandate_types"],
                    },
                )
                if flag is not None:
                    ctx.session.add(flag)
                    await ctx.session.flush()
            except Exception:
                pass  # flag write must not break the agent run

            drafts.append(RecommendationDraft(
                title=(
                    f"Reverse churning risk — {item['months_inactive']}m inactive — {hh['name']}"
                ),
                summary=(
                    f"Fee-based mandate, no review or meeting on record for {item['months_inactive']} months. "
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
