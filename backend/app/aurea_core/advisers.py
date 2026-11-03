"""Whose client a household is, and an adviser's day worked out from their own clients.

The adviser is an "adviser" relationship edge from a user, to the household or to one of its people or entities, as
the seed, the admin pages and onboarding have always written it; `of_households` is the one reading of it, and an
edge to the household itself outranks one to a member. A person changes it with a reason, on the audit log, and the
change replaces every edge the household had. Nothing here works out an adviser from activity: a household nobody
assigned stays unassigned, and says so. The day reads what is on file for the adviser's own households - proposals
their role is asked to decide, reviews and tasks falling due, money waiting, meetings ahead and whether each has a
brief - and for any other role, the firm's. It writes nothing."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, reviews, tasks
from app.aurea_core.billing import _mandate_households
from app.core.db import utcnow
from app.core.decision_rights import queue_action
from app.models.cash_movement import CashRequest
from app.models.engagement import Meeting
from app.models.enums import MeetingStatus, RecommendationStatus, UserRole
from app.models.governance import Recommendation
from app.models.graph import Account, Household, LegalEntity, Mandate, Person, RelationshipEdge
from app.models.identity import AuditEvent, User
from app.models.money_in import IncomingDeposit
from app.models.tenant import Firm

WEEK_DAYS = 7
SHOWN = 5
UNASSIGN = {"none", "unassigned", "nobody", "no one"}


class AdviserError(Exception):
    """An assignment that cannot be made, in words a person can act on."""


async def advisers(session: AsyncSession, firm_id: uuid.UUID) -> list[User]:
    return list((await session.execute(select(User).where(
        User.firm_id == firm_id, User.role == UserRole.ADVISER, User.is_active.is_(True)).order_by(User.full_name))).scalars().all())


async def _active(session: AsyncSession, firm_id: uuid.UUID) -> list[Household]:
    return list((await session.execute(select(Household).where(
        Household.firm_id == firm_id, Household.left_on.is_(None)).order_by(Household.name))).scalars().all())


async def members(session: AsyncSession, firm_id: uuid.UUID) -> tuple[dict, dict]:
    people = {p.id: p.household_id for p in (await session.execute(select(Person).where(Person.firm_id == firm_id))).scalars().all()}
    entities = {e.id: e.household_id for e in (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm_id))).scalars().all()}
    return people, entities


async def of_households(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, uuid.UUID]:
    """Each household's adviser's user id. An edge to the household outranks one to a member; among
    members, the latest stands. A household with no edge is not in the result."""
    edges = (await session.execute(select(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm_id, RelationshipEdge.kind == "adviser", RelationshipEdge.from_type == "user")
        .order_by(RelationshipEdge.created_at))).scalars().all()
    people, entities = await members(session, firm_id)
    out: dict[uuid.UUID, uuid.UUID] = {}
    for e in edges:
        home = people.get(e.to_id) if e.to_type == "person" else entities.get(e.to_id) if e.to_type == "entity" else None
        if home:
            out[home] = e.from_id
    for e in edges:
        if e.to_type == "household":
            out[e.to_id] = e.from_id
    return out


async def adviser_of(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> User | None:
    uid = (await of_households(session, firm_id)).get(household_id)
    return await session.get(User, uid) if uid else None


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Each adviser and the households they hold, and the households nobody holds."""
    people = await advisers(session, firm_id)
    names = {u.id: u.full_name for u in people}
    held = await of_households(session, firm_id)
    households = await _active(session, firm_id)
    return {
        "advisers": [{"id": str(u.id), "name": u.full_name, "households": sum(1 for h in households if held.get(h.id) == u.id)} for u in people],
        "households": [{"id": str(h.id), "name": h.name, "adviser_id": str(held[h.id]) if h.id in held else None,
                        "adviser": names.get(held.get(h.id)) if h.id in held else None} for h in households],
        "unassigned": sum(1 for h in households if h.id not in held),
    }


async def _resolve(session: AsyncSession, firm_id: uuid.UUID, ref: str | None) -> User | None:
    ref = (ref or "").strip()
    if not ref:
        raise AdviserError("Name the adviser, or say none to leave the households unassigned.")
    if ref.lower() in UNASSIGN:
        return None
    people = await advisers(session, firm_id)
    try:
        wanted = uuid.UUID(ref)
        hit = [u for u in people if u.id == wanted]
    except ValueError:
        low = ref.lower()
        hit = [u for u in people if u.full_name.lower() == low] or [u for u in people if low in u.full_name.lower()]
    if len(hit) == 1:
        return hit[0]
    if not hit:
        raise AdviserError(f"No active adviser at this firm matches {ref!r}. The firm's advisers: "
                           + (", ".join(u.full_name for u in people) or "none") + ".")
    raise AdviserError(f"{ref!r} matches {', '.join(u.full_name for u in hit)}. Name one.")


