"""Phase 2: Thread and gateway API endpoints for conversation-first interface."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import get_current_user
from app.llm.gateway import Gateway, ConfirmationRequiredError, RoleForbiddenError, GatewayError
from app.models.enums import UserRole
from app.models.identity import User
from app.models.thread import Thread, ThreadKind

router = APIRouter(prefix="/api/threads", tags=["threads"])


@router.post("/{thread_id}/send")
async def send_message(
    thread_id: uuid.UUID,
    message: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Send a message to a thread and process the orchestrator response.

    The gateway validates role access, executes read-only tools immediately,
    and pauses state-changing tools waiting for confirmation.

    Returns streaming events (via SSE or WebSocket in Phase 3).
    """

    text = message.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="message.text is required")

    # Verify thread access
    thread = await session.get(Thread, thread_id)
    if not thread or thread.firm_id != user.firm_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    gateway = Gateway(session, user.id, user.role, user.firm_id)

    try:
        # Orchestrate the message and collect responses
        responses = []
        async for event in gateway.send_message(thread_id, text):
            responses.append(event)

        return {
            "thread_id": str(thread_id),
            "events": responses,
        }

    except ConfirmationRequiredError as e:
        # Return pending action requiring user confirmation
        return {
            "thread_id": str(thread_id),
            "type": "pending_action",
            "pending_action_id": str(e.pending_action_id),
            "tool_key": e.tool_key,
            "confirmation_text": str(e),
        }

    except RoleForbiddenError as e:
        raise HTTPException(status_code=403, detail=str(e))

    except GatewayError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/pending-actions/{pending_action_id}/confirm")
async def confirm_pending_action(
    pending_action_id: uuid.UUID,
    confirm: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """
    Confirm or reject a pending state-changing action.

    If confirmed, executes the tool immediately. If rejected, marks as rejected.
    """

    confirmed = confirm.get("confirmed", False)

    gateway = Gateway(session, user.id, user.role, user.firm_id)

    try:
        result = await gateway.confirm_pending_action(pending_action_id, confirmed)
        return {
            "pending_action_id": str(pending_action_id),
            **result,
        }

    except GatewayError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("")
async def create_thread(
    thread_data: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new thread."""

    kind = thread_data.get("kind")  # "ask_astra", "household", etc.
    subject_id = thread_data.get("subject_id")
    title = thread_data.get("title")

    if not kind:
        raise HTTPException(status_code=400, detail="kind is required")

    try:
        kind_enum = ThreadKind(kind)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid kind: {kind}")

    thread = Thread(
        firm_id=user.firm_id,
        kind=kind_enum,
        subject_id=subject_id,
        created_by_user_id=user.id,
        title=title,
    )
    session.add(thread)
    await session.commit()

    return {
        "id": str(thread.id),
        "kind": thread.kind.value,
        "subject_id": str(thread.subject_id) if thread.subject_id else None,
        "title": thread.title,
        "created_at": thread.created_at.isoformat(),
    }


@router.get("")
async def list_threads(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List all threads for the current user's firm."""

    stmt = select(Thread).where(Thread.firm_id == user.firm_id).order_by(Thread.updated_at.desc())
    result = await session.execute(stmt)
    threads = result.scalars().all()

    return {
        "threads": [
            {
                "id": str(t.id),
                "kind": t.kind.value,
                "subject_id": str(t.subject_id) if t.subject_id else None,
                "title": t.title,
                "created_at": t.created_at.isoformat(),
                "updated_at": t.updated_at.isoformat(),
            }
            for t in threads
        ]
    }


@router.get("/{thread_id}")
async def get_thread(
    thread_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Fetch a thread with its messages."""

    stmt = select(Thread).where(Thread.id == thread_id).options(selectinload(Thread.messages))
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if not thread or thread.firm_id != user.firm_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    return {
        "id": str(thread.id),
        "kind": thread.kind.value,
        "subject_id": str(thread.subject_id) if thread.subject_id else None,
        "title": thread.title,
        "created_at": thread.created_at.isoformat(),
        "messages": [
            {
                "id": str(m.id),
                "role": m.role.value,
                "text": m.text,
                "card": {
                    "kind": m.card_kind,
                    "data": m.card_data,
                } if m.card_kind else None,
                "created_at": m.created_at.isoformat(),
            }
            for m in thread.messages
        ],
    }
