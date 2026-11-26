"""Plan Summary (B-196): drafts the client's plan summary from the plan of record's frozen figures alone.

It reads the household's plan of record, and only when the plan may go to the client (reviewed, or needing no review)
sets out the summary from that baseline's figures (aurea_core/plan_summary.py): no figure is worked out again and no
model writes it. The draft is a client report, proposed for a decision whatever the agent's tier
(`agent_runs.forced_to_proposal`). Only an adviser approves it, and the household's recorded adviser when there is one
(`decision_rights.PLAN_SUMMARY_AGENTS`, `named_decider`). Approving checks the plan again and publishes the summary to
the client; rolling it back withdraws it. A rerun rewrites the draft still waiting for a decision.
"""
from __future__ import annotations

import uuid

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import plan_record, plan_summary, reports
from app.core.db import utcnow
from app.models.engagement import ClientReport
from app.models.enums import AgentKey, AutonomyTier, ReportStatus


class PlanSummaryAgent(BaseAgent):
    key = AgentKey.PLAN_SUMMARY
    name = "Plan Summary"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1

    def on_approval(self, rec) -> str:
        return "The summary is published, and the client can read it; it does not record the plan as delivered"

    async def sense(self, ctx: AgentContext) -> dict:
        hid = ctx.subject.id if ctx.subject.type == "household" else None
        if not hid:
            return {"applicable": False, "why": "A plan summary is drafted for one household."}
        record = await plan_record.for_household(ctx.session, hid, firm_id=ctx.firm.id, with_today=False)
        now = plan_summary.ready(record)
        if not now["ok"]:
            return {"applicable": False, "why": now["why"]}
        return {"applicable": True, "household_id": str(hid), "record": record}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        from app.aurea_core import advisers

        if not sensed.get("applicable"):
            return []
        record = sensed["record"]
        hid = uuid.UUID(sensed["household_id"])
        sections = plan_summary.sections(record, ctx.firm.base_currency)
        data = plan_summary.data_of(record)
        title = f"Plan summary — {record['household']}"
        period = f"Plan v{record['version']} · figures as at {data['as_of']}"
        report = await reports.waiting_draft(ctx.session, ctx.firm.id, hid, agent=plan_summary.AGENT)
        if report:
            report.title, report.period, report.sections, report.data = title, period, sections, data
        else:
            report = ClientReport(firm_id=ctx.firm.id, household_id=hid, title=title, period=period,
                                  status=ReportStatus.DRAFT, sections=sections, data=data)
            ctx.session.add(report)
        await ctx.session.flush()
        adviser = await advisers.adviser_of(ctx.session, ctx.firm.id, hid)
        fig = data["figures"]
        r = fig.get("retirement") or {}
        return [RecommendationDraft(
            title=f"Plan summary — {record['household']}",
            summary=(f"Plan v{record['version']} summarised for the client from its baseline of "
                     f"{record['baseline']['happened_on']}: {len(sections)} sections, figures as frozen."),
            rationale=("Set out from the plan of record's frozen figures alone; nothing was worked out again and no model "
                       "wrote it. Approving publishes it to the client."),
            confidence=0.9, priority=3,
            subject=Subject("household", hid, record["household"]),
            payload={"report_id": str(report.id), "sections": sections, "period": period, "kind": plan_summary.KIND,
                     "plan_version": record["version"], "baseline_id": record["baseline"]["id"],
                     "adviser_id": str(adviser.id) if adviser else None, "adviser": adviser.full_name if adviser else None},
            evidence={"source": "Plan of record", "baseline_on": record["baseline"]["happened_on"],
                      "assumption_version": fig.get("assumption_version"), "as_of": data["as_of"],
                      "retirement_success": r.get("success_probability"), "goals": len(fig.get("goals") or [])},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        from app.core import decision_rights
        from app.models.identity import User

        rec = recommendation
        decider = await ctx.session.get(User, rec.decided_by) if rec.decided_by else None
        if decider is not None:
            decision_rights.named_decider(decider.role, decider.id, rec.agent_key, rec.payload, "approve")
        report = await reports.of_proposal(ctx.session, ctx.firm.id, rec.modified_payload or rec.payload)
        if report is None:
            return {"executed": False, "note": "The summary is no longer on file; nothing was published."}
        why = await plan_summary.still_current(ctx.session, ctx.firm.id, report)
        if why:
            return {"executed": False, "note": f"Not published: {why}"}
        why = await reports.promotion_refusal(ctx.session, ctx.firm, report)       # an Indian firm's promotional report cites its approval (B-332)
        if why:
            return {"executed": False, "note": f"Not published: {why}"}
        report.status = ReportStatus.CLIENT_READY
        report.published_at = utcnow()
        await ctx.session.flush()
        return {"executed": True, "note": "Plan summary published; the client can read it.", "report_id": str(report.id)}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        report = await reports.of_proposal(ctx.session, ctx.firm.id, recommendation.payload)
        if report:
            report.status = ReportStatus.DRAFT
            report.published_at = None
            await ctx.session.flush()
            return {"reversed": True, "note": "Plan summary withdrawn from the client, back to draft."}
        return {"reversed": False, "note": "The summary is no longer on file."}
