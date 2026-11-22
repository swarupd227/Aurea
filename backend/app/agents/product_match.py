"""Product Match: which of each adviser's clients a newly offerable product fits (B-228).

For every product that may be offered (approved on the shelf, a target market in force and, where it needs one,
compliance's sign-off), it reads the whole book through `aurea_core/product_fit.py` and raises one list per adviser:
the clients it fits, ordered by the signals on file, and those who need a fact recorded first. Clients it is not for
are counted, and the reasons stay with the fit check.

Choosing whom to offer a product to is advice, so the agent stops at a proposal whatever its tier
(`agent_runs.forced_to_proposal`), and only the adviser the list names or a branch leader approves it
(`decision_rights.DISTRIBUTION_AGENTS`, `named_decider`). Approving reads each client again, gives the adviser a task
per client who still fits and records the pitch with the fit it rested on; removing a client keeps the adviser's
reason. Nothing is sent to a client and nothing is bought.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select

from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time
from app.aurea_core import product_distribution as pd
from app.aurea_core import product_fit
from app.models.enums import AgentKey, AutonomyTier, TaskStatus
from app.models.portfolio import Instrument
from app.models.product_distribution import ProductTargetMarket

TASK_DAYS = 14
UNASSIGNED = "no adviser on record"


def _row(r: dict) -> dict:
    """What a list carries of each client: enough for the card and the task, nothing more."""
    return {"household_id": r["household_id"], "household": r["household"], "mandate_id": r["mandate_id"],
            "mandate": r["mandate"], "points": r["points"], "signals": r["signals"],
            "missing": [l["said"] for l in r["lines"] if l["result"] == "missing"]}


class ProductMatchAgent(BaseAgent):
    key = AgentKey.PRODUCT_MATCH
    name = "Product Match"
    lifecycle_stage = "manage_optimise"
    default_tier = AutonomyTier.TIER_1
    scheduled = True
    # A product closed, watched or no longer signed off is not offered: its lists go.
    expires_unraised = True

    def on_approval(self, rec) -> str:
        return ("A task for the adviser per client who still fits, to discuss the product; nothing is sent to a client "
                "and nothing is bought")

    async def sense(self, ctx: AgentContext) -> dict:
        s = ctx.session
        ids = (await s.execute(select(ProductTargetMarket.instrument_id).where(
            ProductTargetMarket.firm_id == ctx.firm.id).distinct())).scalars().all()
        if ctx.subject.type == "instrument" and ctx.subject.id:
            ids = [i for i in ids if i == ctx.subject.id]
        products = []
        for inst in (await s.execute(select(Instrument).where(Instrument.id.in_(ids)))).scalars().all() if ids else []:
            fit = await product_fit.for_book(s, ctx.firm, inst)
            if fit["offerable"]:
                products.append({"instrument_id": str(inst.id), "symbol": inst.symbol, "name": inst.name, "fit": fit})
        return {"products": products}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        out: list[RecommendationDraft] = []
        for p in sensed["products"]:
            fit, tm = p["fit"], p["fit"]["target_market"]
            by_adviser: dict[str | None, dict] = {}
            for verdict in ("fits", "needs_fact", "not_for"):
                for r in fit[verdict]:
                    group = by_adviser.setdefault(r["adviser_id"], {"adviser": r["adviser"], "fits": [], "needs_fact": [], "not_for": 0})
                    if verdict == "not_for":
                        group["not_for"] += 1
                    else:
                        group[verdict].append(_row(r))
            for adviser_id, g in by_adviser.items():
                if not g["fits"]:
                    continue
                who = g["adviser"] or UNASSIGNED
                n = len(g["fits"])
                leaders = "; ".join(f"{r['household']}: {', '.join(s['said'] for s in r['signals']) or 'fits, no signal on file'}"
                                    for r in g["fits"][:5])
                out.append(RecommendationDraft(
                    title=f"Clients for {p['name']}: {n} fit — {who}",
                    summary=(f"{n} of {who}'s clients fit {p['symbol']}; {len(g['needs_fact'])} need a fact first; "
                             f"{g['not_for']} it is not for."),
                    rationale=f"Read against target market version {tm['version']} ({tm['document']}). {leaders}.",
                    confidence=0.8, priority=2,
                    subject=Subject("instrument", uuid.UUID(p["instrument_id"]), p["name"]),
                    payload={"signal": "product_match", "instrument_id": p["instrument_id"], "symbol": p["symbol"],
                             "product": p["name"], "adviser_id": adviser_id, "adviser": g["adviser"],
                             "target_market_id": tm["id"], "target_market_version": tm["version"],
                             "fits": g["fits"], "needs_fact": g["needs_fact"], "not_for": g["not_for"],
                             "conflict": tm["conflict"], "document": tm["document"]},
                    evidence={"source": "Product fit check", "target_market_version": tm["version"],
                              "document": tm["document"], "as_of": fit["as_of"]},
                ))
        return out

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """Read each client again, as approving any proposal re-checks: a client who no longer fits is left off and named."""
        from app.aurea_core import tasks as task_rules
        from app.core import decision_rights
        from app.models.engagement import Task
        from app.models.graph import Household
        from app.models.identity import User

        rec = recommendation
        payload = rec.modified_payload or rec.payload or {}
        decider = await ctx.session.get(User, rec.decided_by) if rec.decided_by else None
        if decider is not None:
            decision_rights.named_decider(decider.role, decider.id, rec.agent_key, rec.payload, "approve")
        inst = await ctx.session.get(Instrument, uuid.UUID(payload["instrument_id"]))
        removed = {str(r.get("household_id")): (r.get("reason") or "").strip() for r in payload.get("removed") or []}
        kept = [r for r in payload.get("fits") or [] if r["household_id"] not in removed]
        today = firm_time.today(ctx.firm)
        by = decider.full_name if decider else "unknown"
        now = await product_fit.for_book(ctx.session, ctx.firm, inst, household_ids={uuid.UUID(r["household_id"]) for r in kept}) if kept else {"fits": [], "offerable": True}
        if not now["offerable"]:
            return {"executed": False, "note": f"{inst.symbol} can no longer be offered: {now['why_not']}. Nothing was added."}
        still = {r["household_id"]: r for r in now["fits"]}
        tm = await pd.target_market(ctx.session, ctx.firm.id, inst.id)
        adviser_id = uuid.UUID(payload["adviser_id"]) if payload.get("adviser_id") else None
        added, dropped = [], []
        for r in kept:
            fresh = still.get(r["household_id"])
            if fresh is None:
                dropped.append(r["household"])
                continue
            household = await ctx.session.get(Household, uuid.UUID(r["household_id"]))
            detail = "; ".join(s["said"] for s in fresh["signals"]) or "fits its target market"
            task = Task(firm_id=ctx.firm.id, household_id=household.id,
                        title=f"Discuss {inst.name} with {household.name}"[: task_rules.TITLE_MAX],
                        detail=f"{detail}. Target market version {tm.version if tm else '?'} ({tm.document if tm else 'no document'}).",
                        status=TaskStatus.OPEN, source=str(self.key.value), subject_label=household.name,
                        due_date=today + timedelta(days=TASK_DAYS), assigned_to=adviser_id,
                        assigned_by=rec.decided_by if adviser_id else None)
            ctx.session.add(task)
            await ctx.session.flush()
            ctx.session.add(pd.pitch(ctx.firm.id, instrument_id=inst.id, household_id=household.id, status="to_discuss", by=by,
                                     as_of=today, fit=product_fit.snapshot(fresh, tm),
                                     mandate_id=uuid.UUID(fresh["mandate_id"]) if fresh["mandate_id"] else None,
                                     adviser_id=adviser_id, target_market_id=tm.id if tm else None,
                                     recommendation_id=rec.id, task_id=task.id))
            added.append(str(task.id))
        for household_id, reason in removed.items():
            household = await ctx.session.get(Household, uuid.UUID(household_id))
            if household is not None and household.firm_id == ctx.firm.id:
                ctx.session.add(pd.pitch(ctx.firm.id, instrument_id=inst.id, household_id=household.id, status="not_suitable",
                                         by=by, as_of=today, adviser_id=adviser_id, target_market_id=tm.id if tm else None,
                                         recommendation_id=rec.id, reason=f"Removed from the list: {reason or 'no reason given'}"))
        await ctx.session.flush()
        note = f"Added {len(added)} task(s) to discuss {inst.symbol}"
        if dropped:
            note += f"; left off {', '.join(dropped)}, who no longer fit"
        if removed:
            note += f"; {len(removed)} removed with the adviser's reason"
        return {"executed": True, "task_ids": added, "dropped": dropped, "removed": len(removed), "note": note + "."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        from app.models.engagement import Task

        result = (recommendation.payload or {}).get("execution_result") or {}
        withdrawn = 0
        for task_id in result.get("task_ids") or []:
            task = await ctx.session.get(Task, uuid.UUID(task_id))
            if task is not None and str(task.status) == TaskStatus.OPEN.value:
                task.status = TaskStatus.DISMISSED
                withdrawn += 1
        latest = await pd.latest_pitches(ctx.session, ctx.firm.id, instrument_id=uuid.UUID((recommendation.payload or {})["instrument_id"]))
        today = firm_time.today(ctx.firm)
        for p in latest.values():
            if p.recommendation_id == recommendation.id and pd.is_open(p):
                ctx.session.add(pd.pitch(ctx.firm.id, instrument_id=p.instrument_id, household_id=p.household_id, status="withdrawn",
                                         by="rollback", as_of=today, fit=p.fit, mandate_id=p.mandate_id, adviser_id=p.adviser_id,
                                         target_market_id=p.target_market_id, recommendation_id=p.recommendation_id, task_id=p.task_id,
                                         reason="The list was rolled back."))
        await ctx.session.flush()
        return {"reversed": bool(withdrawn), "note": f"Withdrew {withdrawn} open task(s); any already worked stay."}
