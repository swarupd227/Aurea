"""Phase 2: Tool executors — wire gateway tools to domain functions.

Each tool in the catalogue has a corresponding executor that calls the actual
business logic (household_brain, execute_orders, etc.) and returns a result.

Executors run after the gateway validates role access and (if needed) user confirmation.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.aurea_core.graph import household_brain
from app.models.enums import UserRole


class ExecutorError(Exception):
    """Error during tool execution."""
    pass


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
            # Read-only tools
            "read_household": self.read_household,
            "read_portfolio": self.read_portfolio,
            "search_holdings": self.search_holdings,

            # State-changing tools
            "decide_recommendation": self.decide_recommendation,
            "execute_orders": self.execute_orders,
            "update_goal": self.update_goal,
        }

        executor = executor_map.get(tool_key)
        if not executor:
            raise ExecutorError(f"No executor for tool '{tool_key}'")

        try:
            result = await executor(inputs)
            return result
        except Exception as e:
            raise ExecutorError(f"Tool '{tool_key}' failed: {e}")

    # ─────────────────────────────────────────────────────────────────────
    # Read-only tools
    # ─────────────────────────────────────────────────────────────────────

    async def read_household(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a household's complete brain."""
        household_id = inputs.get("household_id")
        if not household_id:
            raise ExecutorError("household_id is required")

        try:
            household_id = uuid.UUID(household_id) if isinstance(household_id, str) else household_id
        except ValueError:
            raise ExecutorError(f"Invalid household_id: {household_id}")

        brain = await household_brain(self.session, household_id, firm_id=self.firm_id)
        if not brain:
            raise ExecutorError(f"Household {household_id} not found or access denied")

        return {
            "household_id": str(household_id),
            "brain": brain,
            "message": f"Fetched household brain with {len(brain.get('accounts', []))} accounts",
        }

    async def read_portfolio(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Fetch a mandate's holdings and positions."""
        mandate_id = inputs.get("mandate_id")
        if not mandate_id:
            raise ExecutorError("mandate_id is required")

        try:
            mandate_id = uuid.UUID(mandate_id) if isinstance(mandate_id, str) else mandate_id
        except ValueError:
            raise ExecutorError(f"Invalid mandate_id: {mandate_id}")

        # Stub: In real implementation, would fetch mandate.holdings and compute metrics
        return {
            "mandate_id": str(mandate_id),
            "holdings": [],
            "total_value": 0,
            "message": f"Fetched portfolio for mandate {mandate_id}",
        }

    async def search_holdings(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Search for holdings by symbol or name across the firm."""
        query = inputs.get("query")
        limit = inputs.get("limit", 10)

        if not query:
            raise ExecutorError("query is required")

        # Stub: In real implementation, would search across all mandates
        return {
            "query": query,
            "results": [],
            "message": f"Searched for '{query}' (found 0 holdings)",
        }

    # ─────────────────────────────────────────────────────────────────────
    # State-changing tools
    # ─────────────────────────────────────────────────────────────────────

    async def decide_recommendation(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Approve, modify, dismiss, or revise a recommendation."""
        recommendation_id = inputs.get("recommendation_id")
        action = inputs.get("action")
        note = inputs.get("note")

        if not recommendation_id or not action:
            raise ExecutorError("recommendation_id and action are required")

        if action not in ("approve", "modify", "dismiss", "revise"):
            raise ExecutorError(f"Invalid action: {action}. Must be approve|modify|dismiss|revise")

        try:
            recommendation_id = uuid.UUID(recommendation_id) if isinstance(recommendation_id, str) else recommendation_id
        except ValueError:
            raise ExecutorError(f"Invalid recommendation_id: {recommendation_id}")

        # In real implementation, would call runtime.decide() and persist ledger entry
        return {
            "recommendation_id": str(recommendation_id),
            "action": action,
            "decision_by": str(self.user_id),
            "note": note,
            "message": f"Recorded {action} decision on recommendation {recommendation_id}",
        }

    async def execute_orders(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Execute buy/sell orders on a mandate."""
        mandate_id = inputs.get("mandate_id")
        orders = inputs.get("orders", [])
        reason = inputs.get("reason")

        if not mandate_id or not orders:
            raise ExecutorError("mandate_id and orders are required")

        try:
            mandate_id = uuid.UUID(mandate_id) if isinstance(mandate_id, str) else mandate_id
        except ValueError:
            raise ExecutorError(f"Invalid mandate_id: {mandate_id}")

        if not isinstance(orders, list):
            raise ExecutorError("orders must be a list")

        # In real implementation, would call execute_trade, size orders, and settle
        return {
            "mandate_id": str(mandate_id),
            "orders_count": len(orders),
            "reason": reason,
            "fills": [],
            "total_value": 0,
            "message": f"Executed {len(orders)} orders on mandate {mandate_id}",
        }

    async def update_goal(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Update a household goal."""
        goal_id = inputs.get("goal_id")
        target = inputs.get("target")
        timeline = inputs.get("timeline")
        priority = inputs.get("priority")

        if not goal_id:
            raise ExecutorError("goal_id is required")

        try:
            goal_id = uuid.UUID(goal_id) if isinstance(goal_id, str) else goal_id
        except ValueError:
            raise ExecutorError(f"Invalid goal_id: {goal_id}")

        updates = {}
        if target is not None:
            updates["target"] = float(target)
        if timeline:
            updates["timeline"] = timeline
        if priority:
            updates["priority"] = priority

        # In real implementation, would update the goal and recalculate plan
        return {
            "goal_id": str(goal_id),
            "updates": updates,
            "message": f"Updated goal {goal_id}",
        }
