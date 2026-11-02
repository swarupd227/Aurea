"""Account features a client asked for and a principal decided, and temporary holds on suspected exploitation
(L200-1 §4). Rows are added; a decision, an extension or a release is a step on the row's list, never an edit of it."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AccountFeature(Base):
    __tablename__ = "account_feature"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    feature: Mapped[str] = mapped_column(String(16))            # margin | options | checkwriting
    level: Mapped[int | None] = mapped_column(Integer)          # options only, 1 to 4
    status: Mapped[str] = mapped_column(String(12), default="requested", index=True)   # requested | approved | declined | removed
    agreement: Mapped[str] = mapped_column(String(300))         # the signed agreement it rests on
    profile: Mapped[dict] = mapped_column(JSON, default=dict)   # what the client told us: experience, objective
    requested_by: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list] = mapped_column(JSON, default=list)


class SeniorHold(Base):
    __tablename__ = "senior_hold"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), index=True)
    basis: Mapped[str] = mapped_column(String(600))             # why the firm reasonably believes exploitation is happening
    adult_basis: Mapped[str] = mapped_column(String(16))        # age_65 | impairment
    placed_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(12), default="active", index=True)    # active | released | expired
    placed_by: Mapped[str] = mapped_column(String(200))
    steps: Mapped[list] = mapped_column(JSON, default=list)
