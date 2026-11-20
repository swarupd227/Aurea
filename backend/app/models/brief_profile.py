"""How a member of staff wants their briefs laid out, and what they open in them (B-203).

A profile is the RM's own: which sections lead, how much opens at first, and the strengths they lead a conversation
with. It changes the order and emphasis of a brief, never its facts. A section event records a section the RM opened
or closed on a brief, so the platform can offer a change back; it never changes the profile by itself.
"""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class BriefProfile(Base):
    __tablename__ = "brief_profile"
    __table_args__ = (UniqueConstraint("user_id", name="uq_brief_profile_user"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    lead: Mapped[list] = mapped_column(JSON, default=list)          # section keys, in the order they lead
    length: Mapped[str] = mapped_column(String(12), default="standard")
    strengths: Mapped[list] = mapped_column(JSON, default=list)     # a few words each, the RM's own
    version: Mapped[int] = mapped_column(Integer, default=1)


class BriefSectionEvent(Base):
    __tablename__ = "brief_section_event"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    brief_kind: Mapped[str] = mapped_column(String(16))     # meeting | prospect
    section: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(8))           # open | close
