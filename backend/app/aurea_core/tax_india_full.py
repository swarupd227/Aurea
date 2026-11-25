"""The rest of the Indian tax engine, beside `tax_india.py`'s per-taxpayer, first-in first-out gains (India row 11).

`tax_india.py` reads the book taxpayer by taxpayer and nets nothing across a family. This adds what an adviser and a
qualified person still need around it, each from what a person recorded and "not assessed" where they did not:

  asset classes     each class's holding period, its rate and whether it has the exemption, from `law_figures`
  loss ledger       capital losses carried forward from filed returns, eight tax years, only if the return was filed by
                    its due date; long-term losses only against long-term gains (the set-off itself is `tax_india.set_off`)
  grandfathering    listed equity bought before 1 Feb 2018 is valued at the higher of its cost and the lower of its
                    31 Jan 2018 value and the sale price, only for an instrument whose 2018 value a person recorded
  regime comparison tax on ordinary income under the new and the old regime, only where the income is recorded; without the
                    old regime's deductions on file it says what deductions would make the old regime win, and does not pick
  advance tax       the four instalments of the year's tax, and the dates of the return
  flags             what a harvest does to the exemption, and that a rebuy is a review item (`harvest_guard` decides)

Every figure is an estimate for a qualified person to check. The rates are the Income-tax Act 2025 as the research report read
it, overlaid with the Finance Bill 2026; the enacted Finance Act and the 2026 Rules were not read. What is left out travels in
`tax_india.NOT_MODELLED`. Nothing here files, pays or trades, and nothing says a rebuy is safe: India has no wash-sale rule, and
the same-day, bonus-stripping and GAAR guards stay in `harvest_guard.py`.
"""
from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, tax_india
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Household, LegalEntity, Person
from app.models.india_tax import GrandfatherValue, TaxLossEntry
from app.models.portfolio import Instrument
from app.models.tenant import Firm
from app.provenance import ledger as audit_ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM})
LOSS_KINDS = {"short_term": "Short-term capital loss", "long_term": "Long-term capital loss"}
DUE_SOON_DAYS = 14
ADVANCE = (("15_jun", 6, 15), ("15_sep", 9, 15), ("15_dec", 12, 15), ("15_mar", 3, 15))
ESTIMATE = "An estimate for a qualified person to check: the enacted Finance Act 2026 and the Income-tax Rules 2026 were not read."
# The return's due day for a taxpayer with no audit: the research read 31 July for an individual (s.263(1)); an audited
# business or a different date is not modelled, and the day used is named.
RETURN_DUE = (7, 31)
RETURN_DUE_BASIS = "assumed: 31 July after the year, an individual or HUF with no audit; the Act's s.263(1) day for other taxpayers is not modelled"


class TaxFactError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


