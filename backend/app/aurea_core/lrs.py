"""Overseas investing under the Liberalised Remittance Scheme (L100 India edition section 7, B-293).

A resident individual may remit up to USD 250,000 abroad in a financial year (April to March), and tax is collected at source on what a
person remits above a threshold in that year. Neither is knowable from the book: a remittance happens at a bank. So a person records each one
from the bank's advice (the amount in dollars the advice counts, the day, its purpose, the bank, and the rupees debited and the tax collected
if the advice shows them), added and never edited; a correction is a reversing entry. Nothing here estimates a remittance, and a person with
none on file is "not assessed", which is not "has remitted nothing".

What it reads is laid out as figures: the limit, what is remitted, the headroom, the rupees counted toward the tax threshold, the tax
collected so far. A direct order for an overseas product (an instrument that does not trade in rupees, or one tagged `lrs`) reads the headroom
in `order_guard`: a buy that would take the year past the limit is refused, naming the figures; one inside it goes ahead with the tax at
source laid out as an estimate. With no remittance on file it goes ahead with a warning, because a fact not on file is not a no. A sale is
never stopped.

The tax estimate is the rate for investment, gifts and other purposes on the part of the year's remittances above the threshold, in rupees. An
order's rupees come from the rate on the person's latest recorded advice that shows both amounts: with none, the estimate is not assessed. The
threshold counts every purpose together. Not modelled, and named: the purposes' own rates applied to an order (an order is an investment), a
tax credit claimed back, an entity's overseas direct investment, GIFT City IFSC funds, overseas mutual fund schemes' industry limits (an Indian
fund bought in rupees is not a remittance), and the Budget of February 2026's changes to the rate (the figures are in `law_figures`, unreviewed).
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Person
from app.models.india_batch_a import LrsRemittance
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
PURPOSES = {"investment": "Investment abroad", "education": "Education", "education_loan": "Education, financed by a loan", "medical": "Medical treatment",
            "travel": "Travel", "gift": "Gift", "family_maintenance": "Family maintenance", "other": "Other"}
FIGURES = ("in_lrs_limit_usd", "in_lrs_tcs_threshold", "in_lrs_tcs_rate_other")
NOT_MODELLED = ["GIFT City IFSC funds", "overseas mutual fund schemes' industry-level limits (an Indian fund bought in rupees is not a remittance)",
                "an entity's overseas direct investment", "the tax collected claimed back in the return", "the Budget of February 2026's changes to the rate"]
ESTIMATE = "An estimate for a qualified person to check: the rate is assumed from the Finance Act 2025 and not reviewed by counsel."


class LrsError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def is_overseas(instrument, firm: Firm | None = None) -> bool:
    """An instrument that trades outside rupees, or one a person tagged `lrs`. An Indian fund bought in rupees is not a remittance."""
    if instrument is None:
        return False
    tags = instrument.values_tags or {}
    base = ((firm.base_currency if firm else None) or "INR").upper()
    return bool(tags.get("lrs")) or (instrument.currency or base).upper() != base


def _year(day: date) -> dict:
    return markets.tax_year(markets.MARKETS["IN"], day)


def _day(value, label: str) -> date:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        raise LrsError(f"Give {label} as YYYY-MM-DD.")


def _num(value, label: str, *, required: bool) -> float | None:
    if value in (None, ""):
        if required:
            raise LrsError(f"Give {label}.")
        return None
    try:
        out = round(float(str(value).replace(",", "")), 2)
    except ValueError:
        raise LrsError(f"{label.capitalize()} must be a number.")
    if out < 0 or (required and out == 0):
        raise LrsError(f"{label.capitalize()} must be more than nothing." if required else f"{label.capitalize()} cannot be negative.")
    return out


async def effective(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID, year: dict | None = None) -> list[LrsRemittance]:
    """The person's remittances that stand: a reversed one and the reversal itself are both left out. Within a financial year when one is given."""
    rows = (await session.execute(select(LrsRemittance).where(LrsRemittance.firm_id == firm_id, LrsRemittance.person_id == person_id)
                                  .order_by(LrsRemittance.remitted_on, LrsRemittance.created_at, LrsRemittance.id))).scalars().all()
    reversed_ids = {r.reverses_id for r in rows if r.reverses_id}
    live = [r for r in rows if r.reverses_id is None and r.id not in reversed_ids]
    if year:
        live = [r for r in live if year["start"] <= r.remitted_on <= year["end"]]
    return live


