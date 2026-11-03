"""Abandonment Recovery Agent (L200 A7). Tier 1 · Scheduled daily.

Raises an onboarding account that has stayed in its state past that state's SLA. The state, its owner, its SLA and the
time in it come from `aurea_core/account_states.py` through the onboarding board's own reading
(`api.onboarding.states_overview`), so a proposal and the board never disagree: the card says which state, whose move
it is, the SLA in days and how far over. An account inside its SLA is not raised, however long it has been since
anyone touched it, and one that moves on drops its proposal (`expires_unraised`).

Approving gives the move to a person: a task naming the account, the step that moves it on and whose it is, due
today. Nothing else changes; the account's state moves only when its evidence does.
"""
from __future__ import annotations

import uuid

from app.agents._common import firm_voice, narrate
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.models.enums import AgentKey, AutonomyTier

# What moves an account out of each state, in the words of the person who owns it.
LABELS = {
    "draft": "Draft", "data_complete": "Details complete", "documents_generated": "Out for signature", "signed": "Signed",
    "in_review": "In compliance review", "submitted": "Waiting for the custodian", "open": "Open, not funded",
    "funding_in_progress": "Funding in progress", "funded": "Funded, not invested",
}
NEXT = {
    "draft": "Record the people, roles and documents the registration needs",
    "data_complete": "Send the agreement to sign",
    "documents_generated": "Chase the client's signature",
    "signed": "Activate the account and submit it to the custodian",
    "in_review": "Clear the compliance gates: screening, identity, EDD and disclosures",
    "submitted": "Chase the custodian to open the account",
    "open": "Start the transfer or deposit that funds the account",
    "funding_in_progress": "Chase the delivering firm or the payment",
    "funded": "Place the first purchase",
}


def overdue_rows(accounts: list[dict]) -> list[dict]:
    """The accounts past their state's SLA, furthest over first, each with how far over."""
    out = []
    for a in accounts:
        if not a["overdue"] or a["state"] not in NEXT:
            continue
        over = round(a["age_days"] - a["sla_days"], 1)
        out.append({**a, "days_over": over, "label": LABELS[a["state"]], "next_step": NEXT[a["state"]]})
    return sorted(out, key=lambda a: -a["days_over"])


class AbandonmentRecoveryAgent(BaseAgent):
    key = AgentKey.ABANDONMENT_RECOVERY
    name = "Abandonment Recovery"
    lifecycle_stage = "acquire_onboard"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    # An account that has moved on, or come back inside its SLA, is no longer late; its proposal goes.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        from app.api.onboarding import states_overview

        board = await states_overview(ctx.session, ctx.firm)
        late = overdue_rows(board["accounts"])
        return {"accounts": len(board["accounts"]), "n_late": len(late), "late": late}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        drafts = []
        for a in sensed["late"]:
            days = f"{a['age_days']:.0f} days in it against a {a['sla_days']}-day SLA, {a['days_over']:.0f} over"
            exceptions = [f"{e['label']}: {e['detail']}" for e in a["exceptions"]]
            fallback = (f"{a['account']} has been {a['label'].lower()} for {days}. Next: {a['next_step'].lower()}. "
                        f"It is {a['owner'].lower()}'s move." + (f" Open exceptions: {'; '.join(exceptions)}." if exceptions else ""))
            note = await narrate(
                ctx, task="client_comms", system=firm_voice(ctx), fallback=fallback,
                prompt=("Write two or three plain sentences about a late onboarding account, for whoever reads the onboarding queue. "
                        "Name the owner of the next move in the third person; do not address the reader as 'you'. "
                        "Use only these facts and add none: "
                        f"account {a['account']}; state '{a['label']}'; {days}; owner {a['owner']}; next step: {a['next_step']}"
                        + (f"; open exceptions: {'; '.join(exceptions)}" if exceptions else "; no open exceptions") + "."))
            drafts.append(RecommendationDraft(
                title=f"Past its SLA — {a['account']}: {a['label'].lower()} ({a['days_over']:.0f}d over)",
                summary=f"{a['label']} for {days}. Owner: {a['owner']}.",
                rationale=note,
                confidence=0.95,
                priority=1 if a["days_over"] >= a["sla_days"] or exceptions else 2,
                subject=Subject("onboarding_case", uuid.UUID(a["case_id"]), a["account"]),
                payload={"state": a["state"], "state_label": a["label"], "owner": a["owner"], "sla_days": a["sla_days"],
                         "days_in_state": a["age_days"], "days_over": a["days_over"], "since": a["since"],
                         "step": f"{a['step']} of {a['steps']}", "next_step": a["next_step"], "exceptions": exceptions},
                evidence={"state": a["state"], "since": a["since"], "sla_days": a["sla_days"], "days_in_state": a["age_days"]},
            ))
        return drafts

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """A task for the owner of the next move, due today. It changes nothing on the account."""
        from app.aurea_core import firm_time, tasks
        from app.models.graph import Household
        from app.models.onboarding import OnboardingCase

        p = recommendation.modified_payload or recommendation.payload or {}
        case = await ctx.session.get(OnboardingCase, recommendation.subject_id) if recommendation.subject_id else None
        home = (case.materialized or {}).get("household_id") if case else None
        household = await ctx.session.get(Household, uuid.UUID(str(home))) if home else None
        task = tasks.new(ctx.firm.id, f"{recommendation.subject_label}: {p.get('next_step', 'move the account on')} ({p.get('owner', 'owner not known')})",
                         household=household, due_date=firm_time.today(ctx.firm), assignee=None, by=recommendation.decided_by)
        task.source = str(self.key.value)
        ctx.session.add(task)
        await ctx.session.flush()
        return {"executed": True, "task_id": str(task.id), "note": f"Task added for {p.get('owner', 'the owner')}."}
