"""Gateway — the streaming, multi-step orchestration loop for the conversation-first
interface.

The gateway runs the core loop:
1. User sends a message to Astra
2. Astra (Claude, via native tool-use) streams a response and may request tools
3. Gateway validates role access as each tool request arrives
4. A read-only tool executes immediately, and its result is handed straight back to
   Claude so it can decide whether to call another tool or answer — up to
   MAX_TOOL_ROUNDS times in one turn, so "check the portfolio, then look up that
   symbol, then tell me the answer" resolves without the user re-prompting.
5. A state-changing tool pauses the whole turn for the user's confirmation.
6. The full turn (prose + tool activity, across every round) streams back to the
   caller as it happens.

The gateway is the single enforcement point for decision rights, role checks, and
exactly-once execution of state-changing tools.
"""
from __future__ import annotations

import json
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

# A tool round-trip (Claude's tool_use -> our result -> Claude again) this many times
# in one turn before the gateway stops chaining and just answers with what it has —
# a guardrail against a confused model looping, not a limit anyone should normally hit.
MAX_TOOL_ROUNDS = 4


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

You can call a tool, see its result, and call another tool in the same turn — so if
answering well takes two or three steps (look up a household, then check a mandate it
owns, then answer), just do them in sequence rather than stopping to ask the user to
continue. Once you have what you need, answer in prose; don't narrate empty tool calls.

State-changing tools (deciding a recommendation, executing orders, updating a goal) always
pause for the user's explicit confirmation before anything happens — that pause is handled
for you automatically when you call the tool, so call it as soon as you have what you need
rather than asking the user to confirm in words first. A state-changing tool call always
ends your turn (there is no result to continue from, since nothing happens until the user
confirms), so make it your last step.

Be concise, direct, and conversational. This is a regulated wealth platform: don't guess at
client identifiers: ask for them, or use search_holdings, if you don't already have one.

