"""The Indian tax habits the L100 edition names and the engine did not lay out (section 8.1 to 8.3, B-299).

`tax_india.py` already reads each taxpayer's gains first-in first-out, and `_exemption_harvest` already finds the long-term equity lots that use
what is left of the Rs 1.25 lakh exemption. This adds the four habits as figures a qualified person can check, each with its source, and none
advised on:

  gain harvesting   per taxpayer: the exemption, what is used this year, what is left, the lots at the front of each first-in first-out queue
                    that would use it, and how far each lot's cost would step up. Only lots on file; a taxpayer with no equity lot in gain has none.
                    A sale and a rebuy raises the same substance question as a loss harvest, so `harvest_guard` is unchanged and nothing here says
                    a rebuy is safe.
  dividends         the dividends on file in the tax year (transactions of type dividend) by payer, the tax deducted where one payer paid above the
                    threshold, and the tax at the taxpayer's recorded slab rate; with no slab rate on file the tax is not assessed.
  clubbing          a gift to a spouse or to a minor child, recorded from its document: the income the gifted asset earned afterwards, from the
                    dividends on file in the receiver's accounts, shown as added back to the giver (a minor child's, after the exemption for each
                    child). A cash gift, or an asset with no income on file, is not assessed.
  gifts             a gift between people in the household, with the relationship the document or the adviser states, never inferred from the family
                    graph: from a specified relative it is not income in the receiver's hands; from anyone else it is, above the yearly threshold. A date
                    of birth on file that contradicts "minor child" or "adult child" on the gift's day refuses the record.

Every figure is an estimate for a qualified person to check, and the sections are the 1961 Act's as understood: the Income-tax Act 2025's renumbered
sections were not read. Not modelled: a gift to a son's wife, or for her benefit, a gift to a trust for the benefit of a spouse or a daughter-in-law, income
earned on the income, the clubbing of a capital gain on the gifted asset, the cost and holding period the receiver takes over from the giver, and the gift
tax in a market other than India's. Nothing here files, pays or trades.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets, tax_india
from app.aurea_core import tax_india_full as full
from app.aurea_core.valuation import latest_prices
from app.core.db import utcnow
from app.models.graph import Account, Household, Mandate, Person
from app.models.india_batch_a import TaxGift
from app.models.portfolio import Holding, Instrument, TaxLot, Transaction
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = full.RECORDERS
RELATIVES = {
    "spouse": "Spouse", "minor_child": "Child, under 18", "adult_child": "Child, 18 or over", "parent": "Parent", "grandparent": "Grandparent", "grandchild": "Grandchild",
    "sibling": "Brother or sister", "spouse_sibling": "Brother or sister of the spouse", "parent_sibling": "Brother or sister of a parent",
    "spouse_parent": "Parent of the spouse", "spouse_descendant": "Lineal descendant of the spouse", "spouse_of_relative": "Spouse of one of these relatives",
}
RELATIONSHIPS = {**RELATIVES, "other": "Not a specified relative"}
NOT_MODELLED = ["a gift to a son's wife, or for her benefit, and a gift to a trust for a spouse's or a daughter-in-law's benefit", "income earned on the income",
                "the clubbing of a capital gain on the gifted asset", "the cost and the holding period the receiver takes over from the giver"]
GIFT_FIGURES = ("in_gift_relative_exempt", "in_gift_other_threshold", "in_clubbing_rule", "in_clubbing_minor_exemption")
DIVIDEND_FIGURES = ("in_dividend_tds_threshold", "in_dividend_tds_rate", "in_cg_equity_lt_exemption")


class HabitError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    return full.applies(firm)


def _year(day: date) -> dict:
    return markets.tax_year(markets.MARKETS["IN"], day)


def _age_on(born: date, day: date) -> int:
    return day.year - born.year - ((day.month, day.day) < (born.month, born.day))


def _slab_tax_rate(person: Person | None) -> float | None:
    rate = (tax_india._tax_inputs(person, None)["slab_rate"]) if person else None
    return None if rate in (None, "") else round(float(rate) * (1 + law_figures.value("in_cess")), 6)


async def _people(session: AsyncSession, firm_id: uuid.UUID, household_id: uuid.UUID) -> list[Person]:
    return list((await session.execute(select(Person).where(Person.firm_id == firm_id, Person.household_id == household_id).order_by(Person.full_name))).scalars().all())


async def _person_accounts(session: AsyncSession, firm_id: uuid.UUID, person: Person) -> list[Account]:
    return list((await session.execute(select(Account).join(Mandate, Mandate.id == Account.mandate_id)
                                       .where(Mandate.firm_id == firm_id, Mandate.person_id == person.id))).scalars().all())


# ── Gain harvesting ───────────────────────────────────────────────────────────────────────────

async def _rows(session: AsyncSession, firm: Firm, accounts: list[Account]) -> list[dict]:
    """A taxpayer's holdings as the first-in first-out lot queues `tax_india` reads."""
    if not accounts:
        return []
    ids = [a.id for a in accounts]
    names = {a.id: a for a in accounts}
    holdings = (await session.execute(select(Holding).where(Holding.account_id.in_(ids)))).scalars().all()
    lots: dict = {}
    for lot in (await session.execute(select(TaxLot).where(TaxLot.holding_id.in_([h.id for h in holdings] or [uuid.uuid4()])))).scalars().all():
        lots.setdefault(lot.holding_id, []).append(lot)
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_({h.instrument_id for h in holdings} or [uuid.uuid4()])))).scalars().all()}
    prices = await latest_prices(session, firm.id)
    fmv = await full.fmv_map(session, firm.id)
    out = []
    for h in holdings:
        inst = instruments.get(h.instrument_id)
        qty = float(h.quantity or 0)
        px = float(prices[h.instrument_id].close) if h.instrument_id in prices else 0.0
        klass = tax_india.tax_class_of(inst)
        out.append({"symbol": inst.symbol if inst else "?", "name": inst.name if inst else "", "tax_class": klass, "account": names[h.account_id].name, "account_id": str(h.account_id),
                    "price": px, "lots": [{"quantity": float(l.quantity), "acquired_on": l.acquired_on, "raw_cost": float(l.cost_per_unit),
                                           "cost": tax_india.grandfathered_cost(klass, l.acquired_on, float(l.cost_per_unit), px, fmv.get(h.instrument_id))}
                                          for l in sorted(lots.get(h.id, []), key=lambda l: l.acquired_on)]})
    return out


