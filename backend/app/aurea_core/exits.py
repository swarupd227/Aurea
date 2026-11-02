"""The end of the lifecycle: a client leaving, or dying, and the money that moves because of it (L100 §5.11).

A person says either has happened, from a document, and the book as it stood that day is kept with it. A
position with no real price on that day is listed and left out of the value, never estimated. From then on the
mandate is held: a departing client's accounts may sell and may not buy, and nothing trades on a dead client's
accounts until an executor is on file. The hold sits on the mandate, so the rebalancer and a direct order read
it without a query.

The custody connectors are mocks, so nothing here moves an asset. A transfer records what a custodian did, from
its paper, and only then do holdings, lots and cash change. A beneficiary is paid what their designation says.
With no designation the account goes through the estate, which needs an executor. A retirement account keeps
its basis, and elsewhere basis steps up only where the market's law says so.

Closing is a person's act too, once every account is empty and no order is open. It ends the mandates, and a
household that has wholly gone leaves the list every agent scans. A final part-period fee is shown as days
unbilled and raised by hand. Estate tax, community property and a spouse's half step-up are not modelled.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time, markets, reconciliation, registration, wash_sale
from app.aurea_core.valuation import latest_prices
from app.models.billing import Invoice
from app.models.consent import TradeConsent
from app.models.enums import RecommendationStatus
from app.models.exits import AccountTransfer, ClientExit
from app.models.governance import Recommendation
from app.models.graph import Account, AccountBeneficiary, Household, LegalEntity, Mandate, Person
from app.models.portfolio import Holding, Instrument, Price, TaxLot, Transaction
from app.models.tenant import Firm
from app.models.trading import Order
from app.provenance import ledger

LEAVING, DECEASED, EXECUTOR = "leaving", "deceased", "executor"
NOT_MODELLED = ["estate_tax", "community_property", "spousal_half_step_up", "final_part_period_fee"]
SHOWN_CLOSED = 10
# What passes as a retirement wrapper keeps its basis wherever it goes.
_RETIREMENT = {"traditional_ira", "roth_ira", "employer_rollover", "inherited_ira", "estate_inherited", "hsa", "plan_529"}
_JOINT = {"joint_jtwros", "joint_tic"}
_DUST = Decimal("0.005")


class ExitError(ValueError):
    """This cannot be recorded, with the reason."""


def _d(value) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def order_refusal(mandate: Mandate, orders: list[dict]) -> str | None:
    """Why a mandate on hold may not place these orders. A sale raises what a departing client or an
    executor needs; a buy adds to what is about to move."""
    hold = mandate.exit_hold
    if hold is None:
        return None
    if hold == DECEASED:
        return "This client has died and no executor is on file, so nothing may trade on this mandate."
    if any(o["side"] == "buy" for o in orders):
        return ("This client is leaving the firm, so nothing may be bought on this mandate." if hold == LEAVING
                else "This mandate is in an estate, so nothing may be bought on it.")
    return None


async def _household_mandates(session: AsyncSession, household_id: uuid.UUID) -> list[Mandate]:
    return (await session.execute(select(Mandate).where(
        Mandate.person_id.in_(select(Person.id).where(Person.household_id == household_id))
        | Mandate.entity_id.in_(select(LegalEntity.id).where(LegalEntity.household_id == household_id)),
        Mandate.is_active.is_(True)).order_by(Mandate.name))).scalars().all()


async def _accounts(session: AsyncSession, mandate_ids: list) -> list[Account]:
    ids = [uuid.UUID(str(m)) for m in mandate_ids]
    return (await session.execute(select(Account).where(Account.mandate_id.in_(ids)).order_by(Account.name))).scalars().all() if ids else []


async def _price_on(session: AsyncSession, instrument_id: uuid.UUID, day: date) -> Price | None:
    return (await session.execute(select(Price).where(Price.instrument_id == instrument_id, Price.as_of <= day)
                                  .order_by(Price.as_of.desc()).limit(1))).scalars().first()


async def snapshot(session: AsyncSession, accounts: list[Account], day: date) -> dict:
    """Each account as the book stood at the end of `day`, at the real prices of that day. What has none, or
    could not be rolled back to it, is listed with no value, and the total says it is incomplete."""
    rows, total, complete = [], Decimal(0), True
    for account in accounts:
        book = await reconciliation.book_as_of(session, account, day)
        positions, value = [], Decimal(0)
        for instrument_id, quantity in book["positions"].items():
            if quantity <= 0:
                continue
            inst = await session.get(Instrument, instrument_id)
            price = await _price_on(session, instrument_id, day)
            row = {"instrument_id": str(instrument_id), "symbol": inst.symbol if inst else "?", "quantity": float(quantity),
                   "price": None, "price_date": None, "value": None, "status": "valued"}
            if instrument_id in book["not_comparable"]:
                row["status"] = "not_comparable"
            elif price is None or not price.is_real:
                row["status"] = "no_real_price"
            else:
                worth = (quantity * _d(price.close)).quantize(Decimal("0.01"))
                row.update(price=float(price.close), price_date=price.as_of.isoformat(), value=float(worth))
                value += worth
            complete = complete and row["status"] == "valued"
            positions.append(row)
        cash = book["cash"] if book["cash_comparable"] else None
        complete = complete and cash is not None
        total += value + (cash or Decimal(0))
        rows.append({"account_id": str(account.id), "name": account.name, "registration_type": account.registration_type,
                     "currency": account.currency, "cash": float(cash) if cash is not None else None, "value": float(value + (cash or 0)),
                     "positions": sorted(positions, key=lambda p: -(p["value"] or 0))})
    return {"as_of": day.isoformat(), "accounts": rows, "total": float(total), "complete": complete}


def _document(source_document: str | None) -> str:
    named = (source_document or "").strip()
    if len(named) < 5:
        raise ExitError("Say which document this comes from.")
    return named


async def _open_in(session: AsyncSession, household_id: uuid.UUID) -> list[ClientExit]:
    return (await session.execute(select(ClientExit).where(
        ClientExit.household_id == household_id, ClientExit.status == "open"))).scalars().all()


async def prepare_departure(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, notice_date: date | None,
                            reason: str | None, source_document: str | None, receiving_institution: str | None = None,
                            mandate_id: uuid.UUID | None = None) -> dict:
    """Everything recording would refuse, refused without writing, and the book it would keep."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise ExitError("Household not found.")
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    day = notice_date or today
    if day > today:
        raise ExitError("Notice cannot be recorded before it has been given.")
    named = _document(source_document)
    why = (reason or "").strip()
    if len(why) < 3:
        raise ExitError("Say why the client is leaving.")
    mandates = await _household_mandates(session, household_id)
    if mandate_id is not None:
        mandates = [m for m in mandates if m.id == mandate_id]
    if not mandates:
        raise ExitError("There is no active mandate to close here.")
    taken = {m for row in await _open_in(session, household_id) for m in row.mandates or []}
    if any(str(m.id) in taken for m in mandates):
        raise ExitError("A departure or estate is already open on this mandate.")
    accounts = await _accounts(session, [m.id for m in mandates])
    return {"household": household, "mandates": mandates, "accounts": accounts, "day": day, "reason": why, "document": named,
            "receiving": (receiving_institution or "").strip() or None, "whole": mandate_id is None,
            "book": await snapshot(session, accounts, day)}


