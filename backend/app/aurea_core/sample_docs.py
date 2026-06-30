"""Synthetic document generators — stand in for real uploaded files in the demo.

These produce `Label: value` text that the document-intelligence extractor parses. The Studio
'add document' action offers these templates so the onboarding flow is exercisable without
real file uploads; a firm would instead point the document-intelligence connector at real docs."""
from __future__ import annotations


def passport(name: str, dob: str = "1979-03-14", number: str = "NZ "  "P1234567",
             nationality: str = "New Zealand", expiry: str = "2031-08-01") -> str:
    return (
        "REPUBLIC PASSPORT\n"
        f"Full Name: {name}\n"
        f"Date of Birth: {dob}\n"
        f"Passport No: {number}\n"
        f"Nationality: {nationality}\n"
        f"Date of Expiry: {expiry}\n"
    )


def drivers_licence(name: str, dob: str = "1979-03-14", number: str = "DL889213",
                    address: str = "12 Marine Parade, Auckland 1010") -> str:
    return (
        "DRIVER LICENCE\n"
        f"Full Name: {name}\n"
        f"Date of Birth: {dob}\n"
        f"Licence No: {number}\n"
        f"Address: {address}\n"
    )


def proof_of_address(name: str, address: str = "12 Marine Parade, Auckland 1010",
                     issued: str = "2026-05-02") -> str:
    return (
        "UTILITY STATEMENT\n"
        f"Account Holder: {name}\n"
        f"Service Address: {address}\n"
        f"Statement Date: {issued}\n"
    )


def trust_deed(trust_name: str, settlor: str, trustees: list[str], beneficiaries: list[str],
               established: str = "2014-11-20") -> str:
    return (
        "DEED OF TRUST\n"
        f"Name of Trust: {trust_name}\n"
        f"Settlor: {settlor}\n"
        f"Trustees: {'; '.join(trustees)}\n"
        f"Beneficiaries: {'; '.join(beneficiaries)}\n"
        f"Date of Settlement: {established}\n"
    )


def overseas_pension(scheme: str = "Aurora UK Retirement Scheme", provider: str = "Aurora Pensions Ltd",
                     jurisdiction: str = "United Kingdom", value: str = "GBP 480,000") -> str:
    return (
        "OVERSEAS PENSION TRANSFER STATEMENT\n"
        f"Scheme Name: {scheme}\n"
        f"Provider: {provider}\n"
        f"Jurisdiction: {jurisdiction}\n"
        f"Transfer Value: {value}\n"
    )


def capital_call_notice(fund: str, investor: str, amount: str, due: str, currency: str = "USD") -> str:
    return (
        "CAPITAL CALL NOTICE\n"
        f"Fund: {fund}\n"
        f"Investor: {investor}\n"
        f"Call Amount: {amount}\n"
        f"Due Date: {due}\n"
        f"Currency: {currency}\n"
    )


# Catalogue offered in the 'add document' UI.
TEMPLATES = [
    {"doc_type": "passport", "label": "Passport"},
    {"doc_type": "drivers_licence", "label": "Driver's Licence"},
    {"doc_type": "proof_of_address", "label": "Proof of Address"},
    {"doc_type": "trust_deed", "label": "Trust Deed"},
    {"doc_type": "overseas_pension", "label": "Overseas Pension Statement"},
]


def generate(doc_type: str, name: str) -> str:
    """Generate a default sample document of a type for a named applicant."""
    if doc_type == "passport":
        return passport(name)
    if doc_type == "drivers_licence":
        return drivers_licence(name)
    if doc_type == "proof_of_address":
        return proof_of_address(name)
    if doc_type == "trust_deed":
        return trust_deed(name, settlor=name, trustees=[name, "Independent Trustee Ltd"],
                          beneficiaries=["Family members"])
    if doc_type == "overseas_pension":
        return overseas_pension()
    return f"Document\nFull Name: {name}\n"
