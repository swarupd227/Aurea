"""Compliance assessment record — the cited, versioned result of running a recommendation through
the regulatory rules engine. One row per assessment, linked to the recommendation and run, so any
decision can be proven compliant against a specific framework version (FMC Act s446 record-keeping)."""
from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ComplianceCheck(Base):
    __tablename__ = "compliance_check"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation.id", ondelete="CASCADE"), nullable=True, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    agent_key: Mapped[str] = mapped_column(String(48), index=True)
    regime: Mapped[str] = mapped_column(String(16))            # NZ-FMA / UK-FCA / AU-ASIC
    framework_version: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), index=True)  # clear | flags | blocked
    # [{rule_id, code, citation, title, category, severity, status, finding}, ...]
    results: Mapped[list] = mapped_column(JSON, default=list)
