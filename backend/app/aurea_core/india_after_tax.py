"""What is left after tax: deposits against debt and arbitrage funds, and growth plans against income plans (B-396, L200-2
India section 3.2). Figures laid out, never a recommendation.

**The rates are stated, never invented.** An adviser, a paraplanner or the portfolio team records a yearly return before
tax from a document: a bank's rate sheet for a deposit, a scheme's factsheet, or the firm's own investment note. It is
added and never edited; the statement with the latest date stands for its vehicle. A vehicle with none on file is not
assessed, and no market figure or assumption set stands in for it. A stated rate is a number to compare tax on, not a
forecast, and a fund's return is not promised.

**The tax is each taxpayer's own.** A person or an HUF with a slab rate on file; without one they are not assessed. The
surcharge is read from the income on file, and with no income on file it is left off and said so. An amount and a holding
period are the question's, and the ones assumed when none is given are named. The rules are `law_figures` rows, assumed
and unreviewed:

  deposit      the interest is taxed at slab in the year it accrues.
  debt fund    bought now, the gain is taxed at slab when sold, however long it was held.
  arbitrage    taxed as equity: the short-term rate up to twelve months, the long-term rate beyond, shown without the yearly
  and equity   exemption and, beside it, with the whole of it unused in the year of sale.
  IDCW         every distribution is income at slab. For the comparison the whole return is taken to be paid out each year
               and put back after tax, against the growth plan of the same scheme at the same stated return.

Nothing here ranks, picks or recommends, and a client reads none of it.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import counsel_review, firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Household, LegalEntity, Mandate, Person
from app.models.india_tax_planning import StatedReturn
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM})
VEHICLES = {"deposit": "Deposit", "debt_fund": "Debt fund", "arbitrage_fund": "Arbitrage fund", "equity_fund": "Equity fund"}
FUNDS = ("debt_fund", "arbitrage_fund", "equity_fund")
EQUITY_TAXED = ("arbitrage_fund", "equity_fund")
RULE_OF = {"deposit": "in_deposit_interest_rule", "debt_fund": "in_specified_fund_rule", "arbitrage_fund": "in_arbitrage_equity_rule",
           "equity_fund": "in_arbitrage_equity_rule"}
FIGURES = ("in_deposit_interest_rule", "in_specified_fund_rule", "in_arbitrage_equity_rule", "in_idcw_rule", "in_cg_equity_st_rate",
           "in_cg_equity_lt_rate", "in_cg_equity_lt_exemption", "in_cg_equity_months", "in_surcharge_special_cap", "in_cess")
DEFAULT_AMOUNT, DEFAULT_MONTHS = 1_000_000, 36
RATE_MAX, MONTHS_MAX, AMOUNT_MAX = 30.0, 360, 10_000_000_000
ADULT = 18
NOT_MODELLED = [
    "Differences in risk, liquidity and deposit insurance between these",
    "That a fund's return varies and may be negative",
    "Exit loads, securities transaction tax and a deposit broken early",
    "Fund expenses, beyond what the stated return already carries",
    "Tax deducted at source and when it is paid",
    "The surcharge band moving because of this income",
    "The section 87A rebate and losses set off",
    "Debt fund units bought before 1 April 2023",
    "A trust's rate of tax",
]
ASSUMPTIONS = [
    "The stated return is the same every year and compounds yearly",
    "Deposit interest is taxed each year at slab and the rest is left in",
    "An income plan pays out the whole return each year, and it is put back after tax",
    "A fund is sold, and taxed, at the end of the period",
]
NOTE = "Figures laid out for a qualified person to check. Nothing here recommends a product, a plan or a switch."


class AfterTaxError(ValueError):
    """This cannot be recorded or read, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


# ── Stated returns ───────────────────────────────────────────────────────────

async def standing_returns(session: AsyncSession, firm_id: uuid.UUID) -> dict[str, StatedReturn]:
    rows = (await session.execute(select(StatedReturn).where(StatedReturn.firm_id == firm_id)
                                  .order_by(StatedReturn.stated_on, StatedReturn.created_at, StatedReturn.id))).scalars().all()
    return {r.vehicle: r for r in rows}


def return_view(row: StatedReturn) -> dict:
    return {"id": str(row.id), "vehicle": row.vehicle, "label": VEHICLES.get(row.vehicle, row.vehicle), "name": row.name,
            "rate": float(row.rate), "stated_on": row.stated_on.isoformat(), "document": row.source_document, "recorded_by": row.recorded_by}


async def _rate_views(session: AsyncSession, firm_id: uuid.UUID) -> list[dict]:
    standing = await standing_returns(session, firm_id)
    return [{**return_view(standing[k]), "state": "on_file"} if k in standing else
            {"vehicle": k, "label": label, "state": "not_on_file", "rate": None} for k, label in VEHICLES.items()]


