"""Reconciliation - works the break queue so operations only sees what needs a judgement.

Each run compares every account's current custodian statement with the book again (a late posting
can clear a break or change its cause without a new statement), then sorts the open breaks: timing
differences inside a settlement cycle are left to clear by themselves, and the rest are put to a
person in one proposal - escalate to the custodian what nothing on the book explains or what was
timing and did not clear; accept as explained what has a known cause that needs work here.

It never changes the book, and it never escalates on its own: approving the proposal records each
decision on its break under the approver's name (`aurea_core/reconciliation.py`).
"""
from __future__ import annotations

import uuid
from collections import Counter

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import reconciliation
from app.models.enums import AgentKey, AutonomyTier
from app.models.identity import User

REASON_WORDS = {
    "unexplained": "nothing on the book accounts for it",
    "timing_not_cleared": "it looked like settlement timing but a later statement still shows it",
    "entitlement_to_post": "a corporate action the custodian has applied is not posted here",
    "unmapped_security": "the custodian reports a security the firm has no instrument for",
}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class ReconciliationAgent(BaseAgent):
    key = AgentKey.RECONCILIATION
    name = "Reconciliation"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = True
    # One proposal for the queue as it stands; a run that finds nothing needing a person closes it.
    expires_unraised = True

    async def sense(self, ctx: AgentContext) -> dict:
        rechecked = await reconciliation.recheck(ctx.session, ctx.firm.id)
        queue = await reconciliation.needs_attention(ctx.session, ctx.firm.id)
        overview = await reconciliation.overview(ctx.session, ctx.firm.id)
        return {"rechecked": rechecked, **queue, "coverage": {
            k: overview["summary"][k] for k in ("accounts", "reconciled", "no_statement", "stale_statement")}}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        items = sensed.get("items") or []
        if not items:
            return []
        reasons = Counter(i["reason"] for i in items)
        escalate = [i for i in items if i["action"] == "escalated"]
        accept = [i for i in items if i["action"] == "explained"]
        cov = sensed.get("coverage") or {}

        parts = []
        if escalate:
            parts.append(f"{_plural(len(escalate), 'break')} to escalate to the custodian")
        if accept:
            parts.append(f"{_plural(len(accept), 'break')} to accept as explained and fix here")
        if sensed.get("waiting"):
            parts.append(f"{_plural(len(sensed['waiting']), 'timing difference')} left to clear on the next statement")
        if cov.get("no_statement") or cov.get("stale_statement"):
            parts.append(f"{cov.get('no_statement', 0)} of {cov.get('accounts', 0)} accounts have no custodian "
                         f"statement and {cov.get('stale_statement', 0)} are on one more than a week old")
        why = "; ".join(f"{_plural(n, 'break')}: {REASON_WORDS[r]}" for r, n in reasons.items())

        return [RecommendationDraft(
            # "break(s)", not a plural that changes with the count: the title is the proposal's
            # identity once its numbers are stripped, and it must not change when one break clears.
            title=f"Reconciliation - {len(items)} break(s) need a decision",
            summary="; ".join(parts) + ".",
            rationale=(
                f"Every current statement was compared with the book again as it stood on the statement's "
                f"date. {why}. Approving records each decision on its break under your name; nothing in "
                f"the book changes, and an escalated break still needs taking to the custodian."
            ),
            confidence=0.9,
            priority=2 if reasons.get("unexplained") or reasons.get("timing_not_cleared") else 3,
            subject=Subject("firm", ctx.firm.id, ctx.firm.name),
            payload={"items": items},
            evidence={"reasons": dict(reasons), "waiting": len(sensed.get("waiting") or []),
                      "rechecked": sensed.get("rechecked"), "coverage": cov},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        # Escalating to a custodian is a person's decision. At Tier 3 the runtime calls act() with
        # nobody having decided; decision_rights guards only a human decision, so the proposal
        # simply stays open for one.
        if not recommendation.decided_by:
            return {"executed": False,
                    "note": "Breaks are escalated or accepted only when operations or the portfolio team "
                            "approves; this proposal is waiting for that decision."}
        payload = {**(recommendation.payload or {}), **(recommendation.modified_payload or {})}
        approver = await ctx.session.get(User, recommendation.decided_by)
        by = approver.email if approver else "unknown"
        done, skipped = Counter(), []
        for item in payload.get("items") or []:
            try:
                await reconciliation.resolve_break(
                    ctx.session, ctx.firm.id, _uuid(item["break_id"]), action=item["action"],
                    note=f"Approved from the reconciliation agent's proposal: {REASON_WORDS[item['reason']]}.",
                    by=by)
                done[item["action"]] += 1
            except reconciliation.ReconciliationError as exc:
                # Moved on since the proposal was drafted - cleared by a statement, or decided by hand.
                skipped.append({"break_id": item["break_id"], "reason": str(exc)})
        return {"executed": bool(done), "escalated": done["escalated"], "explained": done["explained"],
                "skipped": skipped,
                "note": (f"Escalated {done['escalated']} and accepted {done['explained']} break(s)"
                         + (f"; {len(skipped)} had already moved on" if skipped else "") + ".")}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        return {"reversed": False,
                "note": "A decision on a break stays on its history. A break can still be set aside or marked "
                        "fixed, with a reason, on the Reconciliation page."}


def _uuid(v) -> uuid.UUID:
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))
