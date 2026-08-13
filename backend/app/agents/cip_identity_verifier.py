"""A2 — CIP Identity Verifier Agent.  Tier 2 (HITL for review/failed outcomes).

Runs automated KYC identity verification via the pluggable IdentityAdapter.
Default: mock Socure.  Production: set AUREA_IDENTITY_PROVIDER=socure.

L200 target: zero manual ID re-verification for 'verified' outcomes; auto-escalate
'review'/'failed' to the compliance queue.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.integrations import identity as id_integration
from app.models.enums import AgentKey, AutonomyTier
from app.models.onboarding import OnboardingCase


class CIPIdentityVerifierAgent(BaseAgent):
    key = AgentKey.CIP_IDENTITY_VERIFIER
    name = "CIP Identity Verifier"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject else None
        if not case or case.firm_id != ctx.firm.id:
            return {"applicable": False}
        intake = case.intake or {}
        return {
            "applicable": True,
            "case_id": str(case.id),
            "prospect_name": case.prospect_name,
            "dob": intake.get("dob"),
            "address": intake.get("address"),
            "id_number": intake.get("id_number"),
            "registration_type": getattr(case, "registration_type", None),
            "cip_status": getattr(case, "cip_status", None),
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        adapter = id_integration.get_adapter(
            ctx.config.get("identity_provider") if ctx.config else None
        )
        result = adapter.verify(
            full_name=sensed["prospect_name"],
            dob=sensed.get("dob"),
            address=sensed.get("address"),
            id_number=sensed.get("id_number"),
            registration_type=sensed.get("registration_type"),
        )

        flag_text = f"Flags: {', '.join(result.flags)}." if result.flags else "No flags raised."
        priority = {"verified": 4, "review": 2, "failed": 1}.get(result.status, 3)

        fallback = (
            f"CIP {result.status.upper()} for {sensed['prospect_name']}. "
            f"Score {result.score:.0%} via {result.provider}. "
            f"Ref: {result.reference_id}. {flag_text}"
        )
        prompt = (
            f"Summarise the CIP identity verification result for {sensed['prospect_name']}. "
            f"Status: {result.status}. Confidence score: {result.score:.0%}. "
            f"Provider: {result.provider}. Reference: {result.reference_id}. "
            f"Flags: {result.flags or 'none'}. "
            "Explain to the compliance officer what this means and what action, if any, is required."
        )
        rationale = await narrate(
            ctx, task="compliance", system=firm_voice(ctx), prompt=prompt, fallback=fallback
        )

        return [RecommendationDraft(
            title=f"CIP {result.status.upper()} — {sensed['prospect_name']}",
            summary=f"Score {result.score:.0%} · provider {result.provider} · {flag_text}",
            rationale=rationale,
            confidence=result.score,
            priority=priority,
            subject=Subject("onboarding_case", sensed["case_id"], sensed["prospect_name"]),
            payload={
                "cip_status": result.status,
                "cip_score": result.score,
                "cip_flags": result.flags,
                "cip_reference_id": result.reference_id,
                "provider": result.provider,
            },
            evidence={
                "score": result.score,
                "flags": result.flags,
                "reference_id": result.reference_id,
            },
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}
        payload = recommendation.modified_payload or recommendation.payload or {}
        case.cip_status = payload.get("cip_status")
        case.cip_score = payload.get("cip_score")
        case.cip_flags = payload.get("cip_flags", [])
        case.cip_reference_id = payload.get("cip_reference_id")
        await s.flush()
        return {
            "executed": True,
            "cip_status": case.cip_status,
            "cip_score": case.cip_score,
            "flags": case.cip_flags,
        }
