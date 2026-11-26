"""What the KYC registry answered, and what was done about it (B-386).

A `KycLookup` is the registry's answer on one day: recorded by a person from the answer, or returned by a connected lookup
(only a simulated one exists, tagged `simulated:<key>`). A `KycFollowUp` is one step of putting an on-hold or rejected record
right, or one attempt to reach the client. Both are added and never edited: the latest stands. Nothing here changes a KYC
record, an account or an order.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class KycLookup(Base):
    __tablename__ = "kyc_lookup"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="SET NULL"), nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String(200))
    registry: Mapped[str] = mapped_column(String(8))                       # kra | ckyc
    status: Mapped[str] = mapped_column(String(12))                        # validated | registered | on_hold | rejected | not_found
    agency: Mapped[str | None] = mapped_column(String(60), nullable=True)
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    answered_on: Mapped[date] = mapped_column(Date)
    deficiencies: Mapped[list] = mapped_column(JSON, default=list)         # coded reasons (aurea_core.kyc_registry.DEFICIENCIES)
    deficiency_note: Mapped[str | None] = mapped_column(String(300), nullable=True)  # the registry's words: staff only
    holds: Mapped[list] = mapped_column(JSON, default=list)                # documents the answer says the registry holds
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)


class KycFollowUp(Base):
    __tablename__ = "kyc_follow_up"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="SET NULL"), nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8))                           # step | contact
    lookup_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("kyc_lookup.id", ondelete="SET NULL"), nullable=True, index=True)
    step: Mapped[str | None] = mapped_column(String(32), nullable=True)    # the deficiency a step answers
    state: Mapped[str | None] = mapped_column(String(12), nullable=True)   # open | asked | received | done | not_needed
    channel: Mapped[str | None] = mapped_column(String(12), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(16), nullable=True)
    happened_on: Mapped[date] = mapped_column(Date)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(40), nullable=True)