async def gain_harvest(session: AsyncSession, firm: Firm, person: Person, today: date) -> dict:
    exemption = law_figures.value("in_cg_equity_lt_exemption")
    rate = law_figures.value("in_cg_equity_lt_rate")
    accounts = await _person_accounts(session, firm.id, person)
    year = _year(today)
    base = {"person_id": str(person.id), "person": person.full_name, "year": year["label"], "exemption": exemption, "rate": rate}
    if not accounts:
        return {**base, "state": "not_assessed", "said": "No account is held in this person's own name, so no lot is on file to harvest."}
    mandate = (await session.execute(select(Mandate).where(Mandate.firm_id == firm.id, Mandate.person_id == person.id))).scalars().first()
    position = await tax_india.position_for(session, firm, mandate, today=today)
    left = float(position["exempt_left"])
    candidates = tax_india._exemption_harvest(await _rows(session, firm, accounts), left, today, rate)
    gain = round(sum(c["gain"] for c in candidates), 2)
    return {**base, "state": "assessed", "used": round(exemption - left, 2), "left": round(left, 2), "days_left": year["days_left"], "tax_year_end": year["end"].isoformat(),
            "candidates": [{"symbol": c["symbol"], "instrument": c["instrument"], "account": c["account"], "quantity": c["quantity"], "gain": c["gain"],
                            "cost_step_up": c["gain"], "tax_saved_later": c["tax_saved"]} for c in candidates],
            "total_step_up": gain, "tax_saved_later": round(sum(c["tax_saved"] for c in candidates), 2),
            "said": ("Nothing to harvest: no long-term equity lot in gain at the front of its queue." if not candidates else
                     "Realising these gains uses the exemption and raises each lot's cost to the sale price. A sale and a rebuy raises the substance question of a loss harvest; "
                     "nothing here says it is safe."),
            "basis": "First-in first-out, lots on file only, at the latest real price. An estimate for a qualified person to check."}


# ── Dividends ─────────────────────────────────────────────────────────────────────────────────

