"""Document intelligence (spec §6.4).

Extracts and standardises unstructured client documents (IDs, trust deeds, overseas-pension
paperwork, capital-call notices) into structured fields for the client brain, retaining the
source for verification. The extraction here is a deterministic simulation: synthetic
documents carry `Label: value` lines, which we parse into typed fields with a per-field
confidence — standing in for a real OCR/IDP connector that a firm would configure in Conduit."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Expected fields per document type. (key, label-aliases, is_list)
DOC_SCHEMAS: dict[str, list[tuple[str, list[str], bool]]] = {
    "passport": [
        ("full_name", ["full name", "name", "surname/given names"], False),
        ("date_of_birth", ["date of birth", "dob"], False),
        ("document_number", ["passport no", "document number", "passport number"], False),
        ("nationality", ["nationality"], False),
        ("expiry_date", ["expiry", "date of expiry", "expiry date"], False),
    ],
    "drivers_licence": [
        ("full_name", ["full name", "name"], False),
        ("date_of_birth", ["date of birth", "dob"], False),
        ("licence_number", ["licence no", "license number", "licence number"], False),
        ("address", ["address"], False),
    ],
    "proof_of_address": [
        ("full_name", ["name", "account holder"], False),
        ("address", ["address", "service address"], False),
        ("issued_date", ["issued", "statement date", "date"], False),
    ],
    "trust_deed": [
        ("trust_name", ["trust name", "name of trust"], False),
        ("settlor", ["settlor"], False),
        ("trustees", ["trustees", "trustee"], True),
        ("beneficiaries", ["beneficiaries", "beneficiary"], True),
        ("establishment_date", ["date of settlement", "established", "date"], False),
    ],
    "overseas_pension": [
        ("scheme_name", ["scheme", "scheme name"], False),
        ("provider", ["provider", "administrator"], False),
        ("jurisdiction", ["jurisdiction", "country"], False),
        ("transfer_value", ["transfer value", "ctv", "value"], False),
    ],
    "capital_call_notice": [
        ("fund_name", ["fund", "fund name"], False),
        ("investor", ["investor", "limited partner", "lp"], False),
        ("call_amount", ["call amount", "amount called", "amount"], False),
        ("due_date", ["due date", "payment due", "due"], False),
        ("currency", ["currency", "ccy"], False),
    ],
}

DOC_LABELS = {
    "passport": "Passport",
    "drivers_licence": "Driver's Licence",
    "proof_of_address": "Proof of Address",
    "trust_deed": "Trust Deed",
    "overseas_pension": "Overseas Pension Statement",
    "capital_call_notice": "Capital-Call Notice",
}


@dataclass
class ExtractedField:
    key: str
    value: object
    confidence: float
    found: bool


@dataclass
class ExtractionResult:
    doc_type: str
    fields: dict
    field_confidence: dict
    confidence: float
    missing: list[str] = field(default_factory=list)
    low_confidence: list[str] = field(default_factory=list)


def _parse_lines(raw_text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (raw_text or "").splitlines():
        if ":" in line:
            label, _, value = line.partition(":")
            out[label.strip().lower()] = value.strip()
    return out


def extract(doc_type: str, raw_text: str) -> ExtractionResult:
    """Parse a synthetic document into structured fields with per-field confidence."""
    schema = DOC_SCHEMAS.get(doc_type)
    parsed = _parse_lines(raw_text)
    if not schema:
        return ExtractionResult(doc_type, {}, {}, 0.0, missing=["unknown_doc_type"])

    fields: dict = {}
    field_conf: dict = {}
    missing: list[str] = []
    low: list[str] = []

    for key, aliases, is_list in schema:
        value = None
        for alias in aliases:
            if alias in parsed and parsed[alias]:
                value = parsed[alias]
                break
        if value is None:
            missing.append(key)
            field_conf[key] = 0.0
            continue
        if is_list:
            parts = [p.strip() for p in re.split(r"[;,]", value) if p.strip()]
            fields[key] = parts
            # Confidence dips for lists (harder to parse cleanly).
            conf = 0.9 if parts else 0.4
        else:
            fields[key] = value
            # Numbers / dates that look well-formed score higher.
            conf = 0.97 if re.search(r"[A-Za-z0-9]", value) else 0.6
            if len(value) < 3:
                conf = 0.55
        field_conf[key] = round(conf, 3)
        if conf < 0.7:
            low.append(key)

    present = [c for c in field_conf.values() if c > 0]
    overall = round(sum(present) / len(schema), 3) if schema else 0.0
    return ExtractionResult(doc_type, fields, field_conf, overall, missing, low)
