"""The consolidation workbench, India (B-389): what a person recorded about money held elsewhere while bringing it together.

Four records, each added and never edited:

- `ConsolidationFacts`: what a person read off a document about one holding that the statement line does not carry - the
  lots bought (units, cost, date), its tax class, the exit load in the scheme document, the distributor code on the folio,
  whether it sits in a folio or a demat account. The latest record that states a fact stands for it.
- `ConsolidationDecision`: an adviser's classification of a holding (keep and re-map, transfer, exit) with its reason; for
  an exit, the reason about the product and the planned day. The latest stands.
- `ConsolidationItem`: one tracked piece of work - a distributor code change on a folio, an off-market demat transfer, a
  Portfolio Manager transition in kind, or a taxable exit.
- `ConsolidationStep`: each thing that happened to an item, from its document. Where an item stands is its latest step.

A holding is named by `holding_key`, worked from its owner, institution, account and ISIN or name, so a newer statement
that replaces a line keeps what was recorded about the holding. Nothing here holds, moves or trades anything.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ConsolidationFacts(Base):
    __tablename__ = "consolidation_facts"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    holding_key: Mapped[str] = mapped_column(String(40), index=True)
    tax_class: Mapped[str | None] = mapped_column(String(24), nullable=True)
    lots: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)     # [{units, cost_per_unit, acquired_on}]
    exit_load_rate: Mapped[float | None] = mapped_column(Numeric(8, 6), nullable=True)
    exit_load_days: Mapped[int | None] = mapped_column(Integer, nullable=True)            # charged on units held fewer days
    distributor_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    form: Mapped[str | None] = mapped_column(String(8), nullable=True)                    # folio | demat
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    recorded_on: Mapped[date] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(Integer, default=1)


class ConsolidationDecision(Base):
    __tablename__ = "consolidation_decision"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    holding_key: Mapped[str] = mapped_column(String(40), index=True)
    decision: Mapped[str] = mapped_column(String(12))                                     # keep_remap | transfer | exit
    reason: Mapped[str] = mapped_column(String(500))
    product_reason: Mapped[str | None] = mapped_column(String(24), nullable=True)         # an exit's reason about the product
    planned_on: Mapped[date | None] = mapped_column(Date, nullable=True)                  # an exit's planned day
    units: Mapped[float | None] = mapped_column(Numeric(24, 6), nullable=True)            # part of the holding; empty is all
    decided_by: Mapped[str] = mapped_column(String(200))
    decided_on: Mapped[date] = mapped_column(Date)
    seq: Mapped[int] = mapped_column(Integer, default=1)


class ConsolidationItem(Base):
    __tablename__ = "consolidation_item"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)         # code_change | demat_transfer | pms_transition | exit
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True)
    institution: Mapped[str] = mapped_column(String(120))
    account_label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    holding_keys: Mapped[list] = mapped_column(JSON, default=list)
    lines: Mapped[list] = mapped_column(JSON, default=list)           # the holdings as they stood when it was opened
    details: Mapped[dict] = mapped_column(JSON, default=dict)         # codes, accounts, reason code, consideration, managers
    opened_by: Mapped[str] = mapped_column(String(200))
    opened_on: Mapped[date] = mapped_column(Date)


class ConsolidationStep(Base):
    __tablename__ = "consolidation_step"
    __table_args__ = (UniqueConstraint("item_id", "seq", name="uq_consolidation_step_seq"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("consolidation_item.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    step: Mapped[str] = mapped_column(String(12))                     # requested|consented|submitted|confirmed|rejected|withdrawn
    happened_on: Mapped[date] = mapped_column(Date)
    evidence: Mapped[str] = mapped_column(String(300))
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    stamp_duty_stated: Mapped[float | None] = mapped_column(Numeric(20, 2), nullable=True)   # on a demat transfer, as the document states it
    recorded_by: Mapped[str] = mapped_column(String(200))
