"""Thread and gateway API endpoints for the conversation-first workspace.

Response shapes are designed for the workspace UI's rail (Needs You, Conversations,
Your Agents) and thread view (proof-of-work sources, pending-action confirm cards,
starter briefing). Every count here is a real query — nothing is invented; a stat
we don't have data for is left out rather than faked.
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import get_current_user
from app.core.tools import tools_for_role
from app.llm.gateway import Gateway, GatewayError
from app.models.identity import User
from app.models.thread import (
    Thread, ThreadKind, ThreadMessage, ToolCall, ToolCallStatus,
    PendingAction, PendingActionStatus,
)

router = APIRouter(prefix="/api/threads", tags=["threads"])


def _serialize_message(m: ThreadMessage, pending_by_tool_call: dict[uuid.UUID, PendingAction]) -> dict:
    done_calls = [tc for tc in m.tool_calls if tc.status == ToolCallStatus.DONE]
    sources = sorted({tc.tool_key for tc in done_calls})
    # One artifact per completed tool call, in call order — the frontend picks a
    # renderer by tool_key and falls back to nothing (the prose already covers it)
    # for a tool_key it doesn't have a card for.
    artifacts = [{"tool_key": tc.tool_key, "result": tc.result} for tc in done_calls]
    pending = next((pending_by_tool_call[tc.id] for tc in m.tool_calls if tc.id in pending_by_tool_call), None)

    return {
        "id": str(m.id),
        "role": m.role.value,
        "speaking_agent": m.speaking_agent,
        "text": m.text,
        "sources": sources,
        "artifacts": artifacts,
        "pending_action": {
            "id": str(pending.id),
            "tool_key": next(tc.tool_key for tc in m.tool_calls if tc.id == pending.tool_call_id),
            "confirmation_text": pending.confirmation_text,
            "decision": (
                "confirmed" if pending.status == PendingActionStatus.CONFIRMED
                else "declined" if pending.status == PendingActionStatus.REJECTED
                else None
            ),
        } if pending else None,
        "suggestions": m.suggestions or [],
        "created_at": m.created_at.isoformat(),
    }


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@router.post("/{thread_id}/send")
async def send_message(
    thread_id: uuid.UUID,
    message: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """
    Send a message to a thread and stream Astra's turn back as Server-Sent Events.

    The gateway validates role access, streams prose as it's generated, executes
    read-only tools immediately, and pauses state-changing tools for confirmation.
    Each line is `data: <json>\\n\\n`; event shapes are documented on
    Gateway.send_message. The frontend re-fetches the thread once it sees "done"
    or "pending_action" rather than trying to reconstruct persisted state from the
    stream itself.
    """

    text = message.get("text")
    if not text:
        raise HTTPException(status_code=400, detail="message.text is required")

    # Checked before the stream starts, so a bad thread id still gets a normal 404
    # rather than a 200 that immediately emits an error event.
    thread = await session.get(Thread, thread_id)
    if not thread or thread.firm_id != user.firm_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    gateway = Gateway(session, user.id, user.role, user.firm_id)

    async def event_stream():
        try:
            async for event in gateway.send_message(thread_id, text):
                yield _sse(event)
        except GatewayError as e:
            yield _sse({"type": "error", "message": str(e)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/pending-actions/{pending_action_id}/confirm")
async def confirm_pending_action(
    pending_action_id: uuid.UUID,
    confirm: dict,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Confirm or reject a pending state-changing action."""

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

    kind = thread_data.get("kind")
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
        "status": "idle",
        "created_at": thread.created_at.isoformat(),
    }


