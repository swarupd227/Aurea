"""The Client State Graph (spec §6.1, Table 7).

A client's world is modelled as a connected graph — people, households, legal entities,
mandates, accounts, goals — joined by typed relationship edges, not as isolated logins."""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.models.enums import ClientSegment, EntityType, MandateType


class Household(Base):
    """Links people who plan together (spec: 'advice is rarely about one account')."""

    __tablename__ = "household"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    segment: Mapped[ClientSegment] = mapped_column(String(32), default=ClientSegment.PRIVATE_WEALTH)
    # Shared values / impact objectives, used for values-aligned reporting (spec §9).
    values: Mapped[dict] = mapped_column(JSON, default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    persons: Mapped[list["Person"]] = relationship(back_populates="household")


class Person(Base):
    """The unit of relationship and suitability (spec Table 7)."""

    __tablename__ = "person"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200))
    preferred_name: Mapped[str | None] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(254))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    segment: Mapped[ClientSegment] = mapped_column(String(32), default=ClientSegment.PRIVATE_WEALTH)

    # KYC / AML status object: {"status":"verified","aml_screened":true,"as_of":"..."}.
    kyc: Mapped[dict] = mapped_column(JSON, default=dict)
    # Stated preferences, values, life-stage signals.
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    is_next_gen: Mapped[bool] = mapped_column(Boolean, default=False)

    household: Mapped["Household | None"] = relationship(back_populates="persons")


class LegalEntity(Base):
    """Trusts, foundations, for-purpose & indigenous entities — first-class (spec §4)."""

    __tablename__ = "legal_entity"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    entity_type: Mapped[EntityType] = mapped_column(String(24))
    # Governance: trustees, settlor, beneficiaries, decision rules.
    governance: Mapped[dict] = mapped_column(JSON, default=dict)
    # Mandate / impact objectives for for-purpose entities.
    impact_objectives: Mapped[dict] = mapped_column(JSON, default=dict)
    jurisdiction: Mapped[str] = mapped_column(String(8), default="NZ")


class Mandate(Base):
    """Determines what an agent may do autonomously (spec Table 7).

    Owned by exactly one of person / entity (the holder of the advisory relationship)."""

    __tablename__ = "mandate"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="CASCADE"), nullable=True
    )
    entity_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    mandate_type: Mapped[MandateType] = mapped_column(String(24))
    # Suitability profile: risk tolerance, capacity, constraints, ESG/values exclusions.
    suitability: Mapped[dict] = mapped_column(JSON, default=dict)
    # Scope/constraints: asset-class limits, CGT budget, concentration caps.
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    # The target model this mandate is managed against.
    model_portfolio_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("model_portfolio.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    accounts: Mapped[list["Account"]] = relationship(back_populates="mandate")


class Account(Base):
    """A portfolio/account holding positions at a custodian (spec Table 7)."""

    __tablename__ = "account"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("mandate.id", ondelete="SET NULL"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    account_number: Mapped[str | None] = mapped_column(String(64))
    custodian: Mapped[str | None] = mapped_column(String(120))  # source system / connector
    currency: Mapped[str] = mapped_column(String(3), default="NZD")
    cash_balance: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    # Lineage for the account-level figures (source connector, as_of, confidence).
    lineage: Mapped[dict] = mapped_column(JSON, default=dict)

    mandate: Mapped["Mandate | None"] = relationship(back_populates="accounts")
    holdings: Mapped[list["Holding"]] = relationship(back_populates="account")


class Goal(Base):
    """Turns performance into 'am I on track?' (spec Table 7, §9)."""

    __tablename__ = "goal"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("household.id", ondelete="SET NULL"), nullable=True, index=True
    )
    person_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(48), default="retirement")  # retirement/education/legacy
    target_amount: Mapped[float] = mapped_column(Numeric(20, 2), default=0)
    target_date: Mapped[date | None] = mapped_column(Date)
    # Decumulation / longevity assumptions.
    assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(default=1)


class RelationshipEdge(Base):
    """Typed edges between graph nodes — powers retention across the wealth transfer."""

    __tablename__ = "relationship_edge"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # RelationshipKind value
    # Generic endpoints by (node_type, node_id) so any node can connect to any node.
    from_type: Mapped[str] = mapped_column(String(24))  # person|entity|user|household
    from_id: Mapped[uuid.UUID] = mapped_column()
    to_type: Mapped[str] = mapped_column(String(24))
    to_id: Mapped[uuid.UUID] = mapped_column()
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
