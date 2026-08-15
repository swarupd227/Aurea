"""Party completeness rules and the screening gate (L200 §2.2 Track B, §5).

Two questions this module answers:

  1. Does the case have the roles its registration type requires?
     (A joint account with one owner, a trust with no trustee, an LLC with no control
     person — each is a rejected package waiting to happen.)

  2. Has every party been screened?
     L200's control for the sanctions-breach failure mode is "party-model completeness
     checks: every role on the account must have a screened identity record". That is a
     gate, not a report — an unscreened trustee should block activation.

Kept as data so the matrix is configurable rather than tribal knowledge.
"""
from __future__ import annotations

from app.models.enums import PartyRole

# CDD Rule 31 CFR 1010.230 — a beneficial owner is a 25%+ owner.
BENEFICIAL_OWNER_THRESHOLD_PCT = 25.0

# role -> (label, what it means)
ROLE_LABELS: dict[str, str] = {
    PartyRole.OWNER: "Owner",
    PartyRole.JOINT_OWNER: "Joint owner",
    PartyRole.TRUSTEE: "Trustee",
    PartyRole.SETTLOR: "Settlor",
    PartyRole.BENEFICIARY: "Beneficiary",
    PartyRole.BENEFICIAL_OWNER: "Beneficial owner",
    PartyRole.CONTROL_PERSON: "Control person",
    PartyRole.AUTHORISED_SIGNER: "Authorised signer",
    PartyRole.POA_HOLDER: "Power of attorney",
    PartyRole.CUSTODIAN: "Custodian",
    PartyRole.MINOR: "Minor",
    PartyRole.EXECUTOR: "Executor",
}

# registration_type -> [(role, min_count, why)]
_REQUIRED: dict[str, list[tuple[str, int, str]]] = {
    "individual": [
        (PartyRole.OWNER, 1, "The account holder."),
    ],
    "joint_jtwros": [
        (PartyRole.OWNER, 1, "The primary account holder."),
        (PartyRole.JOINT_OWNER, 1, "Joint tenancy requires at least two owners."),
    ],
    "joint_tic": [
        (PartyRole.OWNER, 1, "The primary account holder."),
        (PartyRole.JOINT_OWNER, 1, "Tenants in common requires at least two owners."),
    ],
    "traditional_ira": [
        (PartyRole.OWNER, 1, "The account holder."),
        (PartyRole.BENEFICIARY, 1,
         "Missing beneficiary designations send retirement assets through probate."),
    ],
    "roth_ira": [
        (PartyRole.OWNER, 1, "The account holder."),
        (PartyRole.BENEFICIARY, 1,
         "Missing beneficiary designations send retirement assets through probate."),
    ],
    "employer_rollover": [
        (PartyRole.OWNER, 1, "The account holder."),
        (PartyRole.BENEFICIARY, 1,
         "Missing beneficiary designations send retirement assets through probate."),
    ],
    "trust": [
        (PartyRole.TRUSTEE, 1, "At least one trustee must be identified and screened."),
    ],
    "entity_llc": [
        (PartyRole.BENEFICIAL_OWNER, 1,
         "CDD Rule — each individual owning 25% or more must be certified."),
        (PartyRole.CONTROL_PERSON, 1,
         "CDD Rule — exactly one control person must be named."),
    ],
    "entity_corp": [
        (PartyRole.BENEFICIAL_OWNER, 1,
         "CDD Rule — each individual owning 25% or more must be certified."),
        (PartyRole.CONTROL_PERSON, 1,
         "CDD Rule — exactly one control person must be named."),
    ],
    "entity_partnership": [
        (PartyRole.BENEFICIAL_OWNER, 1,
         "CDD Rule — each individual owning 25% or more must be certified."),
        (PartyRole.CONTROL_PERSON, 1,
         "CDD Rule — exactly one control person must be named."),
    ],
    "custodial_utma": [
        (PartyRole.CUSTODIAN, 1, "The adult custodian operating the account."),
        (PartyRole.MINOR, 1, "The minor beneficiary."),
    ],
    "custodial_ugma": [
        (PartyRole.CUSTODIAN, 1, "The adult custodian operating the account."),
        (PartyRole.MINOR, 1, "The minor beneficiary."),
    ],
    "estate_inherited": [
        (PartyRole.EXECUTOR, 1, "The executor or personal representative."),
        (PartyRole.BENEFICIARY, 1, "At least one estate beneficiary."),
    ],
}

