"""Client reports: what the Reports page and the conversation both read.

Research & Reporting drafts a report and proposes it. A report reaches a client one way, a person
approving that proposal, and leaves one way, that proposal being rolled back. So nothing here
publishes anything: a report is read together with its newest proposal, which says what can happen
to it next. Everything is firm-scoped: a report from another firm reads as not found.
"""
from __future__ import annotations

import re
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engagement import ClientReport
from app.models.enums import AgentKey, RecommendationStatus, ReportStatus
from app.models.governance import Recommendation
from app.models.graph import Household

STATUSES = ("all", "draft", "client_ready")
LIST_LIMIT = 200
_AGENT = AgentKey.RESEARCH_REPORTING.value
# The agents that draft a client report and propose it: the portfolio review, and the plan summary (B-196), which is
# drafted from the plan of record alone (plan_summary.py). Each keeps its own draft per household.
REPORT_AGENTS: tuple[str, ...] = (_AGENT, AgentKey.PLAN_SUMMARY.value)
_PUBLISHED = (RecommendationStatus.APPROVED.value, RecommendationStatus.MODIFIED.value,
              RecommendationStatus.EXECUTED.value)


# A draft whose figures are older than this is flagged on the card that would put it in front of the client (B-55).
# Thirty days is one monthly valuation cycle: past it the figures have missed at least one month-end valuation and a
# month of markets, and a firm reports monthly at the most often, so a client would be reading last period's numbers.
# A redraft (run research_reporting) refreshes the same draft and its date.
STALE_AFTER_DAYS = 30
_AS_AT = re.compile(r"As at (\d{4}-\d{2}-\d{2})")


def figures_as_of(report: ClientReport) -> date | None:
    """The day a draft's figures were taken: recorded on the draft by Research & Reporting, else read from the period
    it was drafted with ("As at YYYY-MM-DD"). None when neither says, and then no age is given, not a guess."""
    raw = (report.data or {}).get("as_of")
    if not raw:
        m = _AS_AT.search(report.period or "")
        raw = m.group(1) if m else None
    try:
        return date.fromisoformat(str(raw)) if raw else None
    except ValueError:
        return None


def age(report: ClientReport, today: date) -> dict:
    """How old a draft's figures are on `today`, and whether that is past STALE_AFTER_DAYS."""
    taken = figures_as_of(report)
    days = (today - taken).days if taken else None
    return {"as_of": taken.isoformat() if taken else None, "days": days,
            "stale": days is not None and days > STALE_AFTER_DAYS, "stale_after_days": STALE_AFTER_DAYS}


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
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key.in_(REPORT_AGENTS))
        .order_by(Recommendation.created_at.desc()))).scalars().all()
    out: dict[str, Recommendation] = {}
    for rec in rows:
        report_id = (rec.payload or {}).get("report_id")
        if not report_id:
            continue
        held = out.get(str(report_id))
        # Two raised in one transaction share its timestamp: the one a redraft superseded is not the newest.
        if held is None or (held.created_at == rec.created_at and str(held.status) == RecommendationStatus.EXPIRED.value
                            and str(rec.status) != RecommendationStatus.EXPIRED.value):
            out[str(report_id)] = rec
    return out


async def waiting_draft(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID, *,
                        agent: str = _AGENT) -> ClientReport | None:
    """The household's draft from `agent` still waiting for a decision, if it has one. A portfolio review and a plan
    summary each keep their own, so redrafting one never rewrites the other."""
    rows = (await session.execute(
        select(Recommendation).where(Recommendation.firm_id == firm_id, Recommendation.agent_key == agent,
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
        "kind": (r.data or {}).get("kind") or "portfolio_review",
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