async def start_departure(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, notice_date: date | None,
                          reason: str | None, source_document: str | None, receiving_institution: str | None = None,
                          mandate_id: uuid.UUID | None = None) -> ClientExit:
    ready = await prepare_departure(session, household_id, firm_id=firm_id, notice_date=notice_date, reason=reason,
                                    source_document=source_document, receiving_institution=receiving_institution, mandate_id=mandate_id)
    row = ClientExit(firm_id=firm_id, household_id=household_id, kind="departure", event_date=ready["day"], reason=ready["reason"],
                     mandates=[str(m.id) for m in ready["mandates"]], whole_household=ready["whole"], source_document=ready["document"],
                     receiving_institution=ready["receiving"], book=ready["book"], opened_by=by)
    session.add(row)
    for mandate in ready["mandates"]:
        mandate.exit_hold = LEAVING
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_departure_recorded", actor=by, content={
        "exit_id": str(row.id), "household_id": str(household_id), "notice_date": row.event_date.isoformat(), "reason": row.reason,
        "receiving_institution": row.receiving_institution, "document": row.source_document, "value": ready["book"]["total"]})
    return row


async def prepare_death(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, date_of_death: date | None,
                        source_document: str | None, executor: str | None = None) -> dict:
    person = await session.get(Person, person_id)
    if person is None or person.firm_id != firm_id or person.household_id is None:
        raise ExitError("Person not found.")
    if person.deceased_on is not None:
        raise ExitError(f"{person.full_name}'s death is already on record, on {person.deceased_on.isoformat()}.")
    firm = await session.get(Firm, firm_id)
    if date_of_death is None:
        raise ExitError("Give the date of death.")
    if date_of_death > firm_time.today(firm):
        raise ExitError("A date of death cannot be in the future.")
    if person.date_of_birth and date_of_death < person.date_of_birth:
        raise ExitError("A date of death cannot be before the date of birth.")
    named = _document(source_document)
    mandates = (await session.execute(select(Mandate).where(Mandate.person_id == person.id, Mandate.is_active.is_(True))
                                      .order_by(Mandate.name))).scalars().all()
    accounts = await _accounts(session, [m.id for m in mandates])
    return {"person": person, "mandates": mandates, "accounts": accounts, "day": date_of_death, "document": named,
            "executor": (executor or "").strip() or None, "book": await snapshot(session, accounts, date_of_death)}


