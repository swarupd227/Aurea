"""NIGO Prevention Agent (L200 A1 enhanced). Tier 2.

Triggered on document upload to an onboarding case. Validates documents against the
registration-type document matrix, computes a pre-submission readiness score (0–100),
and flags NIGO risks before the package is submitted — turning reject-and-rework into
catch-and-fix.

L200 target: NIGO rate <5% for digitised flows (vs 20–40% for paper).
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.document_intel import DOC_LABELS
from app.models.enums import AgentKey, AutonomyTier, NIGORootCause
from app.models.onboarding import BeneficialOwner, OnboardingCase, OnboardingDocument

# Required document sets by registration type.
_DOC_MATRIX: dict[str, set[str]] = {
    "individual":         {"passport", "drivers_licence"},
    "joint_jtwros":       {"passport", "drivers_licence"},
    "joint_tic":          {"passport", "drivers_licence"},
    "traditional_ira":    {"passport", "drivers_licence"},
    "roth_ira":           {"passport", "drivers_licence"},
    "employer_rollover":  {"passport", "drivers_licence"},
    "trust":              {"trust_deed", "passport"},
    "entity_llc":         {"trust_deed", "passport"},          # trust_deed covers entity docs
    "entity_corp":        {"trust_deed", "passport"},
    "entity_partnership": {"trust_deed", "passport"},
    "custodial_utma":     {"passport", "drivers_licence"},
    "custodial_ugma":     {"passport", "drivers_licence"},
    "estate_inherited":   {"passport"},
}

# Registration types that require beneficial owner certification.
_BO_REQUIRED_TYPES = {"entity_llc", "entity_corp", "entity_partnership", "trust"}

# Registration types that require beneficiary designation.
_BENEFICIARY_REQUIRED_TYPES = {
    "traditional_ira", "roth_ira", "employer_rollover", "custodial_utma", "custodial_ugma"
}

# Registration types that require rollover PTE 2020-02 documentation.
_PTE_REQUIRED_TYPES = {"employer_rollover", "traditional_ira", "roth_ira"}


def _readiness_score(issues: list[dict]) -> int:
    """Compute 0–100 score. Each issue deducts points based on severity."""
    severity_weight = {"critical": 20, "high": 10, "medium": 5, "low": 2}
    total_deduction = sum(severity_weight.get(i["severity"], 5) for i in issues)
    return max(0, 100 - total_deduction)


class NIGOPreventionAgent(BaseAgent):
    key = AgentKey.NIGO_PREVENTION
    name = "NIGO Prevention"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject else None
        if not case or case.firm_id != ctx.firm.id:
            return {"applicable": False}

        reg_type = getattr(case, "registration_type", None) or (
            "trust" if getattr(case, "is_entity", False) else "individual"
        )
        required_docs = _DOC_MATRIX.get(reg_type, {"passport"})

        docs = (
            await s.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
        ).scalars().all()
        present_docs = {d.doc_type for d in docs}

        bos = (
            await s.execute(select(BeneficialOwner).where(BeneficialOwner.case_id == case.id))
        ).scalars().all()

        intake = case.intake or {}

        return {
            "applicable": True,
            "case_id": str(case.id),
            "case_name": case.prospect_name,
            "registration_type": reg_type,
            "required_docs": list(required_docs),
            "present_docs": list(present_docs),
            "docs": [
                {
                    "doc_type": d.doc_type,
                    "filename": d.filename,
                    "confidence": float(d.confidence or 0),
                    "verified": d.verified,
                    "extracted": d.extracted or {},
                    "field_confidence": d.field_confidence or {},
                }
                for d in docs
            ],
            "n_bos": len(bos),
            "has_beneficiary": bool(intake.get("beneficiary_name") or intake.get("beneficiaries")),
            "has_sow": bool(intake.get("source_of_wealth")),
            "intake": intake,
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        reg_type = sensed["registration_type"]
        required = set(sensed["required_docs"])
        present = set(sensed["present_docs"])
        issues: list[dict] = []

        # Missing required documents.
        for missing in required - present:
            issues.append({
                "root_cause": NIGORootCause.MISSING_ID.value,
                "severity": "critical",
                "description": f"Missing required document: {DOC_LABELS.get(missing, missing)}.",
                "field": missing,
            })

        # Low-confidence fields in uploaded documents.
        for doc in sensed["docs"]:
            for field, conf in (doc["field_confidence"] or {}).items():
                if conf < 0.7:
                    issues.append({
                        "root_cause": NIGORootCause.MISSING_SIGNATURE.value
                        if "signature" in field else NIGORootCause.STALE_FORM.value,
                        "severity": "high",
                        "description": f"{DOC_LABELS.get(doc['doc_type'], doc['doc_type'])}: "
                                       f"low confidence on '{field}' ({int(conf*100)}%).",
                        "field": field,
                    })

        # Beneficial owner completeness for entity accounts.
        if reg_type in _BO_REQUIRED_TYPES and sensed["n_bos"] == 0:
            issues.append({
                "root_cause": NIGORootCause.BENEFICIAL_OWNER_INCOMPLETE.value,
                "severity": "critical",
                "description": "Entity account requires beneficial owner certification "
                               "(CDD Rule — 25%+ owners + control person).",
                "field": "beneficial_owners",
            })

        # Beneficiary check for IRA / custodial accounts.
        if reg_type in _BENEFICIARY_REQUIRED_TYPES and not sensed["has_beneficiary"]:
            issues.append({
                "root_cause": NIGORootCause.MISSING_BENEFICIARY.value,
                "severity": "high",
                "description": "Beneficiary designation required for IRA/custodial accounts.",
                "field": "beneficiary",
            })

        # PTE 2020-02 for rollover accounts.
        if reg_type in _PTE_REQUIRED_TYPES and not sensed["has_sow"]:
            issues.append({
                "root_cause": NIGORootCause.STALE_FORM.value,
                "severity": "medium",
                "description": "Rollover account — source of wealth/funds not documented. "
                               "Required for PTE 2020-02 rationale.",
                "field": "source_of_wealth",
            })

        score = _readiness_score(issues)
        critical_count = sum(1 for i in issues if i["severity"] == "critical")
        priority = 1 if critical_count > 0 else (2 if issues else 4)

        fallback = (
            f"Pre-submission readiness check for {sensed['case_name']} "
            f"({reg_type}): score {score}/100, {len(issues)} issue(s)."
            + (f" Critical: {critical_count}." if critical_count else "")
        )
        prompt = (
            f"Summarise the pre-submission NIGO prevention check for {sensed['case_name']} "
            f"(registration type: {reg_type}). Readiness score: {score}/100. "
            f"Issues found: {issues}. "
            "Give a clear, actionable summary for the operations team — what must be fixed before submission."
        )
        rationale = await narrate(ctx, task="operations", system=firm_voice(ctx),
                                  prompt=prompt, fallback=fallback)

        return [RecommendationDraft(
            title=f"NIGO check — {sensed['case_name']} — score {score}/100",
            summary=(
                f"Readiness {score}/100: {len(issues)} issue(s)"
                + (f", {critical_count} critical" if critical_count else "")
                + f". Registration type: {reg_type}."
            ),
            rationale=rationale,
            confidence=0.95,
            priority=priority,
            subject=Subject("onboarding_case", sensed["case_id"], sensed["case_name"]),
            payload={
                "readiness_score": score,
                "issues": issues,
                "registration_type": reg_type,
                "required_docs": sensed["required_docs"],
                "present_docs": sensed["present_docs"],
            },
            evidence={
                "readiness_score": score,
                "n_issues": len(issues),
                "n_critical": critical_count,
                "registration_type": reg_type,
            },
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Persist readiness score and NIGO root-cause to the case."""
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}

        payload = recommendation.modified_payload or recommendation.payload or {}
        issues = payload.get("issues", [])
        score = payload.get("readiness_score", 100)

        case.readiness_score = score
        if issues:
            case.nigo_flag = True
            # Use the most severe issue's root cause as the primary.
            for sev in ("critical", "high", "medium", "low"):
                primary = next((i for i in issues if i["severity"] == sev), None)
                if primary:
                    case.nigo_root_cause = primary["root_cause"]
                    case.nigo_reason = "; ".join(i["description"] for i in issues)
                    break
        else:
            case.nigo_flag = False
            case.nigo_root_cause = None
            case.nigo_reason = None

        await s.flush()
        return {
            "executed": True,
            "readiness_score": score,
            "nigo_flag": case.nigo_flag,
            "nigo_root_cause": case.nigo_root_cause,
            "note": f"NIGO check complete for {case.prospect_name}.",
        }