async def prepare_assign(session: AsyncSession, firm_id: uuid.UUID, *, household_ids: list[uuid.UUID], adviser: str | None,
                         reason: str | None) -> dict:
    """What an assignment would change. Writes nothing."""
    reason = (reason or "").strip()
    if not reason:
        raise AdviserError("Give the reason, for example a new client, a departing adviser or a book being shared out.")
    if not household_ids:
        raise AdviserError("Name the households to assign.")
    to = await _resolve(session, firm_id, adviser)
    rows = (await session.execute(select(Household).where(Household.firm_id == firm_id, Household.id.in_(household_ids))
                                  .order_by(Household.name))).scalars().all()
    found = {h.id for h in rows}
    missing = [str(i) for i in household_ids if i not in found]
    if missing:
        raise AdviserError(f"No household at this firm with id {', '.join(missing)}.")
    gone = [h.name for h in rows if h.left_on]
    if gone:
        raise AdviserError(f"{', '.join(gone)} left the firm and keeps the adviser it had.")
    held = await of_households(session, firm_id)
    changing = [h for h in rows if held.get(h.id) != (to.id if to else None)]
    if not changing:
        raise AdviserError("Each of these households already has that adviser.")
    names = {u.id: u.full_name for u in (await session.execute(select(User).where(User.firm_id == firm_id))).scalars().all()}
    return {"to": to, "households": changing, "from": {h.id: names.get(held[h.id], "someone no longer here") if h.id in held else None
                                                       for h in changing}, "reason": reason}


async def set_adviser(session: AsyncSession, firm_id: uuid.UUID, household: Household, to: User | None, *, actor: User, reason: str,
                      was: str | None) -> None:
    """Replace every adviser edge the household and its members have with one to the household, or none."""
    people, entities = await members(session, firm_id)
    targets = [household.id] + [pid for pid, hid in people.items() if hid == household.id] + [eid for eid, hid in entities.items() if hid == household.id]
    await session.execute(delete(RelationshipEdge).where(
        RelationshipEdge.firm_id == firm_id, RelationshipEdge.kind == "adviser", RelationshipEdge.to_id.in_(targets)))
    if to:
        session.add(RelationshipEdge(firm_id=firm_id, kind="adviser", from_type="user", from_id=to.id,
                                     to_type="household", to_id=household.id, attributes={}))
    session.add(AuditEvent(firm_id=firm_id, actor_id=actor.id, actor_email=actor.email, event_type="client.adviser_assigned",
                           subject=household.name, detail={"from": was, "to": to.full_name if to else None, "reason": reason}))


async def assign(session: AsyncSession, firm_id: uuid.UUID, *, actor: User, **terms) -> dict:
    p = await prepare_assign(session, firm_id, **terms)
    for h in p["households"]:
        await set_adviser(session, firm_id, h, p["to"], actor=actor, reason=p["reason"], was=p["from"][h.id])
    await session.flush()
    return await for_firm(session, firm_id)


# ── The day ───────────────────────────────────────────────────────────────────

def _aware(at: datetime) -> datetime:
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


async def _subject_households(session: AsyncSession, firm_id: uuid.UUID, recs: list[Recommendation]) -> dict:
    """Each proposal's household, where its subject has one. A firm-wide proposal has none."""
    def ids(kind: str) -> list[uuid.UUID]:
        return [r.subject_id for r in recs if r.subject_type == kind and r.subject_id]

    out: dict = {r.id: r.subject_id for r in recs if r.subject_type == "household"}
    if ids("mandate"):
        mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm_id, Mandate.id.in_(ids("mandate"))))).scalars().all()
        owners = await _mandate_households(session, firm_id, list(mandates))
        out.update({r.id: owners.get(r.subject_id) for r in recs if r.subject_type == "mandate"})
    for kind, model in (("person", Person), ("meeting", Meeting)):
        if ids(kind):
            homes = {row.id: row.household_id for row in (await session.execute(select(model).where(
                model.firm_id == firm_id, model.id.in_(ids(kind))))).scalars().all()}
            out.update({r.id: homes.get(r.subject_id) for r in recs if r.subject_type == kind})
    return out


