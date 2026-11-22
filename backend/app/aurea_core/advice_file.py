"""The advice file (B-175): what an adviser recommended to a client, and what became of it.

An agent's proposal has its ledger; an adviser's own recommendation had nowhere to live. Here it is a record a person
makes: what was recommended, the facts it rested on and the version of the firm's assumption set it was recorded
against, the alternatives considered and why each was rejected, the client's decision with its evidence, who owns
it, when it is due and when it is to be revisited. "Recommended umbrella cover; client declined" is a row.

Added and never edited. The recommendation stays as it was made; the client deciding, changing their mind, or the
recommendation being carried out is a new outcome that supersedes the last, and the history stays. Only an adviser or
a paraplanner records either, checked here and not only at the route; other staff read; a client reads none of it,
because advice reaches a client through their adviser, so nothing here is a client tool.

The implementation rate is implemented over made, by type and overall, for the recommendations made in a period and
where each stands today. Declined and open are counted beside it, never folded in, and nothing is estimated: what is
not on file here is not counted, which does not mean no advice was given.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.models.advice_file import ADVICE_CATEGORIES, ADVICE_STATUSES, EVIDENCE_KINDS, AdviceOutcome, AdviceRecommendation
from app.models.graph import Household, Person
from app.models.identity import User
from app.models.tenant import Firm
from app.provenance import ledger

# Those who advise record. Compliance and a branch leader supervise and read; the portfolio team reads.
RECORDERS = ("adviser", "paraplanner")
READERS = ("adviser", "paraplanner", "portfolio_team", "compliance", "branch_leader")
# Who may own a recommendation: whoever is to carry it out.
OWNER_ROLES = ("adviser", "paraplanner", "portfolio_team", "operations")
CATEGORY_TEXT = {"protection": "Protection", "investment": "Investment", "retirement": "Retirement", "estate": "Estate",
                 "tax": "Tax", "cash_debt": "Cash and debt", "other": "Other"}
STATUS_TEXT = {"open": "Open", "accepted": "Accepted", "declined": "Declined", "implemented": "Implemented"}
# From where a recommendation stands, what it may move to. Implemented is the end: a further step is a new
# recommendation. A declined recommendation carried out after all is accepted first, so the change of mind has its evidence.
MOVES = {"open": ("accepted", "declined", "implemented"), "accepted": ("declined", "implemented"),
         "declined": ("accepted",), "implemented": ()}
DEFAULT_PERIOD_DAYS = 365
SHOWN = 50
NOT_ON_FILE = "Only what is recorded here is counted; a recommendation not on file is not one never made."
REFUSED_ROLE = "The advice file is recorded by an adviser or paraplanner."


class AdviceError(ValueError):
    """A recommendation or outcome that cannot be recorded, with the reason."""


def _may_record(role: str | None) -> None:
    if role is None or str(role) not in RECORDERS:
        raise AdviceError(REFUSED_ROLE)


def _text(value: Any, label: str, least: int = 3, most: int = 4000) -> str:
    text = str(value or "").strip()
    if len(text) < least:
        raise AdviceError(f"Give {label}.")
    return text[:most]


def _day(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise AdviceError(f"{label} must be a date, YYYY-MM-DD.")


def _uuid(value: Any, label: str) -> uuid.UUID | None:
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise AdviceError(f"{label} must be an id from a read tool.")


def _alternatives(raw: Any) -> list[dict]:
    """Each alternative and why it was rejected, in the adviser's words. At least one: doing nothing is an alternative."""
    if not isinstance(raw, list) or not raw:
        raise AdviceError("Name at least one alternative considered and why it was rejected (doing nothing counts).")
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise AdviceError("Give each alternative as an option and why it was rejected.")
        option = str(item.get("option") or item.get("alternative") or "").strip()
        why = str(item.get("why_rejected") or item.get("why") or item.get("reason") or "").strip()
        if not option or len(why) < 3:
            raise AdviceError("Each alternative needs the option and why it was rejected.")
        out.append({"option": option[:500], "why_rejected": why[:1000]})
    return out


def _evidence(status: str, kind: Any, evidence: Any) -> tuple[str | None, str | None]:
    """A decision or an implementation needs its evidence: a document, or a note of what was said. Open needs none."""
    text = str(evidence or "").strip()
    kind = str(kind or "").strip().lower() or None
    if status == "open":
        if text and kind not in EVIDENCE_KINDS:
            kind = "note"
        return (kind, text[:2000]) if text else (None, None)
    if kind not in EVIDENCE_KINDS:
        raise AdviceError("A client's decision or an implementation is recorded from its evidence: say whether it is a document or a note.")
    if len(text) < 5:
        raise AdviceError(f"Name the {kind} that shows the recommendation was {status}.")
    return kind, text[:2000]


