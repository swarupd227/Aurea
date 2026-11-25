"""An Indian firm's governance, as compliance recorded it (India row 14, B-258).

Two things the firm keeps and the platform reads, never writes:

**The licence map (D1).** Which licences the firm holds, what each track lets an agent do, and which agents the licences cap. It
is `licences.py` read out, nothing more: the same ceiling `provenance/policy.resolve_tier` applies, the configured tier beside the
effective one, and whether an agent that moves a book stops at a proposal whatever its tier. A licence the firm does not hold is
listed as not held, with what it would allow, so the map answers "what is this firm not licensed to do". No tier is invented: an
agent's configured tier is the firm's policy or its own configuration, as `resolve_tier` finds it.

**The policy register.** Compliance records each policy the firm keeps (grievance redressal, cybersecurity and cyber resilience,
business continuity and disaster recovery, outsourcing, conflict of interest) from the document: its version, owner, who approved
it and when, and when it is next reviewed (a year after approval unless the document sets another day). A row is added and never
edited; the latest for a kind stands, and an earlier version stays on file. A kind with no row reads "not recorded", which does not
mean the firm has no such policy, and a policy past its review day reads overdue, never a countdown. Whether a particular licence
requires a particular policy is for counsel: the register records what the firm says it keeps. The calendar and the Licences & SEBI
page read it. This is not a statement that the firm is compliant.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.catalogue import CATALOGUE
from app.aurea_core import firm_time, licences, markets
from app.core import decision_rights
from app.core.security import UserRole
from app.models.enums import AgentKey, AutonomyTier
from app.models.graph import Mandate
from app.models.india_governance import GovernancePolicy
from app.models.tenant import AgentConfig, AutonomyPolicy, Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.COMPLIANCE})
READERS = frozenset({UserRole.COMPLIANCE, UserRole.BRANCH_LEADER, UserRole.OPERATIONS, UserRole.ADMIN})
DUE_SOON_DAYS = 30
POLICIES = {
    "grievance": ("Grievance redressal", "SEBI investor-grievance circulars and the IA and PM Master Circulars, as read in the research report; not re-read"),
    "cyber": ("Cybersecurity and cyber resilience", "SEBI CSCRF (Aug 2024) and the CERT-In Directions (Apr 2022), as read in the research report; not re-read"),
    "business_continuity": ("Business continuity and disaster recovery", "SEBI CSCRF (Aug 2024) as read in the research report; not re-read"),
    "outsourcing": ("Outsourcing", "SEBI guidelines on outsourcing of activities by intermediaries (Dec 2011) as read in the research report; not re-read"),
    "conflict_of_interest": ("Conflict of interest", "SEBI (Investment Advisers) Regulations 2013 and the IA Master Circular, as read in the research report; not re-read"),
}
NOT_COMPLIANCE = ("What the firm recorded it keeps, from the document. Not on file does not mean the firm has no such policy, and "
                  "nothing here says the firm is compliant: whether a licence requires a policy is for counsel.")


class GovernanceError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN" and bool(licences.held(firm))


def _add_year(day: date) -> date:
    try:
        return day.replace(year=day.year + 1)
    except ValueError:      # 29 February
        return day.replace(year=day.year + 1, day=28)


def _day(value, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise GovernanceError(f"Give the {label} as YYYY-MM-DD.")


async def _rows(session: AsyncSession, firm_id: uuid.UUID) -> list[GovernancePolicy]:
    return list((await session.execute(select(GovernancePolicy).where(GovernancePolicy.firm_id == firm_id)
                                       .order_by(GovernancePolicy.approved_on, GovernancePolicy.created_at, GovernancePolicy.id))).scalars().all())


def state_of(row: GovernancePolicy | None, today: date) -> str:
    if row is None:
        return "not_recorded"
    left = (row.next_review_on - today).days
    return "overdue" if left < 0 else "due_soon" if left <= DUE_SOON_DAYS else "current"


async def policies(session: AsyncSession, firm: Firm) -> dict:
    """Each kind's standing policy and where its review stands, with the earlier versions kept."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "policies": []}
    by_kind: dict[str, list[GovernancePolicy]] = {k: [] for k in POLICIES}
    for r in await _rows(session, firm.id):
        by_kind.setdefault(r.kind, []).append(r)
    out = []
    for kind, (label, cite) in POLICIES.items():
        rows = by_kind.get(kind, [])
        latest = rows[-1] if rows else None
        s = state_of(latest, today)
        out.append({"kind": kind, "label": label, "citation": cite, "state": s, "version": latest.version if latest else None,
                    "owner": latest.owner if latest else None, "approved_on": latest.approved_on.isoformat() if latest else None,
                    "approved_by": latest.approved_by if latest else None,
                    "next_review_on": latest.next_review_on.isoformat() if latest else None,
                    "review_set_by_document": bool(latest and latest.review_set), "days": (latest.next_review_on - today).days if latest else None,
                    "document": latest.source_document if latest else None, "recorded_by": latest.recorded_by if latest else None,
                    "earlier_versions": [{"version": r.version, "approved_on": r.approved_on.isoformat(), "document": r.source_document}
                                         for r in reversed(rows[:-1])]})
    counts = {s: sum(1 for p in out if p["state"] == s) for s in ("overdue", "due_soon", "current", "not_recorded")}
    return {"applies": True, "as_of": today.isoformat(), "policies": out, "counts": counts, "not_compliance": NOT_COMPLIANCE}


