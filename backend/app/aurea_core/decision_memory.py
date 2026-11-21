"""Agents that learn from the decisions people take (B-204), in the open and never by widening what they may do.

Two things carry what people decided into an agent's next run:

  - **The last decision.** Every new proposal carries the last time a person decided the same finding (same agent,
    subject and title stem, `atlas/proposals.identity`): what they decided, who, when, and the reason they gave. The
    next person deciding sees it on the card; the model reading the proposal reads it as data.
  - **A preference a person made.** From a proposal they decided, someone who may decide that agent's proposals can
    ask it to remember: a *hold* keeps it from raising its finding for that subject until a date (a year at most), a
    *note* goes on its proposals for the subject. Each is in the person's own words, on the audit log, listed and
    ended like any record. A run that a hold kept back says so in its context; nothing is silently dropped.

What this never does: widen an agent's tier or autonomy, hold back a supervision, IPS, reconciliation, billing, book
integration or screening finding (those may carry a note but are never held), or change a fact. A preference is a
person's decision, so no model writes one.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_preference import AgentPreference
from app.models.enums import AgentKey, RecommendationStatus
from app.models.governance import Recommendation
from app.models.identity import AuditEvent, User

KINDS = ("hold", "note")
MAX_HOLD_DAYS = 365
TEXT_MIN, TEXT_MAX = 3, 300
# A finding these agents raise is a control: it may carry a note, never be held back by one.
NEVER_HELD = frozenset({
    AgentKey.CONDUCT_SURVEILLANCE.value, AgentKey.IPS_MONITORING.value, AgentKey.RECONCILIATION.value,
    AgentKey.BILLING.value, AgentKey.BOOK_INTEGRATION.value, AgentKey.ONBOARDING_KYC_AML.value,
    AgentKey.ADVERSE_MEDIA_PEP.value, AgentKey.EDD_SOW_NARRATOR.value, AgentKey.CIP_IDENTITY_VERIFIER.value,
})
DECIDED = [s.value for s in RecommendationStatus if s.value not in ("proposed", "expired")]


class PreferenceError(Exception):
    """Refused, with a reason fit to show the person."""


def _key(agent_key) -> str:
    return str(agent_key.value if hasattr(agent_key, "value") else agent_key)


def _out(p: AgentPreference) -> dict:
    return {"id": str(p.id), "agent_key": p.agent_key, "subject_type": p.subject_type, "subject_id": str(p.subject_id),
            "subject": p.subject_label, "kind": p.kind, "text": p.text, "until": p.until.isoformat() if p.until else None,
            "made_by": p.made_by_name, "made_on": p.created_at.date().isoformat() if p.created_at else None,
            "from_recommendation_id": str(p.from_recommendation_id) if p.from_recommendation_id else None,
            "ended": bool(p.ended_at)}


async def active(session: AsyncSession, firm, *, agent_key=None, subject_id=None, today: date | None = None) -> list[AgentPreference]:
    from app.aurea_core import firm_time

    today = today or firm_time.today(firm)
    q = select(AgentPreference).where(AgentPreference.firm_id == firm.id, AgentPreference.ended_at.is_(None))
    if agent_key is not None:
        q = q.where(AgentPreference.agent_key == _key(agent_key))
    if subject_id is not None:
        q = q.where(AgentPreference.subject_id == uuid.UUID(str(subject_id)))
    rows = (await session.execute(q.order_by(AgentPreference.created_at.desc()))).scalars().all()
    return [p for p in rows if p.until is None or p.until >= today]


async def last_decision(session: AsyncSession, firm_id, agent_key, subject_type, subject_id, subject_label, title) -> dict | None:
    """The most recent decision a person took on the same finding, with its reason."""
    from app.atlas import proposals

    if not subject_id:
        return None
    want = proposals.identity(agent_key, subject_type, subject_id, subject_label, title)
    rows = (await session.execute(select(Recommendation).where(
        Recommendation.firm_id == firm_id, Recommendation.agent_key == _key(agent_key),
        Recommendation.subject_id == subject_id, Recommendation.status.in_(DECIDED),
        Recommendation.decided_at.is_not(None)).order_by(Recommendation.decided_at.desc()).limit(20))).scalars().all()
    for r in rows:
        if proposals.rec_identity(r) == want:
            who = await session.get(User, r.decided_by) if r.decided_by else None
            return {"decision": str(r.status), "by": who.full_name if who else None, "on": r.decided_at.date().isoformat(),
                    "reason": r.decision_note, "recommendation_id": str(r.id)}
    return None


async def apply(session: AsyncSession, firm, agent_key, drafts: list, subject) -> tuple[list, list[dict]]:
    """Before a run persists its drafts: drop those a hold keeps back (returned, so the run can say so), and put the
    last decision and any notes on the rest. Agents whose findings are controls are never held."""
    key = _key(agent_key)
    prefs = await active(session, firm, agent_key=key)
    kept, held = [], []
    for d in drafts:
        stype, sid = d.subject.type or subject.type, d.subject.id or subject.id
        mine = [p for p in prefs if sid and p.subject_id == sid]
        hold = next((p for p in mine if p.kind == "hold"), None)
        if hold is not None and key not in NEVER_HELD:
            held.append({"title": d.title, "subject": d.subject.label or subject.label, "subject_type": stype,
                         "subject_id": str(sid), "preference_id": str(hold.id),
                         "until": hold.until.isoformat() if hold.until else None, "by": hold.made_by_name, "text": hold.text})
            continue
        memory = {}
        last = await last_decision(session, firm.id, key, stype, sid, d.subject.label or subject.label, d.title)
        if last:
            memory["last_decision"] = last
        notes = [{"text": p.text, "by": p.made_by_name, "until": p.until.isoformat() if p.until else None}
                 for p in mine if p.kind == "note" or (p.kind == "hold" and key in NEVER_HELD)]
        if notes:
            memory["notes"] = notes
        if memory:
            d.payload = {**(d.payload or {}), "memory": memory}
        kept.append(d)
    return kept, held


async def prepare(session: AsyncSession, firm, actor: User, *, recommendation_id, kind, text, until=None) -> dict:
    """Check a preference before its Confirm card: from a proposal the person may decide, in their words, a hold with
    an end date within a year. Raises what remembering would."""
    from app.aurea_core import firm_time
    from app.core import decision_rights

    try:
        rid = uuid.UUID(str(recommendation_id))
    except (TypeError, ValueError):
        raise PreferenceError("Give the proposal it comes from (recommendation_id).")
    rec = await session.get(Recommendation, rid)
    if rec is None or rec.firm_id != firm.id:
        raise PreferenceError("That proposal is not this firm's.")
    if not rec.subject_id:
        raise PreferenceError("That proposal has no single subject to remember it for.")
    key = _key(rec.agent_key)
    if key != "skill" and actor.role not in decision_rights.roles_for(key, "dismiss"):
        raise PreferenceError("Only someone who may decide this agent's proposals may ask it to remember something.")
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise PreferenceError("kind is hold (do not raise it again until a date) or note (carry this on its proposals).")
    text = (text or "").strip()
    if not TEXT_MIN <= len(text) <= TEXT_MAX:
        raise PreferenceError(f"Say it in your own words, {TEXT_MIN} to {TEXT_MAX} characters.")
    today = firm_time.today(firm)
    until_d = None
    if until:
        try:
            until_d = date.fromisoformat(str(until)[:10])
        except ValueError:
            raise PreferenceError("until is a date, such as 2027-03-31.")
        if until_d <= today:
            raise PreferenceError("until is a date after today.")
    if kind == "hold":
        if key in NEVER_HELD:
            raise PreferenceError("This agent's findings are a control and are not held back; record a note instead.")
        if until_d is None:
            raise PreferenceError("A hold needs the date it ends.")
        if until_d > today + timedelta(days=MAX_HOLD_DAYS):
            raise PreferenceError("A hold lasts a year at most; a standing fact held longer stops being looked at.")
    same = [p for p in await active(session, firm, agent_key=key, subject_id=rec.subject_id) if p.kind == kind and p.text == text]
    if same:
        raise PreferenceError("That is already remembered.")
    return {"rec": rec, "agent_key": key, "kind": kind, "text": text, "until": until_d}


async def remember(session: AsyncSession, firm, actor: User, **terms) -> dict:
    p = await prepare(session, firm, actor, **terms)
    rec = p["rec"]
    row = AgentPreference(firm_id=firm.id, agent_key=p["agent_key"], subject_type=rec.subject_type or "household",
                          subject_id=rec.subject_id, subject_label=rec.subject_label, kind=p["kind"], text=p["text"],
                          until=p["until"], made_by=actor.id, made_by_name=actor.full_name, from_recommendation_id=rec.id)
    session.add(row)
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="agent_preference.made",
                           subject=f"{p['agent_key']} · {rec.subject_label}",
                           detail={"kind": p["kind"], "text": p["text"], "until": p["until"].isoformat() if p["until"] else None,
                                   "from_recommendation_id": str(rec.id)}))
    await session.flush()
    await session.refresh(row)
    return _out(row)


async def end(session: AsyncSession, firm, actor: User, preference_id) -> dict:
    from app.core import decision_rights
    from app.core.db import utcnow

    try:
        pid = uuid.UUID(str(preference_id))
    except (TypeError, ValueError):
        raise PreferenceError("Give the preference's id.")
    row = await session.get(AgentPreference, pid)
    if row is None or row.firm_id != firm.id:
        raise PreferenceError("That preference is not this firm's.")
    if row.ended_at is not None:
        raise PreferenceError("That preference has already ended.")
    if row.made_by != actor.id and actor.role not in decision_rights.roles_for(row.agent_key, "dismiss"):
        raise PreferenceError("Only its maker or someone who may decide this agent's proposals may end it.")
    row.ended_at, row.ended_by = utcnow(), actor.full_name
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="agent_preference.ended",
                           subject=f"{row.agent_key} · {row.subject_label}", detail={"preference_id": str(row.id)}))
    await session.flush()
    return _out(row)


async def listing(session: AsyncSession, firm, *, agent_key=None, subject_id=None) -> list[dict]:
    return [_out(p) for p in await active(session, firm, agent_key=agent_key, subject_id=subject_id)]