@router.get("/needs-you")
async def needs_you(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Pending actions assigned to this user, awaiting their decision."""

    stmt = (
        select(PendingAction, Thread, ToolCall)
        .join(Thread, PendingAction.thread_id == Thread.id)
        .join(ToolCall, PendingAction.tool_call_id == ToolCall.id)
        .where(
            PendingAction.requires_user_id == user.id,
            PendingAction.status == PendingActionStatus.AWAITING_CONFIRMATION,
            Thread.firm_id == user.firm_id,
        )
        .order_by(PendingAction.created_at.asc())
    )
    result = await session.execute(stmt)
    rows = result.all()

    items = [
        {
            "id": str(pending.id),
            "thread_id": str(thread.id),
            "thread_title": thread.title,
            "title": pending.confirmation_text,
            "tool_key": tool_call.tool_key,
            "created_at": pending.created_at.isoformat(),
        }
        for pending, thread, tool_call in rows
    ]

    return {
        "needs_decision_count": len(items),
        "needs_decision": items,
    }


@router.get("/briefing")
async def briefing(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """Home stats: real counts only. A row we can't measure is left out, not faked."""

    needs_stmt = (
        select(func.count(PendingAction.id))
        .join(Thread, PendingAction.thread_id == Thread.id)
        .where(
            PendingAction.requires_user_id == user.id,
            PendingAction.status == PendingActionStatus.AWAITING_CONFIRMATION,
            Thread.firm_id == user.firm_id,
        )
    )
    needs_count = (await session.execute(needs_stmt)).scalar_one()

    threads_stmt = select(func.count(Thread.id)).where(Thread.firm_id == user.firm_id)
    threads_count = (await session.execute(threads_stmt)).scalar_one()

    agents_count = len(tools_for_role(user.role))

    rows = [
        {
            "id": "needs",
            "label": "Waiting on a decision",
            "count": needs_count,
            "detail": None,
            "prompt": "Show me what's waiting on my decision.",
            "tone": "attention" if needs_count > 0 else "neutral",
        },
        {
            "id": "agents",
            "label": "Agents you can run",
            "count": agents_count,
            "detail": None,
            "prompt": "What agents can I run?",
            "tone": "neutral",
        },
        {
            "id": "conversations",
            "label": "Conversations",
            "count": threads_count,
            "detail": None,
            "prompt": "Show me my recent conversations.",
            "tone": "neutral",
        },
    ]

    return {
        "rows": rows,
        "not_shown": ["Cost and outcome value aren't shown here — the rates behind them aren't measured yet."],
    }


@router.get("/mentionables")
async def mentionables(
    user: User = Depends(get_current_user),
) -> dict:
    """The @-mentionable agents for this role — the distinct speaking_agent values
    across the tools this role may call."""

    tools = tools_for_role(user.role)
    by_agent: dict[str, list[str]] = {}
    for tool in tools.values():
        by_agent.setdefault(tool.speaking_agent, []).append(tool.name)

    items = [
        {
            "id": agent,
            "name": agent,
            "description": ", ".join(sorted(names)),
        }
        for agent, names in sorted(by_agent.items())
        if agent != "astra"  # Astra is who you're already talking to, not a mention target
    ]

    return {"mentionables": items}


@router.get("")
async def list_threads(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> dict:
    """List all threads for the current user's firm, with a computed status."""

    stmt = select(Thread).where(Thread.firm_id == user.firm_id).order_by(Thread.updated_at.desc())
    result = await session.execute(stmt)
    threads = result.scalars().all()

    if not threads:
        return {"threads": []}

    pending_stmt = select(PendingAction.thread_id).where(
        PendingAction.thread_id.in_([t.id for t in threads]),
        PendingAction.status == PendingActionStatus.AWAITING_CONFIRMATION,
    )
    waiting_ids = {row[0] for row in (await session.execute(pending_stmt)).all()}

    return {
        "threads": [
            {
                "id": str(t.id),
                "kind": t.kind.value,
                "subject_id": str(t.subject_id) if t.subject_id else None,
                "title": t.title,
                "status": "awaiting_confirmation" if t.id in waiting_ids else "idle",
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
    """Fetch a thread with its messages, tool-call sources, and any pending action."""

    stmt = (
        select(Thread)
        .where(Thread.id == thread_id)
        .options(selectinload(Thread.messages).selectinload(ThreadMessage.tool_calls))
    )
    result = await session.execute(stmt)
    thread = result.scalar_one_or_none()
    if not thread or thread.firm_id != user.firm_id:
        raise HTTPException(status_code=404, detail="Thread not found")

    pending_stmt = select(PendingAction).where(PendingAction.thread_id == thread_id)
    pending_rows = (await session.execute(pending_stmt)).scalars().all()
    pending_by_tool_call = {p.tool_call_id: p for p in pending_rows if p.status == PendingActionStatus.AWAITING_CONFIRMATION}

    status = "awaiting_confirmation" if pending_by_tool_call else "idle"

    return {
        "id": str(thread.id),
        "kind": thread.kind.value,
        "subject_id": str(thread.subject_id) if thread.subject_id else None,
        "title": thread.title,
        "status": status,
        "created_at": thread.created_at.isoformat(),
        "messages": [_serialize_message(m, pending_by_tool_call) for m in thread.messages],
    }
