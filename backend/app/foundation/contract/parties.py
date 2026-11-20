"""Parties: households, the people in them and the legal entities (trusts, companies, foundations) they plan with.

A PAN is not in the contract: it is recorded by a person from a document (aurea_core/pan.py)."""
from __future__ import annotations

from datetime import date

from app.foundation.contract.base import Record


class Household(Record):
    entity = "household"
    domain = "parties"

    name: str
    segment: str | None = None
    values: dict = {}
    notes: str | None = None
    review_cadence_months: int | None = None
    left_on: date | None = None


class Person(Record):
    entity = "person"
    domain = "parties"

    household_ref: str | None = None
    full_name: str
    preferred_name: str | None = None
    email: str | None = None
    date_of_birth: date | None = None
    segment: str | None = None
    kyc: dict = {}
    profile: dict = {}
    is_next_gen: bool = False
    deceased_on: date | None = None


class LegalEntity(Record):
    entity = "legal_entity"
    domain = "parties"

    household_ref: str | None = None
    name: str
    entity_type: str
    governance: dict = {}
    impact_objectives: dict = {}
    jurisdiction: str | None = None
