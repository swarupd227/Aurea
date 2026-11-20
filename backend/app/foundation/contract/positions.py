"""Positions: holdings and the tax lots under them. An instrument is named by its symbol, which is unique in a firm."""
from __future__ import annotations

from datetime import date

from app.foundation.contract.base import Record


class Holding(Record):
    entity = "holding"
    domain = "positions"

    account_ref: str
    symbol: str
    quantity: float
    market_value: float = 0.0
    cost_basis: float = 0.0
    confidence: float = 1.0


class TaxLot(Record):
    entity = "tax_lot"
    domain = "positions"

    holding_ref: str
    quantity: float
    cost_per_unit: float
    acquired_on: date