# ── Asset classes ───────────────────────────────────────────────────────────────────────────────────────────────
def asset_classes(today: date) -> list[dict]:
    """How the Act treats each class, as a table: when it goes long-term, what a short and a long gain are taxed at."""
    st, lt = law_figures.value("in_cg_equity_st_rate"), law_figures.value("in_cg_equity_lt_rate")
    other = law_figures.value("in_cg_other_lt_rate")
    ex = law_figures.value("in_cg_equity_lt_exemption")
    eq_m, ot_m = int(law_figures.value("in_cg_equity_months")), int(law_figures.value("in_cg_other_months"))
    cite = law_figures.get("in_cg_equity_st_rate").source
    rows = [
        ("listed_equity", "Listed equity, STT paid", eq_m, st, lt, ex, "Equity bought before 1 Feb 2018 is grandfathered where its 2018 value is recorded."),
        ("equity_fund", "Equity-oriented fund units", eq_m, st, lt, ex, "More than 65% in equity for an ordinary scheme; the fund's own test, not worked out here."),
        ("reit_invit", "REIT and InvIT units, STT paid", eq_m, st, lt, ex, ""),
        ("specified_fund", "Specified mutual fund (65% or more in debt, bought on or after 1 Apr 2023)", None, None, None, None, "Always short-term, taxed at slab."),
        ("mld", "Market-linked debenture", None, None, None, None, "Always short-term, taxed at slab."),
        ("unlisted_debt", "Unlisted bond or debenture transferred on or after 23 Jul 2024", None, None, None, None, "Always short-term, taxed at slab."),
        ("other_listed", "Other listed funds, ETFs and bonds", eq_m, None, other, None, "Short-term at slab; long-term without the exemption or indexation."),
        ("sgb", "Sovereign Gold Bond, sold on the exchange before maturity", eq_m, None, other, None,
         "Short-term at slab; long-term without the exemption. A gain on redemption at maturity is exempt for an individual (in_sgb_rule); new issuance has been discontinued."),
        ("unlisted", "Unlisted shares and other unlisted funds", ot_m, None, other, None, "Short-term at slab."),
        ("foreign_shares", "Foreign shares not listed in India", ot_m, None, other, None, "The research report's inference from the general rule."),
        ("physical_gold", "Physical gold", ot_m, None, other, None, "The research report's inference from the general rule."),
        ("land_building", "Land or building", ot_m, None, other, None,
         "For a resident individual or HUF who bought before 23 Jul 2024 the tax above 20% of the indexed gain is ignored: see real_estate_choice."),
    ]
    return [{"key": k, "label": label, "long_after_months": m, "short_rate": s, "long_rate": l, "exemption": e,
             "short_text": "Slab" if s is None else f"{s * 100:g}%",
             "long_text": "None: always short-term" if m is None else (f"{l * 100:g}%" + (" above the exemption" if e else "")),
             "note": note, "source": cite} for k, label, m, s, l, e, note in rows]


def real_estate_choice(*, acquired_on: date, sold_on: date, cost: float, proceeds: float, cii_acquired: float | None,
                       cii_sold: float | None, resident_individual_or_huf: bool) -> dict:
    """Land or a building held long enough: 12.5% on the unindexed gain, or for a resident individual or HUF who bought before
    23 Jul 2024 the lower of that and 20% on the indexed gain. The index values are the caller's, from the notification."""
    cutoff = date(2024, 7, 23)
    gain = proceeds - cost
    plain = max(gain, 0.0) * law_figures.value("in_cg_other_lt_rate")
    out = {"gain": round(gain, 2), "tax_without_indexation": round(plain, 2), "tax_with_indexation": None, "tax": round(plain, 2),
           "choice": "12.5% on the gain, no indexation", "needs": []}
    if not (resident_individual_or_huf and acquired_on < cutoff):
        out["choice"] = "12.5% on the gain, no indexation (indexation is for a resident individual or HUF who bought before 23 Jul 2024)"
        return out
    if not cii_acquired or not cii_sold:
        out["needs"] = ["cost inflation index for the year of purchase and the year of sale"]
        out["choice"] = "Not assessed: indexation needs the cost inflation index for both years"
        return out
    indexed = max(proceeds - cost * (cii_sold / cii_acquired), 0.0) * 0.20
    out["tax_with_indexation"] = round(indexed, 2)
    out["tax"] = round(min(plain, indexed), 2)
    out["choice"] = "20% on the indexed gain" if indexed < plain else "12.5% on the gain, no indexation"
    out["note"] = "A loss is not indexed: the formula adjusts tax, not income."
    return out


# ── Loss ledger and grandfather values: what a person recorded ───────────────────────────────────────────────────
def _year_start(label: str) -> int:
    return int(label[:4])


def year_label(start: int) -> str:
    return f"{start}-{(start + 1) % 100:02d}"


