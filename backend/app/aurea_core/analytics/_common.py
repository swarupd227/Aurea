"""Shared helpers for the analytics layers."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain, list_households


async def gather_brains(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    """Load every household's brain snapshot once (analytics read from one source)."""
    out = []
    for h in await list_households(session, firm_id):
        brain = await household_brain(session, uuid.UUID(h["id"]))
        if brain:
            out.append(brain)
    return out


def positions_of(brain: dict) -> list[dict]:
    return [p for acc in brain.get("accounts", []) for p in acc.get("positions", [])]


def age_from(dob: str | None) -> int | None:
    if not dob:
        return None
    try:
        y, m, d = map(int, dob.split("-"))
        today = date.today()
        return today.year - y - ((today.month, today.day) < (m, d))
    except Exception:
        return None


def pct(n: float, d: float) -> float:
    return round(n / d, 4) if d else 0.0


async def goal_projections_all(brains: list[dict]) -> list[dict]:
    """Goal projections across every household (reuses the planning engine)."""
    from app.agents._signals import goal_projections

    out = []
    for b in brains:
        for g in goal_projections(b):
            out.append({"household": b["household"]["name"], **g})
    return out
