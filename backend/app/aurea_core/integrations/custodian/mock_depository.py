"""A stand-in for opening an Indian account at a depository participant (B-385).

No depository participant, registrar or exchange platform is connected. This answers the same call as the other adapters so an
Indian demonstration case can move on, and every reference it gives starts "SIM-IN-" and names itself simulated, so nobody reads
it as an account anyone opened. It carries no US account code and names no US custodian.
"""
from __future__ import annotations

import hashlib

from .base import AccountOpenResult, CustodianAdapter

_CODE = {
    "individual": "IND", "joint_all": "JNTALL", "joint_anyone": "JNTAOS", "joint_jtwros": "JNT", "joint_tic": "JNT", "huf": "HUF",
    "trust": "TRUST", "entity_corp": "COMPANY", "entity_partnership": "PARTNER", "entity_llp": "LLP", "nri_nre": "NRE",
    "nri_nro": "NRO", "minor_guardian": "MINOR", "estate_inherited": "ESTATE",
}


class MockDepositoryAdapter(CustodianAdapter):
    custodian_name = "simulated_depository"

    def open_account(self, *, prospect_name: str, registration_type: str | None, segment: str = "private_wealth",
                     tax_id: str | None = None, preferred_account_type: str | None = None, **kwargs) -> AccountOpenResult:
        kind = registration_type or "individual"
        number = int(hashlib.sha256(f"{prospect_name}|{kind}".encode()).hexdigest()[:10], 16) % 999_999_999
        return AccountOpenResult(custodian=self.custodian_name, account_id=f"SIM-IN-{_CODE.get(kind, 'ACC')}-{number:09d}",
                                 status="active", raw_response={"simulated": True})
