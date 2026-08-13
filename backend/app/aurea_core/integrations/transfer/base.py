"""Abstract interface for transfer tracking providers (ACAT, wire, ACH).

Real implementations (DTCC / NSCC for ACAT, custodian wire APIs, Plaid for ACH)
must subclass ``TransferAdapter`` and implement ``submit()`` and ``get_status()``.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TransferSubmitResult:
    """Returned when a new transfer is submitted to the provider."""

    reference_id: str        # provider's tracking reference — store for poll/webhook
    status: str              # initial status: "initiated" | "pending_review"
    custodian: str
    estimated_settlement_days: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    raw_response: dict | None = None


@dataclass
class TransferStatusResult:
    """Returned when polling the provider for a transfer's current status."""

    reference_id: str
    status: str              # "initiated"|"pending_review"|"in_transit"|"settled"|"failed"
    custodian: str
    error_message: str | None = None
    raw_response: dict | None = None


# Canonical transfer status progression.
TRANSFER_STATUSES = ["initiated", "pending_review", "in_transit", "settled"]
TERMINAL_STATUSES = {"settled", "failed"}


class TransferAdapter(ABC):
    """Base class every transfer-tracking provider adapter must implement."""

    provider_name: str

    @abstractmethod
    def submit(
        self,
        *,
        transfer_type: str,     # "acat" | "wire" | "ach"
        direction: str,         # "in" | "out"
        amount: float | None,
        asset_description: str,
        custodian: str | None = None,
        case_reference: str | None = None,
        **kwargs,
    ) -> TransferSubmitResult:
        """Submit a new transfer request to the provider."""

    @abstractmethod
    def get_status(self, reference_id: str) -> TransferStatusResult:
        """Poll the provider for the current status of an existing transfer."""

    def advance(self, reference_id: str) -> TransferStatusResult:
        """Advance a transfer to the next status (mock convenience method).

        Real providers do not expose this — status changes come via webhooks
        or polling.  Only used by the mock adapter for demo purposes.
        """
        raise NotImplementedError("advance() is only available on mock adapters.")
