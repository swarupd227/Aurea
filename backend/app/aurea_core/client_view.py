"""What a client sees of their own household, read once for the Canvas pages and for the client's tools.

A client's conversation and their pages answer from here, so the two cannot disagree. Everything is the
household's own record: a goal's likelihood is the planning engine's estimate and says which of its inputs
were assumed, and a fee no schedule or segment sets is not on file, never a default rate."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, goal_tradeoff, planning
from app.aurea_core.graph import household_brain
from app.models.client_experience import Message
from app.models.engagement import ClientReport
from app.models.enums import MessageAuthor, ReportStatus
from app.models.graph import Person
from app.models.identity import User
from app.models.tenant import Firm

STRESS_SCENARIO = "covid_2020"


class ClientViewError(Exception):
    pass


async def household_of(session: AsyncSession, user: User) -> uuid.UUID:
    """The one household a client may read: their own person's. A client tied to none gets none."""
    person = await session.get(Person, user.person_id) if user.person_id else None
    if person is None or person.household_id is None or person.firm_id != user.firm_id:
        raise ClientViewError("No household is linked to this client.")
    return person.household_id


async def adviser_for(session: AsyncSession, firm: Firm, brain: dict) -> dict | None:
    """The household's named adviser (aurea_core/advisers.py), or None when nobody is on file."""
    from app.aurea_core.advisers import adviser_of

    u = await adviser_of(session, firm.id, uuid.UUID(brain["household"]["id"]))
    return {"name": u.full_name, "title": u.title, "email": u.email} if u else None


def goals(brain: dict, today) -> dict:
    """Each goal's likelihood from the one engine every screen reads (goal_tradeoff.likelihoods), with what
    it assumed: a goal's horizon where neither a date nor one is on file, and for the household whether
    career earnings were counted from an income on file."""
    result = goal_tradeoff.likelihoods(brain, today)
    return {"goals": [{"name": g["name"], "kind": g["kind"], "target_amount": g["target_amount"], "on_track": g["on_track"],
                       "probability": g["success_probability"], "projected_median": g["projected_median"],
                       "horizon_years": g["years"], "horizon": g["horizon"], "assumed": g["assumed"], "estimate": True}
                      for g in result["goals"]],
            "assumed": result["assumed"], "earnings": result["earnings"]["basis"]}


async def reports(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, limit: int = 5) -> list[dict]:
    """Reports published to this household. A draft waiting for its proposal is not the client's yet."""
    rows = (await session.execute(
        select(ClientReport).where(ClientReport.firm_id == firm_id, ClientReport.household_id == household_id,
                                   ClientReport.status == ReportStatus.CLIENT_READY)
        .order_by(ClientReport.created_at.desc()).limit(limit))).scalars().all()
    return [{"id": str(r.id), "title": r.title, "period": r.period,
             "published_at": r.published_at.isoformat() if r.published_at else None} for r in rows]


async def report(session: AsyncSession, report_id: uuid.UUID, household_id: uuid.UUID | None, *, firm_id: uuid.UUID) -> dict | None:
    """One published report, if it is this firm's and, where a household is named, that household's."""
    r = await session.get(ClientReport, report_id)
    if r is None or r.firm_id != firm_id or r.status != ReportStatus.CLIENT_READY:
        return None
    if household_id is not None and r.household_id != household_id:
        return None
    return {"id": str(r.id), "household_id": str(r.household_id), "title": r.title, "period": r.period,
            "sections": r.sections, "data": r.data, "status": r.status}


async def home(session: AsyncSession, household_id: uuid.UUID, *, firm: Firm) -> dict | None:
    """The client's home page: what they hold, their goals, who looks after them, and what is waiting."""
    brain = await household_brain(session, household_id, firm_id=firm.id)
    if not brain:
        return None
    total = brain["totals"]["total_value"]
    allocation = brain["totals"]["by_asset_class"]
    planned = goals(brain, firm_time.today(firm))
    projected = planned["goals"]
    unread = (await session.execute(
        select(func.count(Message.id)).where(Message.firm_id == firm.id, Message.household_id == household_id,
                                             Message.read_by_client.is_(False),
                                             Message.author_role != MessageAuthor.CLIENT))).scalar_one()
    stress = planning.stress_test(allocation, [STRESS_SCENARIO]).get(STRESS_SCENARIO) if allocation else None
    return {
        "firm": {"name": firm.name, "branding": firm.branding},
        "household": brain["household"],
        "household_id": str(household_id),
        "currency": firm.base_currency,
        "adviser": await adviser_for(session, firm, brain),
        "members": [{"id": p["id"], "name": p.get("preferred_name") or p["full_name"]} for p in brain["persons"]],
        "total_wealth": total,
        "allocation": allocation,
        "data_confidence": brain["totals"]["data_confidence"],
        "goals": projected,
        "goals_on_track": sum(1 for g in projected if g["on_track"]),
        "goals_assumed": planned["assumed"],
        "earnings": planned["earnings"],
        "stress": {"scenario": STRESS_SCENARIO, "impact_pct": stress["impact_pct"], "estimate": True} if stress else None,
        "values": brain["household"].get("values", {}),
        "reports": await reports(session, household_id, firm_id=firm.id),
        "unread_messages": unread,
        "next_gen": [{"id": p["id"], "name": p.get("preferred_name") or p["full_name"]}
                     for p in brain["persons"] if p.get("is_next_gen")],
        "as_of": firm_time.today(firm).isoformat(),
    }


