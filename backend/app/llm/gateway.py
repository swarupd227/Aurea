"""Phase 2: Gateway — the orchestration loop for conversation-first interface.

The gateway runs the core loop:
1. User sends a message to Astra
2. Astra (the LLM) decides what tools to call
3. Gateway validates role access, transcript integrity
4. For state-changing tools, gateway pauses and waits for confirmation
5. Once confirmed, gateway executes and returns result
6. Astra sees the result and continues (or loops back)

The gateway is the single enforcement point for decision rights, role checks, and
exactly-once execution of state-changing tools.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.tools import ToolChangeState, Tool, get_tool, validate_tool_call, TOOLS
from app.models.enums import UserRole
from app.models.thread import (
    Thread, ThreadKind, ThreadMessage, MessageRole, ToolCall, ToolCallStatus,
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


class ConfirmationRequiredError(GatewayError):
    """This tool changes state and requires user confirmation."""
    def __init__(self, tool_key: str, confirmation_text: str, pending_action_id: uuid.UUID):
        super().__init__(confirmation_text)
        self.tool_key = tool_key
        self.pending_action_id = pending_action_id


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
        Send a user message and orchestrate the tool loop.

        Yields:
        - {"type": "message", "role": "astra", "text": "...", "tool_calls": [...]}
        - {"type": "tool_result", "tool_key": "...", "result": {...}}
        - {"type": "pending_action", "id": "...", "confirmation": "..."}
        - {"type": "error", "code": "...", "message": "..."}
        """

        # Fetch the thread and verify access
        thread = await self.session.get(Thread, thread_id)
        if not thread or thread.firm_id != self.firm_id:
            raise GatewayError(f"Thread {thread_id} not found or access denied")

        # Store the user's message
        user_msg = ThreadMessage(
            thread_id=thread_id,
            role=MessageRole.USER,
            text=text,
        )
        self.session.add(user_msg)
        await self.session.flush()

        # Call Astra with the thread history and ask what to do
        # (Note: this is a stub — actual LLM integration comes in Phase 3)
        astra_response = await self._call_orchestrator(thread_id)

        # Store Astra's message
        astra_msg = ThreadMessage(
            thread_id=thread_id,
            role=MessageRole.ASTRA,
            text=astra_response.get("text", ""),
            card_kind=astra_response.get("card_kind"),
            card_data=astra_response.get("card_data"),
            suggestions=astra_response.get("suggestions", []),
        )
        self.session.add(astra_msg)

        # Process tool calls from Astra's response
        tool_calls = astra_response.get("tool_calls", [])
        if tool_calls:
            await self._process_tool_calls(thread_id, astra_msg, tool_calls)

        await self.session.commit()

        # Yield the response to stream back to the user
        yield {
            "type": "message",
            "role": "astra",
            "text": astra_response.get("text"),
            "card": {
                "kind": astra_response.get("card_kind"),
                "data": astra_response.get("card_data"),
            } if astra_response.get("card_kind") else None,
            "suggestions": astra_response.get("suggestions", []),
        }

    async def _process_tool_calls(
        self,
        thread_id: uuid.UUID,
        message: ThreadMessage,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        """Process and validate tool calls from Astra."""

        for tool_spec in tool_calls:
            tool_key = tool_spec.get("tool_key")
            inputs = tool_spec.get("inputs", {})

            # Validate access
            is_valid, error_msg = validate_tool_call(self.role, tool_key, inputs)
            if not is_valid:
                raise RoleForbiddenError(error_msg)

            tool = get_tool(tool_key)
            if not tool:
                raise TranscriptInvalidError(f"Tool '{tool_key}' does not exist in catalogue")

            # Create the ToolCall record
            tool_call = ToolCall(
                message_id=message.id,
                tool_key=tool_key,
                inputs=inputs,
                status=ToolCallStatus.PENDING,
            )
            self.session.add(tool_call)
            await self.session.flush()

            # If this tool changes state, create a pending action
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

                raise ConfirmationRequiredError(
                    tool_key=tool_key,
                    confirmation_text=pending.confirmation_text,
                    pending_action_id=pending.id,
                )

            # For read-only tools, execute immediately
            elif tool.change_state == ToolChangeState.NO:
                result = await self._execute_tool(tool_key, inputs)
                tool_call.status = ToolCallStatus.DONE
                tool_call.result = result
                tool_call.completed_at = datetime.utcnow()

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

        # Fetch the tool call and execute it
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

        # Delegate to the executor layer
        executors = ToolExecutors(self.session, self.user_id, self.role, self.firm_id)
        return await executors.execute(tool_key, inputs)

    async def _call_orchestrator(self, thread_id: uuid.UUID) -> dict[str, Any]:
        """Call Astra (the LLM) to decide what tools to invoke next."""

        # Fetch thread and build message history
        thread = await self.session.get(Thread, thread_id)
        if not thread:
            return {"text": "Thread not found.", "tool_calls": [], "suggestions": []}

        # Build message history for context — query directly rather than lazy-loading
        # thread.messages, which would attempt a sync-style load outside the async
        # greenlet context and raise sqlalchemy.exc.MissingGreenlet.
        recent_stmt = (
            select(ThreadMessage)
            .where(ThreadMessage.thread_id == thread_id)
            .order_by(ThreadMessage.created_at.desc())
            .limit(5)
        )
        recent_result = await self.session.execute(recent_stmt)
        recent_messages = list(reversed(recent_result.scalars().all()))

        history = []
        for msg in recent_messages:
            role = "user" if msg.role == MessageRole.USER else "assistant"
            history.append(f"{role}: {msg.text}")

        history_text = "\n".join(history) if history else "(no prior messages)"

        # Build tool catalogue for the LLM
        tool_descriptions = self._build_tool_descriptions()

        # System prompt for Astra
        system_prompt = f"""You are Astra, the wealth intelligence orchestrator for Astra for Wealth.

Your role is to help users manage their wealth through conversation. You have access to tools that can:
- Read client data (households, portfolios, holdings)
- Make recommendations (approve, modify, dismiss, revise)
- Execute trades (buy/sell orders)
- Update goals

**Important:**
- You always respond conversationally and naturally.
- When you need to take an action, you MUST include tool calls in your response.
- Tool calls should be formatted as JSON objects in your response.
- Only call tools that the user's role permits.
- Be concise and focused.

**Available Tools:**
{tool_descriptions}

**Thread History:**
{history_text}

**User's Role:** {self.role}

Respond naturally. When you decide to call a tool, include it as a JSON object:
{{"tool_key": "tool_name", "inputs": {{...}}}}

You can include multiple tool calls or none at all, depending on what the conversation needs."""

        # Get the latest user message
        messages_stmt = select(ThreadMessage).where(
            (ThreadMessage.thread_id == thread_id) & (ThreadMessage.role == MessageRole.USER)
        ).order_by(ThreadMessage.created_at.desc())
        result = await self.session.execute(messages_stmt)
        latest_user_msg = result.scalars().first()

        if not latest_user_msg:
            return {"text": "No message found.", "tool_calls": [], "suggestions": []}

        prompt = latest_user_msg.text

        # Call the LLM
        try:
            llm_result = await llm_service.generate(
                task="advice",
                system=system_prompt,
                prompt=prompt,
                max_tokens=1500,
                temperature=0.5,
            )

            # Parse tool calls from the response
            tool_calls = self._extract_tool_calls(llm_result.text)

            return {
                "text": self._clean_response(llm_result.text, tool_calls),
                "tool_calls": tool_calls,
                "suggestions": ["Continue", "Show details", "Confirm action"],
            }
        except Exception as e:
            # Fallback response on LLM failure
            return {
                "text": f"I encountered an issue: {str(e)}. Please try again.",
                "tool_calls": [],
                "suggestions": ["Retry", "Cancel"],
            }

    def _build_tool_descriptions(self) -> str:
        """Build a human-readable description of available tools."""
        descriptions = []
        for tool_key in sorted(TOOLS.keys()):
            tool = TOOLS[tool_key]
            if self.role not in tool.roles_required:
                continue  # Skip tools this role can't use

            inputs_desc = ", ".join(
                f"{inp.name}" for inp in tool.inputs
            )
            descriptions.append(
                f"- **{tool.name}** ({tool_key}): {tool.description}\n"
                f"  Inputs: {inputs_desc or '(none)'}"
            )

        return "\n".join(descriptions)

    def _extract_tool_calls(self, text: str) -> list[dict[str, Any]]:
        """Extract JSON tool calls from LLM response."""
        tool_calls = []
        lines = text.split("\n")

        for line in lines:
            line = line.strip()
            if line.startswith("{") and "tool_key" in line:
                try:
                    obj = json.loads(line)
                    if "tool_key" in obj and "inputs" in obj:
                        tool_calls.append(obj)
                except json.JSONDecodeError:
                    pass  # Skip malformed JSON

        return tool_calls

    def _clean_response(self, text: str, tool_calls: list[dict[str, Any]]) -> str:
        """Remove JSON tool calls from the response text."""
        lines = []
        for line in text.split("\n"):
            line_strip = line.strip()
            # Skip lines that look like tool call JSON
            if line_strip.startswith("{") and "tool_key" in line_strip:
                continue
            lines.append(line)

        return "\n".join(lines).strip()
