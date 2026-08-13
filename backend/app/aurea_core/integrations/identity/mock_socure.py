"""Mock Socure CIP adapter.

Produces deterministic results from input data so the same case always gets
the same verification outcome — useful for demos and regression tests.

Real Socure integration: implement IdentityAdapter, call
  POST https://api.socure.com/api/3.0/EmailAuthScore
with the SDK payload, map the ``decision.value`` + ``fraudScore`` fields
to CIPResult, and register as provider_name = "socure".
"""
from __future__ import annotations

import hashlib
import re

from .base import CIPResult, IdentityAdapter

_CRITICAL_FLAGS = {"synthetic_identity_indicator", "thin_file"}
_HIGH_FLAGS = {"dob_missing", "id_number_missing"}


def _stable_seed(full_name: str, dob: str | None, id_number: str | None) -> float:
    """Deterministic base score 0.55–0.99 derived from input hash."""
    raw = f"{full_name.lower().strip()}|{dob or ''}|{id_number or ''}"
    digest = int(hashlib.sha256(raw.encode()).hexdigest()[:8], 16)
    return round(0.55 + (digest % 44_000) / 100_000, 4)


def _ref_id(full_name: str) -> str:
    h = abs(hash(full_name)) % 1_000_000
    return f"MOCK-SOCURE-{h:06d}"


class MockSocureAdapter(IdentityAdapter):
    """Deterministic mock that mirrors Socure's KYC module response shape."""

    provider_name = "mock_socure"

    def verify(
        self,
        *,
        full_name: str,
        dob: str | None = None,
        address: str | None = None,
        id_number: str | None = None,
        registration_type: str | None = None,
    ) -> CIPResult:
        flags: list[str] = []
        name_lower = full_name.lower()

        # Synthetic-identity heuristics (mirrors Socure sigma module).
        if any(t in name_lower for t in ("test", "sample", "fake", "dummy")):
            flags.append("synthetic_identity_indicator")

        if not dob:
            flags.append("dob_missing")
        elif not re.match(r"\d{4}-\d{2}-\d{2}", dob):
            flags.append("dob_format_invalid")

        if not address:
            flags.append("address_missing")

        if not id_number:
            flags.append("id_number_missing")
        elif id_number.lower() in {"unknown", "n/a", "na", ""}:
            flags.append("thin_file")

        if registration_type and "trust" in registration_type:
            flags.append("trust_entity_enhanced_check")

        score = _stable_seed(full_name, dob, id_number)
        penalty = sum(
            0.30 if f in _CRITICAL_FLAGS else 0.10
            for f in flags if f in _CRITICAL_FLAGS | _HIGH_FLAGS
        )
        score = max(0.10, round(score - penalty, 2))

        if score >= 0.80 and not (_CRITICAL_FLAGS & set(flags)):
            status = "verified"
        elif score >= 0.55:
            status = "review"
        else:
            status = "failed"

        return CIPResult(
            status=status,
            score=score,
            flags=flags,
            reference_id=_ref_id(full_name),
            provider=self.provider_name,
            raw_response={
                "modules": ["kyc", "sigma"],
                "kyc": {"fieldValidations": {"firstName": score, "dob": 0.9 if dob else 0.0}},
                "sigma": {"scores": [{"name": "combinedScore", "score": score}]},
                "referenceId": _ref_id(full_name),
                "mock": True,
            },
        )
