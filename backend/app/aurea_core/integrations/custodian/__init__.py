"""Custodian account-opening adapter factory.

Active custodian is selected from the environment:
  AUREA_CUSTODIAN_PROVIDER=mock_schwab   (default — no credentials needed)
  AUREA_CUSTODIAN_PROVIDER=schwab        (real Schwab API — implement custodian/schwab.py)
  AUREA_CUSTODIAN_PROVIDER=fidelity      (real Fidelity API — implement custodian/fidelity.py)
  AUREA_CUSTODIAN_PROVIDER=pershing      (real Pershing API — implement custodian/pershing.py)

Per-firm override: set ``firm.integrations_config["custodian_provider"]`` in the DB.
"""
from __future__ import annotations

import os

from .base import AccountOpenResult, CustodianAdapter
from .mock_schwab import MockSchwabAdapter

# ── Provider registry ─────────────────────────────────────────────────────────
_REGISTRY: dict[str, type[CustodianAdapter]] = {
    "mock_schwab": MockSchwabAdapter,
    # "schwab":    SchwabAdapter,       # implement custodian/schwab.py
    # "fidelity":  FidelityAdapter,     # implement custodian/fidelity.py
    # "pershing":  PershingAdapter,     # implement custodian/pershing.py
    # "apex":      ApexAdapter,         # implement custodian/apex.py
}

_DEFAULT_PROVIDER = os.getenv("AUREA_CUSTODIAN_PROVIDER", "mock_schwab")


def get_adapter(provider: str | None = None) -> CustodianAdapter:
    """Return an initialised CustodianAdapter for the given (or default) provider."""
    name = provider or _DEFAULT_PROVIDER
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown custodian provider '{name}'. "
            f"Available: {sorted(_REGISTRY)}. "
            "Set AUREA_CUSTODIAN_PROVIDER or implement a new CustodianAdapter subclass."
        )
    return cls()


__all__ = ["AccountOpenResult", "CustodianAdapter", "get_adapter"]
