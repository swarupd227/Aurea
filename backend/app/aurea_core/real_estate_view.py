"""The real estate gains choice, read from the conversation (B-274): land or a building sold after a long hold, 12.5% on the gain or, for a
resident individual or HUF who bought before 23 July 2024, the lower of that and 20% on the indexed gain.

`tax_india_full.real_estate_choice` was a function with no tool. This puts it behind `read_real_estate_choice`: the person gives the two dates,
the cost and the proceeds, the cost inflation index for each year as the notification states it (never worked out here) and whether the seller
is a resident individual or HUF. A fact not given is "not assessed", and nothing is recorded, saved or filed: it is a worked figure for a
qualified person to check, an estimate.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, tax_india_full
from app.models.tenant import Firm

KEYS = ["in_cg_other_lt_rate"]
NOT_MODELLED = "Surcharge and cess, the s.54 and s.54EC exemptions, expenses of sale and stamp duty value (s.50C, s.43CA) are not modelled. An estimate for a qualified person to check."


def _date(value, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise ValueError(f"Give {label} as YYYY-MM-DD.")


def _num(value, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        raise ValueError(f"{label[0].upper() + label[1:]} is a number.")


def _flag(value) -> bool | None:
    if value in (None, ""):
        return None
    return str(value).strip().lower() in ("true", "yes", "1", "y")


async def read(session: AsyncSession, firm: Firm, inputs: dict) -> dict:
    from app.llm.executors import ExecutorError

    today = firm_time.today(firm)
    market = markets.for_firm(firm)
    if market is None or market.code != "IN":
        return {"applies": False, "message": "The real estate choice is worked for an Indian firm."}
    try:
        acquired, sold = _date(inputs.get("acquired_on"), "the day it was bought"), _date(inputs.get("sold_on"), "the day it was sold")
        cost, proceeds = _num(inputs.get("cost"), "the cost"), _num(inputs.get("proceeds"), "the proceeds")
        cii_a, cii_s = _num(inputs.get("cii_acquired"), "the index for the year of purchase"), _num(inputs.get("cii_sold"), "the index for the year of sale")
    except ValueError as exc:
        raise ExecutorError(str(exc))
    resident = _flag(inputs.get("resident_individual_or_huf"))
    missing = [name for name, v in (("the day it was bought", acquired), ("the day it was sold", sold), ("the cost", cost), ("the proceeds", proceeds),
                                     ("whether the seller is a resident individual or HUF", resident)) if v is None]
    if missing:
        return {"applies": True, "as_of": today.isoformat(), "state": "not_assessed", "needs": missing, "estimate": True, "figures": law_figures.cited(KEYS, today),
                "message": "Not assessed: " + ", ".join(missing) + " needed. Nothing is assumed."}
    if sold < acquired:
        raise ExecutorError("It cannot be sold before it was bought.")
    result = tax_india_full.real_estate_choice(acquired_on=acquired, sold_on=sold, cost=cost, proceeds=proceeds, cii_acquired=cii_a, cii_sold=cii_s,
                                               resident_individual_or_huf=resident)
    result.update({"applies": True, "as_of": today.isoformat(), "state": "not_assessed" if result["needs"] else "assessed", "estimate": True,
                   "acquired_on": acquired.isoformat(), "sold_on": sold.isoformat(), "cost": cost, "proceeds": proceeds,
                   "figures": law_figures.cited(KEYS, today), "not_modelled": NOT_MODELLED})
    result["message"] = f"{result['choice']}. Tax {markets.money(result['tax'], firm.base_currency or 'INR')}. {NOT_MODELLED}"
    return result
