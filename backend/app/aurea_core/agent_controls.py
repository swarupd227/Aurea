"""An agent's switches, set by a person: whether it runs, its autonomy tier, its pause, its schedule (the
Agents tab). The foundation settings an agent runs under are aurea_core/rulebook.py's, each its owner's.

One place for the change whichever surface asks for it (the admin page now, the conversation next), so
each rule holds everywhere:

  - a tier above what the firm's licence allows is refused (aurea_core/licences.py);
  - an agent conduct surveillance paused stays paused while the flag that paused it is open. Resolving the
    flag is compliance's or a branch leader's (provenance/supervision.py); only then may it be resumed;
  - a schedule is a valid cron, the same check the Schedules page makes;
  - every change of tier, pause or enablement is an AutonomyChange made by a person, with a reason, and
    every change is on the audit trail. A pause cleared by hand used to leave no record at all.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import licences
from app.models.enums import AgentKey, AutonomyTier
from app.models.governance import AutonomyChange, SurveillanceFlag
from app.models.identity import AuditEvent
from app.models.tenant import AgentConfig, Firm


class ControlError(Exception):
    pass


def valid_cron(cron: str) -> str:
    """The cron as the scheduler will read it, or ControlError."""
    from apscheduler.triggers.cron import CronTrigger

    from app.agents import schedules as agent_schedules

    cron = cron.strip()
    try:
        CronTrigger.from_crontab(agent_schedules.normalise_cron(cron))
    except ValueError as exc:
        raise ControlError(f"'{cron}' is not a valid 5-field cron expression (minute hour day-of-month month weekday): {exc}")
    return cron


async def open_pausing_flags(session: AsyncSession, firm_id: uuid.UUID, agent_key: str) -> list[SurveillanceFlag]:
    """The unresolved surveillance flags that paused this agent."""
    return list((await session.execute(
        select(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm_id, SurveillanceFlag.target_agent_key == str(agent_key),
                                       SurveillanceFlag.auto_paused_agent.is_(True), SurveillanceFlag.resolved.is_(False))
    )).scalars().all())


async def change(session: AsyncSession, firm: Firm, agent_key: AgentKey, *, actor_id: uuid.UUID, actor_email: str,
                 enabled: bool | None = None, default_tier: AutonomyTier | None = None, paused: bool | None = None,
                 paused_reason: str | None = None, schedule_cron: str | None = None, schedule_enabled: bool | None = None,
                 config: dict | None = None, reason: str | None = None) -> AgentConfig:
    """Apply what is given, after every check, or raise ControlError and change nothing."""
    if default_tier is not None and (refused := licences.refusal(firm, default_tier)):
        raise ControlError(refused)
    cron = valid_cron(schedule_cron) if schedule_cron is not None else None
    cfg = (await session.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm.id, AgentConfig.agent_key == agent_key))).scalar_one_or_none()
    if paused is False and cfg is not None and cfg.paused:
        if await open_pausing_flags(session, firm.id, agent_key):
            raise ControlError("Conduct surveillance paused this agent and its flag is still open. Compliance or a branch "
                               "leader resolves the flag first; only then can the agent be resumed.")
    if cfg is None:
        cfg = AgentConfig(firm_id=firm.id, agent_key=agent_key)
        session.add(cfg)
        await session.flush()

    before = {"enabled": cfg.enabled, "default_tier": cfg.default_tier, "paused": cfg.paused}
    if enabled is not None:
        cfg.enabled = enabled
    if default_tier is not None:
        cfg.default_tier = default_tier
    if paused is not None:
        cfg.paused = paused
        cfg.paused_reason = (paused_reason or reason or "Paused by a person") if paused else None
    if cron is not None:
        cfg.schedule_cron = cron
    if schedule_enabled is not None:
        cfg.schedule_enabled = schedule_enabled
    if config is not None:
        cfg.config = {**(cfg.config or {}), **config}

    after = {"enabled": cfg.enabled, "default_tier": cfg.default_tier, "paused": cfg.paused}
    moved = {k: [before[k], after[k]] for k in after if before[k] != after[k]}
    if moved:
        said = reason or paused_reason or "Changed by a person on the admin page"
        session.add(AutonomyChange(firm_id=firm.id, agent_key=agent_key, from_tier=str(before["default_tier"] or ""),
                                   to_tier=str(after["default_tier"] or ""), paused=bool(cfg.paused), automatic=False,
                                   reason=f"{said} ({actor_email})"))
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor_id, actor_email=actor_email, event_type="agent.controls_changed",
                           subject=str(agent_key), detail={"changed": moved, "schedule_cron": cfg.schedule_cron,
                                                           "schedule_enabled": cfg.schedule_enabled,
                                                           "config_keys": sorted((config or {}).keys()), "reason": reason}))
    await session.flush()
    return cfg
