"""The agent scheduler (worker process).

Periodically triggers the scheduled monitoring agents (drift, next-best-action, conduct
surveillance) across active mandates/households — the 'sense' that keeps the workforce
proactive (spec §7 'without waiting to be asked'). Runs as its own container in compose."""
from __future__ import annotations

import asyncio
from datetime import timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.agents import schedules as agent_schedules
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.conduit.service import sync_market_data
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.enums import MandateType
from app.models.graph import Household, Mandate
from app.models.tenant import AgentConfig, Firm

log = get_logger("aurea.worker")


_HEARTBEATS = [
    ("drift_rebalancing", "Drift & Tax-Managed Rebalancing is watching {mandates} mandates for tolerance breaches"),
    ("next_best_action", "Next-Best-Action is scanning {households} households for opportunities and risks"),
    ("conduct_surveillance", "Conduct Surveillance is supervising every recommendation and communication"),
    ("client_care", "Client Care is monitoring for volatility, milestones and at-risk signals"),
    ("research_reporting", "Research & Reporting is tracking the firm's house views for relevance"),
]
_hb_idx = 0


async def _heartbeat() -> None:
    """Emit an on-duty 'watching' pulse so the workforce reads as continuously alive."""
    global _hb_idx
    from sqlalchemy import func

    from app.atlas import activity
    from app.models.enums import ActivityKind
    from app.models.graph import Household, Mandate

    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            agent_key, template = _HEARTBEATS[_hb_idx % len(_HEARTBEATS)]
            mandates = (await s.execute(
                select(func.count(Mandate.id)).where(Mandate.firm_id == firm.id))).scalar_one()
            households = (await s.execute(
                select(func.count(Household.id)).where(Household.firm_id == firm.id))).scalar_one()
            await activity.emit(
                s, firm_id=firm.id, agent_key=agent_key, kind=ActivityKind.WATCHING,
                summary=template.format(mandates=mandates, households=households),
            )
        _hb_idx += 1
        await s.commit()


async def _refresh_market_data() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            try:
                await sync_market_data(s, firm.id)
                await s.commit()
            except Exception as exc:  # pragma: no cover
                await s.rollback()
                log.warning("market_refresh_failed", firm=firm.slug, error=str(exc))


async def _run_evaluation() -> None:
    from app.provenance.evaluation import evaluate_firm

    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            try:
                result = await evaluate_firm(s, firm.id)
                await s.commit()
                if result["autonomy_changes"]:
                    log.warning("eval_autonomy_changes", firm=firm.slug, changes=result["autonomy_changes"])
            except Exception as exc:  # pragma: no cover
                await s.rollback()
                log.warning("evaluation_failed", firm=firm.slug, error=str(exc))


# `_run_drift_monitor` lived here. It is gone: the generic `_run_scheduled_agent` does
# exactly the same work — same enabled/paused checks, same active-mandate-with-a-model
# query, same `mandate_type` argument — driven by AgentConfig rather than a hardcoded
# 6-hour interval. Keeping both would have meant drift running twice.


async def _check_holding_alerts() -> None:
    """I8 — Scan holdings for concentration or drift breaches and fire SurveillanceFlag alerts."""
    from sqlalchemy import func

    from app.models.governance import SurveillanceFlag
    from app.models.graph import Account, Household, Mandate
    from app.models.portfolio import Holding

    CONCENTRATION_LIMIT = 0.25  # 25% in any single holding triggers alert

    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            try:
                # Get all active mandates.
                mandates = (await s.execute(
                    select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True))
                )).scalars().all()

                for mandate in mandates:
                    # Sum total market value for this mandate.
                    total_mv = (await s.execute(
                        select(func.coalesce(func.sum(Holding.market_value), 0))
                        .join(Account, Account.id == Holding.account_id)
                        .where(Account.mandate_id == mandate.id)
                    )).scalar_one()
                    if float(total_mv) < 1000:
                        continue

                    # Find holdings exceeding concentration limit.
                    holdings = (await s.execute(
                        select(Holding)
                        .join(Account, Account.id == Holding.account_id)
                        .where(Account.mandate_id == mandate.id, Holding.market_value > 0)
                    )).scalars().all()

                    for h in holdings:
                        pct = float(h.market_value) / float(total_mv)
                        if pct > CONCENTRATION_LIMIT:
                            # Check if unresolved alert already exists.
                            existing = (await s.execute(
                                select(SurveillanceFlag).where(
                                    SurveillanceFlag.firm_id == firm.id,
                                    SurveillanceFlag.kind == "holding_alert",
                                    SurveillanceFlag.resolved == False,
                                    SurveillanceFlag.attributes["mandate_id"].as_string() == str(mandate.id),
                                    SurveillanceFlag.attributes["instrument_id"].as_string() == str(h.instrument_id),
                                )
                            )).scalars().first()
                            if not existing:
                                flag = SurveillanceFlag(
                                    firm_id=firm.id,
                                    kind="holding_alert",
                                    category="concentration",
                                    severity="medium",
                                    finding=f"Holding {h.instrument_id} is {pct:.0%} of mandate — exceeds {CONCENTRATION_LIMIT:.0%} limit",
                                    resolved=False,
                                    attributes={
                                        "mandate_id": str(mandate.id),
                                        "instrument_id": str(h.instrument_id),
                                        "concentration_pct": round(pct, 4),
                                        "market_value": float(h.market_value),
                                        "total_mv": float(total_mv),
                                    },
                                )
                                s.add(flag)
                                log.info("holding_alert_fired", firm=firm.slug,
                                         mandate=str(mandate.id), pct=round(pct, 3))

                await s.commit()
            except Exception as exc:  # pragma: no cover
                await s.rollback()
                log.warning("holding_alert_check_failed", firm=firm.slug, error=str(exc))


