"""Next-best-action & growth agent (spec Table 10) — deep. Tier 1/2 — adviser chooses to act.

Scans the client brain for prioritised, explainable opportunities, risks and anomalies and
pushes them to the adviser 'without waiting to be asked'. Runs over a single household or —
when given a firm subject — across the whole book, returning the highest-priority items."""
from __future__ import annotations

from sqlalchemy import select

from app.agents._signals import book_signals
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core.graph import household_brain, list_households
from app.models.enums import AgentKey, AutonomyTier

MAX_ITEMS = 15


class NextBestActionAgent(BaseAgent):
    key = AgentKey.NEXT_BEST_ACTION
    name = "Next-Best-Action & Growth"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        if ctx.subject.type in (None, "firm"):
            households = await list_households(s, ctx.firm.id)
            ids = [h["id"] for h in households]
            scope = "book"
        else:
            ids = [str(ctx.subject.id)]
            scope = "household"
        brains = []
        for hid in ids:
            brain = await household_brain(s, hid)
            if brain:
                brains.append(brain)
        return {"scope": scope, "brains": brains}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        # Group candidate signals by kind so we can surface a DIVERSE set across the book,
        # rather than letting one dominant signal (e.g. concentration) crowd out the rest.
        by_kind: dict[str, list[tuple[int, float, RecommendationDraft]]] = {}
        for brain in sensed["brains"]:
            hh = brain["household"]
            for sig in book_signals(brain):
                label = hh["name"]
                draft = RecommendationDraft(
                    title=f"{sig['title']}",
                    summary=f"{label} · {sig['detail']}",
                    rationale=sig["detail"], confidence=sig["confidence"], priority=sig["priority"],
                    subject=Subject("household", hh["id"], label),
                    payload={"signal": sig["kind"], **sig["payload"]},
                    evidence={
                        "source": "Client-brain scan",
                        "scope": sensed["scope"],
                        "signal": sig["kind"],
                        "household": label,
                        "data_confidence": brain["totals"].get("data_confidence", 1.0),
                    },
                )
                by_kind.setdefault(sig["kind"], []).append((sig["priority"], sig["confidence"], draft))

        for items in by_kind.values():
            items.sort(key=lambda t: (t[0], -t[1]))  # priority asc, confidence desc
        # Order kinds by their strongest item, then round-robin so every signal type is represented.
        kinds = sorted(by_kind, key=lambda k: (by_kind[k][0][0], -by_kind[k][0][1]))

        out: list[RecommendationDraft] = []
        while len(out) < MAX_ITEMS and any(by_kind[k] for k in kinds):
            for k in kinds:
                if by_kind[k]:
                    out.append(by_kind[k].pop(0)[2])
                    if len(out) >= MAX_ITEMS:
                        break
        return out
