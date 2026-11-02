"""A2 — CIP Identity Verifier Agent.  Tier 2 (HITL for review/failed outcomes).

Runs identity verification for every person or entity on the account whose identity the CIP rule reaches: owners,
joint owners, trustees, the control person and beneficial owners, signers, attorneys, a minor's custodian and an
executor. Each is verified on their own data, documentary where an identity document is on file and
non-documentary otherwise, and the case is verified only when every one of them is. The identity adapter is a mock
(Socure-shaped) unless a provider is configured.

L200-1 §5.1: verification "within a reasonable time" of opening. Once an account is open, a party not verified
inside the firm's clock is a KYC exception, to restrict or close, which is a person's decision.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.integrations import identity as id_integration
from app.core.db import utcnow
from app.models.enums import AgentKey, AutonomyTier, PartyRole
from app.models.onboarding import OnboardingCase, OnboardingParty

# Whose identity is verified. A beneficiary of an IRA is not a customer; a beneficial owner and a control person are.
CIP_ROLES = {PartyRole.OWNER, PartyRole.JOINT_OWNER, PartyRole.TRUSTEE, PartyRole.BENEFICIAL_OWNER, PartyRole.CONTROL_PERSON,
             PartyRole.AUTHORISED_SIGNER, PartyRole.POA_HOLDER, PartyRole.CUSTODIAN, PartyRole.EXECUTOR}
DOCUMENTARY = {"passport", "licence", "drivers_licence", "national_id"}
CIP_DAYS = 30   # how long after opening an unverified party may stand before it is an exception


def method_for(party) -> str:
    return "documentary" if (party.id_type or "").lower() in DOCUMENTARY else "non_documentary"


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
        parties = (await s.execute(select(OnboardingParty).where(OnboardingParty.case_id == case.id))).scalars().all()
        subjects = [{"party_id": str(p.id), "name": p.legal_name, "role": p.role, "dob": p.dob, "address": p.address,
                     "id_number": p.id_number, "method": method_for(p)} for p in parties if p.role in CIP_ROLES]
        if not subjects:
            # A case with no parties recorded yet: the prospect, from the intake, as before.
            subjects = [{"party_id": None, "name": case.prospect_name, "role": "prospect", "dob": intake.get("dob"),
                         "address": intake.get("address"), "id_number": intake.get("id_number"), "method": "non_documentary"}]
        return {"applicable": True, "case_id": str(case.id), "prospect_name": case.prospect_name,
                "registration_type": getattr(case, "registration_type", None), "subjects": subjects}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []

        adapter = id_integration.get_adapter(ctx.config.get("identity_provider") if ctx.config else None)
        results = []
        for subj in sensed["subjects"]:
            r = adapter.verify(full_name=subj["name"], dob=subj.get("dob"), address=subj.get("address"),
                               id_number=subj.get("id_number"), registration_type=sensed.get("registration_type"))
            results.append({**{k: subj[k] for k in ("party_id", "name", "role", "method")}, "status": r.status, "score": r.score,
                            "flags": r.flags, "reference_id": r.reference_id, "provider": r.provider})
        statuses = {r["status"] for r in results}
        overall = "failed" if "failed" in statuses else "review" if "review" in statuses else "verified"
        worst = min(results, key=lambda r: r["score"])
        lines = [f"{r['name']} ({r['role'].replace('_', ' ')}, {r['method'].replace('_', '-')}): {r['status']}, {r['score']:.0%}"
                 + (f", flags {', '.join(r['flags'])}" if r["flags"] else "") for r in results]
        fallback = f"CIP {overall.upper()} for {sensed['prospect_name']}: " + "; ".join(lines) + "."
        prompt = (f"Summarise the identity verification of each party on {sensed['prospect_name']} for the compliance officer: "
                  + "; ".join(lines) + ". Say which party needs what next. The provider is a mock.")
        rationale = await narrate(ctx, task="compliance", system=firm_voice(ctx), prompt=prompt, fallback=fallback)
        return [RecommendationDraft(
            title=f"CIP {overall.upper()} — {sensed['prospect_name']}",
            summary=f"{len(results)} part(ies): " + ", ".join(f"{r['name']} {r['status']}" for r in results),
            rationale=rationale,
            confidence=worst["score"],
            priority={"verified": 4, "review": 2, "failed": 1}[overall],
            subject=Subject("onboarding_case", sensed["case_id"], sensed["prospect_name"]),
            payload={"cip_status": overall, "cip_score": worst["score"], "cip_flags": sorted({f for r in results for f in r["flags"]}),
                     "cip_reference_id": worst["reference_id"], "provider": worst["provider"], "parties": results},
            evidence={"parties": len(results), "status": overall},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, recommendation.subject_id)
        if not case:
            return {"executed": False, "note": "Case not found."}
        payload = recommendation.modified_payload or recommendation.payload or {}
        now = utcnow()
        for r in payload.get("parties", []):
            if r.get("party_id"):
                party = await s.get(OnboardingParty, uuid.UUID(str(r["party_id"])))
                if party is not None and party.case_id == case.id:
                    party.cip_status, party.cip_checked_at = r["status"], now
        case.cip_status = payload.get("cip_status")
        case.cip_score = payload.get("cip_score")
        case.cip_flags = payload.get("cip_flags", [])
        case.cip_reference_id = payload.get("cip_reference_id")
        await s.flush()
        return {"executed": True, "cip_status": case.cip_status, "cip_score": case.cip_score, "flags": case.cip_flags,
                "parties": [{"name": r["name"], "status": r["status"]} for r in payload.get("parties", [])]}
