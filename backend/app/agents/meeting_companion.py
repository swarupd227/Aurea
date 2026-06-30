"""Meeting companion & note agent (spec Table 9) — deep. Tier 1/2 — adviser approves.

Turns a meeting transcript into structured notes, action items and proposed goals — 'the
transcript becomes the control plane for downstream work'. Extraction is deterministic
(auditable); the LLM adds a narrative summary. On approval, action items become Tasks and
proposed objectives become Goals, and the meeting is marked completed."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.agents._common import firm_voice, narrate
from app.llm.service import firm_llm_creds, llm_service
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.core.db import utcnow
from app.models.engagement import Meeting, Task
from app.models.enums import AgentKey, AutonomyTier, MeetingStatus, TaskStatus
from app.models.graph import Goal

ACTION_CUES = ["send", "review", "model", "free up", "set up", "follow up", "prepare",
               "update", "rebalance", "arrange", "schedule", "draft", "look at"]
GOAL_WORDS = {"deposit": "property", "house": "property", "gift": "legacy", "education": "education",
              "retire": "retirement", "retirement": "retirement", "school": "education"}
NEG_SENTIMENT = ["nervous", "worried", "anxious", "concerned", "scared", "uneasy"]
POS_SENTIMENT = ["happy", "confident", "pleased", "excited", "reassured"]


def _parse_amount(text: str) -> float | None:
    m = re.search(r"\$?\s?([\d,]+(?:\.\d+)?)\s?([kKmM])?", text)
    if not m:
        return None
    try:
        val = float(m.group(1).replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group(2) or "").lower()
    if suffix == "k":
        val *= 1_000
    elif suffix == "m":
        val *= 1_000_000
    return val if val >= 100 else None


class MeetingCompanionAgent(BaseAgent):
    key = AgentKey.MEETING_COMPANION
    name = "Meeting Companion & Note"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1

    async def sense(self, ctx: AgentContext) -> dict:
        meeting = await ctx.session.get(Meeting, ctx.subject.id) if ctx.subject.type == "meeting" else None
        transcript = (meeting.transcript if meeting else None) or (ctx.config or {}).get("transcript")
        if not transcript:
            transcript = (
                "Adviser: How are you feeling about the portfolio?\n"
                "Client: A bit nervous after the dip, and we want to help our daughter with a "
                "house deposit next year — about $150k.\n"
                "Adviser: Understood. I'll review your cash buffer and model a tax-efficient drawdown, "
                "and set up the gifting goal in your plan."
            )
        return {"applicable": bool(meeting or ctx.subject.id),
                "meeting_id": str(meeting.id) if meeting else None,
                "household_id": str(meeting.household_id) if meeting else None,
                "transcript": transcript}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        transcript = sensed["transcript"]
        lines = [ln.strip() for ln in transcript.splitlines() if ln.strip()]

        action_items, proposed_goals, summary = [], [], []
        sentiment = "neutral"
        for ln in lines:
            low = ln.lower()
            speaker, _, said = ln.partition(":")
            said = said.strip() or ln
            if any(w in low for w in NEG_SENTIMENT):
                sentiment = "cautious"
            elif any(w in low for w in POS_SENTIMENT) and sentiment == "neutral":
                sentiment = "positive"
            # Action items from the adviser's commitments.
            if "adviser" in speaker.lower():
                for clause in re.split(r"[,;.]| and ", said):
                    cl = clause.strip()
                    if any(cue in cl.lower() for cue in ACTION_CUES) and len(cl) > 8:
                        action_items.append(cl[0].upper() + cl[1:])
            else:
                if said and "?" not in said:
                    summary.append(said)
            # Proposed goals from amounts + context.
            amount = _parse_amount(said)
            if amount:
                kind = next((v for k, v in GOAL_WORDS.items() if k in low), None)
                if kind:
                    proposed_goals.append({
                        "name": f"{kind.title()} goal", "kind": kind,
                        "target_amount": amount,
                        "target_date": (date.today() + timedelta(days=365)).isoformat(),
                    })

        notes = {"summary": summary[:5], "sentiment": sentiment,
                 "action_items": action_items, "proposed_goals": proposed_goals,
                 "extracted_by": "rules"}

        # Prefer a real LLM structured extraction when a provider is configured;
        # the deterministic rules above remain the auditable fallback.
        llm_notes = await self._llm_extract(ctx, transcript)
        if llm_notes:
            notes = {**notes, **llm_notes, "extracted_by": "llm"}

        if sensed.get("meeting_id"):
            meeting = await ctx.session.get(Meeting, sensed["meeting_id"])
            if meeting:
                meeting.notes = notes
                await ctx.session.flush()

        fallback = (
            f"Meeting notes captured ({sentiment} sentiment). {len(action_items)} follow-up task(s) and "
            f"{len(proposed_goals)} proposed goal(s) drafted for approval: "
            + "; ".join(action_items) + "."
        )
        prompt = (
            "Write a 3-bullet summary of this client meeting for the adviser to approve. Note the "
            f"client's sentiment ({sentiment}). Keep it factual.\n\nTranscript:\n{transcript}"
        )
        rationale = await narrate(ctx, task="narrative", system=firm_voice(ctx), prompt=prompt, fallback=fallback)

        return [RecommendationDraft(
            title="Meeting notes & follow-ups",
            summary=f"{len(action_items)} task(s), {len(proposed_goals)} proposed goal(s); sentiment {sentiment}.",
            rationale=rationale, confidence=0.8, priority=2,
            subject=Subject("household", sensed.get("household_id"), None),
            payload={**notes, "meeting_id": sensed.get("meeting_id")},
            evidence={"source": "meeting_transcript", "sentiment": sentiment},
        )]

    async def _llm_extract(self, ctx, transcript: str) -> dict | None:
        """LLM structured extraction → notes/tasks/goals. Returns None if no LLM or parse fails."""
        creds = firm_llm_creds(ctx.firm)
        if not llm_service.enabled(creds):
            return None
        system = (firm_voice(ctx) + " You extract structured data from a meeting transcript. "
                  "Respond with ONLY a JSON object, no prose, no code fences.")
        prompt = (
            "From the transcript below, return a JSON object with keys:\n"
            '  "summary": array of up to 5 short factual bullet strings,\n'
            '  "sentiment": one of "positive", "neutral", "cautious",\n'
            '  "action_items": array of short imperative follow-up strings (adviser commitments),\n'
            '  "proposed_goals": array of objects {"name","kind","target_amount" (number), "target_date" (YYYY-MM-DD or null)}.\n'
            "Use kind from: retirement, education, property, legacy, other.\n\n"
            f"Transcript:\n{transcript}"
        )
        res = await llm_service.generate(task="advice", system=system, prompt=prompt,
                                         firm_model_config=(ctx.firm.model_config_json or {}),
                                         creds=creds, max_tokens=700, fallback=lambda: "")
        if res.is_fallback or not res.text.strip():
            return None
        try:
            m = re.search(r"\{.*\}", res.text, re.S)
            data = json.loads(m.group(0) if m else res.text)
            out = {
                "summary": [str(x) for x in (data.get("summary") or [])][:5],
                "sentiment": data.get("sentiment") if data.get("sentiment") in ("positive", "neutral", "cautious") else "neutral",
                "action_items": [str(x) for x in (data.get("action_items") or [])],
                "proposed_goals": [
                    {"name": str(g.get("name", "Goal")), "kind": str(g.get("kind", "other")),
                     "target_amount": float(g.get("target_amount") or 0),
                     "target_date": g.get("target_date")}
                    for g in (data.get("proposed_goals") or []) if isinstance(g, dict)
                ],
            }
            return out if (out["action_items"] or out["proposed_goals"] or out["summary"]) else None
        except Exception:
            return None

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        """On approval, create Tasks and Goals, and complete the meeting."""
        s = ctx.session
        payload = recommendation.modified_payload or recommendation.payload or {}
        household_id = recommendation.subject_id
        created_tasks = created_goals = 0

        task_ids, goal_ids = [], []
        for item in payload.get("action_items", []):
            t = Task(firm_id=ctx.firm.id, household_id=household_id,
                     meeting_id=payload.get("meeting_id"), title=item, source="meeting_companion",
                     status=TaskStatus.OPEN, due_date=date.today() + timedelta(days=7),
                     subject_label=recommendation.subject_label)
            s.add(t)
            await s.flush()
            task_ids.append(str(t.id))
            created_tasks += 1

        for g in payload.get("proposed_goals", []):
            td = g.get("target_date")
            goal = Goal(firm_id=ctx.firm.id, household_id=household_id, name=g["name"], kind=g["kind"],
                        target_amount=g["target_amount"],
                        target_date=date.fromisoformat(td) if td else None,
                        assumptions={"years": 1, "funding_share": 0.1})
            s.add(goal)
            await s.flush()
            goal_ids.append(str(goal.id))
            created_goals += 1

        if payload.get("meeting_id"):
            meeting = await s.get(Meeting, payload["meeting_id"])
            if meeting:
                meeting.status = MeetingStatus.COMPLETED
        await s.flush()
        return {"executed": True, "tasks_created": created_tasks, "goals_created": created_goals,
                "task_ids": task_ids, "goal_ids": goal_ids,
                "note": f"{created_tasks} task(s) and {created_goals} goal(s) added to the plan."}

    async def rollback(self, ctx: AgentContext, recommendation) -> dict:
        """Delete created tasks/goals and reopen the meeting."""
        s = ctx.session
        result = (recommendation.payload or {}).get("execution_result", {})
        removed = 0
        for tid in result.get("task_ids", []):
            obj = await s.get(Task, tid)
            if obj:
                await s.delete(obj)
                removed += 1
        for gid in result.get("goal_ids", []):
            obj = await s.get(Goal, gid)
            if obj:
                await s.delete(obj)
                removed += 1
        mid = (recommendation.payload or {}).get("meeting_id")
        if mid:
            meeting = await s.get(Meeting, mid)
            if meeting:
                meeting.status = MeetingStatus.PREPPED if meeting.brief else MeetingStatus.SCHEDULED
        await s.flush()
        return {"reversed": True, "removed": removed,
                "note": f"Removed {removed} task(s)/goal(s) and reopened the meeting."}