async def record_death(session: AsyncSession, person_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, date_of_death: date | None,
                       source_document: str | None, executor: str | None = None) -> ClientExit:
    ready = await prepare_death(session, person_id, firm_id=firm_id, date_of_death=date_of_death,
                                source_document=source_document, executor=executor)
    person = ready["person"]
    person.deceased_on = ready["day"]
    row = ClientExit(firm_id=firm_id, household_id=person.household_id, kind="death", person_id=person.id, event_date=ready["day"],
                     mandates=[str(m.id) for m in ready["mandates"]], source_document=ready["document"], executor=ready["executor"],
                     executor_document=ready["document"] if ready["executor"] else None, book=ready["book"], opened_by=by)
    session.add(row)
    for mandate in ready["mandates"]:
        mandate.exit_hold = EXECUTOR if ready["executor"] else DECEASED
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_death_recorded", actor=by, content={
        "exit_id": str(row.id), "person_id": str(person.id), "date_of_death": row.event_date.isoformat(),
        "document": row.source_document, "executor": row.executor, "value": ready["book"]["total"], "complete": ready["book"]["complete"]})
    return row


async def _exit_of(session: AsyncSession, exit_id: uuid.UUID, firm_id: uuid.UUID) -> ClientExit:
    row = await session.get(ClientExit, exit_id)
    if row is None or row.firm_id != firm_id:
        raise ExitError("Departure or estate not found.")
    if row.status != "open":
        raise ExitError(f"This is already {row.status}.")
    return row


async def prepare_executor(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID, executor: str | None,
                           source_document: str | None) -> dict:
    row = await _exit_of(session, exit_id, firm_id)
    if row.kind != "death":
        raise ExitError("An executor belongs to an estate.")
    name = (executor or "").strip()
    if len(name) < 2:
        raise ExitError("Give the executor's name.")
    return {"exit": row, "executor": name, "document": _document(source_document),
            "person": await session.get(Person, row.person_id) if row.person_id else None}


async def record_executor(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, executor: str | None,
                          source_document: str | None) -> ClientExit:
    ready = await prepare_executor(session, exit_id, firm_id=firm_id, executor=executor, source_document=source_document)
    row = ready["exit"]
    row.executor, row.executor_document = ready["executor"], ready["document"]
    for mandate in (await session.execute(select(Mandate).where(Mandate.id.in_([uuid.UUID(m) for m in row.mandates])))).scalars().all():
        mandate.exit_hold = EXECUTOR
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="executor_recorded", actor=by, content={
        "exit_id": str(row.id), "executor": row.executor, "document": row.executor_document})
    return row


async def _moved_share(session: AsyncSession, exit_id: uuid.UUID, account_id: uuid.UUID) -> float:
    rows = (await session.execute(select(AccountTransfer.share).where(
        AccountTransfer.exit_id == exit_id, AccountTransfer.account_id == account_id))).scalars().all()
    return sum(float(s) for s in rows)


async def _unsettled(session: AsyncSession, account_ids: list[uuid.UUID]) -> int:
    orders = (await session.execute(select(Order).where(Order.account_id.in_(account_ids)))).scalars().all() if account_ids else []
    return sum(1 for o in orders if not o.is_terminal)


def _basis(firm: Firm, household: Household | None, row: ClientExit, account: Account, staying: bool) -> str:
    if row.kind != "death" or not staying:
        return "carried" if staying else "left_firm"
    market = markets.of_household(firm, household.values if household else None)
    if account.registration_type in _RETIREMENT or market is None or market.basis_on_death != "step_up":
        return "carried"
    return "stepped_up"