async def has_record(session: AsyncSession, firm_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    """Whether anything is on file for the person, a reversed remittance included: a person whose only entries were reversed is assessed at nothing remitted."""
    return (await session.execute(select(LrsRemittance.id).where(LrsRemittance.firm_id == firm_id, LrsRemittance.person_id == person_id).limit(1))).first() is not None


def rate_on_file(rows: list[LrsRemittance]) -> tuple[float, str] | None:
    """Rupees per dollar on the latest advice that shows both, with the day: the only rate this module uses, and never a market rate."""
    for r in sorted(rows, key=lambda r: r.remitted_on, reverse=True):
        if r.amount_inr and float(r.amount_usd) > 0:
            return float(r.amount_inr) / float(r.amount_usd), r.remitted_on.isoformat()
    return None


def position(rows: list[LrsRemittance], today: date) -> dict:
    """The year's figures from the remittances that stand in it."""
    limit = law_figures.value("in_lrs_limit_usd")
    threshold = law_figures.value("in_lrs_tcs_threshold")
    usd = round(sum(float(r.amount_usd) for r in rows), 2)
    with_inr = [r for r in rows if r.amount_inr is not None]
    inr = round(sum(float(r.amount_inr) for r in with_inr), 2)
    collected = round(sum(float(r.tcs or 0) for r in rows), 2)
    unknown = len(rows) - len(with_inr)
    return {"limit_usd": limit, "remitted_usd": usd, "headroom_usd": round(limit - usd, 2), "over_limit": usd > limit,
            "remitted_inr": inr, "inr_unknown": unknown, "tcs_threshold": threshold,
            "before_tcs_inr": round(max(threshold - inr, 0.0), 2) if not unknown else None,
            "tcs_collected": collected, "count": len(rows)}


def view_row(r: LrsRemittance) -> dict:
    return {"id": str(r.id), "remitted_on": r.remitted_on.isoformat(), "amount_usd": float(r.amount_usd),
            "amount_inr": float(r.amount_inr) if r.amount_inr is not None else None, "purpose": r.purpose, "purpose_label": PURPOSES.get(r.purpose, r.purpose),
            "bank": r.bank, "tcs": float(r.tcs) if r.tcs is not None else None, "document": r.source_document}


async def person_view(session: AsyncSession, firm: Firm, person: Person, today: date | None = None) -> dict:
    today = today or firm_time.today(firm)
    year = _year(today)
    rows = await effective(session, firm.id, person.id, year)
    ever = await effective(session, firm.id, person.id)
    out = {"person_id": str(person.id), "person": person.full_name, "year": year["label"], "year_start": year["start"].isoformat(), "year_end": year["end"].isoformat()}
    if not await has_record(session, firm.id, person.id):
        return {**out, "state": "not_assessed", "said": "No remittance is on file for this person. That does not mean none was made: record each one from the bank's advice.",
                "remittances": [], "limit_usd": law_figures.value("in_lrs_limit_usd")}
    return {**out, "state": "assessed", **position(rows, today), "remittances": [view_row(r) for r in rows],
            "rate": (lambda r: {"inr_per_usd": round(r[0], 4), "from": r[1]} if r else None)(rate_on_file(ever))}


async def overview(session: AsyncSession, firm: Firm) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "people": []}
    ids = {r for (r,) in (await session.execute(select(LrsRemittance.person_id).where(LrsRemittance.firm_id == firm.id).distinct())).all()}
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.id.in_(list(ids) or [uuid.uuid4()])).order_by(Person.full_name))).scalars().all()
    rows = [await person_view(session, firm, p, today) for p in people]
    return {"applies": True, "as_of": today.isoformat(), "year": _year(today)["label"], "people": rows,
            "figures": law_figures.cited(list(FIGURES), today), "not_modelled": NOT_MODELLED, "estimate": ESTIMATE,
            "note": "A person with no remittance on file is not assessed: it does not mean they have remitted nothing."}


