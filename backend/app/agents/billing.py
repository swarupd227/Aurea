"""Billing - works out what is due, proposes the run, issues it on approval.

It never bills on its own: the run is a proposal at Tier 2, approved by operations or a
branch leader (`decision_rights`), and only then are invoices issued and fees taken from
cash. Everything it needs to explain a bill - the bands, the balance, the valuation date and
how old the oldest price was - is stored on the invoice it drafts (`aurea_core/billing.py`).
"""
from __future__ import annotations

import uuid
from collections import Counter

from sqlalchemy import select

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import billing_run
from app.models.billing import BillingRun, Invoice
from app.models.enums import AgentKey, AutonomyTier
from app.models.identity import User


def _money(by_currency: dict) -> str:
    return " + ".join(f"{cur} {amt:,.2f}" for cur, amt in by_currency.items()) or "nothing"


class BillingAgent(BaseAgent):
    key = AgentKey.BILLING
    name = "Billing"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_2
    scheduled = True

    async def sense(self, ctx: AgentContext) -> dict:
        waiting = await billing_run.open_run(ctx.session, ctx.firm.id)
        if waiting is not None:
            return {"waiting_run_id": str(waiting.id)}
        run = await billing_run.propose_run(
            ctx.session, ctx.firm.id, trigger=(ctx.run.trigger or "manual")[:32], agent_run_id=ctx.run.id)
        return {"run_id": str(run.id)} if run else {}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("run_id"):
            return []
        s = ctx.session
        run = await s.get(BillingRun, uuid.UUID(sensed["run_id"]))
        invoices = (await s.execute(select(Invoice).where(Invoice.run_id == run.id))).scalars().all()
        summary = run.summary or {}
        blocks = Counter(c["code"] for i in invoices for c in (i.checks or []) if c["severity"] == "block")
        warns = Counter(c["code"] for i in invoices for c in (i.checks or []) if c["severity"] == "warn")
        unagreed = summary.get("no_agreement", [])
        total = _money(summary.get("amount_by_currency", {}))
        n, held = summary.get("invoices", len(invoices)), summary.get("blocked", 0)

        parts = [f"{n} bill(s) drafted, {total} billable"]
        if held:
            parts.append(f"{held} held by a block ({', '.join(f'{c} x{k}' for c, k in blocks.items())})")
        if warns:
            parts.append("warnings: " + ", ".join(f"{c} x{k}" for c, k in warns.items()))
        if unagreed:
            parts.append(f"{len(unagreed)} mandate(s) have no agreement on file and are not billed")
        if summary.get("already_billed"):
            parts.append(f"{summary['already_billed']} already billed for their period")

        return [RecommendationDraft(
            title=f"Billing run {run.as_of.isoformat()} - {n} bill(s), {total}",
            summary="; ".join(parts) + ".",
            rationale=(
                "Each bill is the confirmed fee schedule applied to the mandate's cash and holdings "
                "valued on the billing date, prorated for any part period, with household accounts "
                "billed on their combined balance. Approving issues the bills and takes each fee from "
                "the mandate's own cash; a bill with no cash behind it stays issued and unpaid. "
                "Held bills are not issued."
            ),
            confidence=0.95, priority=2,
            subject=Subject("firm", ctx.firm.id, ctx.firm.name),
            payload={"billing_run_id": str(run.id), "as_of": run.as_of.isoformat(), "invoices": n,
                     "held": held, "amount_by_currency": summary.get("amount_by_currency", {}),
                     "no_agreement": [u["mandate_name"] for u in unagreed], "acknowledge": []},
            evidence={"blocks": dict(blocks), "warnings": dict(warns),
                      "already_billed": summary.get("already_billed", 0)},
        )]

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        s = ctx.session
        payload = {**(recommendation.payload or {}), **(recommendation.modified_payload or {})}
        run = await s.get(BillingRun, uuid.UUID(payload["billing_run_id"]))
        if run is None:
            return {"executed": False, "note": "The billing run no longer exists."}
        approver = await s.get(User, recommendation.decided_by) if recommendation.decided_by else None
        try:
            result = await billing_run.approve_run(
                s, run, approved_by=approver.email if approver else "unknown",
                acknowledge=payload.get("acknowledge") or [])
        except billing_run.BillingError as exc:
            return {"executed": False, "note": str(exc)}
        run.recommendation_id = recommendation.id
        return {"executed": result["issued"] > 0, **result,
                "note": (f"Issued {result['issued']} bill(s); {result['debited']} paid from cash, "
                         f"{result['insufficient_cash']} issued with not enough cash, {result['held']} held.")}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        payload = {**(recommendation.payload or {}), **(recommendation.modified_payload or {})}
        run = await ctx.session.get(BillingRun, uuid.UUID(payload["billing_run_id"]))
        if run is None or run.status != "issued":
            return {"reversed": False, "note": "There is nothing issued to offset."}
        result = await billing_run.credit_run(
            ctx.session, run, reason="Billing run rolled back", by="billing rollback")
        return {"reversed": result["credit_notes"] > 0,
                "note": (f"Offset {result['credit_notes']} bill(s) with credit notes totalling "
                         f"{result['credited']:,.2f}; fees already taken were returned to cash as separate "
                         "entries. The original bills are kept.")}
