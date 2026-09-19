"""Conflicts inventory + WSP grid analytics (L200-7 §2.2, §3.1, §10).

Pure read computations over the two governed artifacts in
`app.models.compliance_program` — no I/O beyond the session passed in, mirroring the
`aurea_core/analytics/*` layer's shape.
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.compliance_program import ConflictInventoryItem, WSPRule

_FREQUENCY_DAYS = {
    "daily": 1, "weekly": 7, "monthly": 31, "quarterly": 93,
    "annual": 366, "per_event": None,  # per_event rows are never "overdue" on a clock
}


def _aware(dt):
    """SQLite round-trips a DateTime(timezone=True) column as naive; Postgres does not.
    Normalise to aware-UTC so callers can compare against utcnow() either way."""
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def conflicts_summary(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Status counts and which conflicts are overdue for their review cadence (§2.2)."""
    items = (await session.execute(
        select(ConflictInventoryItem).where(ConflictInventoryItem.firm_id == firm_id)
    )).scalars().all()

    now = utcnow()
    overdue = []
    for c in items:
        if c.status != "active":
            continue
        anchor = _aware(c.last_reviewed_at) or _aware(c.created_at)
        due_by = anchor + timedelta(days=c.review_cadence_days)
        if now > due_by:
            overdue.append({
                "conflict_key": c.conflict_key, "title": c.title,
                "last_reviewed_at": c.last_reviewed_at.isoformat() if c.last_reviewed_at else None,
                "days_overdue": (now - due_by).days,
            })

    return {
        "total": len(items),
        "active": sum(1 for c in items if c.status == "active"),
        "retired": sum(1 for c in items if c.status == "retired"),
        "overdue_review": sorted(overdue, key=lambda r: -r["days_overdue"]),
    }


async def evidence_coverage(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """L200-7 §10's 'Evidence coverage' metric: automated vs. attestation-only WSP rows,
    plus which active rows have gone stale against their own stated frequency — the
    "attestation-only rows are the risk backlog" reading the module gives the metric."""
    rows = (await session.execute(
        select(WSPRule).where(WSPRule.firm_id == firm_id, WSPRule.is_active == True)  # noqa: E712
    )).scalars().all()

    now = utcnow()
    stale = []
    for r in rows:
        window = _FREQUENCY_DAYS.get(r.frequency)
        if window is None:
            continue
        last = _aware(r.last_evidence_at)
        if last is None or (now - last).days > window:
            stale.append({
                "rule_key": r.rule_key, "obligation": r.obligation, "frequency": r.frequency,
                "last_evidence_at": last.isoformat() if last else None,
                "evidence_automated": r.evidence_automated,
            })

    automated = sum(1 for r in rows if r.evidence_automated)
    total = len(rows) or 1
    return {
        "total_rules": len(rows),
        "automated_evidence": automated,
        "manual_attestation": len(rows) - automated,
        "coverage_pct": round(100 * automated / total, 1),
        "stale_evidence": sorted(stale, key=lambda r: r["rule_key"]),
    }
