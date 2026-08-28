"""Execution venues — where an order actually goes.

A venue is anything that can accept an order and report fills. This module defines that
contract and ships one implementation; a live broker is a second implementation of the
same protocol, registered under its own key. Nothing above this layer knows which venue
is in use, so moving a firm onto a real broker is configuration plus credentials, not a
rewrite of the lifecycle.

On honesty about what the shipped venue is: `PaperVenue` executes against the firm's own
market data. The prices are real — the same feed the valuations use — and the resulting
cash, positions, tax lots and transactions are real records in the book. What it does not
do is reach a market. That is paper trading, it is named as such, and every order it
fills is stamped with the venue that filled it and the price lineage behind it, so no
report can later imply an execution that never touched a market.

It refuses to fill on a synthetic price. A position built on an invented number is worse
than no position, because it looks identical to a real one downstream.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core import orders as order_engine
from app.core.logging import get_logger
from app.models.enums import LotRelief, OrderStatus, SettlementStatus
from app.models.portfolio import Price
from app.models.tenant import Firm
from app.models.trading import Execution, Order

log = get_logger("aurea.execution")


class VenueError(Exception):
    """The venue could not accept or fill the order."""


class ExecutionVenue(Protocol):
    """What any venue must provide. A live broker connector implements exactly this."""

    key: str
    reaches_market: bool

    async def place(self, session: AsyncSession, order: Order) -> str:
        """Accept the order. Returns the venue's own reference for it."""
        ...

    async def poll(self, session: AsyncSession, order: Order) -> list[Execution]:
        """Report any new fills. May return an empty list for a working order."""
        ...


def _fee_model(firm: Firm) -> tuple[Decimal, Decimal]:
    """(bps, minimum) per trade, from firm settings. Explicit zero is respected."""
    cfg = ((firm.settings or {}).get("execution") or {}) if firm else {}
    bps = Decimal(str(cfg.get("fee_bps", 10)))
    minimum = Decimal(str(cfg.get("fee_minimum", 5)))
    return bps, minimum


def compute_fees(firm: Firm, gross: Decimal) -> Decimal:
    bps, minimum = _fee_model(firm)
    fee = (gross * bps) / Decimal(10000)  # a basis point is 1/10,000
    return max(fee, minimum).quantize(Decimal("0.01"))


class PaperVenue:
    """Fills at the latest real close price, then books the trade. No market involved."""

    key = "paper"
    reaches_market = False

    async def place(self, session: AsyncSession, order: Order) -> str:
        ref = f"paper-{order.id}"
        order.venue = self.key
        order.external_ref = ref
        order_engine.transition(order, OrderStatus.PLACED, actor=f"venue:{self.key}",
                                note="accepted by paper venue")
        await session.flush()
        return ref

    async def _price(self, session: AsyncSession, order: Order) -> Price:
        price = (await session.execute(
            select(Price)
            .where(Price.instrument_id == order.instrument_id)
            .order_by(Price.as_of.desc())
            .limit(1)
        )).scalar_one_or_none()
        if price is None:
            raise VenueError(
                "No price for this instrument, so there is nothing honest to fill at."
            )
        if not price.is_real:
            raise VenueError(
                f"The most recent price for this instrument ({price.as_of}) is synthetic, "
                f"not from a real feed. Refusing to fill — a position built on an invented "
                f"price is indistinguishable from a real one once it is in the book."
            )
        return price

    async def poll(self, session: AsyncSession, order: Order) -> list[Execution]:
        """Fill whatever is outstanding, at the last real close."""
        if order.is_terminal:
            return []
        remaining = Decimal(str(order.remaining_quantity))
        if remaining <= 0:
            return []

        price = await self._price(session, order)
        px = Decimal(str(price.close))

        if str(order.order_type) == "limit" and order.limit_price is not None:
            limit = Decimal(str(order.limit_price))
            marketable = px <= limit if str(order.side) == "buy" else px >= limit
            if not marketable:
                # A working limit order that is not marketable is not a failure.
                return []
            px = limit

        firm = await session.get(Firm, order.firm_id)
        fee = compute_fees(firm, remaining * px)

        ex = await order_engine.record_fill(
            session, order, quantity=remaining, price=px, fees=fee,
            venue=self.key, external_ref=f"{order.external_ref}-f1",
            actor=f"venue:{self.key}",
        )
        ex.lineage = {
            "price_as_of": str(price.as_of),
            "price_source": price.source,
            "price_is_real": bool(price.is_real),
            "reaches_market": self.reaches_market,
        }
        await session.flush()
        log.info("paper_fill", order=str(order.id), quantity=float(remaining),
                 price=float(px), fees=float(fee), price_as_of=str(price.as_of))
        return [ex]


_VENUES: dict[str, ExecutionVenue] = {PaperVenue.key: PaperVenue()}


def register_venue(venue: ExecutionVenue) -> None:
    """Register a venue implementation — this is where a live broker connector lands."""
    _VENUES[venue.key] = venue


