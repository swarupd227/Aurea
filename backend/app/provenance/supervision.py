"""Supervision: closing or escalating a conduct-surveillance flag, and re-running the evaluation that can
narrow an agent's autonomy.

These are compliance's and the branch leader's, the people the Provenance page is for. An adviser does not
close a flag raised about advice, and an administrator configures the platform rather than supervising it,
though one may re-run the evaluation. Every change goes on the ledger with who made it and why.

Resolving a flag does not unpause an agent the flag paused: that is a separate decision, taken in Admin.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.core.db import utcnow
from app.models.enums import UserRole
from app.models.governance import SurveillanceFlag
from app.models.tenant import AgentConfig
from app.provenance import ledger
from app.provenance.evaluation import MIN_SAMPLE, TIER_ORDER, compute_metrics

SUPERVISORS: frozenset[UserRole] = frozenset({UserRole.COMPLIANCE, UserRole.BRANCH_LEADER})
EVALUATORS: frozenset[UserRole] = SUPERVISORS | {UserRole.ADMIN}


class SupervisionError(Exception):
    """Refused, with a reason fit to show the person."""


def agent_name(key: str | None) -> str | None:
    return ((CATALOGUE.get(key) or {}).get("name") or str(key).replace("_", " ").title()) if key else None


async def flag(session: AsyncSession, firm_id: uuid.UUID, flag_id: uuid.UUID) -> SurveillanceFlag:
    row = await session.get(SurveillanceFlag, flag_id)
    if row is None or row.firm_id != firm_id:
        raise SupervisionError("That surveillance flag was not found.")
    return row


def _note(note: str | None, what: str) -> str:
    if len((note or "").strip()) < 3:
        raise SupervisionError(f"Say why the flag is {what}. It goes on the ledger with your name.")
    return note.strip()


def check_resolve(row: SurveillanceFlag, note: str | None) -> str:
    if row.resolved:
        raise SupervisionError("This flag is already resolved.")
    return _note(note, "resolved")


def check_escalate(row: SurveillanceFlag, to: str | None, note: str | None) -> tuple[str, str]:
    if row.resolved:
        raise SupervisionError("This flag is already resolved, so there is nothing to escalate.")
    if len((to or "").strip()) < 2:
        raise SupervisionError("Say who it is escalated to: a person or a role.")
    return to.strip()[:200], _note(note, "escalated")


async def resolve(session: AsyncSession, row: SurveillanceFlag, *, note: str, by: str) -> dict:
    """Close a flag. The row is locked and re-read, so two people cannot both close it."""
    row = (await session.execute(select(SurveillanceFlag).where(SurveillanceFlag.id == row.id)
                                 .with_for_update().execution_options(populate_existing=True))).scalar_one()
    note = check_resolve(row, note)
    row.resolved, row.resolution_note, row.resolved_by, row.resolved_at = True, note, by, utcnow()
    await ledger.append_entry(session, firm_id=row.firm_id, event_type="flag_resolved", agent_key=row.target_agent_key,
                              actor=by, content={"flag_id": str(row.id), "severity": row.severity,
                                                 "category": row.category, "resolution_note": note})
    await session.flush()
    return {"flag_id": str(row.id), "resolved": True, "agent_still_paused": bool(row.auto_paused_agent)}


async def escalate(session: AsyncSession, row: SurveillanceFlag, *, to: str, note: str, by: str) -> dict:
    to, note = check_escalate(row, to, note)
    row.escalated, row.escalated_to, row.escalated_at, row.escalation_note = True, to, utcnow(), note
    await ledger.append_entry(session, firm_id=row.firm_id, event_type="flag_escalated", agent_key=row.target_agent_key,
                              actor=by, content={"flag_id": str(row.id), "severity": row.severity,
                                                 "escalated_to": to, "note": note})
    await session.flush()
    return {"flag_id": str(row.id), "escalated_to": to}


async def would_narrow(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    """What re-running the evaluation would do to each agent's autonomy, worked out without writing anything."""
    configs = {str(c.agent_key): c for c in (await session.execute(
        select(AgentConfig).where(AgentConfig.firm_id == firm_id))).scalars().all()}
    out = []
    for key in CATALOGUE:
        cfg = configs.get(str(key))
        if cfg is None or cfg.paused:
            continue
        m = await compute_metrics(session, firm_id, key)
        if m["grade"] != "regressed" or m["sample_size"] < MIN_SAMPLE:
            continue
        idx = TIER_ORDER.index(cfg.default_tier) if cfg.default_tier in TIER_ORDER else 0
        out.append({"agent_key": str(key), "agent": agent_name(str(key)), "score": m["quality_score"],
                    "from": str(cfg.default_tier), "to": str(TIER_ORDER[idx - 1]) if idx > 0 else None, "paused": idx == 0})
    return out
