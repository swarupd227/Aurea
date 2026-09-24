"""Aggregates all API routers."""
from fastapi import APIRouter

from app.api import (
    account_registration, admin, agents, ai_governance, analytics, atlas, auth, canvas,
    compliance_program, composites, conduit, core, corporate_actions, crm, engage, onboarding,
    portfolios, provenance, skills, sleeves, studio, superadmin, threads, vault,
)

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
api_router.include_router(canvas.router)
api_router.include_router(onboarding.router)
api_router.include_router(engage.router)
api_router.include_router(skills.router)
api_router.include_router(superadmin.router)
api_router.include_router(portfolios.router)
api_router.include_router(threads.router)
api_router.include_router(vault.router)
