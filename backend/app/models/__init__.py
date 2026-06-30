"""SQLAlchemy models. Importing this package registers every table on Base.metadata."""
from app.models import (  # noqa: F401
    client_experience,
    compliance,
    connectors,
    engagement,
    governance,
    graph,
    identity,
    knowledge,
    onboarding,
    portfolio,
    skill,
    telemetry,
    tenant,
    vault,
)