async def _designation(session: AsyncSession, row: ClientExit, account: Account, to_party: str, share: float | None) -> tuple[float, str]:
    """How much of this account goes to this party, and on what footing."""
    if row.kind == "departure":
        return (share if share is not None else 1.0), "departure"
    designated = (await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id == account.id))).scalars().all()
    if designated:
        match = next((b for b in designated if b.beneficiary_name.strip().lower() == to_party.lower()), None)
        if match is None:
            raise ExitError(f"{to_party} is not a beneficiary on {account.name}: "
                            f"{', '.join(b.beneficiary_name for b in designated)}.")
        return (share if share is not None else float(match.percentage) / 100), "designation"
    if account.registration_type in _JOINT:
        return (share if share is not None else 1.0), "survivor"
    if row.executor is None:
        raise ExitError(f"{account.name} has no beneficiary on file, so it passes through the estate, and no executor is on file.")
    return (share if share is not None else 1.0), "estate"


async def prepare_transfer(session: AsyncSession, exit_id: uuid.UUID, account_id: uuid.UUID, *, firm_id: uuid.UUID, to_party: str | None,
                           transfer_date: date | None, source_document: str | None, share: float | None = None,
                           to_account_id: uuid.UUID | None = None) -> dict:
    row = await _exit_of(session, exit_id, firm_id)
    account = await session.get(Account, account_id)
    if account is None or account.firm_id != firm_id or str(account.mandate_id) not in (row.mandates or []):
        raise ExitError("That account is not part of this.")
    if account.status == "closed":
        raise ExitError(f"{account.name} is closed.")
    firm = await session.get(Firm, firm_id)
    if transfer_date is None:
        raise ExitError("Give the date the custodian moved it.")
    if transfer_date > firm_time.today(firm):
        raise ExitError("A transfer cannot be recorded before it has happened.")
    if transfer_date < row.event_date:
        raise ExitError(f"This cannot be dated before {row.event_date.isoformat()}.")
    named = _document(source_document)
    party = (to_party or "").strip() or row.receiving_institution or ""
    if len(party) < 2:
        raise ExitError("Say who received it.")
    if await _unsettled(session, [account.id]):
        raise ExitError(f"{account.name} has an order still open. Settle or cancel it first.")
    fraction, footing = await _designation(session, row, account, party, share)
    if not 0 < fraction <= 1:
        raise ExitError("The share must be above 0% and at most 100%.")
    moved = await _moved_share(session, row.id, account.id)
    if moved + fraction > 1.000001:
        raise ExitError(f"{moved:.1%} of {account.name} has already moved, so {fraction:.1%} more is too much.")
    last = moved + fraction > 0.999999

    to_account = None
    if to_account_id is not None:
        to_account = await session.get(Account, to_account_id)
        if to_account is None or to_account.firm_id != firm_id or to_account.id == account.id:
            raise ExitError("The receiving account is not in this firm.")
        if to_account.status == "closed":
            raise ExitError(f"{to_account.name} is closed.")
        if str(to_account.mandate_id) in (row.mandates or []):
            raise ExitError("The receiving account is itself on hold here.")
        if account.registration_type in ("traditional_ira", "employer_rollover", "roth_ira") and row.kind == "death" \
                and footing != "survivor" and to_account.registration_type != "inherited_ira":
            raise ExitError(f"An inherited retirement account stays in that wrapper: register {to_account.name} as inherited_ira first.")

    # Both shares are of the account as it stood, so 60% then 40% leaves nothing, whatever fell in between.
    base = next((a for a in (row.book or {}).get("accounts", []) if a["account_id"] == str(account.id)), {})
    held_at = {p["instrument_id"]: _d(p["quantity"]) for p in base.get("positions", [])}
    holdings = (await session.execute(select(Holding).where(Holding.account_id == account.id))).scalars().all()
    moves, value = [], Decimal(0)
    for h in holdings:
        quantity = _d(h.quantity)
        if quantity <= 0:
            continue
        leaving = quantity if last else min(quantity, held_at.get(str(h.instrument_id), quantity) * _d(fraction))
        inst = await session.get(Instrument, h.instrument_id)
        price = await _price_on(session, h.instrument_id, transfer_date)
        worth = (leaving * _d(price.close)).quantize(Decimal("0.01")) if price is not None and price.is_real else None
        value += worth or Decimal(0)
        moves.append({"holding": h, "instrument": inst, "quantity": leaving, "price": _d(price.close) if worth is not None else None, "value": worth})
    cash_now = _d(account.cash_balance)
    cash = cash_now if last else min(cash_now, _d(base.get("cash")) * _d(fraction)).quantize(Decimal("0.01"))
    if not moves and cash <= 0:
        raise ExitError(f"{account.name} has nothing left to move.")
    household = await session.get(Household, row.household_id)
    return {"exit": row, "account": account, "to_party": party, "to_account": to_account, "day": transfer_date, "document": named,
            "share": fraction, "footing": footing, "moves": moves, "cash": cash, "value": value + cash, "last": last,
            "basis": _basis(firm, household, row, account, to_account is not None),
            "unpriced": [m["instrument"].symbol for m in moves if m["value"] is None]}


