"""Client messages: what the Messages page, Canvas and the conversation all read and write.

One thread per household. A message from the firm reaches the client at once, in the sender's name,
so writing one is for the people who look after the client. Reading a thread as those people clears
its unread count; anyone else reading, a colleague previewing Canvas or the conversation's tool,
leaves it unread for them. Everything is firm-scoped: another firm's household reads as not found.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_experience import Message
from app.models.enums import MessageAuthor, UserRole
from app.models.graph import Household

WRITERS: frozenset[UserRole] = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER})
BODY_MAX = 4000


class MessageError(Exception):
    """A message that cannot be read or sent, in words a person can act on."""


async def household(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> Household:
    hh = await session.get(Household, household_id)
    if hh is None or hh.firm_id != firm_id:
        raise MessageError(f"Household {household_id} not found or access denied")
    return hh


def clean(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        raise MessageError("A message needs some words.")
    if len(text) > BODY_MAX:
        raise MessageError(f"A message is at most {BODY_MAX} characters.")
    return text


def view(m: Message) -> dict:
    return {"id": str(m.id), "author_role": m.author_role, "author_name": m.author_name,
            "body": m.body, "from_agent": m.source_recommendation_id is not None,
            "created_at": m.created_at.isoformat() if m.created_at else None}


def _waiting(m: Message) -> bool:
    return m.author_role == MessageAuthor.CLIENT and not m.read_by_adviser


async def inbox(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    """One row per household with a message, newest first. A client wrote last: it needs a reply."""
    rows = (await session.execute(
        select(Message).where(Message.firm_id == firm_id).order_by(Message.created_at.desc()))).scalars().all()
    names = {h.id: h.name for h in (await session.execute(
        select(Household).where(Household.firm_id == firm_id))).scalars().all()}
    threads: dict = {}
    for m in rows:
        t = threads.setdefault(m.household_id, {
            "household_id": str(m.household_id), "household_name": names.get(m.household_id),
            "last_body": m.body, "last_at": m.created_at.isoformat() if m.created_at else None,
            "last_from": m.author_name, "needs_reply": m.author_role == MessageAuthor.CLIENT,
            "unread": 0, "total": 0,
        })
        t["total"] += 1
        t["unread"] += 1 if _waiting(m) else 0
    return list(threads.values())


async def thread(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[Message]:
    """Oldest first, as it reads."""
    return list((await session.execute(
        select(Message).where(Message.firm_id == firm_id, Message.household_id == household_id)
        .order_by(Message.created_at.asc()))).scalars().all())


def unread(rows: list[Message]) -> int:
    return sum(1 for m in rows if _waiting(m))


def mark_read_by_adviser(rows: list[Message], role: UserRole | str) -> None:
    if UserRole(role) in WRITERS:
        for m in rows:
            if m.author_role == MessageAuthor.CLIENT:
                m.read_by_adviser = True


def from_firm(firm_id: uuid.UUID, household_id: uuid.UUID, body: str, *, author: str) -> Message:
    return Message(firm_id=firm_id, household_id=household_id, author_role=MessageAuthor.ADVISER,
                   author_name=author, body=clean(body), read_by_client=False, read_by_adviser=True)