async def _holder(session: AsyncSession, firm: Firm, person_id, entity_id):
    if bool(person_id) == bool(entity_id):
        raise TaxFactError("Say whose it is: one person or one entity.")
    try:
        row = await session.get(Person, uuid.UUID(str(person_id))) if person_id else await session.get(LegalEntity, uuid.UUID(str(entity_id)))
    except ValueError:
        raise TaxFactError("Give the taxpayer's id, from the client's page.")
    household = await session.get(Household, row.household_id) if row is not None and row.household_id else None
    if row is None or household is None or household.firm_id != firm.id:
        raise TaxFactError("No such person or entity at this firm.")
    return row


async def ledger(session: AsyncSession, firm_id: uuid.UUID, *, person_id=None, entity_id=None, today: date) -> list[dict]:
    """A taxpayer's recorded losses, each with the last tax year it may be set off in and whether it can be carried forward.

    The latest record for a year and kind stands. A loss of the current tax year is not brought forward, and one whose
    return was not filed by its due day cannot be carried forward at all (s.121)."""
    col = TaxLossEntry.person_id == person_id if person_id else TaxLossEntry.entity_id == entity_id
    rows = (await session.execute(select(TaxLossEntry).where(TaxLossEntry.firm_id == firm_id, col)
                                  .order_by(TaxLossEntry.created_at))).scalars().all()
    latest: dict = {}
    for r in rows:
        latest[(r.tax_year, r.kind)] = r
    current = _year_start(markets.tax_year(markets.MARKETS["IN"], today)["label"])
    carry = int(law_figures.value("in_loss_carry_years"))
    out = []
    for (year, kind), r in sorted(latest.items(), key=lambda kv: (_year_start(kv[0][0]), kv[0][1])):
        start = _year_start(year)
        last = start + carry
        due = date(start + 1, *RETURN_DUE)
        amount, used = float(r.amount), float(r.used or 0)
        if start >= current:
            state, why = "current_year", "Arose this tax year: set off in the year's own position, not brought forward."
        elif r.return_filed_on is None:
            state, why = "no_return", "No return on file for that year: a loss is carried forward only from a return filed by its due date."
        elif r.return_filed_on > due:
            state, why = "late_return", f"The return was filed {r.return_filed_on.isoformat()}, after the due day ({due.isoformat()}, assumed): it cannot be carried forward."
        elif current > last:
            state, why = "expired", f"Carried forward {carry} tax years, to {year_label(last)}; that has passed."
        else:
            state, why = "carried", f"May be set off to {year_label(last)}."
        out.append({"tax_year": year, "kind": kind, "label": LOSS_KINDS[kind], "amount": round(amount, 2), "used": round(used, 2),
                    "available": round(max(amount - used, 0.0), 2) if state == "carried" else 0.0, "state": state, "why": why,
                    "last_year": year_label(last), "return_filed_on": r.return_filed_on.isoformat() if r.return_filed_on else None,
                    "due_assumed": due.isoformat(), "document": r.document, "recorded_by": r.recorded_by})
    return out


async def brought_forward(session: AsyncSession, firm_id: uuid.UUID, *, person_id=None, entity_id=None, today: date,
                          fallback: dict | None = None) -> dict:
    """What the year starts with: the ledger where a person recorded one, else the single figure on the profile. The ledger is
    the more detailed record, so it replaces the profile's and nothing counts twice."""
    if not person_id and not entity_id:
        return fallback or {}
    rows = await ledger(session, firm_id, person_id=person_id, entity_id=entity_id, today=today)
    if not rows:
        return fallback or {}
    return {"short_term": round(sum(r["available"] for r in rows if r["kind"] == "short_term"), 2),
            "long_term": round(sum(r["available"] for r in rows if r["kind"] == "long_term"), 2), "from_ledger": True}