async def _receive(session: AsyncSession, ready: dict, move: dict, lots: list[tuple]) -> None:
    """The same units arrive in the account that stays at the firm. Stepped-up lots start from the value at death."""
    target: Account = ready["to_account"]
    row: ClientExit = ready["exit"]
    holding = (await session.execute(select(Holding).where(
        Holding.account_id == target.id, Holding.instrument_id == move["instrument"].id))).scalar_one_or_none()
    if holding is None:
        holding = Holding(firm_id=target.firm_id, account_id=target.id, instrument_id=move["instrument"].id, quantity=Decimal(0),
                          market_value=Decimal(0), cost_basis=Decimal(0), lineage={"source": "exit", "exit_id": str(row.id)}, confidence=1.0)
        session.add(holding)
        await session.flush()
    stepped = None
    if ready["basis"] == "stepped_up":
        book = next((a for a in row.book["accounts"] if a["account_id"] == str(ready["account"].id)), {})
        priced = next((p for p in book.get("positions", []) if p["symbol"] == move["instrument"].symbol), {})
        stepped = _d(priced["price"]) if priced.get("price") else None
    cost = Decimal(0)
    for quantity, per_unit, acquired in lots:
        unit, since = (stepped, row.event_date) if stepped is not None else (per_unit, acquired)
        session.add(TaxLot(firm_id=target.firm_id, holding_id=holding.id, quantity=quantity, cost_per_unit=unit, acquired_on=since))
        cost += quantity * unit
    holding.quantity = _d(holding.quantity) + move["quantity"]
    holding.cost_basis = _d(holding.cost_basis) + cost
    holding.market_value = _d(holding.market_value) + (move["value"] or Decimal(0))


