"""Net worth: the managed book, what a household holds elsewhere, what it owes, and what it spends.

It only works from what is on file. A figure is recorded by a person from a document and never estimated,
so wallet-share's guess at what a client holds elsewhere does not belong here. With no liabilities on file
the result is an assets-only figure and says so, because no debt on file does not mean no debt. With no
income or no expenses on file, cash flow is not assessed, and saying so is the result.

An item in a currency other than the firm's is listed and left out of the sums, because adding it unconverted
would be wrong in a way that looks exact.

A figure recorded here replaces what a person's profile says about the same thing, so an amount is
never counted twice: recorded held-away investments replace `profile.held_away`, and a recorded pension
replaces the pots in their tax details. A joint item replaces them for everyone in the household.
"""
from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import firm_time
from app.aurea_core.graph import held_away_of, household_brain, list_households, replaced_by
from app.core.db import utcnow
from app.models.balance_sheet import ASSET_KINDS, EXPENSE_KINDS, LIABILITY_KINDS, BalanceItem, side_of
from app.models.graph import Household, Person
from app.models.onboarding import HeldAwayAsset, OnboardingCase
from app.models.tenant import Firm
from app.provenance import ledger

# Pots a person's tax details record. An account in the book carries no tax wrapper, so a pot here is
# not also in the managed total. A firm that books one as well as recording it would count it twice.
RETIREMENT_ACCOUNTS = ("pension_pot", "isa_value", "ira_value", "roth_value")
SHOWN_ENDED = 10


class BalanceSheetError(ValueError):
    """An item that cannot be recorded or ended, with the reason."""


def income_of(person: Person) -> float | None:
    """The income on file, or None. Zero is a stated figure and stays zero."""
    value = ((person.profile or {}).get("tax") or {}).get("annual_income")
    return None if value is None else float(value)


def retirement_accounts_of(person: Person) -> float:
    tax = (person.profile or {}).get("tax") or {}
    return sum(float(tax.get(k) or 0) for k in RETIREMENT_ACCOUNTS)


def _sum(items: list[BalanceItem], *kinds: str) -> float:
    return sum(float(i.amount) for i in items if i.kind in kinds)


def assets_on_file(persons: list[Person], items: list[BalanceItem]) -> dict[str, float]:
    """Everything a household owns outside the book, by kind. Pure. `items` are current and in the
    firm's currency. Recorded figures replace the profile's, person by person."""
    pots = _sum(items, "pension") + sum(retirement_accounts_of(p) for p in persons if not replaced_by(items, "pension", p))
    return {"held_away": held_away_of(persons, items), "retirement_accounts": pots, "cash": _sum(items, "cash"),
            "property": _sum(items, "property"), "business": _sum(items, "business"), "other": _sum(items, "other_asset")}


def liquid_of(assets: dict[str, float]) -> float:
    """What could be drawn on to meet a claim, of what `assets_on_file` found. A family home or a
    business cannot pay one."""
    return assets["held_away"] + assets["retirement_accounts"] + assets["cash"]


def debts_of(items: list[BalanceItem], person_id: uuid.UUID | None = None) -> float:
    """What is owed: a person's own debts and the household's joint ones, or with no person, all of it."""
    return sum(float(i.amount) for i in items if i.side == "liability" and (person_id is None or i.person_id in (None, person_id)))


