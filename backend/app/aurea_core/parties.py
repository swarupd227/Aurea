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

from app.aurea_core import law_figures, markets
from app.aurea_core.aml_review import SETTLED, party_state
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
    "joint_tbe": [
        (PartyRole.OWNER, 1, "The primary account holder."),
        (PartyRole.JOINT_OWNER, 1, "Tenancy by the entirety is a married couple."),
    ],
    "community_property": [
        (PartyRole.OWNER, 1, "The primary account holder."),
        (PartyRole.JOINT_OWNER, 1, "Community property is a married couple."),
    ],
    "sep_ira": [
        (PartyRole.OWNER, 1, "The account holder."),
        (PartyRole.BENEFICIARY, 1,
         "Missing beneficiary designations send retirement assets through probate."),
    ],
    "simple_ira": [
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
    "huf": [
        (PartyRole.KARTA, 1, "The karta manages the HUF and acts and signs for it."),
    ],
    "joint_all": [
        (PartyRole.OWNER, 1, "The first holder."),
        (PartyRole.JOINT_OWNER, 1, "A joint holding has at least two holders, and each one signs."),
    ],
    "joint_anyone": [
        (PartyRole.OWNER, 1, "The first holder."),
        (PartyRole.JOINT_OWNER, 1, "An anyone-or-survivor holding has at least two holders."),
    ],
    "entity_llp": [
        (PartyRole.BENEFICIAL_OWNER, 1, "Each individual who owns or controls the LLP above the line must be recorded."),
        (PartyRole.CONTROL_PERSON, 1, "The designated partner or person in control must be named."),
    ],
    "nri_nre": [(PartyRole.OWNER, 1, "The non-resident account holder.")],
    "nri_nro": [(PartyRole.OWNER, 1, "The non-resident account holder.")],
    "minor_guardian": [
        (PartyRole.MINOR, 1, "The minor the account is for; the minor never trades."),
        (PartyRole.GUARDIAN, 1, "The guardian who operates the account and signs for the minor."),
    ],
}

_ENTITY_TYPES = {"entity_llc", "entity_corp", "entity_partnership", "entity_llp"}


def _india(market: str | None) -> bool:
    found = markets.get(market) if market else None
    return found is not None and found.code == "IN"


def ownership_line(registration_type: str | None, market: str | None) -> tuple[float, bool]:
    """(percentage, whether it is "more than" rather than "or more") above which a person is a beneficial owner. The
    Indian lines are the law figures `kyc_india` reads, unreviewed by counsel; an LLP is read at the partnership line,
    which is assumed. Elsewhere the CDD Rule's 25% or more."""
    if not _india(market):
        return BENEFICIAL_OWNER_THRESHOLD_PCT, False
    if registration_type == "trust":
        return round(law_figures.value("in_kyc_bo_trust") * 100, 2), False
    if registration_type in _ENTITY_TYPES:
        return round(law_figures.value("in_kyc_bo_company") * 100, 2), True
    return round(law_figures.value("in_kyc_bo_unincorporated") * 100, 2), True


def required_roles(registration_type: str | None, market: str | None = None) -> list[tuple[str, int, str]]:
    rows = _REQUIRED.get(registration_type or "individual", _REQUIRED["individual"])
    if not (_india(market) and registration_type in _ENTITY_TYPES):
        return rows
    line, _ = ownership_line(registration_type, market)
    llp = " An LLP is read at the partnership line (assumed)." if registration_type == "entity_llp" else ""
    why = {PartyRole.BENEFICIAL_OWNER: f"PML Rules: each individual with more than {line:g}% of the capital or profits is recorded (line not reviewed by counsel).{llp}",
           PartyRole.CONTROL_PERSON: f"PML Rules: name who controls it by other means, or its senior managing official, "
                                     f"whether or not they hold more than {line:g}%."}
    return [(role, n, why.get(role, said)) for role, n, said in rows]


def completeness_for(registration_type: str | None, parties: list, decisions: dict | None = None, *,
                     market: str | None = None) -> dict:
    """Evaluate role coverage, screening coverage and the CDD ownership rules.

    Returns the gate result: `blocks_activation` is true when any required role is
    missing, any party is not screened or has a hit nobody has decided, or the entity
    ownership rules are unmet. `decisions` are each party's decisions on its hits
    (aml_review.decisions_for); without them every hit is open.
    """
    states = {p.id: party_state(p, (decisions or {}).get(p.id, [])) for p in parties}
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
    for role, minimum, why in required_roles(registration_type, market):
        have = len(control_people) if role == PartyRole.CONTROL_PERSON else len(by_role.get(role, []))
        if have < minimum:
            role_gaps.append({
                "role": role,
                "label": ROLE_LABELS.get(role, role),
                "have": have,
                "need": minimum,
                "why": why,
            })

    # 2. Every party screened, and every hit decided? This is the sanctions-breach control:
    # the screener running is not the same as the party being cleared.
    unscreened = [
        {"id": str(p.id), "legal_name": p.legal_name, "role": p.role,
         "label": ROLE_LABELS.get(p.role, p.role), "state": states[p.id]["state"]}
        for p in parties
        if states[p.id]["state"] not in SETTLED
    ]

    # 3. Entity ownership rules under the CDD Rule.
    ownership_issues = []
    india = _india(market)
    line, more_than = ownership_line(registration_type, market)
    if (registration_type or "") in _ENTITY_TYPES or (india and registration_type == "trust"):
        bos = by_role.get(PartyRole.BENEFICIAL_OWNER, [])
        total = sum(float(p.ownership_pct or 0) for p in bos)

        if len(control_people) > 1 and not india:      # "exactly one" is the CDD Rule's
            ownership_issues.append({
                "code": "multiple_control_persons",
                "detail": (
                    f"{len(control_people)} control persons named "
                    f"({', '.join(p.legal_name for p in control_people)}) — "
                    "the CDD Rule expects exactly one."
                ),
            })
        below = [p for p in bos if p.ownership_pct is not None
                 and (float(p.ownership_pct) <= line if more_than else float(p.ownership_pct) < line)]
        if below:
            ownership_issues.append({
                "code": "below_threshold",
                "detail": (
                    f"{len(below)} party(ies) recorded as beneficial owners {'at or below' if more_than else 'below'} the "
                    f"{line:g}% threshold: "
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
        "market": "IN" if india else None,
        # What the ownership gate says when nothing is wrong, in the market's own terms.
        "ownership_ok": (f"Owners above the {line:g}% line and the person in control are recorded." if india
                         else "25% owners and control person certified."),
        "n_parties": len(parties),
        "role_gaps": role_gaps,
        "unscreened": unscreened,
        "sanctions": [p.legal_name for p in parties if states[p.id]["sanctions"]],
        "names": [p.legal_name for p in parties],
        "ownership_issues": ownership_issues,
        "screened_count": len(parties) - len(unscreened),
        "blocks_activation": blocks,
    }