async def fmv_map(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    rows = (await session.execute(select(GrandfatherValue).where(GrandfatherValue.firm_id == firm_id)
                                  .order_by(GrandfatherValue.created_at))).scalars().all()
    return {r.instrument_id: float(r.fmv_per_unit) for r in rows}


async def prepare_loss(session: AsyncSession, firm: Firm, *, person_id=None, entity_id=None, tax_year: str | None, kind: str | None,
                       amount, used=None, return_filed_on=None, document: str | None) -> dict:
    if not applies(firm):
        raise TaxFactError("A capital loss is recorded for an Indian firm's taxpayer.")
    holder = await _holder(session, firm, person_id, entity_id)
    today = firm_time.today(firm)
    year = (tax_year or "").strip()
    try:
        start = int(year[:4])
        ok = len(year) == 7 and year[4] == "-" and int(year[5:]) == (start + 1) % 100
    except ValueError:
        ok = False
    if not ok:
        raise TaxFactError("The tax year is written like 2023-24.")
    current = _year_start(markets.tax_year(markets.MARKETS["IN"], today)["label"])
    if start >= current:
        raise TaxFactError(f"{year} is this tax year or later: a loss is carried forward from a year already filed, so give an earlier one.")
    if start < current - int(law_figures.value("in_loss_carry_years")) - 1:
        raise TaxFactError(f"{year} is beyond the {int(law_figures.value('in_loss_carry_years'))} years a loss is carried forward.")
    k = (kind or "").strip().lower()
    if k not in LOSS_KINDS:
        raise TaxFactError("The kind is short_term or long_term.")
    try:
        amt = float(amount)
        spent = float(used or 0)
    except (TypeError, ValueError):
        raise TaxFactError("The amount is the loss in rupees, as the filed return states it.")
    if amt <= 0:
        raise TaxFactError("The loss is a positive amount in rupees.")
    if spent < 0 or spent > amt:
        raise TaxFactError("What was already set off is between nothing and the loss.")
    filed = None
    if return_filed_on not in (None, ""):
        try:
            filed = return_filed_on if isinstance(return_filed_on, date) else date.fromisoformat(str(return_filed_on)[:10])
        except ValueError:
            raise TaxFactError("The day the return was filed is a date, YYYY-MM-DD.")
        if filed > today:
            raise TaxFactError("A return cannot have been filed in the future.")
    if len((document or "").strip()) < 4:
        raise TaxFactError("Say where it is on file: the acknowledgement of the return that states the loss.")
    before = [r for r in await ledger(session, firm.id, person_id=holder.id if isinstance(holder, Person) else None,
                                      entity_id=holder.id if isinstance(holder, LegalEntity) else None, today=today)
              if r["tax_year"] == year and r["kind"] == k]
    return {"holder": holder, "tax_year": year, "kind": k, "amount": amt, "used": spent, "return_filed_on": filed,
            "document": document.strip(), "before": before[0] if before else None,
            "name": holder.full_name if isinstance(holder, Person) else holder.name}


async def record_loss(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare_loss(session, firm, **terms)
    holder = p["holder"]
    session.add(TaxLossEntry(firm_id=firm.id, person_id=holder.id if isinstance(holder, Person) else None,
                             entity_id=holder.id if isinstance(holder, LegalEntity) else None, tax_year=p["tax_year"], kind=p["kind"],
                             amount=p["amount"], used=p["used"], return_filed_on=p["return_filed_on"], document=p["document"],
                             recorded_by=by, note=(note or "").strip() or None, created_at=utcnow()))
    await session.flush()
    await audit_ledger.append_entry(session, firm_id=firm.id, event_type="tax_loss_recorded", actor=by, content={
        "taxpayer": p["name"], "tax_year": p["tax_year"], "kind": p["kind"], "amount": p["amount"], "used": p["used"],
        "return_filed_on": p["return_filed_on"].isoformat() if p["return_filed_on"] else None, "document": p["document"]})
    return await records(session, firm)


async def prepare_fmv(session: AsyncSession, firm: Firm, *, instrument_id=None, symbol: str | None = None, fmv_per_unit, document: str | None) -> dict:
    if not applies(firm):
        raise TaxFactError("A 31 Jan 2018 value is recorded for an Indian firm's equity.")
    inst = None
    if instrument_id:
        try:
            inst = await session.get(Instrument, uuid.UUID(str(instrument_id)))
        except ValueError:
            raise TaxFactError("Give the instrument's id, or its symbol.")
    elif symbol:
        inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == symbol.strip()))).scalars().first()
    if inst is None or inst.firm_id != firm.id:
        raise TaxFactError("No such instrument at this firm.")
    if tax_india.tax_class_of(inst) not in tax_india.EQUITY_CLASSES:
        raise TaxFactError(f"{inst.symbol} is not listed equity, an equity fund or a REIT or InvIT, so the 2018 value does not apply to it.")
    try:
        value = float(fmv_per_unit)
    except (TypeError, ValueError):
        raise TaxFactError("The value is the price per unit on 31 Jan 2018, in rupees.")
    if value <= 0:
        raise TaxFactError("The value is a positive price in rupees.")
    if len((document or "").strip()) < 4:
        raise TaxFactError("Say where it is from: the exchange's published price for 31 Jan 2018.")
    before = (await fmv_map(session, firm.id)).get(inst.id)
    return {"instrument": inst, "fmv": value, "document": document.strip(), "before": before}


