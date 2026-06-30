"""Provenance API — the decision ledger, chain verification, and surveillance flags."""
from __future__ import annotations

import csv
import io
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_firm
from app.core.db import get_db, utcnow
from app.core.security import STAFF_ROLES, get_current_user, staff_user, require_roles
from app.models.governance import AutonomyChange, LedgerEntry, SurveillanceFlag
from app.models.identity import User
from app.models.tenant import Firm
from app.provenance.evaluation import evaluate_firm, latest_evaluations
from app.provenance.ledger import append_entry, verify_chain

router = APIRouter(prefix="/api/provenance", tags=["provenance"], dependencies=[Depends(staff_user)])


@router.get("/compliance")
async def compliance_assessments(
    limit: int = 30, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    """Recent regulatory assessments + the active framework (the audit trail of cited compliance)."""
    from app.compliance.ontology import framework_for
    from app.models.compliance import ComplianceCheck
    from app.models.governance import Recommendation

    fw = framework_for(firm)
    rows = (await db.execute(
        select(ComplianceCheck).where(ComplianceCheck.firm_id == firm.id)
        .order_by(ComplianceCheck.created_at.desc()).limit(limit)
    )).scalars().all()
    titles = {}
    rec_ids = [r.recommendation_id for r in rows if r.recommendation_id]
    if rec_ids:
        for rid, title in (await db.execute(
            select(Recommendation.id, Recommendation.title).where(Recommendation.id.in_(rec_ids))
        )).all():
            titles[rid] = title
    by_status = {"clear": 0, "flags": 0, "blocked": 0}
    for r in rows:
        by_status[r.status] = by_status.get(r.status, 0) + 1
    return {
        "framework": {"regime": fw.regime, "name": fw.name, "version": fw.version,
                      "authority": fw.authority, "rule_count": len(fw.rules)},
        "by_status": by_status,
        "assessments": [
            {"id": str(r.id), "agent_key": r.agent_key, "status": r.status,
             "subject": titles.get(r.recommendation_id), "results": r.results,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }


@router.get("/ledger")
async def ledger(
    limit: int = 100, firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)
):
    entries = (
        await db.execute(
            select(LedgerEntry).where(LedgerEntry.firm_id == firm.id)
            .order_by(LedgerEntry.seq.desc()).limit(limit)
        )
    ).scalars().all()
    return [
        {
            "seq": e.seq, "event_type": e.event_type, "agent_key": e.agent_key,
            "actor": e.actor, "entry_hash": e.entry_hash, "prev_hash": e.prev_hash,
            "content": e.content, "created_at": e.created_at.isoformat() if e.created_at else None,
            "recommendation_id": str(e.recommendation_id) if e.recommendation_id else None,
        }
        for e in entries
    ]


@router.get("/ledger/verify")
async def verify(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Recompute the hash chain and report integrity (tamper-evidence)."""
    return await verify_chain(db, firm.id)


@router.get("/evaluations")
async def evaluations(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    """Latest quality score per agent from the evaluation harness."""
    return await latest_evaluations(db, firm.id)


@router.post("/evaluate")
async def run_evaluation(
    user: User = Depends(require_roles(*STAFF_ROLES)),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Run the evaluation harness now; applies adaptive autonomy on regressions."""
    return await evaluate_firm(db, firm.id)


@router.get("/autonomy-changes")
async def autonomy_changes(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(
            select(AutonomyChange).where(AutonomyChange.firm_id == firm.id)
            .order_by(AutonomyChange.created_at.desc()).limit(50)
        )
    ).scalars().all()
    return [
        {"agent_key": c.agent_key, "from_tier": c.from_tier, "to_tier": c.to_tier,
         "paused": c.paused, "automatic": c.automatic, "reason": c.reason,
         "created_at": c.created_at.isoformat() if c.created_at else None}
        for c in rows
    ]


class ResolveIn(BaseModel):
    resolution_note: str | None = None
    resolved: bool = True


class EscalateIn(BaseModel):
    escalated_to: str  # email or role name
    escalation_note: str | None = None


@router.patch("/surveillance/{flag_id}/resolve")
async def resolve_flag(
    flag_id: uuid.UUID,
    body: ResolveIn = Body(default_factory=ResolveIn),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    flag = await db.get(SurveillanceFlag, flag_id)
    if not flag or flag.firm_id != firm.id:
        raise HTTPException(404, "Flag not found.")
    flag.resolved = body.resolved
    flag.resolution_note = body.resolution_note
    flag.resolved_by = user.email
    flag.resolved_at = utcnow()
    await append_entry(db, firm_id=firm.id, event_type="flag_resolved",
                       agent_key=flag.target_agent_key, actor=user.email,
                       content={"flag_id": str(flag_id), "severity": flag.severity,
                                "category": flag.category, "resolution_note": body.resolution_note})
    return {"ok": True, "resolved": body.resolved}


@router.post("/surveillance/{flag_id}/escalate")
async def escalate_flag(
    flag_id: uuid.UUID, body: EscalateIn,
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    flag = await db.get(SurveillanceFlag, flag_id)
    if not flag or flag.firm_id != firm.id:
        raise HTTPException(404, "Flag not found.")
    flag.escalated = True
    flag.escalated_to = body.escalated_to
    flag.escalated_at = utcnow()
    flag.escalation_note = body.escalation_note
    await append_entry(db, firm_id=firm.id, event_type="flag_escalated",
                       agent_key=flag.target_agent_key, actor=user.email,
                       content={"flag_id": str(flag_id), "severity": flag.severity,
                                "escalated_to": body.escalated_to,
                                "note": body.escalation_note})
    return {"ok": True, "escalated_to": body.escalated_to}


@router.get("/ledger/export")
async def export_ledger(
    format: str = "csv",
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Export the full decision ledger as CSV or JSONL for auditors."""
    entries = (
        await db.execute(
            select(LedgerEntry).where(LedgerEntry.firm_id == firm.id)
            .order_by(LedgerEntry.seq)
        )
    ).scalars().all()

    if format == "jsonl":
        import json
        lines = [
            json.dumps({
                "seq": e.seq, "event_type": e.event_type, "agent_key": e.agent_key,
                "actor": e.actor, "entry_hash": e.entry_hash, "prev_hash": e.prev_hash,
                "content": e.content,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })
            for e in entries
        ]
        return StreamingResponse(
            io.StringIO("\n".join(lines)),
            media_type="application/x-ndjson",
            headers={"Content-Disposition": "attachment; filename=audit_ledger.jsonl"},
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["seq", "event_type", "agent_key", "actor", "entry_hash", "prev_hash",
                     "created_at", "content_summary"])
    for e in entries:
        summary = ""
        if isinstance(e.content, dict):
            summary = str(e.content.get("recommendation",
                          e.content.get("action",
                          e.content.get("finding", ""))))[:200]
        writer.writerow([
            e.seq, e.event_type, e.agent_key or "", e.actor or "",
            e.entry_hash, e.prev_hash or "",
            e.created_at.isoformat() if e.created_at else "",
            summary,
        ])
    output.seek(0)
    return StreamingResponse(
        output,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_ledger.csv"},
    )


@router.get("/reports/generate")
async def generate_compliance_report(
    date_from: str, date_to: str, format: str = "csv",
    user: User = Depends(get_current_user),
    firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db),
):
    """Generate a compliance summary report for the given date range (CSV or JSON)."""
    from datetime import date as _date
    try:
        d_from = _date.fromisoformat(date_from)
        d_to = _date.fromisoformat(date_to)
    except ValueError:
        raise HTTPException(status_code=400, detail="date_from and date_to must be YYYY-MM-DD")

    from app.models.governance import Recommendation
    from app.models.enums import RecommendationStatus

    # Surveillance flags in range.
    flags = (await db.execute(
        select(SurveillanceFlag).where(
            SurveillanceFlag.firm_id == firm.id,
            SurveillanceFlag.created_at >= d_from,
            SurveillanceFlag.created_at <= d_to,
        ).order_by(SurveillanceFlag.created_at)
    )).scalars().all()

    # Ledger entries in range.
    entries = (await db.execute(
        select(LedgerEntry).where(
            LedgerEntry.firm_id == firm.id,
            LedgerEntry.created_at >= d_from,
            LedgerEntry.created_at <= d_to,
        ).order_by(LedgerEntry.created_at)
    )).scalars().all()

    # Recommendations in range.
    recs = (await db.execute(
        select(Recommendation).where(
            Recommendation.firm_id == firm.id,
            Recommendation.created_at >= d_from,
            Recommendation.created_at <= d_to,
        )
    )).scalars().all()

    flags_open = sum(1 for f in flags if not f.resolved)
    flags_resolved = sum(1 for f in flags if f.resolved)
    flags_escalated = sum(1 for f in flags if f.escalated)
    recs_approved = sum(1 for r in recs if r.status == RecommendationStatus.APPROVED)
    recs_dismissed = sum(1 for r in recs if r.status == RecommendationStatus.DISMISSED)

    if format == "json":
        return {
            "firm": firm.name, "period_from": date_from, "period_to": date_to,
            "generated_by": user.email,
            "summary": {
                "surveillance_flags_total": len(flags),
                "flags_open": flags_open, "flags_resolved": flags_resolved,
                "flags_escalated": flags_escalated,
                "ledger_entries": len(entries),
                "recommendations_total": len(recs),
                "recs_approved": recs_approved, "recs_dismissed": recs_dismissed,
            },
            "flags": [
                {"date": f.created_at.date().isoformat() if f.created_at else None,
                 "severity": f.severity, "category": f.category, "finding": f.finding[:120],
                 "resolved": f.resolved, "escalated": f.escalated}
                for f in flags
            ],
        }

    # CSV output.
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Section", "Metric", "Value"])
    w.writerow(["Summary", "Period", f"{date_from} to {date_to}"])
    w.writerow(["Summary", "Firm", firm.name])
    w.writerow(["Summary", "Surveillance flags (total)", len(flags)])
    w.writerow(["Summary", "Flags open", flags_open])
    w.writerow(["Summary", "Flags resolved", flags_resolved])
    w.writerow(["Summary", "Flags escalated", flags_escalated])
    w.writerow(["Summary", "Ledger entries", len(entries)])
    w.writerow(["Summary", "Recommendations (total)", len(recs)])
    w.writerow(["Summary", "Recommendations approved", recs_approved])
    w.writerow(["Summary", "Recommendations dismissed", recs_dismissed])
    w.writerow([])
    w.writerow(["Flag date", "Severity", "Category", "Finding", "Resolved", "Escalated"])
    for f in flags:
        w.writerow([
            f.created_at.date().isoformat() if f.created_at else "",
            f.severity, f.category, f.finding[:120],
            "Yes" if f.resolved else "No", "Yes" if f.escalated else "No",
        ])
    buf.seek(0)
    fname = f"compliance_report_{date_from}_{date_to}.csv"
    return StreamingResponse(buf, media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename={fname}"})


@router.get("/surveillance")
async def surveillance(firm: Firm = Depends(current_firm), db: AsyncSession = Depends(get_db)):
    flags = (
        await db.execute(
            select(SurveillanceFlag).where(SurveillanceFlag.firm_id == firm.id)
            .order_by(SurveillanceFlag.created_at.desc()).limit(200)
        )
    ).scalars().all()
    return [
        {
            "id": str(f.id), "severity": f.severity, "category": f.category, "finding": f.finding,
            "target_agent_key": f.target_agent_key, "resolved": f.resolved,
            "auto_paused_agent": f.auto_paused_agent,
            "recommendation_id": str(f.recommendation_id) if f.recommendation_id else None,
            "created_at": f.created_at.isoformat() if f.created_at else None,
            "resolution_note": f.resolution_note, "resolved_by": f.resolved_by,
            "resolved_at": f.resolved_at.isoformat() if f.resolved_at else None,
            "escalated": f.escalated, "escalated_to": f.escalated_to,
            "escalated_at": f.escalated_at.isoformat() if f.escalated_at else None,
            "escalation_note": f.escalation_note,
        }
        for f in flags
    ]
