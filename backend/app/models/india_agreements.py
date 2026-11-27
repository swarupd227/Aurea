"""India's agreements, disclosures and the NRI checklist (B-391). Every row is what a person read off a document: added
and never edited, the latest standing. Nothing here moves money, places an order or files anything.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class FundAcceptance(Base):
    """Whether a fund house, or one of its schemes, takes a resident of the United States or Canada, and on what terms,
    as a person recorded it from the fund house's document."""
    __tablename__ = "fund_acceptance"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    fund_house: Mapped[str] = mapped_column(String(120))
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)      # one scheme; empty for the whole house
    country: Mapped[str] = mapped_column(String(2))                            # US | CA
    stance: Mapped[str] = mapped_column(String(16))                            # accepts | conditions | refuses
    conditions: Mapped[str | None] = mapped_column(Text, nullable=True)
    stated_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class MfOrderEuin(Base):
    """The distributor employee code a mutual fund transaction carried, or the investor's execution-only declaration."""
    __tablename__ = "mf_order_euin"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("order.id", ondelete="CASCADE"), index=True)
    euin: Mapped[str | None] = mapped_column(String(12), nullable=True)
    arn: Mapped[str | None] = mapped_column(String(20), nullable=True)
    execution_only: Mapped[bool] = mapped_column(Boolean, default=False)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class SchemeDocument(Base):
    """A scheme's information document (SID) or key information memorandum (KIM) the firm holds."""
    __tablename__ = "mf_scheme_document"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))                               # sid | kim
    dated_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))                         # where it is filed
    recorded_by: Mapped[str] = mapped_column(String(200))


class DistributionDisclosure(Base):
    """What a client was given about a scheme: the commission disclosure, or the scheme documents themselves."""
    __tablename__ = "distribution_disclosure"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("instrument.id", ondelete="CASCADE"), nullable=True)
    fund_house: Mapped[str | None] = mapped_column(String(120), nullable=True) # a commission stated for a house, or the scheme's house
    kind: Mapped[str] = mapped_column(String(20))                              # commission | scheme_documents
    terms: Mapped[str | None] = mapped_column(Text, nullable=True)             # the commission as the disclosure states it
    items: Mapped[list] = mapped_column(JSON, default=list)                    # ["sid", "kim"] for scheme documents
    method: Mapped[str] = mapped_column(String(16))                            # email | portal | in_person | post | with_application
    given_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AgreementItem(Base):
    """One item of a Portfolio Manager's, an Alternative Investment Fund's or a broking and demat relationship's
    paperwork, answered from the document."""
    __tablename__ = "agreement_item"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    mandate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("mandate.id", ondelete="CASCADE"), nullable=True)
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"), nullable=True)
    commitment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("aif_commitment.id", ondelete="CASCADE"), nullable=True)
    relationship_kind: Mapped[str] = mapped_column(String(12))                 # pms | aif | broking
    item: Mapped[str] = mapped_column(String(40))
    answer: Mapped[str] = mapped_column(String(16))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    dated_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AifCommitment(Base):
    """What a client committed to an Alternative Investment Fund, from the contribution agreement."""
    __tablename__ = "aif_commitment"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="SET NULL"), nullable=True)
    investor_name: Mapped[str] = mapped_column(String(200))
    fund_name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(8))                           # aif_1 | aif_2 | aif_3
    commitment: Mapped[float] = mapped_column(Numeric(16, 2))                  # INR
    committed_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class AifDrawdown(Base):
    """A fund's call on the commitment, from its drawdown notice. Unpaid, it is a task."""
    __tablename__ = "aif_drawdown"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    commitment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aif_commitment.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    amount: Mapped[float] = mapped_column(Numeric(16, 2))
    notice_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("task.id", ondelete="SET NULL"), nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class AifPayment(Base):
    """A payment against a drawdown, from the bank's advice or the fund's receipt. It moves no money here."""
    __tablename__ = "aif_drawdown_payment"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    drawdown_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("aif_drawdown.id", ondelete="CASCADE"), index=True)
    amount: Mapped[float] = mapped_column(Numeric(16, 2))
    paid_on: Mapped[date] = mapped_column(Date)
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))


class EStampRecord(Base):
    """An e-stamp certificate on an instrument, as the certificate states it. Nothing here pays or verifies duty."""
    __tablename__ = "estamp_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    instrument_kind: Mapped[str] = mapped_column(String(32))
    certificate_number: Mapped[str] = mapped_column(String(60))
    state: Mapped[str] = mapped_column(String(60))
    duty: Mapped[float] = mapped_column(Numeric(14, 2))
    stamped_on: Mapped[date] = mapped_column(Date)
    parties: Mapped[str] = mapped_column(String(300))
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