def _number(value, label: str) -> float:
    try:
        n = float(str(value).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        raise AfterTaxError(f"Give {label} as a number.") from None
    if n != n or n in (float("inf"), float("-inf")):
        raise AfterTaxError(f"Give {label} as a number.")
    return n


async def prepare_return(session: AsyncSession, firm: Firm, role, *, vehicle=None, name=None, rate_percent=None, stated_on=None,
                         document=None) -> dict:
    if not applies(firm):
        raise AfterTaxError("A stated return for the after-tax comparison is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise AfterTaxError("A stated return is recorded by an adviser, a paraplanner or the portfolio team, from its document.")
    kind = str(vehicle or "").strip().lower().replace(" ", "_")
    if kind not in VEHICLES:
        raise AfterTaxError(f"The vehicle is one of {', '.join(VEHICLES)}.")
    said = str(name or "").strip()
    if len(said) < 3:
        raise AfterTaxError("Name the deposit, the scheme or the note the return belongs to, as the document names it.")
    rate = _number(rate_percent, "the yearly return before tax, in percent")
    if not 0 <= rate <= RATE_MAX:
        raise AfterTaxError(f"The yearly return before tax is a percentage from 0 to {RATE_MAX:g}.")
    today = firm_time.today(firm)
    try:
        day = stated_on if isinstance(stated_on, date) else date.fromisoformat(str(stated_on or "")[:10])
    except ValueError:
        raise AfterTaxError("Give the document's date as YYYY-MM-DD.") from None
    if day > today:
        raise AfterTaxError("A document cannot be dated ahead.")
    source = str(document or "").strip()
    if len(source) < 5:
        raise AfterTaxError("Say which document states the return: the rate sheet, the factsheet or the firm's note.")
    before = (await standing_returns(session, firm.id)).get(kind)
    if before is not None and day < before.stated_on:
        raise AfterTaxError(f"A return of {firm_time.spoken(before.stated_on)} is already on file for this, which is newer than this one.")
    if before is not None and day == before.stated_on and float(before.rate) == round(rate, 4) and before.name == said[:200]:
        raise AfterTaxError("That return is already on file.")
    return {"vehicle": kind, "name": said[:200], "rate": round(rate, 4), "stated_on": day, "document": source[:300], "replaces": before}


async def record_return(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    ready = await prepare_return(session, firm, role, **terms)
    row = StatedReturn(created_at=utcnow(), firm_id=firm.id, vehicle=ready["vehicle"], name=ready["name"], rate=ready["rate"],
                       stated_on=ready["stated_on"], source_document=ready["document"], recorded_by=by[:200])
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="stated_return_recorded", actor=by, content={
        "return_id": str(row.id), "vehicle": row.vehicle, "name": row.name, "rate": float(row.rate), "stated_on": row.stated_on.isoformat(),
        "document": row.source_document, "replaces": str(ready["replaces"].id) if ready["replaces"] else None})
    return return_view(row)


# ── The arithmetic ───────────────────────────────────────────────────────────

def _result(amount: float, months: int, before: float, after: float) -> dict:
    return {"before_tax_value": round(before, 2), "after_tax_value": round(after, 2), "tax_takes": round(before - after, 2),
            "after_tax_return": round(((after / amount) ** (12 / months) - 1) * 100, 2)}


def effective_rates(slab: float, surcharge: float) -> dict:
    """The rate each kind of income bears for one taxpayer, with surcharge and cess."""
    cess, cap = law_figures.value("in_cess"), law_figures.value("in_surcharge_special_cap")
    capped = min(surcharge, cap)
    return {"slab": slab * (1 + surcharge) * (1 + cess),                # interest, and a debt fund's gain
            "distribution": slab * (1 + capped) * (1 + cess),          # an income distribution
            "equity_short": law_figures.value("in_cg_equity_st_rate") * (1 + capped) * (1 + cess),
            "equity_long": law_figures.value("in_cg_equity_lt_rate") * (1 + capped) * (1 + cess)}


def held(vehicle: str, rate_percent: float, amount: float, months: int, rates: dict) -> dict:
    """One vehicle bought now and ended after `months`, at one taxpayer's rates. A fund here is its growth plan."""
    r, years = rate_percent / 100, months / 12
    before = amount * (1 + r) ** years
    gain = before - amount
    if vehicle == "deposit":
        return {**_result(amount, months, before, amount * (1 + r * (1 - rates["slab"])) ** years),
                "basis": "Interest at slab each year", "rate_borne": round(rates["slab"], 6)}
    if vehicle == "debt_fund":
        return {**_result(amount, months, before, before - gain * rates["slab"]),
                "basis": "Gain at slab when sold", "rate_borne": round(rates["slab"], 6)}
    long_term = months > int(law_figures.value("in_cg_equity_months"))
    borne = rates["equity_long" if long_term else "equity_short"]
    out = {**_result(amount, months, before, before - gain * borne), "rate_borne": round(borne, 6),
           "basis": "Long-term equity gain" if long_term else "Short-term equity gain"}
    if long_term:
        exempt = law_figures.value("in_cg_equity_lt_exemption")
        out["if_exemption_unused"] = _result(amount, months, before, before - max(gain - exempt, 0.0) * borne)
    return out


def income_plan(rate_percent: float, amount: float, months: int, rates: dict) -> dict:
    r, years = rate_percent / 100, months / 12
    return {**_result(amount, months, amount * (1 + r) ** years, amount * (1 + r * (1 - rates["distribution"])) ** years),
            "basis": "Each distribution at slab", "rate_borne": round(rates["distribution"], 6)}


# ── The reading ──────────────────────────────────────────────────────────────

def _surcharge(income: float | None, regime: str) -> float:
    if income is None:
        return 0.0
    for upper, rate in law_figures.bands("in_surcharge_old" if regime == "old" else "in_surcharge_new"):
        if float(income) <= upper:
            return rate
    return 0.0


async def _taxpayers(session: AsyncSession, firm: Firm, household_id: uuid.UUID, today: date) -> list[dict]:
    """The household's adults with a tax profile or an account of their own, and its entities with either."""
    owners = {x for row in (await session.execute(select(Mandate.person_id, Mandate.entity_id).join(Account, Account.mandate_id == Mandate.id)
                                                  .where(Mandate.firm_id == firm.id))).all() for x in row if x}
    out = []
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == household_id,
                                                         Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all()
    for p in people:
        born = p.date_of_birth
        if born is not None and today.year - born.year - ((today.month, today.day) < (born.month, born.day)) < ADULT:
            continue
        tax = (p.profile or {}).get("tax") or {}
        if tax or p.id in owners:
            out.append({"id": str(p.id), "name": p.full_name, "kind": "individual", "tax": tax})
    entities = (await session.execute(select(LegalEntity).where(LegalEntity.firm_id == firm.id, LegalEntity.household_id == household_id)
                                      .order_by(LegalEntity.name))).scalars().all()
    for e in entities:
        tax = (e.governance or {}).get("tax") or {}
        if tax or e.id in owners:
            out.append({"id": str(e.id), "name": e.name, "kind": str(e.entity_type), "tax": tax})
    return out


async def comparison(session: AsyncSession, firm: Firm, *, household_id: uuid.UUID | None = None, amount=None, months=None) -> dict:
    """The stated returns and, for a household, what each leaves after tax for each taxpayer."""
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    value = DEFAULT_AMOUNT if amount in (None, "") else _number(amount, "the amount in rupees")
    if not 0 < value <= AMOUNT_MAX:
        raise AfterTaxError("The amount is a positive sum in rupees.")
    period = DEFAULT_MONTHS if months in (None, "") else _number(months, "the months held")
    if period != int(period) or not 1 <= period <= MONTHS_MAX:
        raise AfterTaxError(f"The period is a whole number of months from 1 to {MONTHS_MAX}.")
    period = int(period)
    rates = await _rate_views(session, firm.id)
    out = {"applies": True, "as_of": today.isoformat(), "amount": round(value, 2), "months": period,
           "amount_assumed": amount in (None, ""), "months_assumed": months in (None, ""), "rates": rates,
           "figures": law_figures.cited(list(FIGURES), today), "assumptions": ASSUMPTIONS, "not_modelled": NOT_MODELLED, "note": NOTE,
           "household_id": None, "household": None, "taxpayers": []}
    if household_id is None:
        return await counsel_review.annotate(session, firm, out)
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise AfterTaxError("Household not found.")
    market = markets.of_household(firm, household.values)
    if market is None or market.code != "IN":
        raise AfterTaxError("This comparison is for a household taxed in India.")
    on_file = {r["vehicle"]: r for r in rates if r["state"] == "on_file"}
    rows = []
    for t in await _taxpayers(session, firm, household.id, today):
        tax = t.pop("tax")
        base = {**t, "income_on_file": tax.get("annual_income")}
        slab = tax.get("marginal_rate")
        if t["kind"] not in ("individual", "huf"):
            rows.append({**base, "state": "not_assessed", "needs": "A trust's rate of tax is not modelled."})
            continue
        if slab in (None, ""):
            rows.append({**base, "state": "not_assessed", "needs": "The slab rate, recorded on the tax profile from a document."})
            continue
        regime = tax.get("regime") or "new"
        surcharge = _surcharge(tax.get("annual_income"), regime)
        borne = effective_rates(float(slab), surcharge)
        rows.append({**base, "state": "assessed", "slab_rate": float(slab), "surcharge_rate": surcharge, "regime": regime,
                     "regime_assumed": not tax.get("regime"), "surcharge_assessed": tax.get("annual_income") is not None,
                     "held": [{"vehicle": k, "label": label, **({"state": "assessed", "name": on_file[k]["name"], "rate": on_file[k]["rate"],
                                                               **held(k, on_file[k]["rate"], value, period, borne)}
                                                              if k in on_file else {"state": "not_assessed", "needs": "no stated return on file"})}
                              for k, label in VEHICLES.items()],
                     "plans": [{"vehicle": k, "label": VEHICLES[k], "name": on_file[k]["name"], "rate": on_file[k]["rate"],
                                "growth": held(k, on_file[k]["rate"], value, period, borne),
                                "income": income_plan(on_file[k]["rate"], value, period, borne)} for k in FUNDS if k in on_file]})
    out |= {"household_id": str(household.id), "household": household.name, "taxpayers": rows}
    return await counsel_review.annotate(session, firm, out)
