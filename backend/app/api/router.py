"""Aggregates all API routers."""
from fastapi import APIRouter

from app.api import (
    admin, agents, analytics, atlas, auth, canvas, conduit, core, engage, onboarding,
    portfolios, provenance, skills, studio, superadmin, vault,
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
api_router.include_router(canvas.router)
api_router.include_router(onboarding.router)
api_router.include_router(engage.router)
api_router.include_router(skills.router)
api_router.include_router(superadmin.router)
api_router.include_router(portfolios.router)
api_router.include_router(vault.router)
