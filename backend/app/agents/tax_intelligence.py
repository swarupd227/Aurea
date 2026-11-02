"""Tax Intelligence Agent — a cross-book tax read, for the household's own market.

Scans all household mandates simultaneously for what that market's report raises: in New Zealand
loss harvesting, PIE mismatches, KiwiSaver gaps and the bright-line test; in the US short-term gains,
NIIT, wash sales, RMDs and Roth conversions; in the UK the CGT and dividend allowances and a tapered
pension allowance. India has its own path. A market with no report raises nothing.
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


REGULATOR = {"NZ": "IRD", "US": "IRS", "UK": "HMRC"}


def _nz_findings(result: dict, say) -> list[str]:
    sections = []
    lh, pie, ks = result["loss_harvest"], result["pie_optimisation"], result["kiwisaver"]
    if lh["count"] > 0:
        sections.append(f"Loss-harvest: {lh['count']} lot(s) with total unrealised loss of {say(abs(lh['total_harvestable_loss']))} "
                        f"(est. {say(lh['estimated_tax_saving_at_top_pir'])} tax saving at top PIR).")
    if pie["count"] > 0:
        sections.append(f"PIE optimisation: {pie['count']} mandate(s) could save ~{say(pie['total_annual_saving'])}/yr by switching to PIE fund structure.")
    for flag in result["bright_line"]["flags"]:
        sections.append(f"Bright-line: {flag['property_address']} — {flag['action']}")
    for rec in ks["recommendations"]:
        if rec.get("recommended_rate_pct") != rec.get("current_rate_pct"):
            sections.append(f"KiwiSaver: {rec['person_name']} at {rec['current_rate_pct']}% — consider increasing to "
                            f"{rec['recommended_rate_pct']}% to capture full govt top-up of {say(rec['max_govt_topup'])}/yr.")
    return sections


def _us_findings(result: dict, say) -> list[str]:
    cgt, rmd, roth = result["cgt_analysis"], result["rmd_planning"], result["roth_conversion"]
    sections = []
    if cgt["total_short_term_gains"] > 10_000:
        sections.append(f"Short-term gains: {say(cgt['total_short_term_gains'])} unrealised. {cgt['st_conversion_action']}")
    if cgt["niit_exposed"]:
        sections.append(f"NIIT: about {say(cgt['niit_additional_tax'])} on long-term gains. {cgt['niit_action']}")
    if cgt["wash_sale_violations"]:
        sections.append(f"Wash sales: {len(cgt['wash_sale_violations'])} lot(s) would have a loss disallowed if sold now.")
    if rmd["rmd_active"]:
        sections.append(f"RMDs: {rmd['rmd_active']} required this year, about {say(rmd['total_rmd_this_year'])} in all.")
    for c in roth["candidates"]:
        if c["attractive"]:
            sections.append(f"Roth: {c['person_name']} has room of {say(c['recommended_conversion'])} at a {c['marginal_rate_pct']}% marginal rate.")
    return sections


def _uk_findings(result: dict, say) -> list[str]:
    cgt, div = result["cgt_allowance"], result["dividend_allowance"]
    sections = []
    if cgt["flag"]:
        sections.append(f"CGT: {say(cgt['net_taxable_gains'])} of unrealised gains above the exempt amount, about {say(cgt['estimated_cgt_at_higher_rate'])} at the higher rate. {cgt['action']}")
    if div["flag"]:
        sections.append(f"Dividends: about {say(div['estimated_excess'])} above the allowance, {say(div['estimated_tax'])} of tax. {div['action']}")
    for p in result["pension_allowance"]["persons"]:
        if p["tapered"]:
            sections.append(f"Pension: {p['person_name']}'s annual allowance is tapered to {say(p['effective_allowance'])}. {p['action']}")
    return sections


FINDINGS = {"NZ": _nz_findings, "US": _us_findings, "UK": _uk_findings}


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

            market = result.get("jurisdiction")
            if market not in FINDINGS:
                continue
            say = lambda v, c=result.get("currency"): markets.money(v, c)  # noqa: E731
            sections = FINDINGS[market](result, say)
            if not sections:
                continue
            regulator = REGULATOR[market]
            memo_prompt = (
                f"Write a 3-paragraph tax intelligence memo for the adviser on the {hh_name} household, "
                f"covering the following findings:\n\n" + "\n".join(f"- {s}" for s in sections)
                + f"\n\nBe concrete with the amounts given, and use no others. Mention {regulator} rules where relevant. "
                f"These are estimates for a qualified person to check. "
                f"Close with the one highest-priority action for the next meeting."
            )
            memo = await narrate(ctx, task="advice", system=firm_voice(ctx), prompt=memo_prompt,
                                 fallback="\n".join(sections) + "\nEstimates for a qualified person to check.")
            extra = summary.get("bright_line_flags", 0)

            drafts.append(RecommendationDraft(
                title=f"Tax intelligence — {hh_name}",
                summary=(
                    f"{summary['total_flags']} tax flag(s): {say(abs(summary['harvestable_loss']))} harvestable loss, "
                    f"{say(summary['estimated_tax_saving'])} est. saving."
                    + (f" {extra} bright-line alert(s)." if extra else "")
                ),
                rationale=memo,
                confidence=0.85,
                priority=1 if extra > 0 else 2,
                subject=Subject(type="household", id=uuid.UUID(hh_id), label=hh_name),
                payload=result,
                evidence={"jurisdiction": market, "harvestable_loss": summary["harvestable_loss"], "estimated_tax_saving": summary["estimated_tax_saving"],
                          "findings": len(sections)},
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