async def prepare_policy(session: AsyncSession, firm: Firm, role, *, kind: str | None, version: str | None, owner: str | None, approved_on,
                         document: str | None, approved_by: str | None = None, next_review_on=None, note: str | None = None) -> dict:
    if not applies(firm):
        raise GovernanceError("This firm holds no Indian licence, so it has no governance register to record.")
    if role not in RECORDERS:
        raise GovernanceError("A policy is recorded by compliance, from the document: it is the firm's own record, and compliance owns it.")
    k = (kind or "").strip().lower()
    if k not in POLICIES:
        raise GovernanceError(f"A policy is one of {', '.join(POLICIES)}.")
    v = " ".join((version or "").split())
    if not v:
        raise GovernanceError("Give the version the document carries.")
    who = " ".join((owner or "").split())
    if len(who) < 3:
        raise GovernanceError("Name the owner of the policy.")
    today = firm_time.today(firm)
    day = _day(approved_on, "day it was approved")
    if day is None:
        raise GovernanceError("Give the day it was approved, as YYYY-MM-DD.")
    if day > today:
        raise GovernanceError("A policy cannot be approved in the future.")
    nxt = _day(next_review_on, "next review day")
    if nxt is not None and nxt <= day:
        raise GovernanceError("The next review falls after the approval.")
    if len((document or "").strip()) < 5:
        raise GovernanceError("Say where the approved policy is filed.")
    before = [r for r in await _rows(session, firm.id) if r.kind == k]
    if before and before[-1].version == v and before[-1].approved_on == day:
        raise GovernanceError(f"{POLICIES[k][0]} version {v} approved {day.isoformat()} is already recorded.")
    if before and day < before[-1].approved_on:
        raise GovernanceError(f"Version {before[-1].version}, approved {before[-1].approved_on.isoformat()}, is already on file: a later approval stands over an earlier one.")
    return {"kind": k, "label": POLICIES[k][0], "version": v, "owner": who, "approved_on": day, "approved_by": " ".join((approved_by or "").split()) or None,
            "next_review_on": nxt or _add_year(day), "review_set": nxt is not None, "document": document.strip(), "note": (note or "").strip() or None,
            "before": before[-1] if before else None}


async def record_policy(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_policy(session, firm, role, **terms)
    row = GovernancePolicy(firm_id=firm.id, kind=p["kind"], version=p["version"], owner=p["owner"], approved_on=p["approved_on"],
                           approved_by=p["approved_by"], next_review_on=p["next_review_on"], review_set=p["review_set"],
                           source_document=p["document"], note=p["note"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="governance_policy_recorded", actor=by, content={
        "kind": row.kind, "version": row.version, "owner": row.owner, "approved_on": row.approved_on.isoformat(),
        "next_review_on": row.next_review_on.isoformat(), "document": row.source_document})
    return await policies(session, firm)


# ── The licence map (D1) ─────────────────────────────────────────────────────────────────────────────────────────────

async def licence_map(session: AsyncSession, firm: Firm) -> dict:
    """Which licences the firm holds and what each lets an agent do, and which agents the licences cap. Read out of `licences.py`."""
    from app.aurea_core import compliance_calendar

    held = {l.key for l in licences.held(firm)}
    ceiling = licences.ceiling(firm)
    on_track: dict[str, int] = {}
    for m in (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.is_active.is_(True)))).scalars().all():
        track = licences.track_for(firm, m)
        if track is not None:
            on_track[track.key] = on_track.get(track.key, 0) + 1
    obligations = {l: sum(1 for o in compliance_calendar.OBLIGATIONS if l in o.licences) for l in licences.LICENCES}
    rows = []
    for l in licences.LICENCES.values():
        rows.append({**licences.describe(l), "held": l.key in held, "mandates": on_track.get(l.key, 0), "obligations": obligations[l.key],
                     "allows": ("may place an order" if l.may_execute else "may not place an order")
                               + ("; the client consents to each trade" if l.per_trade_consent else "; acts without a per-trade consent, inside the mandate"
                                                                                                    if l.may_execute else ""),
                     "plans": " and ".join(l.fund_plans) + " plan funds"})
    configs = {str(c.agent_key): c for c in (await session.execute(select(AgentConfig).where(AgentConfig.firm_id == firm.id))).scalars().all()}
    policies_ = {}
    for p in (await session.execute(select(AutonomyPolicy).where(AutonomyPolicy.firm_id == firm.id, AutonomyPolicy.mandate_type.is_(None)))).scalars().all():
        policies_[str(p.agent_key)] = p
    agents = []
    for key, meta in CATALOGUE.items():
        k = str(key)
        pol, cfg = policies_.get(k), configs.get(k)
        configured = AutonomyTier(pol.tier if pol else (cfg.default_tier if cfg else AutonomyTier.TIER_1))
        effective = licences.cap(firm, configured)      # `provenance/policy.resolve_tier` for no mandate type, read from the rows above
        agents.append({"agent_key": k, "agent": meta["name"], "stage": meta.get("stage"), "configured": str(configured.value),
                       "source": "firm policy" if pol else "agent configuration" if cfg else "not configured: the lowest tier",
                       "effective": str(AutonomyTier(effective).value), "capped": str(AutonomyTier(effective).value) != str(configured.value),
                       "moves_the_book": decision_rights.moves_the_book(AgentKey(k))})
    capped = [a for a in agents if a["capped"]]
    return {"applies": bool(held), "as_of": firm_time.today(firm).isoformat(), "licences": rows, "held": sorted(held),
            "ceiling": str(ceiling.value) if ceiling else None, "agents": sorted(agents, key=lambda a: (not a["capped"], a["agent"])),
            "capped": len(capped), "total_agents": len(agents),
            "note": "Read from the licences the firm holds (settings), the same ceiling an agent's tier is capped at when it runs. "
                    "No licence reaches Tier 3 in India, and an agent that moves a book stops at a proposal whatever its tier. "
                    "Which licences the firm and its group hold is the firm's own record; it is not checked against SEBI's register."}
