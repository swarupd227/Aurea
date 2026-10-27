"""Natural-language delegation — turn 'watch the Chen trust and rebalance if it drifts' into a
routed agent task. Deterministic intent + client matching (auditable); the workforce then runs."""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import list_households
from app.models.enums import AgentKey, MandateType
from app.models.graph import Household, LegalEntity, Mandate, Person

# keyword → (agent, subject scope)
INTENTS = [
    (("rebalance", "drift", "rebalanc", "trade", "trim", "tax-loss", "harvest"), AgentKey.DRIFT_REBALANCING, "mandate"),
    (("meeting", "prep", "brief", "agenda"), AgentKey.MEETING_PREP, "household"),
    (("report", "commentary", "performance", "review note", "statement"), AgentKey.RESEARCH_REPORTING, "household"),
    (("outreach", "reassure", "care", "volatility", "check in", "check-in", "retention"), AgentKey.CLIENT_CARE, "household"),
    (("opportunit", "next best", "next-best", "scan", "risk", "anomal", "wallet"), AgentKey.NEXT_BEST_ACTION, "household"),
]


async def managed_mandates(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[Mandate]:
    """A household's managed mandates (those with a model portfolio), discretionary first.

    The one place that decides which mandate a "rebalance this household" request means, shared
    by the Delegate bar and by run_agent in the conversation."""
    mandates = (
        await session.execute(
            select(Mandate).where(
                Mandate.firm_id == firm_id, Mandate.model_portfolio_id.isnot(None),
                (Mandate.person_id.in_(select(Person.id).where(Person.household_id == household_id)))
                | (Mandate.entity_id.in_(select(LegalEntity.id).where(LegalEntity.household_id == household_id))))
        )
    ).scalars().all()
    return sorted(mandates, key=lambda m: 0 if m.mandate_type == MandateType.DISCRETIONARY else 1)


async def interpret(session: AsyncSession, firm_id: uuid.UUID, text: str) -> dict:
    t = (text or "").lower()

    agent_key = AgentKey.NEXT_BEST_ACTION
    scope = "firm"
    for kws, ak, sc in INTENTS:
        if any(k in t for k in kws):
            agent_key, scope = ak, sc
            break

    # Match a household by name (or a distinctive token).
    households = await list_households(session, firm_id)
    matched = None
    for h in households:
        name = h["name"].lower()
        tokens = [w for w in name.replace("the ", "").replace("&", " ").split() if len(w) > 3]
        if name in t or any(tok in t for tok in tokens):
            matched = h
            break

    subject_type = scope
    subject_id = None
    subject_label = None
    mandate_type = None

    if matched and scope in ("household", "mandate"):
        hid = uuid.UUID(matched["id"])
        subject_label = matched["name"]
        if scope == "mandate":
            # Find a managed mandate under this household (prefer discretionary).
            mandates = await managed_mandates(session, firm_id, hid)
            if mandates:
                m = mandates[0]
                subject_type, subject_id, subject_label = "mandate", m.id, m.name
                mandate_type = MandateType(m.mandate_type)
            else:  # fall back to a household-level scan
                agent_key, subject_type, subject_id = AgentKey.NEXT_BEST_ACTION, "household", hid
        else:
            subject_id = hid
    elif scope in ("household", "mandate"):
        # No client matched — broaden to a book-wide scan.
        agent_key, subject_type = AgentKey.NEXT_BEST_ACTION, "firm"

    return {"agent_key": agent_key, "subject_type": subject_type,
            "subject_id": subject_id, "subject_label": subject_label,
            "mandate_type": mandate_type,
            "interpreted": f"{agent_key.value.replace('_', ' ')} on {subject_label or 'the whole book'}"}
