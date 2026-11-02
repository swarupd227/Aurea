"""Transaction monitoring decisions and investigations (L200-1 §5.5).

An alert is worked out from the money movements each time it is read; what is stored is a person's decision on it,
by its key, and the investigation it opened. Neither is ever shown to a client.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AlertDecision(Base):
    __tablename__ = "aml_alert_decision"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    alert_key: Mapped[str] = mapped_column(String(120), index=True)
    scenario: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(16))          # closed | escalated
    reason: Mapped[str] = mapped_column(String(500))
    facts: Mapped[dict] = mapped_column(JSON, default=dict)     # the alert as it stood when decided
    investigation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aml_investigation.id", ondelete="SET NULL"), nullable=True)
    decided_by: Mapped[str] = mapped_column(String(200))


class AmlInvestigation(Base):
    __tablename__ = "aml_investigation"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    detected_on: Mapped[date] = mapped_column(Date)            # the first alert's date: the US clock starts here
    subject_known: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)   # open | reported | closed
    suspicion_on: Mapped[date | None] = mapped_column(Date)    # when suspicion was formed: NZ and India start here
    reported_on: Mapped[date | None] = mapped_column(Date)
    report_reference: Mapped[str | None] = mapped_column(String(80))
    steps: Mapped[list] = mapped_column(JSON, default=list)     # every step, added and never edited
    opened_by: Mapped[str] = mapped_column(String(200))
