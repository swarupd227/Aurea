"""Streaming firm-wide book scan — sweep every household, surface prioritised actions live.

Visualises what the Next-Best-Action agent does across the whole book: it reads each household's
brain, scans for opportunities/risks/anomalies, and surfaces the highest-priority items. The sweep
is read-only for display; persistence goes through the governed runtime so every surfaced item still
lands in the ledger at its tier."""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents._signals import book_signals
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey
from app.models.governance import Recommendation
from app.models.tenant import Firm


async def scan_book_stream(session: AsyncSession, *, firm: Firm, agent_key: AgentKey):
    """Async generator yielding the sweep progress and the final, governed result."""
    households = await list_households(session, firm.id)
    yield {"phase": "start", "households": len(households), "agent": str(agent_key)}

    detected = 0
    tally: dict[str, int] = {}
    for i, h in enumerate(households):
        brain = await household_brain(session, h["id"])
        sigs = book_signals(brain) if brain else []
        detected += len(sigs)
        for s in sigs:
            tally[s["kind"]] = tally.get(s["kind"], 0) + 1
        await asyncio.sleep(0.2)  # let the sweep breathe so the adviser sees it work
        yield {"phase": "progress", "index": i + 1, "total": len(households),
               "household": h["name"], "found": len(sigs), "detected_total": detected,
               "tally": dict(tally)}

    # Persist through the real runtime (sense → think → governed recommendations + ledger).
    try:
        run = await run_agent(session, firm=firm, agent_key=agent_key,
                              subject=Subject("firm", firm.id, firm.name), trigger="book_scan")
    except AgentPausedError as exc:
        yield {"phase": "error", "message": str(exc)}
        return
    await session.commit()  # durable before we hand the items to the client

    recs = (await session.execute(
        select(Recommendation).where(Recommendation.run_id == run.id)
        .order_by(Recommendation.priority, Recommendation.confidence.desc())
    )).scalars().all()
    items = [{
        "id": str(r.id), "title": r.title, "summary": r.summary, "subject_label": r.subject_label,
        "priority": r.priority, "confidence": r.confidence,
        "signal": (r.payload or {}).get("signal", "insight"), "payload": r.payload or {},
    } for r in recs]
    by_signal: dict[str, int] = {}
    for it in items:
        by_signal[it["signal"]] = by_signal.get(it["signal"], 0) + 1

    # Concrete value at stake across the surfaced actions (for the headline tiles).
    harvestable = sum(abs(float(it["payload"].get("total_loss", 0) or 0))
                      for it in items if it["signal"] == "loss_harvest")
    idle_cash = sum(float(it["payload"].get("cash", 0) or 0)
                    for it in items if it["signal"] == "idle_cash")

    yield {"phase": "done", "households_scanned": len(households), "detected": detected,
           "items_surfaced": len(items), "by_signal": by_signal, "items": items,
           "aggregates": {"harvestable_losses": round(harvestable), "idle_cash": round(idle_cash),
                          "concentrated_positions": by_signal.get("concentration", 0),
                          "goals_off_track": by_signal.get("goal_gap", 0)},
           "run_id": str(run.id)}
