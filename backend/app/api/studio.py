"""Aurea Studio API — the adviser cockpit surfaces (spec §8, Table 13)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.atlas.base import Subject
from app.atlas.runtime import AgentPausedError, run_agent
from app.aurea_core.graph import household_brain, list_households
from app.core.db import get_db
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.llm import usage as llm_usage
from app.llm.service import firm_llm_creds, llm_service
from app.models.enums import AgentKey, AgentRunStatus, HumanAction, MandateType, RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.models.graph import Mandate
from app.models.identity import User
from app.models.tenant import Firm

router = APIRouter(prefix="/api/studio", tags=["studio"], dependencies=[Depends(staff_user)])


class DecisionIn(BaseModel):
    action: HumanAction
    note: str | None = None
    modified_payload: dict | None = None


def _rec_dict(r: Recommendation) -> dict:
    return {
        "id": str(r.id), "run_id": str(r.run_id), "agent_key": r.agent_key, "tier": r.tier,
        "status": r.status, "title": r.title, "summary": r.summary, "rationale": r.rationale,
        "confidence": r.confidence, "priority": r.priority, "subject_label": r.subject_label,
        "subject_type": r.subject_type, "subject_id": str(r.subject_id) if r.subject_id else None,
        "payload": r.modified_payload or r.payload, "evidence": r.evidence, "citations": r.citations,
        "decision_note": r.decision_note,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    }


@router.get("/feed")
async def nba_feed(
    status: str = "open", firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """The next-best-action feed: prioritised open recommendations across the book."""
    stmt = select(Recommendation).where(Recommendation.firm_id == firm.id)
    if status == "open":
        stmt = stmt.where(Recommendation.status == RecommendationStatus.PROPOSED)
    recs = (
        await db.execute(stmt.order_by(Recommendation.priority.asc(), Recommendation.created_at.desc()).limit(200))
    ).scalars().all()
    return [_rec_dict(r) for r in recs]


@router.get("/recommendations/{rec_id}")
async def recommendation_detail(
    rec_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    rec = await db.get(Recommendation, rec_id)
    if not rec or rec.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    return _rec_dict(rec)


@router.get("/recommendations/{rec_id}/compliance")
async def recommendation_compliance(
    rec_id: uuid.UUID, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """The cited regulatory assessment for a recommendation (latest), for the Compliance panel."""
    from app.models.compliance import ComplianceCheck
    row = (await db.execute(
        select(ComplianceCheck).where(ComplianceCheck.recommendation_id == rec_id,
                                      ComplianceCheck.firm_id == firm.id)
        .order_by(ComplianceCheck.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not row:
        return {"available": False}
    return {"available": True, "regime": row.regime, "version": row.framework_version,
            "status": row.status, "results": row.results}


@router.post("/recommendations/{rec_id}/decide")
async def decide_recommendation(
    rec_id: uuid.UUID, body: DecisionIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """The approve / modify / dismiss surface — the HITL gate (spec Table 13)."""
    from app.atlas.runtime import decide  # local import to avoid cycle

    rec = await db.get(Recommendation, rec_id)
    if not rec or rec.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status != RecommendationStatus.PROPOSED:
        raise HTTPException(status_code=409, detail=f"Already {rec.status}")

    rec = await decide(
        db, firm=firm, recommendation=rec, action=body.action,
        actor_id=user.id, actor_label=f"{user.full_name} ({user.role})",
        note=body.note, modified_payload=body.modified_payload,
    )
    return _rec_dict(rec)


class ReviseIn(BaseModel):
    note: str
    cgt_budget: float | None = None
    drift_band: float | None = None
    protect: list[str] | None = None  # holdings the adviser asked not to sell


@router.post("/recommendations/{rec_id}/revise")
async def revise_recommendation(
    rec_id: uuid.UUID, body: ReviseIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Re-run the agent incorporating the adviser's comment, superseding the original proposal."""
    rec = await db.get(Recommendation, rec_id)
    if not rec or rec.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    if rec.status != RecommendationStatus.PROPOSED:
        raise HTTPException(status_code=409, detail=f"Already {rec.status}")
    if rec.agent_key != AgentKey.DRIFT_REBALANCING.value or rec.subject_type != "mandate":
        raise HTTPException(status_code=400, detail="Revise is available for rebalancing proposals.")

    overrides = {"note": body.note}
    if body.cgt_budget is not None:
        overrides["cgt_budget"] = body.cgt_budget
    if body.drift_band is not None:
        overrides["drift_band"] = body.drift_band
    if body.protect:
        overrides["protect"] = body.protect

    mandate = await db.get(Mandate, rec.subject_id)
    mandate_type = MandateType(mandate.mandate_type) if mandate else None
    subject = Subject("mandate", rec.subject_id, rec.subject_label)

    try:
        run = await run_agent(db, firm=firm, agent_key=AgentKey.DRIFT_REBALANCING, subject=subject,
                              trigger="revise", mandate_type=mandate_type, overrides=overrides)
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    # Supersede the original proposal (full lineage kept).
    rec.status = RecommendationStatus.DISMISSED
    rec.decided_by = user.id
    rec.decided_at = func.now()
    rec.decision_note = f"Superseded by revision: {body.note}"

    new_recs = (
        await db.execute(select(Recommendation).where(Recommendation.run_id == run.id))
    ).scalars().all()
    for nr in new_recs:
        nr.payload = {**(nr.payload or {}), "revised_from": str(rec.id), "revision_note": body.note}

    from app.provenance import ledger
    await ledger.append_entry(db, firm_id=firm.id, event_type="revision", agent_key=rec.agent_key,
                              run_id=run.id, recommendation_id=rec.id, actor=f"{user.full_name} ({user.role})",
                              content={"superseded": str(rec.id), "note": body.note, "overrides": overrides,
                                       "new_recommendations": [str(r.id) for r in new_recs]})
    await db.flush()

    if not new_recs:
        return {"revised": True, "new_recommendation": None,
                "message": "After your changes there is nothing to rebalance — within tolerance."}
    return {"revised": True, "new_recommendation": _rec_dict(new_recs[0])}


