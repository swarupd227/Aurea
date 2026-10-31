"""Thread and gateway API endpoints for the conversation-first workspace.

Response shapes are designed for the workspace UI's rail (Needs You, Conversations,
Your Agents) and thread view (proof-of-work sources, pending-action confirm cards,
starter briefing). Every count here is a real query — nothing is invented; a stat
we don't have data for is left out rather than faked.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.atlas import narration
from app.core import agent_runs
from app.core.db import SessionLocal, get_db
from app.core.decision_rights import queue_action, roles_for
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.core.tools import tools_for_role
from app.llm import turns
from app.llm.executors import ExecutorError, ToolExecutors
from app.llm.gateway import Gateway, GatewayError
from app.models.enums import RecommendationStatus, UserRole
from app.models.governance import Recommendation
from app.models.identity import User
from app.models.thread import (
    Thread, ThreadKind, ThreadMessage, ToolCall, ToolCallStatus,
    PendingAction, PendingActionStatus,
)

router = APIRouter(prefix="/api/threads", tags=["threads"])
log = get_logger("aurea.threads")

# Work that must outlive the request that started it (see confirm_pending_action). Held here so
# the event loop does not drop a task nobody is awaiting any more.
_DETACHED: set[asyncio.Future] = set()


def _forget(task: asyncio.Future) -> None:
    _DETACHED.discard(task)
    if not task.cancelled() and task.exception() is not None and not isinstance(task.exception(), GatewayError):
        log.error("detached_task_failed", error=str(task.exception()))


def _serialize_message(
    m: ThreadMessage, pending_by_tool_call: dict[uuid.UUID, PendingAction],
    previews: dict[uuid.UUID, dict] | None = None,
) -> dict:
    done_calls = [tc for tc in m.tool_calls if tc.status == ToolCallStatus.DONE]
    sources = sorted({tc.tool_key for tc in done_calls})
    # One artifact per completed tool call, in call order — the frontend picks a
    # renderer by tool_key and falls back to nothing (the prose already covers it)
    # for a tool_key it doesn't have a card for.
    artifacts = [{"tool_key": tc.tool_key, "result": tc.result} for tc in done_calls]
    pending = next((pending_by_tool_call[tc.id] for tc in m.tool_calls if tc.id in pending_by_tool_call), None)
    pending_call = next((tc for tc in m.tool_calls if pending and tc.id == pending.tool_call_id), None)

    return {
        "id": str(m.id),
        "role": m.role.value,
        "speaking_agent": m.speaking_agent,
        "text": m.text,
        "sources": sources,
        "artifacts": artifacts,
        "pending_action": {
            "id": str(pending.id),
            "tool_key": pending_call.tool_key,
            "confirmation_text": pending.confirmation_text,
            "decision": (
                None if pending.status == PendingActionStatus.AWAITING_CONFIRMATION
                else "expired" if pending.status == PendingActionStatus.EXPIRED
                else "declined" if pending.status == PendingActionStatus.REJECTED
                else "failed" if pending_call.status == ToolCallStatus.FAILED
                else "confirmed"
            ),
            # Waiting: what confirming would do, read now. Decided: what the card showed at the time.
            "preview": (
                (previews or {}).get(pending.id)
                if pending.status == PendingActionStatus.AWAITING_CONFIRMATION else pending.shown
            ),
        } if pending else None,
        # A run's card lives on the message of the agent that ran it, so it is served as the
        # message's own card rather than as a tool's artifact.
        "card": (
            {"kind": m.card_kind, "data": narration.as_served(m.card_data)}
            if m.card_kind and m.card_data else None
        ),
        "suggestions": m.suggestions or [],
        "created_at": m.created_at.isoformat(),
    }


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _is_theirs(thread: Thread | None, user: User) -> bool:
    """A conversation belongs to the person who started it. Being in the same firm is not enough:
    a thread holds tool results gated to its author's role, so a colleague in another role reading
    it would see what their own tools would refuse them. Another person's thread reads as not found."""
    return thread is not None and thread.firm_id == user.firm_id and thread.created_by_user_id == user.id


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
    if not _is_theirs(thread, user):
        raise HTTPException(status_code=404, detail="Thread not found")

    # The turn is generated by a task of its own, on its own session, and the response only relays
    # what the task produces. A browser that navigates away closes the response; it must not take
    # the turn with it, or the reply (and the question) would vanish from the thread. The task runs
    # to the end and persists, whoever is still listening.
    user_id, role, firm_id = user.id, user.role, user.firm_id
    events: asyncio.Queue = asyncio.Queue()

    async def run_turn() -> None:
        turns.begin(thread_id)
        try:
            async with SessionLocal() as own:
                async for event in Gateway(own, user_id, role, firm_id).send_message(thread_id, text):
                    events.put_nowait(event)
        except GatewayError as e:
            events.put_nowait({"type": "error", "message": str(e)})
        except Exception as e:  # the turn still ends; the error is in the thread, not only on the wire
            log.error("turn_failed", error=str(e))
            events.put_nowait({"type": "error", "message": "Something went wrong with that reply. Try again."})
        finally:
            turns.end(thread_id)
            events.put_nowait(None)

    task = asyncio.ensure_future(run_turn())
    _DETACHED.add(task)
    task.add_done_callback(_forget)

    async def event_stream():
        while True:
            event = await events.get()
            if event is None:
                break
            yield _sse(event)

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
    seen_tier = confirm.get("seen_tier")
    user_id, role, firm_id = user.id, user.role, user.firm_id

    # The confirmation runs as its own task on its own session, and the request only waits for it.
    # A user who closes the tab or navigates away mid-run has still confirmed: the action finishes
    # and settles on the thread rather than being cancelled half way with the connection.
    async def settle() -> dict:
        async with SessionLocal() as own:
            return await Gateway(own, user_id, role, firm_id).confirm_pending_action(
                pending_action_id, confirmed, seen_tier=seen_tier)

    task = asyncio.ensure_future(settle())
    _DETACHED.add(task)
    task.add_done_callback(_forget)

    try:
        result = await asyncio.shield(task)
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


