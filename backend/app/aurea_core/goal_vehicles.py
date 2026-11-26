"""Which kinds of vehicle suit a goal by how far off it is, laid out as categories (B-394, L200-2 India section 5.3).

A planning convention as the India module states it, not a rule of law and not reviewed: categories and the reason for
each, as data. It names no product, scheme, fund or share of anything, it reads nothing about the client's money beyond
the goal's horizon and kind, and it recommends nothing: what a household holds is a person's decision, under their
licence. Sukanya Samriddhi's age condition is the law figure `in_ssy_rule`; whether a child qualifies is not assessed.

Laid out for a household of an Indian market only: no other market has a mapping on file here.
"""
from __future__ import annotations

from datetime import date

from app.aurea_core import law_figures

SOURCE = "L200-2 India edition section 5.3, as understood: a planning convention, not law and not reviewed"
SHORT_UNDER, LONG_FROM = 3, 7          # years: under three is short, seven or more is long
BANDS = {"short": f"Under {SHORT_UNDER} years", "medium": f"{SHORT_UNDER} to {LONG_FROM} years", "long": f"{LONG_FROM} years or more"}
NOTE = "categories only: no product, no allocation, not advice"

_ROWS = {
    "short": [
        ("Fixed deposits", "Known value on a near date", None),
        ("Liquid and short-duration debt funds", "Little price movement over a short wait", None),
        ("Arbitrage funds", "Debt-like movement, taxed differently", "Upper tax slab: the tax is not worked here"),
    ],
    "medium": [
        ("Hybrid funds", "Some growth with less movement than equity", None),
        ("Balanced advantage funds", "Equity share varies inside the fund", None),
        ("Falling equity share", "A fall near the date has no time to recover", "As the date approaches"),
    ],
    "long": [
        ("Equity funds through SIPs", "Time to ride out falls; the engine for long goals", None),
        ("Systematic transfer to debt", "Moves the corpus out of equity before it is spent", "In the final years before the date"),
    ],
}
_EDUCATION_LONG = [
    ("Public Provident Fund", "Tax-sheltered debt beside equity for education", "Limits and lock-in apply"),
    ("Sukanya Samriddhi Account", "Government-backed debt for a daughter's goal", "Girl child under 10: eligibility not assessed"),
]


def band_of(years: int) -> str:
    return "short" if years < SHORT_UNDER else "long" if years >= LONG_FROM else "medium"


def for_goal(kind: str, years: int, today: date) -> dict:
    band = band_of(int(years))
    rows = _ROWS[band] + (_EDUCATION_LONG if band == "long" and kind == "education" else [])
    out = {"band": band, "label": BANDS[band], "source": SOURCE, "note": NOTE,
           "rows": [{"category": c, "reason": r, "condition": k} for c, r, k in rows]}
    if any(c == "Sukanya Samriddhi Account" for c, _, _ in rows):
        out["law"] = law_figures.cited(["in_ssy_rule"], today)
    return out


def applies(market) -> bool:
    return bool(market and market.code == "IN")
