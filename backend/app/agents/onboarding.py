"""Onboarding · KYC · AML agent (spec Table 8) — deep implementation. Tier 2.

sense:  extract every uploaded document via document intelligence; screen the applicant and
        all associated parties (e.g. trustees) for AML/CFT.
think:  draft a suitability profile and mandate set-up grounded in the firm's playbook; surface
        every exception that needs a compliance decision (never auto-clears them).
act:    on compliance approval, materialise the prospect into the client brain as golden
        records (person / entity / household / mandate / account), KYC verified.
"""
from __future__ import annotations

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import knowledge
from app.aurea_core.document_intel import DOC_LABELS, extract
from app.aurea_core.screening import screen_parties
from app.core.db import utcnow
from app.models.enums import (
    AgentKey, AutonomyTier, EntityType, MandateType, OnboardingStatus,
)
from app.models.graph import Account, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import User
from app.models.onboarding import OnboardingCase, OnboardingDocument
from app.models.portfolio import ModelPortfolio

RISK_TO_MODEL = {"conservative": "Balanced", "balanced": "Balanced", "growth": "Growth", "aggressive": "Growth"}


class OnboardingAgent(BaseAgent):
    key = AgentKey.ONBOARDING_KYC_AML
    name = "Onboarding · KYC · AML"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_2

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id) if ctx.subject.id else None
        if case is None:
            return {"applicable": False}

        docs = (
            await s.execute(select(OnboardingDocument).where(OnboardingDocument.case_id == case.id))
        ).scalars().all()

        # Document intelligence: extract + persist structured fields with confidence.
        extracted_docs = []
        trustees: list[str] = []
        for d in docs:
            res = extract(d.doc_type, d.raw_text)
            d.extracted = res.fields
            d.field_confidence = res.field_confidence
            d.confidence = res.confidence
            d.verified = res.confidence >= 0.9 and not res.missing
            if d.doc_type == "trust_deed":
                trustees.extend(res.fields.get("trustees", []) or [])
            extracted_docs.append({
                "id": str(d.id), "doc_type": d.doc_type, "label": DOC_LABELS.get(d.doc_type, d.doc_type),
                "filename": d.filename, "fields": res.fields, "field_confidence": res.field_confidence,
                "confidence": res.confidence, "missing": res.missing, "low_confidence": res.low_confidence,
            })

        # AML/CFT screening across the applicant and associated parties.
        parties = [case.prospect_name]
        parties += [p for p in (case.intake or {}).get("associated_parties", [])]
        parties += trustees
        screening = screen_parties(sorted(set(p for p in parties if p)))
        case.screening = screening
        case.status = OnboardingStatus.SCREENING

        await s.flush()
        return {
            "applicable": True,
            "case": {"id": str(case.id), "name": case.prospect_name, "is_entity": case.is_entity,
                     "entity_type": case.entity_type, "segment": case.segment, "intake": case.intake},
            "documents": extracted_docs,
            "screening": screening,
            "trustees": trustees,
        }

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        s = ctx.session
        case = sensed["case"]
        intake = case["intake"] or {}
        screening = sensed["screening"]
        docs = sensed["documents"]

        # Suitability draft.
        risk_profile = (intake.get("risk_profile") or "balanced").lower()
        mandate_type = MandateType.ADVISORY if intake.get("mandate_preference") != "discretionary" else MandateType.DISCRETIONARY
        model_name = RISK_TO_MODEL.get(risk_profile, "Balanced")
        model = (
            await s.execute(
                select(ModelPortfolio).where(ModelPortfolio.firm_id == ctx.firm.id,
                                             ModelPortfolio.name.ilike(f"%{model_name}%"))
            )
        ).scalars().first()

        proposal = {
            "suitability": {
                "risk_profile": risk_profile,
                "objectives": intake.get("objectives", []),
                "time_horizon_years": intake.get("time_horizon_years"),
                "capacity_for_loss": intake.get("capacity_for_loss", "medium"),
            },
            "mandate": {
                "mandate_type": mandate_type.value,
                "model_portfolio": model.name if model else model_name,
                "model_portfolio_id": str(model.id) if model else None,
                "constraints": {"cgt_budget": intake.get("cgt_budget", 10000),
                                "values_exclusions": intake.get("values_exclusions", [])},
            },
            "segment": case["segment"],
        }

        # Exceptions — escalate, never auto-clear.
        exceptions: list[str] = []
        if screening["status"] == "blocked":
            exceptions.append("Sanctions match — onboarding must not proceed without compliance escalation.")
        elif screening["status"] == "review":
            exceptions.append("PEP / adverse-media hit — enhanced due diligence required before approval.")
        required = {"passport", "drivers_licence"} if not case["is_entity"] else {"trust_deed"}
        present = {d["doc_type"] for d in docs}
        for missing_doc in required - present:
            exceptions.append(f"Missing required document: {DOC_LABELS.get(missing_doc, missing_doc)}.")
        for d in docs:
            if d["missing"]:
                exceptions.append(f"{d['label']}: could not extract {', '.join(d['missing'])}.")
            if d["low_confidence"]:
                exceptions.append(f"{d['label']}: low-confidence field(s) {', '.join(d['low_confidence'])} — verify.")
        if not intake.get("source_of_wealth"):
            exceptions.append("Source of wealth not documented.")

        # Persist proposal + exceptions to the case and move to review.
        case_obj = await s.get(OnboardingCase, ctx.subject.id)
        case_obj.proposal = proposal
        case_obj.exceptions = exceptions
        case_obj.status = OnboardingStatus.REVIEW
        await s.flush()

        citations = await knowledge.retrieve(
            ctx.session, ctx.firm.id, "onboarding suitability KYC values exclusions mandate", k=2
        )

        # Confidence reflects screening + document quality.
        doc_conf = sum(d["confidence"] for d in docs) / len(docs) if docs else 0.6
        screen_penalty = 0.4 if screening["status"] == "blocked" else (0.75 if screening["status"] == "review" else 1.0)
        confidence = round(min(doc_conf, 0.97) * screen_penalty, 3)

        rationale = await self._rationale(ctx, case, proposal, screening, docs, exceptions, citations)

        title = f"Onboard {case['name']} — {model_name} {mandate_type.value} mandate"
        status_word = {"clear": "AML clear", "review": "AML review needed", "blocked": "AML BLOCKED"}[screening["status"]]
        summary = (
            f"{len(docs)} document(s) extracted, {status_word}; "
            + (f"{len(exceptions)} exception(s) for compliance." if exceptions else "ready for compliance sign-off.")
        )

        return [RecommendationDraft(
            title=title, summary=summary, rationale=rationale, confidence=confidence,
            priority=1 if screening["status"] == "blocked" else (2 if exceptions else 3),
            subject=Subject("onboarding_case", ctx.subject.id, case["name"]),
            payload={"proposal": proposal, "exceptions": exceptions, "documents": docs,
                     "screening_status": screening["status"], "screening": screening},
            evidence={"data_confidence": confidence, "n_documents": len(docs),
                      "screening_status": screening["status"], "n_screening_hits": screening["n_hits"]},
            citations=citations,
        )]

    async def _rationale(self, ctx, case, proposal, screening, docs, exceptions, citations) -> str:
        hits = [h for p in screening["parties"] for h in p["hits"]]
        fallback = (
            f"Onboarding pack prepared for {case['name']}. Identity and supporting documents were "
            f"extracted via document intelligence ({len(docs)} document(s)). AML/CFT screening status: "
            f"{screening['status']}"
            + (f" — {len(hits)} hit(s) requiring enhanced due diligence." if hits else " — no hits.")
            + f" A {proposal['mandate']['model_portfolio']} {proposal['mandate']['mandate_type']} mandate "
            "is proposed based on the stated risk profile. "
            + (f"{len(exceptions)} exception(s) require a compliance decision; the agent has escalated "
               "rather than cleared them." if exceptions else "No exceptions — ready for sign-off.")
        )
        prompt = (
            f"Summarise an onboarding & KYC pack for compliance sign-off. Applicant: {case['name']} "
            f"({'entity' if case['is_entity'] else 'individual'}, segment {case['segment']}). "
            f"AML status: {screening['status']}; hits: {hits}. Proposed mandate: "
            f"{proposal['mandate']}. Exceptions (must be decided by a human, not auto-cleared): "
            f"{exceptions}. Be precise; do not clear any exception yourself."
        )
        return await narrate(ctx, task="advice", system=firm_voice(ctx), prompt=prompt, fallback=fallback)

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """On compliance approval, materialise the prospect into the client brain."""
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id)
        if case is None:
            return {"executed": False, "note": "Case not found."}
        if case.status == OnboardingStatus.APPROVED:
            return {"executed": True, "note": "Already materialised.", **case.materialized}

        payload = recommendation.modified_payload or recommendation.payload or {}
        proposal = payload.get("proposal", case.proposal or {})
        mandate_cfg = proposal.get("mandate", {})

        household = Household(firm_id=ctx.firm.id, name=case.prospect_name,
                              segment=case.segment, values={})
        s.add(household)
        await s.flush()

        created = {"household_id": str(household.id)}
        owner_person_id = None
        owner_entity_id = None

        if case.is_entity:
            entity = LegalEntity(
                firm_id=ctx.firm.id, household_id=household.id, name=case.prospect_name,
                entity_type=EntityType(case.entity_type or "trust"),
                governance={"trustees": (case.intake or {}).get("associated_parties", [])},
                impact_objectives={},
            )
            s.add(entity)
            await s.flush()
            owner_entity_id = entity.id
            created["entity_id"] = str(entity.id)
        else:
            person = Person(
                firm_id=ctx.firm.id, household_id=household.id, full_name=case.prospect_name,
                preferred_name=case.prospect_name.split()[0], email=(case.intake or {}).get("email"),
                segment=case.segment,
                kyc={"status": "verified", "id_verified": True, "aml_screened": True,
                     "screening_status": (case.screening or {}).get("status"), "as_of": utcnow().isoformat()},
                profile={"risk_profile": proposal.get("suitability", {}).get("risk_profile")},
            )
            s.add(person)
            await s.flush()
            owner_person_id = person.id
            created["person_id"] = str(person.id)

        mandate = Mandate(
            firm_id=ctx.firm.id, person_id=owner_person_id, entity_id=owner_entity_id,
            name=f"{case.prospect_name} — {mandate_cfg.get('model_portfolio', 'Balanced')}",
            mandate_type=MandateType(mandate_cfg.get("mandate_type", "advisory")),
            suitability=proposal.get("suitability", {}),
            constraints=mandate_cfg.get("constraints", {}),
            model_portfolio_id=mandate_cfg.get("model_portfolio_id"),
        )
        s.add(mandate)
        await s.flush()
        created["mandate_id"] = str(mandate.id)

        account = Account(firm_id=ctx.firm.id, mandate_id=mandate.id,
                          name=f"{case.prospect_name.split()[0]} Account", custodian="BNY Pershing",
                          currency=ctx.firm.base_currency, cash_balance=0,
                          lineage={"source": "onboarding", "as_of": utcnow().isoformat()})
        s.add(account)
        await s.flush()
        created["account_id"] = str(account.id)

        # Link the firm's adviser to the new relationship.
        adviser = (
            await s.execute(select(User).where(User.firm_id == ctx.firm.id, User.role == "adviser"))
        ).scalars().first()
        if adviser:
            s.add(RelationshipEdge(
                firm_id=ctx.firm.id, kind="adviser", from_type="user", from_id=adviser.id,
                to_type="entity" if owner_entity_id else "person",
                to_id=owner_entity_id or owner_person_id,
            ))

        case.status = OnboardingStatus.APPROVED
        case.materialized = created
        await s.flush()
        return {"executed": True, "note": f"{case.prospect_name} materialised into the client brain as golden records.",
                **created}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        """Undo materialisation — delete the created graph nodes and reopen the case."""
        s = ctx.session
        case = await s.get(OnboardingCase, ctx.subject.id)
        if case is None or not case.materialized:
            return {"reversed": False, "note": "Nothing to reverse."}
        refs = case.materialized
        # Delete in dependency order; cascades handle children.
        for model, key in ((Account, "account_id"), (Mandate, "mandate_id"),
                           (Person, "person_id"), (LegalEntity, "entity_id"), (Household, "household_id")):
            if refs.get(key):
                obj = await s.get(model, refs[key])
                if obj:
                    await s.delete(obj)
        case.materialized = {}
        case.status = OnboardingStatus.REVIEW
        await s.flush()
        return {"reversed": True, "note": f"Reversed onboarding of {case.prospect_name}; "
                "graph nodes removed and the case reopened for compliance."}
