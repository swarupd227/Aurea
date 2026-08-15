"""Adverse Media & PEP Screener Agent (L200 A3). Tier 2 · Scheduled.

Screens every account party — owners, trustees, POA holders, beneficial owners — against
OFAC/SDN, PEP databases, and adverse media feeds. Generates a disposition log entry per
party. PEP hits auto-trigger EDD; sanctions hits block the case. Runs on new onboarding
cases and on a periodic schedule for continuous rescreening.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.screening import screen_parties
from app.core.db import utcnow
from app.models.enums import AgentKey, AMLRiskTier, AutonomyTier, EDDStatus, SurveillanceSeverity
from app.models.governance import SurveillanceFlag
from app.models.onboarding import BeneficialOwner, OnboardingCase


class AdverseMediaPEPScreenerAgent(BaseAgent):
    key = AgentKey.ADVERSE_MEDIA_PEP
    name = "Adverse Media & PEP Screener"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        # When called on a specific case subject, screen that case only.
        if ctx.subject and ctx.subject.type == "onboarding_case":
            cases = [await s.get(OnboardingCase, ctx.subject.id)]
            cases = [c for c in cases if c and c.firm_id == ctx.firm.id]
        else:
            # Scheduled: scan all active (non-terminal) cases for the firm.
            cases = (
                await s.execute(
                    select(OnboardingCase).where(
                        OnboardingCase.firm_id == ctx.firm.id,
                        OnboardingCase.status.not_in(["approved", "rejected"]),
                    )
                )
            ).scalars().all()

        results = []
        for case in cases:
            intake = case.intake or {}
            # Collect all parties: prospect + associated parties + trustees + BOs
            parties = [case.prospect_name]
            parties += list(intake.get("associated_parties", []))
            parties += list(intake.get("poa_holders", []))

            bos = (
                await s.execute(select(BeneficialOwner).where(BeneficialOwner.case_id == case.id))
            ).scalars().all()
            parties += [bo.legal_name for bo in bos]
            parties = sorted(set(p for p in parties if p))

            screening = screen_parties(parties)
            # Enrich each party result with PEP and adverse-media categorisation.
            has_pep = any(
                h["category"] == "pep"
                for p in screening["parties"]
                for h in p["hits"]
            )
            has_adverse = any(
                h["category"] == "adverse_media"
                for p in screening["parties"]
                for h in p["hits"]
            )
            results.append({
                "case_id": str(case.id),
                "case_name": case.prospect_name,
                "parties": parties,
                "screening": screening,
                "has_pep": has_pep,
                "has_adverse_media": has_adverse,
                "n_parties": len(parties),
            })

        return {"results": results, "n_cases": len(results)}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed["results"]:
            return []

        drafts = []
        for r in sensed["results"]:
            screening = r["screening"]
            if screening["status"] == "clear":
                continue  # nothing to surface

            priority = 1 if screening["status"] == "blocked" else 2
            hits_summary = []
            for p in screening["parties"]:
                for h in p["hits"]:
                    hits_summary.append(
                        f"{p['subject']}: {h['category']} ({h['severity']}) — {h.get('note', '')}"
                    )

            edd_note = ""
            if r["has_pep"]:
                edd_note = " PEP identified — EDD and senior compliance approval required."
            if screening["status"] == "blocked":
                edd_note += " Sanctions match — onboarding must not proceed."

            rationale_prompt = (
                f"Adverse media & PEP screening result for {r['case_name']}. "
                f"Status: {screening['status']}. Hits: {hits_summary}. "
                f"{'PEP identified — EDD mandatory.' if r['has_pep'] else ''} "
                f"{'Adverse media detected.' if r['has_adverse_media'] else ''} "
                "Summarise the screening findings and recommended compliance action. "
                "Be precise about which parties triggered hits and what due diligence is required."
            )
            fallback = (
                f"Screening of {r['n_parties']} parties for {r['case_name']}: "
                f"status {screening['status']}, {screening['n_hits']} hit(s). {edd_note}"
            )
            rationale = await narrate(ctx, task="compliance", system=firm_voice(ctx),
                                      prompt=rationale_prompt, fallback=fallback)

            drafts.append(RecommendationDraft(
                title=f"Screening alert — {r['case_name']}",
                summary=(
                    f"{screening['n_hits']} hit(s) across {r['n_parties']} parties: "
                    f"status {screening['status']}.{edd_note}"
                ),
                rationale=rationale,
                confidence=0.92,
                priority=priority,
                subject=Subject("onboarding_case", r["case_id"], r["case_name"]),
                payload={
                    "screening": screening,
                    "has_pep": r["has_pep"],
                    "has_adverse_media": r["has_adverse_media"],
                    "parties_screened": r["parties"],
                },
                evidence={
                    "screening_status": screening["status"],
                    "n_hits": screening["n_hits"],
                    "n_parties": r["n_parties"],
                },
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Persist screening log, set EDD status, and raise a SurveillanceFlag for confirmed hits."""
        s = ctx.session
        payload = recommendation.modified_payload or recommendation.payload or {}
        screening = payload.get("screening", {})
        case_id = recommendation.subject_id
        case = await s.get(OnboardingCase, case_id)
        if not case:
            return {"executed": False, "note": "Case not found."}

        # Build per-party disposition log entries.
        log_entries = []
        for p in screening.get("parties", []):
            entry = {
                "party": p["subject"],
                "status": p["status"],
                "hits": p["hits"],
                "disposition_note": "Screened by Adverse Media & PEP Screener agent.",
                "screened_at": utcnow().isoformat(),
                "provider": screening.get("provider", "World-Check (mock)"),
            }
            log_entries.append(entry)
        case.screening_log = log_entries

        # Escalate EDD status if PEP found.
        if payload.get("has_pep"):
            case.edd_status = EDDStatus.EDD_PENDING.value

        # Raise SurveillanceFlag for blocking or review hits.
        if screening.get("status") in ("blocked", "review"):
            severity = (
                SurveillanceSeverity.HIGH if screening["status"] == "blocked"
                else SurveillanceSeverity.MEDIUM
            )
            # SurveillanceFlag has no run/subject columns — the case is carried in
            # `attributes`, and the finding text lives in `finding`.
            s.add(SurveillanceFlag(
                firm_id=ctx.firm.id,
                recommendation_id=recommendation.id,
                target_agent_key=str(self.key),
                severity=severity,
                category="aml_screening",
                kind="screening_hit",
                finding=(
                    f"Screening hit(s) on {case.prospect_name}: "
                    f"{screening['n_hits']} match(es), status {screening['status']}."
                ),
                resolved=False,
                attributes={
                    "screening_status": screening["status"],
                    "n_hits": screening["n_hits"],
                    "has_pep": payload.get("has_pep", False),
                    "subject_type": "onboarding_case",
                    "subject_id": str(case.id),
                    "prospect_name": case.prospect_name,
                },
            ))
        await s.flush()
        return {
            "executed": True,
            "log_entries": len(log_entries),
            "edd_triggered": payload.get("has_pep", False),
            "note": f"Screening log updated for {case.prospect_name}.",
        }
