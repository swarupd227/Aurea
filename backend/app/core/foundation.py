"""Firm-level Common-Foundation policy — the configurable scaffold every agent inherits.

Stored in `firm.settings['foundation']`; `policy(firm)` merges it over the defaults. Each key is
wired to real behaviour (see callers): the model gateway, PII redaction, surveillance thresholds,
autonomy cap, default guardrails, eval gate and grounding policy."""
from __future__ import annotations

DEFAULTS: dict = {
    # Model gateway
    "fallback_enabled": True,        # try the secondary provider if the primary fails
    "monthly_cost_cap_usd": 0.0,     # 0 = no cap; over cap → live calls blocked, deterministic fallback
    "max_tokens_default": 1200,      # ceiling applied to model calls
    # Security & compliance
    "pii_redaction": True,
    "pii_categories": ["names", "accounts", "emails", "ids"],
    "min_confidence": 0.5,           # actions below this are flagged/held by surveillance
    # Governance & guardrails
    "require_approval_everywhere": False,  # cap every agent at a human checkpoint (no auto-act)
    "default_cgt_budget": 15000,     # fallback CGT budget when a mandate doesn't set one
    "max_turnover_pct": 0.0,         # 0 = no cap; rebalances above this raise a surveillance flag
    # Eval & quality gates
    "enforce_eval_gate": True,       # block a model/config change unless the golden gates are green
    # Grounding & context
    "require_grounding": False,      # escalate "no firm research cited" from advisory to blocking
    "rag_top_k": 3,                  # retrieval depth for grounded reasoning
}


# Keys an individual agent may override (a subset that makes sense per-agent).
OVERRIDABLE = [
    "monthly_cost_cap_usd", "min_confidence", "require_approval_everywhere", "max_tokens_default",
    "require_grounding", "rag_top_k", "default_cgt_budget", "max_turnover_pct", "fallback_enabled",
]


def policy(firm) -> dict:
    """The effective foundation policy for a firm (defaults ← stored overrides)."""
    settings = (getattr(firm, "settings", None) or {})
    out = dict(DEFAULTS)
    out.update(settings.get("foundation") or {})
    # Back-compat: an older top-level pii_redaction toggle.
    if "foundation" not in settings and "pii_redaction" in settings:
        out["pii_redaction"] = bool(settings["pii_redaction"])
    return out


def merge(base: dict, overrides: dict | None) -> dict:
    """Layer per-agent overrides on top of the firm policy (only known, non-null keys)."""
    out = dict(base)
    for k, v in (overrides or {}).items():
        if k in DEFAULTS and v is not None:
            out[k] = v
    return out


async def agent_overrides(session, firm_id, agent_key) -> dict:
    """The foundation overrides stored on an agent's config (empty if none)."""
    from sqlalchemy import select
    from app.models.tenant import AgentConfig
    cfg = (await session.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm_id, AgentConfig.agent_key == str(agent_key))
    )).scalar_one_or_none()
    return (cfg.config or {}).get("foundation") or {} if cfg else {}


async def for_agent(session, firm, agent_key) -> dict:
    """The effective policy for one agent: firm policy with its own overrides layered on."""
    base = policy(firm)
    if not agent_key:
        return base
    return merge(base, await agent_overrides(session, firm.id, agent_key))
