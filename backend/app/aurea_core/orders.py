"""The order lifecycle and settlement.

Two rules hold this together:

  An order never changes state by assignment. Every move goes through `transition`,
  which refuses an illegal one and appends to the order's history. That history is the
  audit trail — who moved it, when, and why — and it is why a filled order cannot
  quietly become a draft again.

  Settlement is what makes an approval mean something. Applying a fill moves the cash,
  moves the position, creates or relieves tax lots, and writes the `txn` row that
  performance and billing read. Before this existed the book never changed, so drift
  re-proposed the same trades after every approval.

Terminal states are terminal. A filled order is not undone; it is offset by another
order. Rollback therefore cancels what has not executed and says so plainly about what
has, rather than reporting a reversal that did not happen.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.logging import get_logger
from app.models.enums import LotRelief, OrderSide, OrderStatus, SettlementStatus
from app.models.graph import Account
from app.models.portfolio import Holding, TaxLot, Transaction
from app.models.trading import Execution, Order

log = get_logger("aurea.orders")


class OrderTransitionError(Exception):
    """An illegal state move was attempted. Raised rather than silently ignored, so a
    caller with a wrong assumption fails loudly instead of corrupting an audit trail."""


class SettlementError(Exception):
    """A fill could not be applied to the book."""


# The only legal moves. Anything absent is refused.
TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.DRAFT: {OrderStatus.STAGED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
    OrderStatus.STAGED: {OrderStatus.PLACED, OrderStatus.CANCELLED, OrderStatus.REJECTED},
    OrderStatus.PLACED: {
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        OrderStatus.CANCELLED, OrderStatus.REJECTED, OrderStatus.EXPIRED,
    },
    OrderStatus.PARTIALLY_FILLED: {
        OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
        OrderStatus.CANCELLED, OrderStatus.EXPIRED,
    },
    # Terminal.
    OrderStatus.FILLED: set(),
    OrderStatus.CANCELLED: set(),
    OrderStatus.REJECTED: set(),
    OrderStatus.EXPIRED: set(),
}


def transition(order: Order, to: str, *, actor: str, note: str = "") -> Order:
    """Move an order to `to`, or refuse. The only sanctioned way to change status."""
    # A column default is applied by the INSERT, so an order constructed but not yet
    # flushed has status None. That is a draft — not, as the bare lookup would have it,
    # an unknown state with no legal moves out of it.
    frm = str(order.status or OrderStatus.DRAFT)
    to = str(to)
    if to not in TRANSITIONS.get(frm, set()):
        raise OrderTransitionError(
            f"{frm} -> {to} is not a legal order transition"
            + (" (terminal state)" if not TRANSITIONS.get(frm) else "")
        )
    order.status = to
    entry = {"from": frm, "to": to, "actor": actor, "at": utcnow().isoformat()}
    if note:
        entry["note"] = note
    # Reassign rather than append: SQLAlchemy does not track in-place JSON mutation.
    order.history = list(order.history or []) + [entry]
    if to == OrderStatus.PLACED:
        order.placed_at = utcnow()
    if to in {OrderStatus.FILLED, OrderStatus.CANCELLED,
              OrderStatus.REJECTED, OrderStatus.EXPIRED}:
        order.completed_at = utcnow()
    return order


def _d(v) -> Decimal:
    """Money and quantities stay Decimal end to end — a float here is a rounding bug
    that only shows up once a position is reconciled against its orders."""
    return v if isinstance(v, Decimal) else Decimal(str(v or 0))


async def record_fill(
    session: AsyncSession, order: Order, *, quantity, price, fees=0,
    venue: str | None = None, external_ref: str | None = None, actor: str = "venue",
) -> Execution:
    """Record one fill and roll it up onto the order. Does not settle it."""
    qty, px, fee = _d(quantity), _d(price), _d(fees)
    if qty <= 0:
        raise SettlementError("A fill must have a positive quantity")
    remaining = _d(order.quantity) - _d(order.filled_quantity)
    if qty > remaining:
        raise SettlementError(
            f"Fill of {qty} exceeds the {remaining} still outstanding on the order"
        )

    ex = Execution(
        firm_id=order.firm_id, order_id=order.id, quantity=qty, price=px, fees=fee,
        venue=venue or order.venue, external_ref=external_ref, executed_at=utcnow(),
    )
    session.add(ex)

    prior_qty, prior_avg = _d(order.filled_quantity), _d(order.avg_fill_price)
    new_qty = prior_qty + qty
    order.avg_fill_price = ((prior_avg * prior_qty) + (px * qty)) / new_qty
    order.filled_quantity = new_qty
    order.fees = _d(order.fees) + fee

    fully = new_qty >= _d(order.quantity)
    transition(order, OrderStatus.FILLED if fully else OrderStatus.PARTIALLY_FILLED,
               actor=actor, note=f"{qty} @ {px}")
    await session.flush()
    return ex


def _relieve(lots: list[TaxLot], quantity: Decimal, method: str) -> tuple[list[tuple], Decimal]:
    """Consume `quantity` from `lots` under the firm's relief method.

    Returns the consumed (lot, qty, cost) tuples and the total cost relieved. HIFO takes
    the highest-cost lots first, which minimises realised gain — the reason the method is
    a firm-level decision and not an implementation detail."""
    if method == LotRelief.HIFO:
        ordered = sorted(lots, key=lambda l: _d(l.cost_per_unit), reverse=True)
    elif method == LotRelief.LIFO:
        ordered = sorted(lots, key=lambda l: l.acquired_on, reverse=True)
    else:
        ordered = sorted(lots, key=lambda l: l.acquired_on)

    consumed, cost, left = [], Decimal(0), quantity
    for lot in ordered:
        if left <= 0:
            break
        take = min(_d(lot.quantity), left)
        if take <= 0:
            continue
        lot_cost = take * _d(lot.cost_per_unit)
        consumed.append((lot, take, lot_cost))
        cost += lot_cost
        left -= take
    if left > 0:
        raise SettlementError(
            f"Sale exceeds the tax lots on hand by {left} units — the book and the "
            f"order disagree, so nothing was settled"
        )
    return consumed, cost


async def settle(
    session: AsyncSession, execution: Execution, *, lot_method: str = LotRelief.FIFO,
) -> dict:
    """Apply a fill to the book: cash, position, tax lots and the txn ledger.

    This is the step that was missing entirely. It is idempotent per execution — a fill
    already settled is a no-op, so a retried settlement run cannot double-book."""
    if execution.settled:
        return {"settled": False, "reason": "already settled"}

    order = await session.get(Order, execution.order_id)
    if order is None:
        raise SettlementError("Execution has no order")
    account = await session.get(Account, order.account_id)
    if account is None:
        raise SettlementError("Order has no account")

    qty, px, fee = _d(execution.quantity), _d(execution.price), _d(execution.fees)
    gross = qty * px

    holding = (await session.execute(
        select(Holding).where(
            Holding.account_id == order.account_id,
            Holding.instrument_id == order.instrument_id,
        )
    )).scalar_one_or_none()

    realised = Decimal(0)

    if str(order.side) == OrderSide.BUY:
        cash_delta = -(gross + fee)
        # An account cannot pay with money it does not have. The engine sizes buys against
        # available funds, but settlement is the last line: an order set built elsewhere, a
        # stale price, or fills arriving in a different order than proposed can all overdraw
        # an account, and a negative custody balance is a broken book, not a rounding issue.
        if _d(account.cash_balance) + cash_delta < 0:
            raise SettlementError(
                f"Buying {qty} at {px} costs {gross + fee} but the account holds only "
                f"{_d(account.cash_balance)} — settling would overdraw it by "
                f"{-(_d(account.cash_balance) + cash_delta)}"
            )
        if holding is None:
            holding = Holding(
                firm_id=order.firm_id, account_id=order.account_id,
                instrument_id=order.instrument_id, quantity=Decimal(0),
                market_value=Decimal(0), cost_basis=Decimal(0),
                lineage={"source": "settlement"}, confidence=1.0,
            )
            session.add(holding)
            await session.flush()
        holding.quantity = _d(holding.quantity) + qty
        # Fees capitalise into cost basis, so realised gain on a later sale is net of them.
        holding.cost_basis = _d(holding.cost_basis) + gross + fee
        session.add(TaxLot(
            firm_id=order.firm_id, holding_id=holding.id, quantity=qty,
            cost_per_unit=(gross + fee) / qty, acquired_on=date.today(),
        ))
    else:
        if holding is None:
            raise SettlementError("Cannot sell an instrument the account does not hold")
        if qty > _d(holding.quantity):
            raise SettlementError(
                f"Sale of {qty} exceeds the {_d(holding.quantity)} held"
            )
        lots = (await session.execute(
            select(TaxLot).where(TaxLot.holding_id == holding.id)
        )).scalars().all()
        consumed, cost_relieved = _relieve(lots, qty, lot_method)
        for lot, take, _lot_cost in consumed:
            lot.quantity = _d(lot.quantity) - take
            if _d(lot.quantity) <= 0:
                await session.delete(lot)
        realised = gross - fee - cost_relieved
        cash_delta = gross - fee
        holding.quantity = _d(holding.quantity) - qty
        holding.cost_basis = _d(holding.cost_basis) - cost_relieved
        if _d(holding.quantity) <= 0:
            holding.quantity = Decimal(0)
            holding.cost_basis = Decimal(0)

    holding.market_value = _d(holding.quantity) * px
    account.cash_balance = _d(account.cash_balance) + cash_delta

    session.add(Transaction(
        firm_id=order.firm_id, account_id=order.account_id,
        instrument_id=order.instrument_id, txn_type=str(order.side),
        quantity=qty, price=px, amount=gross, trade_date=date.today(),
        lineage={"order_id": str(order.id), "execution_id": str(execution.id),
                 "venue": execution.venue, "fees": float(fee),
                 "realised_gain": float(realised), "lot_method": str(lot_method)},
    ))

    execution.settled = True
    order.realised_gain = _d(order.realised_gain) + realised
    if _d(order.filled_quantity) >= _d(order.quantity):
        order.settlement_status = SettlementStatus.SETTLED
        order.settled_at = utcnow()
    await session.flush()

    log.info("execution_settled", order=str(order.id), side=str(order.side),
             quantity=float(qty), price=float(px), realised_gain=float(realised),
             cash_delta=float(cash_delta))
    return {
        "settled": True, "side": str(order.side), "quantity": float(qty),
        "price": float(px), "fees": float(fee), "realised_gain": float(realised),
        "cash_delta": float(cash_delta),
        "holding_quantity": float(_d(holding.quantity)),
        "account_cash": float(_d(account.cash_balance)),
    }


async def cancel(session: AsyncSession, order: Order, *, actor: str, note: str = "") -> dict:
    """Cancel what has not executed. Honest about what already has."""
    if str(order.status) == OrderStatus.FILLED:
        return {"cancelled": False, "filled_quantity": float(_d(order.filled_quantity)),
                "reason": "Order is already filled. A filled order is offset by a further "
                          "order, never reversed."}
    if order.is_terminal:
        return {"cancelled": False, "reason": f"Order is already {order.status}."}
    partial = float(_d(order.filled_quantity))
    transition(order, OrderStatus.CANCELLED, actor=actor, note=note or "cancelled")
    await session.flush()
    return {"cancelled": True, "unfilled_quantity": float(order.remaining_quantity),
            "already_filled": partial,
            "reason": (f"{partial} unit(s) had already executed and remain on the book."
                       if partial else "Nothing had executed.")}