_ENTITY_TYPES = {"entity_llc", "entity_corp", "entity_partnership"}
_SCREENED_OK = {"clear", "review", "blocked"}   # anything other than not_screened


def required_roles(registration_type: str | None) -> list[tuple[str, int, str]]:
    return _REQUIRED.get(registration_type or "individual", _REQUIRED["individual"])


def completeness_for(registration_type: str | None, parties: list) -> dict:
    """Evaluate role coverage, screening coverage and the CDD ownership rules.

    Returns the gate result: `blocks_activation` is true when any required role is
    missing, any party is unscreened, or the entity ownership rules are unmet.
    """
    by_role: dict[str, list] = {}
    for p in parties:
        by_role.setdefault(p.role, []).append(p)

    # Under the CDD Rule the control person is usually *also* a beneficial owner, so the
    # requirement is satisfied either by the dedicated role or by the flag on any party.
    # Counting only the role reported a missing control person for entities that plainly
    # had one.
    control_people = [
        p for p in parties
        if p.role == PartyRole.CONTROL_PERSON or p.is_control_person
    ]

    # 1. Required roles present?
    role_gaps = []
    for role, minimum, why in required_roles(registration_type):
        have = len(control_people) if role == PartyRole.CONTROL_PERSON else len(by_role.get(role, []))
        if have < minimum:
            role_gaps.append({
                "role": role,
                "label": ROLE_LABELS.get(role, role),
                "have": have,
                "need": minimum,
                "why": why,
            })

    # 2. Every party screened? This is the sanctions-breach control.
    unscreened = [
        {"id": str(p.id), "legal_name": p.legal_name, "role": p.role,
         "label": ROLE_LABELS.get(p.role, p.role)}
        for p in parties
        if (p.screening_status or "not_screened") not in _SCREENED_OK
    ]

    # 3. Entity ownership rules under the CDD Rule.
    ownership_issues = []
    if (registration_type or "") in _ENTITY_TYPES:
        bos = by_role.get(PartyRole.BENEFICIAL_OWNER, [])
        total = sum(float(p.ownership_pct or 0) for p in bos)

        if len(control_people) > 1:
            ownership_issues.append({
                "code": "multiple_control_persons",
                "detail": (
                    f"{len(control_people)} control persons named "
                    f"({', '.join(p.legal_name for p in control_people)}) — "
                    "the CDD Rule expects exactly one."
                ),
            })
        below = [p for p in bos if p.ownership_pct is not None
                 and float(p.ownership_pct) < BENEFICIAL_OWNER_THRESHOLD_PCT]
        if below:
            ownership_issues.append({
                "code": "below_threshold",
                "detail": (
                    f"{len(below)} party(ies) recorded as beneficial owners below the "
                    f"{BENEFICIAL_OWNER_THRESHOLD_PCT:.0f}% threshold: "
                    + ", ".join(p.legal_name for p in below) + "."
                ),
            })
        missing_pct = [p for p in bos if p.ownership_pct is None]
        if missing_pct:
            ownership_issues.append({
                "code": "ownership_not_recorded",
                "detail": ", ".join(p.legal_name for p in missing_pct) + " have no ownership % recorded.",
            })
        # Certified ownership should account for the entity. Short of 100% is normal
        # (holdings under 25% need not be certified); over 100% is a data error.
        if total > 100.5:
            ownership_issues.append({
                "code": "ownership_exceeds_100",
                "detail": f"Certified ownership totals {total:.0f}%.",
            })

    blocks = bool(role_gaps or unscreened or ownership_issues)
    return {
        "registration_type": registration_type,
        "n_parties": len(parties),
        "role_gaps": role_gaps,
        "unscreened": unscreened,
        "ownership_issues": ownership_issues,
        "screened_count": len(parties) - len(unscreened),
        "blocks_activation": blocks,
    }