@router.post("/scan")
async def book_scan(
    agent: str = "next_best_action",
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Run a monitoring agent across the whole book (firm subject) and surface new items."""
    key = AgentKey.CLIENT_CARE if agent == "client_care" else AgentKey.NEXT_BEST_ACTION
    try:
        run = await run_agent(db, firm=firm, agent_key=key,
                              subject=Subject("firm", firm.id, firm.name), trigger="book_scan")
    except AgentPausedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    n = (
        await db.execute(
            select(func.count(Recommendation.id)).where(Recommendation.run_id == run.id)
        )
    ).scalar_one()
    return {"run_id": str(run.id), "agent": key.value, "items_surfaced": n}


@router.post("/recommendations/{rec_id}/rollback")
async def rollback_recommendation(
    rec_id: uuid.UUID, body: dict | None = None,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Reverse an approved/modified/executed recommendation (spec §10.2 — rollback first-class)."""
    from app.atlas.runtime import rollback as do_rollback

    rec = await db.get(Recommendation, rec_id)
    if not rec or rec.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Recommendation not found")
    try:
        rec = await do_rollback(db, firm=firm, recommendation=rec, actor_id=user.id,
                                actor_label=f"{user.full_name} ({user.role})",
                                note=(body or {}).get("note"))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return _rec_dict(rec)


class DraftCommIn(BaseModel):
    tone: str = "formal"  # formal / warm
    output_type: str = "email"  # email / letter / sms_summary


@router.post("/recommendations/{rec_id}/draft-communication")
async def draft_communication(
    rec_id: uuid.UUID, body: DraftCommIn,
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Draft a client-facing communication from an approved recommendation (G5)."""
    from app.provenance import ledger as _ledger

    rec = await db.get(Recommendation, rec_id)
    if not rec or rec.firm_id != firm.id:
        raise HTTPException(status_code=404, detail="Recommendation not found")

    client_name = rec.subject_label or "Valued client"
    tone_desc = "warm and approachable" if body.tone == "warm" else "formal and professional"
    type_desc = {
        "email": "a concise client email (subject line + 3–4 paragraphs)",
        "letter": "a formal client letter (greeting, body paragraphs, sign-off)",
        "sms_summary": "a short SMS-ready summary (under 160 characters)",
    }.get(body.output_type, "a client email")

    prompt = (
        f"You are a wealth adviser drafting {type_desc} to inform a client about "
        f"a portfolio recommendation that has been reviewed and approved.\n\n"
        f"Client name: {client_name}\n"
        f"Recommendation title: {rec.title}\n"
        f"Summary: {rec.summary}\n"
        f"Rationale: {rec.rationale}\n\n"
        f"Tone: {tone_desc}. Do not include specific dollar amounts or order details. "
        f"Use plain language the client will understand. "
        f"If writing an email or letter, begin with a subject line prefixed 'Subject: '."
    )

    try:
        creds = await firm_llm_creds(db, firm)
        svc = llm_service(creds)
        resp = await svc.complete([{"role": "user", "content": prompt}], max_tokens=600)
        text_out = resp.content if hasattr(resp, "content") else str(resp)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"LLM unavailable: {exc}")

    # Split subject from body if present
    subject = None
    body_text = text_out
    lines = text_out.strip().splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip()
        body_text = "\n".join(lines[1:]).strip()

    await _ledger.append_entry(
        db, firm_id=firm.id, event_type="communication_drafted",
        agent_key=rec.agent_key, recommendation_id=rec.id,
        actor=f"{user.full_name} ({user.role})",
        content={"tone": body.tone, "output_type": body.output_type,
                 "subject": subject, "client_name": client_name},
    )

    return {"subject": subject, "body": body_text, "tone": body.tone, "output_type": body.output_type}


@router.get("/capacity")
async def capacity(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Capacity & outcomes — how AI-reclaimed time is translating into client outcomes."""
    total_runs = (
        await db.execute(select(func.count(AgentRun.id)).where(AgentRun.firm_id == firm.id))
    ).scalar_one()
    by_status = {}
    for st in RecommendationStatus:
        n = (
            await db.execute(
                select(func.count(Recommendation.id)).where(
                    Recommendation.firm_id == firm.id, Recommendation.status == st
                )
            )
        ).scalar_one()
        by_status[st.value] = n
    decided = by_status["approved"] + by_status["modified"] + by_status["dismissed"] + by_status["executed"]
    # Indicative time saved: ~25 min of prep/monitoring per agent recommendation handled.
    minutes_saved = (total_runs + decided) * 25
    return {
        "total_agent_runs": total_runs,
        "recommendations_by_status": by_status,
        "open_items": by_status["proposed"],
        "decisions_made": decided,
        "estimated_hours_reclaimed": round(minutes_saved / 60, 1),
    }


@router.post("/ask")
async def ask_your_book(
    body: dict, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Ask-your-book: natural-language / what-if query over the client brain (cited, governed)."""
    question = (body.get("question") or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="Empty question")

    households = await list_households(db, firm.id)
    # Build a compact, governed snapshot of the book to ground the answer.
    snapshot_lines = []
    structured = []
    for h in households[:40]:
        brain = await household_brain(db, uuid.UUID(h["id"]))
        if not brain:
            continue
        mix = brain["totals"]["by_asset_class"]
        total = brain["totals"]["total_value"] or 1.0
        equity_pct = mix.get("equity", 0) / total
        losers = [
            p for acc in brain["accounts"] for p in acc["positions"] if p["unrealised_gain"] < 0
        ]
        structured.append({
            "household": h["name"], "total": total, "equity_pct": round(equity_pct, 3),
            "has_loss_to_harvest": bool(losers),
            "loss_total": round(sum(p["unrealised_gain"] for p in losers), 2),
        })
        snapshot_lines.append(
            f"- {h['name']}: total ${total:,.0f}; equity {equity_pct:.0%}; "
            f"{'has' if losers else 'no'} harvestable losses "
            f"(${-sum(p['unrealised_gain'] for p in losers):,.0f})."
        )

    snapshot = "\n".join(snapshot_lines) or "No client data."

    def fallback() -> str:
        ql = question.lower()
        hits = []
        for s in structured:
            if "overweight" in ql and "equit" in ql and s["equity_pct"] > 0.6:
                hits.append(s["household"])
            elif ("loss" in ql or "harvest" in ql) and s["has_loss_to_harvest"]:
                hits.append(s["household"])
        if hits:
            return "Households matching your query: " + ", ".join(sorted(set(hits))) + "."
        return ("I read this over the governed client brain. Based on the current snapshot, no "
                "households clearly match — refine the question (e.g. 'overweight equities with a "
                "loss to harvest').")

    gp = await llm_usage.gateway_params(db, firm, base_max_tokens=700)
    result = await llm_service.generate(
        task="advice",
        system=("You answer an adviser's natural-language questions strictly from the governed "
                "client-brain snapshot provided. Cite household names. If the data does not "
                "support an answer, say so. Never invent figures."),
        prompt=f"Question: {question}\n\nGoverned client-brain snapshot:\n{snapshot}",
        firm_model_config=firm.model_config_json or {},
        creds=firm_llm_creds(firm),
        fallback=fallback,
        max_tokens=gp["max_tokens"],
        pii_terms=gp["pii_terms"], pii_categories=gp["pii_categories"], redact=gp["redact"],
        allow_fallback=gp["allow_fallback"], force_fallback=gp["force_fallback"],
    )
    try:
        await llm_usage.record(db, firm.id, agent="ask", task="advice", result=result)
    except Exception:
        pass
    return {
        "question": question,
        "answer": result.text,
        "grounded_on": [s["household"] for s in structured],
        "is_fallback": result.is_fallback,
        "governed": True,
    }


# ── Notification bell feed ────────────────────────────────────────────────────

@router.get("/notifications")
async def get_notifications(
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Aggregated alert feed for the notification bell (pending recs + critical flags)."""
    from app.models.governance import SurveillanceFlag

    items = []

    # All staff: pending recommendations (highest-priority first, max 5)
    recs = (await db.execute(
        select(Recommendation)
        .where(Recommendation.firm_id == firm.id, Recommendation.status == RecommendationStatus.PROPOSED)
        .order_by(Recommendation.priority.asc(), Recommendation.created_at.desc())
        .limit(5)
    )).scalars().all()
    for r in recs:
        items.append({
            "type": "recommendation", "id": str(r.id),
            "title": r.title,
            "summary": (r.summary or "")[:80],
            "priority": r.priority, "agent_key": r.agent_key,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })

    # Admin/compliance only: unresolved high/medium surveillance flags
    if user.role in ("admin", "compliance"):
        flags = (await db.execute(
            select(SurveillanceFlag)
            .where(
                SurveillanceFlag.firm_id == firm.id,
                SurveillanceFlag.resolved == False,
                SurveillanceFlag.severity.in_(["high", "medium"]),
            )
            .order_by(SurveillanceFlag.created_at.desc())
            .limit(5)
        )).scalars().all()
        for f in flags:
            items.append({
                "type": "surveillance", "id": str(f.id),
                "title": f"Compliance: {f.category}",
                "summary": (f.finding or "")[:80],
                "severity": f.severity,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            })

    # Holding alerts (I8)
    from app.models.governance import SurveillanceFlag
    alerts = (await db.execute(
        select(SurveillanceFlag)
        .where(
            SurveillanceFlag.firm_id == firm.id,
            SurveillanceFlag.resolved == False,
            SurveillanceFlag.kind == "holding_alert",
        )
        .order_by(SurveillanceFlag.created_at.desc())
        .limit(5)
    )).scalars().all()
    for a in alerts:
        items.append({
            "type": "holding_alert", "id": str(a.id),
            "title": f"Holding alert: {a.category}",
            "summary": (a.finding or "")[:80],
            "severity": a.severity,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        })

    return {"count": len(items), "pending_recs": len(recs), "items": items[:10]}


# ── I5: Cross-Household Family Aggregate ─────────────────────────────────────

@router.get("/family")
async def get_family_aggregate(
    person_id: uuid.UUID,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """Follow intergenerational/spouse/parent/child edges and return consolidated family view."""
    import uuid as _uuid
    from app.models.graph import (
        Account, Household, LegalEntity, Mandate, Person, RelationshipEdge,
    )
    from app.models.portfolio import Holding
    from app.models.graph import Goal

    FAMILY_KINDS = {"intergenerational", "spouse", "parent", "child"}

    # BFS over relationship edges to collect all linked person/entity IDs.
    visited_person_ids: set[_uuid.UUID] = {person_id}
    frontier = [person_id]
    while frontier:
        next_frontier: list[_uuid.UUID] = []
        edges = (await db.execute(
            select(RelationshipEdge).where(
                RelationshipEdge.firm_id == firm.id,
                RelationshipEdge.kind.in_(FAMILY_KINDS),
                RelationshipEdge.from_id.in_(frontier),
            )
        )).scalars().all()
        for e in edges:
            if e.to_type == "person" and e.to_id not in visited_person_ids:
                visited_person_ids.add(e.to_id)
                next_frontier.append(e.to_id)
        frontier = next_frontier

    # Find all households these persons belong to.
    person_rows = (await db.execute(
        select(Person).where(Person.id.in_(visited_person_ids))
    )).scalars().all()
    household_ids: set[_uuid.UUID] = {p.household_id for p in person_rows if p.household_id}

    # Household names.
    households = (await db.execute(
        select(Household).where(Household.id.in_(household_ids))
    )).scalars().all()

    # AUM: sum via person → mandate → account → holding.
    total_aum = 0.0
    if visited_person_ids:
        aum_row = (await db.execute(
            select(func.coalesce(func.sum(Holding.market_value), 0))
            .join(Account, Account.id == Holding.account_id)
            .join(Mandate, Mandate.id == Account.mandate_id)
            .where(Mandate.firm_id == firm.id, Mandate.person_id.in_(visited_person_ids))
        )).scalar_one()
        total_aum = float(aum_row)

    # Goals across all households.
    goals = []
    if household_ids:
        goal_rows = (await db.execute(
            select(Goal).where(Goal.firm_id == firm.id, Goal.household_id.in_(household_ids))
        )).scalars().all()
        goals = [
            {"name": g.name, "kind": g.kind, "target_amount": float(g.target_amount or 0),
             "household_id": str(g.household_id)}
            for g in goal_rows
        ]

    return {
        "person_ids": [str(p) for p in visited_person_ids],
        "household_count": len(household_ids),
        "households": [{"id": str(h.id), "name": h.name, "segment": h.segment} for h in households],
        "total_family_aum": total_aum,
        "goal_count": len(goals),
        "goals": goals,
    }


# ── I6: Agent Run History ─────────────────────────────────────────────────────

@router.get("/agents/history")
async def get_agent_run_history(
    agent_key: str | None = None,
    status: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 50,
    firm: Firm = Depends(current_firm),
    db: AsyncSession = Depends(get_db),
):
    """List historical AgentRun records with optional filters."""
    from datetime import date as _date
    q = select(AgentRun).where(AgentRun.firm_id == firm.id)
    if agent_key:
        q = q.where(AgentRun.agent_key == agent_key)
    if status:
        q = q.where(AgentRun.status == status)
    if date_from:
        try:
            d = _date.fromisoformat(date_from)
            q = q.where(AgentRun.created_at >= d)
        except ValueError:
            pass
    if date_to:
        try:
            d2 = _date.fromisoformat(date_to)
            q = q.where(AgentRun.created_at <= d2)
        except ValueError:
            pass
    q = q.order_by(AgentRun.created_at.desc()).limit(min(limit, 200))
    runs = (await db.execute(q)).scalars().all()
    return [
        {
            "id": str(r.id),
            "agent_key": r.agent_key,
            "status": r.status,
            "tier": r.tier,
            "trigger": r.trigger,
            "duration_ms": r.duration_ms if hasattr(r, "duration_ms") else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in runs
    ]
