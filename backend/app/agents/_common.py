"""Shared helpers for agents — grounded LLM narration with deterministic fallback."""
from __future__ import annotations

from app.atlas.base import AgentContext
from app.llm import usage as usage_mod
from app.llm.service import firm_llm_creds, llm_service


async def narrate(
    ctx: AgentContext,
    *,
    task: str,
    system: str,
    prompt: str,
    fallback: str,
    max_tokens: int = 900,
) -> str:
    """Generate grounded text — with per-firm model + key config, PII redaction, and usage telemetry."""
    firm = ctx.firm
    agent_key = getattr(getattr(ctx, "run", None), "agent_key", None)
    gp = await usage_mod.gateway_params(ctx.session, firm, base_max_tokens=max_tokens, agent_key=agent_key)
    result = await llm_service.generate(
        task=task,
        system=system,
        prompt=prompt,
        firm_model_config=(firm.model_config_json or {}),
        creds=firm_llm_creds(firm),
        max_tokens=gp["max_tokens"],
        fallback=lambda: fallback,
        pii_terms=gp["pii_terms"], pii_categories=gp["pii_categories"], redact=gp["redact"],
        allow_fallback=gp["allow_fallback"], force_fallback=gp["force_fallback"],
    )
    try:
        await usage_mod.record(ctx.session, firm.id, agent=str(agent_key or "agent"), task=task, result=result)
    except Exception:  # telemetry must never break a run
        pass
    return result.text


def firm_voice(ctx: AgentContext) -> str:
    b = ctx.firm.branding or {}
    return (
        f"You are a governed AI agent inside {ctx.firm.name}, an advice-led wealth firm "
        f"regulated by the {ctx.firm.regulator or 'local regulator'} in {ctx.firm.jurisdiction}. "
        "Write in the firm's professional, measured voice. Be precise, cite the firm's own "
        "research where provided, never give the impression of final authority — the named "
        "human adviser always decides. Avoid hype and absolute guarantees."
    )
