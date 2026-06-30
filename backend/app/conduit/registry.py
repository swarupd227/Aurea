"""The Conduit connector catalogue (spec §11, Table 17) + popular extras.

Each provider definition declares a typed config schema so the Admin console can render a
configuration form, mark which fields are secret (write-only over the API), and indicate
whether a live (non-mock) connection is implemented. The posture is connect-and-normalise:
every connector ships ready to configure, runs on realistic mock data by default, and the
market-data connector additionally supports a genuinely live feed."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.models.enums import ConnectorDomain


@dataclass
class ConfigField:
    key: str
    label: str
    type: str = "string"  # string | password | number | bool | select
    secret: bool = False
    required: bool = False
    default: object = None
    options: list[str] = field(default_factory=list)
    help: str = ""


@dataclass
class ProviderDef:
    key: str
    domain: ConnectorDomain
    display_name: str
    description: str
    # Whether a real (live) integration is implemented; others are mock-only for now.
    supports_live: bool = False
    config_schema: list[ConfigField] = field(default_factory=list)
    # Default sync cadence suggestion.
    default_cron: str = "0 */6 * * *"


def _api(*extra: ConfigField) -> list[ConfigField]:
    base = [
        ConfigField("base_url", "Base URL", "string", help="API endpoint of the system."),
        ConfigField("api_key", "API key", "password", secret=True),
    ]
    return base + list(extra)


CONNECTOR_REGISTRY: list[ProviderDef] = [
    # ── Custody & registry ────────────────────────────────────────────────────
    ProviderDef(
        "custody.generic", ConnectorDomain.CUSTODY, "Generic Custodian / Registry",
        "Holdings, valuations, transactions and beneficial ownership from a custodian.",
        config_schema=_api(ConfigField("account_scope", "Account scope filter")),
    ),
    ProviderDef(
        "custody.pershing", ConnectorDomain.CUSTODY, "BNY Pershing", "Custody feed (mock).",
        config_schema=_api(),
    ),
    ProviderDef(
        "custody.fnz", ConnectorDomain.CUSTODY, "FNZ", "Custody / wrap platform feed (mock).",
        config_schema=_api(),
    ),
    # ── Portfolio accounting ──────────────────────────────────────────────────
    ProviderDef(
        "pa.generic", ConnectorDomain.PORTFOLIO_ACCOUNTING, "Portfolio Accounting",
        "Performance, tax lots, fees and reconciliations.", config_schema=_api(),
    ),
    ProviderDef(
        "pa.addepar", ConnectorDomain.PORTFOLIO_ACCOUNTING, "Addepar",
        "Multi-asset portfolio accounting & reporting (mock).", config_schema=_api(),
    ),
    # ── Order management / execution ──────────────────────────────────────────
    ProviderDef(
        "oms.generic", ConnectorDomain.OMS_EXECUTION, "Order Management / Execution",
        "Routes approved orders to market (incl. local exchange). No live execution in demo.",
        config_schema=_api(ConfigField("exchange", "Default exchange", default="NZX")),
    ),
    ProviderDef(
        "oms.flextrade", ConnectorDomain.OMS_EXECUTION, "FlexTrade OMS", "OMS routing (mock).",
        config_schema=_api(),
    ),
    # ── Market & research data (REAL feed available) ──────────────────────────
    ProviderDef(
        "marketdata.yahoo", ConnectorDomain.MARKET_RESEARCH_DATA, "Yahoo Finance Market Data (Live)",
        "Real market prices via Yahoo Finance — no API key required. Default live feed.",
        supports_live=True, default_cron="0 */1 * * *",
        config_schema=[ConfigField("note", "Note", default="No key required.")],
    ),
    ProviderDef(
        "marketdata.stooq", ConnectorDomain.MARKET_RESEARCH_DATA, "Stooq Market Data (Live)",
        "Real end-of-day prices via Stooq — no API key required.",
        supports_live=True, default_cron="0 */1 * * *",
        config_schema=[ConfigField("note", "Note", default="No key required.")],
    ),
    ProviderDef(
        "marketdata.alphavantage", ConnectorDomain.MARKET_RESEARCH_DATA, "Alpha Vantage (Live)",
        "Real market data via Alpha Vantage. Requires a free API key.",
        supports_live=True, default_cron="0 */1 * * *",
        config_schema=[ConfigField("api_key", "API key", "password", secret=True, required=True)],
    ),
    ProviderDef(
        "research.firmstore", ConnectorDomain.MARKET_RESEARCH_DATA, "Firm Research Store",
        "The firm's own proprietary research & house views (ingested to the client brain).",
        config_schema=_api(),
    ),
    # ── Private markets & alternatives ────────────────────────────────────────
    ProviderDef(
        "alts.generic", ConnectorDomain.PRIVATE_MARKETS, "Private Markets / Alternatives",
        "Capital calls, distributions, fund look-through and alternatives administration.",
        config_schema=_api(),
    ),
    ProviderDef(
        "alts.icapital", ConnectorDomain.PRIVATE_MARKETS, "iCapital", "Alternatives admin (mock).",
        config_schema=_api(),
    ),
    # ── Held-away / open finance ──────────────────────────────────────────────
    ProviderDef(
        "openfinance.plaid", ConnectorDomain.HELD_AWAY, "Plaid (Open Finance)",
        "Account aggregation of held-away assets for a true total-wealth view.",
        config_schema=_api(
            ConfigField("client_id", "Client ID"), ConfigField("environment", "Environment",
            "select", options=["sandbox", "development", "production"], default="sandbox"),
        ),
    ),
    ProviderDef(
        "openfinance.akahu", ConnectorDomain.HELD_AWAY, "Akahu (NZ Open Finance)",
        "NZ open-finance aggregation (mock).", config_schema=_api(),
    ),
    # ── Best-of-breed investment engines ──────────────────────────────────────
    ProviderDef(
        "engine.aladdin", ConnectorDomain.INVESTMENT_ENGINE, "BlackRock Aladdin Wealth",
        "Institutional whole-portfolio risk analytics via API/MCP.", config_schema=_api(),
    ),
    ProviderDef(
        "engine.directindex", ConnectorDomain.INVESTMENT_ENGINE, "Direct Indexing Engine",
        "Tax-managed direct-indexing engine (mock).", config_schema=_api(),
    ),
    ProviderDef(
        "engine.planning", ConnectorDomain.INVESTMENT_ENGINE, "Goals-Based Planning Engine",
        "Third-party financial-planning / projection engine (mock).", config_schema=_api(),
    ),
    # ── CRM ───────────────────────────────────────────────────────────────────
    ProviderDef(
        "crm.dynamics", ConnectorDomain.CRM, "Microsoft Dynamics 365",
        "Relationships, interactions and tasks.", config_schema=_api(
            ConfigField("tenant_id", "Tenant ID")),
    ),
    ProviderDef(
        "crm.salesforce", ConnectorDomain.CRM, "Salesforce Financial Services Cloud",
        "CRM relationships & activities (mock).", config_schema=_api(
            ConfigField("instance_url", "Instance URL")),
    ),
    # ── Productivity & BI ─────────────────────────────────────────────────────
    ProviderDef(
        "prod.m365", ConnectorDomain.PRODUCTIVITY_BI, "Microsoft 365 / Copilot",
        "Calendar, mail and embedded Studio surfaces via Graph/MCP.", config_schema=_api(
            ConfigField("tenant_id", "Tenant ID")),
    ),
    ProviderDef(
        "prod.fabric", ConnectorDomain.PRODUCTIVITY_BI, "Microsoft Fabric / Power BI",
        "BI & analytics estate (mock).", config_schema=_api(),
    ),
    # ── AML / KYC ─────────────────────────────────────────────────────────────
    ProviderDef(
        "aml.generic", ConnectorDomain.AML_KYC, "AML / KYC Provider",
        "Identity verification, screening and ongoing monitoring.", config_schema=_api(),
    ),
    ProviderDef(
        "aml.refinitiv", ConnectorDomain.AML_KYC, "LSEG World-Check",
        "Sanctions / PEP screening (mock).", config_schema=_api(),
    ),
    # ── Documents & e-signature ───────────────────────────────────────────────
    ProviderDef(
        "docs.docusign", ConnectorDomain.DOCUMENTS_ESIGN, "DocuSign",
        "Intake, generation and execution of client documents.", config_schema=_api(),
    ),
    ProviderDef(
        "docs.generic", ConnectorDomain.DOCUMENTS_ESIGN, "Document Intelligence / e-Sign",
        "Document extraction & e-signature (mock).", config_schema=_api(),
    ),
    # ── Comms (popular extra) ─────────────────────────────────────────────────
    ProviderDef(
        "comms.twilio", ConnectorDomain.OPEN_API_EVENTS, "Twilio (SMS / Voice)",
        "Client messaging & notifications (mock).", config_schema=_api(
            ConfigField("from_number", "From number")),
    ),
    # ── Data platform (popular extra) ─────────────────────────────────────────
    ProviderDef(
        "data.snowflake", ConnectorDomain.OPEN_API_EVENTS, "Snowflake",
        "Enterprise data platform / warehouse (mock).", config_schema=_api(
            ConfigField("account", "Account"), ConfigField("warehouse", "Warehouse")),
    ),
    # ── Open APIs & events ────────────────────────────────────────────────────
    ProviderDef(
        "events.webhook", ConnectorDomain.OPEN_API_EVENTS, "Outbound Webhooks / Event Bus",
        "Outbound APIs and an event backbone for real-time updates and extensibility.",
        config_schema=[ConfigField("webhook_url", "Webhook URL"),
                       ConfigField("signing_secret", "Signing secret", "password", secret=True)],
    ),
    # ── Custom connectors (registered via POST /api/admin/connectors/custom) ──
    ProviderDef(
        "custom.webhook", ConnectorDomain.OPEN_API_EVENTS, "Custom Webhook",
        "Outbound webhook with configurable payload and field mappings.",
        supports_live=True,
        config_schema=[
            ConfigField("webhook_url", "Webhook URL", required=True),
            ConfigField("auth_header", "Auth header name", default="Authorization"),
            ConfigField("auth_value", "Auth value / token", "password", secret=True),
            ConfigField("event_filter", "Event filter", help="Comma-separated event types to forward"),
            ConfigField("field_mappings", "Field mappings (JSON)", help='{"src_field": "dest_field"}'),
        ],
    ),
    ProviderDef(
        "custom.rest", ConnectorDomain.OPEN_API_EVENTS, "Custom REST API",
        "Inbound REST connector that polls a custom API endpoint.",
        supports_live=True,
        config_schema=[
            ConfigField("base_url", "Base URL", required=True),
            ConfigField("api_key", "API key", "password", secret=True),
            ConfigField("auth_scheme", "Auth scheme", "select",
                        options=["bearer", "basic", "header", "none"], default="bearer"),
            ConfigField("poll_endpoint", "Data endpoint path", default="/data"),
            ConfigField("field_mappings", "Field mappings (JSON)", help='{"src_field": "dest_field"}'),
        ],
    ),
]

_BY_KEY = {p.key: p for p in CONNECTOR_REGISTRY}


def get_provider_def(key: str) -> ProviderDef | None:
    return _BY_KEY.get(key)


def default_connectors() -> list[ProviderDef]:
    """One sensible default connector per domain for a freshly-seeded firm."""
    seen: set[ConnectorDomain] = set()
    out: list[ProviderDef] = []
    # Prefer the live market-data feed and one generic per other domain.
    preferred = {
        ConnectorDomain.MARKET_RESEARCH_DATA: "marketdata.yahoo",
    }
    for dom, key in preferred.items():
        out.append(_BY_KEY[key])
        seen.add(dom)
    for p in CONNECTOR_REGISTRY:
        if p.domain not in seen:
            out.append(p)
            seen.add(p.domain)
    return out
