"""Tool executors — wire gateway tools to real domain logic.

Each tool in the catalogue has a corresponding executor that calls the actual
business logic (household_brain, runtime.decide, execute_order_set, etc.) and
returns a result. Executors run after the gateway validates role access and
(if needed) user confirmation — but decide_recommendation and execute_orders
still re-check the domain-level rules (decision_rights, exactly-once locking)
because those are stricter than the tool catalogue's coarse role list and are
the actual source of truth per CLAUDE.md.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain
from app.models.enums import UserRole
from app.models.graph import Account, Goal, Mandate
from app.models.portfolio import Holding, Instrument, Price


class ExecutorError(Exception):
    """Error during tool execution."""
    pass


def _parse_uuid(value: Any, field: str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        raise ExecutorError(f"Invalid {field}: {value}")


class ToolExecutors:
    """Factory for tool executors. Each tool_key maps to an executor function."""

    def __init__(self, session: AsyncSession, user_id: uuid.UUID, role: UserRole, firm_id: uuid.UUID):
        self.session = session
        self.user_id = user_id
        self.role = role
        self.firm_id = firm_id

    async def execute(self, tool_key: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool by key and return the result."""

        executor_map = {
            "read_household": self.read_household,
            "read_portfolio": self.read_portfolio,
            "search_holdings": self.search_holdings,
            "decide_recommendation": self.decide_recommendation,
            "execute_orders": self.execute_orders,
            "update_goal": self.update_goal,
        }

        executor = executor_map.get(tool_key)
        if not executor:
            raise ExecutorError(f"No executor for tool '{tool_key}'")

        try:
            return await executor(inputs)
        except ExecutorError:
            raise
        except Exception as e:
            raise ExecutorError(f"Tool '{tool_key}' failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Read-only tools
    # ─────────────────────────────────────────────────────────────────────

    async def read_household(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a household's complete brain."""
        household_id = _parse_uuid(inputs.get("household_id") or "", "household_id")

        brain = await household_brain(self.session, household_id, firm_id=self.firm_id)
        if not brain:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        return {
            "household_id": str(household_id),
            "brain": brain,
            "message": f"Fetched household brain with {len(brain.get('accounts', []))} accounts",
        }

    async def read_portfolio(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a mandate's holdings, cash, and total value."""
        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")

        mandate = await self.session.get(Mandate, mandate_id)
        if not mandate or mandate.firm_id != self.firm_id:
            raise ExecutorError(f"Mandate {mandate_id} not found or access denied")

        accounts = (
            await self.session.execute(select(Account).where(Account.mandate_id == mandate_id))
        ).scalars().all()
        account_ids = [a.id for a in accounts]

        holdings: list[dict[str, Any]] = []
        total_value = 0.0
        if account_ids:
            rows = (
                await self.session.execute(
                    select(Holding, Instrument)
                    .join(Instrument, Holding.instrument_id == Instrument.id)
                    .where(Holding.account_id.in_(account_ids))
                )
            ).all()
            for h, inst in rows:
                mv = float(h.market_value or 0)
                total_value += mv
                holdings.append({
                    "symbol": inst.symbol,
                    "name": inst.name,
                    "asset_class": str(inst.asset_class),
                    "quantity": float(h.quantity),
                    "market_value": mv,
                })

        cash = sum(float(a.cash_balance or 0) for a in accounts)
        total_value += cash

        return {
            "mandate_id": str(mandate_id),
            "mandate_name": mandate.name,
            "holdings": holdings,
            "cash": cash,
            "total_value": total_value,
            "message": f"{mandate.name}: {len(holdings)} holding(s), cash {cash:,.2f}, total value {total_value:,.2f}",
        }

    async def search_holdings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Search for instruments by symbol or name across the firm."""
        query = inputs.get("query")
        if not query:
            raise ExecutorError("query is required")
        limit = int(inputs.get("limit") or 10)

        stmt = (
            select(Instrument)
            .where(
                Instrument.firm_id == self.firm_id,
                or_(Instrument.symbol.ilike(f"%{query}%"), Instrument.name.ilike(f"%{query}%")),
            )
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        results = [
            {
                "instrument_id": str(i.id),
                "symbol": i.symbol,
                "name": i.name,
                "asset_class": str(i.asset_class),
            }
            for i in rows
        ]
        return {
            "query": query,
            "results": results,
            "message": f"Found {len(results)} holding(s) matching '{query}'",
        }

    # ─────────────────────────────────────────────────────────────────────
    # State-changing tools
    # ─────────────────────────────────────────────────────────────────────

    async def decide_recommendation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Approve, modify, or dismiss a recommendation via the same exactly-once,
        role-checked path the Studio review page uses (app.atlas.runtime.decide)."""
        from app.atlas.runtime import AlreadyDecidedError, decide
        from app.core import decision_rights
        from app.models.enums import HumanAction, RecommendationStatus
        from app.models.governance import Recommendation
        from app.models.identity import User
        from app.models.tenant import Firm

        recommendation_id = _parse_uuid(inputs.get("recommendation_id") or "", "recommendation_id")
        action_str = inputs.get("action")

        if action_str == "revise":
            raise ExecutorError(
                "Revise isn't available through conversation yet — it re-runs the agent with "
                "new constraints (CGT budget, drift band, protected holdings). Use the "
                "Recommendations page in Studio for that, or approve/dismiss here."
            )
        if action_str not in ("approve", "modify", "dismiss"):
            raise ExecutorError(f"Invalid action: {action_str}. Must be approve, modify, or dismiss")

        rec = await self.session.get(Recommendation, recommendation_id)
        if not rec or rec.firm_id != self.firm_id:
            raise ExecutorError(f"Recommendation {recommendation_id} not found or access denied")

        try:
            decision_rights.check(self.role, rec.agent_key, action_str)
        except decision_rights.DecisionForbidden as exc:
            raise ExecutorError(str(exc))

        if str(rec.status) != str(RecommendationStatus.PROPOSED):
            raise ExecutorError(f"Already {rec.status}")

        firm = await self.session.get(Firm, self.firm_id)
        user = await self.session.get(User, self.user_id)
        note = inputs.get("note")

        try:
            rec = await decide(
                self.session, firm=firm, recommendation=rec, action=HumanAction(action_str),
                actor_id=self.user_id, actor_label=f"{user.full_name} ({user.role})",
                note=note,
            )
        except AlreadyDecidedError as exc:
            raise ExecutorError(str(exc))

        return {
            "recommendation_id": str(rec.id),
            "action": action_str,
            "status": str(rec.status),
            "message": f"{action_str.capitalize()}d '{rec.title}'. Status: {rec.status}.",
        }

    async def execute_orders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Submit buy/sell orders on a mandate's first account, via the same order
        engine (draft -> staged -> placed -> filled -> settled) the drift-rebalancing
        agent uses — real prices, real book entries, honest about a paper venue."""
        from app.conduit.execution import execute_order_set
        from app.models.tenant import Firm

        mandate_id = _parse_uuid(inputs.get("mandate_id") or "", "mandate_id")
        orders_in = inputs.get("orders") or []
        reason = inputs.get("reason") or ""

        if not orders_in:
            raise ExecutorError("orders is required and must be a non-empty list")

        mandate = await self.session.get(Mandate, mandate_id)
        if not mandate or mandate.firm_id != self.firm_id:
            raise ExecutorError(f"Mandate {mandate_id} not found or access denied")

        accounts = (
            await self.session.execute(select(Account).where(Account.mandate_id == mandate_id))
        ).scalars().all()
        if not accounts:
            raise ExecutorError(f"Mandate {mandate_id} has no account to trade in")
        # Simplification: trade against the mandate's first account. Mandates with
        # multiple accounts (rare) need an account_id per order — not yet supported
        # from conversation.
        account = accounts[0]

        order_set = []
        for o in orders_in:
            symbol = o.get("symbol")
            side = o.get("side", "buy")
            quantity = o.get("quantity")
            if not symbol or not quantity:
                raise ExecutorError("Each order needs a symbol and quantity")

            inst = (
                await self.session.execute(
                    select(Instrument).where(Instrument.firm_id == self.firm_id, Instrument.symbol == symbol)
                )
            ).scalar_one_or_none()
            if not inst:
                raise ExecutorError(f"Unknown instrument symbol: {symbol}")

            price_row = (
                await self.session.execute(
                    select(Price).where(Price.instrument_id == inst.id).order_by(Price.as_of.desc()).limit(1)
                )
            ).scalar_one_or_none()
            est_price = float(price_row.close) if price_row else 0.0
            est_value = est_price * float(quantity)

            order_set.append({
                "account_id": str(account.id),
                "instrument_id": str(inst.id),
                "side": side,
                "quantity": quantity,
                "est_price": est_price,
                "est_value": est_value,
                "symbol": symbol,
                "asset_class": str(inst.asset_class),
                "reason": reason,
            })

        firm = await self.session.get(Firm, self.firm_id)
        result = await execute_order_set(
            self.session, firm=firm, order_set=order_set, mandate_id=mandate_id, actor=str(self.role),
        )

        note = (
            f"{result['orders_settled']} of {result['orders_created']} order(s) filled and "
            f"settled via '{result['venue']}'"
            + ("" if result["reaches_market"] else " (paper venue — real prices and real book "
                                                   "entries, but no market was reached)")
            + (f". {result['orders_failed']} could not be executed." if result["orders_failed"] else ".")
        )

        return {
            "mandate_id": str(mandate_id),
            "orders_count": len(order_set),
            "orders_settled": result["orders_settled"],
            "orders_failed": result["orders_failed"],
            "venue": result["venue"],
            "fills": result["settled"],
            "failed": result["failed"],
            "message": note,
        }

    async def update_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Update a household goal's target, timeline, or priority."""
        goal_id = _parse_uuid(inputs.get("goal_id") or "", "goal_id")
        target = inputs.get("target")
        timeline = inputs.get("timeline")
        priority = inputs.get("priority")

        goal = await self.session.get(Goal, goal_id)
        if not goal or goal.firm_id != self.firm_id:
            raise ExecutorError(f"Goal {goal_id} not found or access denied")

        updates: dict[str, Any] = {}
        if target is not None:
            goal.target_amount = float(target)
            updates["target_amount"] = float(target)
        if timeline:
            # Only a bare 4-digit year is unambiguous from conversation; anything
            # else (e.g. "5 years") is left for the goal-planning UI to resolve.
            text = str(timeline).strip()
            if text.isdigit() and len(text) == 4:
                from datetime import date
                goal.target_date = date(int(text), 12, 31)
                updates["target_date"] = goal.target_date.isoformat()
            else:
                updates["timeline_note"] = f"'{timeline}' wasn't a specific year — target date unchanged"
        if priority:
            priority_map = {"high": 1, "medium": 2, "low": 3}
            mapped = priority_map.get(str(priority).lower())
            if mapped:
                goal.priority = mapped
                updates["priority"] = str(priority).lower()

        await self.session.flush()

        return {
            "goal_id": str(goal_id),
            "updates": updates,
            "message": f"Updated goal '{goal.name}'.",
        }