async def household_view(session: AsyncSession, firm: Firm, household_id: uuid.UUID) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "people": []}
    people = (await session.execute(select(Person).where(Person.firm_id == firm.id, Person.household_id == household_id).order_by(Person.full_name))).scalars().all()
    return {"applies": True, "as_of": today.isoformat(), "year": _year(today)["label"],
            "people": [await person_view(session, firm, p, today) for p in people],
            "figures": law_figures.cited(list(FIGURES), today), "not_modelled": NOT_MODELLED, "estimate": ESTIMATE}


async def prepare(session: AsyncSession, firm: Firm, role, *, person_id, remitted_on=None, amount_usd=None, amount_inr=None, purpose: str | None = None,
                  bank: str | None = None, tcs=None, document: str | None = None, reverses_id=None, reason: str | None = None) -> dict:
    if not applies(firm):
        raise LrsError("Remittances under the Scheme are recorded for an Indian firm.")
    if role not in RECORDERS:
        raise LrsError("An adviser, paraplanner or operations records a remittance, from the bank's advice.")
    try:
        pid = person_id if isinstance(person_id, uuid.UUID) else uuid.UUID(str(person_id))
    except ValueError:
        raise LrsError("Give the person from the client's page.")
    person = await session.get(Person, pid)
    if person is None or person.firm_id != firm.id:
        raise LrsError("Person not found.")
    today = firm_time.today(firm)
    if len((document or "").strip()) < 5:
        raise LrsError("Say where the bank's advice is filed.")
    if reverses_id not in (None, ""):
        try:
            rid = reverses_id if isinstance(reverses_id, uuid.UUID) else uuid.UUID(str(reverses_id))
        except ValueError:
            raise LrsError("Give the remittance to reverse from the list.")
        before = await session.get(LrsRemittance, rid)
        if before is None or before.firm_id != firm.id or before.person_id != pid or before.reverses_id is not None:
            raise LrsError("That remittance is not on file for this person.")
        if before not in await effective(session, firm.id, pid):
            raise LrsError("That remittance is already reversed.")
        if len((reason or "").strip()) < 5:
            raise LrsError("Say why it is reversed: the bank's returned-funds advice, a mis-keyed amount.")
        return {"person": person, "reverses": before, "remitted_on": before.remitted_on, "amount_usd": float(before.amount_usd),
                "amount_inr": float(before.amount_inr) if before.amount_inr is not None else None, "purpose": before.purpose, "bank": before.bank,
                "tcs": float(before.tcs) if before.tcs is not None else None, "document": document.strip(), "reason": reason.strip(), "today": today}
    day = _day(remitted_on, "the day the remittance was made")
    if day > today:
        raise LrsError("A remittance cannot be dated ahead.")
    usd = _num(amount_usd, "the amount in dollars the bank's advice counts", required=True)
    inr = _num(amount_inr, "the rupees debited", required=False)
    tax = _num(tcs, "the tax collected at source", required=False)
    kind = (purpose or "").strip().lower().replace(" ", "_")
    if kind not in PURPOSES:
        raise LrsError(f"The purpose is one of: {', '.join(PURPOSES)}.")
    if len((bank or "").strip()) < 2:
        raise LrsError("Name the bank that remitted it.")
    return {"person": person, "reverses": None, "remitted_on": day, "amount_usd": usd, "amount_inr": inr, "purpose": kind, "bank": bank.strip(), "tcs": tax,
            "document": document.strip(), "reason": (reason or "").strip() or None, "today": today,
            "after": position([*await effective(session, firm.id, pid, _year(day)), _preview(pid, day, usd, inr, tax)], today) if _year(day)["label"] == _year(today)["label"] else None}


