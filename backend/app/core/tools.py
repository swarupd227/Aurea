"""Phase 2: Tool catalogue schema and definitions.

Every tool the orchestrator can call is defined here: input/output schema, whether it
changes state, what roles may use it, and the confirmation sentence if it needs one.

This is the single source of truth for the gateway and the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.models.enums import UserRole


class ToolChangeState(str, Enum):
    """Whether a tool changes application state."""
    NO = "no"  # Purely read, no side effects (e.g., read_household)
    YES = "yes"  # Modifies state on client's book or firm data (e.g., execute_trade)
    PAUSE_FOR_CONFIRMATION = "pause"  # Changes state but requires user confirmation first


@dataclass
class ToolInput:
    """Describes a single input parameter to a tool."""
    name: str
    type_name: str  # e.g., "uuid", "string", "number", "list[string]"
    description: str
    required: bool = True


@dataclass
class ToolOutput:
    """Describes the output of a tool."""
    type_name: str  # e.g., "object", "list[dict]", "string"
    description: str


@dataclass
class Tool:
    """Describes a single tool the orchestrator can invoke."""
    key: str  # e.g., "read_household", "execute_trade"
    name: str  # e.g., "Read Household", "Execute Trade"
    speaking_agent: str  # Which agent claims credit (AgentKey value)
    description: str  # Plain-English what it does

    change_state: ToolChangeState  # Whether it modifies data
    confirmation: str | None  # If PAUSE_FOR_CONFIRMATION, the confirmation text to show

    inputs: list[ToolInput]  # What it takes
    output: ToolOutput  # What it returns

    roles_required: set[UserRole]  # Which roles may call this tool


# =============================================================================
# TOOLS CATALOGUE
# =============================================================================

TOOLS: dict[str, Tool] = {
    # ─── Read-only tools (no confirmation needed)

    "read_household": Tool(
        key="read_household",
        name="Read Household",
        speaking_agent="astra",
        description="Fetch a household's full brain: accounts, goals, holdings, family members, financial health.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("household_id", "uuid", "The household to read", required=True),
        ],
        output=ToolOutput("object", "The household brain with all nested data"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "read_portfolio": Tool(
        key="read_portfolio",
        name="Read Portfolio",
        speaking_agent="astra",
        description="Fetch a mandate's current holdings, positions, and performance.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("mandate_id", "uuid", "The mandate to read", required=True),
        ],
        output=ToolOutput("object", "Holdings, positions, and performance metrics"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "search_holdings": Tool(
        key="search_holdings",
        name="Search Holdings",
        speaking_agent="astra",
        description="Search for holdings by symbol, name, or other criteria across a firm's clients.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("query", "string", "Search term or symbol", required=True),
            ToolInput("limit", "number", "Max results to return", required=False),
        ],
        output=ToolOutput("list[object]", "Matching holdings across the firm"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    # ─── State-changing tools (require confirmation)

    "decide_recommendation": Tool(
        key="decide_recommendation",
        name="Approve Recommendation",
        speaking_agent="astra",
        description="Approve, modify, revise, or dismiss an agent recommendation.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This decision will be recorded permanently. Continue?",
        inputs=[
            ToolInput("recommendation_id", "uuid", "The recommendation to decide", required=True),
            ToolInput("action", "string", "approve|modify|dismiss|revise", required=True),
            ToolInput("note", "string", "Optional note or revision instruction", required=False),
        ],
        output=ToolOutput("object", "Decision record with ledger entry"),
        # Only adviser and portfolio team may approve trades; compliance may veto; see CLAUDE.md
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "execute_orders": Tool(
        key="execute_orders",
        name="Execute Orders",
        speaking_agent="drift_rebalancing",
        description="Submit a set of orders (buys/sells) to settle them onto a client's book.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will place orders and settle trades. Confirm?",
        inputs=[
            ToolInput("mandate_id", "uuid", "The mandate to trade", required=True),
            ToolInput("orders", "list[object]", "Buy/sell orders with symbols and quantities", required=True),
            ToolInput("reason", "string", "Why these orders (e.g., drift rebalance, tax loss harvest)", required=False),
        ],
        output=ToolOutput("object", "Execution record with fills and fees"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM},  # Only these roles may execute
    ),

    "update_goal": Tool(
        key="update_goal",
        name="Update Goal",
        speaking_agent="astra",
        description="Update a household goal's parameters (target, timeline, priority).",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will update the goal and may trigger plan recalculations. Continue?",
        inputs=[
            ToolInput("goal_id", "uuid", "The goal to update", required=True),
            ToolInput("target", "number", "New target amount", required=False),
            ToolInput("timeline", "string", "New timeline (e.g., 2030, 5 years)", required=False),
            ToolInput("priority", "string", "New priority (high/medium/low)", required=False),
        ],
        output=ToolOutput("object", "Updated goal with recalculated progress"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER},
    ),
}


def get_tool(key: str) -> Tool | None:
    """Fetch a tool by key, or None if it does not exist."""
    return TOOLS.get(key)


def tools_for_role(role: UserRole) -> dict[str, Tool]:
    """Return all tools this role may use."""
    return {key: tool for key, tool in TOOLS.items() if role in tool.roles_required}


def validate_tool_call(role: UserRole, tool_key: str, inputs: dict[str, Any]) -> tuple[bool, str]:
    """
    Validate that a role may call this tool with these inputs.

    Returns (is_valid, error_message).
    """
    tool = get_tool(tool_key)
    if not tool:
        return False, f"Tool '{tool_key}' does not exist"

    if role not in tool.roles_required:
        allowed = ", ".join(sorted(r.value for r in tool.roles_required))
        return False, f"Role {role.value} may not call '{tool.name}'. Allowed: {allowed}"

    # Validate required inputs
    required = {inp.name for inp in tool.inputs if inp.required}
    provided = set(inputs.keys())
    missing = required - provided
    if missing:
        return False, f"Missing required inputs: {', '.join(sorted(missing))}"

    return True, ""