# ── Config-driven agent scheduling ────────────────────────────────────────────
#
# Thirteen agents declare `scheduled = True`, but the worker only ever registered three
# of them — so ten agents that are meant to run proactively only ever ran when someone
# pressed a button, while the heartbeat announced them as "on duty, watching".
#
# `AgentConfig` already carries `schedule_cron` and `schedule_enabled`, and the admin API
# already writes them. Nothing read them. Rather than hardcode a cadence table here, the
# worker now honours that config: cadence becomes a firm-level product decision that can
# be changed in Admin without a deploy.


async def _subjects_for(session, firm, subject_kind: str) -> list[tuple[Subject, dict]]:
    """The subjects one scheduled run should fan out across, with any extra run kwargs.

    A firm-level agent runs once; a household agent runs once per household; a mandate
    agent once per active mandate. Getting this from the catalogue rather than per-agent
    branching means a new agent is scheduled correctly by declaring its subject.
    """
    if subject_kind == "firm":
        return [(Subject("firm", firm.id, firm.name), {})]

    if subject_kind == "household":
        rows = (await session.execute(
            select(Household).where(Household.firm_id == firm.id)
        )).scalars().all()
        return [(Subject("household", h.id, h.name), {}) for h in rows]

    if subject_kind == "mandate":
        rows = (await session.execute(
            select(Mandate).where(
                Mandate.firm_id == firm.id,
                Mandate.is_active.is_(True),
                Mandate.model_portfolio_id.isnot(None),
            )
        )).scalars().all()
        # Drift needs the mandate type to pick its autonomy tier.
        return [(Subject("mandate", m.id, m.name), {"mandate_type": MandateType(m.mandate_type)})
                for m in rows]

    if subject_kind == "onboarding_case":
        # These agents scan all open cases themselves when given a firm subject, which is
        # cheaper than one run per case and keeps their own filtering logic authoritative.
        return [(Subject("firm", firm.id, firm.name), {})]

    log.warning("scheduler_unknown_subject", subject=subject_kind)
    return []


async def _run_scheduled_agent(firm_id, agent_key: str) -> None:
    """Execute one scheduled agent for one firm, fanned out over its subjects."""
    from app.agents.catalogue import CATALOGUE

    async with SessionLocal() as s:
        firm = await s.get(Firm, firm_id)
        if not firm:
            return

        cfg = (await s.execute(
            select(AgentConfig).where(
                AgentConfig.firm_id == firm.id,
                AgentConfig.agent_key == agent_key,
            )
        )).scalar_one_or_none()
        # Re-checked at fire time, not just at registration: a kill-switch should take
        # effect on the next run rather than the next worker restart.
        if not cfg or not cfg.enabled or cfg.paused or not cfg.schedule_enabled:
            return

        subject_kind = (CATALOGUE.get(agent_key) or {}).get("subject", "firm")
        subjects = await _subjects_for(s, firm, subject_kind)

        ran = failed = 0
        for subject, extra in subjects:
            try:
                await run_agent(s, firm=firm, agent_key=agent_key, subject=subject,
                                trigger="scheduled_monitor", **extra)
                await s.commit()
                ran += 1
            except AgentPausedError:
                await s.rollback()
                return
            except Exception as exc:  # pragma: no cover
                await s.rollback()
                failed += 1
                log.warning("scheduled_agent_failed", firm=firm.slug,
                            agent=agent_key, subject=str(subject.id), error=str(exc))

        log.info("scheduled_agent_sweep", firm=firm.slug, agent=agent_key,
                 subject_kind=subject_kind, ran=ran, failed=failed)


