"""Record & aggregate LLM usage for token-cost governance and ROI telemetry."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.base import LLMResult
from app.models.telemetry import LlmUsage

# Indicative prices, USD per 1M tokens (input, output). Labelled "est." in the UI.
PRICES = {
    "claude-opus-4-8": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (0.8, 4.0),
    "claude-haiku-4-5": (0.8, 4.0),
}


def est_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICES.get(model, (5.0, 15.0))
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 6)


async def record(session: AsyncSession, firm_id, *, agent: str, task: str, result: LLMResult) -> None:
    """Persist one usage row (including fallbacks, so fallback-rate is visible)."""
    u = result.usage or {}
    i, o = int(u.get("input_tokens", 0) or 0), int(u.get("output_tokens", 0) or 0)
    session.add(LlmUsage(
        firm_id=firm_id, agent=agent or "agent", task=task, model=result.model,
        provider=result.provider, input_tokens=i, output_tokens=o,
        est_cost=est_cost(result.model, i, o), is_fallback=bool(result.is_fallback),
        redacted_count=int(getattr(result, "redacted_count", 0) or 0),
    ))


_PII_CACHE: dict = {}


async def firm_pii_terms(session: AsyncSession, firm_id) -> list[str]:
    """Sensitive strings for a firm (person names + account names), cached per process."""
    if firm_id in _PII_CACHE:
        return _PII_CACHE[firm_id]
    from app.models.graph import Account, Household, Person
    terms: set[str] = set()
    for full, pref in (await session.execute(
        select(Person.full_name, Person.preferred_name).where(Person.firm_id == firm_id)
    )).all():
        if full:
            terms.add(full)
        if pref:
            terms.add(pref)
    for name in (await session.execute(
        select(Household.name).where(Household.firm_id == firm_id)
    )).scalars().all():
        if name:
            terms.add(name)
    for name in (await session.execute(
        select(Account.name).where(Account.firm_id == firm_id)
    )).scalars().all():
        if name:
            terms.add(name)
    out = sorted(terms, key=len, reverse=True)
    _PII_CACHE[firm_id] = out
    return out


async def agent_cost(session: AsyncSession, firm_id, agent: str) -> float:
    """Estimated spend for a single agent (for per-agent cost caps)."""
    total = (await session.execute(
        select(func.coalesce(func.sum(LlmUsage.est_cost), 0.0))
        .where(LlmUsage.firm_id == firm_id, LlmUsage.agent == agent)
    )).scalar_one()
    return float(total or 0.0)


async def gateway_params(session: AsyncSession, firm, *, base_max_tokens: int, agent_key=None) -> dict:
    """Resolve model-gateway + redaction params from the effective (firm + per-agent) policy."""
    from app.core.foundation import agent_overrides, merge, policy as foundation_policy
    base = foundation_policy(firm)
    ov = await agent_overrides(session, firm.id, agent_key) if agent_key else {}
    pol = merge(base, ov)
    redact = bool(pol["pii_redaction"])
    pii_terms = await firm_pii_terms(session, firm.id) if redact else None
    cap = float(pol.get("monthly_cost_cap_usd") or 0)
    force_fb = False
    if cap > 0:  # an agent-specific cap measures that agent's spend; a firm cap measures total
        spend = (await agent_cost(session, firm.id, str(agent_key))) \
            if ov.get("monthly_cost_cap_usd") is not None else (await summary(session, firm.id))["est_cost"]
        force_fb = spend >= cap
    return {
        "pii_terms": pii_terms, "pii_categories": pol.get("pii_categories"), "redact": redact,
        "allow_fallback": bool(pol.get("fallback_enabled", True)), "force_fallback": force_fb,
        "max_tokens": min(base_max_tokens, int(pol.get("max_tokens_default") or base_max_tokens)),
    }


async def summary(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Aggregate usage for the telemetry / foundation panels."""
    rows = (await session.execute(
        select(LlmUsage).where(LlmUsage.firm_id == firm_id)
    )).scalars().all()
    total_calls = len(rows)
    by_model: dict[str, int] = {}
    by_agent: dict[str, int] = {}
    in_tok = out_tok = cost = redacted = fallback = 0
    for r in rows:
        by_model[r.model] = by_model.get(r.model, 0) + 1
        by_agent[r.agent] = by_agent.get(r.agent, 0) + 1
        in_tok += r.input_tokens
        out_tok += r.output_tokens
        cost += r.est_cost
        redacted += r.redacted_count
        fallback += 1 if r.is_fallback else 0
    return {
        "calls": total_calls,
        "input_tokens": in_tok, "output_tokens": out_tok, "total_tokens": in_tok + out_tok,
        "est_cost": round(cost, 4),
        "redacted_entities": redacted,
        "fallback_calls": fallback,
        "fallback_rate": round(fallback / total_calls, 3) if total_calls else 0.0,
        "by_model": dict(sorted(by_model.items(), key=lambda kv: -kv[1])),
        "by_agent": dict(sorted(by_agent.items(), key=lambda kv: -kv[1])),
    }
