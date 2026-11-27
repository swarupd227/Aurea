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
below were checked against their sources on 20 Nov 2026 (B-197); the deductions, Social Security's base
amounts and Medicare's IRMAA tiers on 22 Nov 2026 (B-190).
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
    # "USD", "GBP", "NZD", "INR", "rate", "date", "bands" (USD bounds) or "tiers" (USD bounds, then the monthly amounts
    # each tier adds, read through tiers())
    unit: str
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
    # The standard deduction and its additions, which turn income into taxable income (IRC §63; B-190).
    LawFigure("us_standard_deduction_mfj", "Standard deduction, married filing jointly", 32_200, "USD",
              "IRS Rev. Proc. 2025-32 §4.14(1) (2026)", date(2025, 10, 9), applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_standard_deduction_mfj", "Standard deduction, married filing jointly", 33_200, "USD",
              "IRS Rev. Proc. 2026-35 §3.15(1) (2027; IR-2026-133, 5 Nov 2026)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_standard_deduction_single", "Standard deduction, single", 16_100, "USD",
              "IRS Rev. Proc. 2025-32 §4.14(1) (2026)", date(2025, 10, 9), applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_standard_deduction_single", "Standard deduction, single", 16_600, "USD",
              "IRS Rev. Proc. 2026-35 §3.15(1) (2027; IR-2026-133, 5 Nov 2026)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_aged_addition_married", "Additional standard deduction at 65 or over, married, per person", 1_650, "USD",
              "IRS Rev. Proc. 2025-32 §4.14(3), IRC §63(f) (2026)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_aged_addition_married", "Additional standard deduction at 65 or over, married, per person", 1_700, "USD",
              "IRS Rev. Proc. 2026-35 §3.15(3), IRC §63(f) (2027)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_aged_addition_unmarried", "Additional standard deduction at 65 or over, unmarried", 2_050, "USD",
              "IRS Rev. Proc. 2025-32 §4.14(3), IRC §63(f) (2026)", date(2025, 10, 9),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_aged_addition_unmarried", "Additional standard deduction at 65 or over, unmarried", 2_100, "USD",
              "IRS Rev. Proc. 2026-35 §3.15(3), IRC §63(f) (2027)", date(2026, 11, 5),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    # The deduction for seniors: $6,000 for each person 65 or over, 2025 to 2028 only, less 6% of modified AGI over the
    # line. The line is not indexed. It ends with 2028, so a year after reads it as expired and adds nothing.
    LawFigure("us_senior_deduction", "Deduction for seniors, per person 65 or over", 6_000, "USD",
              "P.L. 119-21 §70103, IRC §151(d)(5)(C) (IRS: One Big Beautiful Bill Act, tax deductions for working Americans "
              "and seniors)", date(2025, 7, 4), applies_from=date(2025, 1, 1), applies_to=date(2028, 12, 31)),
    LawFigure("us_senior_deduction_line_mfj", "Deduction for seniors: reduced above this modified AGI, joint return", 150_000, "USD",
              "P.L. 119-21 §70103, IRC §151(d)(5)(C)(ii)", date(2025, 7, 4), applies_from=date(2025, 1, 1), applies_to=date(2028, 12, 31)),
    LawFigure("us_senior_deduction_line_single", "Deduction for seniors: reduced above this modified AGI, other returns", 75_000, "USD",
              "P.L. 119-21 §70103, IRC §151(d)(5)(C)(ii)", date(2025, 7, 4), applies_from=date(2025, 1, 1), applies_to=date(2028, 12, 31)),
    LawFigure("us_senior_deduction_taper", "Deduction for seniors: reduced by this share of modified AGI over the line", 0.06, "rate",
              "P.L. 119-21 §70103, IRC §151(d)(5)(C)(ii)", date(2025, 7, 4), applies_from=date(2025, 1, 1), applies_to=date(2028, 12, 31)),
    # How much of a Social Security benefit is taxed (IRC §86): the base amounts are fixed in the statute, not indexed.
    LawFigure("us_ss_base_mfj", "Social Security taxed above this provisional income, joint return (base amount)", 32_000, "USD",
              "IRC §86(c)(1)(B)", date(1993, 8, 10)),
    LawFigure("us_ss_adjusted_base_mfj", "Social Security taxed at up to 85% above this, joint return (adjusted base amount)", 44_000, "USD",
              "IRC §86(c)(2)(B), added by OBRA 1993 §13215", date(1993, 8, 10)),
    LawFigure("us_ss_base_single", "Social Security taxed above this provisional income, single (base amount)", 25_000, "USD",
              "IRC §86(c)(1)(A)", date(1993, 8, 10)),
    LawFigure("us_ss_adjusted_base_single", "Social Security taxed at up to 85% above this, single (adjusted base amount)", 34_000, "USD",
              "IRC §86(c)(2)(A), added by OBRA 1993 §13215", date(1993, 8, 10)),
    # Giving (B-192). A qualified charitable distribution from an IRA, once the owner has reached 70 and a half, is kept
    # out of income up to a yearly limit, indexed; a one-time gift to a split-interest entity sits inside it.
    LawFigure("us_qcd_age", "Qualified charitable distribution: the IRA owner has reached this age", 70.5, "age",
              "IRC §408(d)(8)(B)(ii)", date(2006, 8, 17)),
    LawFigure("us_qcd_limit", "Qualified charitable distributions kept out of income, a year per IRA owner", 111_000, "USD",
              "IRS Notice 2025-67 (IR-2025-111, 13 Nov 2025), IRC §408(d)(8)(A)", date(2025, 11, 13),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_qcd_limit", "Qualified charitable distributions kept out of income, a year per IRA owner", 114_000, "USD",
              "IRS Notice 2026-66 (IR-2026-136, 12 Nov 2026), IRC §408(d)(8)(A)", date(2026, 11, 12),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_qcd_split_interest", "One-time QCD to a split-interest entity (inside the yearly limit)", 55_000, "USD",
              "IRS Notice 2025-67 (IR-2025-111, 13 Nov 2025), IRC §408(d)(8)(F)", date(2025, 11, 13),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_qcd_split_interest", "One-time QCD to a split-interest entity (inside the yearly limit)", 57_000, "USD",
              "IRS Notice 2026-66 (IR-2026-136, 12 Nov 2026), IRC §408(d)(8)(F)", date(2026, 11, 12),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    # From 2026: a deduction for cash gifts by those who do not itemize (not to a donor-advised fund), a floor of 0.5% of
    # AGI under what an itemizer deducts, and an itemized deduction worth at most 35% to the top bracket.
    LawFigure("us_nonitemizer_charity_mfj", "Cash gifts deducted without itemizing, joint return (not to a donor-advised fund)",
              2_000, "USD", "P.L. 119-21 §70424 (IRS: permanent charitable contribution deduction for taxpayers who do not itemize)",
              date(2025, 7, 4), applies_from=date(2026, 1, 1)),
    LawFigure("us_nonitemizer_charity_single", "Cash gifts deducted without itemizing, other returns (not to a donor-advised fund)",
              1_000, "USD", "P.L. 119-21 §70424 (IRS: permanent charitable contribution deduction for taxpayers who do not itemize)",
              date(2025, 7, 4), applies_from=date(2026, 1, 1)),
    LawFigure("us_charity_floor", "Itemized charitable gifts deductible only above this share of AGI", 0.005, "rate",
              "P.L. 119-21 §70425 (IRS Publication 505, 2026)", date(2025, 7, 4), applies_from=date(2026, 1, 1)),
    LawFigure("us_itemized_value_cap", "Itemized deductions worth at most this rate to the top bracket", 0.35, "rate",
              "P.L. 119-21 §70111, IRC §68 (the 2/37 reduction)", date(2025, 7, 4), applies_from=date(2026, 1, 1)),
    LawFigure("us_charity_cash_limit", "Cash gifts to a public charity or donor-advised fund deductible up to this share of AGI",
              0.60, "rate", "IRC §170(b)(1)(G), made permanent by P.L. 119-21", date(2025, 7, 4)),
    LawFigure("us_charity_stock_limit", "Long-held appreciated stock given to a public charity deductible up to this share of AGI",
              0.30, "rate", "IRC §170(b)(1)(C)", date(2025, 7, 4)),
    # Medicare Part B's standard monthly premium, by year (B-191). What the firm states for healthcare on Medicare is
    # beyond it.
    LawFigure("us_part_b_premium", "Medicare Part B standard monthly premium", 202.90, "USD",
              "CMS fact sheet, 2026 Medicare Parts A & B Premiums and Deductibles (14 Nov 2025)", date(2025, 11, 14),
              applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_part_b_premium", "Medicare Part B standard monthly premium", 209.40, "USD",
              "CMS fact sheet, 2027 Medicare Parts A & B Premiums and Deductibles (17 Nov 2026)", date(2026, 11, 17),
              applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    # Medicare's income-related monthly adjustment (IRMAA), by premium year: each tier's upper bound of modified AGI from
    # the tax return two years before, then the monthly Part B and Part D amounts it adds per person. A bound is "at or
    # below", except the last finite one, which is "below" (the top tier starts at it).
    LawFigure("us_irmaa_mfj", "Medicare IRMAA tiers, joint return (premium year)",
              ((218_000, 0.0, 0.0), (274_000, 81.20, 14.50), (342_000, 202.90, 37.50), (410_000, 324.60, 60.40),
               (750_000, 446.30, 83.30), (None, 487.00, 91.00)),
              "tiers", "CMS fact sheet, 2026 Medicare Parts A & B Premiums and Deductibles (14 Nov 2025); 42 CFR 418.2115",
              date(2025, 11, 14), applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_irmaa_mfj", "Medicare IRMAA tiers, joint return (premium year)",
              ((224_000, 0.0, 0.0), (282_000, 83.70, 15.40), (354_000, 209.40, 39.70), (424_000, 335.00, 64.00),
               (750_000, 460.60, 88.30), (None, 502.40, 96.40)),
              "tiers", "CMS fact sheet, 2027 Medicare Parts A & B Premiums and Deductibles (17 Nov 2026); 42 CFR 418.2115",
              date(2026, 11, 17), applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
    LawFigure("us_irmaa_single", "Medicare IRMAA tiers, individual return (premium year)",
              ((109_000, 0.0, 0.0), (137_000, 81.20, 14.50), (171_000, 202.90, 37.50), (205_000, 324.60, 60.40),
               (500_000, 446.30, 83.30), (None, 487.00, 91.00)),
              "tiers", "CMS fact sheet, 2026 Medicare Parts A & B Premiums and Deductibles (14 Nov 2025); 42 CFR 418.2115",
              date(2025, 11, 14), applies_from=date(2026, 1, 1), applies_to=date(2026, 12, 31)),
    LawFigure("us_irmaa_single", "Medicare IRMAA tiers, individual return (premium year)",
              ((112_000, 0.0, 0.0), (141_000, 83.70, 15.40), (177_000, 209.40, 39.70), (212_000, 335.00, 64.00),
               (500_000, 460.60, 88.30), (None, 502.40, 96.40)),
              "tiers", "CMS fact sheet, 2027 Medicare Parts A & B Premiums and Deductibles (17 Nov 2026); 42 CFR 418.2115",
              date(2026, 11, 17), applies_from=date(2027, 1, 1), applies_to=date(2027, 12, 31)),
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
    # ── India ────────────────────────────────────────────────────────────────────────────────────────
    # An Investment Adviser's fee to an individual or HUF who is not an accredited investor, per family of client
    # across all services, a year. Statutory charges are outside it, and AUA is the value of securities in SEBI's
    # purview, shown by demat or unit statements. The fixed limit is revised by IAASB every three years on the
    # Cost Inflation Index, so it is checked against the circular before a client relies on it.
    LawFigure("in_ia_fee_fixed_cap", "Investment Adviser fee cap, fixed-fee mode, per family a year", 151_000, "INR",
              "SEBI Master Circular for Investment Advisers (6 Feb 2026) para 1(iii)", date(2026, 2, 6)),
    LawFigure("in_ia_fee_aua_cap", "Investment Adviser fee cap, AUA mode, share of AUA per family a year", 0.025, "rate",
              "SEBI Master Circular for Investment Advisers (6 Feb 2026) para 1(iii)", date(2026, 2, 6)),
    # ── India: residency (row 7). Taken from the research report's reading of 31 Oct 2026 and the statute's text as
    # amended by the Finance Act 2020. The Income-tax Act 2025 (in force from 1 Apr 2026) carries the test under a
    # renumbered section that was not read, so each is checked by a qualified person before a client relies on it.
    LawFigure("in_res_days_year", "Resident in India: days in India in the year, on their own", 182, "days",
              "Income-tax Act 1961 s.6(1)(a), carried into the Income-tax Act 2025 (renumbered section not read)", date(2026, 10, 31)),
    LawFigure("in_res_days_short", "Resident in India: days in the year, with the earlier years below", 60, "days",
              "Income-tax Act 1961 s.6(1)(c), carried into the Income-tax Act 2025 (renumbered section not read)", date(2026, 10, 31)),
    LawFigure("in_res_days_prior_four", "Resident in India: days in the four years before the year, with the short test", 365, "days",
              "Income-tax Act 1961 s.6(1)(c), carried into the Income-tax Act 2025 (renumbered section not read)", date(2026, 10, 31)),
    LawFigure("in_res_days_citizen_away", "Indian citizen or person of Indian origin who is abroad: the short test's days become", 182, "days",
              "ASSUMED: Income-tax Act 1961 s.6(1) Explanation 1, as amended by the Finance Act 2020, as understood: it applies to a citizen who "
              "left India for employment, or a citizen or person of Indian origin who lives abroad and comes on a visit; the section was not "
              "re-read and counsel has not reviewed it", date(2026, 10, 31)),
    LawFigure("in_res_days_high_income", "Citizen or person of Indian origin visiting with high Indian income: the short test's days become", 120, "days",
              "ASSUMED: Income-tax Act 1961 s.6(1) Explanation 1(b) and s.6(6)(c), as amended by the Finance Act 2020, as understood: a visitor over "
              "the income line is resident from these days and, under 182, not ordinarily resident; the sections were not re-read and counsel has "
              "not reviewed them", date(2026, 10, 31)),
    LawFigure("in_res_income_line", "Indian income, other than foreign sources, above which the 120-day test and deemed residence apply", 1_500_000, "INR",
              "ASSUMED: Income-tax Act 1961 s.6(1) Explanation 1(b), s.6(1A) and s.6(6)(c) and (d), as amended by the Finance Act 2020, as "
              "understood; not re-read and not reviewed by counsel", date(2026, 10, 31)),
    LawFigure("in_res_rnor_days_prior_seven", "Not ordinarily resident: at most this many days in the seven years before the year", 729, "days",
              "Income-tax Act 1961 s.6(6)(b), carried into the Income-tax Act 2025 (renumbered section not read)", date(2026, 10, 31)),
    LawFigure("in_res_rnor_years_nonresident", "Not ordinarily resident: non-resident in at least this many of the ten years before the year", 9, "count",
              "Income-tax Act 1961 s.6(6)(a), carried into the Income-tax Act 2025 (renumbered section not read)", date(2026, 10, 31)),
    # FEMA and RBI conditions for a non-resident's accounts and holdings, laid out and not advised on.
    LawFigure("in_nro_repatriation", "NRO balances and sale proceeds an NRI may remit abroad, a financial year, after tax", 1_000_000, "USD",
              "RBI Master Direction on Remittance of Assets (FED Master Direction 14/2015-16) and FEMA (Non-debt Instruments) Rules 2019; "
              "not re-read in this build, counsel to confirm", date(2026, 10, 31)),
    LawFigure("in_pis_individual_limit", "Portfolio investment: one NRI's or OCI's share of an Indian company's paid-up equity, at most", 0.05, "rate",
              "FEMA (Non-debt Instruments) Rules 2019, Schedule III. The Budget proposal to move the limits to 10% and 24% "
              "is not law and is not encoded", date(2026, 10, 31)),
    LawFigure("in_pis_aggregate_limit", "Portfolio investment: all NRIs' and OCIs' share of an Indian company's paid-up equity, at most, "
              "unless the company resolves higher", 0.10, "rate",
              "FEMA (Non-debt Instruments) Rules 2019, Schedule III. The Budget proposal to move the limits to 10% and 24% "
              "is not law and is not encoded", date(2026, 10, 31)),
    LawFigure("in_majority_age", "Age at which a person is no longer a minor in India", 18, "count",
              "Indian Majority Act 1875 s.3", date(2026, 10, 31)),
    LawFigure("in_majority_age_court_guardian", "Age at which a minor for whom a court appointed a guardian stops being a minor", 21, "count",
              "Indian Majority Act 1875 s.3(1)", date(2026, 10, 31)),
    # ── India: Portfolio Managers (row 8). PM Master Circular (Jul 2025) and the SEBI (Portfolio Managers)
    # Regulations 2020 as read in the research report (section 2 and 3.4); the 2026 Regulations restate them from
    # 1 Apr 2027 and their implementing circulars had not issued on 31 Oct 2026.
    LawFigure("in_pms_min_ticket", "Portfolio Manager: the least a client invests at the start", 5_000_000, "INR",
              "SEBI (Portfolio Managers) Regulations 2020, as read in the research report; the clause was not re-read. "
              "An accredited investor's lower figure is not recorded here", date(2026, 10, 31)),
    LawFigure("in_pms_opex_cap", "Portfolio Manager: operating expenses excluding brokerage, a year, share of average daily AUM, at most", 0.005, "rate",
              "SEBI Master Circular for Portfolio Managers (Jul 2025)", date(2026, 10, 31)),
    LawFigure("in_pms_benchmarks", "Portfolio Manager: benchmarks a strategy may name, at most", 3, "count",
              "SEBI Master Circular for Portfolio Managers (Jul 2025): APMI's list of benchmarks", date(2026, 10, 31)),
    LawFigure("in_pms_exit_load_y1", "Portfolio Manager: exit load in the first year, at most", 0.03, "rate",
              "SEBI Master Circular for Portfolio Managers (Jul 2025)", date(2026, 10, 31)),
    LawFigure("in_pms_exit_load_y2", "Portfolio Manager: exit load in the second year, at most", 0.02, "rate",
              "SEBI Master Circular for Portfolio Managers (Jul 2025)", date(2026, 10, 31)),
    LawFigure("in_pms_exit_load_y3", "Portfolio Manager: exit load in the third year, at most", 0.01, "rate",
              "SEBI Master Circular for Portfolio Managers (Jul 2025)", date(2026, 10, 31)),
    # ── India: complaints (row 10). Read in the research report (section 3): every licence resolves a complaint within
    # 21 calendar days through the entity, then SCORES, then SMART ODR. The acknowledgement period was not found there,
    # so the figure below is assumed by this build and is the firm's own target, never presented as the circular's.
    LawFigure("in_complaint_resolve_days", "A complaint is resolved by the entity within, calendar days of receipt", 21, "days",
              "SEBI circular of Sep 2023 on redressal of investor grievances through SCORES and linking it to online dispute "
              "resolution, as read in the research report; the circular's clause was not re-read", date(2026, 10, 31)),
    LawFigure("in_complaint_ack_days", "ASSUMED: the firm acknowledges a complaint within, calendar days of receipt", 3, "days",
              "Assumed by this build: no acknowledgement period was found in the sources read. The firm's own policy "
              "(settings complaints.ack_days) replaces it", date(2026, 10, 31)),
    LawFigure("in_complaint_pending_months", "Annexure C counts a complaint as long pending after, months", 3, "count",
              "SEBI complaints data format (Annexure C) as read in the research report: pending over three months; the table "
              "was not re-read", date(2026, 10, 31)),
    # -- India: the tax engine (row 11) and KYC (row 12) --
    # Read from the research report's tax notes (Income-tax Act 2025 as Gazetted, overlaid with the Finance Bill and
    # Memorandum 2026). The enacted Finance Act 2026 and the Rules were not read, so each is an estimate for a qualified
    # person to check.
    LawFigure("in_cg_equity_st_rate", 'Short-term gain on listed equity, equity funds and REIT/InvIT units (STT paid), rate', 0.2, "rate",
              'Income-tax Act 2025 s.196 (1961 Act s.111A); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cg_equity_lt_rate", 'Long-term gain on listed equity, equity funds and REIT/InvIT units (STT paid), rate', 0.125, "rate",
              'Income-tax Act 2025 s.198 (1961 Act s.112A); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cg_equity_lt_exemption", 'Long-term gain on equity a taxpayer may take free of tax in a tax year', 125000, "INR",
              'Income-tax Act 2025 s.198 (1961 Act s.112A); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cg_other_lt_rate", 'Long-term gain on other assets, rate, no indexation (real estate: see the cost inflation index)', 0.125, "rate",
              'Income-tax Act 2025 s.197 (1961 Act s.112); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cg_equity_months", 'Listed securities and equity-fund units: held longer than this many months to be long-term', 12, "count",
              'Income-tax Act 2025 s.2(101); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cg_other_months", 'Unlisted shares, foreign shares, physical gold, land and buildings: held longer than this many months to be long-term', 24, "count",
              "Income-tax Act 2025 s.2(101); the unlisted share, foreign share and gold rows are the research report's inference from the general rule, not a named provision; counsel to confirm", date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cess", 'Health and education cess, on tax plus surcharge', 0.04, "rate",
              'Finance Bill 2026 Memorandum; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_surcharge_special_cap", 'Surcharge cap on tax on dividends and gains at special rates (ss.196-198); slab-taxed gains are not capped', 0.15, "rate",
              'Finance Bill 2026 Memorandum; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_loss_carry_years", 'Capital losses carry forward this many tax years, only if the return was filed by its due date', 8, "count",
              'Income-tax Act 2025 ss.108-111 and 121 (return under s.263(1)); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cii_2026_27", 'Cost Inflation Index, FY 2026-27', 384, "count",
              'CBDT Notification 85/2026, read through Taxscan and StudyCafe (secondary); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_cii_2025_26", 'Cost Inflation Index, FY 2025-26', 376, "count",
              'CBDT notification, read through StudyCafe (secondary); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31)),
    LawFigure("in_new_regime_bands", 'Income tax slabs, new regime (the default)',
              ((400_000, 0.0), (800_000, 0.05), (1_200_000, 0.10), (1_600_000, 0.15), (2_000_000, 0.20), (2_400_000, 0.25), (None, 0.30)),
              "bands", 'Income-tax Act 2025 s.202; Finance Bill 2026 Memorandum; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_bands", 'Income tax slabs, old regime, under 60',
              ((250_000, 0.0), (500_000, 0.05), (1_000_000, 0.20), (None, 0.30)),
              "bands", 'Income-tax Act 2025 Part I-B Paragraph A; Finance Bill 2026 Memorandum; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_exemption_senior", "Old regime: basic exemption for a resident aged 60 to 79", 300000, "INR",
              "Finance Bill 2026 Memorandum and HSBC tax reckoner, read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read", date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_exemption_super_senior", "Old regime: basic exemption for a resident aged 80 or over", 500000, "INR",
              "Finance Bill 2026 Memorandum and HSBC tax reckoner, read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read", date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_new_regime_std_deduction", 'Standard deduction from salary, new regime', 75000, "INR",
              'HSBC tax reckoner FY 2026-27, read in the research notes (secondary); research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_std_deduction", 'Standard deduction from salary, old regime (assumed)', 50000, "INR",
              "Assumed from the 1961 Act's figure; the research notes do not state it; counsel to confirm", date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_new_regime_rebate", 'Rebate under s.156, new regime, resident individual, up to this much tax', 60000, "INR",
              'Income-tax Act 2025 s.156; marginal relief above the line is not modelled; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_new_regime_rebate_line", 'Rebate under s.156, new regime: total income up to', 1200000, "INR",
              'Income-tax Act 2025 s.156; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_rebate", 'Rebate under s.156, old regime, resident individual, up to this much tax', 12500, "INR",
              'Income-tax Act 2025 s.156; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_old_regime_rebate_line", 'Rebate under s.156, old regime: total income up to', 500000, "INR",
              'Income-tax Act 2025 s.156; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_surcharge_new", 'Surcharge on income tax by total income, new regime',
              ((5_000_000, 0.0), (10_000_000, 0.10), (20_000_000, 0.15), (None, 0.25)),
              "bands", 'Finance Bill 2026 Memorandum; the Act also tests income without dividends and special-rate gains, which is not modelled; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_surcharge_old", 'Surcharge on income tax by total income, old regime',
              ((5_000_000, 0.0), (10_000_000, 0.10), (20_000_000, 0.15), (50_000_000, 0.25), (None, 0.37)),
              "bands", 'Finance Bill 2026 Memorandum; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_advance_tax_15_jun", "Advance tax: share of the year's tax due by 15 June", 0.15, "rate",
              'Income-tax Act 2025, deferment interest table, as read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_advance_tax_15_sep", "Advance tax: share of the year's tax due by 15 September", 0.45, "rate",
              'Income-tax Act 2025, deferment interest table, as read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_advance_tax_15_dec", "Advance tax: share of the year's tax due by 15 December", 0.75, "rate",
              'Income-tax Act 2025, deferment interest table, as read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_advance_tax_15_mar", "Advance tax: share of the year's tax due by 15 March", 1.0, "rate",
              'Income-tax Act 2025, deferment interest table, as read in the research notes; research report section 3.2 and its tax notes; the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read', date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_advance_tax_floor", "Advance tax is asked only where the year's tax is at least this much (assumed from the 1961 Act s.208)", 10000, "INR",
              "Assumed from the 1961 Act s.208; the 2025 Act's section was not read; counsel to confirm", date(2026, 10, 31), applies_from=date(2026, 4, 1)),
    LawFigure("in_kyc_bo_company", 'Beneficial owner of a company or partnership: more than this share', 0.1, "rate",
              "SEBI Master Circular on AML/CFT (6 Jun 2024), read through Taxguru's reproduction (research report section 3.3)", date(2026, 10, 31)),
    LawFigure("in_kyc_bo_unincorporated", 'Beneficial owner of an unincorporated body or association: more than this share', 0.15, "rate",
              "SEBI Master Circular on AML/CFT (6 Jun 2024), read through Taxguru's reproduction (research report section 3.3)", date(2026, 10, 31)),
    LawFigure("in_kyc_bo_trust", 'Beneficial owner of a trust: a beneficiary with at least this interest, and always the author, trustee and protector', 0.1, "rate",
              "SEBI Master Circular on AML/CFT (6 Jun 2024), read through Taxguru's reproduction (research report section 3.3)", date(2026, 10, 31)),
    LawFigure("in_kyc_crs_company", 'CRS controlling person of a company: more than this share (a different test from beneficial ownership)', 0.25, "rate",
              'Income-tax Rules 2026 r.238-240 and the CBDT guidance note of 24 Jul 2026, read through a reproduction (research report section 3.3)', date(2026, 10, 31)),
    LawFigure("in_kyc_address_proof_months", 'A deemed address proof (a utility bill) is accepted if no older than this many months; an updated official document follows within 3 months', 2, "count",
              'SEBI Master Circular on KYC (12 Oct 2023) para 17', date(2026, 10, 31)),
    LawFigure("in_kyc_aadhaar_xml_days", 'An Aadhaar XML or Secure QR code used for KYC must be no older than this many days', 3, "days",
              'SEBI Master Circular on KYC (12 Oct 2023) para 41, as read in the research report section 3.3', date(2026, 10, 31)),
    # ── India: family and estate (row 13). Read in the research report (3.7) and the statutes as generally known; none of
    # these clauses was re-read for this build, and each is for counsel before a client relies on it (see counsel_review.py).
    LawFigure("in_family_of_client", "SEBI's family of client: the individual, the spouse, dependent children and dependent parents "
              "(an adult child who is not dependent is a family of their own)", None, "rule",
              "SEBI (Investment Advisers) Regulations 2013 and the IA Master Circular (6 Feb 2026) para 1(iii), as read in the research "
              "report; the definition's clause was not re-read, counsel to confirm", date(2026, 10, 31)),
    LawFigure("in_nominee_cap_demat_mf", "Demat account or mutual fund folio: nominees, at most", 3, "count",
              "SEBI circular of 29 May 2026 on nomination, from 1 Sep 2026, as reproduced; SEBI's own PDF was not opened and one "
              "source says 4, the consultation figure", date(2026, 10, 31), applies_from=date(2026, 9, 1)),
    LawFigure("in_nominee_cap_bank", "Bank account: nominees, at most", 4, "count",
              "Banking Laws (Amendment) Act 2025, provisions on nomination notified from 1 Nov 2025, as reported; not re-read",
              date(2026, 10, 31)),
    LawFigure("in_epf_nominee_family_only", "EPF: a nominee is a member of the member's family (until a family is recorded, any person "
              "may be nominated, and a nomination to a non-member is invalid)", None, "rule",
              "EPF Scheme 1952 para 61 (the research report cites para 44 of the Scheme as reproduced), not re-read", date(2026, 10, 31)),
    LawFigure("in_nomination_opt_out", "A new single-holder demat account or folio needs a nomination or a signed opt-out declaration",
              None, "rule", "SEBI nomination circulars as read in the research report (3.7); the clause was not re-read", date(2026, 10, 31)),
    LawFigure("in_hindu_succession_class_i", "Hindu male intestate: Class I heirs take together, equally (son, daughter, widow, mother, "
              "and the children of a predeceased son or daughter taking their parent's share), then Class II, agnates and cognates", None, "rule",
              "Hindu Succession Act 1956 s.8 and the Schedule, as amended by the 2005 Act (daughters are coparceners by birth: "
              "Vineeta Sharma v Rakesh Sharma, 2020); not re-read", date(2026, 10, 31)),
    LawFigure("in_hindu_succession_female", "Hindu female intestate: sons, daughters and husband first; then the husband's heirs; then her "
              "parents; property from her parents goes back to her father's heirs and from her husband to his, if she leaves no child", None, "rule",
              "Hindu Succession Act 1956 s.15 and s.16; not re-read", date(2026, 10, 31)),
    LawFigure("in_isa_succession", "Christian and other intestate estates: the widow takes one third and the lineal descendants two thirds; "
              "with no descendant the widow takes half and the kindred half. Parsis follow ss.51 to 56 of the same Act", None, "rule",
              "Indian Succession Act 1925 ss.33 and 51-56; not re-read. Muslim succession is under personal law and is not laid out here",
              date(2026, 10, 31)),
    LawFigure("in_probate_not_compulsory", "Probate of a will is no longer compulsory in Mumbai, Chennai and Kolkata", None, "rule",
              "Indian Succession Act 1925 s.213 omitted, December 2025, as reported by several law firms; the Gazette text was not opened",
              date(2026, 10, 31), applies_from=date(2025, 12, 1)),
    # ── India: deadlines (row 14). The dates the Income-tax Act 1961 fixes, which the Income-tax Act 2025 (in force from 1 Apr 2026)
    # carries under renumbered sections that were not read.
    LawFigure("in_advance_tax_dates", "Advance tax instalments: 15 Jun (15%), 15 Sep (45%), 15 Dec (75%), 15 Mar (100%) of the year's "
              "tax; due where the tax after TDS is Rs 10,000 or more", None, "rule",
              "Income-tax Act 1961 ss.207, 208, 211, carried into the Income-tax Act 2025 (renumbered sections not read)", date(2026, 10, 31)),
    LawFigure("in_itr_due_date", "Income-tax return of an individual or HUF not needing an audit: 31 July after the year; 31 October with an audit", None, "rule",
              "Income-tax Act 1961 s.139(1), carried into the Income-tax Act 2025 (renumbered section not read); the due date is "
              "often extended by notification, so the day in force is checked", date(2026, 10, 31)),
    LawFigure("in_dpdp_duties_start", "ASSUMED: the Digital Personal Data Protection Rules' main duties start (sources say 13 or 14 May 2027)", None, "date",
              "DPDP Act 2023 and Rules 2025 as read in the research report; the Gazette was not fetched, so the day is assumed",
              date(2026, 10, 31), applies_from=date(2027, 5, 13)),
    LawFigure("in_pm_regs_2026_start", "SEBI (Portfolio Managers) Regulations 2026 apply (the 2020 Regulations until 31 Mar 2027)", None, "date",
              "SEBI (Portfolio Managers) Regulations 2026 as read in the research report; implementing circulars had not issued on 31 Oct 2026",
              date(2026, 10, 31), applies_from=date(2027, 4, 1)),
    LawFigure("in_ad_code_start", "SEBI Common Advertisement Code operative (Intermediaries Regs 16G-16P)", None, "date",
              "SEBI (Intermediaries) Regulations, as read in the research report", date(2026, 10, 31), applies_from=date(2027, 4, 8)),
    # ── India: the L100 India edition (Nov 2026), B-290 to B-292. Each is as that document reads it and none was re-read against the
    # circular or the Regulations: it says to verify every threshold before client use, so each starts "not reviewed" in the counsel tracker.
    LawFigure("in_sif_min_ticket", "Specialised Investment Fund: the least an investor puts in, across all of one manager's SIF strategies, per PAN", 1_000_000, "INR",
              "SEBI circular of 27 Feb 2025 on Specialised Investment Funds (live from 1 Apr 2025), as read in the L100 India edition (Nov 2026) "
              "section 2.2; the circular was not re-read. An accredited investor is exempt from it", date(2026, 11, 25)),
    LawFigure("in_aif_min_ticket", "Alternative Investment Fund (Category I, II or III): the least an investor commits", 10_000_000, "INR",
              "SEBI (Alternative Investment Funds) Regulations 2012, as read in the L100 India edition (Nov 2026) section 2.2; "
              "the Regulation was not re-read", date(2026, 11, 25)),
    LawFigure("in_aif_min_ticket_employee", "Alternative Investment Fund: the least a manager's own employee or director commits", 2_500_000, "INR",
              "SEBI (Alternative Investment Funds) Regulations 2012, as read in the product-gate backlog row B-290; neither the L100 "
              "edition nor the Regulation was re-read for this figure, so counsel confirms it first", date(2026, 11, 25)),
    LawFigure("in_accredited_criteria", "Accredited investor, an individual: annual income Rs 2 crore, or net worth Rs 7.5 crore with at least half "
              "in financial assets, or a combination of the two", None, "rule",
              "SEBI (Alternative Investment Funds) Regulations 2012 accredited-investor framework, as read in the L100 India edition (Nov 2026) "
              "section 2.2, which says to confirm the exact criteria before using them with a client. Never applied to a client's wealth here: "
              "a certificate is recorded from its document", date(2026, 11, 25)),
    LawFigure("in_ia_advance_fee_months", "Investment Adviser: fees may be collected in advance for at most, months", 12, "count",
              "SEBI Master Circular for Investment Advisers (6 Feb 2026) as amended in April 2025 (earlier, two quarters), as read in the L100 "
              "India edition (Nov 2026) section 4.2; the circular's clause was not re-read", date(2026, 11, 25)),
    LawFigure("in_ia_second_opinion_cap", "Investment Adviser: an AUA-based fee for a second opinion on assets held under a distribution "
              "arrangement, at most, share of those assets a year", 0.025, "rate",
              "SEBI circular of October 2025 on second-opinion fees, as read in the L100 India edition (Nov 2026) section 4.2; the circular "
              "was not re-read", date(2026, 11, 25), applies_from=date(2025, 10, 1)),
    LawFigure("in_ia_deposit_liquid_funds", "Investment Adviser: the refundable deposit with IAASB may be held in liquid or overnight mutual funds", None, "rule",
              "SEBI circular of August 2025 on the Investment Adviser deposit, as read in the L100 India edition (Nov 2026) section 4.2; the "
              "circular was not re-read", date(2026, 11, 25), applies_from=date(2025, 8, 1)),
    # ── India: advice under the IA operating model (B-399). Neither the Regulations nor a circular was re-read.
    LawFigure("in_ia_record_retention_years", "Investment Adviser: records of advice are preserved for at least, years", 5, "years",
              "ASSUMED: SEBI (Investment Advisers) Regulations 2013, regulation 19, as understood (a minimum of five years); the L200-2 India edition "
              "(Nov 2026) section 9 names record retention and gives no period; the Regulations were not re-read", date(2026, 11, 27)),
    LawFigure("in_ia_individual_client_limit", "An individual Investment Adviser moves to non-individual registration above this many clients", 300, "count",
              "ASSUMED: SEBI (Investment Advisers) (Second Amendment) Regulations 2024 and the guidelines of January 2025, as understood (300 clients, "
              "or Rs 3 crore of fees in a year, which is not counted here); the L200-2 India edition (section 9) says to verify the current thresholds; "
              "not re-read", date(2026, 11, 27)),
    # ── India: the L100 batch A items (B-293, B-295, B-296, B-299). The L100 edition states the LRS limit and says to verify the tax
    # collected at source, so those are ASSUMED here from the Finance Act 2025 as understood and stand "not reviewed" in the counsel
    # tracker until compliance records counsel's reading. None was re-read against its source on 25 Nov 2026.
    LawFigure("in_lrs_limit_usd", "Liberalised Remittance Scheme: the most a resident individual may remit abroad in a financial year", 250_000, "USD",
              "RBI Master Direction on the Liberalised Remittance Scheme under FEMA, as read in the L100 India edition (Nov 2026) section 7 (international "
              "investing); the direction was not re-read", date(2026, 11, 25)),
    LawFigure("in_lrs_tcs_threshold", "Tax collected at source on a remittance under the Scheme: it starts above this much remitted by a person in a financial year", 1_000_000, "INR",
              "ASSUMED: the Income-tax Act's tax-collected-at-source provision for remittances (1961 Act s.206C(1G)) as amended by the Finance Act 2025, "
              "which raised it to Rs 10 lakh; the L100 India edition (section 7) says the rules were revised in recent Budgets and to verify current rates; "
              "the February 2026 Budget and the Income-tax Act 2025's renumbered section were not read", date(2026, 11, 25)),
    LawFigure("in_lrs_tcs_rate_other", "Tax collected at source on a remittance under the Scheme above the threshold, for investment, gifts and other purposes", 0.20, "rate",
              "ASSUMED: as for in_lrs_tcs_threshold (Finance Act 2025 as understood; the L100 edition says to verify current rates); the Budget of "
              "February 2026 was not read", date(2026, 11, 25)),
    LawFigure("in_lrs_tcs_rate_edu_medical", "Tax collected at source above the threshold, for education and medical treatment", 0.05, "rate",
              "ASSUMED: as for in_lrs_tcs_threshold; shown for a remittance's own purpose, never applied to an order; the February 2026 Budget was not read",
              date(2026, 11, 25)),
    LawFigure("in_lrs_tcs_rate_edu_loan", "Tax collected at source above the threshold, for education financed by a loan from a specified institution", 0.005, "rate",
              "ASSUMED: as for in_lrs_tcs_threshold; shown for a remittance's own purpose, never applied to an order; the February 2026 Budget was not read",
              date(2026, 11, 25)),
    LawFigure("in_upi_autopay_limit_mf", "A UPI Autopay mandate for mutual-fund subscriptions: the most a single debit may be without a further authentication", 100_000, "INR",
              "ASSUMED: RBI's e-mandate framework as raised in December 2023 for mutual funds, insurance premium and credit-card payments, as understood; "
              "not read in the L100 India edition and not re-read; a bank's own limit may be lower", date(2026, 11, 25)),
    LawFigure("in_third_party_minor_limit", "A parent or relative paying for a minor's mutual-fund investment from their own bank: the most one purchase or instalment may be", 50_000, "INR",
              "ASSUMED: AMFI best-practice circular 135/BP/16/10-11 (2010) on third-party payments, as understood; not re-read, and later SEBI circulars on "
              "a minor's folio may treat a parent or guardian differently; the L200-1 India edition section 4.2 says 'within limits' and states no figure",
              date(2026, 11, 27)),
    LawFigure("in_ppf_rule", "Public Provident Fund: 15-year term, up to Rs 1.5 lakh a year, rate set by the Government each quarter; interest and maturity are tax-free", None, "rule",
              "Public Provident Fund Scheme 2019 and the Income-tax Act's exempt-exempt-exempt treatment, as understood; L100 India edition section 7 names the scheme and "
              "says its rates are administratively set; neither was re-read", date(2026, 11, 25)),
    LawFigure("in_ssy_rule", "Sukanya Samriddhi Account: a girl child under 10, up to Rs 1.5 lakh a year for 15 years, matures 21 years after opening; interest tax-free", None, "rule",
              "Sukanya Samriddhi Account Rules 2016, as understood; L100 India edition section 7 names the scheme; not re-read", date(2026, 11, 25)),
    LawFigure("in_scss_rule", "Senior Citizens' Savings Scheme: age 60 or over, up to Rs 30 lakh, five-year term; interest is taxable", None, "rule",
              "Senior Citizens' Savings Scheme Rules 2019 as amended in 2023, as understood; L100 India edition section 7 names the scheme; not re-read", date(2026, 11, 25)),
    LawFigure("in_nsc_rule", "National Savings Certificate: five-year term; interest is taxable and treated as reinvested each year", None, "rule",
              "National Savings Certificates (VIII Issue) Rules 2019, as understood; L100 India edition section 7 names the scheme; not re-read", date(2026, 11, 25)),
    LawFigure("in_nps_rule", "National Pension System: locked in to age 60; at exit 60% may be taken as a lump sum, tax-free, and 40% must buy an annuity", None, "rule",
              "PFRDA regulations as stated in the L100 India edition (Nov 2026) section 7; the regulations and the tax treatment of the lump sum were not re-read",
              date(2026, 11, 25)),
    LawFigure("in_epf_rule", "Employees' Provident Fund: interest is tax-free on yearly employee contributions up to Rs 2.5 lakh (Rs 5 lakh with no employer contribution); "
              "interest on the part above is taxable", None, "rule",
              "ASSUMED: the Income-tax Act's treatment of interest on provident fund contributions as amended in 2021, as understood; L100 India edition section 7 names the "
              "fund as the payroll default beside NPS; not re-read", date(2026, 11, 25)),
    LawFigure("in_gsec_rule", "RBI Retail Direct: an individual buys government securities directly, in lots of Rs 10,000 and multiples; interest is taxable at slab", None, "rule",
              "RBI Retail Direct scheme (2021) as understood and as named in the L100 India edition section 7; the lot size and the tax were not re-read", date(2026, 11, 25)),
    LawFigure("in_mld_rule", "Market-linked debentures: a gain is short-term whatever the holding period, taxed at slab", None, "rule",
              "Income-tax Act's deemed short-term treatment of market-linked debentures from April 2023, as stated in the L100 India edition (Nov 2026) section 7; "
              "tax_india.NEVER_LONG already holds the class; not re-read", date(2026, 11, 25)),
    LawFigure("in_reit_invit_rule", "REIT and InvIT units: a gain on listed units is taxed as listed equity (STT paid); the distributions are taxed by what they are made of", None, "rule",
              "Income-tax Act 2025 as read for in_cg_equity_lt_rate for the units' gain; the character of a distribution (interest, rent, dividend, return of capital) "
              "was not read and is not modelled", date(2026, 11, 25)),
    LawFigure("in_sgb_rule", "Sovereign Gold Bonds: an eight-year term with exit from the fifth year, 2.5% interest taxable; a gain on redemption at maturity is exempt for an individual; "
              "new issuance has been discontinued", None, "rule",
              "ASSUMED: the Sovereign Gold Bond Scheme and the Income-tax Act's exemption on redemption, as understood; the L100 India edition (section 7) says fresh issuance "
              "has been discontinued and to verify current status; neither was re-read", date(2026, 11, 25)),
    LawFigure("in_ulip_lock_in_years", "A unit-linked insurance policy is locked in for this many years", 5, "count",
              "IRDAI regulations on linked insurance products, as understood and as named in the L100 India edition section 7 (insurance products); not re-read", date(2026, 11, 25)),
    LawFigure("in_ulip_fmc_cap", "A unit-linked insurance policy: the fund management charge is at most this share of the fund a year", 0.0135, "rate",
              "ASSUMED: IRDAI's cap on the fund management charge, as understood; the L100 India edition names ULIP economics as a deeper topic and was not the source; not re-read",
              date(2026, 11, 25)),
    LawFigure("in_pledge_rule", "A loan against securities: the lender marks a lien on the units at the depository or the registrar, and a unit under lien cannot be sold or transferred", None, "rule",
              "Depositories Act and the depositories' bye-laws on pledge, and the L100 India edition (Nov 2026) section 7 on loans against securities; not re-read", date(2026, 11, 25)),
    LawFigure("in_dividend_tds_threshold", "Tax deducted at source on dividends: a company deducts only when what it pays one shareholder in a year goes above this", 10_000, "INR",
              "ASSUMED: the Income-tax Act's dividend provision (1961 Act s.194) as raised by the Finance Act 2025; the L100 India edition (Nov 2026) section 8.1 says only "
              "'a small threshold'; not re-read", date(2026, 11, 25)),
    LawFigure("in_dividend_tds_rate", "Tax deducted at source on a dividend paid to a resident, rate", 0.10, "rate",
              "ASSUMED: the Income-tax Act's dividend provision (1961 Act s.194) as understood; the L100 India edition section 8.1 says dividends are taxed at slab with TDS above "
              "a small threshold; not re-read", date(2026, 11, 25)),
    LawFigure("in_clubbing_minor_exemption", "Income of a minor child clubbed with a parent: an exemption of this much for each child", 1_500, "INR",
              "Income-tax Act 1961 s.10(32), as understood; the Income-tax Act 2025's renumbered section was not read", date(2026, 11, 25)),
    LawFigure("in_clubbing_rule", "Income from an asset a person gave a spouse, or a minor child, without adequate consideration is added to the giver's income "
              "(a minor child's, to the parent whose income is higher)", None, "rule",
              "Income-tax Act 1961 s.64(1)(iv) and s.64(1A), as understood; the L100 India edition (section 8.3) names the gift exemption and not this rule; the Income-tax "
              "Act 2025's renumbered sections were not read", date(2026, 11, 25)),
    LawFigure("in_gift_relative_exempt", "A gift from a specified relative is not income in the receiver's hands; the relatives are a spouse, a brother or sister of the receiver "
              "or of the receiver's spouse, a brother or sister of either parent, and a lineal ascendant or descendant of the receiver or of the receiver's spouse, and their spouses", None, "rule",
              "Income-tax Act 1961 s.56(2)(x) and its definition of relative, as understood; the L100 India edition (Nov 2026) section 8.3 says gifts between specified relatives "
              "are exempt; the Income-tax Act 2025's renumbered sections were not read", date(2026, 11, 25)),
    LawFigure("in_gift_other_threshold", "A gift from anyone else is income in the receiver's hands only when what they received in the year goes above this", 50_000, "INR",
              "Income-tax Act 1961 s.56(2)(x), as understood; the Income-tax Act 2025's renumbered section was not read", date(2026, 11, 25)),
    # ── India: the L100 batch C items (B-294, B-297, B-298). The L100 edition says cut-off time decides which day's NAV an order gets and that
    # for a large amount it depends on when the funds are realised (section 6.1), but gives no time and no amount, so every figure here is
    # ASSUMED from SEBI's mutual fund cut-off circulars as understood, and says verify against the circular before relying on it. None was re-read.
    LawFigure("in_mf_cutoff_standard", "Mutual fund purchase or redemption (not a liquid or overnight scheme): an order taken before this time of day (minutes after midnight, firm time; 900 is 3:00 pm) gets that day's NAV, one after it the next business day's", 900, "minutes",
              "ASSUMED: SEBI's mutual fund cut-off circulars (the 3:00 pm cut-off for equity, hybrid and debt schemes) as understood; the L100 India edition (Nov 2026) "
              "section 6.1 says the cut-off decides the day's NAV and gives no time; verify against the circular, which was not re-read", date(2026, 11, 25)),
    LawFigure("in_mf_cutoff_liquid_purchase", "A liquid or overnight scheme's purchase: the cut-off is this time of day (minutes after midnight; 810 is 1:30 pm), and the funds must be available by it for that day's NAV", 810, "minutes",
              "ASSUMED: SEBI's cut-off circulars for liquid and overnight schemes as understood; the L100 India edition says nothing of the time; verify against the circular, "
              "which was not re-read", date(2026, 11, 25)),
    LawFigure("in_mf_cutoff_liquid_redemption", "A liquid or overnight scheme's redemption: the cut-off is this time of day (minutes after midnight; 900 is 3:00 pm)", 900, "minutes",
              "ASSUMED: SEBI's cut-off circulars for liquid and overnight schemes as understood; verify against the circular, which was not re-read", date(2026, 11, 25)),
    LawFigure("in_mf_large_amount", "A purchase at or above this amount gets the NAV of the day the funds are realised, if later than the cut-off's day", 200_000, "INR",
              "ASSUMED: the line SEBI drew for the realisation rule before it applied the rule to every purchase except liquid and overnight (February 2020 circular, "
              "effective 2021), as understood; the L100 India edition (section 6.1) says for large amounts the NAV depends on when funds are realised and gives no figure; "
              "verify against the circular, which was not re-read. Here the realisation rule is applied to a purchase at or above it, and a purchase below it is read on its cut-off alone",
              date(2026, 11, 25)),
    LawFigure("in_ia_group_commission_rule", "An Investment Adviser, and at group level its family of entities, may not earn distribution commission from a client it advises", None, "rule",
              "SEBI (Investment Advisers) Regulations 2013 as amended in 2020 (segregation of advisory and distribution) and the IA Master Circular, as read in the L100 India edition "
              "(Nov 2026) sections 3.1 and 13; the regulations were not re-read, and who counts as the family of entities is for counsel", date(2026, 11, 25)),
    LawFigure("in_nism_validity_years", "A NISM certificate is valid for this many years from its issue, and is renewed by a fresh certificate", 3, "years",
              "NISM's certification norms for investment advisers (X-A, X-B) and distributors (V-A) as understood; the L100 India edition (Nov 2026) sections 4.2 and 9.3 name the "
              "certificates and no validity. Not used to work an expiry: the certificate's own expiry is what a person records. Verify against NISM", date(2026, 11, 25)),
    LawFigure("in_ad_preapproval_rule", "A firm's marketing is approved by its own compliance before it is used, and what is used is what was approved", None, "rule",
              "SEBI Common Advertisement Code (Intermediaries Regs 16G-16P) and the IA Master Circular, as read in the L100 India edition (Nov 2026) section 13 "
              "('advertisement pre-approval under the 2023 advertisement code'); the Code is operative on 8 April 2027 and was not re-read", date(2026, 11, 25)),
    # ── India: the PMLA programme (B-390). From the L200-1 India edition section 5.3; the Act and the Rules were not read, so every row is
    # ASSUMED and stands "not reviewed" in the counsel tracker.
    LawFigure("in_pml_str_working_days", "A suspicious transaction report is filed with FIU-IND within this many working days of concluding suspicion", 7, "count",
              "ASSUMED: the L200-1 India edition (Nov 2026) section 5.3, on the PML (Maintenance of Records) Rules; the Rules were not re-read. Working "
              "days are counted as weekdays, holidays not modelled", date(2026, 11, 27)),
    LawFigure("in_pml_cash_threshold", "Cash transactions above this amount are reported to FIU-IND; monitoring reads deposits against the same line", 1_000_000, "INR",
              "ASSUMED: PML (Maintenance of Records) Rules 2005, the cash transaction line of Rs 10 lakh as understood; the L200-1 India edition section 5.3 "
              "states no amount; the Rules were not re-read, and how connected transactions add up is not modelled", date(2026, 11, 27)),
    LawFigure("in_pml_ctr_due_day", "The monthly reports to FIU-IND are due by this day of the following month", 15, "count",
              "ASSUMED: the L200-1 India edition (Nov 2026) section 5.3 says cash transaction and other prescribed reports run on monthly cycles and gives no "
              "day; the 15th is as understood from the PML (Maintenance of Records) Rules, which were not re-read", date(2026, 11, 27)),
    # ── India: the consolidation workbench (B-389). From the L200-1 India edition section 6.2; no circular, Act or bye-law was re-read, so every row
    # is ASSUMED and stands "not reviewed" in the counsel tracker.
    LawFigure("in_code_change_rule", "A distributor or broker code change re-maps who services a folio at the registrar: no unit moves, nothing is sold and no tax event arises", None, "rule",
              "ASSUMED: the L200-1 India edition (Nov 2026) section 6.2; the AMFI and registrar procedures, with their consent and cooling rules, were not re-read and are not encoded",
              date(2026, 11, 27)),
    LawFigure("in_same_owner_transfer_rule", "An off-market transfer between one person's own demat accounts is not a sale: the cost and the holding period carry; a gift is a transfer with its own tax treatment",
              None, "rule", "ASSUMED: the L200-1 India edition (Nov 2026) section 6.2; the Income-tax Act's provision on what is a transfer was not re-read", date(2026, 11, 27)),
    # ── India: the retirement stack inside the projection (B-395). From the L200-2 India edition section 4; no regulation or Act was
    # re-read, so every row is ASSUMED and stands "not reviewed" in the counsel tracker.
    LawFigure("in_nps_exit_age", "NPS Tier I: the age at which the account is taken to exit", 60, "years",
              "ASSUMED: PFRDA exit regulations as stated in the L200-2 India edition (Nov 2026) section 4 (locked to 60); the regulations were not re-read, "
              "and a premature or deferred exit has its own rules, not encoded", date(2026, 11, 27)),
    LawFigure("in_nps_lump_share", "NPS Tier I at exit: the share of the corpus taken as a lump sum", 0.60, "rate",
              "ASSUMED: PFRDA exit regulations as stated in the L200-2 India edition (Nov 2026) section 4 (60% withdraws); not re-read. A later PFRDA "
              "amendment is believed to let some subscribers take more and buy a smaller annuity: it was not read and is not encoded, so verify before "
              "client use", date(2026, 11, 27)),
    LawFigure("in_nps_annuity_share", "NPS Tier I at exit: the share of the corpus that buys an annuity", 0.40, "rate",
              "ASSUMED: PFRDA exit regulations as stated in the L200-2 India edition (Nov 2026) section 4 (40% must buy an annuity); not re-read. The "
              "smaller shares for a small corpus or under a later amendment were not read and are not encoded, so verify before client use",
              date(2026, 11, 27)),
    LawFigure("in_nps_lump_tax_rule", "NPS at exit: the lump sum of up to 60% of the corpus is exempt from income tax; what is taken above that is not "
              "worked out here", None, "rule",
              "ASSUMED: Income-tax Act 1961 s.10(12A) as understood, and the L200-2 India edition (Nov 2026) section 4 ('60 percent withdraws tax-free'); "
              "the Income-tax Act 2025's renumbered provision was not read", date(2026, 11, 27)),
    LawFigure("in_nps_annuity_tax_rule", "NPS annuity: the amount that buys it is not taxed at exit; the annuity paid is income taxed at slab in the year "
              "it is received", None, "rule",
              "ASSUMED: Income-tax Act 1961 s.80CCD(3) and (5) as understood, and the L200-2 India edition (Nov 2026) section 4 (annuity income taxable at "
              "slab); the Income-tax Act 2025's renumbered provisions were not read", date(2026, 11, 27)),
    LawFigure("in_ppf_annual_cap", "Public Provident Fund: the most one person deposits in a financial year", 150_000, "INR",
              "ASSUMED: Public Provident Fund Scheme 2019 as stated in the L200-2 India edition (Nov 2026) section 4; the Scheme was not re-read",
              date(2026, 11, 27)),
    LawFigure("in_ups_rule", "Unified Pension Scheme: an assured-pension option beside NPS for central government staff, recorded here as a "
              "membership; its pension is not worked out", None, "rule",
              "ASSUMED: the L200-2 India edition (Nov 2026) section 4.2, which says to verify current terms; the notification was not read",
              date(2026, 11, 27)),
    LawFigure("in_stamp_duty_securities_transfer", "Stamp duty on a transfer of securities other than debentures on a delivery basis, share of the consideration", 0.00015, "rate",
              "ASSUMED: Indian Stamp Act 1899, Schedule I, as amended with effect from 1 July 2020, as understood; the L200-1 India edition (section 6.2) says duty "
              "'where applicable' and gives no rate; the Act was not re-read", date(2026, 11, 27), applies_from=date(2020, 7, 1)),
    LawFigure("in_stamp_duty_no_consideration_rule", "A transfer of securities that states no consideration (between one person's own accounts, or a gift) carries no stamp duty", None, "rule",
              "ASSUMED: Indian Stamp Act 1899 as amended from 1 July 2020, which charges duty on consideration, as understood; the Act and the depositories' notices were not re-read",
              date(2026, 11, 27)),
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
    if figure.unit in ("bands", "tiers"):
        raise ValueError(f"{key} is a table of {figure.unit}: read it with {figure.unit}()")
    return figure.value


def bands(key: str, today: date | None = None) -> list[tuple[float, float]]:
    """A table of (upper bound, rate), lowest band first; the top band's bound is infinity."""
    figure = get(key, today)
    if figure.unit != "bands":
        raise ValueError(f"{key} is not a table of bands")
    return [(float("inf") if bound is None else float(bound), rate) for bound, rate in figure.value]


def tiers(key: str, today: date | None = None) -> list[tuple[float, ...]]:
    """A table of tiers, lowest first: (upper bound, then the amounts the tier adds); the top tier's bound is infinity."""
    figure = get(key, today)
    if figure.unit != "tiers":
        raise ValueError(f"{key} is not a table of tiers")
    return [(float("inf") if row[0] is None else float(row[0]), *row[1:]) for row in figure.value]


def cited(keys: list[str] | tuple[str, ...], today: date) -> list[dict]:
    """The figures an answer used, each with its source, as-of date and whether it is in force on `today`."""
    return [get(k, today).view(today) for k in keys]
