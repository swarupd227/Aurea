"""Required minimum distributions, by the rule (B-170, L200-2 §5.3).

One place for what the law fixes, so the account card, the cash page and the tax view give one answer:

- The divisor is the Uniform Lifetime Table for the age the owner reaches in the distribution year, every age to 120
  and over. It used to stop at 90, so at 95 the required amount read about 27% low.
- The start age follows the birth year under SECURE 2.0: 73 for those born 1951 to 1959, 75 from 1960. It used to be
  73 for everyone.
- The balance is the account's value at the end of the previous 31 December, at that day's real prices, as the book
  stood then (`exits.snapshot`). What cannot be valued on that day is not estimated: the account reads "not valued".
- Traditional, SEP, SIMPLE and rollover IRAs aggregate: each one's amount is worked out, and the owner's total may be
  taken from any of them. An employer plan would not aggregate, and none is held here.

Not modelled, and named: the Joint Life table for a spouse more than ten years younger who is the sole beneficiary,
and delaying the first distribution to 1 April of the next year.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.graph import Account

TABLE_SOURCE = "IRS Pub 590-B (2025), Appendix B, Table III (Uniform Lifetime); 26 CFR 1.401(a)(9)-9(c)"
START_SOURCE = "SECURE 2.0 Act §107, IRC §401(a)(9)(C)(v): 73 if born 1951-1959, 75 if born 1960 or later"
AGGREGATION_SOURCE = "IRS Pub 590-B, Miscellaneous rules: the RMDs of several IRAs may be totalled and taken from any of them"

# Age reached in the distribution year -> distribution period (checked against Pub 590-B (2025) on 21 Nov 2026).
UNIFORM_LIFETIME: dict[int, float] = {
    72: 27.4, 73: 26.5, 74: 25.5, 75: 24.6, 76: 23.7, 77: 22.9, 78: 22.0, 79: 21.1, 80: 20.2, 81: 19.4,
    82: 18.5, 83: 17.7, 84: 16.8, 85: 16.0, 86: 15.2, 87: 14.4, 88: 13.7, 89: 12.9, 90: 12.2, 91: 11.5,
    92: 10.8, 93: 10.1, 94: 9.5, 95: 8.9, 96: 8.4, 97: 7.8, 98: 7.3, 99: 6.8, 100: 6.4, 101: 6.0,
    102: 5.6, 103: 5.2, 104: 4.9, 105: 4.6, 106: 4.3, 107: 4.1, 108: 3.9, 109: 3.7, 110: 3.5, 111: 3.4,
    112: 3.3, 113: 3.1, 114: 3.0, 115: 2.9, 116: 2.8, 117: 2.7, 118: 2.5, 119: 2.3,
}
TOP_PERIOD = 2.0   # 120 and over

# Registrations whose owner takes an RMD, all IRAs, so they aggregate for one owner.
IRA_GROUP = frozenset({"traditional_ira", "employer_rollover", "sep_ira", "simple_ira"})
NOT_MODELLED = ["joint_life_table_spouse_over_10_years_younger", "first_distribution_delayed_to_1_april"]


def start_age(dob: date) -> int:
    """The age a required distribution starts, from the birth year (SECURE 2.0). Before 1951 the owner started under
    the earlier rules and is past it, so 72 stands for "already started"."""
    return 75 if dob.year >= 1960 else 73 if dob.year >= 1951 else 72


def age_in_year(dob: date, year: int) -> int:
    """The age the owner reaches in `year`, which is the age the table is read at."""
    return year - dob.year


def divisor(age: int) -> float | None:
    if age >= 120:
        return TOP_PERIOD
    return UNIFORM_LIFETIME.get(age)


def law() -> list[dict]:
    return [{"what": "divisor", "source": TABLE_SOURCE}, {"what": "start age", "source": START_SOURCE},
            {"what": "aggregation", "source": AGGREGATION_SOURCE}]


async def account_due(session: AsyncSession, account: Account, dob: date, today: date) -> dict:
    """This year's RMD for one owner-held IRA, from its value at the previous 31 December."""
    year = today.year
    age, start = age_in_year(dob, year), start_age(dob)
    base = {"owner_age": age, "starts_at_age": start, "starts_in_year": dob.year + start, "law": law(),
            "not_modelled": NOT_MODELLED}
    if age < start:
        return {**base, "status": "not_yet"}

    from app.aurea_core import exits   # exits reads this module's caller; imported here to keep the graph acyclic

    year_end = date(year - 1, 12, 31)
    snap = await exits.snapshot(session, [account], year_end)
    factor = divisor(age)
    base.update(year_end=year_end.isoformat(), divisor=factor, by=date(year, 12, 31).isoformat())
    if not snap["complete"]:
        unvalued = [p["symbol"] for p in snap["accounts"][0]["positions"] if p["status"] != "valued"]
        return {**base, "status": "not_valued", "account_value": None, "amount": None,
                "reason": f"The account cannot be valued at {year_end.isoformat()}: "
                          + (f"no real price that day for {', '.join(unvalued)}." if unvalued else "its cash on that day is not known.")}
    value = float(snap["accounts"][0]["value"])
    return {**base, "status": "required", "account_value": round(value, 2), "amount": round(value / factor, 2),
            "first_year": age == start}
