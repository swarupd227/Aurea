"""Units pledged to a lender for a loan against securities (L100 India edition section 7, B-296).

A lender who lends against units marks a lien on them at the depository or the registrar, and a unit under lien cannot be sold or
transferred until the lender releases it. The platform cannot see the depository, so a person records the pledge from the lender's
confirmation (the lender, the units, the day) and the release from its release letter, added and never edited: a release is a second row that
names the pledge it lifts, so the pledge stays. The units pledged now are the pledges less the releases.

What a pledge changes is what may be sold, and nothing else. A pledged unit is excluded from what a sale may use: `order_guard` refuses a direct
sale that needs it, naming the units; the rebalancer works from the units not pledged (`agents/drift_rebalancing`) and says what it left;
and settlement refuses a sale of a pledged unit last of all (`orders.settle`), so no path round the others sells one. The holding itself
stays in the book at its full value: the units are the client's and still count in what they own, and the loan against them is not recorded
here (a debt is recorded as a debt, from its document). A pledge names no loan amount, advance rate or margin call, and nothing here
calls a margin or invokes a pledge: those are the lender's. No pledge on file does not mean no pledge exists.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, law_figures, licences, markets
from app.core.db import utcnow
from app.core.security import UserRole
from app.models.graph import Account, Mandate
from app.models.india_batch_a import SecuritiesPledge
from app.models.portfolio import Holding, Instrument
from app.models.tenant import Firm
from app.provenance import ledger

RECORDERS = frozenset({UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.OPERATIONS})
RULE_KEY = "in_pledge_rule"


class PledgeError(ValueError):
    """This cannot be recorded, with the reason."""


def applies(firm: Firm | None) -> bool:
    market = markets.for_firm(firm) if firm else None
    return market is not None and market.code == "IN"


def in_force(firm: Firm | None) -> bool:
    """Whether a sale reads the pledges: an Indian firm that holds a licence. The trade path (settlement, the rebalancer, a direct order) asks
    this and not `applies`, so a firm with no licence on file, which has nothing pledged to read, never touches the table."""
    return applies(firm) and bool(licences.held(firm))


async def _rows(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None) -> list[SecuritiesPledge]:
    stmt = select(SecuritiesPledge).where(SecuritiesPledge.firm_id == firm_id)
    if account_ids is not None:
        stmt = stmt.where(SecuritiesPledge.account_id.in_(account_ids or [uuid.uuid4()]))
    return list((await session.execute(stmt.order_by(SecuritiesPledge.marked_on, SecuritiesPledge.created_at, SecuritiesPledge.id))).scalars().all())


def outstanding(rows: list[SecuritiesPledge]) -> list[tuple[SecuritiesPledge, float]]:
    """Each pledge with the units still under lien: what it pledged less what its releases lifted."""
    lifted: dict[uuid.UUID, float] = {}
    for r in rows:
        if r.kind == "release" and r.pledge_id:
            lifted[r.pledge_id] = lifted.get(r.pledge_id, 0.0) + float(r.units)
    return [(r, round(float(r.units) - lifted.get(r.id, 0.0), 6)) for r in rows if r.kind == "pledge"]


async def pledged_map(session: AsyncSession, firm_id: uuid.UUID, account_ids: list[uuid.UUID] | None = None) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    """Units under lien by (account, instrument)."""
    out: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    for r, left in outstanding(await _rows(session, firm_id, account_ids)):
        if left > 1e-9:
            out[(r.account_id, r.instrument_id)] = round(out.get((r.account_id, r.instrument_id), 0.0) + left, 6)
    return out


async def pledged_units(session: AsyncSession, firm_id: uuid.UUID, account_id: uuid.UUID, instrument_id: uuid.UUID) -> float:
    return (await pledged_map(session, firm_id, [account_id])).get((account_id, instrument_id), 0.0)


def sellable(quantity: float, pledged: float) -> float:
    return max(round(float(quantity) - float(pledged), 6), 0.0)


async def order_refusals(session: AsyncSession, firm: Firm, account: Account, sells: list[dict]) -> list[str]:
    """A direct sale that needs a unit under lien is refused, naming the units. A sale of what is free goes ahead. Read for an Indian firm
    only: the table is not touched for any other, so a book outside India is exactly what it was."""
    if not in_force(firm):
        return []
    pledged = await pledged_map(session, firm.id, [account.id])
    if not pledged:
        return []
    out = []
    for o in sells:
        held = pledged.get((account.id, uuid.UUID(str(o["instrument_id"]))), 0.0)
        if held <= 0:
            continue
        holding = (await session.execute(select(Holding).where(Holding.account_id == account.id, Holding.instrument_id == uuid.UUID(str(o["instrument_id"]))))).scalar_one_or_none()
        free = sellable(float(holding.quantity) if holding else 0.0, held)
        if float(o["quantity"]) > free + 1e-9:
            out.append(f"{held:g} of the {o['symbol']} in {account.name} is pledged to a lender, so {free:g} can be sold and not {float(o['quantity']):g}.")
    return out


async def prepare(session: AsyncSession, firm: Firm, role, *, account_id, instrument: str | None = None, lender: str | None = None, units=None, marked_on=None,
                  document: str | None = None, release_of=None) -> dict:
    if not applies(firm):
        raise PledgeError("A pledge of units is recorded for an Indian firm.")
    if role not in RECORDERS:
        raise PledgeError("An adviser, paraplanner or operations records a pledge, from the lender's confirmation.")
    try:
        aid = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    except ValueError:
        raise PledgeError("Give the account from the client's page.")
    account = await session.get(Account, aid)
    if account is None or account.firm_id != firm.id:
        raise PledgeError("Account not found.")
    today = firm_time.today(firm)
    if len((document or "").strip()) < 5:
        raise PledgeError("Say where the lender's confirmation, or its release letter, is filed.")
    day = marked_on or today
    if isinstance(day, str):
        try:
            day = date.fromisoformat(day)
        except ValueError:
            raise PledgeError("Give the day as YYYY-MM-DD.")
    if day > today:
        raise PledgeError("A pledge cannot be dated ahead.")
    try:
        count = round(float(str(units).replace(",", "")), 6)
    except (TypeError, ValueError):
        raise PledgeError("Give the number of units.")
    if count <= 0:
        raise PledgeError("The units must be more than none.")
    rows = await _rows(session, firm.id, [aid])
    if release_of not in (None, ""):
        try:
            rid = release_of if isinstance(release_of, uuid.UUID) else uuid.UUID(str(release_of))
        except ValueError:
            raise PledgeError("Give the pledge to release from the list.")
        left = {r.id: n for r, n in outstanding(rows)}
        pledge = next((r for r in rows if r.id == rid and r.kind == "pledge"), None)
        if pledge is None:
            raise PledgeError("That pledge is not on file for this account.")
        if left.get(rid, 0.0) <= 1e-9:
            raise PledgeError("That pledge is already released.")
        if count > left[rid] + 1e-9:
            raise PledgeError(f"Only {left[rid]:g} units are still under that pledge, so {count:g} cannot be released.")
        inst = await session.get(Instrument, pledge.instrument_id)
        return {"account": account, "kind": "release", "instrument": inst, "lender": pledge.lender, "units": count, "marked_on": day, "pledge": pledge,
                "document": document.strip(), "under_lien_after": round(left[rid] - count, 6), "today": today}
    ref = (instrument or "").strip()
    if not ref:
        raise PledgeError("Say which instrument is pledged.")
    inst = None
    try:
        inst = await session.get(Instrument, uuid.UUID(ref))
    except ValueError:
        inst = (await session.execute(select(Instrument).where(Instrument.firm_id == firm.id, Instrument.symbol == ref.upper()))).scalars().first()
    if inst is None or inst.firm_id != firm.id:
        raise PledgeError(f"No instrument {ref!r} on file at this firm.")
    if len((lender or "").strip()) < 2:
        raise PledgeError("Name the lender, as its confirmation does.")
    holding = (await session.execute(select(Holding).where(Holding.account_id == aid, Holding.instrument_id == inst.id))).scalar_one_or_none()
    held = float(holding.quantity) if holding else 0.0
    already = (await pledged_map(session, firm.id, [aid])).get((aid, inst.id), 0.0)
    if count > held - already + 1e-9:
        raise PledgeError(f"{account.name} holds {held:g} {inst.symbol} and {already:g} is already pledged, so {max(held - already, 0):g} is free to pledge and not {count:g}.")
    return {"account": account, "kind": "pledge", "instrument": inst, "lender": lender.strip(), "units": count, "marked_on": day, "pledge": None,
            "document": document.strip(), "under_lien_after": round(already + count, 6), "holding_units": held, "today": today}


async def record(session: AsyncSession, firm: Firm, role, *, by: str, **terms) -> dict:
    p = await prepare(session, firm, role, **terms)
    row = SecuritiesPledge(created_at=utcnow(), firm_id=firm.id, account_id=p["account"].id, instrument_id=p["instrument"].id, kind=p["kind"], lender=p["lender"],
                           units=p["units"], marked_on=p["marked_on"], pledge_id=p["pledge"].id if p["pledge"] else None, source_document=p["document"], recorded_by=by)
    session.add(row)
    await session.flush()
    await ledger.append_entry(session, firm_id=firm.id, event_type="securities_pledge_recorded", actor=by, content={
        "account_id": str(p["account"].id), "instrument": p["instrument"].symbol, "kind": row.kind, "lender": row.lender, "units": float(row.units),
        "marked_on": row.marked_on.isoformat(), "document": row.source_document})
    return await overview(session, firm, account_ids=[p["account"].id])


async def overview(session: AsyncSession, firm: Firm, *, account_ids: list[uuid.UUID] | None = None, household_id: uuid.UUID | None = None) -> dict:
    today = firm_time.today(firm)
    if not applies(firm):
        return {"applies": False, "as_of": today.isoformat(), "pledges": []}
    if household_id is not None:
        from app.aurea_core.performance import household_account_ids

        account_ids = await household_account_ids(session, household_id, firm_id=firm.id) or []
    rows = await _rows(session, firm.id, account_ids)
    accounts = {a.id: a for a in (await session.execute(select(Account).where(Account.id.in_({r.account_id for r in rows} or [uuid.uuid4()])))).scalars().all()}
    instruments = {i.id: i for i in (await session.execute(select(Instrument).where(Instrument.id.in_({r.instrument_id for r in rows} or [uuid.uuid4()])))).scalars().all()}
    holdings = {(h.account_id, h.instrument_id): float(h.quantity) for h in (await session.execute(
        select(Holding).where(Holding.account_id.in_(list(accounts) or [uuid.uuid4()])))).scalars().all()}
    pledges = []
    for r, left in outstanding(rows):
        inst = instruments.get(r.instrument_id)
        held = holdings.get((r.account_id, r.instrument_id), 0.0)
        pledges.append({"id": str(r.id), "account": accounts[r.account_id].name if r.account_id in accounts else None, "account_id": str(r.account_id),
                        "symbol": inst.symbol if inst else None, "instrument": inst.name if inst else None, "lender": r.lender, "units": float(r.units),
                        "under_lien": left, "state": "pledged" if left > 1e-9 else "released", "label": "Pledged" if left > 1e-9 else "Released",
                        "marked_on": r.marked_on.isoformat(), "document": r.source_document, "held": held, "sellable": sellable(held, left) if left > 1e-9 else held})
    pledges.sort(key=lambda p: (p["state"] != "pledged", p["marked_on"]), reverse=False)
    return {"applies": True, "as_of": today.isoformat(), "pledges": pledges, "pledged": sum(1 for p in pledges if p["state"] == "pledged"),
            "rule": law_figures.cited([RULE_KEY], today)[0],
            "note": "A pledged unit cannot be sold by a direct order, the rebalancer or settlement. No pledge on file does not mean no pledge exists, and the loan is not recorded here."}
