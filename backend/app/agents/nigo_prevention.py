"""NIGO Prevention Agent (L200 A1 enhanced). Tier 2.

Triggered on document upload to an onboarding case. Checks the package against the one
document matrix (aurea_core/document_matrix.py), computes a pre-submission readiness score (0–100),
and flags NIGO risks before the package is submitted — turning reject-and-rework into
catch-and-fix.

L200 target: NIGO rate <5% for digitised flows (vs 20–40% for paper).
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import document_matrix
from app.aurea_core.document_intel import DOC_LABELS
from app.models.enums import AgentKey, AutonomyTier, NIGORootCause
from app.models.onboarding import OnboardingCase, OnboardingDocument


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

        docs = (
            await s.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
        ).scalars().all()
        present_docs = {d.doc_type for d in docs}

        check = await document_matrix.check_case(s, case, ctx.firm)
        reg_type = check["registration_type"]

        return {
            "applicable": True,
            "case_id": str(case.id),
            "case_name": case.prospect_name,
            "registration_type": reg_type,
            "required_docs": [r["label"] for r in check["requirements"]["documents"]],
            "present_docs": sorted(present_docs),
            "matrix_issues": check["issues"],
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
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        reg_type = sensed["registration_type"]
        # The one matrix's check: documents, registration detail, signatures, dates, tenancy and shares.
        issues: list[dict] = [dict(i) for i in sensed["matrix_issues"]]

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
