"""Activity: what happened in an account. Cash is the account's balance, carried on the account record."""
from __future__ import annotations

from datetime import date

from app.foundation.contract.base import Record


class Transaction(Record):
    entity = "txn"
    domain = "activity"

    account_ref: str
    symbol: str | None = None
    txn_type: str
    quantity: float = 0.0
    price: float = 0.0
    amount: float = 0.0
    trade_date: date
    # What the source says about the trade beyond its figures; a sale's realised gain lives here (wash_sale.py).
    lineage: dict = {}
