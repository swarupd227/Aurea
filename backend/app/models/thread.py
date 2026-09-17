"""Phase 2: Thread, Message, ToolCall, and PendingAction models for conversation persistence.

These models store the complete transcript of an agentic conversation: every message,
tool invocation, result, and pending action awaiting confirmation.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import JSON, ForeignKey, String, Text, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import UserRole


class ThreadKind(str, Enum):
    """What kind of thread this is."""
    ASK_ASTRA = "ask_astra"  # Firm-wide thread, opening with daily brief
    HOUSEHOLD = "household"  # Per-household conversation
    ONBOARDING_CASE = "onboarding_case"  # Per-onboarding case
    INCIDENT = "incident"  # Auto-paused agent or surveillance flag


class MessageRole(str, Enum):
    """Who sent this message."""
    USER = "user"  # The human
    ASTRA = "astra"  # The orchestrator
    AGENT = "agent"  # A specific agent (e.g., drift_rebalancing)


class ToolCallStatus(str, Enum):
    """Status of a tool invocation."""
    PENDING = "pending"  # Awaiting execution
    RUNNING = "running"  # Currently executing
    DONE = "done"  # Completed successfully
    FAILED = "failed"  # Failed with an error


class PendingActionStatus(str, Enum):
    """Status of an action awaiting confirmation."""
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    EXPIRED = "expired"


class Thread(Base):
    """A conversation thread between a user and Astra."""
    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    firm_id: Mapped[uuid.UUID] = mapped_column(index=True)
    kind: Mapped[ThreadKind] = mapped_column(SQLEnum(ThreadKind))

    # The subject of the conversation (optional — household, case, etc.)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)

    # Who started this thread
    created_by_user_id: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    # Thread metadata
    title: Mapped[str] = mapped_column(String(255), nullable=True)  # User-friendly name
    is_archived: Mapped[bool] = mapped_column(default=False)

    # Relationships
    messages: Mapped[list[Message]] = relationship("Message", back_populates="thread", cascade="all, delete-orphan")
    pending_actions: Mapped[list[PendingAction]] = relationship("PendingAction", back_populates="thread", cascade="all, delete-orphan")


class Message(Base):
    """A single message in a thread."""
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id"), index=True)

    # Who sent it
    role: Mapped[MessageRole] = mapped_column(SQLEnum(MessageRole))
    speaking_agent: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g., "drift_rebalancing" if agent

    # The message content
    text: Mapped[str] = mapped_column(Text)

    # Optional card/attachment (e.g., a recommendation card)
    card_kind: Mapped[str | None] = mapped_column(String(64), nullable=True)  # e.g., "recommendation", "brief"
    card_data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=True)

    # Tool calls made in this message
    tool_calls: Mapped[list[ToolCall]] = relationship("ToolCall", back_populates="message", cascade="all, delete-orphan")

    # Suggestions for what to say next
    suggestions: Mapped[list[str]] = mapped_column(JSON, default=list)  # e.g., ["Approve", "Revise", "Dismiss"]

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)

    # Relationship back to thread
    thread: Mapped[Thread] = relationship("Thread", back_populates="messages")


class ToolCall(Base):
    """A tool invocation made during a message."""
    __tablename__ = "tool_calls"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    message_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("messages.id"), index=True)

    # What tool was called
    tool_key: Mapped[str] = mapped_column(String(64))  # e.g., "execute_orders"

    # Inputs and result
    inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)  # None until done
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Status
    status: Mapped[ToolCallStatus] = mapped_column(SQLEnum(ToolCallStatus), default=ToolCallStatus.PENDING)

    # Timeline
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(nullable=True)

    # Relationship back to message
    message: Mapped[Message] = relationship("Message", back_populates="tool_calls")


class PendingAction(Base):
    """An action awaiting user confirmation before execution."""
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("threads.id"), index=True)

    # What tool is pending
    tool_call_id: Mapped[uuid.UUID]  # Reference to the ToolCall in pending status

    # The confirmation sentence to show
    confirmation_text: Mapped[str] = mapped_column(Text)

    # The user who must confirm
    requires_user_id: Mapped[uuid.UUID]

    # Status
    status: Mapped[PendingActionStatus] = mapped_column(SQLEnum(PendingActionStatus), default=PendingActionStatus.AWAITING_CONFIRMATION)

    # Timeline
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)  # Auto-reject if not confirmed by this time

    # Relationship back to thread
    thread: Mapped[Thread] = relationship("Thread", back_populates="pending_actions")
