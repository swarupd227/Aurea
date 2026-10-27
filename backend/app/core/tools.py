"""Phase 2: Tool catalogue schema and definitions.

Every tool the orchestrator can call is defined here: input/output schema, whether it
changes state, what roles may use it, and the confirmation sentence if it needs one.

This is the single source of truth for the gateway and the frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.core import agent_runs
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

    "check_household_wash_sale": Tool(
        key="check_household_wash_sale",
        name="Check Household Wash Sale",
        speaking_agent="astra",
        description="Check a household's wash-sale calendar — which lots a loss-harvest would disallow right now, and which account's recent purchase (a spouse's account, an IRA, anywhere in the household) is the reason.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("household_id", "uuid", "The household to check", required=True),
        ],
        output=ToolOutput("object", "Flagged lots with disallowed-loss amounts and conflicting purchases"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "read_performance": Tool(
        key="read_performance",
        name="Read Performance",
        speaking_agent="astra",
        description="A household's investment performance: time-weighted and money-weighted return over the months of price history on file, benchmark and alpha, and what drove it by asset class. Securities only, gross of fees, with synthetic or unpriced holdings and stale prices named.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("household_id", "uuid", "The household to measure", required=True),
        ],
        output=ToolOutput("object", "TWR, MWR, monthly returns, attribution by asset class, benchmark, and disclosures"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "read_billing": Tool(
        key="read_billing",
        name="Read Billing",
        speaking_agent="billing",
        description="Billing position. With a household: its invoices and credit notes, whether each was paid from cash, and any checks raised on them. Without one: the billing run waiting for approval, the exceptions (mandates with no fee agreement on file, bills held by a block, bills issued but unpaid for lack of cash) and what has been billed this year.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("household_id", "uuid", "A household to show invoices for; leave out for the firm-wide position", required=False),
        ],
        output=ToolOutput("object", "Invoices, the open run, and billing exceptions"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS,
                       UserRole.BRANCH_LEADER, UserRole.ADMIN},
    ),

    "explain_invoice": Tool(
        key="explain_invoice",
        name="Explain Invoice",
        speaking_agent="billing",
        description="Show how one invoice was worked out: the fee schedule and its bands, the balance each account contributed, the proration, the date it was valued on and how old the oldest price was, and every check raised on it. Give an invoice id, or a household to explain its most recent bill.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("invoice_id", "uuid", "The invoice to explain", required=False),
            ToolInput("household_id", "uuid", "Explain this household's most recent invoice", required=False),
        ],
        output=ToolOutput("object", "The invoice with its full working"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS,
                       UserRole.BRANCH_LEADER, UserRole.ADMIN},
    ),

    "run_billing": Tool(
        key="run_billing",
        name="Prepare Billing Run",
        speaking_agent="billing",
        description="Have the billing agent work out what is due for the current period and prepare a billing run for approval. Nothing is issued and no fee is taken until operations or a branch leader approves the run.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="Prepare a billing run? It only drafts the bills for approval - nothing is issued and no fee is taken from any account until operations or a branch leader approves it.",
        inputs=[],
        output=ToolOutput("object", "The proposed run, its bills and their checks"),
        roles_required={UserRole.OPERATIONS, UserRole.BRANCH_LEADER, UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "credit_invoice": Tool(
        key="credit_invoice",
        name="Credit Invoice",
        speaking_agent="billing",
        description="Offset an issued invoice in full with a credit note. The invoice is kept; any fee already taken from cash is returned as its own entry.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="Credit this invoice in full? The invoice stays on record, a credit note offsets it, and any fee already taken from the client's cash is returned.",
        inputs=[
            ToolInput("invoice_id", "uuid", "The issued invoice to credit", required=True),
            ToolInput("reason", "string", "Why it is being credited", required=True),
        ],
        output=ToolOutput("object", "The credit note"),
        roles_required={UserRole.OPERATIONS, UserRole.BRANCH_LEADER},
    ),

    "read_private_valuations": Tool(
        key="read_private_valuations",
        name="Read Private Valuations",
        speaking_agent="astra",
        description="What each private holding (unlisted fund) is carried at: the value, whether it is a stated valuation or only a placeholder, the date, how old that is against the limit for private holdings, who recorded it and from which document, and how much of the book rests on it.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("object", "Private instruments with their latest valuation, age and exposure"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.RESEARCH_CIO, UserRole.OPERATIONS,
                       UserRole.BRANCH_LEADER, UserRole.ADMIN},
    ),

    "record_valuation": Tool(
        key="record_valuation",
        name="Record Valuation",
        speaking_agent="astra",
        description="Record the value a manager has stated for a private holding, with the document it comes from. This revalues every holding of it, which changes client balances and the fees charged on them. Needs a source document; a move of more than 50% also needs a note explaining it.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="Record this valuation? It revalues every holding of this fund, which changes client balances and the fees charged on them.",
        inputs=[
            ToolInput("symbol", "string", "The private instrument's symbol, e.g. PPEF1", required=True),
            ToolInput("unit_value", "number", "The stated value per unit", required=True),
            ToolInput("as_of", "string", "The date the value is stated as at (YYYY-MM-DD)", required=True),
            ToolInput("source_document", "string", "The statement it comes from - name, date and manager", required=True),
            ToolInput("notes", "string", "Required if the value moved by more than 50%", required=False),
        ],
        output=ToolOutput("object", "The recorded valuation, the move, and the value revalued"),
        roles_required={UserRole.OPERATIONS, UserRole.PORTFOLIO_TEAM},
    ),

    "read_composites": Tool(
        key="read_composites",
        name="Read Composites",
        speaking_agent="astra",
        description="List the firm's GIPS composites (one per strategy/model portfolio).",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("list[object]", "Composites with their strategy and inclusion criteria"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "read_composite_report": Tool(
        key="read_composite_report",
        name="Read Composite Report",
        speaking_agent="astra",
        description="Fetch a GIPS composite's presentation: gross return, internal dispersion, ex-post standard deviation (honestly labelled by how many months of history it actually used), benchmark, and account count.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("composite_id", "uuid", "The composite to report on", required=True),
        ],
        output=ToolOutput("object", "The composite's GIPS presentation"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "read_firm_definition_check": Tool(
        key="read_firm_definition_check",
        name="Read Firm Definition Check",
        speaking_agent="astra",
        description="Check whether every discretionary account is captured in a composite — GIPS's 'foundational sin' verifiers test is a firm definition narrowed to exclude bad history.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("object", "Discretionary AUM vs. composite-captured AUM"),
        roles_required={UserRole.PORTFOLIO_TEAM, UserRole.RESEARCH_CIO, UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "read_ai_governance_summary": Tool(
        key="read_ai_governance_summary",
        name="Read AI Governance Summary",
        speaking_agent="astra",
        description="Fetch the AI use-case inventory: risk-tier breakdown, use cases overdue for review, entitlement-violation counts (zero-tolerance), and the most recent prompt/model/tool changes logged.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("object", "Use-case inventory coverage, violations, and recent changes"),
        roles_required={UserRole.ADMIN, UserRole.COMPLIANCE},
    ),

    "read_account_registration": Tool(
        key="read_account_registration",
        name="Read Account Registration",
        speaking_agent="astra",
        description="Fetch an account's registration type, RMD status (computed from its actual holdings value), and beneficiary designations.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("account_id", "uuid", "The account to read", required=True),
        ],
        output=ToolOutput("object", "Registration type, RMD status, and beneficiaries"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "set_account_registration": Tool(
        key="set_account_registration",
        name="Set Account Registration",
        speaking_agent="astra",
        description="Set an account's registration type, or (for an inherited IRA) the original owner's date of death and the 10-year-rule vs. life-expectancy RMD election.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will set the account's registration/RMD details, which changes how its tax treatment is computed. Continue?",
        inputs=[
            ToolInput("account_id", "uuid", "The account to update", required=True),
            ToolInput("registration_type", "string", "e.g. traditional_ira, roth_ira, trust, inherited_ira", required=False),
            ToolInput("original_owner_death_date", "string", "ISO date, for an inherited account", required=False),
            ToolInput("rmd_election_method", "string", "10_year_rule|life_expectancy, for an inherited account", required=False),
        ],
        output=ToolOutput("object", "The updated account registration"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "add_account_beneficiary": Tool(
        key="add_account_beneficiary",
        name="Add Account Beneficiary",
        speaking_agent="astra",
        description="Add a primary or contingent beneficiary designation to an account.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will add a beneficiary designation to the account record. Continue?",
        inputs=[
            ToolInput("account_id", "uuid", "The account to add a beneficiary to", required=True),
            ToolInput("beneficiary_name", "string", "The beneficiary's name", required=True),
            ToolInput("percentage", "number", "Share of this designation class, 0-100", required=True),
            ToolInput("designation_class", "string", "primary|contingent (default primary)", required=False),
            ToolInput("relationship_to_owner", "string", "e.g. spouse, child", required=False),
            ToolInput("per_stirpes", "string", "true if per stirpes (default false)", required=False),
        ],
        output=ToolOutput("object", "The added beneficiary"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "read_beneficiary_audit": Tool(
        key="read_beneficiary_audit",
        name="Read Beneficiary Audit",
        speaking_agent="astra",
        description="Check which accounts needing beneficiary designations (IRAs, trusts, 529s, HSAs, inherited accounts) are missing them or have percentages that don't sum to 100 — firm-wide, or for one household.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("household_id", "uuid", "Scope to one household instead of the whole firm", required=False),
        ],
        output=ToolOutput("object", "Accounts with incomplete beneficiary coverage"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "search_households": Tool(
        key="search_households",
        name="Search Households",
        speaking_agent="astra",
        description="Search for households by name across the firm. Each match includes its accounts (id, name, custodian), so a specific account can be resolved by name in one call — e.g. 'the Chen Family's Trust Custody account'.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("query", "string", "Household name or partial name", required=True),
            ToolInput("limit", "number", "Max households to return", required=False),
        ],
        output=ToolOutput("list[object]", "Matching households, each with its accounts"),
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

    "read_recommendations": Tool(
        key="read_recommendations",
        name="Read Recommendations",
        speaking_agent="astra",
        description="The agents' proposals waiting for a human decision (or recently decided), most urgent first, with the agent, the client it concerns, its priority, and whether the caller's role may decide it. Use it to find a recommendation_id before deciding one - never guess an id.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("status", "string", "open (default: waiting for a decision) or all", required=False),
            ToolInput("subject", "string", "Only proposals whose client or subject name contains this text", required=False),
            ToolInput("limit", "number", "Max results to return (default 20)", required=False),
        ],
        output=ToolOutput("object", "Proposals with id, title, agent, subject, priority, confidence and can_decide"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.RESEARCH_CIO, UserRole.COMPLIANCE, UserRole.OPERATIONS,
                       UserRole.BRANCH_LEADER, UserRole.ADMIN},
    ),

    "read_agents": Tool(
        key="read_agents",
        name="Read Agents",
        speaking_agent="astra",
        description="The firm's agents: each one's stage, the autonomy tier it runs at, whether it is paused or disabled (and why), how many runs it has made and proposals it has put forward, what it did last, and whether the caller's role may start it from a conversation. Use it to answer 'what agents can I run' and to find an agent_key before running one.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("runnable_only", "string", "true to list only the agents this role may start from a conversation", required=False),
        ],
        output=ToolOutput("object", "Agents with tier, status, paused reason, run counts and can_run"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.RESEARCH_CIO, UserRole.COMPLIANCE, UserRole.OPERATIONS,
                       UserRole.BRANCH_LEADER, UserRole.ADMIN},
    ),

    # ─── State-changing tools (require confirmation)

    "run_agent": Tool(
        key="run_agent",
        name="Run Agent",
        speaking_agent="astra",
        description=(
            "Start one agent on a client or mandate, or across the book. The agent senses, reasons, checks and then PROPOSES; "
            "it never approves its own proposal, and an agent that would move a client's book or cash always stops at a "
            "proposal for a human to decide. Whether it may act on its own is the autonomy tier the firm set, shown on the "
            "confirmation. Give the agent_key (call read_agents if unsure) and, where the agent works on one client, the "
            "household_id (find it with search_households) or mandate_id - never guess an id. Agents you can start: "
            + ", ".join(agent_runs.conversation_agent_keys()) + "."
        ),
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation=(
            "Start this agent run? Confirming only starts it - it is not an approval of anything the agent proposes, "
            "and it does not change the agent's autonomy tier."
        ),
        inputs=[
            ToolInput("agent_key", "string", "Which agent to run, e.g. drift_rebalancing or next_best_action", required=True),
            ToolInput("household_id", "uuid", "The household the run is about (from search_households)", required=False),
            ToolInput("mandate_id", "uuid", "The mandate the run is about (drift_rebalancing works on a mandate)", required=False),
        ],
        output=ToolOutput("object", "The run's beats, any proposals it put forward, and the tier it ran at"),
        roles_required=set(agent_runs.ANY_RUN_ROLES),
    ),

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

    # ─── Compliance program: conflicts inventory + WSP grid (L200-7)

    "read_compliance_program": Tool(
        key="read_compliance_program",
        name="Read Compliance Program",
        speaking_agent="astra",
        description="Fetch the firm's conflicts inventory, WSP grid, and evidence-coverage summary.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("object", "Conflicts, WSP rules, and coverage/overdue-review summary"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                       UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "mark_conflict_reviewed": Tool(
        key="mark_conflict_reviewed",
        name="Mark Conflict Reviewed",
        speaking_agent="astra",
        description="Record that a conflicts-inventory item has been reviewed today, resetting its review cadence.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will record a review of this conflict as of today. Continue?",
        inputs=[
            ToolInput("conflict_key", "string", "The conflict's key (e.g. revenue_sharing)", required=True),
            ToolInput("notes", "string", "Optional notes on the review", required=False),
        ],
        output=ToolOutput("object", "The updated conflict-inventory item"),
        roles_required={UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    "log_wsp_evidence": Tool(
        key="log_wsp_evidence",
        name="Log WSP Evidence",
        speaking_agent="astra",
        description="Record that a WSP grid rule's supervisory activity produced evidence today.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will log evidence for this obligation as of today. Continue?",
        inputs=[
            ToolInput("rule_key", "string", "The WSP rule's key (e.g. wash_sale.loss_harvest)", required=True),
        ],
        output=ToolOutput("object", "The updated WSP rule"),
        roles_required={UserRole.COMPLIANCE, UserRole.ADMIN},
    ),

    # ─── CRM pipeline (L200-8 §2.1)

    "read_crm_pipeline": Tool(
        key="read_crm_pipeline",
        name="Read CRM Pipeline",
        speaking_agent="astra",
        description="Fetch contacts, pipeline opportunities, and pipeline summary stats (stage counts, weighted value, win rate).",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[],
        output=ToolOutput("object", "Contacts, opportunities, and pipeline summary"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM,
                       UserRole.RESEARCH_CIO, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "create_crm_contact": Tool(
        key="create_crm_contact",
        name="Create CRM Contact",
        speaking_agent="astra",
        description="Add a new prospect, referral source, or centre-of-influence contact to the pipeline.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will add a new contact to the CRM pipeline. Continue?",
        inputs=[
            ToolInput("full_name", "string", "The contact's name", required=True),
            ToolInput("contact_type", "string", "prospect|referral_source|centre_of_influence", required=False),
            ToolInput("email", "string", "Contact email", required=False),
            ToolInput("source", "string", "How this contact came in (referral, event, inbound, cold)", required=False),
            ToolInput("notes", "string", "Free-form notes", required=False),
        ],
        output=ToolOutput("object", "The created contact"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN},
    ),

    "create_crm_opportunity": Tool(
        key="create_crm_opportunity",
        name="Create CRM Opportunity",
        speaking_agent="astra",
        description="Open a new pipeline deal against an existing contact.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will open a new pipeline opportunity. Continue?",
        inputs=[
            ToolInput("contact_id", "uuid", "The contact this opportunity is against", required=True),
            ToolInput("title", "string", "A short deal title", required=True),
            ToolInput("estimated_aum", "number", "Estimated AUM if won", required=False),
            ToolInput("probability_pct", "number", "Probability of winning, 0-100", required=False),
        ],
        output=ToolOutput("object", "The created opportunity"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN},
    ),

    "log_crm_activity": Tool(
        key="log_crm_activity",
        name="Log CRM Activity",
        speaking_agent="astra",
        description="Log a call, meeting, email, or note against a CRM contact (and optionally an opportunity).",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will log an activity on this contact's record. Continue?",
        inputs=[
            ToolInput("contact_id", "uuid", "The contact this activity is against", required=True),
            ToolInput("activity_type", "string", "call|email|meeting|note|task", required=False),
            ToolInput("detail", "string", "What happened", required=True),
            ToolInput("opportunity_id", "uuid", "The opportunity this activity relates to", required=False),
        ],
        output=ToolOutput("object", "The logged activity"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN},
    ),

    "update_crm_opportunity_stage": Tool(
        key="update_crm_opportunity_stage",
        name="Update CRM Opportunity Stage",
        speaking_agent="astra",
        description="Move a pipeline opportunity to a new stage (lead/qualified/proposal/won/lost).",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will move the opportunity to a new pipeline stage. Continue?",
        inputs=[
            ToolInput("opportunity_id", "uuid", "The opportunity to move", required=True),
            ToolInput("stage", "string", "lead|qualified|proposal|won|lost", required=True),
            ToolInput("lost_reason", "string", "Why it was lost, if stage=lost", required=False),
        ],
        output=ToolOutput("object", "The updated opportunity"),
        roles_required={UserRole.ADVISER, UserRole.PARAPLANNER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN},
    ),

    # ─── Corporate actions (L200-4 §5)

    "read_corporate_actions": Tool(
        key="read_corporate_actions",
        name="Read Corporate Actions",
        speaking_agent="astra",
        description="Fetch announced/open corporate actions and their entitlement status.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("status", "string", "Filter by status (announced|election_open|election_closed|posted|verified)", required=False),
        ],
        output=ToolOutput("object", "Corporate actions with their entitlement rows"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.COMPLIANCE,
                       UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "compute_corporate_action_entitlements": Tool(
        key="compute_corporate_action_entitlements",
        name="Compute Corporate Action Entitlements",
        speaking_agent="astra",
        description="Compute one entitlement row per account currently holding the instrument for a corporate action.",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will compute entitlements for every account holding this instrument. Continue?",
        inputs=[
            ToolInput("corporate_action_id", "uuid", "The corporate action to compute entitlements for", required=True),
        ],
        output=ToolOutput("object", "Newly created entitlement rows"),
        roles_required={UserRole.OPERATIONS, UserRole.ADMIN, UserRole.COMPLIANCE},
    ),

    "post_corporate_action_entitlement": Tool(
        key="post_corporate_action_entitlement",
        name="Post Corporate Action Entitlement",
        speaking_agent="astra",
        description="Post one account's entitlement — applies cash/holding/tax-lot arithmetic for mechanical events (dividends, splits, return of capital); records the human's cost-basis call for voluntary events (mergers, spin-offs).",
        change_state=ToolChangeState.PAUSE_FOR_CONFIRMATION,
        confirmation="This will post the entitlement and update the account's cash, holding, and tax lots. This cannot be undone or re-posted. Continue?",
        inputs=[
            ToolInput("entitlement_id", "uuid", "The entitlement to post", required=True),
        ],
        output=ToolOutput("object", "The posted entitlement"),
        roles_required={UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    # ─── UMA sleeves (L200-3 §6)

    "read_sleeves": Tool(
        key="read_sleeves",
        name="Read Sleeves",
        speaking_agent="astra",
        description="Fetch an account's sleeves and their reconciliation status against the flat custodial holdings.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("account_id", "uuid", "The account to read sleeves for", required=True),
        ],
        output=ToolOutput("object", "Sleeves and a reconciliation report"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.OPERATIONS, UserRole.ADMIN},
    ),

    "net_sleeve_intents": Tool(
        key="net_sleeve_intents",
        name="Net Sleeve Intents",
        speaking_agent="astra",
        description="Cross conflicting sleeve trade intents on the same instrument into one net order per (account, instrument) — a stateless computation, nothing is placed or changed.",
        change_state=ToolChangeState.NO,
        confirmation=None,
        inputs=[
            ToolInput("intents", "list[object]", "Sleeve intents: {sleeve_id, account_id, instrument_id, symbol, side, quantity}", required=True),
        ],
        output=ToolOutput("object", "Net orders per (account, instrument) with sleeve allocations"),
        roles_required={UserRole.ADVISER, UserRole.PORTFOLIO_TEAM, UserRole.ADMIN},
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
        return False, f"Role {role} may not call '{tool.name}'. Allowed: {allowed}"

    # Validate required inputs
    required = {inp.name for inp in tool.inputs if inp.required}
    provided = set(inputs.keys())
    missing = required - provided
    if missing:
        return False, f"Missing required inputs: {', '.join(sorted(missing))}"

    return True, ""


def _json_schema_type(type_name: str) -> dict[str, Any]:
    """Map a ToolInput.type_name to a JSON Schema fragment for Claude's tool-use API."""
    if type_name == "uuid":
        return {"type": "string", "description": "A UUID"}
    if type_name == "number":
        return {"type": "number"}
    if type_name.startswith("list["):
        inner = type_name[len("list["):-1]
        item_type = {"type": "object"} if inner in ("object", "dict") else {"type": "string"}
        return {"type": "array", "items": item_type}
    return {"type": "string"}


def anthropic_tool_schema(tool: Tool) -> dict[str, Any]:
    """Convert a Tool into Claude's native tool-use schema (Anthropic Messages API)."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    for inp in tool.inputs:
        schema = _json_schema_type(inp.type_name)
        schema["description"] = inp.description
        properties[inp.name] = schema
        if inp.required:
            required.append(inp.name)

    return {
        "name": tool.key,
        "description": tool.description,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


def anthropic_tools_for_role(role: UserRole) -> list[dict[str, Any]]:
    """The Claude tool-use schema list for every tool this role may call."""
    return [anthropic_tool_schema(t) for t in tools_for_role(role).values()]