async def record_fmv(session: AsyncSession, firm: Firm, *, by: str, note: str | None = None, **terms) -> dict:
    p = await prepare_fmv(session, firm, **terms)
    session.add(GrandfatherValue(firm_id=firm.id, instrument_id=p["instrument"].id, fmv_per_unit=p["fmv"], document=p["document"],
                                 recorded_by=by, note=(note or "").strip() or None, created_at=utcnow()))
    await session.flush()
    await audit_ledger.append_entry(session, firm_id=firm.id, event_type="grandfather_value_recorded", actor=by, content={
        "symbol": p["instrument"].symbol, "fmv_per_unit": p["fmv"], "document": p["document"]})
    return await records(session, firm)


async def records(session: AsyncSession, firm: Firm) -> dict:
    """Everything a person recorded for the engine, with the asset class table and the year's dates."""
    if not applies(firm):
        return {"applies": False}
    today = firm_time.today(firm)
    taxpayers = []
    for p in (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.deceased_on.is_(None)).order_by(Person.full_name))).scalars().all():
        rows = await ledger(session, firm.id, person_id=p.id, today=today)
        if rows:
            taxpayers.append({"id": str(p.id), "name": p.full_name, "losses": rows,
                              "available": {k: round(sum(r["available"] for r in rows if r["kind"] == k), 2) for k in LOSS_KINDS}})
    for e in (await session.execute(select(LegalEntity).join(Household, Household.id == LegalEntity.household_id).where(Household.firm_id == firm.id))).scalars().all():
        rows = await ledger(session, firm.id, entity_id=e.id, today=today)
        if rows:
            taxpayers.append({"id": str(e.id), "name": e.name, "losses": rows,
                              "available": {k: round(sum(r["available"] for r in rows if r["kind"] == k), 2) for k in LOSS_KINDS}})
    values = await fmv_map(session, firm.id)
    names = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_(list(values))))).scalars().all()} if values else {}
    return {"applies": True, "as_of": today.isoformat(), "tax_year": markets.tax_year(markets.MARKETS["IN"], today)["label"],
            "taxpayers": taxpayers, "grandfather_values": [{"symbol": names[i].symbol, "name": names[i].name, "fmv_per_unit": v}
                                                             for i, v in values.items() if i in names],
            "asset_classes": asset_classes(today), "dates": return_dates(today), "estimate": ESTIMATE,
            "not_modelled": tax_india.NOT_MODELLED}


# ── Regime comparison ───────────────────────────────────────────────────────────────────────────────────────────
def slab_tax(taxable: float, bands: list[tuple[float, float]], exemption: float | None = None) -> float:
    """Tax on `taxable` through the slabs; a larger basic exemption replaces the first band's bound."""
    tax, lower = 0.0, 0.0
    for i, (upper, rate) in enumerate(bands):
        if i == 0 and exemption is not None:
            upper = max(upper, exemption)
        if taxable > lower:
            tax += (min(taxable, upper) - lower) * rate
        lower = max(lower, upper)
    return tax