async def dividends(session: AsyncSession, firm: Firm, person: Person, today: date) -> dict:
    year = _year(today)
    accounts = await _person_accounts(session, firm.id, person)
    threshold, rate = law_figures.value("in_dividend_tds_threshold"), law_figures.value("in_dividend_tds_rate")
    base = {"person_id": str(person.id), "person": person.full_name, "year": year["label"], "tds_threshold": threshold, "tds_rate": rate}
    if not accounts:
        return {**base, "state": "not_assessed", "said": "No account is held in this person's own name, so no dividend is on file."}
    txns = (await session.execute(select(Transaction).where(Transaction.account_id.in_([a.id for a in accounts]), Transaction.txn_type == "dividend",
                                                           Transaction.trade_date >= year["start"], Transaction.trade_date <= year["end"]))).scalars().all()
    if not txns:
        return {**base, "state": "none_on_file", "total": 0.0, "payers": [], "said": "No dividend is on file for this tax year. That does not mean none was paid."}
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_({t.instrument_id for t in txns if t.instrument_id} or [uuid.uuid4()])))).scalars().all()}
    by: dict = {}
    for t in txns:
        inst = instruments.get(t.instrument_id)
        slot = by.setdefault(t.instrument_id, {"payer": inst.name if inst else "Unknown payer", "symbol": inst.symbol if inst else None, "amount": 0.0})
        slot["amount"] += float(t.amount)
    payers = []
    for slot in sorted(by.values(), key=lambda s: -s["amount"]):
        over = slot["amount"] > threshold
        payers.append({**slot, "amount": round(slot["amount"], 2), "tds": round(slot["amount"] * rate, 2) if over else 0.0, "over_threshold": over})
    total = round(sum(p["amount"] for p in payers), 2)
    tds = round(sum(p["tds"] for p in payers), 2)
    slab = _slab_tax_rate(person)
    out = {**base, "state": "assessed", "total": total, "payers": payers, "tds": tds, "slab_rate": tax_india._tax_inputs(person, None)["slab_rate"]}
    if slab is None:
        out.update(tax=None, balance=None, needs="the person's marginal rate, recorded on their tax profile, so the tax at slab is known")
    else:
        tax = round(total * slab, 2)
        out.update(tax=tax, balance=round(tax - tds, 2), cess=law_figures.value("in_cess"))
    out["said"] = ("Dividends are taxed in the receiver's hands at slab. Tax is deducted by a payer only when what it paid the person in the year is above the threshold, "
                   "and what was deducted is credited against the tax. Surcharge is not applied here.")
    return out


# ── Gifts and clubbing ───────────────────────────────────────────────────────────────────────

async def _dividend_income(session: AsyncSession, receiver_accounts: list[Account], instrument_id, since: date, until: date) -> float:
    if not receiver_accounts or instrument_id is None:
        return 0.0
    rows = (await session.execute(select(Transaction).where(Transaction.account_id.in_([a.id for a in receiver_accounts]), Transaction.instrument_id == instrument_id,
                                                           Transaction.txn_type == "dividend", Transaction.trade_date >= since, Transaction.trade_date <= until))).scalars().all()
    return round(sum(float(t.amount) for t in rows), 2)


async def prepare_gift(session: AsyncSession, firm: Firm, role, *, giver_id, receiver_id, relationship: str | None, gifted_on=None, value=None, description: str | None = None,
                       document: str | None = None, consideration=None, instrument: str | None = None, quantity=None) -> dict:
    if not applies(firm):
        raise HabitError("A gift is recorded for the Indian tax reading, for an Indian firm.")
    if role not in RECORDERS:
        raise HabitError("An adviser, paraplanner, operations or the portfolio team records a gift, from its document.")
    today = firm_time.today(firm)
    try:
        gid, rid = (x if isinstance(x, uuid.UUID) else uuid.UUID(str(x)) for x in (giver_id, receiver_id))
    except ValueError:
        raise HabitError("Give the giver and the receiver from the client's page.")
    if gid == rid:
        raise HabitError("A gift is from one person to another.")
    giver, receiver = await session.get(Person, gid), await session.get(Person, rid)
    if giver is None or receiver is None or giver.firm_id != firm.id or receiver.firm_id != firm.id:
        raise HabitError("Person not found.")
    if not giver.household_id or giver.household_id != receiver.household_id:
        raise HabitError("Both people must be in the same household.")
    rel = (relationship or "").strip().lower().replace(" ", "_")
    if rel not in RELATIONSHIPS:
        raise HabitError(f"Say what the receiver is to the giver, as the document or the adviser states it: one of {', '.join(RELATIONSHIPS)}. It is never worked out from the family graph.")
    if len((document or "").strip()) < 5:
        raise HabitError("Say where the gift deed or the document of transfer is filed.")
    day = gifted_on if isinstance(gifted_on, date) else None
    if day is None:
        try:
            day = date.fromisoformat(str(gifted_on))
        except ValueError:
            raise HabitError("Give the day of the gift as YYYY-MM-DD.")
    if day > today:
        raise HabitError("A gift cannot be dated ahead.")
    try:
        worth = round(float(str(value).replace(",", "")), 2)
    except (TypeError, ValueError):
        raise HabitError("Give the value of the gift on the day, from its document.")
    if worth <= 0:
        raise HabitError("The value must be more than nothing.")
    paid = 0.0
    if consideration not in (None, ""):
        try:
            paid = round(float(str(consideration).replace(",", "")), 2)
        except ValueError:
            raise HabitError("The consideration is a number.")
    if paid >= worth:
        raise HabitError("What was paid is the value or more: that is a sale, not a gift, and the clubbing rule is for a transfer without adequate consideration.")
    if len((description or "").strip()) < 3:
        raise HabitError("Say what was given: the shares, the cash, the units.")
    if receiver.date_of_birth is not None:
        age = _age_on(receiver.date_of_birth, day)
        if rel == "minor_child" and age >= 18:
            raise HabitError(f"{receiver.full_name} was {age} on that day, from the date of birth on file, so not a minor child. If the date of birth is wrong, correct it first.")
        if rel == "adult_child" and age < 18:
            raise HabitError(f"{receiver.full_name} was {age} on that day, from the date of birth on file, so a minor child. If the date of birth is wrong, correct it first.")
    inst = None
    if instrument:
        ref = instrument.strip()
        try:
            inst = await session.get(Instrument, uuid.UUID(ref))
        except ValueError:
            inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == ref.upper()))).scalars().first()
        if inst is None or inst.firm_id != firm.id:
            raise HabitError(f"No instrument {ref!r} on file at this firm.")
    units = None
    if quantity not in (None, ""):
        try:
            units = float(str(quantity).replace(",", ""))
        except ValueError:
            raise HabitError("The quantity is a number.")
    dup = (await session.execute(select(TaxGift.id).where(TaxGift.firm_id == firm.id, TaxGift.giver_id == gid, TaxGift.receiver_id == rid, TaxGift.gifted_on == day,
                                                         TaxGift.value == worth, TaxGift.description == description.strip()))).first()
    if dup:
        raise HabitError("That gift is already recorded.")
    return {"giver": giver, "receiver": receiver, "relationship": rel, "gifted_on": day, "value": worth, "consideration": paid, "description": description.strip(),
            "instrument": inst, "quantity": units, "document": document.strip(), "today": today,
            "relative": rel in RELATIVES, "clubbed": rel in ("spouse", "minor_child")}


