"""Client document vault — adviser-uploaded documents shared with a household."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ClientDocument(Base):
    __tablename__ = "client_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    uploaded_by: Mapped[str] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(255))
    doc_type: Mapped[str] = mapped_column(String(64), default="general")
    content_text: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    is_client_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    size_bytes: Mapped[int] = mapped_column(default=0)
