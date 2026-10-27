"""Shared price rules - what "fresh" means, and how a dense price history is read monthly.

Prices are stored as they arrive: a close per trading day. Several consumers are built on a
monthly view (the performance engine, GIPS composites), and counting days as months would
silently scale a standard deviation and a "months of history" disclosure by about twenty.
`month_end_points` is the one place that reduces a history to its month-end closes, so the
answer to "what is this instrument's monthly series" cannot differ between callers.
"""
from __future__ import annotations

from datetime import date

# A price this old or newer is current: it covers a weekend, or a market holiday either side.
PRICE_FRESH_DAYS = 5


def month_end_points(points: dict[date, float]) -> dict[date, float]:
    """The last close in each calendar month, keyed by the date it was struck on.

    Older history here is stored as monthly bars, dated the first of the month but carrying
    that month's closing price; a month that also has daily rows resolves to its last daily
    row, which is the same quantity. The latest, possibly part-month, point is kept as the
    "to date" figure."""
    last: dict[tuple[int, int], date] = {}
    for d in points:
        key = (d.year, d.month)
        if key not in last or d > last[key]:
            last[key] = d
    return {d: points[d] for d in sorted(last.values())}


def range_for_gap(days: int | None) -> str:
    """The smallest Yahoo range that covers a gap of `days` since the last stored price."""
    if days is None:
        return "1y"
    if days <= 3:
        return "5d"
    if days <= 25:
        return "1mo"
    if days <= 80:
        return "3mo"
    if days <= 165:
        return "6mo"
    if days <= 350:
        return "1y"
    return "2y"