async def _sync_agent_schedules(scheduler) -> None:
    """Register a job per (firm, agent) from AgentConfig, and drop ones no longer wanted.

    Re-run periodically so a cadence changed in Admin takes effect without a restart.
    """
    from apscheduler.triggers.cron import CronTrigger

    wanted: dict[str, tuple] = {}
    async with SessionLocal() as s:
        rows = (await s.execute(
            select(AgentConfig, Firm).join(Firm, Firm.id == AgentConfig.firm_id).where(
                AgentConfig.schedule_enabled.is_(True),
                AgentConfig.enabled.is_(True),
                AgentConfig.paused.is_(False),
                AgentConfig.schedule_cron.isnot(None),
            )
        )).all()
        for cfg, firm in rows:
            job_id = f"agent:{firm.slug}:{cfg.agent_key}"
            try:
                # Standard crontab weekday numbering, not APScheduler's — see
                # schedules.normalise_cron. Without this, "0 5 * * 1" means Tuesday.
                trigger = CronTrigger.from_crontab(
                    agent_schedules.normalise_cron(cfg.schedule_cron))
            except ValueError:
                # An invalid expression silently never fires, which looks identical to a
                # working schedule that has nothing to do. Say so.
                log.warning("scheduler_invalid_cron", firm=firm.slug,
                            agent=str(cfg.agent_key), cron=cfg.schedule_cron)
                continue
            wanted[job_id] = (trigger, firm.id, str(cfg.agent_key), cfg.schedule_cron)

    existing = {j.id: j for j in scheduler.get_jobs() if j.id.startswith("agent:")}

    added = changed = removed = 0
    for job_id, (trigger, firm_id, agent_key, cron) in wanted.items():
        current = existing.get(job_id)
        # Re-adding an unchanged job every cycle is not free: it recomputes the next fire
        # time, so a sync landing on a fire time can displace that run, and it buries any
        # real change under a dozen identical "Added job" lines every 15 minutes.
        if current is not None and str(current.trigger) == str(trigger):
            continue
        scheduler.add_job(_run_scheduled_agent, trigger, id=job_id, replace_existing=True,
                          args=[firm_id, agent_key], max_instances=1, coalesce=True,
                          misfire_grace_time=3600)
        if current is None:
            added += 1
        else:
            changed += 1
            log.info("scheduler_cadence_changed", job=job_id, cron=cron)

    for stale in set(existing) - set(wanted):
        scheduler.remove_job(stale)
        removed += 1

    # Logged every cycle even when idle: a periodic "12 active" line is how you tell a
    # working scheduler from a dead one without waiting hours for a fire that never comes.
    log.info("agent_schedules_synced", active=len(wanted),
             added=added, changed=changed, removed=removed)


async def main() -> None:
    configure_logging()
    log.info("worker_starting")
    # Give the API time to bootstrap schema + seed.
    await asyncio.sleep(15)

    # Pinned, not inherited. Without a timezone APScheduler takes the container's local
    # one, so a base-image change could silently move every cadence by hours — and the
    # rationale in agents/schedules.py ("before the adviser's day starts") would quietly
    # stop being true. The container is UTC today; this makes that a decision, not luck.
    scheduler = AsyncIOScheduler(timezone=timezone.utc)
    scheduler.add_job(_refresh_market_data, "interval", minutes=60, id="market", next_run_time=None)
    # Drift is no longer registered here — it is scheduled from AgentConfig like every
    # other scheduled agent, at the same 6-hourly cadence (see agents/schedules.py). One
    # mechanism rather than two, and its cadence is now changeable in Admin.
    scheduler.add_job(_run_evaluation, "interval", hours=12, id="evaluation")
    scheduler.add_job(_heartbeat, "interval", seconds=40, id="heartbeat")
    scheduler.add_job(_check_holding_alerts, "interval", hours=1, id="holding_alerts")
    # Pick up cadence changes made in Admin without needing a worker restart.
    scheduler.add_job(_sync_agent_schedules, "interval", minutes=15, id="schedule_sync",
                      args=[scheduler])
    scheduler.start()

    # Register the config-driven agent jobs before the first heartbeat, so the roster
    # reflects what is actually scheduled rather than what merely declares itself so.
    try:
        await _sync_agent_schedules(scheduler)
    except Exception as exc:  # pragma: no cover
        log.warning("initial_schedule_sync_failed", error=str(exc))
    log.info("worker_started", jobs=[j.id for j in scheduler.get_jobs()])
    try:
        await _heartbeat()  # an immediate first pulse
    except Exception as exc:  # pragma: no cover
        log.warning("heartbeat_failed", error=str(exc))

    # Keep the loop alive.
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