async def _household(session: AsyncSession, household_id: uuid.UUID | None, firm_id: uuid.UUID) -> Household:
    household = await session.get(Household, household_id) if household_id else None
    if household is None or household.firm_id != firm_id:
        raise AdviceError("That household is not in this firm.")
    return household


async def _owner(session: AsyncSession, firm_id: uuid.UUID, ref: Any, household_id: uuid.UUID, by_user: uuid.UUID | None) -> User | None:
    """The named owner, else the household's recorded adviser, else whoever is recording it."""
    from app.aurea_core import advisers

    ref = str(ref or "").strip()
    if not ref:
        return await advisers.adviser_of(session, firm_id, household_id) or (await session.get(User, by_user) if by_user else None)
    people = (await session.execute(select(User).where(User.firm_id == firm_id, User.is_active.is_(True)))).scalars().all()
    people = [u for u in people if str(u.role) in OWNER_ROLES]
    try:
        wanted = uuid.UUID(ref)
        hit = [u for u in people if u.id == wanted]
    except ValueError:
        low = ref.lower()
        hit = [u for u in people if u.full_name.lower() == low] or [u for u in people if low in u.full_name.lower()]
    if len(hit) != 1:
        raise AdviceError(f"No one at this firm who could own it matches {ref!r}." if not hit else
                          f"{ref!r} matches {', '.join(u.full_name for u in hit)}. Name one.")
    return hit[0]


async def assumption_version_of(session: AsyncSession, firm_id: uuid.UUID) -> str:
    """The fingerprint of the firm's assumption set as it stands (planning.assumption_version)."""
    from app.aurea_core import planning

    firm = await session.get(Firm, firm_id)
    return planning.assumption_version((firm.settings or {}) if firm else None)


async def prepare_recommendation(
    session: AsyncSession, firm_id: uuid.UUID, *, role: str | None, household_id: Any, category: Any, recommendation: Any,
    facts: Any, alternatives: Any, decision: Any = None, evidence_kind: Any = None, evidence: Any = None, decided_on: Any = None,
    owner: Any = None, due_on: Any = None, revisit_on: Any = None, person_id: Any = None, recommended_on: Any = None,
    data_as_of: Any = None, by_user: uuid.UUID | None = None,
) -> dict:
    """Everything recording would refuse, refused without writing, and what would be recorded."""
    _may_record(role)
    household = await _household(session, _uuid(household_id, "household_id"), firm_id)
    if household.left_on is not None:
        raise AdviceError(f"{household.name} has left the firm; a new recommendation is not recorded for them.")
    person = None
    pid = _uuid(person_id, "person_id")
    if pid:
        person = await session.get(Person, pid)
        if person is None or person.firm_id != firm_id or person.household_id != household.id:
            raise AdviceError("That person is not in this household.")
    kind = str(category or "").strip().lower().replace(" ", "_").replace("/", "_")
    if kind not in ADVICE_CATEGORIES:
        raise AdviceError(f"The type is one of: {', '.join(ADVICE_CATEGORIES)}.")
    today = await firm_time.today_of(session, firm_id)
    made_on = _day(recommended_on, "recommended_on") or today
    if made_on > today:
        raise AdviceError("A recommendation is recorded once it is made, not before.")
    as_of = _day(data_as_of, "data_as_of") or made_on
    if as_of > made_on:
        raise AdviceError("The facts it rested on are as of the day it was made or earlier.")
    due = _day(due_on, "due_on")
    revisit = _day(revisit_on, "revisit_on")
    if due and due < made_on:
        raise AdviceError("The due date is on or after the day it was made.")
    if revisit and revisit <= made_on:
        raise AdviceError("The revisit date is after the day it was made.")
    status = str(decision or "open").strip().lower()
    if status not in ("open", "accepted", "declined"):
        raise AdviceError("The client's decision is open, accepted or declined; implementation is recorded after.")
    ev_kind, ev_text = _evidence(status, evidence_kind, evidence)
    decided = _day(decided_on, "decided_on") or (today if status != "open" else made_on)
    if status == "open":
        decided = made_on
    elif decided < made_on or decided > today:
        raise AdviceError("The client decided on or after the day it was made, and not after today.")
    return {
        "household": household, "person": person, "category": kind,
        "recommendation": _text(recommendation, "what was recommended", 10),
        "facts": _text(facts, "the facts it rested on", 10), "data_as_of": as_of,
        "alternatives": _alternatives(alternatives), "recommended_on": made_on, "due_on": due, "revisit_on": revisit,
        "status": status, "decided_on": decided, "evidence_kind": ev_kind, "evidence": ev_text,
        "owner": await _owner(session, firm_id, owner, household.id, by_user),
        "assumption_version": await assumption_version_of(session, firm_id), "today": today,
    }