async def billing(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """What this household has actually been billed: issued bills and credit notes only. A client never
    sees a draft or a held bill. The year is the firm's calendar year."""
    from app.models.billing import Invoice

    rows = (await session.execute(
        select(Invoice).where(Invoice.firm_id == firm.id, Invoice.household_id == household_id,
                              Invoice.status.in_(("issued", "debited", "credited")))
        .order_by(Invoice.issued_at.desc()))).scalars().all()
    year = firm_time.today(firm).year
    billed_ytd = sum(float(i.amount) for i in rows if i.issued_at and firm_time.today(firm, i.issued_at).year == year)
    return {
        "invoices": [{
            "id": str(i.id), "number": i.invoice_number, "kind": i.kind, "status": i.status,
            "period_start": i.period_start.isoformat(), "period_end": i.period_end.isoformat(),
            "amount": float(i.amount), "currency": i.currency, "paid": i.debit_status == "posted",
        } for i in rows],
        "billed_ytd": round(billed_ytd, 2) if rows else None,
    }


async def fee_terms(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    """The fee for this household: the schedule confirmed at onboarding where its mandate carries one,
    else the firm's segment estimate, labelled as one. With neither, the fee is not on file - no rate
    is supplied in its place."""
    from app.aurea_core import fees as fees_core
    from app.aurea_core.performance import household_account_ids
    from app.models.graph import Account, Mandate
    from app.models.onboarding import FeeSchedule
    from app.models.portfolio import Holding
    from app.models.tenant import FirmSegment

    # Every account under every person and entity in the household, cash included: the balance billing charges on.
    account_ids = await household_account_ids(session, household_id, firm_id=firm.id) or []
    total_aum = 0.0
    mandates: list[Mandate] = []
    if account_ids:
        mandate_ids = {a.mandate_id for a in (await session.execute(
            select(Account).where(Account.id.in_(account_ids)))).scalars().all() if a.mandate_id}
        mandates = (await session.execute(
            select(Mandate).where(Mandate.firm_id == firm.id, Mandate.id.in_(mandate_ids)))).scalars().all() if mandate_ids else []
        held = (await session.execute(
            select(func.coalesce(func.sum(Holding.market_value), 0)).where(Holding.account_id.in_(account_ids)))).scalar_one()
        cash = (await session.execute(
            select(func.coalesce(func.sum(Account.cash_balance), 0)).where(Account.id.in_(account_ids)))).scalar_one()
        total_aum = float(held) + float(cash)
    months = firm_time.today(firm).month

    schedule = None
    for m in mandates:
        if m.fee_schedule_id:
            schedule = await session.get(FeeSchedule, m.fee_schedule_id)
            if schedule and schedule.firm_id == firm.id:
                break
            schedule = None
    if schedule:
        computed = fees_core.compute_annual_fee(schedule, total_aum)
        annual = computed["annual_fee"] or 0.0
        bps = computed["effective_bps"] or 0.0
        return {"aum": total_aum, "currency": schedule.currency or firm.base_currency, "fee_bps": bps,
                "fee_pct": round(bps / 100, 4), "annual_fee": round(annual, 2), "monthly_fee": round(annual / 12, 2),
                "ytd_fee": round(annual / 12 * months, 2), "source": "confirmed_fee_schedule",
                "fee_schedule_name": schedule.name, "breakdown": computed["breakdown"],
                "applicable_segment": None, "fee_schedule": []}

    segments = (await session.execute(
        select(FirmSegment).where(FirmSegment.firm_id == firm.id, FirmSegment.is_active.is_(True))
        .order_by(FirmSegment.min_aum_usd))).scalars().all()
    applicable = None
    for seg in segments:
        if seg.min_aum_usd is None or total_aum >= (seg.min_aum_usd or 0):
            applicable = seg
    schedule_rows = [{"slug": s.slug, "label": s.label, "fee_bps": s.fee_tier_bps,
                      "fee_pct": round((s.fee_tier_bps or 0) / 100, 4), "min_aum": s.min_aum_usd}
                     for s in segments if s.fee_tier_bps]
    bps = applicable.fee_tier_bps if applicable and applicable.fee_tier_bps else None
    if bps is None:
        return {"aum": total_aum, "currency": firm.base_currency, "fee_bps": None, "fee_pct": None, "annual_fee": None,
                "monthly_fee": None, "ytd_fee": None, "source": "not_on_file", "applicable_segment": None,
                "fee_schedule": schedule_rows}
    annual = total_aum * bps / 10000
    return {"aum": total_aum, "currency": firm.base_currency, "fee_bps": bps, "fee_pct": round(bps / 100, 4),
            "annual_fee": round(annual, 2), "monthly_fee": round(annual / 12, 2), "ytd_fee": round(annual / 12 * months, 2),
            "source": "segment_estimate",
            "applicable_segment": {"slug": applicable.slug, "label": applicable.label, "fee_bps": applicable.fee_tier_bps,
                                   "min_aum": applicable.min_aum_usd},
            "fee_schedule": schedule_rows}


async def fees(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    return {**(await fee_terms(session, firm, household_id)), **(await billing(session, firm, household_id))}
