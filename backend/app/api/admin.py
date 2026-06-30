"""Admin API — firm configuration, agent governance, autonomy policies, research, models.

This is what makes Aurea a generic, configurable platform (build guideline #1)."""
from __future__ import annotations

import secrets
import uuid
from datetime import timezone
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import time as _time

from sqlalchemy import delete as sql_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.aurea_core import knowledge
from app.core.config import settings
from app.core.db import get_db, utcnow
from app.core.security import UserRole, hash_password, require_roles
from app.llm import usage as llm_usage
from app.llm.service import firm_llm_creds, llm_service
from app.models.enums import AgentKey, AutonomyTier, MandateType, RecommendationStatus
from app.models.governance import AgentRun, AutonomyChange, LedgerEntry, Recommendation
from app.models.graph import Household, Mandate, Person, RelationshipEdge
from app.models.identity import AuditEvent, User, UserInviteToken
from app.models.knowledge import ResearchChunk, ResearchDocument
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm, FirmSegment, MandateTypeConfig, NotificationConfig
from app.provenance import eval_gates

router = APIRouter(prefix="/api/admin", tags=["admin"])
AdminDep = Depends(require_roles(UserRole.ADMIN, UserRole.COMPLIANCE))


# ── Firm settings & branding ──────────────────────────────────────────────────
class FirmUpdate(BaseModel):
    name: str | None = None
    legal_name: str | None = None
    jurisdiction: str | None = None
    regulator: str | None = None
    base_currency: str | None = None
    branding: dict | None = None
    settings: dict | None = None
    model_config_json: dict | None = None


@router.get("/firm")
async def get_firm(user: User = AdminDep, firm: Firm = Depends(current_firm)):
    return {
        "id": str(firm.id), "slug": firm.slug, "name": firm.name, "legal_name": firm.legal_name,
        "jurisdiction": firm.jurisdiction, "regulator": firm.regulator,
        "base_currency": firm.base_currency, "branding": firm.branding, "settings": firm.settings,
        "model_config": firm.model_config_json,
        "model_defaults": {"advice": settings.model_advice, "narrative": settings.model_narrative,
                           "classify": settings.model_classify},
        "llm": _llm_status(firm),
    }


def _llm_status(firm: Firm) -> dict:
    cfg = firm.llm_config or {}
    return {
        "anthropic_configured": bool(cfg.get("anthropic_api_key")),
        "openai_configured": bool(cfg.get("openai_api_key")),
        "anthropic_from_env": bool(settings.anthropic_api_key) and not cfg.get("anthropic_api_key"),
        "enabled": llm_service.enabled(firm_llm_creds(firm)),
        "models": firm.model_config_json or {},
        "model_defaults": {"advice": settings.model_advice, "narrative": settings.model_narrative,
                           "classify": settings.model_classify},
    }


# ── LLM provider credentials (Admin only) ─────────────────────────────────────
class LLMConfigIn(BaseModel):
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None


@router.get("/llm")
async def get_llm(user: User = Depends(require_roles(UserRole.ADMIN)),
                  firm: Firm = Depends(current_firm)):
    return _llm_status(firm)