async def record_recommendation(session: AsyncSession, firm_id: uuid.UUID, *, recorded_by: str, role: str | None,
                                by_user: uuid.UUID | None = None, **terms) -> dict:
    """Add a recommendation and where it stood when made. Edits nothing."""
    ready = await prepare_recommendation(session, firm_id, role=role, by_user=by_user, **terms)
    row = AdviceRecommendation(
        firm_id=firm_id, household_id=ready["household"].id, person_id=ready["person"].id if ready["person"] else None,
        category=ready["category"], recommendation=ready["recommendation"], facts=ready["facts"],
        data_as_of=ready["data_as_of"], assumption_version=ready["assumption_version"], alternatives=ready["alternatives"],
        recommended_on=ready["recommended_on"], owner_id=ready["owner"].id if ready["owner"] else None,
        due_on=ready["due_on"], revisit_on=ready["revisit_on"], recorded_by=recorded_by, recorded_by_role=str(role))
    session.add(row)
    await session.flush()
    session.add(AdviceOutcome(firm_id=firm_id, recommendation_id=row.id, seq=1, status=ready["status"],
                              happened_on=ready["decided_on"], evidence_kind=ready["evidence_kind"], evidence=ready["evidence"],
                              recorded_by=recorded_by, recorded_by_role=str(role)))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="advice_recommended", actor=recorded_by, content={
        "recommendation_id": str(row.id), "household_id": str(row.household_id), "category": row.category,
        "status": ready["status"], "assumption_version": row.assumption_version})
    return await one(session, row.id, firm_id=firm_id)


async def _current(session: AsyncSession, recommendation_id: uuid.UUID) -> list[AdviceOutcome]:
    return list((await session.execute(select(AdviceOutcome).where(AdviceOutcome.recommendation_id == recommendation_id)
                                       .order_by(AdviceOutcome.seq))).scalars().all())


async def prepare_outcome(session: AsyncSession, firm_id: uuid.UUID, *, role: str | None, recommendation_id: Any, status: Any,
                          evidence_kind: Any = None, evidence: Any = None, happened_on: Any = None, revisit_on: Any = None) -> dict:
    """What recording the client's decision or the implementation would refuse, refused without writing."""
    _may_record(role)
    rid = _uuid(recommendation_id, "recommendation_id")
    rec = await session.get(AdviceRecommendation, rid) if rid else None
    if rec is None or rec.firm_id != firm_id:
        raise AdviceError("That recommendation is not on this firm's advice file.")
    history = await _current(session, rec.id)
    now = history[-1]
    new = str(status or "").strip().lower()
    if new not in ADVICE_STATUSES or new == "open":
        raise AdviceError("Record the client's decision (accepted or declined) or that it was implemented.")
    if new == now.status:
        raise AdviceError(f"It is already {new}.")
    if new not in MOVES[now.status]:
        raise AdviceError("It has been implemented; anything further is a new recommendation." if now.status == "implemented" else
                          "A declined recommendation carried out after all is recorded as accepted first, with the evidence of the change of mind.")
    ev_kind, ev_text = _evidence(new, evidence_kind, evidence)
    today = await firm_time.today_of(session, firm_id)
    on = _day(happened_on, "happened_on") or today
    if on > today:
        raise AdviceError("Record what has happened, not what is planned.")
    if on < now.happened_on:
        raise AdviceError(f"It was {now.status} on {now.happened_on.isoformat()}; this follows it.")
    revisit = _day(revisit_on, "revisit_on")
    if revisit and revisit <= on:
        raise AdviceError("The revisit date is after this.")
    household = await session.get(Household, rec.household_id)
    return {"recommendation": rec, "household": household, "was": now, "status": new, "happened_on": on,
            "evidence_kind": ev_kind, "evidence": ev_text, "revisit_on": revisit, "seq": now.seq + 1}


