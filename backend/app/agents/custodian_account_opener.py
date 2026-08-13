"""F6 — Custodian Account Opener Agent.  Tier 2 (HITL — adviser approves push).

Pushes an approved onboarding case to the custodian's account-opening API via
the pluggable CustodianAdapter.
Default: mock Schwab.  Production: set AUREA_CUSTODIAN_PROVIDER=schwab.

Guards:
  - Only runs on cases in 'approved' status.
  - Blocks if CIP status is not 'verified' (prevents account opening without identity check).
  - Idempotent: skips if custodian_account_id already set.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.integrations import custodian as custodian_integration
from app.models.enums import AgentKey, AutonomyTier
from app.models.onboarding import OnboardingCase


class CustodianAccountOpenerAgent(BaseAgent):
    key = AgentKey.CUSTODIAN_ACCOUNT_OPENER
    name = "Custodian Account Opener"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject else None
        if not case or case.firm_id != ctx.firm.id:
            return {"applicable": False, "reason": "Case not found."}
        if case.status != "approved":
            return {"applicable": False, "reason": f"Case status is '{case.status}' — must be 'approved'."}
        if getattr(case, "custodian_account_id", None):
            return {"applicable": False, "reason": "Account already pushed to custodian."}
        return {
            "applicable": True,
            "case_id": str(case.id),
            "prospect_name": case.prospect_name,
            "registration_type": getattr(case, "registration_type", None),
            "segment": case.segment,
            "cip_status": getattr(case, "cip_status", None),
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        cip = sensed.get("cip_status")
        if cip and cip not in ("verified",):
            return [RecommendationDraft(
                title=f"Custodian push blocked — CIP status '{cip}'",
                summary=f"CIP must be 'verified' before opening a custodian account. Current: {cip}.",
                rationale="Identity verification must pass before the custodian account can be created.",
                confidence=1.0,
                priority=1,
                subject=Subject("onboarding_case", sensed["case_id"], sensed["prospect_name"]),
                payload={"blocked": True, "reason": f"cip_status={cip}"},
            )]

        adapter = custodian_integration.get_adapter(
            ctx.config.get("custodian_provider") if ctx.config else None
        )
        result = adapter.open_account(
            prospect_name=sensed["prospect_name"],
            registration_type=sensed.get("registration_type"),
            segment=sensed.get("segment", "private_wealth"),
        )

        if result.status == "active":
            fallback = (
                f"Account {result.account_id} opened at {result.custodian.title()} "
                f"for {sensed['prospect_name']} ({sensed.get('registration_type', 'individual')}). "
                "Ready for funding and initial mandate setup."
            )
            prompt = (
                f"Summarise the custodian account opening for {sensed['prospect_name']}. "
                f"Account ID: {result.account_id} at {result.custodian}. "
                f"Registration type: {sensed.get('registration_type', 'individual')}. "
                "Explain next steps for the operations team."
            )
            rationale = await narrate(
                ctx, task="operations", system=firm_voice(ctx), prompt=prompt, fallback=fallback
            )
            return [RecommendationDraft(
                title=f"Custodian account created — {result.account_id}",
                summary=f"{result.account_id} at {result.custodian.title()} · status {result.status}",
                rationale=rationale,
                confidence=0.99,
                priority=4,
                subject=Subject("onboarding_case", sensed["case_id"], sensed["prospect_name"]),
                payload={
                    "custodian_name": result.custodian,
                    "custodian_account_id": result.account_id,
                    "custodian_push_status": result.status,
                },
                evidence={"account_id": result.account_id, "custodian": result.custodian},
            )]
        else:
            return [RecommendationDraft(
                title=f"Custodian account failed — {result.error_code}",
                summary=result.error_message or "Account opening failed.",
                rationale=f"Error from {result.custodian}: {result.error_code} — {result.error_message}",
                confidence=1.0,
                priority=1,
                subject=Subject("onboarding_case", sensed["case_id"], sensed["prospect_name"]),
                payload={
                    "custodian_name": result.custodian,
                    "custodian_push_status": "failed",
                    "error_code": result.error_code,
                },
            )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        from app.core.db import utcnow
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}
        payload = recommendation.modified_payload or recommendation.payload or {}
        if payload.get("blocked"):
            return {"executed": False, "note": payload.get("reason", "Blocked.")}
        case.custodian_name = payload.get("custodian_name")
        case.custodian_account_id = payload.get("custodian_account_id")
        case.custodian_push_status = payload.get("custodian_push_status")
        case.custodian_push_at = utcnow()
        await s.flush()
        return {
            "executed": True,
            "custodian": case.custodian_name,
            "account_id": case.custodian_account_id,
            "status": case.custodian_push_status,
        }