async def record_transfer(session: AsyncSession, exit_id: uuid.UUID, account_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, to_party: str | None,
                          transfer_date: date | None, source_document: str | None, share: float | None = None,
                          to_account_id: uuid.UUID | None = None) -> AccountTransfer:
    """Book what the custodian did: the holdings, lots and cash go down, and come up in the receiving account
    if the money stayed. Each lot leaves pro rata, because a transfer in kind sells nothing."""
    ready = await prepare_transfer(session, exit_id, account_id, firm_id=firm_id, to_party=to_party, transfer_date=transfer_date,
                                   source_document=source_document, share=share, to_account_id=to_account_id)
    row, account, day = ready["exit"], ready["account"], ready["day"]
    kind = "beneficiary_payout" if row.kind == "death" else "transfer_out"
    lineage = {"exit_id": str(row.id), "to": ready["to_party"], "kind": kind}
    shown = []
    for move in ready["moves"]:
        holding, leaving = move["holding"], move["quantity"]
        held = _d(holding.quantity)
        fraction = leaving / held
        carried = []
        for lot in (await session.execute(select(TaxLot).where(TaxLot.holding_id == holding.id))).scalars().all():
            out = _d(lot.quantity) if fraction >= 1 else _d(lot.quantity) * fraction
            carried.append((out, _d(lot.cost_per_unit), lot.acquired_on))
            lot.quantity = _d(lot.quantity) - out
            if _d(lot.quantity) <= 0:
                await session.delete(lot)
        remaining = held - leaving
        holding.cost_basis = _d(holding.cost_basis) * (remaining / held)
        holding.market_value = _d(holding.market_value) * (remaining / held)
        holding.quantity = remaining
        amount = move["value"] or Decimal(0)
        session.add(Transaction(firm_id=firm_id, account_id=account.id, instrument_id=move["instrument"].id, txn_type="transfer_out",
                                quantity=leaving, price=move["price"] or Decimal(0), amount=amount, trade_date=day, lineage=lineage))
        if ready["to_account"] is not None:
            await _receive(session, ready, move, carried)
            session.add(Transaction(firm_id=firm_id, account_id=ready["to_account"].id, instrument_id=move["instrument"].id, txn_type="transfer_in",
                                    quantity=leaving, price=move["price"] or Decimal(0), amount=amount, trade_date=day, lineage=lineage))
        shown.append({"symbol": move["instrument"].symbol, "quantity": float(leaving), "value": float(amount) if move["value"] is not None else None})
    if ready["cash"] > 0:
        account.cash_balance = _d(account.cash_balance) - ready["cash"]
        session.add(Transaction(firm_id=firm_id, account_id=account.id, instrument_id=None, txn_type="transfer_out",
                                quantity=0, price=0, amount=ready["cash"], trade_date=day, lineage=lineage))
        if ready["to_account"] is not None:
            ready["to_account"].cash_balance = _d(ready["to_account"].cash_balance) + ready["cash"]
            session.add(Transaction(firm_id=firm_id, account_id=ready["to_account"].id, instrument_id=None, txn_type="transfer_in",
                                    quantity=0, price=0, amount=ready["cash"], trade_date=day, lineage=lineage))
    if ready["to_account"] is not None and ready["basis"] != "left_firm" and row.kind == "death" \
            and ready["to_account"].registration_type == "inherited_ira" and ready["to_account"].original_owner_death_date is None:
        ready["to_account"].original_owner_death_date = row.event_date
    transfer = AccountTransfer(firm_id=firm_id, exit_id=row.id, account_id=account.id, kind=kind, to_party=ready["to_party"],
                               to_account_id=ready["to_account"].id if ready["to_account"] else None, share=ready["share"], transfer_date=day,
                               value=ready["value"], cash=ready["cash"], positions=shown, basis=ready["basis"],
                               source_document=ready["document"], recorded_by=by)
    session.add(transfer)
    if ready["last"]:
        account.status, account.closed_on = "closed", day
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="account_transfer_recorded", actor=by, content={
        "exit_id": str(row.id), "account_id": str(account.id), "to": ready["to_party"], "share": ready["share"], "value": float(ready["value"]),
        "basis": ready["basis"], "document": ready["document"], "date": day.isoformat()})
    return transfer


async def prepare_close(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict:
    row = await _exit_of(session, exit_id, firm_id)
    accounts = await _accounts(session, row.mandates)
    full = []
    for account in accounts:
        held = sum((_d(h.quantity) for h in (await session.execute(
            select(Holding).where(Holding.account_id == account.id))).scalars().all()), Decimal(0))
        if held > 0 or _d(account.cash_balance) > _DUST:
            full.append(account.name)
    if full:
        raise ExitError(f"Still holding assets: {', '.join(full)}. Record what moved first.")
    if await _unsettled(session, [a.id for a in accounts]):
        raise ExitError("An order is still open on one of these accounts.")
    return {"exit": row, "accounts": accounts, "household": await session.get(Household, row.household_id)}


async def close(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID, by: str) -> ClientExit:
    ready = await prepare_close(session, exit_id, firm_id=firm_id)
    row = ready["exit"]
    firm = await session.get(Firm, firm_id)
    today = firm_time.today(firm)
    row.status, row.closed_by, row.closed_on = "complete", by, today
    for account in ready["accounts"]:
        if account.status != "closed":
            account.status, account.closed_on = "closed", today
    for mandate in (await session.execute(select(Mandate).where(Mandate.id.in_([uuid.UUID(m) for m in row.mandates])))).scalars().all():
        mandate.is_active, mandate.exit_hold = False, None
    await session.flush()
    if row.whole_household or not await _household_mandates(session, row.household_id):
        ready["household"].left_on = today
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_exit_closed", actor=by, content={
        "exit_id": str(row.id), "household_id": str(row.household_id), "kind": row.kind})
    return row


