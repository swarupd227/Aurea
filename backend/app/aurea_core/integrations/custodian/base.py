"""Abstract interface for custodian account-opening APIs.

Real implementations (Schwab Advisor Services, Fidelity WealthCentral,
Pershing NetX360, Apex Clearing, …) must subclass ``CustodianAdapter``
and implement ``open_account()``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class AccountOpenResult:
    """Normalised response from any custodian account-opening API."""

    custodian: str           # which custodian processed this
    account_id: str | None   # custodian-assigned account number (None on failure)
    status: str              # "pending" | "active" | "failed" | "rejected"
    error_code: str | None = None
    error_message: str | None = None
    raw_response: dict | None = None


class CustodianAdapter(ABC):
    """Base class every custodian adapter must implement."""

    custodian_name: str  # set as a class variable on each subclass

    @abstractmethod
    def open_account(
        self,
        *,
        prospect_name: str,
        registration_type: str | None,
        segment: str = "private_wealth",
        tax_id: str | None = None,         # SSN / EIN (encrypted in prod)
        preferred_account_type: str | None = None,
        **kwargs,
    ) -> AccountOpenResult:
        """Submit a new account application to the custodian and return the result.

        Implementations should be idempotent on the same prospect_name + registration_type
        to prevent duplicate submissions on retry.
        """

    def get_supported_registration_types(self) -> list[str]:
        """Return which registration types this custodian supports.

        Default: all standard types.  Override for custodian-specific restrictions.
        """
        return [
            "individual", "joint_jtwros", "joint_tic",
            "traditional_ira", "roth_ira", "employer_rollover",
            "trust", "entity_llc", "entity_corp", "entity_partnership",
            "custodial_utma", "custodial_ugma", "estate_inherited",
        ]
