"""A confirmation lapses when its time is up, not when someone next clicks it (B-51).

A paused change waits 24 hours for its person (`gateway`, `expires_at`). It used to expire only when someone pressed
Confirm or Not now, so until then the card kept live buttons and Needs you kept counting it, and the click was what
turned it into "Expired". Every read that shows a waiting confirmation - the thread, the conversation list, Needs you -
and every new message on a thread first marks those past their time as expired, so the card reads Expired the moment
it is shown, the model is told it was not done (`gateway._not_done`), and nothing is carried out from it.

`expires_at` is naive UTC, as the gateway writes it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.thread import PendingAction, PendingActionStatus


async def lapse(session: AsyncSession, *, thread_ids: list[uuid.UUID] | None = None,
                user_id: uuid.UUID | None = None, now: datetime | None = None) -> int:
    """Mark the waiting confirmations past their time as expired: those on these threads, or put to this person.
    Returns how many lapsed. One of the two scopes is required, so a read never touches another person's cards."""
    if not thread_ids and user_id is None:
        raise ValueError("lapse needs thread_ids or a user_id")
    stmt = (update(PendingAction)
            .where(PendingAction.status == PendingActionStatus.AWAITING_CONFIRMATION,
                   PendingAction.expires_at.is_not(None),
                   PendingAction.expires_at < (now or datetime.utcnow()))
            .values(status=PendingActionStatus.EXPIRED)
            .execution_options(synchronize_session="fetch"))  # a row already loaded in this session reads expired too
    if thread_ids:
        stmt = stmt.where(PendingAction.thread_id.in_(list(thread_ids)))
    if user_id is not None:
        stmt = stmt.where(PendingAction.requires_user_id == user_id)
    result = await session.execute(stmt)
    await session.flush()
    return int(result.rowcount or 0)
