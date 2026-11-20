"""Meeting preparation agent (spec Table 9) — deep. Tier 1 — adviser owns the conversation.

Assembles a structured pre-meeting brief from the client brain: portfolio snapshot, goals
('am I on track?'), watch-items (concentration, harvestable losses, off-track goals), life
events, relevant house views, and a suggested agenda. The brief is saved on the meeting."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from app.agents._common import firm_voice, narrate
from app.agents._signals import book_signals, goal_projections, life_events
from app.atlas.base import AgentContext, BaseAgent, RecommendationDraft, Subject
from app.aurea_core import firm_time, knowledge, markets
from app.aurea_core.graph import household_brain
from app.models.engagement import Meeting
from app.models.enums import AgentKey, AutonomyTier, MeetingStatus


class MeetingPrepAgent(BaseAgent):
    key = AgentKey.MEETING_PREP
    name = "Meeting Preparation"
    lifecycle_stage = "advise_engage"
    default_tier = AutonomyTier.TIER_1

    async def _resolve(self, ctx: AgentContext):
        """Subject may be a meeting or a household. Returns (meeting | None, household_id)."""
        if ctx.subject.type == "meeting" and ctx.subject.id:
            meeting = await ctx.session.get(Meeting, ctx.subject.id)
            return meeting, (meeting.household_id if meeting else None)
        return None, ctx.subject.id

    async def sense(self, ctx: AgentContext) -> dict:
        meeting, household_id = await self._resolve(ctx)
        if not household_id:
            return {"applicable": False}
        brain = await household_brain(ctx.session, household_id, firm_id=ctx.firm.id)
        if not brain:
            return {"applicable": False}
        citations = await knowledge.retrieve(ctx.session, ctx.firm.id, "house view outlook positioning", k=2)
        return {"applicable": True, "meeting_id": str(meeting.id) if meeting else None,
                "meeting_title": meeting.title if meeting else f"Review — {brain['household']['name']}",
                "household_id": str(household_id), "brain": brain, "citations": citations}

    async def think(self, ctx: AgentContext, sensed: dict) -> list[RecommendationDraft]:
        if not sensed.get("applicable"):
            return []
        brain = sensed["brain"]
        hh = brain["household"]
        totals = brain["totals"]
        goals = goal_projections(brain, firm_time.today(ctx.firm))
        signals = book_signals(brain, firm_time.today(ctx.firm))
        events = life_events(brain)
        cites = sensed["citations"]

        record = await self._record(ctx, sensed["household_id"], brain)
        if record.get("tax"):
            # The tax report counts losses lot by lot; a position netted whole would give a second, different figure.
            signals = [s for s in signals if s.get("kind") != "loss_harvest"]
        agenda = self._agenda(goals, signals, events, record)
        brief = {
            "portfolio": {"total_value": totals["total_value"], "by_asset_class": totals["by_asset_class"],
                          "data_confidence": totals["data_confidence"]},
            "goals": goals,
            "watch_items": [{"title": s["title"], "detail": s["detail"]} for s in signals],
            "life_events": events,
            "house_views": [{"title": c["title"], "excerpt": c["excerpt"][:200]} for c in cites],
            "agenda": agenda,
            **record,
        }
        # Laid out for the RM who holds the meeting: the household's adviser as recorded (B-203).
        from app.aurea_core import advisers, brief_profile

        adviser = await advisers.adviser_of(ctx.session, ctx.firm.id, uuid.UUID(str(sensed["household_id"])))
        present = [k for k, v in (("agenda", agenda), ("mandates", record.get("mandates")), ("goals", goals),
                                  ("history", record.get("history")), ("review", record.get("review")),
                                  ("tax", record.get("tax")), ("open_proposals", record.get("open_proposals")),
                                  ("watch_items", signals), ("life_events", events), ("house_views", cites)) if v]
        brief["shaped_for"] = await brief_profile.for_user(ctx.session, ctx.firm.id, adviser.id if adviser else None,
                                                           "meeting", present)
        strengths = brief["shaped_for"]["strengths"]

        # Persist the brief on the meeting.
        if sensed.get("meeting_id"):
            meeting = await ctx.session.get(Meeting, uuid.UUID(str(sensed["meeting_id"])))
            if meeting:
                meeting.brief = brief
                if meeting.status == MeetingStatus.SCHEDULED:
                    meeting.status = MeetingStatus.PREPPED
                await ctx.session.flush()

        on_track = sum(1 for g in goals if g["on_track"])
        fallback = (
            f"Pre-meeting brief for {hh['name']}: portfolio {markets.money_for(ctx.firm)(totals['total_value'])}, "
            f"{on_track}/{len(goals)} goals on track, {len(signals)} watch-item(s). Suggested agenda: "
            + "; ".join(agenda) + "."
        )
        outside = [m["mandate"] for m in record.get("mandates", []) if m["outside"]]
        history = record.get("history") or {}
        last_meeting = (history.get("meetings") or [{}])[0].get("at")
        lead = [brief_profile.LABEL[k] for k in brief["shaped_for"]["lead"]]
        prompt = (
            f"Write a 2-3 sentence opening summary for an adviser's pre-meeting brief for household "
            f"'{hh['name']}'. Use only these facts and add none. Portfolio {markets.money_for(ctx.firm)(totals['total_value'])}; "
            f"{on_track}/{len(goals)} goals on track; mandates outside their band or IPS: {outside or 'none'}; "
            f"review on record: {(record.get('review') or {}).get('status', 'unknown')}; "
            f"open proposals: {len(record.get('open_proposals', []))}; watch-items {[s['title'] for s in signals]}; "
            f"last meeting held: {last_meeting or 'none recorded'}; open items for the household: {history.get('open_count', 0)}, "
            f"{sum(1 for t in history.get('open_items', []) if t['overdue'])} overdue; "
            f"house views {[c['title'] for c in cites]}. Be warm, concise and specific."
            + (f" Order what you say to lead with {', '.join(lead)}." if lead else "")
            + (f" The adviser leads conversations with: {', '.join(strengths)}; let the summary suit that, adding no fact."
               if strengths else "")
        )
        rationale = await narrate(ctx, task="narrative", system=firm_voice(ctx), prompt=prompt, fallback=fallback)

        return [RecommendationDraft(
            title=f"Meeting brief — {sensed['meeting_title']}",
            summary=f"Portfolio {markets.money_for(ctx.firm)(totals['total_value'])} · {on_track}/{len(goals)} goals on track · "
                    f"{len(signals)} watch-item(s) · {len(agenda)} agenda topics.",
            rationale=rationale, confidence=0.85, priority=3,
            subject=Subject("household", uuid.UUID(str(sensed["household_id"])), hh["name"]),
            payload={"brief": brief, "meeting_id": sensed.get("meeting_id")},
            evidence={"data_confidence": totals["data_confidence"]}, citations=cites,
        )]

    async def _record(self, ctx: AgentContext, household_id: str, brain: dict) -> dict:
        """What is on file that a meeting should cover, each from its own module: every mandate against its IPS or
        model, the review record, the tax report's headline, and the proposals still open. A read that fails is left
        out rather than guessed."""
        from app.aurea_core import ips, reviews, tax_intelligence
        from app.models.enums import RecommendationStatus
        from app.models.governance import Recommendation

        hid = uuid.UUID(str(household_id))
        out: dict = {}
        monitored = await ips.for_household(ctx.session, hid, firm_id=ctx.firm.id)
        if monitored:
            out["mandates"] = [{
                "mandate": m["mandate"], "measured_against": "IPS" if m["ips"] else (m.get("model") or "model"), "status": m["status"],
                "max_drift": (m.get("model_drift") or {}).get("max_drift"), "band": m.get("drift_band"),
                "outside": bool((m.get("model_drift") or {}).get("outside") or m["status"] == "breach"),
                "findings": len(m.get("findings") or []), "prices": m.get("price_source"),
            } for m in monitored["mandates"]]
        review = await reviews.for_household(ctx.session, hid, firm_id=ctx.firm.id)
        if review:
            out["review"] = {"status": review["status"], "last": (review["last_review"] or {}).get("held_on"),
                             "next_due": review["next_due"], "cadence": review["cadence"],
                             "unrecorded": review["unrecorded_meeting"]}
        try:
            tax = await tax_intelligence.for_household(ctx.session, hid, ctx.firm.jurisdiction, firm_id=ctx.firm.id)
        except Exception:  # a market with no report, or a report that cannot be built, adds nothing
            tax = None
        if tax and tax.get("supported") is not False and tax.get("summary"):
            s = tax["summary"]
            out["tax"] = {"jurisdiction": tax.get("jurisdiction"), "flags": s.get("total_flags"),
                          "harvestable_loss": s.get("harvestable_loss"), "estimated_saving": s.get("estimated_tax_saving"),
                          "estimate": True}
        mandate_ids = [uuid.UUID(m["id"]) for m in brain.get("mandates", [])]
        recs = (await ctx.session.execute(select(Recommendation).where(
            Recommendation.firm_id == ctx.firm.id, Recommendation.status == RecommendationStatus.PROPOSED,
            Recommendation.agent_key != self.key.value,
            ((Recommendation.subject_type == "household") & (Recommendation.subject_id == hid))
            | ((Recommendation.subject_type == "mandate") & (Recommendation.subject_id.in_(mandate_ids)))))).scalars().all()
        out["open_proposals"] = [{"id": str(r.id), "title": r.title, "agent_key": str(r.agent_key)} for r in recs]
        out["history"] = await self._history(ctx, hid)
        return out

    async def _history(self, ctx: AgentContext, hid: uuid.UUID) -> dict:
        """What has happened with the client, each item dated and named by where it is recorded (B-199): the last
        touches logged in the CRM against the client's contacts, the messages exchanged, the meetings held and what
        they agreed, the decisions taken on proposals with their reasons, and what is still open. There is no
        complaints register here, and the brief says so rather than reading its absence as none."""
        from datetime import datetime, timezone

        from app.core.db import utcnow
        from app.models.client_experience import Message
        from app.models.crm import CrmActivity, CrmContact
        from app.models.engagement import Task
        from app.models.enums import RecommendationStatus, TaskStatus
        from app.models.governance import Recommendation
        from app.models.identity import User

        now = utcnow()
        today = firm_time.today(ctx.firm)
        aware = lambda t: t if t is None or t.tzinfo else t.replace(tzinfo=timezone.utc)
        users = {u.id: u.full_name for u in (await ctx.session.execute(
            select(User).where(User.firm_id == ctx.firm.id))).scalars().all()}

        contact_ids = [c.id for c in (await ctx.session.execute(select(CrmContact).where(
            CrmContact.firm_id == ctx.firm.id, CrmContact.household_id == hid))).scalars().all()]
        touches = []
        if contact_ids:
            for a in (await ctx.session.execute(select(CrmActivity).where(
                    CrmActivity.contact_id.in_(contact_ids)).order_by(CrmActivity.occurred_at.desc()).limit(10))).scalars().all():
                if aware(a.occurred_at) <= now:
                    touches.append({"at": aware(a.occurred_at).date().isoformat(), "type": a.activity_type,
                                    "detail": (a.detail or "")[:140], "by": users.get(a.logged_by_user_id), "source": "CRM"})
        messages = (await ctx.session.execute(select(Message).where(
            Message.firm_id == ctx.firm.id, Message.household_id == hid).order_by(Message.created_at.desc()).limit(5))).scalars().all()
        meetings = (await ctx.session.execute(select(Meeting).where(
            Meeting.firm_id == ctx.firm.id, Meeting.household_id == hid, Meeting.status == MeetingStatus.COMPLETED)
            .order_by(Meeting.scheduled_at.desc()).limit(3))).scalars().all()
        decided = (await ctx.session.execute(select(Recommendation).where(
            Recommendation.firm_id == ctx.firm.id, Recommendation.subject_type == "household", Recommendation.subject_id == hid,
            Recommendation.decided_at.is_not(None), Recommendation.agent_key != self.key.value,
            Recommendation.status.in_([s.value for s in RecommendationStatus if s.value not in ("proposed", "expired")]))
            .order_by(Recommendation.decided_at.desc()).limit(5))).scalars().all()
        open_tasks = (await ctx.session.execute(select(Task).where(
            Task.firm_id == ctx.firm.id, Task.household_id == hid, Task.status == TaskStatus.OPEN)
            .order_by(Task.due_date))).scalars().all()
        return {
            "touches": touches[:5],
            "messages": [{"at": aware(m.created_at).date().isoformat(), "from": m.author_name, "role": str(m.author_role),
                          "unread": not m.read_by_adviser and str(m.author_role) == "client", "source": "Messages"}
                         for m in messages],
            "meetings": [{"at": aware(m.scheduled_at).date().isoformat() if m.scheduled_at else None, "title": m.title,
                          "agreed": len((m.notes or {}).get("action_items") or []),
                          "decisions": len((m.notes or {}).get("decisions") or []), "source": "Meetings"} for m in meetings],
            "decisions": [{"at": aware(r.decided_at).date().isoformat(), "title": r.title, "decision": str(r.status),
                           "by": users.get(r.decided_by), "note": r.decision_note, "source": "Ledger"} for r in decided],
            "open_items": [{"title": t.title, "due": t.due_date.isoformat() if t.due_date else None,
                            "overdue": bool(t.due_date and t.due_date < today), "owner": users.get(t.assigned_to),
                            "source": "Tasks"} for t in open_tasks[:6]],
            "open_count": len(open_tasks),
            "not_kept_here": ["Complaints"],
        }

    def _agenda(self, goals, signals, events, record: dict | None = None) -> list[str]:
        record = record or {}
        agenda = ["Portfolio review & positioning vs the house view"]
        for m in record.get("mandates", []):
            if m["outside"] and m["max_drift"] is not None:
                agenda.append(f"{m['mandate']}: {m['max_drift']:.0%} from its {m['measured_against']}, outside the {m['band']:.0%} band")
            elif m["outside"]:
                agenda.append(f"{m['mandate']}: outside its IPS")
        if any(not g["on_track"] for g in goals):
            agenda.append("Goals that need attention & funding options")
        else:
            agenda.append("Progress against goals — on track")
        tax = record.get("tax")
        if tax and tax.get("flags"):
            agenda.append(f"Tax: {tax['flags']} item(s) to check")
        for s in signals[:2]:
            agenda.append(s["title"])
        review = record.get("review") or {}
        if review.get("unrecorded"):
            from datetime import date

            from app.aurea_core import firm_time
            held = firm_time.spoken(date.fromisoformat(review["unrecorded"]["held_on"]))
            agenda.append(f"Record whether the {held} meeting was a review")
        overdue = [t for t in (record.get("history") or {}).get("open_items", []) if t["overdue"]]
        if overdue:
            agenda.append(f"Overdue: {overdue[0]['title']}" + (f" and {len(overdue) - 1} more" if len(overdue) > 1 else ""))
        if events:
            agenda.append("Life events & next-gen engagement")
        return agenda

    async def act(self, ctx: AgentContext, recommendation) -> dict:
        return {"executed": True, "note": "Brief acknowledged; the adviser leads the conversation."}