User's role: {role}
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
        Send a user message and stream Astra's turn back as it happens — possibly
        across several rounds of tool use before the final answer.

        Yields, in order:
        - {"type": "token", "text": "..."} — prose chunks, zero or more per round
        - {"type": "tool_start", "tool_key": "..."} — before a read-only tool executes
        - {"type": "tool_result", "tool_key": "...", "ok": bool} — after it finishes
        - {"type": "pending_action", "pending_action_id": "...", "tool_key": "...",
           "confirmation_text": "..."} — a state-changing tool paused the turn
        - {"type": "done", "message_id": "..."} — the turn is fully persisted
        - {"type": "error", "message": "..."} — something went wrong; the turn still ends
        """

        thread = await self.session.get(Thread, thread_id)
        if not thread or thread.firm_id != self.firm_id:
            raise GatewayError(f"Thread {thread_id} not found or access denied")

        user_msg = ThreadMessage(thread_id=thread_id, role=MessageRole.USER, text=text)
        self.session.add(user_msg)
        await self.session.flush()

        messages = await self._build_messages(thread_id)
        accumulated_text: list[str] = []
        astra_msg: ThreadMessage | None = None

        try:
            for _round in range(MAX_TOOL_ROUNDS):
                round_text = ""
                tool_uses: list[dict[str, Any]] = []
                content_blocks: list[dict[str, Any]] = []

                async for event in self._stream_tokens(messages):
                    if event["type"] == "token":
                        round_text += event["text"]
                        yield event
                    elif event["type"] == "_final":
                        tool_uses = event["tool_uses"]
                        content_blocks = event["content_blocks"]

                if round_text:
                    accumulated_text.append(round_text)

                if not tool_uses:
                    break  # Claude answered in prose — the turn is done.

                # A message now exists to attach ToolCall rows to. Created once, on
                # the first round that actually calls a tool, and reused across
                # further rounds in the same turn.
                if astra_msg is None:
                    astra_msg = ThreadMessage(thread_id=thread_id, role=MessageRole.ASTRA, text="", suggestions=[])
                    self.session.add(astra_msg)
                    await self.session.flush()

                messages.append({"role": "assistant", "content": content_blocks})
                tool_result_blocks: list[dict[str, Any]] = []

                for tool_use in tool_uses:
                    tool_key = tool_use["name"]
                    inputs = tool_use["input"] or {}

                    is_valid, error_msg = validate_tool_call(self.role, tool_key, inputs)
                    if not is_valid:
                        astra_msg.text = "\n\n".join(accumulated_text)
                        await self.session.commit()
                        yield {"type": "error", "message": error_msg}
                        return

                    tool = get_tool(tool_key)
                    if not tool:
                        astra_msg.text = "\n\n".join(accumulated_text)
                        await self.session.commit()
                        yield {"type": "error", "message": f"Tool '{tool_key}' does not exist"}
                        return

                    tool_call = ToolCall(
                        message_id=astra_msg.id, tool_key=tool_key, inputs=inputs, status=ToolCallStatus.PENDING,
                    )
                    self.session.add(tool_call)
                    await self.session.flush()

                    if tool.change_state == ToolChangeState.PAUSE_FOR_CONFIRMATION:
                        # Nothing left to chain: a state change can't happen until the
                        # user confirms, so there is no result yet to hand back to
                        # Claude. Any further tool_uses this round are dropped — the
                        # confirmed action's result (once decided) starts a fresh turn.
                        pending = PendingAction(
                            thread_id=thread_id,
                            tool_call_id=tool_call.id,
                            confirmation_text=tool.confirmation or f"Confirm {tool.name}?",
                            requires_user_id=self.user_id,
                            expires_at=datetime.utcnow() + timedelta(hours=24),
                        )
                        self.session.add(pending)
                        await self.session.flush()
                        astra_msg.text = "\n\n".join(accumulated_text)
                        await self.session.commit()

                        yield {
                            "type": "pending_action",
                            "pending_action_id": str(pending.id),
                            "tool_key": tool_key,
                            "confirmation_text": pending.confirmation_text,
                        }
                        return

                    # Read-only: execute now and feed the result back to Claude so it
                    # can decide the next step (another tool, or the final answer).
                    yield {"type": "tool_start", "tool_key": tool_key}
                    try:
                        result = await self._execute_tool(tool_key, inputs)
                        tool_call.status = ToolCallStatus.DONE
                        tool_call.result = result
                        tool_call.completed_at = datetime.utcnow()
                        yield {"type": "tool_result", "tool_key": tool_key, "ok": True}
                        tool_result_blocks.append({
                            "type": "tool_result", "tool_use_id": tool_use["id"],
                            "content": json.dumps(result, default=str),
                        })
                    except Exception as e:
                        # A failed tool isn't fatal to the turn — Claude sees the error
                        # as the tool's result and can explain it or try something
                        # else, same as a person would read an error message.
                        tool_call.status = ToolCallStatus.FAILED
                        tool_call.error = str(e)
                        tool_call.completed_at = datetime.utcnow()
                        yield {"type": "tool_result", "tool_key": tool_key, "ok": False}
                        tool_result_blocks.append({
                            "type": "tool_result", "tool_use_id": tool_use["id"],
                            "content": f"Error: {e}", "is_error": True,
                        })

                messages.append({"role": "user", "content": tool_result_blocks})
                # Loop: call Claude again with the tool results now in the conversation.
            else:
                accumulated_text.append(
                    "(I've made several tool calls on this — let me know if you'd like me to keep going.)"
                )
        except Exception as e:  # pragma: no cover - network dependent
            await self.session.rollback()
            yield {"type": "error", "message": f"I hit an error talking to the model: {e}"}
            return

        final_text = "\n\n".join(t for t in accumulated_text if t.strip())
        if astra_msg is None:
            astra_msg = ThreadMessage(
                thread_id=thread_id, role=MessageRole.ASTRA, text=final_text,
                suggestions=["Continue", "Show details", "Confirm action"] if final_text else [],
            )
            self.session.add(astra_msg)
            await self.session.flush()
        else:
            astra_msg.text = final_text
            astra_msg.suggestions = ["Continue", "Show details", "Confirm action"] if final_text else []

        await self.session.commit()
        yield {"type": "done", "message_id": str(astra_msg.id)}

    async def _build_messages(self, thread_id: uuid.UUID) -> list[dict[str, Any]]:
        """The last few turns of this thread, in Anthropic message format, ending with
        the just-added user message. Consecutive same-role messages are merged —
        Claude's API requires strict user/assistant alternation, and while normal
        conversation already alternates, this is a cheap guard against anything that
        doesn't (e.g. two system-inserted messages in a row)."""

        recent_stmt = (
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.created_at.desc())
            .limit(8)
        )
        recent_result = await self.session.execute(recent_stmt)
        recent_messages = list(reversed(recent_result.scalars().all()))

        messages: list[dict[str, Any]] = []
        for msg in recent_messages:
            role = "user" if msg.role == MessageRole.USER else "assistant"
            if messages and messages[-1]["role"] == role:
                messages[-1]["content"] = f"{messages[-1]['content']}\n\n{msg.text}"
            else:
                messages.append({"role": role, "content": msg.text})
        return messages

    async def _stream_tokens(self, messages: list[dict[str, Any]]) -> AsyncGenerator[dict[str, Any], None]:
        """Yields {"type": "token", ...} chunks then a final {"type": "_final", ...}
        carrying the tool_uses Claude requested this round and its raw content_blocks
        (for the caller to append as the next "assistant" turn if it continues)."""

        system_prompt = SYSTEM_PROMPT_TEMPLATE.format(role=str(self.role))
        tools = anthropic_tools_for_role(self.role)

        tool_uses: list[dict[str, Any]] = []
        content_blocks: list[dict[str, Any]] = []
        async for event in llm_service.stream(
            task="advice", system=system_prompt, messages=messages, tools=tools,
            max_tokens=1500, temperature=0.5,
        ):
            if event["type"] == "text":
                yield {"type": "token", "text": event["text"]}
            elif event["type"] == "done":
                tool_uses = event.get("tool_uses", [])
                content_blocks = event.get("content_blocks", [])

        yield {"type": "_final", "tool_uses": tool_uses, "content_blocks": content_blocks}

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
