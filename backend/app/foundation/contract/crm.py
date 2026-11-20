"""The CRM: contacts, their opportunities and the activity logged against them.

A person is named by email (an owner, who logged an activity), because the CRM's own user ids mean nothing here. A
contact's conversion to a household and a deal's onboarding case are Astra's records, not the CRM's."""
from __future__ import annotations

from datetime import date, datetime

from app.foundation.contract.base import Record


class Contact(Record):
    entity = "crm_contact"
    domain = "crm"

    full_name: str
    email: str | None = None
    phone: str | None = None
    contact_type: str = "prospect"
    lead_source: str | None = None
    notes: str | None = None
    owner_email: str | None = None
    is_active: bool = True


class Opportunity(Record):
    entity = "crm_opportunity"
    domain = "crm"

    contact_ref: str
    title: str
    stage: str = "lead"
    estimated_aum: float | None = None
    # What someone said the chance is; none said is none, never a default.
    probability_pct: int | None = None
    expected_close_date: date | None = None
    lost_reason: str | None = None
    opened_at: datetime | None = None
    closed_at: datetime | None = None


class Activity(Record):
    entity = "crm_activity"
    domain = "crm"

    contact_ref: str
    opportunity_ref: str | None = None
    activity_type: str = "note"
    occurred_at: datetime
    detail: str
    logged_by_email: str | None = None
