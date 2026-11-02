"""Rollover Best-Interest Documenter Agent (L200 A6). Tier 2.

Triggered when an onboarding case involves a retirement plan rollover. It drafts the DOL PTE 2020-02 rollover
documentation from what is on file and nothing else: the plan's facts as a person recorded them from the plan
statement, the points `aurea_core/transfer_file.analyse` lays out (cost, direct or indirect, employer stock, a plan
loan, the required distribution first, the age-55 exception), and the adviser's own reason. With any of that missing it
drafts nothing, because a memo built on assumed fees is not evidence. It does not conclude for the adviser.

Regulatory anchor: DOL PTE 2020-02 - a fiduciary documents why a rollover recommendation is in the client's best interest.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import transfer_file
from app.models.enums import AgentKey, AutonomyTier
from app.models.onboarding import OnboardingCase


class RolloverPTEDocumenterAgent(BaseAgent):
    key = AgentKey.ROLLOVER_PTE_DOCUMENTER
    name = "Rollover PTE 2020-02 Documenter"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject else None
        if not case or case.firm_id != ctx.firm.id:
            return {"applicable": False}
        rollover = await transfer_file.rollover_for(s, case, ctx.firm)
        if not rollover or not rollover.get("applies"):
            return {"applicable": False, "reason": (rollover or {}).get("why", "Not a rollover.")}
        return {"applicable": True, "case_id": str(case.id), "case_name": case.prospect_name, "rollover": rollover,
                "pte_status": case.pte_status}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        r = sensed["rollover"]
        if not r["complete"]:
            # Not assessed is the answer: the read tool names what to record.
            return []
        facts = r["facts"]
        points = [{k: p[k] for k in ("label", "status", "figures")} for p in r["points"]]
        prompt = (
            f"Draft the DOL PTE 2020-02 rollover documentation for {sensed['case_name']}, for compliance records, from these "
            f"facts only: plan {facts.get('plan_name') or 'as recorded'}, recorded from {facts.get('source')}; the points laid out: {points}; "
            f"the adviser's reason, in their words: {r['adviser_reason']!r}. Sections: the plan and the IRA compared, each point as laid out, "
            "the adviser's reason quoted. Add no fact, figure or conclusion that is not given here, and do not argue for the rollover."
        )
        fallback = (f"PTE 2020-02 documentation for {sensed['case_name']}: " + "; ".join(f"{p['label']}: {p['status'].replace('_', ' ')}" for p in points)
                    + f". Adviser's reason: {r['adviser_reason']}")
        memo = await narrate(ctx, task="compliance", system=firm_voice(ctx), prompt=prompt, fallback=fallback)
        cost = next(p for p in r["points"] if p["key"] == "cost")["figures"]
        return [RecommendationDraft(
            title=f"PTE 2020-02 rollover documentation — {sensed['case_name']}",
            summary=f"Plan {cost.get('plan_bps'):g} bps against IRA {cost.get('ira_bps'):g} bps; {sum(1 for p in points if p['status'] == 'applies')} point(s) apply.",
            rationale=memo, confidence=0.9, priority=2,
            subject=Subject("onboarding_case", sensed["case_id"], sensed["case_name"]),
            payload={"pte_memo": memo, "points": points, "adviser_reason": r["adviser_reason"], "plan_expense_ratio_bps": cost.get("plan_bps"),
                     "advisory_fee_bps": cost.get("ira_bps"), "net_fee_difference_bps": cost.get("difference_bps")},
            evidence={"source": facts.get("source"), "plan_bps": cost.get("plan_bps"), "ira_bps": cost.get("ira_bps")},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Store the PTE memo in the case proposal and mark pte_status = generated."""
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}

        payload = recommendation.modified_payload or recommendation.payload or {}
        proposal = dict(case.proposal or {})
        proposal["pte_2020_02_memo"] = payload.get("pte_memo", "")
        proposal["pte_fee_comparison"] = {
            "plan_expense_ratio_bps": payload.get("plan_expense_ratio_bps"),
            "advisory_fee_bps": payload.get("advisory_fee_bps"),
            "net_difference_bps": payload.get("net_fee_difference_bps"),
        }
        case.proposal = proposal
        case.pte_status = "generated"
        await s.flush()
        return {
            "executed": True,
            "note": f"PTE 2020-02 rollover rationale stored for {case.prospect_name}.",
        }