def _surcharge(total_income: float, key: str) -> float:
    for upper, rate in law_figures.bands(key):
        if total_income <= upper:
            return rate
    return 0.0


def regime_tax(regime: str, *, income: float, salary: float | None, deductions: float, age: int | None, gains: float) -> dict:
    """Tax on ordinary income under one regime, with rebate, surcharge and cess. `gains` are the year's taxable gains, which
    count toward the rebate line and the surcharge band but are taxed by `tax_india`, not here."""
    new = regime == "new"
    std = law_figures.value("in_new_regime_std_deduction" if new else "in_old_regime_std_deduction") if salary else 0.0
    std = min(std, salary or 0.0)
    ded = 0.0 if new else deductions
    taxable = max(income - std - ded, 0.0)
    exemption = None
    if not new and age is not None:
        exemption = (law_figures.value("in_old_regime_exemption_super_senior") if age >= 80
                      else law_figures.value("in_old_regime_exemption_senior") if age >= 60 else None)
    slab = slab_tax(taxable, law_figures.bands("in_new_regime_bands" if new else "in_old_regime_bands"), exemption)
    line = law_figures.value("in_new_regime_rebate_line" if new else "in_old_regime_rebate_line")
    rebate = min(slab, law_figures.value("in_new_regime_rebate" if new else "in_old_regime_rebate")) if taxable + gains <= line else 0.0
    after = slab - rebate
    rate = _surcharge(taxable + gains, "in_surcharge_new" if new else "in_surcharge_old")
    cess = law_figures.value("in_cess")
    total = after * (1 + rate) * (1 + cess)
    return {"regime": regime, "taxable_income": round(taxable, 2), "standard_deduction": round(std, 2), "deductions": round(ded, 2),
            "slab_tax": round(slab, 2), "rebate": round(rebate, 2), "surcharge_rate": rate, "tax": round(total, 2)}


def regime_comparison(*, income, salary, deductions, age: int | None, gains: float, is_individual: bool, resident: bool | None = None) -> dict:
    """The new regime against the old, for salary and other ordinary income, only where that income was recorded."""
    out = {"assessed": False, "needs": [], "note": ESTIMATE + " Dividends, interest and TDS are not in the figures; surcharge is tested on the total only."}
    if not is_individual:
        out["needs"] = ["an individual: the comparison is for a person's salary and other income"]
        return out
    if income is None:
        out["needs"] = ["annual income"]
        return out
    out["assessed"] = True
    have_ded = deductions is not None
    new = regime_tax("new", income=float(income), salary=salary, deductions=0.0, age=age, gains=gains)
    old = regime_tax("old", income=float(income), salary=salary, deductions=float(deductions or 0.0), age=age, gains=gains)
    out.update({"new": new, "old": old, "old_deductions_recorded": have_ded, "salary_recorded": salary is not None,
                "age_recorded": age is not None})
    if salary is None:
        out["needs"].append("salary, for the standard deduction")
    if age is None:
        out["needs"].append("date of birth, for the old regime's higher basic exemption at 60 and 80")
    if have_ded:
        out["cheaper"] = "new" if new["tax"] <= old["tax"] else "old"
        out["saving"] = round(abs(new["tax"] - old["tax"]), 2)
        out["conclusive"] = True
        return out
    out["needs"].append("old-regime deductions (80C, 80D, home loan interest and the rest), from the client's papers")
    out["conclusive"] = False
    # What deductions would make the old regime cheaper: found by halving, since the tax is not a simple line in them.
    lo, hi = 0.0, max(float(income), 1.0)
    if old["tax"] <= new["tax"]:
        out["cheaper"], out["break_even_deductions"] = "old", 0.0
        return out
    if regime_tax("old", income=float(income), salary=salary, deductions=hi, age=age, gains=gains)["tax"] > new["tax"]:
        out["cheaper"], out["break_even_deductions"] = "new", None
        out["saving"] = round(old["tax"] - new["tax"], 2)
        return out
    for _ in range(40):
        mid = (lo + hi) / 2
        if regime_tax("old", income=float(income), salary=salary, deductions=mid, age=age, gains=gains)["tax"] > new["tax"]:
            lo = mid
        else:
            hi = mid
    out["cheaper"], out["break_even_deductions"] = "new", round(hi, 2)
    out["saving"] = round(old["tax"] - new["tax"], 2)
    return out


