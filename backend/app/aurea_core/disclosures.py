"""Required-disclosure matrix (L200 §2.1 Track A, step 4).

Which disclosures a case requires depends on the firm's jurisdiction and, for a few, on
the registration type. L200's control for the disclosure failure mode is a *system-enforced
delivery gate before account activation* — that gate needs a definition of "required", and
this module is it.

Kept as data rather than scattered conditionals so it reads as the configurable matrix
L200 asks for ("configurable document matrices rather than tribal knowledge") and can move
into firm configuration later without touching call sites.
"""
from __future__ import annotations

# doc_type -> (label, regime, why it is required)
CATALOGUE: dict[str, tuple[str, str, str]] = {
    "form_adv_2a": (
        "Form ADV Part 2A (firm brochure)", "US SEC",
        "Advisers Act Rule 204-3 — brochure delivery at or before entering the advisory "
        "agreement, with annual redelivery obligations running from this date.",
    ),
    "form_adv_2b": (
        "Form ADV Part 2B (brochure supplement)", "US SEC",
        "Supplement covering the supervised persons who advise this client.",
    ),
    "form_crs": (
        "Form CRS (client relationship summary)", "US SEC",
        "Rule 204-5 / 17a-14 — relationship summary delivered at account opening.",
    ),
    "privacy_notice": (
        "Privacy notice", "US SEC",
        "Regulation S-P initial privacy notice, including incident-response notification terms.",
    ),
    "wrap_brochure": (
        "Wrap fee program brochure", "US SEC",
        "Required where the account is opened under a wrap fee program.",
    ),
    "margin_disclosure": (
        "Margin risk disclosure", "US SEC",
        "Required where margin is enabled on the account.",
    ),
    "pte_rollover_disclosure": (
        "Rollover best-interest disclosure", "US DOL",
        "PTE 2020-02 — the written rationale for why the rollover is in the client's "
        "best interest must be provided, not merely documented internally.",
    ),
    "kid": (
        "Key Information Document (KID)", "UK/EU",
        "PRIIPs KID for packaged retail investment products.",
    ),
    "terms_of_business": (
        "Terms of business", "UK FCA",
        "FCA COBS client agreement setting out the service, fees and cancellation rights.",
    ),
    "soa": (
        "Statement of Advice (SOA)", "AU/NZ",
        "Advice documentation given to a retail client before the advice is acted on.",
    ),
    "disclosure_statement": (
        "Adviser disclosure statement", "NZ FMA",
        "Financial advice provider disclosure — licensing, fees, conflicts and complaints.",
    ),
}

# Baseline required set per firm jurisdiction.
_BY_JURISDICTION: dict[str, list[str]] = {
    "US": ["form_adv_2a", "form_adv_2b", "form_crs", "privacy_notice"],
    "UK": ["terms_of_business", "kid", "privacy_notice"],
    "NZ": ["disclosure_statement", "soa", "privacy_notice"],
    "AU": ["soa", "privacy_notice"],
}

# Registration types that add a conditional disclosure on top of the baseline.
_ROLLOVER_TYPES = {"employer_rollover", "traditional_ira", "roth_ira"}


def required_for(jurisdiction: str | None, registration_type: str | None) -> list[str]:
    """The doc_types this case must evidence before the account can be activated."""
    base = list(_BY_JURISDICTION.get((jurisdiction or "NZ").upper(), _BY_JURISDICTION["NZ"]))

    # US rollovers carry the PTE 2020-02 best-interest disclosure as well as the memo.
    if (registration_type or "") in _ROLLOVER_TYPES and (jurisdiction or "").upper() == "US":
        base.append("pte_rollover_disclosure")

    return base


def describe(doc_type: str) -> dict:
    label, regime, why = CATALOGUE.get(
        doc_type, (doc_type.replace("_", " ").title(), "—", "")
    )
    return {"doc_type": doc_type, "label": label, "regime": regime, "why": why}


def status_for(
    jurisdiction: str | None,
    registration_type: str | None,
    delivered: list,
) -> dict:
    """Reconcile the required set against delivery records.

    `delivered` is an iterable of DisclosureDelivery rows. Extra deliveries outside the
    required set are still reported — a firm may deliver more than the minimum, and the
    evidence matters either way.
    """
    required = required_for(jurisdiction, registration_type)
    by_type: dict[str, list] = {}
    for d in delivered:
        by_type.setdefault(d.doc_type, []).append(d)

    items = []
    for doc_type in required:
        rows = by_type.get(doc_type, [])
        latest = max(rows, key=lambda r: r.delivered_at) if rows else None
        items.append({
            **describe(doc_type),
            "required": True,
            "delivered": latest is not None,
            "delivered_at": latest.delivered_at.isoformat() if latest else None,
            "method": latest.method if latest else None,
            "evidence_ref": latest.evidence_ref if latest else None,
            "delivered_by": latest.delivered_by if latest else None,
            "acknowledged_at": (
                latest.acknowledged_at.isoformat() if latest and latest.acknowledged_at else None
            ),
            "delivery_id": str(latest.id) if latest else None,
        })

    for doc_type, rows in by_type.items():
        if doc_type in required:
            continue
        latest = max(rows, key=lambda r: r.delivered_at)
        items.append({
            **describe(doc_type),
            "required": False,
            "delivered": True,
            "delivered_at": latest.delivered_at.isoformat(),
            "method": latest.method,
            "evidence_ref": latest.evidence_ref,
            "delivered_by": latest.delivered_by,
            "acknowledged_at": (
                latest.acknowledged_at.isoformat() if latest.acknowledged_at else None
            ),
            "delivery_id": str(latest.id),
        })

    outstanding = [i["doc_type"] for i in items if i["required"] and not i["delivered"]]
    return {
        "jurisdiction": (jurisdiction or "NZ").upper(),
        "items": items,
        "required_count": len(required),
        "delivered_count": len(required) - len(outstanding),
        "outstanding": outstanding,
        # The activation gate. L200: delivery must be evidenced *before* the account opens.
        "blocks_activation": bool(outstanding),
    }
