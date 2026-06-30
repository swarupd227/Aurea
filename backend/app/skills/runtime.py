"""Run an advisor-defined skill as a governed Atlas agent.

The skill's plain-English instruction is the `think` step — Claude reasons over each client's brain
(PII-masked, cost-capped, metered via the model gateway) and proposes a recommendation. Output is
ALWAYS a proposal: skills are capped at Tier 2 and never execute on the world. Every proposal runs
through the same persistence, compliance/surveillance check and ledger as the built-in workforce."""
from __future__ import annotations

import json
import re

from app.atlas import activity
from app.atlas.base import Subject
from app.atlas.runtime import _ledger_content
from app.aurea_core.graph import household_brain, list_households
from app.core.db import utcnow
from app.core.logging import get_logger
from app.llm import usage as usage_mod
from app.llm.service import firm_llm_creds, llm_service
from app.models.enums import ActivityKind, AgentRunStatus, AutonomyTier, RecommendationStatus
from app.models.governance import AgentRun, Recommendation
from app.provenance import ledger, surveillance

log = get_logger("aurea.skills")

SKILL_AGENT_KEY = "skill"  # non-enum key — universal compliance rules apply, no executable action

_SYSTEM = (
    "You are running an adviser-defined skill inside {firm}, a wealth firm regulated by {reg}. "
    "Decide whether THIS client matches the adviser's instruction and, if so, draft a concise, "
    "governed recommendation in the firm's measured voice. Never guarantee outcomes; the named human "
    "adviser always decides. Respond with STRICT JSON only: "
    '{{"applies": true|false, "title": "...", "summary": "...", "rationale": "...", '
    '"priority": 1-3, "confidence": 0.0-1.0}}.'
)


def _snapshot(brain: dict) -> str:
    totals = brain["totals"]
    total = totals.get("total_value") or 1.0
    mix = totals.get("by_asset_class") or {}
    lines = [f"Total portfolio: ${total:,.0f}", f"Segment: {brain['household'].get('segment')}"]
    for cls, v in mix.items():
        lines.append(f"  {cls}: {v / total:.0%}")
    positions = [p for acc in brain.get("accounts", []) for p in acc.get("positions", [])]
    if positions:
        top = max(positions, key=lambda p: p.get("market_value", 0))
        lines.append(f"Largest holding: {top.get('instrument')} at {top.get('market_value', 0) / total:.0%}")
        losers = [p for p in positions if (p.get("unrealised_gain") or 0) < 0]
        if losers:
            lines.append(f"Positions in a loss: {len(losers)}")
    if brain.get("goals"):
        lines.append(f"Goals on file: {len(brain['goals'])}")
    if any(p.get("is_next_gen") for p in brain.get("persons", [])):
        lines.append("Has a next-generation family member")
    return "\n".join(lines)


async def _ask(session, firm, skill, brain) -> dict:
    snapshot = _snapshot(brain)
    system = _SYSTEM.format(firm=firm.name, reg=firm.regulator or "the local regulator")
    prompt = (f"Adviser's skill instruction:\n\"{skill.instruction}\"\n\n"
              f"Client household: {brain['household']['name']}\nSnapshot:\n{snapshot}")
    gp = await usage_mod.gateway_params(session, firm, base_max_tokens=500, agent_key=SKILL_AGENT_KEY)
    result = await llm_service.generate(
        task="advice", system=system, prompt=prompt,
        firm_model_config=firm.model_config_json or {}, creds=firm_llm_creds(firm),
        fallback=lambda: '{"applies": false}', max_tokens=gp["max_tokens"],
        pii_terms=gp["pii_terms"], pii_categories=gp["pii_categories"], redact=gp["redact"],
        allow_fallback=gp["allow_fallback"], force_fallback=gp["force_fallback"],
    )
    try:
        await usage_mod.record(session, firm.id, agent=SKILL_AGENT_KEY, task="advice", result=result)
    except Exception:
        pass
    m = re.search(r"\{.*\}", result.text or "", re.S)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return {"applies": False}


def _build_item(res: dict, hh: dict, skill) -> dict:
    return {"title": (res.get("title") or skill.name)[:200], "summary": res.get("summary", ""),
            "rationale": res.get("rationale", ""), "confidence": float(res.get("confidence", 0.7) or 0.7),
            "priority": int(res.get("priority", 3) or 3), "household": hh["name"], "household_id": hh["id"]}


def _rec_dict(r) -> dict:
    return {"id": str(r.id), "run_id": str(r.run_id), "agent_key": r.agent_key, "tier": r.tier,
            "status": r.status, "title": r.title, "summary": r.summary, "rationale": r.rationale,
            "confidence": r.confidence, "priority": r.priority, "subject_label": r.subject_label,
            "subject_type": r.subject_type, "subject_id": str(r.subject_id) if r.subject_id else None,
            "payload": r.payload, "evidence": r.evidence, "citations": r.citations,
            "created_at": r.created_at.isoformat() if r.created_at else None}


