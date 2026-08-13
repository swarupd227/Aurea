"""Identity verification adapter factory.

Active provider is selected from the environment:
  AUREA_IDENTITY_PROVIDER=mock_socure   (default — no credentials needed)
  AUREA_IDENTITY_PROVIDER=socure        (real Socure API — implement identity/socure.py)
  AUREA_IDENTITY_PROVIDER=jumio         (real Jumio API — implement identity/jumio.py)

Per-firm override: set ``firm.integrations_config["identity_provider"]`` in the DB.
"""
from __future__ import annotations

import os

from .base import CIPResult, IdentityAdapter
from .mock_socure import MockSocureAdapter

# ── Provider registry ─────────────────────────────────────────────────────────
# To add a real provider:
#   1. Implement IdentityAdapter in a new module (e.g. identity/socure.py)
#   2. Import it below and add it here
#   3. Set AUREA_IDENTITY_PROVIDER=<key> in .env
_REGISTRY: dict[str, type[IdentityAdapter]] = {
    "mock_socure": MockSocureAdapter,
    # "socure": SocureAdapter,
    # "jumio": JumioAdapter,
    # "onfido": OnfidoAdapter,
    # "persona": PersonaAdapter,
}

_DEFAULT_PROVIDER = os.getenv("AUREA_IDENTITY_PROVIDER", "mock_socure")


def get_adapter(provider: str | None = None) -> IdentityAdapter:
    """Return an initialised IdentityAdapter for the given (or default) provider."""
    name = provider or _DEFAULT_PROVIDER
    cls = _REGISTRY.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown identity provider '{name}'. "
            f"Available: {sorted(_REGISTRY)}. "
            "Set AUREA_IDENTITY_PROVIDER or implement a new IdentityAdapter subclass."
        )
    return cls()


__all__ = ["CIPResult", "IdentityAdapter", "get_adapter"]
