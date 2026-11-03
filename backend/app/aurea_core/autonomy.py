"""The firm's autonomy policies: for an agent, and optionally one mandate type, the tier it runs at and its guardrails.

`provenance/policy.resolve_tier` reads them (a mandate type's own policy, else the agent's default, else its config)
and a licence caps whatever wins. A Tier 3 agent acts on its own only inside its guardrails (`policy.GUARDRAILS`); a
run a person starts of an agent that moves a book or cash stops at a proposal whatever the tier. Client consent is the
licence's, not a guardrail. A change is a person's, with a reason, on the audit log; "default" removes a policy so the
agent falls back to its configured tier. Nothing here runs an agent.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.aurea_core import licences
from app.models.enums import AgentKey, AutonomyTier, MandateType
from app.models.identity import AuditEvent, User
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm
from app.provenance.policy import GUARDRAILS

TIER_WORDS = {"tier_1": "Tier 1 · assistive: drafts for a person", "tier_2": "Tier 2 · supervised: a person approves each",
              "tier_3": "Tier 3 · bounded autonomy: acts inside its guardrails, reviewed after"}


class AutonomyError(Exception):
    """A policy change that cannot be made, in words a person can act on."""


def _name(agent_key: str) -> str:
    try:
        return (CATALOGUE.get(AgentKey(agent_key)) or {}).get("name") or agent_key.replace("_", " ").title()
    except ValueError:
        return agent_key


def guardrail_rows(guardrails: dict) -> list[dict]:
    """Each guardrail with whether anything enforces it, so a stored key nothing reads is never shown as a control."""
    return [{"key": k, "value": v, "enforced": k in GUARDRAILS,
             "does": GUARDRAILS.get(k, "nothing reads this key; client consent is the licence's")} for k, v in (guardrails or {}).items()]


async def for_firm(session: AsyncSession, firm: Firm) -> dict:
    rows = (await session.execute(select(AutonomyPolicy).where(AutonomyPolicy.firm_id == firm.id))).scalars().all()
    configs = {str(c.agent_key): c for c in (await session.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalars().all()}
    ceiling = licences.ceiling(firm)
    return {
        "policies": sorted([{
            "id": str(p.id), "agent_key": str(p.agent_key), "agent": _name(str(p.agent_key)),
            "mandate_type": str(p.mandate_type) if p.mandate_type else None, "tier": str(p.tier),
            "effective_tier": str(licences.cap(firm, p.tier)), "guardrails": guardrail_rows(p.guardrails), "rationale": p.rationale,
            "agent_default": str(configs[str(p.agent_key)].default_tier) if str(p.agent_key) in configs else None,
        } for p in rows], key=lambda r: (r["agent"], r["mandate_type"] or "")),
        "licence_ceiling": str(ceiling) if ceiling else None,
        "tiers": TIER_WORDS, "guardrails": GUARDRAILS,
        "mandate_types": [m.value for m in MandateType],
    }


def _guardrails(raw) -> dict:
    out: dict = {}
    for k, v in (raw or {}).items():
        if k not in GUARDRAILS:
            raise AutonomyError(f"{k} is not a guardrail anything enforces. The guardrails are: "
                                + "; ".join(f"{g} ({d})" for g, d in GUARDRAILS.items()) + ".")
        if k == "max_trade_value":
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise AutonomyError("max_trade_value is an amount in the firm's currency.")
            if v <= 0:
                raise AutonomyError("max_trade_value must be above zero.")
        else:
            v = str(v).strip().lower() in ("true", "1", "yes") if not isinstance(v, bool) else v
        out[k] = v
    return out


async def prepare_set(session: AsyncSession, firm: Firm, *, agent_key: str | None, mandate_type: str | None, tier: str | None,
                      guardrails: dict | None, reason: str | None) -> dict:
    """What a policy change would do. Writes nothing."""
    try:
        key = AgentKey(str(agent_key or "").strip())
    except ValueError:
        raise AutonomyError(f"No agent called {agent_key!r}; read_agents lists their keys.")
    mtype = (mandate_type or "").strip().lower() or None
    if mtype in ("all", "any", "default"):
        mtype = None
    if mtype and mtype not in {m.value for m in MandateType}:
        raise AutonomyError(f"No mandate type {mandate_type!r}. The types are: {', '.join(m.value for m in MandateType)}, or leave it out for all.")
    tier = (tier or "").strip().lower().replace(" ", "_")
    if tier not in (*TIER_WORDS, "default"):
        raise AutonomyError("The tier is tier_1, tier_2 or tier_3, or default to remove the policy.")
    reason = (reason or "").strip()
    if not reason:
        raise AutonomyError("Give the reason for the change; it goes on the audit log.")
    if tier != "default" and (refused := licences.refusal(firm, AutonomyTier(tier))):
        raise AutonomyError(refused)
    existing = (await session.execute(select(AutonomyPolicy).where(
        AutonomyPolicy.firm_id == firm.id, AutonomyPolicy.agent_key == key.value,
        (AutonomyPolicy.mandate_type == mtype) if mtype else AutonomyPolicy.mandate_type.is_(None)))).scalar_one_or_none()
    if tier == "default" and existing is None:
        raise AutonomyError("There is no policy to remove for that agent and mandate type.")
    rails = _guardrails(guardrails) if guardrails is not None else (dict(existing.guardrails or {}) if existing else {})
    if existing and tier != "default" and str(existing.tier) == tier and dict(existing.guardrails or {}) == rails:
        raise AutonomyError("That is already the policy.")
    cfg = (await session.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id, AgentConfig.agent_key == key.value))).scalar_one_or_none()
    return {"agent_key": key, "agent": _name(key.value), "mandate_type": mtype, "tier": tier, "guardrails": rails, "reason": reason,
            "existing": existing, "fallback": str(cfg.default_tier) if cfg else AutonomyTier.TIER_1.value}


async def set_policy(session: AsyncSession, firm: Firm, *, actor: User, **terms) -> dict:
    p = await prepare_set(session, firm, **terms)
    before = {"tier": str(p["existing"].tier), "guardrails": p["existing"].guardrails} if p["existing"] else None
    if p["tier"] == "default":
        await session.delete(p["existing"])
    elif p["existing"]:
        p["existing"].tier, p["existing"].guardrails, p["existing"].rationale = p["tier"], p["guardrails"], p["reason"]
    else:
        session.add(AutonomyPolicy(firm_id=firm.id, agent_key=p["agent_key"].value, mandate_type=p["mandate_type"],
                                   tier=p["tier"], guardrails=p["guardrails"], rationale=p["reason"]))
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="autonomy.policy_set",
                           subject=f"{p['agent']} · {p['mandate_type'] or 'all mandates'}",
                           detail={"before": before, "after": None if p["tier"] == "default" else {"tier": p["tier"], "guardrails": p["guardrails"]},
                                   "reason": p["reason"]}))
    await session.flush()
    return await for_firm(session, firm)
