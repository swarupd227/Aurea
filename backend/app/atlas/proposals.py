"""One open proposal per thing, not one per run.

A scheduled agent that finds the same condition on Tuesday that it found on Monday used to
raise it again, so a case idle for sixty days sat in the queue sixty times and a reviewer had
to clear all of them. A person decides about a *subject* ("is this stalled onboarding being
chased?"), not about a run, so while a proposal is still open a later run refreshes it
instead of adding another.

Identity is (firm, agent, subject, title stem). The stem is the title with its numbers
removed, because the numbers are what change from day to day: "Stalled onboarding — Priya
Raman (71d idle)" and "(72d idle)" are one proposal. An agent that raises several different
proposals about one subject keeps them apart through their different titles.

Only PROPOSED rows are ever touched, under a row lock, so a decision landing at the same
moment is never edited out from under the person making it.
"""
from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.enums import RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.provenance import ledger

_NUMBER = re.compile(r"\d[\d,.]*")

Identity = tuple[str, str, str, str]


def stem(title: str | None) -> str:
    """The title with its numbers taken out — what stays the same while a case ages."""
    return re.sub(r"\s+", " ", _NUMBER.sub("#", title or "")).strip().lower()


def identity(
    agent_key, subject_type: str | None, subject_id: uuid.UUID | None,
    subject_label: str | None, title: str | None,
) -> Identity:
    return (str(agent_key), subject_type or "",
            str(subject_id) if subject_id else (subject_label or ""), stem(title))


def rec_identity(rec: Recommendation) -> Identity:
    return identity(rec.agent_key, rec.subject_type, rec.subject_id, rec.subject_label, rec.title)


async def open_from_earlier_runs(
    session: AsyncSession, *, firm_id: uuid.UUID, agent_key, run_id: uuid.UUID,
) -> dict[Identity, list[Recommendation]]:
    """This agent's open proposals from before this run, newest first, locked for update."""
    rows = (await session.execute(
        select(Recommendation)
        .join(AgentRun, AgentRun.id == Recommendation.run_id)
        .where(Recommendation.firm_id == firm_id,
               Recommendation.agent_key == agent_key,
               Recommendation.status == RecommendationStatus.PROPOSED,
               Recommendation.run_id != run_id)
        .order_by(AgentRun.started_at.desc(), Recommendation.created_at.desc())
        .with_for_update(of=Recommendation)
    )).scalars().all()
    out: dict[Identity, list[Recommendation]] = {}
    for rec in rows:
        out.setdefault(rec_identity(rec), []).append(rec)
    return out


async def expire(
    session: AsyncSession, rec: Recommendation, *, run_id: uuid.UUID | None, reason: str,
) -> None:
    """Close an open proposal nobody decided because it no longer stands. The ledger says why."""
    rec.status = RecommendationStatus.EXPIRED
    rec.decided_at = utcnow()
    rec.decision_note = reason
    await ledger.append_entry(
        session, firm_id=rec.firm_id, event_type="recommendation_expired",
        agent_key=rec.agent_key, run_id=run_id or rec.run_id, recommendation_id=rec.id,
        actor="agent", content={"recommendation": rec.title, "subject": rec.subject_label,
                                "reason": reason},
    )


async def refresh(
    session: AsyncSession, rec: Recommendation, draft, *, run_id: uuid.UUID, trigger: str,
) -> None:
    """Bring an open proposal up to date with what this run found. Its place in the queue,
    its id and its history stay; the numbers and the wording are the latest."""
    seen = ((rec.payload or {}).get("refreshed") or {}).get("times", 1) + 1
    rec.title = draft.title
    rec.summary = draft.summary
    rec.rationale = draft.rationale
    rec.confidence = draft.confidence
    rec.priority = draft.priority
    rec.subject_label = draft.subject.label or rec.subject_label
    rec.payload = {**(draft.payload or {}),
                   "refreshed": {"times": seen, "last_run_id": str(run_id),
                                 "last_at": utcnow().isoformat()}}
    rec.evidence = draft.evidence
    rec.citations = draft.citations
    await ledger.append_entry(
        session, firm_id=rec.firm_id, event_type="recommendation_refreshed",
        agent_key=rec.agent_key, run_id=run_id, recommendation_id=rec.id, actor="agent",
        content={"trigger": trigger, "agent": rec.agent_key, "subject": rec.subject_label,
                 "recommendation": rec.title, "summary": rec.summary, "rationale": rec.rationale,
                 "confidence": rec.confidence, "data_used": rec.evidence, "payload": rec.payload,
                 "refreshed": True, "times_seen": seen},
    )


async def supersede_earlier(
    session: AsyncSession, rec: Recommendation, *, run_id: uuid.UUID,
) -> list[str]:
    """A run a person asked for stands on its own: its new proposal replaces any still-open one
    from an earlier run about the same thing, instead of sitting beside it ("prepare a meeting
    brief" twice should leave one brief to decide, the newer). Returns the ids it closed."""
    same = (await open_from_earlier_runs(
        session, firm_id=rec.firm_id, agent_key=rec.agent_key, run_id=run_id)).get(rec_identity(rec), [])
    for old in same:
        await expire(session, old, run_id=run_id,
                     reason=f"Superseded by a newer proposal for the same subject ({rec.id}).")
    return [str(old.id) for old in same]


def newest_run_only(recs: list[Recommendation]) -> tuple[list[Recommendation], list[Recommendation]]:
    """Split one identity's open proposals (newest first) into those from the latest run and
    those from earlier runs. An agent may raise several proposals about one subject in a
    single run; those belong together and are not duplicates of each other."""
    latest = recs[0].run_id
    return ([r for r in recs if r.run_id == latest], [r for r in recs if r.run_id != latest])


async def collapse_open_duplicates(
    session: AsyncSession, *, firm_id: uuid.UUID, agent_keys=None,
) -> dict:
    """Close the duplicates already in a firm's queue: keep each identity's newest run's
    proposals and expire those left by earlier runs, saying which run replaced them.
    Idempotent. `agent_keys` limits it to those agents."""
    stmt = (select(Recommendation)
            .join(AgentRun, AgentRun.id == Recommendation.run_id)
            .where(Recommendation.firm_id == firm_id,
                   Recommendation.status == RecommendationStatus.PROPOSED)
            .order_by(AgentRun.started_at.desc(), Recommendation.created_at.desc())
            .with_for_update(of=Recommendation))
    if agent_keys is not None:
        stmt = stmt.where(Recommendation.agent_key.in_(list(agent_keys)))
    rows = (await session.execute(stmt)).scalars().all()
    groups: dict[Identity, list[Recommendation]] = {}
    for rec in rows:
        groups.setdefault(rec_identity(rec), []).append(rec)

    expired, by_agent = 0, {}
    for recs in groups.values():
        keep, older = newest_run_only(recs)
        for rec in older:
            await expire(session, rec, run_id=None,
                         reason=f"Superseded by a newer open proposal for the same subject ({keep[0].id}).")
            expired += 1
            by_agent[str(rec.agent_key)] = by_agent.get(str(rec.agent_key), 0) + 1
    return {"open_before": len(rows), "kept": len(rows) - expired, "expired": expired,
            "by_agent": by_agent}