async def day(session: AsyncSession, firm: Firm, user: User) -> dict:
    """The four figures at the top of an adviser's cockpit, each from what is on file."""
    today, now = firm_time.today(firm), utcnow()
    role = UserRole(user.role)
    everyone = await _active(session, firm.id)
    held = await of_households(session, firm.id)
    mine = role == UserRole.ADVISER
    book = [h for h in everyone if held.get(h.id) == user.id] if mine else everyone
    ids = {h.id for h in book}
    names = {h.id: h.name for h in everyone}

    # Waiting on you: open proposals this role is asked about, on these households.
    open_recs = list((await session.execute(select(Recommendation).where(
        Recommendation.firm_id == firm.id, Recommendation.status == RecommendationStatus.PROPOSED)
        .order_by(Recommendation.created_at))).scalars().all())
    homes = await _subject_households(session, firm.id, open_recs) if mine else {}
    asked = [r for r in open_recs if queue_action(role, r.agent_key) and (not mine or homes.get(r.id) in ids)]
    oldest = asked[0] if asked else None

    # Due this week: recorded reviews falling due or overdue, and this person's tasks.
    review_rows = [r for r in (await reviews.for_firm(session, firm.id))["households"] if uuid.UUID(r["household_id"]) in ids]
    overdue_reviews = [r for r in review_rows if r["status"] == "overdue"]
    due_reviews = [r for r in review_rows if r["days_to_due"] is not None and 0 <= r["days_to_due"] <= WEEK_DAYS]
    my_tasks = await tasks.for_firm(session, firm.id, status="open", assigned_to=user.id)
    due_tasks = [t for t in my_tasks if t.due_date is not None and t.due_date <= today + timedelta(days=WEEK_DAYS)]
    due_items = ([{"kind": "review", "what": f"Review · {r['household']}", "on": r["next_due"], "overdue": r["status"] == "overdue"}
                  for r in overdue_reviews + due_reviews]
                 + [{"kind": "task", "what": t.title, "on": t.due_date.isoformat(), "overdue": tasks.overdue(t, today)} for t in due_tasks])
    due_items.sort(key=lambda i: i["on"])

    # Money waiting: payments asked for and not yet paid, and deposits still on hold.
    mandates = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id))).scalars().all()
    owners = await _mandate_households(session, firm.id, list(mandates))
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.firm_id == firm.id))).scalars().all()
                if owners.get(a.mandate_id) in ids}
    account_ids = list(accounts)
    requests = (await session.execute(select(CashRequest).where(CashRequest.account_id.in_(account_ids),
                                                                CashRequest.status == "requested"))).scalars().all() if account_ids else []
    held = [d for d in (await session.execute(select(IncomingDeposit).where(IncomingDeposit.account_id.in_(account_ids)))).scalars().all()
            if not d.returned_on and d.hold_until and d.hold_until > today] if account_ids else []
    money_items = ([{"kind": "payment", "what": f"{names.get(owners.get(accounts[r.account_id].mandate_id))} · {r.kind}", "amount": float(r.amount)}
                    for r in requests]
                   + [{"kind": "deposit_hold", "what": f"{names.get(owners.get(accounts[d.account_id].mandate_id))} · held to {d.hold_until.isoformat()}",
                       "amount": float(d.amount)} for d in held])

    # Meetings in the next seven days, and whether each has a brief.
    meetings = [m for m in (await session.execute(select(Meeting).where(
        Meeting.firm_id == firm.id, Meeting.scheduled_at >= now, Meeting.scheduled_at < now + timedelta(days=WEEK_DAYS),
        Meeting.status != MeetingStatus.COMPLETED).order_by(Meeting.scheduled_at))).scalars().all() if m.household_id in ids]

    return {
        "as_of": today.isoformat(), "currency": firm.base_currency,
        "scope": "yours" if mine else "firm",
        "households": len(book), "unassigned": sum(1 for h in everyone if h.id not in held),
        "waiting": {"count": len(asked), "oldest_days": (now - _aware(oldest.created_at)).days if oldest and oldest.created_at else None,
                    "items": [{"id": str(r.id), "title": r.title, "agent_key": str(r.agent_key)} for r in asked[:SHOWN]]},
        "due": {"count": len(due_items), "overdue": sum(1 for i in due_items if i["overdue"]),
                "reviews": len(overdue_reviews) + len(due_reviews), "tasks": len(due_tasks), "items": due_items[:SHOWN]},
        "money": {"count": len(money_items), "payments": len(requests), "deposits_held": len(held),
                  "value": round(sum(i["amount"] for i in money_items), 2), "items": money_items[:SHOWN]},
        "meetings": {"count": len(meetings), "briefed": sum(1 for m in meetings if m.brief),
                     "items": [{"id": str(m.id), "title": m.title, "household": names.get(m.household_id),
                                "on": firm_time.today(firm, m.scheduled_at).isoformat(), "briefed": bool(m.brief)} for m in meetings[:SHOWN]]},
    }