async def record_outcome(session: AsyncSession, firm_id: uuid.UUID, *, recorded_by: str, role: str | None, **terms) -> dict:
    """Add where a recommendation now stands. It supersedes the last outcome and edits nothing."""
    ready = await prepare_outcome(session, firm_id, role=role, **terms)
    rec = ready["recommendation"]
    session.add(AdviceOutcome(firm_id=firm_id, recommendation_id=rec.id, seq=ready["seq"], status=ready["status"],
                              happened_on=ready["happened_on"], evidence_kind=ready["evidence_kind"], evidence=ready["evidence"],
                              revisit_on=ready["revisit_on"], recorded_by=recorded_by, recorded_by_role=str(role)))
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="advice_outcome_recorded", actor=recorded_by, content={
        "recommendation_id": str(rec.id), "household_id": str(rec.household_id), "status": ready["status"],
        "supersedes": ready["was"].status})
    return await one(session, rec.id, firm_id=firm_id)


# ── Reading ─────────────────────────────────────────────────────────────────

def _view(rec: AdviceRecommendation, history: list[AdviceOutcome], names: dict, today: date) -> dict:
    now = history[-1]
    revisit = next((o.revisit_on for o in reversed(history) if o.revisit_on), None) or rec.revisit_on
    return {
        "id": str(rec.id), "household_id": str(rec.household_id), "household": names.get(rec.household_id),
        "person_id": str(rec.person_id) if rec.person_id else None, "person": names.get(rec.person_id) if rec.person_id else None,
        "category": rec.category, "recommendation": rec.recommendation, "facts": rec.facts,
        "data_as_of": rec.data_as_of.isoformat(), "assumption_version": rec.assumption_version,
        "alternatives": list(rec.alternatives or []), "recommended_on": rec.recommended_on.isoformat(),
        "owner_id": str(rec.owner_id) if rec.owner_id else None, "owner": names.get(rec.owner_id) if rec.owner_id else None,
        "due_on": rec.due_on.isoformat() if rec.due_on else None, "revisit_on": revisit.isoformat() if revisit else None,
        "status": now.status, "status_on": now.happened_on.isoformat(), "evidence_kind": now.evidence_kind, "evidence": now.evidence,
        "overdue": now.status in ("open", "accepted") and rec.due_on is not None and rec.due_on < today,
        "revisit_due": now.status in ("open", "declined") and revisit is not None and revisit <= today,
        "recorded_by": rec.recorded_by, "recorded_by_role": rec.recorded_by_role,
        "history": [{"seq": o.seq, "status": o.status, "happened_on": o.happened_on.isoformat(), "evidence_kind": o.evidence_kind,
                     "evidence": o.evidence, "revisit_on": o.revisit_on.isoformat() if o.revisit_on else None,
                     "recorded_by": o.recorded_by, "recorded_by_role": o.recorded_by_role} for o in history],
    }


def _counts(rows: list[dict]) -> dict:
    made = len(rows)
    by = {s: sum(1 for r in rows if r["status"] == s) for s in ADVICE_STATUSES}
    return {"made": made, **by, "rate": round(by["implemented"] / made, 4) if made else None}


def implementation_rate(rows: list[dict], *, since: date | None = None, until: date | None = None) -> dict:
    """Implemented over made, by type and overall, for what was made in [since, until], where each stands today.
    Accepted, declined and open are counted beside it. No recommendation made means no rate, never zero."""
    made = [r for r in rows if (since is None or r["recommended_on"] >= since.isoformat())
            and (until is None or r["recommended_on"] <= until.isoformat())]
    return {
        "since": since.isoformat() if since else None, "until": until.isoformat() if until else None,
        "basis": "recommendations made in the period, by where each stands today",
        "overall": _counts(made),
        "by_category": [{"category": c, **_counts([r for r in made if r["category"] == c])}
                        for c in ADVICE_CATEGORIES if any(r["category"] == c for r in made)],
    }


