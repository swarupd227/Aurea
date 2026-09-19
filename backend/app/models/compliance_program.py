"""The compliance program as governed artifacts, not prose (L200-7 §1-3).

L200-7's organizing thesis: "modern compliance is an evidence factory... every obligation
must terminate in a named artifact produced by a repeatable process." Two artifacts from
that module, neither of which the rules engine (`app.compliance.ontology`) already covers,
because that engine evaluates recommendations against rules — it does not track who owns a
conflict, how it is mitigated, or which supervisory activity produces evidence for which
obligation on what cadence.

`ConflictInventoryItem` is L200-7 §2.2's "modern organizing artifact" — every conflict a
firm has identified, the mechanism by which it could harm a client, how the firm mitigates
and discloses it, and who owns keeping that answer current.

`WSPRule` is L200-7 §3.1's written-supervisory-procedures grid: rule/obligation →
designated principal → supervisory activity → frequency → evidence produced. The grid is
what makes an obligation testable (FINRA 3120) and certifiable (3130) rather than a
sentence in a manual nobody re-reads.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

CONFLICT_STATUSES = ("active", "retired")


class ConflictInventoryItem(Base):
    """One row of the conflicts inventory (L200-7 §2.2)."""

    __tablename__ = "conflict_inventory_item"
    __table_args__ = (UniqueConstraint("firm_id", "conflict_key", name="uq_firm_conflict"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    conflict_key: Mapped[str] = mapped_column(String(64))          # slug, e.g. "revenue_sharing"
    title: Mapped[str] = mapped_column(String(200))
    mechanism_of_harm: Mapped[str] = mapped_column(Text)
    mitigation: Mapped[str] = mapped_column(Text)
    disclosure: Mapped[str] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)   # role or named owner
    status: Mapped[str] = mapped_column(String(16), default="active")       # active | retired
    # Refreshed "on product/comp changes and annually" — L200-7 §2.2.
    review_cadence_days: Mapped[int] = mapped_column(Integer, default=365)
    last_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class WSPRule(Base):
    """One row of the written-supervisory-procedures grid (L200-7 §3.1)."""

    __tablename__ = "wsp_rule"
    __table_args__ = (UniqueConstraint("firm_id", "rule_key", name="uq_firm_wsp_rule"),)

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    rule_key: Mapped[str] = mapped_column(String(64))               # e.g. "finra.2111.suitability"
    # Optional link to a coded rule in the regulatory ontology (app.compliance.ontology) —
    # a WSP row does not require one, since firms also supervise activities the deterministic
    # rules engine never evaluates (correspondence sampling, branch inspections, ...).
    ontology_rule_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    obligation: Mapped[str] = mapped_column(String(300))            # plain description + citation
    designated_role: Mapped[str] = mapped_column(String(120))       # e.g. "Branch Manager (S24)"
    supervisory_activity: Mapped[str] = mapped_column(Text)
    frequency: Mapped[str] = mapped_column(String(32))              # daily|weekly|monthly|quarterly|annual|per_event
    evidence_description: Mapped[str] = mapped_column(Text)
    # Whether evidence_description names an artifact the platform produces automatically
    # (a ledger entry, a system report) vs. one a person must attest to by hand — the exact
    # split L200-7 §10's "Evidence coverage" metric measures.
    evidence_automated: Mapped[bool] = mapped_column(Boolean, default=False)
    last_evidence_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    change_log: Mapped[list] = mapped_column(JSON, default=list)    # [{at, by, change}, ...] — §3.1's version history