async def _persist_rec(session, firm, skill, run, tier, item) -> Recommendation:
    rec = Recommendation(
        firm_id=firm.id, run_id=run.id, agent_key=SKILL_AGENT_KEY, tier=tier,
        status=RecommendationStatus.PROPOSED, title=item["title"], summary=item["summary"],
        rationale=item["rationale"], confidence=item["confidence"], priority=item["priority"],
        subject_type="household", subject_id=item["household_id"], subject_label=item["household"],
        payload={"skill_id": str(skill.id), "output_kind": skill.output_kind},
        evidence={"source": f"Skill: {skill.name}", "scope": skill.scope, "authored_by": "adviser"},
        citations=[])
    session.add(rec)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="recommendation",
                              agent_key=SKILL_AGENT_KEY, run_id=run.id, recommendation_id=rec.id,
                              actor="skill", content=_ledger_content(rec, tier, "skill"))
    await surveillance.review_recommendation(session, rec)  # compliance + flags + auto-pause
    await activity.emit(session, firm_id=firm.id, agent_key=SKILL_AGENT_KEY, kind=ActivityKind.PROPOSED,
                        summary=f"Skill '{skill.name}': {item['title']}", subject_label=item["household"],
                        meta={"recommendation_id": str(rec.id)})
    return rec


async def stream_skill(session, firm, skill, *, subject_type: str | None = None, subject_id=None,
                       persist: bool = True):
    """Run a skill, yielding live progress events (one per household) so the UI can show a log."""
    if not skill.enabled and persist:
        yield {"phase": "error", "message": "Skill is disabled."}
        return
    tier = AutonomyTier.TIER_2 if skill.default_tier == AutonomyTier.TIER_3 else skill.default_tier

    if subject_type == "household" and subject_id:
        ids = [str(subject_id)]
    else:
        ids = [h["id"] for h in await list_households(session, firm.id)]
    brains = []
    for hid in ids:
        b = await household_brain(session, hid)
        if b:
            brains.append(b)

    yield {"phase": "start", "skill": skill.name, "scanned": len(brains), "test": not persist}

    run = None
    if persist:
        run = AgentRun(firm_id=firm.id, agent_key=SKILL_AGENT_KEY, status=AgentRunStatus.THINKING,
                       tier=tier, trigger="skill", subject_type="firm", subject_label=skill.name,
                       started_at=utcnow(), context={"skill_id": str(skill.id), "skill_name": skill.name})
        session.add(run)
        await session.flush()
        await activity.emit(session, firm_id=firm.id, agent_key=SKILL_AGENT_KEY, kind=ActivityKind.SENSING,
                            summary=f"Skill '{skill.name}' swept {len(brains)} household(s)")

    proposals, rec_ids, recs = [], [], []
    for i, brain in enumerate(brains):
        hh = brain["household"]
        yield {"phase": "scan", "index": i + 1, "total": len(brains), "household": hh["name"]}
        res = await _ask(session, firm, skill, brain)
        applies = bool(res.get("applies"))
        item = _build_item(res, hh, skill) if applies else None
        yield {"phase": "result", "index": i + 1, "total": len(brains), "household": hh["name"],
               "applies": applies, "title": (item["title"] if item else None)}
        if item:
            proposals.append(item)
            if persist:
                rec = await _persist_rec(session, firm, skill, run, tier, item)
                rec_ids.append(str(rec.id))
                recs.append(_rec_dict(rec))

    if persist:
        run.status = AgentRunStatus.AWAITING_APPROVAL if rec_ids else AgentRunStatus.COMPLETED
        run.finished_at = None if rec_ids else utcnow()
        await session.commit()  # durable before the client is told it's done

    yield {"phase": "done", "scanned": len(brains), "surfaced": len(proposals), "run_id": str(run.id) if run else None,
           "recommendation_ids": rec_ids, "proposals": proposals, "recommendations": recs}


async def run_skill(session, firm, skill, *, subject_type: str | None = None, subject_id=None,
                    persist: bool = True) -> dict:
    """Blocking wrapper over stream_skill — returns the final result (used by the non-stream endpoints)."""
    out = {"run_id": None, "scanned": 0, "surfaced": 0, "recommendation_ids": [], "proposals": [], "recommendations": []}
    async for ev in stream_skill(session, firm, skill, subject_type=subject_type, subject_id=subject_id, persist=persist):
        if ev["phase"] == "done":
            out = {k: ev.get(k, out.get(k)) for k in out}
        elif ev["phase"] == "error":
            raise ValueError(ev["message"])
    return out