async def _my_open_proposals(session: AsyncSession, user: User) -> list[tuple[Recommendation, str]]:
    """Open agent proposals this role is asked about, most urgent first, each with what it is asked
    to do ("decide", or "veto" for compliance on a proposal that moves money).

    The rights come from decision_rights - the same source the decide route and the executor
    use - so the rail never offers a decision the server would refuse. Admins configure the
    platform and hold no proposals to decide. Compliance is asked only where its veto is the
    control (decision_rights.queue_action), not about every draft it could technically approve.
    """
    if user.role in (UserRole.ADMIN, UserRole.SUPERADMIN, UserRole.CLIENT):
        return []
    role = UserRole(user.role)
    recs = (await session.execute(
        select(Recommendation)
        .where(Recommendation.firm_id == user.firm_id,
               Recommendation.status == RecommendationStatus.PROPOSED)
        .order_by(Recommendation.priority.asc(), Recommendation.created_at.desc())
        .limit(200)
    )).scalars().all()
    return [(r, a) for r in recs if (a := queue_action(role, r.agent_key))]


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

    mine = await _my_open_proposals(session, user)
    proposals_count = len(mine)
    proposals = [
        {
            "id": str(r.id),
            "title": r.title,
            "agent_key": str(r.agent_key),
            "subject_label": r.subject_label,
            "priority": r.priority,
            "action": action,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r, action in mine[:8]
    ]

    return {
        "needs_decision_count": len(items),
        "needs_decision": items,
        "proposals_count": proposals_count,
        "proposals": proposals,
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
    needs_count = (await session.execute(needs_stmt)).scalar_one() + len(await _my_open_proposals(session, user))

    threads_stmt = select(func.count(Thread.id)).where(
        Thread.firm_id == user.firm_id, Thread.created_by_user_id == user.id)
    threads_count = (await session.execute(threads_stmt)).scalar_one()

    # Agents, not tools: the count of agents this role may start from a conversation.
    agents_count = len(agent_runs.runnable_agents(user.role))

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
    """List the current user's own threads, with a computed status."""

    stmt = (
        select(Thread)
        .where(Thread.firm_id == user.firm_id, Thread.created_by_user_id == user.id)
        .order_by(Thread.updated_at.desc())
    )
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
    if not _is_theirs(thread, user):
        raise HTTPException(status_code=404, detail="Thread not found")

    pending_stmt = select(PendingAction).where(PendingAction.thread_id == thread_id)
    pending_rows = (await session.execute(pending_stmt)).scalars().all()
    pending_by_tool_call = {p.tool_call_id: p for p in pending_rows}
    waiting = {k: p for k, p in pending_by_tool_call.items() if p.status == PendingActionStatus.AWAITING_CONFIRMATION}

    status = "awaiting_confirmation" if waiting else "idle"

    # A run's confirmation shows what it would start - agent, subject, the tier it resolves to
    # now - read from live policy each time, so the card cannot be stale about the tier.
    previews: dict[uuid.UUID, dict] = {}
    for m in thread.messages:
        for tc in m.tool_calls:
            pending = waiting.get(tc.id)
            if pending is not None:
                try:
                    executors = ToolExecutors(session, user.id, user.role, user.firm_id)
                    preview = (await executors.preview_run_agent(tc.inputs or {}) if tc.tool_key == "run_agent"
                               else await executors.preview_change(tc.tool_key, tc.inputs or {}))
                    if preview is not None:
                        previews[pending.id] = preview
                except ExecutorError as exc:
                    # Say why it can no longer be confirmed rather than showing a stale card.
                    previews[pending.id] = {"unavailable": str(exc)}

    return {
        "id": str(thread.id),
        "kind": thread.kind.value,
        "subject_id": str(thread.subject_id) if thread.subject_id else None,
        "title": thread.title,
        "status": status,
        # A reply is being written to this thread right now (on this replica).
        "working": turns.is_active(thread.id),
        "created_at": thread.created_at.isoformat(),
        "messages": [_serialize_message(m, pending_by_tool_call, previews) for m in thread.messages],
    }
