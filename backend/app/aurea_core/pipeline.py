"""From lead to invested: the pipeline and onboarding read as one funnel (B-159).

The CRM (`models/crm.py`) holds people who are not clients yet; onboarding (`onboarding_start.py`,
`account_states.py`) holds a client on the way in. They meet in one place: an opportunity is won when its onboarding
case opens, and only then (`link_case`), so a deal and its case cannot disagree. The case carries the deal to a
household at activation (`on_activation`), and a reversed activation takes it back (`on_reversal`).

Everything here is what a person recorded: a stage someone moved, an estimate someone gave, a probability someone
said (none said is none, never a default), a call or meeting someone logged. A deal is quiet when nothing has been
logged against it for longer than its stage's working target (`QUIET_DAYS`); that is a prompt to a person, and moves
nothing. An adviser reads the deals of contacts they own; every other role reads the firm's.
"""
from __future__ import annotations

import statistics
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.crm import OPEN_STAGES, CrmActivity, CrmContact, CrmOpportunity
from app.models.enums import UserRole
from app.models.identity import AuditEvent, User
from app.models.tenant import Firm

STAGE_LABEL = {"lead": "Lead", "qualified": "Qualified", "proposal": "Proposal", "won": "Won", "lost": "Lost"}
# Days without a logged call, email, meeting or note before an open deal is quiet: the firm's working target for each
# stage, the same for every firm until one is set. A proposal waits least, because it is the client's move.
QUIET_DAYS = {"lead": 21, "qualified": 14, "proposal": 10}
NEXT_STEP = {
    "lead": "Call to qualify: what they hold, what they want and when",
    "qualified": "Book the discovery meeting or send the proposal",
    "proposal": "Follow up on the proposal: a decision, their questions, or close it as lost with the reason",
}
YEAR = 365


class PipelineError(Exception):
    """A pipeline change that cannot be made, in words a person can act on."""


def _aware(t: datetime | None) -> datetime | None:
    return t if t is None or t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def _days(a: datetime | None, b: datetime | None) -> float | None:
    a, b = _aware(a), _aware(b)
    return round((b - a).total_seconds() / 86400, 1) if a and b else None


def scope_of(user: User | None) -> str:
    """An adviser reads their own contacts' deals; everyone else, the firm's."""
    return "mine" if user is not None and UserRole(user.role) == UserRole.ADVISER else "firm"


async def _contacts(session: AsyncSession, firm_id, owner_id=None) -> dict:
    q = select(CrmContact).where(CrmContact.firm_id == firm_id)
    if owner_id is not None:
        q = q.where(CrmContact.owner_user_id == owner_id)
    return {c.id: c for c in (await session.execute(q)).scalars().all()}


async def last_touch(session: AsyncSession, firm_id, opps: list) -> dict:
    """When each deal was last worked: the latest activity logged on it or on its contact, up to now, else when it
    opened. A meeting booked for later is not a touch until it has happened."""
    if not opps:
        return {}
    now = utcnow()
    rows = (await session.execute(
        select(CrmActivity.contact_id, CrmActivity.opportunity_id, func.max(CrmActivity.occurred_at))
        .where(CrmActivity.firm_id == firm_id, CrmActivity.contact_id.in_({o.contact_id for o in opps}),
               CrmActivity.occurred_at <= now)
        .group_by(CrmActivity.contact_id, CrmActivity.opportunity_id))).all()
    by_contact: dict = {}
    by_opp: dict = {}
    for contact_id, opp_id, at in rows:
        at = _aware(at)
        if opp_id is None:
            by_contact[contact_id] = max(filter(None, (by_contact.get(contact_id), at)))
        else:
            by_opp[opp_id] = max(filter(None, (by_opp.get(opp_id), at)))
    out = {}
    for o in opps:
        # An activity logged against the contact counts for each of their deals; one logged against another deal does not.
        out[o.id] = max(filter(None, (by_opp.get(o.id), by_contact.get(o.contact_id), _aware(o.opened_at)))) \
            if any((by_opp.get(o.id), by_contact.get(o.contact_id), o.opened_at)) else None
    return out


