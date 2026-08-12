"""Tax Intelligence Agent — cross-book NZ tax optimisation.

Scans all household mandates simultaneously for:
  - Loss-harvesting opportunities (not just drift-triggered)
  - PIE fund regime mismatches
  - KiwiSaver contribution rate gaps
  - Bright-line property test proximity
  - Suboptimal withdrawal sequencing in decumulation
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import tax_intelligence
from app.aurea_core.graph import list_households
from app.models.enums import AgentKey, AutonomyTier
from app.models.graph import Household


class TaxIntelligenceAgent(BaseAgent):
    key = AgentKey.TAX_INTELLIGENCE
    name = "Tax Intelligence"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        households = await list_households(ctx.db, ctx.firm_id)
        results = []
        for h in households:
            import uuid
            result = await tax_intelligence.for_household(ctx.db, uuid.UUID(h["id"]))
            if result:
                results.append(result)
        return {"results": results}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts: list[RecommendationDraft] = []
        for result in sensed["results"]:
            hh_id = result["household_id"]
            hh_name = result["household_name"]
            summary = result["summary"]

            if summary["total_flags"] == 0:
                continue

            # Build a concise memo for the adviser
            sections = []

            lh = result["loss_harvest"]
            if lh["count"] > 0:
                sections.append(
                    f"Loss-harvest: {lh['count']} lot(s) with total unrealised loss of "
                    f"${abs(lh['total_harvestable_loss']):,.0f} "
                    f"(est. ${lh['estimated_tax_saving_at_top_pir']:,.0f} tax saving at top PIR)."
                )

            pie = result["pie_optimisation"]
            if pie["count"] > 0:
                sections.append(
                    f"PIE optimisation: {pie['count']} mandate(s) could save "
                    f"~${pie['total_annual_saving']:,.0f}/yr by switching to PIE fund structure."
                )

            bl = result["bright_line"]
            if bl["count"] > 0:
                for flag in bl["flags"]:
                    sections.append(
                        f"Bright-line: {flag['property_address']} — {flag['action']}"
                    )

            ks = result["kiwisaver"]
            if ks["count"] > 0:
                for rec in ks["recommendations"]:
                    if rec.get("recommended_rate_pct") != rec.get("current_rate_pct"):
                        sections.append(
                            f"KiwiSaver: {rec['person_name']} at {rec['current_rate_pct']}% "
                            f"— consider increasing to {rec['recommended_rate_pct']}% "
                            f"to capture full govt top-up of ${rec['max_govt_topup']:,.2f}/yr."
                        )

            memo_prompt = (
                f"You are a senior NZ wealth adviser. Write a 3-paragraph tax intelligence memo "
                f"for the {hh_name} household, covering the following findings:\n\n"
                + "\n".join(f"- {s}" for s in sections)
                + "\n\nBe concrete with dollar amounts. Mention IRD rules where relevant. "
                f"Close with the one highest-priority action for the next meeting."
            )
            memo = await narrate(ctx, memo_prompt)

            drafts.append(RecommendationDraft(
                title=f"Tax intelligence — {hh_name}",
                summary=(
                    f"{summary['total_flags']} tax flag(s): "
                    f"${abs(summary['harvestable_loss']):,.0f} harvestable loss, "
                    f"${summary['estimated_tax_saving']:,.0f} est. annual saving. "
                    f"{summary['bright_line_flags']} bright-line alert(s)."
                ),
                rationale=memo,
                confidence=0.85,
                priority=1 if summary["bright_line_flags"] > 0 else 2,
                subject=Subject(type="household", id=hh_id, label=hh_name),
                payload=result,
                evidence={
                    "harvestable_loss": summary["harvestable_loss"],
                    "estimated_tax_saving": summary["estimated_tax_saving"],
                    "bright_line_flags": summary["bright_line_flags"],
                    "pie_flags": pie["count"],
                    "kiwisaver_flags": ks["count"],
                },
            ))

        return drafts
