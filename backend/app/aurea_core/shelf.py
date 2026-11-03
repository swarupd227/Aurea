"""The product shelf: what may be bought for a client, and who has said so (L100 §5.7, §7).

A research team's decision on a product is a person's, from their due diligence, added and never edited. Closed
stops every purchase at once, and a product with a minimum investor class is bought only for someone certified
at that class, from a document. Neither is ever worked out.

"Only what is approved" is a separate switch, because a shelf is built one product at a time and turning it on
with the first decision would stop nearly every purchase in the firm. Until a person turns it on, a product with
no decision may be bought, and the shelf says so. No decision on file does not mean a product was never reviewed.

The rebalancer reads `barred` as what it may not buy and a direct order reads it in `order_guard`, so neither can
buy what the other may not. Sales are never stopped: a closed product is a reason to leave it, and what the
monitor raises about one already held is for a person. Share classes of one fund are compared within a family,
because the cheapest available class is what regulators look for, and a dearer one is reported and not changed.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.models.graph import Account, Mandate, Person
from app.models.portfolio import Holding, Instrument
from app.models.shelf import INVESTOR_CLASSES, STATUSES, InvestorCertification, ProductDecision
from app.models.tenant import Firm
from app.provenance import ledger


class ShelfError(ValueError):
    """This cannot be recorded, with the reason."""


def enforced(firm: Firm) -> bool:
    return bool((firm.settings or {}).get("approved_products_only"))


async def decisions(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, ProductDecision]:
    """The decision in force on each product: the latest of each."""
    rows = (await session.execute(select(ProductDecision).where(ProductDecision.firm_id == firm_id)
                                  .order_by(ProductDecision.reviewed_on, ProductDecision.created_at))).scalars().all()
    return {r.instrument_id: r for r in rows}


async def diversified(session: AsyncSession, firm_id: uuid.UUID) -> set[uuid.UUID]:
    """The products the research team says are diversified funds. An instrument does not say what it is."""
    return {i for i, row in (await decisions(session, firm_id)).items() if row.diversified}


async def standing_of(session: AsyncSession, person_id: uuid.UUID, *, today: date) -> InvestorCertification | None:
    """The person's latest certification, if it has not run out."""
    row = (await session.execute(select(InvestorCertification).where(InvestorCertification.person_id == person_id)
                                 .order_by(InvestorCertification.as_of.desc(), InvestorCertification.created_at.desc()).limit(1))).scalars().first()
    return row if row is not None and (row.expires_on is None or row.expires_on >= today) else None


async def barred(session: AsyncSession, firm: Firm, mandate: Mandate) -> dict[uuid.UUID, str]:
    """Each product this mandate may not buy, and why. Empty until someone decides something."""
    today = firm_time.today(firm)
    decided = await decisions(session, firm.id)
    out: dict[uuid.UUID, str] = {}
    standing = await standing_of(session, mandate.person_id, today=today) if mandate.person_id and decided else None
    rank = INVESTOR_CLASSES.index(standing.investor_class) if standing else -1
    for instrument_id, row in decided.items():
        if row.status == "closed":
            out[instrument_id] = "closed to new money by the research team"
        elif row.minimum_class and rank < INVESTOR_CLASSES.index(row.minimum_class):
            out[instrument_id] = f"only for a {row.minimum_class.replace('_', ' ')} investor, and this client has no such certification on file"
    if enforced(firm):
        rows = (await session.execute(select(Instrument.id).where(Instrument.firm_id == firm.id))).scalars().all()
        for instrument_id in rows:
            if instrument_id not in decided:
                out[instrument_id] = "not on the approved list"
    for instrument_id, reason in (await plan_bars(session, firm, mandate)).items():
        out.setdefault(instrument_id, reason)
    return out


async def plan_bars(session: AsyncSession, firm: Firm, mandate: Mandate) -> dict[uuid.UUID, str]:
    """A mutual fund plan the mandate's licence does not buy: a regular plan under an adviser or a portfolio
    manager. Nothing for a firm that holds no licence, since only a licence says which plan is right."""
    from app.aurea_core import licences

    licence = licences.track_for(firm, mandate)
    if licence is None:
        return {}
    out: dict[uuid.UUID, str] = {}
    for inst in (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id))).scalars().all():
        plan = (inst.values_tags or {}).get("fund_plan")
        if plan and plan not in licence.fund_plans:
            out[inst.id] = (f"a {plan} plan, and under the {licence.name} licence only "
                            f"{' or '.join(licence.fund_plans)} plans are bought")
    return out