async def record_gift(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare_gift(session, firm, role, **terms)
    row = TaxGift(created_at=utcnow(), firm_id=firm.id, household_id=p["giver"].household_id, giver_id=p["giver"].id, receiver_id=p["receiver"].id, relationship=p["relationship"],
                  gifted_on=p["gifted_on"], value=p["value"], consideration=p["consideration"], description=p["description"], instrument_id=p["instrument"].id if p["instrument"] else None,
                  quantity=p["quantity"], source_document=p["document"], attributes={}, recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="tax_gift_recorded", actor=by, content={
        "giver_id": str(p["giver"].id), "receiver_id": str(p["receiver"].id), "relationship": row.relationship, "gifted_on": row.gifted_on.isoformat(),
        "value": float(row.value), "document": row.source_document})
    return await household_view(session, firm, p["giver"].household_id)


async def gifts(session: AsyncSession, firm: Firm, household_id: uuid.UUID, today: date) -> list[dict]:
    year = _year(today)
    rows = (await session.execute(select(TaxGift).where(TaxGift.firm_id == firm.id, TaxGift.household_id == household_id).order_by(TaxGift.gifted_on, TaxGift.created_at))).scalars().all()
    people = {p.id: p for p in await _people(session, firm.id, household_id)}
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_({r.instrument_id for r in rows if r.instrument_id} or [uuid.uuid4()])))).scalars().all()}
    threshold = law_figures.value("in_gift_other_threshold")
    exemption = law_figures.value("in_clubbing_minor_exemption")
    # What a person received from anyone not a specified relative in each year: the threshold is on the year's total.
    other_by: dict = {}
    for r in rows:
        if r.relationship == "other":
            k = (r.receiver_id, _year(r.gifted_on)["label"])
            other_by[k] = other_by.get(k, 0.0) + float(r.value) - float(r.consideration)
    out = []
    income_by_receiver: dict = {}
    for r in rows:
        giver, receiver = people.get(r.giver_id), people.get(r.receiver_id)
        view = {"id": str(r.id), "giver": giver.full_name if giver else None, "receiver": receiver.full_name if receiver else None,
                "relationship": r.relationship, "relationship_label": RELATIONSHIPS.get(r.relationship, r.relationship), "gifted_on": r.gifted_on.isoformat(),
                "tax_year": _year(r.gifted_on)["label"], "value": float(r.value), "description": r.description, "document": r.source_document,
                "symbol": instruments[r.instrument_id].symbol if r.instrument_id in instruments else None}
        if r.relationship in RELATIVES:
            view["receiver_tax"] = {"state": "exempt", "said": "From a specified relative: not income in the receiver's hands."}
        else:
            total = other_by.get((r.receiver_id, view["tax_year"]), 0.0)
            over = total > threshold
            view["receiver_tax"] = {"state": "taxable" if over else "under_threshold", "year_total": round(total, 2), "threshold": threshold,
                                    "said": ("From someone who is not a specified relative, and the year's total is above the threshold: income from other sources in the receiver's hands."
                                             if over else "From someone who is not a specified relative; the year's total is not above the threshold.")}
        if r.relationship in ("spouse", "minor_child"):
            since, until = r.gifted_on, min(_year(today)["end"], today) if _year(r.gifted_on)["label"] == _year(today)["label"] else _year(r.gifted_on)["end"]
            income = None
            if r.instrument_id and receiver and r.quantity:
                accounts = await _person_accounts(session, firm.id, receiver)
                total = await _dividend_income(session, accounts, r.instrument_id, since, until)
                held = sum(float(h.quantity) for h in (await session.execute(select(Holding).where(
                    Holding.account_id.in_([a.id for a in accounts] or [uuid.uuid4()]), Holding.instrument_id == r.instrument_id))).scalars().all())
                # The dividends on the receiver's whole holding of it, by the share of the units held that were the gift.
                income = round(total * min(1.0, float(r.quantity) / held), 2) if held > 0 else 0.0
            if income is None or income <= 0:
                view["clubbing"] = {"state": "not_assessed", "said": "No income from the gifted asset is on file for the year, so nothing is added back. A cash gift's interest is not on file, and that does not mean there was none."}
            else:
                add = income
                if r.relationship == "minor_child":
                    left = exemption - income_by_receiver.get((r.receiver_id, view["tax_year"]), 0.0)
                    add = max(income - max(left, 0.0), 0.0)
                    income_by_receiver[(r.receiver_id, view["tax_year"])] = income_by_receiver.get((r.receiver_id, view["tax_year"]), 0.0) + income
                rate = _slab_tax_rate(giver)
                view["clubbing"] = {"state": "added_back", "income": income, "added_to_giver": round(add, 2), "exemption": exemption if r.relationship == "minor_child" else None,
                                    "to": "the giver" if r.relationship == "spouse" else "the parent whose income is higher (shown against the giver; which parent that is is not assessed)",
                                    "tax_effect": round(add * rate, 2) if rate is not None else None,
                                    "needs": None if rate is not None else "the giver's marginal rate, so the tax effect is known",
                                    "said": "Income the gifted asset earned after the gift, added to the giver's income: the dividends on the receiver's holding, by the share of the units held that were the gift."}
        out.append(view)
    return out


