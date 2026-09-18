"""Gateway — the streaming orchestration loop for the conversation-first interface.

The gateway runs the core loop:
1. User sends a message to Astra
2. Astra (Claude, via native tool-use) streams a response and may request tools
3. Gateway validates role access as each tool request arrives
4. Read-only tools execute immediately; state-changing tools pause for confirmation
5. The full turn (prose + tool activity) streams back to the caller as it happens

The gateway is the single enforcement point for decision rights, role checks, and
exactly-once execution of state-changing tools.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tools import ToolChangeState, anthropic_tools_for_role, get_tool, validate_tool_call
from app.models.enums import UserRole
from app.models.thread import (
    Thread, ThreadMessage, MessageRole, ToolCall, ToolCallStatus,
    PendingAction, PendingActionStatus,
)
from app.llm.executors import ToolExecutors
from app.llm.service import llm_service


class GatewayError(Exception):
    """Base error for gateway violations."""
    pass


class RoleForbiddenError(GatewayError):
    """User's role does not permit this tool."""
    pass


class TranscriptInvalidError(GatewayError):
    """Transcript contains invalid tool calls or state."""
    pass


SYSTEM_PROMPT_TEMPLATE = """You are Astra, the wealth intelligence orchestrator for Astra for Wealth.

You help advisers and their team manage client wealth through conversation. You have tools
to read client data (households, portfolios, holdings), decide on recommendations, execute
trades, and update goals. Use a tool whenever the user's request calls for real data or a
real action — never invent numbers or pretend to have checked something you haven't.

State-changing tools (deciding a recommendation, executing orders, updating a goal) always
pause for the user's explicit confirmation before anything happens — that pause is handled
for you automatically when you call the tool, so call it as soon as you have what you need
rather than asking the user to confirm in words first.

Be concise, direct, and conversational. This is a regulated wealth platform: don't guess at
client identifiers: ask for them, or use search_holdings, if you don't already have one.

User's role: {role}

Recent conversation:
{history}
"""


