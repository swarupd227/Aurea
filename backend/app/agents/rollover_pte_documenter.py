"""Rollover Best-Interest Documenter Agent (L200 A6). Tier 2.

Triggered when an onboarding case involves a retirement account rollover
(registration_type in employer_rollover, traditional_ira, roth_ira).

Generates a DOL PTE 2020-02 compliant rollover-rationale document by comparing the
leaving plan's fees and features against the proposed IRA — evidencing why the rollover
is in the client's best interest. The memo is stored in the case proposal and
pte_status advances to "generated".

Regulatory anchor: DOL PTE 2020-02 — fiduciary must document why the rollover benefits
the client when recommending a rollover from an employer plan to an IRA.
"""
from __future__ import annotations

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.models.enums import AgentKey, AutonomyTier
from app.models.onboarding import OnboardingCase

_ROLLOVER_TYPES = {"employer_rollover", "traditional_ira", "roth_ira"}

# Default plan feature comparison points when intake data is sparse.
_DEFAULT_PLAN_FEATURES = {
    "leaving_plan_expense_ratio_bps": 45,
    "leaving_plan_investment_options": "Limited (typically 15–25 funds)",
    "leaving_plan_distribution_flexibility": "Limited — 59½ rule, required minimum distributions",
    "proposed_ira_expense_ratio_bps": 0,  # advisory fee billed separately
    "proposed_ira_investment_options": "Broad — full universe of securities",
    "proposed_ira_distribution_flexibility": "Flexible — in-service withdrawals, Roth conversions",
}


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

        reg_type = getattr(case, "registration_type", None)
        if reg_type not in _ROLLOVER_TYPES:
            # Also check intake mandate_type hint.
            intake = case.intake or {}
            if "rollover" not in str(intake.get("source_of_funds", "")).lower() and \
               "rollover" not in str(intake.get("source_of_wealth", "")).lower():
                return {"applicable": False,
                        "reason": f"Registration type '{reg_type}' is not a rollover account."}

        intake = case.intake or {}
        plan_data = intake.get("leaving_plan", {}) or {}

        return {
            "applicable": True,
            "case_id": str(case.id),
            "case_name": case.prospect_name,
            "registration_type": reg_type,
            "plan_name": plan_data.get("plan_name", "Employer 401(k)/403(b) plan"),
            "plan_balance": plan_data.get("balance"),
            "plan_expense_ratio_bps": plan_data.get(
                "expense_ratio_bps", _DEFAULT_PLAN_FEATURES["leaving_plan_expense_ratio_bps"]
            ),
            "plan_features": plan_data.get("features", _DEFAULT_PLAN_FEATURES["leaving_plan_investment_options"]),
            "advisory_fee_bps": (intake.get("fee_bps") or 75),
            "proposal": case.proposal or {},
            "pte_status": getattr(case, "pte_status", None),
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        plan_bps = sensed["plan_expense_ratio_bps"]
        advisory_bps = sensed["advisory_fee_bps"]
        net_fee_difference = advisory_bps - plan_bps

        prompt = (
            f"Draft a DOL PTE 2020-02 rollover rationale memorandum for compliance records. "
            f"This document evidences why the retirement rollover is in the client's best interest. "
            f"Client: {sensed['case_name']}. "
            f"Leaving plan: {sensed['plan_name']} — expense ratio {plan_bps} bps, "
            f"features: {sensed['plan_features']}. "
            f"Proposed IRA: advisory fee {advisory_bps} bps (billed on AUM), "
            f"full investment universe, personalised management, estate integration. "
            f"Net fee change: {'+' if net_fee_difference > 0 else ''}{net_fee_difference} bps "
            f"({'higher in the IRA' if net_fee_difference > 0 else 'lower in the IRA or equivalent'}). "
            "Structure: (1) Executive summary of the recommendation, "
            "(2) Fee comparison table — plan vs proposed IRA, "
            "(3) Services and features comparison (investment choice, distribution flexibility, portability), "
            "(4) Fiduciary best-interest conclusion — why the rollover benefits this specific client, "
            "(5) Conflicts of interest disclosed, "
            "(6) Adviser attestation placeholder. "
            "If fees are higher in the IRA, acknowledge this clearly and explain the offsetting benefits. "
            "Be precise; this is a regulatory document."
        )
        fallback = (
            f"PTE 2020-02 rollover rationale for {sensed['case_name']}: leaving plan "
            f"({plan_bps} bps expense ratio) → proposed IRA ({advisory_bps} bps advisory fee). "
            "Rollover rationale: broader investment universe, personalised asset management, "
            "estate planning integration, and consolidated reporting justify the recommendation."
        )
        memo = await narrate(ctx, task="compliance", system=firm_voice(ctx),
                             prompt=prompt, fallback=fallback)

        return [RecommendationDraft(
            title=f"PTE 2020-02 rollover rationale — {sensed['case_name']}",
            summary=(
                f"DOL PTE 2020-02 memo generated. Plan: {sensed['plan_name']} at {plan_bps} bps → "
                f"IRA at {advisory_bps} bps advisory fee."
            ),
            rationale=memo,
            confidence=0.90,
            priority=2,
            subject=Subject("onboarding_case", sensed["case_id"], sensed["case_name"]),
            payload={
                "pte_memo": memo,
                "plan_name": sensed["plan_name"],
                "plan_expense_ratio_bps": plan_bps,
                "advisory_fee_bps": advisory_bps,
                "net_fee_difference_bps": net_fee_difference,
            },
            evidence={
                "registration_type": sensed["registration_type"],
                "plan_bps": plan_bps,
                "ira_bps": advisory_bps,
            },
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
