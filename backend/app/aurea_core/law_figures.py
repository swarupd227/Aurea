"""Figures fixed in law, each with where it comes from and the date it was taken (B-120).

A figure the platform does not hold on a client's file - an exemption, a band, a date a rule starts - is a fact about
the law, and the law moves. So every one used by the regulatory countdown, the tax engines and the estate agent lives
here, once, with its source and the day it was read from that source, and the window it applies to. Nothing reads a
figure without being able to say where it came from; one whose window has closed reports itself as expired rather than
being used as if it were current; and a date that has passed is in force, not a countdown at zero.

A figure that is set year by year (the US exclusions) keeps one version per year under the same key, and is read with
the firm's today (`firm_time.today`), which picks the version in force. Reading one of those without a date is refused,
so a new year can never silently be answered with last year's figure.

Adding or updating a figure is a code change with its citation, as a rule is in aurea_core/rulebook.py. The figures
below were checked against their sources on 20 Nov 2026 (B-197).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LawFigure:
    key: str
    label: str
    # A number; None for a figure that is only a date; for unit "bands", a table of (upper bound, rate) with the top
    # band's bound None (it has none), read through bands().
    value: float | tuple[tuple[float | None, float], ...] | None
    unit: str                   # "USD", "GBP", "NZD", "rate", "date" or "bands" (USD bounds)
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


ALL: tuple[LawFigure, ...] = (
    # ── United States ────────────────────────────────────────────────────────────────────────────────
    # The TCJA's higher exemption was due to fall back on 1 Jan 2026. It did not: P.L. 119-21 set the basic
    # exclusion amount at $15M a person from 2026, indexed after. The sunset this module used to count down to
    # is therefore an expired date, and the $7M "post-sunset" figure it used had no source at all.
    LawFigure("us_tcja_sunset", "TCJA exemption sunset (superseded)", None, "date",
              "Tax Cuts and Jobs Act 2017 (P.L. 115-97) §11061; superseded by P.L. 119-21 §70106",
              date(2025, 7, 4), applies_to=date(2025, 12, 31)),
    LawFigure("us_basic_exclusion", "Federal estate and gift basic exclusion, per person", 15_000_000, "USD",
              "P.L. 119-21 (4 Jul 2025) §70106, amending IRC §2010(c)(3); IRS Rev. Proc. 2025-32 §2.14", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_basic_exclusion", "Federal estate and gift basic exclusion, per person", 15_480_000, "USD",
              "IRS Rev. Proc. 2026-35 §3.44 (IR-2026-133, 5 Nov 2026), indexing P.L. 119-21 §70106", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_estate_tax_rate", "Federal estate tax top rate", 0.40, "rate", "IRC §2001(c)", date(2025, 7, 4)),
    # Ordinary income brackets, each band's upper bound of taxable income and its rate (IRC §1(j), indexed yearly).
    LawFigure("us_brackets_mfj", "Federal income tax brackets, married filing jointly",
              ((24_800, 0.10), (100_800, 0.12), (211_400, 0.22), (403_550, 0.24), (512_450, 0.32), (768_700, 0.35), (None, 0.37)),
              "bands", "IRS Rev. Proc. 2025-32 §4.01, Table 1 (2026)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_brackets_mfj", "Federal income tax brackets, married filing jointly",
              ((25_600, 0.10), (104_050, 0.12), (218_250, 0.22), (416_650, 0.24), (529_100, 0.32), (793_650, 0.35), (None, 0.37)),
              "bands", "IRS Rev. Proc. 2026-35 §3.01, Table 1 (2027; IR-2026-133, 5 Nov 2026)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_brackets_single", "Federal income tax brackets, single",
              ((12_400, 0.10), (50_400, 0.12), (105_700, 0.22), (201_775, 0.24), (256_225, 0.32), (640_600, 0.35), (None, 0.37)),
              "bands", "IRS Rev. Proc. 2025-32 §4.01, Table 3 (2026)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_brackets_single", "Federal income tax brackets, single",
              ((12_800, 0.10), (52_025, 0.12), (109_125, 0.22), (208_325, 0.24), (264_550, 0.32), (661_375, 0.35), (None, 0.37)),
              "bands", "IRS Rev. Proc. 2026-35 §3.01, Table 3 (2027; IR-2026-133, 5 Nov 2026)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_annual_gift_exclusion", "Annual gift exclusion, per donor per recipient", 19_000, "USD",
              "IRS Rev. Proc. 2025-32 §4.42(1) (2026 inflation adjustments)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_annual_gift_exclusion", "Annual gift exclusion, per donor per recipient", 20_000, "USD",
              "IRS Rev. Proc. 2026-35 §3.46(1) (2027 inflation adjustments; IR-2026-133, 5 Nov 2026)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    # ── United Kingdom ───────────────────────────────────────────────────────────────────────────────
    # Autumn Budget 2025 (26 Nov 2025) extended the freeze of both nil-rate bands and the taper by a year, to 5 Apr 2031.
    LawFigure("uk_nrb", "IHT nil-rate band, per person", 325_000, "GBP",
              "IHTA 1984 Sch 1; frozen to 5 Apr 2031 (HMRC policy paper 'Inheritance Tax thresholds', 26 Nov 2025; "
              "Finance Act 2026)", date(2025, 11, 26), applies_to=date(2031, 4, 5)),
    LawFigure("uk_rnrb", "IHT residence nil-rate band, per person", 175_000, "GBP",
              "IHTA 1984 s 8D; frozen to 5 Apr 2031 (HMRC policy paper 'Inheritance Tax thresholds', 26 Nov 2025; "
              "Finance Act 2026)", date(2025, 11, 26), applies_to=date(2031, 4, 5)),
    LawFigure("uk_rnrb_taper", "RNRB taper threshold (£1 per £2 above)", 2_000_000, "GBP",
              "IHTA 1984 s 8D(5); frozen to 5 Apr 2031 (HMRC policy paper 'Inheritance Tax thresholds', 26 Nov 2025; "
              "Finance Act 2026)", date(2025, 11, 26), applies_to=date(2031, 4, 5)),
    LawFigure("uk_iht_rate", "IHT rate", 0.40, "rate", "IHTA 1984 Sch 1", date(2024, 10, 30)),
    LawFigure("uk_pension_iht", "Unused pension funds enter the IHT estate", None, "date",
              "Finance Act 2026 (HMRC 'Inheritance Tax on pensions' technical note 2, 27 Aug 2026): deaths on or after "
              "6 Apr 2027", date(2026, 8, 27), applies_from=date(2027, 4, 6)),
    # Announced at £1M in the Autumn Budget 2024; raised to £2.5M on 23 Dec 2025 before it was enacted.
    LawFigure("uk_bpr_apr_cap", "BPR/APR 100% relief allowance, per person (unused part transfers to a spouse or civil partner)",
              2_500_000, "GBP",
              "IHTA 1984 ss 124D-124E, inserted by Finance Act 2026 Sch 12 (HMRC manual IHTM25520); fixed to 5 Apr 2031",
              date(2026, 4, 7), applies_from=date(2026, 4, 6), applies_to=date(2031, 4, 5)),
    # ── New Zealand ──────────────────────────────────────────────────────────────────────────────────
    LawFigure("nz_ks_govt_rate", "KiwiSaver government contribution per $1 contributed", 0.25, "rate",
              "Income Tax Act 2007 s MK 4, as amended by the Taxation (Budget Measures) Act 2025 (2025 No 26): from 1 Jul 2025",
              date(2025, 5, 29), applies_from=date(2025, 7, 1)),
    LawFigure("nz_ks_govt_max", "KiwiSaver government contribution, yearly maximum", 260.72, "NZD",
              "Income Tax Act 2007 s MK 4, as amended by the Taxation (Budget Measures) Act 2025 (2025 No 26): from 1 Jul 2025",
              date(2025, 5, 29), applies_from=date(2025, 7, 1)),
    LawFigure("nz_ks_govt_income_limit", "KiwiSaver government contribution: none for taxable income above this", 180_000, "NZD",
              "Income Tax Act 2007 ss MK 1-MK 3, as amended by the Taxation (Budget Measures) Act 2025 (2025 No 26): "
              "from 1 Jul 2025", date(2025, 5, 29), applies_from=date(2025, 7, 1)),
)

VERSIONS: dict[str, tuple[LawFigure, ...]] = {}
for _f in ALL:
    VERSIONS[_f.key] = tuple(sorted((*VERSIONS.get(_f.key, ()), _f), key=lambda f: f.applies_from or date.min))
del _f


def get(key: str, today: date | None = None) -> LawFigure:
    """The figure, and for one set year by year, the version in force on `today` (the firm's date). Past every window
    it is the latest, which reports itself expired; before every window, the first, which reports itself upcoming."""
    versions = VERSIONS[key]
    if len(versions) == 1:
        return versions[0]
    if today is None:
        raise ValueError(f"{key} is set year by year: read it with the firm's today")
    for f in versions:
        if f.status(today) == "in_force":
            return f
    started = [f for f in versions if f.applies_from is None or f.applies_from <= today]
    return started[-1] if started else versions[0]


def value(key: str, today: date | None = None) -> float:
    figure = get(key, today)
    if figure.value is None:
        raise ValueError(f"{key} is a date, not an amount")
    if figure.unit == "bands":
        raise ValueError(f"{key} is a table of bands: read it with bands()")
    return figure.value


def bands(key: str, today: date | None = None) -> list[tuple[float, float]]:
    """A table of (upper bound, rate), lowest band first; the top band's bound is infinity."""
    figure = get(key, today)
    if figure.unit != "bands":
        raise ValueError(f"{key} is not a table of bands")
    return [(float("inf") if bound is None else float(bound), rate) for bound, rate in figure.value]


def cited(keys: list[str] | tuple[str, ...], today: date) -> list[dict]:
    """The figures an answer used, each with its source, as-of date and whether it is in force on `today`."""
    return [get(k, today).view(today) for k in keys]