class Gateway:
    """The orchestration gateway for the conversation loop.

    Responsibilities:
    - Validate that each tool call is permitted by the user's role
    - Enforce state-changing tool confirmations
    - Ensure exactly-once execution under transaction boundaries
    - Persist the complete transcript
    - Stream results back to the user
    """

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, role: UserRole, firm_id: uuid.UUID):
        self.session = session
        self.user_id = user_id
        self.role = role
        self.firm_id = firm_id

    async def send_message(
        self,
        thread_id: uuid.UUID,
        text: str,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Send a user message and stream Astra's turn back as it happens.

        Yields, in order:
        - {"type": "token", "text": "..."} — prose chunks, zero or more
        - {"type": "tool_start", "tool_key": "..."} — before a read-only tool executes
        - {"type": "tool_result", "tool_key": "...", "ok": bool} — after it finishes
        - {"type": "pending_action", "pending_action_id": "...", "tool_key": "...",
           "confirmation_text": "..."} — a state-changing tool is paused for confirmation
        - {"type": "done", "message_id": "..."} — the turn is fully persisted
        - {"type": "error", "message": "..."} — something went wrong; the turn still ends
        """

        thread = await self.session.get(Thread, thread_id)
        if not thread or thread.firm_id != self.firm_id:
            raise GatewayError(f"Thread {thread_id} not found or access denied")

        user_msg = ThreadMessage(thread_id=thread_id, role=MessageRole.USER, text=text)
        self.session.add(user_msg)
        await self.session.flush()

        full_text = ""
        tool_uses: list[dict[str, Any]] = []
        try:
            async for event in self._stream_tokens(thread_id):
                if event["type"] == "token":
                    yield event
                elif event["type"] == "_final":
                    full_text = event["text"]
                    tool_uses = event["tool_uses"]
        except Exception as e:  # pragma: no cover - network dependent
            await self.session.rollback()
            yield {"type": "error", "message": f"I hit an error talking to the model: {e}"}
            return

        astra_msg = ThreadMessage(
            thread_id=thread_id,
            role=MessageRole.ASTRA,
            text=full_text,
            suggestions=["Continue", "Show details", "Confirm action"] if full_text else [],
        )
        self.session.add(astra_msg)
        # Flush so astra_msg.id (a client-side UUID default) is assigned before
        # ToolCall rows reference it — without this, message_id is still None and
        # the insert violates the NOT NULL constraint.
        await self.session.flush()

        for tool_use in tool_uses:
            tool_key = tool_use["name"]
            inputs = tool_use["input"] or {}

            is_valid, error_msg = validate_tool_call(self.role, tool_key, inputs)
            if not is_valid:
                await self.session.commit()
                yield {"type": "error", "message": error_msg}
                return

            tool = get_tool(tool_key)
            if not tool:
                await self.session.commit()
                yield {"type": "error", "message": f"Tool '{tool_key}' does not exist"}
                return

            tool_call = ToolCall(
                message_id=astra_msg.id, tool_key=tool_key, inputs=inputs, status=ToolCallStatus.PENDING,
            )
            self.session.add(tool_call)
            await self.session.flush()

            if tool.change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION:
                pending = PendingAction(
                    thread_id=thread_id,
                    tool_call_id=tool_call.id,
                    confirmation_text=tool.confirmation or f"Confirm {tool.name}?",
                    requires_user_id=self.user_id,
                    expires_at=datetime.utcnow() + timedelta(hours=24),
                )
                self.session.add(pending)
                await self.session.flush()
                await self.session.commit()

                yield {
                    "type": "pending_action",
                    "pending_action_id": str(pending.id),
                    "tool_key": tool_key,
                    "confirmation_text": pending.confirmation_text,
                }
                return

            # Read-only: execute immediately and let the caller know it happened.
            yield {"type": "tool_start", "tool_key": tool_key}
            try:
                result = await self._execute_tool(tool_key, inputs)
                tool_call.status = ToolCallStatus.DONE
                tool_call.result = result
                tool_call.completed_at = datetime.utcnow()
                yield {"type": "tool_result", "tool_key": tool_key, "ok": True}
            except Exception as e:
                tool_call.status = ToolCallStatus.FAILED
                tool_call.error = str(e)
                tool_call.completed_at = datetime.utcnow()
                yield {"type": "tool_result", "tool_key": tool_key, "ok": False}
                await self.session.commit()
                yield {"type": "error", "message": str(e)}
                return

        await self.session.commit()
        yield {"type": "done", "message_id": str(astra_msg.id)}

    async def _stream_tokens(self, thread_id: uuid.UUID) -> AsyncGenerator[dict[str, Any], None]:
        """Yields {"type": "token", ...} chunks then a final {"type": "_final", ...}
        carrying the accumulated text and any tool_uses Claude requested."""

        recent_stmt = (
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.created_at.desc())
            .limit(8)
        )
        recent_result = await self.session.execute(recent_stmt)
        recent_messages = list(reversed(recent_result.scalars().all()))

        history_lines = []
        for msg in recent_messages[:-1] if recent_messages else []:
            role = "user" if msg.role == MessageRole.USER else "assistant"
            history_lines.append(f"{role}: {msg.text}")
        history_text = "\n".join(history_lines) if history_lines else "(no prior messages)"

        latest = recent_messages[-1] if recent_messages else None
        if not latest or latest.role != MessageRole.USER:
            yield {"type": "_final", "text": "No message found.", "tool_uses": []}
            return

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(role=str(self.role), history=history_text)
        tools = anthropic_tools_for_role(self.role)

        full_text = ""
        tool_uses: list[dict[str, Any]] = []
        async for event in llm_service.stream(
            task="advice", system=system_prompt, prompt=latest.text, tools=tools,
            max_tokens=1500, temperature=0.5,
        ):
            if event["type"] == "text":
                full_text += event["text"]
                yield {"type": "token", "text": event["text"]}
            elif event["type"] == "done":
                tool_uses = event.get("tool_uses", [])

        yield {"type": "_final", "text": full_text, "tool_uses": tool_uses}

    async def confirm_pending_action(
        self,
        pending_action_id: uuid.UUID,
        confirmed: bool = True,
    ) -> dict[str, Any]:
        """
        Confirm or reject a pending state-changing action.

        If confirmed, executes the tool and marks it done.
        If rejected, marks as rejected and returns error.
        """

        pending = await self.session.get(PendingAction, pending_action_id)
        if not pending:
            raise GatewayError(f"Pending action {pending_action_id} not found")

        if pending.status != PendingActionStatus.AWAITING_CONFIRMATION:
            raise GatewayError(f"Pending action is already {pending.status.value}")

        if not confirmed:
            pending.status = PendingActionStatus.REJECTED
            await self.session.commit()
            return {"status": "rejected", "message": "Action was rejected by user"}

        tool_call = await self.session.get(ToolCall, pending.tool_call_id)
        if not tool_call:
            raise GatewayError("Tool call not found")

        try:
            result = await self._execute_tool(tool_call.tool_key, tool_call.inputs)
            tool_call.status = ToolCallStatus.DONE
            tool_call.result = result
            tool_call.completed_at = datetime.utcnow()
            pending.status = PendingActionStatus.CONFIRMED
            pending.confirmed_at = datetime.utcnow()
            await self.session.commit()

            return {
                "status": "confirmed",
                "tool_key": tool_call.tool_key,
                "result": result,
            }
        except Exception as e:
            tool_call.status = ToolCallStatus.FAILED
            tool_call.error = str(e)
            tool_call.completed_at = datetime.utcnow()
            await self.session.commit()

            raise GatewayError(f"Tool execution failed: {e}")

    async def _execute_tool(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool and return its result."""

        tool = get_tool(tool_key)
        if not tool:
            raise GatewayError(f"Tool '{tool_key}' not found")

        executors = ToolExecutors(self.session, self.user_id, self.role, self.firm_id)
        return await executors.execute(tool_key, inputs)