async def _load(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID | None = None,
                recommendation_id: uuid.UUID | None = None) -> list[dict]:
    q = select(AdviceRecommendation).where(AdviceRecommendation.firm_id == firm_id)
    if household_id:
        q = q.where(AdviceRecommendation.household_id == household_id)
    if recommendation_id:
        q = q.where(AdviceRecommendation.id == recommendation_id)
    recs = (await session.execute(q.order_by(AdviceRecommendation.recommended_on.desc(), AdviceRecommendation.created_at.desc()))).scalars().all()
    if not recs:
        return []
    ids = [r.id for r in recs]
    outcomes: dict[uuid.UUID, list[AdviceOutcome]] = {}
    for o in (await session.execute(select(AdviceOutcome).where(AdviceOutcome.firm_id == firm_id, AdviceOutcome.recommendation_id.in_(ids))
                                    .order_by(AdviceOutcome.seq))).scalars().all():
        outcomes.setdefault(o.recommendation_id, []).append(o)
    names: dict = {}
    for h in (await session.execute(select(Household).where(Household.id.in_({r.household_id for r in recs})))).scalars().all():
        names[h.id] = h.name
    pids = {r.person_id for r in recs if r.person_id}
    if pids:
        names.update({p.id: p.full_name for p in (await session.execute(select(Person).where(Person.id.in_(pids)))).scalars().all()})
    uids = {r.owner_id for r in recs if r.owner_id}
    if uids:
        names.update({u.id: u.full_name for u in (await session.execute(select(User).where(User.id.in_(uids)))).scalars().all()})
    today = await firm_time.today_of(session, firm_id)
    return [_view(r, outcomes[r.id], names, today) for r in recs if outcomes.get(r.id)]


async def one(session: AsyncSession, recommendation_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    rows = await _load(session, firm_id, recommendation_id=recommendation_id)
    if not rows:
        raise AdviceError("That recommendation is not on this firm's advice file.")
    return rows[0]


def _rank(r: dict) -> tuple:
    return (not r["overdue"], not r["revisit_due"], ("open", "accepted", "declined", "implemented").index(r["status"]))


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """One household's advice file, every recommendation with its history, and its implementation rate over all time."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    rows = await _load(session, firm_id, household_id=household_id)
    return {"household_id": str(household.id), "household": household.name, "as_of": (await firm_time.today_of(session, firm_id)).isoformat(),
            "categories": list(ADVICE_CATEGORIES), "recommendations": rows, "implementation": implementation_rate(rows),
            "overdue": sum(1 for r in rows if r["overdue"]), "revisit_due": sum(1 for r in rows if r["revisit_due"]),
            "assumption_version": await assumption_version_of(session, firm_id), "not_on_file": NOT_ON_FILE}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID, *, since: Any = None, until: Any = None, category: Any = None) -> dict:
    """The firm's advice file: the implementation rate over a period (the last year unless given), each household's
    counts, and the recommendations that need someone first."""
    today = await firm_time.today_of(session, firm_id)
    end = _day(until, "until") or today
    start = _day(since, "since") or end - timedelta(days=DEFAULT_PERIOD_DAYS)
    if start > end:
        raise AdviceError("since is on or before until.")
    kind = str(category or "").strip().lower() or None
    if kind and kind not in ADVICE_CATEGORIES:
        raise AdviceError(f"The type is one of: {', '.join(ADVICE_CATEGORIES)}.")
    rows = [r for r in await _load(session, firm_id) if kind is None or r["category"] == kind]
    period = [r for r in rows if start.isoformat() <= r["recommended_on"] <= end.isoformat()]
    homes: dict[str, dict] = {}
    for r in period:
        h = homes.setdefault(r["household_id"], {"household_id": r["household_id"], "household": r["household"], "made": 0,
                                                  **{s: 0 for s in ADVICE_STATUSES}, "overdue": 0, "revisit_due": 0})
        h["made"] += 1
        h[r["status"]] += 1
        h["overdue"] += r["overdue"]
        h["revisit_due"] += r["revisit_due"]
    # Overdue and revisit-due are listed whenever they were made: a year-old open recommendation is still owed.
    in_period = {r["id"] for r in period}
    # Rows come newest first, and the sort is stable, so within each rank the newest leads.
    shown = sorted((r for r in rows if r["id"] in in_period or r["overdue"] or r["revisit_due"]), key=_rank)
    return {"household_id": None, "as_of": today.isoformat(), "category": kind, "categories": list(ADVICE_CATEGORIES),
            "implementation": implementation_rate(rows, since=start, until=end),
            "summary": {"made": len(period), "overdue": sum(1 for r in rows if r["overdue"]),
                        "revisit_due": sum(1 for r in rows if r["revisit_due"]), "households": len(homes)},
            "households": sorted(homes.values(), key=lambda h: (-h["overdue"], -h["open"], h["household"] or "")),
            "recommendations": shown[:SHOWN], "total": len(shown),
            "assumption_version": await assumption_version_of(session, firm_id), "not_on_file": NOT_ON_FILE}
