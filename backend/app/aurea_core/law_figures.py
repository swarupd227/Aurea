"""Figures fixed in law, each with where it comes from and the date it was taken (B-120).

A figure the platform does not hold on a client's file - an exemption, a band, a date a rule starts - is a fact about
the law, and the law moves. So every one used by the regulatory countdown, the tax engines and the estate agent lives
here, once, with its source and the day it was read from that source, and the window it applies to. Nothing reads a
figure without being able to say where it came from; one whose window has closed reports itself as expired rather than
being used as if it were current; and a date that has passed is in force, not a countdown at zero.

Adding or updating a figure is a code change with its citation, as a rule is in aurea_core/rulebook.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LawFigure:
    key: str
    label: str
    value: float | None         # None for a figure that is only a date
    unit: str                   # "USD", "GBP", "NZD", "rate" or "date"
    source: str                 # the instrument or the official publication it was read from
    as_of: date                 # the day the figure was taken from that source
    applies_from: date | None = None
    applies_to: date | None = None   # last day it applies; None while no end is set in law

    def status(self, today: date) -> str:
        if self.applies_to is not None and today > self.applies_to:
            return "expired"
        if self.applies_from is not None and today < self.applies_from:
            return "upcoming"
        return "in_force"

    def view(self, today: date) -> dict:
        return {"key": self.key, "label": self.label, "value": self.value, "unit": self.unit, "source": self.source,
                "as_of": self.as_of.isoformat(),
                "applies_from": self.applies_from.isoformat() if self.applies_from else None,
                "applies_to": self.applies_to.isoformat() if self.applies_to else None,
                "status": self.status(today)}


FIGURES: dict[str, LawFigure] = {f.key: f for f in (
    # ── United States ────────────────────────────────────────────────────────────────────────────────
    # The TCJA's higher exemption was due to fall back on 1 Jan 2026. It did not: P.L. 119-21 set the basic
    # exclusion amount at $15M a person from 2026, indexed after. The sunset this module used to count down to
    # is therefore an expired date, and the $7M "post-sunset" figure it used had no source at all.
    LawFigure("us_tcja_sunset", "TCJA exemption sunset (superseded)", None, "date",
              "Tax Cuts and Jobs Act 2017 (P.L. 115-97) §11061; superseded by P.L. 119-21 §70106",
              date(2025, 7, 4), applies_to=date(2025, 12, 31)),
    LawFigure("us_basic_exclusion", "Federal estate and gift basic exclusion, per person", 15_000_000, "USD",
              "P.L. 119-21 (4 Jul 2025) §70106, amending IRC §2010(c)(3)", date(2025, 7, 4),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_estate_tax_rate", "Federal estate tax top rate", 0.40, "rate", "IRC §2001(c)", date(2025, 7, 4)),
    LawFigure("us_annual_gift_exclusion", "Annual gift exclusion, per donor per recipient", 19_000, "USD",
              "IRS Rev. Proc. 2025-32 (2026 inflation adjustments)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    # ── United Kingdom ───────────────────────────────────────────────────────────────────────────────
    LawFigure("uk_nrb", "IHT nil-rate band, per person", 325_000, "GBP",
              "IHTA 1984 Sch 1; frozen to 5 Apr 2030 (Autumn Budget 2024)", date(2024, 10, 30),
              applies_to=date(2030, 4, 5)),
    LawFigure("uk_rnrb", "IHT residence nil-rate band, per person", 175_000, "GBP",
              "IHTA 1984 s 8D; frozen to 5 Apr 2030 (Autumn Budget 2024)", date(2024, 10, 30),
              applies_to=date(2030, 4, 5)),
    LawFigure("uk_rnrb_taper", "RNRB taper threshold (£1 per £2 above)", 2_000_000, "GBP",
              "IHTA 1984 s 8D(5); frozen to 5 Apr 2030 (Autumn Budget 2024)", date(2024, 10, 30),
              applies_to=date(2030, 4, 5)),
    LawFigure("uk_iht_rate", "IHT rate", 0.40, "rate", "IHTA 1984 Sch 1", date(2024, 10, 30)),
    LawFigure("uk_pension_iht", "Unused pension funds enter the IHT estate", None, "date",
              "Autumn Budget 2024 (30 Oct 2024): pensions in IHT from 6 Apr 2027", date(2024, 10, 30),
              applies_from=date(2027, 4, 6)),
    LawFigure("uk_bpr_apr_cap", "BPR/APR 100% relief allowance, per person", 1_000_000, "GBP",
              "Autumn Budget 2024 (30 Oct 2024): 100% relief on the first £1M from 6 Apr 2026", date(2024, 10, 30),
              applies_from=date(2026, 4, 6)),
    # ── New Zealand ──────────────────────────────────────────────────────────────────────────────────
    LawFigure("nz_ks_govt_rate", "KiwiSaver government contribution per $1 contributed", 0.25, "rate",
              "Budget 2025 (22 May 2025): from 1 Jul 2025", date(2025, 5, 22), applies_from=date(2025, 7, 1)),
    LawFigure("nz_ks_govt_max", "KiwiSaver government contribution, yearly maximum", 260.72, "NZD",
              "Budget 2025 (22 May 2025): from 1 Jul 2025", date(2025, 5, 22), applies_from=date(2025, 7, 1)),
    LawFigure("nz_ks_govt_income_limit", "KiwiSaver government contribution: no contribution above this income", 180_000, "NZD",
              "Budget 2025 (22 May 2025): from 1 Jul 2025", date(2025, 5, 22), applies_from=date(2025, 7, 1)),
)}


def get(key: str) -> LawFigure:
    return FIGURES[key]


def value(key: str) -> float:
    figure = FIGURES[key]
    if figure.value is None:
        raise ValueError(f"{key} is a date, not an amount")
    return figure.value


def cited(keys: list[str] | tuple[str, ...], today: date) -> list[dict]:
    """The figures an answer used, each with its source, as-of date and whether it is in force on `today`."""
    return [FIGURES[k].view(today) for k in keys]
