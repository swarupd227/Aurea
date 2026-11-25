"""What a person recorded for India's batch C items: how a mutual fund order travelled and which NAV day the RTA reports (B-294), the firm's group
entities and what they earn from an advised household, with a scheme's recorded expense ratios (B-297), and each adviser's NISM certificate and
the advertisement pre-approvals (B-298). Each is added and never edited: a correction, a renewal or a decision is a new row, and the latest for
its key stands. Nothing here moves money, units or a holding.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class MfOrderRecord(Base):
    """How one mutual fund order travelled, as the rail's and the RTA's own confirmation shows it (B-294).

    `order_on` and `order_time` are when the rail took the order (firm time, HH:MM). `cutoff_rule` is the rule the order was treated under, and
    `nav_date_reported` is the NAV day the RTA says it allotted at: both are recorded, never worked out. The latest row for an order stands."""

    __tablename__ = "mf_order_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"), index=True)
    rail: Mapped[str] = mapped_column(String(16))              # bse_star_mf | nse_nmf | mfcentral | amc_direct | other
    rta: Mapped[str] = mapped_column(String(10))               # cams | kfintech
    folio: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cutoff_rule: Mapped[str] = mapped_column(String(20))       # standard | liquid_overnight
    order_on: Mapped[date] = mapped_column(Date)
    order_time: Mapped[str] = mapped_column(String(5))
    funds_realised_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    nav_date_reported: Mapped[date] = mapped_column(Date)
    amount_inr: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class GroupEntity(Base):
    """A company in the firm's own group, from a document (B-297). `kind` is `registered` or `ended`; the latest row for a name stands.
    `arn` is the AMFI registration as the document states it, kept as text and never checked against AMFI."""

    __tablename__ = "group_entity"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    relationship_to_firm: Mapped[str] = mapped_column(String(16))   # parent | subsidiary | associate | sister | other
    holds_arn: Mapped[bool] = mapped_column(Boolean, default=False)
    arn: Mapped[str | None] = mapped_column(String(40), nullable=True)
    kind: Mapped[str] = mapped_column(String(10), default="registered")
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class GroupTrail(Base):
    """Compliance's record that a group entity earns trail from a household, from a statement (B-297). `kind` is `earns` or `ceased`;
    the latest row for a household and entity stands. A fact recorded, never inferred from a holding."""

    __tablename__ = "group_trail"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    entity_name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(8), default="earns")
    statement_on: Mapped[date] = mapped_column(Date)
    amount_inr: Mapped[float | None] = mapped_column(Numeric(18, 2), nullable=True)
    source_document: Mapped[str] = mapped_column(String(300))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class SchemeTer(Base):
    """A scheme's total expense ratio for one plan, read from the scheme document (B-297). The latest row for a scheme and plan stands."""

    __tablename__ = "scheme_ter"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    scheme: Mapped[str] = mapped_column(String(200), index=True)
    plan: Mapped[str] = mapped_column(String(8))               # direct | regular
    ter_pct: Mapped[float] = mapped_column(Numeric(6, 3))
    as_of: Mapped[date] = mapped_column(Date)
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AdviserCertification(Base):
    """One NISM certificate held by one of the firm's people, from the certificate (B-298). `status` is as the certificate or the NISM
    record reports it; a renewal is a new row for the same person and kind, and the latest stands."""

    __tablename__ = "adviser_certification"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))               # nism_xa | nism_xb | nism_va
    certificate_number: Mapped[str] = mapped_column(String(60))
    issued_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="valid")   # valid | lapsed | revoked, as the certificate reports
    source_document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AdSubmission(Base):
    """A piece of marketing copy put to compliance before it is used (B-298). The text is kept as submitted with its hash; a change is a new
    submission, never an edit. `supersedes_id` names the submission a resubmission follows."""

    __tablename__ = "ad_submission"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    channel: Mapped[str] = mapped_column(String(20))
    body: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64), index=True)
    submitted_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    submitted_by: Mapped[str] = mapped_column(String(200))
    findings_blocks: Mapped[int] = mapped_column(Integer, default=0)
    findings_flags: Mapped[int] = mapped_column(Integer, default=0)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ad_submission.id", ondelete="SET NULL"), nullable=True)


class AdDecision(Base):
    """Compliance's approval or rejection of one submission, with its reason (B-298). One per submission, added and never edited. The id of an
    approval is what a message, report or research item cites; `text_hash` is the approved wording's."""

    __tablename__ = "ad_decision"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    submission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ad_submission.id", ondelete="CASCADE"), index=True)
    decision: Mapped[str] = mapped_column(String(10))          # approved | rejected
    reason: Mapped[str] = mapped_column(Text)
    text_hash: Mapped[str] = mapped_column(String(64))
    decided_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("app_user.id", ondelete="CASCADE"))
    decided_by: Mapped[str] = mapped_column(String(200))
