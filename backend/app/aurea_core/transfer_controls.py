"""Pre-submission transfer controls (L200 §2.4, §5).

Two failure modes, both with a control that has to run *before* the transfer is submitted:

  ACAT title mismatch     "reject, restart, client frustration"
                          -> pre-submission title verification against the delivering-firm
                             statement

  Third-party wire fraud  "client funds stolen"
                          -> callback verification on a recorded line to the number of
                             record

Both are cheap to check and expensive to miss, which is exactly the shape of control that
belongs in a gate rather than a checklist someone remembers.
"""
from __future__ import annotations

import re

NOT_CHECKED, MATCH, MISMATCH, REVIEW = "not_checked", "match", "mismatch", "review"

# Ownership and entity words that differ harmlessly between firms' title conventions:
# "SMITH JOHN A" vs "John A Smith", "Acme LLC" vs "ACME L.L.C."
_NOISE = {
    "the", "and", "a", "an", "of", "trust", "trustee", "trustees", "ttee", "tr",
    "llc", "l.l.c", "inc", "incorporated", "corp", "corporation", "ltd", "limited",
    "lp", "llp", "partnership", "co", "company", "family", "revocable", "living",
    "jt", "jtwros", "tic", "ten", "com", "survivorship", "ira", "roth", "sep",
    "custodian", "cust", "fbo", "utma", "ugma", "estate", "dtd", "dated",
}


def _tokens(title: str) -> set[str]:
    """Comparable name tokens — case, punctuation and entity words removed."""
    cleaned = re.sub(r"[^a-z0-9\s]", " ", (title or "").lower())
    return {t for t in cleaned.split() if t and t not in _NOISE and not t.isdigit()}


def check_title(delivering_title: str | None, party_names: list[str]) -> dict:
    """Compare a delivering-firm account title against the parties of record.

    Deliberately conservative: anything short of a clean match is surfaced for a human
    rather than auto-passed. A false reassurance here costs the client a rejected ACAT and
    a restart, which is precisely what the control exists to prevent.
    """
    if not delivering_title or not delivering_title.strip():
        return {
            "status": NOT_CHECKED,
            "note": "No delivering-firm account title recorded to verify against.",
        }
    if not party_names:
        return {
            "status": REVIEW,
            "note": "No parties recorded on the case to compare the title against.",
        }

    title_tokens = _tokens(delivering_title)
    if not title_tokens:
        return {"status": REVIEW, "note": "Account title has no comparable name tokens."}

    party_tokens: set[str] = set()
    for name in party_names:
        party_tokens |= _tokens(name)

    overlap = title_tokens & party_tokens
    missing = title_tokens - party_tokens

    if not overlap:
        return {
            "status": MISMATCH,
            "note": (
                f"No name on the delivering account title matches any party of record. "
                f"Title reads \"{delivering_title}\"; parties are "
                f"{', '.join(party_names)}."
            ),
        }
    if missing:
        return {
            "status": REVIEW,
            "note": (
                f"Partial match — {', '.join(sorted(missing))} on the delivering title "
                f"{'is' if len(missing) == 1 else 'are'} not a recorded party. "
                "Confirm against the statement before submitting."
            ),
        }
    return {
        "status": MATCH,
        "note": f"Title matches parties of record ({', '.join(sorted(overlap))}).",
    }


def transfer_readiness(transfer, *, party_names: list[str]) -> dict:
    """Whether a single transfer is safe to submit, and why not."""
    problems: list[str] = []

    if transfer.transfer_type == "acat" and transfer.direction == "in":
        if transfer.title_match_status == MISMATCH:
            problems.append(
                f"ACAT title mismatch — {transfer.title_match_note or 'titles do not match'}"
            )
        elif transfer.title_match_status in (NOT_CHECKED, REVIEW):
            problems.append(
                "ACAT title not verified against the delivering-firm statement."
            )

    # Third-party movements are the imposter-fraud vector; first-party are not.
    if transfer.is_third_party and not transfer.callback_verified_at:
        problems.append(
            "Third-party transfer without callback verification on a recorded line "
            "to the number of record."
        )

    return {
        "id": str(transfer.id),
        "ready": not problems,
        "problems": problems,
    }


def status_for(transfers: list, *, party_names: list[str]) -> dict:
    """Transfer control status across a case."""
    checks = [transfer_readiness(t, party_names=party_names) for t in transfers]
    blocking = [c for c in checks if not c["ready"]]

    acat_in = [t for t in transfers if t.transfer_type == "acat" and t.direction == "in"]
    unverified_titles = [
        t for t in acat_in if t.title_match_status in (NOT_CHECKED, REVIEW, MISMATCH)
    ]
    third_party = [t for t in transfers if t.is_third_party]
    uncalled = [t for t in third_party if not t.callback_verified_at]

    return {
        "n_transfers": len(transfers),
        "checks": checks,
        "blocking": [p for c in blocking for p in c["problems"]],
        "unverified_titles": len(unverified_titles),
        "third_party": len(third_party),
        "callbacks_outstanding": len(uncalled),
        # Funding controls gate *submission of the transfer*, not activation of the
        # account — L200 is explicit that funding runs on its own clock and may complete
        # weeks after the account opens.
        "blocks_submission": bool(blocking),
    }
