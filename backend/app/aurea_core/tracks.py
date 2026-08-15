"""The four onboarding tracks (L200 §2).

L200 decomposes onboarding into "the four tracks that actually run in parallel":

    A  Agreements and disclosures      adviser
    B  Account establishment           operations
    C  Financial-crime compliance      compliance
    D  Funding and asset transfer      operations + external

A single linear status cannot express a case that is simultaneously agreement-signed,
account-documents-incomplete, EDD-pending and ACAT-in-flight — which is the state of most
real cases, and why the board could not tell an adviser what to do next.

Track state is **derived, not stored**. Everything needed is already recorded: disclosure
deliveries, party screening, CIP outcome, documents, transfers. Deriving it means the
tracks cannot drift out of sync with the underlying records, there is no transition
machinery to maintain, and existing cases get correct tracks with no migration. The cost
is that a track cannot be set by hand — which is the right trade for a compliance surface.
"""
from __future__ import annotations

# Track states, ordered from least to most advanced for roll-up purposes.
NOT_STARTED = "not_started"
IN_PROGRESS = "in_progress"
WAITING_EXTERNAL = "waiting_external"
BLOCKED = "blocked"
COMPLETE = "complete"

TRACKS = [
    ("A", "agreements", "Agreements & disclosures", "Adviser"),
    ("B", "account", "Account establishment", "Operations"),
    ("C", "financial_crime", "Financial crime", "Compliance"),
    ("D", "funding", "Funding & transfer", "Operations + external"),
]

# Required documents by registration type. Mirrors the NIGO agent's matrix; the two should
# converge on one source, which is noted in the redesign as its own step.
_DOC_MATRIX: dict[str, set[str]] = {
    "individual": {"passport", "drivers_licence"},
    "joint_jtwros": {"passport", "drivers_licence"},
    "joint_tic": {"passport", "drivers_licence"},
    "traditional_ira": {"passport", "drivers_licence"},
    "roth_ira": {"passport", "drivers_licence"},
    "employer_rollover": {"passport", "drivers_licence"},
    "trust": {"trust_deed", "passport"},
    "entity_llc": {"trust_deed", "passport"},
    "entity_corp": {"trust_deed", "passport"},
    "entity_partnership": {"trust_deed", "passport"},
    "custodial_utma": {"passport", "drivers_licence"},
    "custodial_ugma": {"passport", "drivers_licence"},
    "estate_inherited": {"passport"},
}

_SETTLED = "settled"
_IN_FLIGHT = {"in_transit"}
_STARTED = {"initiated", "pending_review"}


def _track_a(case, disclosure_status: dict) -> dict:
    """Agreements and disclosures. Currently evidenced by disclosure delivery."""
    required = disclosure_status.get("required_count", 0)
    delivered = disclosure_status.get("delivered_count", 0)
    if required and delivered >= required:
        state, detail = COMPLETE, "All required disclosures delivered and evidenced."
    elif delivered:
        state, detail = IN_PROGRESS, f"{delivered} of {required} disclosures delivered."
    else:
        state, detail = NOT_STARTED, f"No disclosures delivered yet ({required} required)."
    return {
        "state": state, "detail": detail,
        "next_action": None if state == COMPLETE else "Deliver and evidence the outstanding disclosures.",
    }


def _track_b(case, party_status: dict, documents: list) -> dict:
    """Account establishment — parties, documents, custodian account."""
    have_docs = {d.doc_type for d in documents}
    required_docs = _DOC_MATRIX.get(case.registration_type or "individual", set())
    missing_docs = sorted(required_docs - have_docs)

    role_gaps = party_status.get("role_gaps", [])
    ownership_issues = party_status.get("ownership_issues", [])

    if role_gaps or ownership_issues:
        first = (
            role_gaps[0]["label"] + " required"
            if role_gaps else ownership_issues[0]["detail"]
        )
        return {
            "state": BLOCKED,
            "detail": first,
            "next_action": "Complete the party record before submission.",
        }
    if missing_docs:
        return {
            "state": IN_PROGRESS,
            "detail": f"Missing {', '.join(d.replace('_', ' ') for d in missing_docs)}.",
            "next_action": "Collect the outstanding documents.",
        }
    if case.custodian_account_id:
        return {
            "state": COMPLETE,
            "detail": f"Account open at {case.custodian_name or 'custodian'} "
                      f"({case.custodian_account_id}).",
            "next_action": None,
        }
    return {
        "state": IN_PROGRESS,
        "detail": "Parties and documents complete; custodian account not yet opened.",
        "next_action": "Open the custodian account.",
    }


