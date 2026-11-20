"""The adapters built here, by key. An adapter's key is its connector's provider key (``crm.dynamics``), so a
firm's connector is the adapter's configuration; ``native`` is Astra's own tables and has none."""
from __future__ import annotations

from app.foundation.adapters.base import Adapter, AdapterError, Pulled
from app.foundation.adapters.dynamics import DynamicsAdapter
from app.foundation.adapters.native import NativeAdapter

ADAPTERS: dict[str, Adapter] = {a.key: a for a in (NativeAdapter(), DynamicsAdapter())}


def get_adapter(key: str | None) -> Adapter | None:
    return ADAPTERS.get(str(key or ""))


def register(adapter: Adapter) -> None:
    """Add an adapter (a test's, or a new product's once it is built)."""
    ADAPTERS[adapter.key] = adapter


def for_domain(domain: str) -> list[Adapter]:
    """The adapters that can own a domain, native first."""
    return [a for a in ADAPTERS.values() if a.serves(domain)]


__all__ = ["ADAPTERS", "Adapter", "AdapterError", "Pulled", "get_adapter", "register", "for_domain"]
