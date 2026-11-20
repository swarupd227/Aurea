"""The canonical contract, version 1: one module per domain, so a firm can take its book from one product and its
CRM from another. Every record carries source, external_id, as_of and contract_version (contract/base.py)."""
from __future__ import annotations

from app.foundation.contract.accounts import Account, Mandate
from app.foundation.contract.activity import Transaction
from app.foundation.contract.base import CONTRACT_VERSION, Record
from app.foundation.contract.crm import Activity, Contact, Opportunity
from app.foundation.contract.parties import Household, LegalEntity, Person
from app.foundation.contract.positions import Holding, TaxLot
from app.foundation.contract.prices import Instrument, Price

# The domains, and their records in the order an import must take them (a record after those it refers to).
DOMAINS: dict[str, tuple[type[Record], ...]] = {
    "parties": (Household, Person, LegalEntity),
    "accounts": (Mandate, Account),
    "prices": (Instrument, Price),
    "positions": (Holding, TaxLot),
    "activity": (Transaction,),
    "crm": (Contact, Opportunity, Activity),
}
DOMAIN_ORDER = ("parties", "accounts", "prices", "positions", "activity", "crm")
RECORDS: dict[str, type[Record]] = {cls.entity: cls for classes in DOMAINS.values() for cls in classes}

__all__ = ["CONTRACT_VERSION", "DOMAINS", "DOMAIN_ORDER", "RECORDS", "Record", "Household", "Person", "LegalEntity",
           "Mandate", "Account", "Holding", "TaxLot", "Transaction", "Instrument", "Price", "Contact", "Opportunity",
           "Activity"]