def _track_c(case, party_status: dict) -> dict:
    """Financial crime — CIP, screening coverage, risk rating, EDD."""
    unscreened = party_status.get("unscreened", [])
    n_parties = party_status.get("n_parties", 0)

    # A sanctions hit stops everything.
    if getattr(case, "screening", None) and (case.screening or {}).get("status") == "blocked":
        return {
            "state": BLOCKED,
            "detail": "Sanctions match — case cannot proceed without compliance clearance.",
            "next_action": "Compliance must disposition the sanctions match.",
        }
    if not n_parties:
        return {
            "state": NOT_STARTED,
            "detail": "No parties recorded to screen.",
            "next_action": "Record the account's parties.",
        }
    if unscreened:
        return {
            "state": IN_PROGRESS,
            "detail": f"{len(unscreened)} of {n_parties} parties not screened.",
            "next_action": "Run the Adverse Media & PEP Screener.",
        }
    if case.cip_status == "review":
        return {
            "state": WAITING_EXTERNAL,
            "detail": "CIP returned review — awaiting provider or manual verification.",
            "next_action": "Resolve the CIP review outcome.",
        }
    if case.edd_status == "edd_pending":
        return {
            "state": BLOCKED,
            "detail": "Enhanced due diligence pending — source of wealth not yet signed off.",
            "next_action": "Complete EDD source-of-wealth corroboration.",
        }
    if case.cip_status != "verified":
        return {
            "state": IN_PROGRESS,
            "detail": "All parties screened; identity not yet verified.",
            "next_action": "Run the CIP identity check.",
        }
    return {
        "state": COMPLETE,
        "detail": "All parties screened, identity verified, no outstanding EDD.",
        "next_action": None,
    }


def _track_d(case, transfers: list) -> dict:
    """Funding — runs on its own clock and may complete long after activation."""
    if not transfers:
        return {
            "state": NOT_STARTED,
            "detail": "No transfer initiated.",
            "next_action": "Initiate funding once the account is open.",
        }
    if any(t.status == _SETTLED for t in transfers):
        return {"state": COMPLETE, "detail": "Funds settled.", "next_action": None}
    if any(t.status in _IN_FLIGHT for t in transfers):
        return {
            "state": WAITING_EXTERNAL,
            "detail": "Transfer in transit with the delivering firm.",
            "next_action": "Awaiting counterparty — no action required.",
        }
    if any(t.status in _STARTED for t in transfers):
        return {
            "state": IN_PROGRESS,
            "detail": "Transfer submitted, not yet in transit.",
            "next_action": "Confirm the transfer was accepted.",
        }
    return {"state": IN_PROGRESS, "detail": "Transfer status unknown.", "next_action": None}


# Which team is waiting on each track, used to route the queue by owner.
_OWNER = {"agreements": "adviser", "account": "ops", "financial_crime": "compliance", "funding": "external"}


def evaluate(case, *, party_status: dict, disclosure_status: dict,
             documents: list, transfers: list) -> dict:
    """Compute all four tracks plus the derived case-level position."""
    tracks = {
        "agreements": _track_a(case, disclosure_status),
        "account": _track_b(case, party_status, documents),
        "financial_crime": _track_c(case, party_status),
        "funding": _track_d(case, transfers),
    }
    for code, key, label, owner_label in TRACKS:
        tracks[key].update({"code": code, "key": key, "label": label, "owner_label": owner_label})

    # A, B and C gate activation; D runs on its own clock and may finish weeks later.
    gating = ["agreements", "account", "financial_crime"]
    gating_states = [tracks[k]["state"] for k in gating]

    blocked = [k for k in gating if tracks[k]["state"] == BLOCKED]
    if case.status == "approved":
        position, owner = "Activated", None
    elif blocked:
        position = f"Blocked on {tracks[blocked[0]]['label'].lower()}"
        owner = _OWNER[blocked[0]]
    elif all(s == COMPLETE for s in gating_states):
        position, owner = "Ready to activate", "adviser"
    elif any(tracks[k]["state"] == WAITING_EXTERNAL for k in gating):
        waiting = next(k for k in gating if tracks[k]["state"] == WAITING_EXTERNAL)
        position, owner = "Waiting on external party", _OWNER[waiting]
    else:
        # Route to whoever owns the least-advanced gating track.
        order = [NOT_STARTED, IN_PROGRESS, WAITING_EXTERNAL, BLOCKED, COMPLETE]
        least = min(gating, key=lambda k: order.index(tracks[k]["state"]))
        position, owner = f"In progress — {tracks[least]['label'].lower()}", _OWNER[least]

    # The next action must come from whatever is actually holding the case up — a blocked
    # track first, then the least-advanced one. Taking the first track with an action
    # simply reported track A every time, regardless of what was really blocking.
    if blocked:
        action_track = blocked[0]
    else:
        order = [NOT_STARTED, IN_PROGRESS, WAITING_EXTERNAL, BLOCKED, COMPLETE]
        candidates = [k for k in gating if tracks[k]["state"] != COMPLETE]
        action_track = (
            min(candidates, key=lambda k: order.index(tracks[k]["state"]))
            if candidates else "funding"
        )
    next_action = tracks[action_track].get("next_action")

    return {
        "tracks": [tracks[key] for _, key, _, _ in TRACKS],
        "position": position,
        "owner": owner,
        "next_action": next_action,
        "ready_to_activate": all(s == COMPLETE for s in gating_states),
        "blocked_tracks": blocked,
    }