async def prepare_cancel(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    row = await _exit_of(session, exit_id, firm_id)
    why = (reason or "").strip()
    if len(why) < 3:
        raise ExitError("Say why this is being withdrawn.")
    if (await session.execute(select(AccountTransfer.id).where(AccountTransfer.exit_id == row.id).limit(1))).first():
        raise ExitError("Money has already moved on this, so it cannot be withdrawn.")
    return {"exit": row, "reason": why, "household": await session.get(Household, row.household_id),
            "person": await session.get(Person, row.person_id) if row.person_id else None}


async def cancel(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, reason: str | None) -> ClientExit:
    """The client stayed, or the record was wrong. It stays on record as withdrawn."""
    ready = await prepare_cancel(session, exit_id, firm_id=firm_id, reason=reason)
    row = ready["exit"]
    row.status, row.cancel_reason, row.closed_by = "cancelled", ready["reason"], by
    if ready["person"] is not None:
        ready["person"].deceased_on = None
    for mandate in (await session.execute(select(Mandate).where(Mandate.id.in_([uuid.UUID(m) for m in row.mandates])))).scalars().all():
        mandate.exit_hold = None
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="client_exit_withdrawn", actor=by, content={
        "exit_id": str(row.id), "reason": row.reason, "because": ready["reason"]})
    return row


async def _accounts_view(session: AsyncSession, row: ClientExit, transfers: list[AccountTransfer]) -> list[dict]:
    moved: dict[str, list[AccountTransfer]] = defaultdict(list)
    for t in transfers:
        moved[str(t.account_id)].append(t)
    prices = await latest_prices(session, row.firm_id)
    out = []
    for account in await _accounts(session, row.mandates):
        book = next((a for a in (row.book or {}).get("accounts", []) if a["account_id"] == str(account.id)), {})
        holdings = (await session.execute(select(Holding).where(Holding.account_id == account.id))).scalars().all()
        left = float(account.cash_balance or 0) + sum(
            float(h.quantity) * float(prices[h.instrument_id].close) for h in holdings
            if h.instrument_id in prices and prices[h.instrument_id].is_real)
        designated = (await session.execute(select(AccountBeneficiary).where(AccountBeneficiary.account_id == account.id))).scalars().all()
        primary = sum(float(b.percentage) for b in designated if b.designation_class == "primary")
        rows = moved[str(account.id)]
        rmd = await registration.rmd_for_account(session, account) if row.kind == "death" and account.status != "closed" else None
        out.append({
            "account_id": str(account.id), "name": account.name, "custodian": account.custodian, "status": account.status,
            "registration_type": account.registration_type, "value_then": book.get("value"), "value_left": round(left, 2),
            "unpriced": [p["symbol"] for p in book.get("positions", []) if p.get("value") is None],
            "moved_share": round(sum(float(t.share) for t in rows), 6),
            "beneficiaries": [{"name": b.beneficiary_name, "percentage": float(b.percentage), "class": b.designation_class,
                               "paid": any(t.to_party.strip().lower() == b.beneficiary_name.strip().lower() for t in rows)} for b in designated],
            "beneficiary_status": "not_needed" if row.kind != "death" else "survivor" if not designated and account.registration_type in _JOINT
            else "estate" if not designated else "complete" if abs(primary - 100) < 0.01 else "incomplete",
            "rmd": rmd if rmd and rmd.get("status") == "required" else None,
            "transfers": [{"id": str(t.id), "to": t.to_party, "stays_at_firm": t.to_account_id is not None, "share": float(t.share),
                           "date": t.transfer_date.isoformat(), "value": float(t.value), "cash": float(t.cash), "basis": t.basis,
                           "document": t.source_document, "positions": t.positions} for t in rows],
        })
    return out


