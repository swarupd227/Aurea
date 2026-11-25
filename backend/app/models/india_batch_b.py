"""Languages, WhatsApp and the India Stack seams (L100 India edition, B-301 and B-302).

Everything here is added and never edited: a later row stands over an earlier one, so the record shows what stood on any day.
Nothing is translated, delivered or fetched by these tables on their own: a language is what a person recorded from the client's
request, a template is what compliance recorded with the provider's approval reference, a delivery row is what the channel
reported, and a stack item is what a provider delivered for a person to confirm. `source_system` is `simulated:<key>` for a row a
simulation wrote, and None for a real one.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class ClientLanguage(Base):
    """The language a household reads, as an adviser recorded it from the client's request (B-301). Latest stands."""

    __tablename__ = "client_language"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    language: Mapped[str] = mapped_column(String(4))                  # a code in aurea_core.client_language.LANGUAGES
    asked_by: Mapped[str] = mapped_column(String(200))               # who in the household asked
    evidence: Mapped[str] = mapped_column(String(300))               # where the request is: the message, the call, the form
    recorded_by: Mapped[str] = mapped_column(String(200))


class WhatsappTemplate(Base):
    """A WhatsApp template compliance recorded with its exact wording and the provider's approval reference (B-301).
    `state` is approved or withdrawn; the latest row for a name and language stands."""

    __tablename__ = "whatsapp_template"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    language: Mapped[str] = mapped_column(String(4))
    body: Mapped[str] = mapped_column(Text)                          # the approved wording; {name} is the household's name, the only variable
    provider_ref: Mapped[str] = mapped_column(String(120))           # the WhatsApp Business account's approval reference
    state: Mapped[str] = mapped_column(String(10), default="approved")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))


class MessageDelivery(Base):
    """One thing that happened to a message on a channel (B-301). `event` is `submitted` (what a person confirmed: both texts, the
    language, the template), `status` (what the channel reported: sent, delivered, read, failed) or `inbound` (the client wrote on the
    channel, which opens WhatsApp's 24-hour window; no words are kept)."""

    __tablename__ = "message_delivery"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("message.id", ondelete="SET NULL"), nullable=True, index=True)
    event: Mapped[str] = mapped_column(String(10))
    channel: Mapped[str] = mapped_column(String(10), default="portal")        # portal | whatsapp
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(120), nullable=True)
    happened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(4), nullable=True)
    translation_state: Mapped[str | None] = mapped_column(String(16), nullable=True)   # written | seam | not_connected | english
    english_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    template_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    recorded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_system: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)


class StackItem(Base):
    """What a DigiLocker pull or an eSign provider delivered, for a person to confirm into a record (B-302).

    kind `digilocker`: a document's reference (issuer, type, masked number, the day it was fetched). kind `esign`: an envelope
    (the document, the signer, its state as the provider reports it). `state` is offered (a provider delivered it), recorded (a
    person confirmed it into the KYC record or the consent registry; `of_item_id` names the offer) or dismissed. A full Aadhaar number
    is never kept: only the last four, masked."""

    __tablename__ = "india_stack_item"

    firm_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("firm.id", ondelete="CASCADE"), index=True)
    household_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("household.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("person.id", ondelete="CASCADE"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(10))
    state: Mapped[str] = mapped_column(String(10), default="offered")
    of_item_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True, index=True)
    provider: Mapped[str] = mapped_column(String(80))
    reference: Mapped[str] = mapped_column(String(160))              # the DigiLocker URI or the eSign envelope id
    issuer: Mapped[str | None] = mapped_column(String(160), nullable=True)
    doc_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    masked_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fetched_on: Mapped[date | None] = mapped_column(Date, nullable=True)      # the day the document was fetched, or the envelope signed
    document_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    signer: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider_state: Mapped[str | None] = mapped_column(String(12), nullable=True)   # an envelope: sent | signed | declined
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(200))
    source_system: Mapped[str | None] = mapped_column(String(60), nullable=True, index=True)