async def current_items(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> list[BalanceItem]:
    return (await session.execute(select(BalanceItem).where(
        BalanceItem.firm_id == firm_id, BalanceItem.household_id == household_id, BalanceItem.status == "current"
    ).order_by(BalanceItem.side, BalanceItem.kind, BalanceItem.name))).scalars().all()


def in_currency(items: list[BalanceItem], firm: Firm | None) -> tuple[list[BalanceItem], list[BalanceItem]]:
    """Items that can be added up, and those that cannot, because nothing here converts between currencies."""
    base = ((firm.base_currency if firm else None) or "NZD").upper()
    return [i for i in items if (i.currency or base).upper() == base], [i for i in items if (i.currency or base).upper() != base]


def _item_view(i: BalanceItem, names: dict) -> dict:
    return {
        "id": str(i.id), "side": i.side, "kind": i.kind, "name": i.name, "amount": float(i.amount), "currency": i.currency,
        "person_id": str(i.person_id) if i.person_id else None, "person": names.get(i.person_id) if i.person_id else None,
        "annual_payment": float(i.annual_payment) if i.annual_payment is not None else None,
        "interest_rate": float(i.interest_rate) if i.interest_rate is not None else None,
        "ends_on": i.ends_on.isoformat() if i.ends_on else None, "as_of": i.as_of.isoformat(), "status": i.status,
        "source_document": i.source_document, "recorded_by": i.recorded_by,
        "ended_on": i.ended_on.isoformat() if i.ended_on else None, "ended_reason": i.ended_reason,
    }


def _cash_flow(persons: list[Person], items: list[BalanceItem]) -> dict:
    """What comes in, what goes out, and what is left. Only from what is on file."""
    adults = [p for p in persons if not p.is_next_gen]
    known = [income_of(p) for p in adults if income_of(p) is not None]
    spending = [i for i in items if i.side == "expense"]
    debts = [i for i in items if i.side == "liability"]
    payments = sum(float(i.annual_payment or 0) for i in debts)
    missing = []
    if not known:
        missing.append("income")
    if not spending:
        missing.append("expenses")
    income, expenses = sum(known), sum(float(i.amount) for i in spending)
    return {
        "status": "not_assessed" if missing else "assessed", "missing": missing, "income": round(income, 2) if known else None,
        "income_partial": bool(known) and len(known) < len(adults), "expenses": round(expenses, 2) if spending else None,
        "debt_payments": round(payments, 2), "payments_missing": sum(1 for i in debts if i.annual_payment is None),
        "surplus": None if missing else round(income - expenses - payments, 2),
    }


async def for_household(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID) -> dict | None:
    """One household's balance sheet, or None if it is not in this firm."""
    brain = await household_brain(session, household_id, firm_id=firm_id)
    if brain is None:
        return None
    firm = await session.get(Firm, firm_id)
    persons = (await session.execute(select(Person).where(Person.household_id == household_id))).scalars().all()
    names = {p.id: p.full_name for p in persons}
    items = await current_items(session, household_id, firm_id=firm_id)
    usable, foreign = in_currency(items, firm)
    outside = assets_on_file(persons, usable)
    managed = float(brain["totals"]["total_value"])
    assets = managed + sum(outside.values())
    owed = debts_of(usable)
    liabilities = [i for i in items if i.side == "liability"]
    ended = (await session.execute(select(BalanceItem).where(
        BalanceItem.firm_id == firm_id, BalanceItem.household_id == household_id, BalanceItem.status == "ended"
    ).order_by(BalanceItem.ended_on.desc()).limit(SHOWN_ENDED))).scalars().all()
    return {
        "household_id": str(household_id), "household": brain["household"]["name"],
        "currency": firm.base_currency if firm else None,
        "people": [{"person_id": str(p.id), "name": p.full_name} for p in persons],
        "assets": {"managed": round(managed, 2), **{k: round(v, 2) for k, v in outside.items()}, "total": round(assets, 2)},
        "liquid": round(managed + liquid_of(outside), 2),
        "liabilities": {"total": round(owed, 2), "by_kind": {k: round(_sum(usable, k), 2) for k in LIABILITY_KINDS if _sum(usable, k)}},
        "net_worth": round(assets - owed, 2),
        # No debt on file is not no debt: the figure says which it is.
        "basis": "assets_and_liabilities" if liabilities else "assets_only",
        "cash_flow": _cash_flow(persons, usable),
        "items": [_item_view(i, names) for i in items],
        "not_converted": [_item_view(i, names) for i in foreign],
        "ended": [_item_view(i, names) for i in ended],
    }


async def for_firm(session: AsyncSession, firm_id: uuid.UUID) -> dict:
    """Every household, the largest net worth first."""
    firm = await session.get(Firm, firm_id)
    rows = []
    for h in await list_households(session, firm_id):
        view = await for_household(session, uuid.UUID(h["id"]), firm_id=firm_id)
        if view:
            rows.append(view)
    rows.sort(key=lambda r: (-r["net_worth"], r["household"]))
    return {
        "households": rows, "currency": firm.base_currency if firm else None, "as_of": firm_time.today(firm).isoformat(),
        "kinds": {"asset": list(ASSET_KINDS), "liability": list(LIABILITY_KINDS), "expense": list(EXPENSE_KINDS)},
        "summary": {
            "households": len(rows), "with_liabilities": sum(1 for r in rows if r["basis"] != "assets_only"),
            "assets_only": sum(1 for r in rows if r["basis"] == "assets_only"),
            "cash_flow_assessed": sum(1 for r in rows if r["cash_flow"]["status"] == "assessed"),
            "not_converted": sum(len(r["not_converted"]) for r in rows),
        },
    }


def _money(value, label: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        amount = float(value)
    except (TypeError, ValueError):
        raise BalanceSheetError(f"{label} must be a number.")
    if amount < 0:
        raise BalanceSheetError(f"{label} cannot be negative.")
    return amount


async def prepare_item(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, kind: str, name: str, amount,
                       as_of: date | None, source_document: str, person_id: uuid.UUID | None = None, annual_payment=None,
                       interest_rate=None, ends_on: date | None = None, currency: str | None = None,
                       replaces_id: uuid.UUID | None = None) -> dict:
    """Everything recording would refuse, refused without writing, so a Confirm card that would only
    fail is not shown. Returns what would be written."""
    household = await session.get(Household, household_id)
    if household is None or household.firm_id != firm_id:
        raise BalanceSheetError("Household not found.")
    firm = await session.get(Firm, firm_id)
    side = side_of(kind)
    if side is None:
        raise BalanceSheetError(f"Unknown kind '{kind}'. Choose one of: {', '.join(ASSET_KINDS + LIABILITY_KINDS + EXPENSE_KINDS)}.")
    label = (name or "").strip()
    if len(label) < 2:
        raise BalanceSheetError("Name the institution, lender or what it is.")
    figure = _money(amount, "The amount")
    if not figure:
        raise BalanceSheetError("Give the amount from the document.")
    today = firm_time.today(firm)
    if as_of is None:
        raise BalanceSheetError("Give the date the figure was true.")
    if as_of > today:
        raise BalanceSheetError("A figure cannot be dated in the future.")
    source = (source_document or "").strip()
    if len(source) < 3:
        raise BalanceSheetError("Say which document this comes from - its name, date and issuer. "
                                "A figure with no source cannot be taken to a client.")
    person = None
    if person_id is not None:
        person = await session.get(Person, person_id)
        if person is None or person.household_id != household_id:
            raise BalanceSheetError("That person is not in this household.")
    payment, rate = _money(annual_payment, "The annual payment"), _money(interest_rate, "The interest rate")
    if side != "liability" and (payment is not None or rate is not None or ends_on is not None):
        raise BalanceSheetError("A payment, rate or end date belongs to a debt.")
    if rate is not None and rate > 1:
        raise BalanceSheetError("The interest rate must be between 0% and 100%.")
    if ends_on is not None and ends_on < today:
        raise BalanceSheetError(f"This debt ended on {ends_on.isoformat()}, so it is not owed.")
    code = (currency or firm.base_currency or "NZD").strip().upper()
    if len(code) != 3:
        raise BalanceSheetError("Give the currency as a three-letter code.")

    replacing = None
    if replaces_id is not None:
        replacing = await session.get(BalanceItem, replaces_id)
        if replacing is None or replacing.household_id != household_id or replacing.status != "current":
            raise BalanceSheetError("The item to replace is not on file for this household.")
        if replacing.side != side:
            raise BalanceSheetError("An item can only be replaced by one of the same type.")
        # A new balance is not a new loan: what is not restated carries over, or the payment would vanish
        # from the cash flow without anyone having said so.
        payment = payment if payment is not None else (float(replacing.annual_payment) if replacing.annual_payment is not None else None)
        rate = rate if rate is not None else (float(replacing.interest_rate) if replacing.interest_rate is not None else None)
        ends_on = ends_on if ends_on is not None else replacing.ends_on
    else:
        same = (await session.execute(select(BalanceItem.id).where(
            BalanceItem.household_id == household_id, BalanceItem.status == "current", BalanceItem.kind == kind,
            BalanceItem.name == label, BalanceItem.person_id == person_id if person_id else BalanceItem.person_id.is_(None)
        ))).first()
        if same:
            raise BalanceSheetError(f"{label} is already on file. Replace it with the new figure rather than adding it twice.")
    return {"household": household, "person": person, "side": side, "kind": kind, "name": label, "amount": figure,
            "as_of": as_of, "source": source[:255], "annual_payment": payment, "interest_rate": rate, "ends_on": ends_on,
            "currency": code, "replacing": replacing, "not_converted": code != (firm.base_currency or "NZD").upper()}


async def record_item(session: AsyncSession, household_id: uuid.UUID, *, firm_id: uuid.UUID, by: str, **fields) -> dict:
    ready = await prepare_item(session, household_id, firm_id=firm_id, **fields)
    old = ready["replacing"]
    row = BalanceItem(firm_id=firm_id, household_id=household_id, person_id=ready["person"].id if ready["person"] else None,
                      side=ready["side"], kind=ready["kind"], name=ready["name"], amount=ready["amount"], currency=ready["currency"],
                      annual_payment=ready["annual_payment"], interest_rate=ready["interest_rate"], ends_on=ready["ends_on"],
                      as_of=ready["as_of"], status="current", source_document=ready["source"], recorded_by=by,
                      replaces_id=old.id if old else None)
    session.add(row)
    if old is not None:
        old.status, old.ended_on, old.ended_by, old.ended_reason = "ended", ready["as_of"], by, f"Replaced by a figure as at {ready['as_of'].isoformat()}"
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="balance_item_recorded", actor=by, content={
        "item_id": str(row.id), "household_id": str(household_id), "side": row.side, "kind": row.kind, "name": row.name,
        "amount": ready["amount"], "currency": row.currency, "as_of": ready["as_of"].isoformat(), "source_document": row.source_document,
        "replaces": str(old.id) if old else None,
    })
    view = await for_household(session, household_id, firm_id=firm_id)
    return {**view, "item_id": str(row.id), "recorded": row.name}


async def prepare_end(session: AsyncSession, item_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None) -> dict:
    """What ending would refuse, refused without writing, and the item it would end."""
    text = (reason or "").strip()
    if len(text) < 3:
        raise BalanceSheetError("Say why this no longer stands - repaid, sold, closed.")
    row = await session.get(BalanceItem, item_id)
    if row is None or row.firm_id != firm_id:
        raise BalanceSheetError("That item was not found.")
    if row.status != "current":
        raise BalanceSheetError("This item has already ended.")
    return {"item": row, "reason": text[:255], "household": await session.get(Household, row.household_id)}


async def end_item(session: AsyncSession, item_id: uuid.UUID, *, firm_id: uuid.UUID, reason: str | None, by: str) -> dict:
    """Mark an item as no longer standing. Kept, with why, rather than deleted. The row is locked and its
    status re-read, so two people ending it cannot both record a reason."""
    ready = await prepare_end(session, item_id, firm_id=firm_id, reason=reason)
    row = (await session.execute(select(BalanceItem).where(BalanceItem.id == item_id).with_for_update()
                                 .execution_options(populate_existing=True))).scalar_one()
    if row.status != "current":
        raise BalanceSheetError("This item has already ended.")
    row.status, row.ended_on, row.ended_by, row.ended_reason = "ended", utcnow().date(), by, ready["reason"]
    await session.flush()
    await ledger.append_entry(session, firm_id=firm_id, event_type="balance_item_ended", actor=by, content={
        "item_id": str(row.id), "household_id": str(row.household_id), "kind": row.kind, "name": row.name, "reason": ready["reason"],
    })
    view = await for_household(session, row.household_id, firm_id=firm_id)
    return {**view, "ended_item": row.name}


_ONBOARDED = {"pension": "pension", "property": "property"}


async def carry_from_onboarding(session: AsyncSession, case: OnboardingCase, *, household_id: uuid.UUID, today: date) -> int:
    """What a client declared they hold elsewhere at onboarding, on the household it became. A figure with
    no value is left behind: nothing here works one out. Safe to run twice."""
    declared = (await session.execute(select(HeldAwayAsset).where(HeldAwayAsset.case_id == case.id))).scalars().all()
    carried = 0
    for a in declared:
        if a.approx_value is None or float(a.approx_value) <= 0:
            continue
        kind = _ONBOARDED.get((a.account_type or "").strip().lower(), "held_away_investment")
        name = (a.institution or "Held away")[:120]
        if (await session.execute(select(BalanceItem.id).where(
                BalanceItem.household_id == household_id, BalanceItem.kind == kind, BalanceItem.name == name))).first():
            continue
        session.add(BalanceItem(firm_id=case.firm_id, household_id=household_id, side="asset", kind=kind, name=name,
                                amount=float(a.approx_value), currency=(a.currency or "NZD")[:3].upper(), as_of=today,
                                source_document=f"Declared at onboarding ({a.source}): {case.prospect_name}"[:255],
                                recorded_by="onboarding"))
        carried += 1
    await session.flush()
    return carried
