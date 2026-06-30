"""Conduct surveillance (spec §10.2) — the supervisor.

Runs every recommendation through the regulatory rules engine (cited, versioned obligations),
turns rule failures into surveillance flags, auto-pauses an agent on a HIGH breach (kill-switch),
and writes the action to the ledger. The compliance *assessment* (all rules, pass and fail, with
citations) is persisted by the engine; surveillance is the enforcement layer on top of it."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compliance import engine
from app.core.logging import get_logger
from app.models.enums import SurveillanceSeverity
from app.models.governance import Recommendation, SurveillanceFlag
from app.models.tenant import AgentConfig, Firm
from app.provenance.ledger import append_entry

log = get_logger("aurea.surveillance")

_SEV = {"high": SurveillanceSeverity.HIGH, "medium": SurveillanceSeverity.MEDIUM,
        "low": SurveillanceSeverity.LOW}


async def review_recommendation(session: AsyncSession, rec: Recommendation) -> list[SurveillanceFlag]:
    """Assess against the regime, flag failures, auto-pause on HIGH, write to the ledger."""
    firm = await session.get(Firm, rec.firm_id)
    assessment = await engine.assess(session, firm, rec)  # persists the cited ComplianceCheck + ledger

    flags = [
        SurveillanceFlag(
            firm_id=rec.firm_id, recommendation_id=rec.id, target_agent_key=rec.agent_key,
            severity=_SEV.get(r["severity"], SurveillanceSeverity.MEDIUM),
            category=r["category"], finding=f"{r['code']}: {r['finding']}",
        )
        for r in assessment["fails"]
    ]
    for f in flags:
        session.add(f)

    # Auto-pause on any HIGH breach (the conduct agent supervising the others).
    high = [f for f in flags if f.severity == SurveillanceSeverity.HIGH]
    if high:
        cfg = (await session.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == rec.firm_id, AgentConfig.agent_key == rec.agent_key)
        )).scalar_one_or_none()
        if cfg and not cfg.paused:
            cfg.paused = True
            cfg.paused_reason = f"Auto-paused by conduct surveillance: {high[0].category} — {high[0].finding}"
            for f in high:
                f.auto_paused_agent = True
            log.warning("agent_auto_paused", agent=rec.agent_key, reason=high[0].category)

    if flags:
        await session.flush()
        from app.atlas import activity
        worst = max(flags, key=lambda f: ["info", "low", "medium", "high"].index(f.severity))
        await activity.emit(
            session, firm_id=rec.firm_id, agent_key="conduct_surveillance", kind="flagged",
            summary=f"Conduct Surveillance flagged {rec.agent_key.replace('_', ' ')}: {worst.finding}",
            subject_label=rec.subject_label, meta={"severity": worst.severity},
        )
        await append_entry(
            session, firm_id=rec.firm_id, event_type="surveillance", agent_key="conduct_surveillance",
            recommendation_id=rec.id, actor="agent",
            content={
                "reviewed_agent": rec.agent_key, "recommendation": rec.title,
                "regime": assessment["regime"], "framework_version": assessment["version"],
                "flags": [{"severity": f.severity, "category": f.category, "finding": f.finding}
                          for f in flags],
                "auto_paused": bool(high),
            },
        )
    return flags
