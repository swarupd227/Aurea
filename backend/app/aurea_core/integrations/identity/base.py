"""Abstract interface for CIP identity verification providers.

Real implementations (Socure, Jumio, Onfido, Persona, …) must subclass
``IdentityAdapter`` and implement ``verify()``.  The factory in __init__.py
resolves the active adapter from ``AUREA_IDENTITY_PROVIDER``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class CIPResult:
    """Normalised response from any identity verification provider."""

    status: str          # "verified" | "review" | "failed"
    score: float         # 0.0 – 1.0  (provider-normalised confidence)
    flags: list[str]     # e.g. ["thin_file", "address_mismatch", "pep_hit"]
    reference_id: str    # provider's own reference — store for audit trail
    provider: str        # which adapter produced this result
    raw_response: dict = field(default_factory=dict)  # verbatim provider payload


class IdentityAdapter(ABC):
    """Base class every CIP provider adapter must implement."""

    provider_name: str  # set as a class variable on each subclass

    @abstractmethod
    def verify(
        self,
        *,
        full_name: str,
        dob: str | None = None,      # ISO YYYY-MM-DD
        address: str | None = None,
        id_number: str | None = None,
        registration_type: str | None = None,
    ) -> CIPResult:
        """Submit identity data to the provider and return a normalised result.

        Implementations must be idempotent — the same inputs should return a
        consistent result (important for deterministic mock/test behaviour).
        """