def quiet(opp, touched: datetime | None, now: datetime) -> dict | None:
    """How long an open deal has gone without a logged touch, if past its stage's target."""
    if opp.stage not in QUIET_DAYS or touched is None:
        return None
    days = _days(touched, now)
    limit = QUIET_DAYS[opp.stage]
    return {"days": days, "limit": limit, "over": round(days - limit, 1)} if days is not None and days > limit else None


async def overview(session: AsyncSession, firm: Firm, user: User | None, *, today=None) -> dict:
    """The funnel as recorded: open deals by stage, what was won and where its onboarding stands, what was lost and
    why, conversion over the last year, and the deals gone quiet."""
    from app.api.onboarding import states_overview

    scope = scope_of(user)
    contacts = await _contacts(session, firm.id, user.id if scope == "mine" else None)
    opps = (await session.execute(select(CrmOpportunity).where(
        CrmOpportunity.firm_id == firm.id, CrmOpportunity.contact_id.in_(list(contacts) or [uuid.uuid4()]))
        .order_by(CrmOpportunity.opened_at.desc()))).scalars().all()
    owners = {u.id: u.full_name for u in (await session.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    touched = await last_touch(session, firm.id, list(opps))
    now = utcnow()

    case_ids = {o.onboarding_case_id for o in opps if o.onboarding_case_id}
    board = {r["case_id"]: r for r in (await states_overview(session, firm))["accounts"]} if case_ids else {}

    def row(o) -> dict:
        c = contacts.get(o.contact_id)
        r = {"id": str(o.id), "title": o.title, "stage": o.stage, "contact_id": str(o.contact_id),
             "contact": c.full_name if c else None, "source": c.source if c else None,
             "owner": owners.get(c.owner_user_id) if c and c.owner_user_id else None,
             "estimated_aum": float(o.estimated_aum) if o.estimated_aum is not None else None,
             "probability_pct": o.probability_pct,
             "opened_at": _aware(o.opened_at).isoformat() if o.opened_at else None,
             "closed_at": _aware(o.closed_at).isoformat() if o.closed_at else None,
             "last_touch": touched[o.id].isoformat() if touched.get(o.id) else None,
             "days_since_touch": _days(touched.get(o.id), now)}
        if o.stage in OPEN_STAGES:
            r["quiet"] = quiet(o, touched.get(o.id), now)
            r["next_step"] = NEXT_STEP[o.stage]
        if o.stage == "lost":
            r["lost_reason"] = o.lost_reason
        if o.stage == "won":
            acct = board.get(str(o.onboarding_case_id)) if o.onboarding_case_id else None
            r["onboarding"] = ({"case_id": str(o.onboarding_case_id), "state": acct["state"], "label": state_label(acct["state"]),
                                "step": acct["step"], "steps": acct["steps"], "owner": acct["owner"], "overdue": acct["overdue"],
                                "since": acct.get("since")}
                               if acct else ({"case_id": str(o.onboarding_case_id), "state": "rejected"} if o.onboarding_case_id else None))
            r["client"] = bool(o.converted_household_id)
        return r

    rows = [row(o) for o in opps]
    open_rows = [r for r in rows if r["stage"] in OPEN_STAGES]
    stages = []
    for s in OPEN_STAGES:
        here = [r for r in open_rows if r["stage"] == s]
        stages.append({"stage": s, "label": STAGE_LABEL[s], "count": len(here),
                       "estimated_aum": round(sum(r["estimated_aum"] or 0 for r in here), 2),
                       "no_estimate": sum(1 for r in here if r["estimated_aum"] is None),
                       "quiet": sum(1 for r in here if r.get("quiet"))})
    won = [r for r in rows if r["stage"] == "won"]
    onboarding = [r for r in won if r.get("onboarding") and r["onboarding"]["state"] not in ("invested", "rejected")]
    invested = [r for r in won if r.get("onboarding") and r["onboarding"]["state"] == "invested"]
    won_no_case = [r for r in won if not r.get("onboarding")]
    lost = [r for r in rows if r["stage"] == "lost"]

    since = now - timedelta(days=YEAR)
    closed = [o for o in opps if o.stage in ("won", "lost") and o.closed_at and _aware(o.closed_at) >= since]
    won_year = [o for o in closed if o.stage == "won"]
    to_won = [d for d in (_days(o.opened_at, o.closed_at) for o in won_year) if d is not None]
    to_invested = []
    for o in won_year:
        acct = board.get(str(o.onboarding_case_id)) if o.onboarding_case_id else None
        if acct and acct["state"] == "invested" and acct.get("since"):
            d = _days(o.closed_at, datetime.fromisoformat(acct["since"]))
            if d is not None and d >= 0:
                to_invested.append(d)
    weighted = [r for r in open_rows if r["probability_pct"] is not None and r["estimated_aum"] is not None]
    reasons: dict[str, int] = {}
    for r in lost:
        reasons[r.get("lost_reason") or "No reason recorded"] = reasons.get(r.get("lost_reason") or "No reason recorded", 0) + 1

    return {
        "scope": scope, "scope_label": "Your contacts" if scope == "mine" else "The firm",
        "contacts": len(contacts),
        "stages": stages,
        "open": {"count": len(open_rows), "estimated_aum": round(sum(r["estimated_aum"] or 0 for r in open_rows), 2),
                 "weighted_aum": round(sum(r["estimated_aum"] * r["probability_pct"] / 100 for r in weighted), 2),
                 "weighted_from": len(weighted), "no_probability": sum(1 for r in open_rows if r["probability_pct"] is None),
                 "quiet": sum(1 for r in open_rows if r.get("quiet"))},
        "won": {"count": len(won), "in_onboarding": len(onboarding), "invested": len(invested), "no_case": len(won_no_case),
                "by_state": _count(r["onboarding"]["state"] for r in onboarding)},
        "lost": {"count": len(lost), "reasons": reasons},
        "last_year": {"won": len(won_year), "lost": len(closed) - len(won_year),
                      "win_rate_pct": round(100 * len(won_year) / len(closed), 1) if closed else None,
                      "median_days_to_won": round(statistics.median(to_won), 1) if to_won else None,
                      "median_days_won_to_invested": round(statistics.median(to_invested), 1) if to_invested else None,
                      "invested_measured": len(to_invested)},
        "quiet": sorted([r for r in open_rows if r.get("quiet")], key=lambda r: -r["quiet"]["over"]),
        "opportunities": rows,
        "quiet_days": QUIET_DAYS,
    }


def state_label(state: str) -> str:
    from app.agents.abandonment_recovery import LABELS
    return {**LABELS, "invested": "Invested", "rejected": "Case rejected"}.get(state, state)


def _count(items) -> dict:
    out: dict = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


# ── Won: the onboarding case opens ─────────────────────────────────────────────────────────────

async def open_deal(session: AsyncSession, firm: Firm, opportunity_id) -> tuple[CrmOpportunity, CrmContact]:
    """The deal a case would come from: this firm's, still open, with no case of its own."""
    try:
        oid = uuid.UUID(str(opportunity_id))
    except ValueError:
        raise PipelineError(f"No opportunity {opportunity_id!r}; read_crm_pipeline lists them.")
    opp = (await session.execute(select(CrmOpportunity).where(
        CrmOpportunity.id == oid, CrmOpportunity.firm_id == firm.id))).scalar_one_or_none()
    if opp is None:
        raise PipelineError("That opportunity is not this firm's; read_crm_pipeline lists them.")
    if opp.onboarding_case_id:
        raise PipelineError(f"\"{opp.title}\" already opened its onboarding case; read_onboarding_cases finds it.")
    if opp.stage not in OPEN_STAGES:
        raise PipelineError(f"\"{opp.title}\" is {STAGE_LABEL.get(opp.stage, opp.stage).lower()}, not open.")
    contact = await session.get(CrmContact, opp.contact_id)
    return opp, contact


async def link_case(session: AsyncSession, firm: Firm, opp: CrmOpportunity, contact: CrmContact, case, *, actor: User) -> None:
    """The deal is won because its case opened: recorded on the deal, its activity and the audit log."""
    was = opp.stage
    opp.stage, opp.closed_at, opp.onboarding_case_id = "won", utcnow(), case.id
    session.add(CrmActivity(firm_id=firm.id, contact_id=contact.id, opportunity_id=opp.id, activity_type="note",
                            occurred_at=utcnow(), logged_by_user_id=actor.id,
                            detail=f"{STAGE_LABEL[was]} to Won: onboarding case opened for {case.prospect_name}."))
    session.add(AuditEvent(firm_id=firm.id, actor_id=actor.id, actor_email=actor.email, event_type="crm.opportunity_won",
                           subject=contact.full_name, detail={"opportunity_id": str(opp.id), "case_id": str(case.id), "from": was}))
    await session.flush()


async def on_activation(session: AsyncSession, case, household_id) -> int:
    """The case became a household: its deal records the client it made, and its contact becomes a client contact."""
    opps = (await session.execute(select(CrmOpportunity).where(CrmOpportunity.onboarding_case_id == case.id))).scalars().all()
    for o in opps:
        o.converted_household_id = household_id
        contact = await session.get(CrmContact, o.contact_id)
        if contact is not None:
            contact.household_id, contact.contact_type = household_id, "client_contact"
    await session.flush()
    return len(opps)


async def on_reversal(session: AsyncSession, case) -> None:
    """An activation reversed: the deal is won and onboarding again, with no client yet."""
    for o in (await session.execute(select(CrmOpportunity).where(CrmOpportunity.onboarding_case_id == case.id))).scalars().all():
        o.converted_household_id = None
        contact = await session.get(CrmContact, o.contact_id)
        if contact is not None and contact.contact_type == "client_contact":
            contact.household_id, contact.contact_type = None, "prospect"
    await session.flush()


# ── Moving a stage ─────────────────────────────────────────────────────────────────────────────

def check_move(opp: CrmOpportunity, stage: str | None, reason: str | None) -> dict:
    """What moving a deal would record. Won comes only from its onboarding case opening; lost needs its reason."""
    stage = (stage or "").strip().lower()
    if stage == "won":
        raise PipelineError("Won is when the client's onboarding case opens: start_onboarding with this opportunity_id "
                            "opens it and marks the deal won.")
    if stage not in ("lead", "qualified", "proposal", "lost"):
        raise PipelineError("A stage is lead, qualified, proposal or lost.")
    if opp.stage == "won":
        raise PipelineError(f"\"{opp.title}\" is won and has its onboarding case; the case is where it moves now.")
    if stage == opp.stage:
        raise PipelineError(f"\"{opp.title}\" is already {STAGE_LABEL[stage].lower()}.")
    reason = " ".join((reason or "").split()) or None
    if stage == "lost" and not reason:
        raise PipelineError("Say why it was lost, in the client's or adviser's words; that is what the funnel learns from.")
    return {"from": opp.stage, "to": stage, "reason": reason}


async def move(session: AsyncSession, firm: Firm, opp: CrmOpportunity, stage: str, reason: str | None, *, actor: User) -> dict:
    m = check_move(opp, stage, reason)
    opp.stage = m["to"]
    if m["to"] == "lost":
        opp.lost_reason, opp.closed_at = m["reason"], utcnow()
    elif opp.closed_at is not None:   # reopened from lost
        opp.closed_at, opp.lost_reason = None, None
    session.add(CrmActivity(firm_id=firm.id, contact_id=opp.contact_id, opportunity_id=opp.id, activity_type="note",
                            occurred_at=utcnow(), logged_by_user_id=actor.id,
                            detail=f"{STAGE_LABEL[m['from']]} to {STAGE_LABEL[m['to']]}" + (f": {m['reason']}" if m["reason"] else ".")))
    await session.flush()
    return m


# ── Before a prospect meeting ──────────────────────────────────────────────────────────────────

async def prospect_brief(session: AsyncSession, firm: Firm, contact_id) -> dict:
    """What is on file about a prospect before meeting them: who, how they came, their deals, every logged touch, the
    next meeting booked, and what is not on file yet. It recommends nothing about their money."""
    try:
        cid = uuid.UUID(str(contact_id))
    except ValueError:
        raise PipelineError(f"No contact {contact_id!r}; read_crm_pipeline lists them.")
    c = (await session.execute(select(CrmContact).where(CrmContact.id == cid, CrmContact.firm_id == firm.id))).scalar_one_or_none()
    if c is None:
        raise PipelineError("That contact is not this firm's; read_crm_pipeline lists them.")
    opps = (await session.execute(select(CrmOpportunity).where(CrmOpportunity.contact_id == c.id)
                                  .order_by(CrmOpportunity.opened_at.desc()))).scalars().all()
    acts = (await session.execute(select(CrmActivity).where(CrmActivity.contact_id == c.id)
                                  .order_by(CrmActivity.occurred_at.desc()))).scalars().all()
    users = {u.id: u.full_name for u in (await session.execute(select(User).where(User.firm_id == firm.id))).scalars().all()}
    now = utcnow()
    past = [a for a in acts if _aware(a.occurred_at) <= now]
    booked = sorted((a for a in acts if _aware(a.occurred_at) > now and a.activity_type == "meeting"), key=lambda a: a.occurred_at)
    touched = await last_touch(session, firm.id, list(opps))
    live = [o for o in opps if o.stage in OPEN_STAGES]
    missing = [label for label, value in (("Email", c.email), ("Phone", c.phone), ("How they came to the firm", c.source))
               if not value]
    if live and all(o.estimated_aum is None for o in live):
        missing.append("Assets they would bring")
    if live and all(o.expected_close_date is None for o in live):
        missing.append("When they expect to decide")
    if not past:
        missing.append("Any logged conversation")
    return {
        "contact": {"id": str(c.id), "name": c.full_name, "type": c.contact_type, "source": c.source, "email": c.email,
                    "phone": c.phone, "notes": c.notes, "owner": users.get(c.owner_user_id),
                    "on_file_since": _aware(c.created_at).date().isoformat() if c.created_at else None,
                    "client": bool(c.household_id)},
        "opportunities": [{"id": str(o.id), "title": o.title, "stage": o.stage, "stage_label": STAGE_LABEL.get(o.stage, o.stage),
                           "estimated_aum": float(o.estimated_aum) if o.estimated_aum is not None else None,
                           "probability_pct": o.probability_pct,
                           "expected_close_date": o.expected_close_date.isoformat() if o.expected_close_date else None,
                           "days_open": _days(o.opened_at, now) if o.stage in OPEN_STAGES else None,
                           "quiet": quiet(o, touched.get(o.id), now) if o.stage in OPEN_STAGES else None,
                           "next_step": NEXT_STEP.get(o.stage), "lost_reason": o.lost_reason,
                           "onboarding_case_id": str(o.onboarding_case_id) if o.onboarding_case_id else None} for o in opps],
        "activity": [{"type": a.activity_type, "at": _aware(a.occurred_at).isoformat(), "detail": a.detail,
                      "by": users.get(a.logged_by_user_id)} for a in past[:12]],
        "activity_count": len(past),
        "booked": [{"at": _aware(a.occurred_at).isoformat(), "detail": a.detail, "by": users.get(a.logged_by_user_id)} for a in booked],
        "not_on_file": missing,
    }
