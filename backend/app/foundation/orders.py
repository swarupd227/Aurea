"""Orders out through the platform that owns the book (B-165).

When a firm's positions are owned by a Foundation product, Astra's copy of the book is that product's, and a trade is
something only the product can book. So an approved proposal's orders and a direct order leave as a command
(``orders.place``) to the product instead of filling on the paper venue, and nothing in Astra's book moves when they
go: holdings, cash and the trade itself arrive when the product's own record of the fill comes back through the sync,
as a custodian's paper moves money today. The order waits as placed until then; the sync's confirmation records the
fill on it, already booked, so ``orders.settle`` never runs and nothing is booked twice.

Everything before the order is unchanged: ``order_guard``, the wash-sale guard, the IPS and the shelf run on the local
copy before the card and at Confirm, as they do for a native firm. A native firm is untouched: ``venue_for_firm`` gives
it the venue it always had.

A product that owns the positions and takes no orders refuses every order, naming itself, as ``owners.write_refusal``
does for a record; so does one whose fills could not come back because it does not own the activity too.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.foundation import contract as c
from app.foundation.owners import NATIVE, owner, owner_name

ORDERS_PLACE = "orders.place"


def route(firm) -> tuple[object | None, str | None]:
    """The product orders go to, and why they cannot, for a firm whose positions it owns. (None, None) when the
    positions are Astra's own."""
    from app.foundation.adapters import get_adapter

    key = owner(firm, "positions")
    if key == NATIVE:
        return None, None
    adapter = get_adapter(key)
    name = owner_name(key)
    if adapter is None or ORDERS_PLACE not in adapter.commands_for(firm):
        return adapter, (f"{name} owns this firm's positions and takes no orders from Astra: place the order in {name}, "
                         f"and the trade reaches Astra on the next sync.")
    if owner(firm, "activity") != key:
        return adapter, (f"{name} owns this firm's positions but not its activity, so a fill could not come back to "
                         f"Astra. Nothing is sent until {name} owns both.")
    return adapter, None


class PlatformVenue:
    """The product that owns the book, as a venue: ``place`` sends the order as a command and ``poll`` reports no
    fill, because the fill is the product's to report, through the sync. It does not claim a market was reached."""

    reaches_market = False
    platform = True

    def __init__(self, adapter, refusal: str | None):
        self.adapter = adapter
        self.key = adapter.key if adapter is not None else "unbuilt"
        self.name = adapter.name if adapter is not None else "The owner"
        self.refusal = refusal
        self.label = (f"Refused: {refusal}" if refusal else
                      f"{self.name}: sent as an order; the book moves when its confirmation syncs back")

    async def place(self, session: AsyncSession, order) -> str:
        from app.aurea_core import orders as order_engine
        from app.conduit.execution import VenueError
        from app.foundation import commands
        from app.models.enums import OrderStatus
        from app.models.portfolio import Instrument
        from app.models.tenant import Firm

        if self.refusal:
            raise VenueError(self.refusal)
        firm = await session.get(Firm, order.firm_id)
        connector = await commands.connector_for(session, firm, self.key)
        if connector is None:
            raise VenueError(f"No {self.name} connector is switched on, so the order cannot be sent. Operations sets it "
                             "up on the Connectors page.")
        account_ext = await commands.external_id_of(session, firm, self.key, "account", order.account_id)
        if not account_ext:
            raise VenueError(f"This account is not on {self.name}, so the order cannot go there.")
        inst = await session.get(Instrument, order.instrument_id)
        symbol = (order.lineage or {}).get("symbol") or (inst.symbol if inst else None)
        payload = {"order_id": str(order.id), "account_external_id": account_ext, "symbol": symbol,
                   "side": str(order.side), "quantity": float(order.quantity or 0),
                   "order_type": str(order.order_type or "market"),
                   "limit_price": float(order.limit_price) if order.limit_price is not None else None,
                   "est_price": float(order.est_price or 0), "reason": (order.reason or "")[:200]}
        # Who sent it, as the order's own history names them (the approving role, or the direct order's).
        by = next((h.get("actor") for h in reversed(order.history or []) if h.get("to") == "staged"), None) or "astra"
        actor = SimpleNamespace(id=None, email=f"orders:{by}")
        cmd = await commands.send(session, firm, actor, adapter=self.adapter, connector=connector, domain="positions",
                                  kind=ORDERS_PLACE, payload=payload,
                                  subject=f"{str(order.side).title()} {float(order.quantity or 0):g} {symbol}")
        if cmd.status == "failed":
            raise VenueError(f"{self.name} refused the order: {cmd.error}")
        order.venue = self.key
        order.external_ref = cmd.external_id
        order.lineage = {**(order.lineage or {}), "sent_to": self.key, "command_id": str(cmd.id)}
        order_engine.transition(order, OrderStatus.PLACED, actor=f"venue:{self.key}",
                                note=f"sent to {self.name}; booked when its confirmation syncs back")
        await session.flush()
        return cmd.external_id

    async def poll(self, session: AsyncSession, order) -> list:
        return []


