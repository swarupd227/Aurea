"""What a firm's SEBI licence lets an agent do, client by client.

In India the licence attaches to the client relationship and decides what may lawfully happen, so a
firm's autonomy tier cannot be the only word. An Investment Adviser needs the client's specific and
positive consent to every trade and may not hold a power of attorney. A Research Analyst may not
execute at all. Only a discretionary Portfolio Manager acts without per-trade consent, inside the
mandate, and it stays answerable for every decision.

A firm holds licences in settings["licences"], and each mandate sits on one service track in
constraints["service_track"]. A firm with no licences on file is not governed here: this says
nothing about New Zealand, the US or the UK.

Autonomy stops short of acting alone for every licence, the discretionary one included. A SEBI order
of May 2026, known here only through one law-firm note, treated quantities, timing and client-level
translation as investment decisions, so for now a named person at the firm approves every order set.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import AutonomyTier
from app.models.graph import Household, LegalEntity, Mandate, Person

_TIER_ORDER = (AutonomyTier.TIER_1, AutonomyTier.TIER_2, AutonomyTier.TIER_3)


@dataclass(frozen=True)
class Licence:
    key: str
    name: str
    may_execute: bool           # may a trade be placed under this track at all
    per_trade_consent: bool     # does each trade need the client's own consent
    max_tier: AutonomyTier
    citation: str


LICENCES: dict[str, Licence] = {l.key: l for l in (
    Licence("investment_adviser", "Investment Adviser", True, True, AutonomyTier.TIER_2,
            "IA Master Circular (6 Feb 2026), MITC cl 5"),
    Licence("research_analyst", "Research Analyst", False, False, AutonomyTier.TIER_1,
            "RA Master Circular (Feb 2026)"),
    Licence("pms_discretionary", "Portfolio Manager, discretionary", True, False, AutonomyTier.TIER_2,
            "SEBI (Portfolio Managers) Regulations"),
    Licence("pms_non_discretionary", "Portfolio Manager, non-discretionary", True, True, AutonomyTier.TIER_2,
            "SEBI (Portfolio Managers) Regulations"),
    Licence("pms_advisory", "Portfolio Manager, advisory", False, False, AutonomyTier.TIER_1,
            "SEBI (Portfolio Managers) Regulations"),
    Licence("mutual_fund_distributor", "Mutual Fund Distributor", True, True, AutonomyTier.TIER_1,
            "MF Master Circular (Mar 2026)"),
)}


def held(firm) -> list[Licence]:
    keys = ((getattr(firm, "settings", None) or {}).get("licences")) or []
    return [LICENCES[k] for k in keys if k in LICENCES]


def describe(licence: Licence | None) -> dict | None:
    if licence is None:
        return None
    return {"key": licence.key, "name": licence.name, "may_execute": licence.may_execute,
            "per_trade_consent": licence.per_trade_consent, "max_tier": licence.max_tier.value,
            "citation": licence.citation}


def track_for(firm, mandate: Mandate | None) -> Licence | None:
    """The licence a mandate sits under. None where the firm holds none.

    A licensed firm's mandate on no recognised track gets the strictest licence the firm holds,
    since guessing the other way would place a trade nobody consented to."""
    licences = held(firm)
    if not licences:
        return None
    chosen = ((mandate.constraints or {}).get("service_track") if mandate else None)
    if chosen in {l.key for l in licences}:
        return LICENCES[chosen]
    return next((l for l in licences if not l.may_execute), None) or next(
        (l for l in licences if l.per_trade_consent), licences[0])


def ceiling(firm) -> AutonomyTier | None:
    """The highest tier any agent may run at in this firm. None where no licence applies."""
    licences = held(firm)
    if not licences:
        return None
    return max((l.max_tier for l in licences), key=_TIER_ORDER.index)


def cap(firm, tier: AutonomyTier) -> AutonomyTier:
    """A licence caps a tier and never raises one: the firm's preference can only narrow it."""
    limit = ceiling(firm)
    if limit is None:
        return tier
    return min(AutonomyTier(tier), limit, key=_TIER_ORDER.index)


def refusal(firm, tier: AutonomyTier) -> str | None:
    """Why this firm may not set this tier, or None if it may."""
    limit = ceiling(firm)
    if limit is None or _TIER_ORDER.index(AutonomyTier(tier)) <= _TIER_ORDER.index(limit):
        return None
    names = ", ".join(l.name for l in held(firm))
    return f"Licensed as {names}, this firm's agents may not run above {limit.value}."


async def overview(session: AsyncSession, firm) -> dict:
    """The firm's licences, what each lets an agent do, and which mandate sits where."""
    from app.aurea_core import consent

    licences = held(firm)
    mandates = (await session.execute(
        select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True)).order_by(Mandate.name)
    )).scalars().all()
    rows = []
    for m in mandates:
        owner = await session.get(Person, m.person_id) if m.person_id else None
        owner = owner or (await session.get(LegalEntity, m.entity_id) if m.entity_id else None)
        household = await session.get(Household, owner.household_id) if owner and owner.household_id else None
        track = track_for(firm, m)
        rows.append({"mandate_id": str(m.id), "mandate": m.name, "mandate_type": str(m.mandate_type),
                     "household": household.name if household else None,
                     "licence": track.key if track else None, "licence_name": track.name if track else None,
                     "per_trade_consent": track.per_trade_consent if track else None})
    limit = ceiling(firm)
    return {
        "applies": bool(licences),
        "licences": [describe(l) for l in licences],
        "ceiling": limit.value if limit else None,
        "demonstration": bool((firm.settings or {}).get("demonstration")),
        "mandates": rows,
        "awaiting_consent": await consent.awaiting(session, firm),
        "recent_consents": await consent.recent(session, firm),
    }
