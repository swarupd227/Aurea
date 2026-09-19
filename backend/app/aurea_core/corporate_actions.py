"""Corporate actions: entitlement computation and posting (L200-4 §5.1 steps 2, 4, 6).

Mechanical events (cash dividends, splits, return of capital) post automatically once
entitlements are computed — the arithmetic is deterministic. Complex voluntary events
(mergers, spin-offs, rights issues, tender offers) get entitlement rows and an election
workflow same as any event, but their cost-basis allocation is a judgment call L200-4 §5.1
step 6 assigns to a human ("spin-off allocations by fair-value ratios") — `post_entitlement`
records whatever `cost_basis_adjustment` the caller supplies for those types rather than
inventing a fair-value split it has no market data to compute.

No historical position tracking exists yet, so entitlement is computed off the account's
current Holding as of now, not a true as-of-record-date snapshot — a disclosed
simplification worth revisiting once corporate-actions volume justifies it.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.corporate_actions import CorporateAction, CorporateActionEntitlement
from app.models.graph import Account
from app.models.portfolio import Holding, TaxLot, Transaction

_MECHANICAL_TYPES = {"cash_dividend", "split", "reverse_split", "return_of_capital"}


def _d(v) -> Decimal:
    """Money and quantities stay Decimal end to end (app.aurea_core.orders._d's rule) — a
    float here is a rounding bug that only shows up once a position is reconciled."""
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


class CorporateActionError(Exception):
    """A posting was attempted that the arithmetic cannot support (e.g. double-post)."""


async def compute_entitlements(session: AsyncSession, corporate_action_id: uuid.UUID) -> list[CorporateActionEntitlement]:
    """One entitlement row per account currently holding the instrument."""
    action = await session.get(CorporateAction, corporate_action_id)
    if action is None:
        raise CorporateActionError(f"Corporate action {corporate_action_id} not found.")

    holdings = (await session.execute(
        select(Holding).where(
            Holding.firm_id == action.firm_id, Holding.instrument_id == action.instrument_id
        )
    )).scalars().all()

    existing = {
        e.account_id for e in (await session.execute(
            select(CorporateActionEntitlement).where(
                CorporateActionEntitlement.corporate_action_id == action.id
            )
        )).scalars().all()
    }

    created = []
    for h in holdings:
        if h.account_id in existing:
            continue
        row = CorporateActionEntitlement(
            firm_id=action.firm_id, corporate_action_id=action.id, account_id=h.account_id,
            holding_id=h.id, status="pending", shares_entitled=h.quantity,
        )
        session.add(row)
        created.append(row)

    if created:
        action.status = "election_open" if action.is_voluntary else action.status
    await session.flush()
    return created


async def elect(session: AsyncSession, entitlement_id: uuid.UUID, *, choice: str, actor: str) -> CorporateActionEntitlement:
    row = await session.get(CorporateActionEntitlement, entitlement_id)
    if row is None:
        raise CorporateActionError(f"Entitlement {entitlement_id} not found.")
    if row.status not in ("pending",):
        raise CorporateActionError(f"Entitlement is '{row.status}', not open for election.")
    row.election_choice = choice
    row.election_made_at = utcnow()
    row.election_made_by = actor
    row.status = "elected"
    await session.flush()
    return row


async def post_entitlement(
    session: AsyncSession, entitlement_id: uuid.UUID, *,
    cash_amount: float | None = None, share_amount: float | None = None,
    cost_basis_adjustment: dict | None = None,
) -> CorporateActionEntitlement:
    """Post the entitlement and, for mechanical event types, apply the arithmetic to the
    account's cash, holding and tax lots directly — never re-postable once posted (the same
    exactly-once discipline `runtime.decide` uses for recommendations, applied here to an
    event rather than a decision)."""
    row = await session.get(CorporateActionEntitlement, entitlement_id)
    if row is None:
        raise CorporateActionError(f"Entitlement {entitlement_id} not found.")
    if row.status == "posted":
        raise CorporateActionError("Entitlement already posted — corporate actions post exactly once.")

    action = await session.get(CorporateAction, row.corporate_action_id)
    now = utcnow()

    if action.action_type in _MECHANICAL_TYPES:
        await _apply_mechanical(session, action, row)
    else:
        row.cash_amount = _d(cash_amount) if cash_amount is not None else None
        row.share_amount = _d(share_amount) if share_amount is not None else None
        row.cost_basis_adjustment = cost_basis_adjustment or {}

    row.posted_at = now
    row.status = "posted"
    await session.flush()
    return row


async def _apply_mechanical(session: AsyncSession, action: CorporateAction, row: CorporateActionEntitlement) -> None:
    account = await session.get(Account, row.account_id)
    holding = await session.get(Holding, row.holding_id) if row.holding_id else None
    details = action.details or {}

    if action.action_type == "cash_dividend":
        per_share = _d(details.get("cash_per_share"))
        amount = (_d(row.shares_entitled) * per_share).quantize(Decimal("0.01"))
        account.cash_balance = _d(account.cash_balance) + amount
        row.cash_amount = amount
        session.add(Transaction(
            firm_id=action.firm_id, account_id=account.id, instrument_id=action.instrument_id,
            txn_type="dividend", quantity=row.shares_entitled, price=per_share, amount=amount,
            trade_date=action.payable_date or utcnow().date(),
            lineage={"corporate_action_id": str(action.id)},
        ))

    elif action.action_type in ("split", "reverse_split"):
        ratio = _d(details.get("ratio") or 1)  # new shares per old share, e.g. 2 for 2-for-1
        if ratio <= 0:
            raise CorporateActionError("A split ratio must be positive.")
        if holding is not None:
            old_qty = _d(holding.quantity)
            new_qty = old_qty * ratio
            holding.quantity = new_qty
            lots = (await session.execute(
                select(TaxLot).where(TaxLot.holding_id == holding.id)
            )).scalars().all()
            for lot in lots:
                lot.quantity = _d(lot.quantity) * ratio
                lot.cost_per_unit = _d(lot.cost_per_unit) / ratio  # total cost basis unchanged
            row.share_amount = new_qty - old_qty
        row.cost_basis_adjustment = {"kind": "split", "ratio": float(ratio)}

    elif action.action_type == "return_of_capital":
        per_share = _d(details.get("cash_per_share"))
        amount = (_d(row.shares_entitled) * per_share).quantize(Decimal("0.01"))
        account.cash_balance = _d(account.cash_balance) + amount
        row.cash_amount = amount
        if holding is not None:
            lots = (await session.execute(
                select(TaxLot).where(TaxLot.holding_id == holding.id)
            )).scalars().all()
            total_qty = sum((_d(l.quantity) for l in lots), Decimal(0)) or Decimal(1)
            for lot in lots:
                share = _d(lot.quantity) / total_qty
                # A basis reduction below zero becomes an immediate capital gain in a real
                # tax engine — recorded here as the adjustment; the tax layer reads it later.
                reduced = _d(lot.cost_per_unit) - (amount * share) / _d(lot.quantity)
                lot.cost_per_unit = max(Decimal(0), reduced)
            holding.cost_basis = sum((_d(l.quantity) * _d(l.cost_per_unit) for l in lots), Decimal(0))
        row.cost_basis_adjustment = {"kind": "return_of_capital", "amount": float(amount)}
        session.add(Transaction(
            firm_id=action.firm_id, account_id=account.id, instrument_id=action.instrument_id,
            txn_type="distribution", quantity=row.shares_entitled, price=per_share, amount=amount,
            trade_date=action.payable_date or utcnow().date(),
            lineage={"corporate_action_id": str(action.id)},
        ))
