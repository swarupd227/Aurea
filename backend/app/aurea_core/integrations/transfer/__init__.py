"""Transfer tracking adapter factory.

Active provider is selected from the environment:
  AUREA_TRANSFER_PROVIDER=mock           (default — in-memory state machine)
  AUREA_TRANSFER_PROVIDER=dtcc_nscc      (real DTCC ACAT — implement transfer/dtcc_nscc.py)
  AUREA_TRANSFER_PROVIDER=schwab_wire    (real Schwab wire — implement transfer/schwab_wire.py)

Per-firm override: set ``firm.integrations_config["transfer_provider"]`` in the DB.
"""
from __future__ import annotations

import os

from .base import (
    TERMINAL_STATUSES,
    TRANSFER_STATUSES,
    TransferAdapter,
    TransferStatusResult,
    TransferSubmitResult,
)
from .mock import MockTransferAdapter

# ── Provider registry ─────────────────────────────────────────────────────────
_REGISTRY: dict[str, type[TransferAdapter]] = {
    "mock": MockTransferAdapter,
    # "dtcc_nscc":   DTCCNSCCAdapter,     # implement transfer/dtcc_nscc.py
    # "schwab_wire": SchwabWireAdapter,   # implement transfer/schwab_wire.py
    # "plaid_ach":   PlaidACHAdapter,     # implement transfer/plaid_ach.py
}

_DEFAULT_PROVIDER = os.getenv("AUREA_TRANSFER_PROVIDER", "mock")


def get_adapter(provider: str | None = None) -> TransferAdapter:
    """Return an initialised TransferAdapter for the given (or default) provider."""
    name = provider or _DEFAULT_PROVIDER
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown transfer provider '{name}'. "
            f"Available: {sorted(_REGISTRY)}. "
            "Set AUREA_TRANSFER_PROVIDER or implement a new TransferAdapter subclass."
        )
    return cls()


__all__ = [
    "TERMINAL_STATUSES", "TRANSFER_STATUSES",
    "TransferAdapter", "TransferStatusResult", "TransferSubmitResult",
    "get_adapter",
]
