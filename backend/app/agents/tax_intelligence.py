"""Tax Intelligence Agent — cross-book NZ tax optimisation.

Scans all household mandates simultaneously for:
  - Loss-harvesting opportunities (not just drift-triggered)
  - PIE fund regime mismatches
  - KiwiSaver contribution rate gaps
  - Bright-line property test proximity
  - Suboptimal withdrawal sequencing in decumulation
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import markets, tax_intelligence
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
        # ctx.session and ctx.firm.id — this used ctx.db and ctx.firm_id, neither of which
        # AgentContext has, so the agent raised on its first line on every run, including
        # every scheduled one. The scheduler logged it and moved on.
        households = await list_households(ctx.session, ctx.firm.id)
        results = []
        for h in households:
            import uuid
            # The firm's own jurisdiction, as the /tax route already passes. Omitting it
            # defaulted every firm to NZ tax rules.
            result = await tax_intelligence.for_household(
                ctx.session, uuid.UUID(h["id"]), ctx.firm.jurisdiction or "NZ",
                firm_id=ctx.firm.id)
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

            if result.get("jurisdiction") == "IN":
                drafts.append(await self._india(ctx, result))
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

    async def _india(self, ctx: AgentContext, result: dict) -> RecommendationDraft:
        """One household's Indian position: an estimate, taxpayer by taxpayer, for a person to check."""
        say = markets.money_for(ctx.firm)
        s, name = result["summary"], result["household_name"]
        lines = []
        for t in result["taxpayers"]:
            losses = [o for o in t["opportunities"] if o["kind"] == "loss"]
            room = [o for o in t["opportunities"] if o["kind"] == "exemption"]
            lines.append(
                f"- {t['name']} ({t['kind']}): estimated tax so far {say(t['estimated_tax'])}, "
                f"exemption left {say(t['position']['exempt_left'])}, {len(losses)} loss(es) to harvest "
                f"saving {say(sum(o['tax_saved'] for o in losses))} and carrying "
                f"{say(sum(o['carries_forward'] for o in losses))} forward, {len(room)} gain(s) that fit "
                f"the exemption saving {say(sum(o['tax_saved'] for o in room))}")
        facts = (
            f"Tax Year {result['tax_year']}, {result['days_to_year_end']} days left. "
            f"{s['taxpayers']} taxpayer(s). Unused long-term exemption {say(s['exemption_unused'])}; "
            f"harvestable loss {say(s['harvestable_loss'])}; estimated saving {say(s['estimated_tax_saving'])} "
            f"({say(s['saving_from_losses'])} from harvesting losses, {say(s['saving_from_exemption'])} from the exemption)."
        )
        # "The Sharma Family" as it reads inside a sentence, or the model writes "the The Sharma Family".
        in_a_sentence = "t" + name[1:] if name.startswith("The ") else name
        memo = await narrate(
            ctx, task="advice", system=firm_voice(ctx),
            prompt=(
                f"Write a short tax memo for the adviser on {in_a_sentence}.\n{facts}\n" + "\n".join(lines)
                + "\n\nEach person is a separate taxpayer: never net one against another. Lots go first-in "
                  "first-out and cannot be chosen. India has no wash-sale rule, but never say a rebuy is safe. "
                  "Credit a saving only to the source shown above: a loss shown saving nothing saves no tax "
                  "this year and only carries forward. These are estimates for a qualified person to check. "
                  "Keep it under 250 words. Close with the one action to take before 31 March."
            ),
            fallback=facts + " Estimates for a qualified person to check.",
            max_tokens=1200,
        )
        return RecommendationDraft(
            title=f"Tax position - {name}",
            summary=facts,
            rationale=memo,
            confidence=0.8,
            priority=2,
            subject=Subject(type="household", id=uuid.UUID(result["household_id"]), label=name),
            payload=result,
            evidence={"tax_year": result["tax_year"], "harvestable_loss": s["harvestable_loss"],
                      "estimated_tax_saving": s["estimated_tax_saving"], "exemption_unused": s["exemption_unused"]},
        )