@router.put("/llm")
async def set_llm(
    body: LLMConfigIn, user: User = Depends(require_roles(UserRole.ADMIN)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Set provider keys for this firm. A blank string clears that key; an absent field is unchanged."""
    cfg = dict(firm.llm_config or {})
    for field in ("anthropic_api_key", "openai_api_key"):
        val = getattr(body, field)
        if val is None:
            continue
        val = val.strip()
        if val:
            cfg[field] = val
        else:
            cfg.pop(field, None)
    firm.llm_config = cfg
    await db.flush()
    return _llm_status(firm)


def _save_foundation(firm: Firm, patch: dict) -> dict:
    from app.core.foundation import DEFAULTS, policy as foundation_policy
    s = dict(firm.settings or {})
    f = dict(s.get("foundation") or {})
    for k, v in (patch or {}).items():
        if k in DEFAULTS:
            f[k] = v
    s["foundation"] = f
    firm.settings = s
    return foundation_policy(firm)


class PiiIn(BaseModel):
    enabled: bool


@router.put("/security/pii")
async def set_pii(body: PiiIn, user: User = Depends(require_roles(UserRole.ADMIN)),
                  firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    pol = _save_foundation(firm, {"pii_redaction": body.enabled})
    await db.flush()
    return {"pii_redaction": pol["pii_redaction"]}


class FoundationIn(BaseModel):
    policy: dict


@router.put("/foundation")
async def set_foundation(body: FoundationIn, user: User = Depends(require_roles(UserRole.ADMIN)),
                         firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Save the configurable foundation policy (model gateway, security, governance, eval, grounding)."""
    pol = _save_foundation(firm, body.policy)
    await db.flush()
    return pol


@router.get("/compliance")
async def get_compliance(user: User = AdminDep, firm: Firm = Depends(current_firm)):
    """The active regulatory framework + per-firm rule config (enable/disable, severity)."""
    from app.compliance.ontology import FRAMEWORKS, framework_for
    fw = framework_for(firm)
    cfg = (firm.settings or {}).get("compliance") or {}
    disabled = set(cfg.get("disabled") or [])
    sev = cfg.get("severity") or {}
    return {
        "regime": fw.regime, "name": fw.name, "version": fw.version, "authority": fw.authority,
        "available_regimes": list(FRAMEWORKS.keys()),
        "rules": [{
            "id": r.id, "code": r.code, "citation": r.citation, "title": r.title,
            "category": r.category, "severity": sev.get(r.id, r.severity),
            "default_severity": r.severity, "applies_to": list(r.applies_to),
            "enabled": r.id not in disabled, "description": r.description,
        } for r in fw.rules],
    }


class RuleConfigIn(BaseModel):
    rule_id: str
    enabled: bool | None = None
    severity: str | None = None


@router.put("/compliance/rule")
async def set_rule(body: RuleConfigIn, user: User = Depends(require_roles(UserRole.ADMIN)),
                   firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    s = dict(firm.settings or {})
    c = dict(s.get("compliance") or {})
    disabled = set(c.get("disabled") or [])
    sev = dict(c.get("severity") or {})
    if body.enabled is not None:
        disabled.discard(body.rule_id) if body.enabled else disabled.add(body.rule_id)
    if body.severity in ("high", "medium", "low"):
        sev[body.rule_id] = body.severity
    c["disabled"], c["severity"] = sorted(disabled), sev
    s["compliance"] = c
    firm.settings = s
    await db.flush()
    return {"ok": True}


# ── I4: Regulatory Rule Impact ────────────────────────────────────────────────

@router.get("/compliance/impact")
async def get_rule_impact(
    rule_code: str,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Return recommendations where rule_code (e.g. 'NZ-FMA-001') has triggered a finding."""
    from app.models.compliance import ComplianceCheck
    from app.models.governance import Recommendation

    checks = (await db.execute(
        select(ComplianceCheck).where(ComplianceCheck.firm_id == firm.id)
    )).scalars().all()

    affected_rec_ids: list[uuid.UUID] = []
    for check in checks:
        for finding in check.results or []:
            if finding.get("code") == rule_code and finding.get("status") in ("flag", "blocked"):
                if check.recommendation_id:
                    affected_rec_ids.append(check.recommendation_id)
                break

    affected_recs = []
    if affected_rec_ids:
        recs = (await db.execute(
            select(Recommendation).where(
                Recommendation.firm_id == firm.id,
                Recommendation.id.in_(affected_rec_ids),
            )
        )).scalars().all()
        affected_recs = [
            {"id": str(r.id), "title": r.title, "agent_key": r.agent_key,
             "status": r.status, "subject_label": r.subject_label}
            for r in recs
        ]

    return {
        "rule_code": rule_code,
        "affected_count": len(affected_recs),
        "affected_recommendations": affected_recs,
    }


# ── I7: Branding Editor ───────────────────────────────────────────────────────

class BrandingIn(BaseModel):
    primary: str | None = None
    accent: str | None = None
    logo_text: str | None = None
    tagline: str | None = None
    logo_url: str | None = None


@router.patch("/firm/branding")
async def update_branding(
    body: BrandingIn,
    user: User = AdminDep,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Update the firm's Canvas/Studio branding — colors, logo text, tagline."""
    from sqlalchemy.orm.attributes import flag_modified as _flag
    b = dict(firm.branding or {})
    for field in ("primary", "accent", "logo_text", "tagline", "logo_url"):
        val = getattr(body, field)
        if val is not None:
            b[field] = val
    firm.branding = b
    _flag(firm, "branding")
    await db.flush()
    return {"ok": True, "branding": b}


@router.get("/firm/branding")
async def get_branding(user: User = AdminDep, firm: Firm = Depends(current_firm)):
    """Return the current firm branding configuration."""
    return {"branding": firm.branding or {}}


@router.get("/usage")
async def ai_usage(user: User = AdminDep, firm: Firm = Depends(current_firm),
                   db: AsyncSession = Depends(get_db)):
    """AI-usage telemetry — calls, tokens, est. cost, model & agent mix, fallback rate."""
    return await llm_usage.summary(db, firm.id)


@router.get("/eval-gates")
async def get_eval_gates(user: User = AdminDep):
    """Golden eval gates for the rebalancing engine (regression / equivalence / behaviour)."""
    return eval_gates.run_gates()


@router.get("/foundation")
async def foundation(user: User = AdminDep, firm: Firm = Depends(current_firm),
                     db: AsyncSession = Depends(get_db)):
    """The six common-foundation pillars every agent inherits, with live status + metrics."""
    async def count(model, *where):
        stmt = select(func.count(model.id)).where(*where) if where else select(func.count(model.id))
        return (await db.execute(stmt)).scalar_one()

    ledger_entries = await count(LedgerEntry, LedgerEntry.firm_id == firm.id)
    total_runs = await count(AgentRun, AgentRun.firm_id == firm.id)
    decided = await count(Recommendation, Recommendation.firm_id == firm.id,
                          Recommendation.status != RecommendationStatus.PROPOSED)
    docs = await count(ResearchDocument, ResearchDocument.firm_id == firm.id)
    chunks = await count(ResearchChunk, ResearchChunk.firm_id == firm.id)

    gates = eval_gates.run_gates()
    usage = await llm_usage.summary(db, firm.id)
    llm = _llm_status(firm)
    pii = bool((firm.settings or {}).get("pii_redaction", True))
    hours = round(((total_runs + decided) * 25) / 60, 1)
    models = {**llm["model_defaults"], **(firm.model_config_json or {})}

    pillars = [
        {"key": "governance", "title": "Governance & guardrails", "status": "strong",
         "summary": "Policy, approvals and a full audit trail on every action.",
         "detail": f"{ledger_entries:,} hash-chained ledger entries · {decided} human decisions · autonomy tiers, surveillance & kill-switch.",
         "metrics": {"Ledger entries": f"{ledger_entries:,}", "Decisions": decided, "Tamper-evident": "Yes"}},
        {"key": "grounding", "title": "Grounding & context",
         "status": "strong" if docs > 0 else "partial",
         "summary": "Tuned to your data, research and documents — not a generic model.",
         "detail": (
             f"{docs} firm research document(s) in a {chunks}-chunk RAG store, cited on recommendations; "
             "agents reason over the governed client brain with house views injected into prompts."
             if docs > 0 else
             "No firm research documents uploaded yet. Add house views in Admin → Research to enable grounded advice."
         ),
         "metrics": {"Research docs": docs, "RAG chunks": chunks,
                     "RAG injection": "Active" if docs > 0 else "No docs loaded",
                     "House views cited": "Yes" if docs > 0 else "None uploaded"}},
        {"key": "eval", "title": "Eval & quality gates", "status": "strong" if gates["all_green"] else "attention",
         "summary": "Nothing ships until it passes evals — regression, equivalence, behaviour, groundedness.",
         "detail": f"{gates['passed']}/{gates['total']} golden gates green: rebalancing engine (regression, equivalence, behaviour, guardrails) + narrative groundedness check; adaptive autonomy narrows a tier on regression.",
         "metrics": {"Gates passing": f"{gates['passed']}/{gates['total']}", "Status": "Green" if gates["all_green"] else "Attention",
                     "Groundedness gate": "On"}},
        {"key": "model_gateway", "title": "Model gateway", "status": "strong" if llm["enabled"] else "partial",
         "summary": "Multi-model routing, fallback and token-cost governance.",
         "detail": f"Routing: advice→{models.get('advice')}, classify→{models.get('classify')}. Fallback: Anthropic → OpenAI → deterministic. {usage['calls']} calls metered.",
         "metrics": {"LLM connected": "Yes" if llm["enabled"] else "No", "Fallback rate": f"{usage['fallback_rate']:.0%}", "Calls metered": usage["calls"]}},
        {"key": "security", "title": "Security & compliance", "status": "strong" if pii else "partial",
         "summary": "PII redaction, access control, secret handling, regulatory alignment.",
         "detail": f"RBAC on every staff route · PII redaction {'ON' if pii else 'OFF'} ({usage['redacted_entities']} entities masked) · secrets & keys write-only/redacted · {firm.regulator}/{firm.jurisdiction} conduct alignment.",
         "metrics": {"RBAC": "On", "PII redaction": "On" if pii else "Off", "Entities masked": usage["redacted_entities"]}},
        {"key": "telemetry", "title": "Telemetry & ROI", "status": "strong" if usage["calls"] else "partial",
         "summary": "Adoption, throughput and outcome measurement, in the open.",
         "detail": f"{usage['calls']} model calls · {usage['total_tokens']:,} tokens · ~${usage['est_cost']:.2f} est. cost · {hours}h adviser time reclaimed.",
         "metrics": {"Model calls": usage["calls"], "Tokens": f"{usage['total_tokens']:,}", "Est. cost": f"${usage['est_cost']:.2f}", "Hours reclaimed": hours}},
    ]
    from app.core.foundation import policy as foundation_policy
    return {"pillars": pillars, "usage": usage, "eval": gates, "models": models,
            "model_defaults": llm["model_defaults"], "pii_redaction": pii, "llm_enabled": llm["enabled"],
            "policy": foundation_policy(firm)}


@router.post("/llm/test")
async def test_llm(user: User = Depends(require_roles(UserRole.ADMIN)),
                   firm: Firm = Depends(current_firm)):
    """Make a tiny real call to confirm the configured key works."""
    creds = firm_llm_creds(firm)
    if not llm_service.enabled(creds):
        return {"ok": False, "message": "No API key configured."}
    result = await llm_service.generate(
        task="classify", system="You are a connectivity test. Reply with one short word.",
        prompt="Reply with the single word: connected", firm_model_config=firm.model_config_json or {},
        creds=creds, max_tokens=20, fallback=lambda: "")
    if result.is_fallback or not result.text.strip():
        return {"ok": False, "message": "Call failed — check the key is valid."}
    return {"ok": True, "provider": result.provider, "model": result.model,
            "reply": result.text.strip()[:60], "usage": result.usage}


@router.patch("/firm")
async def update_firm(
    body: FirmUpdate, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    for field in ("name", "legal_name", "jurisdiction", "regulator", "base_currency"):
        val = getattr(body, field)
        if val is not None:
            setattr(firm, field, val)
    if body.branding is not None:
        firm.branding = {**(firm.branding or {}), **body.branding}
    if body.settings is not None:
        firm.settings = {**(firm.settings or {}), **body.settings}
    if body.model_config_json is not None:
        # Eval gate — if the firm requires it, a model change can't ship unless the golden gates are green.
        from app.core.foundation import policy as foundation_policy
        if foundation_policy(firm).get("enforce_eval_gate") and not eval_gates.run_gates()["all_green"]:
            raise HTTPException(status_code=409,
                                detail="Eval gates are not green — model/config change blocked by policy.")
        firm.model_config_json = {**(firm.model_config_json or {}), **body.model_config_json}
    await db.flush()
    return {"ok": True}


# ── Agent governance ──────────────────────────────────────────────────────────
class AgentConfigUpdate(BaseModel):
    enabled: bool | None = None
    default_tier: AutonomyTier | None = None
    paused: bool | None = None
    paused_reason: str | None = None
    schedule_cron: str | None = None
    schedule_enabled: bool | None = None
    config: dict | None = None


@router.get("/agents")
async def list_agent_configs(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))
    ).scalars().all()
    return [
        {"agent_key": c.agent_key, "enabled": c.enabled, "default_tier": c.default_tier,
         "paused": c.paused, "paused_reason": c.paused_reason,
         "schedule_cron": c.schedule_cron, "schedule_enabled": c.schedule_enabled,
         "config": c.config}
        for c in rows
    ]


@router.patch("/agents/{agent_key}")
async def update_agent_config(
    agent_key: AgentKey, body: AgentConfigUpdate, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    cfg = (
        await db.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == firm.id, AgentConfig.agent_key == agent_key
            )
        )
    ).scalar_one_or_none()
    if not cfg:
        cfg = AgentConfig(firm_id=firm.id, agent_key=agent_key)
        db.add(cfg)
    for field in ("enabled", "default_tier", "paused", "paused_reason", "schedule_cron", "schedule_enabled"):
        val = getattr(body, field)
        if val is not None:
            setattr(cfg, field, val)
    if body.config is not None:
        cfg.config = {**(cfg.config or {}), **body.config}
    # Clearing the pause is the kill-switch reset.
    if body.paused is False:
        cfg.paused_reason = None
    await db.flush()
    return {"ok": True}


# ── Bulk CSV client import ────────────────────────────────────────────────────
class CsvImportIn(BaseModel):
    csv_data: str = ""


@router.post("/clients/import")
async def bulk_import_clients(
    body: CsvImportIn,
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Accept JSON {csv_data: <csv text>} → create OnboardingCases."""
    import csv as _csv
    import io as _io
    from app.models.onboarding import OnboardingCase

    csv_data = body.csv_data
    reader = _csv.DictReader(_io.StringIO(csv_data.strip()))
    created = []
    for row in reader:
        name = (row.get("prospect_name") or row.get("name") or "").strip()
        if not name:
            continue
        case = OnboardingCase(
            firm_id=firm.id,
            prospect_name=name,
            segment=row.get("segment", "private_wealth").strip(),
            entity_type=row.get("entity_type") or None,
            notes=row.get("notes") or None,
            intake={"source": "bulk_import", "email": row.get("email", ""), "phone": row.get("phone", "")},
        )
        db.add(case)
        created.append(name)
    await db.flush()
    return {"imported": len(created), "names": created}


# ── Autonomy policies ─────────────────────────────────────────────────────────
class PolicyUpsert(BaseModel):
    agent_key: AgentKey
    mandate_type: MandateType | None = None
    tier: AutonomyTier
    guardrails: dict = {}
    rationale: str | None = None


@router.get("/policies")
async def list_policies(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(select(AutonomyPolicy).where(AutonomyPolicy.firm_id == firm.id))
    ).scalars().all()
    return [
        {"id": str(p.id), "agent_key": p.agent_key, "mandate_type": p.mandate_type,
         "tier": p.tier, "guardrails": p.guardrails, "rationale": p.rationale}
        for p in rows
    ]


@router.post("/policies")
async def upsert_policy(
    body: PolicyUpsert, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    existing = (
        await db.execute(
            select(AutonomyPolicy).where(
                AutonomyPolicy.firm_id == firm.id, AutonomyPolicy.agent_key == body.agent_key,
                AutonomyPolicy.mandate_type == body.mandate_type,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.tier = body.tier
        existing.guardrails = body.guardrails
        existing.rationale = body.rationale
    else:
        db.add(AutonomyPolicy(
            firm_id=firm.id, agent_key=body.agent_key, mandate_type=body.mandate_type,
            tier=body.tier, guardrails=body.guardrails, rationale=body.rationale,
        ))
    await db.flush()
    return {"ok": True}


@router.delete("/policies/{policy_id}")
async def delete_policy(
    policy_id: uuid.UUID, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    p = await db.get(AutonomyPolicy, policy_id)
    if not p or p.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Policy not found")
    await db.delete(p)
    return {"ok": True}


# ── Firm research (RAG) ───────────────────────────────────────────────────────
class ResearchIn(BaseModel):
    title: str
    doc_type: str = "house_view"
    author: str | None = None
    summary: str | None = None
    body: str
    tags: list[str] = []


@router.get("/research")
async def list_research(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (
        await db.execute(select(ResearchDocument).where(ResearchDocument.firm_id == firm.id))
    ).scalars().all()
    return [
        {"id": str(d.id), "title": d.title, "doc_type": d.doc_type, "author": d.author,
         "summary": d.summary, "tags": d.tags,
         "status": getattr(d, "status", "published"),
         "version": getattr(d, "version", 1),
         "published_by": getattr(d, "published_by", None),
         "published_at": d.published_at.isoformat() if getattr(d, "published_at", None) else None,
         "created_at": d.created_at.isoformat() if d.created_at else None}
        for d in rows
    ]


@router.post("/research/{doc_id}/submit")
async def submit_research(
    doc_id: uuid.UUID, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    doc = await db.get(ResearchDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(404, "Document not found.")
    doc.status = "under_review"
    await db.flush()
    return {"ok": True, "status": "under_review"}


@router.post("/research/{doc_id}/publish")
async def publish_research(
    doc_id: uuid.UUID, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    doc = await db.get(ResearchDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(404, "Document not found.")
    doc.status = "published"
    doc.published_by = user.email
    doc.published_at = utcnow()
    doc.version = (getattr(doc, "version", 0) or 0) + 1
    await db.flush()
    return {"ok": True, "status": "published", "version": doc.version}


@router.post("/research/{doc_id}/reject")
async def reject_research(
    doc_id: uuid.UUID, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    doc = await db.get(ResearchDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(404, "Document not found.")
    doc.status = "draft"
    await db.flush()
    return {"ok": True, "status": "draft"}


@router.post("/research")
async def add_research(
    body: ResearchIn, user: User = AdminDep,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    doc = ResearchDocument(
        firm_id=firm.id, title=body.title, doc_type=body.doc_type, author=body.author,
        summary=body.summary, body=body.body, tags=body.tags,
    )
    db.add(doc)
    await db.flush()
    n = await knowledge.ingest_document(db, doc)
    return {"id": str(doc.id), "chunks": n}


# ── User management ───────────────────────────────────────────────────────────

STAFF_ROLES = [r for r in UserRole if r != UserRole.CLIENT]


def _user_out(u: User) -> dict:
    return {
        "id": str(u.id), "email": u.email, "full_name": u.full_name,
        "role": u.role, "title": u.title, "is_active": u.is_active,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    }


def _make_token(db: AsyncSession, user: User, firm_id: uuid.UUID, token_type: str, days: int) -> UserInviteToken:
    tok = UserInviteToken(
        firm_id=firm_id, user_id=user.id,
        token=secrets.token_urlsafe(32),
        token_type=token_type,
        expires_at=utcnow() + timedelta(days=days),
    )
    db.add(tok)
    return tok


class UserCreate(BaseModel):
    email: str
    full_name: str
    role: UserRole = UserRole.ADVISER
    title: str | None = None


class UserUpdate(BaseModel):
    full_name: str | None = None
    role: UserRole | None = None
    title: str | None = None
    is_active: bool | None = None


@router.get("/users")
async def list_users(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (await db.execute(
        select(User).where(User.firm_id == firm.id).order_by(User.full_name)
    )).scalars().all()
    return [_user_out(u) for u in rows]


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    existing = (await db.execute(
        select(User).where(User.email == body.email.lower().strip())
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "A user with that email already exists.")
    new_user = User(
        firm_id=firm.id,
        email=body.email.lower().strip(),
        full_name=body.full_name,
        hashed_password=hash_password(secrets.token_urlsafe(32)),
        role=body.role,
        title=body.title,
        is_active=False,
    )
    db.add(new_user)
    await db.flush()
    tok = _make_token(db, new_user, firm.id, "invite", days=7)
    await db.flush()
    _audit_sync(db, firm.id, actor, "user.created", subject=new_user.email, detail={"role": body.role})
    invite_url = f"{settings.frontend_url}/accept-invite?token={tok.token}"
    import structlog
    structlog.get_logger().info("mock_email_invite", to=new_user.email, url=invite_url)
    return {**_user_out(new_user), "invite_token": tok.token, "invite_url": invite_url}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: UserUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    target = await db.get(User, user_id)
    if not target or target.firm_id != firm.id:
        raise HTTPException(404, "User not found.")
    if body.full_name is not None:
        target.full_name = body.full_name
    if body.role is not None:
        target.role = body.role
    if body.title is not None:
        target.title = body.title
    if body.is_active is not None:
        target.is_active = body.is_active
        _audit_sync(db, firm.id, actor, "user.deactivated" if not body.is_active else "user.activated", subject=target.email)
    return _user_out(target)


@router.delete("/users/{user_id}", status_code=200)
async def delete_user(
    user_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Soft-deactivate a user and immediately invalidate all their active sessions."""
    if user_id == actor.id:
        raise HTTPException(400, "You cannot deactivate your own account.")
    target = await db.get(User, user_id)
    if not target or target.firm_id != firm.id:
        raise HTTPException(404, "User not found.")
    target.is_active = False
    from app.core.security import _get_redis
    r = await _get_redis()
    ttl = settings.access_token_ttl_minutes * 60
    await r.setex(f"fl:{str(user_id)}", ttl, str(int(_time.time())))
    _audit_sync(db, firm.id, actor, "user.deactivated", subject=target.email)
    return {"ok": True}


@router.post("/users/{user_id}/resend-invite")
async def resend_invite(
    user_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    target = await db.get(User, user_id)
    if not target or target.firm_id != firm.id:
        raise HTTPException(404, "User not found.")
    tok = _make_token(db, target, firm.id, "invite", days=7)
    await db.flush()
    _audit_sync(db, firm.id, actor, "user.invite_resent", subject=target.email)
    return {"invite_token": tok.token}


@router.post("/users/{user_id}/reset-password")
async def admin_reset_password(
    user_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    target = await db.get(User, user_id)
    if not target or target.firm_id != firm.id:
        raise HTTPException(404, "User not found.")
    tok = _make_token(db, target, firm.id, "reset", days=1)
    await db.flush()
    _audit_sync(db, firm.id, actor, "user.password_reset_issued", subject=target.email)
    return {"reset_token": tok.token}


# ── Audit log ─────────────────────────────────────────────────────────────────

def _audit_sync(db: AsyncSession, firm_id: uuid.UUID, actor: User, event_type: str, subject: str | None = None, detail: dict | None = None) -> None:
    db.add(AuditEvent(
        firm_id=firm_id, actor_id=actor.id, actor_email=actor.email,
        event_type=event_type, subject=subject, detail=detail,
    ))


@router.get("/audit")
async def audit_log(
    limit: int = 100,
    event_type: str | None = None,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    rows = []

    # Access/admin events
    q = select(AuditEvent).where(AuditEvent.firm_id == firm.id)
    if event_type:
        q = q.where(AuditEvent.event_type == event_type)
    ae_rows = (await db.execute(q.order_by(AuditEvent.created_at.desc()).limit(limit))).scalars().all()
    for r in ae_rows:
        category = "client" if r.event_type.startswith("client.") else "access"
        rows.append({
            "ts": r.created_at.isoformat(), "category": category,
            "event_type": r.event_type, "actor": r.actor_email,
            "subject": r.subject, "detail": r.detail,
        })

    # Recommendation decisions from ledger (unless filtering by event_type)
    if not event_type or event_type.startswith("recommendation"):
        ledger_rows = (await db.execute(
            select(LedgerEntry)
            .where(LedgerEntry.firm_id == firm.id, LedgerEntry.event_type.in_(["decision", "recommendation"]))
            .order_by(LedgerEntry.created_at.desc()).limit(50)
        )).scalars().all()
        for r in ledger_rows:
            rows.append({
                "ts": r.created_at.isoformat(), "category": "recommendation",
                "event_type": f"recommendation.{r.event_type}",
                "actor": r.actor or "agent",
                "subject": r.content.get("subject") if r.content else None,
                "detail": {"agent_key": r.agent_key},
            })

    # Autonomy changes
    if not event_type or event_type.startswith("autonomy"):
        ac_rows = (await db.execute(
            select(AutonomyChange)
            .where(AutonomyChange.firm_id == firm.id)
            .order_by(AutonomyChange.created_at.desc()).limit(30)
        )).scalars().all()
        for r in ac_rows:
            rows.append({
                "ts": r.created_at.isoformat(), "category": "autonomy",
                "event_type": "autonomy.tier_changed",
                "actor": "adaptive" if r.automatic else "admin",
                "subject": r.agent_key,
                "detail": {"from": r.from_tier, "to": r.to_tier, "reason": r.reason},
            })

    rows.sort(key=lambda x: x["ts"], reverse=True)
    return rows[:limit]


# ── Express client intake ─────────────────────────────────────────────────────

RISK_SUITABILITY = {
    "conservative": {"risk_tolerance": "conservative", "investment_horizon": "short", "max_equity": 0.3},
    "balanced": {"risk_tolerance": "balanced", "investment_horizon": "medium", "max_equity": 0.6},
    "growth": {"risk_tolerance": "growth", "investment_horizon": "long", "max_equity": 0.8},
    "aggressive": {"risk_tolerance": "aggressive", "investment_horizon": "long", "max_equity": 1.0},
}


class ClientCreate(BaseModel):
    full_name: str
    email: str | None = None
    date_of_birth: str | None = None
    segment: str = "private_wealth"
    mandate_type: str = "advisory"
    risk_profile: str = "balanced"
    adviser_id: str | None = None


@router.get("/clients")
async def list_clients(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    households = (await db.execute(
        select(Household).where(Household.firm_id == firm.id).order_by(Household.name)
    )).scalars().all()
    result = []
    for h in households:
        # Primary contact
        persons = (await db.execute(
            select(Person).where(Person.household_id == h.id).limit(2)
        )).scalars().all()
        # Adviser edge
        adviser_edge = (await db.execute(
            select(RelationshipEdge).where(
                RelationshipEdge.firm_id == firm.id,
                RelationshipEdge.to_type == "household",
                RelationshipEdge.to_id == h.id,
                RelationshipEdge.kind == "adviser",
            ).limit(1)
        )).scalar_one_or_none()
        adviser_name = None
        if adviser_edge:
            adv = await db.get(User, adviser_edge.from_id)
            if adv:
                adviser_name = adv.full_name
        result.append({
            "id": str(h.id), "name": h.name, "segment": h.segment,
            "member_count": len(persons),
            "primary_contact": persons[0].full_name if persons else None,
            "primary_email": persons[0].email if persons else None,
            "adviser": adviser_name,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        })
    return result


@router.post("/clients", status_code=201)
async def create_client(
    body: ClientCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    from datetime import date as date_type
    household = Household(firm_id=firm.id, name=body.full_name, segment=body.segment)
    db.add(household)
    await db.flush()

    dob = None
    if body.date_of_birth:
        try:
            dob = date_type.fromisoformat(body.date_of_birth)
        except ValueError:
            pass
    person = Person(
        firm_id=firm.id, household_id=household.id,
        full_name=body.full_name, email=body.email, date_of_birth=dob,
        segment=body.segment,
        kyc={"status": "pending", "aml_screened": False},
    )
    db.add(person)
    await db.flush()

    mandate = Mandate(
        firm_id=firm.id, person_id=person.id,
        name=f"{body.full_name} — {body.mandate_type.title()} mandate",
        mandate_type=body.mandate_type,
        suitability=RISK_SUITABILITY.get(body.risk_profile, RISK_SUITABILITY["balanced"]),
        constraints={},
        is_active=True,
    )
    db.add(mandate)

    if body.adviser_id:
        edge = RelationshipEdge(
            firm_id=firm.id, kind="adviser",
            from_type="user", from_id=uuid.UUID(body.adviser_id),
            to_type="household", to_id=household.id,
            attributes={},
        )
        db.add(edge)

    _audit_sync(db, firm.id, actor, "client.created", subject=body.full_name,
                detail={"mandate_type": body.mandate_type, "segment": body.segment})
    await db.flush()
    return {
        "id": str(household.id), "name": household.name,
        "person_id": str(person.id), "mandate_id": str(mandate.id),
    }


@router.patch("/clients/{household_id}/adviser")
async def assign_adviser(
    household_id: uuid.UUID, adviser_id: str,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    household = await db.get(Household, household_id)
    if not household or household.firm_id != firm.id:
        raise HTTPException(404, "Household not found.")
    adviser = await db.get(User, uuid.UUID(adviser_id))
    if not adviser or adviser.firm_id != firm.id:
        raise HTTPException(404, "Adviser not found.")
    existing = (await db.execute(
        select(RelationshipEdge).where(
            RelationshipEdge.firm_id == firm.id,
            RelationshipEdge.to_id == household_id,
            RelationshipEdge.kind == "adviser",
        )
    )).scalar_one_or_none()
    if existing:
        existing.from_id = adviser.id
    else:
        db.add(RelationshipEdge(
            firm_id=firm.id, kind="adviser",
            from_type="user", from_id=adviser.id,
            to_type="household", to_id=household_id,
            attributes={},
        ))
    _audit_sync(db, firm.id, actor, "client.adviser_assigned",
                subject=household.name, detail={"adviser": adviser.full_name})
    return {"ok": True}


# ── Mandate management ────────────────────────────────────────────────────────

def _mandate_out(m: Mandate, client_name: str | None = None) -> dict:
    return {
        "id": str(m.id), "name": m.name, "mandate_type": m.mandate_type,
        "suitability": m.suitability or {}, "constraints": m.constraints or {},
        "is_active": m.is_active, "client_name": client_name,
        "created_at": m.created_at.isoformat() if m.created_at else None,
    }


@router.get("/mandates")
async def list_mandates(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    mandates = (await db.execute(
        select(Mandate).where(Mandate.firm_id == firm.id).order_by(Mandate.name)
    )).scalars().all()
    result = []
    for m in mandates:
        client_name = None
        if m.person_id:
            p = await db.get(Person, m.person_id)
            if p:
                client_name = p.full_name
        result.append(_mandate_out(m, client_name))
    return result


class MandateUpdate(BaseModel):
    mandate_type: str | None = None
    risk_tolerance: str | None = None
    investment_horizon: str | None = None
    max_equity: float | None = None
    esg_preference: str | None = None
    notes: str | None = None
    is_active: bool | None = None


@router.patch("/mandates/{mandate_id}")
async def update_mandate(
    mandate_id: uuid.UUID, body: MandateUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    m = await db.get(Mandate, mandate_id)
    if not m or m.firm_id != firm.id:
        raise HTTPException(404, "Mandate not found.")
    if body.mandate_type is not None:
        m.mandate_type = body.mandate_type
    if body.is_active is not None:
        m.is_active = body.is_active
    suit = dict(m.suitability or {})
    changed = False
    for field in ("risk_tolerance", "investment_horizon", "max_equity", "esg_preference"):
        val = getattr(body, field)
        if val is not None:
            suit[field] = val
            changed = True
    if changed:
        m.suitability = suit
    _audit_sync(db, firm.id, actor, "mandate.updated", subject=m.name,
                detail={"mandate_type": m.mandate_type})
    return _mandate_out(m)


# ── Per-client data quality ───────────────────────────────────────────────────

@router.get("/data-quality")
async def get_data_quality(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Per-household data quality health matrix — completeness, freshness, suitability."""
    from app.models.connectors import Connector

    households = (await db.execute(
        select(Household).where(Household.firm_id == firm.id).order_by(Household.name)
    )).scalars().all()
    persons = (await db.execute(select(Person).where(Person.firm_id == firm.id))).scalars().all()
    mandates_all = (await db.execute(
        select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active == True)
    )).scalars().all()
    connectors = (await db.execute(
        select(Connector).where(Connector.firm_id == firm.id)
    )).scalars().all()

    persons_by_hh: dict = {}
    for p in persons:
        if p.household_id:
            persons_by_hh.setdefault(p.household_id, []).append(p)

    # Mandate links to Household via person_id → person.household_id
    # OR via entity_id → legal_entity.household_id
    from app.models.graph import LegalEntity
    entities = (await db.execute(
        select(LegalEntity).where(LegalEntity.firm_id == firm.id)
    )).scalars().all()
    person_hh: dict = {p.id: p.household_id for p in persons if p.household_id}
    entity_hh: dict = {e.id: e.household_id for e in entities if e.household_id}
    mandates_by_hh: dict = {}
    for m in mandates_all:
        if m.person_id:
            hh_id = person_hh.get(m.person_id)
        elif m.entity_id:
            hh_id = entity_hh.get(m.entity_id)
        else:
            hh_id = None
        if hh_id:
            mandates_by_hh.setdefault(hh_id, []).append(m)

    now = utcnow()
    last_sync_raw = max((c.last_synced_at for c in connectors if c.last_synced_at), default=None)
    if last_sync_raw is not None and last_sync_raw.tzinfo is None:
        last_sync_raw = last_sync_raw.replace(tzinfo=timezone.utc)
    days_since_sync = round((now - last_sync_raw).total_seconds() / 86400, 1) if last_sync_raw else None

    results = []
    for hh in households:
        people = persons_by_hh.get(hh.id, [])
        mands = mandates_by_hh.get(hh.id, [])
        has_persons = len(people) > 0

        missing: list[str] = []
        # email/DOB only checked for person-linked households
        if has_persons and not any(p.email for p in people):
            missing.append("email")
        if has_persons and not any(p.date_of_birth for p in people):
            missing.append("date_of_birth")
        # Suitability: accept risk_profile OR risk_tolerance as the risk field
        has_risk = any(
            (m.suitability or {}).get("risk_tolerance") or (m.suitability or {}).get("risk_profile")
            for m in mands
        )
        if not has_risk:
            missing.append("suitability_profile")
        # Check whether active mandates exist at all
        if not mands:
            missing.append("active_mandate")

        total_fields = 4 if has_persons else 2
        completeness = round(1 - len(missing) / total_fields, 2)
        if len(missing) == 0 and (days_since_sync is None or days_since_sync < 3):
            health = "green"
        elif len(missing) <= 2 and (days_since_sync is None or days_since_sync < 7):
            health = "amber"
        else:
            health = "red"

        results.append({
            "household_id": str(hh.id), "name": hh.name,
            "completeness": completeness, "missing_fields": missing,
            "missing_count": len(missing), "days_since_sync": days_since_sync,
            "health": health, "mandate_count": len(mands), "person_count": len(people),
        })

    results.sort(key=lambda x: ({"red": 0, "amber": 1, "green": 2}[x["health"]], x["name"]))
    summary = {
        "total": len(results),
        "green": sum(1 for r in results if r["health"] == "green"),
        "amber": sum(1 for r in results if r["health"] == "amber"),
        "red":   sum(1 for r in results if r["health"] == "red"),
    }
    return {"summary": summary, "households": results, "days_since_sync": days_since_sync}


# ── Agent schedules ───────────────────────────────────────────────────────────

_SCHED_META: dict[str, dict] = {
    "drift_rebalancing":   {"label": "Drift & Rebalancing",    "default_hours": 6.0},
    "conduct_surveillance": {"label": "Conduct Surveillance",   "default_hours": 12.0},
    "market_data":          {"label": "Market Data Refresh",    "default_hours": 1.0},
}


@router.get("/schedules")
async def get_schedules(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Schedulable agent jobs — current intervals, last-run metadata, run counts."""
    configs = {
        c.agent_key: c for c in (await db.execute(
            select(AgentConfig).where(AgentConfig.firm_id == firm.id)
        )).scalars().all()
    }
    results = []
    for key, meta in _SCHED_META.items():
        cfg = configs.get(key)
        interval = float((cfg.config or {}).get("schedule_hours", meta["default_hours"])) if cfg else meta["default_hours"]
        run_count = (await db.execute(
            select(func.count()).select_from(AgentRun).where(
                AgentRun.firm_id == firm.id, AgentRun.agent_key == key
            )
        )).scalar() or 0
        last = (await db.execute(
            select(AgentRun).where(AgentRun.firm_id == firm.id, AgentRun.agent_key == key)
            .order_by(AgentRun.started_at.desc()).limit(1)
        )).scalar_one_or_none()
        results.append({
            "agent_key": key, "label": meta["label"],
            "interval_hours": interval, "default_hours": meta["default_hours"],
            "last_run_at":     last.started_at.isoformat() if last else None,
            "last_run_status": last.status if last else None,
            "run_count": run_count,
            "enabled": cfg.enabled if cfg else True,
            "paused":  cfg.paused  if cfg else False,
        })
    return results


class ScheduleUpdate(BaseModel):
    interval_hours: float | None = None
    enabled: bool | None = None


@router.patch("/schedules/{agent_key}")
async def update_schedule(
    agent_key: str, body: ScheduleUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    if agent_key not in _SCHED_META:
        raise HTTPException(404, "Unknown schedule key.")
    if body.interval_hours is not None and not (0.25 <= body.interval_hours <= 168):
        raise HTTPException(400, "interval_hours must be between 0.25 and 168.")
    cfg = (await db.execute(
        select(AgentConfig).where(
            AgentConfig.firm_id == firm.id, AgentConfig.agent_key == agent_key
        )
    )).scalar_one_or_none()
    if not cfg:
        cfg = AgentConfig(firm_id=firm.id, agent_key=agent_key, config={})
        db.add(cfg)
    config = dict(cfg.config or {})
    if body.interval_hours is not None:
        config["schedule_hours"] = body.interval_hours
    cfg.config = config
    if body.enabled is not None:
        cfg.enabled = body.enabled
    _audit_sync(db, firm.id, actor, "schedule.updated", subject=agent_key,
                detail={"interval_hours": config.get("schedule_hours")})
    return {
        "ok": True, "agent_key": agent_key,
        "interval_hours": config.get("schedule_hours", _SCHED_META[agent_key]["default_hours"]),
    }


# ── Force-logout ──────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Revoke all active sessions for a user by setting a Redis force-logout timestamp."""
    from app.core.security import _get_redis
    target = await db.get(User, user_id)
    if not target or target.firm_id != firm.id:
        raise HTTPException(404, "User not found.")
    r = await _get_redis()
    ttl = settings.access_token_ttl_minutes * 60
    await r.setex(f"fl:{str(user_id)}", ttl, str(int(_time.time())))
    _audit_sync(db, firm.id, actor, "user.force_logout", subject=target.email)
    return {"ok": True}


# ── Research edit / delete ────────────────────────────────────────────────────

class ResearchUpdate(BaseModel):
    title: str | None = None
    doc_type: str | None = None
    author: str | None = None
    summary: str | None = None


@router.patch("/research/{doc_id}")
async def update_research(
    doc_id: uuid.UUID, body: ResearchUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    doc = await db.get(ResearchDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(404, "Research document not found.")
    if body.title is not None:
        doc.title = body.title
    if body.doc_type is not None:
        doc.doc_type = body.doc_type
    if body.author is not None:
        doc.author = body.author
    if body.summary is not None:
        doc.summary = body.summary
    _audit_sync(db, firm.id, actor, "research.updated", subject=doc.title)
    return {"ok": True, "id": str(doc.id)}


@router.delete("/research/{doc_id}")
async def delete_research(
    doc_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    doc = await db.get(ResearchDocument, doc_id)
    if not doc or doc.firm_id != firm.id:
        raise HTTPException(404, "Research document not found.")
    title = doc.title
    await db.execute(sql_delete(ResearchChunk).where(ResearchChunk.document_id == doc_id))
    await db.delete(doc)
    _audit_sync(db, firm.id, actor, "research.deleted", subject=title)
    return {"ok": True}


# ── Adviser-household assignments ──────────────────────────────────────────────

@router.get("/assignments")
async def list_assignments(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    # RelationshipEdge: from_type="user"/from_id=adviser → to_type="household"/to_id=household
    edges = (await db.execute(
        select(RelationshipEdge)
        .where(RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "adviser")
        .order_by(RelationshipEdge.created_at.desc())
    )).scalars().all()
    out = []
    for e in edges:
        adviser = await db.get(User, e.from_id) if e.from_type == "user" else None
        household = await db.get(Household, e.to_id) if e.to_type == "household" else None
        out.append({
            "id": str(e.id),
            "household_id": str(e.to_id),
            "household_name": household.name if household else str(e.to_id),
            "adviser_id": str(e.from_id),
            "adviser_name": adviser.full_name if adviser else "—",
            "adviser_email": adviser.email if adviser else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return out


class AssignmentCreate(BaseModel):
    household_id: uuid.UUID
    adviser_id: uuid.UUID


@router.post("/assignments", status_code=201)
async def create_assignment(
    body: AssignmentCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    household = await db.get(Household, body.household_id)
    if not household or household.firm_id != firm.id:
        raise HTTPException(404, "Household not found.")
    adviser = await db.get(User, body.adviser_id)
    if not adviser or adviser.firm_id != firm.id:
        raise HTTPException(404, "Adviser not found.")
    # Upsert: update the existing adviser edge or create a new one
    existing = (await db.execute(
        select(RelationshipEdge).where(
            RelationshipEdge.firm_id == firm.id,
            RelationshipEdge.to_id == body.household_id,
            RelationshipEdge.kind == "adviser",
        )
    )).scalar_one_or_none()
    if existing:
        existing.from_id = body.adviser_id
        edge = existing
    else:
        edge = RelationshipEdge(
            firm_id=firm.id, kind="adviser",
            from_type="user", from_id=body.adviser_id,
            to_type="household", to_id=body.household_id,
            attributes={},
        )
        db.add(edge)
    await db.flush()
    _audit_sync(db, firm.id, actor, "assignment.created",
                subject=household.name, detail={"adviser": adviser.full_name})
    return {"ok": True, "id": str(edge.id)}


@router.delete("/assignments/{edge_id}")
async def delete_assignment(
    edge_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    edge = await db.get(RelationshipEdge, edge_id)
    if not edge or edge.firm_id != firm.id or edge.kind != "adviser":
        raise HTTPException(404, "Assignment not found.")
    household = await db.get(Household, edge.to_id) if edge.to_type == "household" else None
    _audit_sync(db, firm.id, actor, "assignment.deleted",
                subject=household.name if household else str(edge.to_id))
    await db.delete(edge)
    return {"ok": True}


# ── Client segments ────────────────────────────────────────────────────────────

def _segment_out(s: FirmSegment) -> dict:
    return {
        "id": str(s.id), "slug": s.slug, "label": s.label,
        "fee_tier_bps": s.fee_tier_bps, "min_aum_usd": s.min_aum_usd,
        "description": s.description, "is_active": s.is_active,
    }


@router.get("/segments")
async def list_segments(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (await db.execute(
        select(FirmSegment).where(FirmSegment.firm_id == firm.id).order_by(FirmSegment.label)
    )).scalars().all()
    return [_segment_out(s) for s in rows]


class SegmentCreate(BaseModel):
    slug: str
    label: str
    fee_tier_bps: int | None = None
    min_aum_usd: float | None = None
    description: str | None = None


class SegmentUpdate(BaseModel):
    label: str | None = None
    fee_tier_bps: int | None = None
    min_aum_usd: float | None = None
    description: str | None = None
    is_active: bool | None = None


@router.post("/segments", status_code=201)
async def create_segment(
    body: SegmentCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    existing = (await db.execute(
        select(FirmSegment).where(FirmSegment.firm_id == firm.id, FirmSegment.slug == body.slug)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "A segment with that slug already exists.")
    seg = FirmSegment(firm_id=firm.id, **body.model_dump())
    db.add(seg)
    await db.flush()
    _audit_sync(db, firm.id, actor, "segment.created", subject=body.label)
    return _segment_out(seg)


@router.patch("/segments/{seg_id}")
async def update_segment(
    seg_id: uuid.UUID, body: SegmentUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    seg = await db.get(FirmSegment, seg_id)
    if not seg or seg.firm_id != firm.id:
        raise HTTPException(404, "Segment not found.")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(seg, field, val)
    _audit_sync(db, firm.id, actor, "segment.updated", subject=seg.label)
    return _segment_out(seg)


@router.delete("/segments/{seg_id}")
async def delete_segment(
    seg_id: uuid.UUID,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    seg = await db.get(FirmSegment, seg_id)
    if not seg or seg.firm_id != firm.id:
        raise HTTPException(404, "Segment not found.")
    seg.is_active = False
    _audit_sync(db, firm.id, actor, "segment.deactivated", subject=seg.label)
    return {"ok": True}


# ── Mandate type configuration ─────────────────────────────────────────────────

def _mtc_out(m: MandateTypeConfig) -> dict:
    return {
        "id": str(m.id), "slug": m.slug, "label": m.label,
        "default_autonomy_tier": m.default_autonomy_tier,
        "description": m.description,
    }


@router.get("/mandate-types")
async def list_mandate_types(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (await db.execute(
        select(MandateTypeConfig).where(MandateTypeConfig.firm_id == firm.id).order_by(MandateTypeConfig.label)
    )).scalars().all()
    return [_mtc_out(m) for m in rows]


class MandateTypeCreate(BaseModel):
    slug: str
    label: str
    default_autonomy_tier: AutonomyTier = AutonomyTier.TIER_2
    description: str | None = None


class MandateTypeUpdate(BaseModel):
    label: str | None = None
    default_autonomy_tier: AutonomyTier | None = None
    description: str | None = None


@router.post("/mandate-types", status_code=201)
async def create_mandate_type(
    body: MandateTypeCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    existing = (await db.execute(
        select(MandateTypeConfig).where(MandateTypeConfig.firm_id == firm.id, MandateTypeConfig.slug == body.slug)
    )).scalar_one_or_none()
    if existing:
        raise HTTPException(400, "A mandate type with that slug already exists.")
    mtc = MandateTypeConfig(firm_id=firm.id, **body.model_dump())
    db.add(mtc)
    await db.flush()
    _audit_sync(db, firm.id, actor, "mandate_type.created", subject=body.label)
    return _mtc_out(mtc)


@router.patch("/mandate-types/{mtc_id}")
async def update_mandate_type(
    mtc_id: uuid.UUID, body: MandateTypeUpdate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    mtc = await db.get(MandateTypeConfig, mtc_id)
    if not mtc or mtc.firm_id != firm.id:
        raise HTTPException(404, "Mandate type not found.")
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(mtc, field, val)
    _audit_sync(db, firm.id, actor, "mandate_type.updated", subject=mtc.label)
    return _mtc_out(mtc)


# ── Notification configuration ─────────────────────────────────────────────────

def _notif_out(n: NotificationConfig) -> dict:
    return {
        "id": str(n.id), "event_type": n.event_type, "channel": n.channel,
        "enabled": n.enabled, "recipients": n.recipients or [], "config": n.config or {},
    }


@router.get("/notifications")
async def get_notifications(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rows = (await db.execute(
        select(NotificationConfig)
        .where(NotificationConfig.firm_id == firm.id)
        .order_by(NotificationConfig.event_type, NotificationConfig.channel)
    )).scalars().all()
    return [_notif_out(n) for n in rows]


class NotifUpdate(BaseModel):
    event_type: str
    channel: str
    enabled: bool
    recipients: list[str] = []
    config: dict = {}


@router.patch("/notifications")
async def update_notifications(
    body: list[NotifUpdate],
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Bulk upsert notification config rows."""
    for item in body:
        existing = (await db.execute(
            select(NotificationConfig).where(
                NotificationConfig.firm_id == firm.id,
                NotificationConfig.event_type == item.event_type,
                NotificationConfig.channel == item.channel,
            )
        )).scalar_one_or_none()
        if existing:
            existing.enabled = item.enabled
            existing.recipients = item.recipients
            existing.config = item.config
        else:
            db.add(NotificationConfig(
                firm_id=firm.id, event_type=item.event_type, channel=item.channel,
                enabled=item.enabled, recipients=item.recipients, config=item.config,
            ))
    _audit_sync(db, firm.id, actor, "notifications.updated")
    return {"ok": True}


# ── Custom connectors ──────────────────────────────────────────────────────────

class CustomConnectorCreate(BaseModel):
    display_name: str
    domain: str
    connector_type: str = "custom.webhook"  # "custom.webhook" | "custom.rest"
    webhook_url: str | None = None
    base_url: str | None = None
    auth_header: str | None = "Authorization"
    auth_value: str | None = None
    event_filter: str | None = None
    field_mappings: dict | None = None


@router.post("/connectors/custom", status_code=201)
async def create_custom_connector(
    body: CustomConnectorCreate,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Register a custom webhook or REST connector not in the built-in catalogue."""
    from app.conduit.registry import get_provider_def
    from app.models.connectors import Connector
    from app.models.enums import ConnectorDomain, ConnectorStatus

    if body.connector_type not in ("custom.webhook", "custom.rest"):
        raise HTTPException(400, "connector_type must be 'custom.webhook' or 'custom.rest'.")

    pdef = get_provider_def(body.connector_type)
    if not pdef:
        raise HTTPException(400, "Unknown connector type.")

    try:
        domain = ConnectorDomain(body.domain)
    except ValueError:
        raise HTTPException(400, f"Unknown domain '{body.domain}'.")

    cfg: dict = {}
    if body.connector_type == "custom.webhook":
        if not body.webhook_url:
            raise HTTPException(400, "webhook_url is required for custom.webhook.")
        cfg["webhook_url"] = body.webhook_url
        if body.auth_header:
            cfg["auth_header"] = body.auth_header
        if body.auth_value:
            cfg["auth_value"] = body.auth_value
    else:
        if not body.base_url:
            raise HTTPException(400, "base_url is required for custom.rest.")
        cfg["base_url"] = body.base_url
        if body.auth_value:
            cfg["api_key"] = body.auth_value

    if body.event_filter:
        cfg["event_filter"] = body.event_filter
    if body.field_mappings:
        cfg["field_mappings"] = body.field_mappings

    connector = Connector(
        firm_id=firm.id,
        domain=domain,
        provider_key=body.connector_type,
        display_name=body.display_name,
        status=ConnectorStatus.CONFIGURED,
        use_mock=False,
        config=cfg,
    )
    db.add(connector)
    await db.flush()
    _audit_sync(db, firm.id, actor, "connector.custom_created",
                subject=body.display_name, detail={"type": body.connector_type})

    out_cfg = dict(cfg)
    for secret_key in ("auth_value", "api_key"):
        if out_cfg.get(secret_key):
            out_cfg[secret_key] = "••••••••"
    return {
        "id": str(connector.id), "domain": str(domain),
        "provider_key": body.connector_type, "display_name": body.display_name,
        "status": connector.status, "config": out_cfg,
    }
