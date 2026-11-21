"""Briefs shaped to the RM who holds the meeting (B-203).

Every RM approaches a client their own way. A profile they keep says which sections of a brief lead, how much of it
opens at first (short, standard or full), and the strengths they lead a conversation with. `shape` turns it into the
order a brief is laid out in: the same facts, only their order and emphasis. A brief with no profile behind it is laid
out the default way and says so.

What the RM opens or closes on a brief is recorded (`record_event`), and `suggestions` offers a change back: a section
they keep opening that does not lead, or a lead section they keep closing. A suggestion changes nothing until the RM
accepts it through `set_brief_profile` and its Confirm card. Only the RM sets their own profile.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.brief_profile import BriefProfile, BriefSectionEvent
from app.models.identity import AuditEvent, User

# Each brief's sections in their default order. Keys are unique across both kinds, so a profile names them plainly.
SECTIONS: dict[str, list[tuple[str, str]]] = {
    "meeting": [("agenda", "Agenda"), ("mandates", "Mandates"), ("held_away", "Held away"), ("goals", "Goals"), ("history", "History"),
                ("review", "Review"), ("tax", "Tax"), ("open_proposals", "Open proposals"),
                ("watch_items", "Watch items"), ("life_events", "Life events"), ("house_views", "House views")],
    "prospect": [("deals", "Deals"), ("introductions", "Ways in"), ("activity", "Touches"), ("booked", "Booked"),
                 ("not_on_file", "Not on file")],
}
# Talking points from public sources (B-202, aurea_core/persona.py), present only where profiling runs.
SECTIONS["prospect"].append(("persona", "Persona"))
LABEL = {k: v for sections in SECTIONS.values() for k, v in sections}
KIND_OF = {k: kind for kind, sections in SECTIONS.items() for k, _ in sections}
# How many sections open at first; lead sections always open.
LENGTHS = {"short": 3, "standard": 5, "full": None}
DEFAULT_LENGTH = "standard"
MAX_STRENGTHS, STRENGTH_CHARS = 5, 60
# A suggestion needs a habit, not a click: opened (or closed) on this many separate days in the window.
SUGGEST_AFTER, WINDOW_DAYS = 3, 60


class BriefProfileError(Exception):
    """Refused, with a reason fit to show the person."""


def _out(row: BriefProfile | None, user: User | None) -> dict:
    return {"user": user.full_name if user else None, "set": row is not None,
            "lead": list(row.lead or []) if row else [], "lead_labels": [LABEL.get(k, k) for k in (row.lead or [])] if row else [],
            "length": row.length if row else DEFAULT_LENGTH, "strengths": list(row.strengths or []) if row else [],
            "version": row.version if row else 0,
            "updated": row.updated_at.isoformat() if row and row.updated_at else None}


async def _row(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID) -> BriefProfile | None:
    return (await session.execute(select(BriefProfile).where(
        BriefProfile.firm_id == firm_id, BriefProfile.user_id == user_id))).scalar_one_or_none()


async def get(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID) -> dict:
    user = await session.get(User, user_id)
    return _out(await _row(session, firm_id, user_id), user)


def shape(profile: dict | None, kind: str, present: list[str] | None = None) -> dict:
    """The order a brief of `kind` is laid out in for this profile, which sections lead and which open at first."""
    default = [k for k, _ in SECTIONS[kind]]
    present = [k for k in default if present is None or k in present]
    profile = profile or {}
    lead = [k for k in profile.get("lead") or [] if k in present]
    order = lead + [k for k in present if k not in lead]
    n = LENGTHS.get(profile.get("length") or DEFAULT_LENGTH)
    expanded = order if n is None else order[:max(n, len(lead))]
    return {"for": profile.get("user") if profile.get("set") else None, "set": bool(profile.get("set")),
            "order": order, "lead": lead, "expanded": expanded, "length": profile.get("length") or DEFAULT_LENGTH,
            "strengths": list(profile.get("strengths") or []), "labels": {k: LABEL[k] for k in order}}


async def for_user(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID | None, kind: str,
                   present: list[str] | None = None) -> dict:
    profile = await get(session, firm_id, user_id) if user_id else None
    return shape(profile, kind, present)


def check(lead, length, strengths) -> dict:
    lead = [str(k).strip().lower() for k in (lead or []) if str(k).strip()]
    unknown = [k for k in lead if k not in LABEL]
    if unknown:
        raise BriefProfileError(f"No section called {', '.join(unknown)}. Sections: "
                                + "; ".join(f"{kind}: {', '.join(k for k, _ in s)}" for kind, s in SECTIONS.items()) + ".")
    if len(set(lead)) != len(lead):
        raise BriefProfileError("A section leads once.")
    length = (length or DEFAULT_LENGTH).strip().lower()
    if length not in LENGTHS:
        raise BriefProfileError("length is short, standard or full.")
    strengths = [str(s).strip() for s in (strengths or []) if str(s).strip()]
    if len(strengths) > MAX_STRENGTHS:
        raise BriefProfileError(f"At most {MAX_STRENGTHS} strengths.")
    if any(len(s) > STRENGTH_CHARS for s in strengths):
        raise BriefProfileError(f"A strength is a few words, at most {STRENGTH_CHARS} characters.")
    return {"lead": lead, "length": length, "strengths": strengths}


async def prepare(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID, *, lead=None, length=None,
                  strengths=None, keep_unset: bool = True) -> dict:
    """What setting the profile would change, before its Confirm card. Anything not given stays as it is."""
    before = await get(session, firm_id, user_id)
    after = check(before["lead"] if (lead is None and keep_unset) else lead,
                  before["length"] if (length is None and keep_unset) else length,
                  before["strengths"] if (strengths is None and keep_unset) else strengths)
    if before["set"] and after == {k: before[k] for k in ("lead", "length", "strengths")}:
        raise BriefProfileError("That is the profile already.")
    return {"before": before, "after": after}


async def save(session: AsyncSession, firm_id: uuid.UUID, user: User, **terms) -> dict:
    p = await prepare(session, firm_id, user.id, **terms)
    row = await _row(session, firm_id, user.id)
    if row is None:
        row = BriefProfile(firm_id=firm_id, user_id=user.id, version=0)
        session.add(row)
    row.lead, row.length, row.strengths = p["after"]["lead"], p["after"]["length"], p["after"]["strengths"]
    row.version = (row.version or 0) + 1
    session.add(AuditEvent(firm_id=firm_id, actor_id=user.id, actor_email=user.email, event_type="brief_profile.changed",
                           subject=user.full_name, detail={"before": {k: p["before"][k] for k in ("lead", "length", "strengths")},
                                                           "after": p["after"]}))
    await session.flush()
    await session.refresh(row)      # updated_at is set by the database, and read here outside a lazy load
    return _out(row, user)


async def record_event(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID, *, kind: str, section: str,
                       action: str) -> None:
    if kind not in SECTIONS or KIND_OF.get(section) != kind or action not in ("open", "close"):
        raise BriefProfileError("Unknown brief section or action.")
    session.add(BriefSectionEvent(firm_id=firm_id, user_id=user_id, brief_kind=kind, section=section, action=action))
    await session.flush()


async def suggestions(session: AsyncSession, firm_id: uuid.UUID, user_id: uuid.UUID, profile: dict | None = None) -> list[dict]:
    """Changes worth offering back, from what the RM opened and closed over the last WINDOW_DAYS. None is applied."""
    profile = profile or await get(session, firm_id, user_id)
    since = utcnow() - timedelta(days=WINDOW_DAYS)
    events = (await session.execute(select(BriefSectionEvent).where(
        BriefSectionEvent.firm_id == firm_id, BriefSectionEvent.user_id == user_id,
        BriefSectionEvent.created_at >= since))).scalars().all()
    days: dict = {}
    for e in events:
        days.setdefault((e.section, e.action), set()).add(e.created_at.date())
    out = []
    lead = profile["lead"]
    for (section, action), ds in sorted(days.items(), key=lambda kv: -len(kv[1])):
        if len(ds) < SUGGEST_AFTER:
            continue
        if action == "open" and section not in lead:
            out.append({"section": section, "label": LABEL[section], "kind": KIND_OF[section], "move": "up",
                        "days": len(ds), "lead_after": lead + [section]})
        elif action == "close" and section in lead:
            out.append({"section": section, "label": LABEL[section], "kind": KIND_OF[section], "move": "down",
                        "days": len(ds), "lead_after": [k for k in lead if k != section]})
    return out
