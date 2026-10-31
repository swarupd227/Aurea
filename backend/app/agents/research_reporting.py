"""Research & reporting agent (spec Table 9) — deep. Tier 1/2 — reviewed before client-facing.

Drafts a client-ready report: performance & positioning grounded in the firm's house views,
goals-based projections, scenario/stress outcomes, and values-aligned framing. Produces a
ClientReport (draft); on approval it is marked client-ready and surfaces in Canvas."""
from __future__ import annotations

import uuid

from app.agents._common import firm_voice, narrate
from app.agents._signals import goal_projections, positions_of
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time, knowledge, markets, planning, reports
from app.aurea_core.graph import household_brain
from app.core.db import utcnow
from app.models.engagement import ClientReport
from app.models.enums import AgentKey, AutonomyTier, ReportStatus


class ResearchReportingAgent(BaseAgent):
    key = AgentKey.RESEARCH_REPORTING
    name = "Research & Reporting"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1

    async def sense(self, ctx: AgentContext) -> dict:
        hid = ctx.subject.id
        brain = await household_brain(ctx.session, hid, firm_id=ctx.firm.id) if hid else None
        if not brain:
            return {"applicable": False}
        citations = await knowledge.retrieve(ctx.session, ctx.firm.id,
                                             "market commentary outlook performance positioning", k=3)
        return {"applicable": True, "household_id": str(hid), "brain": brain, "citations": citations}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        brain = sensed["brain"]
        hh = brain["household"]
        totals = brain["totals"]
        allocation = totals["by_asset_class"]
        cites = sensed["citations"]
        values = hh.get("values") or {}

        goals = goal_projections(brain)
        risk = planning.portfolio_risk(allocation, totals["total_value"])
        stress = planning.stress_test(allocation)
        positions = positions_of(brain)
        excluded_present = [p for p in positions
                            if p["instrument"] in (values.get("exclusions", []) or [])
                            or any(x in str(p.get("name", "")).lower() for x in (values.get("exclusions", []) or []))]

        commentary = await self._commentary(ctx, hh, totals, cites, values)

        tv = totals["total_value"] or 1.0
        mix_parts = [f"{k.replace('_', ' ')} {v / tv:.0%}" for k, v in allocation.items() if v > 0]
        mix_str = ", ".join(mix_parts)
        n_classes = len(mix_parts)
        stress_parts = [f"{k.replace('_', ' ')} {v['impact_pct']:.0%}" for k, v in stress.items()]

        sections = [
            {"heading": "Portfolio overview",
             "body": f"Your total portfolio is valued at {markets.money_for(ctx.firm)(totals['total_value'])}, diversified across "
                     f"{n_classes} asset classes ({mix_str})."},
            {"heading": "Positioning & house view", "body": commentary},
            {"heading": "Goals — are you on track?",
             "body": "; ".join(f"{g['name']}: {'on track' if g['on_track'] else 'needs attention'} "
                               f"({g['probability']:.0%} likelihood)" for g in goals) or "No goals modelled."},
            {"heading": "Scenario & stress testing",
             "body": "Against historical shocks, your plan would move: " + "; ".join(stress_parts)
                     + f". Estimated 1-year 95% value-at-risk is {markets.money_for(ctx.firm)(risk['var_95_1y'])}."},
            {"heading": "Values alignment",
             "body": (f"Your portfolio is framed against your stated values"
                      + (f" ({', '.join(values.get('themes', []))})" if values.get("themes") else "")
                      + (f". Note: {len(excluded_present)} holding(s) conflict with your exclusions and are flagged for review."
                         if excluded_present else ". No holdings conflict with your stated exclusions."))},
        ]

        # Persist the report as a draft. One still waiting for a decision is rewritten rather than
        # left behind: the open proposal only follows the newest, so an older draft could never be
        # approved and would sit in the list for good.
        hid = uuid.UUID(str(sensed["household_id"]))
        period = f"As at {firm_time.today(ctx.firm).isoformat()}"
        title = f"Portfolio review — {hh['name']}"
        data = {"allocation": allocation, "risk": risk, "stress": stress, "goals": goals}
        report = await reports.waiting_draft(ctx.session, ctx.firm.id, hid)
        if report:
            report.title, report.period, report.sections, report.data = title, period, sections, data
        else:
            report = ClientReport(firm_id=ctx.firm.id, household_id=hid, title=title, period=period,
                                  status=ReportStatus.DRAFT, sections=sections, data=data)
            ctx.session.add(report)
        await ctx.session.flush()

        return [RecommendationDraft(
            title=f"Client report — {hh['name']}",
            summary=f"Client-ready report drafted: {len(sections)} sections, values-aligned, grounded in firm research.",
            rationale=commentary, confidence=0.82, priority=3,
            subject=Subject("household", hid, hh["name"]),
            payload={"report_id": str(report.id), "sections": sections, "period": period},
            evidence={"data_confidence": totals["data_confidence"], "var_95_1y": risk["var_95_1y"]},
            citations=cites,
        )]

    async def _commentary(self, ctx, hh, totals, cites, values) -> str:
        fallback = (
            f"Your portfolio remains positioned in line with {ctx.firm.name}'s house view — a neutral "
            "stance with a quality tilt. We continue to monitor concentration and tax efficiency, and "
            "rebalance back to target when an asset class drifts beyond its tolerance band. Commentary "
            "is grounded in the firm's own research."
        )
        prompt = (
            f"Write a warm, client-ready 'positioning & house view' paragraph for household '{hh['name']}' "
            f"(total {markets.money_for(ctx.firm)(totals['total_value'])}; values {values}). Ground it in this firm research and "
            f"cite by title: " + "; ".join(f"[{c['title']}] {c['excerpt'][:160]}" for c in cites)
            + ". Be precise, no guarantees."
        )
        return await narrate(ctx, task="narrative", system=firm_voice(ctx), prompt=prompt,
                             fallback=fallback, max_tokens=600)

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        payload = recommendation.modified_payload or recommendation.payload or {}
        report = await reports.of_proposal(ctx.session, ctx.firm.id, payload)
        if report:
            report.status = ReportStatus.CLIENT_READY
            report.published_at = utcnow()
            await ctx.session.flush()
            return {"executed": True, "note": "Report approved and published as client-ready.",
                    "report_id": str(report.id)}
        return {"executed": False, "note": "Report not found."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        report = await reports.of_proposal(ctx.session, ctx.firm.id, recommendation.payload)
        if report:
            report.status = ReportStatus.DRAFT
            report.published_at = None
            await ctx.session.flush()
            return {"reversed": True, "note": "Report withdrawn from client-ready back to draft."}
        return {"reversed": False, "note": "Report not found."}
