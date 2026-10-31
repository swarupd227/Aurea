"""Client reports: what the Reports page and the conversation both read.

Research & Reporting drafts a report and proposes it. A report reaches a client one way, a person
approving that proposal, and leaves one way, that proposal being rolled back. So nothing here
publishes anything: a report is read together with its newest proposal, which says what can happen
to it next. Everything is firm-scoped: a report from another firm reads as not found.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import ClientReport
from app.models.enums import AgentKey, RecommendationStatus, ReportStatus
from app.models.governance import Recommendation
from app.models.graph import Household

STATUSES = ("all", "draft", "client_ready")
LIST_LIMIT = 200
_AGENT = AgentKey.RESEARCH_REPORTING.value
_PUBLISHED = (RecommendationStatus.APPROVED.value, RecommendationStatus.MODIFIED.value,
              RecommendationStatus.EXECUTED.value)


class ReportError(Exception):
    """A report that cannot be reached or changed, in words a person can act on."""


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, status: str = "all",
                   household_id: uuid.UUID | None = None) -> list[ClientReport]:
    """Newest first."""
    stmt = select(ClientReport).where(ClientReport.firm_id == firm_id)
    if status != "all":
        stmt = stmt.where(ClientReport.status == status)
    if household_id:
        stmt = stmt.where(ClientReport.household_id == household_id)
    return list((await session.execute(
        stmt.order_by(ClientReport.created_at.desc()).limit(LIST_LIMIT))).scalars().all())


async def get(session: AsyncSession, firm_id: uuid.UUID, report_id: uuid.UUID) -> ClientReport:
    r = await session.get(ClientReport, report_id)
    if r is None or r.firm_id != firm_id:
        raise ReportError(f"Report {report_id} not found or access denied")
    return r


async def of_proposal(session: AsyncSession, firm_id: uuid.UUID, payload: dict | None) -> ClientReport | None:
    """The report a proposal is about, if it is one and it is still there."""
    try:
        return await get(session, firm_id, uuid.UUID(str((payload or {}).get("report_id"))))
    except (ValueError, ReportError):
        return None


async def proposals(session: AsyncSession, firm_id: uuid.UUID) -> dict[str, Recommendation]:
    """The newest proposal about each report, by report id. A seeded report has none."""
    rows = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key == _AGENT)
        .order_by(Recommendation.created_at.desc()))).scalars().all()
    out: dict[str, Recommendation] = {}
    for rec in rows:
        report_id = (rec.payload or {}).get("report_id")
        if report_id:
            out.setdefault(str(report_id), rec)
    return out


async def waiting_draft(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> ClientReport | None:
    """The household's draft still waiting for a decision, if it has one."""
    rows = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key == _AGENT,
                                     Recommendation.subject_id == household_id,
                                     Recommendation.status == RecommendationStatus.PROPOSED)
        .order_by(Recommendation.created_at.desc()))).scalars().all()
    for rec in rows:
        report = await of_proposal(session, firm_id, rec.payload)
        if report and str(report.status) == ReportStatus.DRAFT.value:
            return report
    return None


def withdrawable(report: ClientReport, rec: Recommendation | None) -> bool:
    """With the client, through a proposal that can be rolled back. A seeded report has no proposal."""
    return str(report.status) == ReportStatus.CLIENT_READY.value and rec is not None and str(rec.status) in _PUBLISHED


def _view(r: ClientReport, household: str | None, rec: Recommendation | None, *, full: bool) -> dict:
    out = {
        "id": str(r.id), "title": r.title, "period": r.period, "status": str(r.status),
        "household_id": str(r.household_id), "household_name": household, "n_sections": len(r.sections or []),
        "published_at": r.published_at.isoformat() if r.published_at else None,
        "recommendation_id": str(rec.id) if rec else None,
        "proposal_status": str(rec.status) if rec else None,
        "awaiting_decision": rec is not None and str(rec.status) == RecommendationStatus.PROPOSED.value,
        "can_withdraw": withdrawable(r, rec),
    }
    return {**out, "sections": r.sections or [], "data": r.data or {}} if full else out


async def views(session: AsyncSession, firm_id: uuid.UUID, rows: list[ClientReport], *, full: bool = False) -> list[dict]:
    names = {h.id: h.name for h in (await session.execute(
        select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    latest = await proposals(session, firm_id)
    return [_view(r, names.get(r.household_id), latest.get(str(r.id)), full=full) for r in rows]
