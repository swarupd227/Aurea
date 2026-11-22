"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from app.models import (  # noqa: F401
    agent_preference,
    ai_governance,
    aml_review,
    aml_risk,
    aml_monitoring,
    balance_sheet,
    billing,
    brief_profile,
    cash_movement,
    sip,
    client_file,
    client_experience,
    compliance,
    compliance_program,
    composites,
    connectors,
    consent,
    consent_registry,
    transfer_file,
    money_in,
    account_features,
    onboarding_feedback,
    repapering,
    corporate_actions,
    crm,
    engagement,
    financial_plan,
    equity_comp,
    exits,
    governance,
    graph,
    identity,
    knowledge,
    onboarding,
    portfolio,
    protection,
    reconciliation,
    risk_assessment,
    shelf,
    tax_documents,
    skill,
    sleeves,
    telemetry,
    tenant,
    valuations,
    thread,
    trading,
    vault,
)
# The Foundation layer (B-161 to B-163): external ids, breaks and outbound commands.
from app.models import foundation  # noqa: F401,E402
# Holdings held elsewhere, line by line (B-198).
from app.models import held_away  # noqa: F401,E402
# Keys a person gives an outside agent to call Astra's tools through MCP (B-166).
from app.models import outside_agent  # noqa: F401,E402
# Who a product is for, its distribution sign-off, and who it was offered to (B-226 to B-231).
from app.models import product_distribution  # noqa: F401,E402
# The firm's planning assumptions, one versioned record (B-174).
from app.models import assumptions  # noqa: F401,E402
# Plan scenarios a person saved on a household (B-179).
from app.models import scenarios  # noqa: F401,E402