# ── Dates ───────────────────────────────────────────────────────────────────────────────────────────────────────
def return_dates(today: date) -> list[dict]:
    """The year's dates a taxpayer meets, in the compliance calendar's shape: each with its day, days away and state."""
    year = markets.tax_year(markets.MARKETS["IN"], today)
    start = year["start"].year
    items = []
    for key, month, day in ADVANCE:
        due = date(start + (1 if month < 4 else 0), month, day)
        items.append({"key": f"advance_tax_{key}", "label": f"Advance tax, {int(law_figures.value('in_advance_tax_' + key) * 100)}% of the year's tax",
                      "due_on": due, "share": law_figures.value("in_advance_tax_" + key),
                      "citation": law_figures.get("in_advance_tax_" + key).source})
    items.append({"key": "return", "label": "Return for the year, so losses may be carried forward", "due_on": date(start + 1, *RETURN_DUE),
                  "share": None, "citation": f"Income-tax Act 2025 s.263(1), s.121; {RETURN_DUE_BASIS}"})
    out = []
    for i in sorted(items, key=lambda x: x["due_on"]):
        days = (i["due_on"] - today).days
        out.append({**i, "due_on": i["due_on"].isoformat(), "days": days,
                    "state": "passed" if days < 0 else "due_soon" if days <= DUE_SOON_DAYS else "upcoming"})
    return out


def advance_tax(total_tax: float | None, today: date, *, needs: list[str]) -> dict:
    """The year's tax in four instalments. What was paid is not on file, so a date that has passed reads "passed, payment not
    recorded", never overdue. Below the floor no advance tax is asked."""
    floor = law_figures.value("in_advance_tax_floor")
    dates = [d for d in return_dates(today) if d["share"] is not None]
    if total_tax is None:
        return {"assessed": False, "needs": needs or ["the year's tax"], "instalments": dates}
    due = total_tax >= floor
    prev = 0.0
    inst = []
    for d in dates:
        amount = round(total_tax * d["share"] - prev, 2) if due else 0.0
        prev = total_tax * d["share"]
        inst.append({**d, "cumulative": round(total_tax * d["share"], 2) if due else 0.0, "amount": amount,
                     "paid": "not recorded" if d["state"] == "passed" else None})
    return {"assessed": True, "year_tax": round(total_tax, 2), "asked": due, "floor": floor, "instalments": inst, "needs": needs,
            "note": "Tax paid already, TDS and relief for gains that arise after an instalment date are not modelled."}


# ── What a harvest does ─────────────────────────────────────────────────────────────────────────────────────────
def interaction_flags(opportunities: list[dict], realised: dict, settled: dict) -> list[dict]:
    """What a sale does beside the tax it saves. Flags only: `harvest_guard` holds a proposal at its own checks, and no
    line here says a rebuy is safe, because India has no wash-sale rule and no case tests a sell-and-rebuy."""
    flags = []
    lt_equity = float(realised.get(tax_india.LT_EQUITY) or 0)
    ex = tax_india.LTCG_EXEMPTION
    for o in opportunities:
        sym = o.get("symbol")
        if o["kind"] == "loss":
            if o.get("long_term") and lt_equity < ex:
                flags.append({"kind": "exemption_burn", "symbol": sym, "tone": "warn",
                              "text": f"A long-term loss on {sym} is set off before the exemption, so it can use gains the exemption would have covered: "
                                      f"this year's long-term equity gain stands at {lt_equity:,.0f} against {ex:,.0f}."})
            flags.append({"kind": "rebuy_review", "symbol": sym, "tone": "info",
                          "text": f"A rebuy of {sym} after this loss is a review item for the adviser. India has no wash-sale rule and the guard does not "
                                  "bar it; nothing here says it is safe."})
        elif o["kind"] == "exemption":
            flags.append({"kind": "gain_harvest_review", "symbol": sym, "tone": "info",
                          "text": f"Selling {sym} to use the exemption and buying it back raises the same substance question as a loss harvest (GAAR); "
                                  "the benefit is counted against the Rs 3 crore tracker."})
    if float((settled.get("carry_forward") or {}).get("long_term") or 0) > 0 and lt_equity > 0:
        flags.append({"kind": "carry_forward_left", "symbol": None, "tone": "info",
                      "text": "A long-term loss is left after set-off. It carries forward only if the return is filed by its due day."})
    return flags


