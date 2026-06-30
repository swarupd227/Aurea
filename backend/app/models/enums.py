"""Domain enumerations shared across the platform.

Kept as str-enums so they serialise cleanly to JSON and store as plain text — values
are deliberately stable strings (changing one is a data migration)."""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ── People & roles ────────────────────────────────────────────────────────────
class UserRole(StrEnum):
    """Spec §4 internal personas, mapped to RBAC roles."""

    SUPERADMIN = "superadmin"  # Platform-level: cross-firm firm management
    ADMIN = "admin"
    ADVISER = "adviser"
    PARAPLANNER = "paraplanner"
    PORTFOLIO_TEAM = "portfolio_team"
    RESEARCH_CIO = "research_cio"
    COMPLIANCE = "compliance"
    OPERATIONS = "operations"
    BRANCH_LEADER = "branch_leader"
    CLIENT = "client"  # Canvas


# ── Client graph ──────────────────────────────────────────────────────────────
class EntityType(StrEnum):
    """Legal-entity kinds — for-purpose & family structures are first-class (spec §4)."""

    TRUST = "trust"
    FOUNDATION = "foundation"
    CHARITY = "charity"
    COMPANY = "company"
    PARTNERSHIP = "partnership"
    IWI = "iwi"  # indigenous / iwi organisation
    SMSF = "smsf"  # self-managed / retirement scheme
    ESTATE = "estate"


class ClientSegment(StrEnum):
    MASS_AFFLUENT = "mass_affluent"
    PRIVATE_WEALTH = "private_wealth"
    FOR_PURPOSE = "for_purpose"
    INSTITUTIONAL = "institutional"
    NEXT_GEN = "next_gen"


class MandateType(StrEnum):
    DISCRETIONARY = "discretionary"
    ADVISORY = "advisory"
    EXECUTION_ONLY = "execution_only"


class RelationshipKind(StrEnum):
    ADVISER = "adviser"
    SPOUSE = "spouse"
    PARENT = "parent"
    CHILD = "child"
    TRUSTEE = "trustee"
    BENEFICIARY = "beneficiary"
    PROFESSIONAL = "professional"  # accountant, lawyer
    INTERGENERATIONAL = "intergenerational"


# ── Portfolio ─────────────────────────────────────────────────────────────────
class AssetClass(StrEnum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    CASH = "cash"
    ALTERNATIVES = "alternatives"  # private markets, hedge, real assets
    PROPERTY = "property"
    COMMODITY = "commodity"
    MULTI_ASSET = "multi_asset"


class MarketType(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


# ── Agents & governance ───────────────────────────────────────────────────────
class AutonomyTier(StrEnum):
    """The autonomy ladder (spec §7.1)."""

    TIER_1 = "tier_1"  # assistive — drafts, human authors
    TIER_2 = "tier_2"  # supervised — acts on approval (HITL gate)
    TIER_3 = "tier_3"  # bounded autonomy — pre-approved, low-risk, post-hoc review


class AgentKey(StrEnum):
    ONBOARDING_KYC_AML = "onboarding_kyc_aml"
    BOOK_INTEGRATION = "book_integration"
    MEETING_PREP = "meeting_prep"
    MEETING_COMPANION = "meeting_companion"
    RESEARCH_REPORTING = "research_reporting"
    DRIFT_REBALANCING = "drift_rebalancing"
    NEXT_BEST_ACTION = "next_best_action"
    CLIENT_CARE = "client_care"
    CONDUCT_SURVEILLANCE = "conduct_surveillance"


class ActivityKind(StrEnum):
    """Live agent-activity events — the workforce 'pulse' (agentic UX)."""

    WATCHING = "watching"      # on-duty / monitoring heartbeat
    SENSING = "sensing"        # gathering signals from the brain
    THINKING = "thinking"      # reasoning / optimising
    PROPOSED = "proposed"      # surfaced a recommendation for a human
    ACTED = "acted"            # executed (incl. autonomous Tier-3)
    DECIDED = "decided"        # human approved / modified / dismissed
    FLAGGED = "flagged"        # conduct surveillance flag
    ROLLED_BACK = "rolled_back"
    SCANNED = "scanned"        # book-wide scan
    DELEGATED = "delegated"    # human delegated a task in natural language


class AgentRunStatus(StrEnum):
    PENDING = "pending"
    SENSING = "sensing"
    THINKING = "thinking"
    AWAITING_APPROVAL = "awaiting_approval"  # HITL gate
    ACTING = "acting"
    COMPLETED = "completed"
    DISMISSED = "dismissed"
    FAILED = "failed"
    PAUSED = "paused"  # kill-switch / surveillance pause


class RecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    MODIFIED = "modified"
    DISMISSED = "dismissed"
    EXECUTED = "executed"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"


class HumanAction(StrEnum):
    APPROVE = "approve"
    MODIFY = "modify"
    DISMISS = "dismiss"


class SurveillanceSeverity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── Connectors (Conduit) ──────────────────────────────────────────────────────
class ConnectorDomain(StrEnum):
    CUSTODY = "custody"
    PORTFOLIO_ACCOUNTING = "portfolio_accounting"
    OMS_EXECUTION = "oms_execution"
    MARKET_RESEARCH_DATA = "market_research_data"
    PRIVATE_MARKETS = "private_markets"
    HELD_AWAY = "held_away"
    INVESTMENT_ENGINE = "investment_engine"
    CRM = "crm"
    PRODUCTIVITY_BI = "productivity_bi"
    AML_KYC = "aml_kyc"
    DOCUMENTS_ESIGN = "documents_esign"
    OPEN_API_EVENTS = "open_api_events"


class ConnectorStatus(StrEnum):
    CONFIGURED = "configured"
    CONNECTED = "connected"
    DISABLED = "disabled"
    ERROR = "error"
    MOCK = "mock"  # running on mock data


# ── Acquire & onboard ─────────────────────────────────────────────────────────
class OnboardingStatus(StrEnum):
    INTAKE = "intake"  # prospect captured, documents pending
    SCREENING = "screening"  # docs extracted, AML run
    REVIEW = "review"  # agent proposal awaiting compliance
    APPROVED = "approved"  # materialised into the client brain
    REJECTED = "rejected"


class BookBatchStatus(StrEnum):
    RECEIVED = "received"  # inbound feed loaded
    RECONCILED = "reconciled"  # mappings proposed, awaiting operations
    COMMITTED = "committed"  # mappings written as golden records
    REJECTED = "rejected"


# ── Advise & engage ───────────────────────────────────────────────────────────
class MeetingStatus(StrEnum):
    SCHEDULED = "scheduled"
    PREPPED = "prepped"  # a brief has been assembled
    COMPLETED = "completed"  # notes captured & actions decided


class TaskStatus(StrEnum):
    OPEN = "open"
    DONE = "done"
    DISMISSED = "dismissed"


class ReportStatus(StrEnum):
    DRAFT = "draft"
    CLIENT_READY = "client_ready"  # reviewed & approved for the client


class MessageAuthor(StrEnum):
    CLIENT = "client"
    ADVISER = "adviser"
    AGENT = "agent"  # agent-drafted, adviser-approved outreach


class HeirJourneyStatus(StrEnum):
    INVITED = "invited"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
