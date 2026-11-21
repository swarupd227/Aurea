"""AI use-case inventory, change log, and entitlement-violation analytics (L200-8 §8).

Pure read/write helpers over the three tables in `app.models.ai_governance`, plus a
summary that pulls in the platform's existing eval-gate and usage signals rather than
re-computing them — the inventory's job is to attach ownership/risk-tier context to
those, not to duplicate them.
"""
from __future__ import annotations

import uuid
from datetime import timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.tenant import Firm
from app.llm import usage as llm_usage
from app.models.ai_governance import AIChangeLogEntry, AIUseCase, EntitlementViolation


def _aware(dt):
    return dt if dt is None or dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def record_violation(
    session: AsyncSession, firm_id: uuid.UUID, *, actor_user_id: uuid.UUID | None,
    actor_role: str, violation_type: str, subject_key: str, detail: str,
) -> EntitlementViolation:
    row = EntitlementViolation(
        firm_id=firm_id, occurred_at=utcnow(), actor_user_id=actor_user_id, actor_role=actor_role,
        violation_type=violation_type, subject_key=subject_key, detail=detail, blocked=True,
    )
    session.add(row)
    await session.flush()
    return row


async def log_change(
    session: AsyncSession, firm_id: uuid.UUID, *, use_case_id: uuid.UUID | None, change_type: str,
    description: str, changed_by: str, previous_value: str | None = None, new_value: str | None = None,
) -> AIChangeLogEntry:
    row = AIChangeLogEntry(
        firm_id=firm_id, use_case_id=use_case_id, change_type=change_type, description=description,
        previous_value=previous_value, new_value=new_value, changed_by=changed_by, changed_at=utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


# The planning engines (B-189, L200-2 §4.1): each answers a client's question with a model, so each is on the
# inventory with its method, an owner and a review, and the assumption version it runs on.
PLANNING_MODELS = {
    "retirement_engine": ("Retirement income engine", "aurea_core/retirement.py",
                          "Monte Carlo over the firm's returns: accumulation, then each year's need less that year's "
                          "guaranteed income until the younger adult's plan-to age; sustainable income by search; a "
                          "first-year crash and a sequence illustration."),
    "goal_likelihood_engine": ("Goal likelihood engine", "aurea_core/goal_tradeoff.py",
                               "Monte Carlo per goal over the firm's returns: funded from its recorded share, else the pool "
                               "in priority order; targets grown by their own inflation index."),
    "financial_plan_engine": ("Financial plan", "aurea_core/financial_plan.py",
                              "Deterministic assembly of what is on file (spending, streams, debts, education) over the "
                              "retirement engine and goal likelihoods, in today's money."),
    "protection_engine": ("Protection needs", "aurea_core/protection.py",
                          "Deterministic needs analysis from recorded incomes, ages, debts and policies; care from a "
                          "firm-stated cost only; nothing estimated."),
}


def _review_due(uc: AIUseCase):
    anchor = _aware(uc.last_reviewed_at) or _aware(uc.created_at)
    return anchor + timedelta(days=uc.review_cadence_days) if anchor else None


async def models(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    """The planning engines on the inventory, each with the assumption version it runs on today."""
    from app.aurea_core import planning

    firm = await session.get(Firm, firm_id)
    settings = firm.settings if firm else None
    version, source = planning.assumption_version(settings), planning.cma_source(settings)
    rows = (await session.execute(select(AIUseCase).where(
        AIUseCase.firm_id == firm_id, AIUseCase.category == "model"))).scalars().all()
    now, out = utcnow(), []
    for uc in sorted(rows, key=lambda r: r.name):
        name, module, method = PLANNING_MODELS.get(uc.use_case_key, (uc.name, None, uc.description))
        due = _review_due(uc)
        out.append({"use_case_key": uc.use_case_key, "name": uc.name, "module": module, "method": method,
                    "owner": uc.owner, "risk_tier": uc.risk_tier, "status": uc.status,
                    "assumptions": {"source": source, "version": version},
                    "last_reviewed_at": uc.last_reviewed_at.isoformat() if uc.last_reviewed_at else None,
                    "review_due": due.date().isoformat() if due else None,
                    "overdue": bool(due and now > due)})
    return out


async def inventory_summary(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    use_cases = (
        await session.execute(select(AIUseCase).where(AIUseCase.firm_id == firm_id))
    ).scalars().all()

    now = utcnow()
    overdue = []
    by_tier = {"low": 0, "medium": 0, "high": 0}
    for uc in use_cases:
        if uc.status != "active":
            continue
        by_tier[uc.risk_tier] = by_tier.get(uc.risk_tier, 0) + 1
        anchor = _aware(uc.last_reviewed_at) or _aware(uc.created_at)
        if now > anchor + timedelta(days=uc.review_cadence_days):
            overdue.append({"use_case_key": uc.use_case_key, "name": uc.name, "risk_tier": uc.risk_tier})

    usage = await llm_usage.summary(session, firm_id)

    violations = (
        await session.execute(select(EntitlementViolation).where(EntitlementViolation.firm_id == firm_id))
    ).scalars().all()

    recent_changes = (
        await session.execute(
            select(AIChangeLogEntry).where(AIChangeLogEntry.firm_id == firm_id)
            .order_by(AIChangeLogEntry.changed_at.desc()).limit(10)
        )
    ).scalars().all()

    return {
        "total_use_cases": len(use_cases),
        "models": await models(session, firm_id),
        "active": sum(1 for uc in use_cases if uc.status == "active"),
        "by_risk_tier": by_tier,
        "overdue_review": sorted(overdue, key=lambda r: r["risk_tier"] != "high"),
        "entitlement_violations": {
            "total": len(violations),
            "by_type": _count_by(violations, "violation_type"),
        },
        "recent_changes": [{
            "change_type": c.change_type, "description": c.description,
            "changed_by": c.changed_by, "changed_at": c.changed_at.isoformat(),
        } for c in recent_changes],
        "usage": {"calls": usage["calls"], "est_cost": usage["est_cost"], "fallback_rate": usage["fallback_rate"]},
    }


def _count_by(rows: list, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = getattr(r, field)
        out[key] = out.get(key, 0) + 1
    return out
