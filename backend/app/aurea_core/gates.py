"""Activation gates (L200 §5).

Every control L200 names is phrased as a gate — "system-enforced delivery gates before
account activation", "party-model completeness checks". This module makes them literal:
each gate has a key, a state, and whether it blocks activation.

One aggregator, read by both the API (so the UI can explain what is blocking) and the
onboarding agent's act() (so the block is enforced server-side). A gate list the UI
computed separately from the one the server enforced would eventually disagree, and the
disagreement would surface as a button that does nothing.

Gate states:
    pass   satisfied
    fail   not satisfied — blocks activation if `blocks` is set
    warn   worth knowing, never blocks
    n/a    does not apply to this registration type
"""
from __future__ import annotations

PASS, FAIL, WARN, NA = "pass", "fail", "warn", "n/a"

_ENTITY_TYPES = {"entity_llc", "entity_corp", "entity_partnership"}


def _gate(key, label, state, detail, *, blocks=True, track=None) -> dict:
    return {
        "key": key, "label": label, "state": state, "detail": detail,
        # A gate only blocks when it actually failed.
        "blocks": bool(blocks and state == FAIL),
        "track": track,
    }


def evaluate(case, *, party_status: dict, disclosure_status: dict) -> dict:
    """All activation gates for a case, and whether any of them blocks."""
    gates: list[dict] = []
    reg = case.registration_type or "individual"

    # ── Track A — disclosures ────────────────────────────────────────────────
    outstanding = disclosure_status.get("outstanding", [])
    required = disclosure_status.get("required_count", 0)
    delivered = disclosure_status.get("delivered_count", 0)
    gates.append(_gate(
        "disclosures_delivered", "Disclosures delivered and evidenced",
        FAIL if outstanding else PASS,
        f"{delivered} of {required} delivered."
        + (f" Outstanding: {', '.join(outstanding)}." if outstanding else ""),
        track="agreements",
    ))

    # ── Track B — party completeness ─────────────────────────────────────────
    role_gaps = party_status.get("role_gaps", [])
    gates.append(_gate(
        "parties_complete", "Required parties recorded",
        FAIL if role_gaps else PASS,
        "; ".join(f"{g['label']} ({g['have']}/{g['need']})" for g in role_gaps)
        or f"{party_status.get('n_parties', 0)} party(ies) recorded.",
        track="account",
    ))

    ownership_issues = party_status.get("ownership_issues", [])
    gates.append(_gate(
        "ownership_certified", "Beneficial ownership certified",
        NA if reg not in _ENTITY_TYPES else (FAIL if ownership_issues else PASS),
        "Not applicable to this registration type."
        if reg not in _ENTITY_TYPES
        else ("; ".join(o["detail"] for o in ownership_issues) or "25% owners and control person certified."),
        track="account",
    ))

    # ── Track C — financial crime ────────────────────────────────────────────
    unscreened = party_status.get("unscreened", [])
    n_parties = party_status.get("n_parties", 0)
    gates.append(_gate(
        "parties_screened", "Every party screened",
        FAIL if (unscreened or not n_parties) else PASS,
        "No parties to screen." if not n_parties
        else (f"{len(unscreened)} unscreened: "
              + ", ".join(p["legal_name"] for p in unscreened) + "."
              if unscreened else f"All {n_parties} parties screened."),
        track="financial_crime",
    ))

    sanctions_blocked = (case.screening or {}).get("status") == "blocked"
    gates.append(_gate(
        "no_sanctions_match", "No unresolved sanctions match",
        FAIL if sanctions_blocked else PASS,
        "Sanctions match must be dispositioned by compliance."
        if sanctions_blocked else "No blocking match.",
        track="financial_crime",
    ))

    gates.append(_gate(
        "cip_verified", "Identity verified (CIP)",
        PASS if case.cip_status == "verified" else FAIL,
        f"CIP status: {case.cip_status or 'not run'}.",
        track="financial_crime",
    ))

    gates.append(_gate(
        "edd_resolved", "Enhanced due diligence resolved",
        NA if case.edd_status in (None, "", "none") else
        (FAIL if case.edd_status == "edd_pending" else PASS),
        "No EDD required." if case.edd_status in (None, "", "none")
        else f"EDD status: {case.edd_status}.",
        track="financial_crime",
    ))

    # ── Advisory — never blocks ──────────────────────────────────────────────
    gates.append(_gate(
        "nigo_clear", "Pre-submission check clear",
        WARN if case.nigo_flag else (PASS if case.readiness_score is not None else NA),
        case.nigo_reason or (
            f"Readiness {case.readiness_score}/100."
            if case.readiness_score is not None
            else "Pre-submission check not yet run."
        ),
        blocks=False,
        track="account",
    ))

    blocking = [g for g in gates if g["blocks"]]
    return {
        "gates": gates,
        "blocking": [g["key"] for g in blocking],
        "blocks_activation": bool(blocking),
        "passed": sum(1 for g in gates if g["state"] == PASS),
        "total": sum(1 for g in gates if g["state"] != NA),
        # Rendered on the disabled activation control so the reason is never a mystery.
        "summary": (
            f"{len(blocking)} gate(s) blocking activation"
            if blocking else "All activation gates satisfied"
        ),
    }
