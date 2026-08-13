"""Mock transfer tracking adapter.

Simulates DTCC ACAT / custodian wire / ACH transfer flows.
Status is stored in-memory per reference_id (resets on server restart — by design
for a mock).  ``advance()`` moves a transfer to the next status stage for demos.

Real ACAT integration: implement TransferAdapter, subscribe to DTCC NSCC feeds
(FTP or SFTP), map to TransferStatusResult, register as provider_name = "dtcc_nscc".

Real wire integration: call custodian wire API (e.g. Schwab wires endpoint),
poll status, map to TransferStatusResult.
"""
from __future__ import annotations

import hashlib
import time

from .base import (
    TERMINAL_STATUSES,
    TRANSFER_STATUSES,
    TransferAdapter,
    TransferStatusResult,
    TransferSubmitResult,
)

# In-memory status store (keyed by reference_id).
_STATUS_STORE: dict[str, str] = {}

_CUSTODIAN_DEFAULTS: dict[str, str] = {
    "acat": "dtcc_nscc",
    "wire": "schwab",
    "ach":  "plaid",
}

_SETTLEMENT_DAYS: dict[str, int] = {
    "acat": 5,
    "wire": 1,
    "ach":  3,
}


def _ref_id(transfer_type: str, direction: str, case_reference: str | None) -> str:
    raw = f"{transfer_type}|{direction}|{case_reference or ''}|{time.time_ns()}"
    h = int(hashlib.sha256(raw.encode()).hexdigest()[:10], 16) % 999_999_999_999
    prefix = {"acat": "ACAT", "wire": "WIRE", "ach": "ACH"}.get(transfer_type, "TXN")
    return f"MOCK-{prefix}-{h:012d}"


class MockTransferAdapter(TransferAdapter):
    """In-memory mock that mirrors DTCC ACAT / wire / ACH status state machines."""

    provider_name = "mock"

    def submit(
        self,
        *,
        transfer_type: str,
        direction: str,
        amount: float | None,
        asset_description: str,
        custodian: str | None = None,
        case_reference: str | None = None,
        **kwargs,
    ) -> TransferSubmitResult:
        ref = _ref_id(transfer_type, direction, case_reference)
        _STATUS_STORE[ref] = "initiated"
        return TransferSubmitResult(
            reference_id=ref,
            status="initiated",
            custodian=custodian or _CUSTODIAN_DEFAULTS.get(transfer_type, "mock"),
            estimated_settlement_days=_SETTLEMENT_DAYS.get(transfer_type),
            raw_response={"referenceId": ref, "type": transfer_type, "mock": True},
        )

    def get_status(self, reference_id: str) -> TransferStatusResult:
        status = _STATUS_STORE.get(reference_id, "initiated")
        return TransferStatusResult(
            reference_id=reference_id,
            status=status,
            custodian="mock",
            raw_response={"referenceId": reference_id, "status": status, "mock": True},
        )

    def advance(self, reference_id: str) -> TransferStatusResult:
        """Move to the next status in the TRANSFER_STATUSES progression."""
        current = _STATUS_STORE.get(reference_id, "initiated")
        if current in TERMINAL_STATUSES:
            return self.get_status(reference_id)
        idx = TRANSFER_STATUSES.index(current) if current in TRANSFER_STATUSES else 0
        next_status = TRANSFER_STATUSES[min(idx + 1, len(TRANSFER_STATUSES) - 1)]
        _STATUS_STORE[reference_id] = next_status
        return self.get_status(reference_id)
