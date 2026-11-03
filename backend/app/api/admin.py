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
from app.aurea_core import licences, platform_admin
from app.aurea_core.platform_admin import AdminError
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
AdminDep = Depends(require_roles(UserRole.ADMIN, UserRole.COMPLIANCE))   # reads: compliance sees the settings
# What changes the platform - users and roles, agent tiers and pauses, the firm's settings - is the admin's
# alone. Compliance reads it all and keeps its own controls (the regulatory rules, conflicts, WSP evidence).
AdminOnly = Depends(require_roles(UserRole.ADMIN))
# A connector is operations' to set up, as the Connectors page is (frontend/lib/nav.ts).
OperationsDep = Depends(require_roles(UserRole.OPERATIONS))


def _refused(exc: AdminError) -> HTTPException:
    """A refusal from aurea_core/platform_admin.py, with the status this route has always answered with."""
    return HTTPException(status_code=exc.status, detail=str(exc))


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


class PiiIn(BaseModel):
    enabled: bool
    reason: str | None = None


@router.put("/security/pii")
async def set_pii(body: PiiIn, user: User = Depends(require_roles(UserRole.COMPLIANCE)),
                  firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """PII redaction is compliance's to set (aurea_core/rulebook.py), with a reason on the audit log."""
    from app.aurea_core import rulebook

    try:
        await rulebook.set_setting(db, firm, actor=user, key="pii_redaction", value=body.enabled, reason=body.reason)
    except rulebook.RulebookError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    from app.core.foundation import policy as foundation_policy
    return {"pii_redaction": foundation_policy(firm)["pii_redaction"]}


class FoundationIn(BaseModel):
    policy: dict
    reason: str | None = None


@router.put("/foundation")
async def set_foundation(body: FoundationIn, user: User = Depends(require_roles(UserRole.ADMIN, UserRole.COMPLIANCE)),
                         firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Save the agent controls the form changed. Each is its owner's (aurea_core/rulebook.py): governance settings are
    compliance's and platform settings an administrator's, and each change goes on the audit log with the reason."""
    from app.aurea_core import rulebook
    from app.core.foundation import policy as foundation_policy

    changes = rulebook.changed_settings(firm, body.policy)
    for key, value in changes.items():
        try:
            await rulebook.set_setting(db, firm, actor=user, key=key, value=value, reason=body.reason)
        except rulebook.RulebookError as exc:
            raise HTTPException(status_code=403 if "'s to set" in str(exc) else 422, detail=str(exc))
    return foundation_policy(firm)


class CmaIn(BaseModel):
    cmas: dict  # {"equity": [0.08, 0.17], "fixed_income": [0.04, 0.07], ...}


@router.put("/foundation/cmas")
async def set_cmas(body: CmaIn, user: User = Depends(require_roles(UserRole.ADMIN)),
                   firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Save firm-level capital-market assumption overrides (expected_return, volatility per asset class)."""
    return {"cmas": await platform_admin.set_cmas(db, firm, user, body.cmas)}


@router.get("/foundation/cmas")
async def get_cmas(user: User = AdminDep, firm: Firm = Depends(current_firm)):
    """Current capital-market assumption overrides for this firm (defaults if none set)."""
    return platform_admin.cma_table(firm)


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
    reason: str | None = None


@router.put("/compliance/rule")
async def set_rule(body: RuleConfigIn, user: User = Depends(require_roles(UserRole.COMPLIANCE)),
                   firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Whether a rule is checked, and how severe a breach is, is compliance's, with a reason (aurea_core/rulebook.py)."""
    from app.aurea_core import rulebook

    try:
        await rulebook.set_rule(db, firm, actor=user, rule_id=body.rule_id, enabled=body.enabled, severity=body.severity,
                                reason=body.reason)
    except rulebook.RulebookError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
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
    user: User = AdminOnly,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Update the firm's Canvas/Studio branding — colors, logo text, tagline."""
    await platform_admin.update_branding(db, firm, user, body.model_dump())
    return {"ok": True, "branding": firm.branding}


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


# Settings that only their own routes change, each with its own checks (aurea_core/platform_admin.py).
RESERVED_SETTINGS = platform_admin.RESERVED_SETTINGS


@router.patch("/firm")
async def update_firm(
    body: FirmUpdate, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """The firm's profile, branding, settings and model routing. A model change waits for green eval gates when
    the firm enforces them (aurea_core/platform_admin.model_gate_refusal)."""
    try:
        await platform_admin.update_firm(
            db, firm, user, fields={f: getattr(body, f) for f in platform_admin.FIRM_FIELDS},
            branding=body.branding, settings=body.settings, model_config=body.model_config_json)
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True}


# ── Agent governance ──────────────────────────────────────────────────────────
class AgentConfigUpdate(BaseModel):
    reason: str | None = None
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
    return await platform_admin.list_agent_configs(db, firm)


@router.patch("/agents/{agent_key}")
async def update_agent_config(
    agent_key: AgentKey, body: AgentConfigUpdate, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """An agent's controls (aurea_core/agent_controls.py): a tier within the licence, no resuming an agent
    surveillance paused while its flag is open, a valid schedule, and each change on the record."""
    try:
        await platform_admin.set_agent_switches(db, firm, user, agent_key, **body.model_dump(exclude_none=True))
    except AdminError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"ok": True}


# ── Bulk CSV client import ────────────────────────────────────────────────────
class CsvImportIn(BaseModel):
    csv_data: str = ""


@router.post("/clients/import")
async def bulk_import_clients(
    body: CsvImportIn,
    user: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
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
    body: PolicyUpsert, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    if refused := licences.refusal(firm, body.tier):
        raise HTTPException(status_code=422, detail=refused)
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
    policy_id: uuid.UUID, user: User = AdminOnly,
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
    return await platform_admin.list_research(db, firm)


async def _research_step(db: AsyncSession, firm: Firm, user: User, doc_id: uuid.UUID, action: str):
    try:
        return (await platform_admin.research_action(db, firm, user, doc_id, action)).record
    except AdminError as exc:
        raise _refused(exc)


@router.post("/research/{doc_id}/submit")
async def submit_research(
    doc_id: uuid.UUID, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    await _research_step(db, firm, user, doc_id, "submit")
    return {"ok": True, "status": "under_review"}


@router.post("/research/{doc_id}/publish")
async def publish_research(
    doc_id: uuid.UUID, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    doc = await _research_step(db, firm, user, doc_id, "publish")
    return {"ok": True, "status": "published", "version": doc.version}


@router.post("/research/{doc_id}/reject")
async def reject_research(
    doc_id: uuid.UUID, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    await _research_step(db, firm, user, doc_id, "reject")
    return {"ok": True, "status": "draft"}


@router.post("/research")
async def add_research(
    body: ResearchIn, user: User = AdminOnly,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """A research document, starting as a draft to be submitted and published."""
    try:
        done, n = await platform_admin.add_research(db, firm, user, title=body.title, body=body.body, doc_type=body.doc_type,
                                                    author=body.author, summary=body.summary, tags=body.tags)
    except AdminError as exc:
        raise _refused(exc)
    return {"id": done.record_id, "chunks": n}


# ── User management ───────────────────────────────────────────────────────────

STAFF_ROLES = [r for r in UserRole if r != UserRole.CLIENT]


def _user_out(u: User) -> dict:
    return platform_admin.user_out(u)


def _make_token(db: AsyncSession, user: User, firm_id: uuid.UUID, token_type: str, days: int) -> UserInviteToken:
    return platform_admin.make_token(db, user, firm_id, token_type, days)


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
    return await platform_admin.list_users(db, firm)


@router.post("/users", status_code=201)
async def create_user(
    body: UserCreate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Invite a person. The invite link comes back to the admin here, and only here: no conversation tool has it."""
    try:
        done, tok = await platform_admin.invite_user(db, firm, actor, email=body.email, full_name=body.full_name,
                                                     role=body.role, title=body.title)
    except AdminError as exc:
        raise _refused(exc)
    invite_url = f"{settings.frontend_url}/accept-invite?token={tok.token}"
    return {**_user_out(done.record), "invite_token": tok.token, "invite_url": invite_url}


@router.patch("/users/{user_id}")
async def update_user(
    user_id: uuid.UUID, body: UserUpdate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        done = await platform_admin.change_user(db, firm, actor, user_id, full_name=body.full_name, role=body.role,
                                                title=body.title, is_active=body.is_active)
    except AdminError as exc:
        raise _refused(exc)
    return _user_out(done.record)


@router.delete("/users/{user_id}", status_code=200)
async def delete_user(
    user_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Soft-deactivate a user and immediately invalidate all their active sessions."""
    if user_id == actor.id:
        raise HTTPException(400, "You cannot deactivate your own account.")
    try:
        await platform_admin.change_user(db, firm, actor, user_id, is_active=False)
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True}


async def _end_sessions(user_id: uuid.UUID) -> None:
    await platform_admin.end_sessions(user_id)


@router.post("/users/{user_id}/resend-invite")
async def resend_invite(
    user_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
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
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
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
    platform_admin.audit(db, firm_id, actor, event_type, subject=subject, detail=detail)


@router.get("/audit")
async def audit_log(
    limit: int = 100,
    event_type: str | None = None,
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    return await platform_admin.audit_log(db, firm, limit=limit, event_type=event_type)


# ── Express client intake ─────────────────────────────────────────────────────





@router.get("/clients")
async def list_clients(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    from app.aurea_core.advisers import of_households

    households = (await db.execute(
        select(Household).where(Household.firm_id == firm.id).order_by(Household.name)
    )).scalars().all()
    held = await of_households(db, firm.id)
    result = []
    for h in households:
        # Primary contact
        persons = (await db.execute(
            select(Person).where(Person.household_id == h.id).limit(2)
        )).scalars().all()
        adv = await db.get(User, held[h.id]) if h.id in held else None
        adviser_name = adv.full_name if adv else None
        result.append({
            "id": str(h.id), "name": h.name, "segment": h.segment,
            "member_count": len(persons),
            "primary_contact": persons[0].full_name if persons else None,
            "primary_email": persons[0].email if persons else None,
            "adviser": adviser_name,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        })
    return result


@router.post("/clients", status_code=410)
async def create_client(actor: User = AdminOnly):
    """Retired (B-96): this made a household, a person and a mandate with no screening, agreement or disclosures, so
    it bypassed every onboarding gate. A client starts as an onboarding case."""
    raise HTTPException(410, "A client starts as an onboarding case: POST /api/onboarding/cases "
                             "(aurea_core/onboarding_start.py), or start_onboarding in the conversation.")

@router.patch("/clients/{household_id}/adviser")
async def assign_adviser(
    household_id: uuid.UUID, adviser_id: str,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    household = await db.get(Household, household_id)
    if not household or household.firm_id != firm.id:
        raise HTTPException(404, "Household not found.")
    adviser = await db.get(User, uuid.UUID(adviser_id))
    if not adviser or adviser.firm_id != firm.id:
        raise HTTPException(404, "Adviser not found.")
    from app.aurea_core import advisers

    was = await advisers.adviser_of(db, firm.id, household_id)
    await advisers.set_adviser(db, firm.id, household, adviser, actor=actor, reason="Set on the admin clients page",
                               was=was.full_name if was else None)
    return {"ok": True}


# ── Mandate management ────────────────────────────────────────────────────────

def _mandate_out(m: Mandate, client_name: str | None = None) -> dict:
    return platform_admin.mandate_out(m, client_name)


@router.get("/mandates")
async def list_mandates(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_mandates(db, firm)


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
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """A mandate's type and suitability. A maximum equity is held to the client's capacity ceiling, as an IPS and a
    risk profile are (aurea_core/platform_admin.prepare_mandate)."""
    try:
        done = await platform_admin.update_mandate(
            db, firm, actor, mandate_id, mandate_type=body.mandate_type, risk_tolerance=body.risk_tolerance,
            investment_horizon=body.investment_horizon, max_equity=body.max_equity, esg_preference=body.esg_preference,
            is_active=body.is_active)
    except AdminError as exc:
        raise _refused(exc)
    return _mandate_out(done.record)


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

    from app.aurea_core import price_freshness
    price_by_mandate = await price_freshness.statuses_by_mandate(db, firm.id)

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

        # Freshness is read from the prices this household's holdings actually rest on, not from
        # when a connector last ran: a feed can run daily and still leave one symbol months old,
        # and a placeholder price is untrustworthy however recently the feed ran.
        held = {}
        for m in mands:
            for s in price_by_mandate.get(m.id, []):
                held[s["symbol"]] = s
        untrusted = sorted(s["symbol"] for s in held.values() if s["status"] in ("missing", "synthetic", "stale"))
        ageing = any(s["status"] == "ageing" for s in held.values())
        ages = [s["age_days"] for s in held.values() if s["age_days"] is not None]
        oldest = max(ages) if ages else None
        stalest = next((s["symbol"] for s in held.values() if s["age_days"] == oldest), None) if ages else None

        if len(missing) == 0 and not untrusted and not ageing:
            health = "green"
        elif len(missing) <= 2 and not untrusted:
            health = "amber"
        else:
            health = "red"

        results.append({
            "household_id": str(hh.id), "name": hh.name,
            "completeness": completeness, "missing_fields": missing,
            "missing_count": len(missing), "days_since_sync": days_since_sync,
            "price_age_days": oldest, "stalest_symbol": stalest, "untrusted_prices": untrusted,
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
# What the worker actually runs: one job per (firm, agent) from AgentConfig.schedule_cron, picked up
# within 15 minutes of a change (atlas/worker.py `_sync_agent_schedules`), and the price fetch,
# which runs at the fixed times in aurea_core.price_freshness.SCHEDULE. This used to list three
# "intervals in hours" that nothing read, so editing one only changed a number on this page.

MARKET_DATA_KEY = platform_admin.MARKET_DATA_KEY
SCHEDULE_PICKED_UP = platform_admin.SCHEDULE_PICKED_UP


@router.get("/schedules")
async def get_schedules(
    user: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Every job the worker runs on a timer for this firm: the agents' cadences and the price fetch."""
    return await platform_admin.list_schedules(db, firm)


class ScheduleUpdate(BaseModel):
    cron: str | None = None
    enabled: bool | None = None
    reason: str | None = None


@router.patch("/schedules/{agent_key}")
async def update_schedule(
    agent_key: str, body: ScheduleUpdate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    if agent_key == MARKET_DATA_KEY:
        raise HTTPException(409, "Prices are fetched at fixed times just after the market closes; "
                                 "switch the feed on or off under Connectors.")
    try:
        key = AgentKey(agent_key)
    except ValueError:
        raise HTTPException(404, "Unknown schedule key.")
    try:
        done = await platform_admin.set_agent_switches(db, firm, actor, key, schedule_cron=body.cron,
                                                       schedule_enabled=body.enabled, reason=body.reason)
    except AdminError as exc:
        raise HTTPException(422, str(exc))
    cfg = done.record
    return {"ok": True, "agent_key": agent_key, "cron": cfg.schedule_cron,
            "enabled": bool(cfg.schedule_enabled), "takes_effect": SCHEDULE_PICKED_UP}


# ── Force-logout ──────────────────────────────────────────────────────────────

@router.post("/users/{user_id}/force-logout")
async def force_logout_user(
    user_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
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
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        doc = await platform_admin.update_research(db, firm, actor, doc_id, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True, "id": str(doc.id)}


@router.delete("/research/{doc_id}")
async def delete_research(
    doc_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        await platform_admin.research_action(db, firm, actor, doc_id, "delete")
    except AdminError as exc:
        raise HTTPException(exc.status, "Research document not found." if exc.status == 404 else str(exc))
    return {"ok": True}


# ── Adviser-household assignments ──────────────────────────────────────────────

@router.get("/assignments")
async def list_assignments(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_assignments(db, firm)


class AssignmentCreate(BaseModel):
    household_id: uuid.UUID
    adviser_id: uuid.UUID


@router.post("/assignments", status_code=201)
async def create_assignment(
    body: AssignmentCreate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    household = await db.get(Household, body.household_id)
    if not household or household.firm_id != firm.id:
        raise HTTPException(404, "Household not found.")
    adviser = await db.get(User, body.adviser_id)
    if not adviser or adviser.firm_id != firm.id:
        raise HTTPException(404, "Adviser not found.")
    # One adviser per household: every earlier edge, to it or a member, is replaced (aurea_core/advisers.py).
    from app.aurea_core import advisers

    was = await advisers.adviser_of(db, firm.id, body.household_id)
    await advisers.set_adviser(db, firm.id, household, adviser, actor=actor, reason="Set on the admin assignments page",
                               was=was.full_name if was else None)
    await db.flush()
    edge = (await db.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm.id, RelationshipEdge.kind == "adviser", RelationshipEdge.to_id == body.household_id,
    ))).scalar_one()
    return {"ok": True, "id": str(edge.id)}


@router.delete("/assignments/{edge_id}")
async def delete_assignment(
    edge_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
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
    return platform_admin.segment_out(s)


@router.get("/segments")
async def list_segments(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_segments(db, firm)


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
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        done = await platform_admin.save_segment(db, firm, actor, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return _segment_out(done.record)


@router.patch("/segments/{seg_id}")
async def update_segment(
    seg_id: uuid.UUID, body: SegmentUpdate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        done = await platform_admin.save_segment(db, firm, actor, segment_id=seg_id, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return _segment_out(done.record)


@router.delete("/segments/{seg_id}")
async def delete_segment(
    seg_id: uuid.UUID,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        await platform_admin.deactivate_segment(db, firm, actor, seg_id)
    except AdminError as exc:
        raise _refused(exc)
    return {"ok": True}


# ── Mandate type configuration ─────────────────────────────────────────────────

def _mtc_out(m: MandateTypeConfig) -> dict:
    return platform_admin.mandate_type_out(m)


@router.get("/mandate-types")
async def list_mandate_types(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_mandate_types(db, firm)


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
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        done = await platform_admin.save_mandate_type(db, firm, actor, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return _mtc_out(done.record)


@router.patch("/mandate-types/{mtc_id}")
async def update_mandate_type(
    mtc_id: uuid.UUID, body: MandateTypeUpdate,
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    try:
        done = await platform_admin.save_mandate_type(db, firm, actor, mandate_type_id=mtc_id, **body.model_dump())
    except AdminError as exc:
        raise _refused(exc)
    return _mtc_out(done.record)


# ── Notification configuration ─────────────────────────────────────────────────

def _notif_out(n: NotificationConfig) -> dict:
    return platform_admin.notification_out(n)


@router.get("/notifications")
async def get_notifications(
    actor: User = AdminDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    return await platform_admin.list_notifications(db, firm)


class NotifUpdate(BaseModel):
    event_type: str
    channel: str
    enabled: bool
    recipients: list[str] = []
    config: dict = {}


@router.patch("/notifications")
async def update_notifications(
    body: list[NotifUpdate],
    actor: User = AdminOnly, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Bulk upsert notification config rows. Every recipient is one of this firm's own people (B-146)."""
    try:
        await platform_admin.update_notifications(db, firm, actor, [item.model_dump() for item in body])
    except AdminError as exc:
        raise _refused(exc)
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
    actor: User = OperationsDep, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
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