# ── Reads ─────────────────────────────────────────────────────────────────────────────────────

async def for_firm(session: AsyncSession, firm: Firm, *, limit: int = 40) -> dict:
    """Each taxpayer's gain-harvesting room across the book, for the Licences page (B-312): the same per-person reading the conversation gives,
    laid out as rows. Only people with an account are listed; the room is per taxpayer, never netted across a family."""
    from app.aurea_core.graph import list_households

    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "gain_harvest": []}
    rows: list[dict] = []
    for h in (await list_households(session, firm.id))[:limit]:
        for p in await _people(session, firm.id, uuid.UUID(h["id"])):
            if await _person_accounts(session, firm.id, p):
                rows.append({**await gain_harvest(session, firm, p, today), "household": h["name"]})
    return {"applies": True, "as_of": today.isoformat(), "year": _year(today)["label"], "gain_harvest": rows, "not_modelled": NOT_MODELLED, "estimate": full.ESTIMATE,
            "note": "Laid out for a qualified person to check. Nothing here advises a sale or a rebuy."}


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat()}
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm.id:
        raise HabitError("Household not found.")
    people = await _people(session, firm.id, household_id)
    return {"applies": True, "as_of": today.isoformat(), "household_id": str(household_id), "household": household.name, "year": _year(today)["label"],
            "gain_harvest": [await gain_harvest(session, firm, p, today) for p in people if await _person_accounts(session, firm.id, p)],
            "dividends": [await dividends(session, firm, p, today) for p in people if await _person_accounts(session, firm.id, p)],
            "gifts": await gifts(session, firm, household_id, today),
            "figures": law_figures.cited([*DIVIDEND_FIGURES, *GIFT_FIGURES], today), "not_modelled": NOT_MODELLED, "estimate": full.ESTIMATE,
            "note": "Laid out for a qualified person to check. Nothing here advises a sale, a gift or a rebuy."}
