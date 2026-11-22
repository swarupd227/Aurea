"""A market's suggested planning assumptions, dated and with their source (India row 5).

A projection runs on figures nobody can know: inflation, returns, when work stops, how long to plan for. The
platform's defaults are New Zealand and US shaped (2.5% inflation, retirement at 65, planning to 95), which
mis-state an Indian retirement. A market here carries its own suggestions, each with where it came from.

These are analyst suggestions, not regulatory values: India has no official assumption set, and an exam case is
not one. A firm's own setting always replaces a suggestion, and every reader is told which applied
(`planning.assumption_set`). A market with none here keeps the platform's defaults, as before.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Assumption:
    key: str
    label: str
    value: float
    source: str
    as_of: str

    def view(self) -> dict:
        return {"key": self.key, "label": self.label, "value": self.value, "source": self.source, "as_of": self.as_of,
                "basis": "suggested, not a regulatory value"}


_REPORT = "Research report, India market alignment (3.6), 31 Oct 2026"

SUGGESTED: dict[str, dict[str, Assumption]] = {
    "IN": {a.key: a for a in (
        Assumption("inflation", "Inflation", 0.055,
                   f"Within NISM's 4-7% planning range; RBI's 4% is a target, not a planning figure. {_REPORT}", "2026-10"),
        Assumption("education_inflation", "Education inflation", 0.09,
                   f"Within the 8-10% range in press and planner sources; no official series. {_REPORT}", "2026-10"),
        Assumption("retirement_age", "Retirement age", 60,
                   f"58 to 60 is common in private employment and EPS pensions start at 58; no national rule. {_REPORT}", "2026-10"),
        Assumption("plan_to_age", "Plan to age", 90,
                   f"A planning choice within 85-90. SRS 2016-20 expectancy at 60 is 18.3 years, which is not a plan horizon. {_REPORT}", "2026-10"),
        Assumption("equity_return", "Equity return, pre-tax", 0.11,
                   f"Within NISM's 10-15% range. {_REPORT}", "2026-10"),
        Assumption("fixed_income_return", "Fixed income return, pre-tax", 0.07,
                   f"Within NISM's 6-9% range. {_REPORT}", "2026-10"),
    )},
}

# Which return assumption overlays which asset class's expected return. Volatility stays the platform's.
RETURN_KEYS = {"equity": "equity_return", "fixed_income": "fixed_income_return"}


def for_market(jurisdiction: str | None) -> dict[str, Assumption]:
    return SUGGESTED.get((jurisdiction or "").upper(), {})


def value(firm_settings: dict | None, jurisdiction: str | None, key: str, default):
    """A firm's own setting, else the market's suggestion, else the platform default; and which it was."""
    s = firm_settings or {}
    if s.get(key) not in (None, ""):
        try:
            return type(default)(s[key]), "firm"
        except (TypeError, ValueError):
            pass
    suggested = for_market(jurisdiction).get(key)
    if suggested is not None:
        return type(default)(suggested.value), "market"
    return default, "platform"
