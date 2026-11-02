"""An open tax memo kept true to the book after a trade.

Tax Intelligence runs on the 1st of the month, so a memo written before a fill went on describing
the book as it was: on 1 Nov a rebalance used Karthik Iyer's whole exemption while his open memo
still offered it (B-79). A memo is stale when an order on one of its household's accounts settled
after the run that last wrote it started. The worker then reruns the agent for that household
alone, which refreshes the memo in place through `run_agent`, or closes it when nothing is left to
raise. A run that fails is kept as a failed run, and the household waits for its next fill.

Nothing here decides anything: the memo stays a proposal, and only its figures and words change.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.atlas import proposals
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.core.db import utcnow
from app.core.logging import get_logger
from app.models.enums import AgentKey, AgentRunStatus, RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Account, LegalEntity, Mandate, Person
from app.models.tenant import AgentConfig, Firm
from app.models.trading import Order

log = get_logger("aurea.tax_refresh")

TRIGGER = "orders_settled"


def _aware(moment: datetime | None) -> datetime | None:
    return moment.replace(tzinfo=timezone.utc) if moment and moment.tzinfo is None else moment


async def _last_settled(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, datetime]:
    """When an order last settled on each household's accounts."""
    household = func.coalesce(Person.household_id, LegalEntity.household_id)
    rows = (await session.execute(
        select(household, func.max(Order.settled_at))
        .select_from(Order)
        .join(Account, Account.id == Order.account_id)
        .join(Mandate, Mandate.id == Account.mandate_id)
        .outerjoin(Person, Person.id == Mandate.person_id)
        .outerjoin(LegalEntity, LegalEntity.id == Mandate.entity_id)
        .where(Order.firm_id == firm_id, Order.settled_at.isnot(None))
        .group_by(household)
    )).all()
    return {hid: _aware(at) for hid, at in rows if hid is not None}


async def _run_started(session: AsyncSession, run_id) -> datetime | None:
    run = await session.get(AgentRun, uuid.UUID(str(run_id))) if run_id else None
    return _aware(run.started_at) if run else None


async def stale_memos(session: AsyncSession, firm: Firm) -> list[Recommendation]:
    """Open tax memos written before their household's latest settled order, and not tried since."""
    settled = await _last_settled(session, firm.id)
    if not settled:
        return []
    memos = (await session.execute(
        select(Recommendation).where(
            Recommendation.firm_id == firm.id,
            Recommendation.agent_key == AgentKey.TAX_INTELLIGENCE,
            Recommendation.status == RecommendationStatus.PROPOSED,
            Recommendation.subject_type == "household",
            Recommendation.subject_id.in_(list(settled)),
        )
    )).scalars().all()
    out = []
    for rec in memos:
        written = await _run_started(
            session, ((rec.payload or {}).get("refreshed") or {}).get("last_run_id") or rec.run_id)
        tried = _aware((await session.execute(
            select(func.max(AgentRun.started_at)).where(
                AgentRun.firm_id == firm.id, AgentRun.agent_key == AgentKey.TAX_INTELLIGENCE,
                AgentRun.trigger == TRIGGER, AgentRun.subject_id == rec.subject_id)
        )).scalar_one())
        since = max((t for t in (written, tried) if t), default=None)
        if since is None or settled[rec.subject_id] > since:
            out.append(rec)
    return out


async def refresh_after_trades(session: AsyncSession, firm: Firm) -> dict:
    """Rerun Tax Intelligence for each household whose open memo predates a fill. Commits per household."""
    cfg = (await session.execute(select(AgentConfig).where(
        AgentConfig.firm_id == firm.id, AgentConfig.agent_key == AgentKey.TAX_INTELLIGENCE))).scalar_one_or_none()
    if cfg is not None and (not cfg.enabled or cfg.paused):
        return {"refreshed": 0, "closed": 0, "failed": 0}

    # Plain values: a rollback expires every loaded row, and reading one afterwards is IO outside async.
    firm_id, slug = firm.id, firm.slug
    stale = [(rec.id, rec.subject_id, rec.subject_label, rec.tier) for rec in await stale_memos(session, firm)]
    refreshed = closed = failed = 0
    for rec_id, household_id, label, tier in stale:
        subject = Subject("household", household_id, label)
        try:
            run = await run_agent(session, firm=firm, agent_key=AgentKey.TAX_INTELLIGENCE, subject=subject,
                                  trigger=TRIGGER, require_human=True)
            rec = await session.get(Recommendation, rec_id)
            if str(rec_id) in ((run.context or {}).get("refreshed") or []):
                refreshed += 1
            elif rec.status == RecommendationStatus.PROPOSED:
                # The household has nothing left to raise, so the memo no longer stands.
                await proposals.expire(session, rec, run_id=run.id,
                                       reason="Nothing left to raise once the book had traded.")
                closed += 1
            await session.commit()
        except AgentPausedError:
            await session.rollback()
            await session.refresh(firm)
            break
        except Exception as exc:  # the run is kept as failed, so this fill is not retried every tick
            await session.rollback()
            failed += 1
            session.add(AgentRun(
                firm_id=firm_id, agent_key=AgentKey.TAX_INTELLIGENCE, status=AgentRunStatus.FAILED,
                tier=tier, trigger=TRIGGER, subject_type="household", subject_id=household_id,
                subject_label=label, started_at=utcnow(), finished_at=utcnow(), error=str(exc)[:2000], context={}))
            await session.commit()
            await session.refresh(firm)
            log.warning("tax_refresh_failed", firm=slug, household=str(household_id), error=str(exc))
    if stale:
        log.info("tax_refresh", firm=slug, stale=len(stale), refreshed=refreshed, closed=closed, failed=failed)
    return {"refreshed": refreshed, "closed": closed, "failed": failed}
