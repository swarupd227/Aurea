"""AI-usage telemetry — one row per model call, for token-cost governance & ROI (Foundation
pillars 'Model gateway' and 'Telemetry & ROI')."""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class LlmUsage(Base):
    __tablename__ = "llm_usage"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(48), index=True)  # agent key or 'ask' / 'assistant'
    task: Mapped[str] = mapped_column(String(24))               # advice / narrative / classify
    model: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(24))           # anthropic / openai / none(fallback)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost: Mapped[float] = mapped_column(Float, default=0.0)  # USD, indicative
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False)
    redacted_count: Mapped[int] = mapped_column(Integer, default=0)
