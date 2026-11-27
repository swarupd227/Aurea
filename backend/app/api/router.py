"""Aggregates all API routers."""
from fastapi import APIRouter

from app.api import (
    account_features, account_registration, admin, advisers, autonomy, briefs, introductions, rulebook, aml, aml_monitoring, consents, document_matrix, money_in, onboarding_scoreboard, repapering, transfer_files, agents, ai_governance, analytics, atlas, auth, balance_sheet, billing, canvas, cash_movement, client_file, equity_comp, exits, financial_plan, shelf, trades, valuations,
    compliance_program, composites, conduit, core, corporate_actions, crm, engage, ips, licences, onboarding,
    portfolios, preferences, prices, protection, provenance, reconciliation, reviews, risk_profile, skills, sleeves, studio,
    superadmin, threads, vault,
)
from app.api import india_tax_planning

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(core.router)
api_router.include_router(agents.router)
api_router.include_router(studio.router)
api_router.include_router(analytics.router)
api_router.include_router(atlas.router)
api_router.include_router(provenance.router)
api_router.include_router(conduit.router)
api_router.include_router(admin.router)
api_router.include_router(compliance_program.router)
api_router.include_router(crm.router)
api_router.include_router(corporate_actions.router)
api_router.include_router(sleeves.router)
api_router.include_router(account_registration.router)
api_router.include_router(account_registration.registration_audit_router)
api_router.include_router(ai_governance.router)
api_router.include_router(composites.router)
api_router.include_router(billing.router)
api_router.include_router(valuations.router)
api_router.include_router(prices.router)
api_router.include_router(reconciliation.router)
api_router.include_router(protection.router)
api_router.include_router(licences.router)
# India's tax planning (B-396).
api_router.include_router(india_tax_planning.router)
api_router.include_router(risk_profile.router)
api_router.include_router(reviews.router)
api_router.include_router(ips.router)
api_router.include_router(balance_sheet.router)
api_router.include_router(exits.router)
api_router.include_router(trades.router)
api_router.include_router(aml.router)
api_router.include_router(consents.router)
api_router.include_router(document_matrix.router)
api_router.include_router(transfer_files.router)
api_router.include_router(aml_monitoring.router)
api_router.include_router(money_in.router)
api_router.include_router(account_features.router)
api_router.include_router(onboarding_scoreboard.router)
api_router.include_router(repapering.router)
api_router.include_router(advisers.router)
api_router.include_router(briefs.router)
api_router.include_router(preferences.router)
api_router.include_router(introductions.router)
api_router.include_router(autonomy.router)
api_router.include_router(rulebook.router)
api_router.include_router(cash_movement.router)
api_router.include_router(client_file.router)
api_router.include_router(financial_plan.router)
api_router.include_router(equity_comp.router)
api_router.include_router(shelf.router)
api_router.include_router(canvas.router)
api_router.include_router(onboarding.router)
api_router.include_router(engage.router)
api_router.include_router(skills.router)
api_router.include_router(superadmin.router)
api_router.include_router(portfolios.router)
api_router.include_router(threads.router)
api_router.include_router(vault.router)
# Held away, line by line, and how it is doing (B-198).
from app.api import held_away as held_away_api  # noqa: E402

api_router.include_router(held_away_api.router)
# Outside agents call Astra's tools through MCP, with a person's key (B-166).
from app.api import outside_agents as outside_agents_api  # noqa: E402

api_router.include_router(outside_agents_api.router)
# An adviser's own recommendations to a client, and what became of each (B-175).
from app.api import advice_file as advice_file_api  # noqa: E402

api_router.include_router(advice_file_api.router)
# The plan of record: baselines, reviews and deliveries, and the firm's plan metrics (B-181).
from app.api import plan_record as plan_record_api  # noqa: E402

api_router.include_router(plan_record_api.router)
# The consolidation workbench, India (B-389).
from app.api import consolidation as consolidation_api  # noqa: E402

api_router.include_router(consolidation_api.router)

# Health cover and a savings policy's values, India (B-397).
from app.api import health_cover as health_cover_api  # noqa: E402

api_router.include_router(health_cover_api.router)
# India: the KYC registry status (B-386).
from app.api import kyc_registry as kyc_registry_api  # noqa: E402

api_router.include_router(kyc_registry_api.router)

# India's asset register, will checks and NRI planning (B-398).
from app.api import estate_nri as estate_nri_api  # noqa: E402
from app.api import india_agreements as india_agreements_api  # noqa: E402

api_router.include_router(estate_nri_api.router)
api_router.include_router(india_agreements_api.router)