async def order_refusals(session: AsyncSession, firm: Firm, mandate: Mandate, orders: list[dict]) -> tuple[list[str], list[str]]:
    """What stops these buys, and the buys on a product under watch, which go ahead and are named."""
    stops = await barred(session, firm, mandate)
    decided = await decisions(session, firm.id)
    refused, watched = [], []
    for o in orders:
        if o["side"] != "buy":
            continue
        instrument_id = uuid.UUID(str(o["instrument_id"]))
        if instrument_id in stops:
            refused.append(f"{o['symbol']} is {stops[instrument_id]}.")
        elif instrument_id in decided and decided[instrument_id].status == "watch":
            watched.append(o["symbol"])
    return refused, watched


def _day(value: date | None, today: date) -> date:
    return value or today


async def prepare_decision(session: AsyncSession, firm_id: uuid.UUID, *, symbol: str | None, status: str | None, rationale: str | None,
                           document: str | None, reviewed_on: date | None = None, next_review_on: date | None = None, minimum_class: str | None = None,
                           family: str | None = None, share_class: str | None = None, expense_ratio: float | None = None,
                           is_diversified: bool | None = None) -> dict:
    """Everything recording would refuse, refused without writing, and who it reaches."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm_id, Instrument.symbol == (symbol or "").strip()))).scalar_one_or_none()
    if inst is None:
        raise ShelfError(f"No instrument with the symbol {symbol}.")
    if status not in STATUSES:
        raise ShelfError(f"Status must be one of {', '.join(STATUSES)}.")
    why, named = (rationale or "").strip(), (document or "").strip()
    if len(why) < 5:
        raise ShelfError("Give the research team's reason.")
    if len(named) < 5:
        raise ShelfError("Say which due-diligence paper this comes from.")
    day = _day(reviewed_on, today)
    if day > today:
        raise ShelfError("A review cannot be dated in the future.")
    if next_review_on is not None and next_review_on <= day:
        raise ShelfError("The next review must come after this one.")
    if minimum_class is not None and minimum_class not in INVESTOR_CLASSES[1:]:
        raise ShelfError(f"The minimum class must be one of {', '.join(INVESTOR_CLASSES[1:])}.")
    if expense_ratio is not None and not 0 <= expense_ratio < 0.2:
        raise ShelfError("The expense ratio must be between 0% and 20%.")
    was = (await decisions(session, firm_id)).get(inst.id)
    held = (await session.execute(select(func.count(Holding.id), func.sum(Holding.market_value)).where(
        Holding.instrument_id == inst.id, Holding.quantity > 0))).one()
    return {"instrument": inst, "status": status, "rationale": why, "document": named, "day": day, "next": next_review_on, "minimum": minimum_class,
            "family": (family or "").strip() or None, "share_class": (share_class or "").strip() or None, "expense": expense_ratio, "was": was,
            # What it is does not change with what the team thinks of it, so a later decision carries it over.
            "diversified": bool(was.diversified) if was and is_diversified is None else bool(is_diversified),
            "holdings": int(held[0] or 0), "value_held": float(held[1] or 0)}


async def record_decision(session: AsyncSession, firm_id: uuid.UUID, *, by: str, role: str, **terms) -> ProductDecision:
    ready = await prepare_decision(session, firm_id, **terms)
    row = ProductDecision(firm_id=firm_id, instrument_id=ready["instrument"].id, status=ready["status"], rationale=ready["rationale"],
                          document=ready["document"], reviewed_on=ready["day"], next_review_on=ready["next"], minimum_class=ready["minimum"],
                          family=ready["family"], share_class=ready["share_class"], expense_ratio=ready["expense"], diversified=ready["diversified"], decided_by=by, decided_by_role=str(role))
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="product_decision_recorded", actor=by, content={
        "symbol": ready["instrument"].symbol, "status": row.status, "rationale": row.rationale, "document": row.document,
        "minimum_class": row.minimum_class, "reviewed_on": row.reviewed_on.isoformat()})
    return row


async def prepare_certification(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, investor_class: str | None, as_of: date | None,
                                document: str | None, expires_on: date | None = None) -> dict:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id:
        raise ShelfError("Person not found.")
    if investor_class not in INVESTOR_CLASSES:
        raise ShelfError(f"The class must be one of {', '.join(INVESTOR_CLASSES)}.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    day = _day(as_of, today)
    if day > today:
        raise ShelfError("A certification cannot be dated in the future.")
    if expires_on is not None and expires_on <= day:
        raise ShelfError("It cannot run out before it starts.")
    named = (document or "").strip()
    if len(named) < 5:
        raise ShelfError("Say which letter or form shows this.")
    return {"person": person, "investor_class": investor_class, "day": day, "expires": expires_on, "document": named,
            "was": await standing_of(session, person_id, today=today)}


async def record_certification(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, investor_class: str | None, as_of: date | None,
                               document: str | None, expires_on: date | None = None) -> InvestorCertification:
    ready = await prepare_certification(session, person_id, firm_id=firm_id, investor_class=investor_class, as_of=as_of, document=document, expires_on=expires_on)
    row = InvestorCertification(firm_id=firm_id, person_id=person_id, investor_class=ready["investor_class"], as_of=ready["day"],
                                expires_on=ready["expires"], document=ready["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="investor_certification_recorded", actor=by, content={
        "person_id": str(person_id), "investor_class": row.investor_class, "document": row.document})
    return row


async def _held(session: AsyncSession, firm_id: uuid.UUID) -> dict[uuid.UUID, tuple[int, float]]:
    rows = (await session.execute(select(Holding.instrument_id, func.count(func.distinct(Account.mandate_id)), func.sum(Holding.market_value))
                                  .join(Account, Account.id == Holding.account_id)
                                  .where(Holding.firm_id == firm_id, Holding.quantity > 0).group_by(Holding.instrument_id))).all()
    return {i: (int(n), float(v or 0)) for i, n, v in rows}


async def prepare_enforcement(session: AsyncSession, firm_id: uuid.UUID, *, on: bool) -> dict:
    firm = await session.get(Firm, firm_id)
    if enforced(firm) == on:
        raise ShelfError(f"Approved products only is already {'on' if on else 'off'}.")
    decided = await decisions(session, firm_id)
    held = await _held(session, firm_id)
    missing = [i for i in held if i not in decided]
    symbols = (await session.execute(select(Instrument.symbol).where(Instrument.id.in_(missing)).order_by(Instrument.symbol))).scalars().all() if missing else []
    return {"firm": firm, "on": on, "approved": sum(1 for d in decided.values() if d.status != "closed"), "unreviewed_held": symbols,
            "value_unreviewed": round(sum(held[i][1] for i in missing), 2)}


async def set_enforcement(session: AsyncSession, firm_id: uuid.UUID, *, on: bool, by: str) -> dict:
    ready = await prepare_enforcement(session, firm_id, on=on)
    firm = ready["firm"]
    firm.settings = {**(firm.settings or {}), "approved_products_only": on}
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="approved_products_only_set", actor=by, content={"on": on})
    return await for_firm(session, firm_id)


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every product held or decided on, what the book holds of it, and what is out of date."""
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    decided = await decisions(session, firm_id)
    held = await _held(session, firm_id)
    ids = set(decided) | set(held)
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_(list(ids))))).scalars().all()} if ids else {}
    cheapest: dict[str, float] = defaultdict(lambda: 1.0)
    for row in decided.values():
        if row.family and row.expense_ratio is not None and row.status == "approved":
            cheapest[row.family] = min(cheapest[row.family], float(row.expense_ratio))
    rows = []
    for instrument_id, inst in instruments.items():
        row = decided.get(instrument_id)
        mandates, value = held.get(instrument_id, (0, 0.0))
        expense = float(row.expense_ratio) if row and row.expense_ratio is not None else None
        rows.append({
            "symbol": inst.symbol, "name": inst.name, "asset_class": str(inst.asset_class), "market_type": str(inst.market_type),
            "status": row.status if row else "not_reviewed", "mandates": mandates, "value_held": round(value, 2),
            "reviewed_on": row.reviewed_on.isoformat() if row else None, "next_review_on": row.next_review_on.isoformat() if row and row.next_review_on else None,
            "review_overdue": bool(row and row.next_review_on and row.next_review_on < today), "minimum_class": row.minimum_class if row else None,
            "family": row.family if row else None, "share_class": row.share_class if row else None, "expense_ratio": expense, "diversified": bool(row and row.diversified),
            "dearer_class": bool(row and row.family and expense is not None and expense > cheapest[row.family]),
            "rationale": row.rationale if row else None, "document": row.document if row else None, "decided_by": row.decided_by if row else None,
        })
    order = {"closed": 0, "watch": 1, "not_reviewed": 2, "approved": 3}
    rows.sort(key=lambda r: (order[r["status"]], -r["value_held"], r["symbol"]))
    count = lambda s: sum(1 for r in rows if r["status"] == s)  # noqa: E731
    return {"products": rows, "approved_products_only": enforced(firm), "as_of": today.isoformat(), "summary": {
        "approved": count("approved"), "watch": count("watch"), "closed": count("closed"), "not_reviewed": count("not_reviewed"),
        "held_while_closed": sum(1 for r in rows if r["status"] == "closed" and r["mandates"]),
        "reviews_overdue": sum(1 for r in rows if r["review_overdue"]), "dearer_class": sum(1 for r in rows if r["dearer_class"] and r["mandates"])}}
