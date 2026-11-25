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
# An adviser's own recommendations to a client, and what became of each (B-175).
from app.models import advice_file  # noqa: F401,E402
# Plan scenarios a person saved on a household (B-179).
from app.models import scenarios  # noqa: F401,E402
# A regulatory filing a person says was made, for the compliance calendar (India row 6).
from app.models import compliance_calendar  # noqa: F401,E402
# A filed tax return's facts, recorded from the document (B-183).
from app.models import tax_return  # noqa: F401,E402
# The plan of record: baselines, reviews and deliveries of a household's plan (B-181).
from app.models import plan_record  # noqa: F401,E402
# How much of a plan a trigger asks for, set by a person (B-188).
from app.models import plan_tier  # noqa: F401,E402
# A retirement match or a deferred compensation election, from the plan document (B-193).
from app.models import employer_benefit  # noqa: F401,E402
# A client's residence, FATCA and CRS declarations and a minor's guardian (India row 7).
from app.models import residency  # noqa: F401,E402
# A Portfolio Manager's agreement with a client (India row 8).
from app.models import pms  # noqa: F401,E402
# Account Aggregator consents and e-CAS statements (India row 9), and the complaints register (India row 10).
from app.models import aggregator  # noqa: F401,E402
from app.models import complaints  # noqa: F401,E402
# Capital losses carried forward and an equity's 31 Jan 2018 value, as a person recorded them (India row 11).
from app.models import india_tax  # noqa: F401,E402
# A client's KYC as a person recorded it from the document (India row 12).
from app.models import kyc  # noqa: F401,E402
# SEBI family of client, nominations, a will's state, an HUF and gratuity (India row 13), and the governance register and counsel review (India row 14).
from app.models import india_family  # noqa: F401,E402
from app.models import india_governance  # noqa: F401,E402
from app.models import india_polish  # noqa: F401,E402
from app.models import india_gates  # noqa: F401,E402
from app.models import india_batch_b  # noqa: F401,E402
from app.models import india_batch_a  # noqa: F401,E402
from app.models import india_batch_c  # noqa: F401,E402
