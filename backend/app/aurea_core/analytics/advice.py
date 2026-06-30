"""Layer 2.3 — Advice & next-best-action analytics (Analytics Companion §2.3).

Predictive/prescriptive: opportunity detection, attrition/churn risk, goal-tracking
probability and engagement signals — the analytics that prompt action."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._signals import book_signals
from app.aurea_core.analytics._common import gather_brains, goal_projections_all
from app.models.client_experience import HeirJourney, Message
from app.models.engagement import Meeting
from app.models.enums import MessageAuthor


async def compute(session: AsyncSession, firm_id: uuid.UUID, brains: list[dict] | None = None) -> dict:
    brains = brains if brains is not None else await gather_brains(session, firm_id)

    # Context for attrition + engagement: meetings, client messages, heir-journey progress.
    meetings = (await session.execute(select(Meeting).where(Meeting.firm_id == firm_id))).scalars().all()
    msgs = (await session.execute(select(Message).where(Message.firm_id == firm_id))).scalars().all()
    journeys = (await session.execute(select(HeirJourney).where(HeirJourney.firm_id == firm_id))).scalars().all()
    has_meeting = {str(m.household_id) for m in meetings}
    client_msgs: dict[str, int] = {}
    for m in msgs:
        if m.author_role == MessageAuthor.CLIENT:
            client_msgs[str(m.household_id)] = client_msgs.get(str(m.household_id), 0) + 1
    journey_done = {str(j.person_id): (j.status == "completed") for j in journeys}

    # Opportunity & risk detection (reuse the agentic signal scanner).
    opp_counts: dict[str, int] = {}
    opportunities = []
    for b in brains:
        for s in book_signals(b):
            opp_counts[s["kind"]] = opp_counts.get(s["kind"], 0) + 1
            opportunities.append({"household": b["household"]["name"], "kind": s["kind"],
                                  "title": s["title"], "priority": s["priority"]})

    # Attrition / churn risk + engagement, per household.
    goals = await goal_projections_all(brains)
    off_track_hh = {g["household"] for g in goals if not g["on_track"]}
    attrition = []
    engagement = []
    for b in brains:
        hh = b["household"]
        hid = hh["id"]
        name = hh["name"]
        total = b["totals"]["total_value"] or 1.0
        equity_w = b["totals"]["by_asset_class"].get("equity", 0) / total
        nextgen = [p for p in b["persons"] if p.get("is_next_gen")]

        risk = 0.0
        factors = []
        if nextgen and not all(journey_done.get(p["id"], False) for p in nextgen):
            risk += 0.30; factors.append("next-gen heir not yet engaged")
        if hid not in has_meeting:
            risk += 0.25; factors.append("no recent meeting on record")
        if client_msgs.get(hid, 0) == 0:
            risk += 0.20; factors.append("no inbound client messages")
        if name in off_track_hh:
            risk += 0.15; factors.append("a goal is off track")
        if equity_w > 0.70:
            risk += 0.10; factors.append("high equity exposure in volatile markets")
        risk = round(min(risk, 1.0), 3)
        level = "high" if risk >= 0.5 else "medium" if risk >= 0.3 else "low"
        attrition.append({"household": name, "risk": risk, "level": level, "factors": factors})

        eng = 0.0
        eng += min(client_msgs.get(hid, 0) / 3, 1) * 0.3
        eng += 0.3 if hid in has_meeting else 0.0
        if nextgen:
            prog = [j.status == "completed" for j in journeys if str(j.person_id) in {p["id"] for p in nextgen}]
            eng += (sum(prog) / len(prog) if prog else 0) * 0.2
        else:
            eng += 0.2
        sentiment = next((m.notes.get("sentiment") for m in meetings if str(m.household_id) == hid and m.notes), "neutral")
        eng += 0.2 if sentiment in ("positive", "neutral") else 0.05
        engagement.append({"household": name, "score": round(min(eng, 1.0), 3),
                           "sentiment": sentiment})

    return {
        "opportunities": {"total": len(opportunities), "by_kind": opp_counts,
                          "items": sorted(opportunities, key=lambda o: o["priority"])[:30]},
        "attrition": {"high": sum(1 for a in attrition if a["level"] == "high"),
                      "medium": sum(1 for a in attrition if a["level"] == "medium"),
                      "by_household": sorted(attrition, key=lambda a: -a["risk"])},
        "goal_tracking": {"total": len(goals), "on_track": sum(1 for g in goals if g["on_track"]),
                          "avg_probability": round(sum(g["probability"] for g in goals) / len(goals), 3) if goals else None,
                          "at_risk": [g for g in goals if not g["on_track"]]},
        "engagement": {"avg_score": round(sum(e["score"] for e in engagement) / len(engagement), 3) if engagement else None,
                       "by_household": sorted(engagement, key=lambda e: e["score"])},
    }
