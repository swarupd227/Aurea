"""EDD Source-of-Wealth Narrator Agent (L200 A4). Tier 2.

Triggered when a client is rated Medium or High AML risk (aml_risk_tier). Synthesises
uploaded documents and client-provided narratives into a structured SOW/SOF memorandum
with a corroboration-gap checklist. The generated memo is stored in the case proposal
dict and the edd_status advances from edd_pending → edd_complete on human sign-off.

This addresses L200's top AML exam finding: "EDD rationale thin — source of wealth."
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.document_intel import extract
from app.models.enums import AgentKey, AMLRiskTier, AutonomyTier, EDDStatus
from app.models.onboarding import OnboardingCase, OnboardingDocument

# Fields we look for in documents to corroborate source-of-wealth claims.
_SOW_CORROBORATION_FIELDS = [
    "company_name", "transaction_amount", "sale_proceeds", "employment_income",
    "dividend_income", "inheritance_amount", "fund_source",
]


class EDDSOWNarratorAgent(BaseAgent):
    key = AgentKey.EDD_SOW_NARRATOR
    name = "EDD Source-of-Wealth Narrator"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject else None
        if not case or case.firm_id != ctx.firm.id:
            return {"applicable": False}

        # Only EDD-eligible cases.
        risk_tier = getattr(case, "aml_risk_tier", None)
        if risk_tier not in (AMLRiskTier.MEDIUM.value, AMLRiskTier.HIGH.value):
            return {"applicable": False,
                    "reason": f"AML risk tier is '{risk_tier}' — EDD not required."}

        intake = case.intake or {}
        docs = (
            await s.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
        ).scalars().all()

        # Build corroboration map from extracted document fields.
        corroboration: dict[str, str] = {}
        for d in docs:
            for field in _SOW_CORROBORATION_FIELDS:
                val = (d.extracted or {}).get(field)
                if val and field not in corroboration:
                    corroboration[field] = f"{val} (from {d.doc_type})"

        sow_claim = intake.get("source_of_wealth", "")
        sof_claim = intake.get("source_of_funds", sow_claim)

        # Identify what's missing from corroboration.
        missing_corroboration = [f for f in _SOW_CORROBORATION_FIELDS if f not in corroboration]

        return {
            "applicable": True,
            "case_id": str(case.id),
            "case_name": case.prospect_name,
            "aml_risk_tier": risk_tier,
            "sow_claim": sow_claim,
            "sof_claim": sof_claim,
            "corroboration": corroboration,
            "missing_corroboration": missing_corroboration,
            "n_docs": len(docs),
            "edd_status": getattr(case, "edd_status", EDDStatus.NONE.value),
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        corroborated = sensed["corroboration"]
        missing = sensed["missing_corroboration"]
        risk_tier = sensed["aml_risk_tier"]

        # Build corroboration checklist for the narrative.
        checklist_present = [f"{k}: {v}" for k, v in corroborated.items()]
        checklist_missing = [f"{f}: NOT evidenced" for f in missing]

        prompt = (
            f"Draft an Enhanced Due Diligence (EDD) Source-of-Wealth & Source-of-Funds memorandum "
            f"for compliance review. Client: {sensed['case_name']}. AML risk tier: {risk_tier.upper()}. "
            f"Source of wealth claimed: '{sensed['sow_claim']}'. "
            f"Source of funds claimed: '{sensed['sof_claim']}'. "
            f"Document corroboration present: {checklist_present}. "
            f"Corroboration gaps (still required): {checklist_missing}. "
            "Structure the memo with: (1) Executive summary, (2) Source of Wealth narrative, "
            "(3) Source of Funds narrative, (4) Corroboration evidence, (5) Gaps and recommended "
            "additional documents, (6) Compliance sign-off checklist. "
            "Be precise and clinical — this is a regulatory document."
        )
        fallback = (
            f"EDD memorandum for {sensed['case_name']} (AML risk: {risk_tier}). "
            f"SOW claimed: {sensed['sow_claim']}. "
            f"{len(corroborated)} corroboration point(s) evidenced; "
            f"{len(missing)} gap(s) requiring additional documentation."
        )
        memo = await narrate(ctx, task="compliance", system=firm_voice(ctx),
                             prompt=prompt, fallback=fallback)

        gaps_severe = len(missing) > 3
        confidence = 0.88 if not gaps_severe else 0.70

        return [RecommendationDraft(
            title=f"EDD SOW memorandum — {sensed['case_name']}",
            summary=(
                f"AML risk {risk_tier}: {len(corroborated)} corroboration point(s) evidenced, "
                f"{len(missing)} gap(s) requiring additional documents."
            ),
            rationale=memo,
            confidence=confidence,
            priority=1 if risk_tier == AMLRiskTier.HIGH.value else 2,
            subject=Subject("onboarding_case", sensed["case_id"], sensed["case_name"]),
            payload={
                "edd_memo": memo,
                "corroboration": corroborated,
                "gaps": missing,
                "sow_claim": sensed["sow_claim"],
                "sof_claim": sensed["sof_claim"],
                "aml_risk_tier": risk_tier,
            },
            evidence={
                "aml_risk_tier": risk_tier,
                "n_corroborated": len(corroborated),
                "n_gaps": len(missing),
            },
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Store the EDD memo in the case proposal and advance edd_status to edd_pending."""
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}

        payload = recommendation.modified_payload or recommendation.payload or {}
        proposal = dict(case.proposal or {})
        proposal["edd_memo"] = payload.get("edd_memo", "")
        proposal["edd_corroboration"] = payload.get("corroboration", {})
        proposal["edd_gaps"] = payload.get("gaps", [])
        case.proposal = proposal
        case.edd_status = EDDStatus.EDD_PENDING.value
        await s.flush()
        return {
            "executed": True,
            "note": f"EDD memorandum stored for {case.prospect_name}; status: edd_pending.",
            "gaps": payload.get("gaps", []),
        }
