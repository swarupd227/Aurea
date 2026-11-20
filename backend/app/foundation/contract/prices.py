"""Prices: instruments and their prices, each price with the feed it came from and whether it is real."""
from __future__ import annotations

from datetime import date

from app.foundation.contract.base import Record


class Instrument(Record):
    entity = "instrument"
    domain = "prices"

    symbol: str
    name: str
    asset_class: str
    market_type: str = "public"
    currency: str = "USD"
    market_symbol: str | None = None
    values_tags: dict = {}
    private_attributes: dict = {}


class Price(Record):
    entity = "price"
    domain = "prices"

    symbol: str
    price_date: date
    close: float
    currency: str = "USD"
    feed: str | None = None
    # Nothing fills on a synthetic price: a price that is not real says so, wherever it came from.
    is_real: bool = False