# ── One taxpayer ────────────────────────────────────────────────────────────────────────────────────────────────
async def engine(session: AsyncSession, firm: Firm, person: Person | None, entity: LegalEntity | None, *, inputs: dict, rows: list[dict],
                 realised: dict, settled: dict, tax: dict, opportunities: list[dict], today: date, year: dict) -> dict:
    """Everything beside the gains for one taxpayer: ledger, grandfathering, regime comparison, advance tax and flags."""
    losses = await ledger(session, firm.id, person_id=person.id if person else None, entity_id=entity.id if entity else None, today=today)
    gf = []
    for r in rows:
        old = [l for l in r["lots"] if l["acquired_on"] < tax_india.GRANDFATHER_BEFORE]
        if not old or r["tax_class"] not in tax_india.EQUITY_CLASSES:
            continue
        gf.append({"symbol": r["symbol"], "account": r["account"], "quantity": sum(l["quantity"] for l in old), "fmv_per_unit": r.get("fmv"),
                   "applied": r.get("fmv") is not None,
                   "cost_before": round(sum(l["raw_cost"] * l["quantity"] for l in old), 2),
                   "cost_after": round(sum(l["cost"] * l["quantity"] for l in old), 2),
                   "needs": None if r.get("fmv") is not None else "the exchange's price on 31 Jan 2018, from its published record"})
    tax_inputs = (person.profile or {}).get("tax") if person else ((entity.governance or {}).get("tax") if entity else None)
    tax_inputs = tax_inputs or {}
    age = None
    if person is not None and person.date_of_birth is not None:
        b = person.date_of_birth
        age = today.year - b.year - ((today.month, today.day) < (b.month, b.day))
    gains = sum(max(float(v), 0.0) for v in (settled.get("net") or {}).values())
    comparison = regime_comparison(income=tax_inputs.get("annual_income"), salary=tax_inputs.get("salary_income"),
                                   deductions=tax_inputs.get("old_regime_deductions"), age=age, gains=gains, is_individual=person is not None)
    needs, total = [], None
    if comparison["assessed"]:
        chosen = (inputs.get("regime") or "new")
        ordinary = (comparison["old"] if chosen == "old" else comparison["new"])["tax"]
        total = float(tax["amount"]) + ordinary
        if "slab_rate" in (tax.get("incomplete") or []):
            needs.append("the slab rate, for gains taxed at slab")
    else:
        needs = ["annual income, so the tax on ordinary income is known"]
        if float(tax["amount"]) > 0:
            total = None
    return {"asset_classes": [c["key"] for c in asset_classes(today)], "loss_ledger": losses,
            "loss_available": {k: round(sum(r["available"] for r in losses if r["kind"] == k), 2) for k in LOSS_KINDS},
            "ledger_in_use": bool((inputs.get("brought") or {}).get("from_ledger")),
            "grandfathering": gf, "regime": comparison, "advance_tax": advance_tax(total, today, needs=needs),
            "flags": interaction_flags(opportunities, realised, settled), "estimate": ESTIMATE}