async def _view(session: AsyncSession, row: ClientExit) -> dict:
    household = await session.get(Household, row.household_id)
    person = await session.get(Person, row.person_id) if row.person_id else None
    transfers = (await session.execute(select(AccountTransfer).where(AccountTransfer.exit_id == row.id)
                                       .order_by(AccountTransfer.transfer_date))).scalars().all()
    accounts = await _accounts_view(session, row, transfers)
    mandate_ids = [uuid.UUID(m) for m in row.mandates or []]
    live = [a for a in accounts if a["status"] != "closed"]
    ids = [uuid.UUID(a["account_id"]) for a in accounts]
    proposals = (await session.execute(select(Recommendation.id).where(
        Recommendation.subject_id.in_(mandate_ids), Recommendation.status == RecommendationStatus.PROPOSED))).all() if mandate_ids else []
    consents = (await session.execute(select(TradeConsent.id).where(
        TradeConsent.mandate_id.in_(mandate_ids), TradeConsent.status == "awaiting"))).all() if mandate_ids else []
    billed = (await session.execute(select(Invoice.period_end).where(
        Invoice.mandate_id.in_(mandate_ids), Invoice.kind == "invoice", Invoice.status.in_(("issued", "debited")))
        .order_by(Invoice.period_end.desc()).limit(1))).scalars().first() if mandate_ids else None
    trustee_of = []
    if person is not None:
        entities = (await session.execute(select(LegalEntity).where(LegalEntity.household_id == row.household_id))).scalars().all()
        trustee_of = [e.name for e in entities if person.full_name.lower() in str((e.governance or {}).get("trustees") or "").lower()]
    stayed = sum(float(t.value) for t in transfers if t.to_account_id)
    return {
        "id": str(row.id), "kind": row.kind, "status": row.status, "household_id": str(row.household_id),
        "household": household.name if household else None, "person_id": str(row.person_id) if row.person_id else None,
        "person": person.full_name if person else None, "event_date": row.event_date.isoformat(), "reason": row.reason,
        "receiving_institution": row.receiving_institution, "document": row.source_document, "executor": row.executor,
        "whole_household": row.whole_household, "opened_by": row.opened_by, "closed_on": row.closed_on.isoformat() if row.closed_on else None,
        "cancel_reason": row.cancel_reason, "value_then": (row.book or {}).get("total"), "book_complete": (row.book or {}).get("complete"),
        "accounts": accounts, "stayed_at_firm": round(stayed, 2), "left_firm": round(sum(float(t.value) for t in transfers) - stayed, 2),
        "checks": {
            "accounts_open": len(live), "accounts": len(accounts), "open_orders": await _unsettled(session, ids),
            "open_proposals": len(proposals), "consents_waiting": len(consents),
            "billed_through": billed.isoformat() if billed else None,
            "unbilled_days": (row.event_date - billed).days if billed and row.event_date > billed else None,
            "trustee_of": trustee_of, "rmd_due": [a["name"] for a in accounts if a["rmd"]],
            "beneficiaries_incomplete": [a["name"] for a in accounts if a["beneficiary_status"] == "incomplete"],
            "through_estate": [a["name"] for a in accounts if a["beneficiary_status"] == "estate"],
            "no_executor": row.kind == "death" and not row.executor,
        },
        "not_modelled": NOT_MODELLED,
    }


async def view_of(session: AsyncSession, exit_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    row = await session.get(ClientExit, exit_id)
    return await _view(session, row) if row is not None and row.firm_id == firm_id else None


def _summary(rows: list[dict]) -> dict:
    return {"open": sum(1 for r in rows if r["status"] == "open"),
            "departures": sum(1 for r in rows if r["status"] == "open" and r["kind"] == "departure"),
            "estates": sum(1 for r in rows if r["status"] == "open" and r["kind"] == "death"),
            "value_open": round(sum(r["value_then"] or 0 for r in rows if r["status"] == "open"), 2),
            "stayed_at_firm": round(sum(r["stayed_at_firm"] for r in rows), 2), "left_firm": round(sum(r["left_firm"] for r in rows), 2)}


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Everything open, and the latest that closed."""
    rows = (await session.execute(select(ClientExit).where(ClientExit.firm_id == firm_id, ClientExit.status == "open")
                                  .order_by(ClientExit.event_date))).scalars().all()
    done = (await session.execute(select(ClientExit).where(ClientExit.firm_id == firm_id, ClientExit.status != "open")
                                  .order_by(ClientExit.updated_at.desc()).limit(SHOWN_CLOSED))).scalars().all()
    views = [await _view(session, r) for r in [*rows, *done]]
    return {"exits": views, "summary": _summary(views), "as_of": firm_time.today(await session.get(Firm, firm_id)).isoformat()}


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        return None
    rows = (await session.execute(select(ClientExit).where(ClientExit.household_id == household_id)
                                  .order_by(ClientExit.event_date.desc()))).scalars().all()
    views = [await _view(session, r) for r in rows]
    people = (await session.execute(select(Person).where(Person.household_id == household_id, Person.deceased_on.is_(None))
                                    .order_by(Person.full_name))).scalars().all()
    return {"household_id": str(household.id), "household": household.name, "exits": views, "summary": _summary(views),
            "people": [{"person_id": str(p.id), "name": p.full_name} for p in people],
            "as_of": firm_time.today(await session.get(Firm, firm_id)).isoformat()}
