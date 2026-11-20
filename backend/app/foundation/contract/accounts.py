"""Accounts and the mandates they are managed under.

What Astra decides about a mandate stays Astra's and is not in the contract: its fee schedule (billing) and an exit
hold (aurea_core/exits.py). A mandate names its model by the model's name, because a model portfolio is the portfolio
team's record here, not the Foundation's."""
from __future__ import annotations

from datetime import date

from app.foundation.contract.base import Record


class Mandate(Record):
    entity = "mandate"
    domain = "accounts"

    person_ref: str | None = None
    entity_ref: str | None = None
    name: str
    mandate_type: str
    suitability: dict = {}
    constraints: dict = {}
    model_portfolio: str | None = None
    is_active: bool = True


class Account(Record):
    entity = "account"
    domain = "accounts"

    mandate_ref: str | None = None
    name: str
    account_number: str | None = None
    custodian: str | None = None
    currency: str = "NZD"
    cash_balance: float = 0.0
    registration_type: str | None = None
    status: str = "active"
    closed_on: date | None = None
