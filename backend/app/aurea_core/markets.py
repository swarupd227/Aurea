"""Which market a firm or household is in, and what that fixes.

A market is what differs by country and is not itself a rule: its currency, its calendar, its tax
year, how its money is written. The rules live in their own packs (compliance/ontology.py,
aurea_core/tax_india.py) and this says which pack applies.

A country with no market here is not supported, and each caller says so. It used to fall through
to New Zealand, so an Indian firm received New Zealand law that looked like a working product, and
"Securities and Exchange Board of India" matched "SEC" and received American law.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Market:
    code: str
    name: str
    currency: str
    locale: str                     # for the browser's Intl formatting
    zone: str
    regime: str | None              # compliance framework (ontology.FRAMEWORKS)
    tax_report: bool                # tax_intelligence has a report for it
    tax_year_starts: tuple[int, int] = (1, 1)  # month, day
    statutory_fifo: bool = False    # the law fixes which lots a sale relieves; no firm setting may change it


MARKETS: dict[str, Market] = {m.code: m for m in (
    Market("NZ", "New Zealand", "NZD", "en-NZ", "Pacific/Auckland", "NZ-FMA", True, (4, 1)),
    Market("US", "United States", "USD", "en-US", "America/New_York", "US-SEC", True),
    Market("UK", "United Kingdom", "GBP", "en-GB", "Europe/London", "UK-FCA", True, (4, 6)),
    Market("EU", "European Union", "EUR", "en-IE", "Europe/Brussels", "EU-MiFID", False),
    Market("IN", "India", "INR", "en-IN", "Asia/Kolkata", "IN-SEBI", True, (4, 1), statutory_fifo=True),
)}

_ALIASES = {"GB": "UK", "UNITED KINGDOM": "UK", "USA": "US", "UNITED STATES": "US", "INDIA": "IN",
            "NEW ZEALAND": "NZ"}


def code(value: str | None) -> str:
    """The canonical code for a stored jurisdiction. Empty is the column's own default."""
    raw = str(value or "NZ").strip().upper()
    return _ALIASES.get(raw, raw)


def get(value: str | None) -> Market | None:
    """The market, or None when this platform has no rules for it. Never a default."""
    return MARKETS.get(code(value))


def for_firm(firm) -> Market | None:
    return get(getattr(firm, "jurisdiction", None))


def of_household(firm, values: dict | None) -> Market | None:
    """A household's own jurisdiction, else its firm's: the reading every tax engine makes."""
    return get((values or {}).get("jurisdiction") or getattr(firm, "jurisdiction", None))


def currency(jurisdiction: str | None, fallback: str | None = None) -> str:
    """The currency of a jurisdiction. Where it has no market the firm's own books are the answer."""
    market = get(jurisdiction)
    return market.currency if market else (fallback or "")


def tax_year(market: Market, day: date) -> dict:
    """The tax year `day` falls in. India names it for both calendar years: "2026-27"."""
    month, start_day = market.tax_year_starts
    began = day.year if (day.month, day.day) >= (month, start_day) else day.year - 1
    start = date(began, month, start_day)
    # The day before the next one starts. Worked from the start, so 1 Jan years end 31 Dec.
    end = date.fromordinal(date(began + 1, month, start_day).toordinal() - 1)
    label = str(began) if (month, start_day) == (1, 1) else f"{began}-{(began + 1) % 100:02d}"
    return {"label": label, "start": start, "end": end, "days_left": (end - day).days}


_SYMBOLS = {"USD": "$", "NZD": "$", "AUD": "$", "SGD": "$", "GBP": "£", "EUR": "€", "INR": "₹"}


def _lakh_crore(whole: int) -> str:
    """Indian digit grouping: the last three, then twos. 12345678 reads 1,23,45,678."""
    digits = str(whole)
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while head:
        groups.insert(0, head[-2:])
        head = head[:-2]
    return f"{','.join(groups)},{tail}"


def money(amount: float, ccy: str | None, *, dp: int = 0) -> str:
    """An amount as the people who use that currency write it."""
    rendered = f"{abs(float(amount)):,.{dp}f}"
    if ccy == "INR":
        whole, _, fraction = rendered.replace(",", "").partition(".")
        rendered = _lakh_crore(int(whole)) + (f".{fraction}" if fraction else "")
    symbol = _SYMBOLS.get(ccy or "", f"{ccy} " if ccy else "")
    return f"{'-' if float(amount) < 0 else ''}{symbol}{rendered}"


def money_for(firm) -> Callable[[float], str]:
    """A formatter in the firm's own currency, for sentences an agent writes about its books."""
    ccy = getattr(firm, "base_currency", None) or "USD"
    return lambda amount: money(amount, ccy)
