"""Abandonment Recovery Agent (L200 A7). Tier 1 · Scheduled daily.

Identifies onboarding cases that have stalled (no status change for N days) and
diagnoses the stall point in the funnel by track (A=Legal, B=Account, C=KYC/AML, D=Funding).
Generates a personalised adviser nudge — specific action required, specific next step —
not a generic reminder.

L200: "Abandonment rate — digital applications started but not completed; funnel analytics
by step." Industry benchmark: <1 adviser-hour per household (digital-native) vs tens of hours
(traditional).
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.core.db import utcnow
from app.models.enums import AgentKey, AutonomyTier
from app.models.onboarding import OnboardingCase

# Days idle before flagging as stalled.
_STALL_AMBER_DAYS = 3
_STALL_RED_DAYS = 7

# Map a stall point to the human-readable action and track.
_STALL_DIAGNOSIS: dict[str, dict] = {
    "intake": {
        "track": "A — Legal & Disclosures",
        "action": "complete the intake form and upload identity documents",
        "next_step": "Upload at least one identity document (passport or driver's licence) "
                     "and confirm source of wealth.",
    },
    "screening": {
        "track": "C — Financial-Crime Compliance",
        "action": "review the AML/CFT screening results and clear any exceptions",
        "next_step": "Run the onboarding agent to complete document extraction and AML screening.",
    },
    "review": {
        "track": "A — Legal & Disclosures / C — Compliance",
        "action": "provide compliance sign-off on the onboarding proposal",
        "next_step": "Open the case, review the exceptions list, then Approve or Dismiss.",
    },
}


class AbandonmentRecoveryAgent(BaseAgent):
    key = AgentKey.ABANDONMENT_RECOVERY
    name = "Abandonment Recovery"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        now = utcnow()
        cutoff = now - timedelta(days=_STALL_AMBER_DAYS)

        # Find cases that haven't moved since the cutoff and aren't terminal.
        stalled = (
            await s.execute(
                select(OnboardingCase).where(
                    OnboardingCase.firm_id == ctx.firm.id,
                    OnboardingCase.status.not_in(["approved", "rejected"]),
                    OnboardingCase.updated_at < cutoff,
                )
            )
        ).scalars().all()

        stall_reports = []
        for case in stalled:
            idle_days = (now - case.updated_at).days if case.updated_at else 0
            sla = getattr(case, "sla_days", 30) or 30
            sla_risk = "red" if idle_days >= _STALL_RED_DAYS else "amber"
            days_remaining = max(0, sla - (now - case.created_at).days) if case.created_at else None

            diag = _STALL_DIAGNOSIS.get(case.status, {
                "track": "Unknown",
                "action": "review the case and identify the blocking step",
                "next_step": "Open the case and check for missing documents or pending decisions.",
            })

            # Estimate abandonment risk from idle time and SLA proximity.
            abandon_risk = (
                "high" if idle_days >= _STALL_RED_DAYS or (days_remaining is not None and days_remaining < 5)
                else "medium" if idle_days >= _STALL_AMBER_DAYS
                else "low"
            )

            stall_reports.append({
                "case_id": str(case.id),
                "case_name": case.prospect_name,
                "status": case.status,
                "idle_days": idle_days,
                "sla_risk": sla_risk,
                "days_remaining": days_remaining,
                "abandon_risk": abandon_risk,
                "track": diag["track"],
                "action": diag["action"],
                "next_step": diag["next_step"],
                "registration_type": getattr(case, "registration_type", None),
                "nigo_flag": getattr(case, "nigo_flag", False),
                "nigo_reason": getattr(case, "nigo_reason", None),
            })

        return {
            "n_stalled": len(stall_reports),
            "stalled": stall_reports,
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if sensed["n_stalled"] == 0:
            return []

        drafts = []
        for r in sensed["stalled"]:
            # NIGO cases get a more specific nudge.
            nigo_addendum = ""
            if r["nigo_flag"] and r["nigo_reason"]:
                nigo_addendum = (
                    f" The case has a NIGO flag: {r['nigo_reason'][:120]}."
                )

            prompt = (
                f"Draft a specific, personalised adviser nudge for a stalled onboarding case. "
                f"Prospect: {r['case_name']}. "
                f"Current status: {r['status']} — stalled in {r['track']}. "
                f"Idle for {r['idle_days']} days. SLA risk: {r['sla_risk']}. "
                f"Abandonment risk: {r['abandon_risk']}. "
                f"{nigo_addendum} "
                f"Required action: {r['action']}. "
                f"Specific next step: {r['next_step']}. "
                "Write a concise, professional adviser note (3–5 sentences) that: "
                "(1) names the specific step that's blocked, "
                "(2) says exactly what the adviser needs to do, "
                "(3) communicates urgency without alarmism. "
                "Do NOT write a generic 'action required' reminder."
            )
            fallback = (
                f"{r['case_name']} onboarding has been idle for {r['idle_days']} day(s) "
                f"(stage: {r['status']}). "
                f"To resume: {r['next_step']}"
                + (f" NIGO: {r['nigo_reason']}" if r["nigo_flag"] else "")
            )
            nudge = await narrate(ctx, task="client_comms", system=firm_voice(ctx),
                                  prompt=prompt, fallback=fallback)

            drafts.append(RecommendationDraft(
                title=f"Stalled onboarding — {r['case_name']} ({r['idle_days']}d idle)",
                summary=(
                    f"Idle {r['idle_days']} days at '{r['status']}' stage. "
                    f"SLA risk: {r['sla_risk']}. Abandonment risk: {r['abandon_risk']}."
                ),
                rationale=nudge,
                confidence=0.90,
                priority=1 if r["abandon_risk"] == "high" else 2,
                subject=Subject("onboarding_case", r["case_id"], r["case_name"]),
                payload={
                    "nudge_message": nudge,
                    "idle_days": r["idle_days"],
                    "sla_risk": r["sla_risk"],
                    "abandon_risk": r["abandon_risk"],
                    "stall_track": r["track"],
                    "next_step": r["next_step"],
                },
                evidence={
                    "idle_days": r["idle_days"],
                    "sla_risk": r["sla_risk"],
                    "abandon_risk": r["abandon_risk"],
                },
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """No automated act — adviser reads the nudge and takes action manually (Tier 1)."""
        return {
            "executed": False,
            "note": "Tier 1 — nudge message surfaced to adviser; no automated action taken.",
        }