def _preview(pid, day, usd, inr, tax) -> LrsRemittance:
    return LrsRemittance(person_id=pid, remitted_on=day, amount_usd=usd, amount_inr=inr, tcs=tax, purpose="other", bank="", source_document="")


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = LrsRemittance(created_at=utcnow(), firm_id=firm.id, person_id=p["person"].id, remitted_on=p["remitted_on"], amount_usd=p["amount_usd"],
                        amount_inr=p["amount_inr"], purpose=p["purpose"], bank=p["bank"], tcs=p["tcs"], reverses_id=p["reverses"].id if p["reverses"] else None,
                        source_document=p["document"], reason=p["reason"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="lrs_remittance_recorded", actor=by, content={
        "person_id": str(p["person"].id), "remitted_on": row.remitted_on.isoformat(), "amount_usd": float(row.amount_usd), "purpose": row.purpose,
        "reverses": str(row.reverses_id) if row.reverses_id else None, "document": row.source_document})
    return await person_view(session, firm, p["person"])


# ── A direct order ────────────────────────────────────────────────────────────────────────────

async def order_review(session: AsyncSession, firm: Firm, mandate, filling: list[dict], instruments: dict) -> tuple[list[str], list[str]]:
    """(refused, notes) for the buys of an overseas product in a set. A buy that takes the year past the limit is refused with the figures; one
    inside it is named with the tax at source as an estimate; none on file is a warning. Sales are never stopped."""
    if not applies(firm):
        return [], []
    buys = [o for o in filling if o["side"] == "buy" and is_overseas(instruments.get(o["symbol"]), firm)]
    if not buys:
        return [], []
    today = firm_time.today(firm)
    say = markets.money_for(firm)
    if mandate.person_id is None:
        return [], [f"{', '.join(o['symbol'] for o in buys)} trades overseas. This mandate is not an individual's, and an entity's overseas investment is not modelled: "
                    "nothing here reads the Scheme's limit for it."]
    person = await session.get(Person, mandate.person_id)
    year = _year(today)
    ever = await effective(session, firm.id, mandate.person_id)
    names = ", ".join(o["symbol"] for o in buys)
    if not await has_record(session, firm.id, mandate.person_id):
        return [], [f"{names} trades overseas, so it takes a remittance under the Scheme (limit USD {law_figures.value('in_lrs_limit_usd'):,.0f} a person each financial year). "
                    f"No remittance is on file for {person.full_name if person else 'this person'}, so the headroom is not assessed."]
    rows = await effective(session, firm.id, mandate.person_id, year)
    pos = position(rows, today)
    rate = rate_on_file(ever)
    usd_total, inr_total, unknown = 0.0, 0.0, []
    for o in buys:
        inst = instruments[o["symbol"]]
        if (inst.currency or "").upper() == "USD":
            usd_total += float(o["est_value"])
            if rate:
                inr_total += float(o["est_value"]) * rate[0]
        else:
            unknown.append(o["symbol"])
    refused, notes = [], []
    if unknown:
        notes.append(f"{', '.join(unknown)} is not priced in dollars, so what it counts against the limit is not assessed.")
    if usd_total > 0:
        left = pos["headroom_usd"]
        if usd_total > left:
            refused.append(
                f"{names} takes about USD {usd_total:,.0f} of remittance, and {person.full_name if person else 'this person'} has remitted USD {pos['remitted_usd']:,.0f} of the "
                f"USD {pos['limit_usd']:,.0f} limit for {year['label']}, so USD {max(left, 0):,.0f} is left: short by USD {usd_total - max(left, 0):,.0f}.")
        else:
            notes.append(f"Overseas: about USD {usd_total:,.0f} of remittance; USD {left - usd_total:,.0f} would be left of the USD {pos['limit_usd']:,.0f} limit for {year['label']}.")
            if rate and not pos["inr_unknown"]:
                above = max(pos["remitted_inr"] + inr_total - max(pos["tcs_threshold"], pos["remitted_inr"]), 0.0)
                tax = round(above * law_figures.value("in_lrs_tcs_rate_other"), 2)
                notes.append(f"Tax at source, estimated: {say(tax)} on {say(above)} above the {say(pos['tcs_threshold'])} threshold, at {law_figures.value('in_lrs_tcs_rate_other'):.0%}, "
                             f"using Rs {rate[0]:,.2f} to the dollar from the advice of {rate[1]}. {ESTIMATE}")
            else:
                notes.append("Tax at source is not assessed: it needs the rupees on every recorded advice and a rate from one of them.")
    return refused, notes
