"""A client's KYC as a person recorded it from the document or the KRA's acknowledgement (India row 12).

Added and never edited: a new record is a new row and the latest for the party stands, so a re-KYC does not erase the one
before it. Nothing is fetched from a KRA or CKYC registry; a fact not given is None and reads "not assessed". An Aadhaar
number is never stored: only its last four digits, as the client's own document shows them.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class KycRecord(Base):
    __tablename__ = "kyc_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("legal_entity.id", ondelete="CASCADE"), nullable=True, index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("onboarding_case.id", ondelete="SET NULL"), nullable=True, index=True)
    party_name: Mapped[str] = mapped_column(String(200))
    ckyc_number: Mapped[str | None] = mapped_column(String(14), nullable=True)          # the 14-digit KIN, as the registry sent it
    kra_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    kra_status: Mapped[str | None] = mapped_column(String(12), nullable=True)           # validated | registered | on_hold | rejected
    kyc_completed_on: Mapped[date | None] = mapped_column(Date, nullable=True)          # the KYC or re-KYC this record rests on
    verification_mode: Mapped[str | None] = mapped_column(String(14), nullable=True)    # ipv | video_kyc | aadhaar_ekyc | digilocker
    ipv_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    aadhaar_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)         # never the whole number
    aadhaar_xml_dated: Mapped[date | None] = mapped_column(Date, nullable=True)         # the day the XML or Secure QR was generated
    pan_aadhaar_linked: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    id_proof_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    id_proof_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_proof_kind: Mapped[str | None] = mapped_column(String(24), nullable=True)
    address_proof_dated: Mapped[date | None] = mapped_column(Date, nullable=True)       # a utility bill's date
    address_proof_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    risk_band: Mapped[str | None] = mapped_column(String(8), nullable=True)             # low | medium | high, as the firm rated it
    pep: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    structure: Mapped[str | None] = mapped_column(String(16), nullable=True)            # company | partnership | unincorporated | trust | huf
    beneficial_owners: Mapped[list] = mapped_column(JSON, default=list)                 # [{name, share, role, kyc_on_file}]
    triggers: Mapped[list] = mapped_column(JSON, default=list)                          # [{kind, on}] since the KYC, as recorded
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