def platform_venue(firm) -> PlatformVenue | None:
    """The venue for a firm whose positions a product owns; None for a firm whose positions are Astra's own."""
    adapter, refusal = route(firm)
    if adapter is None and refusal is None:
        return None
    return PlatformVenue(adapter, refusal)


async def confirm_fills(session: AsyncSession, firm, source: str, records: list[c.Record], *,
                        now: datetime) -> int:
    """Orders whose fill came back in this pull: the command confirmed with the trade it became, and the fill recorded
    on the order as already booked (the sync brought the holding, the cash and the trade). A trade names its order by
    the command's id at the platform, ``lineage.order_ref``."""
    from app.aurea_core import orders as order_engine
    from app.models.enums import SettlementStatus
    from app.models.foundation import ExternalRef, FoundationCommand
    from app.models.trading import Order

    fills: dict[str, list] = {}
    for r in records:
        if r.entity == "txn":
            ref = str(((getattr(r, "lineage", None) or {}).get("order_ref")) or "")
            if ref:
                fills.setdefault(ref, []).append(r)
    if not fills:
        return 0
    pending = (await session.execute(select(FoundationCommand).where(
        FoundationCommand.firm_id == firm.id, FoundationCommand.source == source,
        FoundationCommand.kind == ORDERS_PLACE, FoundationCommand.status == "pending",
        FoundationCommand.external_id.in_(list(fills))))).scalars().all()
    for cmd in pending:
        trades = fills[cmd.external_id]
        local = []
        for t in trades:
            local.append((await session.execute(select(ExternalRef.local_id).where(
                ExternalRef.firm_id == firm.id, ExternalRef.source == source, ExternalRef.entity_type == "txn",
                ExternalRef.external_id == t.external_id))).scalars().first())
        if not any(local):
            continue   # the trade named a record this firm could not place; it waits for the next pull
        cmd.local_id = next(x for x in local if x)
        cmd.status, cmd.confirmed_at = "confirmed", now
        try:
            order = await session.get(Order, uuid.UUID(str((cmd.payload or {}).get("order_id"))))
        except (TypeError, ValueError):
            order = None
        if order is None or order.firm_id != firm.id:
            continue
        if order.is_terminal:
            # Cancelled or refused here after it went: the platform's fill stands, and the book follows the platform.
            cmd.error = f"{owner_name(source)} filled it after it was {order.status} here; the book follows {owner_name(source)}."
            continue
        for t in trades:
            remaining = float(order.quantity or 0) - float(order.filled_quantity or 0)
            qty = min(float(t.quantity or 0), remaining)
            if qty <= 0:
                break
            lin = dict(t.lineage or {})
            ex = await order_engine.record_fill(session, order, quantity=qty, price=t.price, fees=lin.get("fees") or 0,
                                                venue=source, external_ref=t.external_id, actor=f"venue:{source}")
            ex.settled = True   # booked by the sync, never by orders.settle
            ex.lineage = {"booked_by": "sync", "confirmed_by": source, "txn_external_id": t.external_id,
                          "price_source": lin.get("price_source"), "price_as_of": lin.get("price_as_of"),
                          "reaches_market": lin.get("reaches_market"), "simulated": bool(lin.get("simulated"))}
        if float(order.filled_quantity or 0) >= float(order.quantity or 0):
            order.settlement_status = SettlementStatus.SETTLED
            order.settled_at = now
        order.lineage = {**(order.lineage or {}), "booked_by": "sync"}
    await session.flush()
    return len(pending)
