"""One thing that happened to a transfer into the firm, from a document (L200-1 §6.1, §6.2). Added and never edited:
the transfer's file is these entries in order, so it shows what was known on any day."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class TransferFileEntry(Base):
    __tablename__ = "transfer_file_entry"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    transfer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transfer_request.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    on: Mapped[date] = mapped_column(Date)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
