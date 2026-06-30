"""The agent scheduler (worker process).

Periodically triggers the scheduled monitoring agents (drift, next-best-action, conduct
surveillance) across active mandates/households — the 'sense' that keeps the workforce
proactive (spec §7 'without waiting to be asked'). Runs as its own container in compose."""
from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.conduit.service import sync_market_data
from app.core.db import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models.enums import AgentKey, MandateType
from app.models.graph import Mandate
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


async def _run_drift_monitor() -> None:
    async with SessionLocal() as s:
        firms = (await s.execute(select(Firm))).scalars().all()
        for firm in firms:
            cfg = (
                await s.execute(
                    select(AgentConfig).where(
                        AgentConfig.firm_id == firm.id,
                        AgentConfig.agent_key == AgentKey.DRIFT_REBALANCING,
                    )
                )
            ).scalar_one_or_none()
            if not cfg or not cfg.enabled or cfg.paused:
                continue
            mandates = (
                await s.execute(
                    select(Mandate).where(
                        Mandate.firm_id == firm.id,
                        Mandate.is_active.is_(True),
                        Mandate.model_portfolio_id.isnot(None),
                    )
                )
            ).scalars().all()
            for m in mandates:
                try:
                    await run_agent(
                        s, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING,
                        subject=Subject("mandate", m.id, m.name), trigger="scheduled_monitor",
                        mandate_type=MandateType(m.mandate_type),
                    )
                    await s.commit()
                except AgentPausedError:
                    await s.rollback()
                except Exception as exc:  # pragma: no cover
                    await s.rollback()
                    log.warning("drift_monitor_failed", mandate=str(m.id), error=str(exc))


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


async def main() -> None:
    configure_logging()
    log.info("worker_starting")
    # Give the API time to bootstrap schema + seed.
    await asyncio.sleep(15)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(_refresh_market_data, "interval", minutes=60, id="market", next_run_time=None)
    scheduler.add_job(_run_drift_monitor, "interval", hours=6, id="drift")
    scheduler.add_job(_run_evaluation, "interval", hours=12, id="evaluation")
    scheduler.add_job(_heartbeat, "interval", seconds=40, id="heartbeat")
    scheduler.add_job(_check_holding_alerts, "interval", hours=1, id="holding_alerts")
    scheduler.start()
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
