"""What a person recorded about a client's residence, and who runs a minor's account (India row 7).

Both are added and never edited: a change is a new row, and the latest stands. Nothing here is worked out from where a
client lives, travels or banks. A residency record carries the facts of one tax year's test and the person's FEMA
status, FATCA and CRS declarations; a fact not given is None and reads "not assessed", never false.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import JSON, Boolean, Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ResidencyRecord(Base):
    __tablename__ = "residency_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    tax_year: Mapped[str] = mapped_column(String(9))                       # "2026-27"
    # The residency test's facts for that tax year, as a person read them from a passport stamp, a travel record or a return.
    days_in_india: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_prior_four_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    days_prior_seven_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    nonresident_years_of_prior_ten: Mapped[int | None] = mapped_column(Integer, nullable=True)
    indian_citizen_or_pio: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    left_for_employment_abroad: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    india_income: Mapped[float | None] = mapped_column(Numeric(16, 2), nullable=True)   # other than from foreign sources, INR
    taxed_elsewhere: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # FEMA and immigration status: nri | oci | resident | foreign_national. A different question from tax residence.
    fema_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country_of_residence: Mapped[str | None] = mapped_column(String(80), nullable=True)
    accounts: Mapped[list] = mapped_column(JSON, default=list)             # NRE, NRO, FCNR the client holds, as recorded
    pis_permission: Mapped[str | None] = mapped_column(String(200), nullable=True)   # the designated bank's letter, as filed
    # FATCA and CRS: the client's own declaration, from the signed form.
    tax_residencies: Mapped[list] = mapped_column(JSON, default=list)
    us_person: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    us_indicia: Mapped[list] = mapped_column(JSON, default=list)
    self_certified_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    document: Mapped[str] = mapped_column(String(300))                     # where it is filed
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class GuardianshipRecord(Base):
    __tablename__ = "guardianship_record"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    minor_person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), index=True)
    guardian_person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"), nullable=True)
    guardian_name: Mapped[str] = mapped_column(String(200))
    relationship: Mapped[str] = mapped_column(String(24))                  # father | mother | legal_guardian | court_appointed
    document: Mapped[str] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(String(200))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
