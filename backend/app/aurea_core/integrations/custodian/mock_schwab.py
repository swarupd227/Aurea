"""Mock Schwab Advisor Services custodian adapter.

Simulates the Schwab OpenAPI account-opening flow.  Produces deterministic
account IDs from the prospect name + registration type so retries are safe.

Real Schwab integration: implement CustodianAdapter, call
  POST https://api.schwabapi.com/trader/v1/accounts
with OAuth 2.0 bearer token, map the response ``accountId`` to AccountOpenResult,
and register as custodian_name = "schwab".

Fidelity / Pershing / Apex: same pattern — implement their specific auth and
endpoint shape, register under the appropriate key.
"""
from __future__ import annotations

import hashlib

from .base import AccountOpenResult, CustodianAdapter

_REG_TYPE_CODE: dict[str, str] = {
    "individual":          "IND",
    "joint_jtwros":        "JJWROS",
    "joint_tic":           "JTIC",
    "traditional_ira":     "TIRA",
    "roth_ira":            "RIRA",
    "employer_rollover":   "ROLL",
    "trust":               "TUST",
    "entity_llc":          "ENTL",
    "entity_corp":         "ENTC",
    "entity_partnership":  "ENTP",
    "custodial_utma":      "UTMA",
    "custodial_ugma":      "UGMA",
    "estate_inherited":    "IEST",
}

_SEGMENT_CUSTODIAN: dict[str, str] = {
    "institutional": "pershing",
    "for_purpose":   "pershing",
}


def _account_number(custodian: str, reg_type: str, name: str) -> str:
    h = int(hashlib.sha256(f"{name}|{reg_type}".encode()).hexdigest()[:10], 16) % 999_999_999
    code = _REG_TYPE_CODE.get(reg_type, "ACC")
    return f"{code}-{h:09d}"


class MockSchwabAdapter(CustodianAdapter):
    """Deterministic mock that mirrors Schwab's account-opening response shape."""

    custodian_name = "mock_schwab"

    def open_account(
        self,
        *,
        prospect_name: str,
        registration_type: str | None,
        segment: str = "private_wealth",
        tax_id: str | None = None,
        preferred_account_type: str | None = None,
        **kwargs,
    ) -> AccountOpenResult:
        custodian = _SEGMENT_CUSTODIAN.get(segment, "schwab")
        reg = registration_type or "individual"

        # Simulate a small (5%) failure rate for missing docs — realistic for demos.
        seed = int(hashlib.sha256(prospect_name.encode()).hexdigest()[:4], 16) % 100
        if seed < 5:
            return AccountOpenResult(
                custodian=custodian,
                account_id=None,
                status="failed",
                error_code="MISSING_SUITABILITY_DOCS",
                error_message="Suitability documentation is incomplete. Please resubmit.",
                raw_response={"errorCode": "MISSING_SUITABILITY_DOCS", "mock": True},
            )

        account_id = _account_number(custodian, reg, prospect_name)
        return AccountOpenResult(
            custodian=custodian,
            account_id=account_id,
            status="active",
            raw_response={
                "accountId": account_id,
                "accountType": _REG_TYPE_CODE.get(reg, "ACC"),
                "status": "OPEN",
                "mock": True,
            },
        )