def get_venue(key: str | None = None) -> ExecutionVenue:
    key = key or PaperVenue.key
    venue = _VENUES.get(key)
    if venue is None:
        raise VenueError(
            f"No execution venue registered under '{key}'. "
            f"Available: {', '.join(sorted(_VENUES)) or 'none'}"
        )
    return venue


def venue_for_firm(firm: Firm) -> ExecutionVenue:
    """The venue a firm trades through. Defaults to paper until a broker is configured."""
    cfg = ((firm.settings or {}).get("execution") or {}) if firm else {}
    return get_venue(cfg.get("venue"))


def _uuid(v):
    """Order sets travel through a JSON payload, so their ids arrive as strings. The
    columns are UUID — asyncpg and SQLite both reject a str, so coerce at the boundary."""
    if v is None or isinstance(v, uuid.UUID):
        return v
    return uuid.UUID(str(v))


def lot_method_for_firm(firm: Firm) -> str:
    cfg = ((firm.settings or {}).get("execution") or {}) if firm else {}
    return cfg.get("lot_method", LotRelief.FIFO)


async def execute_order_set(
    session: AsyncSession, *, firm: Firm, order_set: list[dict],
    recommendation_id=None, mandate_id=None, actor: str = "adviser",
) -> dict:
    """Turn an approved order set into real orders, and carry them as far as they go.

    Draft -> staged -> placed at the firm's venue -> filled -> settled onto the book.
    Each order is independent: one that cannot be filled (no real price, a limit that is
    not marketable, more units than the account holds) is recorded with the reason and
    the rest still proceed. A partial outcome is reported as a partial outcome.
    """
    venue = venue_for_firm(firm)
    lot_method = lot_method_for_firm(firm)

    placed, settled, failed, breaks = [], [], [], []
    realised_total = Decimal(0)
    fees_total = Decimal(0)

    for spec in order_set:
        order = Order(
            firm_id=firm.id,
            account_id=_uuid(spec["account_id"]),
            instrument_id=_uuid(spec["instrument_id"]),
            mandate_id=_uuid(mandate_id),
            recommendation_id=_uuid(recommendation_id),
            side=spec.get("side", "buy"),
            quantity=Decimal(str(spec.get("quantity", 0))),
            est_price=Decimal(str(spec.get("est_price", 0))),
            est_value=Decimal(str(spec.get("est_value", 0))),
            est_realised_gain=Decimal(str(spec.get("est_realised_gain", 0))),
            custodian=spec.get("custodian"),
            reason=(spec.get("reason") or "")[:512],
            history=[],
            lineage={"symbol": spec.get("symbol"), "asset_class": spec.get("asset_class")},
        )
        session.add(order)
        await session.flush()

        label = spec.get("symbol") or str(order.instrument_id)

        # Two failures, deliberately kept apart. If the venue will not take or fill the
        # order, nothing happened and the order is rejected. If it filled and the book
        # then refused the fill, something *did* happen at the venue and pretending
        # otherwise would lose it — that is a settlement break, and it stays visible as
        # a filled order with failed settlement for operations to resolve.
        try:
            order_engine.transition(order, OrderStatus.STAGED, actor=actor,
                                    note="approved by adviser")
            await venue.place(session, order)
            fills = await venue.poll(session, order)
        except (VenueError, order_engine.OrderTransitionError) as exc:
            reason = str(exc)
            order.rejected_reason = reason[:512]
            if not order.is_terminal:
                order_engine.transition(order, OrderStatus.REJECTED,
                                        actor=f"venue:{venue.key}", note=reason[:200])
            failed.append({"order_id": str(order.id), "symbol": label, "reason": reason})
            log.warning("order_not_placed", order=str(order.id), symbol=label, reason=reason)
            continue

        placed.append({"order_id": str(order.id), "symbol": label,
                       "side": str(order.side), "quantity": float(order.quantity),
                       "status": str(order.status)})

        for ex in fills:
            try:
                result = await order_engine.settle(session, ex, lot_method=lot_method)
            except order_engine.SettlementError as exc:
                reason = str(exc)
                order.settlement_status = SettlementStatus.FAILED
                order.rejected_reason = reason[:512]
                breaks.append({"order_id": str(order.id), "symbol": label,
                               "execution_id": str(ex.id), "reason": reason})
                log.warning("settlement_break", order=str(order.id), symbol=label,
                            reason=reason)
                continue
            if result.get("settled"):
                realised_total += Decimal(str(result["realised_gain"]))
                fees_total += Decimal(str(result["fees"]))
                settled.append({"order_id": str(order.id), "symbol": label, **result})

    await session.flush()
    return {
        "venue": venue.key,
        "reaches_market": venue.reaches_market,
        "orders_created": len(order_set),
        "orders_placed": len(placed),
        "orders_settled": len(settled),
        "orders_failed": len(failed),
        "settlement_breaks": len(breaks),
        "realised_gain": float(realised_total),
        "fees": float(fees_total),
        "lot_method": str(lot_method),
        "placed": placed,
        "settled": settled,
        "failed": failed,
        "breaks": breaks,
    }
