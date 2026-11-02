"""AML/CFT screening (spec Table 16 — AML/CFT 'screening and verification built into
onboarding; exceptions escalated, not auto-cleared').

Deterministic simulation of a screening provider (e.g. a World-Check-style connector): fuzzy-
matches a name against a synthetic PEP / sanctions / adverse-media watchlist and returns hits
with a match score and severity. Sanctions hits BLOCK (cannot be auto-cleared); PEP / adverse
media route to enhanced due diligence. Real screening is wired via a Conduit AML connector."""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

# Synthetic watchlist. Names are fictional and for demo only.
WATCHLIST: list[dict] = [
    {"name": "Viktor Sokolov", "category": "pep", "country": "RU", "dob": "1961-03-14",
     "note": "Senior political figure; enhanced due diligence required."},
    {"name": "Imelda Castellanos", "category": "sanctions", "country": "VE", "dob": "1958-11-02",
     "note": "Appears on a sanctions list; onboarding must not proceed without escalation."},
    {"name": "Marcus Delacroix", "category": "adverse_media", "country": "FR", "dob": "1970-07-21",
     "note": "Adverse media — alleged financial misconduct (unproven)."},
    {"name": "Olena Petrenko", "category": "pep", "country": "UA", "dob": "1975-05-09",
     "note": "Close associate of a domestic political figure."},
]

SEVERITY = {"sanctions": "high", "pep": "medium", "adverse_media": "low"}


@dataclass
class Hit:
    matched_name: str
    category: str
    severity: str
    score: float
    country: str
    note: str
    # What a reviewer compares beyond the name: the date of birth on file against the list's. A name alone
    # decides nothing, and these do not decide either: they are what a person weighs.
    discriminators: dict = field(default_factory=dict)


@dataclass
class ScreeningResult:
    subject: str
    provider: str
    hits: list[Hit] = field(default_factory=list)
    status: str = "clear"  # clear | review | blocked

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "provider": self.provider,
            "status": self.status,
            "hits": [h.__dict__ for h in self.hits],
        }


def _norm(s: str) -> str:
    return " ".join(s.lower().replace(".", " ").split())


def _similarity(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    token = len(ta & tb) / len(ta | tb) if (ta | tb) else 0.0
    return max(seq, token)


def _compare(ours: str | None, theirs: str | None) -> str:
    if not ours or not theirs:
        return "unknown"
    return "match" if ours.strip()[:10] == theirs.strip()[:10] else "differs"


def screen(name: str, *, provider: str = "World-Check (mock)", threshold: float = 0.82, dob: str | None = None) -> ScreeningResult:
    """Screen a single name against the watchlist."""
    result = ScreeningResult(subject=name, provider=provider)
    for entry in WATCHLIST:
        score = _similarity(name, entry["name"])
        if score >= threshold:
            result.hits.append(Hit(
                matched_name=entry["name"], category=entry["category"],
                severity=SEVERITY[entry["category"]], score=round(score, 3),
                country=entry["country"], note=entry["note"],
                discriminators={"dob": _compare(dob, entry.get("dob"))},
            ))
    if any(h.severity == "high" for h in result.hits):
        result.status = "blocked"
    elif result.hits:
        result.status = "review"
    return result


def screen_parties(parties: list[str], *, provider: str = "World-Check (mock)", dobs: dict | None = None) -> dict:
    """Screen a set of associated parties (e.g. applicant + trustees). Returns a summary."""
    results = [screen(p, provider=provider, dob=(dobs or {}).get(p)) for p in parties if p]
    statuses = {r.status for r in results}
    overall = "blocked" if "blocked" in statuses else ("review" if "review" in statuses else "clear")
    return {
        "provider": provider,
        "status": overall,
        "parties": [r.to_dict() for r in results],
        "n_hits": sum(len(r.hits) for r in results),
    }
